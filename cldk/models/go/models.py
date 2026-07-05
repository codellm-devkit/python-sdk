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

"""Go data models.

Pydantic models mirroring the codeanalyzer-go schema.go types.
All JSON keys match the snake_case names produced by the Go analyzer so
deserialization is zero-transform.

Go serializes nil slices as JSON ``null`` (the zero value for a slice type).
All models inherit from ``_NullSafeBase``, which coerces ``null`` list/dict
fields to their empty-collection defaults before Pydantic validates them.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class _NullSafeBase(BaseModel):
    """Coerce JSON null to an empty list/dict for any field whose default is a collection."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_collections(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for field_name, field_info in cls.model_fields.items():
            if data.get(field_name) is None and field_info.default_factory is not None:
                try:
                    sentinel = field_info.default_factory()
                    if isinstance(sentinel, (list, dict)):
                        data[field_name] = sentinel
                except Exception:
                    pass
        return data


class GoImport(_NullSafeBase):
    module: str
    alias: str = ""
    start_line: int = -1
    end_line: int = -1


class GoComment(_NullSafeBase):
    content: str
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1
    is_doc_comment: bool = False


class GoParameter(_NullSafeBase):
    name: str
    type: str
    is_variadic: bool = False
    start_line: int = -1
    end_line: int = -1


class GoVariableDeclaration(_NullSafeBase):
    name: str
    type: str = ""
    initializer: str = ""
    scope: str = "function"
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class GoField(_NullSafeBase):
    name: str
    type: str
    comments: List[GoComment] = Field(default_factory=list)
    tags: Dict[str, str] = Field(default_factory=dict)
    is_exported: bool = False
    is_embedded: bool = False
    start_line: int = -1
    end_line: int = -1


class GoSymbol(_NullSafeBase):
    name: str
    scope: str = "local"
    kind: str = "variable"
    type: str = ""
    qualified_name: str = ""
    is_builtin: bool = False
    lineno: int = -1
    col_offset: int = -1


class GoCallsite(_NullSafeBase):
    method_name: str
    receiver_expr: str = ""
    receiver_type: str = ""
    argument_types: List[str] = Field(default_factory=list)
    return_type: str = ""
    callee_signature: Optional[str] = None
    is_constructor_call: bool = False
    is_goroutine: bool = False
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1


class GoCallable(_NullSafeBase):
    name: str
    path: str = ""
    signature: str = ""
    comments: List[GoComment] = Field(default_factory=list)
    parameters: List[GoParameter] = Field(default_factory=list)
    return_type: str = ""
    return_types: List[str] = Field(default_factory=list)
    code: str = ""
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[GoSymbol] = Field(default_factory=list)
    call_sites: List[GoCallsite] = Field(default_factory=list)
    inner_callables: Dict[str, "GoCallable"] = Field(default_factory=dict)
    local_variables: List[GoVariableDeclaration] = Field(default_factory=list)
    cyclomatic_complexity: int = 1
    is_entrypoint: bool = False
    entrypoint_framework: str = ""
    receiver_type: str = ""
    receiver_name: str = ""
    is_exported: bool = False


class GoType(_NullSafeBase):
    name: str
    signature: str = ""
    comments: List[GoComment] = Field(default_factory=list)
    code: str = ""
    is_interface: bool = False
    is_exported: bool = False
    fields: List[GoField] = Field(default_factory=list)
    methods: Dict[str, GoCallable] = Field(default_factory=dict)
    base_types: List[str] = Field(default_factory=list)
    inner_types: Dict[str, "GoType"] = Field(default_factory=dict)
    start_line: int = -1
    end_line: int = -1


class GoFile(_NullSafeBase):
    file_path: str = ""
    # JSON key is module_name for spine compatibility with other language models.
    module_name: str = ""
    imports: List[GoImport] = Field(default_factory=list)
    comments: List[GoComment] = Field(default_factory=list)
    # JSON key is classes for spine compatibility.
    classes: Dict[str, GoType] = Field(default_factory=dict)
    functions: Dict[str, GoCallable] = Field(default_factory=dict)
    variables: List[GoVariableDeclaration] = Field(default_factory=list)
    content_hash: Optional[str] = None
    last_modified: Optional[float] = None
    file_size: Optional[int] = None

    @property
    def types(self) -> Dict[str, GoType]:
        """Alias for classes — the Go schema stores types under 'classes' for spine compat."""
        return self.classes

    @property
    def package_name(self) -> str:
        """Alias for module_name — the Go schema stores package name under 'module_name'."""
        return self.module_name


class GoCallEdge(_NullSafeBase):
    source: str
    target: str
    type: str = "CALL_DEP"
    weight: int = 1
    provenance: List[str] = Field(default_factory=list)
    tags: Dict[str, str] = Field(default_factory=dict)


class GoEntrypoint(_NullSafeBase):
    signature: str
    framework: str = ""
    detection_source: str = ""
    tags: Dict[str, str] = Field(default_factory=dict)


class GoApplication(_NullSafeBase):
    symbol_table: Dict[str, GoFile] = Field(default_factory=dict)
    call_graph: List[GoCallEdge] = Field(default_factory=list)
    entrypoints: Dict[str, List[GoEntrypoint]] = Field(default_factory=dict)


# Required for forward references in recursive models.
GoCallable.model_rebuild()
GoType.model_rebuild()
