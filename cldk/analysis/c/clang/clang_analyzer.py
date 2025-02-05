import os
import platform
from clang.cindex import Config
from pathlib import Path
from typing import List, Optional
from cldk.models.c import CFunction, CMacro, CCallSite, CTranslationUnit, CApplication
import logging
from ipdb import set_trace

from cldk.models.c.models import CInclude, CParameter, CVariable, StorageClass

logger = logging.getLogger(__name__)

# First, we only import Config from clang.cindex
from clang.cindex import Config


def find_libclang() -> str:
    """
    Locates the libclang library on the system based on the operating system.
    This function runs before any other Clang functionality is used, ensuring
    proper initialization of the Clang environment.
    """
    system = platform.system()

    # On macOS, we check both Apple Silicon and Intel paths
    if system == "Darwin":
        possible_paths = [
            "/opt/homebrew/opt/llvm/lib/libclang.dylib",  # Apple Silicon
            "/usr/local/opt/llvm/lib/libclang.dylib",  # Intel Mac
            "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libclang.dylib",
        ]
        install_instructions = "Install LLVM using: brew install llvm"

    # On Linux, we check various common installation paths
    elif system == "Linux":
        possible_paths = [
            "/usr/lib/llvm-14/lib/libclang.so",
            "/usr/lib/llvm-13/lib/libclang.so",
            "/usr/lib/llvm-12/lib/libclang.so",
            "/usr/lib/x86_64-linux-gnu/libclang-14.so.1",
            "/usr/lib/libclang.so",
        ]
        install_instructions = "Install libclang using: sudo apt-get install libclang-dev"
    else:
        raise RuntimeError(f"Unsupported operating system: {system}")

    # Check each possible path and return the first one that exists
    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"Found libclang at: {path}")
            return path

    # If no library is found, provide clear installation instructions
    raise RuntimeError(f"Could not find libclang library. \n" f"Please ensure LLVM is installed:\n{install_instructions}")


# Initialize libclang at module level
try:
    libclang_path = find_libclang()
    Config.set_library_file(libclang_path)
    logger.info("Successfully initialized libclang")

    # Now that libclang is initialized, we can safely import other Clang components
    from clang.cindex import Index, TranslationUnit, CursorKind, TypeKind, CompilationDatabase

except Exception as e:
    logger.error(f"Failed to initialize libclang: {e}")
    raise


class ClangAnalyzer:
    """Analyzes C code using Clang's Python bindings."""

    def __init__(self, compilation_database_path: Optional[Path] = None):
        # Configure Clang before creating the Index
        self.index = Index.create()
        self.compilation_database = None
        # TODO: Implement compilation database for C/C++ projects so that we can get compile arguments for each file
        # and parse them correctly. This is useful for projects with complex build systems.
        if compilation_database_path:
            self.compilation_database = CompilationDatabase.fromDirectory(str(compilation_database_path))

    def analyze_file(self, file_path: Path) -> CTranslationUnit:
        """Analyzes a single C source file using Clang."""

        # Get compilation arguments if available
        compile_args = self._get_compile_args(file_path)
        # Parse the file with Clang
        tu = self.index.parse(
            str(file_path),
            args=compile_args,
            options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )

        # Initialize our translation unit model
        translation_unit = CTranslationUnit(
            file_path=str(file_path),
            is_header=file_path.suffix in {".h", ".hpp", ".hxx"},
        )

        # Process all cursors in the translation unit
        self._process_translation_unit(tu.cursor, translation_unit)

        return translation_unit

    def _process_translation_unit(self, cursor, translation_unit: CTranslationUnit):
        """Processes all declarations in a translation unit."""

        for child in cursor.get_children():
            if child.location.file and str(child.location.file) != translation_unit.file_path:
                # Skip declarations from included files
                continue

            elif child.kind == CursorKind.FUNCTION_DECL:
                func = self._extract_function(child)
                translation_unit.functions[func.name] = func

            elif child.kind == CursorKind.INCLUSION_DIRECTIVE:
                include = self._process_inclusion(child)
                translation_unit.includes.append(include)

    def _process_inclusion(self, cursor):
        """
        Processes an include directive, capturing both the include type and the included file.

        Args:
            cursor: Cursor to the include directive
            translation_unit: Translation unit being processed

        Returns:


        In C/C++, we have two main types of includes:
        1. System includes: #include <header.h>   - Usually for standard libraries
        2. Local includes: #include "header.h"    - Usually for your own headers

        The function captures this distinction and stores additional metadata about
        the inclusion.
        """
        include_name = cursor.displayname
        include_location = cursor.location

        # Get the full text of the include directive
        tokens = list(cursor.get_tokens())
        full_text = " ".join(token.spelling for token in tokens)

        # Determine if this is a system include or local include
        is_system_include = False
        if tokens:
            # Look at the actual tokens to see if it uses <> or ""
            for token in tokens:
                if token.spelling == "<":
                    is_system_include = True
                    break

        # Store more detailed information about the include
        # include_info = {"name": include_name, "is_system": is_system_include, "line_number": include_location.line, "full_text": full_text}
        return CInclude(name=include_name, is_system=is_system_include, line_number=include_location.line, full_text=full_text)

    def _extract_parameter(self, param) -> CParameter:
        """
        Extracts parameter information, handling default values carefully.

        In C++, parameters can have default values, but accessing these requires
        careful token handling since the tokens form a generator that can only
        be consumed once.
        """
        default_value = None
        try:
            tokens = list(param.get_tokens())
            if tokens:
                default_value = tokens[0].spelling
        except Exception as e:
            logger.error(f"Warning: Could not extract default value for parameter {param.spelling}: {e}")

        return CParameter(name=param.spelling or f"placeholder_arg_{param.type.spelling.replace(' ', '_')}", type=param.type.spelling, default_value=default_value)

    def _extract_variable(self, cursor) -> CVariable:
        """Extracts detailed variable information from a cursor."""
        return CVariable(
            name=cursor.spelling,
            type=cursor.type.spelling,
            is_static=cursor.storage_class == StorageClass.STATIC,
            is_extern=cursor.storage_class == StorageClass.EXTERN,
            is_const=cursor.type.is_const_qualified(),
            is_volatile=cursor.type.is_volatile_qualified(),
            start_line=cursor.extent.start.line,
            end_line=cursor.extent.end.line,
        )

    def _extract_function_body(self, cursor) -> str:
        """Extracts the body of a function.

        Args:
            cursor: Cursor to the function

        Returns:
            str: The function body
        """
        if cursor.is_definition() == False:
            return ""

        try:
            tokens = list(cursor.get_tokens())
            try:
                body_start = next(i for i, t in enumerate(tokens) if t.spelling == "{")
            except:
                return ""

            brace = 0
            body = []
            for token in tokens[body_start:]:
                if token.spelling == "{":
                    brace += 1
                elif token.spelling == "}":
                    brace -= 1
                    if brace == 0:
                        break
                body.append(token.spelling)

            body_str = " ".join(body)

            if brace != 0:
                logging.warning(f"Unbalanced braces in function body: {cursor.spelling}")

            return body_str

        except Exception as e:
            logging.error(f"Error extracting function body: {e}")
            return ""

    def _extract_function(self, cursor) -> CFunction:
        """Extracts detailed function information from a cursor."""

        # Get storage class
        storage_class = None
        for token in cursor.get_tokens():
            if token.spelling in {"static", "extern"}:
                storage_class = StorageClass(token.spelling)
                break

        # Get function parameters
        parameters = []
        for param in cursor.get_arguments():
            parameters.append(self._extract_parameter(param))

        # Collect call sites and local variables
        call_sites = []
        local_vars = []
        if cursor.is_definition():
            for child in cursor.walk_preorder():
                if child.kind == CursorKind.CALL_EXPR:
                    call_sites.append(self._extract_call_site(child))
                elif child.kind == CursorKind.VAR_DECL:
                    local_vars.append(self._extract_variable(child))

        # Get function body if this is a definition
        body = self._extract_function_body(cursor)
        return CFunction(
            name=cursor.spelling,
            return_type=cursor.result_type.spelling,
            parameters=parameters,
            storage_class=storage_class,
            is_inline="inline" in cursor.get_tokens(),
            is_variadic=cursor.type.is_function_variadic(),
            body=body,
            comment=cursor.brief_comment or "",
            call_sites=call_sites,
            local_variables=local_vars,
            start_line=cursor.extent.start.line,
            end_line=cursor.extent.end.line,
        )

    def _extract_call_site(self, cursor) -> CCallSite:
        """Extracts information about a function call."""

        # Determine if this is an indirect call (through function pointer)
        is_indirect = cursor.referenced is None and cursor.type.kind == TypeKind.FUNCTIONPROTO

        # Get argument types
        arg_types = []
        for arg in cursor.get_arguments():
            arg_types.append(arg.type.spelling)

        return CCallSite(
            function_name=cursor.spelling,
            argument_types=arg_types,
            is_indirect_call=is_indirect,
            return_type=cursor.type.get_result().spelling,
            start_line=cursor.extent.start.line,
            start_column=cursor.extent.start.column,
            end_line=cursor.extent.end.line,
            end_column=cursor.extent.end.column,
        )

    def _get_compile_args(self, file_path: Path) -> List[str]:
        """Gets compilation arguments for a file."""
        if not self.compilation_database:
            return ["-x", "c++", "-std=c++17"]

        commands = self.compilation_database.getCompileCommands(str(file_path))
        if commands:
            cmd = commands[0]
            return [arg for arg in cmd.arguments[1:] if arg != str(file_path)]
        return ["-x", "c++", "-std=c++17"]
