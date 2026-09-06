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

"""Neo4j-backed TypeScript analysis backend (read-only Cypher client) on the codeanalyzer-typescript
1.2.0 graph vocabulary.

A drop-in alternative to :class:`TSCodeanalyzer`: the same query surface, every method answered by
Cypher over a live graph that ``codeanalyzer-typescript --emit neo4j`` populated out of band. This
class never writes and needs neither the analyzer binary nor the sources.

**The graph it reads** (``schema.neo4j.json`` at the 1.2.0 tag; ``main`` renames nothing):
``:Application {id: can://typescript/<app>}`` anchors the application and stamps
``analyzer_version``; every project node carries a ``can://`` ``id`` under the merge label
``CanNode`` -- ``TSModule`` (``name`` holds the file key), ``TSClass``/``TSInterface``/``TSEnum``/
``TSTypeAlias``/``TSNamespace``, ``TSCallable`` (all seven kinds; anonymous ones also
``TSAnonymousCallable``), ``TSField``, ``TSBodyNode``, ``TSExternal``; containment is
``TS_HAS_MODULE`` / ``TS_DECLARES`` / ``TS_HAS_METHOD`` / ``TS_HAS_FIELD``; calls are ``TS_CALLS
{weight, prov}`` and a call site is a ``TSBodyNode {kind:'call'}`` under ``TS_HAS_BODY_NODE``
resolving over ``TS_RESOLVES_TO``.

**Scope (TS-3).** A signature is not application-stamped, so every statement that could match
another application's node carries the two-prefix predicate :func:`_scoped` spells --
``can://typescript/<app>/`` and ``can://javascript/<app>/`` -- or is keyed by an id that embeds the
application, or walks out from the ``:Application`` anchor. There is no ``_module`` property to
fall back on (retired on ``main``, #166).

**Seek labels (measured on the superset graph).** Signature and prefix statements anchor on the
specific label alone (``:TSCallable``): 11,085 callables scan in ~8 ms, and ``:CanNode`` turns the
two-prefix predicate into a slower range-seek union. Id-equality point lookups anchor on
``:CanNode:<Label>``: the ``CanNode.id`` uniqueness constraint makes them a 1.5 ms unique-index
seek instead of a label scan.

**Round trips.** A declaration's whole containment subtree is fetched in one statement
(``_SUBTREE``: a variable-length walk over the containment types from the anchored roots), so a
bulk accessor pays one root fetch plus one subtree fetch however many modules, classes and
callables it walks -- never one statement per parent.

**Lossiness** relative to the in-memory backend (the projection's, not this client's; see
:mod:`reconstruct` for the per-node detail): parameters, comments, type parameters, overloads,
bodies and the L3/L4 graphs are not on ``:TSCallable``; enum member values, imports and exports
are not projected at all; call sites keep lines and the resolved callee only; the anonymous-callable
index is keyed by the tree node's own id rather than the analyzer's older compatibility key;
``config_reads`` are not projected (see :meth:`get_unresolved_config_reads`); an unresolved
call site contributes ``""`` to :meth:`get_call_targets` where the in-memory backend contributes
the call's ``method_name``. One more is the emitter's: two declarations of one name (TypeScript
declaration merging -- ``const X = …`` + ``interface X``, ``const X = …`` + ``type X``, ``type X`` +
a field ``X``) share one id, so ``MERGE`` collapses them onto one node carrying both labels and the
``kind`` of whichever was written last. Such a node is rebuilt as the facet the containment edge
declares (``TS_DECLARES`` names a type or callable; its labels say which) and the other facet's
members under it are lost; a node whose labels cannot name the facet is raised as the defect it is
(three merged nodes on the superset graph).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from functools import cached_property
from typing import Any, Dict, FrozenSet, List, Set, Tuple

import networkx as nx

from cldk.analysis.commons.keys import module_key_of
from cldk.analysis.python.neo4j import reconstruct as PyR  # the artifact layer is shared verbatim (commons/backend.py)
from cldk.analysis.python.neo4j.neo4j_backend import _semver
from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.neo4j import reconstruct as R
from cldk.analysis.typescript.neo4j.reconstruct import CALLABLE_KINDS, TYPE_KINDS, TYPE_LABEL_KINDS
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableOverview,
    TSCallGraphEdge,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, GraphSchemaMismatch

logger = logging.getLogger(__name__)


def _scoped(var: str) -> str:
    """The application-scope predicate for node variable ``var``, spelled once so it cannot drift:
    the two id prefixes as an ``OR`` (which plans as a seek union), never ``any(p IN $prefixes …)``
    (which plans as a label scan). Bound from :attr:`TSNeo4jBackend._scope_params`."""
    return f"({var}.id STARTS WITH $p1 OR {var}.id STARTS WITH $p2)"


#: The kinds a method owner may have -- alongside the label, so a declaration-merged node carrying
#: ``TSClass``/``TSInterface`` with another declaration's kind is not an owner.
_OWNER_KINDS = ["class", "interface"]

#: A child row of the containment subtree: (relationship type, child properties, edge properties).
_Child = Tuple[str, Dict[str, Any], Dict[str, Any]]


class TSNeo4jBackend(TSAnalysisBackend):
    """Query the application view of a TypeScript project over Neo4j (Cypher), read-only.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server.
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``--app-name`` the graph was emitted with; the anchor is
            ``:Application {id: can://typescript/<application_name>}`` on both namespaces.
    """

    #: Relationship types every supported graph has; a graph missing any was emitted by another
    #: generation (0.4.3 has none of them) and is refused at attach.
    _REQUIRED_RELATIONSHIP_TYPES: FrozenSet[str] = frozenset({"TS_HAS_MODULE", "TS_HAS_METHOD", "TS_HAS_BODY_NODE", "TS_CALLS"})
    #: The oldest codeanalyzer-typescript whose graph this backend serves: 1.2.0 introduced the
    #: ``can://`` id grammar and the body-node shape every statement here reads.
    _ANALYZER_FLOOR = (1, 2, 0)
    #: Set by :meth:`_probe_schema`; the class-level ``None`` is for the ``object.__new__`` seam.
    _analyzer_version: Tuple[int, int, int] | None = None
    _call_graph: nx.DiGraph | None = None
    _module_ids: Dict[str, str] = {}

    def __init__(self, neo4j_uri: str, neo4j_username: str, neo4j_password: str, neo4j_database: str | None = None, application_name: str | None = None) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with `pip install neo4j` (or `pip install cldk[neo4j]`).") from e
        self._init_with_driver(GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password)), application_name=application_name, neo4j_database=neo4j_database)

    @classmethod
    def _from_driver(cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> "TSNeo4jBackend":
        """Construct from an already-built driver -- the seam tests inject a fake driver through."""
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
        self._probe_schema()
        self._module_ids = self._load_module_keys()
        self._modules: List[str] = list(self._module_ids)
        self._call_graph = None

    # -----[ scope ]-----
    @property
    def _app_id(self) -> str:
        return f"can://typescript/{self.application_name}"

    @property
    def _scope_prefixes(self) -> List[str]:
        """``can://typescript/<app>/`` and ``can://javascript/<app>/`` (TS-3); the trailing slash
        keeps ``app`` from matching ``app-b``."""
        return [f"can://typescript/{self.application_name}/", f"can://javascript/{self.application_name}/"]

    @property
    def _scope_params(self) -> Dict[str, str]:
        """The parameters :func:`_scoped` binds."""
        p1, p2 = self._scope_prefixes
        return {"p1": p1, "p2": p2}

    @cached_property
    def _module_set(self) -> FrozenSet[str]:
        return frozenset(self._modules)

    def _module_key(self, node_id: str) -> str:
        """The repo-relative module key a node's id embeds, verified against the application's
        module keys (F4) -- never split, never guessed. A miss reloads the keys once (the graph is
        not ours; a re-emit may have added a module) and then raises."""
        for _ in range(2):
            for prefix in self._scope_prefixes:
                if node_id.startswith(prefix):
                    try:
                        return module_key_of(node_id, prefix, self._module_set)
                    except KeyError:
                        break
            self._module_ids = self._load_module_keys()
            self._modules = list(self._module_ids)
            self.__dict__.pop("_module_set", None)
        raise CodeanalyzerExecutionException(
            f"A node of application {self.application_name!r} belongs to none of the {len(self._module_set)} module keys the graph holds for it, even after reloading them. Re-attach to the graph."
        )

    # -----[ lifecycle ]-----
    def close(self) -> None:
        if self._session_obj is not None:
            try:
                self._session_obj.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._session_obj = None
        self._driver.close()

    def __enter__(self) -> "TSNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run one read statement over a reused session; drop the session on failure."""
        if self._session_obj is None:
            self._session_obj = self._driver.session(database=self._database)
        try:
            return [record.data() for record in self._session_obj.run(query, **params)]
        except Exception:
            self._session_obj = None
            raise

    # -----[ attach ]-----
    def _probe_schema(self) -> None:
        """G5: the relationship-type fingerprint, then the analyzer generation the ``:Application``
        anchor stamps against :attr:`_ANALYZER_FLOOR`. Below the floor, absent, or unreadable is
        refused naming what was found -- a served mismatch would read as "no callables"."""
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        missing = self._REQUIRED_RELATIONSHIP_TYPES - found
        if missing:
            raise GraphSchemaMismatch(expected=set(self._REQUIRED_RELATIONSHIP_TYPES), found=found, missing=missing)
        rows = self._run("OPTIONAL MATCH (a:Application {id: $app_id}) RETURN count(a) AS n, a.analyzer_version AS v", app_id=self._app_id)
        present = bool(rows and rows[0].get("n"))
        raw = rows[0].get("v") if rows else None
        version = _semver(raw)
        floor = ".".join(map(str, self._ANALYZER_FLOOR))
        if version is None or version < self._ANALYZER_FLOOR:
            if not present:
                what = "has no :Application node"
            elif version:
                what = f"was emitted by codeanalyzer-typescript {raw}"
            elif raw:
                what = f"reports analyzer_version {raw!r}"
            else:
                what = "has an :Application node that carries no analyzer_version"
            raise GraphSchemaMismatch(
                expected=set(self._REQUIRED_RELATIONSHIP_TYPES),
                found=found,
                missing=set(),
                message=f"The graph for application {self.application_name!r} {what}; this backend needs a graph emitted by codeanalyzer-typescript {floor} or newer.",
            )
        self._analyzer_version = version

    def _load_module_keys(self) -> Dict[str, str]:
        """``file key -> module id`` for the application's modules (``TSModule.name`` holds the key)."""
        rows = self._run("MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m.name AS k, m.id AS id", app_id=self._app_id)
        return {r["k"]: r["id"] for r in rows}

    # =====================================================================================
    # Containment subtree: one statement for the roots, one for everything beneath them.
    # =====================================================================================
    #: Appended to ``MATCH <anchor>`` where the anchor binds ``root``: every containment edge
    #: beneath every root, at any depth, plus the decorator edges, as ``(parent id, child)`` rows.
    _SUBTREE = (
        "MATCH (root)-[:TS_DECLARES|TS_HAS_METHOD|TS_HAS_FIELD*0..]->(par)-[r:TS_DECLARES|TS_HAS_METHOD|TS_HAS_FIELD|TS_DECORATED_BY]->(n) "
        "RETURN par.id AS pk, type(r) AS rel, properties(n) AS p, properties(r) AS e, labels(n) AS labels"
    )

    def _fetch(self, anchor: str, **params: Any) -> Tuple[List[Dict[str, Any]], Dict[str, List[_Child]]]:
        """The properties of every ``root`` the anchor pattern binds, and the containment subtree
        beneath them indexed by parent id. ``anchor`` is a scoped ``MATCH`` body binding ``root``."""
        roots = [{**r["p"], "_labels": r["labels"]} for r in self._run(f"MATCH {anchor} RETURN properties(root) AS p, labels(root) AS labels", **params)]
        children: Dict[str, List[_Child]] = defaultdict(list)
        for r in self._run(f"MATCH {anchor} " + self._SUBTREE, **params):
            children[r["pk"]].append((r["rel"], {**r["p"], "_labels": r["labels"]}, r["e"] or {}))
        return roots, children

    def _decorators(self, node_id: str, children: Dict[str, List[_Child]]) -> List[TSDecorator]:
        return [R.decorator(p, e) for rel, p, e in children.get(node_id, []) if rel == "TS_DECORATED_BY"]

    def _fields(self, node_id: str, children: Dict[str, List[_Child]]) -> Dict[str, TSClassAttribute]:
        rows = sorted((p for rel, p, _ in children.get(node_id, []) if rel == "TS_HAS_FIELD"), key=lambda p: (p.get("start_line") or 0, p.get("name") or ""))
        return {R.child_key(node_id, p): R.field(p, self._decorators(p["id"], children)) for p in rows}

    def _declared(self, node_id: str, children: Dict[str, List[_Child]]) -> Tuple[Dict[str, Any], Dict[str, TSCallable]]:
        """The ``TS_DECLARES`` children split into ``(types, callables)``, each keyed the analyzer's way."""
        types: Dict[str, Any] = {}
        callables: Dict[str, TSCallable] = {}
        for rel, p, _ in children.get(node_id, []):
            if rel != "TS_DECLARES":
                continue
            kind = p["kind"]
            if kind not in TYPE_KINDS and kind not in CALLABLE_KINDS:
                # A declaration-merged node (see the module docstring): TS_DECLARES says type or
                # callable; the labels must name exactly one type facet, else it is a defect.
                facets = [TYPE_LABEL_KINDS[l] for l in p.get("_labels", []) if l in TYPE_LABEL_KINDS]
                if len(facets) != 1:
                    raise CodeanalyzerExecutionException(
                        self._merged_defect(p, "is declared by its parent but its kind is neither a type nor a callable kind, and its labels do not name one type facet")
                    )
                kind = facets[0]
            if kind in TYPE_KINDS:
                types[R.child_key(node_id, p)] = self._type({**p, "kind": kind}, children)
            else:
                callables[R.child_key(node_id, p)] = self._callable(p, children)
        return types, callables

    @staticmethod
    def _merged_defect(props: Dict[str, Any], what: str) -> str:
        """The message for a node the emitter merged from two declarations. Names the node by
        signature/name, labels and kind -- never by its id (E6)."""
        return f"node {props.get('signature') or props.get('name')!r} (labels {sorted(props.get('_labels', []))}, kind {props.get('kind')!r}) {what}: codeanalyzer-typescript minted one id for two declarations"

    def _methods(self, node_id: str, children: Dict[str, List[_Child]]) -> Dict[str, TSCallable]:
        return {R.child_key(node_id, p): self._callable(p, children) for rel, p, _ in children.get(node_id, []) if rel == "TS_HAS_METHOD"}

    def _callable(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> TSCallable:
        types, callables = self._declared(props["id"], children)
        return R.callable_(props, decorators=self._decorators(props["id"], children), callables=callables, types=types)

    def _type(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> Any:
        nid, kind = props["id"], props.get("kind")
        if kind == "class":
            return R.class_(props, callables=self._methods(nid, children), fields=self._fields(nid, children), decorators=self._decorators(nid, children))
        if kind == "interface":
            return R.interface(props, callables=self._methods(nid, children), fields=self._fields(nid, children))
        if kind == "enum":
            return R.enum(props, fields=self._fields(nid, children))
        if kind == "type_alias":
            return R.type_alias(props)
        if kind == "namespace":
            types, functions = self._declared(nid, children)
            return R.namespace(props, types=types, functions=functions, fields=self._fields(nid, children))
        raise CodeanalyzerExecutionException(self._merged_defect(props, "is matched as a type but its kind is none of the five type kinds"))

    def _module(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> TSModule:
        types, functions = self._declared(props["id"], children)
        return R.module(props, types=types, functions=functions, fields=self._fields(props["id"], children))

    # The kind predicate alongside the label keeps a declaration-merged node (two labels, one kind)
    # out of the accessor for the facet it is not; ``cannode_kind`` is indexed on every generation.
    def _types_by_signature(self, label: str) -> Dict[str, Any]:
        roots, children = self._fetch(f"(root:{label}) WHERE {_scoped('root')} AND root.kind = $kind", kind=TYPE_LABEL_KINDS[label], **self._scope_params)
        return {p["signature"]: self._type(p, children) for p in roots}

    def _type_by_signature(self, label: str, signature: str) -> Any:
        roots, children = self._fetch(
            f"(root:{label} {{signature: $sig}}) WHERE {_scoped('root')} AND root.kind = $kind", sig=signature, kind=TYPE_LABEL_KINDS[label], **self._scope_params
        )
        return self._type(roots[0], children) if roots else None

    # =====================================================================================
    # application / whole-program
    # =====================================================================================
    def get_application_view(self) -> TSApplication:
        """The symbol table, the call graph as wire edges, the externals and the anonymous index.
        The artifact layer is answered by its own five accessors (shared ``Py*`` models) and is not
        rebuilt into the view; ``param_in``/``param_out`` (L4) are not rebuilt either."""
        externals = {e.id: e for e in self.get_external_symbols().values()}
        return TSApplication(
            id=self._app_id,
            symbol_table=self.get_symbol_table(),
            call_graph=[TSCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or []), weight=r["weight"] or 1) for r in self._call_rows()],
            external_symbols=externals,
            synthesized_callables=self.get_synthesized_callables(),
        )

    def get_symbol_table(self) -> Dict[str, TSModule]:
        roots, children = self._fetch("(:Application {id: $app_id})-[:TS_HAS_MODULE]->(root:TSModule)", app_id=self._app_id)
        return {p["name"]: self._module(p, children) for p in roots}

    def get_modules(self) -> List[TSModule]:
        return list(self.get_symbol_table().values())

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        module_id = self._module_ids.get(file_path)
        if module_id is None:
            return None
        roots, children = self._fetch("(root:CanNode:TSModule {id: $id})", id=module_id)
        return self._module(roots[0], children) if roots else None

    def get_typescript_file(self, qualified_name: str) -> str | None:
        rows = self._run(
            f"MATCH (n:TSCallable|TSClass|TSInterface|TSEnum|TSTypeAlias|TSNamespace {{signature: $sig}}) WHERE {_scoped('n')} RETURN n.id AS id LIMIT 1",
            sig=qualified_name,
            **self._scope_params,
        )
        return self._module_key(rows[0]["id"]) if rows else None

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """The application's external *symbols* -- ``<app-id>/@external/<module>/<name>``, what
        ``analysis.json``'s ``external_symbols`` holds -- keyed ``"<module>.<name>"``. An external is
        homed on the application under the typescript namespace whichever module called it, so that
        prefix is the scope. The graph also holds one nameless ``:TSExternal`` per *package*
        (``@external/<module>``, the target of ``TS_PROVIDES`` / ``TS_UNRESOLVED_IMPORT``); those are
        not symbols and are not returned here."""
        rows = self._run("MATCH (e:TSExternal) WHERE e.id STARTS WITH $prefix AND e.name IS NOT NULL RETURN properties(e) AS p", prefix=f"{self._app_id}/@external/")
        return {f"{r['p']['module']}.{r['p']['name']}": R.external(r["p"]) for r in rows}

    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """The application's anonymous callables (``:TSAnonymousCallable`` tree nodes), keyed by
        their own id: the analyzer's compatibility index (older key -> tree id) is JSON-only."""
        rows = self._run(f"MATCH (a:TSAnonymousCallable) WHERE {_scoped('a')} RETURN properties(a) AS p", **self._scope_params)
        return {r["p"]["id"]: R.synthesized(r["p"]) for r in rows}

    # =====================================================================================
    # call graph
    # =====================================================================================
    def _call_rows(self) -> List[Dict[str, Any]]:
        """Every ``TS_CALLS`` edge whose source is this application's, with what each endpoint needs
        to be keyed the way every other accessor keys it (see :meth:`_graph_key`)."""
        return self._run(
            f"MATCH (s)-[r:TS_CALLS]->(t) WHERE {_scoped('s')} "
            "RETURN s.id AS src, s.kind AS src_kind, s.signature AS src_sig, s.module AS src_module, s.name AS src_name, "
            "t.id AS dst, t.kind AS dst_kind, t.signature AS dst_sig, t.module AS dst_module, t.name AS dst_name, r.weight AS weight, r.prov AS prov",
            **self._scope_params,
        )

    def _graph_key(self, node_id: str, kind: str | None, signature: str | None, module: str | None, name: str | None) -> Tuple[str, str]:
        """``(node key, kind)`` as the in-memory backend keys them: a module by its file key, an
        external by ``"<module>.<name>"``, a type or callable by signature."""
        if kind == "module":
            return self._module_key(node_id), "module"
        if kind == "external":
            if not name:
                raise CodeanalyzerExecutionException(
                    f"a TS_CALLS endpoint is the package-level external {module!r}, which has no member name to key it by: codeanalyzer-typescript emitted an endpoint this backend cannot address"
                )
            return f"{module}.{name}", "external"
        if not signature:
            raise CodeanalyzerExecutionException(
                f"a TS_CALLS endpoint of kind {kind!r} named {name!r} carries no signature to key it by: codeanalyzer-typescript emitted an endpoint this backend cannot address"
            )
        return signature, kind if kind in TYPE_KINDS else "callable"

    def get_call_graph(self) -> nx.DiGraph:
        """Cached. Nodes carry ``id`` and ``kind`` (module callers and class callees kept, TS-11);
        edges carry ``type="CALL_DEP"``, ``weight`` and ``provenance``."""
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for r in self._call_rows():
            src, src_kind = self._graph_key(r["src"], r["src_kind"], r["src_sig"], r["src_module"], r["src_name"])
            dst, dst_kind = self._graph_key(r["dst"], r["dst_kind"], r["dst_sig"], r["dst_module"], r["dst_name"])
            graph.add_node(src, id=r["src"], kind=src_kind)
            graph.add_node(dst, id=r["dst"], kind=dst_kind)
            graph.add_edge(src, dst, type="CALL_DEP", weight=r["weight"] or 1, provenance=tuple(r["prov"] or []))
        self._call_graph = graph
        return graph

    def get_call_graph_json(self) -> str:
        return self.get_application_view().model_dump_json()

    def _resolve_signature(self, class_or_sig: str, member: str | None = None) -> str:
        if member is None:
            return class_or_sig
        rows = self._run(
            f"MATCH (o:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('o')} AND o.kind IN $kinds MATCH (o)-[:TS_HAS_METHOD]->(m:TSCallable {{name: $name}}) RETURN m.signature AS sig LIMIT 1",
            sig=class_or_sig,
            name=member,
            kinds=_OWNER_KINDS,
            **self._scope_params,
        )
        return rows[0]["sig"] if rows else f"{class_or_sig}.{member}"

    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        graph = self.get_call_graph()
        target = self._resolve_signature(target_class_name, target_method_declaration)
        if target not in graph:
            return {"target_method": target, "caller_details": []}
        return {"target_method": target, "caller_details": [{"caller_signature": src, "edge": graph.get_edge_data(src, target)} for src in graph.predecessors(target)]}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        graph = self.get_call_graph()
        source = self._resolve_signature(source_class_name, source_method_declaration)
        if source not in graph:
            return {"source_method": source, "callee_details": []}
        return {"source_method": source, "callee_details": [{"callee_signature": tgt, "edge": graph.get_edge_data(source, tgt)} for tgt in graph.successors(source)]}

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        graph = self.get_call_graph()
        seeds = [method_signature] if method_signature is not None else [m.signature for m in self.get_all_methods_in_class(qualified_class_name).values()]
        seeds = [s for s in seeds if s in graph]
        return list(nx.edge_bfs(graph, seeds)) if seeds else []

    def get_class_hierarchy(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        rows = self._run(
            f"MATCH (n:TSClass|TSInterface) WHERE {_scoped('n')} AND n.kind IN $kinds RETURN n.signature AS sig, n.base_classes AS bases", kinds=_OWNER_KINDS, **self._scope_params
        )
        for r in rows:
            graph.add_node(r["sig"])
        for r in rows:
            for base in r["bases"] or []:
                graph.add_edge(r["sig"], base)
        return graph

    # =====================================================================================
    # call sites -- ``TSBodyNode {kind:'call'}`` under the callable, resolved over TS_RESOLVES_TO
    # =====================================================================================
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        # OPTIONAL MATCH so an existing callable with no call sites still gets an (empty) entry. The
        # resolved target is keyed as the call graph keys it: a signature, or "<module>.<name>" for
        # an external (which carries no signature); null when the analyzer left the call unresolved.
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.signature IN $sigs "
            "OPTIONAL MATCH (c)-[:TS_HAS_BODY_NODE]->(s:TSBodyNode {kind: 'call'}) "
            "OPTIONAL MATCH (s)-[:TS_RESOLVES_TO]->(t) "
            "RETURN c.signature AS owner, properties(s) AS p, coalesce(t.signature, t.module + '.' + t.name) AS callee "
            "ORDER BY s.start_line",
            sigs=list(signatures),
            **self._scope_params,
        )
        out: Dict[str, List[TSCallsite]] = {}
        for r in rows:
            sites = out.setdefault(r["owner"], [])
            if r["p"] is not None:
                sites.append(R.callsite(r["p"], r["callee"]))
        return out

    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        return self.get_callsites_for([qualified_callable_name]).get(qualified_callable_name, [])

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """Resolved callee keys of a callable's call sites. An unresolved site contributes ``""``:
        the graph keeps no ``method_name`` to fall back on."""
        return {cs.callee_signature or "" for cs in self.get_call_sites(source_signature)}

    def get_calling_lines(self, target_signature: str) -> List[int]:
        rows = self._run(
            f"MATCH (t:TSCallable|TSClass|TSExternal) WHERE {_scoped('t')} AND coalesce(t.signature, t.module + '.' + t.name) = $sig "
            "MATCH (s:TSBodyNode {kind: 'call'})-[:TS_RESOLVES_TO]->(t) WHERE s.start_line IS NOT NULL "
            "RETURN DISTINCT s.start_line AS line ORDER BY line",
            sig=target_signature,
            **self._scope_params,
        )
        return [r["line"] for r in rows]

    # =====================================================================================
    # classes / interfaces / enums / type aliases / namespaces
    # =====================================================================================
    def get_all_classes(self) -> Dict[str, TSClass]:
        return self._types_by_signature("TSClass")

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        return self._type_by_signature("TSClass", qualified_class_name)

    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        return self._types_by_signature("TSInterface")

    def get_all_enums(self) -> Dict[str, TSEnum]:
        return self._types_by_signature("TSEnum")

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        enum = self._type_by_signature("TSEnum", qualified_enum_name)
        return list(enum.members) if enum else []

    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        return self._types_by_signature("TSTypeAlias")

    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        # The v2 class facet nests no types (only namespaces and callables do); same as in-memory.
        return []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        roots, children = self._fetch(f"(root:TSClass) WHERE {_scoped('root')} AND $sig IN root.base_classes", sig=qualified_class_name, **self._scope_params)
        return {p["signature"]: self._type(p, children) for p in roots}

    def _heritage(self, qualified_class_name: str) -> Tuple[List[str], List[str]]:
        rows = self._run(
            f"MATCH (c:TSClass {{signature: $sig}}) WHERE {_scoped('c')} RETURN c.base_classes AS bases, c.implements_types AS impl", sig=qualified_class_name, **self._scope_params
        )
        return (list(rows[0]["bases"] or []), list(rows[0]["impl"] or [])) if rows else ([], [])

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        bases, impl = self._heritage(qualified_class_name)
        return [b for b in bases if b not in impl]

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        return self._heritage(qualified_class_name)[1]

    # =====================================================================================
    # methods / functions / fields
    # =====================================================================================
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        roots, children = self._fetch(f"(root:TSClass|TSInterface) WHERE {_scoped('root')} AND root.kind IN $kinds", kinds=_OWNER_KINDS, **self._scope_params)
        return {p["signature"]: {m.name: m for m in self._methods(p["id"], children).values()} for p in roots}

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        roots, children = self._fetch(
            f"(root:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('root')} AND root.kind IN $kinds", sig=qualified_class_name, kinds=_OWNER_KINDS, **self._scope_params
        )
        return {m.name: m for p in roots[:1] for m in self._methods(p["id"], children).values()}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        """A method by (class/interface signature, short name); else a module/namespace function
        by exact signature (``qualified_method_name`` is the signature, the scope ignored), else by
        short name under ``<scope>.`` -- the in-memory backend's resolution order."""
        roots, children = self._fetch(
            f"(o:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('o')} AND o.kind IN $kinds MATCH (o)-[:TS_HAS_METHOD]->(root:TSCallable {{name: $name}})",
            sig=qualified_class_name,
            name=qualified_method_name,
            kinds=_OWNER_KINDS,
            **self._scope_params,
        )
        if not roots:
            roots, children = self._fetch(
                f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable {{signature: $sig}}) WHERE {_scoped('root')}", sig=qualified_method_name, **self._scope_params
            )
        if not roots:
            roots, children = self._fetch(
                f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable {{name: $name}}) WHERE {_scoped('root')} AND root.signature STARTS WITH $sig_prefix",
                name=qualified_method_name,
                sig_prefix=f"{qualified_class_name}.",
                **self._scope_params,
            )
        return self._callable(roots[0], children) if roots else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """``[]`` for a missing method, as in-memory. For a **found** one this graph cannot answer:
        the 1.2.0 projection carries no parameters on ``:TSCallable`` (nor as nodes), and an empty
        list would read as "takes no parameters", so it raises naming the gap."""
        if self.get_method(qualified_class_name, qualified_method_name) is None:
            return []
        raise CodeanalyzerExecutionException(f"The codeanalyzer-typescript Neo4j projection carries no parameters for {qualified_method_name!r}; they exist only in analysis.json.")

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {name: m for name, m in self.get_all_methods_in_class(qualified_class_name).items() if m.kind == "constructor"}

    def get_all_functions(self) -> Dict[str, TSCallable]:
        roots, children = self._fetch(f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable) WHERE {_scoped('root')}", **self._scope_params)
        return {p["signature"]: self._callable(p, children) for p in roots}

    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        it = self._type_by_signature("TSInterface", qualified_interface_name)
        return list(it.properties.values()) if it else []

    # =====================================================================================
    # imports / exports / variables
    # =====================================================================================
    def get_imports(self) -> Dict[str, List[TSImport]]:
        """The 1.2.0 projection carries no import bindings (no relationship type, no property), so
        this graph cannot say what a module imports; raises naming the gap rather than returning
        empty lists that would read as "imports nothing"."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no import bindings for application {self.application_name!r}; they exist only in analysis.json."
        )

    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        """As :meth:`get_imports`: the projection carries no export bindings."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no export bindings for application {self.application_name!r}; they exist only in analysis.json."
        )

    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        out: Dict[str, List[TSVariableDeclaration]] = {key: [] for key in self._modules}
        rows = self._run(
            "MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule)-[:TS_HAS_FIELD]->(f:TSField) RETURN m.name AS k, properties(f) AS p ORDER BY f.start_line, f.name",
            app_id=self._app_id,
        )
        for r in rows:
            out.setdefault(r["k"], []).append(R.field(r["p"]))
        return out

    # =====================================================================================
    # repository artifacts -- the unprefixed shared layer, the shared Py* models
    # =====================================================================================
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        result: Dict[str, PyArtifact] = {}
        for r in self._run(
            "MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact) OPTIONAL MATCH (a)-[:DEFINES_CONFIG]->(ck:ConfigKey) RETURN properties(a) AS p, collect(properties(ck)) AS cks",
            app_id=self._app_id,
        ):
            art = PyR.artifact(r["p"], config_keys=[PyR.config_key(p) for p in r["cks"] if p])
            result[art.path] = art
        return result

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        conditions: List[str] = []
        params: Dict[str, Any] = {"app_id": self._app_id}
        if direct_only:
            conditions.append("r.direct = true")
        if ecosystem is not None:
            conditions.append("p.ecosystem = $ecosystem")
            params["ecosystem"] = ecosystem
        if declared_in is not None:
            conditions.append("a.id = $declared_in")
            params["declared_in"] = declared_in
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (
            "MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(p:Package)"
            + where
            + " RETURN properties(r) AS rel, p.name AS name, p.ecosystem AS ecosystem, a.id AS declared_in"
        )
        return [PyR.dependency(r["rel"], name=r["name"], ecosystem=r["ecosystem"], declared_in=r["declared_in"]) for r in self._run(query, **params)]

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        rows = self._run("MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(:Artifact)-[:DEFINES_CONFIG]->(ck:ConfigKey) RETURN properties(ck) AS p", app_id=self._app_id)
        return {ck.id: ck for ck in (PyR.config_key(r["p"]) for r in rows)}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        query = f"MATCH (bn:TSBodyNode)-[u:TS_USES_CONFIG]->(ck:ConfigKey) WHERE {_scoped('bn')}"
        params: Dict[str, Any] = dict(self._scope_params)
        if key is not None:
            query += " AND ck.key = $key"
            params["key"] = key
        query += " RETURN bn.id AS src, ck.id AS dst, u.prov AS prov"
        return [PyConfigUseEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or [])) for r in self._run(query, **params)]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """The 1.2.0 projection does not carry ``config_reads`` (no relationship type or property
        in ``schema.neo4j.json``), so this graph cannot say whether a detector-matched read failed
        to resolve. Raising keeps that distinct from "every read resolved"."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no unresolved config reads for application {self.application_name!r}; they exist only in analysis.json."
        )

    # =====================================================================================
    # decorators
    # =====================================================================================
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        rows = self._run(
            f"MATCH (c:TSCallable {{signature: $sig}}) WHERE {_scoped('c')} MATCH (c)-[r:TS_DECORATED_BY]->(d:TSDecorator) RETURN properties(d) AS node, properties(r) AS edge",
            sig=qualified_callable_name,
            **self._scope_params,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        rows = self._run(
            f"MATCH (c:TSClass {{signature: $sig}}) WHERE {_scoped('c')} MATCH (c)-[r:TS_DECORATED_BY]->(d:TSDecorator) RETURN properties(d) AS node, properties(r) AS edge",
            sig=qualified_class_name,
            **self._scope_params,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def _with_decorators(self, label: str, decorators: List[str]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        rows = self._run(
            f"MATCH (c:{label})-[:TS_DECORATED_BY]->(d:TSDecorator) WHERE {_scoped('c')} AND d.name IN $names RETURN d.name AS dn, c.signature AS sig",
            names=list(decorators),
            **self._scope_params,
        )
        for r in rows:
            result[r["dn"]].append(r["sig"])
        return result

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        return self._with_decorators("TSCallable", decorators)

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        return self._with_decorators("TSClass", decorators)

    # =====================================================================================
    # bulk / projected accessors -- one statement each
    # =====================================================================================
    #: Appended to a scoped ``MATCH`` that bound ``c``; ``path`` is derived from ``id`` afterwards.
    _OVERVIEW_PROJECTION = (
        "OPTIONAL MATCH (o:TSClass|TSInterface)-[:TS_HAS_METHOD]->(c) "
        "OPTIONAL MATCH (c)-[:TS_DECORATED_BY]->(d:TSDecorator) "
        "RETURN c.id AS id, c.signature AS signature, c.name AS name, c.kind AS kind, c.start_line AS start_line, c.end_line AS end_line, "
        "c.is_exported AS is_exported, c.is_async AS is_async, c.is_static AS is_static, c.accessibility AS accessibility, "
        "o.signature AS owner_signature, o.kind AS owner_kind, collect(DISTINCT d.name) AS decorators"
    )

    def _overview(self, row: Dict[str, Any]) -> TSCallableOverview:
        return R.overview({**row, "path": self._module_key(row["id"])})

    def get_callables_overview(self) -> List[TSCallableOverview]:
        rows = self._run(f"MATCH (c:TSCallable) WHERE {_scoped('c')} " + self._OVERVIEW_PROJECTION, **self._scope_params)
        return [self._overview(r) for r in rows]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.signature IN $sigs AND c.code IS NOT NULL AND c.code <> '' RETURN c.signature AS signature, c.code AS code",
            sigs=list(signatures),
            **self._scope_params,
        )
        return {r["signature"]: r["code"] for r in rows}

    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        rows = self._run(
            f"MATCH (c:TSCallable)-[:TS_DECORATED_BY]->(marker:TSDecorator) WHERE {_scoped('c')} AND marker.name IN $markers WITH DISTINCT c " + self._OVERVIEW_PROJECTION,
            markers=list(markers),
            **self._scope_params,
        )
        return [self._overview(r) for r in rows]
