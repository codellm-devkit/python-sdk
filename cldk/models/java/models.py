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

"""Java schema models — a pydantic mirror of ``codeanalyzer-java/src/main/java/com/ibm/cldk/schema``
at the pinned release (3.0.1), schema v2.

The wire is one containment tree: ``JAnalysis{analyzer, application}`` →
``JApplication{symbol_table{path → JCompilationUnit}, call_graph, param_in, param_out, artifacts,
dependencies}`` → ``JCompilationUnit{types{name → JType}}`` → ``JType{fields{}, callables{signature →
JCallable}, types{}}`` → ``JCallable{body{}, cfg, cdg, ddg, summary, types{}}``. Every node carries a
``can://`` ``id`` and a ``kind``; a unit carries its full ``source`` once and every node's text is a
slice of it. Gson omits ``null`` fields, so an absent key is a ``None``/empty default here.

What the 1.x models exposed as stored fields is kept as **properties** where the wire still has the
fact in another shape (J-8): ``code`` over ``span`` + ``source``, ``call_sites`` over the ``call``
body nodes, ``thrown_exceptions`` over ``error_channel``, ``cyclomatic_complexity`` over ``metrics``,
``variable_declarations`` over ``local_variables``, ``referenced_types``/``accessed_fields`` over
``refs``, the ``is_*`` type predicates over ``kind`` and the owner chain. What the wire does not
carry (CRUD) is an empty list, and the facade raises for it (J-4).

``extra="forbid"`` is intentional: drift between the analyzer's JSON and these models fails loudly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from typing_extensions import Literal

from cldk.models.java.enums import CRUDOperationType, CRUDQueryType


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------------------------
# Span — the one universal attribute
# ----------------------------------------------------------------------------------------------


class JSpan(_Base):
    """``start``/``end`` are ``[line, column]`` (1-based); ``bytes`` are ``[from, to]`` **UTF-8 byte**
    offsets into the owning unit's ``source`` (the analyzer's ``Spans.java`` computes them as prefix
    sums over ``getBytes(UTF_8)``)."""

    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


class _Spanned(_Base):
    """A node with an optional span. ``start_line``/``end_line``/``start_column``/``end_column`` are
    the 1.x attribute paths, read off ``span`` (``-1`` without one); ``code`` is the UTF-8 byte slice
    ``source_bytes[span.bytes[0]:span.bytes[1]]`` of the owning unit, decoded (J-15). ``""`` only for
    the documented span-less case (implicit callables); a spanned node that was not threaded into a
    :class:`JCompilationUnit` (J-13) raises rather than returning a silent empty."""

    span: Optional[JSpan] = None
    _unit: Optional["JCompilationUnit"] = PrivateAttr(default=None)

    @property
    def start_line(self) -> int:
        return self.span.start[0] if self.span else -1

    @property
    def end_line(self) -> int:
        return self.span.end[0] if self.span else -1

    @property
    def start_column(self) -> int:
        return self.span.start[1] if self.span else -1

    @property
    def end_column(self) -> int:
        return self.span.end[1] if self.span else -1

    def _slice(self, span: Optional[JSpan]) -> str:
        if span is None:
            return ""
        if self._unit is None:
            raise RuntimeError(f"{getattr(self, 'id', type(self).__name__)} is not threaded — construct through JApplication/JCompilationUnit")
        return self._unit.slice(span)

    @property
    def code(self) -> str:
        return self._slice(self.span)


class _Node(_Spanned):
    """A spanned node with a ``can://`` ``id``. Identity is the id: nodes from independent parses of the
    same artifact compare equal and hash alike, and the owner back-references stay out of ``==``."""

    id: str

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


def _decorator_names(decorators: List[JDecorator]) -> List[str]:
    """The 1.x ``annotations`` spelling: ``@Name`` or ``@Name(arg, arg)`` with the source arguments."""
    return ["@" + d.name + (f"({', '.join(d.args)})" if d.args else "") for d in decorators]


def _first_comment(comments: List[JComment]) -> Optional[JComment]:
    return comments[0] if comments else None


# ----------------------------------------------------------------------------------------------
# Leaf models
# ----------------------------------------------------------------------------------------------


class JComment(_Spanned):
    """A comment or Javadoc block."""

    content: str
    is_javadoc: bool = False


class JImport(_Spanned):
    """An import declaration: ``name`` is the imported simple name, ``path`` the fully-qualified target."""

    name: str
    path: str
    is_static: bool = False
    is_wildcard: bool = False


class JDecorator(_Spanned):
    """An annotation use; ``args`` are the source spellings of its arguments (``name="accountejb"``)."""

    name: str
    args: List[str] = []


class JTypeParameter(_Spanned):
    name: str
    bounds: List[str] = []
    decorators: List[JDecorator] = []


class JEnumConstant(_Spanned):
    name: str
    arguments: List[str] = []
    comments: List[JComment] = []
    decorators: List[JDecorator] = []


class JRecordComponent(_Spanned):
    name: str
    type: str
    modifiers: List[str] = []
    decorators: List[JDecorator] = []
    comments: List[JComment] = []
    is_variadic: bool = False

    @property
    def annotations(self) -> List[str]:
        return _decorator_names(self.decorators)

    @property
    def comment(self) -> Optional[JComment]:
        return _first_comment(self.comments)

    @property
    def is_var_args(self) -> bool:
        return self.is_variadic


class JCallableParameter(_Spanned):
    name: Optional[str] = None
    type: str
    modifiers: List[str] = []
    decorators: List[JDecorator] = []
    is_variadic: bool = False

    @property
    def annotations(self) -> List[str]:
        return _decorator_names(self.decorators)


class JLocalVariable(_Spanned):
    """A local variable declaration inside a callable (the 1.x ``JVariableDeclaration``)."""

    name: str
    type: str
    initializer: Optional[str] = None
    comments: List[JComment] = []

    @property
    def comment(self) -> Optional[JComment]:
        return _first_comment(self.comments)


class JField(_Node):
    kind: Literal["field"] = "field"
    name: str
    type: str
    modifiers: List[str] = []
    comments: List[JComment] = []
    decorators: List[JDecorator] = []
    initializer: Optional[str] = None

    @property
    def annotations(self) -> List[str]:
        return _decorator_names(self.decorators)

    @property
    def variables(self) -> List[str]:
        return [self.name]

    @property
    def variable_initializers(self) -> Optional[Dict[str, str]]:
        return {self.name: self.initializer} if self.initializer is not None else None

    @property
    def comment(self) -> Optional[JComment]:
        return _first_comment(self.comments)


class JMetrics(_Base):
    cyclomatic: int


class JRefs(_Base):
    types: List[str] = []
    fields: List[str] = []


# ----------------------------------------------------------------------------------------------
# Body nodes and intra-callable edges (bare local-key endpoints)
# ----------------------------------------------------------------------------------------------


class JBodyNode(_Spanned):
    """One entry of a callable's ``body{}`` map, keyed ``L:C``, ``@entry``/``@exit``/``@formal_in:N``/
    ``@formal_out`` or ``L:C/actual_in:N``/``L:C/actual_out``. Every attribute is optional: the
    analyzer writes the empty call-shaped fields on non-call nodes too."""

    kind: str  # call | statement | branch | loop | switch | return | entry | exit | formal_in | formal_out | actual_in | actual_out
    callee: Optional[str] = None
    arguments: List[str] = []
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    argument_expr: List[str] = []
    callee_signature: Optional[str] = None
    method_name: Optional[str] = None
    return_type: Optional[str] = None
    accessibility: Optional[str] = None  # public | private | protected | package_private
    comment: Optional[JComment] = None
    is_static_call: Optional[bool] = None
    is_constructor_call: bool = False
    of: Optional[str] = None
    parent: Optional[str] = None


class JCfgEdge(_Base):
    src: str
    dst: str
    kind: str  # fallthrough | true | false | return | loop_back | exception | break | switch_case


class JCdgEdge(_Base):
    src: str
    dst: str


class JDdgEdge(_Base):
    src: str
    dst: str
    var: Optional[str] = None
    prov: List[str] = []  # ssa | points-to


class JSummaryEdge(_Base):
    src: str
    dst: str


# ----------------------------------------------------------------------------------------------
# 1.x view models the wire no longer carries as such
# ----------------------------------------------------------------------------------------------


class JCRUDOperation(_Base):
    """Not emitted by codeanalyzer-java 3.0.1 (upstream #187); kept for import compatibility."""

    line_number: int
    operation_type: Optional[CRUDOperationType] = None


class JCRUDQuery(_Base):
    """Not emitted by codeanalyzer-java 3.0.1 (upstream #187); kept for import compatibility."""

    line_number: int
    query_arguments: Optional[List[str]] = None
    query_type: Optional[CRUDQueryType] = None


class JCallSite(_Base):
    """The 1.x per-call record, built on demand from a ``call`` body node (:meth:`from_body_node`).
    The four visibility booleans derive from ``accessibility`` and are ``None`` when the callee was
    not resolved."""

    comment: Optional[JComment] = None
    method_name: str
    receiver_expr: str = ""
    receiver_type: str = ""
    argument_types: List[str] = []
    argument_expr: List[str] = []
    return_type: str = ""
    callee_signature: str = ""
    is_static_call: Optional[bool] = None
    is_private: Optional[bool] = None
    is_public: Optional[bool] = None
    is_protected: Optional[bool] = None
    is_unspecified: Optional[bool] = None
    is_constructor_call: bool = False
    crud_operation: Optional[JCRUDOperation] = None
    crud_query: Optional[JCRUDQuery] = None
    start_line: int = -1
    start_column: int = -1
    end_line: int = -1
    end_column: int = -1

    @classmethod
    def from_body_node(cls, node: JBodyNode) -> "JCallSite":
        acc = node.accessibility
        return cls(
            comment=node.comment,
            method_name=node.method_name or "",
            receiver_expr=node.receiver_expr or "",
            receiver_type=node.receiver_type or "",
            argument_types=node.argument_types,
            argument_expr=node.argument_expr,
            return_type=node.return_type or "",
            callee_signature=node.callee_signature or "",
            is_static_call=node.is_static_call,
            is_private=None if acc is None else acc == "private",
            is_public=None if acc is None else acc == "public",
            is_protected=None if acc is None else acc == "protected",
            is_unspecified=None if acc is None else acc == "package_private",
            is_constructor_call=node.is_constructor_call,
            start_line=node.start_line,
            start_column=node.start_column,
            end_line=node.end_line,
            end_column=node.end_column,
        )


# ----------------------------------------------------------------------------------------------
# Callable and type — mutually recursive (local classes live under a callable's ``types``)
# ----------------------------------------------------------------------------------------------


class JCallable(_Node):
    """A method, constructor or initializer (``<clinit>$N()``). ``cfg``/``cdg``/``ddg`` are present
    from L3, ``summary`` from L4; ``None`` means the level did not compute them. Implicit callables
    (default constructors) carry no span, body, parameters, metrics or declaration."""

    kind: str  # method | constructor | initializer
    signature: str
    parameters: List[JCallableParameter] = []
    return_type: Optional[str] = None
    error_channel: List[str] = []
    modifiers: List[str] = []
    decorators: List[JDecorator] = []
    type_parameters: List[JTypeParameter] = []
    body_span: Optional[JSpan] = None
    declaration: Optional[str] = None
    is_implicit: bool = False
    comments: List[JComment] = []
    is_entrypoint: bool = False
    metrics: Optional[JMetrics] = None
    refs: Optional[JRefs] = None
    local_variables: List[JLocalVariable] = []
    body: Dict[str, JBodyNode] = {}
    cfg: Optional[List[JCfgEdge]] = None
    cdg: Optional[List[JCdgEdge]] = None
    ddg: Optional[List[JDdgEdge]] = None
    summary: Optional[List[JSummaryEdge]] = None
    types: Dict[str, "JType"] = {}  # local / anonymous classes declared in the body
    _owner_type: Optional["JType"] = PrivateAttr(default=None)

    # -- 1.x views ----------------------------------------------------------------------------

    @property
    def code(self) -> str:
        """The 1.x ``code``: the **body block** (``body_span``), which is what ``code_start_line``
        and ``TreesitterJava.get_calling_lines`` were written against; the declaration slice
        (``span``) only when there is no body (abstract / interface methods)."""
        return self._slice(self.body_span or self.span)

    @property
    def code_start_line(self) -> int:
        if self.body_span is not None:
            return self.body_span.start[0]
        return self.start_line

    @property
    def annotations(self) -> List[str]:
        return _decorator_names(self.decorators)

    @property
    def thrown_exceptions(self) -> List[str]:
        return self.error_channel

    @property
    def cyclomatic_complexity(self) -> Optional[int]:
        return self.metrics.cyclomatic if self.metrics is not None else None

    @property
    def variable_declarations(self) -> List[JLocalVariable]:
        return self.local_variables

    @property
    def referenced_types(self) -> List[str]:
        return self.refs.types if self.refs is not None else []

    @property
    def accessed_fields(self) -> List[str]:
        return self.refs.fields if self.refs is not None else []

    @property
    def call_sites(self) -> List[JCallSite]:
        return [JCallSite.from_body_node(n) for n in self.body.values() if n.kind == "call"]

    @property
    def is_constructor(self) -> bool:
        return self.kind == "constructor"

    @property
    def is_static(self) -> bool:
        return "static" in self.modifiers

    @property
    def crud_operations(self) -> List[JCRUDOperation]:
        return []

    @property
    def crud_queries(self) -> List[JCRUDQuery]:
        return []


class JType(_Node):
    """A class, interface, enum, annotation or record. The wire has no ``name``: the map key is the
    simple name and the id's last segment; :attr:`name` is stamped from the key (J-13)."""

    kind: Literal["class", "interface", "enum", "annotation", "record"]
    span: JSpan
    comments: List[JComment] = []
    modifiers: List[str] = []
    base_types: List[str] = []
    interfaces: List[str] = []
    decorators: List[JDecorator] = []
    type_parameters: List[JTypeParameter] = []
    is_entrypoint_class: bool = False
    enum_constants: List[JEnumConstant] = []
    record_components: List[JRecordComponent] = []
    fields: Dict[str, JField] = {}
    callables: Dict[str, JCallable] = {}
    types: Dict[str, "JType"] = {}  # nested member types
    _name: str = PrivateAttr(default="")
    _owner: Union["JType", JCallable, None] = PrivateAttr(default=None)

    @property
    def name(self) -> str:
        return self._name or self.id.rsplit("/", 1)[-1]

    def _enclosing_type(self) -> Optional["JType"]:
        owner = self._owner
        if isinstance(owner, JCallable):
            return owner._owner_type
        return owner

    @property
    def qualified_name(self) -> str:
        """``package.Outer.Inner`` — the source spelling (nested types joined with ``.``)."""
        enclosing = self._enclosing_type()
        if enclosing is not None:
            return f"{enclosing.qualified_name}.{self.name}"
        package = self._unit.package if self._unit is not None else ""
        return f"{package}.{self.name}" if package else self.name

    # -- 1.x views ----------------------------------------------------------------------------

    @property
    def is_interface(self) -> bool:
        return self.kind == "interface"

    @property
    def is_nested_type(self) -> bool:
        return isinstance(self._owner, JType)

    @property
    def is_local_class(self) -> bool:
        return isinstance(self._owner, JCallable)

    @property
    def is_inner_class(self) -> bool:
        return self.is_nested_type and self.kind == "class" and "static" not in self.modifiers

    @property
    def is_class_or_interface_declaration(self) -> bool:
        return self.kind in ("class", "interface")

    @property
    def is_enum_declaration(self) -> bool:
        return self.kind == "enum"

    @property
    def is_annotation_declaration(self) -> bool:
        return self.kind == "annotation"

    @property
    def is_record_declaration(self) -> bool:
        return self.kind == "record"

    @property
    def is_concrete_class(self) -> bool:
        return self.kind == "class" and "abstract" not in self.modifiers

    @property
    def extends_list(self) -> List[str]:
        return self.base_types

    @property
    def implements_list(self) -> List[str]:
        return self.interfaces

    @property
    def annotations(self) -> List[str]:
        return _decorator_names(self.decorators)

    @property
    def parent_type(self) -> str:
        """Qualified name of the enclosing type, ``""`` at top level (the 1.x value)."""
        enclosing = self._enclosing_type()
        return enclosing.qualified_name if enclosing is not None else ""

    @property
    def field_declarations(self) -> List[JField]:
        return list(self.fields.values())

    @property
    def callable_declarations(self) -> Dict[str, JCallable]:
        return self.callables

    @property
    def nested_type_declarations(self) -> List[str]:
        """Qualified names of the member types (the 1.x value; both backends feed them to ``get_class``)."""
        return [t.qualified_name for t in self.types.values()]

    @property
    def initialization_blocks(self) -> List[JCallable]:
        return [c for c in self.callables.values() if c.kind == "initializer"]


# ----------------------------------------------------------------------------------------------
# Compilation unit
# ----------------------------------------------------------------------------------------------


def _thread_type(t: JType, name: str, unit: "JCompilationUnit", owner: Union[JType, JCallable, None]) -> None:
    t._name, t._unit, t._owner = name, unit, owner
    for f in t.fields.values():
        f._unit = unit
    for c in t.callables.values():
        c._unit, c._owner_type = unit, t
        for n in c.body.values():
            n._unit = unit
        for ln, lt in c.types.items():
            _thread_type(lt, ln, unit, c)
    for nn, nt in t.types.items():
        _thread_type(nt, nn, unit, t)


class JCompilationUnit(_Node):
    """One ``.java`` file. The symbol-table key is its repo-relative path (:attr:`file_path`); the
    wire carries no ``file_path``/``package_name``. The wire key ``imports`` holds structured
    :class:`JImport` records, exposed as :attr:`import_declarations`; the 1.x ``imports`` (a list of
    paths) is the property of that name."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    kind: Literal["module"] = "module"
    span: JSpan
    package: str
    source: str
    comments: List[JComment] = []
    import_declarations: List[JImport] = Field(default=[], alias="imports")
    types: Dict[str, JType] = {}
    content_hash: Optional[str] = None
    _file_path: Optional[str] = PrivateAttr(default=None)
    _source_bytes: Optional[bytes] = PrivateAttr(default=None)  # None when ``source`` is ASCII

    def model_post_init(self, __context: Any) -> None:
        # Private attrs are not dumped, so model_dump/model_validate round-trips are unaffected.
        self._source_bytes = None if self.source.isascii() else self.source.encode("utf-8")
        for c in self.comments:
            c._unit = self
        for i in self.import_declarations:
            i._unit = self
        for name, t in self.types.items():
            _thread_type(t, name, self, None)

    def slice(self, span: JSpan) -> str:
        """``source`` between the span's UTF-8 byte offsets (J-15); a plain index when the file is ASCII."""
        b0, b1 = span.bytes
        if self._source_bytes is None:
            return self.source[b0:b1]
        return self._source_bytes[b0:b1].decode("utf-8")

    # -- 1.x views ----------------------------------------------------------------------------

    @property
    def file_path(self) -> str:
        if self._file_path is None:
            raise RuntimeError(f"{self.id} has no file_path — it is the symbol-table key, stamped by JApplication")
        return self._file_path

    @property
    def package_name(self) -> str:
        return self.package

    @property
    def imports(self) -> List[str]:
        return [i.path for i in self.import_declarations]

    @property
    def type_declarations(self) -> Dict[str, JType]:
        return self.types

    @property
    def is_modified(self) -> bool:
        """Always ``False``: the analyzer emits a snapshot, never an edit state."""
        return False

    @property
    def code(self) -> str:
        return self.source


# ----------------------------------------------------------------------------------------------
# Application-scope overlays: call graph, param edges, externals, artifact layer
# ----------------------------------------------------------------------------------------------


class JCallGraphEdge(_Base):
    """A wire call-graph edge: ``can://`` endpoints, provenance tokens ``declared`` / ``rta``."""

    src: str
    dst: str
    prov: List[str] = []
    weight: int = 1


class JParamEdge(_Base):
    """An L4 ``param_in``/``param_out`` edge with global endpoints
    (``<callable id>@L:C/actual_in:N`` → ``<callable id>@formal_in:N``)."""

    src: str
    dst: str


class JExternalSymbol(_Base):
    """A call target outside the project, keyed by its ``@external/…`` id on the application.
    Declared in the analyzer's schema; not emitted for daytrader8 at any level."""

    kind: str
    signature: str
    declaring_type: Optional[str] = None


class JConfigKey(_Base):
    id: str
    key: str
    namespace: str
    value: Optional[str] = None
    span: Optional[JSpan] = None
    references: List[str] = []


class JArtifact(_Base):
    """A recognized non-code file (config, manifest, build descriptor)."""

    id: str
    kind: Literal["artifact"] = "artifact"
    path: str
    format: str
    roles: List[str] = []
    size_bytes: int
    sha256: str
    source: str = ""
    text_truncated: bool = False
    extraction: str = "none"
    config_keys: List[JConfigKey] = []


class JDependency(_Base):
    group: Optional[str] = None
    name: str
    ecosystem: str = "maven"
    spec: str = ""
    kind: str = "runtime"
    extras: List[str] = []
    declared_in: str = ""
    direct: bool = True
    locked_version: Optional[str] = None
    prov: List[str] = []


class JApplication(_Base):
    """The application root. ``call_graph``/``param_in``/``param_out`` are absent below the level
    that computes them — empty here, never ``None``."""

    id: str
    kind: Literal["application"] = "application"
    symbol_table: Dict[str, JCompilationUnit]
    call_graph: List[JCallGraphEdge] = []
    external_symbols: Optional[Dict[str, JExternalSymbol]] = None
    param_in: List[JParamEdge] = []
    param_out: List[JParamEdge] = []
    artifacts: Dict[str, JArtifact] = {}
    dependencies: List[JDependency] = []

    def model_post_init(self, __context: Any) -> None:
        for path, unit in self.symbol_table.items():
            unit._file_path = path


class JAnalyzer(_Base):
    name: str
    version: str


class JAnalysis(_Base):
    """The envelope ``analysis.json`` IS."""

    schema_version: str
    language: str
    max_level: int
    k_limit: Optional[int] = None  # not emitted by 3.0.1 at any level
    analyzer: JAnalyzer
    application: JApplication


# ----------------------------------------------------------------------------------------------
# Call-graph node payload built by the backends (not on the wire)
# ----------------------------------------------------------------------------------------------


class JMethodDetail(_Base):
    """The ``method_detail`` node attribute of ``get_call_graph()``: built from the string node key
    (``klass`` = everything before the signature's simple name) and the resolved callable."""

    method_declaration: Optional[str] = None
    # class is a reserved keyword in python. we'll use klass.
    klass: str
    method: JCallable

    def __repr__(self):
        return f"JMethodDetail({self.method_declaration})"

    def __hash__(self):
        return hash((self.klass, self.method.id))


# ----------------------------------------------------------------------------------------------
# 1.x compatibility aliases
# ----------------------------------------------------------------------------------------------

JGraphEdges = JCallGraphEdge  # J-14: the v1 rich edge is gone; the wire edge is the type
JVariableDeclaration = JLocalVariable
InitializationBlock = JCallable  # ``JType.initialization_blocks`` are the ``initializer`` callables


# Resolve forward references for the mutually-recursive models.
JCallable.model_rebuild()
JType.model_rebuild()
JCompilationUnit.model_rebuild()
JApplication.model_rebuild()
