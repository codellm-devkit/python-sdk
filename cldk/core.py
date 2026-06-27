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
import warnings
from typing import List

from cldk.analysis import AnalysisLevel
from cldk.analysis.c import CAnalysis
from cldk.analysis.java import JavaAnalysis
from cldk.analysis.commons.backend_config import (
    CodeAnalyzerConfig,
    JavaBackend,
    Neo4jConnectionConfig,
    PyBackend,
    PyCodeAnalyzerConfig,
    TSBackend,
    TSCodeAnalyzerConfig,
)
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.analysis.typescript import TypeScriptAnalysis
from cldk.utils.exceptions import CldkInitializationException
from cldk.utils.sanitization.java import TreesitterSanitizer

logger = logging.getLogger(__name__)


def _normalize_project_path(project_path: str | Path | None) -> Path | None:
    """Expand and resolve a project path, validating it is a directory.

    Returns ``None`` unchanged (the Neo4j backends read their graph out of band, so a project
    directory is optional there).
    """
    if project_path is None:
        return None
    resolved = Path(project_path).expanduser().resolve()
    if not resolved.is_dir():
        raise CldkInitializationException(f"project_path does not exist or is not a directory: {resolved}")
    return resolved


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

    # -----[ per-language factory methods (preferred entry points) ]-----
    @staticmethod
    def java(
        project_path: str | Path | None = None,
        source_code: str | None = None,
        *,
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        eager: bool = False,
        backend: JavaBackend | None = None,
    ) -> JavaAnalysis:
        """Create a Java analysis facade.

        Args:
            project_path: Path to the Java project directory.
            source_code: Single Java source string (deprecated; pass ``project_path`` instead).
            analysis_level: Analysis depth (see :class:`~cldk.analysis.AnalysisLevel`).
            target_files: Restrict analysis to these files.
            eager: Force regeneration of cached analysis.
            backend: Backend configuration. Defaults to :class:`CodeAnalyzerConfig`.

        Raises:
            CldkInitializationException: If neither or both of ``project_path`` / ``source_code``
                are provided.
        """
        # The read-only Neo4j backend reads a graph populated out of band, so it needs neither
        # project_path nor source_code.
        is_neo4j = isinstance(backend, Neo4jConnectionConfig)
        if project_path is None and source_code is None and not is_neo4j:
            raise CldkInitializationException("Either project_path or source_code must be provided.")
        if project_path is not None and source_code is not None:
            raise CldkInitializationException("Both project_path and source_code are provided. Please provide only one.")
        if source_code is not None:
            warnings.warn(
                "Passing source_code for Java analysis is deprecated and will be removed in a "
                "future release; provide project_path instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return JavaAnalysis(
            project_dir=_normalize_project_path(project_path),
            source_code=source_code,
            analysis_level=analysis_level,
            target_files=target_files,
            eager_analysis=eager,
            backend=backend,
        )

    @staticmethod
    def python(
        project_path: str | Path | None = None,
        *,
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        eager: bool = False,
        backend: PyBackend | None = None,
    ) -> PythonAnalysis:
        """Create a Python analysis facade.

        Args:
            project_path: Path to the Python project directory. Optional only when ``backend`` is a
                :class:`Neo4jConnectionConfig` (the graph is populated out of band).
            analysis_level: Analysis depth (see :class:`~cldk.analysis.AnalysisLevel`).
            target_files: Restrict analysis to these files.
            eager: Force regeneration of cached analysis.
            backend: Backend configuration. Defaults to :class:`PyCodeAnalyzerConfig`;
                pass a :class:`Neo4jConnectionConfig` to use the read-only Neo4j backend.
        """
        return PythonAnalysis(
            project_dir=_normalize_project_path(project_path),
            analysis_level=analysis_level,
            target_files=target_files,
            eager_analysis=eager,
            backend=backend,
        )

    @staticmethod
    def typescript(
        project_path: str | Path | None = None,
        *,
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        eager: bool = False,
        backend: TSBackend | None = None,
    ) -> TypeScriptAnalysis:
        """Create a TypeScript analysis facade.

        Args:
            project_path: Path to the TypeScript project directory. Optional only when ``backend``
                is a :class:`Neo4jConnectionConfig` (the graph is populated out of band).
            analysis_level: Analysis depth (see :class:`~cldk.analysis.AnalysisLevel`).
            target_files: Restrict analysis to these files.
            eager: Force regeneration of cached analysis.
            backend: Backend configuration. Defaults to :class:`CodeAnalyzerConfig`; pass a
                :class:`TSCodeAnalyzerConfig` to set TypeScript-only knobs such as ``tsc_only``
                (passes ``--tsc-only``), or a :class:`Neo4jConnectionConfig` to use the read-only
                Neo4j backend.
        """
        return TypeScriptAnalysis(
            project_dir=_normalize_project_path(project_path),
            analysis_level=analysis_level,
            target_files=target_files,
            eager_analysis=eager,
            backend=backend,
        )

    @staticmethod
    def c(project_path: str | Path) -> CAnalysis:
        """Create a C analysis facade for the given project directory."""
        return CAnalysis(project_dir=_normalize_project_path(project_path))

    def analysis(
        self,
        project_path: str | Path | None = None,
        source_code: str | None = None,
        eager: bool = False,
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        analysis_backend_path: str | None = None,
        analysis_json_path: str | Path | None = None,
        cache_dir: str | Path | None = None,
        use_codeql: bool = True,
        use_ray: bool = False,
        neo4j_config: "Neo4jConnectionConfig | None" = None,
    ) -> JavaAnalysis | PythonAnalysis | CAnalysis | TypeScriptAnalysis:
        """Deprecated entry point. Use the per-language factory methods instead.

        ``CLDK(language).analysis(...)`` is retained as a thin compatibility shim that forwards to
        :meth:`java` / :meth:`python` / :meth:`typescript` / :meth:`c` with an appropriate
        ``backend=`` configuration object.

        The former ``analysis_json_path`` is folded into the unified ``cache_dir`` (it is used as
        the cache root when ``cache_dir`` is not given). ``analysis_backend_path`` is no longer
        supported: the backend binary ships with the packaged dependency, and passing it is ignored.

        .. deprecated::
            Use :meth:`CLDK.java`, :meth:`CLDK.python`, :meth:`CLDK.typescript`, or :meth:`CLDK.c`
            with a ``backend=<config>`` object.
        """
        warnings.warn(
            "CLDK(language).analysis(...) is deprecated; use the per-language factory methods "
            "CLDK.java()/CLDK.python()/CLDK.typescript()/CLDK.c() with a backend=<config> object.",
            DeprecationWarning,
            stacklevel=2,
        )
        if analysis_backend_path is not None:
            warnings.warn(
                "analysis_backend_path is no longer supported and is ignored; the backend binary "
                "ships with the packaged codeanalyzer-* dependency.",
                DeprecationWarning,
                stacklevel=2,
            )
        if project_path is None and source_code is None and neo4j_config is None:
            raise CldkInitializationException("Either project_path or source_code must be provided.")
        # The former analysis_json_path now folds into the unified cache_dir.
        cache_root = cache_dir if cache_dir is not None else analysis_json_path

        if self.language == "java":
            return CLDK.java(
                project_path=project_path,
                source_code=source_code,
                analysis_level=analysis_level,
                target_files=target_files,
                eager=eager,
                backend=CodeAnalyzerConfig(cache_dir=cache_root),
            )
        elif self.language == "python":
            if source_code is not None:
                raise CldkInitializationException("source_code mode is not supported for Python; please pass project_path.")
            backend = neo4j_config if neo4j_config is not None else PyCodeAnalyzerConfig(cache_dir=cache_root, use_codeql=use_codeql, use_ray=use_ray)
            return CLDK.python(
                project_path=project_path,
                analysis_level=analysis_level,
                target_files=target_files,
                eager=eager,
                backend=backend,
            )
        elif self.language == "typescript":
            if source_code is not None:
                raise CldkInitializationException("source_code mode is not supported for TypeScript; please pass project_path.")
            backend = neo4j_config if neo4j_config is not None else CodeAnalyzerConfig(cache_dir=cache_root)
            return CLDK.typescript(
                project_path=project_path,
                analysis_level=analysis_level,
                target_files=target_files,
                eager=eager,
                backend=backend,
            )
        elif self.language == "c":
            return CLDK.c(project_path)
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
