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

    At L1/L2 the only ``kind`` is ``call`` (a call site). ``callee`` is the one sanctioned ``null``
    on the wire — unresolved at L1, refined to a ``can://`` id at L2. ``is_goroutine`` marks a
    ``go f()`` spawn.
    """

    kind: str
    span: Optional[GoSpan] = None
    callee: Optional[str] = None
    is_goroutine: bool = False


class GoCallGraphEdge(_Base):
    """A wire call-graph edge: ``can://`` endpoints, open provenance tokens (``go/types``, …)."""

    src: str
    dst: str
    prov: List[str] = []
    weight: int = 1


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

    def __hash__(self) -> int:
        return hash(self.signature)


# ----------------------------------------------------------------------------------------------
# Type — one model with a ``kind`` field (struct | interface); never a class.
# ----------------------------------------------------------------------------------------------


class GoType(_NullSafeBase):
    """A Go ``struct`` or ``interface``. The two kinds carry the same wire shape, so this is one
    model discriminated by ``kind`` rather than a ``Union`` of subclasses. ``base_types`` are
    embedded types; ``fields`` are the struct's data fields (empty for an interface)."""

    id: str
    kind: Literal["struct", "interface"]
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


class GoApplication(_NullSafeBase):
    """The application root: the containment tree (``symbol_table``) plus the one app-scope overlay
    the analyzer emits at L2 (``call_graph``)."""

    id: str
    kind: Literal["application"] = "application"
    symbol_table: Dict[str, GoModule]
    call_graph: List[GoCallGraphEdge] = []


class GoAnalyzer(_Base):
    name: str
    version: str


class GoAnalysis(_Base):
    """The envelope ``analysis.json`` IS."""

    schema_version: str
    language: str
    max_level: int
    k_limit: Optional[int] = None  # L3+ (absent at L1/L2)
    analyzer: GoAnalyzer
    application: GoApplication


# Resolve the forward reference in the self-recursive callable.
GoCallable.model_rebuild()
