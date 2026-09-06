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

"""TypeScript schema models — a pydantic mirror of ``codeanalyzer-typescript/src/schema/schema.ts``
at the pinned release (1.2.0), schema v2.

The wire is one additive containment tree: ``TSAnalysis{analyzer, application}`` →
``TSApplication{symbol_table{module → types{}/functions{}/fields{}}, call_graph, …}`` →
``TSType{callables{}/fields{}}`` → ``TSCallable{body{}, cfg, cdg, ddg, summary}``. Every node
carries a ``can://`` ``id``, a ``kind`` and a :class:`TSSpan`; a module carries its full
``source`` once and every node's text is a slice of it.

What the 1.x models exposed as stored fields is kept as **properties** where the wire still has
the fact in another shape (``start_line`` over ``span``, ``code`` over ``source``, the per-kind
``classes``/``interfaces``/… maps over the unified ``types``). What the wire no longer carries
(``path``, ``accessed_symbols``, ``local_variables``, ``code_start_line``) is gone, not faked.

``extra="forbid"`` is intentional: drift between the analyzer's JSON and these models fails
loudly. The fields the next analyzer release (1.3.0) is known to add are already declared
``Optional`` so that pin bump changes no model.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from typing_extensions import Annotated, Literal


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------------------------
# Span — the one universal attribute
# ----------------------------------------------------------------------------------------------


class TSSpan(_Base):
    """``start``/``end`` are ``[line, column]`` (1-based); ``bytes`` are ``[from, to]`` offsets into
    the owning module's ``source``."""

    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


class _Spanned(_Base):
    """A node with a mandatory span. ``start_line``/``end_line``/``start_column``/``end_column``
    are the 1.x attribute paths, read off ``span``; ``code`` is sliced from the owning module's
    ``source``, which :class:`TSModule` threads in after validation."""

    span: TSSpan
    _source: Optional[str] = PrivateAttr(default=None)

    @property
    def start_line(self) -> int:
        return self.span.start[0]

    @property
    def end_line(self) -> int:
        return self.span.end[0]

    @property
    def start_column(self) -> int:
        return self.span.start[1]

    @property
    def end_column(self) -> int:
        return self.span.end[1]

    @property
    def code(self) -> Optional[str]:
        """The node's source text: ``module.source[span.bytes[0]:span.bytes[1]]``, sliced as
        **characters**. ``None`` until the node has been validated as part of a :class:`TSModule`.

        Caveat (codeanalyzer-typescript #174): the analyzer computes ``bytes`` as UTF-16 code-unit
        offsets (JavaScript string indices). Python indexes code points, so on a file containing
        astral characters (emoji, some CJK) the slice drifts after the first such character. The
        Neo4j projection slices the same offsets as bytes and disagrees with both on non-ASCII
        files. Both are documented lossiness until fixed upstream.
        """
        if self._source is None:
            return None
        return self._source[self.span.bytes[0] : self.span.bytes[1]]


# ----------------------------------------------------------------------------------------------
# Leaf models — the analyzer still emits these with flat line/column ints
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

    id: Optional[str] = None  # 1.3.0 additive
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


class TSOverloadSignature(_Base):
    """An overload signature attached to the implementation callable."""

    parameters: List[TSCallableParameter] = []
    return_type: Optional[str] = None
    type_parameters: List[TSTypeParameter] = []
    start_line: int = -1
    end_line: int = -1


# ----------------------------------------------------------------------------------------------
# Body nodes and intra-callable edges (bare local-key endpoints)
# ----------------------------------------------------------------------------------------------


class TSBodyNode(_Base):
    """One entry of a callable's ``body{}`` map, keyed by local id (``L:C`` or ``@tag``).

    ``kind`` is open: L1 emits ``call``/``config_access``, L3 adds ``statement``/``entry``/``exit``,
    L4 adds ``formal_in``/``formal_out``/``actual_in``/``actual_out``. ``callee`` is the one
    sanctioned ``null`` on the wire (a ``call`` node at L1, refined to an id at L2)."""

    id: Optional[str] = None  # 1.3.0 additive
    kind: str
    span: Optional[TSSpan] = None
    callee: Optional[str] = None
    of: Optional[str] = None
    parent: Optional[str] = None
    # call-node attributes
    method_name: Optional[str] = None
    receiver_expr: Optional[str] = None
    receiver_type: Optional[str] = None
    argument_types: List[str] = []
    type_arguments: List[str] = []
    return_type: Optional[str] = None
    is_constructor_call: bool = False
    is_optional_chain: bool = False
    # config_access attributes
    root: Optional[str] = None
    key: Optional[str] = None


class TSCfgEdge(_Base):
    src: str
    dst: str
    kind: str  # fallthrough | true | false | switch_case | loop_back | exception | return | break | continue | yield | await_resume


class TSCdgEdge(_Base):
    src: str
    dst: str


class TSDdgEdge(_Base):
    src: str
    dst: str
    var: Optional[str] = None
    prov: List[str] = []  # 1.2.0 emits ["reaching-defs"]; the analyzer reserves more


class TSSummaryEdge(_Base):
    src: str
    dst: str
    var: Optional[str] = None


# ----------------------------------------------------------------------------------------------
# Field — module-level binding, class attribute / interface property, or enum member
# ----------------------------------------------------------------------------------------------


class TSField(_Base):
    """One open shape for a module variable, a class attribute / interface property, a
    constructor parameter property and an enum member; each origin sets its own subset.
    ``span`` is absent for constructor parameter properties, in which case the 1.x line/column
    properties return ``-1``."""

    id: str
    kind: Literal["field"] = "field"
    span: Optional[TSSpan] = None
    name: str
    type: Optional[str] = None
    # module / namespace variable
    initializer: Optional[str] = None
    scope: Optional[str] = None  # module | namespace
    declaration_kind: Optional[str] = None  # const | let | var | using | unknown
    is_exported: bool = False
    # class attribute / interface property
    comments: List[TSComment] = []
    decorators: List[TSDecorator] = []
    accessibility: Optional[str] = None
    is_static: bool = False
    is_readonly: bool = False
    is_optional: bool = False
    is_abstract: bool = False
    # enum member
    value: Optional[str] = None

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


# ----------------------------------------------------------------------------------------------
# Entrypoints — declared now (1.3.0 additive), never emitted by 1.2.0
# ----------------------------------------------------------------------------------------------


class TSEntrypoint(_Base):
    """One way a callable or class is invoked from outside the application."""

    framework: str
    confidence: str = "certain"  # declared | certain | heuristic
    rule: str = ""
    ruleset: str = "shipped"  # shipped | user:<path>
    evidence: Optional[str] = None
    route: Optional[str] = None
    http_methods: List[str] = []
    via: Optional[str] = None


class TSEntrypointReport(_Base):
    """Coverage and failure record for the entrypoint pass."""

    frameworks_detected: List[str] = []
    rulesets: List[str] = []
    unresolved: Dict[str, int] = {}
    errors: List[str] = []


# ----------------------------------------------------------------------------------------------
# Callable
# ----------------------------------------------------------------------------------------------


class TSCallable(_Spanned):
    """A function / method / constructor / accessor / arrow function.

    ``cfg``/``cdg``/``ddg`` are present from L3, ``summary`` from L4; ``None`` means the level did
    not compute them, ``[]`` means it did and found none."""

    id: str
    kind: str = "function"  # function | method | constructor | getter | setter | arrow | function_expression
    name: str
    signature: str
    comments: List[TSComment] = []
    decorators: List[TSDecorator] = []
    parameters: List[TSCallableParameter] = []
    type_parameters: List[TSTypeParameter] = []
    return_type: Optional[str] = None
    cyclomatic_complexity: int = 0
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
    body: Dict[str, TSBodyNode] = {}
    callables: Dict[str, "TSCallable"] = {}  # nested closures — present only when non-empty
    types: Dict[str, "TSType"] = {}  # local classes — present only when non-empty
    cfg: Optional[List[TSCfgEdge]] = None
    cdg: Optional[List[TSCdgEdge]] = None
    ddg: Optional[List[TSDdgEdge]] = None
    summary: Optional[List[TSSummaryEdge]] = None
    entrypoints: Optional[List[TSEntrypoint]] = None  # 1.3.0 additive
    is_entrypoint: Optional[bool] = None  # 1.3.0 additive

    @property
    def inner_callables(self) -> Dict[str, "TSCallable"]:
        return self.callables

    @property
    def inner_classes(self) -> Dict[str, "TSClass"]:
        return {k: t for k, t in self.types.items() if t.kind == "class"}

    def __hash__(self) -> int:
        return hash(self.signature)


# ----------------------------------------------------------------------------------------------
# Types — one wire shape with a ``kind`` discriminant; five classes because they are the return
# types of existing accessors. The per-kind facets each class declares are exactly what the
# analyzer's builders set for that kind (builders.ts / heritage.ts at 1.2.0).
# ----------------------------------------------------------------------------------------------


class _Type(_Spanned):
    id: str
    name: str
    signature: str
    comments: List[TSComment] = []
    is_exported: bool = False
    is_ambient: bool = False

    def __hash__(self) -> int:
        return hash(self.signature)


class TSClass(_Type):
    """A class declaration."""

    kind: Literal["class"] = "class"
    callables: Dict[str, TSCallable] = {}
    fields: Dict[str, TSField] = {}
    decorators: List[TSDecorator] = []
    base_classes: List[str] = []  # spine: union of extends + implements (signature strings)
    implements_types: List[str] = []  # typed split: just the implemented interfaces
    is_abstract: bool = False
    type_parameters: List[TSTypeParameter] = []
    extends_ids: List[str] = []  # resolved can:// ids, present only when resolved
    implements_ids: List[str] = []
    entrypoints: Optional[List[TSEntrypoint]] = None  # 1.3.0 additive
    is_entrypoint: Optional[bool] = None  # 1.3.0 additive

    @property
    def methods(self) -> Dict[str, TSCallable]:
        return self.callables

    @property
    def attributes(self) -> Dict[str, TSField]:
        return self.fields


class TSInterface(_Type):
    """An interface declaration."""

    kind: Literal["interface"] = "interface"
    callables: Dict[str, TSCallable] = {}
    fields: Dict[str, TSField] = {}
    base_classes: List[str] = []  # extended interfaces (signature strings)
    type_parameters: List[TSTypeParameter] = []
    call_signatures: List[str] = []
    index_signatures: List[str] = []
    extends_ids: List[str] = []

    @property
    def methods(self) -> Dict[str, TSCallable]:
        return self.callables

    @property
    def properties(self) -> Dict[str, TSField]:
        return self.fields


class TSEnum(_Type):
    """An enum declaration; its members are ``fields`` carrying ``value``."""

    kind: Literal["enum"] = "enum"
    fields: Dict[str, TSField] = {}
    is_const: bool = False

    @property
    def members(self) -> List[TSField]:
        return list(self.fields.values())


class TSTypeAlias(_Type):
    """A type-alias declaration."""

    kind: Literal["type_alias"] = "type_alias"
    aliased_type: str = ""
    type_parameters: List[TSTypeParameter] = []


class TSNamespace(_Type):
    """A namespace / module block — a nested scope with the same buckets as a module."""

    kind: Literal["namespace"] = "namespace"
    types: Dict[str, "TSType"] = {}
    functions: Dict[str, TSCallable] = {}
    fields: Dict[str, TSField] = {}

    @property
    def classes(self) -> Dict[str, TSClass]:
        return {k: t for k, t in self.types.items() if t.kind == "class"}

    @property
    def interfaces(self) -> Dict[str, TSInterface]:
        return {k: t for k, t in self.types.items() if t.kind == "interface"}

    @property
    def enums(self) -> Dict[str, TSEnum]:
        return {k: t for k, t in self.types.items() if t.kind == "enum"}

    @property
    def type_aliases(self) -> Dict[str, TSTypeAlias]:
        return {k: t for k, t in self.types.items() if t.kind == "type_alias"}

    @property
    def namespaces(self) -> Dict[str, "TSNamespace"]:
        return {k: t for k, t in self.types.items() if t.kind == "namespace"}

    @property
    def variables(self) -> List[TSField]:
        return list(self.fields.values())


TSType = Annotated[
    Union[TSClass, TSInterface, TSEnum, TSTypeAlias, TSNamespace],
    Field(discriminator="kind"),
]


# ----------------------------------------------------------------------------------------------
# Module
# ----------------------------------------------------------------------------------------------


def _iter_spanned(node: Any) -> Iterator[_Spanned]:
    """Every callable and type beneath ``node`` (a module, type or callable), depth-first."""
    for c in getattr(node, "functions", {}).values():
        yield c
        yield from _iter_spanned(c)
    for c in getattr(node, "callables", {}).values():
        yield c
        yield from _iter_spanned(c)
    for t in getattr(node, "types", {}).values():
        yield t
        yield from _iter_spanned(t)


class TSModule(_Base):
    """A compilation unit (one ``.ts``/``.tsx``/``.js`` file). The symbol-table key is its
    repo-relative path; the wire carries no ``file_path``/``module_name``."""

    id: str
    kind: Literal["module"] = "module"
    span: TSSpan
    source: str
    imports: List[TSImport] = []
    exports: List[TSExport] = []
    comments: List[TSComment] = []
    types: Dict[str, TSType] = {}
    functions: Dict[str, TSCallable] = {}
    fields: Dict[str, TSField] = {}
    is_tsx: bool = False
    is_declaration_file: bool = False
    content_hash: Optional[str] = None

    @model_validator(mode="after")
    def _thread_source(self) -> "TSModule":
        # Private attrs are not dumped, so model_dump/model_validate round-trips are unaffected.
        for node in _iter_spanned(self):
            node._source = self.source
        return self

    @property
    def classes(self) -> Dict[str, TSClass]:
        return {k: t for k, t in self.types.items() if t.kind == "class"}

    @property
    def interfaces(self) -> Dict[str, TSInterface]:
        return {k: t for k, t in self.types.items() if t.kind == "interface"}

    @property
    def enums(self) -> Dict[str, TSEnum]:
        return {k: t for k, t in self.types.items() if t.kind == "enum"}

    @property
    def type_aliases(self) -> Dict[str, TSTypeAlias]:
        return {k: t for k, t in self.types.items() if t.kind == "type_alias"}

    @property
    def namespaces(self) -> Dict[str, TSNamespace]:
        return {k: t for k, t in self.types.items() if t.kind == "namespace"}

    @property
    def variables(self) -> List[TSField]:
        return list(self.fields.values())


# ----------------------------------------------------------------------------------------------
# Application-scope overlays: call graph, param edges, homed endpoints, artifact layer
# ----------------------------------------------------------------------------------------------


class TSCallGraphEdge(_Base):
    """A wire call-graph edge: ``can://`` endpoints, open provenance tokens (``tsc``, ``defuse``,
    ``import``, …)."""

    src: str
    dst: str
    prov: List[str] = []
    weight: int = 1


class TSParamEdge(_Base):
    """An L4 ``param_in``/``param_out`` edge with **global** ordinal endpoints."""

    src: str
    dst: str
    var: Optional[str] = None


class TSExternalNode(_Base):
    """A call target outside the project (library member / builtin), homed on the application
    as ``<appId>/@external/<module>/<name>`` — keyed by that id."""

    id: str
    kind: str = "external"
    module: str
    name: str


class TSSynthesizedNode(_Base):
    """An entry of the anonymous-callable compatibility index: the map key is the **older** id
    (``<enclosing>@<line>:<col>``) and ``id`` the tree id that replaced it; a residual fallback
    node (no tree home) has key == ``id`` and carries ``name``/``path``/``span``."""

    id: str
    kind: str = "callable"
    name: Optional[str] = None
    path: Optional[str] = None
    span: Optional[TSSpan] = None


class TSConfigKey(_Base):
    """A configuration key flattened out of a config-bearing artifact."""

    id: str
    key: str
    namespace: str  # env | json | yaml | toml | ini | properties | dockerfile
    value: Optional[Union[str, int, float, bool]] = None
    span: Optional[TSSpan] = None
    references: List[str] = []


class TSArtifact(_Base):
    """A recognized non-code file (config, manifest, CI, container spec)."""

    id: str
    kind: Literal["artifact"] = "artifact"
    path: str
    format: str
    roles: List[str] = []
    size_bytes: int
    sha256: str
    source: str
    extraction: str  # none | partial | full
    config_keys: List[TSConfigKey] = []


class TSDependency(_Base):
    """One third-party dependency, evidence-tagged via ``prov``."""

    name: str
    spec: str = ""
    kind: str  # runtime | dev | optional | peer | build
    extras: List[str] = []
    declared_in: str
    direct: bool
    locked_version: Optional[str] = None
    provides_imports: List[str] = []
    prov: List[str] = []


class TSImportBinding(_Base):
    """A non-relative import no declared dependency accounts for."""

    module: str
    bound_to: Optional[str] = None
    prov: List[str] = []


class TSConfigUse(_Base):
    """A recognized config read (global ordinal ``src``) joined to the ``TSConfigKey`` it names."""

    src: str
    dst: str
    prov: List[str] = []


class TSConfigRead(_Base):
    """A recognized config read that resolved to no declared key."""

    site: str
    callee: str
    key: Optional[str] = None
    reason: str  # non-literal | undefined-key
    prov: List[str] = []


class TSApplication(_Base):
    """The application root: the containment tree plus the app-scope overlays."""

    id: str
    kind: Literal["application"] = "application"
    symbol_table: Dict[str, TSModule]
    call_graph: List[TSCallGraphEdge] = []
    param_in: List[TSParamEdge] = []
    param_out: List[TSParamEdge] = []
    artifacts: Dict[str, TSArtifact] = {}
    dependencies: List[TSDependency] = []
    unresolved_imports: List[TSImportBinding] = []
    config_uses: List[TSConfigUse] = []
    config_reads: List[TSConfigRead] = []
    external_symbols: Optional[Dict[str, TSExternalNode]] = None  # L2+
    synthesized_callables: Optional[Dict[str, TSSynthesizedNode]] = None  # L2+
    entrypoint_report: Optional[TSEntrypointReport] = None  # 1.3.0 additive


class TSAnalyzer(_Base):
    name: str
    version: str


class TSAnalysis(_Base):
    """The envelope ``analysis.json`` IS."""

    schema_version: str
    language: str
    max_level: int
    k_limit: Optional[int] = None  # L3+
    analyzer: TSAnalyzer
    application: TSApplication


# ----------------------------------------------------------------------------------------------
# 1.x compatibility — names kept importable; the wire no longer carries these shapes
# ----------------------------------------------------------------------------------------------


class TSCallsite(_Base):
    """1.x per-call record. Not on the v2 wire (its view is the ``call`` node in ``body{}``);
    kept so callers that construct or type-check against it keep importing."""

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


class TSSymbol(_Base):
    """1.x accessed-symbol record. Not on the v2 wire; kept importable."""

    name: str
    scope: str
    kind: str
    type: Optional[str] = None
    qualified_name: Optional[str] = None
    is_builtin: bool = False
    lineno: int = -1
    col_offset: int = -1


TSCallEdge = TSCallGraphEdge
TSExternalSymbol = TSExternalNode
TSSynthesizedCallable = TSSynthesizedNode
TSClassAttribute = TSField
TSEnumMember = TSField
TSVariableDeclaration = TSField


# Resolve forward references for the mutually-recursive models.
TSCallable.model_rebuild()
TSClass.model_rebuild()
TSInterface.model_rebuild()
TSNamespace.model_rebuild()
TSModule.model_rebuild()
TSApplication.model_rebuild()
