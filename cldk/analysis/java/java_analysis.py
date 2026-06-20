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

"""Java analysis facade module.

This module provides the :class:`JavaAnalysis` class, which serves as the
primary high-level interface for performing static analysis on Java projects
and source files. It combines Tree-sitter-based parsing with the CodeAnalyzer
backend to provide comprehensive code analysis capabilities.

The analysis supports two modes of operation:
    - **Project mode**: Analyze an entire Java project directory, providing
      access to cross-file analysis features like call graphs and class
      hierarchies.
    - **Source code mode**: Analyze a single Java source code string, useful
      for quick syntactic analysis without a full project structure.

Key capabilities include:
    - Symbol table extraction (classes, methods, fields, imports)
    - Call graph construction and traversal
    - Class hierarchy and inheritance analysis
    - Method parameter and signature analysis
    - Comment and Javadoc extraction
    - CRUD operation detection for enterprise applications
    - Entry point identification (main methods, REST endpoints)

The analysis is powered by:
    - **Tree-sitter**: Fast incremental parsing for syntactic analysis
    - **CodeAnalyzer**: Semantic analysis backend (JAR-based)

See Also:
    - :class:`~cldk.analysis.python.PythonAnalysis`: Python equivalent.
    - :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Backend implementation.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Set, Union
import networkx as nx

from tree_sitter import Tree

from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.models.java import JCallable
from cldk.models.java import JApplication
from cldk.models.java.models import JCRUDOperation, JComment, JCompilationUnit, JMethodDetail, JType, JField
from cldk.analysis.java.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.backend import JavaAnalysisBackend


class JavaAnalysis:
    """Analysis facade for Java code.

    This class provides a comprehensive interface for performing static analysis
    on Java projects and source files. It combines Tree-sitter-based parsing for
    syntactic analysis with the CodeAnalyzer backend for semantic analysis.

    The facade supports two modes of operation:
        - **Project mode**: When initialized with ``project_dir``, provides full
          analysis capabilities including cross-file call graphs, class hierarchies,
          and symbol tables.
        - **Source code mode**: When initialized with ``source_code``, provides
          syntactic analysis capabilities like parsing and AST extraction.

    Key features:
        - Symbol table access with classes, methods, and fields
        - Call graph construction and traversal
        - Caller/callee relationship analysis
        - Class hierarchy and inheritance queries
        - Comment and Javadoc extraction
        - CRUD operation detection
        - Entry point identification

    Attributes:
        project_dir (str | Path | None): Path to the Java project directory.
        source_code (str | None): Java source code string for single-file mode.
        analysis_level (str): The depth of analysis performed.
        analysis_json_path (str | Path | None): Path for persisting analysis results.
        analysis_backend_path (str | None): Path to the CodeAnalyzer JAR.
        eager_analysis (bool): Whether to force regeneration of analysis.
        target_files (List[str] | None): Specific files to analyze.
        treesitter_java (TreesitterJava): Tree-sitter parser for Java.
        backend (JCodeanalyzer): The underlying analysis backend.

    See Also:
        - :class:`~cldk.analysis.python.PythonAnalysis`: Python equivalent.
        - :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Backend.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        source_code: str | None,
        analysis_backend_path: str | None,
        analysis_json_path: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
    ) -> None:
        """Initialize the Java analysis facade.

        Creates a new analysis facade for Java code. Either ``project_dir`` or
        ``source_code`` must be provided, but not both.

        Args:
            project_dir: Absolute or relative path to the Java project directory.
                The directory should contain Java source files (``.java``).
                When provided, enables full analysis including call graphs.
                Mutually exclusive with ``source_code``.
            source_code: Java source code string for single-file analysis.
                Useful for quick syntactic analysis without a project structure.
                Mutually exclusive with ``project_dir``.
            analysis_backend_path: Path to the directory containing the
                ``codeanalyzer-*.jar`` backend. If not provided, the JAR is
                automatically downloaded from the latest release. Only used
                in project mode.
            analysis_json_path: Path where the analysis database
                (``analysis.json``) should be persisted. If ``None``, analysis
                results are computed on-demand and not cached to disk.
            analysis_level: The depth of analysis to perform. Common values:
                - ``"symbol_table"``: Extract symbols only (faster)
                - ``"call_graph"``: Full call graph analysis (comprehensive)
                See :class:`~cldk.analysis.AnalysisLevel` for all options.
            target_files: Optional list of specific file paths (relative to
                ``project_dir``) to include in the analysis. When provided,
                only these files are analyzed, improving performance for
                large projects. Primarily supported for symbol-table level.
            eager_analysis: If ``True``, forces regeneration of the analysis
                database on each run, ignoring any existing cached results.
                If ``False``, cached results are reused when available.

        Raises:
            NotImplementedError: If the requested analysis configuration is
                not supported by the backend.
        """

        self.project_dir = project_dir
        self.source_code = source_code
        self.analysis_level = analysis_level
        self.analysis_json_path = analysis_json_path
        self.analysis_backend_path = analysis_backend_path
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.treesitter_java: TreesitterJava = TreesitterJava()
        # Initialize the analysis analysis_backend
        self.backend: JavaAnalysisBackend = JCodeanalyzer(
            project_dir=self.project_dir,
            source_code=self.source_code,
            eager_analysis=self.eager_analysis,
            analysis_level=self.analysis_level,
            analysis_json_path=self.analysis_json_path,
            analysis_backend_path=self.analysis_backend_path,
            target_files=self.target_files,
        )

    def get_imports(self) -> List[str]:
        """Return all import statements in the source code.

        This method is intended to extract all import declarations from the
        analyzed Java source code, including both single-type imports and
        wildcard imports.

        Returns:
            A list of import statement strings, each representing a fully
            qualified import (e.g., ``"java.util.List"``, ``"java.io.*"``).

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_symbol_table`: For accessing compilation units which
                contain import information.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_variables(self, **kwargs) -> Dict:
        """Return all variables discovered in the source code.

        This method is intended to extract variable declarations from the
        analyzed code, including local variables, fields, and parameters.

        Args:
            **kwargs: Implementation-specific filtering options.

        Returns:
            An implementation-defined view of variables discovered in the code.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_fields`: For class-level field access (implemented).
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_service_entry_point_classes(self, **kwargs) -> Dict[str, JType]:
        """Return all service entry-point classes.

        This method is intended to identify classes that serve as entry points
        for services, such as JAX-RS resources, Spring controllers, or servlet
        classes.

        Args:
            **kwargs: Framework-specific filtering options (e.g., annotation
                filters for ``@RestController``, ``@Path``, etc.).

        Returns:
            A dictionary mapping qualified class names to :class:`JType` objects
            for classes identified as service entry points.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_entry_point_classes`: For general entry point detection.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_service_entry_point_methods(self, **kwargs) -> Dict[str, Dict[str, JCallable]]:
        """Return all service entry-point methods.

        This method is intended to identify methods that serve as entry points
        for services, such as REST endpoint handlers, servlet methods, or
        message handlers.

        Args:
            **kwargs: Framework-specific filtering options (e.g., HTTP method
                filters, annotation filters for ``@GET``, ``@POST``, etc.).

        Returns:
            A nested dictionary mapping class names to method signatures to
            :class:`JCallable` objects for methods identified as service
            entry points.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_entry_point_methods`: For general entry point detection.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_application_view(self) -> JApplication:
        """Return the complete analyzed application model.

        Returns the top-level :class:`JApplication` object that represents
        the entire analyzed Java project. This object contains all compilation
        units, classes, methods, and their relationships discovered during
        analysis.

        Returns:
            A :class:`~cldk.models.java.JApplication` object containing:
                - All compilation units (``symbol_table`` attribute)
                - Project-level metadata
                - Aggregated statistics about the codebase

        Raises:
            NotImplementedError: If called in single-file mode (``source_code``
                was provided instead of ``project_dir``).

        See Also:
            :meth:`get_symbol_table`: For direct access to the symbol table.
            :meth:`get_compilation_units`: For a list of compilation units.
        """
        if self.source_code:
            raise NotImplementedError("Support for this functionality has not been implemented yet.")
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        """Return the symbol table mapping file paths to compilation units.

        Returns a dictionary that maps each analyzed Java file's path to its
        corresponding :class:`JCompilationUnit` object. This is the primary
        data structure for accessing analyzed code structure.

        Returns:
            A dictionary where keys are file paths (as strings) and values are
            :class:`~cldk.models.java.JCompilationUnit` objects containing:
                - Package declaration
                - Import statements
                - Type declarations (classes, interfaces, enums)
                - Method and field definitions

        See Also:
            :meth:`get_compilation_units`: For a list without file paths.
            :meth:`get_java_compilation_unit`: For direct lookup by path.
        """
        return self.backend.get_symbol_table()

    def get_compilation_units(self) -> List[JCompilationUnit]:
        """Return all compilation units in the project as a list.

        Returns all :class:`JCompilationUnit` objects discovered during
        analysis as a flat list. Each compilation unit represents a single
        Java source file.

        Returns:
            A list of :class:`~cldk.models.java.JCompilationUnit` objects,
            one for each Java source file analyzed in the project.

        See Also:
            :meth:`get_symbol_table`: For file-path-keyed access.
        """
        return self.backend.get_compilation_units()

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Return the complete class inheritance hierarchy as a graph.

        This method is intended to return a NetworkX directed graph representing
        the full class inheritance relationships in the project, including
        extends and implements relationships.

        Returns:
            Would return a ``networkx.DiGraph`` with classes as nodes and
            edges representing inheritance (subclass -> superclass).

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_sub_classes`: For finding subclasses of a specific class.
            :meth:`get_extended_classes`: For finding superclasses.
            :meth:`get_implemented_interfaces`: For interface implementations.
        """

        raise NotImplementedError("Class hierarchy is not implemented yet.")

    def is_parsable(self, source_code: str) -> bool:
        """Check if the given source code is valid Java syntax.

        Uses the Tree-sitter Java parser to attempt parsing the source code.
        This is useful for validating code snippets before further processing
        or for filtering out malformed code.

        Args:
            source_code: A string containing Java source code to validate.
                Can be a complete compilation unit, a class definition, or
                any syntactically valid Java code fragment.

        Returns:
            ``True`` if the source code parses without syntax errors,
            ``False`` otherwise. Note that this only checks syntactic validity,
            not semantic correctness (e.g., type errors won't be caught).

        See Also:
            :meth:`get_raw_ast`: To obtain the full AST for valid code.
        """
        return self.treesitter_java.is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> Tree:
        """Parse source code and return the Tree-sitter AST.

        Parses the provided Java source code using Tree-sitter and returns
        the resulting abstract syntax tree. The AST can be traversed to
        extract syntactic information about the code structure.

        Args:
            source_code: A string containing Java source code to parse.
                Should be syntactically valid Java code.

        Returns:
            A Tree-sitter ``Tree`` object representing the parsed AST. The tree
            contains nodes representing all syntactic elements of the code,
            including classes, methods, statements, and expressions.

        Note:
            If the source code contains syntax errors, Tree-sitter will still
            return a tree but with ERROR nodes at the locations of parse errors.
            Use :meth:`is_parsable` to check for valid syntax first.

        See Also:
            :meth:`is_parsable`: To validate syntax before parsing.
        """
        return self.treesitter_java.get_raw_ast(source_code)

    def get_call_graph(self) -> nx.DiGraph:
        """Return the project call graph as a NetworkX directed graph.

        Constructs and returns a directed graph representing method call
        relationships across the entire project. Each node represents a
        method, and each edge represents a call from one method to another.

        The call graph requires ``analysis_level`` to be set to ``"call_graph"``
        during initialization for accurate results.

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes represent methods with attributes containing method
                  metadata (class name, signature, etc.)
                - Edges represent call relationships, directed from caller
                  to callee
                - Edge attributes may include call site information

        See Also:
            :meth:`get_callers`: For finding callers of a specific method.
            :meth:`get_callees`: For finding callees of a specific method.
            :meth:`get_class_call_graph`: For class-scoped call graphs.
        """
        return self.backend.get_call_graph()

    def get_call_graph_json(self) -> str:
        """Return the complete analysis results serialized as JSON.

        Serializes the full analysis results, including the call graph and
        symbol table, to a JSON string. This is useful for persisting
        analysis results, sharing with other tools, or debugging.

        Returns:
            A JSON-formatted string containing the complete analysis data,
            including compilation units, classes, methods, and call
            relationships.

        Raises:
            NotImplementedError: If called in single-file mode (``source_code``
                was provided instead of ``project_dir``).

        See Also:
            :meth:`get_call_graph`: For the graph object directly.
        """
        if self.source_code:
            raise NotImplementedError("Producing a call graph over a single file is not implemented yet.")
        return self.backend.get_call_graph_json()

    def get_callers(self, target_class_name: str, target_method_declaration: str, using_symbol_table: bool = False) -> Dict:
        """Return all methods that call the specified target method.

        Finds and returns information about all methods that invoke the
        specified target method. This is useful for impact analysis and
        understanding how a method is used throughout the codebase.

        Args:
            target_class_name: The fully qualified name of the class containing
                the target method (e.g., ``"com.example.service.UserService"``).
            target_method_declaration: The method signature to find callers for
                (e.g., ``"getUser(String)"`` or ``"process())"``).
            using_symbol_table: If ``True``, uses the symbol table for
                resolution (faster but may be less accurate). If ``False``
                (default), uses the full call graph analysis.

        Returns:
            A dictionary containing information about all callers, including:
                - Caller method signatures
                - Call site locations (file and line)
                - Caller class information

        Raises:
            NotImplementedError: If called in single-file mode (``source_code``
                was provided instead of ``project_dir``).

        See Also:
            :meth:`get_callees`: For the reverse direction (what a method calls).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """

        if self.source_code:
            raise NotImplementedError("Generating all callers over a single file is not implemented yet.")
        return self.backend.get_all_callers(target_class_name, target_method_declaration, using_symbol_table)

    def get_callees(self, source_class_name: str, source_method_declaration: str, using_symbol_table: bool = False) -> Dict:
        """Return all methods called by the specified source method.

        Finds and returns information about all methods that are invoked by
        the specified source method. This is useful for understanding method
        dependencies and tracing execution paths.

        Args:
            source_class_name: The fully qualified name of the class containing
                the source method (e.g., ``"com.example.service.OrderService"``).
            source_method_declaration: The method signature to find callees for
                (e.g., ``"processOrder(Order)"``).
            using_symbol_table: If ``True``, uses the symbol table for
                resolution (faster but may be less accurate). If ``False``
                (default), uses the full call graph analysis.

        Returns:
            A dictionary containing information about all callees, including:
                - Callee method signatures
                - Target class information
                - Call site locations within the source method

        Raises:
            NotImplementedError: If called in single-file mode (``source_code``
                was provided instead of ``project_dir``).

        See Also:
            :meth:`get_callers`: For the reverse direction (who calls a method).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """
        if self.source_code:
            raise NotImplementedError("Generating all callees over a single file is not implemented yet.")
        return self.backend.get_all_callees(source_class_name, source_method_declaration, using_symbol_table)

    def get_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Return all methods in the project grouped by class.

        Retrieves all methods from all classes in the analyzed project,
        organized in a nested dictionary structure by qualified class name
        and then method signature.

        Returns:
            A nested dictionary with structure::

                {
                    "com.example.ClassName": {
                        "methodName(ParamType)": JCallable,
                        "anotherMethod()": JCallable,
                        ...
                    },
                    ...
                }

            Each :class:`~cldk.models.java.JCallable` contains the method's
            signature, parameters, return type, body, annotations, and other
            metadata.

        See Also:
            :meth:`get_methods_in_class`: For methods of a specific class.
            :meth:`get_method`: For a single method by name.
        """
        return self.backend.get_all_methods_in_application()

    def get_classes(self) -> Dict[str, JType]:
        """Return all classes in the project.

        Retrieves all type declarations (classes, interfaces, enums, records)
        discovered during analysis, organized by their fully qualified names.

        Returns:
            A dictionary mapping fully qualified class names (strings) to
            :class:`~cldk.models.java.JType` objects containing class metadata,
            methods, fields, and inheritance information.

        See Also:
            :meth:`get_class`: For a single class by name.
            :meth:`get_classes_by_criteria`: For filtered class retrieval.
        """
        return self.backend.get_all_classes()

    def get_classes_by_criteria(
        self, inclusions: List[str] | None = None, exclusions: List[str] | None = None
    ) -> Dict[str, JType]:
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
            :class:`~cldk.models.java.JType` objects for classes matching
            the criteria.

        Note:
            The filtering uses substring matching (``in`` operator), not
            regular expressions or glob patterns.

        See Also:
            :meth:`get_classes`: For all classes without filtering.
        """
        if exclusions is None:
            exclusions = []
        if inclusions is None:
            inclusions = []
        class_dict: Dict[str, JType] = {}
        all_classes = self.backend.get_all_classes()
        for application_class in all_classes:
            is_selected = False
            for inclusion in inclusions:
                if inclusion in application_class:
                    is_selected = True

            for exclusion in exclusions:
                if exclusion in application_class:
                    is_selected = False
            if is_selected:
                class_dict[application_class] = all_classes[application_class]
        return class_dict

    def get_class(self, qualified_class_name: str) -> JType:
        """Return a specific class by its qualified name.

        Retrieves detailed information about a single class, including its
        methods, fields, annotations, modifiers, and inheritance information.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"com.example.service.UserService"``).

        Returns:
            A :class:`~cldk.models.java.JType` object containing all analyzed
            information about the class. Returns ``None`` if the class is not
            found in the analyzed project.

        See Also:
            :meth:`get_classes`: For all classes in the project.
            :meth:`get_java_file`: To find which file contains a class.
        """

        return self.backend.get_class(qualified_class_name)

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> JCallable:
        """Return a specific method by class and method signature.

        Retrieves detailed information about a single method, including its
        signature, parameters, return type, annotations, body, and metrics.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method (e.g., ``"com.example.service.UserService"``).
            qualified_method_name: The method signature to retrieve
                (e.g., ``"getUser(String)"`` or ``"process())"``).

        Returns:
            A :class:`~cldk.models.java.JCallable` object containing all
            analyzed information about the method. Returns ``None`` if the
            method is not found.

        See Also:
            :meth:`get_methods_in_class`: For all methods of a class.
            :meth:`get_method_parameters`: For just the parameter list.
        """
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """Return the parameter types for a specific method.

        Retrieves the list of parameter type names defined in the method
        signature.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method.
            qualified_method_name: The method signature to get parameters for.

        Returns:
            A list of parameter type names as strings, in the order they
            appear in the method signature. Returns an empty list if the
            method is not found or has no parameters.

        See Also:
            :meth:`get_method`: For complete method information.
        """
        return self.backend.get_method_parameters(qualified_class_name, qualified_method_name)

    def get_java_file(self, qualified_class_name: str) -> str:
        """Return the file path containing a class with the given name.

        Given a qualified class name, returns the file path where that class
        is defined. This is useful for navigating from class references back
        to source files.

        Args:
            qualified_class_name: The fully qualified name of the class to
                locate (e.g., ``"com.example.service.UserService"``).

        Returns:
            The file path (as a string) containing the class definition.
            Returns ``None`` if no class with the given name is found.

        See Also:
            :meth:`get_class`: To get the full class object by name.
            :meth:`get_java_compilation_unit`: To get the compilation unit.
        """
        return self.backend.get_java_file(qualified_class_name)

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """Return the compilation unit for a specific file path.

        Retrieves the :class:`JCompilationUnit` object corresponding to a
        specific Java source file in the analyzed project.

        Args:
            file_path: The path to the Java file, which should be an absolute
                path or a path relative to the project root.

        Returns:
            The :class:`~cldk.models.java.JCompilationUnit` for the file,
            containing all analyzed information about package, imports,
            and type declarations. Returns ``None`` if the file is not
            part of the analyzed project.

        See Also:
            :meth:`get_symbol_table`: For bulk access to all compilation units.
            :meth:`get_java_file`: For reverse lookup (class to file).
        """
        return self.backend.get_java_compilation_unit(file_path)

    def get_methods_in_class(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """Return all methods defined in a specific class.

        Retrieves all methods belonging to the specified class, including
        instance methods, static methods, and constructors.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"com.example.service.UserService"``).

        Returns:
            A dictionary mapping method signatures (strings) to
            :class:`~cldk.models.java.JCallable` objects. Returns an empty
            dictionary if the class is not found or has no methods.

        See Also:
            :meth:`get_method`: For a single method by signature.
            :meth:`get_constructors`: For constructors specifically.
        """
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """Return all constructors of a specific class.

        Retrieves all constructor methods defined in the specified class.
        Constructors are methods with the same name as the class.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"com.example.model.User"``).

        Returns:
            A dictionary mapping constructor signatures to
            :class:`~cldk.models.java.JCallable` objects. Returns an empty
            dictionary if the class has no explicit constructors.

        See Also:
            :meth:`get_methods_in_class`: For all methods including constructors.
        """
        return self.backend.get_all_constructors(qualified_class_name)

    def get_fields(self, qualified_class_name: str) -> List[JField]:
        """Return all fields (member variables) of a specific class.

        Retrieves all field declarations in the specified class, including
        instance fields, static fields, and constants.

        Args:
            qualified_class_name: The fully qualified name of the class
                (e.g., ``"com.example.model.User"``).

        Returns:
            A list of :class:`~cldk.models.java.JField` objects, each
            containing information about a field's name, type, modifiers,
            and annotations.

        See Also:
            :meth:`get_class`: For complete class information.
        """
        return self.backend.get_all_fields(qualified_class_name)

    def get_nested_classes(self, qualified_class_name: str) -> List[JType]:
        """Return all nested (inner) classes of a specific class.

        Retrieves all classes that are defined inside the specified class,
        including static nested classes and inner classes.

        Args:
            qualified_class_name: The fully qualified name of the outer class
                (e.g., ``"com.example.model.Container"``).

        Returns:
            A list of :class:`~cldk.models.java.JType` objects for each
            nested class. Returns an empty list if no nested classes exist.

        See Also:
            :meth:`get_class`: For the outer class information.
        """
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name: str) -> Dict[str, JType]:
        """Return all classes that extend the specified class.

        Finds all classes in the project that directly extend the specified
        base class. This is useful for understanding class hierarchies and
        finding implementations of abstract classes.

        Args:
            qualified_class_name: The fully qualified name of the base class
                to find subclasses of (e.g., ``"com.example.base.BaseService"``).

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.java.JType` objects for all classes that
            extend the specified class.

        See Also:
            :meth:`get_extended_classes`: For the reverse (what a class extends).
            :meth:`get_class_hierarchy`: For the full inheritance graph.
        """
        return self.backend.get_all_sub_classes(qualified_class_name=qualified_class_name)

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """Return the superclass(es) that a class extends.

        Retrieves the parent class for the specified class. In Java, a class
        can extend at most one other class (single inheritance).

        Args:
            qualified_class_name: The fully qualified name of the class to
                get the superclass for.

        Returns:
            A list of superclass names (typically containing zero or one
            element, since Java has single inheritance). Returns empty list
            if the class directly extends Object or is not found.

        See Also:
            :meth:`get_sub_classes`: For finding classes that extend this class.
            :meth:`get_implemented_interfaces`: For interface implementations.
        """
        return self.backend.get_extended_classes(qualified_class_name)

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """Return all interfaces implemented by a class.

        Retrieves the list of interfaces that the specified class implements.
        A Java class can implement multiple interfaces.

        Args:
            qualified_class_name: The fully qualified name of the class to
                get implemented interfaces for.

        Returns:
            A list of interface names (as strings) that the class implements.
            Returns empty list if the class implements no interfaces.

        See Also:
            :meth:`get_extended_classes`: For class inheritance.
        """
        return self.backend.get_implemented_interfaces(qualified_class_name)

    def __get_class_call_graph_using_symbol_table(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Return class-level call graph using the symbol table.

        Internal method that uses symbol table-based resolution for building
        the call graph, which is faster but may be less accurate than full
        call graph analysis.

        Args:
            qualified_class_name: The fully qualified name of the class.
            method_signature: Optional method signature to scope the graph
                to calls originating from a specific method.

        Returns:
            A list of tuples ``(caller, callee)`` where each element is a
            :class:`~cldk.models.java.JMethodDetail` object representing
            a method in the call relationship.
        """
        return self.backend.get_class_call_graph_using_symbol_table(qualified_class_name, method_signature)

    def get_class_call_graph(
        self,
        qualified_class_name: str,
        method_signature: str | None = None,
        using_symbol_table: bool = False
    ) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Return call graph edges reachable from a class or method.

        Extracts a subset of the call graph containing only edges reachable
        from the specified class (and optionally a specific method within
        that class). This is useful for understanding the call structure
        of a specific component without the noise of the full project graph.

        Args:
            qualified_class_name: The fully qualified name of the class to
                start traversal from (e.g., ``"com.example.service.UserService"``).
            method_signature: Optional method signature to further constrain
                the starting point. If provided, only edges reachable from
                that specific method are included. If ``None``, edges from
                all methods in the class are included.
            using_symbol_table: If ``True``, uses the symbol table for faster
                but potentially less accurate resolution. If ``False`` (default),
                uses the full call graph analysis.

        Returns:
            A list of tuples ``(caller, callee)`` where each element is a
            :class:`~cldk.models.java.JMethodDetail` object representing
            a method in the call relationship.

        See Also:
            :meth:`get_call_graph`: For the complete project call graph.
            :meth:`get_callees`: For direct callees of a single method.
        """
        if using_symbol_table:
            return self.__get_class_call_graph_using_symbol_table(qualified_class_name=qualified_class_name, method_signature=method_signature)
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    def get_entry_point_classes(self) -> Dict[str, JType]:
        """Return all classes identified as application entry points.

        Identifies classes that serve as entry points for the application,
        such as classes containing main methods, servlet classes, or
        framework-specific entry point classes.

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.java.JType` objects for classes identified
            as entry points.

        See Also:
            :meth:`get_entry_point_methods`: For entry point methods.
        """
        return self.backend.get_all_entry_point_classes()

    def get_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Return all methods identified as application entry points.

        Identifies methods that serve as entry points for the application,
        such as main methods, servlet doGet/doPost methods, or framework-
        specific handler methods.

        Returns:
            A nested dictionary mapping class names to method signatures
            to :class:`~cldk.models.java.JCallable` objects for methods
            identified as entry points.

        See Also:
            :meth:`get_entry_point_classes`: For entry point classes.
        """
        return self.backend.get_all_entry_point_methods()

    def remove_all_comments(self) -> str:
        """Remove all comments from the source code.

        Strips all single-line (``//``) and multi-line (``/* */``) comments
        from the source code, including Javadoc comments. This is useful
        for code analysis that should ignore comment content.

        Returns:
            A string containing the source code with all comments removed.
            Whitespace where comments were removed may be preserved or
            collapsed depending on the implementation.

        Note:
            This method operates on the ``source_code`` provided during
            initialization. It requires single-file mode.

        See Also:
            :meth:`get_all_comments`: For extracting comments instead.
        """
        return self.backend.remove_all_comments(self.source_code)

    def get_methods_with_annotations(self, annotations: List[str]) -> Dict[str, List[Dict]]:
        """Return methods decorated with specific annotations.

        This method is intended to find all methods that have any of the
        specified annotations, such as ``@Override``, ``@Test``,
        ``@RequestMapping``, or custom annotations.

        Args:
            annotations: List of annotation names to search for (e.g.,
                ``["Override", "Test", "RequestMapping"]``). The ``@`` symbol
                should not be included.

        Returns:
            Would return a dictionary mapping annotation names to lists of
            method information dictionaries containing method details and
            bodies.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_test_methods`: For finding test methods specifically.
        """
        # TODO: This call is missing some implementation. The logic currently resides in java_sitter but tree_sitter will no longer be option, rather it will be default and common. Need to implement this differently. Somthing like, self.commons.treesitter.get_methods_with_annotations(annotations)
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_test_methods(self) -> Dict[str, str]:
        """Return methods identified as test methods.

        Finds all test methods in the source code by looking for methods
        annotated with common test framework annotations (e.g., ``@Test``
        from JUnit).

        Returns:
            A dictionary mapping test method signatures to their source
            code bodies.

        Note:
            This method operates on the ``source_code`` provided during
            initialization. It requires single-file mode.

        See Also:
            :meth:`get_methods_with_annotations`: For finding methods with
                any annotation.
        """

        return self.treesitter_java.get_test_methods(source_class_code=self.source_code)

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """Return line numbers where a method is called.

        This method is intended to find all line numbers in the source code
        where the specified method is invoked.

        Args:
            target_method_name: The name of the method to find calls to.

        Returns:
            Would return a list of line numbers (integers) where calls occur.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_callers`: For finding caller methods instead of lines.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """Return call targets using simple name resolution.

        This method is intended to find all methods that could be called
        based on simple name matching in the AST, without full semantic
        analysis.

        Args:
            declared_methods: Dictionary of declared method names and
                signatures to match against.

        Returns:
            Would return a set of method names that are call targets.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_call_graph`: For full semantic call resolution.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_all_crud_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all CRUD (Create, Read, Update, Delete) operations.

        Identifies and returns all database operations in the project by
        analyzing JPA/Hibernate annotations, repository patterns, and SQL
        statements. This is useful for understanding data access patterns
        in enterprise applications.

        Returns:
            A list of dictionaries, each containing:
                - ``"class"``: The :class:`~cldk.models.java.JType` containing
                  the operation
                - ``"method"``: The :class:`~cldk.models.java.JCallable`
                  performing the operation
                - ``"operations"``: List of
                  :class:`~cldk.models.java.JCRUDOperation` objects

        See Also:
            :meth:`get_all_create_operations`: For create operations only.
            :meth:`get_all_read_operations`: For read operations only.
            :meth:`get_all_update_operations`: For update operations only.
            :meth:`get_all_delete_operations`: For delete operations only.
        """
        return self.backend.get_all_crud_operations()

    def get_all_create_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all Create operations from CRUD analysis.

        Identifies database insert/create operations by analyzing
        ``save()``, ``persist()``, ``insert()``, and similar patterns.

        Returns:
            A list of dictionaries with class, method, and operation details.
            Same structure as :meth:`get_all_crud_operations`.

        See Also:
            :meth:`get_all_crud_operations`: For all CRUD operations.
        """
        return self.backend.get_all_create_operations()

    def get_all_read_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all Read operations from CRUD analysis.

        Identifies database read/select operations by analyzing
        ``find()``, ``get()``, ``select()``, and similar patterns.

        Returns:
            A list of dictionaries with class, method, and operation details.
            Same structure as :meth:`get_all_crud_operations`.

        See Also:
            :meth:`get_all_crud_operations`: For all CRUD operations.
        """
        return self.backend.get_all_read_operations()

    def get_all_update_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all Update operations from CRUD analysis.

        Identifies database update operations by analyzing
        ``update()``, ``merge()``, ``set()``, and similar patterns.

        Returns:
            A list of dictionaries with class, method, and operation details.
            Same structure as :meth:`get_all_crud_operations`.

        See Also:
            :meth:`get_all_crud_operations`: For all CRUD operations.
        """
        return self.backend.get_all_update_operations()

    def get_all_delete_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all Delete operations from CRUD analysis.

        Identifies database delete operations by analyzing
        ``delete()``, ``remove()``, and similar patterns.

        Returns:
            A list of dictionaries with class, method, and operation details.
            Same structure as :meth:`get_all_crud_operations`.

        See Also:
            :meth:`get_all_crud_operations`: For all CRUD operations.
        """
        return self.backend.get_all_delete_operations()

    # Some APIs to process comments
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """Return all comments contained within a specific method.

        Retrieves all comment nodes (single-line, multi-line, and Javadoc)
        that appear within the body of the specified method.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method.
            method_signature: The method signature to get comments from.

        Returns:
            A list of :class:`~cldk.models.java.JComment` objects found
            within the method body. Returns empty list if method not found.

        See Also:
            :meth:`get_comments_in_a_class`: For class-level comments.
            :meth:`get_all_comments`: For all comments in the project.
        """
        return self.backend.get_comments_in_a_method(qualified_class_name, method_signature)

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """Return all comments contained within a specific class.

        Retrieves all comment nodes that appear within the class body,
        including Javadoc comments, method-level comments, and inline
        comments.

        Args:
            qualified_class_name: The fully qualified name of the class.

        Returns:
            A list of :class:`~cldk.models.java.JComment` objects found
            within the class. Returns empty list if class not found.

        See Also:
            :meth:`get_comments_in_a_method`: For method-specific comments.
            :meth:`get_comment_in_file`: For file-level comments.
        """
        return self.backend.get_comments_in_a_class(qualified_class_name)

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """Return all comments in a specific file.

        Retrieves all comment nodes from the specified source file,
        including file-level comments, class comments, and method comments.

        Args:
            file_path: The path to the Java file.

        Returns:
            A list of :class:`~cldk.models.java.JComment` objects found
            in the file. Returns empty list if file not found.

        See Also:
            :meth:`get_all_comments`: For comments across all files.
        """
        return self.backend.get_comment_in_file(file_path)

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """Return all comments in the project grouped by file.

        Retrieves all comment nodes from all analyzed files, organized
        by file path.

        Returns:
            A dictionary mapping file paths (strings) to lists of
            :class:`~cldk.models.java.JComment` objects.

        See Also:
            :meth:`get_all_docstrings`: For Javadoc comments only.
        """
        return self.backend.get_all_comments()

    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        """Return all Javadoc comments in the project grouped by file.

        Retrieves only Javadoc-style comments (``/** ... */``) from all
        analyzed files. These typically document classes, methods, and
        fields.

        Returns:
            A dictionary mapping file paths (strings) to lists of
            :class:`~cldk.models.java.JComment` objects where
            ``is_javadoc`` is ``True``.

        See Also:
            :meth:`get_all_comments`: For all comment types.
        """
        return self.backend.get_all_docstrings()
