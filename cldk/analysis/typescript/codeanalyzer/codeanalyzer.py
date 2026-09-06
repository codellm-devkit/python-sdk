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

"""TypeScript Codeanalyzer backend wrapper.

Subprocess wrapper around the ``codeanalyzer-typescript`` binary (``cants``). Mirrors the Java
``JCodeanalyzer`` / Python ``PyCodeanalyzer`` pattern: shell out to the analyzer, read the
``analysis.json`` envelope (:class:`TSAnalysis`) from stdout or an output dir, keep its
``application`` as the queried :class:`TSApplication`, **and own all query/indexing logic**. The
``TypeScriptAnalysis`` facade is a thin delegating shell over this backend.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import warnings
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, Iterator, List, Set, Tuple, Union

import networkx as nx

from cldk.analysis.commons.levels import analyzer_level
from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.models.typescript import (
    TSAnalysis,
    TSApplication,
    TSBodyNode,
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSConfigKey,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSNamespace,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)

#: The codeanalyzer-typescript release that removed ``--tsc-only`` (the resolver is no longer a
#: choice; 1.x's ``tsc`` and ``defuse`` provenances are both emitted and tagged per edge).
_TSC_ONLY_REMOVED_IN = "1.0.0"


class TSCodeanalyzer(TSAnalysisBackend):
    """Build and query the application view of a TypeScript project by invoking the
    codeanalyzer-typescript binary as a subprocess.

    This backend owns all indexing and query logic (symbol lookups, the NetworkX call graph,
    class hierarchy, call sites, decorators, the artifact layer, ...). The
    :class:`TypeScriptAnalysis` facade simply delegates to it, mirroring how
    :class:`PythonAnalysis` delegates to :class:`PyCodeanalyzer`.

    Args:
        project_dir: Path to the root of the TypeScript project.
        analysis_json_path: Directory to persist ``analysis.json``. If None, output is read from
            the subprocess stdout pipe.
        analysis_level: Any :class:`~cldk.analysis.AnalysisLevel` (or its name); sent to the
            analyzer as ``-a 1..4`` — the backend requests what the caller asked for.
        eager_analysis: If True, re-run the analyzer even if a cached ``analysis.json`` exists, and
            tell the analyzer to rebuild its own cache (``--eager``).
        target_files: Restrict analysis to these files (incremental).
        tsc_only: Deprecated no-op. The flag was removed from codeanalyzer-typescript at 1.0.0;
            passing ``True`` emits a :class:`DeprecationWarning` and changes nothing.

    Attributes:
        analysis: The whole ``analysis.json`` envelope — ``max_level``, ``k_limit``,
            ``analyzer.version`` — for callers that need to know what generation produced the view.
        application: ``analysis.application``, the queried view.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_json_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
        tsc_only: bool = False,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_json_path = analysis_json_path
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        if tsc_only:
            warnings.warn(
                f"tsc_only is a no-op: codeanalyzer-typescript removed --tsc-only in {_TSC_ONLY_REMOVED_IN}; " "every call edge now carries its resolver in `prov` instead.",
                DeprecationWarning,
                stacklevel=3,
            )
        self.analysis: TSAnalysis = self._init_codeanalyzer(analysis_level=analyzer_level(analysis_level))
        self.application: TSApplication = self.analysis.application
        self._call_graph: nx.DiGraph | None = None
        self._index()

    # -----[ binary resolution ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """Resolve the codeanalyzer-typescript executable command.

        The binary ships with the ``codeanalyzer-typescript`` PyPI dependency. ``$CODEANALYZER_TS_BIN``
        remains the only out-of-band override (e.g. a locally built binary).
        """
        env_bin = os.environ.get("CODEANALYZER_TS_BIN")
        if env_bin:
            return shlex.split(env_bin)

        # Prebuilt binary shipped inside the `codeanalyzer-typescript` PyPI package (platform
        # wheel), mirroring how the Python backend depends on `codeanalyzer-python`.
        try:
            import codeanalyzer_typescript

            return [str(codeanalyzer_typescript.bin_path())]
        except (ModuleNotFoundError, FileNotFoundError) as e:
            raise CodeanalyzerExecutionException(
                "codeanalyzer-typescript binary not found: $CODEANALYZER_TS_BIN is unset and the "
                f"`codeanalyzer-typescript` wheel is not importable or carries no binary for this platform ({e}). "
                "Install it with `pip install codeanalyzer-typescript`, or set $CODEANALYZER_TS_BIN."
            ) from e

    def _argv(self, analysis_level: int, output_dir: Path | None) -> List[str]:
        """The 1.2.0 command line: ``-i <project> --app-name <project.name> -a <1..4> [-o <dir>
        --cache-dir <dir>] --skip-tests [--eager] [-t <file>]...``. The application name is what
        the analyzer stamps into every ``can://typescript/<app>/...`` id."""
        project = Path(self.project_dir)
        args = self._get_codeanalyzer_exec() + ["-i", str(project), "--app-name", project.name, "-a", str(analysis_level)]
        if output_dir is not None:
            args += ["-o", str(output_dir), "--cache-dir", str(output_dir)]
        args += ["--skip-tests"]
        if self.eager_analysis:
            args += ["--eager"]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]
        return args

    def _init_codeanalyzer(self, analysis_level: int) -> TSAnalysis:
        """Run the analyzer and return the validated envelope."""
        if self.analysis_json_path is None:
            # Read compact JSON from the stdout pipe.
            args = self._argv(analysis_level, None)
            try:
                logger.info(f"Running codeanalyzer-typescript: {' '.join(args)}")
                console_out: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
                return TSAnalysis.model_validate_json(console_out.stdout)
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e

        # Persist to an output directory and read analysis.json back.
        output_dir = Path(self.analysis_json_path)
        analysis_json_file = output_dir / "analysis.json"
        needs_run = self.eager_analysis or not analysis_json_file.exists() or bool(self.target_files)
        if needs_run:
            args = self._argv(analysis_level, output_dir)
            try:
                logger.info(f"Running codeanalyzer-typescript: {' '.join(args)}")
                subprocess.run(args, capture_output=True, text=True, check=True)
                if not analysis_json_file.exists():
                    raise CodeanalyzerExecutionException("codeanalyzer-typescript did not generate analysis.json.")
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e
        return TSAnalysis.model_validate_json(analysis_json_file.read_text(encoding="utf-8"))

    # -----[ indexing ]-----
    def _index(self) -> None:
        """Flatten the (recursive) symbol table into signature-keyed lookups, built once, and the
        id → (graph key, kind) index that joins ``can://`` edge endpoints to those keys (TS-10)."""
        self._classes: Dict[str, TSClass] = {}
        self._interfaces: Dict[str, TSInterface] = {}
        self._enums: Dict[str, TSEnum] = {}
        self._type_aliases: Dict[str, TSTypeAlias] = {}
        self._callables: Dict[str, TSCallable] = {}
        self._functions: Dict[str, TSCallable] = {}
        self._methods_by_class: Dict[str, Dict[str, TSCallable]] = {}
        self._file_of: Dict[str, str] = {}
        #: ``can://`` id → (call-graph node key, kind). Modules key on the file key, classes and
        #: callables on ``signature``, externals on ``"<module>.<name>"``.
        self._id_index: Dict[str, Tuple[str, str]] = {}

        for fp, mod in self.application.symbol_table.items():
            self._id_index[mod.id] = (fp, "module")
            for f in mod.functions.values():
                self._add_callable(f, fp)
                self._functions[f.signature] = f
            for cl in mod.classes.values():
                self._add_class(cl, fp)
            for it in mod.interfaces.values():
                self._add_interface(it, fp)
            for en in mod.enums.values():
                self._enums[en.signature] = en
                self._add_type(en, fp)
            for ta in mod.type_aliases.values():
                self._type_aliases[ta.signature] = ta
                self._add_type(ta, fp)
            for ns in mod.namespaces.values():
                self._add_namespace(ns, fp)
        for key, ext in (self.application.external_symbols or {}).items():
            node = (f"{ext.module}.{ext.name}", "external")
            self._id_index[key] = node
            self._id_index[ext.id] = node
        # The compatibility index: keyed by the older anonymous id, the value's id is the tree id
        # that replaced it (already indexed above); a residual fallback node (key == id, no tree
        # home) is keyed by its own name.
        for key, syn in (self.application.synthesized_callables or {}).items():
            self._id_index.setdefault(key, self._id_index.get(syn.id, (syn.name or key, "callable")))

    def _add_type(self, t, fp: str) -> None:
        self._file_of[t.signature] = fp
        self._id_index[t.id] = (t.signature, t.kind)

    def _add_callable(self, c: TSCallable, fp: str) -> None:
        self._callables[c.signature] = c
        self._file_of[c.signature] = fp
        self._id_index[c.id] = (c.signature, "callable")
        for ic in c.inner_callables.values():
            self._add_callable(ic, fp)
        for cl in c.inner_classes.values():
            self._add_class(cl, fp)

    def _add_class(self, cl: TSClass, fp: str) -> None:
        self._classes[cl.signature] = cl
        self._add_type(cl, fp)
        methods: Dict[str, TSCallable] = {}
        for m in cl.methods.values():
            self._add_callable(m, fp)
            methods[m.name] = m
        self._methods_by_class[cl.signature] = methods

    def _add_interface(self, it: TSInterface, fp: str) -> None:
        self._interfaces[it.signature] = it
        self._add_type(it, fp)
        methods: Dict[str, TSCallable] = {}
        for m in it.methods.values():
            self._add_callable(m, fp)
            methods[m.name] = m
        self._methods_by_class[it.signature] = methods

    def _add_namespace(self, ns: TSNamespace, fp: str) -> None:
        self._add_type(ns, fp)
        for f in ns.functions.values():
            self._add_callable(f, fp)
            self._functions[f.signature] = f
        for cl in ns.classes.values():
            self._add_class(cl, fp)
        for it in ns.interfaces.values():
            self._add_interface(it, fp)
        for en in ns.enums.values():
            self._enums[en.signature] = en
            self._add_type(en, fp)
        for ta in ns.type_aliases.values():
            self._type_aliases[ta.signature] = ta
            self._add_type(ta, fp)
        for n in ns.namespaces.values():
            self._add_namespace(n, fp)

    def _node_of(self, node_id: str) -> Tuple[str, str]:
        """The (graph key, kind) an endpoint id resolves to. Every endpoint the analyzer emits is
        homed on the tree, the externals or the synthesized index; one that is not is the
        analyzer's defect, surfaced rather than skipped or keyed by a raw id."""
        try:
            return self._id_index[node_id]
        except KeyError:
            raise CodeanalyzerExecutionException(
                f"call-graph endpoint {node_id!r} is not a module, type, callable, external or synthesized callable "
                f"of application {self.application.id!r}: codeanalyzer-typescript {self.analysis.analyzer.version} "
                "emitted an unhomed endpoint"
            ) from None

    def _callee_signature(self, node: TSBodyNode) -> str | None:
        """The graph key a call node's resolved ``callee`` id maps to; ``None`` when unresolved."""
        if node.callee is None:
            return None
        return self._id_index.get(node.callee, (node.callee, "unresolved"))[0]

    def _callsite(self, key: str, node: TSBodyNode) -> TSCallsite:
        """The 1.x per-call record, read off a ``kind == "call"`` body node."""
        span = node.span
        return TSCallsite(
            method_name=node.method_name or "",
            receiver_expr=node.receiver_expr,
            receiver_type=node.receiver_type,
            argument_types=list(node.argument_types),
            type_arguments=list(node.type_arguments),
            return_type=node.return_type,
            callee_signature=self._callee_signature(node),
            is_constructor_call=node.is_constructor_call,
            is_optional_chain=node.is_optional_chain,
            start_line=span.start[0] if span else -1,
            start_column=span.start[1] if span else -1,
            end_line=span.end[0] if span else -1,
            end_column=span.end[1] if span else -1,
        )

    def _call_nodes(self, c: TSCallable) -> Iterator[Tuple[str, TSBodyNode]]:
        return ((k, n) for k, n in c.body.items() if n.kind == "call")

    def _resolve_callable(self, class_or_module: str, method: str | None = None) -> TSCallable | None:
        """Resolve a callable from either a full signature (``method is None``) or a
        ``(class/module, member)`` pair. Mirrors :meth:`PyCodeanalyzer.get_method` resolution."""
        if method is None:
            return self._callables.get(class_or_module)
        # method grouped under a class/interface signature
        members = self._methods_by_class.get(class_or_module, {})
        if method in members:
            return members[method]
        # by short name within the class/interface
        for m in members.values():
            if m.name == method:
                return m
        # module/namespace-level function addressed as "<module>.<name>"
        composed = f"{class_or_module}.{method}"
        if composed in self._callables:
            return self._callables[composed]
        return None

    def _resolve_signature(self, class_or_sig: str, member: str | None = None) -> str:
        """Resolve a ``(class/module, member)`` pair (or a bare signature) to a signature string.
        Falls back to the composed/literal string so external (phantom) targets still match."""
        if member is None:
            return class_or_sig
        callable_ = self._resolve_callable(class_or_sig, member)
        return callable_.signature if callable_ else f"{class_or_sig}.{member}"

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> TSApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, TSModule]:
        return self.application.symbol_table

    def get_modules(self) -> List[TSModule]:
        return list(self.application.symbol_table.values())

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        return {f"{ext.module}.{ext.name}": ext for ext in (self.application.external_symbols or {}).values()}

    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        return dict(self.application.synthesized_callables or {})

    def get_typescript_file(self, qualified_name: str) -> str | None:
        return self._file_of.get(qualified_name)

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        return self.application.symbol_table.get(file_path)

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) the call graph: nodes keyed as every other accessor keys them (module
        file key, type/callable signature, ``"<module>.<name>"`` for an external) with ``id`` and
        ``kind`` attributes; edges carry ``type="CALL_DEP"``, ``weight`` and ``provenance`` as the
        Python backend's do. Module callers and class callees are kept (TS-11)."""
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for edge in self.application.call_graph:
            src, src_kind = self._node_of(edge.src)
            dst, dst_kind = self._node_of(edge.dst)
            graph.add_node(src, id=edge.src, kind=src_kind)
            graph.add_node(dst, id=edge.dst, kind=dst_kind)
            graph.add_edge(src, dst, type="CALL_DEP", weight=edge.weight, provenance=tuple(edge.prov))
        self._call_graph = graph
        return graph

    def get_call_graph_json(self) -> str:
        return self.application.model_dump_json()

    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        """Callers of a method, with the connecting edge metadata. Mirrors
        :meth:`PyCodeanalyzer.get_all_callers`. Pass a bare signature as the first argument and
        leave ``target_method_declaration`` as ``None`` for module-level / already-resolved
        callables and external (phantom) targets."""
        graph = self.get_call_graph()
        target = self._resolve_signature(target_class_name, target_method_declaration)
        if target not in graph:
            return {"target_method": target, "caller_details": []}
        callers = [{"caller_signature": src, "edge": graph.get_edge_data(src, target)} for src in graph.predecessors(target)]
        return {"target_method": target, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting edge metadata. Mirrors
        :meth:`PyCodeanalyzer.get_all_callees`."""
        graph = self.get_call_graph()
        source = self._resolve_signature(source_class_name, source_method_declaration)
        if source not in graph:
            return {"source_method": source, "callee_details": []}
        callees = [{"callee_signature": tgt, "edge": graph.get_edge_data(source, tgt)} for tgt in graph.successors(source)]
        return {"source_method": source, "callee_details": callees}

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods), in BFS order."""
        graph = self.get_call_graph()
        if method_signature is not None:
            seeds = [method_signature]
        else:
            seeds = [m.signature for m in self._methods_by_class.get(qualified_class_name, {}).values()]
        seeds = [s for s in seeds if s in graph]
        return list(nx.edge_bfs(graph, seeds)) if seeds else []

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base_class."""
        graph = nx.DiGraph()
        for sig in list(self._classes) + list(self._interfaces):
            graph.add_node(sig)
        for sig, cl in self._classes.items():
            for base in cl.base_classes:
                graph.add_edge(sig, base)
        for sig, it in self._interfaces.items():
            for base in it.base_classes:
                graph.add_edge(sig, base)
        return graph

    # -----[ call sites ]-----
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        """The syntactic call sites *inside* a callable (receiver/argument types, resolved
        ``callee_signature``, position) — its ``body`` nodes of ``kind == "call"``. Distinct from
        the resolved call-graph edges."""
        callable_ = self._callables.get(qualified_callable_name)
        return [self._callsite(k, n) for k, n in self._call_nodes(callable_)] if callable_ else []

    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted, de-duplicated source lines anywhere in the project where ``target_signature``
        is invoked (matched against each call node's resolved callee)."""
        lines: Set[int] = set()
        for callable_ in self._callables.values():
            for _, n in self._call_nodes(callable_):
                if n.span is not None and self._callee_signature(n) == target_signature:
                    lines.add(n.span.start[0])
        return sorted(lines)

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The set of call targets invoked from a callable, taken from its call nodes. Resolved
        callee signature when available, otherwise the bare ``method_name``."""
        callable_ = self._callables.get(source_signature)
        if callable_ is None:
            return set()
        return {self._callee_signature(n) or n.method_name or "" for _, n in self._call_nodes(callable_)}

    # -----[ classes / interfaces / enums / type-aliases ]-----
    def get_all_classes(self) -> Dict[str, TSClass]:
        return self._classes

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        return self._classes.get(qualified_class_name)

    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        return self._interfaces

    def get_all_enums(self) -> Dict[str, TSEnum]:
        return self._enums

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        enum = self._enums.get(qualified_enum_name)
        return list(enum.members) if enum else []

    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        return self._type_aliases

    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        # The v2 class facet nests no types (only namespaces and callables do), so a class never
        # has nested classes on this wire; kept for the 1.x surface.
        return []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        return {sig: cls for sig, cls in self._classes.items() if qualified_class_name in cls.base_classes}

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        cls = self._classes.get(qualified_class_name)
        if not cls:
            return []
        return [b for b in cls.base_classes if b not in cls.implements_types]

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        cls = self._classes.get(qualified_class_name)
        return list(cls.implements_types) if cls else []

    # -----[ methods / functions / fields ]-----
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        return self._methods_by_class

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return self._methods_by_class.get(qualified_class_name, {})

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        method = self._methods_by_class.get(qualified_class_name, {}).get(qualified_method_name)
        if method is not None:
            return method
        # Class lookup missed (or the scope isn't a class at all): fall back to module/namespace
        # -level functions, which live in `_functions` rather than `_methods_by_class`.
        return self._resolve_function(qualified_class_name, qualified_method_name)

    def _resolve_function(self, scope: str, name: str) -> TSCallable | None:
        """Resolve a module/namespace-level function: an exact signature match first (``name`` is
        already a full signature, ``scope`` ignored), then a short-name match scoped under
        ``scope`` (handles functions nested in a namespace the caller doesn't know the full path
        of, e.g. ``StringUtil.repeat`` when the caller only knows the module ``src/util``)."""
        exact = self._functions.get(name)
        if exact is not None:
            return exact
        prefix = f"{scope}."
        for sig, fn in self._functions.items():
            if fn.name == name and sig.startswith(prefix):
                return fn
        return None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {name: m for name, m in self._methods_by_class.get(qualified_class_name, {}).items() if m.kind == "constructor"}

    def get_all_functions(self) -> Dict[str, TSCallable]:
        return self._functions

    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        cls = self._classes.get(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        it = self._interfaces.get(qualified_interface_name)
        return list(it.properties.values()) if it else []

    # -----[ imports / exports / variables ]-----
    def get_imports(self) -> Dict[str, List[TSImport]]:
        return {fp: list(m.imports) for fp, m in self.application.symbol_table.items()}

    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        return {fp: list(m.exports) for fp, m in self.application.symbol_table.items()}

    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        """Module-level variable declarations per file."""
        return {fp: list(m.variables) for fp, m in self.application.symbol_table.items()}

    # -----[ repository artifacts — the shared Py* models, as the generic ABC promises ]-----
    @staticmethod
    def _py_config_key(ck: TSConfigKey) -> PyConfigKey:
        """``TSConfigKey.value`` may be a JSON number or boolean (``"strict": true``);
        ``PyConfigKey.value`` is a string, so a non-string value is rendered as its JSON text
        (``true``, ``1``, ``1.5``), which is what the artifact itself says."""
        value = ck.value if isinstance(ck.value, str) or ck.value is None else json.dumps(ck.value)
        return PyConfigKey(id=ck.id, key=ck.key, namespace=ck.namespace, value=value, span=ck.span.model_dump() if ck.span else None, references=list(ck.references))

    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Every non-code artifact (see :meth:`AnalysisBackend.get_artifacts`), keyed by
        repo-relative path as the wire keys them; every ``TSArtifact`` field has a home on
        :class:`PyArtifact`."""
        return {
            path: PyArtifact(**a.model_dump(exclude={"config_keys"}), config_keys=[self._py_config_key(ck) for ck in a.config_keys])
            for path, a in self.application.artifacts.items()
        }

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        """Every declared dependency, optionally filtered (see
        :meth:`AnalysisBackend.get_dependencies`). The TypeScript wire carries no ``ecosystem``
        field — every dependency is an npm package (``pkg:npm/<name>``), so that is what the
        shared model's field says and what the ``ecosystem`` filter matches."""
        deps = [PyDependency(ecosystem="npm", **d.model_dump()) for d in self.application.dependencies]
        if direct_only:
            deps = [d for d in deps if d.direct]
        if ecosystem is not None:
            deps = [d for d in deps if d.ecosystem == ecosystem]
        if declared_in is not None:
            deps = [d for d in deps if d.declared_in == declared_in]
        return deps

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Every configuration key, flattened out of the artifact that defines it and keyed by id
        (see :meth:`AnalysisBackend.get_config_keys`)."""
        return {ck.id: self._py_config_key(ck) for a in self.application.artifacts.values() for ck in a.config_keys}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Resolved code-to-config edges (see :meth:`AnalysisBackend.get_config_uses`)."""
        edges = [PyConfigUseEdge(**u.model_dump()) for u in self.application.config_uses]
        if key is None:
            return edges
        matching_ids = {ck.id for ck in self.get_config_keys().values() if ck.key == key}
        return [e for e in edges if e.dst in matching_ids]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Detector-matched config reads that resolved to no declared key (see
        :meth:`AnalysisBackend.get_unresolved_config_reads`) — ``TSApplication.config_reads``."""
        return [PyConfigRead(**r.model_dump()) for r in self.application.config_reads]

    # -----[ decorators ]-----
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        callable_ = self._callables.get(qualified_callable_name)
        return list(callable_.decorators) if callable_ else []

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        cls = self._classes.get(qualified_class_name)
        return list(cls.decorators) if cls else []

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of callables carrying it."""
        wanted = set(decorators)
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        for sig, c in self._callables.items():
            for dec in c.decorators:
                if dec.name in wanted:
                    result[dec.name].append(sig)
        return result

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of classes carrying it."""
        wanted = set(decorators)
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        for sig, cls in self._classes.items():
            for dec in cls.decorators:
                if dec.name in wanted:
                    result[dec.name].append(sig)
        return result

    # -----[ bulk / projected accessors ]-----
    def _iter_callables(self) -> Iterator[Tuple[TSCallable, str | None, str | None]]:
        """Yield ``(callable, owner_signature, owner_kind)`` for every callable in the
        application, including inner/nested callables. The owner map is built only from
        ``_methods_by_class`` keyed against ``_classes``/``_interfaces``: namespace-owned
        functions and module-level/nested callables are never in that map, so they correctly come
        out owner-less (None, None), per the closed "class"|"interface" owner_kind set."""
        owner_of: Dict[str, Tuple[str, str]] = {}
        for owner_sig, methods in self._methods_by_class.items():
            if owner_sig in self._classes:
                owner_kind = "class"
            elif owner_sig in self._interfaces:
                owner_kind = "interface"
            else:
                continue
            for m in methods.values():
                owner_of[m.signature] = (owner_sig, owner_kind)
        for sig, c in self._callables.items():
            owner_sig, owner_kind = owner_of.get(sig, (None, None))
            yield c, owner_sig, owner_kind

    def get_callables_overview(self) -> List[TSCallableOverview]:
        """Return a lightweight overview of every callable in the application (see
        :meth:`TSAnalysisBackend.get_callables_overview`)."""
        return [TSCallableOverview.from_callable(c, owner_sig, owner_kind, path=self._file_of[c.signature]) for c, owner_sig, owner_kind in self._iter_callables()]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Return ``{signature: code}`` for the requested signatures that exist and have source
        text (omits an implicit constructor, whose empty span slices to ``""``)."""
        result: Dict[str, str] = {}
        for sig in signatures:
            c = self._callables.get(sig)
            if c is not None and c.code:
                result[sig] = c.code
        return result

    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        """Return overviews of callables decorated with any of ``markers``."""
        marker_set = set(markers)
        return [
            TSCallableOverview.from_callable(c, owner_sig, owner_kind, path=self._file_of[c.signature])
            for c, owner_sig, owner_kind in self._iter_callables()
            if marker_set.intersection(d.name for d in c.decorators)
        ]

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        """Return ``{signature: call_sites}`` for the requested signatures that exist."""
        result: Dict[str, List[TSCallsite]] = {}
        for sig in signatures:
            c = self._callables.get(sig)
            if c is not None:
                result[sig] = [self._callsite(k, n) for k, n in self._call_nodes(c)]
        return result
