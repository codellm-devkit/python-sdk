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

"""Neo4j-backed Python analysis backend (read-only Cypher client).

A drop-in alternative to :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`: it exposes the
**same query method surface** (the 21 methods of :class:`PythonAnalysisBackend`) so the
:class:`~cldk.analysis.python.PythonAnalysis` facade can delegate to either one, but every method
answers by running **Cypher over a live Neo4j graph** instead of walking the in-memory
pydantic / NetworkX structures. Mirrors :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`.

This class is purely a **query client**: it never builds the graph and has no dependency on the
``codeanalyzer-python`` library or the project sources. It assumes the database is already
populated and just polls it — the shape a cloud deployment wants, where a job loads the graph out
of band and the SDK only reads it.

The graph is the one ``codeanalyzer-python`` (>= 0.2.0) emits with ``--emit neo4j`` (in-process:
``codeanalyzer.neo4j.emit.emit_neo4j``). Populating it always happens out of band — never from this
backend.

Identity model (must match the in-memory backend; see ``codeanalyzer/neo4j/project.py``):

* a class/callable/external is a ``:PySymbol`` keyed by ``signature``
  (also carrying its specific label ``:PyClass`` / ``:PyCallable`` / ``:PyExternal``);
* a module is a ``:PyModule`` keyed by ``file_key`` (which equals the original ``PyModule.file_path``
  and the symbol-table key);
* call-graph edges are ``(:PyCallable|:PyExternal)-[:PY_CALLS {weight, provenance}]->(...)`` with a
  constant ``CALL_DEP`` type;
* class inheritance is ``(:PyClass)-[:PY_EXTENDS]->(:PyClass)`` (plus a ``base_classes`` property);
* every project-owned node carries a ``_module`` provenance property, so a single database may hold
  several applications — all queries here are scoped to this backend's application, anchored on
  ``(:PyApplication {name})-[:PY_HAS_MODULE]->(:PyModule)``.

In-memory dict keys this backend reproduces exactly (the projection stores nodes by ``signature``
only, so the keys are rebuilt from node properties): ``module.classes`` / ``inner_classes`` →
``signature``; ``module.functions`` / ``methods`` / ``inner_callables`` → short ``name``;
``attributes`` → ``name``. ``get_all_classes`` / ``get_class`` return **top-level** classes only
(``PyModule-[:PY_DECLARES]->PyClass``), matching the in-memory backend.

Parity: verified against a real 57-module project — every node and edge **present in the graph**
reconstructs identically to the in-memory ``PyCodeanalyzer`` (3169/3200 checks; on the call edges
present in both, zero weight/provenance mismatches). The residual gap is not in this backend:

* **Upstream emitter gap (not recoverable here):** ``codeanalyzer-python``'s projection drops call
  edges whose target is a bare module name that is *also* imported (e.g. a call to ``os`` /
  ``re`` / ``json`` when ``import os`` is present) — its ``RowBuilder`` keys ``:PyPackage`` names
  and call-target signatures in one namespace, so the edge gets a dangling ``:PySymbol`` reference
  and is silently dropped by the writer. Those edges never reach Neo4j, so the call graph here can
  be missing a small fraction of external-target edges. This is a producer bug, not a query bug.
* **Projection-lossy fields** (inherent to what the graph stores — see :mod:`reconstruct`): comments
  collapse to a single docstring (module-level comments dropped); ``PyVariableDeclaration.value`` and
  its column span, plus per-binding import detail, are not recoverable; the order of ``call_graph``
  edges and a callable's ``call_sites`` / ``local_variables`` is positional, not insertion order.

Everything else round-trips identically to ``PyCodeanalyzer``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import networkx as nx
from codeanalyzer.schema import model_dump_json

from cldk.analysis.python.backend import PythonAnalysisBackend
from cldk.analysis.python.neo4j import reconstruct as R
from cldk.models.python import (
    PyApplication,
    PyCallEdge,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyModule,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class PyNeo4jBackend(PythonAnalysisBackend):
    """Query the application view of a Python project over Neo4j (Cypher), read-only.

    The graph must already be loaded out of band — e.g. a job running
    ``codeanalyzer-python --emit neo4j``. This backend never writes and needs neither the
    ``codeanalyzer-python`` library nor the project sources on disk.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:PyApplication`` anchor name to scope every query to. Matches the
            ``--app-name`` the graph was loaded with (defaults to the project directory name).
    """

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
                "The Neo4j backend requires the 'neo4j' driver. Install it with "
                "`pip install neo4j` (or `pip install cldk[neo4j]`)."
            ) from e

        if not application_name:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        self.application_name = application_name
        self._database = neo4j_database
        self._driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        # One long-lived read session reused across queries (see _run). Reconstruction is an N+1
        # fan-out, so reopening a session per query added real per-call overhead. Created lazily.
        self._session_obj: Any | None = None

        # The application's module file_keys, used to scope every query to this app.
        self._modules: List[str] = self._load_module_keys()
        # Lazily-built call graph cache (mirrors PyCodeanalyzer.call_graph).
        self._call_graph: nx.DiGraph | None = None

    # -----[ lifecycle ]-----
    def close(self) -> None:
        """Close the reused session (if any) and the underlying Neo4j driver."""
        self._close_session()
        self._driver.close()

    def _close_session(self) -> None:
        if self._session_obj is not None:
            try:
                self._session_obj.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._session_obj = None

    def __enter__(self) -> "PyNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _session(self) -> Any:
        """The reused read session, opened lazily on first use."""
        if self._session_obj is None:
            self._session_obj = self._driver.session(database=self._database)
        return self._session_obj

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run a Cypher statement and return the records as plain dicts (nodes/rels → prop maps).

        Reuses one long-lived session across calls. If a query fails the session may be left in a
        bad state, so it is dropped before re-raising and the next call reopens a fresh one.
        """
        try:
            return [record.data() for record in self._session().run(query, **params)]
        except Exception:
            self._close_session()
            raise

    def _load_module_keys(self) -> List[str]:
        """The application's module ``file_key``s — the scope key for every other query."""
        rows = self._run(
            "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule) RETURN m.file_key AS k",
            app=self.application_name,
        )
        return [r["k"] for r in rows]

    # =====================================================================================
    # Reconstruction helpers — fetch a node's children over Cypher, then assemble via R.
    # =====================================================================================
    def _callable_full(self, props: Dict[str, Any]) -> PyCallable:
        """Rebuild a full :class:`PyCallable` (call sites, inner callables/classes, locals)."""
        sig = props["signature"]
        call_sites = [
            R.callsite(r["p"])
            for r in self._run(
                "MATCH (:PyCallable {signature: $sig})-[:PY_HAS_CALLSITE]->(s:PyCallSite) "
                "RETURN properties(s) AS p ORDER BY s.start_line, s.start_column",
                sig=sig,
            )
        ]
        inner_callables: Dict[str, PyCallable] = {}
        for r in self._run("MATCH (:PyCallable {signature: $sig})-[:PY_DECLARES]->(d:PyCallable) RETURN properties(d) AS p", sig=sig):
            ic = self._callable_full(r["p"])
            inner_callables[ic.name] = ic  # inner_callables keyed by short name
        inner_classes: Dict[str, PyClass] = {}
        for r in self._run("MATCH (:PyCallable {signature: $sig})-[:PY_DECLARES]->(d:PyClass) RETURN properties(d) AS p", sig=sig):
            ic2 = self._class_full(r["p"])
            inner_classes[ic2.signature] = ic2  # inner_classes keyed by signature
        local_variables = [
            R.variable(r["p"])
            for r in self._run(
                "MATCH (:PyCallable {signature: $sig})-[:PY_DECLARES_VAR]->(v:PyVariable) "
                "RETURN properties(v) AS p ORDER BY v.start_line, v.name",
                sig=sig,
            )
        ]
        return R.callable_(props, call_sites=call_sites, inner_callables=inner_callables, inner_classes=inner_classes, local_variables=local_variables)

    def _class_full(self, props: Dict[str, Any]) -> PyClass:
        """Rebuild a full :class:`PyClass` (methods, attributes, inner classes)."""
        sig = props["signature"]
        methods: Dict[str, PyCallable] = {}
        for r in self._run("MATCH (:PyClass {signature: $sig})-[:PY_HAS_METHOD]->(m:PyCallable) RETURN properties(m) AS p", sig=sig):
            m = self._callable_full(r["p"])
            methods[m.name] = m  # methods keyed by short name
        attributes: Dict[str, PyClassAttribute] = {}
        for r in self._run("MATCH (:PyClass {signature: $sig})-[:PY_HAS_ATTRIBUTE]->(a:PyAttribute) RETURN properties(a) AS p", sig=sig):
            a = R.attribute(r["p"])
            attributes[a.name] = a  # attributes keyed by name
        inner_classes: Dict[str, PyClass] = {}
        for r in self._run("MATCH (:PyClass {signature: $sig})-[:PY_DECLARES]->(ic:PyClass) RETURN properties(ic) AS p", sig=sig):
            ic = self._class_full(r["p"])
            inner_classes[ic.signature] = ic  # inner_classes keyed by signature
        return R.class_(props, methods=methods, attributes=attributes, inner_classes=inner_classes)

    def _module_full(self, props: Dict[str, Any]) -> PyModule:
        """Rebuild a full :class:`PyModule` (top-level classes, functions, variables, imports)."""
        file_key = props["file_key"]
        classes: Dict[str, PyClass] = {}
        for r in self._run("MATCH (:PyModule {file_key: $fk})-[:PY_DECLARES]->(c:PyClass) RETURN properties(c) AS p", fk=file_key):
            c = self._class_full(r["p"])
            classes[c.signature] = c  # module.classes keyed by signature
        functions: Dict[str, PyCallable] = {}
        for r in self._run("MATCH (:PyModule {file_key: $fk})-[:PY_DECLARES]->(f:PyCallable) RETURN properties(f) AS p", fk=file_key):
            fn = self._callable_full(r["p"])
            functions[fn.name] = fn  # module.functions keyed by short name
        variables = [
            R.variable(r["p"])
            for r in self._run(
                "MATCH (:PyModule {file_key: $fk})-[:PY_DECLARES_VAR]->(v:PyVariable) RETURN properties(v) AS p ORDER BY v.start_line, v.name",
                fk=file_key,
            )
        ]
        imports = self._module_imports(file_key)
        return R.module(props, file_key=file_key, classes=classes, functions=functions, variables=variables, imports=imports)

    def _module_imports(self, file_key: str) -> List[Any]:
        """Best-effort :class:`PyImport` list from the aggregated ``PY_IMPORTS`` edges."""
        out: List[Any] = []
        for r in self._run(
            "MATCH (:PyModule {file_key: $fk})-[e:PY_IMPORTS]->(p:PyPackage) "
            "RETURN p.name AS module, e.imported_names AS names",
            fk=file_key,
        ):
            names = r.get("names") or []
            if names:
                out.extend(R.import_(r["module"], n) for n in names)
            else:
                out.append(R.import_(r["module"], r["module"]))
        return out

    def _call_rows(self) -> List[Dict[str, Any]]:
        """Raw ``PY_CALLS`` edge rows scoped to this application (by source module)."""
        return self._run(
            "MATCH (s:PySymbol)-[r:PY_CALLS]->(t:PySymbol) WHERE s._module IN $mods "
            "RETURN s.signature AS src, t.signature AS tgt, properties(r) AS p",
            mods=self._modules,
        )

    # =====================================================================================
    # PythonAnalysisBackend — application / whole-program
    # =====================================================================================
    def get_application_view(self) -> PyApplication:
        return PyApplication(symbol_table=self.get_symbol_table(), call_graph=self._call_edges())

    def get_symbol_table(self) -> Dict[str, PyModule]:
        result: Dict[str, PyModule] = {}
        for r in self._run(
            "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule) RETURN properties(m) AS p",
            app=self.application_name,
        ):
            mod = self._module_full(r["p"])
            result[mod.file_path] = mod  # symbol_table keyed by file_path (== file_key)
        return result

    def get_modules(self) -> List[PyModule]:
        return list(self.get_symbol_table().values())

    def get_python_module(self, file_path: str) -> PyModule | None:
        rows = self._run(
            "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule {file_key: $fk}) RETURN properties(m) AS p LIMIT 1",
            app=self.application_name,
            fk=str(file_path),
        )
        return self._module_full(rows[0]["p"]) if rows else None

    def get_python_file(self, qualified_class_name: str) -> str | None:
        # Only top-level classes are in the in-memory _class_to_file map (module.classes).
        rows = self._run(
            "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass {signature: $sig}) WHERE c._module IN $mods RETURN c._module AS fk LIMIT 1",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return rows[0]["fk"] if rows else None

    # =====================================================================================
    # call graph
    # =====================================================================================
    def _call_edges(self) -> List[PyCallEdge]:
        """The application's call edges as ``PyCallEdge`` records (``PyApplication.call_graph``)."""
        return [
            PyCallEdge(
                source=r["src"],
                target=r["tgt"],
                weight=r["p"].get("weight", 1),
                provenance=list(r["p"].get("provenance", []) or []),
            )
            for r in self._call_rows()
        ]

    def _build_call_graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for r in self._call_rows():
            p = r["p"]
            graph.add_edge(r["src"], r["tgt"], type="CALL_DEP", weight=p.get("weight", 1), provenance=tuple(p.get("provenance", []) or []))
        return graph

    def get_call_graph(self) -> nx.DiGraph:
        if self._call_graph is None:
            self._call_graph = self._build_call_graph()
        return self._call_graph

    def get_call_graph_json(self) -> str:
        return model_dump_json(self.get_application_view(), indent=None)

    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(target_class_name, target_method_declaration)
        if method is None or method.signature not in graph:
            return {"caller_details": []}
        callers = [{"caller_signature": src, "edge": graph.get_edge_data(src, method.signature)} for src in graph.predecessors(method.signature)]
        return {"target_method": method.signature, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(source_class_name, source_method_declaration)
        if method is None or method.signature not in graph:
            return {"callee_details": []}
        callees = [{"callee_signature": tgt, "edge": graph.get_edge_data(method.signature, tgt)} for tgt in graph.successors(method.signature)]
        return {"source_method": method.signature, "callee_details": callees}

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        graph = self.get_call_graph()
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return []
        if method_signature is not None:
            method = self.get_method(qualified_class_name, method_signature)
            if method is None:
                return []
            return list(nx.edge_dfs(graph, source=method.signature))
        edges: List[Tuple[str, str]] = []
        for method in cls.methods.values():
            if method.signature in graph:
                edges.extend(nx.edge_dfs(graph, source=method.signature))
        return edges

    # =====================================================================================
    # classes
    # =====================================================================================
    def get_all_classes(self) -> Dict[str, PyClass]:
        result: Dict[str, PyClass] = {}
        for r in self._run(
            "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass) WHERE c._module IN $mods RETURN properties(c) AS p",
            mods=self._modules,
        ):
            c = self._class_full(r["p"])
            result[c.signature] = c
        return result

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        # Top-level classes only, matching get_all_classes().get(...) on the in-memory backend.
        rows = self._run(
            "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass {signature: $sig}) WHERE c._module IN $mods RETURN properties(c) AS p LIMIT 1",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return self._class_full(rows[0]["p"]) if rows else None

    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        cls = self.get_class(qualified_class_name)
        return list(cls.inner_classes.values()) if cls else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return {}
        short_name = cls.name
        result: Dict[str, PyClass] = {}
        for sig, candidate in self.get_all_classes().items():
            if sig == qualified_class_name:
                continue
            for base in candidate.base_classes:
                if base == short_name or base == qualified_class_name or base.endswith("." + short_name):
                    result[sig] = candidate
                    break
        return result

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        cls = self.get_class(qualified_class_name)
        return list(cls.base_classes) if cls else []

    # =====================================================================================
    # methods / fields
    # =====================================================================================
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        result: Dict[str, Dict[str, PyCallable]] = {}
        for module in self.get_symbol_table().values():
            for class_sig, cls in module.classes.items():
                result[class_sig] = dict(cls.methods)
            if module.functions:
                result.setdefault(module.module_name, {}).update(module.functions)
        return result

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        cls = self.get_class(qualified_class_name)
        return dict(cls.methods) if cls else {}

    def _get_module_functions(self, module_name: str) -> Dict[str, PyCallable]:
        """Fetch a module's top-level functions by ``module_name`` (not ``file_key``) — the scope
        key ``get_method`` accepts for module-level lookups, mirroring
        ``get_all_methods_in_application``'s module outer key. A single scoped query, so it stays
        as cheap as the class path instead of paying the whole-symbol-table fan-out.
        """
        rows = self._run(
            "MATCH (m:PyModule {module_name: $name})-[:PY_DECLARES]->(f:PyCallable) "
            "WHERE m.file_key IN $mods RETURN properties(f) AS p",
            name=module_name,
            mods=self._modules,
        )
        return {fn.name: fn for fn in (self._callable_full(r["p"]) for r in rows)}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> PyCallable | None:
        """Return a specific method or module-level function by scope and name (see
        :meth:`PythonAnalysisBackend.get_method`).

        ``qualified_class_name`` resolves as a class signature first; if no such class exists it
        is treated as a module name and resolved against that module's top-level functions.
        """
        cls = self.get_class(qualified_class_name)
        methods = dict(cls.methods) if cls is not None else self._get_module_functions(qualified_class_name)
        if qualified_method_name in methods:
            return methods[qualified_method_name]
        for sig, callable_ in methods.items():
            if callable_.name == qualified_method_name:
                return callable_
        return None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        return {sig: c for sig, c in self.get_all_methods_in_class(qualified_class_name).items() if c.name == "__init__"}

    def get_all_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    # =====================================================================================
    # PythonAnalysisBackend — bulk / projected accessors (one round-trip each)
    # =====================================================================================
    # Field-projected RETURNs that sidestep the per-entity reconstruction fan-out: each is a single
    # Cypher statement, not the N+1 walk get_symbol_table()/get_all_methods_in_application() pays.
    _OVERVIEW_PROJECTION = (
        "OPTIONAL MATCH (owner:PyClass)-[:PY_HAS_METHOD]->(c) "
        "RETURN c.signature AS signature, c.name AS name, c.decorators AS decorators, "
        "c.path AS path, c.start_line AS start_line, c.end_line AS end_line, "
        "owner.signature AS class_signature"
    )

    def get_callables_overview(self) -> List[PyCallableOverview]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods " + self._OVERVIEW_PROJECTION,
            mods=self._modules,
        )
        return [R.overview(r) for r in rows]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.signature IN $sigs "
            "RETURN c.signature AS signature, c.code AS code",
            mods=self._modules,
            sigs=list(signatures),
        )
        return {r["signature"]: r.get("code") for r in rows}

    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods "
            "AND any(d IN c.decorators WHERE d IN $markers) " + self._OVERVIEW_PROJECTION,
            mods=self._modules,
            markers=list(markers),
        )
        return [R.overview(r) for r in rows]

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        # OPTIONAL MATCH so a requested callable with no call sites still yields a row (p is null),
        # giving it an empty-list entry — parity with the in-process backend, which keys every
        # existing signature. ORDER mirrors _callable_full's call-site ordering.
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.signature IN $sigs "
            "OPTIONAL MATCH (c)-[:PY_HAS_CALLSITE]->(s:PyCallSite) "
            "RETURN c.signature AS owner, properties(s) AS p "
            "ORDER BY s.start_line, s.start_column",
            mods=self._modules,
            sigs=list(signatures),
        )
        out: Dict[str, List[PyCallsite]] = {}
        for r in rows:
            sites = out.setdefault(r["owner"], [])
            if r["p"] is not None:
                sites.append(R.callsite(r["p"]))
        return out
