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

"""The Python analysis backend contract.

:class:`PythonAnalysis` is a thin façade that delegates every query to a *backend*. Today the only
backend is :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer` (in-memory pydantic /
NetworkX over ``analysis.json``); this ABC formalizes the surface the façade depends on so an
alternative backend (e.g. a forthcoming Neo4j/Cypher backend, mirroring the TypeScript
:class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`) can be dropped in and selected without
touching the façade.

The contract is enforced by the type system and at instantiation time rather than matching only
by convention. Backend-specific lifecycle (caches, drivers) is intentionally not part of it.
"""

from __future__ import annotations

import base64
import json
import os
import posixpath
from abc import abstractmethod
from bisect import bisect_right
from typing import Callable, Dict, Iterable, List, NamedTuple, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.results import EdgePage, EntrypointCoverage, LocateResult, Slice, SliceNode
from cldk.utils.exceptions import SelectorNotInGraph
from cldk.models.python import (
    CdgEdge,
    CfgEdge,
    DdgEdge,
    PyApplication,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
    PyExternalSymbol,
    PyModule,
)


def resolve_module_key(path: str, keys: Iterable[str]) -> str:
    """The symbol-table / graph ``file_key`` naming ``path``, or ``path`` unchanged if none does.

    A caller of :meth:`PythonAnalysisBackend.locate` hands over whatever its scanner printed —
    ``./src/app.py``, ``src/../src/app.py``, or an absolute path from the machine the scan ran on —
    while both backends are keyed by the project-relative path the analyzer saw. Exact key first,
    then the normalised form, then the longest known key the normalised path *ends on a segment
    boundary* of (which is what an absolute path is). Returning ``path`` unchanged when nothing
    matches is deliberate: the caller then gets ``file_not_in_graph`` naming the path it asked
    about, not a silently substituted neighbour.
    """
    keys = list(keys)
    if path in keys:
        return path
    norm = posixpath.normpath(str(path).replace(os.sep, "/"))
    if norm in keys:
        return norm
    suffix_matches = [k for k in keys if norm.endswith("/" + k)]
    return max(suffix_matches, key=len) if suffix_matches else path


def body_key_column(key: str) -> int:
    """The start column encoded in a body node's local key (``"21:12"`` -> ``12``), or ``-1``.

    Both backends need one tie-break for two body nodes that span the *same* line — ``if x: return x``
    emits an ``if`` and a ``return`` each spanning one line — and line numbers are the only positional
    data the Neo4j projection carries, so the span cannot break it. The local *key* can: it is
    ``<line>:<col>`` (sometimes suffixed, as in ``"22:8/actual_in:0"``), it exists on both sides
    (locally the ``body`` dict key, over Neo4j the trailing segment of ``<callable id>@<key>``), and a
    larger column is the more deeply nested statement. Comparing the keys as *strings* instead would
    order ``"29:10"`` before ``"29:4"`` and pick the outer node, so the column is parsed as an int.

    ``-1`` for a key with no column (the synthetic ``@entry`` / ``@exit`` vertices) — they carry no
    span, so they are filtered out before ranking and never reach this.
    """
    _, _, col = key.split("/", 1)[0].partition(":")
    return int(col) if col.isdigit() else -1


def reject_bare_string(kind: str, values: object) -> None:
    """Refuse a single string where a sequence of names is required.

    ``paths='pkg/mod.py'`` is not a type error to Python — a string *is* a sequence, of ten
    characters — so it used to reach :func:`check_selector` as ten requested paths and come back as
    ``10 of 10 paths not in graph: 'p', 'k', 'g', '/', …``. The mistake is the likely one because
    the sibling keyword ``module=`` genuinely is single-valued, so both spellings look plausible.

    Raises:
        TypeError: ``values`` is a ``str``.
    """
    if isinstance(values, str):
        raise TypeError(f"{kind}= takes a sequence of names, not a string; pass [{values!r}] to select just that one")


def check_selector(kind: str, requested: Sequence[str], missing: Sequence[str]) -> None:
    """The one place a scoping keyword's *selection* is judged, for both backends.

    Every scoped accessor — ``get_symbol_table(paths=)``, ``get_classes(module=)``,
    ``get_call_graph(roots=)`` — narrows a whole-application enumeration to what the caller named.
    Two ways of naming nothing must not both come back as an empty result:

    * **an empty sequence** (``paths=[]``, ``roots=[]``) selected nothing while missing nothing. It
      is a caller bug — the argument to omit is the argument that means "everything" — and it
      raises the same :class:`ValueError` ``depth=`` without ``roots=`` already does.
    * **values that match nothing** are the ambiguous empty the parent spec's D7 calls a defect:
      a mistyped path and a module that genuinely declares no classes were the same ``{}``. They
      raise :class:`~cldk.utils.exceptions.SelectorNotInGraph`, which names them and stops. It
      offers no near-miss candidates on purpose — leg 1.5's E8 puts typo-tolerant matching out of
      scope "not in the resolver, not in the error path".

    A **partial** miss raises too. Returning the values that did match would make a result whose
    size the caller cannot check against what it asked for, which is the same silence one step
    quieter.

    Args:
        kind: The keyword's name, as it appears in the caller's own call — ``"paths"``,
            ``"module"`` or ``"roots"``.
        requested: Everything the keyword named, in the caller's spelling.
        missing: The subset of ``requested`` that matched nothing. Callers with no membership
            information to bring (``call_graph_scope``, which has not seen the graph yet) pass an
            empty sequence and get only the empty-selection check.

    Raises:
        ValueError: ``requested`` is empty.
        SelectorNotInGraph: ``missing`` is non-empty.
    """
    if not requested:
        raise ValueError(f"{kind}= selected nothing; omit it to enumerate the whole application")
    if missing:
        raise SelectorNotInGraph(kind, list(missing), len(requested))


def scope_paths(paths: Sequence[str] | None, keys: Iterable[str], kind: str = "paths") -> List[str] | None:
    """Resolve requested module paths to symbol-table keys, or ``None`` for "the whole application".

    Both backends route their ``paths=`` / ``module=`` keywords through here, so the lenient
    resolution (:func:`resolve_module_key` — an absolute path or one with native separators finds
    its module) and the strictness (:func:`check_selector` — a path naming no module raises) cannot
    drift apart between them.

    Args:
        paths: What the caller named, or ``None`` for the unscoped call.
        keys: The symbol-table keys that exist — ``symbol_table.keys()`` locally, the
            application's module ``file_key``s over Neo4j.
        kind: The keyword's name for the error message; ``"module"`` for ``get_classes``, whose
            single-valued keyword routes through here as a one-element sequence.

    **Resolution is many-to-one, and the result is de-duplicated.** Leniency is the whole point of
    :func:`resolve_module_key` — ``"pkg/a.py"`` and ``"/abs/pkg/a.py"`` are two spellings a scanner
    may plausibly hand over for the *same* module — so two requested paths legitimately collapse to
    one key and the caller gets one entry back. Raising on the collapse would punish the very
    caller the leniency exists for; de-duplicating explicitly is what keeps the returned list from
    naming the same module twice and asking both backends to fetch it twice.

    Raises:
        TypeError: ``paths`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``paths`` is an empty sequence.
        SelectorNotInGraph: a path names no module in this application.
    """
    reject_bare_string(kind, paths)
    if paths is None:
        return None
    known = list(keys)
    resolved = [resolve_module_key(p, known) for p in paths]
    check_selector(kind, list(paths), [p for p, r in zip(paths, resolved) if r not in known])
    return list(dict.fromkeys(resolved))


def call_graph_scope(roots: Sequence[str] | None, depth: int | None) -> List[str] | None:
    """Normalise :meth:`PythonAnalysisBackend.get_call_graph`'s scoping keywords.

    Returns the roots as a list, or ``None`` for "the whole application" — the unscoped call,
    which must keep behaving exactly as it did before the keywords existed.

    Both backends route through this so the two cannot drift apart on what a keyword combination
    means (the failure mode Fix 1 of leg 1.5 had to go back and repair on the child-fetch paths).
    Whether each root *exists* is checked later, by whichever backend has the graph in hand, but
    through the same :func:`check_selector` — see :func:`bounded_subgraph`.

    Raises:
        TypeError: ``roots`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``depth`` that is not a positive ``int``, ``depth`` without ``roots``, or an
            empty ``roots``. A hop budget with no origin to count from has no meaning, and quietly
            returning all 364,752 edges would be the worst of the available answers — the caller
            asked for a bounded graph and would be handed an unbounded one with no signal.
            ``depth`` is type-checked rather than merely range-checked because the two ways of
            getting it wrong are silent otherwise: ``depth="2"`` raised ``TypeError`` from the
            comparison, and ``depth=2.5`` was accepted and truncated to 2 by the Cypher/ego-graph
            radius. ``bool`` is rejected for the same reason — ``depth=True`` is ``1`` by accident.
    """
    check_depth(depth)
    reject_bare_string("roots", roots)
    if roots is None:
        if depth is not None:
            raise ValueError("depth= requires roots=; a hop budget needs an origin to count from")
        return None
    check_selector("roots", list(roots), ())
    return list(roots)


def check_depth(depth: int | None) -> int | None:
    """``depth`` is a hop budget: ``None`` for unbounded, otherwise an ``int`` of at least 1.

    Type-checked and not merely range-checked, because the two ways of getting it wrong are silent
    otherwise: ``depth="2"`` raised ``TypeError`` from somewhere further in, and ``depth=2.5`` was
    accepted and truncated to 2 by the Cypher/ego-graph radius. ``bool`` is rejected for the same
    reason — ``depth=True`` is ``1`` by accident.

    One function, so ``get_call_graph``, the slices and the reachability accessors cannot come to
    disagree about what a hop budget is.
    """
    if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 1):
        raise ValueError(f"depth must be an int >= 1, got {depth!r}")
    return depth


def bounded_subgraph(graph: nx.DiGraph, roots: List[str], depth: int | None, declared: Iterable[str]) -> nx.DiGraph:
    """The sub-call-graph reachable from ``roots``, within ``depth`` hops when given.

    **Induced**, not path-only: every edge between two reached nodes is kept, including one
    pointing back towards a root. A path-only answer would let ``graph.predecessors(n)`` lie about
    a node the caller can see, which is a worse defect than the extra edges are a cost. The Neo4j
    backend's Cypher is written to produce the same induced shape rather than the cheaper
    edges-along-the-path shape, for exactly this reason.

    **The domain a root is judged against — stated here because both backends must judge against
    the same one — is the callable inventory, not this graph.** ``graph`` is built from call
    *edges* alone, so a callable that neither calls nor is called by anything is not a node in it:
    444 of the live odoo application's 15,549 in-scope callables, 2.9%. Checking membership of
    ``graph`` therefore raised for a callable that plainly exists, while the Neo4j backend — whose
    Cypher matches a root by node *label*, not by edge participation — returned the one-node graph
    it is. ``declared`` closes that gap: it carries every callable the application declares, and a
    root is valid when it is **in the inventory or is a node of the graph**. The second disjunct is
    not redundant — an ``@external`` ghost is a legitimate root, is a graph node, and is not a
    declared callable — and the union is exactly what the Neo4j root match accepts (a
    ``:PyCallable`` of this application, or a ``:PyExternal``).

    A root outside that domain raises (:func:`check_selector`) rather than contributing nothing:
    "no such callable" and "a callable that calls nothing" are different answers, and before this
    they were the same empty graph.

    The returned graph stays **edge-induced**. An isolated root is added back as a lone node —
    which is the answer, and the one Neo4j gives — but nothing else the inventory knows about is
    seeded into it. Seeding all declared callables would make the unbounded local graph disagree
    with Neo4j's node-for-node, trading one parity defect for a larger one.
    """
    inventory = set(declared)
    check_selector("roots", roots, [r for r in roots if r not in graph and r not in inventory])
    nodes: set = set()
    isolated: set = set()
    for root in roots:
        if root not in graph:
            isolated.add(root)  # declared, but in no call edge: its own one-node graph
        elif depth is None:
            nodes |= nx.descendants(graph, root) | {root}
        else:
            nodes |= set(nx.ego_graph(graph, root, radius=depth).nodes)
    sub = graph.subgraph(nodes).copy()
    sub.add_nodes_from(isolated)
    return sub


# ----------------------------------------------------------------------------------------------
# Paging the per-callable graphs (E5).
#
# THE CANONICAL ORDER, defined once here because it is the only thing that makes a page mean the
# same thing on both backends. Neo4j returns rows in no order unless told to, and the local
# backend returns the analyzer's emission order; without one stated sort, page two on Neo4j is a
# different set of edges from page two locally. Each backend uses these functions -- the local one
# sorts and slices with them directly, the Neo4j one writes the same components into its ORDER BY
# and rebuilds the cursor from them -- so a change here moves both at once.
#
# The order is over the edge's OWN fields, in the order a reader would name them: source, then
# target, then whatever else the edge carries. Nothing positional and nothing backend-specific
# (no relationship element id, no row number), because a key one backend cannot compute is not a
# shared order.
#
# TOTALITY. A keyset cursor resumes strictly *after* a key, so a repeated key would drop its twin.
# The full field tuple is unique on real data: measured across odoo-slim-19's 5,134,655 PY_DDG,
# 247,906 PY_CFG_NEXT and 139,065 PY_CDG edges, zero (src, dst, ...) tuples repeat -- and for CFG
# the endpoints alone are *not* enough (13,310 node pairs carry two edges of different ``kind``),
# which is why ``kind`` is in the key. On the graph side the emitter MERGEs these relationships on
# exactly these properties, so uniqueness is structural there rather than incidental. Two edges
# equal in every field would be equal as values -- the models carry nothing else -- so their
# relative order is unobservable, and the page boundary is the same either way.
#
# ``or ""`` / ``or []`` is not cosmetic: ``DdgEdge.var`` is ``Optional[str]``, and a ``None`` in a
# sort key raises in Python and silently drops the row in Cypher (``null > x`` is null). The
# Cypher spells the same normalisation with ``coalesce``.


#: Edges per page when the caller does not say. 10,000 is where the measured distribution
#: splits: on odoo-slim-19, 15,520 of the 15,549 callables have fewer than 10,000 DDG edges, so
#: this default answers 99.8% of callables completely in one page and no caller of a normal
#: callable ever writes a loop -- while the 29 that are larger, up to 1,386,918 edges, are held to
#: a response a caller can actually hold. CFG and CDG max out at 402 and 314 edges on the same
#: application, so for them it is never reached.
DEFAULT_PAGE_SIZE = 10_000


def cfg_sort_key(edge: CfgEdge) -> Tuple:
    """The canonical order for :meth:`PythonAnalysisBackend.get_cfg`: source, target, kind."""
    return (edge.src, edge.dst, edge.kind or "")


def cdg_sort_key(edge: CdgEdge) -> Tuple:
    """The canonical order for :meth:`PythonAnalysisBackend.get_cdg`: source, target. A control
    dependence carries nothing else to break a tie on, and needs nothing else: the pair is unique.
    """
    return (edge.src, edge.dst)


def ddg_sort_key(edge: DdgEdge) -> Tuple:
    """The canonical order for :meth:`PythonAnalysisBackend.get_ddg`: source, target, variable,
    provenance.

    ``prov`` is a list, and it is in the key rather than dropped from it because the same
    (source, target, variable) triple with two different provenances is two edges a caller can
    tell apart -- so an order that ignored ``prov`` would not be total over what the caller sees.
    Python and Cypher order lists the same way (element-wise, shorter first on a prefix), verified
    on the live graph rather than assumed: see ``test_neo4j_orders_a_page_exactly_as_python_would``.
    """
    return (edge.src, edge.dst, edge.var or "", list(edge.prov or []))


class EdgeOrder(NamedTuple):
    """One edge kind's canonical order, in both spellings that have to agree.

    The Python sort key and the Cypher expressions are the same components said twice, in two
    languages, and the whole point of the order is that the two never disagree — so they are
    written down once, together, and each backend takes the half it can run. ``len(exprs)`` is
    also the order's arity, which is how a cursor from one accessor is refused by another
    (:func:`decode_cursor`): the three arities are 3, 2 and 4.

    ``coalesce`` in the expressions is ``or ""`` / ``or []`` in the key: ``DdgEdge.var`` is
    optional, and a ``None`` in a sort key raises in Python and silently drops the row in Cypher.
    """

    key: Callable[[object], Tuple]
    exprs: Tuple[str, ...]


#: The three orders. ``src``/``dst``/``kind``/``var``/``prov`` are the aliases the backends'
#: ``WITH`` clause projects, so an expression here is valid Cypher exactly where it is used.
CFG_ORDER = EdgeOrder(cfg_sort_key, ("src", "dst", "coalesce(kind,'')"))
CDG_ORDER = EdgeOrder(cdg_sort_key, ("src", "dst"))
DDG_ORDER = EdgeOrder(ddg_sort_key, ("src", "dst", "coalesce(var,'')", "coalesce(prov,[])"))


def encode_cursor(scope: str, key: Tuple) -> str:
    """An opaque, round-trippable spelling of a sort key, stamped with the callable it came from.

    Opaque on purpose: the caller passes it back and never reads it, so the components of the
    order stay an implementation detail rather than joining the caller's vocabulary. Base64 of
    JSON, because the key holds strings and a list of strings, and both survive that unchanged.

    ``scope`` is the resolved callable signature, carried so that :func:`decode_cursor` can refuse
    a cursor minted for a different callable. Without it, an agent looping over callables and
    reusing the wrong ``next_cursor`` would get a plausible page of the *right* callable's edges
    resumed from a position in the *wrong* one — silently, since body-node ids sort by callable id
    and the filter would simply skip everything or nothing.
    """
    return base64.urlsafe_b64encode(json.dumps([scope, list(key)]).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, scope: str, arity: int) -> Tuple:
    """Inverse of :func:`encode_cursor`, checked against the caller it is being used for.

    Three ways a cursor can be wrong, all of them raising rather than being read as "start from
    the beginning" — which would silently hand back page one when page nine was asked for:
    it does not decode; it was minted for another callable; or it has the wrong number of
    components, which is what a cursor from a *different accessor* looks like (the three orders
    have arities 3, 2 and 4, so no cursor is silently valid for the wrong graph).
    """
    try:
        got_scope, key = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 -- any decode failure is the same caller error
        raise ValueError(f"not a cursor from a previous page: {cursor!r}") from exc
    if got_scope != scope:
        raise ValueError(f"this cursor is from a page of {got_scope!r}, not {scope!r}")
    if len(key) != arity:
        raise ValueError(f"cursor has {len(key)} components, this accessor's order has {arity}: {cursor!r}")
    return tuple(key)


def check_page_size(page_size: int) -> int:
    """``page_size`` must ask for at least one edge.

    Zero is refused rather than treated as "no limit": a page of nothing whose ``next_cursor`` can
    never advance is an infinite loop dressed as an empty answer.
    """
    if page_size < 1:
        raise ValueError(f"page_size must be at least 1, got {page_size}")
    return page_size


def keyset_where(exprs: Sequence[str]) -> str:
    """The Cypher for "strictly after the cursor", written out because Cypher has no tuple
    comparison: ``(a, b) > ($c0, $c1)`` has to become
    ``a > $c0 OR (a = $c0 AND (b > $c1))``.

    Keyset rather than ``SKIP``: measured on ``Website.configurator_apply`` (1,386,918 DDG edges,
    10,000 per page, query alone), ``SKIP`` costs 2.6s for the first page, 9.0s for the middle one
    and 4.3s for the last -- it re-sorts a prefix that grows with the offset -- while this filter
    is flat at 3.1s / 2.9s / 2.4s. The offset form is not wrong, it just gets worse the further in
    the caller reads, which is the one direction pagination exists to make cheap.
    """
    clause = ""
    for i in reversed(range(len(exprs))):
        expr, param = exprs[i], f"$c{i}"
        clause = f"{expr} > {param}" + (f" OR ({expr} = {param} AND ({clause}))" if clause else "")
    return clause


def cursor_params(cursor: str, scope: str, arity: int) -> Dict[str, object]:
    """The ``$c0…$cN`` bindings :func:`keyset_where` reads, from an opaque cursor."""
    return {f"c{i}": v for i, v in enumerate(decode_cursor(cursor, scope, arity))}


def edge_page(model, scope: str, edges: List, order: EdgeOrder, page_size: int, cursor: str | None) -> EdgePage:
    """One page of an edge set already held in memory.

    The local backend has every edge in hand, so it sorts by ``key`` and slices. The cursor is
    resolved by binary search over the sorted keys -- ``bisect_right``, i.e. the first edge
    strictly after it -- so it means exactly what :func:`keyset_where` makes it mean on the graph,
    rather than an independently-invented position that happens to line up.
    """
    check_page_size(page_size)
    key = order.key
    rows = sorted(edges, key=key)
    start = bisect_right([key(e) for e in rows], decode_cursor(cursor, scope, len(order.exprs))) if cursor is not None else 0
    window = rows[start : start + page_size]
    more = start + len(window) < len(rows)
    return EdgePage[model](edges=window, total=len(rows), next_cursor=encode_cursor(scope, key(window[-1])) if more and window else None)


# ----------------------------------------------------------------------------------------------
# Slicing and reachability (E2, E3, E5).
#
# THE FIVE RELATIONSHIP TYPES A SLICE FOLLOWS, verified against codeanalyzer's own
# ``neo4j/schema.py`` REL_TYPES and against ``CALL db.relationshipTypes()`` on odoo-slim-19 rather
# than copied from a plan -- the names in this leg's plan have been wrong before (PY_CFG_NEXT is
# not PY_CFG). All five exist, with these edge counts on that application:
#
#   PY_DDG        5,134,655   data dependence, within a callable  (var, prov)
#   PY_CDG          139,065   control dependence, within a callable
#   PY_PARAM_IN     229,035   actual_in -> formal_in     : an argument entering a callee
#   PY_PARAM_OUT    133,267   formal_out -> actual_out   : a value coming back to the caller
#   PY_SUMMARY      453,398   actual_in -> actual_out    : a callee's pass-through, at the call site
#
# All five point WITH the flow -- verified on the live graph, where every PY_PARAM_IN runs
# actual_in -> formal_in and every PY_PARAM_OUT runs formal_out -> actual_out, with no exceptions
# in 362,302 edges. So a forward slice follows them and a backward slice follows them reversed;
# there is no per-type direction table to keep straight, which is why they can share one match.
#
# PY_CFG_NEXT is deliberately NOT here. Control *flow* says what runs next; a slice is about what
# a value or a decision depends on, and following successor edges would pull in every later
# statement whether or not it depends on anything -- the "returns the whole callable" bug that a
# non-emptiness assertion cannot catch.
SDG_RELS = ("PY_DDG", "PY_CDG", "PY_PARAM_IN", "PY_PARAM_OUT", "PY_SUMMARY")

#: The Cypher spelling of :data:`SDG_RELS` for a relationship-type disjunction.
SDG_REL_PATTERN = "|".join(SDG_RELS)

#: Nodes per slice when the caller does not say. The same 10,000 as :data:`DEFAULT_PAGE_SIZE`, and
#: for a different reason: there, it is where 99.8% of callables fit in one page; here, nothing
#: fits, because the measured distribution has no middle (see
#: :class:`~cldk.analysis.commons.results.Slice`). 10,000 is the largest result that stays
#: readable, and every slice above it is one a caller should be re-asking with ``depth=``.
DEFAULT_MAX_NODES = 10_000

#: Hops from the seed when the caller does not say. **Finite, and that is the whole point.**
#:
#: The measured distribution has no middle (see :class:`~cldk.analysis.commons.results.Slice`), so
#: an unbounded default hands a connected seed 10,000 arbitrary nodes of a 195,819-node closure --
#: an unprincipled 5%, honestly flagged ``truncated`` and useless either way. A finite default
#: answers a *narrower* question *completely* instead, and ``depth=None`` is how a caller asks for
#: the whole cone.
#:
#: 5 is the largest bound at which no measured slice needs ``max_nodes`` at all. Over 120 random
#: ``formal_in`` seeds with callers on odoo-slim-19, node counts by depth:
#:
#: =========  =====  =======  =======  =======  ========
#: direction  depth   median      p75      max  > 10,000
#: =========  =====  =======  =======  =======  ========
#: backward       3       14       70      846         0
#: backward       5       33      188    1,539         0
#: backward       6       56      324    2,818         0
#: backward       8      464    2,044   16,028         1
#: backward    None  195,786  195,787  198,306        79
#: forward        3       12       34      440         0
#: forward        5       24       63    1,053         0
#: forward        6       35      166   14,260         1
#: forward        8       48      402   37,326         2
#: forward     None       71  440,269  440,645        52
#: =========  =====  =======  =======  =======  ========
#:
#: 3 is informative but thin; 6 is where a forward slice first exceeds the cap and the default
#: would start truncating again. 5 is the last depth that never does, in either direction.
DEFAULT_DEPTH = 5


def check_max_nodes(max_nodes: int) -> int:
    """``max_nodes`` must admit at least one node — the seed, if nothing else.

    Zero is refused rather than read as "no limit": a slice of nothing whose ``total`` says
    195,784 is a result no caller can act on, and "unbounded" is what ``max_nodes=None`` would
    have to mean if it ever meant anything.
    """
    if max_nodes < 1:
        raise ValueError(f"max_nodes must be at least 1, got {max_nodes}")
    return max_nodes


def cone_sinks(resolve: Callable[[str], SliceNode], sinks: Sequence[str]) -> List[SliceNode]:
    """Resolve ``backward_cone``'s sinks, refusing the two ways of naming nothing.

    The same discipline :func:`check_selector` applies to ``roots=`` and ``paths=``: a bare string
    is ten one-character sinks and is refused as a type error, and an empty sequence is refused
    because "everything" is the argument omitted, not the argument emptied — and there is no
    "everything" here to fall back to. Each surviving name goes through ``resolve``, so an
    ambiguous sink raises listing candidates instead of one of them being picked.

    Duplicates are collapsed by resolved signature, not by the string the caller wrote: naming the
    same callable twice, once bare and once qualified, is one sink.
    """
    reject_bare_string("sinks", sinks)
    if not sinks:
        raise ValueError("sinks= names nothing to walk back from; pass at least one callable")
    resolved = {node.callable: node for node in (resolve(s) for s in sinks)}
    return list(resolved.values())


def slice_resolved(roots: List[SliceNode]) -> str:
    """The audit line on a :class:`~cldk.analysis.commons.results.Slice`: what the caller's names
    matched, in the caller's vocabulary.

    Both backends build it here rather than each formatting its own, so a caller comparing two
    results is comparing answers and not two spellings of one.
    """
    return ", ".join(f"{r.callable} {r.kind} {r.name!r}" if r.kind != "callable" else r.callable for r in roots)


class PythonAnalysisBackend(AnalysisBackend[PyApplication, PyModule, PyClass, PyCallable, PyClassAttribute, str]):
    """Abstract base every Python analysis backend implements.

    A backend owns all indexing and query logic for a Python application; the
    :class:`PythonAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.python`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so backends are behaviorally interchangeable.

    The application/symbol-table/call-graph/class/method/field/parameter accessors are inherited
    from :class:`~cldk.analysis.commons.backend.AnalysisBackend`; everything below is Python-specific.
    """

    # -----[ bounded enumeration ]-----
    # These three are declared on the generic AnalysisBackend without keywords; Python widens them
    # with defaulted, keyword-only scoping arguments so whole-application enumeration is the
    # exception a caller asks for rather than the default shape. Every keyword defaults to the
    # pre-existing behaviour, so no existing call site changes and no public signature moves.
    # Redeclared here rather than left to the generic base because a reader of *this* contract
    # would otherwise see the unwidened signature and believe it.
    @abstractmethod
    def get_symbol_table(self, *, paths: Sequence[str] | None = None) -> Dict[str, PyModule]:
        """The symbol table, keyed by file path.

        Args:
            paths: Restrict to these modules, named by symbol-table key (equivalently, the module's
                file path). Resolved leniently through :func:`resolve_module_key`, so an absolute
                path or one with native separators finds its module. A path naming no module in
                the application raises :class:`~cldk.utils.exceptions.SelectorNotInGraph`, and an
                empty sequence raises ``ValueError`` — see :func:`scope_paths`. ``None`` (the
                default) returns every module.
        """

    @abstractmethod
    def get_all_classes(self, *, module: str | None = None) -> Dict[str, PyClass]:
        """Top-level classes, keyed by signature.

        Args:
            module: Restrict to the classes declared by one module, named the same way
                :meth:`get_symbol_table`'s ``paths`` names one — a symbol-table key, not a dotted
                module name, and a key naming no module raises the same way. ``None`` (the default)
                returns the whole application's classes.
        """

    @abstractmethod
    def get_call_graph(self, *, roots: Sequence[str] | None = None, depth: int | None = None) -> nx.DiGraph:
        """The call graph, as a NetworkX ``DiGraph`` keyed by callable signature.

        Args:
            roots: Restrict to the sub-graph reachable from these callables, named by signature
                (or, for an external ghost, by its ``@external`` can-id — the same strings that
                appear as graph nodes). ``None`` (the default) returns the whole application.
            depth: Maximum number of call hops from a root. ``None`` means unbounded.

        The result is the **induced** sub-graph over the reached nodes (see
        :func:`bounded_subgraph`), identically on both backends.

        Raises:
            ValueError: ``depth`` that is not an ``int`` >= 1, ``depth`` given without ``roots``,
                or an empty ``roots`` (see :func:`call_graph_scope`).
            SelectorNotInGraph: a root that is neither a callable this application declares nor a
                node of the call graph. "No such callable" and "a callable that calls nothing" are
                different answers; the second is a graph of one node, including when the callable
                has no call edge at all (see :func:`bounded_subgraph` for the domain both backends
                validate against).
        """

    @abstractmethod
    def get_modules(self) -> List[PyModule]:
        """All modules."""

    @abstractmethod
    def get_python_module(self, file_path: str) -> PyModule | None:
        """The module for a file path."""

    @abstractmethod
    def get_python_file(self, qualified_class_name: str) -> str | None:
        """The file path declaring the given symbol."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""

    # -----[ classes ]-----
    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """The classes declared inside a class."""

    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Classes that extend the given class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends."""

    # -----[ methods / fields ]-----
    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        """All methods grouped by their owning class signature."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """The constructors of a class.

        Note:
            This accessor (and :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_method`
            / :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_all_methods_in_class`)
            does not resolve call-site ``callee_signature`` the way :meth:`get_callsites_for`
            does for the identical call sites: over the Neo4j backend it is always ``None`` here
            (that reconstruction never follows ``PY_RESOLVES_TO``); over the local backend an
            external target keeps Jedi's raw, unaddressable dotted guess instead of the resolved
            ``@external`` can-id. Use :meth:`get_callsites_for` when resolved call sites matter.
        """

    # -----[ bulk / projected accessors ]-----
    # Set-at-a-time, field-projected reads — one round-trip on the Neo4j backend, one symbol-table
    # walk in-process — for callers that enumerate the whole application and would otherwise pay the
    # per-entity reconstruction of get_all_methods_in_application.
    @abstractmethod
    def get_callables_overview(self) -> List[PyCallableOverview]:
        """A lightweight projection of every callable in the application (methods, module-level and
        nested functions), without the full :class:`PyCallable` reconstruction."""

    @abstractmethod
    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Source bodies for the given callable signatures, keyed by signature. Signatures with no
        matching callable are omitted, as are callables whose ``code`` is ``None`` (e.g. synthesized
        callables the analyzer emits with no source text) — every returned value is a real ``str``."""

    @abstractmethod
    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        """Overviews of callables decorated with any of ``markers`` (matched against the decorator
        names)."""

    @abstractmethod
    def get_entrypoints(self) -> List[PyCallableOverview]:
        """Overviews of every *callable* the analyzer marked as an entrypoint (``PyCallable.
        is_entrypoint``) — a route handler, CLI command, or other externally-invoked callable the
        entrypoint-detection pass already found. An empty list means the pass found no entrypoint
        *callables*, the ordinary "no entrypoints in this project" case for this accessor — never a
        stand-in for the mark not existing at all (the graph carries ``is_entrypoint`` as a real
        boolean, never dropped, so it's never ambiguous at the property level).

        Two things this accessor alone cannot tell you, each answered by a sibling instead of by
        widening this one's frozen ``List[PyCallableOverview]`` return:

        * **Class-level entrypoints.** ``PyClass`` carries its own ``is_entrypoint``/``entrypoints``
          (a class-based view — a Django/Flask CBV, say — marked at the class with no individually
          marked method). This walk is callables-only and never sees those; use
          :meth:`get_entrypoint_classes` for them. A ``PyClass`` is not a callable, so it is not
          folded into this list under a synthetic ``kind`` — that would misrepresent what
          ``PyCallableOverview`` means.
        * **Whether the pass itself had gaps.** The analyzer's own ``PyEntrypointReport`` docstring
          says detection "under-approximates by design, so silence is its failure mode" — an empty
          (or any) result here cannot distinguish "ran clean, found none" from "had gaps" on its
          own. Use :meth:`get_entrypoint_coverage` for that signal.

        Declared here rather than on the generic cross-language ABC: Java stamps a ``JEntrypoint``
        marker label and TypeScript carries them on ``TSApplication.entrypoints`` — a third
        spelling of the same idea — and unifying that vocabulary across languages is out of scope
        for this change."""

    @abstractmethod
    def get_entrypoint_classes(self) -> List[PyClassOverview]:
        """Overviews of every *class* the analyzer marked as an entrypoint in its own right
        (``PyClass.is_entrypoint``) — the class-level sibling of :meth:`get_entrypoints`, which
        walks callables only and so never sees a class-based view marked at the class with no
        individually-marked method. Same empty-vs-absent guarantee as :meth:`get_entrypoints`."""

    @abstractmethod
    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Coverage and failure record for the entrypoint-detection pass (``PyEntrypointReport``),
        so a caller can tell "the pass ran clean and found nothing" apart from "the pass had gaps"
        — a distinction :meth:`get_entrypoints`'s empty list alone cannot make. See
        :class:`~cldk.analysis.commons.results.EntrypointCoverage` for the field-by-field contract,
        including the per-backend availability caveat (the Neo4j projection does not carry this
        report at all; that backend answers with a ``diagnostics``-only result rather than
        fabricating empty-but-clean-looking coverage fields)."""

    @property
    @abstractmethod
    def has_resolution_edges(self) -> bool:
        """Whether this backend can resolve call-site ``callee_signature`` at all right now.

        ``get_callsites_for``'s per-site ``callee_signature`` is ``None`` both for "genuinely
        unresolved" and, on the Neo4j backend, for "this graph was populated at an analysis level
        below the one where the defuse-linker backfill runs, so ``PY_RESOLVES_TO`` doesn't exist
        at all" — ``PyCallsite`` is the analyzer's own frozen model with no field to carry that
        distinction. This is the disambiguator: ``False`` means every ``None`` from
        ``get_callsites_for`` is explained by that, not by individual call sites failing to
        resolve.

        The local backend always attempts resolution via Jedi regardless of analysis level (see
        :meth:`get_callsites_for`'s local-vs-Neo4j caveat), so it is unconditionally ``True``
        there. The Neo4j backend probes for at least one ``PY_RESOLVES_TO`` edge once at
        connection time.
        """

    @abstractmethod
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        """Call sites of the given callable signatures, keyed by owning signature. Each existing
        signature gets an entry (an empty list if it has no call sites); signatures with no matching
        callable are omitted.

        Every returned :class:`~cldk.models.python.PyCallsite`'s ``callee_signature`` is resolved
        through the union of declared callables and :meth:`get_external_symbols` rather than left
        in Jedi's raw, unaddressable dotted-name form for a library/builtin target (field data:
        602 recorded incidents were ``callee_signature=None`` for exactly this case) — a call to a
        declared callable keeps its dotted signature; a call to something outside the project
        resolves to the ``can://…/@external/…`` id under which :meth:`get_external_symbols` files
        it, so ``get_external_symbols()[site.callee_signature]`` finds it. ``None`` still means the
        call site is genuinely unresolved (Jedi failed and no backfilled resolution exists) — the
        one case this cannot and must not manufacture an answer for.

        One caveat, inherent to what each backend can see rather than a bug: the local backend can
        always attempt this (Jedi's own guess is present regardless of analysis level), but the
        Neo4j backend can only follow a call's ``PY_RESOLVES_TO`` edge — present only when the
        graph was populated at an analysis level where the defuse-linker backfill ran (``-a 2`` or
        higher). A ``None`` from the Neo4j backend can therefore mean either "genuinely
        unresolved" or "this graph doesn't carry per-site resolution at all" — ``PyCallsite`` is
        the analyzer's own frozen model with no field to carry that distinction, so it cannot be
        disambiguated here the way an accessor's own empty return could be. Partial mitigation:
        :attr:`has_resolution_edges` is ``False`` exactly when the Neo4j backend's attached graph
        has *no* ``PY_RESOLVES_TO`` edge anywhere — in that case every ``None`` here is explained
        by the graph's analysis level, not by individual call sites failing to resolve. (The
        local backend is always ``True`` here — see :attr:`has_resolution_edges`.)
        """

    @abstractmethod
    def get_external_symbols(self) -> Dict[str, PyExternalSymbol]:
        """Every call-graph endpoint outside the analyzed project — an imported library or builtin
        member — keyed by its ``can://…/@external/…`` id, the ``@external`` can-id
        :class:`~cldk.models.python.PyExternalSymbol` is filed under. The analyzer mints one of
        these ghost symbols for every call target that isn't a declared class/callable, precisely
        so no call-graph edge dangles; this is how a caller resolves one, and how
        :meth:`get_callsites_for` addresses a resolved external ``callee_signature``.

        Declared here rather than on the generic cross-language ABC: ``PyExternalSymbol`` is
        ``codeanalyzer-python``'s own model, with no cross-language equivalent yet (mirrors why
        :meth:`get_entrypoints` stays Python-specific despite a shared-looking return type).

        An empty dict means this project's call graph has no calls outside itself — a real,
        unambiguous fact on both backends, not a stand-in for "can't tell": external symbols are
        homed from the aggregate call graph, which every analysis level and the Neo4j projection
        both carry unconditionally (unlike the per-call-site backfill :meth:`get_callsites_for`'s
        docstring caveats)."""

    @abstractmethod
    def get_config_readers(self, key: str) -> List[PyCallableOverview]:
        """Overviews of every callable that reads configuration key ``key``, resolved from
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_config_uses`'s edges.

        That generic accessor hands back ``PyConfigUseEdge.src`` as an opaque GLOBAL ordinal id
        (``<callable-id>@<local-id>``) — resolving it to "which callable" requires knowing
        ``codeanalyzer-python``'s id grammar, so this stays Python-specific even though its return
        type (``PyCallableOverview``) is the same shared projection :meth:`get_entrypoints` and
        :meth:`get_callables_overview` already return. Empty means no callable reads this key,
        which is not the same as "a read exists but never resolved to a key" — see
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_unresolved_config_reads` for
        that case.
        """

    # -----[ locate ]-----
    # The v2 query-facade spec's D3. Declared here rather than on the generic cross-language ABC
    # because LocateResult carries codeanalyzer-python's BodyNode/Span — see
    # cldk/analysis/commons/backend.py's module docstring for why that stays out of the shared
    # contract until a second language implements it.
    @abstractmethod
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        Four outcomes, kept distinguishable rather than collapsed into an ambiguous empty: inside a
        callable (``callable`` set, and ``node`` set too when a body node is that precise); at module
        scope (a real position with no enclosing callable — a ``module_scope`` diagnostic); in the
        gap between two callables (also module scope, and never silently snapped to the nearest
        callable); or in a file the graph has no module for (``file_not_in_graph``).

        Args:
            path: The file path. Normalised against the backend's module keys, so a ``./``-prefixed
                or absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.
        """

    @abstractmethod
    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in one round trip, in input order.

        The bulk form, not an optimisation over :meth:`locate`: a scanner hands over a whole alert
        set at once, and round trips cost latency for a person and context for an agent.
        """

    # -----[ addressing ]-----
    # A caller names things the way it already thinks of them; the SDK resolves. Nothing here takes
    # or returns a ``can://`` URI (E6), and nothing takes an ordinal (E7). The resolution *policy*
    # is not implemented per backend -- both route through
    # :mod:`cldk.analysis.commons.resolve`, so they cannot drift on what "ambiguous" means. What a
    # backend implements is only how it produces the candidates.
    @abstractmethod
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the callable it names.

        The **candidate domain is every callable in the analysed application** -- exactly the set
        :meth:`get_callables_overview` reports: module-level functions, class methods, and
        callables nested inside either, in the modules belonging to this application. Both backends
        must resolve against that same domain; a shared *predicate* over different *sets* is not
        parity.

        ``name`` is matched whole or as a dotted suffix on segment boundaries (``"execute"`` names
        any ``….execute``; ``"cursor.execute"`` narrows), with an exact match winning outright.
        ``in_class`` / ``in_module`` disambiguate rather than scope -- a callable is the unit of
        address, so there is nothing to scope it *to* -- and are matched the same segment-wise way
        against the owning class's signature and the module's repo-relative path.

        Args:
            name: The callable name, whole or a dotted suffix of its signature.
            in_class: Keep only callables owned by the class this names.
            in_module: Keep only callables in the module this names.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind="callable"``, the
            callable's dotted signature in ``callable``, and its opaque graph id in ``ref``. That
            ``ref`` round-trips through :meth:`get_source` on either backend -- the one sanctioned
            use of an opaque id.

        Raises:
            AmbiguousName: More than one callable matched, listing every match. The resolver never
                picks: 86% of leaf names in a real application are unique and the rest are
                framework methods, where a guess is a confident wrong answer.
            SelectorNotInGraph: Nothing matched. No near-miss suggestions -- E8 puts typo-tolerant
                matching out of scope in the error path as much as in the resolver.
        """

    @abstractmethod
    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable to the position that carries it.

        A value name is scoped by its callable (spec § 5.2), so ``within`` is required and is
        itself resolved by :meth:`resolve_callable` -- ``within="PaymentPortal.invoice_transaction"``
        is enough; the full signature is not needed.

        The **candidate domain is the resolved callable's ``formal_in`` vertices**: every named
        value that *enters* it, which is what a backward slice seeds from. Three things do, and
        they are not all parameters -- on a real application 84% of them are captured module
        globals, with a small tail of closure captures -- so the answer carries
        ``kind="parameter"``, ``"global"`` or ``"capture"``, always addressed by name with no
        ordinal anywhere in it (E7).

        The domain is deliberately *not* every body node carrying a variable. Two reasons, both
        measured: the same name also appears on the callable's ``formal_out`` vertex and at each of
        its call sites' actuals, so collapsing those into one namespace would make every mutated
        parameter ambiguous with its own exit value; and **a local variable has no address here at
        all** -- ``var`` is non-null only on the four parameter-passing kinds (``formal_in``,
        ``formal_out``, ``actual_in``, ``actual_out``: 680,321 vertices on a real application, all
        with a ``var``), while every other kind carries ``var = NULL`` without exception
        (``statement``, ``call``, ``return``, ``branch``, ``loop``, ``raise``, ``handler``,
        ``entry``, ``exit``: 204,897 vertices, none with one). A name that is only ever assigned
        and read inside the body is not resolvable through this method; ``locate(path, line)`` is
        what addresses those positions.

        A parameter or a capture is named by its bare name. A **global** is named
        ``"<module_name>.<name>"``, matched by the same segment rule as a callable signature, so
        ``"AccessError"`` names it and ``"payment.AccessError"`` narrows when the callable captures
        that name from several modules (measured: 14,432 such (callable, leaf name) pairs, and no
        two values whose qualified names collide -- so the qualified spelling always resolves).

        Args:
            name: The value's name, as written in the source; for a global, optionally qualified
                by its defining module.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`. It takes
                no ``in_class=`` / ``in_module=``: ``within`` is matched segment-wise against the
                whole signature, so naming more of the dotted path narrows by class and module
                already, and that is what an ambiguity raised here advises.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind`` one of
            ``"parameter"`` / ``"global"`` / ``"capture"``, ``name`` set to the readable identifier
            (never the analyzer's ``"<global>:payment::AccessError"`` spelling), ``defined_in`` set
            to the defining module for a global, ``file``/``line`` pointing at the *callable's*
            definition (these are dataflow vertices with no span of their own), and the vertex's
            opaque graph id in ``ref``.

        Note:
            Because these vertices have no span, ``ref`` does **not** round-trip through
            :meth:`get_source` on either backend -- there is no source text to return. Only a
            :meth:`resolve_callable` ``ref`` does.

        Raises:
            AmbiguousName: ``within`` named more than one callable, or more than one value matched.
            SelectorNotInGraph: No such callable, or no such value in it.
        """

    # -----[ source access ]-----
    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """Source text for one node, named by ``node_id``.

        Generalises body access below callable granularity: ``node_id`` is either a callable's
        signature (the same key :meth:`get_method_bodies` uses) or the opaque body-node id
        :attr:`LocateResult.node_id` hands back alongside :attr:`LocateResult.node`, so a caller
        can re-fetch the precise statement or call site :meth:`locate` found, not just its
        enclosing callable. The body-node form is the analyzer's own id
        (``"<callable can:// id>@<body key>"``) — round-tripped, never composed by the caller.

        Raises:
            KeyError: No callable has that signature, no body node has that key, or the node
                exists but carries no recoverable source (no span — e.g. an abstract stub).
            NotImplementedError: (Neo4j backend only) ``node_id`` names a body node. The graph
                projects per-callable text (``:PyCallable.code``) but nothing below that —
                ``:PyBodyNode`` carries a line span and no text to slice, and ``:PyModule`` carries
                no source either (see :class:`~cldk.analysis.commons.results.LocateResult`). Only
                the local codeanalyzer backend, which holds the module's real text and byte
                offsets, can answer for a statement or call site.
        """

    # -----[ per-callable graphs ]-----
    # The domain of all three is ONE callable's own body nodes: an edge is returned only when both
    # of its endpoints are body nodes of the callable named. Verified on odoo-slim-19 that the
    # graph carries no cross-callable PY_CFG_NEXT / PY_CDG / PY_DDG edge at all (0 of 5,521,626),
    # so the restriction states the domain rather than narrowing it -- interprocedural flow lives
    # on PY_PARAM_IN / PY_PARAM_OUT / PY_SUMMARY, which these accessors deliberately do not follow.
    #
    # PER-CALLABLE IS A SCOPING BOUND, NOT A SIZE ONE, and E5 wants both. A callable's body is
    # finite and already chosen by the caller, but finite is not small: measured maxima on
    # odoo-slim-19 are 402 CFG edges, 314 CDG edges and 1,386,918 DDG edges -- that last one is
    # 27% of the entire application's data dependence, in one callable, and returning it as a list
    # is on the order of half a gigabyte of models built in one call. So the response is paged.
    #
    # PAGED, NOT TRUNCATED. A cap answers "there was more" and throws the rest away; a page
    # answers the same question and keeps every edge reachable, which is the whole difference when
    # the caller is composing queries and cannot know in advance which edges it will need.
    # :class:`~cldk.analysis.commons.results.EdgePage` carries ``total`` and ``next_cursor``, so
    # "this is everything" and "there is more" are distinguishable from one page (E5's "never
    # silent"), and an empty page with ``total == 0`` still means "no dependence" (D7).
    #
    # ALL THREE PAGE, though only DDG needs it today: CFG tops out at 402 edges and CDG at 314 on
    # a real application, so neither is at risk -- but three sibling accessors returning two
    # different shapes is a defect generator on a surface composed at runtime, and the day a CFG
    # does get large the shape would have to change under callers who had already learnt it.
    #
    # ORDER. Paging is only defined over a total order, and the same one on both backends. It is
    # stated once, in :func:`cfg_sort_key` / :func:`cdg_sort_key` / :func:`ddg_sort_key`, and both
    # backends use those functions rather than each choosing an order that happens to agree.
    #
    # ``src`` / ``dst`` are body-node ids in the one vocabulary the rest of this contract already
    # uses: ``<callable can:// id>@<body key>``, exactly what
    # :attr:`~cldk.analysis.commons.results.LocateResult.node_id` returns and
    # :meth:`get_source` accepts. Both backends emit that spelling, so an endpoint from either is
    # an address, not an opaque token.
    #
    # ANALYSIS LEVEL. All three graphs are built by the analyzer only at level 3
    # (``program_dependency_graph``) and above; ``points-to`` DDG evidence needs level 4. A
    # backend attached to a shallower analysis MUST raise rather than return an empty page: an
    # empty there would be indistinguishable from a callable that genuinely has no data
    # dependence, which is the ambiguous empty D7 rules out. An empty page from a
    # level-3-or-deeper backend is the honest answer and stays available to mean exactly that.
    # (The Neo4j backend is always level 4 -- ``--emit neo4j`` forces it -- so only the local
    # backend can be below the line; the rule is stated here, once, because it is the contract and
    # not an implementation detail.)
    @abstractmethod
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[CfgEdge]:
        """One page of the control flow edges within one callable.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable` -- so an ambiguous
                name raises listing candidates rather than being guessed at.
            in_class: Disambiguate by owning class, as in :meth:`resolve_callable`.
            page_size: Most edges to return. See :data:`DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page; ``None`` starts at the beginning.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.python.CfgEdge`, each carrying the analyzer's ``kind``
            (``fallthrough``, ``true``, ``false``, ``exception``, ``return``, ``loop_back``,
            ``break``, ``continue``, ``yield``, ``await_resume``) -- a conditional's two successors
            stay two edges, discriminated by ``kind``, which is also why ``kind`` is part of the
            order (:func:`cfg_sort_key`).

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page.
        """

    @abstractmethod
    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[CdgEdge]:
        """One page of the control dependence edges within one callable.

        ``src`` is the branching node a ``dst`` is control dependent on -- post-dominance over the
        CFG :meth:`get_cfg` returns, computed by the analyzer, not re-derived here.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable`.
            in_class: Disambiguate by owning class.
            page_size: Most edges to return. See :data:`DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.python.CdgEdge`, ordered by :func:`cdg_sort_key`.

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page.
        """

    @abstractmethod
    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[DdgEdge]:
        """One page of the data dependence edges within one callable.

        Each edge carries the variable it flows (``var``) and its evidence (``prov``), so a caller
        separates syntactic from alias-aware dependence without a second call. Three provenances
        occur, one per edge (measured over all 5,134,655 DDG edges of odoo-slim-19):

        * ``ssa`` (550,316) -- def-use from the callable's SSA form.
        * ``reaching-defs`` (3,036,102) -- the flow-sensitive reaching-definitions closure. Real,
          and undocumented upstream; recorded in this leg's divergence register rather than
          quietly accepted.
        * ``points-to`` (1,548,237) -- the alias-aware delta the level-4 oracle adds beyond the
          syntactic set. Absent at level 3, which is a narrower answer and not a wrong one.

        This is the accessor pagination exists for: one callable's DDG reaches 1,386,918 edges on
        a real application. ``EdgePage.total`` reports that up front, on the first page, so a
        caller sees the size of the answer before deciding to walk it.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable`.
            in_class: Disambiguate by owning class.
            page_size: Most edges to return. See :data:`DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.python.DdgEdge` -- one per (edge, variable, provenance): the same
            statement pair legitimately appears more than once when it carries several variables
            or several kinds of evidence, and collapsing those would drop dependences. That is
            also why ``var`` and ``prov`` are both in the order (:func:`ddg_sort_key`).

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page.
        """

    # -----[ slicing and reachability ]-----
    # THE TRAVERSAL RUNS IN THE DATABASE (E3). The Neo4j backend compiles each of these to one
    # variable-length match over :data:`SDG_RELS`; nothing here streams 6,089,420 edges into
    # Python to walk them. Measured on odoo-slim-19, the worst single slice -- 195,784 nodes
    # reached from one statement of ``Website.configurator_apply`` -- comes back with its exact
    # ``total`` and 10,000 hydrated nodes in 1.5s, because Neo4j plans
    # ``(a)<-[:R*1..]-(b) RETURN DISTINCT b`` as ``VarLengthExpand(Pruning,BFS)`` rather than as
    # trail enumeration. Checked on the live graph, not assumed.
    #
    # THE LOCAL BACKEND ANSWERS THE SAME QUESTIONS, and that was not the expectation going in: it
    # holds ``cfg``/``cdg``/``ddg`` per callable and has no cross-callable index, so an
    # interprocedural slice looked like something it would have to decline with a Diagnostic. It
    # does not have to. A level-4 run carries the whole SDG in the model -- ``PyApplication``'s
    # ``param_in``/``param_out`` (whose endpoints are already global body-node ids) and
    # ``PyCallable.summary`` -- and those are the very lists ``codeanalyzer.neo4j.project``
    # projects as PY_PARAM_IN / PY_PARAM_OUT / PY_SUMMARY. The index it lacks it can build, from
    # the data the graph is emitted from, so the two backends answer from one set of edges.
    #
    # A SLICE IS A SET (E2). Ordered by node id, which is the only total order both backends can
    # compute without agreeing on a traversal, and which is what makes ``max_nodes`` take a
    # deterministic prefix rather than an arbitrary subset. Paths are Task 7 and a different type.
    #
    # THE LEVEL GUARD IS THE ONE ``get_ddg`` ALREADY USES. Slicing needs the same analyzer pass,
    # so it goes through the same check rather than a second one that could drift from it.
    @abstractmethod
    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` depends on: reverse reachability over the SDG.

        ``within`` is **required**, unlike the plan's sketch of this signature. A value name is
        scoped by its callable (spec § 5.2) and :meth:`resolve_value` cannot resolve one without
        it, so a default of ``None`` would be a signature that raises on its own default. The
        keyword narrows by class and module already, because it is matched segment-wise against
        the whole dotted signature.

        What comes back *unbounded* is usually one of two things, and the measurement says so
        plainly: over 200 random entering values on a real application the median backward slice
        is **1 node** -- the seed itself, because nothing calls the callable -- while over the
        values that do have callers the median is **195,786**, a fifth of the program. There is
        no middle, so ``max_nodes`` on the second kind returns an unprincipled 5% of a closure.

        **Which is why ``depth`` defaults to** :data:`DEFAULT_DEPTH` **(5) rather than to
        ``None``.** A bounded traversal answers a narrower question *completely*: measured over
        the same distribution, no backward or forward slice at depth 5 reaches ``max_nodes`` at
        all. ``depth=None`` still asks for the whole closure, and is how a caller who wants the
        fifth of the program asks for it.

        Args:
            src: The value's name, resolved by :meth:`resolve_value` — a parameter, a captured
                global (optionally qualified, ``"payment.AccessError"``) or a closure capture.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`.
            depth: Most hops from the seed. Defaults to :data:`DEFAULT_DEPTH`; ``None`` for the
                whole cone. This is the bound that yields a *complete* answer to a narrower
                question, which is why it, and not ``max_nodes``, is what carries the default.
            max_nodes: Most nodes in the result. A cap that fires is reported by
                :attr:`~cldk.analysis.commons.results.Slice.truncated` and quantified by
                :attr:`~cldk.analysis.commons.results.Slice.total`; it is never silent.

        Returns:
            A :class:`~cldk.analysis.commons.results.Slice` containing the seed, ordered by node
            id, with ``source`` unhydrated on every node.

        Raises:
            AmbiguousName: ``within`` named more than one callable, or ``src`` more than one value.
            SelectorNotInGraph: No such callable, or no such value in it.
            ValueError: ``depth`` that is not a positive ``int``, or ``max_nodes`` below 1.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``, where there is no dataflow to slice.
        """

    @abstractmethod
    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` can affect: forward reachability over the same edges.

        The same accessor read the other way round, and the one that is usually the interesting
        one for a value entering a callable: nothing flows *into* a parameter except from its
        callers, so ``slice_backward`` from one is often the seed alone, while
        ``slice_forward`` follows it through the body and out through every call it feeds.

        Arguments, bounds and failures are :meth:`slice_backward`'s, ``depth``'s default of
        :data:`DEFAULT_DEPTH` included. Measured unbounded on the same 200 seeds: median 1,
        p95 440,270, max 440,662 of 885,218 body nodes — a forward cone is the larger of the two,
        which is why ``depth=None`` matters more here.
        """

    @abstractmethod
    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path from ``src`` to ``dst``?

        A **call-graph** question (spec § 6), over ``PY_CALLS`` — "can control get from here to
        there at all", the cheap check a caller makes before asking for the paths themselves.
        Both names go through :meth:`resolve_callable`, so an ambiguous one raises listing
        candidates rather than being guessed at.

        Returns ``bool`` and nothing else: it is deliberately not a degenerate ``Slice``, because
        "is there a path" and "what is on it" are different questions with different costs.

        **``depth`` still defaults to ``None`` here, unlike the three traversals.** A default that
        bounds a *slice* trades size for a complete answer to a narrower question; a default that
        bounds a *boolean* would turn "there is no path" and "there is no path within 5 hops" into
        the same ``False``, which is a wrong answer rather than a small one. It is not a cost
        question either: measured over 200 random pairs on odoo-slim-19 the unbounded call
        averages 20ms and its worst case is 112ms.

        Args:
            src: The calling callable's name.
            dst: The called callable's name.
            depth: Most call hops, or ``None`` for any distance.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either name matched none.
            ValueError: ``depth`` that is not a positive ``int``.
        """

    @abstractmethod
    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every callable that can reach any of ``sinks`` — "what could get here".

        A **call-graph** cone, so its nodes are callables (``kind="callable"``), not body nodes;
        it is the accessor a caller reaches for when the sink is a dangerous function and the
        question is which entry points lead to it. The sinks themselves are in the result, and in
        :attr:`~cldk.analysis.commons.results.Slice.roots`.

        ``max_nodes`` is not in the plan's signature for this one and is here anyway: a cone is a
        slice, and measured on odoo-slim-19 five ``.write`` methods between them have 9,282
        callables behind them. A result type that reports a cap on one accessor and cannot on its
        sibling would make the bound silent exactly where it fires.

        ``depth`` defaults to :data:`DEFAULT_DEPTH`, as it does on the slices — for a weaker
        reason, stated so a reader does not assume otherwise. A cone is a call-graph question and
        measured on odoo-slim-19 it never reaches ``max_nodes``: over 150 random called callables
        the unbounded cone's max is 9,346, against a 10,000 cap. What the default buys here is
        interpretability and one rule across the three traversals, not protection from truncation
        (median 12 at depth 5 against 14 unbounded; p75 2,929 against 9,282). ``depth=None`` is
        the whole cone.

        Args:
            sinks: The callables to walk back from, each resolved by :meth:`resolve_callable`.
            depth: Most call hops back. Defaults to :data:`DEFAULT_DEPTH`; ``None`` for the whole
                cone.
            max_nodes: Most nodes in the result.

        Raises:
            AmbiguousName: A sink name matched more than one callable.
            SelectorNotInGraph: A sink name matched none.
            TypeError: ``sinks`` is a bare string.
            ValueError: ``sinks`` is empty, ``depth`` is not a positive ``int``, or ``max_nodes``
                is below 1.
        """

    @abstractmethod
    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Who calls this — one hop back over ``PY_CALLS``, addressed by name.

        The name-based sibling of :meth:`get_all_callers`, which takes a class signature plus a
        method name and returns raw dicts. That one is a frozen leg-1 signature and is not
        touched; this one takes a name the caller already has and returns
        :class:`~cldk.analysis.commons.results.SliceNode` objects, so moving from "who calls this" to a
        slice needs no translation between two shapes.

        Callers are declared callables of this application. Call edges *originating* at an
        external ghost exist in the raw graph (5,307 on odoo-slim-19) and are out of scope here
        for the same reason they are out of scope for ``get_call_graph``: an unanalysed symbol has
        no body, so it cannot be the start of anything this surface can then be asked about.

        An empty list is unambiguous: a name that matches nothing raises, so ``[]`` means "nothing
        calls it".

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """

    @abstractmethod
    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """What this calls — one hop forward over ``PY_CALLS``, addressed by name.

        **Externals are included**, with ``kind="external"``. They are 38,585 of this graph's
        370,110 call edges and they are what a caller tracing a sink is usually looking for, so
        dropping them would be the ambiguous empty in another costume. An external was never
        analysed, so it has no position: ``file`` is ``""`` and ``line`` is ``0``, and ``kind``
        is what says why rather than leaving two sentinels to be discovered. Its ``callable`` is
        the readable dotted name built from the node's own ``module`` and ``name`` properties
        (``"odoo.exceptions.ValidationError.__init__"``) — never its ``can://`` id, which stays
        in ``ref`` where an opaque handle belongs (E6).

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """
