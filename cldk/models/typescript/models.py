################################################################################
# Copyright IBM Corporation 2026
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

"""TypeScript schema models.

Pydantic mirror of the analyzer-side schema in ``codeanalyzer-ts/src/schema.ts``. These models
are BOTH the SDK binding and the validation target the analyzer's ``analysis.json`` is checked
against — they must be co-evolved with ``schema.ts`` field-for-field.

The invariant spine matches the identity-only Python model (``PyApplication``/``PyCallEdge``):
``TSApplication { symbol_table: Dict[path, TSModule], call_graph: List[TSCallEdge] }`` with
edges whose ``source``/``target`` are bare ``TSCallable.signature`` strings. Everything else is
TypeScript-native (interface / type-alias / enum / namespace node kinds; generics; modifiers).

``extra="forbid"`` is intentional: it makes any drift between the analyzer's JSON and these
models fail loudly during development rather than silently dropping fields.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from typing_extensions import Literal


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------------------------
# Leaf models
# ----------------------------------------------------------------------------------------------


class TSImport(_Base):
    """A TypeScript import binding (one entry per imported name)."""

    module: str
    name: str
    alias: Optional[str] = None
    is_type_only: bool = False
    import_kind: str = "named"  # named | default | namespace | side_effect
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSExport(_Base):
    """A TypeScript export / re-export binding."""

    module: Optional[str] = None
    name: str
    alias: Optional[str] = None
    is_type_only: bool = False
    export_kind: str = "named"  # named | default | namespace | re_export
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSComment(_Base):
    """A comment or JSDoc block."""

    content: str
    is_docstring: bool = False
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSSymbol(_Base):
    """A symbol referenced or declared in code."""

    name: str
    scope: str
    kind: str
    type: Optional[str] = None
    qualified_name: Optional[str] = None
    is_builtin: bool = False
    lineno: int = -1
    col_offset: int = -1


class TSVariableDeclaration(_Base):
    """A variable / const / let declaration."""

    name: str
    type: Optional[str] = None
    initializer: Optional[str] = None
    value: Optional[Any] = None
    scope: str = "module"  # module | namespace | class | function | block
    declaration_kind: str = "unknown"  # const | let | var | using | unknown
    is_readonly: bool = False
    is_exported: bool = False
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSDecorator(_Base):
    """A decorator applied to a class / member / parameter (structured, with arguments)."""

    name: str
    qualified_name: Optional[str] = None
    positional_arguments: List[str] = []
    keyword_arguments: Dict[str, str] = {}
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSTypeParameter(_Base):
    """A generic type parameter, e.g. ``T extends Base = Default``."""

    name: str
    constraint: Optional[str] = None
    default: Optional[str] = None


class TSCallableParameter(_Base):
    """A function / method parameter."""

    name: str
    type: Optional[str] = None
    default_value: Optional[str] = None
    is_optional: bool = False
    is_rest: bool = False
    is_readonly: bool = False
    accessibility: Optional[str] = None
    decorators: List[TSDecorator] = []
    start_line: int = -1
    end_line: int = -1
    start_column: int = -1
    end_column: int = -1


class TSCallsite(_Base):
    """Rich per-call metadata, attached to the caller. ``callee_signature`` is backfilled by the
    resolver call graph."""

    method_name: str
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    type_arguments: List[str] = []
    return_type: Optional[str] = None
    callee_signature: Optional[str] = None
    is_constructor_call: bool = False
    is_optional_chain: bool = False
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1


class TSOverloadSignature(_Base):
    """An overload signature attached to the implementation callable."""

    parameters: List[TSCallableParameter] = []
    return_type: Optional[str] = None
    type_parameters: List[TSTypeParameter] = []
    start_line: int = -1
    end_line: int = -1


class TSClassAttribute(_Base):
    """A class property / field (also covers constructor parameter-properties)."""

    name: str
    type: Optional[str] = None
    comments: List[TSComment] = []
    decorators: List[TSDecorator] = []
    initializer: Optional[str] = None
    accessibility: Optional[str] = None
    is_static: bool = False
    is_readonly: bool = False
    is_optional: bool = False
    is_abstract: bool = False
    start_line: int = -1
    end_line: int = -1


# ----------------------------------------------------------------------------------------------
# Callable
# ----------------------------------------------------------------------------------------------


class TSCallable(_Base):
    """A function / method / constructor / accessor / arrow function."""

    name: str
    path: str
    signature: str  # e.g. src/user.UserService.getUser — the edge id
    comments: List[TSComment] = []
    decorators: List[TSDecorator] = []
    parameters: List[TSCallableParameter] = []
    type_parameters: List[TSTypeParameter] = []
    return_type: Optional[str] = None
    code: Optional[str] = None
    start_line: int = -1
    end_line: int = -1
    code_start_line: int = -1
    accessed_symbols: List[TSSymbol] = []
    call_sites: List[TSCallsite] = []
    inner_callables: Dict[str, "TSCallable"] = {}
    inner_classes: Dict[str, "TSClass"] = {}
    local_variables: List[TSVariableDeclaration] = []
    cyclomatic_complexity: int = 0
    is_entrypoint: bool = False
    entrypoint_framework: Optional[str] = None
    # TypeScript-native typed fields
    kind: str = "function"  # function | method | constructor | getter | setter | arrow | function_expression
    accessibility: Optional[str] = None
    is_static: bool = False
    is_abstract: bool = False
    is_async: bool = False
    is_generator: bool = False
    is_optional: bool = False
    is_readonly: bool = False
    is_exported: bool = False
    is_ambient: bool = False
    is_implicit: bool = False
    accessor_kind: Optional[str] = None
    overload_signatures: List[TSOverloadSignature] = []

    def __hash__(self) -> int:
        return hash(self.signature)


# ----------------------------------------------------------------------------------------------
# Type-kind node models
# ----------------------------------------------------------------------------------------------


class TSClass(_Base):
    """A class declaration."""

    name: str
    signature: str
    comments: List[TSComment] = []
    code: Optional[str] = None
    decorators: List[TSDecorator] = []
    base_classes: List[str] = []  # spine: union of extends + implements (signature strings)
    implements_types: List[str] = []  # typed split: just the implemented interfaces
    type_parameters: List[TSTypeParameter] = []
    methods: Dict[str, TSCallable] = {}
    attributes: Dict[str, TSClassAttribute] = {}
    inner_classes: Dict[str, "TSClass"] = {}
    is_abstract: bool = False
    is_exported: bool = False
    is_ambient: bool = False
    start_line: int = -1
    end_line: int = -1

    def __hash__(self) -> int:
        return hash(self.signature)


class TSInterface(_Base):
    """An interface declaration (TS node kind)."""

    name: str
    signature: str
    comments: List[TSComment] = []
    code: Optional[str] = None
    base_classes: List[str] = []  # extended interfaces (signature strings)
    type_parameters: List[TSTypeParameter] = []
    methods: Dict[str, TSCallable] = {}
    properties: Dict[str, TSClassAttribute] = {}
    call_signatures: List[str] = []
    index_signatures: List[str] = []
    is_exported: bool = False
    is_ambient: bool = False
    start_line: int = -1
    end_line: int = -1

    def __hash__(self) -> int:
        return hash(self.signature)


class TSEnumMember(_Base):
    """A member of an enum."""

    name: str
    value: Optional[str] = None
    start_line: int = -1
    end_line: int = -1


class TSEnum(_Base):
    """An enum declaration (TS node kind)."""

    name: str
    signature: str
    comments: List[TSComment] = []
    code: Optional[str] = None
    members: List[TSEnumMember] = []
    is_const: bool = False
    is_exported: bool = False
    is_ambient: bool = False
    start_line: int = -1
    end_line: int = -1

    def __hash__(self) -> int:
        return hash(self.signature)


class TSTypeAlias(_Base):
    """A type-alias declaration (TS node kind)."""

    name: str
    signature: str
    comments: List[TSComment] = []
    code: Optional[str] = None
    aliased_type: str = ""
    type_parameters: List[TSTypeParameter] = []
    is_exported: bool = False
    is_ambient: bool = False
    start_line: int = -1
    end_line: int = -1

    def __hash__(self) -> int:
        return hash(self.signature)


class TSNamespace(_Base):
    """A namespace / module block (TS node kind) — recursive container."""

    name: str
    signature: str
    comments: List[TSComment] = []
    classes: Dict[str, TSClass] = {}
    interfaces: Dict[str, TSInterface] = {}
    enums: Dict[str, TSEnum] = {}
    type_aliases: Dict[str, TSTypeAlias] = {}
    functions: Dict[str, TSCallable] = {}
    variables: List[TSVariableDeclaration] = []
    namespaces: Dict[str, "TSNamespace"] = {}
    is_exported: bool = False
    is_ambient: bool = False
    start_line: int = -1
    end_line: int = -1

    def __hash__(self) -> int:
        return hash(self.signature)


# ----------------------------------------------------------------------------------------------
# Module / edge / entrypoint / application
# ----------------------------------------------------------------------------------------------


class TSModule(_Base):
    """A compilation unit (a .ts/.tsx file)."""

    file_path: str
    module_name: str
    imports: List[TSImport] = []
    exports: List[TSExport] = []
    comments: List[TSComment] = []
    classes: Dict[str, TSClass] = {}
    interfaces: Dict[str, TSInterface] = {}
    enums: Dict[str, TSEnum] = {}
    type_aliases: Dict[str, TSTypeAlias] = {}
    functions: Dict[str, TSCallable] = {}
    namespaces: Dict[str, TSNamespace] = {}
    variables: List[TSVariableDeclaration] = []
    is_tsx: bool = False
    is_declaration_file: bool = False
    content_hash: Optional[str] = None
    last_modified: Optional[float] = None
    file_size: Optional[int] = None


class TSCallEdge(_Base):
    """Identity-only call-graph edge. ``source``/``target`` are ``TSCallable.signature`` strings."""

    source: str
    target: str
    type: Literal["CALL_DEP"] = "CALL_DEP"
    weight: int = 1
    provenance: List[str] = []
    tags: Dict[str, str] = {}


class TSExternalSymbol(_Base):
    """A WALA-style phantom node: a synthetic stub for a call target OUTSIDE the project (an
    imported/required library member). An edge's ``target`` byte-matches either a real
    ``TSCallable.signature`` or a ``TSExternalSymbol.signature``, so the call graph stays
    dangling-free while still recording external (e.g. sink) calls."""

    signature: str  # e.g. "node:fs.readFileSync", "express.Router.get"
    name: str
    module: str
    kind: str = "unknown"
    is_external: bool = True


class TSEntrypoint(_Base):
    """A framework entrypoint (populated by level-2 finders; empty for level 1)."""

    signature: str
    framework: str
    detection_source: str
    route_path: Optional[str] = None
    http_methods: List[str] = []
    source_file: Optional[str] = None
    tags: Dict[str, str] = {}


class TSApplication(_Base):
    """The root analysis object emitted as analysis.json."""

    symbol_table: Dict[str, TSModule]
    call_graph: List[TSCallEdge] = []
    external_symbols: Dict[str, TSExternalSymbol] = {}
    entrypoints: Dict[str, List[TSEntrypoint]] = {}


# Resolve forward references for the mutually-recursive models.
TSCallable.model_rebuild()
TSClass.model_rebuild()
TSInterface.model_rebuild()
TSNamespace.model_rebuild()
TSModule.model_rebuild()
TSApplication.model_rebuild()
