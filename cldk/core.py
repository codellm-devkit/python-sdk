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

"""
Core module
"""

from pathlib import Path

import logging
from typing import List

from cldk.analysis import AnalysisLevel
from cldk.analysis.c import CAnalysis
from cldk.analysis.java import JavaAnalysis
from cldk.analysis.java.treesitter import JavaSitter
from cldk.utils.exceptions import CldkInitializationException
from cldk.utils.sanitization.java import TreesitterSanitizer

logger = logging.getLogger(__name__)


class CLDK:
    """
    The CLDK base class.

    Parameters
    ----------
    language : str
        The programming language of the project.

    Attributes
    ----------
    language : str
        The programming language of the project.
    """

    def __init__(self, language: str):
        self.language: str = language

    def analysis(
        self,
        project_path: str | Path | None = None,
        source_code: str | None = None,
        eager: bool = False,
        analysis_backend: str | None = "codeanalyzer",
        analysis_level: str = AnalysisLevel.symbol_table,
        target_files: List[str] | None = None,
        analysis_backend_path: str | None = None,
        analysis_json_path: str | Path = None,
        use_graalvm_binary: bool = False,
    ) -> JavaAnalysis:
        """
        Initialize the preprocessor based on the specified language and analysis_backend.

        Parameters
        ----------
        project_path : str or Path
            The directory path of the project.
        source_code : str, optional
            The source code of the project, defaults to None. If None, it is assumed that the whole project is being
            analyzed.
        analysis_backend : str, optional
            The analysis_backend used for analysis, defaults to "codeql".
        analysis_backend_path : str, optional
            The path to the analysis_backend, defaults to None and in the case of codeql, it is assumed that the cli is
            installed and available in the PATH. In the case of codeanalyzer the codeanalyzer.jar is downloaded from the
            lastest release.
        analysis_json_path : str or Path, optional
            The path save the to the analysis database (analysis.json), defaults to None. If None, the analysis database
            is not persisted.
        use_graalvm_binary : bool, optional
            A flag indicating whether to use the GraalVM binary for SDG analysis, defaults to False. If False,
            the default Java binary is used and one needs to have Java 17 or higher installed.
        eager : bool, optional
            A flag indicating whether to perform eager analysis, defaults to False. If True, the analysis is performed
            eagerly. That is, the analysis.json file is created during analysis every time even if it already exists.
        analysis_level: str, optional
            Analysis levels. Refer to AnalysisLevel.
        target_files: List[str] | None, optional
            The target files (paths) for which the analysis will run or get modified. Currently, this feature only supported
            with symbol table analysis. In the future, we will add this feature to other analysis levels.
        Returns
        -------
        JavaAnalysis
            The initialized JavaAnalysis object.

        Raises
        ------
        CldkInitializationException
            If neither project_path nor source_code is provided.
        NotImplementedError
            If the specified language is not implemented yet.

        Args:
            analysis_level:
            target_files:
            analysis_level:
        """

        if project_path is None and source_code is None:
            raise CldkInitializationException("Either project_path or source_code must be provided.")

        if project_path is not None and source_code is not None:
            raise CldkInitializationException("Both project_path and source_code are provided. Please provide " "only one.")

        if self.language == "java":
            return JavaAnalysis(
                project_dir=project_path,
                source_code=source_code,
                analysis_backend=analysis_backend,
                analysis_level=analysis_level,
                analysis_backend_path=analysis_backend_path,
                analysis_json_path=analysis_json_path,
                use_graalvm_binary=use_graalvm_binary,
                target_files=target_files,
                eager_analysis=eager,
            )
        elif self.language == "c":
            return CAnalysis(project_dir=project_path)
        else:
            raise NotImplementedError(f"Analysis support for {self.language} is not implemented yet.")

    def treesitter_parser(self):
        """
        Parse the project using treesitter.

        Returns
        -------
        List
            A list of treesitter nodes.

        """
        if self.language == "java":
            return JavaSitter()
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")

    def tree_sitter_utils(self, source_code: str) -> [TreesitterSanitizer | NotImplementedError]:  # type: ignore
        """
        Parse the project using treesitter.

        Parameters
        ----------
        source_code : str, optional
            The source code of the project, defaults to None. If None, it is assumed that the whole project is being
            analyzed.

        Returns
        -------
        List
            A list of treesitter nodes.

        """
        if self.language == "java":
            return TreesitterSanitizer(source_code=source_code)
        else:
            raise NotImplementedError(f"Treesitter parser for {self.language} is not implemented yet.")
