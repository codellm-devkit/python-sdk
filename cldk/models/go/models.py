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

"""Go schema models — a pydantic mirror of the ``codeanalyzer-go`` (``cango``) ``analysis.json``,
schema v2, at full-depth L1/L2 (the scope the analyzer emits today).

The wire is one additive containment tree: ``GoAnalysis{analyzer, application}`` →
``GoApplication{symbol_table{module → types{}/functions{}}, call_graph}`` →
``GoType{callables{}/fields{}}`` → ``GoCallable{body{}}``. Every node above the callable leaf
carries a ``can://`` ``id`` and a :class:`GoSpan`; a module carries its full ``source`` once and
every node's text is a slice of it (``module.source[span.bytes]``).

Go is the SDK's first **class-less, type-centric** facade: a ``type`` is a ``struct`` or an
``interface`` (never a class), and package-level ``functions`` are kept distinct from receiver
``methods``. :class:`GoType` is therefore a **single model with a ``kind`` field**, not a
discriminated ``Union`` of per-kind subclasses like :class:`~cldk.models.typescript.TSType` — the
two Go kinds carry the same wire shape, so the union would be ceremony (CLDK design note, issue
python-sdk#1 / spec §4 SDK1).

**Go-native fields** carried as first-class typed fields the way TypeScript carries ``is_async`` /
``is_tsx`` (schema v2 "typed field" additions, recorded per-language): ``GoParam.is_variadic``
(``...T``), ``GoBodyNode.is_goroutine`` (a ``go f()`` spawn), ``GoCallable.error_channel`` (the
generalized ``(T, error)`` return idiom), ``GoCallable.source_file``.

``extra="ignore"`` (matching where TypeScript landed in #386): an additive ``cango`` release is
consumable without an SDK edit, at the cost of the drift detector — an unmodelled field is dropped
rather than failing loudly. :class:`_NullSafeBase` coerces a JSON ``null`` collection to its empty
default **before** validation: Go's zero-value idiom marshals a ``nil`` slice/map to ``null``, and
although ``cango`` emits ``[]``/``{}`` today, this keeps the SDK robust to a future emitter that
does not.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from cldk.models.identity import IdentityVersion

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import Literal


class _Base(BaseModel):
    #: ``ignore``, not ``forbid`` (per TypeScript #386): an additive analyzer release is consumable
    #: without an SDK edit. The cost is the drift detector — a field the SDK does not model is
    #: dropped silently rather than refusing to parse; a declared field is the only way a value is
    #: reachable.
    model_config = ConfigDict(extra="ignore")


class _NullSafeBase(_Base):
    """Coerce a JSON ``null`` into the field's empty collection default before validation.

    Go marshals a ``nil`` slice/map to JSON ``null``, which Pydantic v2 ``List[T]`` / ``Dict[K, V]``
    rejects. ``cango`` emits ``[]`` / ``{}`` on every sample measured, but a future emitter default
    could regress; this makes that invisible to the SDK rather than a hard parse error.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_collections(cls, data):
        if not isinstance(data, dict):
            return data
        for name, info in cls.model_fields.items():
            if data.get(name) is not None:
                continue
            # A collection default reaches us either as ``info.default`` (the ergonomic
            # ``= []`` / ``= {}`` field style) or as ``info.default_factory`` (``Field(
            # default_factory=list)``). Honor both so ``null`` coerces to the empty default
            # regardless of how the field was declared.
            if info.default_factory is not None:
                sentinel = info.default_factory()
            else:
                sentinel = info.default
            if isinstance(sentinel, (list, dict)):
                data[name] = sentinel
        return data


# ----------------------------------------------------------------------------------------------
# Span — the one universal attribute
# ----------------------------------------------------------------------------------------------


class GoSpan(_Base):
    """``start``/``end`` are ``[line, column]`` (1-based); ``bytes`` are ``[from, to]`` offsets into
    the owning module's ``source``."""

    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


# ----------------------------------------------------------------------------------------------
# Leaf models
# ----------------------------------------------------------------------------------------------


class GoImport(_Base):
    """One import binding on a module."""

    name: str
    path: str
    alias: Optional[str] = None
    span: Optional[GoSpan] = None


class GoParam(_Base):
    """A function / method parameter. ``is_variadic`` is Go's ``...T``."""

    name: str
    type: Optional[str] = None
    span: Optional[GoSpan] = None
    is_variadic: bool = False


class GoField(_Base):
    """A struct field (its map key is the field name)."""

    id: str
    kind: Literal["field"] = "field"
    span: Optional[GoSpan] = None
    type: Optional[str] = None


class GoMetrics(_Base):
    """Per-callable metrics. Only ``cyclomatic`` is emitted today; extensible map in spirit."""

    cyclomatic: int = 0


class GoBodyNode(_NullSafeBase):
    """One entry of a callable's ``body{}`` map, keyed by local id (``L:C`` or ``@tag``).

    ``kind`` is open, and widens with the analysis level: L1/L2 emit only ``call`` (a call site);
    L3 adds ``statement``/``entry``/``exit``/``branch``/``return``; L4 adds the four vertex kinds
    ``formal_in``/``formal_out``/``actual_in``/``actual_out``. ``callee`` is the one sanctioned
    ``null`` on the wire — unresolved at L1, refined to a ``can://`` id at L2. ``is_goroutine``
    marks a ``go f()`` spawn and ``is_deferred`` a ``defer f()``.

    ``id`` is the node's absolute ``can://`` address: the owning callable's id with the map key
    appended. **Never recover the callable from it by splitting on the first ``@``** — an external
    callable's own id contains ``/@external/`` before the vertex ``@``
    (``can://app/app/@external/bytes/Buffer.Read@formal_in:0``), so the first ``@`` is the external
    marker, not the vertex separator. Strip the known callable-id prefix instead; that is what
    :func:`~cldk.analysis.go.ids.body_key` does (spec §7, A22).

    ``of`` is what an L4 vertex is a vertex *of*: a parameter name for a ``formal_in``, or
    ``<return>``/``<return>:N`` for a ``formal_out``. It — **not** ``GoCallable.parameters[]`` — is
    how a formal resolves, because ``formal_in:0`` is the *receiver* on a method, which never
    appears in ``parameters`` (spec §7, A9).
    """

    id: Optional[str] = None  # L3+ (absent at L1/L2)
    kind: str
    span: Optional[GoSpan] = None
    callee: Optional[str] = None
    of: Optional[str] = None  # L4 vertices: the parameter / return slot this vertex is of
    parent: Optional[str] = None  # L3: enclosing body-node key
    is_goroutine: bool = False
    is_deferred: bool = False


class GoCallGraphEdge(_Base):
    """A wire call-graph edge: ``can://`` endpoints, open provenance tokens (``go/types``, …)."""

    src: str
    dst: str
    prov: List[str] = []
    weight: int = 1


# ----------------------------------------------------------------------------------------------
# L3 intraprocedural edges (``cfg`` / ``cdg`` / ``ddg`` on a callable) and the L4 ``summary``
# ----------------------------------------------------------------------------------------------


class GoCfgEdge(_Base):
    """A control-flow edge between two body-node keys of one callable.

    ``kind`` is open; v0.3.0 emits ``break``, ``continue``, ``defer_resume``, ``exception``,
    ``fallthrough``, ``false``, ``loop_back``, ``return``, ``switch_case`` and ``true``. It
    defaults to ``fallthrough`` to match :class:`~cldk.models.python.CfgEdge`, so an emitter that
    omits the unconditional case stays readable.
    """

    src: str
    dst: str
    kind: str = "fallthrough"


class GoCdgEdge(_Base):
    """A control-dependence edge. The (``src``, ``dst``) pair is the whole edge — a control
    dependence carries nothing else, and needs nothing else to be unique."""

    src: str
    dst: str


class GoDdgEdge(_Base):
    """A data-dependence edge, carrying the variable it flows and how it was derived.

    ``prov`` is an open set of provenance tokens; v0.3.0 emits ``reaching-defs`` (syntactic, L3),
    ``ssa`` and ``points-to`` (the L4 semantic delta, spec §4.10).
    """

    src: str
    dst: str
    var: Optional[str] = None
    prov: List[str] = []


class GoSummaryEdge(_Base):
    """A per-call-site summary edge (L4, spec §4.9): the shortcut from an ``actual_in`` of a call
    to the ``actual_out`` it reaches *through* the callee, so a walk can cross a call without
    descending into it.

    Known gap: global → global summary edges are omitted (codellm-devkit/codeanalyzer-go#30).
    Reachability is unaffected, but a depth-bounded walk may stop short where the shortcut is gone.
    """

    src: str
    dst: str
    var: Optional[str] = None


# ----------------------------------------------------------------------------------------------
# Callable
# ----------------------------------------------------------------------------------------------


class GoCallable(_NullSafeBase):
    """A package-level ``function`` or a receiver ``method`` (kept distinct — no module-as-class
    hack). ``error_channel`` generalizes the ``(T, error)`` return idiom; ``callables`` holds
    nested func literals / closures when non-empty."""

    id: str
    kind: str = "function"  # function | method
    span: Optional[GoSpan] = None
    signature: str
    parameters: List[GoParam] = []
    return_type: Optional[str] = None
    error_channel: List[str] = []
    metrics: Optional[GoMetrics] = None
    source_file: Optional[str] = None
    body: Dict[str, GoBodyNode] = {}
    callables: Dict[str, "GoCallable"] = {}
    # L3 overlays: the three intraprocedural graphs, over this callable's ``body`` keys. ``None``
    # (not ``[]``) below -a 3, so "the analyzer did not compute this" stays distinguishable from
    # "it computed it and there are no edges" — the distinction ``_require_dataflow`` gates on.
    cfg: Optional[List[GoCfgEdge]] = None
    cdg: Optional[List[GoCdgEdge]] = None
    ddg: Optional[List[GoDdgEdge]] = None
    # L4 overlay: per-call-site actual_in → actual_out shortcuts.
    summary: Optional[List[GoSummaryEdge]] = None

    def __hash__(self) -> int:
        return hash(self.signature)


# ----------------------------------------------------------------------------------------------
# Type — one model with a ``kind`` field; never a class.
# ----------------------------------------------------------------------------------------------


class GoType(_NullSafeBase):
    """A named Go type. All four kinds carry the same wire shape, so this is one model
    discriminated by ``kind`` rather than a ``Union`` of subclasses. ``base_types`` are embedded
    types; ``fields`` are the struct's data fields (empty for every other kind).

    The four kinds the analyzer emits (``internal/syntactic_analysis/symbol_table.go``):
    ``struct``, ``interface``, ``defined`` (``type Celsius float64``) and ``alias``
    (``type Alias = int``).

    This stays a ``Literal`` rather than an open ``str`` so a *new* kind is a loud failure and not
    a silent mis-read — but note what that costs: ``extra="ignore"`` does not cover a ``Literal``
    mismatch, so an unlisted kind fails the **whole document**, not just the one type. That is why
    omitting ``alias`` refused real ``-a 1`` output from any project declaring a type alias
    (python-sdk#436), independently of the analysis level or the dataflow overlays.
    """

    id: str
    kind: Literal["struct", "interface", "defined", "alias"]
    span: Optional[GoSpan] = None
    base_types: List[str] = []
    callables: Dict[str, GoCallable] = {}
    fields: Dict[str, GoField] = {}


# ----------------------------------------------------------------------------------------------
# Module
# ----------------------------------------------------------------------------------------------


class GoModule(_NullSafeBase):
    """A compilation unit (one ``.go`` file). The symbol-table key is its repo-relative path;
    ``source`` is the whole file's text, carried once, that every node's text slices from."""

    id: str
    kind: Literal["module"] = "module"
    span: Optional[GoSpan] = None
    package: str
    source: str
    imports: List[GoImport] = []
    types: Dict[str, GoType] = {}
    functions: Dict[str, GoCallable] = {}
    content_hash: Optional[str] = None


# ----------------------------------------------------------------------------------------------
# Application + envelope
# ----------------------------------------------------------------------------------------------


class GoExternalSymbol(_NullSafeBase):
    """A call target outside the project (library member / builtin), homed on the application as
    ``<appId>/@external/<module>/<name>`` and keyed by that id.

    Richer than its TypeScript counterpart: at L4 the analyzer synthesizes a **stand-in body** for
    a library callable so a flow can pass *through* it — ``body`` holds that callable's
    ``formal_in``/``formal_out`` vertices, and ``receiver`` the method's receiver (which is
    ``formal_in:0``, and is *not* in ``parameters``; spec §6, §7 A9).
    """

    id: str
    kind: str = "external"
    name: str
    module: str
    signature: Optional[str] = None
    parameters: List[GoParam] = []
    return_type: Optional[str] = None
    receiver: Optional[GoParam] = None
    body: Dict[str, GoBodyNode] = {}


class GoParamEdge(_Base):
    """An L4 ``param_in`` / ``param_out`` edge: the interprocedural binding between a call site's
    ``actual_in``/``actual_out`` vertex and the callee's ``formal_in``/``formal_out``. Endpoints
    are absolute ``can://`` ids (unlike the intraprocedural edges, which are callable-local keys),
    because the two ends live in different callables."""

    src: str
    dst: str
    var: Optional[str] = None


class GoDegraded(_NullSafeBase):
    """One degradation marker: an L4 step that fell back to a coarser result (spec §4.15).

    ``code`` is an open vocabulary of plain strings (``range_func``, ``unmapped_ssa``, ``no_vta``,
    ``ssa_build``, …). **This is the honesty signal**: a non-empty ``GoAnalysis.degraded`` means
    part of the graph is approximated, so the SDK must not certify *absence* of flow over it — a
    returned path is still a real may-flow, but "no path" is not a proof.
    """

    code: str
    detail: Optional[str] = None
    packages: List[str] = []
    callables: List[str] = []


class GoApplication(_NullSafeBase):
    """The application root: the containment tree (``symbol_table``) plus the app-scope overlays —
    ``call_graph`` at L2, and at L4 the interprocedural parameter bindings
    (``param_in``/``param_out``) and the library stand-ins (``external_symbols``).

    Note ``degraded`` is **not** here: it sits on the :class:`GoAnalysis` envelope beside
    ``application``, which is where ``internal/schema/v2/schema.go:63`` puts it.
    """

    application_name: str
    identity_namespace: str
    identity_version: IdentityVersion
    id: str
    kind: Literal["application"] = "application"
    symbol_table: Dict[str, GoModule]
    call_graph: List[GoCallGraphEdge] = []
    param_in: List[GoParamEdge] = []
    param_out: List[GoParamEdge] = []
    external_symbols: Dict[str, GoExternalSymbol] = {}


class GoAnalyzer(_Base):
    name: str
    version: str


class GoAnalysis(_Base):
    """The envelope ``analysis.json`` IS."""

    identity_version: IdentityVersion
    schema_version: str
    language: str
    max_level: int
    k_limit: Optional[int] = None  # L3+ (absent at L1/L2)
    analyzer: GoAnalyzer
    application: GoApplication
    #: L4 degradation markers; ``[]`` when the run degraded nothing and below ``-a 4``. Envelope
    #: scope, not application scope — see :class:`GoApplication`.
    degraded: List[GoDegraded] = []

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_degraded(cls, data):
        # The envelope omits ``degraded`` entirely when nothing degraded, but a Go emitter that
        # marshalled a nil slice would send ``null``, which ``List[GoDegraded]`` rejects. The
        # containment models get this from ``_NullSafeBase``; the envelope is a plain ``_Base``
        # (every other field is required or scalar), so it coerces its one collection here.
        if isinstance(data, dict) and data.get("degraded") is None:
            data["degraded"] = []
        return data


# Resolve the forward reference in the self-recursive callable.
GoCallable.model_rebuild()
