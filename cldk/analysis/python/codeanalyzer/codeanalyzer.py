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

"""Python analysis backend that wraps the ``codeanalyzer-python`` library.

This module provides the :class:`PyCodeanalyzer` class, which serves as the
in-process driver for Python static analysis. Unlike the Java backend (which
spawns an external JAR process), this backend imports and uses the
``codeanalyzer-python`` library directly within the same Python process.

The backend produces:
    - A :class:`~cldk.models.python.PyApplication` containing the full symbol
      table with modules, classes, methods, and their relationships.
    - A NetworkX :class:`~networkx.DiGraph` call graph derived from
      :class:`~cldk.models.python.PyCallEdge` records.

The analysis leverages:
    - **Jedi**: For semantic code understanding and symbol resolution.
    - **CodeQL** (optional): For enhanced call graph resolution.
    - **Tree-sitter**: For fast syntactic parsing.

Key features:
    - Symbol table extraction (classes, methods, functions, imports)
    - Call graph construction (inter- and intra-procedural)
    - Class hierarchy and inheritance analysis
    - Comment and docstring extraction

Note:
    This module is typically used internally by :class:`~cldk.analysis.python.PythonAnalysis`.
    Users should prefer the higher-level facade for most use cases.

See Also:
    - :class:`~cldk.analysis.python.PythonAnalysis`: High-level facade.
    - :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Java equivalent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple, Union

import networkx as nx

from codeanalyzer.config import OutputFormat
from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions
from codeanalyzer.schema import model_dump_json

from cldk.analysis import AnalysisLevel
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
    """In-process driver for the ``codeanalyzer-python`` analysis backend.

    This class serves as the primary interface to the codeanalyzer-python
    library, managing analysis execution, caching, and result retrieval.
    It runs entirely in-process, importing the codeanalyzer library directly
    rather than spawning external processes.

    The analyzer produces a :class:`~cldk.models.python.PyApplication` containing:
        - A complete symbol table mapping file paths to module objects
        - Class definitions with methods, attributes, and inheritance info
        - Function definitions with signatures and parameters
        - Import statements and their resolution
        - A call graph (when ``analysis_level`` is ``"call_graph"``)

    Attributes:
        project_dir (Path): Path to the Python project being analyzed.
        analysis_level (str): The depth of analysis performed.
        eager_analysis (bool): Whether to force regeneration of caches.
        target_files (List[str] | None): Specific files to analyze.
        use_codeql (bool): Whether CodeQL is used for call graph enhancement.
        cache_dir (Path | None): Cache directory for the backend.
        analysis_json_path (Path | None): Path for persisting analysis results.
        application (PyApplication): The analyzed application model.
        call_graph (nx.DiGraph | None): The call graph (if analysis_level is call_graph).

    See Also:
        - :class:`~cldk.analysis.python.PythonAnalysis`: High-level facade.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_level: str,
        analysis_json_path: Union[str, Path, None],
        eager_analysis: bool,
        cache_dir: Union[str, Path, None] = None,
        target_files: List[str] | None = None,
        use_codeql: bool = True,
    ) -> None:
        """Initialize the Python code analyzer and run analysis.

        Creates a new analyzer instance for the specified project. Analysis
        is performed immediately during initialization, with results cached
        for subsequent method calls.

        Args:
            project_dir: Absolute or relative path to the Python project root
                directory. This directory should contain Python source files
                to analyze. Required; cannot be ``None``.
            analysis_level: The depth of analysis to perform. Use
                ``"symbol_table"`` for basic symbol extraction or
                ``"call_graph"`` for full call graph construction.
                See :class:`~cldk.analysis.AnalysisLevel` for options.
            analysis_json_path: Path where the analysis results should be
                persisted. Forwarded directly to the codeanalyzer-python
                backend's ``output`` option. If ``None``, the backend
                uses its default location.
            eager_analysis: If ``True``, forces the backend to rebuild
                its analysis from scratch, ignoring any cached results.
                If ``False``, cached results are reused when available.
            cache_dir: Directory for codeanalyzer-python's caches, including
                its virtualenv, CodeQL database, and analysis cache files.
                If ``None``, defaults to ``<project_dir>/.codeanalyzer``.
            target_files: Optional list of specific files to analyze. Note
                that codeanalyzer-python currently supports only a single
                target file; if multiple are provided, only the first is
                used and a warning is logged.
            use_codeql: If ``True`` (default), uses CodeQL to enhance call
                graph resolution beyond what Jedi provides. Set to ``False``
                for faster analysis without CodeQL.

        Raises:
            ValueError: If ``project_dir`` is ``None``.

        Note:
            Analysis is performed synchronously during initialization.
            For large projects, this may take significant time, especially
            with ``use_codeql=True``.
        """
        if project_dir is None:
            raise ValueError("project_dir is required for Python analysis.")
        # Expand ~ and resolve to absolute path for robustness
        self.project_dir = Path(project_dir).expanduser().resolve()
        if not self.project_dir.is_dir():
            raise ValueError(f"project_dir does not exist or is not a directory: {self.project_dir}")
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.use_codeql = use_codeql

        # codeanalyzer-python owns all caching. CLDK forwards these paths
        # verbatim; when cache_dir is None the backend defaults it to
        # <project_dir>/.codeanalyzer.
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
        self.analysis_json_path = Path(analysis_json_path).expanduser().resolve() if analysis_json_path else None

        self.application: PyApplication = self._run_analyzer()
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
    def _run_analyzer(self) -> PyApplication:
        """Execute the codeanalyzer-python analysis and return results.

        Configures and runs the codeanalyzer-python backend with the options
        specified during initialization. The backend handles all caching
        internally.

        Returns:
            A :class:`~cldk.models.python.PyApplication` object containing
            the complete analysis results, including the symbol table and
            call graph edges.

        Note:
            If ``target_files`` contains multiple files, only the first
            is used (with a warning logged) as codeanalyzer-python currently
            supports single-file targeting only.
        """
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
            cache_dir=self.cache_dir,
            clear_cache=False,
            verbosity=0,
        )

        with Codeanalyzer(options) as analyzer:
            return analyzer.analyze()

    @staticmethod
    def _build_call_graph(edges: List[PyCallEdge]) -> nx.DiGraph:
        """Convert a list of call edges into a NetworkX directed graph.

        Transforms the flat list of :class:`PyCallEdge` objects from the
        analysis results into a NetworkX directed graph structure for
        efficient graph queries.

        Args:
            edges: List of :class:`~cldk.models.python.PyCallEdge` objects
                representing call relationships between methods/functions.

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes are method/function signatures (strings)
                - Edges represent call relationships from caller to callee
                - Edge attributes include ``type``, ``weight``, and ``provenance``
        """
        graph = nx.DiGraph()
        for edge in edges:
            graph.add_edge(edge.source, edge.target, type=edge.type, weight=edge.weight, provenance=tuple(edge.provenance))
        return graph

    # --------------------------------------------------------- application
    def get_application_view(self) -> PyApplication:
        """Return the complete analyzed application model.

        Returns:
            The :class:`~cldk.models.python.PyApplication` object containing
            all analysis results for the project.
        """
        return self.application

    def get_symbol_table(self) -> Dict[str, PyModule]:
        """Return the symbol table mapping file paths to modules.

        Returns:
            A dictionary where keys are file paths (strings) and values
            are :class:`~cldk.models.python.PyModule` objects.
        """
        return self.application.symbol_table

    def get_modules(self) -> List[PyModule]:
        """Return all analyzed modules as a list.

        Returns:
            A list of :class:`~cldk.models.python.PyModule` objects,
            one for each analyzed Python file.
        """
        return list(self.application.symbol_table.values())

    def get_call_graph(self) -> nx.DiGraph:
        """Return the call graph as a NetworkX directed graph.

        Lazily builds the call graph from edge data if not already constructed.

        Returns:
            A ``networkx.DiGraph`` representing method/function call
            relationships across the project.
        """
        if self.call_graph is None:
            self.call_graph = self._build_call_graph(self.application.call_graph)
        return self.call_graph

    def get_call_graph_json(self) -> str:
        """Return the complete application model serialized as JSON.

        Returns:
            A JSON string containing the full analysis results,
            suitable for persistence or external tool consumption.
        """
        return model_dump_json(self.application, indent=None)

    def get_python_module(self, file_path: str) -> PyModule | None:
        """Return the module object for a specific file path.

        Args:
            file_path: The path to the Python file.

        Returns:
            The :class:`~cldk.models.python.PyModule` for the file,
            or ``None`` if not found.
        """
        return self.application.symbol_table.get(str(file_path))

    def get_python_file(self, qualified_class_name: str) -> str | None:
        """Return the file path containing a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            The file path as a string, or ``None`` if the class is not found.
        """
        return self._class_to_file.get(qualified_class_name)

    # ----------------------------------------------------------- classes
    def get_all_classes(self) -> Dict[str, PyClass]:
        """Return all classes from all modules in the project.

        Aggregates class definitions from all analyzed modules into a
        single dictionary for convenient access.

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.python.PyClass` objects.
        """
        result: Dict[str, PyClass] = {}
        for module in self.application.symbol_table.values():
            result.update(module.classes)
        return result

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        """Return a specific class by its qualified name.

        Args:
            qualified_class_name: The fully qualified class name
                (e.g., ``"mypackage.models.User"``).

        Returns:
            The :class:`~cldk.models.python.PyClass` object,
            or ``None`` if not found.
        """
        return self.get_all_classes().get(qualified_class_name)

    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """Return inner classes defined within a specific class.

        Args:
            qualified_class_name: The fully qualified name of the outer class.

        Returns:
            A list of :class:`~cldk.models.python.PyClass` objects for
            each nested class. Empty list if no nested classes or class
            not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.inner_classes.values()) if cls else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Return all classes that inherit from a specific class.

        Searches all classes in the project for those that extend the
        specified base class, using both short and qualified name matching.

        Args:
            qualified_class_name: The fully qualified name of the base class.

        Returns:
            A dictionary mapping qualified names to
            :class:`~cldk.models.python.PyClass` objects for all subclasses.
            Returns empty dict if the base class is not found.
        """
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
        """Return the base class names for a specific class.

        Args:
            qualified_class_name: The fully qualified name of the class.

        Returns:
            A list of base class names as strings. Returns empty list
            if the class is not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.base_classes) if cls else []

    # ----------------------------------------------------------- methods
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return all methods in the project grouped by class.

        Aggregates all methods from all classes, plus module-level functions,
        into a nested dictionary structure.

        Returns:
            A nested dictionary with structure::

                {
                    "qualified.class.Name": {
                        "method_signature": PyCallable,
                        ...
                    },
                    "module.name": {  # for module-level functions
                        "function_signature": PyCallable,
                        ...
                    },
                    ...
                }

        Note:
            Module-level functions are included under the module name as
            the outer key, allowing unified access to all callables.
        """
        result: Dict[str, Dict[str, PyCallable]] = {}
        for module in self.application.symbol_table.values():
            for class_sig, cls in module.classes.items():
                result[class_sig] = dict(cls.methods)
            if module.functions:
                result.setdefault(module.module_name, {}).update(module.functions)
        return result

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return all methods defined in a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A dictionary mapping method signatures to
            :class:`~cldk.models.python.PyCallable` objects.
            Returns empty dict if class not found.
        """
        cls = self.get_class(qualified_class_name)
        return dict(cls.methods) if cls else {}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> PyCallable | None:
        """Return a specific method by class and method name.

        Supports both fully qualified method names and simple method names.
        When a simple name is provided, falls back to matching by the
        method's ``name`` attribute.

        Args:
            qualified_class_name: The fully qualified class name.
            qualified_method_name: The method name or signature to find.

        Returns:
            The :class:`~cldk.models.python.PyCallable` object,
            or ``None`` if not found.
        """
        methods = self.get_all_methods_in_class(qualified_class_name)
        if qualified_method_name in methods:
            return methods[qualified_method_name]
        # Fallback: match by short name when only the simple name is given.
        for sig, callable_ in methods.items():
            if callable_.name == qualified_method_name:
                return callable_
        return None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """Return parameter names for a specific method.

        Args:
            qualified_class_name: The fully qualified class name.
            qualified_method_name: The method name or signature.

        Returns:
            A list of parameter names as strings. Returns empty list
            if the method is not found.
        """
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return the ``__init__`` methods of a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A dictionary mapping constructor signatures to
            :class:`~cldk.models.python.PyCallable` objects.
            Typically contains at most one ``__init__`` method.
        """
        return {sig: c for sig, c in self.get_all_methods_in_class(qualified_class_name).items() if c.name == "__init__"}

    def get_all_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        """Return class attributes for a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A list of :class:`~cldk.models.python.PyClassAttribute` objects.
            Returns empty list if class not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    # ----------------------------------------------------------- callers/callees
    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        """Return all methods that call a specific target method.

        Queries the call graph to find all predecessor nodes (callers)
        of the specified method.

        Args:
            target_class_name: The fully qualified class name containing
                the target method.
            target_method_declaration: The method name or signature to
                find callers for.

        Returns:
            A dictionary with structure::

                {
                    "target_method": "method.signature",
                    "caller_details": [
                        {
                            "caller_signature": "caller.sig",
                            "edge": {...edge attributes...}
                        },
                        ...
                    ]
                }

            Returns ``{"caller_details": []}`` if the method is not found
            or has no callers.
        """
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
        """Return all methods called by a specific source method.

        Queries the call graph to find all successor nodes (callees)
        of the specified method.

        Args:
            source_class_name: The fully qualified class name containing
                the source method.
            source_method_declaration: The method name or signature to
                find callees for.

        Returns:
            A dictionary with structure::

                {
                    "source_method": "method.signature",
                    "callee_details": [
                        {
                            "callee_signature": "callee.sig",
                            "edge": {...edge attributes...}
                        },
                        ...
                    ]
                }

            Returns ``{"callee_details": []}`` if the method is not found
            or has no callees.
        """
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
        """Return call graph edges reachable from a class or method.

        Performs a depth-first traversal of the call graph starting from
        the specified class's methods (or a specific method).

        Args:
            qualified_class_name: The fully qualified class name to start from.
            method_signature: Optional specific method to start from. If
                ``None``, traversal starts from all methods in the class.

        Returns:
            A list of ``(caller, callee)`` tuples representing edges in
            the reachable subgraph. Returns empty list if class/method
            not found.
        """
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
        """Return comments contained within a specific method.

        Args:
            qualified_class_name: The fully qualified class name.
            method_signature: The method name or signature.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found within the method body. Returns empty list if method
            not found.
        """
        method = self.get_method(qualified_class_name, method_signature)
        return list(method.comments) if method else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[PyComment]:
        """Return comments contained within a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found within the class body. Returns empty list if class
            not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.comments) if cls else []

    def get_comment_in_file(self, file_path: str) -> List[PyComment]:
        """Return all comments in a specific file.

        Args:
            file_path: The path to the Python file.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found in the file. Returns empty list if file not found.
        """
        module = self.get_python_module(file_path)
        return list(module.comments) if module else []

    def get_all_comments(self) -> Dict[str, List[PyComment]]:
        """Return all comments in the project grouped by file.

        Returns:
            A dictionary mapping file paths to lists of
            :class:`~cldk.models.python.PyComment` objects.
        """
        return {fp: list(module.comments) for fp, module in self.application.symbol_table.items()}

    def get_all_docstrings(self) -> Dict[str, List[PyComment]]:
        """Return all docstrings in the project grouped by file.

        Filters comments to include only those marked as docstrings
        (comments at the beginning of modules, classes, or functions).

        Returns:
            A dictionary mapping file paths to lists of
            :class:`~cldk.models.python.PyComment` objects where
            ``is_docstring`` is ``True``.
        """
        return {
            fp: [c for c in module.comments if c.is_docstring]
            for fp, module in self.application.symbol_table.items()
        }
