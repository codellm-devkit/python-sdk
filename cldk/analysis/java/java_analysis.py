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
primary high-level interface for performing static analysis on Java projects.
It combines Tree-sitter-based parsing with the CodeAnalyzer backend to provide
comprehensive code analysis capabilities.

The analysis operates on a project directory (cross-file call graphs, class
hierarchies, the symbol table). The 1.x single-file ``source_code`` mode was
removed in 2.0 (spec leg 3, J-10): pass the project directory, or hand a source
string to :class:`~cldk.analysis.commons.treesitter.TreesitterJava` directly.

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

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Tuple, Set, Union
import networkx as nx

from tree_sitter import Tree

from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, JavaBackend, Neo4jConnectionConfig, cache_subdir
from cldk.analysis.commons.bounds import DEFAULT_DEPTH, DEFAULT_MAX_NODES, DEFAULT_MAX_PATHS, DEFAULT_PAGE_SIZE
from cldk.analysis.commons.results import EdgePage, EntrypointCoverage, FlowPaths, LocateResult, Slice, SliceNode
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.models.java import JCallable
from cldk.models.java import JApplication
from cldk.models.java.models import (
    JCallableParameter,
    JCallSite,
    JCdgEdge,
    JCfgEdge,
    JCRUDOperation,
    JComment,
    JCompilationUnit,
    JDdgEdge,
    JEnumConstant,
    JExternalSymbol,
    JField,
    JMethodDetail,
    JType,
)
from cldk.models.java.projections import JCallableOverview, JClassOverview
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.analysis.java.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.analysis.java.backend import JavaAnalysisBackend

#: The annotations that *declare* a test method, by simple name — JUnit 4/5 and TestNG. A lifecycle
#: annotation (``@BeforeEach``, ``@AfterAll``) is deliberately not here: it marks a fixture, not a
#: test. Read by :meth:`JavaAnalysis.get_test_methods`.
_TEST_ANNOTATIONS = frozenset({"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"})


class JavaAnalysis:
    """Analysis facade for Java code.

    This class provides a comprehensive interface for performing static analysis
    on Java projects and source files. It combines Tree-sitter-based parsing for
    syntactic analysis with the CodeAnalyzer backend for semantic analysis.

    The facade is initialized with ``project_dir`` and provides full analysis
    capabilities including cross-file call graphs, class hierarchies, and symbol
    tables; the single-file ``source_code`` mode was removed in 2.0.

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
        analysis_level (str): The depth of analysis performed.
        eager_analysis (bool): Whether to force regeneration of analysis.
        target_files (List[str] | None): Specific files to analyze.
        backend_config (JavaBackend): The backend configuration object.
        treesitter_java (TreesitterJava): Tree-sitter parser for Java.
        backend (JCodeanalyzer): The underlying analysis backend.

    See Also:
        - :class:`~cldk.analysis.python.PythonAnalysis`: Python equivalent.
        - :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Backend.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        backend: JavaBackend | None = None,
    ) -> None:
        """Initialize the Java analysis facade.

        Creates a new analysis facade for Java code.

        Args:
            project_dir: Absolute or relative path to the Java project directory.
                The directory should contain Java source files (``.java``).
                Optional only for the read-only Neo4j backend.
            analysis_level: The depth of analysis to perform — any
                :class:`~cldk.analysis.AnalysisLevel`: ``"symbol_table"`` (1),
                ``"call_graph"`` (2), ``"program_dependency_graph"`` (3),
                ``"system_dependency_graph"`` (4); the level reaches the analyzer as ``-a``.
            target_files: Optional list of specific file paths (relative to
                ``project_dir``) to include in the analysis. When provided,
                only these files are analyzed, improving performance for
                large projects. Primarily supported for symbol-table level.
            eager_analysis: If ``True``, forces regeneration of the analysis
                database on each run, ignoring any existing cached results.
                If ``False``, cached results are reused when available.
            backend: The backend configuration object. Defaults to
                :class:`~cldk.analysis.commons.backend_config.CodeAnalyzerConfig`,
                which runs the packaged codeanalyzer-java binary and caches
                ``analysis.json`` under a language-keyed cache directory.

        Raises:
            NotImplementedError: If the requested analysis configuration is
                not supported by the backend.
        """

        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.backend_config: JavaBackend = backend if backend is not None else CodeAnalyzerConfig()
        self.treesitter_java: TreesitterJava = TreesitterJava()
        self.backend: JavaAnalysisBackend
        if isinstance(self.backend_config, Neo4jConnectionConfig):
            # Read-only: the graph is populated out of band; the SDK only polls it.
            cfg = self.backend_config
            application_name = cfg.application_name or (Path(project_dir).name if project_dir else None)
            self.backend = JNeo4jBackend(
                neo4j_uri=cfg.uri,
                neo4j_username=cfg.username,
                neo4j_password=cfg.password,
                neo4j_database=cfg.database,
                application_name=application_name,
            )
        else:
            # The config only carries the cache root. analysis.json is cached under <cache_dir>/java.
            cache_path = cache_subdir(self.backend_config.cache_dir, project_dir, "java")
            if cache_path is not None:
                cache_path.mkdir(parents=True, exist_ok=True)
            self.backend = JCodeanalyzer(
                project_dir=self.project_dir,
                eager_analysis=self.eager_analysis,
                analysis_level=self.analysis_level,
                analysis_json_path=cache_path,
                target_files=self.target_files,
            )

    def get_imports(self) -> List[str]:
        """Return every distinct import target of the project, sorted.

        A **set**, not a per-file listing and not the file's import order: the Neo4j projection
        aggregates every import of a module that resolves to the same target onto one edge, so the
        order within a file is not recoverable there and a list that preserved it locally would be
        one the two backends disagree about. The 1.x signature is a flat ``List[str]`` and never
        carried the file an import belongs to either.

        Returns:
            Fully qualified import targets (``"java.util.List"``, ``"java.io.*"``), sorted and
            distinct. A wildcard keeps its ``.*``; static imports are not marked here.

        See Also:
            :meth:`get_symbol_table`: per-file :class:`~cldk.models.java.models.JImport` records,
                with spans and the static/wildcard flags.
        """
        return self.backend.get_imports()

    def get_variables(self, **kwargs) -> Dict:
        """Return the local variables each callable declares.

        Args:
            **kwargs: The 1.x signature's filtering options, of which there are none. An unexpected
                keyword raises :class:`TypeError` naming it, rather than being ignored: silently
                dropping a filter returns an unfiltered answer that looks filtered.

        Returns:
            ``{"<type fqn>.<signature>": [JLocalVariable, ...]}`` — the J-1 call-graph key of
            :meth:`get_call_graph`, and one entry per callable including those declaring nothing.
            Fields and parameters are *not* folded in; they have their own accessors. Each list is
            ordered by ``(start_line, name)``: the Neo4j projection carries a line-only span, so
            two variables declared on one line have no order there to preserve.

        Raises:
            TypeError: An unexpected keyword argument was passed.

        See Also:
            :meth:`get_fields`: class-level fields.
            :meth:`get_method_parameters`: a callable's parameters.
        """
        if kwargs:
            raise TypeError(f"get_variables() got an unexpected keyword argument {next(iter(kwargs))!r}; it takes no filtering options")
        return self.backend.get_variables()

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

        See Also:
            :meth:`get_symbol_table`: For direct access to the symbol table.
            :meth:`get_compilation_units`: For a list of compilation units.
        """
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
            A ``networkx.DiGraph`` with one node per declared type (interfaces, enums, annotations
            and records included) and an edge **subclass → supertype** carrying
            ``type="EXTENDS"`` or ``type="IMPLEMENTS"`` — Java projects the two as separate
            relationship types and this keeps them apart. A supertype outside the project is a node
            too, spelled as the declaration wrote it.

        See Also:
            :meth:`get_sub_classes`: For finding subclasses of a specific class.
            :meth:`get_extended_classes`: For finding superclasses.
            :meth:`get_implemented_interfaces`: For interface implementations.
        """
        return self.backend.get_class_hierarchy()

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

        The call graph requires ``analysis_level`` of at least ``"call_graph"``;
        below it the graph is empty.

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes are keyed by the string ``"<type fqn>.<signature>"`` (e.g.
                  ``"com.acme.Svc.run(java.lang.String)"``), with a
                  :class:`~cldk.models.java.JMethodDetail` under ``method_detail``
                  and ``kind="callable"``
                - Edges represent call relationships, directed from caller
                  to callee, with ``type``, ``weight`` and ``calling_lines``

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

        See Also:
            :meth:`get_call_graph`: For the graph object directly.
        """
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

        See Also:
            :meth:`get_callees`: For the reverse direction (what a method calls).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """
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

        See Also:
            :meth:`get_callers`: For the reverse direction (who calls a method).
            :meth:`get_call_graph`: For the complete call relationship graph.
        """
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

    def get_class(self, qualified_class_name: str) -> JType | None:
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

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> JCallable | None:
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

        Note:
            Two fields depend on which backend answered. On the
            ``analysis.json`` backend ``code`` is the **body block** and
            ``body`` holds every body node. On the Neo4j backend ``code`` is
            the whole **declaration** (it *ends with* the body block, because
            the graph projects one line range per callable and no
            ``body_span``) and ``body`` holds the ``call`` nodes only — about
            30% of the graph's body nodes, which is what ``call_sites`` needs
            and all it needs.

        See Also:
            :meth:`get_methods_in_class`: For all methods of a class.
            :meth:`get_method_parameters`: For just the parameter list.
        """
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[JCallableParameter]:
        """Return the parameters of a specific method.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method.
            qualified_method_name: The method signature to get parameters for.

        Returns:
            The :class:`~cldk.models.java.models.JCallableParameter` objects
            (name, type, annotations, position), in signature order. Returns an
            empty list if the method is not found or has no parameters. (1.x
            annotated this ``List[str]``; it always returned the objects.)

        See Also:
            :meth:`get_method`: For complete method information.
        """
        return self.backend.get_method_parameters(qualified_class_name, qualified_method_name)

    def get_java_file(self, qualified_class_name: str) -> str | None:
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

        Raises:
            NotImplementedError: always. This accessor only ever operated on the
            ``source_code`` given to the 1.x constructor, and that single-file mode
            was removed in 2.0 (J-10). Pass the source to
            :meth:`TreesitterJava.remove_all_comments` directly.

        See Also:
            :meth:`get_all_comments`: For extracting comments instead.
        """
        raise NotImplementedError("single-file source mode was removed in 2.0; pass the source to TreesitterJava.remove_all_comments directly")

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
            A dictionary keyed by **the strings passed in**, each mapping to a list of
            ``{"class", "signature", "method_name", "body"}`` dicts, sorted by
            ``(class, signature)``. An annotation no callable carries is omitted. ``body`` is
            :attr:`~cldk.models.java.models.JCallable.code`, which is the body block off
            ``analysis.json`` and the whole declaration off the Neo4j projection — the same
            documented model property :meth:`get_test_methods` hands back.

            Matching reads the analyzer's own annotations rather than re-parsing source, so it
            answers on a Neo4j-backed analysis, which carries no module source at all.

        See Also:
            :meth:`get_test_methods`: For finding test methods specifically.
            :meth:`get_decorated_callables`: The projected form, whose J-5 marker rule this shares.
        """
        return self.backend.get_methods_with_annotations(annotations)

    def get_test_methods(self) -> Dict[str, str]:
        """Return methods identified as test methods.

        A callable is a test method when one of its own annotations is a test-declaring one:
        ``@Test`` (JUnit 4/5, TestNG), ``@ParameterizedTest``, ``@RepeatedTest``, ``@TestFactory``
        or ``@TestTemplate``. The annotation is matched by simple name, so a fully qualified
        spelling (``@org.junit.Test``) matches too, and its arguments are ignored — the same
        marker rule the spec's J-5 gives ``get_decorated_callables``.

        This reads the **analyzer's own** annotations off the model rather than re-parsing a
        module's ``source``, so it answers identically on both backends: a Neo4j-backed analysis
        carries no module ``source`` at all (``JCompilationUnit.source`` is ``""``), and the
        source-parsing version returned ``{}`` there — an empty reading as "this application has
        no tests" on an application with thousands.

        Returns:
            A dictionary mapping ``"<type fqn>.<signature>"`` — the call-graph node key of J-1,
            unique application-wide — to the callable's ``code``. Note that ``code`` is the body
            block off ``analysis.json`` and the whole declaration off the Neo4j projection
            (:attr:`~cldk.models.java.models.JCallable.code`).

        See Also:
            :meth:`get_methods_with_annotations`: For finding methods with
                any annotation.
        """
        return {
            f"{klass}.{signature}": callable_.code
            for klass, methods in self.get_methods().items()
            for signature, callable_ in methods.items()
            if any(d.name.rsplit(".", 1)[-1] in _TEST_ANNOTATIONS for d in callable_.decorators)
        }

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """Return line numbers where a method is called.

        This method is intended to find all line numbers in the source code
        where the specified method is invoked.

        Args:
            target_method_name: The name of the method to find calls to.

        Returns:
            Sorted, distinct **absolute file lines** of every call to a method of that name anywhere
            in the project, read off ``get_call_graph()``'s ``calling_lines`` edge attribute. A full
            signature is accepted and cut at its first ``(``; overloads share a name at a call site
            and so cannot be separated here. Empty when nothing calls that name.

        See Also:
            :meth:`get_callers`: For finding caller methods instead of lines.
        """
        return self.backend.get_calling_lines(target_method_name)

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """Return call targets using simple name resolution.

        This method is intended to find all methods that could be called
        based on simple name matching in the AST, without full semantic
        analysis.

        Args:
            declared_methods: Dictionary of declared method names and
                signatures to match against.

        Returns:
            The subset of ``declared_methods``' keys — cut to their simple names at the last
            ``(``, so a signature-keyed dict such as :meth:`get_methods_in_class`'s can be passed
            straight in — that some call site in the project actually writes. Simple-name matching
            only: no overload resolution, no receiver typing, no hierarchy walk.

        See Also:
            :meth:`get_call_graph`: For full semantic call resolution.
        """
        return self.backend.get_call_targets(declared_methods)

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
        """Return the method's own comment.

        **Not** every comment inside the body: on both backends this is the
        analyzer's per-declaration comment list, which holds the comment
        immediately above the declaration and nothing else (at most one; 70 of
        the 128 callables in the committed ``-a 4`` fixture have one, 65 of
        them javadoc). Comments *inside* a method body reach the SDK only
        through :meth:`get_comment_in_file`, which reports the whole file's.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method.
            method_signature: The method signature to get comments from.

        Returns:
            A list of :class:`~cldk.models.java.JComment` objects found
            within the method body. Returns empty list if method not found.

        Note:
            On a backend whose source keeps only per-declaration javadoc — the
            Neo4j backend — this narrows to **the method's javadoc alone**: a
            strictly smaller set than every comment in the body, and still a
            real answer about a real declaration, which is why this accessor
            narrows where :meth:`get_all_comments` and
            :meth:`get_comment_in_file` refuse (J-16).

        See Also:
            :meth:`get_comments_in_a_class`: For class-level comments.
            :meth:`get_all_comments`: For all comments in the project.
        """
        return self.backend.get_comments_in_a_method(qualified_class_name, method_signature)

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """Return the class's own comment.

        **Not** the comments inside the class body: on both backends this is
        the type declaration's own comment list — the comment immediately
        above ``class Foo``. A method's comment is on
        :meth:`get_comments_in_a_method`, and an inline comment in a body is
        on neither; :meth:`get_comment_in_file` reports the whole file's.

        Args:
            qualified_class_name: The fully qualified name of the class.

        Returns:
            A list of :class:`~cldk.models.java.JComment` objects found
            within the class. Returns empty list if class not found.

        Note:
            Narrows to the class's javadoc alone on a javadoc-only backend, in
            exactly the way :meth:`get_comments_in_a_method` does (J-16).

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

        Raises:
            CodeanalyzerExecutionException: If the backend's source carries no
                file-level comments at all — the Neo4j projection does not —
                naming what is missing and what to read instead. An empty list
                would read as "this file has no comments" (J-16).

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

        Raises:
            CodeanalyzerExecutionException: As :meth:`get_comment_in_file`
                does, and for the same reason (J-16).

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

        Note:
            *Which* javadoc depends on the backend: the ``analysis.json``
            backend reports each compilation unit's own comment list, holding
            the **file-level** javadoc; the Neo4j backend reports the javadoc of
            each **declaration** in the file (type, callable, field, enum
            constant, record component). Both are javadoc keyed by file, and
            they are different sets for the same file (J-16).

        See Also:
            :meth:`get_all_comments`: For all comment types.
        """
        return self.backend.get_all_docstrings()

    # =====================================================================================
    # The addressing surface (leg 3b, Task 1). Every signature is
    # :class:`~cldk.analysis.python.python_analysis.PythonAnalysis`'s, keyword-for-keyword.
    # =====================================================================================
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        The single most-needed query for triaging a scanner alert: an alert arrives as
        ``file:line`` and this resolves it to the enclosing callable in one call, rather than
        ``get_method``, falling back to ``get_callers``, falling back to scanning the symbol table
        by hand. Four outcomes stay distinguishable — see
        :class:`~cldk.analysis.commons.results.LocateResult`: inside a callable (``callable`` set,
        plus ``body`` when a body node is that precise), at real module scope (``module_scope``
        diagnostic), in the gap between two callables (also module scope, never snapped to the
        nearest callable), or in a file the analysis has no module for (``file_not_in_graph``).

        There is no ``col`` parameter: the Neo4j graph projects only ``start_line``/``end_line`` on
        ``:JCallable`` and ``:JBodyNode``, so a column would work in process and be silently inert
        over the graph.

        Args:
            path: The file path. Normalised against the backend's module keys, so a ``./``-prefixed
                or absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.

        Returns:
            A :class:`~cldk.analysis.commons.results.LocateResult` carrying the innermost body
            node, the enclosing callable, its owning type, its module, and the source slice — never
            an ambiguous empty. ``module.module_name`` is the unit's **declared package** (J-2),
            and ``source`` is the enclosing callable's text, which is the **body block** on the
            ``analysis.json`` backend and the whole **declaration** over Neo4j (see
            :meth:`get_source`). A module-scope result over Neo4j is ``""`` plus a
            ``module_source_unavailable`` diagnostic: the graph carries no module text.

        See Also:
            :meth:`locate_many`: The bulk form — the point, not an optimisation.
        """
        return self.backend.locate(path, line)

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many ``(path, line)`` positions in one round trip, in input order.

        Args:
            positions: The ``(path, line)`` pairs to resolve, e.g. from a scanner's alert list.

        Returns:
            One :class:`~cldk.analysis.commons.results.LocateResult` per input position, in the
            same order.

        See Also:
            :meth:`locate`: The single-position form.
        """
        return self.backend.locate_many(positions)

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the one callable it names, in the caller's vocabulary.

        The addressing step every name-taking accessor performs, exposed so a caller can perform it
        once and keep the answer::

            node = java.resolve_callable("cancelOrder(java.lang.Integer, boolean)", in_class="TradeDirect")
            node.callable   # "…impl.direct.TradeDirect.cancelOrder(java.lang.Integer, boolean)"
            node.file, node.line

        ``name`` matches whole or as a dotted suffix, **and** against the signature with its
        parameter tail cut, so ``"cancelOrder"`` names a method a caller has not typed the
        parameters of; the tail-carrying spelling is what resolves one overload out of a pair
        (J-3). ``in_class`` is a dotted suffix of the owning type's qualified name — which, for a
        local or anonymous class, carries the callable that declares it (the J-1 erratum);
        ``in_module`` is a repo-relative path suffix or a dotted spelling of the unit's **declared
        package**, optionally qualified by a type it declares (J-2). Ambiguity raises with every
        candidate; nothing is guessed.

        Everything the analyzer emitted as a callable is addressable (J-6): an initializer
        (``<clinit>$0()``) resolves and behaves like a method, and an **implicit** callable
        resolves with ``line=-1`` — it has no span at all, which is why :meth:`get_source` refuses
        it by name rather than returning an empty string.

        Raises:
            AmbiguousName: More than one callable matched.
            SelectorNotInGraph: Nothing matched — naming the argument that missed.
        """
        return self.backend.resolve_callable(name, in_class=in_class, in_module=in_module)

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable — in Java, a parameter — to the position that
        carries it.

        The same resolution the dataflow accessors perform on their ``src``, exposed so a caller can
        check what a name means before asking a question of it::

            java.resolve_value("orderID", within="TradeDirect.cancelOrder").kind   # "parameter"

        Raises:
            AmbiguousName: ``within`` named more than one callable, or ``name`` more than one value.
            SelectorNotInGraph: No such callable, or no such value in it.
        """
        return self.backend.resolve_value(name, within=within)

    def get_source(self, node_id: str) -> str:
        """Return the source text named by ``node_id`` — a callable, or one of its body nodes.

        ``node_id`` is a callable's ``"<type fqn>.<signature>"`` name (what :meth:`resolve_callable`
        returns in ``callable``), a callable's opaque id (what it returns in ``ref``), or the
        body-node id :attr:`~cldk.analysis.commons.results.LocateResult.node_id` hands back, so a
        statement or call site :meth:`locate` found can be re-fetched precisely. Passed back as
        received, never composed.

        **What comes back for a callable depends on the backend, and it is the graph's difference,
        not this method's.** On the ``analysis.json`` backend it is the **body block**; over Neo4j
        it is the whole **declaration**, which *ends with* that body block — the projection carries
        one line range per callable and no ``body_span`` (upstream codeanalyzer-java#176). The
        relation is exact and total, so a caller reading either can rely on it; it is recorded in
        the lossiness table of ``docs/agent-api-reference.md`` and asserted by the live parity
        suite. A **body node** likewise has text only on the ``analysis.json`` backend: the graph
        carries none below callable granularity.

        Args:
            node_id: A callable's name or id, or an id from :meth:`locate` — passed back as
                received, not composed.

        Returns:
            The source text, never an ambiguous empty string.

        Raises:
            KeyError: Nothing matches ``node_id``, or it names a node with no recoverable source —
                an implicit callable (the analyzer emits it with no span and no body), or, over
                Neo4j, a body node. The message names the reason.
        """
        return self.backend.get_source(node_id)

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in ``source`` for these positions, in one round trip.

        Addressing answers *where*; this answers *what*, and it is a second call because source is
        the one field with no size ceiling. Takes anything carrying an address — slice nodes, a
        ``locate()`` result — and gives back the same
        :class:`~cldk.analysis.commons.results.SliceNode` shape with ``source`` filled.

        Afterwards, ``source=None`` means exactly one thing: **this position exists and there is no
        text for it.** A ref that names nothing raises instead.

        Args:
            nodes: The positions to hydrate. An empty sequence costs no round trip.

        Returns:
            The same positions, in the same order, with ``source`` filled where the backend has
            text for them.

        Raises:
            KeyError: A ref names nothing in this application.
            TypeError: An element carries no address to look up.
        """
        return self.backend.describe(nodes)

    # =====================================================================================
    # The dataflow surface (leg 3b, Task 2). Every signature is
    # :class:`~cldk.analysis.python.python_analysis.PythonAnalysis`'s, keyword-for-keyword.
    # =====================================================================================
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCfgEdge]:
        """Return one page of the control flow edges within one callable.

        The intraprocedural half of "how does this method run": the analyzer's own CFG, one edge
        per successor, with the branch kind on the edge rather than implied by order. Endpoints are
        body-node ids :meth:`get_source` accepts, so a statement on a path can be read back.

        Args:
            callable: The callable's name, resolved as in :meth:`resolve_callable`.
            in_class: Disambiguate by owning class.
            page_size: Most edges to return.
            cursor: ``next_cursor`` from a previous page.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.java.models.JCfgEdge`, whose ``complete`` says whether the page is
            the whole graph and whose ``total`` says how large that is.

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or a cursor from another page.
            CodeanalyzerUsageException: ``callable`` is an implicit callable — it resolves (J-6)
                and the analyzer emits it with no body, so there is no flow to page — or the
                analysis was built below ``analysis_level="program_dependency_graph"``.
        """
        return self.backend.get_cfg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCdgEdge]:
        """Return one page of the control dependence edges within one callable.

        ``src`` is the branching node ``dst`` is control dependent on — the analyzer's
        post-dominance over the CFG, not re-derived here. Arguments and failures are
        :meth:`get_cfg`'s.
        """
        return self.backend.get_cdg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JDdgEdge]:
        """Return one page of the data dependence edges within one callable.

        Each edge names the variable it flows (``var``) and the evidence for it (``prov``), which in
        Java is one of **two** tiers — ``ssa`` (324,959 edges on the reference graph) or
        ``points-to`` (1,134). :func:`~cldk.analysis.commons.results.prov_rank` ranks ``points-to``
        least certain, which is what a caller weighing two hops reads. Arguments and failures are
        :meth:`get_cfg`'s.
        """
        return self.backend.get_ddg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return everything the value ``src`` depends on.

        On an analysis whose port lattice carries no dependence edge — codeanalyzer-java before
        3.0.3, or ``--l3-engine wala`` — that is the seed plus the argument vertex at every call
        site that passes a value into the parameter, and nothing behind those arguments. It is
        still a real answer that varies with the program, which is why this one answers where
        :meth:`slice_forward` refuses. From 3.0.3 the walk carries on into the statements that
        computed those arguments.

        Args:
            src: The value's name — in Java, a parameter of ``within``.
            within: The callable to look inside. Required: a value name is scoped by its callable.
            depth: Most hops from the seed; ``None`` for the whole cone.
            max_nodes: Most nodes to return. A cap that fires is reported, never silent.

        Returns:
            A :class:`~cldk.analysis.commons.results.Slice` containing the seed, ordered by node id.

        Raises:
            AmbiguousName: ``within`` or ``src`` matched more than one thing.
            SelectorNotInGraph: Either matched nothing.
            ValueError: ``depth`` is not a positive ``int``, or ``max_nodes`` is below 1.
        """
        return self.backend.slice_backward(src, within=within, depth=depth, max_nodes=max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return everything the value ``src`` can affect.

        **Java cannot answer this today and says so.** A parameter vertex has no outgoing dependence
        edge in codeanalyzer-java's output, so the result would be the seed alone for every
        parameter of every application — indistinguishable from "this parameter affects nothing".
        Arguments and names are judged first; then it raises naming the gap.

        Raises:
            CodeanalyzerExecutionException: The analyzer's port lattice carries no dependence edge.
        """
        return self.backend.slice_forward(src, within=within, depth=depth, max_nodes=max_nodes)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return every callable that can reach any of ``sinks`` — "what could get here".

        A call-graph cone, so its vertices are callables; the sinks are in the result and in
        ``roots``. Bounded by default, because an unbounded cone on a real application is a
        truncated answer to a question nobody asked; ``depth=None`` asks for the whole thing and
        ``total`` says how much a cap left out.

        Args:
            sinks: The callables to walk back from, each resolved as in :meth:`resolve_callable`.
            depth: Most call hops back; ``None`` for the whole cone.
            max_nodes: Most nodes to return.

        Raises:
            AmbiguousName: A sink matched more than one callable.
            SelectorNotInGraph: A sink matched none.
            TypeError: ``sinks`` is a bare string.
            ValueError: ``sinks`` is empty, or a bound is out of range.
        """
        return self.backend.backward_cone(sinks, depth=depth, max_nodes=max_nodes)

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Return whether there is a call path from ``src`` to ``dst``.

        The cheap check before asking for the paths themselves. **Unbounded by default**, unlike the
        slices: a hop budget on a boolean would make "there is no path" and "there is no path within
        five hops" the same ``False``.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either matched none.
            ValueError: ``depth`` is not a positive ``int``.
        """
        return self.backend.reaches(src, dst, depth=depth)

    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Return the callables that call this one, one hop back, addressed by name.

        The name-based sibling of :meth:`get_callers`, which takes a class name plus a method
        signature and returns raw dicts; that one is a frozen 1.x signature and is unchanged. ``[]``
        is unambiguous — a name matching nothing raises.

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """
        return self.backend.callers_of(name, in_class=in_class, in_module=in_module)

    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Return the callables this one calls, one hop forward, addressed by name.

        Java's call graph has no external vertices on either backend (J-1), so unlike Python's and
        TypeScript's this never reports a ``kind="external"`` node; ``get_external_symbols`` is where
        call targets outside the project live.

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """
        return self.backend.callees_of(name, in_class=in_class, in_module=in_module)

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """Return how the value ``src`` reaches the value ``dst`` — the sequences, where a slice is
        the set.

        Two scopes, not one, and neither defaults to the other: a value is addressed by a name plus
        the callable it enters, and a single scope could never find the cross-callable path this
        exists for.

        **Java cannot answer this today and says so** — see :meth:`slice_forward`. Arguments and
        names are judged first.

        Raises:
            AmbiguousName / SelectorNotInGraph: A name matched more than one thing, or nothing.
            ValueError: A bound is out of range, or the two endpoints are the same position.
            CodeanalyzerExecutionException: The analyzer's port lattice carries no dependence edge.
        """
        return self.backend.paths_between(src, dst, src_within=src_within, dst_within=dst_within, depth=depth, max_paths=max_paths)

    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """Return how one callable reaches another — the evidence-carrying form of :meth:`reaches`.

        Every hop is ``via="call"`` with no ``var`` and no ``prov``: a call edge carries neither, and
        saying so is better than inventing a provenance. Only shortest paths, ordered so that
        ``max_paths`` truncates a prefix of one total order rather than an arbitrary subset.

        Raises:
            AmbiguousName / SelectorNotInGraph: A name matched more than one callable, or none.
            ValueError: A bound is out of range, or ``src`` and ``dst`` name the same callable.
        """
        return self.backend.call_paths_between(src, dst, depth=depth, max_paths=max_paths)

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Return whether the value ``src`` reaches any argument of a call to ``callee``.

        **Java cannot answer this today and says so** — see :meth:`slice_forward`.

        Raises:
            AmbiguousName / SelectorNotInGraph: A name matched more than one thing, or nothing.
            ValueError: ``depth`` is not a positive ``int``.
            CodeanalyzerExecutionException: The analyzer's port lattice carries no dependence edge.
        """
        return self.backend.flows_to_call(src, callee, within=within, depth=depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Return whether the value ``src`` reaches ``callee``'s parameter ``arg``.

        A different question from :meth:`flows_to_call`: a tainted value routinely reaches a method
        without reaching the parameter that matters. ``arg`` is named, never numbered.

        **Java cannot answer this today and says so** — see :meth:`slice_forward`.

        Raises:
            AmbiguousName / SelectorNotInGraph: A name matched more than one thing, or nothing —
                including ``arg`` naming no parameter of ``callee``, which is a caller error and not
                a ``False``.
            ValueError: ``depth`` is not a positive ``int``.
            CodeanalyzerExecutionException: The analyzer's port lattice carries no dependence edge.
        """
        return self.backend.flows_to_argument(src, callee, arg, within=within, depth=depth)

    @property
    def has_resolution_edges(self) -> bool:
        """Whether call sites carry a resolved callee on this backend right now.

        ``False`` means every empty ``callee_signature`` is explained by the attached graph
        carrying no ``J_RESOLVES_TO`` edge for this application, not by individual call sites
        failing to resolve. The ``analysis.json`` backend is unconditionally ``True``:
        codeanalyzer-java resolves callees at every analysis level.
        """
        return self.backend.has_resolution_edges

    # =====================================================================================
    # Entrypoints, the bulk projections, the artifact layer and the type-kind leaf accessors
    # (leg 3b, Task 3). The facade delegates; the policy lives once on
    # :class:`~cldk.analysis.java.backend.JavaAnalysisBackend`, which both backends inherit.
    # =====================================================================================
    def get_callables_overview(self) -> List[JCallableOverview]:
        """Return a lightweight overview of every callable in the project, in one bulk read.

        A field-projected alternative to :meth:`get_methods` for enumeration: each
        :class:`~cldk.models.java.projections.JCallableOverview` carries the callable's addressable
        key, declaring type, kind, location, modifiers and annotation names — but not the full
        reconstruction (body nodes, call sites, local classes). Body-inspect the few you need
        afterwards via :meth:`get_method` or :meth:`get_method_bodies`.

        Returns:
            A flat list, one entry per callable the analyzer emitted — initializers, implicit
            constructors and the callables of local and anonymous classes included (J-6).

        See Also:
            :meth:`get_decorated_callables`: The same projection filtered by annotation.
            :meth:`get_method_bodies`: Bulk source fetch for chosen keys.
        """
        return self.backend.get_callables_overview()

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Return source text for the given callables, in one bulk read.

        Args:
            signatures: The keys :meth:`get_callables_overview` hands back
                (``JCallableOverview.key``, the J-1 ``"<type fqn>.<signature>"`` name) — matched
                exactly. A bare Java signature is unique only within its declaring type, so it is
                not an address here.

        Returns:
            A dict mapping each key to its source text. Keys with no matching callable are omitted,
            as are callables with no source text of their own — the implicit constructors, and only
            those (1,117 of daytrader8's 1,216). The ``<clinit>$N()`` initializers carry a body
            block and do come back. Every value is a real, non-empty ``str``.

        Note:
            The text differs by backend exactly as :meth:`get_source` does: the body block off
            ``analysis.json``, the whole declaration off the Neo4j projection
            (codeanalyzer-java#176).
        """
        return self.backend.get_method_bodies(signatures)

    def get_decorated_callables(self, markers: List[str]) -> List[JCallableOverview]:
        """Return overviews of callables annotated with any of the given markers, in one bulk read.

        Args:
            markers: Annotation names. Each matches by simple name (``Test``), with a leading ``@``
                ignored (``@Test``), or by fully-qualified name (``org.junit.Test``) — J-5. Nothing
                is matched fuzzily (E8).

        Returns:
            A list of :class:`~cldk.models.java.projections.JCallableOverview`, one per matching
            callable.

        See Also:
            :meth:`get_callables_overview`: The unfiltered projection.
        """
        return self.backend.get_decorated_callables(markers)

    def get_entrypoints(self) -> List[JCallableOverview]:
        """Return overviews of every callable the analyzer marked as an entrypoint, in one bulk read.

        codeanalyzer-java's own detection pass already finds servlet methods, JAX-RS resource
        methods, MDB listeners and the rest; this surfaces that mark instead of making a caller
        rediscover it. 133 of daytrader8's 1,216 callables carry it.

        Returns:
            A list of :class:`~cldk.models.java.projections.JCallableOverview`. Empty means the pass
            found no entrypoint *callables* — the mark itself is never missing, on either backend.

        See Also:
            :meth:`get_entrypoint_classes`: The type-level sibling this walk never sees.
            :meth:`get_entrypoint_coverage`: Whether the pass itself had gaps — which Java, alone
                of the three languages, cannot say.
        """
        return self.backend.get_entrypoints()

    def get_entrypoint_classes(self) -> List[JClassOverview]:
        """Return overviews of every type the analyzer marked as an entrypoint in its own right.

        :meth:`get_entrypoints` walks callables only, so a type marked at the declaration with no
        individually-marked method is invisible to it. This is that sibling — the projected form of
        :meth:`get_entry_point_classes`, which keeps its 1.x ``Dict[str, JType]`` shape.
        """
        return self.backend.get_entrypoint_classes()

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Report the entrypoint pass's coverage — which for Java is that **there is no report**.

        codeanalyzer-java 3.0.1 emits the entrypoint marks and nothing about the pass that made
        them: ``analysis.json`` has no report key and the ``:JApplication`` anchor carries only
        ``name``/``schema_version``/``analyzer_name``/``analyzer_version``. So this returns an
        :class:`~cldk.analysis.commons.results.EntrypointCoverage` whose ``diagnostics`` carry
        ``entrypoint_report_unavailable`` and whose other fields are therefore not coverage
        information — the same "say so honestly" shape as
        :attr:`~cldk.analysis.commons.results.LocateResult.diagnostics`'s
        ``module_source_unavailable``, and identical on both backends (J-4).

        It is deliberately **not** synthesised from the ``is_entrypoint`` booleans: a count of
        syntactically-marked callables is not a coverage record.
        """
        return self.backend.get_entrypoint_coverage()

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[JCallSite]]:
        """Return the call sites of the given callables, keyed by the key that named them.

        Avoids the per-callable reconstruction fan-out when call sites are wanted for a specific
        frontier.

        Args:
            signatures: The keys :meth:`get_callables_overview` hands back, matched exactly.

        Returns:
            A dict mapping each existing key to its list of
            :class:`~cldk.models.java.models.JCallSite` (empty when the callable makes no calls);
            keys with no matching callable are omitted.

        See Also:
            :attr:`has_resolution_edges`: Distinguishes a genuinely unresolved callee from a graph
                carrying no resolution at all.
        """
        return self.backend.get_callsites_for(signatures)

    def get_external_symbols(self) -> Dict[str, JExternalSymbol]:
        """Return every call-graph endpoint outside the analysed project, keyed by its
        ``@external`` id.

        Returns:
            The analyzer's own ``external_symbols`` map. Empty means the run homed them and this
            project's call graph makes no calls outside itself.

        Raises:
            CodeanalyzerExecutionException: The run never homed them, which is a different fact.
                codeanalyzer-java emits ``external_symbols`` only under ``--external-calls``, which
                ``--emit neo4j`` forces and a local ``-a`` run does not — so the Neo4j backend
                answers and the local one refuses rather than returning an empty dict that would
                read as "nothing outside".
        """
        return self.backend.get_external_symbols()

    # -----[ repository artifacts ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Return every non-code project artifact (``pom.xml``, properties files, descriptors, …),
        keyed by repo-relative path.

        This layer (``Artifact``/``ConfigKey``/``Package`` nodes) is the one part of the graph every
        ``codeanalyzer-<lang>`` projects identically and unprefixed, so it is carried in the shared
        ``Py*`` models rather than in Java-specific ones. ``JArtifact.text_truncated`` has no home
        on the shared model; read it off ``JApplication.artifacts`` when it matters.

        See Also:
            :meth:`get_dependencies`, :meth:`get_config_keys`, :meth:`get_config_uses`.
        """
        return self.backend.get_artifacts()

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        """Return every declared dependency, one entry per declaring manifest, optionally filtered.

        All three filters default to "don't filter". The Maven ``group`` coordinate has no home on
        the shared model; read it off ``JApplication.dependencies`` when ``name`` alone is
        ambiguous.

        Args:
            direct_only: When ``True``, excludes lockfile-only transitive pins.
            ecosystem: When given, only dependencies from this package ecosystem (``"maven"``).
            declared_in: When given, only dependencies declared by this artifact id.
        """
        return self.backend.get_dependencies(direct_only=direct_only, ecosystem=ecosystem, declared_in=declared_in)

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Return every configuration key flattened out of a config-bearing artifact, keyed
        ``"<artifact repo-relative path>@key/<dotted key>"`` (``pom.xml@key/project.artifactId``).

        That is the analyzer's own id with its ``can://artifact/<app>/`` prefix dropped: the
        application name belongs to the run, not to the key, and ``can://`` ids stay off the public
        surface (E6). The full id is still on ``PyConfigKey.id``. Python and TypeScript key this by
        the raw id today; aligning the three is tracked as python-sdk#346 and is deliberately not
        done piecemeal here.
        """
        return self.backend.get_config_keys()

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Return resolved code-to-config edges: which body node reads which config key.

        Always ``[]`` on codeanalyzer-java 3.0.1, which has no code-to-config detector (there is no
        ``config_uses`` on the Java wire) — so there is nothing for ``key`` to filter.

        Args:
            key: When given, only edges whose target key has this bare ``key``.

        See Also:
            :meth:`get_config_readers`: The same edges, resolved to their reading callables.
            :meth:`get_unresolved_config_reads`: The reads this cannot show.
        """
        return self.backend.get_config_uses(key)

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Return every detector-matched config read that never closed on exactly one declared key.

        Always ``[]`` on codeanalyzer-java 3.0.1: there is no config-read detector, so there is
        nothing to have failed to resolve.
        """
        return self.backend.get_unresolved_config_reads()

    def get_config_readers(self, key: str) -> List[JCallableOverview]:
        """Return overviews of every callable reading configuration key ``key``.

        Always ``[]`` for the same reason :meth:`get_config_uses` is: with no code-to-config edges
        on the Java wire there is no edge to resolve to a reading callable.

        Args:
            key: The bare configuration key, matched as :meth:`get_config_uses` matches it.
        """
        return self.backend.get_config_readers(key)

    # -----[ the type-kind leaf accessors (J-7) ]-----
    def get_interfaces(self) -> Dict[str, JType]:
        """Return every interface in the project, keyed by qualified name.

        The ``kind``-filtered siblings of :meth:`get_classes`, sharing TypeScript's names for the
        same concepts (G3). Measured: 3 interfaces in daytrader8, 594 in ThingsBoard.
        """
        return self.backend.get_interfaces()

    def get_enums(self) -> Dict[str, JType]:
        """Return every enum in the project, keyed by qualified name (192 in ThingsBoard; daytrader8
        declares none)."""
        return self.backend.get_enums()

    def get_enum_members(self, qualified_enum_name: str) -> List[JEnumConstant]:
        """Return the constants declared by one enum.

        Args:
            qualified_enum_name: The enum's qualified name, as :meth:`get_enums` keys it.

        Raises:
            SelectorNotInGraph: The name is not an enum of this application — no type at all, or a
                type of another kind. An empty list means an enum that declares no constant, which
                is a different answer (D7).
        """
        return self.backend.get_enum_members(qualified_enum_name)

    def get_records(self) -> Dict[str, JType]:
        """Return every record in the project, keyed by qualified name — the one Java-only type kind
        (35 in ThingsBoard; daytrader8 declares none). Annotation types have no leaf accessor of
        their own and stay reachable through :meth:`get_classes` (J-7)."""
        return self.backend.get_records()
