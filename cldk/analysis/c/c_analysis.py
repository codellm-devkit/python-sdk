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

"""C analysis utilities.

Provides a high-level API to analyze C projects using a Clang-based analyzer
and to query functions, macros, typedefs, structs/unions, enums, and globals.
"""

from pathlib import Path
from typing import Dict, List, Optional
import networkx as nx

from cldk.analysis.c.clang import ClangAnalyzer
from cldk.models.c import CApplication, CFunction, CTranslationUnit, CMacro, CTypedef, CStruct, CEnum, CVariable


class CAnalysis:

    def __init__(self, project_dir: Path) -> None:
        """Initialize the C analysis backend.

        Args:
            project_dir (Path): Path to the C project directory.
        """
        if not isinstance(project_dir, Path):
            project_dir = Path(project_dir)
        self.c_application = self._init_application(project_dir)

    def _init_application(self, project_dir: Path) -> CApplication:
        """Construct the C application model from project sources.

        Args:
            project_dir (Path): Path to the project directory.

        Returns:
            CApplication: Application model.

        Examples:
            Build an application model from a project directory:

            >>> from pathlib import Path
            >>> ca = CAnalysis(project_dir=Path('.'))  # doctest: +SKIP
            >>> isinstance(ca.get_c_application(), CApplication)  # doctest: +SKIP
            True
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
        """Return the C application object.

        Returns:
            CApplication: Application model.

        Examples:
            >>> # Assuming CAnalysis was constructed
            >>> isinstance(CAnalysis(project_dir=Path('.')).get_c_application(), CApplication)  # doctest: +SKIP
            True
        """
        return self.c_application

    def get_imports(self) -> List[str]:
        """Return all include/import statements in the project.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> from pathlib import Path
            >>> CAnalysis(project_dir=Path('.')).get_imports()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_variables(self, **kwargs):
        """Return all variables discovered across the project.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> from pathlib import Path
            >>> CAnalysis(project_dir=Path('.')).get_variables()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_application_view(self) -> CApplication:
        """Return the application view of the C project.

        Returns:
            CApplication: Application model summarizing translation units.

        Examples:
            >>> from pathlib import Path
            >>> ca = CAnalysis(project_dir=Path('.'))  # doctest: +SKIP
            >>> isinstance(ca.get_application_view(), CApplication)  # doctest: +SKIP
            True
        """
        return self.c_application

    def get_symbol_table(self) -> Dict[str, CTranslationUnit]:
        """Return a symbol table view keyed by file path.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> from pathlib import Path
            >>> CAnalysis(project_dir=Path('.')).get_symbol_table()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_compilation_units(self) -> List[CTranslationUnit]:
        """Return all compilation units parsed from C sources.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> from pathlib import Path
            >>> CAnalysis(project_dir=Path('.')).get_compilation_units()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def is_parsable(self, source_code: str) -> bool:
        """Check if the source code is parsable using Clang.

        Args:
            source_code (str): Source code to parse.

        Returns:
            bool: True if parsable, False otherwise.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).is_parsable('int f(){return 1;}')  # doctest: +SKIP
            True
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph(self) -> nx.DiGraph:
        """Return the call graph of the C code.

        Returns:
            networkx.DiGraph: Call graph.

        Examples:
            >>> isinstance(CAnalysis(project_dir=Path('.')).get_call_graph(), nx.DiGraph)  # doctest: +SKIP
            True
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph_json(self) -> str:
        """Return the call graph serialized as JSON.

        Returns:
            str: Call graph encoded as JSON.

        Raises:
            NotImplementedError: Single-file mode unsupported.

        Examples:
            >>> isinstance(CAnalysis(project_dir=Path('.')).get_call_graph_json(), str)  # doctest: +SKIP
            True
        """

        raise NotImplementedError("Producing a call graph over a single file is not implemented yet.")

    def get_callers(self, function: CFunction) -> Dict:
        """Return callers of a function.

        Args:
            function (CFunction): Target function.

        Returns:
            dict: Mapping of callers to call sites/details.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_callers(CFunction(name='f', return_type='int', parameters=[], storage_class=None, is_inline=False, is_variadic=False, body='', comment='', call_sites=[], local_variables=[], start_line=1, end_line=1))  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Generating all callers over a single file is not implemented yet.
        """

        raise NotImplementedError("Generating all callers over a single file is not implemented yet.")

    def get_callees(self, function: CFunction) -> Dict:
        """Return callees of a function.

        Args:
            function (CFunction): Source function.

        Returns:
            dict: Callee details.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_callees(CFunction(name='f', return_type='int', parameters=[], storage_class=None, is_inline=False, is_variadic=False, body='', comment='', call_sites=[], local_variables=[], start_line=1, end_line=1))  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Generating all callees over a single file is not implemented yet.
        """
        raise NotImplementedError("Generating all callees over a single file is not implemented yet.")

    def get_functions(self) -> Dict[str, CFunction]:
        """Return all functions in the project.

        Returns:
            dict[str, CFunction]: Functions keyed by signature/name for a translation unit.

        Examples:
            >>> funcs = CAnalysis(project_dir=Path('.')).get_functions()  # doctest: +SKIP
            >>> isinstance(funcs, dict)  # doctest: +SKIP
            True
        """
        for _, translation_unit in self.c_application.translation_units.items():
            return translation_unit.functions

    def get_function(self, function_name: str, file_name: Optional[str]) -> CFunction | List[CFunction]:
        """Return a function object.

        Args:
            function_name (str): Function name.
            file_name (str | None): Optional file name.

        Returns:
            CFunction | list[CFunction]: Function object(s) matching the query.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_function('main', None)  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_file(self, file_name: str) -> str:
        """Return a C file path by name.

        Args:
            file_name (str): File name.

        Returns:
            str: C source file path.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_C_file('hello.c')  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_compilation_unit(self, file_path: str) -> CTranslationUnit:
        """Return the compilation unit for a C source file.

        Args:
            file_path (str): Absolute path to a C source file.

        Returns:
            CTranslationUnit: Compilation unit object.

        Examples:
            >>> # Retrieve a compilation unit by path
            >>> cu = CAnalysis(project_dir=Path('.')).get_C_compilation_unit('file.c')  # doctest: +SKIP
            >>> (cu is None) or hasattr(cu, 'functions')  # doctest: +SKIP
            True
        """
        return self.c_application.translation_units.get(file_path)

    def get_functions_in_file(self, file_name: str) -> List[CFunction]:
        """Return all functions in a given file.

        Args:
            file_name (str): File name.

        Returns:
            list[CFunction]: Functions in the file.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_functions_in_file('file.c')  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros(self) -> List[CMacro]:
        """Return all macros in the project.

        Returns:
            list[CMacro]: All macros.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_macros()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros_in_file(self, file_name: str) -> List[CMacro] | None:
        """Return all macros in the given file.

        Args:
            file_name (str): File name.

        Returns:
            list[CMacro] | None: Macros in the file, or None if not found.

        Raises:
            NotImplementedError: Not implemented yet.

        Examples:
            >>> CAnalysis(project_dir=Path('.')).get_macros_in_file('file.c')  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")


def get_includes(self) -> List[str]:
    """Return all include statements across the project.

    Returns:
        list[str]: All include statements.
    """
    all_includes = []
    for translation_unit in self.translation_units.values():
        all_includes.extend(translation_unit.includes)
    return all_includes


def get_includes_in_file(self, file_name: str) -> List[str] | None:
    """Return include statements in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[str] | None: Includes in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].includes
    return None


def get_macros(self) -> List[CMacro]:
    """Return all macro definitions across the project.

    Returns:
        list[CMacro]: Macro definitions.
    """
    all_macros = []
    for translation_unit in self.translation_units.values():
        all_macros.extend(translation_unit.macros)
    return all_macros


def get_macros_in_file(self, file_name: str) -> List[CMacro] | None:
    """Return macro definitions in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[CMacro] | None: Macros in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].macros
    return None


def get_typedefs(self) -> List[CTypedef]:
    """Return typedef declarations across the project.

    Returns:
        list[CTypedef]: Typedef declarations.
    """
    all_typedefs = []
    for translation_unit in self.translation_units.values():
        all_typedefs.extend(translation_unit.typedefs)
    return all_typedefs


def get_typedefs_in_file(self, file_name: str) -> List[CTypedef] | None:
    """Return typedef declarations in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[CTypedef] | None: Typedefs in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].typedefs
    return None


def get_structs(self) -> List[CStruct]:
    """Return struct/union declarations across the project.

    Returns:
        list[CStruct]: Struct/union declarations.
    """
    all_structs = []
    for translation_unit in self.translation_units.values():
        all_structs.extend(translation_unit.structs)
    return all_structs


def get_structs_in_file(self, file_name: str) -> List[CStruct] | None:
    """Return struct/union declarations in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[CStruct] | None: Structs in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].structs
    return None


def get_enums(self) -> List[CEnum]:
    """Return enum declarations across the project.

    Returns:
        list[CEnum]: Enum declarations.
    """
    all_enums = []
    for translation_unit in self.translation_units.values():
        all_enums.extend(translation_unit.enums)
    return all_enums


def get_enums_in_file(self, file_name: str) -> List[CEnum] | None:
    """Return enum declarations in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[CEnum] | None: Enums in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].enums
    return None


def get_globals(self, file_name: str) -> List[CVariable] | None:
    """Return global variable declarations in a file.

    Args:
        file_name (str): File name.

    Returns:
        list[CVariable] | None: Globals in the file, or None if not found.
    """
    if file_name in self.translation_units:
        return self.translation_units[file_name].globals
    return None
