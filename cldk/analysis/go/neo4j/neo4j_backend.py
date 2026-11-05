################################################################################
# Copyright IBM Corporation 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""The read-only Neo4j Go analysis backend.

A drop-in alternative to :class:`~cldk.analysis.go.codeanalyzer.codeanalyzer.GoCodeanalyzer`: the
same :class:`~cldk.analysis.go.backend.GoAnalysisBackend` query surface, answered over the
``GO_``-prefixed graph a ``cango --emit neo4j`` run populated out of band.

**Lazy per-subtree fetch, not an eager rebuild.** Go graphs reach enterprise scale (a 525k-node,
156k-edge graph was measured on a real app), so this backend never materialises the whole
application: each accessor issues one *indexed* Cypher query touching only what it needs
(``get_callers(id)`` is a single ``GO_CALLS`` lookup, ``get_class(id)`` one node). Parity with the
local backend is therefore proven by test (the two answer the same question by different paths),
not guaranteed by sharing a rebuilt ``GoApplication``.

Every query is scoped to one application by the id prefix ``can://<app>/`` (and the ``:GoCanNode``
marker label the projector stamps), so a shared graph holding several applications or a
sibling-language subgraph is never scanned into an answer.

The artifact/dependency/config six **raise** here, exactly as on the local backend: the Go neo4j
projector declares ``:Artifact`` / ``:Package`` / ``:ConfigKey`` in its schema but does not yet
emit them, so the layer is reserved-but-empty on this projection too. When the projector starts
emitting it, those six flip to querying the graph for real.
"""

from __future__ import annotations

import json
from functools import cached_property
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    EdgeOrder,
    check_depth,
    check_distinct_endpoints,
    check_max_nodes,
    check_max_paths,
    check_page_size,
    cursor_params,
    encode_cursor,
    keyset_where,
)
from cldk.analysis.commons.graphs import cone_sinks, flow_path, sdg_path_query, sdg_taint_query, slice_resolved
from cldk.analysis.commons.identity import index_module_rows, resolve_identity, scoped_module_keys, validate_identity
from cldk.analysis.commons.resolve import CallableCandidate, body_node_kind, resolve_callable_signature, resolve_value_name, resolve_within
from cldk.analysis.commons.results import Diagnostic, EdgePage, FlowPath, FlowPaths, Slice, SliceNode
from cldk.analysis.go.backend import (
    ARTIFACT_LAYER_UNAVAILABLE,
    CDG_ORDER,
    CFG_ORDER,
    DDG_ORDER,
    PORTS_DISCONNECTED,
    SDG_REL_PATTERN,
    VIA,
    GoAnalysisBackend,
)
from cldk.analysis.go.ids import callable_match_names, external_signature, go_value_names, pick_declaration
from cldk.analysis.go.neo4j import reconstruct as R
from cldk.models.go import (
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoCdgEdge,
    GoCfgEdge,
    GoDdgEdge,
    GoDegraded,
    GoExternalSymbol,
    GoField,
    GoModule,
    GoParam,
    GoType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

#: Relationship types a Go graph must carry to be a codeanalyzer-go projection at all.
_REQUIRED_RELS = {"GO_HAS_MODULE", "GO_DECLARES", "GO_HAS_METHOD", "GO_CALLS"}


def _slice_node(row: Dict[str, Any], module_key: Callable[[str], str]) -> SliceNode:
    """One row of a slice / path query as a :class:`SliceNode`, in the caller's vocabulary.

    The local twin is ``GoCodeanalyzer._slice_node``; the two share
    :func:`~cldk.analysis.commons.resolve.body_node_kind`, so a vertex a caller addressed through
    ``resolve_value`` as a ``global`` comes back from a slice labelled a ``global`` on either
    backend, and they differ only in where the fields are read from.

    ``row["var"]`` is the graph's name for the JSON's ``of`` (see
    :func:`~cldk.analysis.go.neo4j.reconstruct.body_node`), so it is what carries the value a
    vertex stands for.

    A vertex that is a **library stand-in's** has no module — its id holds ``/@external/`` where a
    project node holds a ``.go`` path — so ``file`` is left empty rather than passed through a
    module-key derivation that would mint a path naming nothing.
    """
    kind, name, defined_in = body_node_kind(row["kind"], row["var"])
    ref = row["ref"]
    try:
        file = module_key(ref)
    except ValueError:
        file = ""
    return SliceNode(
        file=file,
        line=row["line"] if row["line"] is not None else row["c_line"],
        callable=row["callable"] or ref,
        kind=kind,
        name=name,
        defined_in=defined_in,
        source=None,
        ref=ref,
    )


def _call_neighbour(row: Dict[str, Any], module_key: Callable[[str], str]) -> SliceNode:
    """One ``GO_CALLS`` neighbour as a :class:`SliceNode` — declared callable or library stand-in.

    Which it is, is read off the row rather than asked for in a second query: only a
    ``:GoCallable`` carries ``signature`` *and* a module, and a stand-in carries ``module``/``name``
    (``internal/neo4j/schema.go``). A stand-in's reported name is
    :func:`~cldk.analysis.go.ids.external_signature`, the same spelling the local backend reports
    and the same one ``resolve_callable`` accepts, so a path's external hop reads identically on
    both backends.
    """
    try:
        file = module_key(row["ref"])
    except ValueError:
        file = ""
    if file:
        return SliceNode(file=file, line=row["line"] or 0, callable=row["signature"], kind="callable", name=row["name"], source=None, ref=row["ref"])
    return SliceNode(
        file="",
        line=0,
        callable=external_signature(row["module"] or "", row["signature"], row["name"] or ""),
        kind="external",
        name=row["name"],
        source=None,
        ref=row["ref"],
    )


class GoNeo4jBackend(GoAnalysisBackend):
    """Answer the Go query surface over a ``GO_``-prefixed Neo4j graph (see the module docstring)."""

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_username: str,
        neo4j_password: str,
        neo4j_database: str | None = None,
        application_name: str | None = None,
        namespace: str | None = None,
        source_root=None,
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with `pip install neo4j` " "(or `pip install cldk[neo4j]`).") from e
        self._init_with_driver(
            GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password)),
            application_name=application_name,
            namespace=namespace,
            source_root=source_root,
            neo4j_database=neo4j_database,
        )

    @classmethod
    def _from_driver(
        cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None, namespace: str | None = None, source_root=None
    ) -> "GoNeo4jBackend":
        """Construct from an already-built driver — the seam tests inject a fake/real driver here."""
        self = cls.__new__(cls)
        self._init_with_driver(driver, application_name=application_name, neo4j_database=neo4j_database, namespace=namespace, source_root=source_root)
        return self

    def _init_with_driver(self, driver: Any, *, application_name: str | None, neo4j_database: str | None, namespace: str | None = None, source_root=None) -> None:
        self.identity = resolve_identity(application_name, namespace)
        self.application_name = self.identity.application_name
        self.source_root = source_root
        self._database = neo4j_database
        self._driver = driver
        self._session_obj: Any | None = None
        self._call_graph: nx.DiGraph | None = None
        self._probe_schema()
        self._probe_identity()

    # -----[ scope + execution ]-----
    @property
    def _app_id(self) -> str:
        """``can://<namespace>/<app>`` — the ``:GoApplication`` anchor id and the root of every id below it."""
        return self.identity.application_id

    @property
    def _scope_prefix(self) -> str:
        """``can://<namespace>/<app>/`` — the trailing slash keeps ``app`` from matching ``app-b``."""
        return f"{self._app_id}/"

    #: The segments that may follow ``can://<ns>/<app>/`` on a node id this application owns.
    #: ``go/`` is project code — the language segment every declared node sits under — and
    #: ``@external/`` is a **library stand-in**, which is equally this application's node: the
    #: projector mints it under this app's prefix, it carries this app's ``formal_in`` ports, and a
    #: flow *through* ``database/sql.DB.Query`` is a flow this graph holds. Admitting only ``go/``
    #: refused every stand-in id, which made ``taint``, ``paths_between`` and the two flow
    #: predicates raise "outside the bound Go application scope" for a sink inside the application
    #: — measured against the live graph, 19 of 22 parity diffs had this one cause. The foreign-owner
    #: and foreign-language cases the guard exists for are unaffected: both differ before this
    #: segment, in the ``can://<ns>/<app>/`` prefix itself.
    _OWNED_SEGMENTS = ("go/", "@external/")

    def _in_scope(self, node_id: str) -> bool:
        """Does ``node_id`` name a node *this* application owns (see :data:`_OWNED_SEGMENTS`)?"""
        return any(node_id.startswith(self._scope_prefix + seg) for seg in self._OWNED_SEGMENTS)

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run one read statement over a reused session; drop the session on failure.

        A ``can://`` id passed as a point-lookup parameter is checked against this application's
        scope **before** the statement runs, so a foreign owner or a sibling language's node is
        refused without a round trip rather than silently answering from another application's
        subgraph.
        """
        for name in ("id", "mid", "tid"):
            value = params.get(name)
            if isinstance(value, str) and value.startswith("can:") and not self._in_scope(value):
                raise ValueError("Node URI is outside the bound Go application scope")
        if self._session_obj is None:
            self._session_obj = self._driver.session(database=self._database)
        try:
            return [record.data() for record in self._session_obj.run(query, **params)]
        except Exception:
            self._session_obj = None
            raise

    def close(self) -> None:
        if self._session_obj is not None:
            self._session_obj = None
        self._driver.close()

    def __enter__(self) -> "GoNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _probe_identity(self) -> None:
        rows = self._run("MATCH (a:GoApplication {id: $app_id}) RETURN properties(a) AS identity", app_id=self._app_id)
        validate_identity(rows[0].get("identity", {}) if rows else {}, self.identity, envelope=False)

    def _probe_schema(self) -> None:
        """Refuse a graph that is not a codeanalyzer-go projection or holds no node for this app."""
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        missing = _REQUIRED_RELS - found
        if missing:
            raise CodeanalyzerExecutionException(f"the graph is not a codeanalyzer-go projection: missing relationship types {sorted(missing)}")
        rows = self._run("OPTIONAL MATCH (a:GoApplication {id: $app_id}) RETURN count(a) AS n", app_id=self._app_id)
        if not (rows and rows[0].get("n")):
            raise CodeanalyzerExecutionException(f"the graph holds no :GoApplication node for {self.application_name!r} (id {self._app_id!r})")

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> GoApplication:
        """The whole application view. Whole-program accessors touch everything by nature; this
        builds the symbol table from the containment relationships and the call graph from
        ``GO_CALLS``, cached."""
        return self._application

    @cached_property
    def _application(self) -> GoApplication:
        return GoApplication(
            identity_version=2,
            application_name=self.identity.application_name,
            identity_namespace=self.identity.namespace,
            id=self._app_id,
            symbol_table=self._build_symbol_table(),
            call_graph=self._call_edges(),
        )

    def get_symbol_table(self) -> Dict[str, GoModule]:
        return self.get_application_view().symbol_table

    def _module_file_key(self, module_id: str) -> str:
        """``can://<app>/<file>`` → ``<file>`` (the symbol-table key the local backend uses)."""
        prefix = self._scope_prefix + "go/"
        if not module_id.startswith(prefix):
            raise ValueError("Module URI is outside the bound Go application scope")
        return module_id[len(prefix) :]

    def _build_symbol_table(self) -> Dict[str, GoModule]:
        """Rebuild every module with its types, functions, methods and fields from the containment
        relationships. This is the expensive whole-program path; point accessors below never call
        it."""
        modules: Dict[str, GoModule] = {}
        mod_rows = self._run(
            "MATCH (:GoApplication {id: $app_id})-[:GO_HAS_MODULE]->(m:GoModule) RETURN properties(m) AS p ORDER BY m.id",
            app_id=self._app_id,
        )
        self._module_index = index_module_rows({"module_id": r["p"]["id"], "file_key": self._module_file_key(r["p"]["id"])} for r in mod_rows)
        scoped_module_keys(self._module_index, self._app_id, ("go",))
        module_props = {r["p"]["id"]: r["p"] for r in mod_rows}
        for module_id in self._module_index:
            mr = {"p": module_props[module_id]}
            mod = R.module(mr["p"])
            # package-level functions
            for fr in self._run(
                "MATCH (m:GoModule {id: $mid})-[:GO_DECLARES]->(c:GoCallable) RETURN properties(c) AS p ORDER BY c.signature",
                mid=mod.id,
            ):
                fn = R.callable_(fr["p"])
                mod.functions[fn.signature] = fn
            # types + their methods and fields
            for tr in self._run(
                "MATCH (m:GoModule {id: $mid})-[:GO_DECLARES]->(t:GoType) RETURN properties(t) AS p ORDER BY t.id",
                mid=mod.id,
            ):
                ty = R.go_type(tr["p"])
                for mrow in self._run(
                    "MATCH (t:GoType {id: $tid})-[:GO_HAS_METHOD]->(c:GoCallable) RETURN properties(c) AS p ORDER BY c.signature",
                    tid=ty.id,
                ):
                    m = R.callable_(mrow["p"])
                    ty.callables[m.signature] = m
                for frow in self._run(
                    "MATCH (t:GoType {id: $tid})-[:GO_HAS_FIELD]->(f:GoField) RETURN properties(f) AS p ORDER BY f.id",
                    tid=ty.id,
                ):
                    fld = R.field(frow["p"])
                    ty.fields[fld.id.split("/")[-1]] = fld
                # keyed by the short name, as in the JSON types{} map
                mod.types[ty.id.split("/")[-1]] = ty
            modules[self._module_file_key(mod.id)] = mod
        return modules

    # -----[ call graph ]-----
    def _call_edges(self) -> List[GoCallGraphEdge]:
        rows = self._run(
            "MATCH (a:GoCallable)-[r:GO_CALLS]->(b) WHERE a.id STARTS WITH $pre " "RETURN a.id AS src, b.id AS dst, properties(r) AS p ORDER BY a.id, b.id",
            pre=self._scope_prefix,
        )
        return [GoCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["p"].get("prov") or []), weight=r["p"].get("weight", 1)) for r in rows]

    def get_call_graph(self) -> nx.DiGraph:
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for e in self._call_edges():
            graph.add_node(e.src, id=e.src)
            graph.add_node(e.dst, id=e.dst)
            graph.add_edge(e.src, e.dst, type="CALL_DEP", weight=e.weight, provenance=tuple(e.prov))
        self._call_graph = graph
        return graph

    def get_call_graph_json(self) -> str:
        return self.get_application_view().model_dump_json()

    def get_callers(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Indexed point query: ``(a)-[:GO_CALLS]->(callable_id)``. No whole-graph materialisation."""
        rows = self._run(
            "MATCH (a:GoCallable)-[r:GO_CALLS]->(b:GoCallable {id: $id}) RETURN a.id AS src, properties(r) AS p ORDER BY a.id",
            id=callable_id,
        )
        return [GoCallGraphEdge(src=r["src"], dst=callable_id, prov=list(r["p"].get("prov") or []), weight=r["p"].get("weight", 1)) for r in rows]

    def get_callees(self, callable_id: str) -> List[GoCallGraphEdge]:
        rows = self._run(
            "MATCH (a:GoCallable {id: $id})-[r:GO_CALLS]->(b) RETURN b.id AS dst, properties(r) AS p ORDER BY b.id",
            id=callable_id,
        )
        return [GoCallGraphEdge(src=callable_id, dst=r["dst"], prov=list(r["p"].get("prov") or []), weight=r["p"].get("weight", 1)) for r in rows]

    # -----[ types (class-named base slots; Go has no class) ]-----
    def get_all_classes(self) -> Dict[str, GoType]:
        """Every Go type (struct + interface), keyed by its durable ``can://`` id. One indexed scan
        of ``:GoType`` nodes under the app prefix; methods/fields are not attached here (that is the
        whole-program rebuild) — use :meth:`get_all_methods_in_class` / :meth:`get_all_fields`."""
        rows = self._run(
            "MATCH (t:GoType) WHERE t.id STARTS WITH $pre RETURN properties(t) AS p ORDER BY t.id",
            pre=self._scope_prefix,
        )
        return {r["p"]["id"]: R.go_type(r["p"]) for r in rows}

    def get_class(self, qualified_class_name: str) -> GoType | None:
        rows = self._run("MATCH (t:GoType {id: $id}) RETURN properties(t) AS p", id=qualified_class_name)
        return R.go_type(rows[0]["p"]) if rows else None

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, GoCallable]:
        rows = self._run(
            "MATCH (t:GoType {id: $id})-[:GO_HAS_METHOD]->(c:GoCallable) RETURN properties(c) AS p ORDER BY c.signature",
            id=qualified_class_name,
        )
        return {r["p"]["signature"]: R.callable_(r["p"]) for r in rows}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> GoCallable | None:
        rows = self._run(
            "MATCH (t:GoType {id: $id})-[:GO_HAS_METHOD]->(c:GoCallable {signature: $sig}) RETURN properties(c) AS p",
            id=qualified_class_name,
            sig=qualified_method_name,
        )
        return R.callable_(rows[0]["p"]) if rows else None

    def get_all_fields(self, qualified_class_name: str) -> List[GoField]:
        rows = self._run(
            "MATCH (t:GoType {id: $id})-[:GO_HAS_FIELD]->(f:GoField) RETURN properties(f) AS p ORDER BY f.id",
            id=qualified_class_name,
        )
        return [R.field(r["p"]) for r in rows]

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[GoParam]:
        # :GoCallable does not project parameters; a reconstructed method carries none. Returning
        # [] keeps parity with "the graph does not carry this", not an invented answer.
        m = self.get_method(qualified_class_name, qualified_method_name)
        return list(m.parameters) if m else []

    # -----[ functions + source ]-----
    def get_functions(self) -> Dict[str, GoCallable]:
        """Every package-level function (``:GoModule``-[:GO_DECLARES]->(:GoCallable)``), keyed by
        signature."""
        rows = self._run(
            "MATCH (m:GoModule)-[:GO_DECLARES]->(c:GoCallable) WHERE m.id STARTS WITH $pre " "RETURN properties(c) AS p ORDER BY c.signature",
            pre=self._scope_prefix,
        )
        return {r["p"]["signature"]: R.callable_(r["p"]) for r in rows}

    def get_source(self, node_id: str) -> str:
        """The source text of ``node_id`` — ``module.source`` sliced by the node's ``span_json``
        byte offsets. The owning ``:GoModule`` carries ``source``; the node's module is the longest
        ``:GoModule.id`` the node id starts with. Byte offsets are UTF-8, as in the local backend."""
        node_rows = self._run(
            "MATCH (n:GoCanNode {id: $id}) RETURN n.span_json AS span",
            id=node_id,
        )
        if not node_rows:
            raise CodeanalyzerExecutionException(f"no node {node_id!r} in the graph for application {self.application_name!r}")
        span_json = node_rows[0]["span"]
        if not span_json:
            raise CodeanalyzerExecutionException(f"node {node_id!r} carries no span, so it has no source text")
        # the owning module: the :GoModule whose id is a prefix of this node's id
        mod_rows = self._run(
            "MATCH (m:GoModule) WHERE $id STARTS WITH m.id + '/' RETURN m.id AS id, m.source AS source ORDER BY size(m.id) DESC LIMIT 1",
            id=node_id,
        )
        if not mod_rows:
            raise CodeanalyzerExecutionException(f"no owning module for {node_id!r}")
        source = mod_rows[0]["source"] or ""
        b0, b1 = json.loads(span_json)["bytes"]
        if source.isascii():
            return source[b0:b1]
        return source.encode("utf-8")[b0:b1].decode("utf-8")

    # =====================================================================================
    # L3/L4 DATAFLOW over Cypher (python-sdk#436). The contract and the four Go traps are on
    # :class:`~cldk.analysis.go.backend.GoAnalysisBackend`; the id/name grammar is
    # :mod:`cldk.analysis.go.ids`, shared with the local backend so the two cannot drift on it.
    #
    # NO LEVEL GATE HERE. ``cango --emit neo4j`` is always full depth (it takes no ``-a``), so an
    # attached graph either carries the SDG or is not a dataflow projection at all — which is why
    # ``_require_dataflow`` is the *local* backend's method. What this backend does share is
    # :meth:`_require_connected_ports`, because a graph can carry the ports and still have nothing
    # crossing into them, and that is a question only the data can answer.
    #
    # THE GRAPH RENAMES TWO BODY-NODE PROPERTIES: the JSON's ``of`` is ``b.var`` and its ``parent``
    # is ``b.call_node`` (``internal/neo4j/schema.go``). Every projection below reads ``b.var``, so
    # the value a vertex stands for arrives under the name the shared helpers expect.
    #
    # A STAND-IN IS A FIRST-CLASS VERTEX. ``:GoExternal`` nodes carry ``GO_HAS_BODY_NODE`` body
    # nodes at L4, so the traversals must not be labelled ``:GoCallable``-only on the interior or
    # a flow *through* ``database/sql.DB.Query`` would be invisible — which is the very flow the
    # httpsql fixture gates. ``:GoCallable|GoExternal`` is therefore deliberate everywhere below.
    # =====================================================================================

    #: Both a project callable and a library stand-in own body nodes at L4, so every containment hop
    #: on this surface accepts either label (see the block comment above).
    _OWNER = "GoCallable|GoExternal"

    def _require_connected_ports(self, accessor: str) -> None:
        """Refuse ``accessor`` when this application's ports carry no dependence (see the ABC)."""
        if not self._ports_carry_dependence:
            raise CodeanalyzerExecutionException(PORTS_DISCONNECTED.format(accessor=accessor, app=self.application_name))

    @cached_property
    def _ports_carry_dependence(self) -> bool:
        """Whether any ``formal_in`` vertex of this application has an outgoing SDG edge.

        One ``LIMIT 1`` existence query, cached: the probe reads the *data* rather than a version
        string, exactly as the Java backend's does, so a graph re-emitted by a build that connects
        the layers makes these accessors work with no change here. An edge *leaving* a port is the
        question — an edge arriving at one only says the caller side was projected.
        """
        rows = self._run(
            f"MATCH (b:GoBodyNode {{kind:'formal_in'}})-[r:{SDG_REL_PATTERN}]->() WHERE b.id STARTS WITH $pre RETURN count(r) > 0 AS ok LIMIT 1",
            pre=self._scope_prefix,
        )
        return bool(rows and rows[0].get("ok"))

    # -----[ addressing ]-----
    @cached_property
    def _callable_candidates(self) -> List[CallableCandidate]:
        """Every callable and stand-in this application declares, as resolution candidates.

        One scan, cached for the backend's life. The stand-ins are in the domain for the local
        backend's reason: ``database/sql.DB.Query`` is spec §7's worked example and is one, and its
        ``formal_in`` ports are real L4 vertices a flow passes through.

        A ``:GoCallable``'s ``in_module=`` name is its module's ``package`` clause, read from the
        graph rather than derived from the path — the shared ``module_dotted`` derivation knows only
        ``.py`` and would leave ``httpsql/httpsql.go`` as a dotted name that matches nothing.
        """
        rows = self._run(
            "MATCH (m:GoModule)-[:GO_HAS_MODULE|GO_DECLARES|GO_HAS_METHOD*0..2]->(c:GoCallable) WHERE c.id STARTS WITH $pre "
            "OPTIONAL MATCH (t:GoType)-[:GO_HAS_METHOD]->(c) "
            "RETURN DISTINCT c.signature AS signature, c.id AS ref, t.id AS type_id, m.package AS package ORDER BY c.signature",
            pre=self._scope_prefix,
        )
        cands = [
            CallableCandidate(
                r["signature"],
                r["type_id"],
                self._path_of(r["ref"]),
                match_names=callable_match_names(r["signature"]),
                module_names=(r["package"],) if r["package"] else (),
            )
            for r in rows
            if r["signature"]
        ]
        for sig in self._external_signatures:
            cands.append(CallableCandidate(sig, None, "", match_names=callable_match_names(sig), module_names=()))
        return cands

    def _path_of(self, node_id: str) -> str:
        """The module file key embedded in ``node_id``, or ``""`` for a stand-in (which has none)."""
        try:
            return self._module_key(node_id)
        except ValueError:
            return ""

    def _module_key(self, node_id: str) -> str:
        """The repo-relative ``.go`` path a node id sits under — the symbol-table key.

        A Go node id is ``can://<ns>/<app>/go/<file>/<decl path>``, so the file key is everything
        between the language segment and the first declaration segment. A stand-in's id holds
        ``@external`` there instead, which is not a path, so this raises and callers substitute
        ``""`` rather than carrying a fictional file.
        """
        prefix = self._scope_prefix + "go/"
        if not node_id.startswith(prefix):
            raise ValueError(f"{node_id!r} is not under this Go application")
        rest = node_id[len(prefix) :]
        head = rest.split("/", 1)[0]
        if head.startswith("@"):
            raise ValueError(f"{node_id!r} is a stand-in id and names no module")
        # The file key runs to the last path segment ending in a source suffix.
        parts = rest.split("/")
        for i, seg in enumerate(parts):
            if seg.endswith(".go"):
                return "/".join(parts[: i + 1])
        raise ValueError(f"{node_id!r} carries no module path")

    #: A **callable** stand-in, told apart from an import ghost by carrying a ``signature``.
    #:
    #: ``:GoExternal`` is two things on this graph. A *callable* stand-in is a library function the
    #: analyzer synthesized a body for (``@external/database/sql/DB.Query``, 35 of them on the
    #: dataflow fixture); an *import ghost* is a package an unresolved import named
    #: (``@external/bytes``, 16 more), reached only by ``GO_IMPORTS`` and carrying neither a
    #: ``signature`` nor a ``module``. Only the first is a callable, and ``get_external_symbols``
    #: reported 51 against the local backend's 35 until this predicate separated them — measured
    #: against the live graph. Keying on ``signature`` rather than on the id's shape is deliberate:
    #: both live under ``@external/``, so the id cannot tell them apart.
    _IS_STANDIN = "e.signature IS NOT NULL"

    @cached_property
    def _external_signatures(self) -> Dict[str, str]:
        """``{reported signature: can:// id}`` for every callable stand-in, cached."""
        rows = self._run(
            f"MATCH (e:GoExternal) WHERE e.id STARTS WITH $pre AND {self._IS_STANDIN} "
            "RETURN e.id AS id, e.module AS module, e.signature AS signature, e.name AS name ORDER BY e.id",
            pre=self._scope_prefix,
        )
        return {external_signature(r["module"] or "", r["signature"], r["name"] or ""): r["id"] for r in rows}

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """The one callable ``name`` names (see :meth:`GoAnalysisBackend.resolve_callable`).

        The shared policy runs over the same unfiltered domain the local backend hands it, which is
        the strongest form of "both backends resolve over the same set"; the Go receiver spellings
        come from the shared :func:`~cldk.analysis.go.ids.callable_match_names`.

        A signature that names several declarations (same-package multi-file ``init()``) is
        narrowed by :func:`~cldk.analysis.go.ids.pick_declaration`, the same function and the same
        keywords the local backend applies. Re-looking-up on the bare signature and refusing on
        ``len(rows) > 1`` — what this did before — threw away the narrowing the policy had just
        done, so ``in_module=`` could not disambiguate even when it had matched.
        """
        sig = resolve_callable_signature(name, self._callable_candidates, in_class=in_class, in_module=in_module, by_full_name="more of the import path")
        if sig in self._external_signatures:
            ref = self._external_signatures[sig]
            return SliceNode(file="", line=0, callable=sig, kind="external", name=sig.rpartition(".")[2], source=None, ref=ref)
        rows = self._run(
            "MATCH (c:GoCallable {signature:$sig}) WHERE c.id STARTS WITH $pre "
            "OPTIONAL MATCH (t:GoType)-[:GO_HAS_METHOD]->(c) "
            "RETURN c.id AS ref, c.span_json AS span, t.id AS type_id ORDER BY c.id",
            sig=sig,
            pre=self._scope_prefix,
        )
        if not rows:
            raise KeyError(f"{sig!r} resolved but no :GoCallable node carries it in application {self.application_name!r}")
        chosen = pick_declaration(sig, [(self._path_of(r["ref"]), r["type_id"]) for r in rows], in_class=in_class, in_module=in_module)
        row = next(r for r in rows if self._path_of(r["ref"]) == chosen)
        span = json.loads(row["span"]) if row["span"] else None
        return SliceNode(
            file=chosen,
            line=span["start"][0] if span else 0,
            callable=sig,
            kind="callable",
            name=sig.rpartition(".")[2],
            source=None,
            ref=row["ref"],
        )

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """The one value of ``within`` that ``name`` names (see
        :meth:`GoAnalysisBackend.resolve_value`).

        Two round trips, not one: the callable is resolved first, so an ambiguous ``within`` raises
        naming *callables* rather than failing obscurely on a value search over a set of them —
        through :func:`~cldk.analysis.commons.resolve.resolve_within`, so the advice it gives names
        a keyword this method accepts.

        The ports are matched by the owner's **id**, not its signature: the owner may be a stand-in,
        which carries no signature, and the id is what ``resolve_callable`` already returned. Each
        port's ``b.var`` (the JSON's ``of``) is given every Go spelling by
        :func:`~cldk.analysis.go.ids.go_value_names`, which is where the package-qualified form of a
        global comes from (trap 3) — and reading ``of`` rather than indexing ``parameters`` is what
        makes a method's receiver addressable (trap 2).
        """
        owner = resolve_within(self.resolve_callable, within)
        rows = self._run(
            f"MATCH (c:{self._OWNER} {{id:$id}})-[:GO_HAS_BODY_NODE]->(b:GoBodyNode {{kind:'formal_in'}}) RETURN b.var AS var, b.id AS id ORDER BY b.id",
            id=owner.ref,
        )
        # A list, not a dict keyed by name: two values resolving to one name are a genuine ambiguity
        # the policy must see, and a dict would silently keep the last row.
        entries = [(r["id"], r["var"], nm) for r in rows if r["var"] for nm in go_value_names(r["var"])]
        chosen = resolve_value_name(name, [nm for _i, _v, nm in entries], within=owner.callable)
        node_id, var, _nm = next((i, v, nm) for i, v, nm in entries if nm == chosen)
        kind, leaf, defined_in = body_node_kind("formal_in", var)
        return SliceNode(file=owner.file, line=owner.line, callable=owner.callable, kind=kind, name=leaf, defined_in=defined_in, source=None, ref=node_id)

    # -----[ per-callable graphs ]-----
    #: One owner's own ``rel`` edges. The second containment hop is a **pattern predicate**, not a
    #: second pattern: written as a second match it would bind the same ``GO_HAS_BODY_NODE``
    #: relationship twice whenever ``s`` and ``d`` are the same node, and Cypher's
    #: relationship-uniqueness rule would then drop the row — which is exactly the shape of a
    #: data-dependence self-loop (a statement that reads a variable it also redefines). Losing those
    #: silently, and counting ``total`` from the same match, is how a short page reports itself
    #: complete (the Python-side bug #349, not repeated here).
    _OWN_EDGES = (
        "MATCH (c:{owner}) WHERE c.id STARTS WITH $prefix AND c.signature = $sig "
        "MATCH (c)-[:GO_HAS_BODY_NODE]->(s:GoBodyNode)-[r:{rel}]->(d:GoBodyNode) "
        "WHERE (c)-[:GO_HAS_BODY_NODE]->(d) "
    )

    def _own_edges(self, name: str, in_class: str | None, rel: str, projection: str, order: EdgeOrder, page_size: int, cursor: str | None):
        """One page of an owner's own ``rel`` edges: ``(signature, rows, whole size, is there more)``.

        ``page_size`` is validated **first**, before resolution, so a malformed argument is a
        ``ValueError`` before any round trip — the order the local backend applies, so the two
        cannot answer the same bad call with different exceptions.

        **Keyset, not ``SKIP``**: ``order.exprs`` is the canonical order written as Cypher (the same
        components ``order.key`` produces in Python, held side by side on
        :class:`~cldk.analysis.commons.bounds.EdgeOrder` so they cannot drift), and a cursor becomes
        a ``WHERE`` filter rather than an offset, so a deep page costs what a shallow one does.

        The page asks for ``page_size + 1`` rows and reports ``more`` from whether it got them, so
        "there is more" is a fact about the data rather than an inference from ``len(rows) ==
        page_size`` — which is wrong exactly when the set ends on a page boundary.
        """
        check_page_size(page_size)
        sig = self.resolve_callable(name, in_class=in_class).callable
        match = self._OWN_EDGES.format(owner=self._OWNER, rel=rel)
        params: Dict[str, Any] = {"prefix": self._scope_prefix, "sig": sig}
        total = self._run(match + "RETURN count(r) AS total", **params)[0]["total"]
        where = f"WHERE {keyset_where(order.exprs)} " if cursor is not None else ""
        rows = self._run(
            f"{match}WITH s.id AS src, d.id AS dst{projection} {where}RETURN * ORDER BY {', '.join(order.exprs)} LIMIT $lim",
            lim=page_size + 1,
            **params,
            **(cursor_params(cursor, sig, len(order.exprs)) if cursor is not None else {}),
        )
        return sig, rows[:page_size], total, len(rows) > page_size

    @staticmethod
    def _page(model, scope: str, edges: List, order: EdgeOrder, total: int, more: bool) -> EdgePage:
        """Wrap a page, deriving ``next_cursor`` from the *same* sort key the local backend uses —
        so a cursor minted here and one minted there name the same position."""
        return EdgePage[model](edges=edges, total=total, next_cursor=encode_cursor(scope, order.key(edges[-1])) if more and edges else None)

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCfgEdge]:
        """One page of control flow within one callable (see :meth:`GoAnalysisBackend.get_cfg`).

        The relationship is ``GO_CFG_NEXT``, not ``GO_CFG``: the Go projector names the control-flow
        edge for the direction it points (``internal/neo4j/schema.go``'s ``RelCFGNext``), while the
        four dependence relationships keep the bare ``sdg_rels`` spelling.
        """
        sig, rows, total, more = self._own_edges(callable, in_class, "GO_CFG_NEXT", ", r.kind AS kind", CFG_ORDER, page_size, cursor)
        return self._page(GoCfgEdge, sig, [GoCfgEdge(src=r["src"], dst=r["dst"], kind=r["kind"] or "fallthrough") for r in rows], CFG_ORDER, total, more)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCdgEdge]:
        """One page of control dependence within one callable (see :meth:`GoAnalysisBackend.get_cdg`)."""
        sig, rows, total, more = self._own_edges(callable, in_class, "GO_CDG", "", CDG_ORDER, page_size, cursor)
        return self._page(GoCdgEdge, sig, [GoCdgEdge(src=r["src"], dst=r["dst"]) for r in rows], CDG_ORDER, total, more)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoDdgEdge]:
        """One page of data dependence within one callable (see :meth:`GoAnalysisBackend.get_ddg`).

        ``prov`` is projected pruned, so it is absent rather than null on an edge carrying none;
        ``or []`` restores the model's default instead of failing validation, and the ``coalesce``
        in :data:`~cldk.analysis.go.backend.DDG_ORDER` does the same for the ordering.
        """
        sig, rows, total, more = self._own_edges(callable, in_class, "GO_DDG", ", r.var AS var, r.prov AS prov", DDG_ORDER, page_size, cursor)
        edges = [GoDdgEdge(src=r["src"], dst=r["dst"], var=r["var"], prov=list(r["prov"] or [])) for r in rows]
        return self._page(GoDdgEdge, sig, edges, DDG_ORDER, total, more)

    # -----[ slicing and reachability ]-----
    #: Variable-length Cypher, for the measured reason the Python backend records: Neo4j plans
    #: ``(a)<-[:R*0..]-(b) RETURN DISTINCT b`` as a pruning breadth-first expansion, while driving
    #: the same search one frontier at a time from Python is an order of magnitude slower and an
    #: ``EXISTS`` subquery does not terminate at all (it enumerates trails instead of pruning).
    #:
    #: ``total`` and the page come back from one statement: the ids are collected in id order,
    #: ``size()`` gives the whole slice's size, and only the first ``$cap`` are joined back to their
    #: owners for hydration — collecting id *strings* rather than nodes is what keeps a large cone
    #: inside the transaction's limits.
    #:
    #: The owner hop is ``OPTIONAL``: at L4 a reached vertex may belong to a stand-in, and an inner
    #: join on ``:GoCallable`` would drop exactly the external hops that make an interprocedural
    #: flow visible.
    _SLICE = (
        "MATCH (r:GoBodyNode {{id:$id}}){left}[:{rels}*0..{depth}]{right}(m:GoBodyNode) "
        "WITH DISTINCT m.id AS nid ORDER BY nid "
        "WITH collect(nid) AS ids "
        "WITH size(ids) AS total, ids[0..$cap] AS page "
        "UNWIND page AS nid "
        "MATCH (b:GoBodyNode {{id:nid}}) "
        "OPTIONAL MATCH (c:{owner})-[:GO_HAS_BODY_NODE]->(b) "
        "RETURN total, b.id AS ref, b.kind AS kind, b.var AS var, b.span_json AS span, "
        "c.signature AS callable, c.span_json AS c_span"
    )

    def _slice(self, src: str, within: str, depth: int | None, max_nodes: int, *, backward: bool) -> Slice:
        """One direction of :meth:`slice_backward` / :meth:`slice_forward`.

        The two differ only in which way the arrows point, so they share a query and a builder — a
        second copy would be a second place for the node vocabulary to drift.

        Not scoped by the application prefix: a body-node id is stamped with its application, and
        the projector only links nodes from its own run, so the traversal cannot leave the
        application it started in. The seed is app-scoped by :meth:`resolve_value`.
        """
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_connected_ports("slice_backward" if backward else "slice_forward")
        root = self.resolve_value(src, within=within)
        query = self._SLICE.format(
            owner=self._OWNER,
            rels=SDG_REL_PATTERN,
            depth="" if depth is None else depth,
            left="<-" if backward else "-",
            right="-" if backward else "->",
        )
        rows = self._run(query, id=root.ref, cap=max_nodes)
        nodes = [_slice_node(self._span_lines(r), self._module_key) for r in rows]
        return Slice(nodes=nodes, roots=[root], resolved=slice_resolved([root]), total=rows[0]["total"] if rows else 0)

    @staticmethod
    def _span_lines(row: Dict[str, Any]) -> Dict[str, Any]:
        """``span_json``/``c_span`` turned into the ``line``/``c_line`` the shared row shape wants.

        The Go projector carries a span as one JSON string (a Bolt property stays scalar) where the
        Python one carries ``start_line`` directly, so the start line is decoded here rather than in
        :func:`_slice_node` — which is then the *same* function for both backends' row shapes.
        """
        out = dict(row)
        out["line"] = json.loads(row["span"])["start"][0] if row.get("span") else None
        out["c_line"] = json.loads(row["c_span"])["start"][0] if row.get("c_span") else 0
        return out

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What affects this value (see :meth:`GoAnalysisBackend.slice_backward`)."""
        return self._slice(src, within, depth, max_nodes, backward=True)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What this value affects (see :meth:`GoAnalysisBackend.slice_forward`)."""
        return self._slice(src, within, depth, max_nodes, backward=False)

    #: **The interior is pinned to ``:GoCallable``; the endpoints are not.** Every *source* of a
    #: hop is a declared callable and prefix-scoped, which is exactly ``get_call_graph``'s edge set
    #: — so a route cannot be manufactured through a node this application does not own. But the
    #: walk's *end* may be a library stand-in, and must be: ``reaches("…closure_1", "DB.Query")``
    #: is the coarse form of the question the httpsql fixture gates, and pinning the endpoint to
    #: ``:GoCallable`` answered ``False`` for a call that plainly exists (measured on the live
    #: graph, where 69 of 247 ``GO_CALLS`` edges land on a stand-in).
    #:
    #: Admitting the endpoint cannot widen the interior, and that is a fact about the data rather
    #: than a hope: **no stand-in has an outgoing ``GO_CALLS`` edge** — 0 of 247 on the live graph,
    #: and 0 of 247 in the JSON the local backend reads, so the two agree. A stand-in is only ever
    #: a terminus, so it can appear as ``(m)`` and never between two hops. Quantified path patterns
    #: need Neo4j 5.9+.
    _REACHES = (
        "MATCH (a:GoCallable {{id:$a}}) "
        "MATCH (a) ((x:GoCallable)-[:GO_CALLS]->(y) WHERE x.id STARTS WITH $prefix){{1,{depth}}} (m) "
        "WITH DISTINCT m WHERE m.id = $b RETURN count(m) > 0 AS ok"
    )

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path (see :meth:`GoAnalysisBackend.reaches`)?

        Keyed by callable **id**, as the Go call graph is — so this needs no signature round trip,
        and a stand-in endpoint (which has no signature) is addressable like any other.
        """
        check_depth(depth)
        a = self.resolve_callable(src).ref
        b = self.resolve_callable(dst).ref
        rows = self._run(self._REACHES.format(depth="" if depth is None else depth), a=a, b=b, prefix=self._scope_prefix)
        return bool(rows and rows[0]["ok"])

    #: ``{0,}``, so a sink with no callers is its own cone rather than an empty answer a caller
    #: could not tell from "this name is wrong" (D7). Properties are projected into maps *before*
    #: the cap, so only ``$cap`` of them cross the wire.
    _CONE = (
        "MATCH (s:GoCallable) WHERE s.id IN $ids "
        "MATCH (s) (()<-[:GO_CALLS]-(x:GoCallable) WHERE x.id STARTS WITH $prefix){{0,{depth}}} (m:GoCallable) "
        "WITH DISTINCT m ORDER BY m.id "
        "WITH collect({{signature: m.signature, name: m.signature, ref: m.id, span: m.span_json}}) AS found "
        "RETURN size(found) AS total, found[0..$cap] AS page"
    )

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything that can reach these sinks (see :meth:`GoAnalysisBackend.backward_cone`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        roots = cone_sinks(self.resolve_callable, sinks)
        row = self._run(
            self._CONE.format(depth="" if depth is None else depth),
            ids=[r.ref for r in roots],
            cap=max_nodes,
            prefix=self._scope_prefix,
        )[0]
        nodes = [
            SliceNode(
                file=self._path_of(n["ref"]),
                line=json.loads(n["span"])["start"][0] if n["span"] else 0,
                callable=n["signature"],
                kind="callable",
                name=(n["signature"] or "").rpartition(".")[2],
                source=None,
                ref=n["ref"],
            )
            for n in row["page"]
        ]
        return Slice(nodes=nodes, roots=roots, resolved=slice_resolved(roots), total=row["total"])

    # -----[ paths and the flow predicates ]-----
    #: ``allShortestPaths`` and not a plain variable-length match: a variable-length pattern
    #: enumerates *trails*, which does not terminate on a real dependence graph, while
    #: ``allShortestPaths`` is a bidirectional breadth-first search. ``$cap`` is ``max_paths + 1``,
    #: so one extra row reports truncation without a second counting traversal.
    _PATHS = sdg_path_query(
        "GO",
        node_label="GoBodyNode",
        projection="ref: n.id, kind: n.kind, var: n.var, span: n.span_json, "
        "callable: head([(c:GoCallable|GoExternal)-[:GO_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_span: head([(c:GoCallable|GoExternal)-[:GO_HAS_BODY_NODE]->(n) | c.span_json])",
    )

    #: ``taint()``'s statement: the same shortest-path search as :attr:`_PATHS`, but m sources
    #: against n sinks in one traversal, with the sanitizer cut **inside** the pattern and the cap
    #: applied per pair. Sharing ``node_label`` and the projection verbatim is what makes a taint
    #: witness and a ``paths_between`` witness describe a node identically.
    _TAINT = sdg_taint_query(
        "GO",
        node_label="GoBodyNode",
        projection="ref: n.id, kind: n.kind, var: n.var, span: n.span_json, "
        "callable: head([(c:GoCallable|GoExternal)-[:GO_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_span: head([(c:GoCallable|GoExternal)-[:GO_HAS_BODY_NODE]->(n) | c.span_json])",
    )

    #: The variable names on SDG edges *leaving* a node inside ``$callable_prefix`` — ``startNode``,
    #: the same end of the hop :attr:`_TAINT`'s cut predicate reads, so a sanitizer this validates
    #: is one that predicate can actually match. **Delimited**, not a bare ``STARTS WITH``: the cut
    #: it validates for is delimited, so a variable reachable only through an undelimited prefix (a
    #: sibling callable whose id merely starts with this one — in Go, a closure of a same-named
    #: outer function) would be accepted here and then sever nothing there.
    _EDGE_VARS = (
        "MATCH (n:GoBodyNode)-[r:{rels}]->() "
        "WHERE (n.id = $callable_prefix OR n.id STARTS WITH $callable_prefix + '@' OR n.id STARTS WITH $callable_prefix + '/') "
        "RETURN collect(DISTINCT r.var) AS vars"
    )

    def _paths(self, query: str, node_of, a: SliceNode, b: SliceNode, *, src: str, dst: str, depth: int | None, max_paths: int) -> FlowPaths:
        """Run one of the path queries and build the result. The ordering, the cap and the
        completeness flag live here once."""
        check_distinct_endpoints(a, b)
        rows = self._run(query.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth), src=src, dst=dst, cap=max_paths + 1, prefix=self._scope_prefix)
        paths = [
            flow_path([node_of(self._span_lines(n), self._module_key) for n in r["ns"]], [(e["via"], e["var"], e["prov"]) for e in r["rs"]], via=VIA)
            for r in rows[:max_paths]
        ]
        return FlowPaths(paths=paths, complete=len(rows) <= max_paths)

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value (see :meth:`GoAnalysisBackend.paths_between`)."""
        check_depth(depth)
        check_max_paths(max_paths)
        self._require_connected_ports("paths_between")
        a = self.resolve_value(src, within=src_within)
        b = self.resolve_value(dst, within=dst_within)
        return self._paths(self._PATHS, _slice_node, a, b, src=a.ref, dst=b.ref, depth=depth, max_paths=max_paths)

    #: ``WITH DISTINCT m`` before the membership test, so this plans as a pruning breadth-first
    #: search rather than a trail enumeration. Not prefix-scoped: body-node ids embed the
    #: application, so both the seed and every ``$dsts`` id are this application's by construction.
    _VALUE_REACHES = "MATCH (a:GoBodyNode {{id:$src}})-[:{rels}*1..{depth}]->(m:GoBodyNode) WITH DISTINCT m WHERE m.id IN $dsts RETURN count(m) > 0 AS ok"

    #: Every value that *enters* an owner — its parameters, its receiver, and the globals and
    #: captures it reads. Keyed by the owner's id, because a stand-in carries no signature.
    _CALLEE_PORTS = f"MATCH (c:{_OWNER} {{id:$id}})-[:GO_HAS_BODY_NODE]->(b:GoBodyNode {{kind:'formal_in'}}) RETURN collect(b.id) AS ids"

    def _value_reaches(self, src: str, dsts: List[str], depth: int | None) -> bool:
        """Does the value at ``src`` reach any of ``dsts``? The one predicate both flow questions
        run, which is what makes ``flows_to_argument`` ⟹ ``flows_to_call`` a fact about their
        *targets* rather than an agreement between two pieces of Cypher."""
        if not dsts:
            return False
        rows = self._run(self._VALUE_REACHES.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth), src=src, dsts=dsts)
        return bool(rows and rows[0]["ok"])

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach any value entering a call to ``callee``
        (see :meth:`GoAnalysisBackend.flows_to_call`)?"""
        check_depth(depth)
        self._require_connected_ports("flows_to_call")
        root = self.resolve_value(src, within=within)
        target = self.resolve_callable(callee).ref
        ids = [i for i in self._run(self._CALLEE_PORTS, id=target)[0]["ids"] if i != root.ref]
        return self._value_reaches(root.ref, ids, depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach ``callee``'s ``arg``
        (see :meth:`GoAnalysisBackend.flows_to_argument`)?"""
        check_depth(depth)
        self._require_connected_ports("flows_to_argument")
        root = self.resolve_value(src, within=within)
        target = self.resolve_value(arg, within=callee).ref
        return target != root.ref and self._value_reaches(root.ref, [target], depth)

    def _taint_walk(
        self,
        srcs: Sequence[SliceNode],
        dsts: Sequence[SliceNode],
        *,
        cuts: List[Dict[str, str]],
        cut_callables: List[str],
        depth: int | None,
        max_paths: int,
    ) -> Tuple[List[Tuple[str, str, FlowPath]], Mapping[Tuple[str, str], List[Diagnostic]]]:
        """The sanitized shortest walks, server-side (see :meth:`GoAnalysisBackend._taint_walk`).

        One statement for the whole batch, one row per witness — ``a.id AS src`` / ``b.id AS dst``
        carry the pairing back, because the m×n batching is only useful if the grouping survives it.
        Ordering, the per-pair ``$cap`` and both cuts are the statement's, so nothing is re-sorted or
        re-filtered here: a Python-side filter is the false-refutation bug
        :func:`~cldk.analysis.commons.graphs.sdg_taint_query` exists to avoid, and a Python-side
        sort would silently disagree with :func:`~cldk.analysis.commons.graphs.path_order`.

        The ledger comes back empty here as it does locally: Go's honesty signal is the
        application-scope ``degraded[]``, which :meth:`taint` reads and files per pair. There is no
        per-frontier signal to add, and inventing one from an unresolved call would void every
        refutation in every application that calls a library.
        """
        rows = self._run(
            self._TAINT.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth),
            srcs=[n.ref for n in srcs],
            dsts=[n.ref for n in dsts],
            cuts=cuts,
            cut_callables=cut_callables,
            cap=max_paths + 1,
            prefix=self._scope_prefix,
        )
        return [
            (
                r["src"],
                r["dst"],
                flow_path([_slice_node(self._span_lines(n), self._module_key) for n in r["ns"]], [(e["via"], e["var"], e["prov"]) for e in r["rs"]], via=VIA),
            )
            for r in rows
        ], {}

    def _edge_vars_in(self, callable_id: str) -> FrozenSet[str]:
        """The edge variables scoped to this callable (see :meth:`GoAnalysisBackend._edge_vars_in`).

        Four of the five relationship types carry no ``var``, so the collected list holds ``None``;
        those are dropped, because a membership test against a set holding ``None`` would answer a
        question no caller can ask — ``resolve_sanitizers`` refuses a blank variable before it asks.
        """
        rows = self._run(self._EDGE_VARS.format(rels=SDG_REL_PATTERN), callable_prefix=callable_id)
        return frozenset(v for v in rows[0]["vars"] if v)

    # -----[ the honesty signals + hydration ]-----
    def get_external_symbols(self) -> Dict[str, GoExternalSymbol]:
        """The library stand-ins this graph carries (see
        :meth:`GoAnalysisBackend.get_external_symbols`).

        Filtered to the **callable** stand-ins by :data:`_IS_STANDIN`: the label is shared with the
        import ghosts an unresolved import mints, which are packages rather than callables and which
        ``analysis.json`` does not carry under ``external_symbols`` at all.
        """
        rows = self._run(
            f"MATCH (e:GoExternal) WHERE e.id STARTS WITH $pre AND {self._IS_STANDIN} RETURN properties(e) AS p ORDER BY e.id",
            pre=self._scope_prefix,
        )
        return {r["p"]["id"]: R.external(r["p"]) for r in rows}

    def get_degraded(self) -> List[GoDegraded]:
        """This graph's degradation markers (see :meth:`GoAnalysisBackend.get_degraded`).

        Off ``:GoApplication.degraded_json`` — the envelope-scope ``degraded`` list, carried as one
        JSON string because a Bolt property stays scalar. A clean lazy push *clears* the property
        rather than leaving a stale one, so an absent value is a positive "nothing degraded".
        """
        rows = self._run("MATCH (a:GoApplication {id: $app_id}) RETURN a.degraded_json AS d", app_id=self._app_id)
        return R.degraded(rows[0]["d"] if rows else None)

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in ``source`` for these positions (see :meth:`GoAnalysisBackend.describe`).

        One query for the batch rather than one per node. A vertex with no span gets ``None``: it is
        a dataflow position, not a region of the file, and there is nothing to read on either
        backend.
        """
        out: List[SliceNode] = []
        for n in nodes:
            if not isinstance(n, SliceNode):
                continue
            try:
                out.append(n.model_copy(update={"source": self.get_source(n.ref)}))
            except CodeanalyzerExecutionException:
                out.append(n.model_copy(update={"source": None}))
        return out

    # -----[ artifact / dependency / config layer — reserved-but-unemitted on this projection ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))
