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

"""C analysis facade module.

This module provides the :class:`CAnalysis` class, which serves as the primary
interface for performing static analysis on C projects. It uses libclang (via
the ClangAnalyzer backend) to parse C source files and extract code structure.

The analysis extracts:
    - **Functions**: Function definitions with signatures, bodies, and call sites
    - **Macros**: Preprocessor macro definitions
    - **Typedefs**: Type alias definitions
    - **Structs/Unions**: Structure and union type definitions
    - **Enums**: Enumeration type definitions
    - **Global Variables**: File-scope variable declarations

Key features:
    - Parse all ``.c`` files in a project directory recursively
    - Build a unified application model with translation units
    - Query functions, types, and other code elements

Note:
    This module requires libclang to be installed on the system. The location
    of the libclang library is auto-detected on common platforms.

See Also:
    - :class:`~cldk.analysis.c.clang.ClangAnalyzer`: Backend implementation.
    - :class:`~cldk.models.c.CApplication`: Application model.
"""

from pathlib import Path
from typing import Dict, List, Optional
import networkx as nx

from cldk.analysis.c.clang import ClangAnalyzer
from cldk.models.c import CApplication, CFunction, CTranslationUnit, CMacro, CTypedef, CStruct, CEnum, CVariable


class CAnalysis:
    """Analysis facade for C projects.

    This class provides a high-level interface for performing static analysis
    on C projects using libclang as the parsing backend. It recursively
    analyzes all ``.c`` files in a project directory and builds a unified
    application model.

    The facade provides access to:
        - **Functions**: Function definitions, signatures, and call sites
        - **Macros**: Preprocessor macro definitions
        - **Typedefs**: Type alias definitions
        - **Structs/Unions**: Structure and union type definitions with fields
        - **Enums**: Enumeration type definitions with values
        - **Globals**: File-scope variable declarations

    Attributes:
        c_application (CApplication): The analyzed application model containing
            all translation units and their contents.

    Note:
        Analysis is performed during initialization. For large projects,
        this may take significant time as each file is parsed by libclang.

    See Also:
        - :class:`~cldk.analysis.c.clang.ClangAnalyzer`: Backend implementation.
        - :class:`~cldk.models.c.CApplication`: Application model.
    """

    def __init__(self, project_dir: Path) -> None:
        """Initialize the C analysis facade and analyze the project.

        Creates a new analysis facade for a C project. All ``.c`` files in
        the project directory (recursively) are parsed during initialization.

        Args:
            project_dir: Path to the C project directory. Can be a
                :class:`pathlib.Path` object or a string path. The directory
                should contain ``.c`` source files.

        Note:
            Analysis is performed synchronously during initialization.
            All ``.c`` files are recursively discovered and parsed.
        """
        if not isinstance(project_dir, Path):
            project_dir = Path(project_dir)
        self.c_application = self._init_application(project_dir)

    def _init_application(self, project_dir: Path) -> CApplication:
        """Construct the C application model from project sources.

        Recursively discovers all ``.c`` files in the project directory
        and parses each using the ClangAnalyzer backend.

        Args:
            project_dir: Path to the project directory to analyze.

        Returns:
            A :class:`~cldk.models.c.CApplication` object containing all
            analyzed translation units with their functions, macros,
            typedefs, structs, enums, and global variables.

        Note:
            This is an internal method called during initialization.
            Users should access the result via :meth:`get_c_application`.
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
        """Return the complete analyzed C application model.

        Returns the top-level :class:`CApplication` object containing all
        analyzed translation units and their contents.

        Returns:
            A :class:`~cldk.models.c.CApplication` object containing:
                - ``translation_units``: Dictionary mapping file paths to
                  :class:`~cldk.models.c.CTranslationUnit` objects

        See Also:
            :meth:`get_application_view`: Alias for this method.
        """
        return self.c_application

    def get_imports(self) -> List[str]:
        """Return all include statements in the project.

        This method is intended to extract all ``#include`` directives from
        the analyzed C source files.

        Returns:
            Would return a list of include statement strings.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_c_application`: For direct access to translation units
                which may contain include information.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_variables(self, **kwargs) -> Dict:
        """Return all variables discovered across the project.

        This method is intended to extract variable declarations from the
        analyzed code, including local variables and function parameters.

        Args:
            **kwargs: Implementation-specific filtering options.

        Returns:
            Would return an implementation-defined view of variables.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_functions`: For accessing function definitions which
                contain local variable information.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_application_view(self) -> CApplication:
        """Return the application view of the C project.

        This is an alias for :meth:`get_c_application` for API consistency
        with other language facades.

        Returns:
            A :class:`~cldk.models.c.CApplication` object containing all
            analyzed translation units.

        See Also:
            :meth:`get_c_application`: Primary method for application access.
        """
        return self.c_application

    def get_symbol_table(self) -> Dict[str, CTranslationUnit]:
        """Return the symbol table mapping file paths to translation units.

        This method is intended to return a dictionary structure similar to
        other language facades for consistent API access.

        Returns:
            Would return a dictionary mapping file paths to
            :class:`~cldk.models.c.CTranslationUnit` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_c_application`: For direct access to translation units.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_compilation_units(self) -> List[CTranslationUnit]:
        """Return all translation units as a list.

        This method is intended to return all parsed translation units
        (one per source file) as a flat list.

        Returns:
            Would return a list of :class:`~cldk.models.c.CTranslationUnit`
            objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_c_application`: For access to translation units via
                the ``translation_units`` dictionary.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def is_parsable(self, source_code: str) -> bool:
        """Check if the given source code is valid C syntax.

        This method is intended to validate C source code by attempting
        to parse it with libclang.

        Args:
            source_code: A string containing C source code to validate.

        Returns:
            Would return ``True`` if parsable, ``False`` otherwise.

        Raises:
            NotImplementedError: This functionality is not yet implemented.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph(self) -> nx.DiGraph:
        """Return the project call graph as a NetworkX directed graph.

        This method is intended to construct a call graph representing
        function call relationships across the entire C project.

        Returns:
            Would return a ``networkx.DiGraph`` representing function calls.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_callers`: For finding callers of a specific function.
            :meth:`get_callees`: For finding callees of a specific function.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_graph_json(self) -> str:
        """Return the call graph serialized as JSON.

        This method is intended to serialize the call graph to JSON format
        for persistence or external tool consumption.

        Returns:
            Would return a JSON-formatted string.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_call_graph`: For the graph object directly.
        """
        raise NotImplementedError("Producing a call graph over a single file is not implemented yet.")

    def get_callers(self, function: CFunction) -> Dict:
        """Return all functions that call the specified function.

        This method is intended to find all call sites where the target
        function is invoked.

        Args:
            function: The target :class:`~cldk.models.c.CFunction` to find
                callers for.

        Returns:
            Would return a dictionary of caller information.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_callees`: For finding functions called by a function.
            :meth:`get_call_graph`: For the complete call graph.
        """
        raise NotImplementedError("Generating all callers over a single file is not implemented yet.")

    def get_callees(self, function: CFunction) -> Dict:
        """Return all functions called by the specified function.

        This method is intended to find all functions invoked within the
        body of the source function.

        Args:
            function: The source :class:`~cldk.models.c.CFunction` to find
                callees for.

        Returns:
            Would return a dictionary of callee information.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_callers`: For finding callers of a function.
            :meth:`get_call_graph`: For the complete call graph.
        """
        raise NotImplementedError("Generating all callees over a single file is not implemented yet.")

    def get_functions(self) -> Dict[str, CFunction]:
        """Return all functions in the project.

        Retrieves all function definitions from all translation units,
        returning the functions from the first translation unit found.

        Returns:
            A dictionary mapping function names to
            :class:`~cldk.models.c.CFunction` objects. Each function
            contains its signature, body, parameters, and call sites.

        Note:
            Currently returns functions from only the first translation
            unit. This behavior may change in future versions.
        """
        for _, translation_unit in self.c_application.translation_units.items():
            return translation_unit.functions

    def get_function(self, function_name: str, file_name: Optional[str] = None) -> CFunction | List[CFunction]:
        """Return a specific function by name.

        This method is intended to look up a function by its name,
        optionally constrained to a specific file.

        Args:
            function_name: The name of the function to find.
            file_name: Optional file name to constrain the search. If
                ``None``, searches all files.

        Returns:
            Would return a :class:`~cldk.models.c.CFunction` object or
            list of functions if multiple matches exist.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_functions`: For all functions in the project.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_file(self, file_name: str) -> str:
        """Return the full path to a C file by name.

        This method is intended to resolve a file name to its full path
        within the analyzed project.

        Args:
            file_name: The name of the C file to find (e.g., ``"main.c"``).

        Returns:
            Would return the full path to the file.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_C_compilation_unit`: For direct access by path.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_C_compilation_unit(self, file_path: str) -> CTranslationUnit:
        """Return the translation unit for a specific file path.

        Retrieves the :class:`CTranslationUnit` object corresponding to
        a specific C source file.

        Args:
            file_path: The path to the C file (as it appears in the
                translation units dictionary).

        Returns:
            The :class:`~cldk.models.c.CTranslationUnit` for the file,
            or ``None`` if the file is not found.

        See Also:
            :meth:`get_c_application`: For access to all translation units.
        """
        return self.c_application.translation_units.get(file_path)

    def get_functions_in_file(self, file_name: str) -> List[CFunction]:
        """Return all functions defined in a specific file.

        This method is intended to retrieve function definitions from
        a single source file.

        Args:
            file_name: The name or path of the C file.

        Returns:
            Would return a list of :class:`~cldk.models.c.CFunction` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_C_compilation_unit`: For access to file contents.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros(self) -> List[CMacro]:
        """Return all macro definitions in the project.

        This method is intended to retrieve all preprocessor macro
        definitions across all translation units.

        Returns:
            Would return a list of :class:`~cldk.models.c.CMacro` objects.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_macros_in_file`: For macros in a specific file.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_macros_in_file(self, file_name: str) -> List[CMacro] | None:
        """Return all macro definitions in a specific file.

        This method is intended to retrieve preprocessor macro definitions
        from a single source file.

        Args:
            file_name: The name or path of the C file.

        Returns:
            Would return a list of :class:`~cldk.models.c.CMacro` objects,
            or ``None`` if the file is not found.

        Raises:
            NotImplementedError: This functionality is not yet implemented.

        See Also:
            :meth:`get_macros`: For macros across all files.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")


# Note: The following functions appear to be orphaned (defined outside the class).
# They are preserved here for reference but may need to be integrated into the
# CAnalysis class or CApplication model in a future refactor.


def _get_includes(translation_units: Dict[str, CTranslationUnit]) -> List[str]:
    """Return all include statements across the project.

    This is a standalone utility function for extracting include directives
    from a collection of translation units.

    Args:
        translation_units: Dictionary mapping file paths to translation units.

    Returns:
        A list of all include statement strings from all translation units.
    """
    all_includes = []
    for translation_unit in translation_units.values():
        all_includes.extend(translation_unit.includes)
    return all_includes


def _get_includes_in_file(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[str] | None:
    """Return include statements in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of include statement strings from the file, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].includes
    return None


def _get_macros(translation_units: Dict[str, CTranslationUnit]) -> List[CMacro]:
    """Return all macro definitions across the project.

    Args:
        translation_units: Dictionary mapping file paths to translation units.

    Returns:
        A list of :class:`~cldk.models.c.CMacro` objects from all files.
    """
    all_macros = []
    for translation_unit in translation_units.values():
        all_macros.extend(translation_unit.macros)
    return all_macros


def _get_macros_in_file(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[CMacro] | None:
    """Return macro definitions in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of :class:`~cldk.models.c.CMacro` objects, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].macros
    return None


def _get_typedefs(translation_units: Dict[str, CTranslationUnit]) -> List[CTypedef]:
    """Return typedef declarations across the project.

    Args:
        translation_units: Dictionary mapping file paths to translation units.

    Returns:
        A list of :class:`~cldk.models.c.CTypedef` objects from all files.
    """
    all_typedefs = []
    for translation_unit in translation_units.values():
        all_typedefs.extend(translation_unit.typedefs)
    return all_typedefs


def _get_typedefs_in_file(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[CTypedef] | None:
    """Return typedef declarations in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of :class:`~cldk.models.c.CTypedef` objects, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].typedefs
    return None


def _get_structs(translation_units: Dict[str, CTranslationUnit]) -> List[CStruct]:
    """Return struct/union declarations across the project.

    Args:
        translation_units: Dictionary mapping file paths to translation units.

    Returns:
        A list of :class:`~cldk.models.c.CStruct` objects from all files.
    """
    all_structs = []
    for translation_unit in translation_units.values():
        all_structs.extend(translation_unit.structs)
    return all_structs


def _get_structs_in_file(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[CStruct] | None:
    """Return struct/union declarations in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of :class:`~cldk.models.c.CStruct` objects, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].structs
    return None


def _get_enums(translation_units: Dict[str, CTranslationUnit]) -> List[CEnum]:
    """Return enum declarations across the project.

    Args:
        translation_units: Dictionary mapping file paths to translation units.

    Returns:
        A list of :class:`~cldk.models.c.CEnum` objects from all files.
    """
    all_enums = []
    for translation_unit in translation_units.values():
        all_enums.extend(translation_unit.enums)
    return all_enums


def _get_enums_in_file(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[CEnum] | None:
    """Return enum declarations in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of :class:`~cldk.models.c.CEnum` objects, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].enums
    return None


def _get_globals(translation_units: Dict[str, CTranslationUnit], file_name: str) -> List[CVariable] | None:
    """Return global variable declarations in a specific file.

    Args:
        translation_units: Dictionary mapping file paths to translation units.
        file_name: The name or path of the C file.

    Returns:
        A list of :class:`~cldk.models.c.CVariable` objects, or ``None``
        if the file is not found.
    """
    if file_name in translation_units:
        return translation_units[file_name].globals
    return None
