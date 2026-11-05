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

"""The abstract base every Go analysis backend implements.

:class:`GoAnalysisBackend` parameterises the shared, cross-language
:class:`~cldk.analysis.commons.backend.AnalysisBackend` with Go's pydantic models and sets the
Go graph vocabulary prefixes (``N="Go"`` / ``P="GO_"``). The local
:class:`~cldk.analysis.go.codeanalyzer.codeanalyzer.GoCodeanalyzer` and the read-only
``GoNeo4jBackend`` both subclass it, which is what guarantees the two backends answer the facade
identically — a ``test_go_backend_contract`` asserts every abstract method is implemented on both.

**Go is the SDK's first class-less, type-centric facade.** The *facade* vocabulary the user sees
is ``get_types`` / ``get_functions`` / ``get_methods_of`` — never ``class``. The class-named slots
on this backend (``get_all_classes`` / ``get_class`` / ``get_all_methods_in_class`` / ``get_method``)
are **inherited from the generic base ABC** and filled only so the backend can instantiate (Python
refuses an abstract class with an unimplemented ``@abstractmethod``); they map ``TypeT=GoType``
(structs + interfaces) and are never user-facing. The base has no package-level-function slot, so
:meth:`get_functions` is the one type-centric accessor added here.

**The artifact/dependency/config layer** (the inherited ``get_artifacts`` / ``get_dependencies`` /
``get_config_keys`` / ``get_config_uses`` / ``get_unresolved_config_reads``) is not emitted by a
local L1/L2 ``cango`` run: those app-scope keys are absent from ``analysis.json``. Following the
Java precedent — ``[]`` is reserved for "the pass ran and found nothing" — the local backend
**raises** :data:`ARTIFACT_LAYER_UNAVAILABLE` rather than returning an ambiguous empty; the Neo4j
backend answers them for real from its ``Artifact`` / ``Package`` / ``ConfigKey`` nodes.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, Collection, Dict, List, Mapping, Sequence, Tuple

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    EdgeOrder,
    check_depth,
    check_max_paths,
    reject_bare_string,
)
from cldk.analysis.commons.graphs import edge_sort_key, sdg_rel_pattern, sdg_rels, taint_verdict, via_table
from cldk.analysis.commons.resolve import resolve_sanitizers
from cldk.analysis.commons.results import Diagnostic, EdgePage, FlowPath, FlowPaths, Slice, SliceNode, TaintResult
from cldk.models.go import (
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoCdgEdge,
    GoCfgEdge,
    GoDdgEdge,
    GoDegraded,
    GoExternalSymbol,
    GoField,
    GoModule,
    GoParam,
    GoType,
)

#: Raised by the local backend for the artifact/dependency/config accessors: a local L1/L2 cango
#: analysis does not emit that layer, so an empty result would be indistinguishable from a proved
#: absence. Both the message and the raise mirror codeanalyzer-java's unavailable-overlay pattern.
#: ``{app}`` is the application anchor name, never a can:// id.
ARTIFACT_LAYER_UNAVAILABLE = (
    "the artifact/dependency/config layer is not emitted by a local codeanalyzer-go L1/L2 analysis "
    "of {app!r}; query the Neo4j backend, which carries it"
)

#: Raised by :meth:`GoAnalysisBackend._require_connected_ports` when this application's
#: ``formal_in`` vertices carry no outgoing dependence edge: the interprocedural layer exists on the
#: wire but nothing crosses into it, so every slice and every path over a port would come back empty
#: and an empty there is indistinguishable from a proved absence. Mirrors the Java backend's
#: ``PORTS_DISCONNECTED`` (``java/backend.py``), down to being decided from the *data* rather than
#: from an analyzer version string.
PORTS_DISCONNECTED = (
    "{accessor} needs the interprocedural ports of {app!r} to carry dependence, and no formal_in "
    "vertex in this analysis has an outgoing dependence edge — so every answer would be empty for "
    "want of edges rather than for want of a flow. Re-emit at -a 4 (`cango -a 4`, or "
    "`--emit neo4j`, which is always full depth)."
)

_CFG_KEY, _CDG_KEY, _DDG_KEY = edge_sort_key("cfg"), edge_sort_key("cdg"), edge_sort_key("ddg")


def cfg_sort_key(edge: GoCfgEdge) -> Tuple:
    """The canonical order for :meth:`GoAnalysisBackend.get_cfg`: source, target, kind."""
    return _CFG_KEY(edge)


def cdg_sort_key(edge: GoCdgEdge) -> Tuple:
    """The canonical order for :meth:`GoAnalysisBackend.get_cdg`: source, target."""
    return _CDG_KEY(edge)


def ddg_sort_key(edge: GoDdgEdge) -> Tuple:
    """The canonical order for :meth:`GoAnalysisBackend.get_ddg`: source, target, variable, prov."""
    return _DDG_KEY(edge)


#: The three orders, shared verbatim with the sibling languages so a page is ordered over the ids a
#: caller sees. The Cypher column list is what the backends' ``WITH`` clause projects.
CFG_ORDER = EdgeOrder(cfg_sort_key, ("src", "dst", "coalesce(kind,'')"))
CDG_ORDER = EdgeOrder(cdg_sort_key, ("src", "dst"))
DDG_ORDER = EdgeOrder(ddg_sort_key, ("src", "dst", "coalesce(var,'')", "coalesce(prov,[])"))

#: The five relationship types a slice follows, in Go's ``GO_`` spelling.
SDG_RELS = sdg_rels("GO")
SDG_REL_PATTERN = sdg_rel_pattern("GO")
VIA = via_table("GO")


class GoAnalysisBackend(AnalysisBackend[GoApplication, GoModule, GoType, GoCallable, GoField, GoParam]):
    """Abstract base every Go analysis backend implements (see the module docstring).

    Inherited abstract (from :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "GO_"
    N: ClassVar[str] = "Go"

    # -----[ type-centric leaf accessors the base ABC does not cover ]-----
    @abstractmethod
    def get_functions(self) -> Dict[str, GoCallable]:
        """Every package-level function, keyed by signature.

        Go keeps package-level functions distinct from receiver methods (no module-as-class hack),
        and the generic base ABC has no slot for them — ``get_all_methods_in_class`` covers only a
        type's methods. This is the facade's ``get_functions``.
        """

    # -----[ call graph (keyed on the callable id, SDK2) ]-----
    @abstractmethod
    def get_callers(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``dst`` is ``callable_id`` — the callables that call it.

        Returns the full :class:`~cldk.models.go.GoCallGraphEdge` (``src``/``dst``/``prov``/
        ``weight``), losslessly: the caller ids are ``[e.src for e in result]``, and ``prov``/
        ``weight`` stay available. Both backends supply the same shape (the Neo4j ``GO_CALLS``
        relationship carries ``weight``/``prov`` as properties).
        """

    @abstractmethod
    def get_callees(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``src`` is ``callable_id`` — the callables it calls.

        Full edges, as :meth:`get_callers`; the callee ids are ``[e.dst for e in result]``.
        """

    # -----[ source + whole-program ]-----
    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """The source text of the node ``node_id`` — ``module.source[node.span.bytes]``."""

    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application's call graph as a JSON string (Tier A)."""

    # =====================================================================================
    # L3/L4 DATAFLOW (python-sdk#436). The names and signatures are
    # :class:`~cldk.analysis.python.backend.PythonAnalysisBackend`'s, deliberately — a caller who
    # has written against one language's dataflow surface can point the same code at another.
    #
    # DEPTH DEFAULTS DIFFER BY RETURN TYPE, as on every sibling. The two *slices* and the cone
    # default to :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH` and cap ``max_nodes``: a
    # bounded traversal is a complete answer to a narrower question, and ``total`` says what was
    # left out. The two *predicates* and the *path* queries default to ``depth=None`` — unbounded —
    # because a hop budget on a boolean is not a smaller answer but a wrong one: "no flow" and "no
    # flow within five hops" would collapse into the same ``False`` with nothing to tell them apart.
    #
    # ANALYSIS LEVEL. ``cfg``/``cdg``/``ddg`` exist from ``cango -a 3`` and the interprocedural
    # overlays from ``-a 4``. A backend attached to a shallower analysis MUST raise rather than
    # return an empty page (D7). ``--emit neo4j`` is always full depth, so only the local backend
    # can be below the line — which is why ``_require_dataflow`` is the *local* backend's method
    # while :meth:`_require_connected_ports` below, asked of the data, is declared for both.
    #
    # FOUR GO-SPECIFIC TRAPS, each measured on real ``cango`` 0.3.0 output rather than assumed
    # (spec `v2-l3-l4-dataflow.md` §7; the issue is python-sdk#436):
    #
    # 1. NEVER SPLIT A BODY-NODE ID ON ITS FIRST ``@``. An external callable's own id contains
    #    ``/@external/`` *before* the vertex ``@``:
    #    ``can://app/app/@external/bytes/Buffer.Read@formal_in:0``. Strip the known callable-id
    #    prefix instead — :func:`~cldk.analysis.go.ids.split_body_node_id`.
    # 2. ``of``, NOT ``parameters[]``, RESOLVES A FORMAL. On a method ``formal_in:0`` is the
    #    *receiver*, which never appears in ``GoCallable.parameters`` — measured: ``of`` equals
    #    ``parameters[0].name`` on only 174 of 276 callables, and the receiver reads ``_recv`` or
    #    the receiver variable's own name. A closure's captures are ``formal_in`` vertices too,
    #    with ``of`` spelled ``<capture>:x`` and ``parameters`` empty.
    # 3. A GLOBAL'S MODULE HALF IS SLASH-SEPARATED.
    #    :func:`~cldk.analysis.commons.resolve.value_candidate` turns ``<global>:example.com/lib::V``
    #    into the dotted ``example.com/lib.V``, so the spelling a Go programmer writes — ``lib.V`` —
    #    fails the dot-separated :func:`~cldk.analysis.commons.resolve.segment_match`. Go therefore
    #    matches a value on the package-qualified spelling as well; see
    #    :func:`~cldk.analysis.go.ids.go_value_names`.
    # 4. ``degraded`` IS THE HONESTY SIGNAL. A non-empty :meth:`get_degraded` means part of the
    #    graph is approximated, so *absence* of a path is not a proof over it. The flow surface
    #    reports this rather than deciding for the caller: it is a :class:`Diagnostic` on the
    #    result's ledger, which voids ``exhausted`` — never a refusal, because a returned path is
    #    still a real may-flow.
    # =====================================================================================

    # -----[ addressing ]-----
    @abstractmethod
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """The one callable ``name`` names, as an addressable position.

        Go accepts the receiver forms a Go programmer writes, resolved segment-wise against the
        full signature by :func:`~cldk.analysis.commons.resolve.resolve_callable_signature`:
        ``Query``, ``DB.Query``, ``(*DB).Query``, ``sql.DB.Query`` and
        ``database/sql.DB.Query`` all name the same method where that is unambiguous.

        Args:
            name: The callable's name, whole or a suffix of its signature on segment boundaries.
            in_class: Disambiguate by owning *type* (Go has no class; the keyword keeps the shared
                spelling so one caller's code ports between languages).
            in_module: Disambiguate by module — the repo-relative ``.go`` path, or the package's
                dotted name.

        Raises:
            AmbiguousName: More than one callable matched; the candidates are carried.
            SelectorNotInGraph: Nothing matched, or a keyword excluded every match.
        """

    @abstractmethod
    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """The one value of ``within`` that ``name`` names — a ``formal_in`` port vertex.

        The domain is the ``formal_in`` vertices of the resolved callable, which in Go is wider
        than "its parameters": a method's receiver is ``formal_in:0``, a closure's captures are
        ``formal_in`` vertices with ``of`` spelled ``<capture>:x``, and a package-level variable the
        callable reads is one with ``of`` spelled ``<global>:<pkgpath>::<Name>``. Each is translated
        out of the analyzer's vocabulary by
        :func:`~cldk.analysis.commons.resolve.value_candidate`, so the returned
        :class:`~cldk.analysis.commons.results.SliceNode` carries ``kind="parameter"`` /
        ``"capture"`` / ``"global"`` and a readable ``name`` — never a raw ``<global>:…`` string.

        **A global is matchable by the spelling a Go programmer writes.** ``value_candidate``
        renders the module half as the import path (``example.com/lib.V``), whose own separators are
        ``/``; matching only dot-wise would refuse ``lib.V``. Go accepts the package-qualified form
        as well (trap 3 above).

        Args:
            name: The value's name — bare (``id``, ``_recv``), package-qualified (``lib.V``) or the
                full import-path form.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`.

        Raises:
            AmbiguousName: More than one value matched.
            SelectorNotInGraph: No value of ``within`` carries that name — which is also how a
                caller attached to an analysis shallower than ``-a 4`` is refused, by name, since
                the port vertices do not exist there at all.
        """

    # -----[ per-callable graphs ]-----
    @abstractmethod
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCfgEdge]:
        """One page of the control flow edges within one callable.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable`.
            in_class: Disambiguate by owning type, as in :meth:`resolve_callable`.
            page_size: Most edges to return. See
                :data:`~cldk.analysis.commons.bounds.DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page; ``None`` starts at the beginning.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.go.GoCfgEdge`, each carrying the analyzer's ``kind``. Go's set —
            ``fallthrough``, ``true``, ``false``, ``switch_case``, ``loop_back``, ``exception``,
            ``return``, ``break``, ``continue`` and the Go-specific ``defer_resume`` (the edge back
            from a deferred call) — measured on ``cango`` 0.3.0. A conditional's two successors stay
            two edges discriminated by ``kind``, which is why ``kind`` is in :data:`CFG_ORDER`.
            Endpoints are the body nodes' own ``can://`` ids, the spelling :meth:`get_source`
            accepts.

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page of this
                accessor and this callable.
            CodeanalyzerUsageException: (local backend) built below ``-a 3``.
        """

    @abstractmethod
    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCdgEdge]:
        """One page of the control dependence edges within one callable.

        ``src`` is the branching node ``dst`` is control dependent on — post-dominance over the CFG
        :meth:`get_cfg` returns, computed by the analyzer and not re-derived here. Arguments, bounds
        and failures are :meth:`get_cfg`'s; the order is :data:`CDG_ORDER`.
        """

    @abstractmethod
    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoDdgEdge]:
        """One page of the data dependence edges within one callable.

        Each edge carries the variable it flows (``var``) and its evidence (``prov``).

        **Go's DDG has all three provenance tiers**, unlike TypeScript's single one: ``cango``
        emits ``reaching-defs`` (the syntactic L3 pass), and at ``-a 4`` adds ``ssa`` and
        ``points-to`` — the semantic delta of spec §4.10. So
        :func:`~cldk.analysis.commons.results.prov_rank`'s certainty ranking is live here, and a
        caller comparing two hops' evidence gets a real ordering.

        Arguments, bounds and failures are :meth:`get_cfg`'s; the order is :data:`DDG_ORDER`, which
        includes ``var`` and ``prov`` because one statement pair legitimately appears more than once
        carrying different variables, and collapsing those would drop dependences.
        """

    # -----[ slicing and reachability ]-----
    @abstractmethod
    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` depends on: reverse reachability over the SDG.

        The edge set is :data:`SDG_RELS` — data and control dependence within a callable, the two
        parameter-passing relationships across a call, and the callee summaries at a call site. All
        five point *with* the flow, so a backward slice follows them reversed.

        ``within`` is required: a value name is scoped by its callable and :meth:`resolve_value`
        cannot resolve one without it, so a ``None`` default would be a signature that raises on
        its own default.

        Args:
            src: The value's name, resolved by :meth:`resolve_value`.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`.
            depth: Most hops from the seed; ``None`` for the whole cone.
            max_nodes: Most nodes in the result. A cap that fires is reported by
                :attr:`~cldk.analysis.commons.results.Slice.complete`, with
                :attr:`~cldk.analysis.commons.results.Slice.total` the full size.

        Raises:
            AmbiguousName / SelectorNotInGraph: as :meth:`resolve_value`.
            ValueError: ``depth`` is not a positive ``int``, or ``max_nodes`` is below 1.
            CodeanalyzerExecutionException: this analysis's ports carry no dependence
                (:data:`PORTS_DISCONNECTED`).
        """

    @abstractmethod
    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` can affect: forward reachability over :data:`SDG_RELS`.

        Arguments, bounds and failures are :meth:`slice_backward`'s, followed with the flow rather
        than against it.
        """

    @abstractmethod
    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every callable that can reach any of these sinks, over the **call graph**.

        The coarse counterpart of :meth:`slice_backward`: a cone of *callables*, not of values, so
        the sinks are resolved by :meth:`resolve_callable` and the answer is what a triage pass
        narrows to before asking for a value-level path.

        Args:
            sinks: Callable names, each resolved by :meth:`resolve_callable`.
            depth: Most call hops; ``None`` for every ancestor.
            max_nodes: Most nodes in the result, reported as in :meth:`slice_backward`.
        """

    @abstractmethod
    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path from the callable ``src`` to the callable ``dst``?

        Over the same edge set :meth:`get_callers` / :meth:`get_callees` expose, so the boolean
        cannot disagree with the neighbours a caller can enumerate. ``depth`` defaults to ``None``:
        a hop budget on a boolean makes ``False`` ambiguous (see the block comment above).
        ``reaches(x, x)`` is the *cycle* question — ``True`` only if a real path returns to ``x``.
        """

    @abstractmethod
    def paths_between(
        self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS
    ) -> FlowPaths:
        """How the value ``src`` reaches the value ``dst`` — the witnesses, shortest first.

        **This proves a flow; it does not refute one.** An empty result means "no path was found",
        which a caller cannot tell from "the flow left the resolved graph". :meth:`taint` is the
        accessor that reports that distinction per pair, and it is what triage should call.

        Args:
            src / src_within: The source value and its callable, as :meth:`resolve_value`.
            dst / dst_within: The sink value and its callable.
            depth: Most hops; ``None`` (the default) for unbounded.
            max_paths: Most witnesses to return. Truncation is reported by
                :attr:`~cldk.analysis.commons.results.FlowPaths.complete`.

        Raises:
            ValueError: ``src`` and ``dst`` resolve to the same position
                (:func:`~cldk.analysis.commons.bounds.check_distinct_endpoints`), ``depth`` is not
                positive, or ``max_paths`` is below 1.
        """

    @abstractmethod
    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does the value ``src`` reach **any** value entering a call to ``callee``?

        The coarse taint question: "does my request parameter reach ``DB.Query`` at all", without
        naming which argument. ``callee`` is resolved by :meth:`resolve_callable`, so the Go
        receiver forms of that method apply here too.
        """

    @abstractmethod
    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does the value ``src`` reach ``callee``'s parameter ``arg`` specifically?

        :meth:`flows_to_call` narrowed to one port: ``arg`` is resolved by :meth:`resolve_value`
        *within* ``callee``, so a Go receiver is addressable as ``arg`` too.
        """

    # -----[ the honesty signals ]-----
    @abstractmethod
    def get_external_symbols(self) -> Dict[str, GoExternalSymbol]:
        """The library stand-ins this analysis synthesized, keyed by ``can://…/@external/…`` id.

        At ``-a 4`` the analyzer gives a library callable a stand-in body — ``formal_in`` /
        ``formal_out`` vertices and a ``receiver`` — so a flow can pass *through* ``bytes.Buffer.Read``
        or ``database/sql.DB.Query`` rather than stopping at the project boundary (spec §6). This is
        where a path's external hops are explained; it is not a dependency list.
        """

    @abstractmethod
    def get_degraded(self) -> List[GoDegraded]:
        """The degradation markers this analysis carries — empty when nothing was approximated.

        **Read this before trusting an absence.** A non-empty list means some L4 step fell back to
        a coarser result (``range_func``, ``unmapped_ssa``, ``no_vta``, …), so a path that *is*
        returned is still a real may-flow, but "no path" is not a proof over the affected packages
        and callables. :meth:`taint` reports this on its own ledger rather than making the judgement
        for the caller; this accessor is how a caller inspects it directly.
        """

    @abstractmethod
    def _require_connected_ports(self, accessor: str) -> None:
        """Refuse ``accessor`` when this application's ports carry no dependence.

        Named after the Java backend's method of the same name and for the same reason: the
        interprocedural layer can be *present* on the wire and still have nothing crossing into it,
        in which case every slice and every path over a port is empty for want of edges rather than
        for want of a flow — indistinguishable from a proved absence, which is the one answer this
        surface must never fake.
        """

    @property
    @abstractmethod
    def _ports_carry_dependence(self) -> bool:
        """Whether **this** application's ``formal_in`` vertices have any outgoing SDG edge.

        Asked of the data, once per backend, rather than hard-coded from an analyzer version: the
        refusal above is a statement about what was emitted, so a graph that connects the two layers
        must make these accessors work with no change here.
        """

    # -----[ taint: the one concrete method, so both backends cannot disagree on policy ]-----
    def taint(
        self,
        sources: Sequence[Tuple[str, str]],
        sinks: Sequence[Tuple[str, str]],
        sanitizers: Sequence[Tuple[str, str] | str] = (),
        *,
        depth: int | None = None,
        max_paths: int = DEFAULT_MAX_PATHS,
    ) -> TaintResult:
        """Which of these sources reach which of these sinks, and what to make of the ones that do not.

        The verb triage needs. :meth:`paths_between` *proves* a flow; a caller who gets ``[]`` back
        from it cannot tell "no flow exists" from "the flow left the resolved graph". ``taint()``
        runs m sources against n sinks in **one** traversal and reports that distinction per pair:
        the witnesses in :attr:`~cldk.analysis.commons.results.FlowPaths.paths`, the pairs an
        absence claim can be built on in
        :attr:`~cldk.analysis.commons.results.TaintResult.exhausted`, and everything else in
        ``unresolved``.

        **Sources, sinks and sanitizers are the caller's to supply.** This SDK ships no Go framework
        catalogue and derives no default set: a per-language vocabulary of taint sources is policy
        that rots, and this accessor is the mechanism.

        **A sanitizer is two mechanisms wearing one word**, told apart by shape. A bare ``str`` cuts
        a *callable* on the path — what a transforming sanitizer is. A ``(name, within)`` pair cuts a
        *variable* inside that callable, which is the only thing that severs a *validating* guard,
        because a guard never appears on the data path at all. Both cuts are applied **inside** the
        search, not to the rows it returns: filtering afterwards would report nothing for a source
        whose ten shortest paths are sanitized and whose eleventh is not — a false refutation, the
        one output this accessor must never produce.

        **Three rules, shared verbatim with the sibling languages (leg 4b, #382):** an explicit
        ``depth`` empties ``exhausted`` by rule — a pair with no path inside five hops is not
        refuted, it is unmeasured; ``complete`` is the *batch's* flag, so one blocked pair voids
        every absence claim in the result; and ``max_paths`` caps per pair, never per call.

        **Go adds a fourth: a degraded analysis cannot certify an absence.** When
        :meth:`get_degraded` is non-empty, part of the graph is approximated, so every pair's
        ``exhausted`` certification is voided by a diagnostic on the ledger. Not a refusal — a
        returned path over a degraded graph is still a real may-flow, and refusing would throw away
        the findings that matter most.

        **The level gate belongs to the walk**, not here: ``_require_dataflow`` is a local backend's
        method and a graph backend has no shallow mode to guard. :meth:`_taint_walk` opens with it.

        Args:
            sources: The values taint enters at, each ``(name, within)`` — :meth:`resolve_value`'s
                addressing.
            sinks: The values it must not reach, addressed the same way.
            sanitizers: Bare names cut callables; ``(name, within)`` pairs cut variables.
            depth: Most hops a path may take; ``None`` (the default) for no bound, because a bound
                turns a refutation into an artefact of the budget.
            max_paths: Most witnesses **per pair**.

        Returns:
            A :class:`~cldk.analysis.commons.results.TaintResult`.

        Raises:
            AmbiguousName: A name, or a sanitizer's ``within``, matched more than one thing.
            SelectorNotInGraph: A name matched nothing, or a sanitizer's shape disagrees with what
                it resolves to.
            TypeError: ``sources`` or ``sinks`` is a bare string, which would unpack into a pair.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, or ``sources``
                / ``sinks`` is empty (refused, not answered ``[]``).
            CodeanalyzerExecutionException: this analysis's ports carry no dependence.
        """
        # The order of the checks is part of the contract, cheapest-to-be-wrong-about first: the
        # bounds before any name, the names before any sanitizer, the sanitizers before the walk.
        check_depth(depth)
        check_max_paths(max_paths)
        reject_bare_string("sources", sources)
        reject_bare_string("sinks", sinks)
        if not sources:
            raise ValueError("sources= names nothing to taint from; pass at least one (name, within) pair")
        if not sinks:
            raise ValueError("sinks= names nothing to taint to; pass at least one (name, within) pair")
        self._require_connected_ports("taint")
        srcs = [self.resolve_value(name, within=within) for name, within in sources]
        dsts = [self.resolve_value(name, within=within) for name, within in sinks]
        cuts, cut_callables = resolve_sanitizers(sanitizers, resolve_callable=self.resolve_callable, edge_vars_in=self._edge_vars_in)
        rows, blocked = self._taint_walk(srcs, dsts, cuts=cuts, cut_callables=cut_callables, depth=depth, max_paths=max_paths)
        # A degraded analysis cannot certify an absence (trap 4), and the condition is a property of
        # the **whole analysis** rather than of any one pair: the approximation is somewhere in the
        # SDG, and nothing here can say which pairs' routes ran through it.
        #
        # So it is filed under one key that **no requested pair claims**, which is exactly the
        # mechanism :func:`~cldk.analysis.commons.graphs.taint_verdict` already has for a diagnostic
        # it cannot attribute: an unclaimed key lands in ``unresolved`` and empties ``exhausted``
        # for the batch. That is the intended outcome here, not a side effect — a batch-wide
        # approximation must cost every pair its certification.
        #
        # Filing it per pair instead gives the identical verdict and m*n copies of one message (400
        # of them on a 40x10 batch, measured), so the single key is the same answer said once.
        degraded = self.get_degraded()
        if degraded:
            codes = sorted({d.code for d in degraded})
            note = Diagnostic(
                code="analysis_degraded",
                message=(
                    f"this analysis carries {len(degraded)} degradation marker(s) ({', '.join(codes)}), so part of the "
                    "SDG is approximated: a path returned here is a real may-flow, but the absence of one is not a "
                    "proof, and no pair in this batch is certified. See get_degraded() for the affected packages "
                    "and callables."
                ),
            )
            blocked = dict(blocked)
            blocked[("", "")] = [note]
        return taint_verdict(sources, sinks, srcs, dsts, rows=rows, blocked=blocked, depth=depth, max_paths=max_paths)

    @abstractmethod
    def _taint_walk(
        self,
        srcs: Sequence[SliceNode],
        dsts: Sequence[SliceNode],
        *,
        cuts: List[Dict[str, str]],
        cut_callables: List[str],
        depth: int | None,
        max_paths: int,
    ) -> Tuple[List[Tuple[str, str, FlowPath]], Mapping[Tuple[str, str], List[Diagnostic]]]:
        """The sanitized shortest walks between every source and every sink, and what stopped a pair.

        Rows are ``(source ref, sink ref, path)`` triples, shortest-first within a pair and in
        :func:`~cldk.analysis.commons.graphs.hop_sort_key` order among equals, capped at
        ``max_paths + 1`` **per pair** — the extra row is what lets :meth:`taint` report truncation
        without a second counting traversal. Grouping and trimming are :meth:`taint`'s.

        The second element is the frontier ledger, keyed by the ``(source ref, sink ref)`` pair it
        implicates. The key is the association, not the message: ``exhausted`` is decided from these
        keys, and recovering a pair by parsing prose out of a :class:`Diagnostic` would resurrect
        the derivation the key exists to avoid. A condition that affects several pairs is filed
        under *each* of them.

        A local backend opens with ``self._require_dataflow()``: a graph backend has no analysis
        level to measure, so the gate lives in the implementation that can answer it.
        """

    @abstractmethod
    def _edge_vars_in(self, callable_id: str) -> Collection[str]:
        """The variable names carried by SDG edges scoped to this callable — the domain a variable
        sanitizer is checked against.

        Not :meth:`resolve_value`, which addresses ``formal_in`` port vertices only: most real edge
        variables are locals, so validating a sanitizer through the resolver would refuse a
        legitimate one for not being a parameter.
        """

    @abstractmethod
    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in :attr:`~cldk.analysis.commons.results.SliceNode.source` for these positions.

        A second call because a slice answers *where* and source answers *what*, and source is the
        one field with no size ceiling — so the traversals return ``source=None`` and a caller
        hydrates the handful of nodes it decided to read.
        """
