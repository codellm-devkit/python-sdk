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

"""Python analysis facade module.

This module provides the :class:`PythonAnalysis` class, which serves as the primary
interface for performing static analysis on Python projects. It mirrors the API
surface of :class:`~cldk.analysis.java.JavaAnalysis` to provide a consistent
experience across languages.

The analysis is powered by the ``codeanalyzer-python`` backend, which uses a
combination of:
    - **Jedi**: For semantic code understanding, symbol resolution, and basic
      call graph construction.
    - **CodeQL** (optional): For enhanced call graph resolution and more
      accurate inter-procedural analysis.
    - **Tree-sitter**: For fast syntactic parsing and AST operations.

Key capabilities include:
    - Extracting symbol tables with classes, methods, and imports
    - Building call graphs (both intra- and inter-procedural)
    - Querying class hierarchies and inheritance relationships
    - Analyzing method signatures and parameters

Note:
    Unlike the Java analysis facade, Python analysis does not support single-file
    ``source_code`` mode. Analysis always requires a project directory containing
    valid Python source files.

See Also:
    - :class:`~cldk.analysis.java.JavaAnalysis`: Java-specific analysis facade.
    - :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`: Backend implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple

import networkx as nx
from tree_sitter import Tree

from cldk.analysis.commons.treesitter import TreesitterPython
from cldk.analysis.python.codeanalyzer import PyCodeanalyzer
from cldk.models.python import (
    PyApplication,
    PyCallable,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyModule,
)


class PythonAnalysis:
    """Analysis facade for Python projects.

    This class provides a comprehensive interface for performing static analysis
    on Python projects. It wraps the ``codeanalyzer-python`` backend and exposes
    methods for extracting code structure, call graphs, and symbol information.

    The facade provides access to:
        - **Symbol tables**: Classes, methods, functions, and their relationships
        - **Call graphs**: Method invocation relationships as NetworkX graphs
        - **Class hierarchies**: Inheritance and composition relationships
        - **Code structure**: Imports, parameters, fields, and nested elements

    The analysis is performed lazily on first access to analysis methods, with
    results cached by the backend. Use ``eager_analysis=True`` to force
    regeneration of all analysis artifacts.

    Attributes:
        project_dir (str | Path): The path to the project directory being analyzed.
        analysis_level (str): The depth of analysis being performed.
        analysis_json_path (str | Path | None): Path where analysis results are persisted.
        cache_dir (str | Path | None): Directory for backend caches.
        eager_analysis (bool): Whether to force regeneration of analysis.
        target_files (List[str] | None): Specific files to analyze, if constrained.
        treesitter_python (TreesitterPython): Tree-sitter parser for Python.
        backend (PyCodeanalyzer): The underlying analysis backend.

    See Also:
        - :class:`~cldk.analysis.java.JavaAnalysis`: Equivalent facade for Java.
        - :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`: Backend.
    """

    def __init__(
        self,
        project_dir: str | Path,
        cache_dir: str | Path | None,
        analysis_json_path: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        use_codeql: bool = True,
        use_ray: bool = False,
    ) -> None:
        """Initialize the Python analysis facade.

        Creates a new analysis facade for a Python project. This constructor
        sets up the Tree-sitter parser and initializes the codeanalyzer-python
        backend with the provided configuration.

        Args:
            project_dir: Absolute or relative path to the Python project directory
                to analyze. This directory should contain Python source files
                (``*.py``). Required; ``source_code`` mode is not supported.
            cache_dir: Directory path for the codeanalyzer-python backend's cache,
                including its virtualenv, CodeQL database, and analysis cache
                files. If ``None``, defaults to ``<project_dir>/.codeanalyzer``.
                The backend manages all caching internally.
            analysis_json_path: Path where the analysis results (``analysis.json``)
                should be persisted. If ``None``, the backend uses a default
                location within the cache directory.
            analysis_level: The depth of analysis to perform. Controls which
                analysis artifacts are generated. Common values include
                ``"symbol_table"`` and ``"call_graph"``. See
                :class:`~cldk.analysis.AnalysisLevel` for options.
            target_files: Optional list of specific file paths (relative to
                ``project_dir``) to include in the analysis. When provided,
                only these files are analyzed, which can significantly improve
                performance for large projects. If ``None``, all Python files
                in the project are analyzed.
            eager_analysis: If ``True``, forces regeneration of all analysis
                caches and databases, ignoring previously cached results.
                If ``False``, cached results are reused when available.
            use_codeql: If ``True`` (default), augments Jedi-based call graph
                resolution with CodeQL analysis for more complete and accurate
                call edges. Set to ``False`` for faster analysis using only
                Jedi, at the cost of potentially missing some call relationships.
            use_ray: If ``True``, enables Ray-based parallel processing for
                analysis. Recommended for very large projects where sequential
                Jedi/CodeQL analysis would be slow. Requires Ray to be installed.
                Defaults to ``False``.

        Raises:
            ValueError: If ``project_dir`` is ``None``. Python analysis requires
                a project directory; single-file source code mode is not supported.

        """
        if project_dir is None:
            raise ValueError(
                "project_dir is required; source_code mode is not supported for Python."
            )
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.analysis_json_path = analysis_json_path
        self.cache_dir = cache_dir
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.treesitter_python: TreesitterPython = TreesitterPython()
        self.backend: PyCodeanalyzer = PyCodeanalyzer(
            project_dir=project_dir,
            analysis_level=analysis_level,
            analysis_json_path=analysis_json_path,
            eager_analysis=eager_analysis,
            cache_dir=cache_dir,
            target_files=target_files,
            use_codeql=use_codeql,
            use_ray=use_ray,
        )

    # -----[ treesitter passthrough ]-----
    def is_parsable(self, source_code: str) -> bool:
        """Check if the given source code is valid Python syntax.

        Uses the Tree-sitter Python parser to attempt parsing the source code.
        This is useful for validating code snippets before further processing
        or for filtering out malformed code.

        Args:
            source_code: A string containing Python source code to validate.
                Can be a complete module, a function definition, or any
                valid Python code fragment.

        Returns:
            ``True`` if the source code parses without syntax errors,
            ``False`` otherwise. Note that this only checks syntactic validity,
            not semantic correctness (e.g., undefined variables won't be caught).

        See Also:
            :meth:`get_raw_ast`: To obtain the full AST for valid code.
        """
        return self.treesitter_python.is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> Tree:
        """Parse source code and return the Tree-sitter AST.

        Parses the provided Python source code using Tree-sitter and returns
        the resulting abstract syntax tree. The AST can be traversed to
        extract syntactic information about the code structure.

        Args:
            source_code: A string containing Python source code to parse.
                Should be syntactically valid Python code.

        Returns:
            A Tree-sitter ``Tree`` object representing the parsed AST. The tree
            contains nodes representing all syntactic elements of the code,
            including functions, classes, statements, and expressions.

        Note:
            If the source code contains syntax errors, Tree-sitter will still
            return a tree but with ERROR nodes at the locations of parse errors.
            Use :meth:`is_parsable` to check for valid syntax first.

        See Also:
            :meth:`is_parsable`: To validate syntax before parsing.
        """
        return self.treesitter_python.get_raw_ast(source_code)

    # -----[ application view ]-----
    def get_application_view(self) -> PyApplication:
        """Return the complete analyzed application model.

        Returns the top-level :class:`PyApplication` object that represents
        the entire analyzed Python project. This object contains all modules,
        classes, functions, and their relationships discovered during analysis.

        Returns:
            A :class:`~cldk.models.python.PyApplication` object containing:
                - All analyzed modules (``modules`` attribute)
                - Project metadata and configuration
                - Aggregated statistics about the codebase

        See Also:
            :meth:`get_symbol_table`: For file-keyed access to modules.
            :meth:`get_modules`: For a flat list of all modules.
        """
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, PyModule]:
        """Return the symbol table mapping file paths to module objects.

        Returns a dictionary that maps each analyzed file's path to its
        corresponding :class:`PyModule` object. This is useful for looking
        up module information when you know the file path.

        Returns:
            A dictionary where keys are file paths (as strings) and values are
            :class:`~cldk.models.python.PyModule` objects containing the
            analyzed structure of each file, including classes, functions,
            imports, and other symbols.

        See Also:
            :meth:`get_python_module`: For direct lookup by file path.
            :meth:`get_modules`: For a flat list without file paths.
        """
        return self.backend.get_symbol_table()

    def get_modules(self) -> List[PyModule]:
        """Return a list of all analyzed modules.

        Returns all :class:`PyModule` objects discovered during analysis as
        a flat list. Each module represents a single Python file and contains
        information about its classes, functions, imports, and other symbols.

        Returns:
            A list of :class:`~cldk.models.python.PyModule` objects, one for
            each Python file analyzed in the project.

        See Also:
            :meth:`get_symbol_table`: For file-path-keyed access.
            :meth:`get_application_view`: For the full application model.
        """
        return self.backend.get_modules()

    def get_python_file(self, qualified_class_name: str) -> str | None:
        """Return the file path containing a class with the given signature.

        Given a qualified class name (typically including the module path),
        returns the file path where that class is defined. This is useful
        for navigating from class references back to source files.

        Args:
            qualified_class_name: The fully qualified name of the class to
                locate. This typically includes the module path and class name
                (e.g., ``"mypackage.module.MyClass"``).

        Returns:
            The file path (as a string) containing the class definition, or
            ``None`` if no class with the given name is found in the analyzed
            project.

        See Also:
            :meth:`get_class`: To get the full class object by name.
            :meth:`get_python_module`: To get the module for a file path.
        """
        return self.backend.get_python_file(qualified_class_name)

    def get_python_module(self, file_path: str) -> PyModule | None:
        """Return the module object for a given file path.

        Retrieves the :class:`PyModule` object corresponding to a specific
        Python source file in the analyzed project.

        Args:
            file_path: The path to the Python file, relative to the project
                root or as an absolute path.

        Returns:
            The :class:`~cldk.models.python.PyModule` object for the file,
            containing all analyzed information about classes, functions,
            imports, and other symbols. Returns ``None`` if the file is
            not part of the analyzed project.

        See Also:
            :meth:`get_symbol_table`: For bulk access to all modules.
            :meth:`get_python_file`: For reverse lookup (class to file).
        """
        return self.backend.get_python_module(file_path)

    # -----[ imports ]-----
    def get_imports(self) -> Dict[str, List]:
        """Return all import statements for each module in the project.

        Collects and returns import statements from all analyzed modules,
        organized by file path. This is useful for dependency analysis,
        understanding module relationships, and identifying external
        dependencies.

        Returns:
            A dictionary mapping file paths (strings) to lists of import
            objects. Each import object contains information about the
            imported module or symbol, including whether it's an absolute
            or relative import.

        See Also:
            :meth:`get_python_module`: For detailed module information.
        """
        return {
            fp: list(m.imports) for fp, m in self.backend.get_symbol_table().items()
        }

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Return the project call graph as a NetworkX directed graph.

        Constructs and returns a directed graph representing method/function
        call relationships across the entire project. Each node represents
        a callable (function or method), and each edge represents a call
        from one callable to another.

        The call graph is built using:
            - Jedi for semantic call resolution
            - CodeQL (if enabled) for enhanced inter-procedural analysis

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes represent callables (functions/methods) with attributes
                  containing callable metadata
                - Edges represent call relationships, directed from caller to callee
                - Edge attributes may include call site information

        Note:
            The completeness of the call graph depends on the analysis
            configuration. With ``use_codeql=True``, more call relationships
            are typically discovered at the cost of longer analysis time.

        See Also:
            :meth:`get_callers`: For finding callers of a specific method.
            :meth:`get_callees`: For finding callees of a specific method.
            :meth:`get_class_call_graph`: For call graph subset by class.
        """
        return self.backend.get_call_graph()

    def get_call_graph_json(self) -> str:
        """Return the complete analysis results serialized as JSON.

        Serializes the full analysis results, including the call graph and
        symbol table, to a JSON string. This is useful for persisting
        analysis results, sharing with other tools, or debugging.

        Returns:
            A JSON-formatted string containing the complete analysis data,
            including modules, classes, methods, and call relationships.

        See Also:
            :meth:`get_call_graph`: For the graph object directly.
        """
        return self.backend.get_call_graph_json()

    def get_callers(
        self, target_class_name: str, target_method_declaration: str
    ) -> Dict:
        """Return all methods that call the specified target method.

        Finds and returns information about all callables (functions and
        methods) that invoke the specified target method. This is useful
        for impact analysis and understanding how a method is used.

        Args:
            target_class_name: The fully qualified name of the class
                containing the target method. Use an empty string or
                module name for module-level functions.
            target_method_declaration: The method/function name or signature
                to find callers for.

        Returns:
            A dictionary containing information about all callers, including:
                - Caller method signatures
                - Call site locations (file and line)
                - Caller class information (if applicable)

        See Also:
            :meth:`get_callees`: For the reverse direction (what a method calls).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """
        return self.backend.get_all_callers(
            target_class_name, target_method_declaration
        )

    def get_callees(
        self, source_class_name: str, source_method_declaration: str
    ) -> Dict:
        """Return all methods called by the specified source method.

        Finds and returns information about all callables (functions and
        methods) that are invoked by the specified source method. This is
        useful for understanding method dependencies and tracing execution
        paths.

        Args:
            source_class_name: The fully qualified name of the class
                containing the source method. Use an empty string or
                module name for module-level functions.
            source_method_declaration: The method/function name or signature
                to find callees for.

        Returns:
            A dictionary containing information about all callees, including:
                - Callee method signatures
                - Target class information (if applicable)
                - Call site locations within the source method

        See Also:
            :meth:`get_callers`: For the reverse direction (who calls a method).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """
        return self.backend.get_all_callees(
            source_class_name, source_method_declaration
        )

    def get_class_call_graph(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[str, str]]:
        """Return call graph edges reachable from a class or method.

        Extracts a subset of the call graph containing only edges reachable
        from the specified class (and optionally a specific method within
        that class). This is useful for understanding the call structure
        of a specific component without the noise of the full project graph.

        Args:
            qualified_class_name: The fully qualified name of the class
                to start traversal from (e.g., ``"mypackage.models.User"``).
            method_signature: Optional method name or signature to further
                constrain the starting point. If provided, only edges
                reachable from that specific method are included.
                If ``None``, edges from all methods in the class are included.

        Returns:
            A list of tuples, where each tuple ``(caller, callee)`` represents
            a directed edge in the call graph. The caller and callee are
            string representations of the callable signatures.

        See Also:
            :meth:`get_call_graph`: For the complete project call graph.
            :meth:`get_callees`: For direct callees of a single method.
        """
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    # -----[ methods ]-----
    def get_methods(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return all methods in the project grouped by class.

        Retrieves all methods (including static methods and class methods)
        from all classes in the analyzed project, organized in a nested
        dictionary structure by class name and then method name.

        Returns:
            A nested dictionary with structure::

                {
                    "qualified.class.Name": {
                        "method_name": PyCallable,
                        "another_method": PyCallable,
                        ...
                    },
                    ...
                }

            Each :class:`~cldk.models.python.PyCallable` contains the method's
            signature, parameters, return type, body, and other metadata.

        See Also:
            :meth:`get_methods_in_class`: For methods of a specific class.
            :meth:`get_method`: For a single method by name.
        """
        return self.backend.get_all_methods_in_application()

    def get_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return all methods defined in a specific class.

        Retrieves all methods belonging to the specified class, including
        instance methods, class methods, static methods, and special
        methods (like ``__init__``, ``__str__``, etc.).

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"mypackage.models.User"``).

        Returns:
            A dictionary mapping method names (strings) to
            :class:`~cldk.models.python.PyCallable` objects. Returns an
            empty dictionary if the class is not found or has no methods.

        See Also:
            :meth:`get_method`: For a single method by name.
            :meth:`get_constructors`: For ``__init__`` methods specifically.
        """
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_method(
        self, qualified_class_name: str, qualified_method_name: str
    ) -> PyCallable | None:
        """Return a specific method by class and method name.

        Retrieves detailed information about a single method, including
        its signature, parameters, return type, decorators, and body.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method (e.g., ``"mypackage.models.User"``).
            qualified_method_name: The name of the method to retrieve
                (e.g., ``"save"`` or ``"__init__"``).

        Returns:
            A :class:`~cldk.models.python.PyCallable` object containing
            all analyzed information about the method, or ``None`` if
            the method is not found.

        See Also:
            :meth:`get_methods_in_class`: For all methods of a class.
            :meth:`get_method_parameters`: For just the parameter names.
        """
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(
        self, qualified_class_name: str, qualified_method_name: str
    ) -> List[str]:
        """Return the parameter names for a specific method.

        Retrieves the list of parameter names (excluding ``self`` for
        instance methods) defined in the method signature.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method.
            qualified_method_name: The name of the method to get parameters for.

        Returns:
            A list of parameter names as strings, in the order they appear
            in the method signature. Returns an empty list if the method
            is not found or has no parameters.

        Note:
            This returns only parameter names, not types or default values.
            Use :meth:`get_method` for full parameter information.

        See Also:
            :meth:`get_method`: For complete method information.
        """
        return self.backend.get_method_parameters(
            qualified_class_name, qualified_method_name
        )

    def get_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return the constructor(s) of a specific class.

        Retrieves the ``__init__`` method(s) defined in the specified class.
        In Python, a class typically has at most one ``__init__`` method,
        but this returns a dictionary for API consistency.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"mypackage.models.User"``).

        Returns:
            A dictionary mapping constructor names (typically ``"__init__"``)
            to :class:`~cldk.models.python.PyCallable` objects. Returns an
            empty dictionary if the class has no explicit constructor.

        See Also:
            :meth:`get_method`: For any method by name.
            :meth:`get_methods_in_class`: For all methods including constructors.
        """
        return self.backend.get_all_constructors(qualified_class_name)

    # -----[ classes ]-----
    def get_classes(self) -> Dict[str, PyClass]:
        """Return all classes in the project.

        Retrieves all class definitions discovered during analysis, organized
        by their fully qualified names. This includes regular classes,
        dataclasses, abstract base classes, and nested classes.

        Returns:
            A dictionary mapping fully qualified class names (strings) to
            :class:`~cldk.models.python.PyClass` objects containing class
            metadata, methods, attributes, and inheritance information.

        See Also:
            :meth:`get_class`: For a single class by name.
            :meth:`get_classes_by_criteria`: For filtered class retrieval.
        """
        return self.backend.get_all_classes()

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        """Return a specific class by its qualified name.

        Retrieves detailed information about a single class, including
        its methods, attributes, base classes, and decorators.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"mypackage.models.User"``).

        Returns:
            A :class:`~cldk.models.python.PyClass` object containing all
            analyzed information about the class, or ``None`` if the class
            is not found in the analyzed project.

        See Also:
            :meth:`get_classes`: For all classes in the project.
            :meth:`get_python_file`: To find which file contains a class.
        """
        return self.backend.get_class(qualified_class_name)

    def get_classes_by_criteria(
        self, inclusions: List[str] | None = None, exclusions: List[str] | None = None
    ) -> Dict[str, PyClass]:
        """Return classes matching inclusion/exclusion filter criteria.

        Filters the project's classes based on substring matching against
        their qualified names. Classes are included if their name contains
        any inclusion substring AND does not contain any exclusion substring.

        Args:
            inclusions: List of substrings that class names must contain to
                be included. If ``None`` or empty, no inclusion filtering is
                applied (effectively includes nothing unless you have at least
                one inclusion pattern).
            exclusions: List of substrings that class names must NOT contain.
                Classes matching any exclusion pattern are filtered out,
                even if they match an inclusion pattern.

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.python.PyClass` objects for classes
            matching the criteria.

        Note:
            The filtering uses substring matching (``in`` operator), not
            regular expressions or glob patterns.

        See Also:
            :meth:`get_classes`: For all classes without filtering.
        """
        inclusions = inclusions or []
        exclusions = exclusions or []
        result: Dict[str, PyClass] = {}
        for sig, cls in self.backend.get_all_classes().items():
            selected = any(inc in sig for inc in inclusions)
            if any(exc in sig for exc in exclusions):
                selected = False
            if selected:
                result[sig] = cls
        return result

    def get_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        """Return class-level attributes (fields) for a specific class.

        Retrieves all class attributes defined in the specified class,
        including instance attributes, class attributes, and properties.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"mypackage.models.User"``).

        Returns:
            A list of :class:`~cldk.models.python.PyClassAttribute` objects,
            each containing information about an attribute's name, type
            annotation (if present), and default value.

        See Also:
            :meth:`get_class`: For complete class information.
        """
        return self.backend.get_all_fields(qualified_class_name)

    def get_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """Return inner/nested classes defined within a class.

        Retrieves all classes that are defined inside the specified class
        (nested class definitions).

        Args:
            qualified_class_name: The fully qualified name of the outer class
                (e.g., ``"mypackage.models.Container"``).

        Returns:
            A list of :class:`~cldk.models.python.PyClass` objects for each
            nested class. Returns an empty list if no nested classes exist.

        See Also:
            :meth:`get_class`: For the outer class information.
        """
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Return all classes that inherit from the specified class.

        Finds all classes in the project that directly or indirectly extend
        the specified base class. This is useful for understanding class
        hierarchies and finding implementations of abstract base classes.

        Args:
            qualified_class_name: The fully qualified name of the base class
                to find subclasses of (e.g., ``"mypackage.base.BaseModel"``).

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.python.PyClass` objects for all classes
            that inherit from the specified class.

        See Also:
            :meth:`get_extended_classes`: For the reverse (what a class extends).
            :meth:`get_class_hierarchy`: For the full inheritance graph (not implemented).
        """
        return self.backend.get_all_sub_classes(qualified_class_name)

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """Return the base class names that a class extends.

        Retrieves the list of parent/base classes for the specified class.
        This includes direct base classes from the class definition.

        Args:
            qualified_class_name: The fully qualified name of the class
                to get base classes for (e.g., ``"mypackage.models.User"``).

        Returns:
            A list of base class names (as strings). These may be qualified
            or unqualified names depending on how they appear in the source.

        Note:
            Python does not distinguish between classes and interfaces,
            so all base types are returned here. Use this method instead
            of :meth:`get_implemented_interfaces`.

        See Also:
            :meth:`get_sub_classes`: For finding classes that extend this class.
        """
        return self.backend.get_extended_classes(qualified_class_name)

    # -----[ unsupported ]-----
    def get_class_hierarchy(self) -> nx.DiGraph:
        """Return the complete class inheritance hierarchy as a graph.

        This method is intended to return a NetworkX directed graph representing
        the full class inheritance relationships in the project.

        Returns:
            Would return a ``networkx.DiGraph`` with classes as nodes and
            inheritance edges from subclass to superclass.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_sub_classes`: For finding subclasses of a specific class.
            :meth:`get_extended_classes`: For finding base classes of a class.
        """
        raise NotImplementedError("Class hierarchy is not implemented yet.")

    def get_service_entry_point_classes(self, **kwargs) -> Dict[str, PyClass]:
        """Return classes that serve as service entry points.

        This method is intended to identify classes that act as entry points
        for services, such as Flask views, Django views, FastAPI endpoints,
        or other framework-specific entry points.

        Args:
            **kwargs: Framework-specific filtering options.

        Returns:
            Would return a dictionary of class names to :class:`PyClass` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_entry_point_classes`: Related entry point detection.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_service_entry_point_methods(self, **kwargs) -> Dict[str, Dict[str, PyCallable]]:
        """Return methods that serve as service entry points.

        This method is intended to identify methods decorated with framework-
        specific decorators like ``@app.route``, ``@api_view``, etc.

        Args:
            **kwargs: Framework-specific filtering options.

        Returns:
            Would return a nested dictionary of class names to method names
            to :class:`PyCallable` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_methods_with_decorators`: For finding decorated methods.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_entry_point_classes(self) -> Dict[str, PyClass]:
        """Return classes identified as application entry points.

        This method is intended to identify main application classes,
        CLI entry points, and other classes that serve as program starting
        points.

        Returns:
            Would return a dictionary of class names to :class:`PyClass` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_entry_point_methods(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return methods identified as application entry points.

        This method is intended to identify main functions, CLI commands,
        and other methods that serve as program starting points.

        Returns:
            Would return a nested dictionary of class names to method names
            to :class:`PyCallable` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """Return interfaces implemented by a class.

        This method exists for API parity with Java analysis. In Python,
        there is no syntactic distinction between classes and interfaces;
        abstract base classes (ABCs) and protocols serve similar purposes
        but are syntactically identical to regular classes.

        Args:
            qualified_class_name: The class to query.

        Raises:
            NotImplementedError: Always raised. Use :meth:`get_extended_classes`
                instead to get all base classes, which may include ABCs or
                Protocol classes.

        See Also:
            :meth:`get_extended_classes`: The correct method for Python
                to get parent classes including abstract base classes.
        """
        raise NotImplementedError(
            "Python does not distinguish interfaces from base classes; use get_extended_classes."
        )

    def get_methods_with_decorators(
        self, decorators: List[str]
    ) -> Dict[str, List[Dict]]:
        """Return methods decorated with specific decorators.

        This method is intended to find all methods that have any of the
        specified decorators applied, such as ``@property``, ``@staticmethod``,
        ``@classmethod``, or custom decorators.

        Args:
            decorators: List of decorator names to search for (e.g.,
                ``["property", "staticmethod", "app.route"]``).

        Returns:
            Would return a dictionary mapping decorator names to lists of
            method information dictionaries.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_methods`: To manually filter methods by decorators.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_test_methods(self) -> Dict[str, str]:
        """Return methods identified as test methods.

        This method is intended to find all test methods in the project,
        typically methods starting with ``test_`` or decorated with
        ``@pytest.mark`` or similar test framework decorators.

        Returns:
            Would return a dictionary mapping test method identifiers to
            their source code or signatures.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_methods_with_decorators`: Alternative approach to
                find pytest-decorated methods.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """Return line numbers where a method is called.

        This method is intended to find all line numbers in the project
        where the specified method is invoked.

        Args:
            target_method_name: The name of the method to find calls to.

        Returns:
            Would return a list of line numbers (integers).

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_callers`: For finding caller methods instead of lines.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """Return call targets using simple name resolution.

        This method is intended to find all methods that could be called
        based on simple name matching, without full semantic analysis.

        Args:
            declared_methods: Dictionary of declared method names and signatures.

        Returns:
            Would return a set of method names that are call targets.

        Raises:
            NotImplementedError: This functionality is not yet implemented
                for Python analysis.

        See Also:
            :meth:`get_call_graph`: For full semantic call resolution.
        """
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_all_crud_operations(self) -> Dict:
        """Return all CRUD (Create, Read, Update, Delete) operations.

        This method is intended for web application analysis to identify
        database operations and REST API endpoints.

        Returns:
            Would return a dictionary of CRUD operations categorized by type.

        Raises:
            NotImplementedError: CRUD analysis is not supported for Python.
                This feature is primarily designed for Java enterprise
                applications with JPA/Hibernate.

        See Also:
            :meth:`get_all_create_operations`: For create operations only.
            :meth:`get_all_read_operations`: For read operations only.
        """
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_create_operations(self) -> Dict:
        """Return all Create operations from CRUD analysis.

        Returns:
            Would return a dictionary of create/insert operations.

        Raises:
            NotImplementedError: CRUD analysis is not supported for Python.
        """
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_read_operations(self) -> Dict:
        """Return all Read operations from CRUD analysis.

        Returns:
            Would return a dictionary of read/select operations.

        Raises:
            NotImplementedError: CRUD analysis is not supported for Python.
        """
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_update_operations(self) -> Dict:
        """Return all Update operations from CRUD analysis.

        Returns:
            Would return a dictionary of update operations.

        Raises:
            NotImplementedError: CRUD analysis is not supported for Python.
        """
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_delete_operations(self) -> Dict:
        """Return all Delete operations from CRUD analysis.

        Returns:
            Would return a dictionary of delete operations.

        Raises:
            NotImplementedError: CRUD analysis is not supported for Python.
        """
        raise NotImplementedError("CRUD analysis is not supported for Python.")
