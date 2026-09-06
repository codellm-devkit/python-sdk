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

"""Structured, language-agnostic results for the agent-facing query facade.

:class:`Diagnostic` is the "absence is never null" primitive: when a lookup can't produce the
value an agent asked for, it returns a ``Diagnostic`` naming *why* (one of a fixed set of
``code``s) instead of ``None`` or an empty collection — so a caller can tell "no such callable"
apart from "the graph doesn't speak this vocabulary" apart from "ambiguous, pick one".

:class:`LocateResult` (and the ``CallableRef`` / ``TypeRef`` / ``ModuleRef`` handles it carries)
answers the single most-needed query: a scanner alert arrives as ``file:line`` and the caller
needs the enclosing callable *and its source* in one round trip (see
:meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.locate`). ``node``/``span`` are typed
against ``codeanalyzer-python``'s models for this leg, so ``locate`` is declared on the *Python*
backend ABC rather than the generic cross-language one — a shared declaration typed on one
language's models is a contract no other language can satisfy. A later leg generalises ``node`` /
``span`` and hoists the declaration once Java or TypeScript needs the same shape.

:class:`EntrypointCoverage` is the same "absence is never null" discipline applied to
``get_entrypoints()``: that accessor's ``List[PyCallableOverview]`` return is frozen and cannot
itself distinguish "no entrypoints" from "the detection pass had gaps", so the coverage/failure
signal rides this separate, Python-typed accessor instead.

:class:`EdgePage` is E5's "a bound is never silent" as a return type: an accessor whose answer can
be millions of edges returns a page that says how big the whole answer is and where to resume,
so the caller is bounded without anything being discarded.

:class:`Slice` is the same discipline applied to a *traversal*, and lands on the other answer —
capped rather than paged — because the measured shape of the thing is different. See its
docstring for the numbers that decided it.
"""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from cldk.models.python import BodyNode, Span


class Diagnostic(BaseModel):
    """A structured, non-``None`` explanation for why a query came back empty or uncertain.

    Attributes:
        code: The fixed vocabulary of reasons a query can fail to produce a definite answer.
        message: A human-readable explanation, safe to surface directly to an agent or user.
        suggestions: Declared by the leg-1 spec for a ``did_you_mean`` diagnostic and **never
            populated**. E8 (leg 1.5) put typo-tolerant matching out of scope "not in the
            resolver, not in the error path", so nothing in the SDK constructs a ``Diagnostic``
            with suggestions or with ``code="did_you_mean"``; the codes actually emitted are
            ``file_not_in_graph``, ``module_scope``, ``module_source_unavailable`` and
            ``entrypoint_report_unavailable``. The field and the code stay because both are part
            of a published model contract (``docs/agent-api-reference.md``) that a caller may
            already destructure; removing either is a separate, breaking change.
    """

    code: Literal[
        "no_match",
        "ambiguous",
        "unknown_callable",
        "unknown_param",
        "did_you_mean",
        "level_too_low",
        "module_scope",
        "file_not_in_graph",
        "module_source_unavailable",
        "unresolved_dispatch",
        "graph_schema_mismatch",
        "entrypoint_report_unavailable",
    ]
    message: str
    suggestions: list[str] = []


class ModuleRef(BaseModel):
    """A lightweight handle on a module — enough to name it and fetch it again.

    Always present on a :class:`LocateResult`, even when the position couldn't be resolved any
    further (a ``file_not_in_graph`` result still echoes the path the caller asked about).
    """

    path: str
    module_name: str | None = None


class TypeRef(BaseModel):
    """A lightweight handle on a class/type — the ``callable``'s owner, when it has one."""

    signature: str
    name: str


class CallableRef(BaseModel):
    """A lightweight handle on a callable — enough to call back into ``get_method`` or
    ``get_method_bodies`` without re-walking the symbol table."""

    signature: str
    name: str
    class_signature: str | None = None


class LocateResult(BaseModel):
    """Resolve a ``file:line`` position to its enclosing callable, source in hand.

    The outcomes below must stay distinguishable — an ambiguous empty is a defect (see the module
    docstring): a caller must be able to tell "inside a callable" apart from "a real position at
    module scope" apart from "the gap between two callables" (also module scope, and never snapped
    to the nearest callable) apart from "this file was never analysed".

    Attributes:
        node: The innermost body node containing the position, if the graph has one that precise.
            ``None`` does not mean "not found" — see ``callable``/``diagnostics`` for that.
        node_id: ``node``'s identifier for :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_source`,
            or ``None`` exactly when ``node`` is ``None``. It is the analyzer's own id for that
            node (``"<callable can:// id>@<body key>"``), read off the graph where the backend can
            and composed the emitter's way where it cannot — **an opaque handle, not a string to
            parse or build**. Treat it as something to pass back, and address a callable by its
            ``callable.signature`` instead, the same key :meth:`get_method_bodies` uses.
        callable: The enclosing callable, or ``None`` if the position is not inside one (module
            scope) or the file isn't in the graph at all.
        type: The class owning ``callable``, or ``None`` for a module-level function/callable
            with no owning class.
        module: The module the position was asked about — always present, even when unresolved.
        source: The slice a caller will ask for next: the enclosing callable's text, or the
            module's text when there is no enclosing callable. Never ``None`` — "absence is never
            null" (see :class:`Diagnostic`); an unrecoverable body is an empty string, not a
            missing field. Backends differ on one case and say so rather than faking it: the
            Neo4j backend cannot produce *module* text (``:PyModule`` carries no ``source``
            property, only ``file_key`` / ``module_name`` / ``content_hash`` / ``last_modified`` /
            ``file_size``), so a module-scope result there is ``""`` plus a
            ``module_source_unavailable`` diagnostic. Callable text is available on both.
        span: The span the ``source`` slice covers. Which of its fields are meaningful depends on
            the backend, because they carry different data: the local backend returns the
            analyzer's real :class:`~cldk.models.python.Span` (1-based line, 0-based column, and
            UTF-8 byte offsets into the module source), while the Neo4j graph projects only
            ``start_line`` / ``end_line`` on ``:PyCallable`` and ``:PyBodyNode`` — so over Neo4j
            the line components are real and the columns and ``bytes`` are ``0`` placeholders,
            never offsets to slice with.
        diagnostics: Empty when the position resolved inside a callable; ``module_scope`` for a
            real position with no enclosing callable (joined by ``module_source_unavailable`` when
            the backend cannot supply the module text); ``file_not_in_graph`` when the path names
            no analysed module.
    """

    node: BodyNode | None
    node_id: str | None = None
    callable: CallableRef | None
    type: TypeRef | None
    module: ModuleRef
    source: str
    span: Span
    diagnostics: list[Diagnostic] = []


class SliceNode(BaseModel):
    """One addressed position in a program: what a name resolved to, in the caller's vocabulary.

    The unit the addressing layer (leg 1.5, E6-E8) hands back, and the unit the traversals built on
    it will carry. Every field is something a person or an agent already understands — a path, a
    line, a dotted callable name, a variable name. Nothing here requires knowing the analyzer's id
    grammar, and nothing here carries an ordinal: a parameter is ``kind="parameter"`` with
    ``name="invoice_id"``, never ``formal_in:1`` (E7 — "an interface requiring the caller to know
    that ``self`` occupies slot 0 will be got wrong by a model and misread by a person").

    ``ref`` is the one exception, and it is deliberately opaque. The graph addresses nodes by a
    ``can://`` id; the caller must never have to read, parse or assemble one (E6), because that
    would mean reimplementing the analyzer's id grammar in the SDK and breaking silently the day it
    changes. So the id rides here, in the same vocabulary as
    :attr:`LocateResult.node_id` — the analyzer's own id, read off the graph, joinable back to the
    node it names — and is documented as something to *pass back*, not to read.

    Attributes:
        file: The module's repo-relative path, the same vocabulary as ``locate().module.path``,
            ``PyCallableOverview.path`` and the symbol table's keys.
        line: 1-based. For a position with no source region of its own — a parameter is a synthetic
            dataflow vertex, not a span in the file — this is the enclosing callable's first line,
            which is where a reader would go looking for it.
        callable: The enclosing callable's dotted signature. Never a ``can://`` id.
        kind: What kind of position this is: ``parameter``, ``global``, ``capture``, ``argument``,
            ``statement``, ``call``, ``return`` or ``callable`` (the callable itself, what
            :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.resolve_callable` returns).
            ``global`` and ``capture`` are the two values that *enter* a callable without being
            parameters — a module global the callable reads and a name closed over from an
            enclosing scope. On a real application 84% of the values entering a callable are
            globals, so collapsing them into ``parameter`` mislabelled most of the domain.
        name: The value's name where it has one (a parameter, a global, an argument), ``None``
            where it does not (a statement). Always the readable identifier as written in the
            source — never the analyzer's internal spelling of it.
        defined_in: For a ``global``, the module it is defined in (``"payment"``) — the same
            vocabulary as :attr:`ModuleRef.module_name`. ``None`` for everything else, whose
            defining scope is the enclosing callable in :attr:`callable`.
        source: The text, when the backend can produce it; ``None`` when it cannot. The Neo4j graph
            carries no text below callable granularity (see :class:`LocateResult`), so it is
            ``None`` there for anything finer.
        ref: The analyzer's own id for this node — **opaque**. Pass it back; do not parse it, and
            do not build one. The one sanctioned use is
            :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_source`, and that
            round-trips for a ``kind="callable"`` ref on either backend. A value's ref does **not**:
            ``parameter`` / ``global`` / ``capture`` are dataflow vertices with no source span, so
            neither backend has text to return for one.
    """

    file: str
    line: int
    callable: str
    kind: str
    name: str | None
    defined_in: str | None = None
    source: str | None = None
    ref: str


class EntrypointCoverage(BaseModel):
    """Coverage and failure record for ``codeanalyzer-python``'s entrypoint-detection pass —
    surfaces its ``PyEntrypointReport`` (``schema/py_schema.py``) so
    :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_entrypoints` returning ``[]``
    stays distinguishable between "the pass ran clean and this project genuinely has none" and
    "the pass had gaps". The pass's own docstring says it best: it "under-approximates by design,
    so silence is its failure mode" — this is what makes a gap visible instead of indistinguishable
    from "this project has no entrypoints".

    ``get_entrypoints()``'s ``List[PyCallableOverview]`` return is frozen and carries no room for
    this signal, so it rides a separate accessor
    (:meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_entrypoint_coverage`) instead.

    Attributes:
        frameworks_detected: Frameworks the pass recognized in this project (e.g. ``"flask"``).
        rulesets: Rule sources consulted (``"shipped"`` and/or ``"user:<path>"``).
        unresolved: Count of near-misses, keyed by rule/framework, that could not be resolved to a
            definite entrypoint — non-zero counts are exactly the under-approximation gap.
        errors: Hard failures the detection pass hit while running.
        diagnostics: Non-empty when a backend cannot supply this report at all: the Neo4j
            projection does not carry ``PyApplication.entrypoint_report`` on the graph (only the
            derived ``is_entrypoint``/``entrypoint_frameworks`` per-node properties), so it returns
            ``entrypoint_report_unavailable`` here instead of fabricating empty-but-clean-looking
            fields — the same "say so honestly" precedent as ``LocateResult``'s
            ``module_source_unavailable``. When ``diagnostics`` is non-empty, the other fields are
            not meaningful coverage information (there was none to report), not "no gaps found".
    """

    frameworks_detected: list[str] = []
    rulesets: list[str] = []
    unresolved: dict[str, int] = {}
    errors: list[str] = []
    diagnostics: list[Diagnostic] = []


E = TypeVar("E", bound=BaseModel)


class EdgePage(BaseModel, Generic[E]):
    """One page of an edge set, plus everything needed to tell what is missing from it.

    Per-callable scoping bounds *which* edges an accessor may return; it does not bound *how
    many*. Measured on odoo-slim-19, one callable — ``Website.configurator_apply`` — has 1,386,918
    DDG edges, 27% of the whole application's 5,134,655, while 15,520 of the 15,549 callables have
    fewer than 10,000. So the accessors that return edges page, and the ruling is **paginate, do
    not truncate**: a cap discards edges the caller may need and can only report that it did,
    whereas a page bounds the response while leaving every edge reachable.

    E5 requires that a bound is never silent. It is satisfied here without a second call:
    ``total`` is the size of the whole answer, ``next_cursor`` is ``None`` exactly when this page
    is the end of it, and the two together let a caller distinguish "this is everything" from
    "there is more" — and, for the D7 case, an empty *page* whose ``total`` is 0 from a page that
    merely ran out of room.

    **Why a page model and not a generator of pages.** A cursor is a value: it survives being
    serialized into a result, stored, and passed back later by a different process, which is how
    this surface is actually driven. A generator is a live object with a position, so it cannot
    cross that boundary — and it would also force the caller to consume pages in order when the
    common case (99.8% of callables) is one page that needs no loop at all.

    Attributes:
        edges: This page's edges, in the accessor's canonical order (see
            :func:`~cldk.analysis.python.backend.ddg_sort_key` and its siblings) — the same order
            on every backend, which is what makes page *n* the same page everywhere.
        total: The size of the whole edge set, not of this page. Reported on every page, so a
            caller reads the cost before deciding whether to walk the rest.
        next_cursor: An opaque handle naming where to resume — pass it back as ``cursor=`` to get
            the next page. ``None`` means this page ends the set. It is not for reading: it
            encodes the sort key of the last edge on the page, which is an implementation detail
            of the order, not part of the caller's vocabulary.
    """

    edges: list[E]
    total: int
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        """Whether edges remain after this page. Derived from ``next_cursor`` rather than stored,
        so the two cannot disagree."""
        return self.next_cursor is not None


class Slice(BaseModel):
    """A set of positions reached by one traversal, with the size of the whole answer alongside.

    **A slice is a set** (E2): no duplicates, and the order carries no meaning beyond making the
    cap deterministic — it is the node-id order, the one total order both backends can compute
    without agreeing on a traversal. A *path* is the sequence, and it is a different accessor
    returning a different type; neither is derived from the other, because a cone of 10,000 nodes
    contains millions of distinct paths.

    **Why this caps where** :class:`EdgePage` **pages.** The sibling per-callable accessors
    paginate, on the ruling that a cap discards edges a caller may need. Slices measured on
    odoo-slim-19 land the other way, for two reasons:

    * **The distribution is bimodal, not long-tailed.** Over 200 random ``formal_in`` seeds,
      ``slice_backward`` has median 1 and p95 195,790 (max 196,117); restricted to the 150 seeds
      that actually have callers, the median is 195,786. ``slice_forward`` over the same seeds is
      median 1, p95 440,270, max 440,662 — of 885,218 body nodes in the whole application. A slice
      is either a handful of nodes or a fifth to a half of the program, with almost nothing in
      between, and "page 3 of 20" of the second kind answers no question anyone asked.
    * **A slice cursor could not be cheap.** An :class:`EdgePage` cursor is a keyset position in
      an order the database already maintains, so each page costs one bounded query. A slice *is*
      the traversal, so resuming it means re-running the whole closure — the cost is in computing
      the set, not in shipping it.

    So the cap stays, and :attr:`total` is what keeps it from being the silent bound E5 forbids:
    a capped result reports the size of the whole slice, so "here are 10,000 of 195,784" arrives
    in one call and the caller learns the question was too broad instead of walking twenty pages
    to find out. The way to a *complete* answer is a narrower question — ``depth=`` bounds the
    traversal rather than the response, and what comes back is the whole slice of that question.

    **And that is why the traversals bound themselves by default** (five hops,
    :data:`~cldk.analysis.python.backend.DEFAULT_DEPTH`). "Here are 10,000 of 195,784" is honest
    but it is still an unprincipled 5% of a closure, and a caller who never passes ``depth=`` was
    getting it every time. Bounded, the same call answers completely: over 120 connected seeds
    measured in both directions, no slice at five hops reaches the cap. A slice you are reading is
    therefore normally one where :attr:`truncated` is ``False`` and :attr:`total` is the size of
    :attr:`nodes` — the truncated kind is now what you get when you ask for ``depth=None``.

    Attributes:
        nodes: The positions in the slice, ordered by :attr:`SliceNode.ref` and including the
            seed(s). ``source`` is ``None`` on every one of them: a slice answers *where*, and
            hydrating text a caller has not chosen to read is the cost E4 exists to avoid — pass
            the ones that matter to ``describe()``.
        roots: What the traversal started from — the resolved seed(s), also present in
            :attr:`nodes`. A list because ``backward_cone`` takes several sinks; see :attr:`root`
            for the singular case.
        resolved: What the caller's name(s) matched, in the caller's vocabulary, so the inference
            is auditable rather than trusted.
        total: The size of the whole slice, not of :attr:`nodes` — the number the caller needs to
            decide whether to ask a narrower question.
        diagnostics: Empty on both Python backends today. Reserved for a backend that cannot
            answer the question asked, which must say which rather than return a partial answer
            that looks whole (the ``entrypoint_report_unavailable`` precedent).
    """

    nodes: list[SliceNode]
    roots: list[SliceNode]
    resolved: str
    total: int
    diagnostics: list[Diagnostic] = []

    @property
    def truncated(self) -> bool:
        """Whether a cap fired. Derived from :attr:`total` and :attr:`nodes` rather than stored, so
        the two cannot disagree — the same construction as :attr:`EdgePage.has_more`."""
        return self.total > len(self.nodes)

    @property
    def root(self) -> SliceNode:
        """The single seed, for the accessors that take one.

        Raises:
            ValueError: This slice has several seeds (a multi-sink ``backward_cone``). Answering
                with the first would be a guess, and the caller wants :attr:`roots`.
        """
        if len(self.roots) != 1:
            raise ValueError(f"this slice has {len(self.roots)} roots, not one; read .roots")
        return self.roots[0]


#: How much a data-dependence provenance is worth as evidence, **least certain first**.
#:
#: The order is decided by what the three *mean*, not by how they sort as strings:
#:
#: * ``points-to`` is alias analysis — "these two expressions may name the same object". The most
#:   approximate of the three, and the only one that can relate values with no syntactic link.
#: * ``reaching-defs`` is a may-analysis over the CFG — "this definition may reach this use". It
#:   over-approximates along paths that never execute together.
#: * ``ssa`` is exact def-use in SSA form — the use *is* that definition, by construction.
#:
#: So certainty runs ``ssa`` > ``reaching-defs`` > ``points-to``, and the *weakest* hop of a path
#: — the one that caps how strongly a caller may state the flow — is the **most approximate** one.
#: Written down here, and pinned by a test, because the ordering reads backwards if you take
#: "weakest" to mean "smallest" and sort the words instead of ranking the analyses.
#:
#: Measured on odoo-slim-19: ``prov`` is a singleton list on **every** one of the 5,134,655
#: ``PY_DDG`` edges (``reaching-defs`` 3,036,102, ``points-to`` 1,548,237, ``ssa`` 550,316), so
#: each hop has exactly one provenance and "the weakest hop" is well defined rather than a
#: question about how to combine a set.
PROV_CERTAINTY = ("points-to", "reaching-defs", "ssa")


def prov_rank(prov: list[str]) -> int:
    """How certain a hop's provenance is: lower is weaker. See :data:`PROV_CERTAINTY`.

    A hop with **no** provenance ranks above every labelled one. ``prov`` is carried by ``PY_DDG``
    and by nothing else (verified on the live graph: ``PY_CDG``, ``PY_PARAM_IN``, ``PY_PARAM_OUT``
    and ``PY_SUMMARY`` carry no properties at all), and those four are structural facts —
    control dependence, argument binding, return binding — not may-analyses. An unlabelled hop
    therefore claims no approximation, and ranking it as the most certain is what stops it from
    being reported as the reason a flow is uncertain.

    An unrecognised provenance ranks weakest of all: a new label from a future analyzer is
    something this SDK has not been taught to trust, and treating it as strong would be the
    optimistic half of the guess.

    A hop carrying **several** provenances takes the weakest of them. That is the conservative
    reading — several labels could as easily mean "any one of these justifies the edge", in which
    case the strongest should win — and it is unobservable on real data today, because ``prov`` is
    a singleton on every edge of the live graph. Conservative is the direction to be wrong in:
    under-claiming certainty costs a caller a follow-up question, over-claiming it costs them a
    wrong conclusion.
    """
    if not prov:
        return len(PROV_CERTAINTY)
    return min((PROV_CERTAINTY.index(p) if p in PROV_CERTAINTY else -1) for p in prov)


class PathHop(BaseModel):
    """One edge of a flow, in the caller's vocabulary: where it went, and what justified it.

    Attributes:
        frm: The position the hop leaves.
        to: The position it arrives at.
        via: What kind of edge justified it — ``data``, ``control``, ``argument``, ``return``,
            ``summary`` (the five SDG relationships) or ``call`` (:meth:`call_paths_between`).
            Never the graph's own ``PY_DDG``/``PY_PARAM_IN`` spelling (E6).
        var: The variable the dependence is on, for a ``data`` hop; ``None`` for the rest, which
            carry no variable on the edge.
        prov: How the hop was established. Empty for everything but a ``data`` hop — see
            :func:`prov_rank`.
    """

    frm: SliceNode
    to: SliceNode
    via: str
    var: str | None = None
    prov: list[str] = []


class FlowPath(BaseModel):
    """**A path is a sequence** (E2), where a :class:`Slice` is a set.

    The order of :attr:`hops` is the order the value travels, and consecutive hops join up:
    ``hops[i].to.ref == hops[i + 1].frm.ref``. That is the whole difference from a slice, and it
    is why the two are separate accessors returning separate types rather than one derived from
    the other — a cone of 10,000 nodes contains millions of distinct paths, so a caller who wants
    to *argue* a flow needs the sequence and a caller who wants to *bound* one needs the set.

    Attributes:
        hops: The edges, in order. Never empty: a path with no hops is not a path, and the
            "does it flow at all" question is :meth:`flows_to_call` / :meth:`reaches`.
    """

    hops: list[PathHop]

    @property
    def weakest(self) -> PathHop:
        """The hop that caps how strongly this flow can be stated — the *most approximate* one.

        Derived rather than stored, the same construction as :attr:`Slice.truncated`, so it cannot
        disagree with :attr:`hops` and ``weakest in hops`` holds by definition. Ranked by
        :func:`prov_rank` (``ssa`` > ``reaching-defs`` > ``points-to``, unlabelled strongest);
        ``min`` is stable, so a tie is broken by position and the earliest weakest hop wins —
        which keeps the answer reproducible instead of depending on iteration order.
        """
        return min(self.hops, key=lambda h: prov_rank(h.prov))


class FlowPaths(list):
    """The paths a query returned, and whether there were more of them.

    A ``list`` subclass rather than a model: everything a caller does with paths — index them,
    iterate them, ``if paths:`` — is what a list does, and the only thing missing was E5's "a
    bound is never silent". So :attr:`truncated` rides alongside instead of a wrapper type that
    would make every caller write ``.paths`` first.

    :attr:`truncated` is a flag and not a ``total`` (which is how :class:`Slice` reports the same
    thing) because counting *every* shortest path costs a second full traversal to produce a
    number a caller cannot act on differently: at ``max_paths`` witnesses, "there are more" is the
    entire actionable content. The extent question — how far a value reaches — is
    ``slice_forward``, which does report its ``total``.
    """

    def __init__(self, paths, truncated: bool = False) -> None:
        super().__init__(paths)
        self.truncated = truncated
