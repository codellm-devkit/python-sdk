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
Models module
"""
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, field_validator
from cldk.models.java.enums import CRUDOperationType, CRUDQueryType

_CALLABLES_LOOKUP_TABLE = dict()


class JComment(BaseModel):
    """Represents a comment in Java code.

    Attributes:
        content (str): The content of the comment.
        start_line (int): The starting line number of the comment in the source file.
        end_line (int): The ending line number of the comment in the source file.
        start_column (int): The starting column of the comment in the source file.
        end_column (int): The ending column of the comment in the source file.
        is_javadoc (bool): A flag indicating whether the comment is a Javadoc comment.
    """

    content: str | None = None
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    is_javadoc: bool = False


class JRecordComponent(BaseModel):
    """Represents a component of a Java record.

    Attributes:
        comment (JComment): The comment associated with the component.
        name (str): The name of the component.
        type (str): The type of the component.
        annotations (List[str]): The annotations applied to the component.
        modifiers (List[str]): The modifiers applied to the component.
    """

    comment: JComment | None
    name: str
    type: str
    modifiers: List[str]
    annotations: List[str]
    default_value: Union[str, None, Any] = None
    is_var_args: bool = False


class JField(BaseModel):
    """Represents a field in a Java class or interface.

    Attributes:
        comment (JComment): The comment associated with the field.
        name (str): The name of the field.
        type (str): The type of the field.
        start_line (int): The starting line number of the field in the source file.
        end_line (int): The ending line number of the field in the source file.
        variables (List[str]): The variables declared in the field.
        modifiers (List[str]): The modifiers applied to the field (e.g., public, static).
        annotations (List[str]): The annotations applied to the field.
    """

    comment: JComment | None
    type: str
    start_line: int
    end_line: int
    variables: List[str]
    modifiers: List[str]
    annotations: List[str]


class JCallableParameter(BaseModel):
    """Represents a parameter of a Java callable.

    Attributes:
        name (str): The name of the parameter.
        type (str): The type of the parameter.
        annotations (List[str]): The annotations applied to the parameter.
        modifiers (List[str]): The modifiers applied to the parameter.
        start_line (int): The starting line number of the parameter in the source file.
        end_line (int): The ending line number of the parameter in the source file.
        start_column (int): The starting column of the parameter in the source file.
        end_column (int): The ending column of the parameter in the source file.
    """

    name: str | None
    type: str
    annotations: List[str]
    modifiers: List[str]
    start_line: int
    end_line: int
    start_column: int
    end_column: int


class JEnumConstant(BaseModel):
    """Represents a constant in an enumeration.

    Attributes:
        name (str): The name of the enum constant.
        arguments (List[str]): The arguments associated with the enum constant.
    """

    name: str
    arguments: List[str]


class JCRUDOperation(BaseModel):
    """Represents a CRUD operation.

    Attributes:
        line_number (int): The line number of the operation.
        operation_type (JCRUDOperationType): The type of the operation.
    """

    line_number: int
    operation_type: CRUDOperationType | None


class JCRUDQuery(BaseModel):
    """Represents a CRUD query.

    Attributes:
        line_number (int): The line number of the query.
        query_arguments (List[str]): The arguments of the query.
        query_type (JCRUDQueryType): The type of the query.
    """

    line_number: int
    query_arguments: List[str] | None
    query_type: CRUDQueryType | None


class JCallSite(BaseModel):
    """Represents a call site.

    Attributes:
        comment (JComment): The comment associated with the call site.
        method_name (str): The name of the method called at the call site.
        receiver_expr (str): Expression for the receiver of the method call.
        receiver_type (str): Name of type declaring the called method.
        argument_types (List[str]): Types of actual parameters for the call.
        return_type (str): Return type of the method call (resolved type of the method call expression; empty string if expression is unresolved).
        callee_signature (str): Signature of the callee.
        is_static_call (bool): Flag indicating whether the call is a static call.
        is_private (bool): Flag indicating whether the call is a private call.
        is_public (bool): Flag indicating whether the call is a public call.
        is_protected (bool): Flag indicating whether the call is a protected call.
        is_unspecified (bool): Flag indicating whether the call is an unspecified call.
        is_constructor_call (bool): Flag indicating whether the call is a constructor call.
        crud_operation (CRUDOperationType): The CRUD operation type of the call site.
        crud_query (CRUDQueryType): The CRUD query type of the call site.
        start_line (int): The starting line number of the call site.
        start_column (int): The starting column of the call site.
        end_line (int): The ending line number of the call site.
        end_column (int): The ending column of the call site.
    """

    comment: JComment | None
    method_name: str
    receiver_expr: str = ""
    receiver_type: str
    argument_types: List[str]
    return_type: str = ""
    callee_signature: str = ""
    is_static_call: bool | None = None
    is_private: bool | None = None
    is_public: bool | None = None
    is_protected: bool | None = None
    is_unspecified: bool | None = None
    is_constructor_call: bool
    crud_operation: JCRUDOperation | None
    crud_query: JCRUDQuery | None
    start_line: int
    start_column: int
    end_line: int
    end_column: int


class JVariableDeclaration(BaseModel):
    """Represents a variable declaration.

    Attributes:
        comment (JComment): The comment associated with the variable declaration.
        name (str): The name of the variable.
        type (str): The type of the variable.
        initializer (str): The initialization expression (if present) for the variable declaration.
        start_line (int): The starting line number of the declaration.
        start_column (int): The starting column of the declaration.
        end_line (int): The ending line number of the declaration.
        end_column (int): The ending column of the declaration.
    """

    comment: JComment | None
    name: str
    type: str
    initializer: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int


class InitializationBlock(BaseModel):
    """Represents an initialization block in Java.

    Attributes:
        file_path (str): The path to the source file.
        comments (List[JComment]): The comments associated with the block.
        annotations (List[str]): The annotations applied to the block.
        thrown_exceptions (List[str]): Exceptions declared via "throws".
        code (str): The code block.
        start_line (int): The starting line number of the block in the source file.
        end_line (int): The ending line number of the block in the source file.
        is_static (bool): A flag indicating whether the block is static.
        referenced_types (List[str]): The types referenced within the block.
        accessed_fields (List[str]): Fields accessed in the block.
        call_sites (List[JCallSite]): Call sites in the block.
        variable_declarations (List[JVariableDeclaration]): Local variable declarations in the block.
        cyclomatic_complexity (int): Cyclomatic complexity of the block.
    """

    file_path: str
    comments: List[JComment]
    annotations: List[str]
    thrown_exceptions: List[str]
    code: str
    start_line: int
    end_line: int
    is_static: bool
    referenced_types: List[str]
    accessed_fields: List[str]
    call_sites: List[JCallSite]
    variable_declarations: List[JVariableDeclaration]
    cyclomatic_complexity: int


class JCallable(BaseModel):
    """Represents a callable entity such as a method or constructor in Java.

    Attributes:
        signature (str): The signature of the callable.
        is_implicit (bool): A flag indicating whether the callable is implicit (e.g., a default constructor).
        is_constructor (bool): A flag indicating whether the callable is a constructor.
        comment (List[JComment]): A list of comments associated with the callable.
        annotations (List[str]): The annotations applied to the callable.
        modifiers (List[str]): The modifiers applied to the callable (e.g., public, static).
        thrown_exceptions (List[str]): Exceptions declared via "throws".
        declaration (str): The declaration of the callable.
        parameters (List[JCallableParameter]): The parameters of the callable.
        return_type (Optional[str]): The return type of the callable. None if the callable does not return a value (e.g., a constructor).
        code (str): The code block of the callable.
        start_line (int): The starting line number of the callable in the source file.
        end_line (int): The ending line number of the callable in the source file.
        referenced_types (List[str]): The types referenced within the callable.
        accessed_fields (List[str]): Fields accessed in the callable.
        call_sites (List[JCallSite]): Call sites in the callable.
        is_entrypoint (bool): A flag indicating whether this is a service entry point method.
        variable_declarations (List[JVariableDeclaration]): Local variable declarations in the callable.
        crud_operations (List[JCRUDOperation]): CRUD operations in the callable.
        crud_queries (List[JCRUDQuery]): CRUD queries in the callable.
        cyclomatic_complexity (int): Cyclomatic complexity of the callable.
    """

    signature: str
    is_implicit: bool
    is_constructor: bool
    comments: List[JComment]
    annotations: List[str]
    modifiers: List[str]
    thrown_exceptions: List[str] = []
    declaration: str
    parameters: List[JCallableParameter]
    return_type: Optional[str] = None  # Pythonic way to denote a nullable field
    code: str
    start_line: int
    end_line: int
    referenced_types: List[str]
    accessed_fields: List[str]
    call_sites: List[JCallSite]
    is_entrypoint: bool = False
    variable_declarations: List[JVariableDeclaration]
    crud_operations: List[JCRUDOperation] | None
    crud_queries: List[JCRUDQuery] | None
    cyclomatic_complexity: int | None

    def __hash__(self):
        """
        Returns the hash value of the declaration.
        """
        return hash(self.declaration)


class JType(BaseModel):
    """Represents a Java class or interface.

    Attributes:
        is_interface (bool): A flag indicating whether the object is an interface.
        is_inner_class (bool): A flag indicating whether the object is an inner class.
        is_local_class (bool): A flag indicating whether the object is a local class.
        is_nested_type (bool): A flag indicating whether the object is a nested type.
        is_class_or_interface_declaration (bool): A flag indicating whether the object is a class or interface declaration.
        is_enum_declaration (bool): A flag indicating whether the object is an enum declaration.
        is_annotation_declaration (bool): A flag indicating whether the object is an annotation declaration.
        is_record_declaration (bool): A flag indicating whether this object is a record declaration.
        is_concrete_class (bool): A flag indicating whether this is a concrete class.
        comments (List[JComment]): A list of comments associated with the class/type.
        extends_list (List[str]): The list of classes or interfaces that the object extends.
        implements_list (List[str]): The list of interfaces that the object implements.
        modifiers (List[str]): The list of modifiers of the object.
        annotations (List[str]): The list of annotations of the object.
        parent_type (str): The name of the parent class (if it exists).
        is_entrypoint_class (bool): A flag indicating whether this is a service entry point class.
        nested_type_declarations (List[str]): All the class declarations nested under this class.
        callable_declarations (Dict[str, JCallable]): The list of constructors and methods of the object.
        field_declarations (List[JField]): The list of fields of the object.
        enum_constants (List[JEnumConstant]): The list of enum constants in the object.
    """

    is_interface: bool = False
    is_inner_class: bool = False
    is_local_class: bool = False
    is_nested_type: bool = False
    is_class_or_interface_declaration: bool = False
    is_enum_declaration: bool = False
    is_annotation_declaration: bool = False
    is_record_declaration: bool = False
    is_concrete_class: bool = False
    comments: List[JComment] | None = []
    extends_list: List[str] | None = []
    implements_list: List[str] | None = []
    modifiers: List[str] | None = []
    annotations: List[str] | None = []
    parent_type: str
    nested_type_declarations: List[str] | None = []
    callable_declarations: Dict[str, JCallable] = {}
    field_declarations: List[JField] = []
    enum_constants: List[JEnumConstant] | None = []
    record_components: List[JRecordComponent] | None = []
    initialization_blocks: List[InitializationBlock] | None = []
    is_entrypoint_class: bool = False


class JCompilationUnit(BaseModel):
    """Represents a compilation unit in Java.

    Attributes:
        comments (List[JComment]): A list of comments in the compilation unit.
        imports (List[str]): A list of import statements in the compilation unit.
        type_declarations (Dict[str, JType]): A dictionary mapping type names to their corresponding JType representations.
    """

    comments: List[JComment]
    imports: List[str]
    type_declarations: Dict[str, JType]
    is_modified: bool = False


class JMethodDetail(BaseModel):
    """Represents details about a method in a Java class.

    Attributes:
        method_declaration (str): The declaration string of the method.
        klass (str): The name of the class containing the method. 'class' is a reserved keyword in Python.
        method (JCallable): An instance of JCallable representing the callable details of the method.
    """

    method_declaration: str
    # class is a reserved keyword in python. we'll use klass.
    klass: str
    method: JCallable

    def __repr__(self):
        return f"JMethodDetail({self.method_declaration})"

    def __hash__(self):
        return hash(tuple(self))


class JGraphEdgesST(BaseModel):
    """Represents an edge in a graph structure for method dependencies.

    Attributes:
        source (JMethodDetail): The source method of the edge.
        target (JMethodDetail): The target method of the edge.
        type (str): The type of the edge.
        weight (str): The weight of the edge, indicating the strength or significance of the connection.
        source_kind (Optional[str]): The kind of the source method. Default is None.
        destination_kind (Optional[str]): The kind of the target method. Default is None.
    """

    source: JMethodDetail
    target: JMethodDetail
    type: str
    weight: str
    source_kind: str | None = None
    destination_kind: str | None = None


class JGraphEdges(BaseModel):
    source: JMethodDetail
    target: JMethodDetail
    type: str
    weight: str
    source_kind: str | None = None
    destination_kind: str | None = None

    @field_validator("source", "target", mode="before")
    @classmethod
    def validate_source(cls, value) -> JMethodDetail:
        _, type_declaration, signature = value["file_path"], value["type_declaration"], value["signature"]
        j_callable: JCallable = _CALLABLES_LOOKUP_TABLE.get(
            (type_declaration, signature),
            JCallable(
                comments=[],
                signature=signature,
                is_implicit=True,
                is_constructor="<init>" in value["callable_declaration"],
                comment="",
                annotations=[],
                modifiers=[],
                thrown_exceptions=[],
                declaration="",
                parameters=[
                    JCallableParameter(name=None, type=t, annotations=[], modifiers=[], start_column=-1, end_column=-1, start_line=-1, end_line=-1)
                    for t in value["callable_declaration"].split("(")[1].split(")")[0].split(",")
                ],
                code="",
                start_line=-1,
                end_line=-1,
                referenced_types=[],
                accessed_fields=[],
                call_sites=[],
                variable_declarations=[],
                crud_operations=[],
                crud_queries=[],
                cyclomatic_complexity=0,
            ),
        )
        _CALLABLES_LOOKUP_TABLE[(type_declaration, signature)] = j_callable
        class_name = type_declaration
        method_decl = j_callable.declaration
        return JMethodDetail(method_declaration=method_decl, klass=class_name, method=j_callable)

    def __hash__(self):
        return hash(tuple(self))


class JApplication(BaseModel):
    """
    Represents a Java application.

    Parameters
    ----------
    symbol_table : List[JCompilationUnit]
        The symbol table representation
    system_dependency : List[JGraphEdges]
        The edges of the system dependency graph. Default None.
    """

    symbol_table: Dict[str, JCompilationUnit]
    call_graph: List[JGraphEdges] = None
    system_dependency_graph: List[JGraphEdges] = None

    @field_validator("symbol_table", mode="after")
    @classmethod
    def validate_source(cls, symbol_table) -> Dict[str, JCompilationUnit]:
        # Populate the lookup table for callables
        for _, j_compulation_unit in symbol_table.items():
            for type_declaration, jtype in j_compulation_unit.type_declarations.items():
                for __, j_callable in jtype.callable_declarations.items():
                    _CALLABLES_LOOKUP_TABLE[(type_declaration, j_callable.signature)] = j_callable

        return symbol_table
