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

"""Python analysis backend that wraps ``codeanalyzer-python``.

Mirrors :class:`cldk.analysis.java.codeanalyzer.codeanalyzer.JCodeanalyzer`
but runs entirely in-process by importing ``codeanalyzer`` as a library.
Produces a :class:`PyApplication` symbol table and a NetworkX call graph
derived from :class:`PyCallEdge` records.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple, Union

import networkx as nx

from codeanalyzer.config import OutputFormat
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json, model_validate_json

from cldk.analysis import AnalysisLevel
from cldk.analysis.python.codeanalyzer.cache import (
    default_analysis_dir,
    default_backend_cache_dir,
)
from cldk.models.python import (
    PyApplication,
    PyCallEdge,
    PyCallable,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyModule,
)

logger = logging.getLogger(__name__)


class PyCodeanalyzer:
    """In-process driver for ``codeanalyzer-python``.

    Args:
        project_dir: Path to the Python project root.
        analysis_level: Analysis level (symbol_table or call_graph).
        analysis_json_path: Directory to persist analysis.json. If the file
            exists and ``eager_analysis`` is False, it is loaded instead of
            re-running the analyzer. When omitted, a content-addressed
            location under the CLDK cache root is used (see
            :mod:`cldk.analysis.python.codeanalyzer.cache`).
        eager_analysis: If True, always re-runs the analyzer even when a
            cached analysis.json is available.
        analysis_backend_path: Cache directory for the analyzer's virtualenv
            and CodeQL artifacts. Forwarded to ``AnalysisOptions.cache_dir``.
            When omitted, a dependency-hash-keyed location under the CLDK
            cache root is used so the virtualenv survives source edits.
        target_files: Optional single target file (relative to project_dir).
            When provided, only that file is analyzed.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_level: str,
        analysis_json_path: Union[str, Path, None],
        eager_analysis: bool,
        analysis_backend_path: Union[str, Path, None] = None,
        target_files: List[str] | None = None,
        use_codeql: bool = False,
    ) -> None:
        if project_dir is None:
            raise ValueError("project_dir is required for Python analysis.")
        self.project_dir = Path(project_dir)
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.use_codeql = use_codeql

        # Cache locations. Explicit args win; otherwise fall back to the
        # content-addressed CLDK cache (two independently-keyed tiers).
        if analysis_backend_path:
            self.analysis_backend_path = Path(analysis_backend_path)
        else:
            self.analysis_backend_path = default_backend_cache_dir(self.project_dir)
        if analysis_json_path:
            self.analysis_json_path = Path(analysis_json_path)
        else:
            self.analysis_json_path = default_analysis_dir(
                self.project_dir, analysis_level, use_codeql, target_files
            )
        logger.info(
            "CLDK cache — backend: %s | analysis: %s",
            self.analysis_backend_path,
            self.analysis_json_path,
        )

        self.application: PyApplication = self._load_or_run_analyzer()
        # Class-signature → file path lookup, built once.
        self._class_to_file: Dict[str, str] = {}
        for file_path, module in self.application.symbol_table.items():
            for class_sig in module.classes:
                self._class_to_file[class_sig] = file_path

        if analysis_level == AnalysisLevel.call_graph:
            self.call_graph: nx.DiGraph | None = self._build_call_graph(self.application.call_graph)
        else:
            self.call_graph = None

    # ----------------------------------------------------------------- core
    def _load_or_run_analyzer(self) -> PyApplication:
        """Load a cached analysis.json when available, else run the analyzer."""
        cached_file = self.analysis_json_path / "analysis.json" if self.analysis_json_path else None
        if cached_file and cached_file.exists() and not self.eager_analysis:
            logger.info(f"Loading cached PyApplication from {cached_file}")
            return model_validate_json(PyApplication, cached_file.read_text())

        target_file = None
        if self.target_files:
            if len(self.target_files) > 1:
                logger.warning("codeanalyzer-python supports only a single target file; using the first.")
            target_file = Path(self.target_files[0])

        options = AnalysisOptions(
            input=self.project_dir,
            output=self.analysis_json_path,
            format=OutputFormat.JSON,
            using_codeql=self.use_codeql,
            using_ray=False,
            rebuild_analysis=self.eager_analysis,
            skip_tests=True,
            file_name=target_file,
            cache_dir=self.analysis_backend_path,
            clear_cache=False,
            verbosity=0,
        )

        with Codeanalyzer(options) as analyzer:
            app = analyzer.analyze()

        if self.analysis_json_path is not None:
            self.analysis_json_path.mkdir(parents=True, exist_ok=True)
            (self.analysis_json_path / "analysis.json").write_text(model_dump_json(app, indent=None))
        return app

    @staticmethod
    def _build_call_graph(edges: List[PyCallEdge]) -> nx.DiGraph:
        graph = nx.DiGraph()
        for edge in edges:
            graph.add_edge(edge.source, edge.target, type=edge.type, weight=edge.weight, provenance=tuple(edge.provenance))
        return graph

    # --------------------------------------------------------- application
    def get_application_view(self) -> PyApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, PyModule]:
        return self.application.symbol_table

    def get_modules(self) -> List[PyModule]:
        return list(self.application.symbol_table.values())

    def get_call_graph(self) -> nx.DiGraph:
        if self.call_graph is None:
            self.call_graph = self._build_call_graph(self.application.call_graph)
        return self.call_graph

    def get_call_graph_json(self) -> str:
        return model_dump_json(self.application, indent=None)

    def get_python_module(self, file_path: str) -> PyModule | None:
        return self.application.symbol_table.get(str(file_path))

    def get_python_file(self, qualified_class_name: str) -> str | None:
        return self._class_to_file.get(qualified_class_name)

    # ----------------------------------------------------------- classes
    def get_all_classes(self) -> Dict[str, PyClass]:
        result: Dict[str, PyClass] = {}
        for module in self.application.symbol_table.values():
            result.update(module.classes)
        return result

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        return self.get_all_classes().get(qualified_class_name)

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

    # ----------------------------------------------------------- methods
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        """Class signature → {method signature → PyCallable}.

        Module-level functions are included under the module name as the
        outer key.
        """
        result: Dict[str, Dict[str, PyCallable]] = {}
        for module in self.application.symbol_table.values():
            for class_sig, cls in module.classes.items():
                result[class_sig] = dict(cls.methods)
            if module.functions:
                result.setdefault(module.module_name, {}).update(module.functions)
        return result

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        cls = self.get_class(qualified_class_name)
        return dict(cls.methods) if cls else {}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> PyCallable | None:
        methods = self.get_all_methods_in_class(qualified_class_name)
        if qualified_method_name in methods:
            return methods[qualified_method_name]
        # Fallback: match by short name when only the simple name is given.
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

    # ----------------------------------------------------------- callers/callees
    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(target_class_name, target_method_declaration)
        if method is None or method.signature not in graph:
            return {"caller_details": []}
        callers = [
            {"caller_signature": src, "edge": graph.get_edge_data(src, method.signature)}
            for src in graph.predecessors(method.signature)
        ]
        return {"target_method": method.signature, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(source_class_name, source_method_declaration)
        if method is None or method.signature not in graph:
            return {"callee_details": []}
        callees = [
            {"callee_signature": tgt, "edge": graph.get_edge_data(method.signature, tgt)}
            for tgt in graph.successors(method.signature)
        ]
        return {"source_method": method.signature, "callee_details": callees}

    def get_class_call_graph(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[str, str]]:
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

    # ----------------------------------------------------------- comments
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[PyComment]:
        method = self.get_method(qualified_class_name, method_signature)
        return list(method.comments) if method else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[PyComment]:
        cls = self.get_class(qualified_class_name)
        return list(cls.comments) if cls else []

    def get_comment_in_file(self, file_path: str) -> List[PyComment]:
        module = self.get_python_module(file_path)
        return list(module.comments) if module else []

    def get_all_comments(self) -> Dict[str, List[PyComment]]:
        return {fp: list(module.comments) for fp, module in self.application.symbol_table.items()}

    def get_all_docstrings(self) -> Dict[str, List[PyComment]]:
        return {
            fp: [c for c in module.comments if c.is_docstring]
            for fp, module in self.application.symbol_table.items()
        }
