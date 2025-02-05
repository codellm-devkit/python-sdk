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
Analysis model for C projects
"""

from pathlib import Path
from typing import Dict, List, Optional

from networkx import DiGraph

from cldk.analysis.c.clang import ClangAnalyzer
from cldk.models.c import CApplication, CFunction, CTranslationUnit, CMacro, CTypedef, CStruct, CEnum, CVariable


class CAnalysis:

    def __init__(self, project_dir: Path) -> None:
        """Initialization method for C Analysis backend."""
        if not isinstance(project_dir, Path):
            project_dir = Path(project_dir)
        self.c_application = self._init_application(project_dir)

    def _init_application(self, project_dir: Path) -> CApplication:
        """Initializes the C application object.

        Args:
            project_dir (Path): Path to the project directory.

        Returns:
            CApplication: C application object.
        """
        analyzer = ClangAnalyzer()

        # Analyze each file
        translation_units = {}
        for source_file in project_dir.rglob("*.c"):
            tu = analyzer.analyze_file(source_file)
            translation_units[str(source_file)] = tu

        # Create application model
        return CApplication(translation_units=translation_units)

    def get_c_application(self) -> CApplication:
        """Returns the C application object.

        Returns:
            CApplication: C application object.
        """
        return self.c_application

    def get_imports(self) -> List[str]:
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_variables(self, **kwargs):
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_application_view(self) -> CApplication:
        return self.c_application

    def get_symbol_table(self) -> Dict[str, CTranslationUnit]:
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_compilation_units(self) -> List[CTranslationUnit]:
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def is_parsable(self, source_code: str) -> bool:
        """
        Check if the code is parsable using clang parser.
        Args:
            source_code: source code

        Returns:
            True if the code is parsable, False otherwise
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph(self) -> DiGraph:
        """Returns the call graph of the C code.

        Returns:
            DiGraph: The call graph of the C code.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph_json(self) -> str:
        """Returns a serialized call graph in json.

        Raises:
            NotImplementedError: Raised when this functionality is not suported.

        Returns:
            str: Call graph in json.
        """

        raise NotImplementedError("Producing a call graph over a single file is not implemented yet.")

    def get_callers(self, function: CFunction) -> Dict:
        """Returns a dictionary of callers of the target method.

        Args:
            function (CFunction): A CFunction object.

        Raises:
            NotImplementedError: Raised when this functionality is not suported.

        Returns:
            Dict: A dictionary of callers of target function.
        """

        raise NotImplementedError("Generating all callers over a single file is not implemented yet.")

    def get_callees(self, function: CFunction) -> Dict:
        """Returns a dictionary of callees in a fuction.

        Args:
            function (CFunction): A CFunction object.

        Raises:
            NotImplementedError: Raised when this functionality is not suported.

        Returns:
            Dict: Dictionary with callee details.
        """
        raise NotImplementedError("Generating all callees over a single file is not implemented yet.")

    def get_functions(self) -> Dict[str, CFunction]:
        """Returns all functions in the project.

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            Dict[str, Dict[str, JCallable]]: Dictionary of dictionaries of all methods in the C code with qualified class name as key and dictionary of methods in that class.
        """
        for _, translation_unit in self.c_application.translation_units.items():
            return translation_unit.functions

    def get_function(self, function_name: str, file_name: Optional[str]) -> CFunction | List[CFunction]:
        """Returns a function object given the function name.

        Args:
            function_name (str): The name of the function.
            file_name (str): The name of the file containing the function.

        Returns:
            CFunction: A method for the given qualified method name. If multiple functions with the same name exist, a list of functions is returned.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_file(self, file_name: str) -> str:
        """Returns a class given qualified class name.

        Args:
            file_name (str): The name of the file.

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            str: C file name containing the given qualified class.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_compilation_unit(self, file_path: str) -> CTranslationUnit:
        """Given the path of a C source file, returns the compilation unit object from the symbol table.

        Args:
            file_path (str): Absolute path to C source file

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            CTranslationUnit: Compilation unit object for C source file
        """
        return self.c_application.translation_units.get(file_path)

    def get_functions_in_file(self, file_name: str) -> List[CFunction]:
        """Returns a dictionary of all methods of the given class.

        Args:
            file_name (str): The name of the file.

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            Dict[str, JCallable]: A dictionary of all constructors of the given class.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros(self) -> List[CMacro]:
        """Returns a list of all macros in the C code.

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            List[CMacro]: A list of all macros in the C code.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros_in_file(self, file_name: str) -> List[CMacro] | None:
        """Returns a list of all macros in the given file.

        Args:
            file_name (str): The name of the file.

        Raises:
            NotImplementedError: Raised when current AnalysisEngine does not support this function.

        Returns:
            List[CMacro]: A list of all macros in the given file. Returns None if no macros are found.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")


def get_includes(self) -> List[str]:
    """Returns a list of all include statements across all files in the C code.

    Returns:
        List[str]: A list of all include statements. Returns empty list if none found.
    """
    all_includes = []
    for translation_unit in self.translation_units.values():
        all_includes.extend(translation_unit.includes)
    return all_includes


def get_includes_in_file(self, file_name: str) -> List[str] | None:
    """Returns a list of all include statements in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[str] | None: List of includes in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].includes
    return None


def get_macros(self) -> List[CMacro]:
    """Returns a list of all macro definitions across all files in the C code.

    Returns:
        List[CMacro]: A list of all macro definitions. Returns empty list if none found.
    """
    all_macros = []
    for translation_unit in self.translation_units.values():
        all_macros.extend(translation_unit.macros)
    return all_macros


def get_macros_in_file(self, file_name: str) -> List[CMacro] | None:
    """Returns a list of all macro definitions in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[CMacro] | None: List of macros in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].macros
    return None


def get_typedefs(self) -> List[CTypedef]:
    """Returns a list of all typedef declarations across all files in the C code.

    Returns:
        List[CTypedef]: A list of all typedef declarations. Returns empty list if none found.
    """
    all_typedefs = []
    for translation_unit in self.translation_units.values():
        all_typedefs.extend(translation_unit.typedefs)
    return all_typedefs


def get_typedefs_in_file(self, file_name: str) -> List[CTypedef] | None:
    """Returns a list of all typedef declarations in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[CTypedef] | None: List of typedefs in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].typedefs
    return None


def get_structs(self) -> List[CStruct]:
    """Returns a list of all struct/union declarations across all files in the C code.

    Returns:
        List[CStruct]: A list of all struct/union declarations. Returns empty list if none found.
    """
    all_structs = []
    for translation_unit in self.translation_units.values():
        all_structs.extend(translation_unit.structs)
    return all_structs


def get_structs_in_file(self, file_name: str) -> List[CStruct] | None:
    """Returns a list of all struct/union declarations in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[CStruct] | None: List of structs in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].structs
    return None


def get_enums(self) -> List[CEnum]:
    """Returns a list of all enum declarations across all files in the C code.

    Returns:
        List[CEnum]: A list of all enum declarations. Returns empty list if none found.
    """
    all_enums = []
    for translation_unit in self.translation_units.values():
        all_enums.extend(translation_unit.enums)
    return all_enums


def get_enums_in_file(self, file_name: str) -> List[CEnum] | None:
    """Returns a list of all enum declarations in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[CEnum] | None: List of enums in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].enums
    return None


def get_globals(self, file_name: str) -> List[CVariable] | None:
    """Returns a list of all global variable declarations in the given file.

    Args:
        file_name (str): The name of the file to search in.

    Returns:
        List[CVariable] | None: List of globals in the file, or None if file not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].globals
    return None
