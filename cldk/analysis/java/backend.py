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

"""The Java analysis backend contract.

:class:`JavaAnalysis` is a thin façade that delegates its static-analysis queries to a *backend*.
Two interchangeable backends exist:

* :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer` — walks the in-memory pydantic
  ``JApplication`` / a NetworkX call graph built from the ``analysis.json`` codeanalyzer-java emits;
* :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` — answers the *same* queries with Cypher over
  the graph codeanalyzer-java emits with ``--emit neo4j``.

The shape shared with every other language — application view, symbol table, call graph, the
class/method/field lookups and the repository-artifact layer — is inherited from the generic
:class:`~cldk.analysis.commons.backend.AnalysisBackend`; what is declared here is the Java-native
remainder (compilation units, the 1.x caller/callee and class-call-graph accessors, constructors,
sub/nested classes, entry points, CRUD, comments). Both backends subclass it; the façade is typed
against it. Note the façade also calls Tree-sitter directly for a few parsing helpers
(``is_parsable``, ``get_raw_ast``); those are not part of the backend contract.

``get_call_graph()`` on both backends is keyed by ``"<type fqn>.<signature>"`` strings (spec J-1),
with a ``method_detail`` (:class:`~cldk.models.java.models.JMethodDetail`) and ``kind`` node
attribute; edges carry ``type``, ``weight`` and ``calling_lines``. A local or anonymous class's
segment of that key carries the signature of the callable that declares it
(``p.Outer.m(int).$anon$0``): ``$anon$N`` is numbered per declaring callable (J-1 erratum).

**Two fields of a returned** :class:`~cldk.models.java.models.JCallable` **depend on the backend.**
Off ``analysis.json``, ``code`` is the body block and ``body`` is every body node. Off the Neo4j
projection, ``code`` is the whole *declaration* (which ends with the body block) and ``body`` holds
the ``call`` nodes only — about 30% of the graph's body nodes, enough for ``call_sites`` and
nothing else.
"""

from __future__ import annotations

import re
from abc import abstractmethod
from functools import cached_property
from typing import ClassVar, Collection, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple, Union

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    EdgeOrder,
    check_depth,
    check_distinct_endpoints,
    check_max_nodes,
    check_max_paths,
    reject_bare_string,
)
from cldk.analysis.commons.graphs import as_slice_node, call_reaches, cone_sinks, edge_sort_key, flow_path, sdg_rel_pattern, sdg_rels, shortest_walks, slice_resolved, taint_verdict, via_table
from cldk.analysis.commons.keys import body_key_column, resolve_module_key
from cldk.analysis.commons.resolve import CallableCandidate, resolve_callable_signature, resolve_sanitizers, resolve_value_name, resolve_within
from cldk.analysis.commons.results import (
    BodyRef,
    CallableRef,
    Diagnostic,
    EdgePage,
    EntrypointCoverage,
    FlowPath,
    FlowPaths,
    LocateResult,
    ModuleRef,
    Slice,
    SliceNode,
    Span,
    TaintResult,
    TypeRef,
)
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.commons.treesitter.models import Captures
from cldk.models.python import PyConfigRead, PyConfigUseEdge
from cldk.models.java.models import (
    JApplication,
    JBodyNode,
    JCallable,
    JCallableParameter,
    JCallSite,
    JCdgEdge,
    JCfgEdge,
    JComment,
    JCompilationUnit,
    JConfigRead,
    JConfigUse,
    JCRUDOperation,
    JDdgEdge,
    JEnumConstant,
    JExternalSymbol,
    JField,
    JLocalVariable,
    JMethodDetail,
    JType,
)
from cldk.models.java.projections import JCallableOverview, JClassOverview
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, CodeanalyzerUsageException, SelectorNotInGraph

# A CRUD query row: the owning type + callable and the operations found within it.
CRUDRow = Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]

#: J-4: the CRUD accessors keep their names and raise this on schema v2, on both backends.
CRUD_UNAVAILABLE = "CRUD operations are not emitted by codeanalyzer-java 3.0.1 or newer (schema v2); tracked upstream as codeanalyzer-java#187"

#: J-4: what ``get_entrypoint_coverage`` says when this analysis carries no report, instead of
#: counting booleans and calling it coverage. codeanalyzer-java 3.1.0 (codeanalyzer-java#235) emits
#: one -- ``JApplication.entrypoint_report`` on the wire, ``:JApplication.entrypoint_report_json``
#: on the graph -- and the accessor reads it. Before 3.1.0 there was none at all, and a 3.0.x
#: payload or graph is still servable (the Neo4j floor is 3.0.1, and a cached ``analysis.json``
#: computed at a sufficient ``max_level`` is reused whatever wrote it), so the refusal stays and is
#: decided by :data:`the overlay probe <CONFIG_OVERLAY_UNAVAILABLE>`, never by a version string.
ENTRYPOINT_REPORT_UNAVAILABLE = (
    "this analysis carries no entrypoint report: codeanalyzer-java emits one only from 3.1.0 onwards, so the entrypoint pass's coverage "
    "(frameworks_detected/rulesets/unresolved/errors) cannot be reported for an older payload or an older graph. Re-analyse, or re-emit "
    "your Neo4j graph, with codeanalyzer-java 3.1.0 or newer. get_entrypoints() and get_entrypoint_classes() still carry the analyzer's "
    "own per-declaration marks."
)

#: Why the three config-read accessors refuse, **when they do**. codeanalyzer-java 3.1.0 added the
#: code-to-config layer (codeanalyzer-java#233 literal tier, #237 dataflow tier); 3.0.x has no
#: detector at all, and answering ``[]`` off a 3.0.x analysis would say "this application reads no
#: configuration" when the truth is "nothing looked" -- the ambiguous empty D7 forbids.
#:
#: **The probe cannot be the config layer's own absence**, and that is measured, not assumed: the
#: analyzer writes ``config_uses``/``config_reads_unresolved`` only when non-empty (the committed
#: ``v2/a4`` fixture is a level-4 3.1.0 run whose pruned tree reads no configuration, and it
#: carries neither key), and the graph declares ``J_USES_CONFIG``/``J_READS_CONFIG_UNRESOLVED`` as
#: relationship types only once an edge of that type exists. Both would refuse a clean 3.1.0
#: analysis that genuinely reads nothing. The witness that *is* unconditional is the sibling
#: overlay from the same release: 3.1.0 writes an entrypoint report on every run and 3.0.x writes
#: none of the three, so all three overlays are probed as the one thing they are -- one analyzer
#: generation's output -- and never as a version literal.
CONFIG_OVERLAY_UNAVAILABLE = (
    "the code-to-config layer cannot be answered for application {app!r}: this analysis carries none of codeanalyzer-java 3.1.0's "
    "application-scope overlays (config_uses, config_reads_unresolved, entrypoint_report), so an empty answer here would say "
    "'this application reads no configuration' where the truth is that nothing looked. Re-analyse, or re-emit your Neo4j graph, with "
    "codeanalyzer-java 3.1.0 or newer. get_config_keys() is unaffected: the keys a config artifact declares are read at every generation."
)

#: What ``get_external_symbols`` raises on a payload whose run never homed out-of-project call
#: targets -- which is not the same fact as a project that calls nothing outside itself (D7).
EXTERNAL_SYMBOLS_UNAVAILABLE = (
    "this analysis did not home out-of-project call targets: codeanalyzer-java emits external_symbols only under --external-calls, which is off "
    "by default and which --emit neo4j forces on, so the Neo4j backend answers this and a payload from a plain -a run has nothing to answer with"
)

# ----------------------------------------------------------------------------------------------
# The dataflow surface's shared vocabulary (leg 3b, Task 2). Each of these is the language-neutral
# ruling from ``cldk.analysis.commons`` bound to Java's relationship prefix and edge models, once,
# here -- so the two backends cannot come to disagree about what a page's order, a slice's edge set
# or a hop's word is.

#: The canonical order of each per-callable graph, in the two spellings that have to agree: the
#: Python sort key (:func:`~cldk.analysis.commons.graphs.edge_sort_key`) and the Cypher
#: expressions. ``coalesce`` is ``or ""`` / ``or []``: an optional field's ``None`` raises in a
#: Python sort key and silently drops the row in Cypher. ``len(exprs)`` is also the order's arity,
#: which is how a cursor minted by one accessor is refused by another (3, 2 and 4).
CFG_ORDER = EdgeOrder(edge_sort_key("cfg"), ("src", "dst", "coalesce(kind,'')"))
CDG_ORDER = EdgeOrder(edge_sort_key("cdg"), ("src", "dst"))
DDG_ORDER = EdgeOrder(edge_sort_key("ddg"), ("src", "dst", "coalesce(var,'')", "coalesce(prov,[])"))

#: The five relationship types a slice follows, spelled with Java's ``J_`` prefix, and the Cypher
#: disjunction of them. ``J_CFG_NEXT`` is deliberately absent: control *flow* says what runs next,
#: while a slice is about what a value or a decision depends on. Counted on the reference graph:
#: ``J_DDG`` 134,742, ``J_CDG`` 46,936, ``J_PARAM_IN`` 76,810, ``J_PARAM_OUT`` 44,961,
#: ``J_SUMMARY`` 22,222.
SDG_RELS = sdg_rels("J")
SDG_REL_PATTERN = sdg_rel_pattern("J")

#: The caller's word for each relationship a path hop can be justified by (E6). Both backends
#: translate through this one table, so a hop cannot be labelled ``data`` over Neo4j and ``ddg``
#: locally.
VIA = via_table("J")

#: The four synthetic vertices of the L4 **port lattice** -- a callable's formal parameters and
#: results, and the arguments and results at a call site. Named here because the one thing Java's
#: dataflow surface has to say about itself is a fact about them (see :data:`PORTS_DISCONNECTED`).
PORT_KINDS = frozenset({"formal_in", "actual_in", "formal_out", "actual_out"})


def java_body_node_kind(node_id: str, kind: str, parameters: Sequence[JCallableParameter]) -> Tuple[str, "str | None"]:
    """One body node's ``(kind, name)`` in the caller's vocabulary — Java's own translation.

    :func:`~cldk.analysis.commons.resolve.body_node_kind` (Python's) and
    :func:`~cldk.analysis.typescript.backend.ts_body_node_kind` are the twins, and neither is
    reused: each is a claim about one analyzer's ``of`` grammar, and a shared function would make
    a change in one language's emitter silently re-word another's results.

    **The name comes from the parameter list, not from the vertex.** codeanalyzer-java writes an
    ``of`` property on ``:JBodyNode`` in ``analysis.json`` (a ``formal_in``'s is the parameter's
    source name, an ``actual_in``'s is ``arg0``/``arg1``…, a ``formal_out``/``actual_out``'s is
    ``$ret``) and **projects none of it into Neo4j** — measured: 0 of daytrader8's 11,436 body
    nodes carry ``of`` in the graph. Reading it would therefore name a parameter locally and leave
    it ``None`` over the graph, which is a divergence dressed as an absence. A ``formal_in``'s id
    ends in ``@formal_in:<n>`` and ``n`` indexes the declared parameter list, which round-trips
    through the projection exactly (``:JCallable.parameters_json`` is the analyzer's own
    serialisation), so both backends read the name from the same place — the same inversion
    :meth:`JavaAnalysisBackend.resolve_value` performs in the other direction.

    Only ``formal_in`` gets a name. An ``actual_in``'s ``of`` is a *position*, and reporting it
    would put an ordinal in a return field (E7) — its identity is recoverable from ``ref``, and
    :meth:`JavaAnalysisBackend.flows_to_argument` addresses arguments by the callee's parameter
    name for exactly this reason. ``$ret`` is a marker, not a name.

    Every other kind passes through in the analyzer's already-English spelling. **One of them,
    ``switch``, is outside** :attr:`~cldk.analysis.commons.results.SliceNode.KINDS` — that list was
    derived from codeanalyzer-python's vocabulary, which has no ``switch`` because Python has no
    switch statement. Re-measured for Task 3, because Task 2 recorded the fan-out wrongly as "1,498
    ``J_CDG`` edges out of one": there are **3 such vertices in daytrader8 and 385 in ThingsBoard**,
    carrying 71 and 3,561 ``J_CDG`` edges between them, at most **154** out of any one. Dropping or
    renaming the kind would hide a real branch; it is reported as the analyzer spells it, as
    TypeScript reports its own out-of-list ``module`` vertices.
    """
    if kind == "formal_in":
        index = node_id.rpartition(":")[2]
        return "parameter", parameters[int(index)].name if index.isdigit() and int(index) < len(parameters) else None
    if kind == "actual_in":
        return "argument", None
    if kind in ("formal_out", "actual_out"):
        return "return", None
    return kind, None


#: Why the five forward value accessors refuse (D7), **when they do**. Up to codeanalyzer-java
#: 3.0.2 the L4 port lattice was emitted *disconnected* from the statement dependence graph: not
#: one of the reference graph's 134,742 ``J_DDG`` and 46,936 ``J_CDG`` edges had a
#: :data:`PORT_KINDS` vertex at either end, so a ``formal_in`` — the only thing
#: :meth:`JavaAnalysisBackend.resolve_value` ever returns — had **out-degree zero** and every
#: forward traversal seeded on one ended where it started. That made ``flows_to_call`` and
#: ``flows_to_argument`` ``False`` for every input, ``paths_between`` empty for every input,
#: ``slice_forward`` the seed alone and ``taint`` every requested pair refuted — each
#: indistinguishable from a proved absence of flow, which is exactly the ambiguous empty D7
#: forbids. They raise instead.
#:
#: **codeanalyzer-java 3.0.3 joins the two layers** (codeanalyzer-java#227): ``@formal_in:k → use``,
#: ``return → @formal_out``, ``statement → <call>/actual_in:i`` and ``<call>/actual_out →
#: statement``. On output from 3.0.3 the probe below answers ``True`` and all five accessors
#: answer, with no change here — which is the point of asking the *data* rather than the analyzer
#: version. The refusal is kept because it can still fire honestly: the Neo4j floor is 3.0.1, so a
#: graph emitted by 3.0.1 or 3.0.2 is still attachable, and ``--l3-engine wala`` leaves
#: ``formal_in`` ports unattached even on 3.0.3 (there are no ``@entry`` edges to mirror).
#:
#: ``slice_backward`` and the whole call-graph half are deliberately **not** guarded: even on the
#: disconnected shape a backward slice from a parameter follows ``J_PARAM_IN`` reversed and reaches
#: the argument vertex at every call site that passes one, which is a real answer that varies with
#: the program.
PORTS_DISCONNECTED = (
    "{accessor}() cannot be answered for application {app!r}: this analysis carries no data or control "
    "dependence edge out of a callable's formal_in vertices, so a value traversal that starts at a parameter "
    "cannot leave the parameter lattice. Every call would return the same answer whatever the program does, "
    "which is indistinguishable from a proved absence of flow, so this raises instead. codeanalyzer-java joins "
    "the port lattice to the statement graph from 3.0.3 onwards (except under --l3-engine wala), so re-emitting "
    "or re-analysing with 3.0.3 or newer makes these answer. slice_backward(), get_ddg(), and the call-graph "
    "accessors (reaches, callers_of, callees_of, backward_cone, call_paths_between) are unaffected."
)


#: What every accessor J-6 exempts says when it is handed an implicit callable. One string because
#: the *premise* is one fact about the analyzer -- a callable with no span, no body, no parameters
#: and no metrics (99 of daytrader8's 1,216) -- and only the consequence differs by accessor. J-6
#: makes such a callable resolve, because it is a real call-graph endpoint and hiding it would
#: dangle edges; what it cannot do is answer a question about text or flow it has none of. Returning
#: an empty page there would read as "this callable has no control flow", which is the ambiguous
#: empty (D7) this surface refuses everywhere else.
IMPLICIT_CALLABLE = "{key!r} is an implicit callable: codeanalyzer-java emits it with no span and no body, so {tail}"


#: Every call-shaped site in a callable body, under **one** capture name. One name matters: the
#: query result is a ``{capture name: [node]}`` mapping, so several names would put the nodes in
#: per-name groups and lose their source order between the groups.
_CALL_SITES = (
    "(object_creation_expression (type_identifier) @call) "
    "(object_creation_expression type: (scoped_type_identifier (type_identifier) @call)) "
    "(method_invocation name: (identifier) @call)"
)


class CallingLines:
    """The ``calling_lines`` edge attribute of ``get_call_graph()``: **absolute file lines**, sorted,
    of the calls a source callable makes to a target — parsed once per source callable.

    **Absolute, not offsets into** ``code``. The 1.x value was the 0-based line offset into
    ``JCallable.code``, and ``code`` is the body block off ``analysis.json`` and the whole
    declaration off the Neo4j projection, so the same call reported two different numbers on the
    two backends (560 of daytrader8's 1,862 edges, measured). ``code_start_line`` is on both, so
    ``code_start_line + offset`` is the file line both agree on — and it is the number a caller
    wants anyway, since it indexes the file rather than a string they would have to fetch first.

    Sorted, because source order within one callable is not otherwise guaranteed: 18 of those 560
    differed only in order, and 24 of the local backend's own lists were not ascending.

    Parsed once per source callable, because the naive form re-parsed the body for every outgoing
    edge — 3.6 parses per callable on ThingsBoard, where tree-sitter was 99.5% of the time
    ``get_call_graph`` spent. Measured on that corpus (21,269 nodes / 53,938 edges):
    **145.7 s → 41.0 s**.
    """

    def __init__(self) -> None:
        self._tsu = TreesitterJava()
        self._by_callable: Dict[str, Dict[str, List[int]]] = {}

    def of(self, source: JCallable, target: JCallable) -> List[int]:
        index = self._by_callable.get(source.id)
        if index is None:
            index = self._by_callable[source.id] = self._index(source)
        return index.get(target.signature.partition("(")[0], [])

    def _index(self, source: JCallable) -> Dict[str, List[int]]:
        """``{callee simple name: sorted absolute file lines}`` for one callable's ``code``."""
        code = source.code
        if not code:
            return {}
        try:
            captures: Captures = self._tsu.frame_query_and_capture_output(_CALL_SITES, code)
        except Exception:  # noqa: BLE001 — an unparsable body costs its lines, not the call graph
            return {}
        first_line = source.code_start_line
        index: Dict[str, List[int]] = {}
        for capture in captures:
            index.setdefault(capture.node.text.decode(), []).append(first_line + capture.node.start_point[0])
        for lines in index.values():
            lines.sort()
        return index


def java_module_dotted(package: str, types: Iterable[str] = ()) -> Tuple[str, ...]:
    """The dotted spellings ``in_module=`` accepts for one compilation unit (J-2): its **declared
    package**, and that package qualified by each type the unit declares.

    Java is the language where :func:`~cldk.analysis.commons.keys.module_dotted` must not be
    called. That helper derives the dotted name from the repo-relative path, which is right for
    Python and TypeScript because their paths *are* their namespaces; a Java path is a build
    layout, so ``src/main/java/com/ibm/…/TradeDirect.java`` derives to
    ``src.main.java.com.ibm.…``, which names nothing — silently, since the derivation cannot fail.
    The declared package is the answer, and it is on the unit (``JCompilationUnit.package``).

    The type-qualified spellings are the second half of J-2's "a dotted package, optionally
    ``.TypeName``", and they are not decoration: two files of one package (daytrader8's
    ``beans.MarketSummaryDataBean`` and ``beans.RunStatsDataBean``, both declaring ``toString()``)
    are indistinguishable by package alone, and ``package.TypeName`` is the dotted way to say which.

    ``types`` is the unit's declared type names — ``JCompilationUnit.types`` locally, the
    ``J_DECLARES`` names over Neo4j. A unit in the default package (no ``package`` statement) dots
    to its type names only, and to nothing at all if it declares none.
    """
    return (package, *(f"{package}.{name}" for name in types)) if package else tuple(types)


def java_callable_names(signature: str) -> Tuple[str, ...]:
    """The spellings a Java callable answers to (J-3): its full name, and the same with the
    parameter tail cut.

    A Java callable is keyed by a signature carrying that tail —
    ``…TradeDirect.cancelOrder(java.lang.Integer, boolean)`` — which is what makes two overloads
    two callables, and what a caller writing ``"cancelOrder"`` has not typed. The tail is the
    analyzer's spelling verbatim, not a normal form: a1 writes
    ``setTopLosers(java.util.Collection)`` and a4 writes ``setTopLosers(Collection<QuoteDataBean>)``
    for the same method, so nothing here — least of all an error message — may describe that tail
    as erased or qualified. Matching both spellings is the whole rule: a bare name finds the
    callable through the cut form, an ambiguity lists the tail-carrying form (the only spelling
    that *resolves* an overload pair, since ``in_class=`` cannot split one), and a caller who
    writes the tail matches on it exactly rather than by prefix.

    **The tail separates overloads, not types, so it is not an address on its own** (J-3 erratum).
    An interface and its implementors declare the same signature by construction:
    ``"cancelOrder(java.lang.Integer, boolean)"`` matches **4** of daytrader8's callables
    (``TradeDirect``, ``TradeSLSBBean``, ``DirectSLSBBean``, ``TradeServices``) and raises. Across
    the whole fixture, 1,216 callables carry only 581 distinct tails, 200 of them declared by more
    than one type, so a bare tail addresses just 381 of the 1,216. The address is J-1's
    ``<type fqn>.<signature>`` — which is exactly what an ambiguity here lists, and what
    ``in_class=`` narrows to.

    **The cut is at the last** ``(``, **not the first.** A local or anonymous class's qualified
    name carries the signature of the callable that declares it (the J-1 erratum), so the name of
    the ``run()`` inside one reads
    ``…PingManagedThread.doGet(javax.servlet.http.HttpServletRequest, …).$anon$0.run()`` — cutting
    at the first ``(`` would strip the declaring callable's tail and lose the class with it.
    The last ``(`` is the one that opens the callable's own tail for **every signature in both
    fixtures** — all 1,344 callables of a1 and a4, checked — because a parameter type there is a
    type name and none of those carry parentheses. That is a measurement, not a theorem: the shape
    that would break it is a *named local class* used as a parameter type of a sibling local class,
    whose qualified name would carry a declaring callable's tail *inside* the outer tail. Neither
    fixture contains one. If one ever appears, the cut has to become paren-balanced.
    """
    head = signature.rpartition("(")[0]
    return (signature, head) if head else (signature,)


def java_resolve_callable(name: str, candidates: Sequence[CallableCandidate], *, in_class: str | None = None, in_module: str | None = None) -> str:
    """:func:`~cldk.analysis.commons.resolve.resolve_callable_signature` with Java's rules — the
    one entry point both backends resolve a callable name through, so neither can drift on what
    ``"cancelOrder"`` means.

    Java's shapes reach the shared policy on the candidates themselves
    (:attr:`~cldk.analysis.commons.resolve.CallableCandidate.match_names` from
    :func:`java_callable_names`, ``module_names`` from :func:`java_module_dotted`); what this adds
    is the advice an ambiguity gives. The shared default — "more of the dotted path" —
    is untrue here: two overloads share every dotted segment and differ only in the parameter tail,
    so the way out is spelling the full signature — copied from the candidates the exception
    carries, since the analyzer normalises that tail no further (see :data:`_BY_FULL_SIGNATURE`).
    """
    return resolve_callable_signature(name, candidates, in_class=in_class, in_module=in_module, by_full_name=_BY_FULL_SIGNATURE)


#: What an ambiguous Java callable name tells the caller to do. Not a suggestion (E8 forbids
#: those): every candidate the exception carries is spelled this way, so the instruction is
#: literally "one of these strings". It is a **noun phrase** because the exception renders
#: ``Narrow it with {narrow_with}.`` around it, and the keyword pruning above makes this the only
#: clause for every Java overload ambiguity -- the most common one there is here.
#:
#: **It points at the listed matches rather than describing them**, because no description of the
#: tail is true. The analyzer's signature keys are *not* normalised: the a4 fixture carries
#: ``setTopLosers(Collection<QuoteDataBean>)`` and ``<init>(Instance<TradeServices>)`` — generic
#: arguments kept, type names unqualified — while a1 spells the same method
#: ``setTopLosers(java.util.Collection)``. A caller told "erased parameter types included" would
#: write ``setTopLosers(java.util.Collection)`` against an a4 graph and get
#: :class:`~cldk.utils.exceptions.SelectorNotInGraph`: advice that reads as a rule, produces a
#: miss, and is exactly the confident-wrong-answer failure E8 keeps out of the error path. The
#: candidates are already in the message, spelled the way the graph spells them, so the advice
#: names *them* — a thing the caller can copy and check — and not a normalisation nothing performs.
_BY_FULL_SIGNATURE = "the full signature, exactly as one of the listed matches spells it"


#: A parameter vertex's body key, ``formal_in:<n>`` -- the only body key this surface *composes*
#: (:meth:`JavaAnalysisBackend.resolve_value`) rather than reads back off a node, and the reason it
#: is the only one that can be answered without one.
_FORMAL_IN = re.compile(r"^formal_in:(\d+)$")


def java_body_node_id(callable_id: str, body_key: str) -> str:
    """The analyzer's own global id for one of a callable's body nodes: ``<callable id>@<key>``.

    Adopted from the emitter rather than re-derived, and verified against the reference graph: a
    statement's key is a bare ``"line:col"`` and joins with an ``@`` (``…cancelOrder(…)@650:9``),
    while every synthetic key already **owns** its leading ``@`` (``"@entry"``, ``"@exit"``,
    ``"@formal_in:0"``) and joins by concatenation — an unconditional ``"@"`` would produce
    ``…@@formal_in:0``, which names nothing in the graph and nothing in :attr:`JCallable.body`.
    ``:JBodyNode`` carries no ``id`` field on the wire (unlike codeanalyzer-typescript's, which
    does), so this is the only way the ids agree; the graph writes them and this composes the same
    strings, checked node for node by the live suite.

    The join is not invertible by splitting — ``…@entry`` could have come from the key ``"entry"``
    or ``"@entry"`` — so nothing here ever splits one back. Body nodes are addressed by the whole
    id, which is what :meth:`JavaAnalysisBackend._body_nodes` keys them by.
    """
    return f"{callable_id}{body_key}" if body_key.startswith("@") else f"{callable_id}@{body_key}"


class _Addressed(NamedTuple):
    """One callable, in every vocabulary the addressing surface speaks about it.

    ``key`` is the J-1 name (``"<type fqn>.<signature>"``) — what a caller reads, what
    ``resolve_callable`` returns and what ``get_source`` accepts; ``callable.id`` is the opaque
    ``can://`` handle that rides in ``ref``.
    """

    key: str
    type: JType
    callable: JCallable
    path: str


class _Addressing(NamedTuple):
    """The application, indexed the four ways the addressing surface reads it — built once per
    backend, from the same :class:`JApplication` both backends hold."""

    by_key: Dict[str, _Addressed]
    by_id: Dict[str, _Addressed]
    by_path: Dict[str, List[_Addressed]]
    candidates: List[CallableCandidate]


def duplicate_type_name(qualified_name: str) -> str:
    """The defect message for two declarations that spell one qualified name — which would make a
    ``get_call_graph()`` node key and a ``get_class()`` key ambiguous, so it is surfaced rather than
    letting the second silently shadow the first. Both backends raise this text, identically, and
    it names only the qualified name: a ``can://`` id must not appear in a message (E6)."""
    return f"type qualified name {qualified_name!r} is declared twice: codeanalyzer-java emitted two declarations that spell one name"


def unhomed_endpoint(node_id: str) -> str:
    """The defect message for a call-graph endpoint that is not one of the application's callables.
    Both backends raise this text, identically, and it names the endpoint by the signature and
    module key its id *spells* rather than by the id itself (E6)."""
    module, sep, rest = node_id.partition(".java/")
    signature = (rest or node_id).rpartition("/")[2]
    where = f"{module.split('/', 4)[4]}.java" if sep and module.count("/") >= 4 else "no module of this application"
    return f"call-graph endpoint {signature!r} in {where!r} is not one of its callables: codeanalyzer-java emitted an unhomed endpoint"


class JavaAnalysisBackend(AnalysisBackend[JApplication, JCompilationUnit, JType, JCallable, JField, JCallableParameter]):
    """Abstract base every Java analysis backend implements.

    A backend owns all indexing and query logic for a Java application; the :class:`JavaAnalysis`
    façade delegates to it. Implementations must return the canonical ``cldk.models.java`` pydantic
    objects (or the documented NetworkX / dict / list shapes) so backends are behaviorally
    interchangeable.

    Inherited abstract (see :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "J"
    N: ClassVar[str] = "J"

    # =====================================================================================
    # The addressing surface (leg 3b, Task 1): locate / resolve / source / describe.
    #
    # IMPLEMENTED HERE, NOT PER BACKEND, and that is the parity guarantee rather than a
    # convenience. Leg 3a made :class:`JNeo4jBackend` rebuild the canonical :class:`JApplication`
    # from the graph and answer every query with the same code the in-memory backend runs, so a
    # second implementation would have nothing to read that this one cannot — it would only be a
    # second place for "what does ``cancelOrder`` mean" to drift. The backends supply three facts
    # each: their flattened index (``_types`` / ``_file_of`` / ``_callables``), the body nodes of a
    # callable (:meth:`_body_nodes`), the text of one (:meth:`_body_source`), and whether call
    # sites resolve at all (:attr:`has_resolution_edges`).
    #
    # WHAT STILL DIFFERS, AND IT IS THE DATA, NOT THE CODE. **Text no longer differs.**
    # codeanalyzer-java 3.2.0 projects ``:JModule.source`` plus the byte offsets every node's text is
    # a slice of, so :meth:`get_source`, :attr:`LocateResult.source` and a module-scope
    # :meth:`locate` read the same characters on both backends and the live parity suite asserts
    # ``neo.code == ref.code`` (it asserted ``neo.code.endswith(ref.code)`` while the graph carried
    # the whole declaration and no ``body_span`` — codeanalyzer-java#176, fixed by the 3.2.0 text
    # model, which is why ``JNeo4jBackend`` floors there). What is still thinner over Neo4j is the
    # *set* of nodes, not their text: a body node has text only where the projection wrote offsets
    # for it, and ``JCallable.body`` holds the ``call`` nodes only. None of that is branched on: it
    # falls out of what the models hold.
    #
    # NO ``can://`` AND NO ORDINAL leaves this surface except in ``ref`` / ``node_id`` (E6/E7);
    # every error names what missed and suggests nothing (E8).
    # =====================================================================================
    @cached_property
    def _addressing(self) -> _Addressing:
        """Every callable of the application, indexed for addressing — built once, lazily.

        Reads the flattened containment index each backend already builds for the call graph
        (``_callables``: ``can://`` id → ``(owning type, callable)``; ``_file_of``: type qualified
        name → module key), so there is no third walk of the tree and no way for the addressing
        domain to disagree with the one :meth:`get_call_graph` homes its endpoints on.

        The candidate domain is **every callable the analyzer emitted** (J-6): initializers,
        implicit constructors and the callables of local and anonymous classes included. Nothing is
        filtered before the shared policy runs — a domain that differs between backends is not
        parity, and one that quietly omits a callable is a name that resolves to nothing for a
        reason the caller cannot see.
        """
        symbol_table = self.get_application_view().symbol_table
        # The dotted spellings ``in_module=`` accepts, once per module rather than once per
        # callable: J-2's rule reads the *unit* (its declared package and the types it declares),
        # and daytrader8 has 1,216 callables across 138 of them.
        module_names = {path: java_module_dotted(unit.package, unit.types) for path, unit in symbol_table.items()}
        by_key: Dict[str, _Addressed] = {}
        by_id: Dict[str, _Addressed] = {}
        by_path: Dict[str, List[_Addressed]] = {}
        candidates: List[CallableCandidate] = []
        for owner, c in self._callables.values():
            path = self._file_of[owner.qualified_name]
            row = _Addressed(f"{owner.qualified_name}.{c.signature}", owner, c, path)
            by_key[row.key] = row
            by_id[c.id] = row
            by_path.setdefault(path, []).append(row)
            candidates.append(CallableCandidate(row.key, owner.qualified_name, path, java_callable_names(row.key), module_names.get(path, ())))
        return _Addressing(by_key, by_id, by_path, candidates)

    # -----[ locate ]-----
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        Four outcomes, kept distinguishable rather than collapsed into an ambiguous empty: inside a
        callable (``callable`` set, and ``body`` too when a body node is that precise); at module
        scope (a real position with no enclosing callable — a ``module_scope`` diagnostic); in the
        gap between two callables (also module scope, and never silently snapped to the nearest
        callable); or in a file the analysis has no module for (``file_not_in_graph``).

        There is no ``col`` parameter, for the reason TypeScript's ``locate`` gives: the Neo4j
        projection writes ``start_line``/``end_line`` and nothing else, so a column would work in
        process and be silently inert over the graph.

        Args:
            path: The file path. Normalised against the module keys, so a ``./``-prefixed or
                absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.
        """
        return self.locate_many([(path, line)])[0]

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in one round trip, in input order.

        The bulk form, not an optimisation over :meth:`locate`: a scanner hands over a whole alert
        set at once, and round trips cost latency for a person and context for an agent. Every
        enclosing callable is found from the in-memory index first, and the body nodes of *all* of
        them are then fetched in a single :meth:`_body_nodes` call — so N positions cost one
        statement over Neo4j, not N.
        """
        found = [self._enclosing(path, line) for path, line in positions]
        bodies = self._body_nodes([row.callable.id for _, row in found if row is not None])
        return [self._locate_result(line, key, row, bodies) for (key, row), (_, line) in zip(found, positions)]

    def _enclosing(self, path: str, line: int) -> Tuple[str, Optional[_Addressed]]:
        """The module key ``path`` names and the innermost callable of it containing ``line``.

        Innermost = the narrowest **line** span containing the position, because lines are all the
        Neo4j projection carries and a rule the two backends cannot both apply is not one rule.
        Ties (a callable declared inside another on one line) break on the longer J-1 key, deeper
        first — a local class's key extends its declaring callable's — and then on the key itself,
        so the order is total and identical on both sides.

        A position between two callables, or at module scope, is contained by none and comes back
        ``None`` rather than snapping to a neighbour. A callable with **no span** — every implicit
        constructor (99 of daytrader8's 1,216) — is contained by nothing, which is right: it has no
        position in the file to be found at.
        """
        key = resolve_module_key(str(path), self.get_application_view().symbol_table.keys())
        rows = [r for r in self._addressing.by_path.get(key, ()) if r.callable.span is not None and r.callable.start_line <= line <= r.callable.end_line]
        if not rows:
            return key, None
        return key, min(rows, key=lambda r: (r.callable.end_line - r.callable.start_line, -len(r.key), r.key))

    def _locate_result(self, line: int, key: str, row: Optional[_Addressed], bodies: Mapping[str, Mapping[str, JBodyNode]]) -> LocateResult:
        unit = self.get_application_view().symbol_table.get(key)
        if unit is None:
            return LocateResult(
                body=None,
                callable=None,
                type=None,
                module=ModuleRef(path=key),
                source="",
                span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
                diagnostics=[Diagnostic(code="file_not_in_graph", message=f"{key} is not covered by any analysed module of this application.")],
            )
        # ``ModuleRef.module_name`` is one string and a Java unit answers to several dotted
        # spellings (J-2), so it carries the first — the declared package, which is the spelling a
        # signature reads. A unit in the default package that declares no type has none at all, and
        # says ``None`` rather than an empty string that would read as a name.
        names = java_module_dotted(unit.package, unit.types)
        module_ref = ModuleRef(path=key, module_name=names[0] if names else None)
        if row is None:
            # Read off the data rather than off which backend is running: "" is never returned as
            # if it were the file. Since codeanalyzer-java 3.2.0 the projection carries
            # ``:JModule.source``, so this diagnostic fires on a module whose text is genuinely
            # missing -- an unreadable or undecodable file -- and on nothing else.
            diagnostics = [Diagnostic(code="module_scope", message=f"line {line} is at module scope in {key}.")]
            if not unit.source:
                diagnostics.append(Diagnostic(code="module_source_unavailable", message=f"no source text is available for {key} on this backend."))
            return LocateResult(
                body=None,
                callable=None,
                type=None,
                module=module_ref,
                source=unit.source,
                span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
                diagnostics=diagnostics,
            )
        c = row.callable
        body = self._innermost_body(bodies.get(c.id) or {}, c.id, line)
        return LocateResult(
            body=body,
            node_id=body.id if body else None,
            callable=CallableRef(signature=c.signature, name=c.signature.rpartition("(")[0] or c.signature, class_signature=row.type.qualified_name),
            type=TypeRef(signature=row.type.qualified_name, name=row.type.name),
            module=module_ref,
            source=c.code,
            span=Span.model_validate(c.span),
            diagnostics=[],
        )

    @staticmethod
    def _innermost_body(nodes: Mapping[str, JBodyNode], callable_id: str, line: int) -> Optional[BodyRef]:
        """The innermost span-bearing body node of one callable containing ``line``, or ``None``.

        ``None`` is a real outcome, not a failure: a position on the declaration line, on a blank
        line, or on a line the analyzer emitted no vertex for is contained by the callable and by
        no body node, and the caller still gets the callable. The synthetic vertices
        (``@entry``/``@exit``/``@formal_in:N``) carry no span and are not positions in the file, so
        they are never candidates.

        Ties break on the narrowest line span, then the deeper column parsed out of the node's own
        key (:func:`~cldk.analysis.commons.keys.body_key_column`), then the id — the same rule
        TypeScript applies, and the only one available here, since the graph carries no column on a
        ``:JBodyNode`` at all.
        """
        matches = [(node_id, n) for node_id, n in nodes.items() if n.span is not None and n.start_line <= line <= n.end_line]
        if not matches:
            return None
        node_id, node = min(matches, key=lambda kn: (kn[1].end_line - kn[1].start_line, -body_key_column(kn[0][len(callable_id) + 1 :]), kn[0]))
        return BodyRef(id=node_id, kind=node.kind, span=Span.model_validate(node.span), callee=node.callee)

    @abstractmethod
    def _body_nodes(self, callable_ids: Sequence[str]) -> Dict[str, Dict[str, JBodyNode]]:
        """``{callable id: {body-node id: node}}`` for these callables, in **one** round trip.

        Keyed by the node's global id (:func:`java_body_node_id`) rather than by its local key,
        because the join is not invertible by splitting — ``…@entry`` could have come from the key
        ``"entry"`` or from ``"@entry"`` — and the whole id is what
        :attr:`~cldk.analysis.commons.results.BodyRef.id` and :meth:`get_source` speak anyway.

        The seam exists because leg 3a's reconstruction rebuilds the ``call`` body nodes only
        (about 30% of what the graph holds), which is enough for ``call_sites`` and not enough for
        :meth:`locate`: an alert on a statement would otherwise come back with ``body=None`` over
        Neo4j and a statement locally, which is a divergence dressed as an absence. The graph has
        every body node; this is how the graph backend reads them.

        An id with no body nodes — or one this backend cannot find — contributes **no entry**, so
        callers read the result with ``.get``. Both are the same thing to every caller here, which
        has already resolved the callable and only ever asks about one that exists.
        """

    @abstractmethod
    def _body_source(self, node: JBodyNode) -> str | None:
        """The source text of one body node, or ``None`` when this backend has none for it.

        ``None`` is the honest answer over Neo4j for every body node: ``:JBodyNode`` carries a line
        range and no text, and ``:JModule`` carries no ``source`` to slice one out of, so there is
        nothing below callable granularity to return and substituting the enclosing callable's text
        would be a wrong answer rather than a missing one.
        """

    # -----[ addressing ]-----
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the callable it names.

        The candidate domain is every callable the analyzer emitted for this application (J-6), and
        it is the same set on both backends — see :attr:`_addressing`. The policy is
        :func:`java_resolve_callable`'s, which is
        :func:`~cldk.analysis.commons.resolve.resolve_callable_signature` with Java's rules: the
        name matches whole or as a dotted suffix on segment boundaries, and **also** against the
        signature with its parameter tail cut (J-3), so ``"cancelOrder"`` names
        ``…TradeDirect.cancelOrder(java.lang.Integer, boolean)`` and the tail-carrying spelling is
        what resolves one overload out of a pair. ``in_class`` / ``in_module`` disambiguate rather
        than scope, and ``in_module`` takes a repo-relative path suffix or a dotted spelling — the
        declared **package**, optionally qualified by a type it declares (J-2), never a name
        derived from the path.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind="callable"``, the J-1
            key (``"<type fqn>.<signature>"``) in ``callable``, and the callable's opaque
            ``can://`` id in ``ref``. ``line`` is the declaration's first line, or **-1** for an
            implicit callable, which the analyzer emits with no span at all — the model's own "not
            known" (never ``0``, which would read as a position).

        Raises:
            AmbiguousName: More than one callable matched, listing every match and naming only ways
                out that could work — for two overloads that is the full signature, since no
                keyword can split them.
            SelectorNotInGraph: Nothing matched, naming the argument that missed. No suggestions
                and no fuzzy matching (E8).
        """
        row = self._addressing.by_key[java_resolve_callable(name, self._addressing.candidates, in_class=in_class, in_module=in_module)]
        c = row.callable
        return SliceNode(file=row.path, line=c.start_line, callable=row.key, kind="callable", name=c.signature.rpartition("(")[0] or c.signature, source=None, ref=c.id)

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable to the position that carries it.

        The candidate domain is the callable's **parameters**, which in Java is exactly its set of
        ``formal_in`` vertices: unlike Python, whose entering values are 84% captured module
        globals, a Java callable closes over nothing — a static it reads is a field, and a field is
        not a value that *enters*. Verified on the level-4 fixture: all 225 ``formal_in`` vertices
        line up name-for-name and index-for-index with the declared parameters, none missing and
        none extra. So the answer is always ``kind="parameter"``, named by the source identifier
        with no ordinal in it (E7).

        Reading the parameter list rather than the ``formal_in`` nodes is what makes the two
        backends answer identically: parameters round-trip exactly through the projection
        (``:JCallable.parameters_json`` is the analyzer's own serialisation), while the vertices
        themselves are not rebuilt by leg 3a's reconstruction.

        ``ref`` is the vertex's own id (``<callable id>@formal_in:<n>``), an opaque handle for the
        dataflow accessors. It does **not** round-trip through :meth:`get_source` on either backend
        — a parameter is a dataflow position, not a region of the file — and it names a node that
        exists only in an analysis built at level 3 or deeper, which is where those accessors live.

        Args:
            name: The parameter's name, as written in the source.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`. It takes
                no ``in_class=`` / ``in_module=``: ``within`` is matched against the whole J-1 key,
                so naming more of it narrows by class and module already, and that is what an
                ambiguity raised here advises.

        Raises:
            AmbiguousName: ``within`` named more than one callable.
            SelectorNotInGraph: No such callable, or no parameter of it carries that name.
            CodeanalyzerUsageException: ``within`` named an **implicit** callable, which J-6 makes
                addressable and the analyzer emits with no parameter list at all. Refused rather
                than reported as a missing name, which would blame the name for the callable.
        """
        owner = resolve_within(self.resolve_callable, within)
        # J-6: an implicit callable resolves and declares nothing. Refused here rather than in each
        # of the six accessors that address a value, and refused before the name is judged, because
        # "no such value" would blame the name for what the callable is.
        self._require_explicit(owner.callable, "it declares no parameter to address")
        c = self._addressing.by_key[owner.callable].callable
        # Positional, because the vertex id is: ``@formal_in:<n>`` indexes the declared list. A
        # parameter the analyzer emitted without a name has no address here and is left out rather
        # than shifting every index after it (none exists in either fixture: 0 of 1,391 checked).
        named = [(i, p.name) for i, p in enumerate(c.parameters) if p.name]
        chosen = resolve_value_name(name, [n for _, n in named], within=owner.callable)
        index = next(i for i, n in named if n == chosen)
        return SliceNode(file=owner.file, line=owner.line, callable=owner.callable, kind="parameter", name=chosen, source=None, ref=java_body_node_id(c.id, f"@formal_in:{index}"))

    # -----[ source access ]-----
    def get_source(self, node_id: str) -> str:
        """Source text for one node, named by ``node_id``.

        ``node_id`` is a callable's J-1 key (``"<type fqn>.<signature>"``, what
        :meth:`resolve_callable` returns in ``callable``), a callable's opaque ``can://`` id (what
        it returns in ``ref``), or the body-node id :meth:`locate` hands back — so the precise
        statement or call site an alert landed on can be re-fetched, not just its enclosing
        callable. Round-tripped, never composed by the caller (E6).

        **The same text on both backends**: the callable's body block, or the statement's own
        region, sliced out of the module's text. Before codeanalyzer-java 3.2.0 the graph carried
        one line range per callable, no ``body_span`` and no module text, so a callable came back as
        the whole **declaration** and a body node not at all (codeanalyzer-java#176); 3.2.0 projects
        ``:JModule.source`` plus byte offsets down to ``:JBodyNode``, and the live parity suite
        asserts equality rather than tolerating a difference.

        Raises:
            KeyError: Nothing this backend holds is named by ``node_id``, or it names a node with
                no recoverable text — an implicit callable (no span and no body at all), or any
                node the projection placed nowhere in a file. The message names the reason.
        """
        found = self._sources_for([node_id])
        if node_id not in found:
            raise KeyError(f"no callable or body node of this application is named by {node_id!r}")
        code = found[node_id]
        if not code:
            row = self._addressing.by_key.get(node_id) or self._addressing.by_id.get(node_id)
            if row is not None and row.callable.is_implicit:
                raise KeyError(IMPLICIT_CALLABLE.format(key=node_id, tail="there is no source text to return"))
            raise KeyError(f"no recoverable source for {node_id!r} on this backend (it carries no span, or the backend holds no text for it)")
        return code

    def _sources_for(self, refs: Sequence[str]) -> Dict[str, "str | None"]:
        """``{ref: source or None}`` for every ref this backend can **find**, in one round trip.

        The seam :meth:`describe` and :meth:`get_source` are both built on, and the reason their
        two kinds of "no source" stay apart: a ref that exists but has no recoverable text maps to
        ``None``; a ref that names nothing is *absent from the mapping*.

        A callable answers to both of its names — the J-1 key and its ``can://`` id — so a ref from
        either field of a :class:`~cldk.analysis.commons.results.SliceNode` resolves. A body-node
        ref is split from its callable at the first ``@``, which is safe because a Java ``can://``
        id contains none (checked: 0 of daytrader8's 1,216 and ThingsBoard's 28,763 callables), and
        the node is then looked up by its **whole** id.

        **A ``@formal_in:<n>`` ref is answered from the parameter list**, not from the body nodes,
        for the same reason :meth:`resolve_value` mints it from there: a parameter exists at every
        analysis level, and the vertex that carries it does not. The body map holds the ``call``
        nodes only below level 3, so looking one up there made ``describe`` raise ``KeyError`` — "a
        stale or foreign address" — on a ref ``resolve_value`` had just returned, at the default
        level of ``CLDK.java(...)``. The answer that is true at every level on both backends is
        *present, with no text*: a parameter is a dataflow position, not a region of a file, which
        is why :meth:`describe`'s own contract calls ``source=None`` the only meaning of ``None``.
        An index past the end of the parameter list is **not** answered this way — that is a stale
        address, and it falls through to the body-node lookup that will not find it.
        """
        index = self._addressing
        found: Dict[str, "str | None"] = {}
        wanted: Dict[str, List[str]] = {}
        for ref in dict.fromkeys(refs):
            row = index.by_key.get(ref) or index.by_id.get(ref)
            callable_id, _, body_key = ref.partition("@")
            owner = index.by_id.get(callable_id)
            formal_in = _FORMAL_IN.match(body_key)
            if row is not None:
                found[ref] = row.callable.code or None
            elif owner is not None and formal_in and int(formal_in.group(1)) < len(owner.callable.parameters):
                found[ref] = None
            elif owner is not None and body_key:
                wanted.setdefault(callable_id, []).append(ref)
        for callable_id, nodes in self._body_nodes(list(wanted)).items():
            for ref in wanted[callable_id]:
                if ref in nodes:
                    found[ref] = self._body_source(nodes[ref])
        return found

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in :attr:`~cldk.analysis.commons.results.SliceNode.source` for these positions.

        A second call because addressing answers *where* and source answers *what*, and source is
        the one field with no size ceiling (E4). Returns the **same**
        :class:`~cldk.analysis.commons.results.SliceNode` type, so nothing downstream has to branch
        on whether a node has been through here, and accepts anything carrying an address — slice
        nodes and :meth:`locate` results alike (see
        :func:`~cldk.analysis.commons.graphs.as_slice_node`).

        **One round trip regardless of node count**, through a single :meth:`_sources_for` call.

        Afterwards ``source=None`` means exactly one thing: *this position exists and the backend
        has no text for it*. It never means "the lookup failed", because a ref naming nothing raises
        instead. Which positions have no text no longer differs by backend: a ``kind="callable"``
        node and a statement or call site both hydrate on either, to the same slice of the same file
        (codeanalyzer-java 3.2.0 carries byte offsets down to ``:JBodyNode``); a ``parameter``
        hydrates on neither, having no span in the analyzer's own model.

        Raises:
            KeyError: A ``ref`` names nothing this backend can find — a ref comes from this SDK, so
                one that resolves to nothing means a stale or foreign address, which is worth
                stopping on rather than discovering three layers later. The message names the
                positions in the caller's vocabulary, never by ``ref`` (E6).
            TypeError: An element carries no ``ref``.
        """
        out = [as_slice_node(n) for n in nodes]
        if not out:
            return []
        sources = self._sources_for([n.ref for n in out])
        missing = [n for n in out if n.ref not in sources]
        if missing:
            named = [f"{n.callable} ({n.file}:{n.line})" if n.file else n.callable for n in missing[:5]]
            raise KeyError(f"{len(missing)} of {len(out)} positions name nothing in this application: {', '.join(named)}")
        return [n.model_copy(update={"source": sources[n.ref]}) for n in out]

    @property
    @abstractmethod
    def has_resolution_edges(self) -> bool:
        """Whether this backend can resolve a call site's ``callee_signature`` at all right now.

        ``JCallSite.callee_signature`` is ``""`` both for "genuinely unresolved" — codeanalyzer-java
        attempts resolution at every analysis level and misses on a call whose receiver type it
        cannot resolve (measured: 608 of 975 resolved on the pruned level-4 fixture, 4,006 of 4,006
        on the whole application at level 1) — and, in principle, for a graph carrying no
        ``J_RESOLVES_TO`` edge at all. This is the disambiguator: ``False`` means every empty
        ``callee_signature`` is explained by the graph, not by individual call sites.

        Unconditionally ``True`` on the in-memory backend: the analyzer's own resolution is in the
        payload at every level. The Neo4j backend probes its attached graph once, at connection
        time; ``--emit neo4j`` always runs at full depth, so ``False`` there means a graph built
        some other way, not a gap in the documented pipeline.
        """

    # =====================================================================================
    # Entrypoints, the bulk projections and the type-kind leaf accessors (leg 3b, Task 3).
    # Python's signatures, keyword-for-keyword, with Java's models.
    #
    # IMPLEMENTED HERE, NOT PER BACKEND, for Task 1's reason: leg 3a made :class:`JNeo4jBackend`
    # rebuild the canonical :class:`JApplication` from the graph -- decorators and all, since
    # ``J_ANNOTATED_BY`` is one of the relationships its containment walk collects -- and answer
    # from it. So these read the same index both backends already build and issue **no Cypher of
    # their own**; the one thing the graph carries and the reconstruction did not is the
    # ``:JExternal`` set, which :meth:`JNeo4jBackend._external_rows` now projects into
    # ``JApplication.external_symbols``.
    #
    # ``get_entrypoint_coverage`` READS THE REPORT FROM 3.1.0 ON, AND REFUSES BELOW IT. Java was
    # the one of the three languages whose analyzer projected no entrypoint report; 3.1.0
    # (codeanalyzer-java#235) projects one, on the wire as ``JApplication.entrypoint_report`` and on
    # the graph as ``:JApplication.entrypoint_report_json``, so the accessor reads the same four
    # fields codeanalyzer-python and codeanalyzer-typescript already carry. A 3.0.x analysis carries
    # none, and a count of syntactically-marked callables is not a coverage report, so it is not
    # dressed up as one (J-4): that case reports :data:`ENTRYPOINT_REPORT_UNAVAILABLE`.
    # =====================================================================================
    def get_callables_overview(self) -> List[JCallableOverview]:
        """A lightweight projection of every callable in the application, without the full
        :class:`~cldk.models.java.models.JCallable` reconstruction.

        The domain is the addressing domain exactly (J-6): initializers, the implicit constructors
        the analyzer synthesises, and the callables of local and anonymous classes are all in it,
        because all of them are addressable and a projection that quietly omits one is a name that
        resolves to nothing for a reason the caller cannot see.
        """
        return [JCallableOverview.of(row.key, row.type, row.callable, path=row.path) for row in self._addressing.by_key.values()]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Source bodies for the given callables, keyed by the key that named them.

        Args:
            signatures: The J-1 keys :meth:`get_callables_overview` hands back
                (``JCallableOverview.key``) — matched exactly, never resolved and never fuzzily
                (E8). Java's own ``signature`` is unique only within its declaring type, so it is
                not an address.

        Returns:
            A dict mapping each key to its source text. A key with no matching callable is
            **omitted**, as is a callable with no source text of its own — the 99 implicit
            constructors of daytrader8, and only those: 1,117 of its 1,216 callables come back.
            Every value is a real, non-empty ``str`` rather than a ``None`` a caller has to
            re-check. The filter is ``code``, not ``declaration``: the two ``<clinit>$N()``
            initializers have no ``declaration`` text to slice but do carry a body block, so they
            *are* in the result. 101 callables have ``declaration is None``; 99 have no ``code``.
        """
        found = ((key, self._addressing.by_key.get(key)) for key in signatures)
        # ``code`` slices (and on a non-ASCII unit decodes) the module source, so it is read once
        # per callable rather than once in the filter and once in the value.
        return {key: code for key, code in ((key, row.callable.code) for key, row in found if row is not None) if code}

    def get_decorated_callables(self, markers: List[str]) -> List[JCallableOverview]:
        """Overviews of every callable carrying at least one of ``markers`` as an annotation.

        Args:
            markers: Annotation names. Each matches by **simple name** (``Test``), with a leading
                ``@`` ignored (``@Test``), or by fully-qualified name (``org.junit.Test``) — J-5.
                The three collapse to one rule: both sides are compared on the segment after the
                last ``.``, with a leading ``@`` stripped. Java's wire carries the annotation's
                simple name, so a qualified marker cannot be matched any more precisely than that;
                J-5 accepts the package ambiguity because this is a filter, not an address. There
                is no fuzzy matching on either side (E8).
        """
        wanted = {m.lstrip("@").rpartition(".")[2] for m in markers}
        return [o for o in self.get_callables_overview() if wanted.intersection(d.rpartition(".")[2] for d in o.decorators)]

    def get_entrypoints(self) -> List[JCallableOverview]:
        """Overviews of every *callable* the analyzer marked ``is_entrypoint`` — a servlet method, a
        JAX-RS resource method, an MDB listener, or whatever else its detection pass recognised.

        Empty means the pass found no entrypoint callables, which for Java is unambiguous at the
        property level: ``is_entrypoint`` is a real boolean on every callable of ``analysis.json``
        and a real property on every ``:JCallable`` of the graph (133 true of daytrader8's 1,216,
        1,501 of ThingsBoard's 28,763). What it cannot tell you is whether the *pass* had gaps —
        and unlike Python and TypeScript, Java has no report to answer that with; see
        :meth:`get_entrypoint_coverage`.

        Class-level marks are not folded in: a :class:`~cldk.models.java.models.JType` is not a
        callable, and calling one would misrepresent what a
        :class:`~cldk.models.java.projections.JCallableOverview` means. Use
        :meth:`get_entrypoint_classes`.
        """
        return [o for o in self.get_callables_overview() if o.is_entrypoint]

    def get_entrypoint_classes(self) -> List[JClassOverview]:
        """Overviews of every *type* the analyzer marked ``is_entrypoint_class`` in its own right —
        the type-level sibling of :meth:`get_entrypoints`, which walks callables only and so never
        sees a type marked at the declaration with no individually-marked method.

        The projected form of :meth:`get_all_entry_point_classes`, which keeps its 1.x
        ``Dict[str, JType]`` shape (J-4); the two never disagree about which types are marked,
        because both read the one flattened index.
        """
        return [JClassOverview.of(t, path=self._file_of[name], qualified_name=name) for name, t in self._types.items() if t.is_entrypoint_class]

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """The entrypoint pass's own coverage and failure record, identically on both backends.

        codeanalyzer-java 3.1.0 (codeanalyzer-java#235) emits it — ``JApplication.entrypoint_report``
        off ``analysis.json``, ``:JApplication.entrypoint_report_json`` off the graph, which
        :class:`~cldk.analysis.java.neo4j.neo4j_backend.JNeo4jBackend` parses back into the same
        model, so the two backends read one object and there is no lossiness between them. The four
        fields are the ones codeanalyzer-python and codeanalyzer-typescript already carry
        (``frameworks_detected``, ``rulesets``, ``unresolved``, ``errors``); on daytrader8 that is
        three frameworks detected of five rulesets run, nothing unresolved and no errors.

        A 3.0.x analysis carries no report at all, and both a cached ``analysis.json`` and an
        attached 3.0.x graph are still servable, so that case answers with a ``diagnostics``-only
        :class:`~cldk.analysis.commons.results.EntrypointCoverage`
        (:data:`ENTRYPOINT_REPORT_UNAVAILABLE`) rather than fabricating empty-but-clean-looking
        fields — the same "say so honestly" shape a Python graph without the report uses. It still
        never synthesises a report out of the ``is_entrypoint`` booleans: a count of
        syntactically-marked callables is not a coverage record (J-4).
        """
        report = self.get_application_view().entrypoint_report
        if report is None:
            return EntrypointCoverage(diagnostics=[Diagnostic(code="entrypoint_report_unavailable", message=ENTRYPOINT_REPORT_UNAVAILABLE)])
        return EntrypointCoverage(
            frameworks_detected=list(report.frameworks_detected),
            rulesets=list(report.rulesets),
            unresolved=dict(report.unresolved),
            errors=list(report.errors),
        )

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[JCallSite]]:
        """Call sites of the given callables, keyed by the key that named them.

        Args:
            signatures: The J-1 keys :meth:`get_callables_overview` hands back, matched exactly —
                see :meth:`get_method_bodies`.

        Returns:
            A dict mapping each **existing** key to its list of
            :class:`~cldk.models.java.models.JCallSite` (an empty list when the callable makes no
            calls); a key matching no callable is omitted. Complete on both backends: the Neo4j
            projection carries the ``call`` body nodes in full even though it carries no other kind
            (the reason :attr:`JCallable.body` is otherwise thinner there).

            **The order within one source line is not part of the cross-backend contract.** The
            graph writes ``start_line``/``end_line`` and no column, so two calls on one line cannot
            be put back in source order there; the set of sites and their lines agree exactly (all
            4,006 of daytrader8's), the sequence within a line does not.

        See Also:
            :attr:`has_resolution_edges`: distinguishes a genuinely unresolved
            ``callee_signature`` from a graph that carries no resolution at all.
        """
        found = ((key, self._addressing.by_key.get(key)) for key in signatures)
        return {key: row.callable.call_sites for key, row in found if row is not None}

    def get_external_symbols(self) -> Dict[str, JExternalSymbol]:
        """Every call-graph endpoint outside the analysed project, keyed by its ``@external`` id.

        Returns:
            The analyzer's own ``external_symbols`` map. An empty dict means the run homed them and
            this project's call graph makes no calls outside itself.

        Raises:
            CodeanalyzerExecutionException: The run did not home them at all, which is **not** the
                same fact and must not read as one (D7). codeanalyzer-java emits
                ``external_symbols`` only under ``--external-calls`` — off by default, "matching
                v1's application-only call graph" in its own words. ``--emit neo4j`` forces the flag
                on, so the graph backend answers (1,195 ``:JExternal`` nodes on daytrader8, 2,570 on
                ThingsBoard); the SDK's own local run does not pass it, so the in-memory backend
                raises this until it does. The policy is one; what differs is what each source was
                asked for, and it is recorded in the lossiness table of
                ``docs/agent-api-reference.md``.
        """
        external = self.get_application_view().external_symbols
        if external is None:
            raise CodeanalyzerExecutionException(EXTERNAL_SYMBOLS_UNAVAILABLE)
        return external

    # -----[ the code-to-config layer (codeanalyzer-java 3.1.0) ]-----
    #
    # IMPLEMENTED HERE, NOT PER BACKEND, for this section's reason: :class:`JNeo4jBackend` rebuilds
    # the canonical :class:`JApplication` from the graph -- these three overlays included -- so both
    # backends read one object through one implementation, and the refusal below cannot come to
    # differ between them.
    #
    # THE TWO TIERS ARE SURFACED, NOT FLATTENED. ``prov`` on every edge names how the key was
    # reached: ``"literal"`` is a string literal at the call site (codeanalyzer-java#233),
    # ``"dataflow"`` is a key closed over the L3 DDG / the L4 call graph (#237) -- weaker evidence,
    # and it widens with the analysis level rather than replacing the literal tier. On a
    # :class:`PyConfigRead` the list is every tier *attempted* before giving up, so
    # ``["literal", "dataflow"]`` there means the dataflow tier ran too and still could not name the
    # key. Measured on daytrader8 at level 4: 13 uses, all ``["literal"]``; 16 unresolved reads,
    # all ``["literal", "dataflow"]`` (8 edges on the graph -- see :meth:`get_unresolved_config_reads`).
    def _config_overlay(self) -> Tuple[List[JConfigUse], List[JConfigRead]]:
        """This analysis's config-use and unresolved-read lists, or raise if it has neither pass.

        Raises:
            CodeanalyzerExecutionException: This analysis predates codeanalyzer-java 3.1.0
                (:data:`CONFIG_OVERLAY_UNAVAILABLE`, which is also where the probe's choice of
                witness is argued from measurement). Both backends raise the same type with the
                same message, which names the application and no ``can://`` id (E6).
        """
        app = self.get_application_view()
        if app.entrypoint_report is None:
            raise CodeanalyzerExecutionException(CONFIG_OVERLAY_UNAVAILABLE.format(app=self._application_name))
        return list(app.config_uses or []), list(app.config_reads_unresolved or [])

    def _config_key_ids(self, key: str) -> Set[str]:
        """The ids of every declared config key spelling ``key`` — a dotted key can be declared by
        more than one artifact (a ``daytrader.properties`` and a ``web.xml`` naming the same one),
        so this is a set and not a lookup. Compared on the id rather than on the dotted tail of the
        edge's ``dst``, because the id is what the analyzer wrote on both sides of the edge."""
        return {ck.id for a in self.get_application_view().artifacts.values() for ck in a.config_keys if ck.key == key}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Every code-to-config edge the analyzer resolved: a config read whose key closed on a
        declared :class:`~cldk.models.java.models.JConfigKey`.

        Args:
            key: Restrict to one dotted key, matched exactly against the declared key's own
                ``key`` (never fuzzily, E8). ``None`` returns every edge.

        Returns:
            The shared :class:`~cldk.models.python.PyConfigUseEdge`, as the generic ABC promises.
            ``src`` is the reading call's body-node id, ``dst`` the matched key's id, and ``prov``
            the tier that resolved it — see this section's note. Empty means the pass ran and
            resolved nothing.

        Raises:
            CodeanalyzerExecutionException: See :meth:`_config_overlay`.
        """
        uses, _ = self._config_overlay()
        wanted = None if key is None else self._config_key_ids(key)
        return [PyConfigUseEdge(src=u.src, dst=u.dst, prov=list(u.prov)) for u in uses if wanted is None or u.dst in wanted]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Every config read whose key closed on no declared key — first class, so a read nobody can
        trace stays as visible as one that resolves.

        Returns:
            The shared :class:`~cldk.models.python.PyConfigRead`. ``key`` is the literal text when it
            *was* a literal matching no declared key (``reason="undefined-key"``) and ``None`` when
            it never closed on one (``reason="non-literal"``); ``prov`` is every tier attempted.

            **The graph is lossier than the payload here, in two named ways, and neither is a
            presence/absence gap.** ``J_READS_CONFIG_UNRESOLVED`` runs application-to-ghost
            (``:JApplication`` → ``:JExternal``) and never touches the body node that made the call,
            so ``site`` comes back ``""`` off Neo4j; and the edge's discriminant is ``(key, reason)``
            rather than the site, so several call sites reading one key collapse into one edge —
            daytrader8's 16 wire entries are 8 edges. Any unresolved read still guarantees at least
            one edge, so "no rows" never means a false negative. Same shape, and the same reason, as
            codeanalyzer-python's ``PY_READS_CONFIG_UNRESOLVED``.

        Raises:
            CodeanalyzerExecutionException: See :meth:`_config_overlay`.
        """
        _, reads = self._config_overlay()
        return [PyConfigRead(site=r.site, callee=r.callee, key=r.key, reason=r.reason, prov=list(r.prov)) for r in reads]

    def get_config_readers(self, key: str) -> List[JCallableOverview]:
        """Overviews of every callable that reads one config key — :meth:`get_config_uses` resolved
        from body nodes back to the callables that own them.

        Args:
            key: The dotted key, matched exactly as in :meth:`get_config_uses`.

        Returns:
            One overview per distinct callable, in the addressing index's order; a callable reading
            the key at several call sites appears once. Empty means nothing reads it.

            A ``J_USES_CONFIG`` edge may also be rooted on a field, a type or a callable rather than
            on a body node (the schema allows all four sources; every one of daytrader8's 13 is a
            body node). A field- or type-rooted read has no reading *callable*, so it has no entry
            here — it is still in :meth:`get_config_uses`, which is where the whole edge set lives.

        Raises:
            CodeanalyzerExecutionException: See :meth:`_config_overlay`.
        """
        # Through :meth:`get_config_uses` rather than around it, so a key this application does not
        # declare cannot skip the refusal: whether the overlay is there is not a fact about which
        # key you asked for.
        found: Dict[str, _Addressed] = {}
        for use in self.get_config_uses(key):
            # ``src`` is a body-node id (``<callable id>@<line>:<col>``) or, for the other three
            # sources the schema allows, the declaration's own id. One lookup covers both: a
            # callable id carries no ``@`` of its own.
            row = self._addressing.by_id.get(use.src) or self._addressing.by_id.get(use.src.rpartition("@")[0])
            if row is not None:
                found[row.key] = row
        return [JCallableOverview.of(r.key, r.type, r.callable, path=r.path) for r in found.values()]

    # -----[ the type-kind leaf accessors (J-7) ]-----
    def get_interfaces(self) -> Dict[str, JType]:
        """Every interface, keyed by qualified name — the subset of :meth:`get_all_classes` whose
        ``kind`` is ``interface`` (3 in daytrader8, 594 in ThingsBoard). Names shared with
        TypeScript for the same concept (G3)."""
        return self._of_kind("interface")

    def get_enums(self) -> Dict[str, JType]:
        """Every enum, keyed by qualified name (none in daytrader8, 192 in ThingsBoard)."""
        return self._of_kind("enum")

    def get_records(self) -> Dict[str, JType]:
        """Every record, keyed by qualified name — the one Java-only kind (none in daytrader8, 35 in
        ThingsBoard). Annotation types have no leaf accessor of their own; they stay reachable
        through :meth:`get_all_classes` (J-7)."""
        return self._of_kind("record")

    def get_enum_members(self, qualified_enum_name: str) -> List[JEnumConstant]:
        """The constants declared by one enum.

        Args:
            qualified_enum_name: The enum's qualified name, as :meth:`get_enums` keys it.

        Raises:
            SelectorNotInGraph: The name is not an enum of this application — either no type at
                all, or a type of another kind. Raising keeps that apart from an enum that
                genuinely declares no constant, which is what an empty list means here (D7).
                It names the value the caller wrote and nothing else (E8).
        """
        found = self._types.get(qualified_enum_name)
        if found is None or found.kind != "enum":
            raise SelectorNotInGraph(kind="enum", missing=[qualified_enum_name], requested=1)
        return list(found.enum_constants)

    def _of_kind(self, kind: str) -> Dict[str, JType]:
        return {name: t for name, t in self._types.items() if t.kind == kind}

    # =====================================================================================
    # The six 1.x accessors leg 3 deferred (#366). Each is implemented **once, here**, over what
    # both backends already answer -- the symbol table, the class index, the callable index and
    # ``get_call_graph()`` -- for the same reason the call-graph half of the dataflow surface is:
    # a per-backend implementation would have nothing to read that this one cannot, and would only
    # be a second place for the answer to drift. **No new Cypher is issued for any of the six**;
    # every fact they need (``J_IMPORTS``, ``J_DECLARES_VAR``, ``J_EXTENDS``/``J_IMPLEMENTS``,
    # ``J_ANNOTATED_BY``, ``J_CALLS``) is already read by the projection's reconstruction into the
    # pydantic models, so the graph backend answers all six off the application it already builds.
    #
    # Their signatures are the 1.x facade's, frozen (the Iron Rule): they are *not* Python's, which
    # has five of the six not at all, and not TypeScript's, which spells three of them differently
    # on purpose. What each one *returns* is decided here, and the decision is written down.
    # =====================================================================================
    def get_imports(self) -> List[str]:
        """Every distinct import target of the application, sorted -- a **set**, not a per-file
        listing, and not the file's import order.

        That is a fact about the projection, not a simplification: ``--emit neo4j`` aggregates every
        import of a module that resolves to the same target onto **one** ``J_IMPORTS`` edge carrying
        their dotted ``spellings`` (see :func:`cldk.analysis.java.neo4j.reconstruct.imports`), so
        the order the imports appear in the file is not recoverable from the graph, and a list that
        preserved it locally would be a list the two backends disagree about. Sorting and
        de-duplicating is the answer both can give -- and the 1.x signature is a flat
        ``List[str]`` anyway, which never carried the file an import belongs to. Per-file import
        declarations, with their spans, are on
        :attr:`~cldk.models.java.models.JCompilationUnit.import_declarations`.

        A wildcard import keeps its ``.*`` (the analyzer writes the spelling, ``java.util.*``) and a
        static import is not marked as such here; both distinctions live on
        :class:`~cldk.models.java.models.JImport`.
        """
        return sorted({i.path for unit in self.get_symbol_table().values() for i in unit.import_declarations})

    def get_variables(self) -> Dict[str, List[JLocalVariable]]:
        """The **local variables** each callable declares, keyed by the J-1 call-graph key
        ``"<type fqn>.<signature>"`` -- the ``J_DECLARES_VAR`` layer of the graph.

        The 1.x return type is a bare ``Dict`` and the docstring names "local variables, fields, and
        parameters". Those are three different things with three different homes, and this is the
        one with no other accessor: fields are :meth:`get_all_fields` (``J_HAS_FIELD``) and
        parameters are :meth:`get_method_parameters`, both already on this surface, so folding all
        three into one dict would give every caller a bag they have to re-split. Locals are what is
        left, and ``J_DECLARES_VAR`` is exactly them.

        Every callable in the addressing domain gets an entry, including the ones that declare
        nothing: an absent key would be indistinguishable from a callable that is not in the
        application, which is the ambiguous empty (D7). Implicit constructors carry no body and so
        carry an empty list.

        **Ordered by (line, name), not by declaration order within a line.** The wire's order is the
        source's, but the projection carries a line-only span -- ``:JLocal`` has no column -- so two
        variables declared on one line (``String htmlString, arrow;``) come back in an order the
        graph does not fix. Measured on the reference graph: 6 of daytrader8's 1,216 callables, 12
        variables, same multiset, different order. Sorting is what the two backends can both do; the
        alternative is a list whose order means "source" on one backend and "arbitrary" on the
        other. The span itself still differs -- ``start_column``/``end_column`` and the byte offsets
        are placeholders over the graph, as they are everywhere else on this surface -- so compare
        variables on ``name``/``type``/``start_line``.
        """
        return {
            f"{klass}.{signature}": sorted(c.local_variables, key=lambda v: (v.start_line, v.name))
            for klass, methods in self.get_all_methods_in_application().items()
            for signature, c in methods.items()
        }

    def get_class_hierarchy(self) -> nx.DiGraph:
        """The inheritance graph: a node per type declared in the application, and an edge
        **child → base** for each supertype, carrying ``type="EXTENDS"`` or ``type="IMPLEMENTS"``.

        Modelled on :meth:`cldk.analysis.typescript.backend.TypeScriptAnalysisBackend.get_class_hierarchy`,
        which is the only sibling that has one, with the one difference Java forces: TypeScript's
        wire has a single ``base_classes`` list, while Java's has ``base_types`` and ``interfaces``
        and the projection keeps them as two relationship types (``J_EXTENDS`` / ``J_IMPLEMENTS``).
        Collapsing them would throw away the distinction the graph is at pains to keep, so it is on
        the edge.

        Nodes are :meth:`get_all_classes`'s keys -- every declared kind, interfaces, enums,
        annotations and records included, since all of them participate. A supertype **outside** the
        application (``javax.servlet.http.HttpServlet``) becomes a node too, by being an edge
        endpoint: it is named exactly as the analyzer wrote it on the declaration, which is the
        source spelling and not necessarily a qualified name. Type arguments are part of that
        spelling where the source wrote them.

        **Read off each declaration's own** ``base_types`` / ``interfaces``, **not off the**
        ``J_EXTENDS`` / ``J_IMPLEMENTS`` **relationships**, and that is the difference between an
        answer and a fifteenth of one: those relationships can only join two types the projection
        has *nodes* for, and daytrader8 extends and implements almost nothing it declares itself.
        Measured on the reference graph: 8 relationships (1 ``J_EXTENDS``, 7 ``J_IMPLEMENTS``)
        against the 103 edges (58 / 45) the declarations carry. The properties are projected onto
        every ``:JType``, so both backends read the same list.

        A type that neither extends nor is extended is an isolated node rather than a missing one,
        which is why the nodes are added before the edges.
        """
        graph = nx.DiGraph()
        for name, declared in self.get_all_classes().items():
            graph.add_node(name)
            for base in declared.base_types:
                graph.add_edge(name, base, type="EXTENDS")
            for interface in declared.interfaces:
                graph.add_edge(name, interface, type="IMPLEMENTS")
        return graph

    def get_methods_with_annotations(self, annotations: List[str]) -> Dict[str, List[Dict]]:
        """The callables carrying each requested annotation, grouped by the requested spelling.

        Neither sibling language has this accessor, so it is designed rather than ported, and the
        pattern it follows is :meth:`JavaAnalysis.get_test_methods`: read the **analyzer's own**
        annotations off the model (``J_ANNOTATED_BY``; 26,162 edges on the reference graph) rather
        than re-parsing a source string, so it answers identically on both backends -- the 1.x
        tree-sitter version returned ``{}`` on any projection that carried no module text.

        Args:
            annotations: Annotation names, matched by the J-5 marker rule
                :meth:`get_decorated_callables` uses: both sides compared on the segment after the
                last ``.``, with a leading ``@`` stripped, so ``Test``, ``@Test`` and
                ``org.junit.Test`` all match a callable annotated ``@Test``.

        Returns:
            A dict keyed by **the string the caller passed**, not by the annotation's spelling in
            the source, so ``result[a]`` works for every ``a`` the caller asked about. An annotation
            no callable carries is **omitted** (the 1.x shape), so an empty dict means none of the
            requested annotations was found. Each value is a list of dicts, in
            :meth:`get_all_methods_in_application` order, with four keys:

            * ``class`` -- the declaring type's qualified name;
            * ``signature`` -- the callable's signature within that type;
            * ``method_name`` -- its simple name (the 1.x key, kept);
            * ``body`` -- :attr:`~cldk.models.java.models.JCallable.code` (the 1.x key, kept),
              the body block on either backend since codeanalyzer-java 3.2.0.

            ``class`` and ``signature`` are new against 1.x, which returned the simple name alone.
            A simple name is not an address in Java -- 200 of daytrader8's 581 distinct signatures
            are declared by more than one type -- so without them a caller cannot find again what
            this hands them.

            Each list is sorted by ``(class, signature)``. The obvious alternative, "the order
            :meth:`get_all_methods_in_application` walks in", is not one order but two: the local
            backend walks the symbol table and the graph backend walks its reconstruction, and the
            same 328 callables came back in different orders (measured on the reference graph).
        """
        wanted = {a: a.lstrip("@").rpartition(".")[2] for a in annotations}
        found: Dict[str, List[Dict]] = {}
        for klass, methods in sorted(self.get_all_methods_in_application().items()):
            for signature, c in sorted(methods.items()):
                carried = {d.name.rpartition(".")[2] for d in c.decorators}
                for asked, marker in wanted.items():
                    if marker in carried:
                        found.setdefault(asked, []).append({"class": klass, "signature": signature, "method_name": signature.partition("(")[0], "body": c.code})
        return found

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """The simple names, out of ``declared_methods``, that are actually invoked somewhere in the
        application -- "simple name resolution", as the 1.x docstring calls it.

        The 1.x version took a method *body* as well and answered for that one body; the facade
        signature the Iron Rule freezes has no body parameter, so the domain is the whole
        application: every call site of every callable, filtered to the names asked about. That is
        the only reading of the frozen signature that uses the argument it does take.

        Deliberately **not** the resolved call graph. A call site's ``method_name`` is the name as
        written at the call, matched against a set of declared names with no overload resolution, no
        receiver typing and no hierarchy walk -- which is what the docstring's "without full
        semantic analysis" means, and what makes this different from :meth:`get_call_graph`. Use the
        call graph when you want the callable that actually runs.

        Args:
            declared_methods: The names to match against, read from its **keys** -- so
                :meth:`get_all_methods_in_class`'s result can be passed straight in. A key may be a
                signature (``cancelOrder(java.lang.Integer, boolean)``) or a bare name
                (``cancelOrder``); the parameter tail is cut at the last ``(``, the cut
                :func:`java_callable_names` documents.

        Returns:
            The matched simple names -- a subset of the cut keys, never a name the caller did not
            ask about. Empty when nothing was asked or nothing was called.
        """
        declared = {java_callable_names(key)[-1] for key in declared_methods}
        return {site.method_name for methods in self.get_all_methods_in_application().values() for c in methods.values() for site in c.call_sites if site.method_name in declared}

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """The **absolute file lines**, sorted and de-duplicated, of every call to a method of this
        name anywhere in the application.

        Read straight off ``get_call_graph()``'s ``calling_lines`` edge attribute, which is
        :class:`CallingLines` -- the one place this SDK turns a call site into a file line, and
        which already resolved (leg 3a) the 1.x bug where the number was an offset into
        :attr:`~cldk.models.java.models.JCallable.code` and so meant two different things on the two
        backends. Nothing here re-derives it.

        Args:
            target_method_name: A method's simple name (``cancelOrder``). A full signature is
                accepted and cut at its first ``(``, as the 1.x accessor did -- and as
                :meth:`CallingLines.of` keys its own index, which is why an overload pair cannot be
                separated here: the index matches on the name a call site *writes*, and a call site
                writes no parameter types. Asking for one overload is asking for the name.

        Returns:
            Sorted, distinct file lines, 1-based as the analyzer's spans are. Empty when no call to
            that name is on the call graph -- including when the name is not a method of this
            application at all: the two are the same fact here, because this asks about call sites
            and not about declarations.
        """
        name = target_method_name.partition("(")[0]
        graph = self.get_call_graph()
        lines: Set[int] = set()
        for _, dst, data in graph.edges(data=True):
            if graph.nodes[dst]["method_detail"].method.signature.partition("(")[0] == name:
                lines.update(data["calling_lines"])
        return sorted(lines)

    # =====================================================================================
    # The dataflow surface (leg 3b, Task 2): per-callable graphs, slices, reachability, paths and
    # the flow predicates. Every signature below is `cldk/analysis/python/backend.py`'s,
    # keyword-for-keyword, default-for-default, with Java's edge models.
    #
    # WHAT IS HERE AND WHAT IS PER BACKEND, and it is Task 1's reason rather than a convenience.
    # The *call-graph* half (``reaches``, ``callers_of``, ``callees_of``, ``backward_cone``,
    # ``call_paths_between``) is implemented **once, here**, over the ``get_call_graph()`` both
    # backends already build -- leg 3a made :class:`JNeo4jBackend` project ``J_CALLS`` into the same
    # ``nx.DiGraph`` keyed by the same J-1 names, so a second implementation would have nothing to
    # read that this one cannot and would only be a second place for "who calls this" to drift. The
    # *body-node* half is not in that reconstruction (``JCallable.cfg``/``cdg``/``ddg``/``summary``
    # are ``None`` over the graph, and ``JApplication.param_in``/``param_out`` empty), so it reaches
    # Cypher there and the fixture here, through four seams: :meth:`get_cfg` / :meth:`get_cdg` /
    # :meth:`get_ddg`, :meth:`_value_slice`, :meth:`_value_paths` and :meth:`_value_reaches`.
    #
    # BOUNDS ARE ASYMMETRIC ON PURPOSE (E5). The three *slices* default ``depth`` to
    # :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH` and cap ``max_nodes``: a bounded traversal
    # is a *complete* answer to a narrower question, and ``total`` says how much was left out. The
    # three *predicates* and the two *path* queries default to ``depth=None`` -- unbounded -- because
    # a hop budget on a boolean or a path list is not a smaller answer but a **wrong** one.
    # Measured on daytrader8: ``reaches(TradeDirect.sell(…), TradeDirect.getStatement(…))`` is
    # ``True`` unbounded and ``False`` at ``depth=1``, and the matching ``call_paths_between`` is
    # ``[]`` at one hop and six paths at two.
    #
    # ONE COMPLETENESS PROTOCOL. Truncation is reported by ``complete`` on ``EdgePage`` / ``Slice``
    # / ``FlowPaths``, never by silently returning less.
    #
    # FIVE ACCESSORS REFUSE ON A DISCONNECTED PORT LATTICE RATHER THAN ANSWERING A CONSTANT.
    # Asked of the analysis, never of the analyzer version: codeanalyzer-java joins the port lattice
    # to the statement graph from 3.0.3, and on such output all five answer with no change here.
    # See :data:`PORTS_DISCONNECTED`.
    # =====================================================================================
    @property
    def _application_name(self) -> str:
        """The ``--app-name`` this application was analysed under, for a message to name it by.

        Read off the application's own id rather than stored, so the in-memory backend (which has
        no ``application_name``) and the graph backend give one answer. Only the *name* segment
        leaves this method: a ``can://`` id must not appear in a message (E6)."""
        return self.get_application_view().id.rpartition("/")[2]

    # -----[ the per-callable graphs ]-----
    @abstractmethod
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCfgEdge]:
        """One page of the control flow edges within one callable.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable` — so an ambiguous
                name raises listing candidates rather than being guessed at.
            in_class: Disambiguate by owning class, as in :meth:`resolve_callable`.
            page_size: Most edges to return. See
                :data:`~cldk.analysis.commons.bounds.DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page; ``None`` starts at the beginning.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.java.models.JCfgEdge`, each carrying the analyzer's ``kind``
            (``fallthrough``, ``true``, ``false``, ``return``, ``loop_back``, ``exception``,
            ``break``, ``switch_case``) — a conditional's two successors stay two edges,
            discriminated by ``kind``, which is why ``kind`` is part of the order
            (:data:`CFG_ORDER`). Endpoints are the body nodes' own ``can://`` ids
            (:func:`java_body_node_id`), the spelling :meth:`get_source` and :meth:`locate` speak.

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page of this
                accessor and this callable.
            CodeanalyzerUsageException: ``callable`` is an **implicit** callable — addressable
                under J-6 and emitted with no body at all, so it has no flow to page; on both
                backends. Or, on the local backend only, this analysis was built below
                ``analysis_level="program_dependency_graph"``, where the analyzer emits no
                cfg/cdg/ddg at all. An empty page for either would read as "no dependence" (D7).
        """

    @abstractmethod
    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCdgEdge]:
        """One page of the control dependence edges within one callable.

        ``src`` is the branching node a ``dst`` is control dependent on — post-dominance over the
        CFG :meth:`get_cfg` returns, computed by the analyzer, not re-derived here. Arguments,
        bounds and failures are :meth:`get_cfg`'s; the order is :data:`CDG_ORDER`.
        """

    @abstractmethod
    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JDdgEdge]:
        """One page of the data dependence edges within one callable.

        Each edge carries the variable it flows (``var``) and its evidence (``prov``).

        **Java's DDG has exactly two provenance tiers**, where Python has three and TypeScript one:
        ``ssa`` (324,959 edges on the reference graph) and ``points-to`` (1,134).
        :func:`~cldk.analysis.commons.results.prov_rank` ranks ``points-to`` least certain and
        ``ssa`` most, which is the ranking a caller comparing two hops' evidence reads; nothing here
        invents a third tier and nothing collapses the two.

        Arguments, bounds and failures are :meth:`get_cfg`'s; the order is :data:`DDG_ORDER`, which
        includes ``var`` and ``prov`` because the same statement pair legitimately appears more than
        once when it carries several variables, and collapsing those would drop dependences.

        **A self-loop is an edge like any other and must be in the page.** 978 ``J_DDG`` edges run
        from a body node to itself (133 in daytrader8, 845 in ThingsBoard); a page built by binding
        the containment relationship twice — ``(c)-[:J_HAS_BODY_NODE]->(s)-[r]->(d)<-[:J_HAS_BODY_NODE]-(c)``,
        which Cypher's relationship-uniqueness rule forbids from matching one relationship twice —
        drops every one of them *and* computes ``total`` from the same MATCH, so the result reports
        itself complete. That is python-sdk#349, 64,702 lost edges on the Python corpus; neither
        Java backend spells it that way, and both suites assert the loops are present.

        **The one place the two backends used to disagree, and it was the analyzer's.**
        codeanalyzer-java up to 3.0.2 emitted ddg edges whose endpoint is a body key it did **not**
        emit as a body node — measured on daytrader8: 87 of 5,434, 38 distinct keys, every one of
        the shape ``<line>:0``, every one on a ``points-to`` edge, and none at all on ``cfg`` or
        ``cdg``. The Neo4j emitter materialises nodes from the ``body{}`` map, so an edge with no
        node to attach to was not projected and the graph reported 5,347. **3.0.3 drops them**
        (codeanalyzer-java#228) and the two counts agree; both suites assert the count rather than
        tolerating a difference, so the divergence coming back is a failure and not a footnote.
        """

    # -----[ slicing ]-----
    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` depends on: reverse reachability over the SDG.

        The edge set is :data:`SDG_RELS` — data and control dependence within a callable, the two
        parameter-passing relationships across a call, and the callee summaries at a call site. All
        five point *with* the flow, so a backward slice follows them reversed.

        **What this answers on Java, stated rather than discovered.** ``src`` is a parameter (see
        :meth:`resolve_value`), and the edge that reaches one is ``J_PARAM_IN`` from a caller's
        argument vertex. On a disconnected port lattice (:data:`PORTS_DISCONNECTED`) that is the
        whole answer — the seed plus one argument vertex per call site, which is why this accessor
        is not among the four that refuse, but a *thin* answer with the expression behind each
        argument missing. From codeanalyzer-java 3.0.3 the walk carries on through the statement
        that computed each argument and into the caller's own parameters: measured on the committed
        a4 fixture, ``TradeDirect.getStatement``'s ``conn`` went from 35 nodes of two kinds to 465
        across 44 callables, and ``cancelOrder``'s ``orderID`` from two to 15.

        ``within`` is **required**: a value name is scoped by its callable and :meth:`resolve_value`
        cannot resolve one without it, so a ``None`` default would be a signature that raises on its
        own default.

        Args:
            src: The value's name, resolved by :meth:`resolve_value` — in Java, a parameter.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`.
            depth: Most hops from the seed. Defaults to
                :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH`; ``None`` for the whole cone.
            max_nodes: Most nodes in the result. A cap that fires is reported by
                :attr:`~cldk.analysis.commons.results.Slice.complete` and quantified by
                :attr:`~cldk.analysis.commons.results.Slice.total`; it is never silent.

        Returns:
            A :class:`~cldk.analysis.commons.results.Slice` containing the seed, ordered by node id,
            with ``source`` unhydrated on every node (:meth:`describe` fills it in).

        Raises:
            AmbiguousName: ``within`` named more than one callable, or ``src`` more than one value.
            SelectorNotInGraph: No such callable, or no such value in it.
            ValueError: ``depth`` that is not a positive ``int``, or ``max_nodes`` below 1.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``.
        """
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_dataflow()
        return self._value_slice(self.resolve_value(src, within=within), backward=True, depth=depth, max_nodes=max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` can affect: forward reachability over the same edges.

        The usually-interesting direction for a value entering a callable. It refuses on an
        analysis whose ``formal_in`` vertices have out-degree zero over all five SDG relationship
        types — there it would return the seed alone for every parameter of every application,
        indistinguishable from "this parameter affects nothing". See :data:`PORTS_DISCONNECTED` for
        when that is still the case and for what is unaffected.

        Arguments, bounds and failures are :meth:`slice_backward`'s, and they are judged **first**:
        a malformed ``depth`` is a ``ValueError`` and a name that misses is
        :class:`~cldk.utils.exceptions.SelectorNotInGraph`, before the gap is mentioned.

        Raises:
            CodeanalyzerExecutionException: The analyzer's port lattice carries no dependence edge
                (:data:`PORTS_DISCONNECTED`).
        """
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_dataflow()
        root = self.resolve_value(src, within=within)
        self._require_connected_ports("slice_forward")
        return self._value_slice(root, backward=False, depth=depth, max_nodes=max_nodes)

    @abstractmethod
    def _value_slice(self, root: SliceNode, *, backward: bool, depth: int | None, max_nodes: int) -> Slice:
        """One direction of the SDG closure from ``root``, described and capped.

        The seam the two slices share, so the bounds, the guard and the resolution are applied once
        above it and a backend supplies only the walk. The whole closure is computed and *then* cut:
        ``total`` has to be the size of the whole slice for the cap to be reportable (E5).
        """

    def _body_slice_node(self, ref: str, kind: str, line: "int | None") -> SliceNode:
        """One reached body node as a :class:`SliceNode`, in the caller's vocabulary.

        Built here, from the ``_addressing`` index both backends already hold, rather than from a
        join each backend writes for itself: the owning callable is the ``ref`` up to its first
        ``@`` (exact — a Java ``can://`` id carries none, checked on 1,216 and 28,763 callables), and
        the J-1 key, the file and the parameter names all come off the same row. So a Cypher slice
        and an in-memory one describe a vertex identically, and the graph statement need not project
        a callable at all.

        A port vertex has no span of its own, so the *callable's* first line stands in — and stays
        ``-1`` for an implicit callable, which is the model's "not known" rather than a position.
        """
        row = self._addressing.by_id.get(ref.partition("@")[0])
        if row is None:
            raise CodeanalyzerExecutionException(unhomed_endpoint(ref))
        node_kind, name = java_body_node_kind(ref, kind, row.callable.parameters)
        return SliceNode(file=row.path, line=line if line and line > 0 else row.callable.start_line, callable=row.key, kind=node_kind, name=name, source=None, ref=ref)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every callable that can reach any of ``sinks`` — "what could get here".

        A **call-graph** cone, so its nodes are call-graph vertices rather than body nodes. In Java
        they are all callables: unlike TypeScript, no module is a caller, and unlike Python and
        TypeScript, an external is not a vertex of :meth:`get_call_graph` on either backend — the
        in-memory payload drops ``@external/`` endpoints and the graph statement matches
        ``:JCallable`` at both ends. The sinks themselves are in the result, and in
        :attr:`~cldk.analysis.commons.results.Slice.roots`.

        Args:
            sinks: The callables to walk back from, each resolved by :meth:`resolve_callable`.
            depth: Most call hops back. Defaults to
                :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH`; ``None`` for the whole cone.
            max_nodes: Most nodes in the result.

        Raises:
            AmbiguousName: A sink name matched more than one callable.
            SelectorNotInGraph: A sink name matched none.
            TypeError: ``sinks`` is a bare string.
            ValueError: ``sinks`` is empty, ``depth`` is not a positive ``int``, or ``max_nodes``
                is below 1.
        """
        check_depth(depth)
        check_max_nodes(max_nodes)
        roots = cone_sinks(self.resolve_callable, sinks)
        graph = self.get_call_graph()
        reached: set = set()
        for root in roots:
            reached.add(root.callable)
            if root.callable in graph:
                # ``ego_graph`` follows *successors*, so a backward question needs the reversed
                # view; on the graph itself ``depth=1`` would otherwise return the forward cone.
                reached |= nx.ancestors(graph, root.callable) if depth is None else set(nx.ego_graph(graph.reverse(copy=False), root.callable, radius=depth).nodes)
        found = sorted((n for n in (self._callable_node(key) for key in reached) if n is not None), key=lambda n: n.ref)
        return Slice(nodes=found[:max_nodes], roots=roots, resolved=slice_resolved(roots), total=len(found))

    # -----[ the call graph ]-----
    def _callable_node(self, key: str) -> "SliceNode | None":
        """The callable ``key`` (a J-1 ``"<type fqn>.<signature>"`` name) as a :class:`SliceNode`.

        ``None`` for a key the call graph carries but the containment index does not declare, which
        on Java cannot happen — both backends home every endpoint on the index and raise
        :func:`unhomed_endpoint` if one is not there — and is kept as the honest reading of a
        lookup that can miss rather than as an assertion about an emitter this SDK does not own.
        """
        row = self._addressing.by_key.get(key)
        if row is None:
            return None
        c = row.callable
        return SliceNode(file=row.path, line=c.start_line, callable=row.key, kind="callable", name=c.signature.rpartition("(")[0] or c.signature, source=None, ref=c.id)

    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Who calls this — one hop back over the call graph, addressed by name.

        The name-based sibling of :meth:`get_all_callers`, which takes a class name plus a method
        signature and returns raw dicts. That one is a frozen 1.x signature and is not touched; this
        one takes a name the caller already has and returns
        :class:`~cldk.analysis.commons.results.SliceNode` objects, ordered by ``ref`` — the one
        total order both backends can compute.

        An empty list is unambiguous: a name that matches nothing raises, so ``[]`` means "nothing
        calls it".

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """
        return self._call_neighbours(name, in_class, in_module, callers=True)

    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """What this calls — one hop forward over the call graph, addressed by name.

        **No externals, on either backend, and that is the graph's shape rather than a filter here.**
        Python's and TypeScript's ``callees_of`` report ``kind="external"`` vertices; Java's
        :meth:`get_call_graph` has none — ``--emit neo4j`` does write ``J_CALLS`` edges to
        ``:JExternal`` targets, and leg 3a's statement matches ``:JCallable`` at both ends so the
        two backends agree, which is the trade J-1 already made. ``get_external_symbols`` is where
        those live.

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """
        return self._call_neighbours(name, in_class, in_module, callers=False)

    def _call_neighbours(self, name: str, in_class: str | None, in_module: str | None, *, callers: bool) -> List[SliceNode]:
        """One hop of the call graph, in the caller's vocabulary. Resolution is
        :meth:`resolve_callable`'s, not a second path."""
        key = self.resolve_callable(name, in_class=in_class, in_module=in_module).callable
        graph = self.get_call_graph()
        if key not in graph:
            return []
        neighbours = graph.predecessors(key) if callers else graph.successors(key)
        return sorted((n for n in (self._callable_node(other) for other in neighbours) if n is not None), key=lambda n: n.ref)

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path from ``src`` to ``dst``?

        A **call-graph** question — "can control get from here to there at all", the cheap check a
        caller makes before asking for the paths themselves. Both names go through
        :meth:`resolve_callable`, so an ambiguous one raises listing candidates rather than being
        guessed at, and both endpoints are therefore callables.

        Returns ``bool`` and nothing else: it is deliberately not a degenerate ``Slice``, because
        "is there a path" and "what is on it" are different questions with different costs.

        **``depth`` defaults to ``None`` here, unlike the three slices.** A default that bounds a
        *slice* trades size for a complete answer to a narrower question; a default that bounds a
        *boolean* would turn "there is no path" and "there is no path within five hops" into the
        same ``False``.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either name matched none.
            ValueError: ``depth`` that is not a positive ``int``.
        """
        check_depth(depth)
        a = self.resolve_callable(src).callable
        b = self.resolve_callable(dst).callable
        # See :func:`~cldk.analysis.commons.graphs.call_reaches`: the self-question is the cycle
        # question, and both backends walk this same networkx graph, so there is one answer.
        return call_reaches(self.get_call_graph(), a, b, depth)

    # -----[ paths and flow predicates ]-----
    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value — the *sequences*, where a slice is the set.

        **Two scopes, not one, and neither defaults to the other.** A value is addressed by a name
        plus the callable it enters, so two values need two callables — and a single scope could
        never find the cross-callable path this accessor exists for.

        Refused when both endpoints are parameters and a ``formal_in`` has out-degree zero over the
        SDG, because the search would then return ``[]`` for every input
        (:data:`PORTS_DISCONNECTED`). Arguments and names are judged first, then it refuses.

        Args:
            src: The value the flow starts at, named as a caller would.
            dst: The value it must reach.
            src_within: The callable ``src`` enters. Required.
            dst_within: The callable ``dst`` enters. Required, and not defaulted.
            depth: Most hops a path may take; ``None`` (the default) for no bound.
            max_paths: Most paths to return. The result's ``complete`` says whether more existed.

        Raises:
            AmbiguousName: ``src``, ``dst`` or either callable name matched more than one thing.
            SelectorNotInGraph: One of them matched nothing.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, or ``src``
                and ``dst`` resolve to the same position.
            CodeanalyzerExecutionException: :data:`PORTS_DISCONNECTED`.
        """
        check_depth(depth)
        check_max_paths(max_paths)
        self._require_dataflow()
        a = self.resolve_value(src, within=src_within)
        b = self.resolve_value(dst, within=dst_within)
        check_distinct_endpoints(a, b)
        self._require_connected_ports("paths_between")
        return self._value_paths(a, b, depth, max_paths)

    @abstractmethod
    def _value_paths(self, a: SliceNode, b: SliceNode, depth: int | None, max_paths: int) -> FlowPaths:
        """Up to ``max_paths`` shortest SDG walks from ``a`` to ``b``, in the documented order.

        **Only shortest paths.** A search that enumerated every walk would not terminate on a real
        dependence graph, and the tenth-longest way a value can reach another is not evidence anyone
        wants. What comes back is the shortest hop-count, and every path of it up to ``max_paths``.
        """

    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How one callable reaches another — the same sequences, over the call graph.

        The evidence-carrying form of :meth:`reaches`: that answers *whether*, this answers *how*.
        Every hop is ``via="call"`` with no ``var`` and no ``prov``, because a ``J_CALLS`` edge
        carries neither — a call is a syntactic fact, and saying so explicitly is better than
        inventing a provenance for it. Over the same ``get_call_graph()`` :meth:`reaches` and
        :meth:`callees_of` read, so the paths cannot disagree with the boolean that summarises them
        or with the neighbours a caller can enumerate.

        Takes no ``within``: a callable is addressed by name alone. ``depth`` defaults to ``None``
        as :meth:`reaches`'s does.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either matched nothing.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, or ``src``
                and ``dst`` name the same callable.
        """
        check_depth(depth)
        check_max_paths(max_paths)
        a_node, b_node = self.resolve_callable(src), self.resolve_callable(dst)
        check_distinct_endpoints(a_node, b_node)
        a, b = a_node.callable, b_node.callable
        graph = self.get_call_graph()
        if a not in graph or b not in graph:
            return FlowPaths(paths=[], complete=True)
        # The call graph re-projected as the ``{src: {dst: [label]}}`` adjacency
        # :func:`~cldk.analysis.commons.graphs.shortest_walks` walks, so one walker serves both
        # kinds of path and the branch order is the caller's vocabulary rather than the graph's.
        edges: Dict[str, Dict[str, list]] = {n: {m: [("J_CALLS", None, ())] for m in graph.successors(n)} for n in graph}
        walks = shortest_walks(edges, a, b, depth, max_paths + 1, via=VIA)
        described = {key: self._callable_node(key) for walk in walks for key, _ in walk}
        described[a] = self._callable_node(a)
        paths = [flow_path([described[a]] + [described[key] for key, _ in walk], [label for _, label in walk], via=VIA) for walk in walks[:max_paths]]
        return FlowPaths(paths=paths, complete=len(walks) <= max_paths)

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach **any** argument of a call to ``callee``?

        The target is the set of ``callee``'s ``formal_in`` vertices — in Java, exactly its declared
        parameters (verified on the level-4 fixture: all 225 line up name-for-name and
        index-for-index). Those are enterable only through ``J_PARAM_IN`` from a caller's argument,
        so reaching one means the value was passed into a real call, not merely that it sits in the
        same program. A value that only *control*-dominates a call site without feeding any of its
        arguments is deliberately **not** counted.

        **One ``within``, scoping ``src`` only.** :meth:`paths_between` takes two callables because
        it takes two *values*; here the second endpoint is ``callee``, a callable addressed by name
        alone, so a second scope would have nothing to scope. ``depth`` defaults to ``None``: a bare
        ``False`` on a boolean carries no signal that a bound fired.

        Refused on an analysis whose port lattice carries no dependence edge
        (:data:`PORTS_DISCONNECTED`), rather than being ``False`` for every input.

        Raises:
            AmbiguousName: ``src`` or ``callee`` matched more than one thing.
            SelectorNotInGraph: Either matched nothing.
            ValueError: ``depth`` is not a positive ``int``.
            CodeanalyzerExecutionException: :data:`PORTS_DISCONNECTED`.
        """
        check_depth(depth)
        self._require_dataflow()
        root = self.resolve_value(src, within=within)
        targets = [ref for ref in self._callee_values(self.resolve_callable(callee).callable) if ref != root.ref]
        self._require_connected_ports("flows_to_call")
        return bool(targets) and self._value_reaches(root.ref, targets, depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach the argument ``arg`` of a call to ``callee``?

        A **different question** from :meth:`flows_to_call`, and kept a separate implementation on
        purpose: a tainted value routinely reaches a function without reaching the parameter that
        matters.

        ``arg`` is resolved to the parameter **by name**, through the same :meth:`resolve_value` the
        other accessors use, with ``within=callee`` — nothing here asks the caller to know which
        slot a parameter occupies (E7). The implication ``flows_to_argument`` ⟹ ``flows_to_call``
        therefore holds by construction rather than by agreement between two queries:
        ``resolve_value(arg, within=callee)`` can only return one of ``callee``'s ``formal_in``
        vertices, and that set is exactly what :meth:`flows_to_call` tests reachability of.

        Refused on an analysis whose port lattice carries no dependence edge
        (:data:`PORTS_DISCONNECTED`).

        Raises:
            AmbiguousName: A name matched more than one thing.
            SelectorNotInGraph: A name matched nothing — including ``arg`` naming no parameter of
                ``callee``, which is a caller error and not a ``False``.
            ValueError: ``depth`` is not a positive ``int``.
            CodeanalyzerExecutionException: :data:`PORTS_DISCONNECTED`.
        """
        check_depth(depth)
        self._require_dataflow()
        root = self.resolve_value(src, within=within)
        target = self.resolve_value(arg, within=callee).ref
        self._require_connected_ports("flows_to_argument")
        return target != root.ref and self._value_reaches(root.ref, [target], depth)

    def _callee_values(self, key: str) -> List[str]:
        """The ids of every value that *enters* the callable ``key`` — in Java, its parameters.

        Composed from the parameter list rather than read off the vertices, for
        :meth:`resolve_value`'s reason: the list round-trips exactly through the Neo4j projection
        while the vertices are not rebuilt by it, so both backends name the same set with no query.
        Verified live that every id composed this way names a real ``:JBodyNode {kind:'formal_in'}``.
        """
        row = self._addressing.by_key[key]
        return [java_body_node_id(row.callable.id, f"@formal_in:{i}") for i, p in enumerate(row.callable.parameters) if p.name]

    @abstractmethod
    def _value_reaches(self, src: str, dsts: Sequence[str], depth: int | None) -> bool:
        """Does the value at ``src`` reach any of ``dsts`` over the SDG?

        The one predicate both flow queries run, which is what makes ``flows_to_argument`` implies
        ``flows_to_call`` a fact about their *targets* rather than an agreement between two walks.
        """

    # -----[ taint: many sources, many sinks, one traversal ]-----
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

        The verb triage needs, and the one nothing else on this surface stands in for.
        :meth:`paths_between` *proves* a flow; a caller who gets ``[]`` back from it cannot tell "no
        flow exists" from "the flow left the resolved graph". ``taint()`` runs m sources against n
        sinks in one traversal and reports that distinction **per pair**: the witnesses in
        :attr:`~cldk.analysis.commons.results.FlowPaths.paths`, the pairs an absence claim can be
        built on in :attr:`~cldk.analysis.commons.results.TaintResult.exhausted`, and everything
        else explained in :attr:`~cldk.analysis.commons.results.TaintResult.unresolved`.

        **Sources, sinks and sanitizers are the caller's to supply.** This SDK ships no framework
        catalogue and derives no default set: a per-language vocabulary of taint sources is policy
        that rots, and this accessor is the mechanism.

        **A sanitizer is two mechanisms wearing one word**, told apart by shape. A bare ``str`` cuts
        a *callable* on the path -- what a transforming sanitizer is, since it sits on the data path
        and is naturally named as the method it is. The name is resolved with
        :meth:`resolve_callable`, so it is the method *this application* declares around
        ``StringEscapeUtils.escapeHtml4`` or a parameterised ``PreparedStatement`` bind, not the
        library call that method delegates to. A
        ``(name, within)`` pair cuts a *variable* inside that callable, which is the only thing that
        severs a *validating* guard, because a guard never appears on the data path at all. Both
        cuts are applied **inside** the search rather than to the rows it returns, so what comes back
        is the shortest *unsanitized* route: filtering afterwards would report nothing for a source
        whose ten shortest paths are sanitized and whose eleventh is not -- a false refutation, the
        one output this accessor must never produce, because in triage it closes a live alert.

        **The order of the checks is part of the contract**, cheapest-to-be-wrong-about first: the
        bounds before any name, the names before any sanitizer, the sanitizers before the walk. A
        caller with a typo in ``depth`` hears about ``depth`` rather than about a name, and pays for
        no round trip to learn it.

        **Two deliberate divergences from :meth:`paths_between`.** A pair whose source and sink are
        the *same position* is skipped with a diagnostic rather than raised -- ``paths_between``
        raises there (:func:`~cldk.analysis.commons.bounds.check_distinct_endpoints`), and raising
        would discard a forty-pair batch over one degenerate pair that a caller assembling sources
        programmatically produces by accident. And an explicit ``depth`` yields **no** ``exhausted``
        pair, ever: a pair with no path within five hops is not refuted, it is unmeasured, and
        conflating the two is the bounded-boolean error.

        **Refused on a disconnected port lattice**, as the other four forward value accessors are
        (:data:`PORTS_DISCONNECTED`), and *after* the arguments and the names are judged: a caller
        learns about their own typo before they learn about a gap in the analysis. The gate is asked
        of the data -- whether this application's ``formal_in`` vertices have any outgoing SDG edge
        at all -- and never of the analyzer's version, so output that connects the two layers makes
        this answer with no change here.

        **The level gate is asked here**, which is Java's one divergence from the other two languages:
        :meth:`_require_dataflow` exists on *this* contract -- a no-op on the graph backend, since
        ``--emit neo4j`` is always full depth, and the real check on the in-memory one -- so asking it
        costs nothing and buys the message that names both levels. Without it a local analysis below
        the dependence level resolved its names, reached the port-lattice gate and was told
        :data:`PORTS_DISCONNECTED`: a true sentence about what the *analyzer emitted*, pointing the
        caller at codeanalyzer-java#227, when the remedy is
        ``analysis_level='system_dependency_graph'``. The Python and TypeScript ABCs carry no such
        method, and there the gate opens each :meth:`_taint_walk` instead.

        Args:
            sources: The values taint enters at, each ``(name, within)`` -- the addressing
                :meth:`resolve_value` and :meth:`paths_between` already use.
            sinks: The values it must not reach, addressed the same way.
            sanitizers: Bare names cut callables; ``(name, within)`` pairs cut variables (above).
            depth: Most hops a path may take; ``None`` (the default) for no bound, because a bound
                turns a refutation into an artefact of the budget -- and ``exhausted`` is empty
                whenever it is set.
            max_paths: Most witnesses **per pair**, not per call: with one sink and forty sources a
                flat cap lets one prolific pair starve the other thirty-nine, and in triage the
                per-source witness is the answer. A pair is a pair of *resolved positions*, so two
                selectors naming the same one are one pair and neither double the witnesses nor the
                cap.

        Returns:
            A :class:`~cldk.analysis.commons.results.TaintResult`: ``paths`` are the witnesses,
            ``exhausted`` the pairs searched to exhaustion with a clean ledger, ``roots`` and
            ``resolved`` what every name matched, ``unresolved`` the ledger, and ``complete`` is the
            whole batch's flag: ``True`` only when nothing was truncated **and** the ledger is empty,
            so one skipped or blocked pair makes it ``False`` however cleanly the rest answered --
            and where nothing was truncated, a bigger ``max_paths`` returns that same ``False``. A
            pair is named in
            ``exhausted`` by the two strings the caller passed, so two sources sharing a name in
            different callables read as one pair there -- ``roots`` is what tells them apart.

        Raises:
            AmbiguousName: A name, or a sanitizer's ``within``, matched more than one thing.
            CodeanalyzerExecutionException: :data:`PORTS_DISCONNECTED` -- this analysis's port lattice
                carries no dependence edge, so every pair would come back refuted for a reason that
                has nothing to do with the program.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``, where there is no dependence edge to
                walk at all -- reported as the level rather than as the port lattice, which is a
                different fact.
            SelectorNotInGraph: A name matched nothing, or a sanitizer's shape disagrees with what it
                resolves to.
            TypeError: ``sources`` or ``sinks`` is a bare string, which would unpack into a pair.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, ``sources`` or
                ``sinks`` is empty (refused, not answered ``[]``), or a sanitizer names a blank
                variable.
        """
        check_depth(depth)
        check_max_paths(max_paths)
        reject_bare_string("sources", sources)
        reject_bare_string("sinks", sinks)
        if not sources:
            raise ValueError("sources= names nothing to taint from; pass at least one (name, within) pair")
        if not sinks:
            raise ValueError("sinks= names nothing to taint to; pass at least one (name, within) pair")
        srcs = [self.resolve_value(name, within=within) for name, within in sources]
        dsts = [self.resolve_value(name, within=within) for name, within in sinks]
        cuts, cut_callables = resolve_sanitizers(sanitizers, resolve_callable=self.resolve_callable, edge_vars_in=self._edge_vars_in)
        self._require_dataflow()
        self._require_connected_ports("taint")
        rows, blocked = self._taint_walk(srcs, dsts, cuts=cuts, cut_callables=cut_callables, depth=depth, max_paths=max_paths)
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
        ``max_paths + 1`` **per pair** -- the extra row is what lets :meth:`taint` report truncation
        without a second counting traversal, and the per-pair cap is why one prolific pair cannot
        starve the rest. Grouping and trimming are :meth:`taint`'s, so a walk returns what it found.

        The second element is the frontier ledger, **keyed by the ``(source ref, sink ref)`` pair it
        implicates**. The key is the association, not the message: ``exhausted`` is decided from
        these keys, and recovering a pair by parsing prose back out of a ``Diagnostic`` would
        resurrect exactly the derivation that field is stored to avoid.

        **An unresolved dispatch is a property of a callable frontier, not of a pair**, and this
        mapping has no key meaning "every pair" -- so a walk that meets one files the same diagnostic
        under *each* ``(source ref, sink ref)`` key it affects, not under one of them and not under a
        key of its own devising. Filing under one arm is not equivalent and the difference is not
        laxity: :meth:`taint` reads every key it is handed, so nothing is dropped either way, but a
        key no requested pair claims costs **every** pair its ``exhausted`` certification, because
        nothing on the receiving side can attribute a stray key to a pair. Filing per affected pair
        is what keeps the verdict as precise as the walk's own knowledge; the signature cannot say
        so, which is why it is said here.

        **The level gate is not this method's**, unlike Python's and TypeScript's walks: Java has
        :meth:`_require_dataflow` on the ABC, so :meth:`taint` asks it before the walk is ever
        entered and an implementation that asked again would only be answering a question already
        answered. Nor is the port-lattice gate: :meth:`taint` opens
        :meth:`_require_connected_ports` too, in the same place the five sibling flow accessors do.

        ``@abstractmethod`` now that every backend has one (Ruling G): it shipped as a concrete stub
        so a backend without an implementation was refused when it was *called* rather than when it
        was constructed, and the last implementation closed that window.
        """

    @abstractmethod
    def _edge_vars_in(self, callable_id: str) -> Collection[str]:
        """The variable names carried by SDG edges scoped to this callable -- the domain a variable
        sanitizer is checked against.

        Not :meth:`resolve_value`, which addresses ``formal_in`` port vertices only: most real edge
        variables are locals (``cleaned``, ``answer``, ``result``), so validating a sanitizer through
        the resolver would refuse a legitimate one for not being a parameter. One ``DISTINCT r.var``
        query on the graph side, the adjacency already built on the local side.

        ``@abstractmethod`` for :meth:`_taint_walk`'s reason, and since the same commit.
        """

    # -----[ the two facts a backend supplies about its own analysis ]-----
    def _require_dataflow(self) -> None:
        """Refuse when this analysis was built below the pass that computes cfg/cdg/ddg.

        A no-op here and overridden by the in-memory backend, because ``--emit neo4j`` always runs
        at full depth: the graph backend has no shallow mode to guard against, and giving it an
        override that can never fire would be a second thing to keep in step.
        """

    def _require_explicit(self, key: str, tail: str) -> None:
        """Refuse an implicit callable (J-6), in the same words on both backends.

        The guard is here rather than in :meth:`resolve_callable` because J-6 draws the line
        between the two: an implicit constructor **resolves** -- it is a call-graph endpoint, and
        ``callers_of`` / ``reaches`` / ``backward_cone`` answer about it honestly -- and only the
        accessors that would have to read a span, a body or a parameter list refuse. Every one of
        those reaches this method.
        """
        row = self._addressing.by_key.get(key) or self._addressing.by_id.get(key)
        if row is not None and row.callable.is_implicit:
            raise CodeanalyzerUsageException(IMPLICIT_CALLABLE.format(key=key, tail=tail))

    def _require_connected_ports(self, accessor: str) -> None:
        """Refuse the forward value accessors while the port lattice carries no dependence
        edge (:data:`PORTS_DISCONNECTED`). Both backends raise the same type with the same message,
        which names the accessor and the application and no ``can://`` id (E6)."""
        if not self._ports_carry_dependence:
            raise CodeanalyzerExecutionException(PORTS_DISCONNECTED.format(accessor=accessor, app=self._application_name))

    @property
    @abstractmethod
    def _ports_carry_dependence(self) -> bool:
        """Whether **this** application's ``formal_in`` vertices have any outgoing SDG edge.

        Asked of the data, once per backend, rather than hard-coded from the analyzer version: the
        refusal above is a statement about what was emitted, and a graph or a payload that connects
        the two layers must make these accessors work with no change here — which is exactly what
        happened when codeanalyzer-java 3.0.3 landed codeanalyzer-java#227.
        """

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_compilation_units(self) -> List[JCompilationUnit]:
        """All compilation units."""

    @abstractmethod
    def get_java_file(self, qualified_class_name: str) -> str | None:
        """The (repo-relative) file path declaring a class. ``None`` if the class is not found."""

    @abstractmethod
    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """The compilation unit for a file path."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The call graph serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        """Callers of a method."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        """Callees of a method."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Call-graph edges out of a class (or one of its methods)."""

    @abstractmethod
    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Call-graph edges out of a class, computed from the symbol table's call sites only."""

    # -----[ classes / methods / fields ]-----
    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, JType]:
        """Classes that extend/implement the given class."""

    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[JType]:
        """The classes declared inside a class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base classes a class extends."""

    @abstractmethod
    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """The interfaces a class implements."""

    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        """All methods grouped by their owning class qualified name."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """The constructors of a class."""

    # -----[ entry points ]-----
    @abstractmethod
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Methods identified as application entry points."""

    @abstractmethod
    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        """Classes identified as application entry points."""

    # -----[ CRUD operations — J-4: raise CRUD_UNAVAILABLE on schema v2 ]-----
    @abstractmethod
    def get_all_crud_operations(self) -> List[CRUDRow]:
        """All CRUD operations across the application."""

    @abstractmethod
    def get_all_create_operations(self) -> List[CRUDRow]:
        """All create operations."""

    @abstractmethod
    def get_all_read_operations(self) -> List[CRUDRow]:
        """All read operations."""

    @abstractmethod
    def get_all_update_operations(self) -> List[CRUDRow]:
        """All update operations."""

    @abstractmethod
    def get_all_delete_operations(self) -> List[CRUDRow]:
        """All delete operations."""

    # -----[ comments / docstrings ]-----
    # J-16, the one rule, split by whether a *smaller* answer is still an answer under the name.
    # A backend that keeps only per-declaration javadoc (the Neo4j projection) can answer the three
    # **declaration-keyed** accessors with a javadoc-only subset — narrower than "every comment in
    # this class", but a real answer about a real declaration. It cannot answer the two
    # **file-keyed** ones at all: it holds nothing file-level, so every answer would be an empty
    # list reading as "this file has no comments" (D7). Those two therefore raise.
    @abstractmethod
    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """All comments across the application, keyed by file.

        Raises:
            CodeanalyzerExecutionException: If the backend's source carries no file-level comments
                at all (the Neo4j projection does not), naming what is missing and what to read
                instead. Returning the per-declaration javadoc under this name would be a silent
                partial (J-16).
        """

    @abstractmethod
    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """The comments in a file.

        Raises:
            CodeanalyzerExecutionException: As :meth:`get_all_comments` does, and for the same
                reason (J-16).
        """

    @abstractmethod
    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """The class declaration's **own** comment. Returns an empty list if the class is not found.

        Not the comments inside the class body: on both backends this is the type's own comment
        list — the comment immediately above ``class Foo``. A method's is on
        :meth:`get_comments_in_a_method`; an inline comment in a body is on neither, and reaches
        the SDK only through :meth:`get_comment_in_file`.

        A backend whose source keeps only per-declaration javadoc narrows further, to **just the
        javadoc** — a real answer rather than a refusal (J-16).
        :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` is such a backend.
        """

    @abstractmethod
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """The method declaration's **own** comment (at most one). Returns an empty list if the
        method is not found.

        Not every comment inside the body — see :meth:`get_comments_in_a_class`.

        Narrows to javadoc only on a javadoc-only backend, exactly as
        :meth:`get_comments_in_a_class` does (J-16).
        """

    @abstractmethod
    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        """All Javadoc comments across the application, keyed by file.

        Which javadoc depends on what the backend's source keeps: the in-memory backend reports
        each compilation unit's own comment list (holding the *file-level* javadoc), the Neo4j
        backend the javadoc of each *declaration* in the file. Both are javadoc keyed by file, and
        they are different sets for the same file (J-16).
        """

    @abstractmethod
    def remove_all_comments(self, src_code: str) -> str:
        """Strip all comments from the given source code."""
