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

"""Neo4j-backed Java analysis backend (read-only Cypher client).

A drop-in alternative to :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: it exposes the
**same query method surface** (the 36 methods of :class:`JavaAnalysisBackend`) so the
:class:`~cldk.analysis.java.JavaAnalysis` facade can delegate to either one, but instead of running
the analyzer JAR it **reconstructs the canonical ``JApplication`` from a Neo4j graph** (the one
``codeanalyzer-java`` >= 2.4.0 emits with ``--emit neo4j``) and then answers every query with the
*identical* logic the in-memory backend uses. Mirrors the Python / TypeScript Neo4j backends.

It is purely a **query client**: it never builds the graph and has no dependency on the analyzer JAR,
a JDK, or the project sources. The graph is populated out of band — e.g. a job running
``codeanalyzer-java --emit neo4j`` — and the SDK only polls it.

Reconstruction strategy (see :mod:`reconstruct`): the backend bulk-fetches every node + relationship
for the application in a handful of Cypher queries, groups children by parent, builds an
``analysis.json``-shaped dict, and hands it to ``JApplication(**payload)`` — the same constructor
path as ``JCodeanalyzer._init_japplication``. With ``self.application`` and ``self.call_graph``
populated, the 36 query methods are the same code the in-memory backend runs.

Identity / scoping model (must match the emitter; see ``codeanalyzer-java/schema.neo4j.json``):
``:JType`` (id = fqn) and ``:JCallable`` (id = ``<fqn>#<signature>``) share a ``:JSymbol`` label;
compilation units are ``:JCompilationUnit`` keyed by ``file_key`` (== file path == symbol-table key);
call edges are ``(:JCallable)-[:J_CALLS {type, weight, source_kind, destination_kind}]->(:JCallable)``;
every project-owned node carries a ``_module`` provenance prop, so one DB can host several apps, all
scoped under ``(:JApplication {name})-[:J_HAS_UNIT]->(:JCompilationUnit)``.

Parity: this backend reconstructs everything the graph actually contains identically to the
in-memory ``JCodeanalyzer`` (verified on the daytrader8 sample — 97% of checks, the rest being the
caveats below). The ``codeanalyzer-java`` **2.4.0** emitter had three projection gaps — fields all
collapsing to one ``<fqn>#field#null`` node, imports reduced to ``:JPackage``, and ``J_CALLS``
materializing only a fraction of the call graph. All three are **fixed in 2.4.1**
(codeanalyzer-java#156/#157/#158); the SDK currently pins 2.4.0, so with the pinned emitter those
gaps still apply until 2.4.1 is released.

Inherent caveats (present even on a complete graph, NOT query-layer bugs):

* ``J_CALLS`` only links resolved app callables, so call edges to external/library targets (which the
  in-memory backend keeps as synthetic nodes) are absent;
* the call graph is built by a separate analyzer run from the in-memory backend's ``analysis.json``,
  so the two can differ by run-to-run WALA variance;
* a ``:JType``'s ``is_class_or_interface_declaration`` / ``is_concrete_class`` flags are not
  projected (only the ``kind`` discriminator is); an absent singular ``comment`` rehydrates to
  ``None``.
"""

from __future__ import annotations

import json
import logging
from itertools import chain, groupby
from typing import Any, Dict, List, Tuple, Union

import networkx as nx

from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.analysis.java.neo4j import reconstruct as R
from cldk.models.java import JGraphEdges
from cldk.models.java.enums import CRUDOperationType
from cldk.models.java.models import JApplication, JCRUDOperation, JCallable, JCallableParameter, JComment, JField, JMethodDetail, JType, JCompilationUnit, JGraphEdgesST
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class JNeo4jBackend(JavaAnalysisBackend):
    """Query the application view of a Java project over Neo4j (Cypher), read-only.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:JApplication`` anchor name to scope every query to. Matches the
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

        self._units: List[str] = self._load_unit_keys()
        self.application: JApplication = self._reconstruct_application()
        self.analysis_level = "call_graph" if self.application.call_graph else "symbol_table"
        self.call_graph: nx.DiGraph | None = self._generate_call_graph(using_symbol_table=False) if self.application.call_graph else None

    # -----[ lifecycle ]-----
    def close(self) -> None:
        """Close the underlying Neo4j driver."""
        self._driver.close()

    def __enter__(self) -> "JNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(query, **params)]

    def _load_unit_keys(self) -> List[str]:
        rows = self._run(
            "MATCH (:JApplication {name: $app})-[:J_HAS_UNIT]->(u:JCompilationUnit) RETURN u.file_key AS k",
            app=self.application_name,
        )
        return [r["k"] for r in rows]

    # =====================================================================================
    # Reconstruction: bulk-fetch the graph and rebuild the canonical JApplication.
    # =====================================================================================
    def _nodes(self, label: str) -> Dict[str, Dict[str, Any]]:
        """All nodes of a label owned by this app, keyed by id/file_key/name."""
        rows = self._run(
            f"MATCH (n:{label}) WHERE n._module IN $u RETURN coalesce(n.id, n.file_key, n.name) AS k, properties(n) AS p",
            u=self._units,
        )
        return {r["k"]: r["p"] for r in rows}

    def _adj(self, rtype: str, scope_child: bool = True) -> Dict[str, List[str]]:
        """Adjacency parent_key → [child_keys] for a relationship, scoped to this app."""
        where = "b._module IN $u" if scope_child else "a._module IN $u"
        rows = self._run(
            f"MATCH (a)-[:{rtype}]->(b) WHERE {where} "
            "RETURN coalesce(a.id, a.file_key, a.name) AS a, coalesce(b.id, b.file_key, b.name) AS b",
            u=self._units,
        )
        out: Dict[str, List[str]] = {}
        for r in rows:
            out.setdefault(r["a"], []).append(r["b"])
        return out

    def _reconstruct_application(self) -> JApplication:
        units = self._units
        # ---- node prop maps ----
        cu_nodes = {
            r["k"]: r["p"]
            for r in self._run(
                "MATCH (:JApplication {name: $app})-[:J_HAS_UNIT]->(u:JCompilationUnit) RETURN u.file_key AS k, properties(u) AS p",
                app=self.application_name,
            )
        }
        types = self._nodes("JType")
        callables = self._nodes("JCallable")
        fields = self._nodes("JField")
        params = self._nodes("JParameter")
        callsites = self._nodes("JCallSite")
        variables = self._nodes("JVariable")
        enums = self._nodes("JEnumConstant")
        records = self._nodes("JRecordComponent")
        initblocks = self._nodes("JInitializationBlock")
        crudops = self._nodes("JCrudOperation")
        crudqs = self._nodes("JCrudQuery")
        comments = self._nodes("JComment")

        # ---- adjacencies ----
        a_callable = self._adj("J_HAS_CALLABLE")
        a_field = self._adj("J_HAS_FIELD")
        a_enum = self._adj("J_HAS_ENUM_CONSTANT")
        a_record = self._adj("J_HAS_RECORD_COMPONENT")
        a_init = self._adj("J_HAS_INIT_BLOCK")
        a_param = self._adj("J_HAS_PARAMETER")
        a_callsite = self._adj("J_HAS_CALLSITE")
        a_var = self._adj("J_DECLARES_VAR")
        a_crudop = self._adj("J_HAS_CRUD_OPERATION")
        a_crudq = self._adj("J_HAS_CRUD_QUERY")
        a_comment = self._adj("J_HAS_COMMENT")
        a_import = self._run(
            "MATCH (u:JCompilationUnit)-[r:J_IMPORTS]->(t) WHERE u._module IN $u "
            "RETURN u.file_key AS cu, coalesce(t.fqn, t.name) AS path, properties(r) AS p",
            u=units,
        )

        # ---- ordered helpers ----
        def _comments_of(owner_id: str) -> List[dict]:
            ids = a_comment.get(owner_id, [])
            built = [R.comment(comments[i]) for i in ids if i in comments]
            return sorted(built, key=lambda c: (c["start_line"], c["start_column"]))

        def _first_comment(owner_id: str) -> dict | None:
            cs = _comments_of(owner_id)
            return cs[0] if cs else None

        def _param_index(pid: str) -> int:
            try:
                return int(pid.rsplit("#param#", 1)[1])
            except (IndexError, ValueError):
                return 0

        def _build_callsite(cs_id: str) -> dict:
            p = callsites[cs_id]
            op_ids = a_crudop.get(cs_id, [])
            q_ids = a_crudq.get(cs_id, [])
            crud_op = R.crud_operation(crudops[op_ids[0]]) if op_ids and op_ids[0] in crudops else None
            crud_q = R.crud_query(crudqs[q_ids[0]]) if q_ids and q_ids[0] in crudqs else None
            return R.callsite(p, comment_node=_first_comment(cs_id), crud_op=crud_op, crud_q=crud_q)

        def _callsites_of(owner_id: str) -> List[dict]:
            ids = a_callsite.get(owner_id, [])
            built = [(callsites[i], _build_callsite(i)) for i in ids if i in callsites]
            return [cs for _, cs in sorted(built, key=lambda t: (t[0].get("start_line", -1), t[0].get("start_column", -1)))]

        def _vars_of(owner_id: str) -> List[dict]:
            ids = a_var.get(owner_id, [])
            built = [(variables[i], R.variable(variables[i], comment_node=_first_comment(i))) for i in ids if i in variables]
            return [v for _, v in sorted(built, key=lambda t: (t[0].get("start_line", -1), t[0].get("name", "")))]

        # ---- callables ----
        def _build_callable(cid: str) -> dict:
            p = callables[cid]
            pids = sorted(a_param.get(cid, []), key=_param_index)
            parameters = [R.parameter(params[i]) for i in pids if i in params]
            op_ids = a_crudop.get(cid, [])
            q_ids = a_crudq.get(cid, [])
            crud_ops = [R.crud_operation(crudops[i]) for i in op_ids if i in crudops]
            crud_qs = [R.crud_query(crudqs[i]) for i in q_ids if i in crudqs]
            return R.callable_(
                p,
                comments=_comments_of(cid),
                parameters=parameters,
                call_sites=_callsites_of(cid),
                variable_declarations=_vars_of(cid),
                crud_operations=crud_ops,
                crud_queries=crud_qs,
            )

        def _build_initblock(ib_id: str) -> dict:
            p = initblocks[ib_id]
            return R.init_block(p, comments=_comments_of(ib_id), call_sites=_callsites_of(ib_id), variable_declarations=_vars_of(ib_id))

        # ---- types ----
        def _build_type(tid: str) -> dict:
            p = types[tid]
            cdecls = {}
            for cid in a_callable.get(tid, []):
                if cid in callables:
                    cdecls[callables[cid].get("signature", cid)] = _build_callable(cid)
            fdecls = [R.field(fields[i], comment_node=_first_comment(i)) for i in a_field.get(tid, []) if i in fields]
            econsts = [R.enum_constant(enums[i]) for i in a_enum.get(tid, []) if i in enums]
            rcomps = [R.record_component(records[i], comment_node=_first_comment(i)) for i in a_record.get(tid, []) if i in records]
            iblocks = [_build_initblock(i) for i in a_init.get(tid, []) if i in initblocks]
            return R.type_(
                p,
                comments=_comments_of(tid),
                callable_declarations=cdecls,
                field_declarations=fdecls,
                enum_constants=econsts,
                record_components=rcomps,
                initialization_blocks=iblocks,
            )

        # group types by owning module (file_key); type_declarations is a flat per-CU map
        types_by_unit: Dict[str, Dict[str, dict]] = {}
        for tid, tp in types.items():
            fkey = tp.get("_module")
            fqn = tp.get("fqn", tid)
            types_by_unit.setdefault(fkey, {})[fqn] = _build_type(tid)

        # imports by unit
        imports_by_unit: Dict[str, List[dict]] = {}
        for r in a_import:
            imports_by_unit.setdefault(r["cu"], []).append(
                {"path": r["path"], "is_static": r["p"].get("is_static", False), "is_wildcard": r["p"].get("is_wildcard", False)}
            )

        # ---- compilation units / symbol table ----
        symbol_table: Dict[str, dict] = {}
        for fkey, cp in cu_nodes.items():
            symbol_table[fkey] = R.compilation_unit(
                cp,
                comments=_comments_of(fkey),
                import_declarations=imports_by_unit.get(fkey, []),
                type_declarations=types_by_unit.get(fkey, {}),
            )

        # ---- call graph edges ----
        call_edges: List[dict] = []
        for r in self._run(
            "MATCH (s:JCallable)-[c:J_CALLS]->(t:JCallable) WHERE s._module IN $u "
            "RETURN s.id AS src, t.id AS tgt, properties(c) AS p",
            u=units,
        ):
            src = self._endpoint(r["src"], callables)
            tgt = self._endpoint(r["tgt"], callables)
            if src and tgt:
                call_edges.append(R.call_edge(src, tgt, r["p"]))

        return JApplication(symbol_table=symbol_table, call_graph=call_edges)

    @staticmethod
    def _endpoint(node_id: str, callables: Dict[str, Dict[str, Any]]) -> dict | None:
        """A J_CALLS endpoint id (``<fqn>#<signature>``) → a JGraphEdges source/target dict."""
        if "#" not in node_id:
            return None
        fqn, signature = node_id.split("#", 1)
        props = callables.get(node_id, {})
        declaration = props.get("declaration") or signature
        if "(" not in declaration:
            declaration = signature
        return {"file_path": props.get("file_path", ""), "type_declaration": fqn, "signature": signature, "callable_declaration": declaration}

    # =====================================================================================
    # JavaAnalysisBackend — leaf accessors (served from the reconstructed application)
    # =====================================================================================
    def get_application_view(self) -> JApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        return self.application.symbol_table

    def get_system_dependency_graph(self) -> list[JGraphEdges]:
        return self.application.call_graph or []

    def get_compilation_units(self) -> List[JCompilationUnit]:
        return list(self.application.symbol_table.values())

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        return self.application.symbol_table[file_path]

    # =====================================================================================
    # Call graph (logic mirrors JCodeanalyzer; calling_lines recomputed from JCallable.code)
    # =====================================================================================
    def _generate_call_graph(self, using_symbol_table) -> nx.DiGraph:
        cg = nx.DiGraph()
        if using_symbol_table:
            NotImplementedError("Call graph generation using symbol table is not implemented yet.")
        else:
            sdg = self.get_system_dependency_graph()
            tsu = TreesitterJava()
            edge_list = [
                (
                    (jge.source.method.signature, jge.source.klass),
                    (jge.target.method.signature, jge.target.klass),
                    {
                        "type": jge.type,
                        "weight": jge.weight,
                        "calling_lines": (
                            tsu.get_calling_lines(jge.source.method.code, jge.target.method.signature)
                            if not jge.source.method.is_implicit or not jge.target.method.is_implicit
                            else []
                        ),
                    },
                )
                for jge in sdg
                if jge.type == "CALL_DEP"
            ]
            for jge in sdg:
                cg.add_node((jge.source.method.signature, jge.source.klass), method_detail=jge.source)
                cg.add_node((jge.target.method.signature, jge.target.klass), method_detail=jge.target)
            cg.add_edges_from(edge_list)
        return cg

    def get_call_graph(self) -> nx.DiGraph:
        if self.analysis_level == "symbol_table":
            self.call_graph = self._generate_call_graph(using_symbol_table=True)
        if self.call_graph is None:
            self.call_graph = self._generate_call_graph(using_symbol_table=False)
        return self.call_graph

    def get_call_graph_json(self) -> str:
        callgraph_list = []
        edges = list(self.call_graph.edges.data("calling_lines"))
        for edge in edges:
            callgraph_dict = {}
            callgraph_dict["source_method_signature"] = edge[0][0]
            callgraph_dict["source_method_body"] = self.call_graph.nodes[edge[0]]["method_detail"].method.code
            callgraph_dict["source_class"] = edge[0][1]
            callgraph_dict["target_method_signature"] = edge[1][0]
            callgraph_dict["target_method_body"] = self.call_graph.nodes[edge[1]]["method_detail"].method.code
            callgraph_dict["target_class"] = edge[1][1]
            callgraph_dict["calling_lines"] = edge[2]
            callgraph_list.append(callgraph_dict)
        return json.dumps(callgraph_list)

    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        caller_detail_dict = {}
        if using_symbol_table:
            call_graph = self.__call_graph_using_symbol_table(qualified_class_name=target_class_name, method_signature=target_method_signature, is_target_method=True)
        else:
            call_graph = self.call_graph
        if (target_method_signature, target_class_name) not in call_graph.nodes():
            return caller_detail_dict
        in_edge_view = call_graph.in_edges(nbunch=(target_method_signature, target_class_name), data=True)
        caller_detail_dict["caller_details"] = []
        caller_detail_dict["target_method"] = call_graph.nodes[(target_method_signature, target_class_name)]["method_detail"]
        for source, target, data in in_edge_view:
            cm = {"caller_method": call_graph.nodes[source]["method_detail"], "calling_lines": data["calling_lines"]}
            caller_detail_dict["caller_details"].append(cm)
        return caller_detail_dict

    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        callee_detail_dict = {}
        if using_symbol_table:
            call_graph = self.__call_graph_using_symbol_table(qualified_class_name=source_class_name, method_signature=source_method_signature)
        else:
            call_graph = self.call_graph
        if (source_method_signature, source_class_name) not in call_graph.nodes():
            return callee_detail_dict
        out_edge_view = call_graph.out_edges(nbunch=(source_method_signature, source_class_name), data=True)
        callee_detail_dict["callee_details"] = []
        callee_detail_dict["source_method"] = call_graph.nodes[(source_method_signature, source_class_name)]["method_detail"]
        for source, target, data in out_edge_view:
            cm = {"callee_method": call_graph.nodes[target]["method_detail"], "calling_lines": data["calling_lines"]}
            callee_detail_dict["callee_details"].append(cm)
        return callee_detail_dict

    # =====================================================================================
    # Classes / methods / fields (operate on the reconstructed symbol table)
    # =====================================================================================
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        class_method_dict = {}
        class_dict = self.get_all_classes()
        for k, v in class_dict.items():
            class_method_dict[k] = v.callable_declarations
        return class_method_dict

    def get_all_classes(self) -> Dict[str, JType]:
        class_dict = {}
        for v in self.get_symbol_table().values():
            class_dict.update(v.type_declarations)
        return class_dict

    def get_class(self, qualified_class_name) -> JType:
        for v in self.get_symbol_table().values():
            if qualified_class_name in v.type_declarations.keys():
                return v.type_declarations.get(qualified_class_name)

    def get_method(self, qualified_class_name, method_signature) -> JCallable:
        for v in self.get_symbol_table().values():
            if qualified_class_name in v.type_declarations.keys():
                ci = v.type_declarations[qualified_class_name]
                for cd in ci.callable_declarations.keys():
                    if cd == method_signature:
                        return ci.callable_declarations[cd]

    def get_method_parameters(self, qualified_class_name, method_signature) -> List[JCallableParameter]:
        return self.get_method(qualified_class_name, method_signature).parameters

    def get_java_file(self, qualified_class_name) -> str:
        for k, v in self.get_symbol_table().items():
            if qualified_class_name in v.type_declarations.keys():
                return k

    def get_all_methods_in_class(self, qualified_class_name) -> Dict[str, JCallable]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            return {}
        return {k: v for (k, v) in ci.callable_declarations.items() if v.is_constructor is False}

    def get_all_constructors(self, qualified_class_name) -> Dict[str, JCallable]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            return {}
        return {k: v for (k, v) in ci.callable_declarations.items() if v.is_constructor is True}

    def get_all_sub_classes(self, qualified_class_name) -> Dict[str, JType]:
        all_classes = self.get_all_classes()
        sub_classes = {}
        for cls in all_classes:
            if qualified_class_name in all_classes[cls].implements_list or qualified_class_name in all_classes[cls].extends_list:
                sub_classes[cls] = all_classes[cls]
        return sub_classes

    def get_all_fields(self, qualified_class_name) -> List[JField]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.field_declarations

    def get_all_nested_classes(self, qualified_class_name) -> List[JType]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return [self.get_class(c) for c in ci.nested_type_declarations]

    def get_extended_classes(self, qualified_class_name) -> List[str]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.extends_list

    def get_implemented_interfaces(self, qualified_class_name) -> List[str]:
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.implements_list

    # =====================================================================================
    # Symbol-table call graph (pure-Python over call sites; mirrors JCodeanalyzer)
    # =====================================================================================
    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        call_graph = self.__call_graph_using_symbol_table(qualified_class_name, method_signature)
        if method_signature is None:
            filter_criteria = {node for node in call_graph.nodes if node[1] == qualified_class_name}
        else:
            filter_criteria = {node for node in call_graph.nodes if tuple(node) == (method_signature, qualified_class_name)}
        graph_edges: List[Tuple[JMethodDetail, JMethodDetail]] = list()
        for edge in call_graph.edges(nbunch=filter_criteria):
            source: JMethodDetail = call_graph.nodes[edge[0]]["method_detail"]
            target: JMethodDetail = call_graph.nodes[edge[1]]["method_detail"]
            graph_edges.append((source, target))
        return graph_edges

    def __call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str, is_target_method: bool = False) -> nx.DiGraph:
        cg = nx.DiGraph()
        if is_target_method:
            sdg = self.__raw_call_graph_using_symbol_table_target_method(target_class_name=qualified_class_name, target_method_signature=method_signature)
        else:
            sdg = self.__raw_call_graph_using_symbol_table(qualified_class_name=qualified_class_name, method_signature=method_signature)
        tsu = TreesitterJava()
        edge_list = [
            (
                (jge.source.method.signature, jge.source.klass),
                (jge.target.method.signature, jge.target.klass),
                {"type": jge.type, "weight": jge.weight, "calling_lines": tsu.get_calling_lines(jge.source.method.code, jge.target.method.signature)},
            )
            for jge in sdg
        ]
        for jge in sdg:
            cg.add_node((jge.source.method.signature, jge.source.klass), method_detail=jge.source)
            cg.add_node((jge.target.method.signature, jge.target.klass), method_detail=jge.target)
        cg.add_edges_from(edge_list)
        return cg

    def __raw_call_graph_using_symbol_table_target_method(self, target_class_name: str, target_method_signature: str, cg=None) -> list[JGraphEdgesST]:
        if cg is None:
            cg = []
        target_method_details = self.get_method(qualified_class_name=target_class_name, method_signature=target_method_signature)
        for class_name in self.get_all_classes():
            for method in self.get_all_methods_in_class(qualified_class_name=class_name):
                method_details = self.get_method(qualified_class_name=class_name, method_signature=method)
                for call_site in method_details.call_sites:
                    source_method_details = None
                    source_class = ""
                    callee_signature = call_site.callee_signature if call_site.callee_signature != "" else ""
                    if call_site.receiver_type != "":
                        if self.get_class(qualified_class_name=call_site.receiver_type):
                            found_method, found_class = self.__find_method_in_hierarchy(call_site.receiver_type, callee_signature)
                            if found_method is not None and callee_signature == target_method_signature and found_class == target_class_name:
                                source_method_details = self.get_method(method_signature=method, qualified_class_name=class_name)
                                source_class = class_name
                    else:
                        found_method, found_class = self.__find_method_in_hierarchy(class_name, callee_signature)
                        if found_method is not None and callee_signature == target_method_signature and found_class == target_class_name:
                            source_method_details = self.get_method(method_signature=method, qualified_class_name=class_name)
                            source_class = class_name
                    if source_class != "" and source_method_details is not None:
                        call_edge = JGraphEdgesST(
                            source=JMethodDetail(method_declaration=source_method_details.declaration, klass=source_class, method=source_method_details),
                            target=JMethodDetail(method_declaration=target_method_details.declaration, klass=target_class_name, method=target_method_details),
                            type="CALL_DEP",
                            weight="1",
                        )
                        if call_edge not in cg:
                            cg.append(call_edge)
        return cg

    def __find_method_in_hierarchy(self, qualified_class_name: str, method_signature: str) -> Tuple[JCallable | None, str]:
        klass = self.get_class(qualified_class_name=qualified_class_name)
        method_details = self.get_method(method_signature=method_signature, qualified_class_name=qualified_class_name)
        if method_details is not None and klass is not None and not klass.is_interface:
            return method_details, qualified_class_name
        if klass is not None:
            for parent_class in klass.extends_list:
                parent_method, found_class = self.__find_method_in_hierarchy(parent_class, method_signature)
                if parent_method is not None:
                    return parent_method, found_class
        return None, ""

    def __raw_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str, cg=None) -> list[JGraphEdgesST]:
        if cg is None:
            cg = []
        source_method_details = self.get_method(qualified_class_name=qualified_class_name, method_signature=method_signature)
        if source_method_details is None:
            return cg
        for call_site in source_method_details.call_sites:
            target_method_details = None
            target_class = ""
            callee_signature = call_site.callee_signature if call_site.callee_signature != "" else ""
            if call_site.receiver_type != "":
                if self.get_class(qualified_class_name=call_site.receiver_type):
                    tmd, found_class = self.__find_method_in_hierarchy(call_site.receiver_type, callee_signature)
                    if tmd is not None:
                        target_method_details = tmd
                        target_class = found_class
            else:
                tmd, found_class = self.__find_method_in_hierarchy(qualified_class_name, callee_signature)
                if tmd is not None:
                    target_method_details = tmd
                    target_class = found_class
            if target_class != "" and target_method_details is not None:
                call_edge = JGraphEdgesST(
                    source=JMethodDetail(method_declaration=source_method_details.declaration, klass=qualified_class_name, method=source_method_details),
                    target=JMethodDetail(method_declaration=target_method_details.declaration, klass=target_class, method=target_method_details),
                    type="CALL_DEP",
                    weight="1",
                )
                if call_edge not in cg:
                    cg.append(call_edge)
        return cg

    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        if method_name is None:
            filter_criteria = {node for node in self.call_graph.nodes if node[1] == qualified_class_name}
        else:
            filter_criteria = {node for node in self.call_graph.nodes if tuple(node) == (method_name, qualified_class_name)}
        graph_edges: List[Tuple[JMethodDetail, JMethodDetail]] = list()
        for edge in self.call_graph.edges(nbunch=filter_criteria):
            source: JMethodDetail = self.call_graph.nodes[edge[0]]["method_detail"]
            target: JMethodDetail = self.call_graph.nodes[edge[1]]["method_detail"]
            graph_edges.append((source, target))
        return graph_edges

    def remove_all_comments(self, src_code: str) -> str:
        raise NotImplementedError("This function is not implemented yet.")

    # =====================================================================================
    # Entry points / CRUD / comments (operate on the reconstructed symbol table)
    # =====================================================================================
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        methods = chain.from_iterable(
            ((typename, method, callable) for method, callable in methods.items() if callable.is_entrypoint) for typename, methods in self.get_all_methods_in_application().items()
        )
        return {typename: {method: callable for _, method, callable in group} for typename, group in groupby(methods, key=lambda x: x[0])}

    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        return {typename: klass for typename, klass in self.get_all_classes().items() if klass.is_entrypoint_class}

    def _crud(self, op_filter: CRUDOperationType | None) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        rows = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if method_details.crud_operations and len(method_details.crud_operations) > 0:
                    ops = method_details.crud_operations if op_filter is None else [o for o in method_details.crud_operations if o.operation_type == op_filter]
                    rows.append({class_name: class_details, method_name: method_details, "crud_operations": ops})
        return rows

    def get_all_crud_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        return self._crud(None)

    def get_all_read_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        return self._crud(CRUDOperationType.READ)

    def get_all_create_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        return self._crud(CRUDOperationType.CREATE)

    def get_all_update_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        return self._crud(CRUDOperationType.UPDATE)

    def get_all_delete_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        return self._crud(CRUDOperationType.DELETE)

    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        return self.get_method(qualified_class_name, method_signature).comments

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        return self.get_class(qualified_class_name).comments

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        compilation_unit = self.get_symbol_table().get(file_path, None)
        if compilation_unit is None:
            raise CodeanalyzerExecutionException(f"File {file_path} not found in the symbol table.")
        return compilation_unit.comments

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        return {file_path: self.get_comment_in_file(file_path) for file_path in self.get_symbol_table()}

    def get_all_docstrings(self) -> List[Tuple[str, JComment]]:
        docstrings = {}
        for file_path, list_of_comments in self.get_all_comments().items():
            javadoc_comments = [docstring for docstring in list_of_comments if docstring.is_javadoc]
            if javadoc_comments:
                docstrings[file_path] = javadoc_comments
        return docstrings
