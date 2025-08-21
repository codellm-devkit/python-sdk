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

Provides the top-level CLDK entry point used to initialize language-specific
analysis, Treesitter parsers, and related utilities.
"""

from pathlib import Path

import logging
from typing import List

from cldk.analysis import AnalysisLevel
from cldk.analysis.c import CAnalysis
from cldk.analysis.java import JavaAnalysis
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.utils.exceptions import CldkInitializationException
from cldk.utils.sanitization.java import TreesitterSanitizer

logger = logging.getLogger(__name__)


class CLDK:
    """Core class for the Code Language Development Kit (CLDK).

    Initialize with the desired programming language and use the exposed
    helpers to perform language-specific analysis.

    Args:
        language (str): Programming language (e.g., "java", "python", "c").

    Attributes:
        language (str): Programming language of the project.
    """

    def __init__(self, language: str):
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
    ) -> JavaAnalysis | PythonAnalysis | CAnalysis:
        """Initialize a language-specific analysis façade.

        Args:
            project_path (str | Path | None): Directory path of the project.
            source_code (str | None): Source code for single-file analysis.
            eager (bool): If True, forces regeneration of analysis databases.
            analysis_level (str): Analysis level. See AnalysisLevel.
            target_files (list[str] | None): Files to constrain analysis (optional).
            analysis_backend_path (str | None): Path to the analysis backend.
            analysis_json_path (str | Path | None): Path to persist analysis database.

        Returns:
            JavaAnalysis | PythonAnalysis | CAnalysis: Initialized analysis façade for the chosen language.

        Raises:
            CldkInitializationException: If both or neither of project_path and source_code are provided.
            NotImplementedError: If the specified language is unsupported.

        Examples:
            Initialize Python analysis with inline source code and verify type:

            >>> from cldk import CLDK
            >>> cldk = CLDK(language="python")
            >>> analysis = cldk.analysis(source_code='def f(): return 1')
            >>> from cldk.analysis.python import PythonAnalysis
            >>> isinstance(analysis, PythonAnalysis)
            True
        """

        if project_path is None and source_code is None:
            raise CldkInitializationException("Either project_path or source_code must be provided.")

        if project_path is not None and source_code is not None:
            raise CldkInitializationException("Both project_path and source_code are provided. Please provide " "only one.")

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
            return PythonAnalysis(
                project_dir=project_path,
                source_code=source_code,
            )
        elif self.language == "c":
            return CAnalysis(project_dir=project_path)
        else:
            raise NotImplementedError(f"Analysis support for {self.language} is not implemented yet.")

    def treesitter_parser(self):
        """Return a Treesitter parser for the selected language.

        Returns:
            TreesitterJava: Parser for Java language.

        Raises:
            NotImplementedError: If the language is unsupported.

        Examples:
            Get a Java Treesitter parser:

            >>> from cldk import CLDK
            >>> parser = CLDK(language="java").treesitter_parser()
            >>> parser.__class__.__name__
            'TreesitterJava'
        """
        if self.language == "java":
            return TreesitterJava()
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")

    def tree_sitter_utils(self, source_code: str) -> [TreesitterSanitizer | NotImplementedError]:  # type: ignore
        """Return Treesitter utilities for the selected language.

        Args:
            source_code (str): Source code to initialize the utilities with.

        Returns:
            TreesitterSanitizer: Utility wrapper for Java Treesitter operations.

        Raises:
            NotImplementedError: If the language is unsupported.

        Examples:
            Create Java Treesitter sanitizer utilities:

            >>> from cldk import CLDK
            >>> utils = CLDK(language="java").tree_sitter_utils('class A {}')
            >>> from cldk.utils.sanitization.java import TreesitterSanitizer
            >>> isinstance(utils, TreesitterSanitizer)
            True
        """
        if self.language == "java":
            return TreesitterSanitizer(source_code=source_code)
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")
