from typing import Dict, List, Optional, Union
from pydantic import BaseModel, field_validator
from enum import Enum


class StorageClass(Enum):
    """Represents C storage class specifiers."""

    AUTO = "auto"
    REGISTER = "register"
    STATIC = "static"
    EXTERN = "extern"
    TYPEDEF = "typedef"


class CVariable(BaseModel):
    """Represents a variable declaration in C.

    Attributes:
        name (str): The name of the variable
        type (str): The type of the variable (including any type qualifiers)
        storage_class: The storage class specifier (if any)
        is_const (bool): Whether the variable is const-qualified
        is_volatile (bool): Whether the variable is volatile-qualified
        initializer (str): Initial value expression, if any
        array_dimensions (List[str]): Dimensions if this is an array variable
        is_pointer (bool): Whether this is a pointer variable
        pointer_level (int): Level of pointer indirection (e.g., 2 for char**)
    """

    name: str
    type: str
    storage_class: Optional[StorageClass] = None
    is_const: bool = False
    is_volatile: bool = False
    initializer: Optional[str] = None
    array_dimensions: List[str] = []
    is_pointer: bool = False
    pointer_level: int = 0
    start_line: int
    end_line: int


class CFunctionPointer(BaseModel):
    """Represents a function pointer type.

    Attributes:
        return_type (str): Return type of the function being pointed to
        parameter_types (List[str]): Types of the parameters
        calling_convention (Optional[str]): Calling convention if specified
    """

    return_type: str
    parameter_types: List[str]
    calling_convention: Optional[str] = None


class CMacro(BaseModel):
    """Represents a C preprocessor macro.

    Attributes:
        name (str): Name of the macro
        parameters (List[str]): Parameters for function-like macros
        replacement (str): Replacement text
        is_function_like (bool): Whether this is a function-like macro
        start_line (int): Starting line in source
        end_line (int): Ending line in source
    """

    name: str
    parameters: List[str] = []
    replacement: str
    is_function_like: bool = False
    start_line: int
    end_line: int


class CParameter(BaseModel):
    """Represents a parameter in a function declaration.

    Attributes:
        name (str): Parameter name (may be empty in declarations)
        type (str): Parameter type
        is_const (bool): Whether parameter is const-qualified
        is_volatile (bool): Whether parameter is volatile-qualified
        is_pointer (bool): Whether parameter is a pointer
        pointer_level (int): Level of pointer indirection
        array_dimensions (List[str]): Array dimensions if parameter is array
    """

    name: str
    type: str
    is_const: bool = False
    is_volatile: bool = False
    is_pointer: bool = False
    pointer_level: int = 0
    array_dimensions: List[str] = []


class CCallSite(BaseModel):
    """Represents a function call in C code.

    Attributes:
        function_name (str): Name of the called function
        argument_types (List[str]): Types of the arguments
        is_indirect_call (bool): Whether this is a call through function pointer
        is_macro_expansion (bool): Whether this call is from macro expansion
        return_type (str): Return type of the called function
        start_line (int): Starting line of the call
        start_column (int): Starting column of the call
        end_line (int): Ending line of the call
        end_column (int): Ending column of the call
    """

    function_name: str
    argument_types: List[str]
    is_indirect_call: bool = False
    is_macro_expansion: bool = False
    return_type: str = ""
    start_line: int
    start_column: int
    end_line: int
    end_column: int


class CFunction(BaseModel):
    """Represents a C function.

    Attributes:
        name (str): Function name
        return_type (str): Return type
        parameters (List[CParameter]): Function parameters
        storage_class (Optional[StorageClass]): Storage class if specified
        is_inline (bool): Whether function is inline
        is_const (bool): Whether function is const-qualified (C++)
        is_variadic (bool): Whether function takes variable arguments
        body (str): Function body code
        comment (str): Associated comments/documentation
        referenced_types (List[str]): Types referenced in function
        accessed_globals (List[str]): Global variables accessed
        call_sites (List[CCallSite]): Function calls made
        local_variables (List[CVariable]): Local variable declarations
        macros_used (List[str]): Macros used in function
        start_line (int): Starting line in source
        end_line (int): Ending line in source
        cyclomatic_complexity (Optional[int]): Cyclomatic complexity if calculated
    """

    name: str
    return_type: str
    parameters: List[CParameter]
    storage_class: Optional[StorageClass] = None
    is_inline: bool = False
    is_const: bool = False
    is_variadic: bool = False
    body: str
    comment: str = ""
    referenced_types: List[str] = []
    accessed_globals: List[str] = []
    call_sites: List[CCallSite] = []
    local_variables: List[CVariable] = []
    macros_used: List[str] = []
    start_line: int
    end_line: int
    cyclomatic_complexity: Optional[int] = None


class CStruct(BaseModel):
    """Represents a C struct or union.

    Attributes:
        name (str): Name of the struct
        is_union (bool): Whether this is a union
        members (List[CVariable]): Member variables
        is_packed (bool): Whether struct is packed
        alignment (Optional[int]): Specified alignment if any
        comment (str): Associated comments
        referenced_types (List[str]): Types referenced in struct
    """

    name: str
    is_union: bool = False
    members: List[CVariable]
    is_packed: bool = False
    alignment: Optional[int] = None
    comment: str = ""
    referenced_types: List[str] = []
    start_line: int
    end_line: int


class CEnum(BaseModel):
    """Represents a C enum declaration.

    Attributes:
        name (str): Name of the enum
        constants (Dict[str, int]): Enum constants and their values
        comment (str): Associated comments
    """

    name: str
    constants: Dict[str, int]
    comment: str = ""
    start_line: int
    end_line: int


class CTypedef(BaseModel):
    """Represents a typedef declaration.

    Attributes:
        name (str): New type name being defined
        underlying_type (str): The actual type being aliased
        is_function_pointer (bool): Whether this is a function pointer typedef
        function_pointer: Details if this is a function pointer typedef
    """

    name: str
    underlying_type: str
    is_function_pointer: bool = False
    function_pointer: Optional[CFunctionPointer] = None
    start_line: int
    end_line: int


class CInclude(BaseModel):
    """Represents a C include directive.

    Attributes:
        name (str): Name of the included file
        is_system (bool): Whether this is a system include
        line_number (int): Line number in source
        full_text (str): Full text of the include directive
    """

    name: str
    is_system: bool
    line_number: int
    full_text: str


class CTranslationUnit(BaseModel):
    """Represents a C source file.

    Attributes:
        file_path (str): Path to the source file
        includes (List[str]): Header files included
        macros (List[CMacro]): Macro definitions
        typedefs (List[CTypedef]): Typedef declarations
        structs (List[CStruct]): Struct/union declarations
        enums (List[CEnum]): Enum declarations
        globals (List[CVariable]): Global variable declarations
        functions (Dict[str, CFunction]): Function declarations/definitions
        is_header (bool): Whether this is a header file
    """

    file_path: str
    includes: List[CInclude] = []
    macros: List[CMacro] = []
    typedefs: List[CTypedef] = []
    structs: List[CStruct] = []
    enums: List[CEnum] = []
    globals: List[CVariable] = []
    functions: Dict[str, CFunction] = {}
    is_header: bool = False
    is_modified: bool = False


class CFunctionDetail(BaseModel):
    """Represents detailed information about a function.

    Attributes:
        function_declaration (str): Full function declaration
        file_path (str): Path to the file containing the function
        function (CFunction): Detailed function information
    """

    function_declaration: str
    file_path: str
    function: CFunction

    def __hash__(self):
        return hash((self.function_declaration, self.file_path))


class CCallGraphEdge(BaseModel):
    """Represents an edge in the call graph.

    Attributes:
        source (CFunctionDetail): Calling function
        target (CFunctionDetail): Called function
        type (str): Type of call relationship
        weight (str): Edge weight/importance
        is_indirect (bool): Whether this is through function pointer
    """

    source: CFunctionDetail
    target: CFunctionDetail
    type: str
    weight: str
    is_indirect: bool = False


class CApplication(BaseModel):
    """Represents a complete C application.

    Attributes:
        translation_units (Dict[str, CTranslationUnit]): All source files
        call_graph (List[CCallGraphEdge]): Function call relationships
    """

    translation_units: Dict[str, CTranslationUnit]
    call_graph: List[CCallGraphEdge] = []
