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
from typing import Any, Dict, List

import networkx as nx

from cldk.analysis.go.backend import ARTIFACT_LAYER_UNAVAILABLE, GoAnalysisBackend
from cldk.analysis.go.neo4j import reconstruct as R
from cldk.models.go import GoApplication, GoCallable, GoCallGraphEdge, GoField, GoModule, GoParam, GoType
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

#: Relationship types a Go graph must carry to be a codeanalyzer-go projection at all.
_REQUIRED_RELS = {"GO_HAS_MODULE", "GO_DECLARES", "GO_HAS_METHOD", "GO_CALLS"}


class GoNeo4jBackend(GoAnalysisBackend):
    """Answer the Go query surface over a ``GO_``-prefixed Neo4j graph (see the module docstring)."""

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_username: str,
        neo4j_password: str,
        neo4j_database: str | None = None,
        application_name: str | None = None,
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException(
                "The Neo4j backend requires the 'neo4j' driver. Install it with `pip install neo4j` "
                "(or `pip install cldk[neo4j]`)."
            ) from e
        self._init_with_driver(
            GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password)),
            application_name=application_name,
            neo4j_database=neo4j_database,
        )

    @classmethod
    def _from_driver(cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> "GoNeo4jBackend":
        """Construct from an already-built driver — the seam tests inject a fake/real driver here."""
        self = cls.__new__(cls)
        self._init_with_driver(driver, application_name=application_name, neo4j_database=neo4j_database)
        return self

    def _init_with_driver(self, driver: Any, *, application_name: str | None, neo4j_database: str | None) -> None:
        if not application_name:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        self.application_name = application_name
        self._database = neo4j_database
        self._driver = driver
        self._session_obj: Any | None = None
        self._call_graph: nx.DiGraph | None = None
        self._probe_schema()

    # -----[ scope + execution ]-----
    @property
    def _app_id(self) -> str:
        """``can://<app>`` — the ``:GoApplication`` anchor id and the root of every id below it."""
        return f"can://{self.application_name}"

    @property
    def _scope_prefix(self) -> str:
        """``can://<app>/`` — the trailing slash keeps ``app`` from matching ``app-b``."""
        return f"{self._app_id}/"

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run one read statement over a reused session; drop the session on failure."""
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

    def _probe_schema(self) -> None:
        """Refuse a graph that is not a codeanalyzer-go projection or holds no node for this app."""
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        missing = _REQUIRED_RELS - found
        if missing:
            raise CodeanalyzerExecutionException(
                f"the graph is not a codeanalyzer-go projection: missing relationship types {sorted(missing)}"
            )
        rows = self._run("OPTIONAL MATCH (a:GoApplication {id: $app_id}) RETURN count(a) AS n", app_id=self._app_id)
        if not (rows and rows[0].get("n")):
            raise CodeanalyzerExecutionException(
                f"the graph holds no :GoApplication node for {self.application_name!r} (id {self._app_id!r})"
            )

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> GoApplication:
        """The whole application view. Whole-program accessors touch everything by nature; this
        builds the symbol table from the containment relationships and the call graph from
        ``GO_CALLS``, cached."""
        return self._application

    @cached_property
    def _application(self) -> GoApplication:
        return GoApplication(id=self._app_id, symbol_table=self._build_symbol_table(), call_graph=self._call_edges())

    def get_symbol_table(self) -> Dict[str, GoModule]:
        return self.get_application_view().symbol_table

    def _module_file_key(self, module_id: str) -> str:
        """``can://<app>/<file>`` → ``<file>`` (the symbol-table key the local backend uses)."""
        return module_id[len(self._scope_prefix):]

    def _build_symbol_table(self) -> Dict[str, GoModule]:
        """Rebuild every module with its types, functions, methods and fields from the containment
        relationships. This is the expensive whole-program path; point accessors below never call
        it."""
        modules: Dict[str, GoModule] = {}
        mod_rows = self._run(
            "MATCH (:GoApplication {id: $app_id})-[:GO_HAS_MODULE]->(m:GoModule) RETURN properties(m) AS p ORDER BY m.id",
            app_id=self._app_id,
        )
        for mr in mod_rows:
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
            "MATCH (a:GoCallable)-[r:GO_CALLS]->(b) WHERE a.id STARTS WITH $pre "
            "RETURN a.id AS src, b.id AS dst, properties(r) AS p ORDER BY a.id, b.id",
            pre=self._scope_prefix,
        )
        return [
            GoCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["p"].get("prov") or []), weight=r["p"].get("weight", 1))
            for r in rows
        ]

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
            "MATCH (m:GoModule)-[:GO_DECLARES]->(c:GoCallable) WHERE m.id STARTS WITH $pre "
            "RETURN properties(c) AS p ORDER BY c.signature",
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
            raise CodeanalyzerExecutionException(
                f"no node {node_id!r} in the graph for application {self.application_name!r}"
            )
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

    # -----[ artifact / dependency / config layer — reserved-but-unemitted on this projection ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))
