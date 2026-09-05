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
    - **PyCG**: For call-graph construction.
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
from typing import Dict, List, Sequence, Tuple

import networkx as nx
from tree_sitter import Tree

from cldk.analysis.commons.backend_config import Neo4jConnectionConfig, PyBackend, PyCodeAnalyzerConfig, cache_subdir
from cldk.analysis.commons.results import EntrypointCoverage, LocateResult
from cldk.analysis.commons.treesitter import TreesitterPython
from cldk.analysis.python.backend import PythonAnalysisBackend
from cldk.analysis.python.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.models.python import (
    CdgEdge,
    CfgEdge,
    DdgEdge,
    PyApplication,
    PyArtifact,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
    PyComment,
    PyConfigKey,
    PyConfigRead,
    PyConfigUseEdge,
    PyDependency,
    PyExternalSymbol,
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
        project_dir: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        backend: PyBackend | None = None,
    ) -> None:
        """Initialize the Python analysis facade.

        Creates a new analysis facade for a Python project. This constructor
        sets up the Tree-sitter parser and initializes the analysis backend
        selected by the type of ``backend``.

        Args:
            project_dir: Absolute or relative path to the Python project directory
                to analyze. This directory should contain Python source files
                (``*.py``). Required for the in-process backend (``source_code``
                mode is not supported); optional for the Neo4j backend, whose
                graph is populated out of band.
            analysis_level: The depth of analysis to perform, and what the in-process
                backend actually asks the analyzer for: ``"symbol_table"``,
                ``"call_graph"``, ``"program_dependency_graph"`` (intraprocedural
                CFG/CDG/DDG) or ``"system_dependency_graph"`` (interprocedural, the
                level that produces the ``formal_in`` vertices ``resolve_value``
                addresses). Deeper levels cost more analysis time. Ignored by the
                Neo4j backend, whose graph is always emitted at full depth. See
                :class:`~cldk.analysis.AnalysisLevel`.
            target_files: Optional list of specific file paths (relative to
                ``project_dir``) to include in the analysis. When provided,
                only these files are analyzed, which can significantly improve
                performance for large projects. If ``None``, all Python files
                in the project are analyzed.
            eager_analysis: If ``True``, forces regeneration of all analysis
                caches and databases, ignoring previously cached results.
                If ``False``, cached results are reused when available.
            backend: The backend configuration object. A
                :class:`~cldk.analysis.commons.backend_config.PyCodeAnalyzerConfig`
                (the default) selects the in-process codeanalyzer-python backend;
                a :class:`~cldk.analysis.commons.backend_config.Neo4jConnectionConfig`
                selects the read-only Neo4j backend. Defaults to
                ``PyCodeAnalyzerConfig()``.

        Raises:
            ValueError: If ``project_dir`` is ``None`` while using the in-process
                backend. Python analysis requires a project directory; single-file
                source code mode is not supported.

        """
        # The backend is selected by the *type* of the config: Neo4jConnectionConfig picks the
        # read-only Cypher backend, PyCodeAnalyzerConfig (the default) the in-process analyzer.
        self.backend_config: PyBackend = backend if backend is not None else PyCodeAnalyzerConfig()
        # With a Neo4j config the graph is read out of band, so project_dir is optional there;
        # the in-memory backend still requires it (source_code mode is not supported for Python).
        if project_dir is None and not isinstance(self.backend_config, Neo4jConnectionConfig):
            raise ValueError("project_dir is required; source_code mode is not supported for Python.")
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.treesitter_python: TreesitterPython = TreesitterPython()
        self.backend: PythonAnalysisBackend
        if isinstance(self.backend_config, Neo4jConnectionConfig):
            # Read-only: the graph is populated out of band; the SDK only polls it.
            cfg = self.backend_config
            application_name = cfg.application_name or (Path(project_dir).name if project_dir else None)
            self.backend = PyNeo4jBackend(
                neo4j_uri=cfg.uri,
                neo4j_username=cfg.username,
                neo4j_password=cfg.password,
                neo4j_database=cfg.database,
                application_name=application_name,
            )
        else:
            cfg = self.backend_config
            cache_path = cache_subdir(cfg.cache_dir, project_dir, "python")
            if cache_path is not None:
                cache_path.mkdir(parents=True, exist_ok=True)
            self.backend = PyCodeanalyzer(
                project_dir=project_dir,
                analysis_level=analysis_level,
                analysis_json_path=None,
                eager_analysis=eager_analysis,
                cache_dir=cache_path,
                target_files=target_files,
                use_ray=getattr(cfg, "use_ray", False),
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

    def get_symbol_table(self, *, paths: Sequence[str] | None = None) -> Dict[str, PyModule]:
        """Return the symbol table mapping file paths to module objects.

        Returns a dictionary that maps each analyzed file's path to its
        corresponding :class:`PyModule` object. This is useful for looking
        up module information when you know the file path.

        Args:
            paths: Restrict the result to these modules, named by symbol-table key (the module's
                file path). Absolute paths and native separators are accepted; a path naming no
                module raises rather than contributing nothing. ``None`` (the default) returns the
                whole application — on a large graph that is thousands of modules, so prefer naming
                the ones you need.

        Returns:
            A dictionary where keys are file paths (as strings) and values are
            :class:`~cldk.models.python.PyModule` objects containing the
            analyzed structure of each file, including classes, functions,
            imports, and other symbols.

        Raises:
            TypeError: ``paths`` is a bare string. It takes a *sequence* of paths — a string is a
                sequence of characters, and iterating it is never what you meant.
            ValueError: ``paths`` is an empty sequence. Omit the keyword to enumerate everything;
                the argument that means "the whole application" is the argument not passed.
            SelectorNotInGraph: a path names no module in this application
                (``cldk.utils.exceptions``, a ``ValueError``). A partial miss raises too, so a
                short result can never be read as a complete one.

        See Also:
            :meth:`get_python_module`: For direct lookup by file path.
            :meth:`get_modules`: For a flat list without file paths.
        """
        return self.backend.get_symbol_table(paths=paths)

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
    def get_call_graph(self, *, roots: Sequence[str] | None = None, depth: int | None = None) -> nx.DiGraph:
        """Return the project call graph as a NetworkX directed graph.

        Constructs and returns a directed graph representing method/function
        call relationships across the entire project. Each node represents
        a callable (function or method), and each edge represents a call
        from one callable to another.

        Args:
            roots: Restrict the result to the sub-graph reachable from these callables, named by
                signature. ``None`` (the default) returns the whole application's call graph.
            depth: Maximum number of call hops from a root, an ``int`` >= 1; ``None`` is
                unbounded. Requires ``roots``.

        The unscoped graph on a real application runs to hundreds of thousands of edges, which is
        not an answer to a question about one function — ``roots=`` and ``depth=`` are how you ask
        the question you actually have. The result is the *induced* sub-graph over the reached
        nodes, so an edge between two nodes you can see is never silently absent, and a root that
        calls nothing is a graph of one node rather than an empty one. A root the graph does not
        hold raises :class:`~cldk.utils.exceptions.SelectorNotInGraph` instead of quietly
        contributing nothing.

        The call graph is built using:
            - Jedi for semantic call resolution
            - PyCG for inter-procedural call-graph construction

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes represent callables (functions/methods) with attributes
                  containing callable metadata
                - Edges represent call relationships, directed from caller to callee
                - Edge attributes may include call site information

        Note:
            The completeness of the call graph depends on the analysis backend
            (Jedi plus PyCG in codeanalyzer-python 0.3.0).

        See Also:
            :meth:`get_callers`: For finding callers of a specific method.
            :meth:`get_callees`: For finding callees of a specific method.
            :meth:`get_class_call_graph`: For call graph subset by class.
        """
        return self.backend.get_call_graph(roots=roots, depth=depth)

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

    def get_callables_overview(self) -> List[PyCallableOverview]:
        """Return a lightweight overview of every callable in the project, in one bulk read.

        A field-projected alternative to :meth:`get_methods` for enumeration: each
        :class:`~cldk.models.python.PyCallableOverview` carries the callable's signature, owning
        class (if any), kind, location, and decorators — but not the full reconstruction (call
        sites, inner callables, locals). On the Neo4j backend this is a single Cypher query instead
        of the per-entity fan-out :meth:`get_methods` pays. Body-inspect the few you need afterwards
        via :meth:`get_method` or :meth:`get_method_bodies`.

        Returns:
            A flat list of :class:`~cldk.models.python.PyCallableOverview`, one per callable
            (methods, module-level functions, and nested functions).

        See Also:
            :meth:`get_decorated_callables`: The same projection filtered by decorator.
            :meth:`get_method_bodies`: Bulk source-body fetch for chosen signatures.
        """
        return self.backend.get_callables_overview()

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Return source bodies for the given callable signatures, in one bulk read.

        Args:
            signatures: Callable signatures to fetch bodies for (e.g. from
                :meth:`get_callables_overview`).

        Returns:
            A dict mapping each signature to its source body. Signatures with no matching callable
            are omitted, as are callables whose ``code`` is ``None`` — every returned value is a
            real ``str``.
        """
        return self.backend.get_method_bodies(signatures)

    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        """Return overviews of callables decorated with any of the given markers, in one bulk read.

        Args:
            markers: Decorator names to match (e.g. ``["staticmethod", "app.route"]``).

        Returns:
            A list of :class:`~cldk.models.python.PyCallableOverview` for every callable carrying at
            least one of ``markers`` as a decorator.

        See Also:
            :meth:`get_callables_overview`: The unfiltered projection.
        """
        return self.backend.get_decorated_callables(markers)

    def get_entrypoints(self) -> List[PyCallableOverview]:
        """Return overviews of every callable the analyzer marked as an entrypoint, in one bulk read.

        The analyzer's own entrypoint-detection pass already finds route handlers, CLI commands,
        and other externally-invoked callables (``PyCallable.is_entrypoint``); this just surfaces
        that mark instead of making a caller rediscover it (e.g. by sharding
        :meth:`get_callables_overview` across workers to guess which callables are reachable from
        outside the application).

        Returns:
            A list of :class:`~cldk.models.python.PyCallableOverview` for every entrypoint
            callable. Empty means the project genuinely has none, not that the graph lacks the mark.

        See Also:
            :meth:`get_callables_overview`: The unfiltered projection.
            :meth:`get_decorated_callables`: The same projection filtered by decorator instead.
            :meth:`get_entrypoint_classes`: The class-level sibling this walk never sees.
            :meth:`get_entrypoint_coverage`: Whether the detection pass itself had gaps.
        """
        return self.backend.get_entrypoints()

    def get_entrypoint_classes(self) -> List[PyClassOverview]:
        """Return overviews of every class the analyzer marked as an entrypoint in its own right,
        in one bulk read.

        :meth:`get_entrypoints` walks callables only, so a class-based view (a Django/Flask CBV,
        say) marked ``is_entrypoint`` at the class with no individually-marked method is invisible
        to it. This is that sibling.

        Returns:
            A list of :class:`~cldk.models.python.PyClassOverview` for every entrypoint class.
            Empty means the project genuinely has none, not that the graph lacks the mark.

        See Also:
            :meth:`get_entrypoints`: The callable-level projection.
        """
        return self.backend.get_entrypoint_classes()

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Return the entrypoint-detection pass's own coverage/failure record, in one bulk read.

        The analyzer's detection pass "under-approximates by design, so silence is its failure
        mode" (its own ``PyEntrypointReport`` docstring); :meth:`get_entrypoints` returning ``[]``
        cannot, on its own, distinguish "ran clean, found none" from "had gaps". This can.

        Returns:
            An :class:`~cldk.analysis.commons.results.EntrypointCoverage`. Non-empty
            ``diagnostics`` means this backend cannot supply the report at all (the Neo4j
            projection does not carry it) rather than the pass having run clean — see the model's
            own docstring for the field-by-field contract.

        See Also:
            :meth:`get_entrypoints`: The accessor whose empty result this disambiguates.
        """
        return self.backend.get_entrypoint_coverage()

    @property
    def has_resolution_edges(self) -> bool:
        """Whether :meth:`get_callsites_for` can resolve call sites on this backend right now.

        ``False`` is only possible on the Neo4j backend, and only when the attached graph has no
        ``PY_RESOLVES_TO`` edge anywhere (populated at an analysis level below the one where the
        defuse-linker backfill runs) — in that case every ``callee_signature=None`` from
        :meth:`get_callsites_for` is explained by the graph's analysis level, not by individual
        call sites failing to resolve. The local backend is always ``True`` here: it attempts
        Jedi resolution on every call site regardless of analysis level.

        See Also:
            :meth:`get_callsites_for`: The accessor whose ``None`` this disambiguates.
        """
        return self.backend.has_resolution_edges

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        """Return the call sites of the given callables, keyed by signature, in one bulk read.

        Avoids the per-callable reconstruction fan-out when you need call sites for a specific
        frontier (e.g. dispatch-edge synthesis or external-reader detection).

        Args:
            signatures: Callable signatures to fetch call sites for.

        Returns:
            A dict mapping each existing signature to its list of
            :class:`~cldk.models.python.PyCallsite` (empty if the callable has no call sites).
            Signatures with no matching callable are omitted.

        See Also:
            :attr:`has_resolution_edges`: Distinguishes a genuinely unresolved call site from a
                graph with no resolution data at all.
        """
        return self.backend.get_callsites_for(signatures)

    def get_external_symbols(self) -> Dict[str, PyExternalSymbol]:
        """Every call-graph endpoint outside the analyzed project (an imported library or builtin
        member), keyed by its ``can://…/@external/…`` id.

        The analyzer mints one of these ghost symbols for every call target that isn't a declared
        class/callable, so no call-graph edge dangles; :meth:`get_callsites_for`'s resolved
        ``callee_signature`` for an external target is exactly this dict's key.

        Returns:
            A dict mapping each ``@external`` can-id to its
            :class:`~cldk.models.python.PyExternalSymbol`. Empty means this project's call graph
            makes no calls outside itself.
        """
        return self.backend.get_external_symbols()

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

        Note:
            Returned callables' call sites are not resolved the way :meth:`get_callsites_for`
            resolves them: on the Neo4j backend ``callee_signature`` is always ``None`` here; on
            the local backend an external target keeps Jedi's raw, unaddressable dotted guess
            instead of the resolved ``@external`` can-id. Use :meth:`get_callsites_for` for the
            same call sites with resolved signatures.

        See Also:
            :meth:`get_method`: For a single method by name.
            :meth:`get_constructors`: For ``__init__`` methods specifically.
        """
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_method(
        self, qualified_class_name: str, qualified_method_name: str
    ) -> PyCallable | None:
        """Return a specific method or module-level function by scope and name.

        Retrieves detailed information about a single method, including
        its signature, parameters, return type, decorators, and body.

        ``qualified_class_name`` is looked up the same way as
        :meth:`get_all_methods_in_application`'s outer keys: a class signature resolves to that
        class's methods, and a module name (``PyModule.module_name``) resolves to that module's
        top-level functions.

        Args:
            qualified_class_name: The fully qualified name of the class
                containing the method (e.g., ``"mypackage.models.User"``), or a module name for
                module-level functions.
            qualified_method_name: The name of the method to retrieve
                (e.g., ``"save"`` or ``"__init__"``).

        Returns:
            A :class:`~cldk.models.python.PyCallable` object containing
            all analyzed information about the method, or ``None`` if
            neither a matching class nor a matching module resolves.

        Note:
            The returned callable's call sites are not resolved the way :meth:`get_callsites_for`
            resolves them: on the Neo4j backend ``callee_signature`` is always ``None`` here; on
            the local backend an external target keeps Jedi's raw, unaddressable dotted guess
            instead of the resolved ``@external`` can-id. Use :meth:`get_callsites_for` for the
            same call sites with resolved signatures.

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

        Note:
            Returned callables' call sites are not resolved the way :meth:`get_callsites_for`
            resolves them: on the Neo4j backend ``callee_signature`` is always ``None`` here; on
            the local backend an external target keeps Jedi's raw, unaddressable dotted guess
            instead of the resolved ``@external`` can-id. Use :meth:`get_callsites_for` for the
            same call sites with resolved signatures.

        See Also:
            :meth:`get_method`: For any method by name.
            :meth:`get_methods_in_class`: For all methods including constructors.
        """
        return self.backend.get_all_constructors(qualified_class_name)

    # -----[ locate ]-----
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        The single most-needed query for triaging a scanner alert: an alert arrives as
        ``file:line`` and this resolves it to the enclosing callable in one call, rather than
        ``get_method``, falling back to ``get_callers``, falling back to scanning the symbol table
        by hand. Four outcomes stay distinguishable — see
        :class:`~cldk.analysis.commons.results.LocateResult`: inside a callable (``callable`` set,
        plus ``node`` when a body node is that precise), at real module scope (``module_scope``
        diagnostic), in the gap between two callables (also module scope, never snapped to the
        nearest callable), or in a file the graph has no module for (``file_not_in_graph``).

        There is no ``col`` parameter. Column-level disambiguation would have to be honoured by
        both backends to mean anything, and the Neo4j graph projects only ``start_line`` /
        ``end_line`` on ``:PyCallable`` and ``:PyBodyNode`` — so a ``col`` would work in-process
        and be silently ignored over Neo4j. Better absent than documented and inert.

        Args:
            path: The file path. Normalised against the backend's module keys, so a
                ``./``-prefixed or absolute path resolves rather than reading back as
                ``file_not_in_graph``.
            line: The 1-based line number.

        Returns:
            A :class:`~cldk.analysis.commons.results.LocateResult` carrying the innermost body
            node, the enclosing callable, its owning type, its module, and the source slice —
            never an ambiguous empty.

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

    # -----[ source access ]-----
    def get_source(self, node_id: str) -> str:
        """Return the source text named by ``node_id`` — a callable, or one of its body nodes.

        Generalises :meth:`get_method_bodies` below callable granularity: ``node_id`` is either a
        callable's signature, or the opaque body-node id
        :attr:`~cldk.analysis.commons.results.LocateResult.node_id` hands back alongside
        :attr:`~cldk.analysis.commons.results.LocateResult.node`, so a statement or call site
        :meth:`locate` found can be re-fetched precisely, not just the callable enclosing it.

        Args:
            node_id: A callable signature, or a body-node id from :meth:`locate` — passed back as
                received, not composed.

        Returns:
            The source text, never an ambiguous empty string.

        Raises:
            KeyError: No callable/body node matches ``node_id``, or it has no recoverable source
                (no span).
            NotImplementedError: (Neo4j backend only) ``node_id`` names a body node — the attached
                graph carries no source text below callable granularity.

        See Also:
            :meth:`get_method_bodies`: The bulk, callable-only, omit-if-absent form.
            :meth:`locate`: The usual way to obtain a ``node_id`` in the first place.
        """
        return self.backend.get_source(node_id)

    # -----[ per-callable graphs ]-----
    def get_cfg(self, callable: str, *, in_class: str | None = None) -> List[CfgEdge]:
        """Return the control flow edges inside one callable.

        Bounded by construction: the domain is that one callable's own body nodes, so there is no
        depth argument and no cap to get wrong. Finite is not the same as small — the largest CFG
        measured on a real application is 402 edges, but its DDG is over a million (see
        :meth:`get_ddg`).

        ``src`` and ``dst`` are body-node ids in the same vocabulary
        :attr:`~cldk.analysis.commons.results.LocateResult.node_id` uses, so an endpoint can be
        handed straight back to :meth:`get_source`. No ``can://`` URI and no ordinal appears in
        either the argument or the result.

        Args:
            callable: The callable's name — resolved the way :meth:`locate` and the addressing
                layer resolve names, so ``"charge"`` is enough when it is unique and an ambiguous
                name raises listing the candidates instead of being guessed at.
            in_class: Narrow to the class this names, when the bare name is ambiguous.

        Returns:
            A list of :class:`~cldk.models.python.CfgEdge`, each carrying the edge ``kind``
            (``"true"``/``"false"`` on a conditional, ``"exception"``, ``"loop_back"``, ...). It
            is a set, not a sequence: no order is implied.

        Raises:
            AmbiguousName: ``callable`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
            CodeanalyzerUsageException: This analysis was built below
                ``analysis_level="program_dependency_graph"``, where the analyzer emits no control
                or data flow at all — reported rather than returned as a misleading empty list.

        See Also:
            :meth:`get_cdg`, :meth:`get_ddg`: The other two graphs of the same callable.
        """
        return self.backend.get_cfg(callable, in_class=in_class)

    def get_cdg(self, callable: str, *, in_class: str | None = None) -> List[CdgEdge]:
        """Return the control dependence edges inside one callable.

        ``src`` is the branch a ``dst`` is control dependent on — "this statement runs only
        because that test went this way" — computed by the analyzer over the CFG :meth:`get_cfg`
        returns.

        Args:
            callable: The callable's name, resolved as in :meth:`get_cfg`.
            in_class: Narrow to the class this names.

        Returns:
            A list of :class:`~cldk.models.python.CdgEdge`, in no particular order.

        Raises:
            AmbiguousName: ``callable`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
            CodeanalyzerUsageException: Analysis level below ``program_dependency_graph``.
        """
        return self.backend.get_cdg(callable, in_class=in_class)

    def get_ddg(self, callable: str, *, in_class: str | None = None) -> List[DdgEdge]:
        """Return the data dependence edges inside one callable.

        Every edge names the variable that flows (``var``) and the evidence for it (``prov``), so
        a caller separates syntactic dependence from alias-aware dependence without asking a
        second question. ``prov`` is one of ``"ssa"``, ``"reaching-defs"`` or ``"points-to"``;
        ``"points-to"`` is the alias-derived delta that only a level-4 analysis carries, so a
        level-3 answer is narrower rather than wrong.

        The same statement pair appears more than once when it carries several variables or
        several kinds of evidence — that is the point, not duplication.

        This is the one accessor here whose per-callable result can be large: the maximum measured
        on a real application is 1,386,918 edges for a single callable (96% of them
        ``reaching-defs``). It is still bounded by construction and still uncapped — a caller who
        cannot afford the whole dependence graph of one callable wants a slice of it.

        Args:
            callable: The callable's name, resolved as in :meth:`get_cfg`.
            in_class: Narrow to the class this names.

        Returns:
            A list of :class:`~cldk.models.python.DdgEdge`, in no particular order. An empty list
            from a level-3-or-deeper analysis is an honest answer: this callable has no data
            dependence.

        Raises:
            AmbiguousName: ``callable`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
            CodeanalyzerUsageException: Analysis level below ``program_dependency_graph``, where an
                empty list could not be told apart from the honest empty above.
        """
        return self.backend.get_ddg(callable, in_class=in_class)

    # -----[ repository artifacts ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Return every non-code project artifact (manifest, config file, lockfile, ...), keyed by
        its repo-relative path.

        This layer (``Artifact``/``ConfigKey``/``Package`` nodes, ``HAS_ARTIFACT``/
        ``DECLARES_DEPENDENCY``/``DEFINES_CONFIG``/``LOCKS`` edges) is the one part of the graph
        every ``codeanalyzer-<lang>`` projects identically and unprefixed.

        See Also:
            :meth:`get_dependencies`, :meth:`get_config_keys`, :meth:`get_config_uses`.
        """
        return self.backend.get_artifacts()

    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        """Return every declared third-party dependency, one entry per declaring manifest,
        optionally filtered.

        All three filters default to "don't filter" — a pure widening, so existing calls are
        unaffected.

        Args:
            direct_only: When ``True``, excludes lockfile-only transitive pins.
            ecosystem: When given, only dependencies from this package ecosystem (e.g. ``"pypi"``).
            declared_in: When given, only dependencies declared by this artifact id (see
                :meth:`get_artifacts`).
        """
        return self.backend.get_dependencies(direct_only=direct_only, ecosystem=ecosystem, declared_in=declared_in)

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Return every configuration key flattened out of a config-bearing artifact, keyed by its
        id (``<artifact-id>@key/<dotted.key>``) — a bare ``key`` (e.g. ``"DB_URL"``) is not unique
        across artifacts/namespaces, so the id is the dict key."""
        return self.backend.get_config_keys()

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Return resolved code-to-config edges: which body node reads which config key.

        Args:
            key: When given, only edges whose target :class:`PyConfigKey` has this bare ``key``
                (e.g. ``"DB_URL"``) — matched against :meth:`get_config_keys`, since
                :class:`PyConfigUseEdge` itself carries only ``src``/``dst``/``prov``, not the key
                text. ``None`` (default) returns every edge.

        See Also:
            :meth:`get_config_readers`: The same edges, resolved to their reading callables.
            :meth:`get_unresolved_config_reads`: The reads this can't show — a match the detector
                found but never closed on a declared key.
        """
        return self.backend.get_config_uses(key)

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Return every detector-matched config read that never closed on exactly one declared
        key, in one bulk read.

        :meth:`get_config_uses` (and :meth:`get_config_readers`) can only show reads that
        *resolved*; a call the detector matched but couldn't pin to a key (a dynamic key
        expression, or a key with no matching declaration) is otherwise invisible — an empty
        :meth:`get_config_uses` for some key cannot then distinguish "nothing reads this" from "a
        read exists but the analyzer couldn't resolve it." This is that missing signal.

        Returns:
            A list of :class:`~cldk.models.python.PyConfigRead`, each naming *why* resolution
            failed (``reason="non-literal"`` or ``"undefined-key"``). Over the Neo4j backend,
            ``site`` always comes back ``""`` and several call sites sharing the same
            ``(callee, key, reason)`` may collapse into one entry — the graph doesn't carry the
            call site on this edge (see :meth:`~cldk.analysis.python.neo4j.PyNeo4jBackend.get_unresolved_config_reads`'s
            comment) — but "no unresolved reads" here is never a false negative.
        """
        return self.backend.get_unresolved_config_reads()

    def get_config_readers(self, key: str) -> List[PyCallableOverview]:
        """Return overviews of every callable reading configuration key ``key``, in one bulk read.

        :meth:`get_config_uses` hands back ``PyConfigUseEdge.src``/``dst`` as opaque ordinal ids —
        answering "which callable reads this" otherwise means parsing
        ``codeanalyzer-python``'s id grammar yourself. This does that resolution for you.

        Args:
            key: The bare configuration key (e.g. ``"DB_URL"``), matched the same way
                :meth:`get_config_uses` matches it.

        Returns:
            A list of :class:`~cldk.models.python.PyCallableOverview`, one per distinct reading
            callable. Empty means no callable reads this key — see :meth:`get_unresolved_config_reads`
            if you need to rule out "a read exists but never resolved" too.
        """
        return self.backend.get_config_readers(key)

    # -----[ classes ]-----
    def get_classes(self, *, module: str | None = None) -> Dict[str, PyClass]:
        """Return all classes in the project.

        Retrieves all class definitions discovered during analysis, organized
        by their fully qualified names. This includes regular classes,
        dataclasses, abstract base classes, and nested classes.

        Args:
            module: Restrict the result to one module's classes, named by symbol-table key (the
                module's file path — *not* a dotted module name, so it reads the same way as
                :meth:`get_symbol_table`'s ``paths``). A key naming no module raises. ``None``
                (the default) returns every class in the application.

        Returns:
            A dictionary mapping fully qualified class names (strings) to
            :class:`~cldk.models.python.PyClass` objects containing class
            metadata, methods, attributes, and inheritance information.

        Raises:
            SelectorNotInGraph: ``module`` names no module in this application
                (``cldk.utils.exceptions``, a ``ValueError``). A mistyped key used to return the
                same ``{}`` as a module that genuinely declares no classes.

        See Also:
            :meth:`get_class`: For a single class by name.
            :meth:`get_classes_by_criteria`: For filtered class retrieval.
        """
        return self.backend.get_all_classes(module=module)

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
            so all base types are returned here.

        See Also:
            :meth:`get_sub_classes`: For finding classes that extend this class.
        """
        return self.backend.get_extended_classes(qualified_class_name)

