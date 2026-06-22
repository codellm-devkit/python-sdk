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

Subprocess wrapper around the ``codeanalyzer-typescript`` binary (built from ``codeanalyzer-ts``
with ``bun build --compile``). Mirrors the Java ``JCodeanalyzer`` / Python ``PyCodeanalyzer``
pattern: shell out to the analyzer, read ``analysis.json`` from stdout (or an output dir),
validate it into a ``TSApplication`` pydantic model, **and own all query/indexing logic**. The
``TypeScriptAnalysis`` facade is a thin delegating shell over this backend.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from collections import deque
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, List, Set, Tuple, Union

import networkx as nx

from cldk.analysis import AnalysisLevel
from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
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
    TSNamespace,
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class TSCodeanalyzer(TSAnalysisBackend):
    """Build and query the application view of a TypeScript project by invoking the
    codeanalyzer-typescript binary as a subprocess.

    This backend owns all indexing and query logic (symbol lookups, the NetworkX call graph,
    class hierarchy, call sites, entrypoints, decorators, ...). The :class:`TypeScriptAnalysis`
    facade simply delegates to it, mirroring how :class:`PythonAnalysis` delegates to
    :class:`PyCodeanalyzer`.

    Args:
        project_dir: Path to the root of the TypeScript project.
        analysis_backend_path: Directory containing the ``codeanalyzer-typescript`` binary. If
            None, falls back to ``$CODEANALYZER_TS_BIN`` then the ``codeanalyzer-typescript``
            PyPI package (``pip install codeanalyzer-typescript``).
        analysis_json_path: Directory to persist ``analysis.json``. If None, output is read from
            the subprocess stdout pipe.
        analysis_level: ``AnalysisLevel.symbol_table`` (1) or ``AnalysisLevel.call_graph`` (2).
        eager_analysis: If True, re-run the analyzer even if a cached ``analysis.json`` exists.
        target_files: Restrict analysis to these files (incremental).
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
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
        self.application: TSApplication = self._init_codeanalyzer(
            analysis_level=1 if analysis_level == AnalysisLevel.symbol_table else 2
        )
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
        except (ModuleNotFoundError, FileNotFoundError):
            pass

        raise CodeanalyzerExecutionException(
            "codeanalyzer-typescript binary not found. Install it with `pip install codeanalyzer-typescript`, "
            "or set $CODEANALYZER_TS_BIN."
        )

    @staticmethod
    def _init_tsapplication(data: str) -> TSApplication:
        """Build a TSApplication from a stringified analysis.json."""
        return TSApplication(**json.loads(data))

    def _init_codeanalyzer(self, analysis_level: int = 1) -> TSApplication:
        """Run the analyzer and return the validated TSApplication."""
        codeanalyzer_exec = self._get_codeanalyzer_exec()
        target_args: List[str] = []
        if self.target_files:
            for tf in self.target_files:
                target_args += ["-t", str(tf).strip()]

        if self.analysis_json_path is None:
            # Read compact JSON from the stdout pipe.
            args = codeanalyzer_exec + ["-i", str(Path(self.project_dir)), "-a", str(analysis_level)] + target_args
            try:
                logger.info(f"Running codeanalyzer-typescript: {' '.join(args)}")
                console_out: CompletedProcess[str] = subprocess.run(
                    args, capture_output=True, text=True, check=True
                )
                return self._init_tsapplication(console_out.stdout)
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e

        # Persist to an output directory and read analysis.json back.
        analysis_json_file = Path(self.analysis_json_path).joinpath("analysis.json")
        needs_run = self.eager_analysis or not analysis_json_file.exists() or bool(self.target_files)
        if needs_run:
            args = (
                codeanalyzer_exec
                + ["-i", str(Path(self.project_dir)), "-a", str(analysis_level), "-o", str(self.analysis_json_path)]
                + target_args
            )
            try:
                logger.info(f"Running codeanalyzer-typescript: {' '.join(args)}")
                subprocess.run(args, capture_output=True, text=True, check=True)
                if not analysis_json_file.exists():
                    raise CodeanalyzerExecutionException("codeanalyzer-typescript did not generate analysis.json.")
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e
        with open(analysis_json_file, encoding="utf-8") as f:
            return self._init_tsapplication(json.dumps(json.load(f)))

    # -----[ indexing ]-----
    def _index(self) -> None:
        """Flatten the (recursive) symbol table into signature-keyed lookups, built once."""
        self._classes: Dict[str, TSClass] = {}
        self._interfaces: Dict[str, TSInterface] = {}
        self._enums: Dict[str, TSEnum] = {}
        self._type_aliases: Dict[str, TSTypeAlias] = {}
        self._callables: Dict[str, TSCallable] = {}
        self._functions: Dict[str, TSCallable] = {}
        self._methods_by_class: Dict[str, Dict[str, TSCallable]] = {}
        self._file_of: Dict[str, str] = {}

        for fp, mod in self.application.symbol_table.items():
            for f in mod.functions.values():
                self._add_callable(f, fp)
                self._functions[f.signature] = f
            for cl in mod.classes.values():
                self._add_class(cl, fp)
            for it in mod.interfaces.values():
                self._add_interface(it, fp)
            for en in mod.enums.values():
                self._enums[en.signature] = en
                self._file_of[en.signature] = fp
            for ta in mod.type_aliases.values():
                self._type_aliases[ta.signature] = ta
                self._file_of[ta.signature] = fp
            for ns in mod.namespaces.values():
                self._add_namespace(ns, fp)

    def _add_callable(self, c: TSCallable, fp: str) -> None:
        self._callables[c.signature] = c
        self._file_of[c.signature] = fp
        for ic in c.inner_callables.values():
            self._add_callable(ic, fp)
        for cl in c.inner_classes.values():
            self._add_class(cl, fp)

    def _add_class(self, cl: TSClass, fp: str) -> None:
        self._classes[cl.signature] = cl
        self._file_of[cl.signature] = fp
        methods: Dict[str, TSCallable] = {}
        for m in cl.methods.values():
            self._add_callable(m, fp)
            methods[m.name] = m
        self._methods_by_class[cl.signature] = methods
        for ic in cl.inner_classes.values():
            self._add_class(ic, fp)

    def _add_interface(self, it: TSInterface, fp: str) -> None:
        self._interfaces[it.signature] = it
        self._file_of[it.signature] = fp
        methods: Dict[str, TSCallable] = {}
        for m in it.methods.values():
            self._add_callable(m, fp)
            methods[m.name] = m
        self._methods_by_class[it.signature] = methods

    def _add_namespace(self, ns: TSNamespace, fp: str) -> None:
        for f in ns.functions.values():
            self._add_callable(f, fp)
            self._functions[f.signature] = f
        for cl in ns.classes.values():
            self._add_class(cl, fp)
        for it in ns.interfaces.values():
            self._add_interface(it, fp)
        for en in ns.enums.values():
            self._enums[en.signature] = en
            self._file_of[en.signature] = fp
        for ta in ns.type_aliases.values():
            self._type_aliases[ta.signature] = ta
            self._file_of[ta.signature] = fp
        for n in ns.namespaces.values():
            self._add_namespace(n, fp)

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
    def get_application(self) -> TSApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, TSModule]:
        return self.application.symbol_table

    def get_modules(self) -> List[TSModule]:
        return list(self.application.symbol_table.values())

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        return self.application.external_symbols

    def get_typescript_file(self, qualified_name: str) -> str | None:
        return self._file_of.get(qualified_name)

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        return self.application.symbol_table.get(file_path)

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) a NetworkX DiGraph whose nodes are callable signatures (and phantom
        external symbols) and whose edges are the identity-only call edges."""
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for sig, callable_ in self._callables.items():
            graph.add_node(sig, callable=callable_, external=False)
        # Phantom (external) nodes so that import-attributed edges don't dangle.
        for sig, ext in self.application.external_symbols.items():
            graph.add_node(sig, external=True, module=ext.module, name=ext.name)
        for edge in self.application.call_graph:
            graph.add_edge(
                edge.source,
                edge.target,
                type=edge.type,
                weight=edge.weight,
                provenance=edge.provenance,
                tags=edge.tags,
            )
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
        callers = [
            {"caller_signature": src, "edge": graph.get_edge_data(src, target)}
            for src in graph.predecessors(target)
        ]
        return {"target_method": target, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting edge metadata. Mirrors
        :meth:`PyCodeanalyzer.get_all_callees`."""
        graph = self.get_call_graph()
        source = self._resolve_signature(source_class_name, source_method_declaration)
        if source not in graph:
            return {"source_method": source, "callee_details": []}
        callees = [
            {"callee_signature": tgt, "edge": graph.get_edge_data(source, tgt)}
            for tgt in graph.successors(source)
        ]
        return {"source_method": source, "callee_details": callees}

    def get_class_call_graph(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""
        adjacency: Dict[str, List[str]] = {}
        for e in self.application.call_graph:
            adjacency.setdefault(e.source, []).append(e.target)
        if method_signature is not None:
            seeds = [method_signature]
        else:
            seeds = [m.signature for m in self._methods_by_class.get(qualified_class_name, {}).values()]
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
        """The rich, syntactic call sites *inside* a callable (receiver/argument types, resolved
        ``callee_signature``, position). Distinct from the resolved call-graph edges."""
        callable_ = self._callables.get(qualified_callable_name)
        return list(callable_.call_sites) if callable_ else []

    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted, de-duplicated source lines anywhere in the project where ``target_signature``
        is invoked (matched against each call site's resolved ``callee_signature``)."""
        lines: Set[int] = set()
        for callable_ in self._callables.values():
            for cs in callable_.call_sites:
                if cs.callee_signature == target_signature and cs.start_line >= 0:
                    lines.add(cs.start_line)
        return sorted(lines)

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The set of call targets invoked from a callable, taken from its call sites. Resolved
        ``callee_signature`` when available, otherwise the bare ``method_name``."""
        callable_ = self._callables.get(source_signature)
        if callable_ is None:
            return set()
        return {cs.callee_signature or cs.method_name for cs in callable_.call_sites}

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
        cls = self._classes.get(qualified_class_name)
        return list(cls.inner_classes.values()) if cls else []

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
        return self._methods_by_class.get(qualified_class_name, {}).get(qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {
            name: m
            for name, m in self._methods_by_class.get(qualified_class_name, {}).items()
            if m.kind == "constructor"
        }

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
