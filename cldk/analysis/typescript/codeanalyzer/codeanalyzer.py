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
from collections import defaultdict
from functools import cached_property, partial
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, FrozenSet, Iterator, List, Mapping, Sequence, Set, Tuple, Union

import networkx as nx

from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    check_depth,
    check_distinct_endpoints,
    check_max_nodes,
    check_max_paths,
    check_page_size,
    edge_page,
)
from cldk.analysis.commons.graphs import call_reaches, cone_sinks, flow_path, shortest_walks, slice_resolved, under_callable
from cldk.analysis.commons.keys import body_key_column, resolve_module_key
from cldk.analysis.commons.levels import ANALYZER_LEVELS, LEVEL_NAMES, analyzer_level
from cldk.analysis.commons.resolve import CallableCandidate, resolve_callable_signature, resolve_value_name, resolve_within
from cldk.analysis.commons.results import (
    BodyRef,
    CallableRef,
    Diagnostic,
    EdgePage,
    EntrypointCoverage,
    FlowPath,
    FlowPaths,
    LocateResult,
    ModuleRef,
    Slice,
    SliceNode,
    Span,
    TypeRef,
)
from cldk.analysis.typescript.backend import CDG_ORDER, CFG_ORDER, DDG_ORDER, VIA, TSAnalysisBackend, ts_body_node_kind, ts_module_dotted
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.models.typescript import (
    TSAnalysis,
    TSApplication,
    TSBodyNode,
    TSCallable,
    TSCdgEdge,
    TSCfgEdge,
    TSDdgEdge,
    TSCallableOverview,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSClassOverview,
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
    TSSpan,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.analysis import AnalysisLevel
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, CodeanalyzerUsageException

logger = logging.getLogger(__name__)

#: The codeanalyzer-typescript release that removed ``--tsc-only`` (the resolver is no longer a
#: choice; 1.x's ``tsc`` and ``defuse`` provenances are both emitted and tagged per edge).
_TSC_ONLY_REMOVED_IN = "1.0.0"

#: The analyzer level at which cants resolves a call node's ``callee`` (its level-2 pass). Below
#: it every ``call`` body node carries ``callee: null`` -- what ``has_resolution_edges`` reports.
_CALLEE_RESOLUTION_LEVEL = 2


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
                stacklevel=4,  # warn at the CLDK.typescript(...) call, through the facade
            )
        self.analysis: TSAnalysis = self._init_codeanalyzer(analysis_level=analyzer_level(analysis_level))
        self.application: TSApplication = self.analysis.application
        #: The ``--app-name`` the analyzer stamped into every id, read back off the application's
        #: own ``can://<app>`` id. Spelled the same as :attr:`TSNeo4jBackend.application_name`
        #: so a message naming the application reads identically whichever backend raised it -- and
        #: so no message has to embed a ``can://`` id to name it (E6).
        #:
        #: The scheme is *stripped*, never split off: the root id is exactly ``can://<app>`` since
        #: 1.5.1 put the application outermost, and an application legitimately named ``typescript``
        #: makes any positional reading of the segments a coin flip.
        self.application_name: str = self.application.id.removeprefix("can://")
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
        the analyzer stamps into every ``can://<app>/<lang>/...`` id."""
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
        # home) is keyed by its own name. Anything else is an analyzer defect, never a node keyed
        # by a raw id.
        for key, syn in (self.application.synthesized_callables or {}).items():
            if syn.id in self._id_index:
                node = self._id_index[syn.id]
            elif syn.id == key and syn.name:
                node = (syn.name, "callable")
            else:
                raise CodeanalyzerExecutionException(
                    f"synthesized callable {key!r} resolves to {syn.id!r}, which is neither a callable of application "
                    f"{self.application.id!r} nor a named residual node: codeanalyzer-typescript "
                    f"{self.analysis.analyzer.version} emitted an unhomed endpoint"
                )
            self._id_index.setdefault(key, node)

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
        """The graph key a call node's resolved ``callee`` id maps to; ``None`` when the analyzer
        left it unresolved (``null``). A ``callee`` that is neither is the same unhomed-endpoint
        defect :meth:`_node_of` raises for — a raw id never reaches a return field."""
        if node.callee is None:
            return None
        return self._node_of(node.callee)[0]

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

    # ----------------------------------------------------------- entrypoints / config readers
    def get_entrypoints(self) -> List[TSCallableOverview]:
        """Return overviews of every callable marked ``is_entrypoint`` (see
        :meth:`TSAnalysisBackend.get_entrypoints`). ``is_entrypoint`` is ``Optional[bool]``, so
        the test is truthiness, not ``is True``: below 1.3.0 it is ``None``, which is not a mark."""
        return [
            TSCallableOverview.from_callable(c, owner_sig, owner_kind, path=self._file_of[c.signature]) for c, owner_sig, owner_kind in self._iter_callables() if c.is_entrypoint
        ]

    def get_entrypoint_classes(self) -> List[TSClassOverview]:
        """Return overviews of every class marked ``is_entrypoint`` (see
        :meth:`TSAnalysisBackend.get_entrypoint_classes`)."""
        return [TSClassOverview.from_class(cl, path=self._file_of[sig]) for sig, cl in self._classes.items() if cl.is_entrypoint]

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Return the entrypoint pass's coverage record (see
        :meth:`TSAnalysisBackend.get_entrypoint_coverage`) -- a passthrough of
        ``TSApplication.entrypoint_report``, which this backend has in full.

        The field is optional on the model (TS-1 kept it so, because the graph-backed application
        view carries the report as a string property on the anchor rather than as a structured
        field), so its absence is reported rather than fabricated."""
        report = self.application.entrypoint_report
        if report is None:
            return EntrypointCoverage(
                diagnostics=[
                    Diagnostic(
                        code="entrypoint_report_unavailable",
                        message="This analysis.json carries no TSApplication.entrypoint_report, so the entrypoint pass's coverage cannot be reported. "
                        "codeanalyzer-typescript 1.3.0 and newer always emit it.",
                    )
                ]
            )
        return EntrypointCoverage(
            frameworks_detected=list(report.frameworks_detected),
            rulesets=list(report.rulesets),
            unresolved=dict(report.unresolved),
            errors=list(report.errors),
        )

    def get_config_readers(self, key: str) -> List[TSCallableOverview]:
        """Return overviews of every callable reading configuration key ``key`` (see
        :meth:`TSAnalysisBackend.get_config_readers`).

        ``PyConfigUseEdge.src`` is the reading call's own body-node id. It is matched against each
        callable's ``body`` map rather than split on ``@`` back to an owner: an anonymous
        callable's id contains an ``@`` of its own (``…/<anon@22:52>``), so the split the Python
        twin can afford is not sound here."""
        reading = {e.src for e in self.get_config_uses(key)}
        if not reading:
            return []
        return [
            TSCallableOverview.from_callable(c, owner_sig, owner_kind, path=self._file_of[c.signature])
            for c, owner_sig, owner_kind in self._iter_callables()
            if not reading.isdisjoint(node.id for node in (c.body or {}).values())
        ]

    # =====================================================================================
    # The addressing surface (leg 2.5b, TS-2) -- over the in-memory tree.
    # =====================================================================================
    @cached_property
    def _by_module(self) -> Dict[str, List[Tuple[TSCallable, str | None, str | None]]]:
        """``module key -> the callables declared in it``, with their owner pair.

        Built lazily rather than in :meth:`_index`: every existing accessor answers without it, and
        on a real application (superset-frontend: 11,085 callables) an index nobody asked for is
        memory nobody asked for. :meth:`_iter_callables` is the domain, so ``locate`` and
        ``resolve_callable`` see exactly the set ``get_callables_overview`` reports.
        """
        out: Dict[str, List[Tuple[TSCallable, str | None, str | None]]] = defaultdict(list)
        for c, owner_sig, owner_kind in self._iter_callables():
            out[self._file_of[c.signature]].append((c, owner_sig, owner_kind))
        return dict(out)

    def _owner_name(self, owner_sig: str | None) -> str | None:
        owner = self._classes.get(owner_sig or "") or self._interfaces.get(owner_sig or "")
        return owner.name if owner is not None else None

    @staticmethod
    def _contains(span: TSSpan | None, line: int) -> bool:
        return span is not None and span.start[0] <= line <= span.end[0]

    def _not_analysed(self, path: str, line: int) -> LocateResult:
        """The ``file_not_in_graph`` outcome, with the one distinction this backend *can* draw.

        Unlike the Neo4j backend (which attaches to a graph and may not have the project checked
        out), this one runs against the project directory, so it can tell "the file is there and
        was not analysed" -- a ``--target-files`` narrowing, an excluded directory, a parse the
        analyzer skipped -- from "there is no such file". The code stays ``file_not_in_graph``
        either way; the distinction rides in the message, which is the field an agent reads.
        """
        on_disk = Path(path).is_file() or bool(self.project_dir and (Path(self.project_dir) / path).is_file())
        why = "the file exists but no analysed module covers it" if on_disk else "no such file in the analysed project"
        return LocateResult(
            body=None,
            callable=None,
            type=None,
            module=ModuleRef(path=str(path)),
            source="",
            span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
            diagnostics=[Diagnostic(code="file_not_in_graph", message=f"{path} is not covered by any analysed module ({why}).")],
        )

    def _body_ref(self, c: TSCallable, line: int) -> BodyRef | None:
        """The innermost body node of ``c`` containing ``line``, as the language-neutral handle.

        ``None`` is a real outcome, not an error: a position on a declaration line or a blank line
        inside a callable is contained by the callable and by no body node, and the caller still
        gets the callable. Ties break the same way the Neo4j backend breaks them -- narrowest line
        span, then the deeper column parsed out of the node's own key
        (:func:`~cldk.analysis.commons.keys.body_key_column`), then the key -- so both backends
        resolve a tie to the same node.
        """
        matches = [(k, n) for k, n in (c.body or {}).items() if self._contains(n.span, line)]
        if not matches:
            return None
        key, node = min(matches, key=lambda kn: (kn[1].span.end[0] - kn[1].span.start[0], -body_key_column(kn[0]), kn[0]))
        if not node.id:
            raise CodeanalyzerExecutionException(
                f"body node {key!r} of {c.signature!r} carries no id: codeanalyzer-typescript {self.analysis.analyzer.version} "
                "emitted an unaddressable body node (ids are required from 1.3.0, cants#165)"
            )
        return BodyRef(id=node.id, kind=node.kind, span=node.span, callee=node.callee)

    def _locate_one(self, path: str, line: int) -> LocateResult:
        # Whatever the caller's scanner printed ("./src/app.ts", an absolute path) is normalised to
        # the symbol-table key first; an unnormalised path would otherwise read as file_not_in_graph.
        key = resolve_module_key(str(path), self.application.symbol_table.keys())
        module = self.application.symbol_table.get(key)
        if module is None:
            return self._not_analysed(key, line)
        module_ref = ModuleRef(path=key)
        # Innermost callable = narrowest line span containing the position. A position between two
        # callables, or at module scope, matches none and falls through to module_scope rather than
        # snapping to a neighbour. Equal widths (an arrow inside a one-line function) tie, and the
        # tie is broken on the longer signature, deeper first -- a nested callable's signature
        # extends its owner's -- which is the rule the Neo4j backend applies to the same rows.
        found = min(
            ((c, o, k) for c, o, k in self._by_module.get(key, ()) if self._contains(c.span, line)),
            key=lambda cok: (cok[0].span.end[0] - cok[0].span.start[0], -len(cok[0].signature), cok[0].signature),
            default=None,
        )
        if found is None:
            return LocateResult(
                body=None,
                callable=None,
                type=None,
                module=module_ref,
                source=module.source,
                span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
                diagnostics=[Diagnostic(code="module_scope", message=f"line {line} is at module scope in {key}.")],
            )
        c, owner_sig, _ = found
        owner_name = self._owner_name(owner_sig)
        body = self._body_ref(c, line)
        return LocateResult(
            body=body,
            node_id=body.id if body else None,
            callable=CallableRef(signature=c.signature, name=c.name, class_signature=owner_sig),
            type=TypeRef(signature=owner_sig, name=owner_name) if owner_sig and owner_name else None,
            module=module_ref,
            source=c.code or "",
            span=c.span,
            diagnostics=[],
        )

    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable (see :meth:`TSAnalysisBackend.locate`)."""
        return self._locate_one(path, line)

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions (see :meth:`TSAnalysisBackend.locate_many`). Purely in memory
        here -- there is no round trip to batch -- but the results still come back in input order,
        matching the Neo4j backend's contract."""
        return [self._locate_one(path, line) for path, line in positions]

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name against the in-memory tree (see
        :meth:`TSAnalysisBackend.resolve_callable`).

        The candidate domain is :meth:`_iter_callables` -- the same set
        :meth:`get_callables_overview` reports and the same set the Neo4j backend resolves against.
        Nothing is filtered before the shared policy runs: this backend has the whole list in memory
        already, so handing the policy the unfiltered domain is the strongest form of "both backends
        resolve over the same set".
        """
        candidates = [CallableCandidate(c.signature, owner_sig, self._file_of[c.signature]) for c, owner_sig, _ in self._iter_callables()]
        sig = resolve_callable_signature(name, candidates, in_class=in_class, in_module=in_module, dotted=ts_module_dotted)
        c = self._callables[sig]
        return SliceNode(file=self._file_of[sig], line=c.span.start[0], callable=sig, kind="callable", name=c.name, source=None, ref=c.id)

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable (see :meth:`TSAnalysisBackend.resolve_value`).

        ``TSBodyNode.of`` carries the value a ``formal_in`` vertex stands for -- the same fact the
        graph projects as ``b.of`` -- and in TypeScript it is the parameter's own text, with none of
        the ``"<global>:mod::name"`` grammar codeanalyzer-python marks captured globals with. So
        there is nothing to translate and no kind to infer: it is a parameter.
        """
        owner = resolve_within(self.resolve_callable, within)
        c = self._callables[owner.callable]
        # A list, not a dict keyed by name: two values that resolve to the same name are a genuine
        # ambiguity the policy must see and raise on, and a dict would silently keep the last.
        entries = [(n, n.of) for n in (c.body or {}).values() if n.kind == "formal_in" and n.of]
        chosen = resolve_value_name(name, [v for _, v in entries], within=owner.callable)
        node = next(n for n, v in entries if v == chosen)
        return SliceNode(file=owner.file, line=owner.line, callable=owner.callable, kind="parameter", name=chosen, source=None, ref=node.id or "")

    def _sources_for(self, refs: Sequence[str]) -> Dict[str, "str | None"]:
        """Source text for every ref this application holds (see
        :meth:`TSAnalysisBackend._sources_for`).

        One walk of the callable tree for the whole batch, not one per ref. A callable answers to
        both of its names (signature and ``can://`` id); a body node is sliced out of its module by
        its span, so this backend fills in the statements and call sites the graph cannot. A vertex
        with **no** span -- every ``formal_in``/``formal_out``/``actual_*`` -- maps to ``None``: it
        is a dataflow position, not a region of the file, and there is nothing to read on either
        backend. An external ghost is likewise found and textless, by definition.
        """
        wanted = set(refs)
        found: Dict[str, "str | None"] = {}
        for key, ext in (self.application.external_symbols or {}).items():
            for ref in (key, ext.id):
                if ref in wanted:
                    found[ref] = None
        for c, _, _ in self._iter_callables():
            source = self.application.symbol_table[self._file_of[c.signature]].source
            for ref in (c.signature, c.id):
                if ref in wanted:
                    found[ref] = c.code or None
            for node in (c.body or {}).values():
                if node.id in wanted:
                    found[node.id] = source[node.span.bytes[0] : node.span.bytes[1]] if node.span else None
        return found

    def get_source(self, node_id: str) -> str:
        """Source text for one node (see :meth:`TSAnalysisBackend.get_source`).

        Routed through :meth:`_sources_for` rather than re-walking the tree with its own splitting
        rule: a TypeScript body-node id cannot be taken apart on ``@`` (an anonymous callable's own
        id contains one), so the id is looked up whole, and the two ways of having no text stay
        apart -- absent from the mapping is "nothing carries this id", ``None`` is "this exists and
        has no recoverable source".
        """
        found = self._sources_for([node_id])
        if node_id not in found:
            raise KeyError(f"no callable, body node or external symbol of application {self.application_name!r} is addressed by {node_id!r}")
        code = found[node_id]
        if not code:
            raise KeyError(f"no recoverable source for {node_id!r} (it carries no span, or the analyzer emitted no text for it)")
        return code

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`TSAnalysisBackend.has_resolution_edges`. ``True`` from analysis level 2, the
        level at which cants resolves a call node's ``callee``; below it every call node carries
        ``callee: null`` and every ``callee_signature`` from :meth:`get_callsites_for` is ``None``
        for that reason and not because the individual sites failed to resolve. Read off
        ``analysis.max_level`` -- what the analyzer actually produced -- not off the level the
        caller asked for."""
        return self.analysis.max_level >= _CALLEE_RESOLUTION_LEVEL

    # =====================================================================================
    # The dataflow surface (leg 2.5b, Task 2) -- over the in-memory tree.
    #
    # THE LOCAL BACKEND ANSWERS INTERPROCEDURALLY, as the Python twin does and for the same reason:
    # a level-4 run carries the whole SDG in the model, so the cross-callable index this backend
    # lacks it can BUILD out of the very lists ``--emit neo4j`` projects.
    #
    #   TSCallable.ddg / .cdg / .summary   endpoints are LOCAL body keys -> joined through the
    #                                      callable's own ``body`` map, whose nodes carry ``id``
    #   TSApplication.param_in / param_out endpoints are ALREADY global ids
    #
    # ONE DIFFERENCE FROM PYTHON, AND IT IS THE ANALYZER'S, NOT A CHOICE HERE: Python composes a
    # body node's global id from ``(callable id, body key)``. TypeScript reads it off the node --
    # 1.3.0 emits ``id`` on all 125,532 body nodes (cants#165) -- because a TypeScript id cannot be
    # composed safely: an anonymous callable's own id already contains an ``@``
    # (``.../<anon@22:52>``), so the ``<callable id>@<key>`` grammar is not invertible by splitting
    # and is not worth re-deriving when the analyzer states it.
    # =====================================================================================
    #: The analyzer level at which ``cfg``/``cdg``/``ddg`` first exist. Read against
    #: ``analysis.max_level`` -- what the analyzer actually produced -- not the level the caller
    #: asked for, the same rule :attr:`has_resolution_edges` already applies.
    _DATAFLOW_LEVEL = ANALYZER_LEVELS[AnalysisLevel.program_dependency_graph]

    def _require_dataflow(self) -> None:
        """Refuse, naming both levels, when this analysis was built below the dataflow pass.

        The one guard, shared by the per-callable graphs, the slices and the value-flow accessors:
        they come from the same analyzer pass and go dark together, and a second copy of this check
        is a second thing to keep in step. It raises instead of returning empty because at a
        shallower level an empty answer would mean "not analysed" while looking exactly like "no
        dependence" (D7). The Neo4j backend has no such mode -- ``--emit neo4j`` is always full
        depth -- so this is the only place the contract can be broken.
        """
        level = self.analysis.max_level
        if level < self._DATAFLOW_LEVEL:
            raise CodeanalyzerUsageException(
                f"control and data flow need analysis_level='program_dependency_graph' or deeper "
                f"(analyzer level {self._DATAFLOW_LEVEL}); this analysis was produced at "
                f"'{LEVEL_NAMES.get(level, level)}' (analyzer level {level}), where codeanalyzer-typescript emits no "
                "cfg/cdg/ddg at all. Returning an empty result would be indistinguishable from a callable that has "
                "no dependence, so this raises instead. Rebuild with "
                "CLDK.typescript(..., analysis_level='system_dependency_graph')."
            )

    def _body_ids(self, c: TSCallable) -> Dict[str, str]:
        """``local body key -> global body-node id`` for one callable.

        Read off the nodes, never composed (see the block comment above). A span-bearing node
        without an id is an analyzer defect and is raised as one rather than being worked around --
        the same guard :meth:`_body_ref` applies to the addressing surface.
        """
        out: Dict[str, str] = {}
        for key, node in (c.body or {}).items():
            if not node.id:
                raise CodeanalyzerExecutionException(
                    f"body node {key!r} of {c.signature!r} carries no id: codeanalyzer-typescript {self.analysis.analyzer.version} "
                    "emitted an unaddressable body node (ids are required from 1.3.0, cants#165)"
                )
            out[key] = node.id
        return out

    @staticmethod
    def _endpoint(c: TSCallable, ids: Dict[str, str], key: str) -> str:
        """One graph endpoint's global id, refusing a key the callable's ``body`` map does not hold.

        Silently dropping such an edge would make a page's ``total`` disagree with the graph's for
        no reason a caller could see; the graph, whose relationships are between real
        ``:TSBodyNode``s, cannot have the problem at all.
        """
        try:
            return ids[key]
        except KeyError:
            raise CodeanalyzerExecutionException(
                f"an edge of {c.signature!r} names the body key {key!r}, which is not in that callable's body map: "
                "codeanalyzer-typescript emitted a dangling intra-callable edge"
            ) from None

    def _graphs_of(self, name: str, in_class: str | None, page_size: int) -> TSCallable:
        """The callable ``name`` resolves to, once this backend is deep enough to have dataflow.

        ``page_size`` is validated *first*, before the level guard and before resolution, so a
        malformed argument is a ``ValueError`` before anything else -- the order the Neo4j backend
        also applies, so the two cannot answer the same bad call with different exceptions.
        Resolution is :meth:`resolve_callable`'s, not a second path.
        """
        check_page_size(page_size)
        self._require_dataflow()
        return self._callables[self.resolve_callable(name, in_class=in_class).callable]

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCfgEdge]:
        """One page of control flow within one callable (see :meth:`TSAnalysisBackend.get_cfg`)."""
        c = self._graphs_of(callable, in_class, page_size)
        ids = self._body_ids(c)
        edges = [TSCfgEdge(src=self._endpoint(c, ids, e.src), dst=self._endpoint(c, ids, e.dst), kind=e.kind) for e in c.cfg or []]
        return edge_page(TSCfgEdge, c.signature, edges, CFG_ORDER, page_size, cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCdgEdge]:
        """One page of control dependence within one callable (see :meth:`TSAnalysisBackend.get_cdg`)."""
        c = self._graphs_of(callable, in_class, page_size)
        ids = self._body_ids(c)
        edges = [TSCdgEdge(src=self._endpoint(c, ids, e.src), dst=self._endpoint(c, ids, e.dst)) for e in c.cdg or []]
        return edge_page(TSCdgEdge, c.signature, edges, CDG_ORDER, page_size, cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSDdgEdge]:
        """One page of data dependence within one callable (see :meth:`TSAnalysisBackend.get_ddg`)."""
        c = self._graphs_of(callable, in_class, page_size)
        ids = self._body_ids(c)
        edges = [TSDdgEdge(src=self._endpoint(c, ids, e.src), dst=self._endpoint(c, ids, e.dst), var=e.var, prov=list(e.prov or [])) for e in c.ddg or []]
        return edge_page(TSDdgEdge, c.signature, edges, DDG_ORDER, page_size, cursor)

    # -----[ slicing and reachability ]-----
    @cached_property
    def _sdg(self) -> Tuple[Dict[str, Dict[str, Dict[str, list]]], Dict[str, Tuple[TSCallable, TSBodyNode, str]]]:
        """``(adjacency, node index)`` over the whole application's SDG, built once and cached.

        ``adjacency`` is ``{"forward": {src: {dst: [label]}}, "backward": {dst: {src: [label]}}}`` --
        both directions, because a backward slice is not derivable from a forward index without
        inverting it, and inverting it per call is the same work done repeatedly. A ``label`` is
        ``(relationship type, var, prov)``: what a path hop has to report, and what the graph carries
        on the corresponding relationship. It is a **list** per pair because parallel edges are
        ordinary and collapsing them would merge several pieces of evidence into one.

        ``node index`` maps a global body-node id to ``(owning callable, body node, module key)``,
        which is everything :meth:`_slice_node` needs to describe a reached node without a second
        walk. It is built from :meth:`_iter_callables`, so the described set is exactly the graph's:
        ``_SLICE`` there joins each reached node back to a ``:TSCallable`` and drops what it cannot.
        """
        forward: Dict[str, Dict[str, list]] = {}
        backward: Dict[str, Dict[str, list]] = {}
        nodes: Dict[str, Tuple[TSCallable, TSBodyNode, str]] = {}

        def link(src: str, dst: str, label: tuple) -> None:
            forward.setdefault(src, {}).setdefault(dst, []).append(label)
            backward.setdefault(dst, {}).setdefault(src, []).append(label)

        for c, _, _ in self._iter_callables():
            path = self._file_of[c.signature]
            ids = self._body_ids(c)
            for key, node in (c.body or {}).items():
                nodes[ids[key]] = (c, node, path)
            # The relationship name each list is projected as, so a hop reports the same ``via``
            # here as it does over the graph -- ``VIA`` is the single translation table.
            for rel, edges in (("TS_DDG", c.ddg), ("TS_CDG", c.cdg), ("TS_SUMMARY", c.summary)):
                for e in edges or []:
                    link(
                        self._endpoint(c, ids, e.src),
                        self._endpoint(c, ids, e.dst),
                        (rel, getattr(e, "var", None), tuple(getattr(e, "prov", None) or ())),
                    )
        # Endpoints here are already global, so they are used as-is -- joining them again would
        # mint ids that name nothing.
        for rel, edges in (("TS_PARAM_IN", self.application.param_in), ("TS_PARAM_OUT", self.application.param_out)):
            for e in edges or []:
                link(e.src, e.dst, (rel, e.var, ()))
        return {"forward": forward, "backward": backward}, nodes

    def _reach(self, ref: str, direction: str, depth: int | None) -> set:
        """The set of node ids reachable from ``ref`` in at most ``depth`` hops.

        Level-by-level rather than a plain stack, because ``depth`` is a hop budget and a
        depth-first walk cannot count hops without revisiting. Shared by the slices and by the two
        flow predicates so "reachable" means one thing on this backend.
        """
        edges = self._sdg[0][direction]
        seen, frontier, hops = {ref}, [ref], 0
        while frontier and (depth is None or hops < depth):
            nxt = [d for src in frontier for d in edges.get(src, ()) if d not in seen]
            seen.update(nxt)
            frontier = nxt
            hops += 1
        return seen

    def _slice_node(self, ref: str) -> SliceNode:
        """One reached body node in the caller's vocabulary.

        A parameter-passing vertex has no span of its own -- it is a dataflow position, not a region
        of the file (55,778 of the reference application's 125,532 body nodes carry no lines at
        all) -- so the *callable's* first line stands in, which is where a reader would go looking
        for it and what the Neo4j projection's ``coalesce`` produces from the same two properties.
        """
        c, node, path = self._sdg[1][ref]
        kind, name = ts_body_node_kind(node.kind, node.of)
        return SliceNode(file=path, line=node.span.start[0] if node.span else c.span.start[0], callable=c.signature, kind=kind, name=name, source=None, ref=ref)

    def _slice_from(self, root: SliceNode, direction: str, depth: int | None, max_nodes: int) -> Slice:
        """:meth:`_reach`'s closure from ``root``, described and capped like the graph's.

        The whole closure is computed and *then* cut: ``total`` has to be the size of the whole
        slice for the cap to be reportable (E5), and there is nothing cheaper to compute it from --
        the same reason the Cypher counts before it pages.
        """
        nodes = self._sdg[1]
        found = [self._slice_node(ref) for ref in sorted(self._reach(root.ref, direction, depth)) if ref in nodes]
        return Slice(nodes=found[:max_nodes], roots=[root], resolved=slice_resolved([root]), total=len(found))

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What affects this value (see :meth:`TSAnalysisBackend.slice_backward`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_dataflow()
        return self._slice_from(self.resolve_value(src, within=within), "backward", depth, max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What this value affects (see :meth:`TSAnalysisBackend.slice_forward`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_dataflow()
        return self._slice_from(self.resolve_value(src, within=within), "forward", depth, max_nodes)

    # -----[ the call graph, in the caller's vocabulary ]-----
    @cached_property
    def _externals_by_key(self) -> Dict[str, TSExternalSymbol]:
        """``"<module>.<name>" -> the external`` — the key the call graph uses for a ghost, which is
        not the key :meth:`get_external_symbols` is keyed by (that is the analyzer's own wire key)."""
        return {f"{e.module}.{e.name}": e for e in (self.application.external_symbols or {}).values()}

    def _vertex(self, key: str, kind: str, node_id: str) -> SliceNode:
        """One call-graph vertex as a :class:`SliceNode`, whatever kind of thing it is.

        Three shapes, because TypeScript's call graph has three (TS-11): a declared callable; a
        **module**, which cants makes the caller of its own top-level code and which is addressed by
        its file key everywhere on this surface; and an **external** ghost, which was never analysed
        and so has no position -- ``file=""`` and ``line=0``, with ``kind`` saying why. Its
        ``callable`` is the readable ``"<module>.<name>"``, never its ``can://`` id (E6).
        """
        if kind == "module":
            module = self.application.symbol_table.get(key)
            return SliceNode(file=key, line=module.span.start[0] if module else 0, callable=key, kind="module", name=key, source=None, ref=node_id)
        if kind == "external":
            ext = self._externals_by_key.get(key)
            return SliceNode(file="", line=0, callable=key, kind="external", name=ext.name if ext else key, source=None, ref=node_id)
        c = self._callables.get(key)
        if c is not None:
            return SliceNode(file=self._file_of[key], line=c.span.start[0], callable=key, kind="callable", name=c.name, source=None, ref=node_id)
        # A type vertex -- a class as the callee of ``new X()``. None occur on the reference
        # application (its 17,712 call edges name only callables, modules and externals), but the
        # id index keeps the five type kinds, so the shape is described rather than dropped.
        return SliceNode(file=self._file_of.get(key, ""), line=0, callable=key, kind=kind, name=key.rsplit(".", 1)[-1], source=None, ref=node_id)

    def _call_graph_node(self, key: str) -> SliceNode:
        """A vertex of :meth:`get_call_graph`, described from the ``id``/``kind`` it already carries."""
        attrs = self.get_call_graph().nodes[key]
        return self._vertex(key, attrs["kind"], attrs["id"])

    @property
    def _callable_call_graph(self) -> nx.DiGraph:
        """The call graph restricted to callable vertices — the domain ``reaches`` and
        ``call_paths_between`` walk.

        A view, not a copy. It is what the Neo4j backend's hop-by-hop ``:TSCallable`` predicate
        selects, written here so the two cannot disagree: a module has no *incoming* call edge and
        an external no *outgoing* one on the reference graph, so restricting changes no answer
        today, and it is what keeps a future emitter from silently widening one.
        """
        graph = self.get_call_graph()
        return graph.subgraph([n for n, a in graph.nodes(data=True) if a.get("kind") == "callable"])

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path (see :meth:`TSAnalysisBackend.reaches`)?"""
        check_depth(depth)
        a = self.resolve_callable(src).callable
        b = self.resolve_callable(dst).callable
        # See :func:`call_reaches`: the self-question is the cycle question, and the Neo4j backend's
        # ``{1,depth}`` pattern has always answered it that way.
        return call_reaches(self._callable_call_graph, a, b, depth)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything that can reach these sinks (see :meth:`TSAnalysisBackend.backward_cone`).

        Walked over the **whole** call graph and not the callable-only view: a module is the caller
        of its own top-level code, so it is part of the answer to "what could get here" even though
        it can never be the interior of a path.
        """
        check_depth(depth)
        check_max_nodes(max_nodes)
        roots = cone_sinks(self.resolve_callable, sinks)
        graph = self.get_call_graph()
        described: Dict[str, SliceNode] = {r.callable: r for r in roots}
        for root in roots:
            if root.callable not in graph:
                continue
            # ``ego_graph`` follows *successors*, so a backward question needs the reversed view.
            back = graph.reverse(copy=False)
            reached = nx.ancestors(graph, root.callable) if depth is None else set(nx.ego_graph(back, root.callable, radius=depth).nodes)
            for key in reached:
                described.setdefault(key, self._call_graph_node(key))
        # Ordered by ``ref``, the order ``Slice.nodes`` documents and the graph's ``ORDER BY m.id``
        # produces -- not by signature, which would make a capped cone return different vertices per
        # backend.
        found = sorted(described.values(), key=lambda n: n.ref)
        return Slice(nodes=found[:max_nodes], roots=roots, resolved=slice_resolved(roots), total=len(found))

    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Who calls this, module callers included (see :meth:`TSAnalysisBackend.callers_of`)."""
        return self._call_neighbours(name, in_class, in_module, callers=True)

    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """What this calls, externals included (see :meth:`TSAnalysisBackend.callees_of`)."""
        return self._call_neighbours(name, in_class, in_module, callers=False)

    def _call_neighbours(self, name: str, in_class: str | None, in_module: str | None, *, callers: bool) -> List[SliceNode]:
        """One hop of the call graph, in the caller's vocabulary, ordered by ``ref``.

        The order is stated rather than left to the graph: NetworkX hands back insertion order and
        Cypher hands back none, so a caller comparing the two backends would be comparing two
        arbitrary orders. ``ref`` is the one total order both can compute.
        """
        sig = self.resolve_callable(name, in_class=in_class, in_module=in_module).callable
        graph = self.get_call_graph()
        if sig not in graph:
            return []
        others = graph.predecessors(sig) if callers else graph.successors(sig)
        return sorted((self._call_graph_node(other) for other in others), key=lambda n: n.ref)

    # -----[ paths and flow predicates ]-----
    #: Up to ``limit`` shortest walks over a ``{src: {dst: [label]}}`` adjacency, in
    #: :func:`~cldk.analysis.commons.graphs.hop_sort_key` order -- the shared implementation, bound
    #: to TypeScript's ``via`` table.
    _shortest_walks = staticmethod(partial(shortest_walks, via=VIA))

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
        """The sanitized shortest walks, in process (see :meth:`TSAnalysisBackend._taint_walk`).

        ``self._require_dataflow()`` first, per Ruling F and :meth:`TSAnalysisBackend.taint`'s own
        note: the level gate is a local backend's to ask, because the graph backends have no level to
        measure. It is first so that a *direct* call to this hook is diagnosed by level rather than by
        an empty walk. Through ``taint()`` it never fires -- resolution runs before the walk and the
        ports it addresses exist only at level 4, so a shallow caller hears ``SelectorNotInGraph``
        naming their value instead. Python's :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.taint`
        docstring carries the whole argument, including why the unreachable gate is still worth having.

        One :func:`~cldk.analysis.commons.graphs.shortest_walks` call **per pair**, which is what
        makes ``max_paths + 1`` a per-pair cap here the way ``collect(p)[0..$cap]`` is one over
        Cypher -- a single walk over the flattened source and sink lists would let one prolific pair
        starve the rest. Pairs are deduplicated by resolved position first, for the same reason
        :func:`~cldk.analysis.commons.graphs.taint_verdict` deduplicates the requested ones: two
        selectors naming one position are one pair, and walking it twice would report each witness
        twice and make a cap of *m* yield *2m*. The graph side gets that free from ``a.id IN $srcs``.

        Both cuts are :func:`~cldk.analysis.commons.graphs.shortest_walks`' predicates rather than a
        filter over the walks it returns, which is the property the whole design rests on: the
        breadth-first pass must measure the shortest *satisfying* distance, or a sanitized short
        route hides a clean longer one and the pair comes back refuted. ``allow_edge`` reads the
        hop's **start** node, mirroring the Cypher predicate's ``startNode(r)`` term, so a variable
        cut severs only the callable the caller named it in. Both are ``None`` when nothing is
        sanitized -- the documented "no filtering" default, and no per-node cost on the common call.

        Scoping goes through :func:`~cldk.analysis.commons.graphs.under_callable` and never through
        ``startswith``, and on TypeScript that is not a stylistic preference (Ruling K): a
        TypeScript callable id ends in a bare member name, so ``.../UserService/create`` is a strict
        non-delimited prefix of ``.../UserService/createGuest`` and a bare prefix test would sever
        every hop in the sibling the caller never named. Python and Java ids end in ``)``, which is
        why the Python twin can get away with the shorter spelling.

        The ledger comes back empty, and the graph backend's twin states the reason in full
        (:meth:`~cldk.analysis.typescript.neo4j.neo4j_backend.TSNeo4jBackend._taint_walk`). In
        short: a signal does exist here, as ``TSBodyNode.callee is None`` on a ``call`` node -- the
        same one :attr:`has_resolution_edges` reads at the application level, and measured 0 of the
        a4 fixture's 31 call nodes, so the fixture has nothing to file -- so this is a refusal to
        file rather than an absence to report. Filing a diagnostic voids ``exhausted`` for the whole
        batch (Ruling I), and a signal that cannot yet be told apart from an ordinary call into an
        ambient declaration would void every refutation in every application that makes one. Same
        consequence, equally uncatchable from in here: a pair whose flow leaves through an
        unresolved call is certified ``exhausted``.
        """
        self._require_dataflow()
        adjacency, nodes = self._sdg
        allow_node = (lambda nid: not under_callable(nid, cut_callables)) if cut_callables else None
        allow_edge = (lambda frm, _rel, var: not any(var == c["var"] and under_callable(frm, (c["prefix"],)) for c in cuts)) if cuts else None
        pairs: Dict[Tuple[str, str], SliceNode] = {}
        for a in srcs:
            for b in dsts:
                pairs.setdefault((a.ref, b.ref), a)
        rows = []
        for (src_ref, dst_ref), a in pairs.items():
            walks = self._shortest_walks(adjacency["forward"], src_ref, dst_ref, depth, max_paths + 1, allow_edge=allow_edge, allow_node=allow_node)
            described = {ref: self._slice_node(ref) for walk in walks for ref, _ in walk if ref in nodes}
            described[src_ref] = a
            rows.extend((src_ref, dst_ref, flow_path([described[src_ref]] + [described[ref] for ref, _ in walk], [label for _, label in walk], via=VIA)) for walk in walks)
        return rows, {}

    def _edge_vars_in(self, callable_id: str) -> FrozenSet[str]:
        """The edge variables scoped to this callable (see :meth:`TSAnalysisBackend._edge_vars_in`).

        Off the adjacency this backend already builds and caches, so a variable sanitizer costs a
        scan of it and no second traversal. Edges *leaving* a node under ``callable_id`` -- the same
        ``startNode`` scoping the cut itself uses, so this validates exactly the domain the cut can
        match. Two of the five relationship types carry no ``var`` (``TS_CDG`` and ``TS_SUMMARY``);
        those ``None`` values are dropped, because ``resolve_sanitizers`` refuses a blank variable
        before it asks.

        :func:`~cldk.analysis.commons.graphs.under_callable` and not ``startswith`` for Ruling K's
        reason, spelled out in :meth:`_taint_walk`: with a bare prefix test ``create``'s domain would
        silently include every variable of ``createGuest``, and a sanitizer naming one of those would
        be *accepted* here and then cut nothing there -- a sanitizer the caller believes is in force.
        """
        forward = self._sdg[0]["forward"]
        return frozenset(var for src, outs in forward.items() if under_callable(src, (callable_id,)) for labels in outs.values() for _rel, var, _prov in labels if var)

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value (see :meth:`TSAnalysisBackend.paths_between`)."""
        check_depth(depth)
        check_max_paths(max_paths)
        self._require_dataflow()
        a = self.resolve_value(src, within=src_within)
        b = self.resolve_value(dst, within=dst_within)
        check_distinct_endpoints(a, b)
        adjacency, nodes = self._sdg
        walks = self._shortest_walks(adjacency["forward"], a.ref, b.ref, depth, max_paths + 1)
        described = {ref: self._slice_node(ref) for walk in walks for ref, _ in walk if ref in nodes}
        described[a.ref] = a
        paths = [flow_path([described[a.ref]] + [described[ref] for ref, _ in walk], [label for _, label in walk], via=VIA) for walk in walks[:max_paths]]
        return FlowPaths(paths=paths, complete=len(walks) <= max_paths)

    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How one callable reaches another (see :meth:`TSAnalysisBackend.call_paths_between`).

        Over the callable-only view :meth:`reaches` walks, so the paths cannot disagree with the
        boolean that summarises them.
        """
        check_depth(depth)
        check_max_paths(max_paths)
        a_node, b_node = self.resolve_callable(src), self.resolve_callable(dst)
        check_distinct_endpoints(a_node, b_node)
        a, b = a_node.callable, b_node.callable
        graph = self._callable_call_graph
        if a not in graph or b not in graph:
            return FlowPaths(paths=[], complete=True)
        # The call graph re-projected as this module's ``{src: {dst: [label]}}`` adjacency, so one
        # walker serves both kinds of path.
        edges: Dict[str, Dict[str, list]] = {n: {m: [("TS_CALLS", None, ())] for m in graph.successors(n)} for n in graph}
        walks = self._shortest_walks(edges, a, b, depth, max_paths + 1)
        described = {key: self._call_graph_node(key) for walk in walks for key, _ in walk}
        described[a] = a_node
        paths = [flow_path([described[a]] + [described[key] for key, _ in walk], [label for _, label in walk], via=VIA) for walk in walks[:max_paths]]
        return FlowPaths(paths=paths, complete=len(walks) <= max_paths)

    def _callee_values(self, signature: str) -> List[str]:
        """The ids of every value that *enters* ``signature`` -- in TypeScript, its parameters. The
        local twin of the graph's ``formal_in`` body nodes."""
        c = self._callables.get(signature)
        return [n.id for n in (c.body or {}).values() if n.kind == "formal_in" and n.id] if c else []

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach any argument of a call to ``callee``
        (see :meth:`TSAnalysisBackend.flows_to_call`)?"""
        check_depth(depth)
        self._require_dataflow()
        root = self.resolve_value(src, within=within)
        targets = self._callee_values(self.resolve_callable(callee).callable)
        return bool(targets) and not self._reach(root.ref, "forward", depth).isdisjoint(set(targets) - {root.ref})

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach ``callee``'s ``arg``
        (see :meth:`TSAnalysisBackend.flows_to_argument`)?"""
        check_depth(depth)
        self._require_dataflow()
        root = self.resolve_value(src, within=within)
        target = self.resolve_value(arg, within=callee).ref
        return target != root.ref and target in self._reach(root.ref, "forward", depth)
