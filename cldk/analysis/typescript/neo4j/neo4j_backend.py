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

"""Neo4j-backed TypeScript analysis backend (read-only Cypher client).

A drop-in alternative to :class:`TSCodeanalyzer`: it exposes the **same query
method surface** (``get_all_classes``, ``get_call_graph``, ``get_all_callers``,
...) so the :class:`TypeScriptAnalysis` facade can delegate to either one, but
every method answers by running **Cypher over a live Neo4j graph** instead of
walking the in-memory pydantic/NetworkX structures.

This class is purely a **query client**: it never builds the graph and has no
dependency on the ``codeanalyzer-typescript`` binary or the project sources. It
assumes the database is already populated and just polls it — the shape a cloud
deployment wants, where a third-party job (e.g. inside Kubernetes) loads the
graph out of band and the SDK only reads it.

The graph is the one ``codeanalyzer-typescript>=1.0.0`` emits with ``--emit
neo4j`` (**graph schema 2.0.0**). Populating it always happens out of band —
never from this backend. On first use the backend reads
``(:Application).schema_version`` and fails fast if it is not the schema this SDK
speaks (see :meth:`_check_schema_version`).

Identity model (graph schema 2.0.0 — must match the in-memory backend):

* every projected node is a ``:CanNode`` carrying a canonical ``id`` (a ``can://``
  URI) as its merge key; TypeScript nodes wear a twin ``:TS*`` label
  (``:CanNode:TSClass``, ``:CanNode:TSCallable``, ...);
* a callable/class/interface/enum/type-alias still carries a ``signature``
  *property*, which is what the SDK's public accessors key on;
* call-graph edges are ``(:TSCallable)-[:TS_CALLS]->(:TSCallable|:TSExternal)``;
* call *sites* are body nodes — ``(:TSBodyNode {kind: "call"})`` reached via
  ``TS_HAS_BODY_NODE`` and resolved through ``TS_RESOLVES_TO`` — not standalone
  call-site nodes;
* module nodes carry a ``_module`` property (the project-relative path); this
  backend further *assumes* — to-verify against a live 1.0.0 graph, see the
  ``VERIFY(2.0.0-e2e)`` markers — that every project-owned node carries the same
  ``_module`` provenance property, so a single database may hold several
  applications and all queries here can be scoped to this backend's application
  by the set of its module ``_module`` keys.

Parity caveats (inherent to what schema 2.0.0 projects, not bugs):

* ``TS_CALLS`` edge ``tags`` only round-trip the three keys the projection keeps
  (``ts.dispatch`` / ``ts.external`` / ``ts.module``);
* decorators, class/interface attributes, module imports/exports and variable
  declarations are **not projected** into graph schema 2.0.0 — the accessors for
  them raise :class:`NotImplementedError` (there is no in-memory/JSON backend to
  fall back to from a read-only Cypher client), and the reconstructed
  ``TSClass`` / ``TSModule`` objects carry empty collections for those fields;
* comments collapse to a single docstring, type-parameters keep only their names.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.neo4j import reconstruct as R
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallEdge,
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
from cldk.utils.exceptions.exceptions import CldkSchemaMismatchException, CodeanalyzerExecutionException, CodeanalyzerUsageException

logger = logging.getLogger(__name__)


def _unprojected(feature: str) -> NotImplementedError:
    """The uniform error for an accessor whose vocabulary graph schema 2.0.0 does not project."""
    return NotImplementedError(f"{feature} is not projected in graph schema {TSNeo4jBackend.SUPPORTED_GRAPH_SCHEMA} — use the in-memory (JSON) backend")


class TSNeo4jBackend(TSAnalysisBackend):
    """Query the application view of a TypeScript project over Neo4j (Cypher), read-only.

    The graph must already be loaded out of band — e.g. a job running
    ``codeanalyzer-typescript --emit neo4j``. This backend never writes and needs neither the
    ``codeanalyzer-typescript`` binary nor the project sources on disk.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:Application`` anchor to scope every query to. Matched against the
            tail of the application's ``can://`` ``id`` (the ``--app-name`` the graph was loaded
            with; defaults to the project directory name).
    """

    #: The graph schema version this backend speaks; enforced on first use.
    SUPPORTED_GRAPH_SCHEMA = "2.0.0"

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
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with " "`pip install neo4j` (or `pip install cldk[neo4j]`).") from e

        if not application_name:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        self.application_name = application_name
        self._database = neo4j_database
        self._driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))

        # Fail fast if the persisted graph isn't the schema this SDK speaks. This runs *before*
        # the application-id resolution on purpose: a pre-2.0.0 graph has no `can://` ids, so
        # resolving first would report "no application found" instead of the (more actionable)
        # schema mismatch.
        self._check_schema_version()

        # Resolve `application_name` to exactly one Application `id` (guards against a shared
        # database where several apps' ids share the same trailing path segment).
        self._app_id: str = self._resolve_application_id()

        # The application's module `_module` keys, used to scope every query to this app.
        self._modules: List[str] = self._load_module_keys()

    # -----[ lifecycle ]-----
    def close(self) -> None:
        """Close the underlying Neo4j driver."""
        self._driver.close()

    def __enter__(self) -> "TSNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run a Cypher statement and return the records as plain dicts (nodes/rels → prop maps)."""
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(query, **params)]

    def _check_schema_version(self, expected: str | None = None, found: str | None = None) -> None:
        """Fail fast unless the persisted graph's ``schema_version`` is the one this SDK speaks.

        Reads ``(:Application).schema_version`` once (unless ``found`` is supplied, as the tests
        do) and raises :class:`CldkSchemaMismatchException` on any mismatch — including a graph
        with no ``:Application`` node at all.
        """
        expected = expected or self.SUPPORTED_GRAPH_SCHEMA
        if found is None:
            rows = self._run("MATCH (a:Application) RETURN a.schema_version AS v LIMIT 1")
            found = rows[0]["v"] if rows else None
        if found != expected:
            raise CldkSchemaMismatchException(
                f"graph schema {found!r} in database, this SDK speaks {expected!r} — re-analyze with codeanalyzer-typescript>=1.0.0"
            )

    def _resolve_application_id(self) -> str:
        """Resolve ``application_name`` to exactly one ``:Application`` node's ``can://`` id.

        The suffix match (``a.id ENDS WITH "/" + $app``) can bind multiple applications in a
        shared database — e.g. two repos whose ids both end in ``/frontend`` — which would
        silently merge their module scopes. Raise :class:`CodeanalyzerUsageException` unless the
        match is unique; the caller disambiguates by passing a longer trailing path (any suffix
        of the ``can://`` id starting at a ``/`` boundary works as ``application_name``).
        """
        rows = self._run(
            'MATCH (a:Application) WHERE a.id ENDS WITH "/" + $app RETURN a.id AS id ORDER BY id',
            app=self.application_name,
        )
        if not rows:
            raise CodeanalyzerUsageException(
                f"no :Application found whose id ends with '/{self.application_name}' — check application_name (the --app-name the graph was loaded with)."
            )
        if len(rows) > 1:
            candidates = ", ".join(r["id"] for r in rows)
            raise CodeanalyzerUsageException(
                f"application_name '{self.application_name}' is ambiguous: it matches {len(rows)} applications in this database ({candidates}). "
                "Pass a longer trailing path of the intended application's id to disambiguate."
            )
        return rows[0]["id"]

    def _load_module_keys(self) -> List[str]:
        # VERIFY(2.0.0-e2e): every project-owned node is assumed to carry a `_module` provenance
        # property (as in the pre-2.0.0 projection); all the `x._module IN $mods` scoping below
        # depends on it — validate against a live 1.0.0 graph (Task 9).
        rows = self._run(
            "MATCH (a:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m._module AS k",
            app_id=self._app_id,
        )
        return [r["k"] for r in rows]

    # -----[ child-fetch helpers (reconstruction) ]-----
    def _callsites_of(self, signature: str) -> List[TSCallsite]:
        rows = self._run(
            "MATCH (c:TSCallable {signature: $sig})-[:TS_HAS_BODY_NODE]->(cs:TSBodyNode {kind: 'call'}) " "RETURN properties(cs) AS p ORDER BY cs.start_line, cs.start_column",
            sig=signature,
        )
        return [R.callsite(r["p"]) for r in rows]

    def _callable_full(self, props: Dict[str, Any]) -> TSCallable:
        sig = props["signature"]
        # Nested-declaration containers are keyed by signature (matching the analyzer's dict keys).
        inner_callables = {p["signature"]: self._callable_full(p) for p in self._children(sig, "TS_DECLARES", "TSCallable")}
        inner_classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "TS_DECLARES", "TSClass")}
        return R.callable_(
            props,
            decorators=[],  # decorators are not projected in graph schema 2.0.0
            call_sites=self._callsites_of(sig),
            inner_callables=inner_callables,
            inner_classes=inner_classes,
        )

    @staticmethod
    def _method_key(props: Dict[str, Any]) -> str:
        """The class/interface ``methods`` dict key: ``sig`` for normal methods, ``sig#get`` /
        ``sig#set`` for accessors (the analyzer disambiguates same-signature get/set this way)."""
        sig = props["signature"]
        accessor = props.get("accessor_kind")
        if accessor == "getter":
            return f"{sig}#get"
        if accessor == "setter":
            return f"{sig}#set"
        return sig

    def _class_full(self, props: Dict[str, Any]) -> TSClass:
        sig = props["signature"]
        # methods keyed by the analyzer's method-key; inner_classes by signature. Attributes and
        # decorators are not projected in graph schema 2.0.0, so those collections stay empty.
        methods = {self._method_key(p): self._callable_full(p) for p in self._members(sig, "TS_HAS_METHOD", "TSCallable")}
        inner_classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "TS_DECLARES", "TSClass")}
        return R.class_(
            props,
            decorators=[],
            methods=methods,
            attributes={},
            inner_classes=inner_classes,
        )

    def _interface_full(self, props: Dict[str, Any]) -> TSInterface:
        sig = props["signature"]
        methods = {self._method_key(p): self._callable_full(p) for p in self._members(sig, "TS_HAS_METHOD", "TSCallable")}
        # interface properties are attributes — not projected in graph schema 2.0.0.
        return R.interface(props, methods=methods, properties={})

    def _children(self, signature: str, rel: str, label: str) -> List[Dict[str, Any]]:
        """Property maps of ``label`` nodes reached from a symbol via ``rel`` (one hop), in
        declaration (source) order."""
        rows = self._run(
            f"MATCH (s:CanNode {{signature: $sig}})-[:{rel}]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
            sig=signature,
        )
        return [r["p"] for r in rows]

    def _members(self, signature: str, rel: str, label: str) -> List[Dict[str, Any]]:
        """Property maps of member ``label`` nodes (methods), in declaration order."""
        rows = self._run(
            f"MATCH (s:CanNode {{signature: $sig}})-[:{rel}]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
            sig=signature,
        )
        return [r["p"] for r in rows]

    # -----[ application / whole-program ]-----
    def get_application(self) -> TSApplication:
        """Re-hydrate the whole :class:`TSApplication` (symbol table + call graph + externals)."""
        return TSApplication(
            symbol_table=self.get_symbol_table(),
            call_graph=self._call_edges(),
            external_symbols=self.get_external_symbols(),
            synthesized_callables=self.get_synthesized_callables(),
        )

    def get_symbol_table(self) -> Dict[str, TSModule]:
        modules: Dict[str, TSModule] = {}
        for key in self._modules:
            mod = self.get_typescript_module(key)
            if mod is not None:
                modules[key] = mod
        return modules

    def get_modules(self) -> List[TSModule]:
        return list(self.get_symbol_table().values())

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        rows = self._run(
            "MATCH (s:CanNode)-[:TS_CALLS]->(e:TSExternal) WHERE s._module IN $mods "
            "RETURN DISTINCT properties(e) AS p "
            "UNION "
            "MATCH (cs:TSBodyNode {kind: 'call'})-[:TS_RESOLVES_TO]->(e:TSExternal) WHERE cs._module IN $mods "
            "RETURN DISTINCT properties(e) AS p",
            mods=self._modules,
        )
        return {r["p"]["signature"]: R.external(r["p"]) for r in rows}

    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """Anonymous-callback endpoints minted as ``:TSAnonymousCallable`` nodes (keyed by
        signature), scoped to this application's modules."""
        rows = self._run(
            "MATCH (a:TSAnonymousCallable) WHERE a._module IN $mods RETURN DISTINCT properties(a) AS p",
            mods=self._modules,
        )
        return {r["p"]["signature"]: R.synthesized(r["p"]) for r in rows}

    def get_typescript_file(self, qualified_name: str) -> str | None:
        rows = self._run(
            "MATCH (s:CanNode {signature: $sig}) WHERE s._module IN $mods RETURN s._module AS m LIMIT 1",
            sig=qualified_name,
            mods=self._modules,
        )
        return rows[0]["m"] if rows else None

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        rows = self._run("MATCH (m:TSModule {_module: $key}) RETURN properties(m) AS p", key=file_path)
        if not rows:
            return None
        props = rows[0]["p"]
        # All declaration containers are keyed by signature (matching the analyzer's dict keys).
        classes = {p["signature"]: self._class_full(p) for p in self._module_decls(file_path, "TSClass")}
        interfaces = {p["signature"]: self._interface_full(p) for p in self._module_decls(file_path, "TSInterface")}
        enums = {p["signature"]: R.enum(p) for p in self._module_decls(file_path, "TSEnum")}
        type_aliases = {p["signature"]: R.type_alias(p) for p in self._module_decls(file_path, "TSTypeAlias")}
        functions = {p["signature"]: self._callable_full(p) for p in self._module_decls(file_path, "TSCallable")}
        namespaces = {p["signature"]: self._namespace_full(p) for p in self._module_decls(file_path, "TSNamespace")}
        # variables / imports / exports are not projected in graph schema 2.0.0.
        return R.module(
            props,
            classes=classes,
            interfaces=interfaces,
            enums=enums,
            type_aliases=type_aliases,
            functions=functions,
            namespaces=namespaces,
            variables=[],
            imports=[],
            exports=[],
        )

    def _module_decls(self, module_key: str, label: str) -> List[Dict[str, Any]]:
        rows = self._run(
            f"MATCH (m:TSModule {{_module: $key}})-[:TS_DECLARES]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
            key=module_key,
        )
        return [r["p"] for r in rows]

    def _namespace_full(self, props: Dict[str, Any]):
        sig = props["signature"]
        classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "TS_DECLARES", "TSClass")}
        interfaces = {p["signature"]: self._interface_full(p) for p in self._children(sig, "TS_DECLARES", "TSInterface")}
        enums = {p["signature"]: R.enum(p) for p in self._children(sig, "TS_DECLARES", "TSEnum")}
        type_aliases = {p["signature"]: R.type_alias(p) for p in self._children(sig, "TS_DECLARES", "TSTypeAlias")}
        functions = {p["signature"]: self._callable_full(p) for p in self._children(sig, "TS_DECLARES", "TSCallable")}
        namespaces = {p["signature"]: self._namespace_full(p) for p in self._children(sig, "TS_DECLARES", "TSNamespace")}
        # namespace-level variables are not projected in graph schema 2.0.0.
        return R.namespace(
            props,
            classes=classes,
            interfaces=interfaces,
            enums=enums,
            type_aliases=type_aliases,
            functions=functions,
            namespaces=namespaces,
            variables=[],
        )

    # -----[ call graph ]-----
    def _call_edges(self) -> List[TSCallEdge]:
        rows = self._run(
            "MATCH (s:CanNode)-[r:TS_CALLS]->(t:CanNode) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt, properties(r) AS edge",
            mods=self._modules,
        )
        return [
            TSCallEdge(
                source=r["src"],
                target=r["tgt"],
                weight=r["edge"].get("weight", 1),
                provenance=list(r["edge"].get("provenance", []) or []),
                tags=self._edge_tags(r["edge"]),
            )
            for r in rows
        ]

    @staticmethod
    def _edge_tags(edge: Dict[str, Any]) -> Dict[str, str]:
        """Invert the flattened TS_CALLS-edge tag props back into the ``ts.*`` tag dict."""
        tags: Dict[str, str] = {}
        if edge.get("dispatch") is not None:
            tags["ts.dispatch"] = edge["dispatch"]
        if edge.get("external") is True:
            tags["ts.external"] = "true"
        if edge.get("module") is not None:
            tags["ts.module"] = edge["module"]
        return tags

    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of callable signatures (+ phantom external symbols) and TS_CALLS edges."""
        graph = nx.DiGraph()
        # Internal callable nodes (with the reconstructed callable, matching the in-memory backend).
        for props in self._all_callable_props():
            graph.add_node(props["signature"], callable=self._callable_full(props), external=False)
        # Phantom (external) nodes so import-attributed edges don't dangle.
        for sig, ext in self.get_external_symbols().items():
            graph.add_node(sig, external=True, module=ext.module, name=ext.name)
        # Edges (auto-create any endpoint not added above, matching nx.add_edge semantics).
        for r in self._run(
            "MATCH (s:CanNode)-[r:TS_CALLS]->(t:CanNode) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt, properties(r) AS edge",
            mods=self._modules,
        ):
            edge = r["edge"]
            graph.add_edge(
                r["src"],
                r["tgt"],
                type="CALL_DEP",
                weight=edge.get("weight", 1),
                provenance=list(edge.get("provenance", []) or []),
                tags=self._edge_tags(edge),
            )
        return graph

    def get_call_graph_json(self) -> str:
        return self.get_application().model_dump_json()

    def _all_callable_props(self) -> List[Dict[str, Any]]:
        rows = self._run("MATCH (c:TSCallable) WHERE c._module IN $mods RETURN properties(c) AS p", mods=self._modules)
        return [r["p"] for r in rows]

    def _resolve_signature(self, class_or_sig: str, member: str | None = None) -> str:
        """Resolve a ``(class/module, member)`` pair (or a bare signature) to a signature string."""
        if member is None:
            return class_or_sig
        rows = self._run(
            "MATCH (o:CanNode {signature: $owner})-[:TS_HAS_METHOD]->(m:TSCallable {name: $name}) " "RETURN m.signature AS sig LIMIT 1",
            owner=class_or_sig,
            name=member,
        )
        if rows:
            return rows[0]["sig"]
        composed = f"{class_or_sig}.{member}"
        rows = self._run("MATCH (c:TSCallable {signature: $sig}) RETURN c.signature AS sig LIMIT 1", sig=composed)
        return rows[0]["sig"] if rows else composed

    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        target = self._resolve_signature(target_class_name, target_method_declaration)
        rows = self._run(
            "MATCH (src:CanNode)-[r:TS_CALLS]->(t:CanNode {signature: $target}) WHERE src._module IN $mods " "RETURN src.signature AS caller, properties(r) AS edge",
            target=target,
            mods=self._modules,
        )
        caller_details = [{"caller_signature": r["caller"], "edge": self._edge_dict(r["edge"])} for r in rows]
        return {"target_method": target, "caller_details": caller_details}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        source = self._resolve_signature(source_class_name, source_method_declaration)
        rows = self._run(
            "MATCH (s:CanNode {signature: $source})-[r:TS_CALLS]->(tgt:CanNode) " "RETURN tgt.signature AS callee, properties(r) AS edge",
            source=source,
        )
        callee_details = [{"callee_signature": r["callee"], "edge": self._edge_dict(r["edge"])} for r in rows]
        return {"source_method": source, "callee_details": callee_details}

    def _edge_dict(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        """The call-graph edge metadata dict, matching ``get_call_graph`` node-edge attributes."""
        return {
            "type": "CALL_DEP",
            "weight": edge.get("weight", 1),
            "provenance": list(edge.get("provenance", []) or []),
            "tags": self._edge_tags(edge),
        }

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable (BFS) from a class (or one of its methods)."""
        adjacency: Dict[str, List[str]] = {}
        for r in self._run(
            "MATCH (s:CanNode)-[:TS_CALLS]->(t:CanNode) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt ORDER BY src, tgt",
            mods=self._modules,
        ):
            adjacency.setdefault(r["src"], []).append(r["tgt"])

        if method_signature is not None:
            seeds = [method_signature]
        else:
            seeds = [p["signature"] for p in self._members(qualified_class_name, "TS_HAS_METHOD", "TSCallable")]
        edges: List[Tuple[str, str]] = []
        seen = set(seeds)
        queue = deque(seeds)
        while queue:
            src = queue.popleft()
            for dst in adjacency.get(src, []):
                edges.append((src, dst))
                if dst not in seen:
                    seen.add(dst)
                    queue.append(dst)
        return edges

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base_class."""
        # VERIFY(2.0.0-e2e): hierarchy is read from the `base_classes` / `implements_types` array
        # properties (as pre-2.0.0), not the TS_EXTENDS / TS_IMPLEMENTS edges — validate against a
        # live 1.0.0 graph (Task 9); this also covers get_extended_classes,
        # get_implemented_interfaces and get_all_sub_classes.
        graph = nx.DiGraph()
        rows = self._run(
            "MATCH (n:CanNode) WHERE (n:TSClass OR n:TSInterface) AND n._module IN $mods " "RETURN n.signature AS sig, n.base_classes AS bases",
            mods=self._modules,
        )
        for r in rows:
            graph.add_node(r["sig"])
        for r in rows:
            for base in r["bases"] or []:
                graph.add_edge(r["sig"], base)
        return graph

    # -----[ call sites ]-----
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        return self._callsites_of(qualified_callable_name)

    def get_calling_lines(self, target_signature: str) -> List[int]:
        rows = self._run(
            "MATCH (cs:TSBodyNode {kind: 'call'}) WHERE cs._module IN $mods AND cs.callee = $sig " "AND cs.start_line >= 0 RETURN DISTINCT cs.start_line AS line ORDER BY line",
            mods=self._modules,
            sig=target_signature,
        )
        return [r["line"] for r in rows]

    def get_call_targets(self, source_signature: str) -> Set[str]:
        # VERIFY(2.0.0-e2e): falls back to `cs.method_name` when a call body node has no resolved
        # `callee`; whether TSBodyNode carries `method_name` is unconfirmed — validate against a
        # live 1.0.0 graph (Task 9).
        rows = self._run(
            "MATCH (c:TSCallable {signature: $sig})-[:TS_HAS_BODY_NODE]->(cs:TSBodyNode {kind: 'call'}) " "RETURN cs.callee AS cosig, cs.method_name AS mn",
            sig=source_signature,
        )
        return {(r["cosig"] or r["mn"]) for r in rows}

    # -----[ classes / interfaces / enums / type-aliases ]-----
    def get_all_classes(self) -> Dict[str, TSClass]:
        rows = self._run("MATCH (c:TSClass) WHERE c._module IN $mods RETURN properties(c) AS p", mods=self._modules)
        return {r["p"]["signature"]: self._class_full(r["p"]) for r in rows}

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        rows = self._run(
            "MATCH (c:TSClass {signature: $sig}) WHERE c._module IN $mods RETURN properties(c) AS p",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return self._class_full(rows[0]["p"]) if rows else None

    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        rows = self._run("MATCH (i:TSInterface) WHERE i._module IN $mods RETURN properties(i) AS p", mods=self._modules)
        return {r["p"]["signature"]: self._interface_full(r["p"]) for r in rows}

    def get_all_enums(self) -> Dict[str, TSEnum]:
        rows = self._run("MATCH (e:TSEnum) WHERE e._module IN $mods RETURN properties(e) AS p", mods=self._modules)
        return {r["p"]["signature"]: R.enum(r["p"]) for r in rows}

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        rows = self._run("MATCH (e:TSEnum {signature: $sig}) RETURN properties(e) AS p", sig=qualified_enum_name)
        return R.enum(rows[0]["p"]).members if rows else []

    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        rows = self._run("MATCH (t:TSTypeAlias) WHERE t._module IN $mods RETURN properties(t) AS p", mods=self._modules)
        return {r["p"]["signature"]: R.type_alias(r["p"]) for r in rows}

    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        return [self._class_full(p) for p in self._children(qualified_class_name, "TS_DECLARES", "TSClass")]

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        rows = self._run(
            "MATCH (c:TSClass) WHERE c._module IN $mods AND $sig IN c.base_classes " "RETURN properties(c) AS p",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return {r["p"]["signature"]: self._class_full(r["p"]) for r in rows}

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        rows = self._run(
            "MATCH (c:TSClass {signature: $sig}) RETURN c.base_classes AS bases, c.implements_types AS impl",
            sig=qualified_class_name,
        )
        if not rows:
            return []
        bases = rows[0]["bases"] or []
        impl = set(rows[0]["impl"] or [])
        return [b for b in bases if b not in impl]

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        rows = self._run("MATCH (c:TSClass {signature: $sig}) RETURN c.implements_types AS impl", sig=qualified_class_name)
        return list(rows[0]["impl"] or []) if rows else []

    # -----[ methods / functions / fields ]-----
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        # Mirror the in-memory `_methods_by_class`: an entry for *every* class and interface
        # (even those with no methods), each keyed by the method's short name.
        out: Dict[str, Dict[str, TSCallable]] = {}
        for r in self._run(
            "MATCH (n:CanNode) WHERE (n:TSClass OR n:TSInterface) AND n._module IN $mods " "RETURN n.signature AS sig",
            mods=self._modules,
        ):
            out[r["sig"]] = {}
        for r in self._run(
            "MATCH (owner:CanNode)-[:TS_HAS_METHOD]->(m:TSCallable) WHERE owner._module IN $mods " "RETURN owner.signature AS owner, properties(m) AS p",
            mods=self._modules,
        ):
            out.setdefault(r["owner"], {})[r["p"]["name"]] = self._callable_full(r["p"])
        return out

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {p["name"]: self._callable_full(p) for p in self._members(qualified_class_name, "TS_HAS_METHOD", "TSCallable")}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        rows = self._run(
            "MATCH (o:CanNode {signature: $sig})-[:TS_HAS_METHOD]->(m:TSCallable {name: $name}) " "RETURN properties(m) AS p LIMIT 1",
            sig=qualified_class_name,
            name=qualified_method_name,
        )
        if rows:
            return self._callable_full(rows[0]["p"])
        # Class lookup missed (or the scope isn't a class at all): fall back to module/namespace
        # -level functions via TS_DECLARES, mirroring get_all_functions.
        return self._resolve_function(qualified_class_name, qualified_method_name)

    def _resolve_function(self, scope: str, name: str) -> TSCallable | None:
        """Resolve a module/namespace-level function: an exact signature match first (``name`` is
        already a full signature, ``scope`` ignored), then a short-name match scoped under
        ``scope`` (handles functions nested in a namespace the caller doesn't know the full path
        of)."""
        rows = self._run(
            "MATCH (parent)-[:TS_DECLARES]->(c:TSCallable {signature: $sig}) "
            "WHERE (parent:TSModule OR parent:TSNamespace) AND c._module IN $mods "
            "RETURN properties(c) AS p LIMIT 1",
            sig=name,
            mods=self._modules,
        )
        if rows:
            return self._callable_full(rows[0]["p"])
        rows = self._run(
            "MATCH (parent)-[:TS_DECLARES]->(c:TSCallable {name: $name}) "
            "WHERE (parent:TSModule OR parent:TSNamespace) AND c._module IN $mods AND c.signature STARTS WITH $prefix "
            "RETURN properties(c) AS p LIMIT 1",
            name=name,
            mods=self._modules,
            prefix=f"{scope}.",
        )
        return self._callable_full(rows[0]["p"]) if rows else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {p["name"]: self._callable_full(p) for p in self._members(qualified_class_name, "TS_HAS_METHOD", "TSCallable") if p.get("kind") == "constructor"}

    def get_all_functions(self) -> Dict[str, TSCallable]:
        rows = self._run(
            "MATCH (parent)-[:TS_DECLARES]->(c:TSCallable) " "WHERE (parent:TSModule OR parent:TSNamespace) AND c._module IN $mods " "RETURN properties(c) AS p",
            mods=self._modules,
        )
        return {r["p"]["signature"]: self._callable_full(r["p"]) for r in rows}

    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        # Class attributes are not projected as nodes in graph schema 2.0.0.
        raise _unprojected("get_all_fields")

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        # Interface properties are attributes — not projected in graph schema 2.0.0.
        raise _unprojected("get_interface_properties")

    # -----[ imports / exports / variables ]-----
    def get_imports(self) -> Dict[str, List[TSImport]]:
        # Module imports are not projected in graph schema 2.0.0.
        raise _unprojected("get_imports")

    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        # Module (re-)exports are not projected in graph schema 2.0.0.
        raise _unprojected("get_all_exports")

    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        # Variable declarations are not projected in graph schema 2.0.0.
        raise _unprojected("get_all_variables")

    # -----[ decorators ]-----
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        # Decorators are not projected in graph schema 2.0.0.
        raise _unprojected("get_decorators")

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        # Decorators are not projected in graph schema 2.0.0.
        raise _unprojected("get_class_decorators")

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        # Decorators are not projected in graph schema 2.0.0.
        raise _unprojected("get_methods_with_decorators")

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        # Decorators are not projected in graph schema 2.0.0.
        raise _unprojected("get_classes_with_decorators")
