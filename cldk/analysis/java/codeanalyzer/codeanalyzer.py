################################################################################
# Copyright IBM Corporation 2024
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

"""Java Codeanalyzer backend.

Subprocess wrapper around the analyzer the ``codeanalyzer-java`` wheel carries (the ``java``
extra; pinned in ``pyproject.toml``, mirrored in ``[tool.backend-versions]``), run on the JVM that
same wheel bundles -- the SDK downloads no JDK and touches no JDK environment variable.
Reads the schema-v2 ``analysis.json`` envelope (:class:`JAnalysis`), keeps its ``application`` as
the queried :class:`JApplication`, and owns all query/indexing logic; the :class:`JavaAnalysis`
facade is a thin delegating shell over it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, Iterable, List, Tuple, Union

import networkx as nx

from cldk.analysis.commons.levels import analyzer_level
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.java.backend import CRUD_UNAVAILABLE, CRUDRow, JavaAnalysisBackend, duplicate_type_name, unhomed_endpoint
from cldk.models.java import JGraphEdges
from cldk.models.java.models import JAnalysis, JApplication, JCallable, JCallableParameter, JCallSite, JComment, JCompilationUnit, JField, JMethodDetail, JType
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class JCodeanalyzer(JavaAnalysisBackend):
    """Build and query the application view of a Java project by invoking codeanalyzer-java.

    Args:
        project_dir: Path to the root of the Java project.
        analysis_json_path: Directory to persist ``analysis.json`` (the language-keyed cache dir,
            ``<cache>/java``). If None, the envelope is read from the subprocess stdout pipe.
        analysis_level: Any :class:`~cldk.analysis.AnalysisLevel` (or its name); sent to the
            analyzer as ``-a 1..4`` — the backend requests what the caller asked for.
        eager_analysis: If True, re-run the analyzer even if a compatible ``analysis.json`` is cached.
        target_files: Restrict analysis to these files (``-t``); always re-runs.

    Attributes:
        analysis: The whole ``analysis.json`` envelope — ``schema_version``, ``max_level``,
            ``analyzer.version`` — for callers that need to know what produced the view.
        application: ``analysis.application``, the queried view.
    """

    def __init__(
        self,
        project_dir: Union[str, Path, None],
        analysis_json_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_json_path = analysis_json_path
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.analysis: JAnalysis = self._init_codeanalyzer(analysis_level=analyzer_level(analysis_level))
        self.application: JApplication = self.analysis.application
        self._call_graph: nx.DiGraph | None = None
        self._index()

    # -----[ driving the analyzer ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """``codeanalyzer_java.command()`` — ``[<jdk4py java>, -jar, <the wheel's jar>]``.

        The ``codeanalyzer-java`` wheel is the single source of both the jar and the JVM it runs
        on; 3.0.x reads its primordial scope from ``jrt:/`` inside that JVM, so the SDK has nothing
        to provision and no environment to point the analyzer at -- whatever JDK the machine has (or
        has not) is left alone. Imported here rather than at module import so ``import cldk`` (and
        ``cldk.analysis.java``) work without the ``java`` extra.
        """
        try:
            import codeanalyzer_java
        except ImportError as exc:
            raise CodeanalyzerExecutionException(
                'the Java analyzer is not installed: the codeanalyzer-java distribution (module "codeanalyzer_java") carries the analyzer jar and the JVM it runs on. Install it with: pip install "cldk[java]"'
            ) from exc
        return codeanalyzer_java.command()

    def _argv(self, analysis_level: int, output_dir: Path | None) -> List[str]:
        """The 3.0.x command line: ``-i <project> -a <1..4> [-o <dir> -c <dir>/cache] --app-name
        <project.name> [-t <file>]...``. The application name is what the analyzer stamps into every
        ``can://java/<app>/...`` id; without ``-o`` the analyzer prints the JSON to stdout."""
        if self.project_dir is None:
            raise CodeanalyzerExecutionException("Cannot run codeanalyzer-java: no project directory.")
        args = self._get_codeanalyzer_exec()
        project = Path(self.project_dir)
        args += ["-i", str(project), "-a", str(analysis_level)]
        if output_dir is not None:
            args += ["-o", str(output_dir), "-c", str(output_dir / "cache")]
        args += ["--app-name", project.name]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]
        return args

    @staticmethod
    def check_exisiting_analysis_file_level(analysis_json_path_file: Path, analysis_level: int) -> bool:
        """Whether a cached ``analysis.json`` can serve a request at ``analysis_level``.

        ``False`` (re-run) when the file is missing, unparsable, or was computed at a lower
        ``max_level`` than requested. A file without ``schema_version`` is a pre-v2 (2.x) artifact
        and is refused outright (J-9): the v2 models cannot read it and a silent re-run would hide
        that the cache directory holds a stale generation.
        """
        if not analysis_json_path_file.exists():
            return False
        try:
            data = json.loads(analysis_json_path_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(data, dict):
            return False
        if "schema_version" not in data:
            raise CodeanalyzerExecutionException(f"cached analysis.json at {analysis_json_path_file} predates schema v2 (no schema_version); delete it or pass eager_analysis=True")
        return int(data.get("max_level", 0)) >= analysis_level

    def _init_codeanalyzer(self, analysis_level: int) -> JAnalysis:
        """Run the analyzer (or reuse a compatible cache) and return the validated envelope."""
        if self.analysis_json_path is None:
            args = self._argv(analysis_level, None)
            try:
                logger.info(f"Running codeanalyzer-java: {' '.join(args)}")
                console_out: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
                return JAnalysis.model_validate_json(console_out.stdout)
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e

        output_dir = Path(self.analysis_json_path)
        analysis_json_file = output_dir / "analysis.json"
        needs_run = self.eager_analysis or bool(self.target_files) or not self.check_exisiting_analysis_file_level(analysis_json_file, analysis_level)
        if needs_run:
            args = self._argv(analysis_level, output_dir)
            try:
                logger.info(f"Running codeanalyzer-java: {' '.join(args)}")
                subprocess.run(args, capture_output=True, text=True, check=True)
                if not analysis_json_file.exists():
                    raise CodeanalyzerExecutionException("codeanalyzer-java did not generate analysis.json.")
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e
        return JAnalysis.model_validate_json(analysis_json_file.read_text(encoding="utf-8"))

    # -----[ indexing ]-----
    def _index(self) -> None:
        """Flatten the containment tree once: every type (top-level, nested, local/anonymous) by
        its source-spelled qualified name, its file, and every callable by its ``can://`` id — the
        join that turns a wire call-graph endpoint into the ``"<type fqn>.<signature>"`` node key."""
        self._types: Dict[str, JType] = {}
        self._file_of: Dict[str, str] = {}
        self._callables: Dict[str, Tuple[JType, JCallable]] = {}
        for path, unit in self.application.symbol_table.items():
            for t in unit.types.values():
                self._add_type(t, path)

    def _add_type(self, t: JType, path: str) -> None:
        name = t.qualified_name
        if name in self._types:
            raise CodeanalyzerExecutionException(duplicate_type_name(name))
        self._types[name] = t
        self._file_of[name] = path
        for c in t.callables.values():
            self._callables[c.id] = (t, c)
            for lt in c.types.values():
                self._add_type(lt, path)
        for nt in t.types.values():
            self._add_type(nt, path)

    @staticmethod
    def _detail(klass: str, c: JCallable) -> JMethodDetail:
        return JMethodDetail(method_declaration=c.declaration, klass=klass, method=c)

    def _node_of(self, node_id: str) -> Tuple[str, JMethodDetail]:
        """The (node key, method detail) a call-graph endpoint id resolves to. Every endpoint the
        analyzer emits is homed on the tree; one that is not is the analyzer's defect, surfaced
        rather than skipped — named by the signature and module key its id spells, never by the id
        (E6), in the same words the Neo4j backend uses."""
        try:
            t, c = self._callables[node_id]
        except KeyError:
            raise CodeanalyzerExecutionException(unhomed_endpoint(node_id)) from None
        return f"{t.qualified_name}.{c.signature}", self._detail(t.qualified_name, c)

    def _is_external(self, node_id: str) -> bool:
        """An ``@external/…`` endpoint (a call target outside the project). 3a keeps the 1.x
        callable-only graph and drops edges to them; ``get_external_symbols`` arrives in 3b."""
        return "@external/" in node_id or node_id in (self.application.external_symbols or {})

    @staticmethod
    def _calling_lines(tsu: TreesitterJava, source: JCallable, target: JCallable) -> List[int]:
        return tsu.get_calling_lines(source.code, target.signature) if source.code else []

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> JApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        return self.application.symbol_table

    def get_compilation_units(self) -> List[JCompilationUnit]:
        return list(self.application.symbol_table.values())

    def get_java_file(self, qualified_class_name: str) -> str | None:
        return self._file_of.get(qualified_class_name)

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        return self.application.symbol_table[file_path]

    def get_system_dependency_graph(self) -> list[JGraphEdges]:
        """The wire call graph (``JApplication.call_graph``), one :class:`JCallGraphEdge` per edge."""
        return self.application.call_graph

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) the call graph keyed by ``"<type fqn>.<signature>"`` (J-1): node attrs
        ``method_detail`` / ``kind="callable"``; edge attrs ``type="CALL_DEP"``, ``weight``,
        ``calling_lines``. Empty below level 2 (the wire carries no ``call_graph`` there)."""
        if self._call_graph is not None:
            return self._call_graph
        cg = nx.DiGraph()
        tsu = TreesitterJava()
        for edge in self.application.call_graph:
            if self._is_external(edge.src) or self._is_external(edge.dst):
                continue
            src, src_detail = self._node_of(edge.src)
            dst, dst_detail = self._node_of(edge.dst)
            cg.add_node(src, method_detail=src_detail, kind="callable")
            cg.add_node(dst, method_detail=dst_detail, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=edge.weight, calling_lines=self._calling_lines(tsu, src_detail.method, dst_detail.method))
        self._call_graph = cg
        return cg

    def get_call_graph_json(self) -> str:
        cg = self.get_call_graph()
        rows = []
        for source, target, calling_lines in cg.edges.data("calling_lines"):
            s: JMethodDetail = cg.nodes[source]["method_detail"]
            t: JMethodDetail = cg.nodes[target]["method_detail"]
            rows.append(
                {
                    "source_method_signature": s.method.signature,
                    "source_method_body": s.method.code,
                    "source_class": s.klass,
                    "target_method_signature": t.method.signature,
                    "target_method_body": t.method.code,
                    "target_class": t.klass,
                    "calling_lines": calling_lines,
                }
            )
        return json.dumps(rows)

    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        cg = self._symbol_table_call_graph(target_class_name, target_method_signature, is_target=True) if using_symbol_table else self.get_call_graph()
        key = f"{target_class_name}.{target_method_signature}"
        if key not in cg:
            return {}
        return {
            "caller_details": [{"caller_method": cg.nodes[s]["method_detail"], "calling_lines": d["calling_lines"]} for s, _, d in cg.in_edges(key, data=True)],
            "target_method": cg.nodes[key]["method_detail"],
        }

    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        cg = self._symbol_table_call_graph(source_class_name, source_method_signature) if using_symbol_table else self.get_call_graph()
        key = f"{source_class_name}.{source_method_signature}"
        if key not in cg:
            return {}
        return {
            "callee_details": [{"callee_method": cg.nodes[t]["method_detail"], "calling_lines": d["calling_lines"]} for _, t, d in cg.out_edges(key, data=True)],
            "source_method": cg.nodes[key]["method_detail"],
        }

    @staticmethod
    def _edges_out_of(cg: nx.DiGraph, qualified_class_name: str, method_signature: str | None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        if method_signature is None:
            seeds = [n for n, a in cg.nodes(data=True) if a["method_detail"].klass == qualified_class_name]
        else:
            key = f"{qualified_class_name}.{method_signature}"
            seeds = [key] if key in cg else []
        return [(cg.nodes[s]["method_detail"], cg.nodes[t]["method_detail"]) for s, t in cg.edges(seeds)]

    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        return self._edges_out_of(self.get_call_graph(), qualified_class_name, method_name)

    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Edges out of a class (or one method) resolved from its call sites through the symbol
        table alone — incomplete by construction: only receivers the symbol table can see, only
        concrete implementations up the ``extends`` chain."""
        return self._edges_out_of(self._symbol_table_call_graph(qualified_class_name, method_signature), qualified_class_name, method_signature)

    # -----[ symbol-table call graph (call sites → declarations) ]-----
    def _symbol_table_call_graph(self, qualified_class_name: str, method_signature: str | None, is_target: bool = False) -> nx.DiGraph:
        cg = nx.DiGraph()
        tsu = TreesitterJava()
        edges = self._st_edges_into(qualified_class_name, method_signature) if is_target else self._st_edges_from(qualified_class_name, method_signature)
        for source, target in edges:
            src, dst = f"{source.klass}.{source.method.signature}", f"{target.klass}.{target.method.signature}"
            cg.add_node(src, method_detail=source, kind="callable")
            cg.add_node(dst, method_detail=target, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=1, calling_lines=self._calling_lines(tsu, source.method, target.method))
        return cg

    def _st_edges_from(self, qualified_class_name: str, method_signature: str | None) -> Iterable[Tuple[JMethodDetail, JMethodDetail]]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return
        if method_signature is None:
            sources = list(klass.callables.values())
        else:
            source = self.get_method(qualified_class_name, method_signature)
            sources = [source] if source is not None else []
        for source in sources:
            for call_site in source.call_sites:
                target, target_class = self._resolve_call_site(qualified_class_name, call_site)
                if target is not None:
                    yield self._detail(qualified_class_name, source), self._detail(target_class, target)

    def _st_edges_into(self, target_class_name: str, target_method_signature: str) -> Iterable[Tuple[JMethodDetail, JMethodDetail]]:
        target = self.get_method(target_class_name, target_method_signature)
        if target is None:
            return
        for owner, source in self._callables.values():
            for call_site in source.call_sites:
                found, found_class = self._resolve_call_site(owner.qualified_name, call_site)
                if found is not None and found_class == target_class_name and call_site.callee_signature == target_method_signature:
                    yield self._detail(owner.qualified_name, source), self._detail(target_class_name, target)

    def _resolve_call_site(self, owner_class_name: str, call_site: JCallSite) -> Tuple[JCallable | None, str]:
        """The (declaration, declaring class) a call site names, or ``(None, "")``: an explicit
        receiver type is followed only when it is a project class; an implicit receiver means the
        owning class (and its ``extends`` chain)."""
        if not call_site.callee_signature:
            return None, ""
        if call_site.receiver_type:
            if self.get_class(call_site.receiver_type) is None:
                return None, ""
            return self._find_in_hierarchy(call_site.receiver_type, call_site.callee_signature)
        return self._find_in_hierarchy(owner_class_name, call_site.callee_signature)

    def _find_in_hierarchy(self, qualified_class_name: str, method_signature: str) -> Tuple[JCallable | None, str]:
        """The concrete declaration of ``method_signature`` on the class or up its ``extends``
        chain; interface declarations are not call-graph targets and are skipped."""
        klass = self.get_class(qualified_class_name)
        method = self.get_method(qualified_class_name, method_signature)
        if method is not None and klass is not None and not klass.is_interface:
            return method, qualified_class_name
        if klass is not None:
            for parent in klass.extends_list:
                found, found_class = self._find_in_hierarchy(parent, method_signature)
                if found is not None:
                    return found, found_class
        return None, ""

    # -----[ classes / methods / fields ]-----
    def get_all_classes(self) -> Dict[str, JType]:
        return dict(self._types)

    def get_class(self, qualified_class_name: str) -> JType | None:
        return self._types.get(qualified_class_name)

    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        return {name: t.callable_declarations for name, t in self._types.items()}

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, JCallable]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return {}
        return {sig: c for sig, c in klass.callables.items() if not c.is_constructor}

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return {}
        return {sig: c for sig, c in klass.callables.items() if c.is_constructor}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> JCallable | None:
        klass = self.get_class(qualified_class_name)
        return klass.callables.get(qualified_method_name) if klass is not None else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[JCallableParameter]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return method.parameters if method is not None else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, JType]:
        return {name: t for name, t in self._types.items() if qualified_class_name in t.extends_list or qualified_class_name in t.implements_list}

    def get_all_fields(self, qualified_class_name: str) -> List[JField]:
        klass = self.get_class(qualified_class_name)
        return klass.field_declarations if klass is not None else []

    def get_all_nested_classes(self, qualified_class_name: str) -> List[JType]:
        klass = self.get_class(qualified_class_name)
        return list(klass.types.values()) if klass is not None else []

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        klass = self.get_class(qualified_class_name)
        return klass.extends_list if klass is not None else []

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        klass = self.get_class(qualified_class_name)
        return klass.implements_list if klass is not None else []

    # -----[ entry points ]-----
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        result: Dict[str, Dict[str, JCallable]] = {}
        for name, methods in self.get_all_methods_in_application().items():
            entrypoints = {sig: c for sig, c in methods.items() if c.is_entrypoint}
            if entrypoints:
                result[name] = entrypoints
        return result

    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        return {name: t for name, t in self._types.items() if t.is_entrypoint_class}

    # -----[ CRUD (J-4) ]-----
    def get_all_crud_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_create_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_read_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_update_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_delete_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    # -----[ repository artifacts — the shared Py* models, as the generic ABC promises ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Every non-code artifact (see :meth:`AnalysisBackend.get_artifacts`), keyed by repo-relative
        path as the wire keys them. ``JArtifact.text_truncated`` has no home on the shared model and
        is not carried; read it off ``JApplication.artifacts`` when it matters."""
        return {path: PyArtifact(**a.model_dump(exclude={"config_keys", "text_truncated"}), config_keys=[PyConfigKey(**ck.model_dump()) for ck in a.config_keys]) for path, a in self.application.artifacts.items()}

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        """Every declared dependency, optionally filtered (see :meth:`AnalysisBackend.get_dependencies`).
        The Maven ``group`` coordinate has no home on the shared model and is not carried; read it off
        ``JApplication.dependencies`` when ``name`` alone is ambiguous."""
        deps = [PyDependency(**d.model_dump(exclude={"group"})) for d in self.application.dependencies]
        if direct_only:
            deps = [d for d in deps if d.direct]
        if ecosystem is not None:
            deps = [d for d in deps if d.ecosystem == ecosystem]
        if declared_in is not None:
            deps = [d for d in deps if d.declared_in == declared_in]
        return deps

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        return {ck.id: PyConfigKey(**ck.model_dump()) for a in self.application.artifacts.values() for ck in a.config_keys}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Always empty: codeanalyzer-java 3.0.1 emits no code-to-config edges (there is no
        ``config_uses`` on the Java wire), so there is nothing to filter by ``key``."""
        return []

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Always empty: codeanalyzer-java 3.0.1 has no config-read detector (no ``config_reads``
        on the Java wire)."""
        return []

    # -----[ comments ]-----
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        method = self.get_method(qualified_class_name, method_signature)
        return method.comments if method is not None else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        klass = self.get_class(qualified_class_name)
        return klass.comments if klass is not None else []

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        unit = self.application.symbol_table.get(file_path)
        if unit is None:
            raise CodeanalyzerExecutionException(f"File {file_path} not found in the symbol table.")
        return unit.comments

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        return {path: unit.comments for path, unit in self.application.symbol_table.items()}

    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        docstrings = {}
        for path, comments in self.get_all_comments().items():
            javadoc = [c for c in comments if c.is_javadoc]
            if javadoc:
                docstrings[path] = javadoc
        return docstrings

    def remove_all_comments(self, src_code: str) -> str:
        raise NotImplementedError("This function is not implemented yet.")
