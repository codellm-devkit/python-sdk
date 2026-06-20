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

"""Core CLDK module.

This module provides the top-level entry point for the Code Language Development
Kit (CLDK), a unified framework for performing static analysis across multiple
programming languages. The primary interface is the :class:`CLDK` class, which
serves as a factory for creating language-specific analysis objects, tree-sitter
parsers, and sanitization utilities.

The CLDK supports the following languages:
    - **Java**: Full static analysis via CodeAnalyzer backend, including symbol
      tables, call graphs, and code metrics.
    - **Python**: Static analysis via codeanalyzer-python backend with optional
      CodeQL-augmented call graph resolution.
    - **C**: Basic analysis via libclang for parsing and extracting code structure.

Typical usage involves instantiating :class:`CLDK` with a target language, then
calling :meth:`CLDK.analysis` to obtain a language-specific analysis facade.

Note:
    This module requires language-specific backends to be available:
    - Java: ``codeanalyzer-*.jar`` (auto-downloaded or specified via path)
    - Python: ``codeanalyzer-python`` (auto-installed in virtualenv)
    - C: ``libclang`` (must be installed on the system)
"""

from pathlib import Path

import logging
from typing import List

from cldk.analysis import AnalysisLevel
from cldk.analysis.c import CAnalysis
from cldk.analysis.java import JavaAnalysis
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.analysis.typescript import TypeScriptAnalysis
from cldk.analysis.typescript.neo4j import Neo4jConnectionConfig
from cldk.utils.exceptions import CldkInitializationException
from cldk.utils.sanitization.java import TreesitterSanitizer

logger = logging.getLogger(__name__)


class CLDK:
    """Core class for the Code Language Development Kit (CLDK).

    The CLDK class serves as the primary entry point and factory for all code
    analysis operations. It provides a unified interface for initializing
    language-specific analysis facades, tree-sitter parsers, and code
    sanitization utilities.

    This class follows the factory pattern, where the ``language`` parameter
    determines which concrete analysis implementation is returned by the
    :meth:`analysis`, :meth:`treesitter_parser`, and :meth:`tree_sitter_utils`
    methods.

    Args:
        language: The target programming language for analysis. Supported values
            are ``"java"``, ``"python"``, and ``"c"`` (case-sensitive).

    Attributes:
        language (str): The programming language specified during initialization.
            This determines which analysis backend and utilities are used.

    Raises:
        NotImplementedError: Raised by factory methods when the specified
            language is not yet supported.

    See Also:
        - :class:`~cldk.analysis.java.JavaAnalysis`: Java-specific analysis facade.
        - :class:`~cldk.analysis.python.PythonAnalysis`: Python-specific analysis facade.
        - :class:`~cldk.analysis.c.CAnalysis`: C-specific analysis facade.
    """

    def __init__(self, language: str) -> None:
        """Initialize the CLDK instance with a target programming language.

        Args:
            language: The programming language to use for analysis. Must be one
                of the supported languages: ``"java"``, ``"python"``, or ``"c"``.
                The language string is case-sensitive.
        """
        self.language: str = language

    def analysis(
        self,
        project_path: str | Path | None = None,
        source_code: str | None = None,
        eager: bool = False,
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        analysis_backend_path: str | None = None,
        analysis_json_path: str | Path = None,
        cache_dir: str | Path | None = None,
        use_codeql: bool = True,
        use_ray: bool = False,
        neo4j_config: "Neo4jConnectionConfig | None" = None,
    ) -> JavaAnalysis | PythonAnalysis | CAnalysis | TypeScriptAnalysis:
        """Initialize and return a language-specific analysis facade.

        This factory method creates an appropriate analysis object based on the
        language specified during CLDK initialization. The analysis facade provides
        methods for extracting code structure, call graphs, symbol tables, and
        other static analysis artifacts.

        The method supports two modes of operation:

        1. **Project mode**: Analyze an entire project directory by providing
           ``project_path``. This is the recommended mode for comprehensive
           analysis.
        2. **Source code mode** (Java only): Analyze a single source code string
           by providing ``source_code``. Useful for quick analysis of code
           snippets.

        Args:
            project_path: Absolute or relative path to the project directory
                to analyze. The directory should contain source files in the
                target language. Mutually exclusive with ``source_code``.
            source_code: Raw source code string to analyze (Java only). Useful
                for analyzing code snippets without a project structure.
                Mutually exclusive with ``project_path``. Not supported for
                Python or C languages.
            eager: If ``True``, forces regeneration of all analysis caches and
                databases, ignoring any previously cached results. Defaults to
                ``False`` for incremental analysis performance.
            analysis_level: The depth of analysis to perform. Controls which
                analysis artifacts are generated. See :class:`~cldk.analysis.AnalysisLevel`
                for available options. Defaults to ``AnalysisLevel.symbol_table``.
            target_files: Optional list of specific file paths (relative to
                ``project_path``) to analyze. When provided, only these files
                are included in the analysis, improving performance for large
                projects. Defaults to ``None`` (analyze all files).
            analysis_backend_path: **Java only.** Path to the directory containing
                the ``codeanalyzer-*.jar`` backend executable. If not provided,
                the JAR is automatically downloaded. Not valid for Python
                analysis; use ``cache_dir`` instead.
            analysis_json_path: Path where the analysis database (typically
                ``analysis.json``) should be persisted. Useful for caching
                analysis results between sessions. If not provided, a default
                location within the project is used.
            cache_dir: **Python only.** Directory path for the codeanalyzer-python
                backend's cache, including its virtualenv, CodeQL database, and
                ``analysis_cache.json``. When omitted, defaults to
                ``<project_path>/.codeanalyzer``. Ignored for Java and C.
            use_codeql: **Python only.** If ``True`` (default), augments
                Jedi-based call graph resolution with CodeQL analysis for more
                complete call edges. Set to ``False`` for faster analysis using
                only Jedi. Ignored for Java and C.
            use_ray: **Python only.** If ``True``, enables Ray-based parallel
                processing for analysis. Recommended for very large projects
                where sequential Jedi/CodeQL analysis would be slow. Requires
                Ray to be installed. Defaults to ``False``. Ignored for Java
                and C.

        Returns:
            A language-specific analysis facade instance:
                - :class:`~cldk.analysis.java.JavaAnalysis` for Java projects
                - :class:`~cldk.analysis.python.PythonAnalysis` for Python projects
                - :class:`~cldk.analysis.c.CAnalysis` for C projects

        Raises:
            CldkInitializationException: Raised in the following cases:
                - Neither ``project_path`` nor ``source_code`` is provided.
                - Both ``project_path`` and ``source_code`` are provided.
                - ``source_code`` is provided for Python analysis (not supported).
                - ``analysis_backend_path`` is provided for Python analysis
                  (use ``cache_dir`` instead).
            NotImplementedError: If the language specified during CLDK
                initialization is not supported.

        Note:
            The analysis process may download or build backend tools on first
            run, which can take additional time. Subsequent runs use cached
            backends for faster startup.

        See Also:
            - :class:`~cldk.analysis.AnalysisLevel`: Available analysis depth options.
            - :class:`~cldk.analysis.java.JavaAnalysis`: Java analysis methods.
            - :class:`~cldk.analysis.python.PythonAnalysis`: Python analysis methods.
        """

        if project_path is None and source_code is None:
            raise CldkInitializationException("Either project_path or source_code must be provided.")

        if project_path is not None and source_code is not None:
            raise CldkInitializationException("Both project_path and source_code are provided. Please provide " "only one.")

        # Normalize project_path: expand ~ and resolve to absolute path
        if project_path is not None:
            project_path = Path(project_path).expanduser().resolve()
            if not project_path.is_dir():
                raise CldkInitializationException(f"project_path does not exist or is not a directory: {project_path}")

        if self.language == "java":
            return JavaAnalysis(
                project_dir=project_path,
                source_code=source_code,
                analysis_level=analysis_level,
                analysis_backend_path=analysis_backend_path,
                analysis_json_path=analysis_json_path,
                target_files=target_files,
                eager_analysis=eager,
            )
        elif self.language == "python":
            if source_code is not None:
                raise CldkInitializationException("source_code mode is not supported for Python; please pass project_path.")
            if analysis_backend_path is not None:
                raise CldkInitializationException(
                    "analysis_backend_path is Java-only (it locates codeanalyzer-*.jar). " "For Python, use cache_dir for the backend's virtualenv/CodeQL cache."
                )
            return PythonAnalysis(
                project_dir=project_path,
                analysis_level=analysis_level,
                cache_dir=cache_dir,
                analysis_json_path=analysis_json_path,
                target_files=target_files,
                eager_analysis=eager,
                use_codeql=use_codeql,
                use_ray=use_ray,
            )
        elif self.language == "c":
            return CAnalysis(project_dir=project_path)
        elif self.language == "typescript":
            if source_code is not None:
                raise CldkInitializationException("source_code mode is not supported for TypeScript; please pass project_path.")
            if cache_dir is not None or use_ray:
                raise CldkInitializationException(
                    "cache_dir and use_ray are Python-only. For TypeScript, use analysis_backend_path "
                    "to locate the codeanalyzer-typescript binary (or set $CODEANALYZER_TS_BIN)."
                )
            return TypeScriptAnalysis(
                project_dir=project_path,
                analysis_level=analysis_level,
                analysis_backend_path=analysis_backend_path,
                analysis_json_path=analysis_json_path,
                target_files=target_files,
                eager_analysis=eager,
                neo4j_config=neo4j_config,
            )
        else:
            raise NotImplementedError(f"Analysis support for {self.language} is not implemented yet.")

    def treesitter_parser(self) -> TreesitterJava:
        """Return a Tree-sitter parser for the selected language.

        Creates and returns a language-specific Tree-sitter parser instance
        that can be used for syntactic analysis, AST traversal, and code
        querying operations. Tree-sitter provides incremental parsing with
        excellent performance characteristics for real-time code analysis.

        The returned parser provides methods for:
            - Parsing source code into an AST
            - Running Tree-sitter queries to extract code patterns
            - Extracting syntactic elements (methods, classes, imports, etc.)
            - Performing lexical analysis

        Returns:
            TreesitterJava: A Tree-sitter parser wrapper for Java source code.
                The parser provides methods such as :meth:`is_parsable`,
                :meth:`get_raw_ast`, :meth:`get_all_imports`, and various
                code extraction utilities.

        Raises:
            NotImplementedError: If the language specified during CLDK
                initialization does not have a Tree-sitter parser implementation.
                Currently, only Java is supported.

        Note:
            The Tree-sitter parser operates at the syntactic level only and
            does not perform semantic analysis. For semantic information like
            resolved types or call graphs, use :meth:`analysis` instead.

        See Also:
            - :class:`~cldk.analysis.commons.treesitter.TreesitterJava`:
              Java Tree-sitter parser implementation.
        """
        if self.language == "java":
            return TreesitterJava()
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")

    def tree_sitter_utils(self, source_code: str) -> TreesitterSanitizer:
        """Return Tree-sitter-based code sanitization utilities for the selected language.

        Creates and returns a utility class that provides code transformation
        and sanitization operations using Tree-sitter for parsing. These utilities
        are particularly useful for preparing code for LLM consumption, test
        generation, and code analysis tasks.

        The sanitization utilities provide operations such as:
            - Removing unused imports from source code
            - Keeping only focal methods and their callees for context reduction
            - Extracting and manipulating test assertions
            - Identifying and removing dead code

        Args:
            source_code: The source code string to initialize the utilities with.
                This code will be parsed and made available for transformation
                operations. Must be valid syntax for the target language.

        Returns:
            TreesitterSanitizer: A utility wrapper that provides sanitization
                and transformation methods for Java source code, including:
                - :meth:`~TreesitterSanitizer.keep_only_focal_method_and_its_callees`
                - :meth:`~TreesitterSanitizer.remove_unused_imports`

        Raises:
            NotImplementedError: If the language specified during CLDK
                initialization does not have sanitization utilities implemented.
                Currently, only Java is supported.

        Note:
            The sanitization utilities modify code at the syntactic level using
            Tree-sitter patterns. For complex refactoring that requires semantic
            understanding, consider using the full analysis capabilities via
            :meth:`analysis`.

        See Also:
            - :class:`~cldk.utils.sanitization.java.TreesitterSanitizer`:
              Java sanitization utility implementation.
            - :meth:`treesitter_parser`: For raw Tree-sitter parsing without
              sanitization utilities.
        """
        if self.language == "java":
            return TreesitterSanitizer(source_code=source_code)
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")
