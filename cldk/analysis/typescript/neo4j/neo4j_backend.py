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

"""Neo4j-backed TypeScript analysis backend.

A drop-in alternative to :class:`TSCodeanalyzer`: it exposes the **same query
method surface** (``get_all_classes``, ``get_call_graph``, ``get_all_callers``,
...) so the :class:`TypeScriptAnalysis` facade can delegate to either one, but
every method answers by running **Cypher over a live Neo4j graph** instead of
walking the in-memory pydantic/NetworkX structures.

The graph is the one ``codeanalyzer-typescript`` emits with ``--emit neo4j``
(schema: ``codeanalyzer-ts/schema.neo4j.json``). On construction this backend can
populate the database for you by shelling out to the analyzer binary with
``--emit neo4j --neo4j-uri ...`` (mirroring how :class:`TSCodeanalyzer` shells
out to produce ``analysis.json``); or you can point it at an already-loaded DB
with ``build_db=False``.

Identity model (must match the in-memory backend):

* a callable/class/interface/enum/type-alias is a ``:Symbol`` keyed by ``signature``;
* call-graph edges are ``(:Symbol)-[:CALLS]->(:Symbol|:External)``;
* every project-owned node carries a ``_module`` provenance property, so a single
  database may hold several applications — all queries here are scoped to this
  backend's application by the set of its module ``file_key``s.

Parity caveats (inherent to what the projection stores, not bugs):

* ``CALLS`` edge ``tags`` only round-trip the three keys the projection keeps
  (``ts.dispatch`` / ``ts.external`` / ``ts.module``);
* ``get_imports`` / ``get_all_exports`` are reconstructed from the *aggregated*
  ``IMPORTS`` / ``RE_EXPORTS`` edges (individual bindings, aliases and positions
  are not stored);
* comments collapse to a single docstring, type-parameters keep only their names.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections import deque
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Union

import networkx as nx

from cldk.analysis import AnalysisLevel
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
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class TSNeo4jBackend(TSAnalysisBackend):
    """Build and query the application view of a TypeScript project over Neo4j (Cypher).

    Args:
        project_dir: Root of the TypeScript project (required when ``build_db`` is True).
        analysis_backend_path: Directory containing the ``codeanalyzer-typescript`` binary. If
            None, falls back to ``$CODEANALYZER_TS_BIN``, then the ``codeanalyzer_typescript``
            wheel, then a bundled binary.
        analysis_level: ``AnalysisLevel.symbol_table`` (1) or ``AnalysisLevel.call_graph`` (2).
        eager_analysis: If True, force a clean rebuild of the graph even if this application's
            ``:Application`` anchor already exists in the database.
        target_files: Restrict analysis to these files (incremental push).
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials.
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:Application`` anchor name. Defaults to the project directory
            name, matching ``codeanalyzer-typescript``'s ``--app-name`` default.
        build_db: If True (default), populate the database from ``project_dir`` on construction.
            If False, query an already-loaded graph (``project_dir`` may be None).
    """

    def __init__(
        self,
        project_dir: Union[str, Path, None],
        analysis_backend_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
        neo4j_uri: str,
        neo4j_username: str,
        neo4j_password: str,
        neo4j_database: str | None = None,
        application_name: str | None = None,
        build_db: bool = True,
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with " "`pip install neo4j` (or `pip install cldk[neo4j]`).") from e

        self.project_dir = project_dir
        self.analysis_backend_path = analysis_backend_path
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.application_name = application_name or (Path(project_dir).name if project_dir else None)
        if not self.application_name:
            raise CodeanalyzerExecutionException("application_name could not be inferred; pass application_name explicitly when project_dir is None.")
        self._database = neo4j_database
        self._driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        self._neo4j_conn = (neo4j_uri, neo4j_username, neo4j_password)

        if build_db:
            if project_dir is None:
                raise CodeanalyzerExecutionException("project_dir is required when build_db=True.")
            self._build_graph()

        # The application's module file_keys, used to scope every query to this app.
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

    def _load_module_keys(self) -> List[str]:
        rows = self._run(
            "MATCH (:Application {name: $app})-[:HAS_MODULE]->(m:Module) RETURN m.file_key AS k",
            app=self.application_name,
        )
        return [r["k"] for r in rows]

    # -----[ binary resolution + DB population ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """Resolve the codeanalyzer-typescript executable command (mirrors TSCodeanalyzer)."""
        if self.analysis_backend_path:
            backend = Path(self.analysis_backend_path)
            binary = next(
                (p for p in backend.rglob("codeanalyzer-typescript*") if p.is_file()),
                None,
            ) or next((p for p in backend.rglob("codeanalyzer-ts*") if p.is_file()), None)
            if binary is None:
                raise CodeanalyzerExecutionException("codeanalyzer-typescript binary not found in the provided analysis_backend_path.")
            return [str(binary)]

        env_bin = os.environ.get("CODEANALYZER_TS_BIN")
        if env_bin:
            return shlex.split(env_bin)

        try:
            import codeanalyzer_typescript

            return [str(codeanalyzer_typescript.bin_path())]
        except (ModuleNotFoundError, FileNotFoundError):
            pass

        try:
            with resources.as_file(resources.files("cldk.analysis.typescript.codeanalyzer.bin")) as bin_dir:
                binary = next((p for p in bin_dir.iterdir() if p.is_file() and p.name.startswith("codeanalyzer")), None)
                if binary is not None:
                    return [str(binary)]
        except (ModuleNotFoundError, FileNotFoundError):
            pass

        raise CodeanalyzerExecutionException(
            "codeanalyzer-typescript binary not found. Pass analysis_backend_path=<dir containing the "
            "binary>, set $CODEANALYZER_TS_BIN, or bundle it under cldk/analysis/typescript/codeanalyzer/bin/."
        )

    def _build_graph(self) -> None:
        """Push this project's graph into Neo4j via ``--emit neo4j --neo4j-uri`` (Bolt).

        Lazy by default: if the ``:Application`` anchor already exists and ``eager_analysis`` is
        False (and this is not a targeted/incremental run), the push is skipped.
        """
        if not self.eager_analysis and not self.target_files and self._application_exists():
            logger.info("Neo4j already has application '%s'; skipping rebuild (lazy).", self.application_name)
            return

        uri, user, password = self._neo4j_conn
        level = 1 if self.analysis_level == AnalysisLevel.symbol_table else 2
        args = self._get_codeanalyzer_exec() + [
            "-i",
            str(Path(self.project_dir)),
            "-a",
            str(level),
            "--emit",
            "neo4j",
            "--neo4j-uri",
            uri,
            "--neo4j-user",
            user,
            "--neo4j-password",
            password,
            "--app-name",
            self.application_name,
        ]
        if self._database:
            args += ["--neo4j-database", self._database]
        if self.eager_analysis:
            args += ["--eager"]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]

        try:
            logger.info("Running codeanalyzer-typescript (neo4j emit): %s", " ".join(args))
            subprocess.run(args, capture_output=True, text=True, check=True)
        except Exception as e:  # noqa: BLE001
            raise CodeanalyzerExecutionException(str(e)) from e

    def _application_exists(self) -> bool:
        rows = self._run("MATCH (a:Application {name: $app}) RETURN count(a) AS c", app=self.application_name)
        return bool(rows and rows[0]["c"] > 0)

    # -----[ child-fetch helpers (reconstruction) ]-----
    def _decorators_of(self, signature: str) -> List[TSDecorator]:
        rows = self._run(
            "MATCH (s:Symbol {signature: $sig})-[r:DECORATED_BY]->(d:Decorator) " "RETURN properties(d) AS node, properties(r) AS edge ORDER BY r.start_line",
            sig=signature,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def _attribute_decorators(self, attr_id: str) -> List[TSDecorator]:
        rows = self._run(
            "MATCH (a:Attribute {id: $id})-[r:DECORATED_BY]->(d:Decorator) " "RETURN properties(d) AS node, properties(r) AS edge ORDER BY r.start_line",
            id=attr_id,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def _callsites_of(self, signature: str) -> List[TSCallsite]:
        rows = self._run(
            "MATCH (c:Callable {signature: $sig})-[:HAS_CALLSITE]->(cs:CallSite) " "RETURN properties(cs) AS p ORDER BY cs.start_line, cs.start_column",
            sig=signature,
        )
        return [R.callsite(r["p"]) for r in rows]

    def _callable_full(self, props: Dict[str, Any]) -> TSCallable:
        sig = props["signature"]
        # Symbol-keyed containers are keyed by signature (matching the analyzer's dict keys).
        inner_callables = {p["signature"]: self._callable_full(p) for p in self._children(sig, "DECLARES", "Callable")}
        inner_classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "DECLARES", "Class")}
        return R.callable_(
            props,
            decorators=self._decorators_of(sig),
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
        # methods keyed by the analyzer's method-key; inner_classes by signature; attributes by name.
        methods = {self._method_key(p): self._callable_full(p) for p in self._members(sig, "HAS_METHOD", "Callable")}
        attributes: Dict[str, TSClassAttribute] = {}
        for p in self._members(sig, "HAS_ATTRIBUTE", "Attribute"):
            attributes[p["name"]] = R.attribute(p, self._attribute_decorators(p.get("id", "")))
        inner_classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "DECLARES", "Class")}
        return R.class_(
            props,
            decorators=self._decorators_of(sig),
            methods=methods,
            attributes=attributes,
            inner_classes=inner_classes,
        )

    def _interface_full(self, props: Dict[str, Any]) -> TSInterface:
        sig = props["signature"]
        methods = {self._method_key(p): self._callable_full(p) for p in self._members(sig, "HAS_METHOD", "Callable")}
        properties: Dict[str, TSClassAttribute] = {}
        for p in self._members(sig, "HAS_ATTRIBUTE", "Attribute"):
            properties[p["name"]] = R.attribute(p, self._attribute_decorators(p.get("id", "")))
        return R.interface(props, methods=methods, properties=properties)

    def _children(self, signature: str, rel: str, label: str) -> List[Dict[str, Any]]:
        """Property maps of ``label`` nodes reached from a symbol via ``rel`` (one hop), in
        declaration (source) order."""
        rows = self._run(
            f"MATCH (s:Symbol {{signature: $sig}})-[:{rel}]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
            sig=signature,
        )
        return [r["p"] for r in rows]

    def _members(self, signature: str, rel: str, label: str) -> List[Dict[str, Any]]:
        """Property maps of member ``label`` nodes (methods/attributes), in declaration order."""
        rows = self._run(
            f"MATCH (s:Symbol {{signature: $sig}})-[:{rel}]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
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
            "MATCH (s:Symbol)-[:CALLS]->(e:External) WHERE s._module IN $mods "
            "RETURN DISTINCT properties(e) AS p "
            "UNION "
            "MATCH (cs:CallSite)-[:RESOLVES_TO]->(e:External) WHERE cs._module IN $mods "
            "RETURN DISTINCT properties(e) AS p",
            mods=self._modules,
        )
        return {r["p"]["signature"]: R.external(r["p"]) for r in rows}

    def get_typescript_file(self, qualified_name: str) -> str | None:
        rows = self._run(
            "MATCH (s:Symbol {signature: $sig}) WHERE s._module IN $mods RETURN s._module AS m LIMIT 1",
            sig=qualified_name,
            mods=self._modules,
        )
        return rows[0]["m"] if rows else None

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        rows = self._run("MATCH (m:Module {file_key: $key}) RETURN properties(m) AS p", key=file_path)
        if not rows:
            return None
        props = rows[0]["p"]
        # All symbol containers are keyed by signature (matching the analyzer's dict keys).
        classes = {p["signature"]: self._class_full(p) for p in self._module_decls(file_path, "Class")}
        interfaces = {p["signature"]: self._interface_full(p) for p in self._module_decls(file_path, "Interface")}
        enums = {p["signature"]: R.enum(p) for p in self._module_decls(file_path, "Enum")}
        type_aliases = {p["signature"]: R.type_alias(p) for p in self._module_decls(file_path, "TypeAlias")}
        functions = {p["signature"]: self._callable_full(p) for p in self._module_decls(file_path, "Callable")}
        namespaces = {p["signature"]: self._namespace_full(p) for p in self._module_decls(file_path, "Namespace")}
        variables = self._module_variables(file_path)
        imports = self._module_imports(file_path)
        exports = self._module_exports(file_path)
        return R.module(
            props,
            classes=classes,
            interfaces=interfaces,
            enums=enums,
            type_aliases=type_aliases,
            functions=functions,
            namespaces=namespaces,
            variables=variables,
            imports=imports,
            exports=exports,
        )

    def _module_decls(self, file_key: str, label: str) -> List[Dict[str, Any]]:
        rows = self._run(
            f"MATCH (m:Module {{file_key: $key}})-[:DECLARES]->(n:{label}) " "RETURN properties(n) AS p ORDER BY n.start_line, n.name",
            key=file_key,
        )
        return [r["p"] for r in rows]

    def _namespace_full(self, props: Dict[str, Any]):
        sig = props["signature"]
        classes = {p["signature"]: self._class_full(p) for p in self._children(sig, "DECLARES", "Class")}
        interfaces = {p["signature"]: self._interface_full(p) for p in self._children(sig, "DECLARES", "Interface")}
        enums = {p["signature"]: R.enum(p) for p in self._children(sig, "DECLARES", "Enum")}
        type_aliases = {p["signature"]: R.type_alias(p) for p in self._children(sig, "DECLARES", "TypeAlias")}
        functions = {p["signature"]: self._callable_full(p) for p in self._children(sig, "DECLARES", "Callable")}
        namespaces = {p["signature"]: self._namespace_full(p) for p in self._children(sig, "DECLARES", "Namespace")}
        rows = self._run(
            "MATCH (s:Symbol {signature: $sig})-[:DECLARES_VAR]->(v:Variable) RETURN properties(v) AS p",
            sig=sig,
        )
        variables = [R.variable(r["p"]) for r in rows]
        return R.namespace(
            props,
            classes=classes,
            interfaces=interfaces,
            enums=enums,
            type_aliases=type_aliases,
            functions=functions,
            namespaces=namespaces,
            variables=variables,
        )

    def _module_variables(self, file_key: str) -> List[TSVariableDeclaration]:
        rows = self._run(
            "MATCH (m:Module {file_key: $key})-[:DECLARES_VAR]->(v:Variable) RETURN properties(v) AS p",
            key=file_key,
        )
        return [R.variable(r["p"]) for r in rows]

    def _module_imports(self, file_key: str) -> List[TSImport]:
        """Best-effort: synthesize one TSImport per imported name on each aggregated IMPORTS edge.

        The projection collapses every binding to a module-pair into a single edge carrying
        ``imported_names`` / ``import_kinds`` / ``is_type_only``, so per-binding aliases, kinds
        and positions are not recoverable.
        """
        rows = self._run(
            "MATCH (m:Module {file_key: $key})-[r:IMPORTS]->(t) " "RETURN coalesce(t.file_key, t.name) AS target, properties(r) AS edge",
            key=file_key,
        )
        out: List[TSImport] = []
        for r in rows:
            edge = r["edge"]
            kinds = edge.get("import_kinds", []) or []
            kind = kinds[0] if len(kinds) == 1 else "named"
            type_only = edge.get("is_type_only", False)
            names = edge.get("imported_names", []) or []
            if not names:
                out.append(TSImport(module=r["target"], name="", import_kind=kind, is_type_only=type_only))
            for name in names:
                out.append(TSImport(module=r["target"], name=name, import_kind=kind, is_type_only=type_only))
        return out

    def _module_exports(self, file_key: str) -> List[TSExport]:
        """Best-effort: only re-exports survive as edges (local exports become ``is_exported`` props)."""
        rows = self._run(
            "MATCH (m:Module {file_key: $key})-[:RE_EXPORTS]->(t) " "RETURN coalesce(t.file_key, t.name) AS target",
            key=file_key,
        )
        return [TSExport(module=r["target"], name="*", export_kind="re_export") for r in rows]

    # -----[ call graph ]-----
    def _call_edges(self) -> List[TSCallEdge]:
        rows = self._run(
            "MATCH (s:Symbol)-[r:CALLS]->(t:Symbol) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt, properties(r) AS edge",
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
        """Invert the flattened CALLS-edge tag props back into the ``ts.*`` tag dict."""
        tags: Dict[str, str] = {}
        if edge.get("dispatch") is not None:
            tags["ts.dispatch"] = edge["dispatch"]
        if edge.get("external") is True:
            tags["ts.external"] = "true"
        if edge.get("module") is not None:
            tags["ts.module"] = edge["module"]
        return tags

    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of callable signatures (+ phantom external symbols) and CALLS edges."""
        graph = nx.DiGraph()
        # Internal callable nodes (with the reconstructed callable, matching the in-memory backend).
        for props in self._all_callable_props():
            graph.add_node(props["signature"], callable=self._callable_full(props), external=False)
        # Phantom (external) nodes so import-attributed edges don't dangle.
        for sig, ext in self.get_external_symbols().items():
            graph.add_node(sig, external=True, module=ext.module, name=ext.name)
        # Edges (auto-create any endpoint not added above, matching nx.add_edge semantics).
        for r in self._run(
            "MATCH (s:Symbol)-[r:CALLS]->(t:Symbol) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt, properties(r) AS edge",
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
        rows = self._run("MATCH (c:Callable) WHERE c._module IN $mods RETURN properties(c) AS p", mods=self._modules)
        return [r["p"] for r in rows]

    def _resolve_signature(self, class_or_sig: str, member: str | None = None) -> str:
        """Resolve a ``(class/module, member)`` pair (or a bare signature) to a signature string."""
        if member is None:
            return class_or_sig
        rows = self._run(
            "MATCH (o:Symbol {signature: $owner})-[:HAS_METHOD]->(m:Callable {name: $name}) " "RETURN m.signature AS sig LIMIT 1",
            owner=class_or_sig,
            name=member,
        )
        if rows:
            return rows[0]["sig"]
        composed = f"{class_or_sig}.{member}"
        rows = self._run("MATCH (c:Callable {signature: $sig}) RETURN c.signature AS sig LIMIT 1", sig=composed)
        return rows[0]["sig"] if rows else composed

    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        target = self._resolve_signature(target_class_name, target_method_declaration)
        rows = self._run(
            "MATCH (src:Symbol)-[r:CALLS]->(t:Symbol {signature: $target}) WHERE src._module IN $mods " "RETURN src.signature AS caller, properties(r) AS edge",
            target=target,
            mods=self._modules,
        )
        caller_details = [{"caller_signature": r["caller"], "edge": self._edge_dict(r["edge"])} for r in rows]
        return {"target_method": target, "caller_details": caller_details}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        source = self._resolve_signature(source_class_name, source_method_declaration)
        rows = self._run(
            "MATCH (s:Symbol {signature: $source})-[r:CALLS]->(tgt:Symbol) " "RETURN tgt.signature AS callee, properties(r) AS edge",
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
            "MATCH (s:Symbol)-[:CALLS]->(t:Symbol) WHERE s._module IN $mods " "RETURN s.signature AS src, t.signature AS tgt ORDER BY src, tgt",
            mods=self._modules,
        ):
            adjacency.setdefault(r["src"], []).append(r["tgt"])

        if method_signature is not None:
            seeds = [method_signature]
        else:
            seeds = [p["signature"] for p in self._members(qualified_class_name, "HAS_METHOD", "Callable")]
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
        graph = nx.DiGraph()
        rows = self._run(
            "MATCH (n:Symbol) WHERE (n:Class OR n:Interface) AND n._module IN $mods " "RETURN n.signature AS sig, n.base_classes AS bases",
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
            "MATCH (cs:CallSite) WHERE cs._module IN $mods AND cs.callee_signature = $sig " "AND cs.start_line >= 0 RETURN DISTINCT cs.start_line AS line ORDER BY line",
            mods=self._modules,
            sig=target_signature,
        )
        return [r["line"] for r in rows]

    def get_call_targets(self, source_signature: str) -> Set[str]:
        rows = self._run(
            "MATCH (c:Callable {signature: $sig})-[:HAS_CALLSITE]->(cs:CallSite) " "RETURN cs.callee_signature AS cosig, cs.method_name AS mn",
            sig=source_signature,
        )
        return {(r["cosig"] or r["mn"]) for r in rows}

    # -----[ classes / interfaces / enums / type-aliases ]-----
    def get_all_classes(self) -> Dict[str, TSClass]:
        rows = self._run("MATCH (c:Class) WHERE c._module IN $mods RETURN properties(c) AS p", mods=self._modules)
        return {r["p"]["signature"]: self._class_full(r["p"]) for r in rows}

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        rows = self._run(
            "MATCH (c:Class {signature: $sig}) WHERE c._module IN $mods RETURN properties(c) AS p",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return self._class_full(rows[0]["p"]) if rows else None

    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        rows = self._run("MATCH (i:Interface) WHERE i._module IN $mods RETURN properties(i) AS p", mods=self._modules)
        return {r["p"]["signature"]: self._interface_full(r["p"]) for r in rows}

    def get_all_enums(self) -> Dict[str, TSEnum]:
        rows = self._run("MATCH (e:Enum) WHERE e._module IN $mods RETURN properties(e) AS p", mods=self._modules)
        return {r["p"]["signature"]: R.enum(r["p"]) for r in rows}

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        rows = self._run("MATCH (e:Enum {signature: $sig}) RETURN properties(e) AS p", sig=qualified_enum_name)
        return R.enum(rows[0]["p"]).members if rows else []

    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        rows = self._run("MATCH (t:TypeAlias) WHERE t._module IN $mods RETURN properties(t) AS p", mods=self._modules)
        return {r["p"]["signature"]: R.type_alias(r["p"]) for r in rows}

    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        return [self._class_full(p) for p in self._children(qualified_class_name, "DECLARES", "Class")]

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        rows = self._run(
            "MATCH (c:Class) WHERE c._module IN $mods AND $sig IN c.base_classes " "RETURN properties(c) AS p",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return {r["p"]["signature"]: self._class_full(r["p"]) for r in rows}

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        rows = self._run(
            "MATCH (c:Class {signature: $sig}) RETURN c.base_classes AS bases, c.implements_types AS impl",
            sig=qualified_class_name,
        )
        if not rows:
            return []
        bases = rows[0]["bases"] or []
        impl = set(rows[0]["impl"] or [])
        return [b for b in bases if b not in impl]

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        rows = self._run("MATCH (c:Class {signature: $sig}) RETURN c.implements_types AS impl", sig=qualified_class_name)
        return list(rows[0]["impl"] or []) if rows else []

    # -----[ methods / functions / fields ]-----
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        # Mirror the in-memory `_methods_by_class`: an entry for *every* class and interface
        # (even those with no methods), each keyed by the method's short name.
        out: Dict[str, Dict[str, TSCallable]] = {}
        for r in self._run(
            "MATCH (n:Symbol) WHERE (n:Class OR n:Interface) AND n._module IN $mods " "RETURN n.signature AS sig",
            mods=self._modules,
        ):
            out[r["sig"]] = {}
        for r in self._run(
            "MATCH (owner:Symbol)-[:HAS_METHOD]->(m:Callable) WHERE owner._module IN $mods " "RETURN owner.signature AS owner, properties(m) AS p",
            mods=self._modules,
        ):
            out.setdefault(r["owner"], {})[r["p"]["name"]] = self._callable_full(r["p"])
        return out

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {p["name"]: self._callable_full(p) for p in self._members(qualified_class_name, "HAS_METHOD", "Callable")}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        rows = self._run(
            "MATCH (o:Symbol {signature: $sig})-[:HAS_METHOD]->(m:Callable {name: $name}) " "RETURN properties(m) AS p LIMIT 1",
            sig=qualified_class_name,
            name=qualified_method_name,
        )
        return self._callable_full(rows[0]["p"]) if rows else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {p["name"]: self._callable_full(p) for p in self._members(qualified_class_name, "HAS_METHOD", "Callable") if p.get("kind") == "constructor"}

    def get_all_functions(self) -> Dict[str, TSCallable]:
        rows = self._run(
            "MATCH (parent)-[:DECLARES]->(c:Callable) " "WHERE (parent:Module OR parent:Namespace) AND c._module IN $mods " "RETURN properties(c) AS p",
            mods=self._modules,
        )
        return {r["p"]["signature"]: self._callable_full(r["p"]) for r in rows}

    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        return [R.attribute(p, self._attribute_decorators(p.get("id", ""))) for p in self._members(qualified_class_name, "HAS_ATTRIBUTE", "Attribute")]

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        return [R.attribute(p, self._attribute_decorators(p.get("id", ""))) for p in self._members(qualified_interface_name, "HAS_ATTRIBUTE", "Attribute")]

    # -----[ imports / exports / variables ]-----
    def get_imports(self) -> Dict[str, List[TSImport]]:
        return {key: self._module_imports(key) for key in self._modules}

    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        return {key: self._module_exports(key) for key in self._modules}

    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        return {key: self._module_variables(key) for key in self._modules}

    # -----[ decorators ]-----
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        return self._decorators_of(qualified_callable_name)

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        return self._decorators_of(qualified_class_name)

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        rows = self._run(
            "MATCH (c:Callable)-[:DECORATED_BY]->(d:Decorator) " "WHERE c._module IN $mods AND d.name IN $names " "RETURN d.name AS dn, c.signature AS sig",
            mods=self._modules,
            names=decorators,
        )
        for r in rows:
            result[r["dn"]].append(r["sig"])
        return result

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        rows = self._run(
            "MATCH (c:Class)-[:DECORATED_BY]->(d:Decorator) " "WHERE c._module IN $mods AND d.name IN $names " "RETURN d.name AS dn, c.signature AS sig",
            mods=self._modules,
            names=decorators,
        )
        for r in rows:
            result[r["dn"]].append(r["sig"])
        return result
