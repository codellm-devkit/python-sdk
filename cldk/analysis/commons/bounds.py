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

"""Bounds, selection and keyset paging: the language-neutral rulings every backend shares.

Lifted out of the Python backend (leg 2.5a, G4) unchanged. Nothing here knows a language: a
``depth`` is a hop budget, a ``page_size`` is a count, a selector that names nothing is refused
the same way whether the names are Python paths or TypeScript ones. The per-language backends
import these rather than re-deriving them, which is what keeps two backends of one language --
and the backends of two languages -- from drifting on what a keyword means.
"""

from __future__ import annotations

import base64
import json
from bisect import bisect_right
from typing import Callable, Dict, List, NamedTuple, Sequence, Tuple

from cldk.analysis.commons.results import EdgePage, SliceNode
from cldk.utils.exceptions import SelectorNotInGraph


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
        # ``roots=`` is an exact filter, unlike every name-taking accessor on this surface, so a
        # correct short name and a typo miss the same way -- the message has to say which
        # vocabulary it wanted (see the assessment on PythonAnalysisBackend.get_call_graph).
        detail = (
            "roots= takes full signatures (as get_callables_overview() reports them) or @external ids, not bare names; "
            "to address a callable by name use resolve_callable(name).callable, or backward_cone / callers_of / call_paths_between"
            if kind == "roots"
            else None
        )
        raise SelectorNotInGraph(kind, list(missing), len(requested), detail=detail)


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


#: Paths per query when the caller does not say. A path list is a set of *witnesses* for a flow,
#: not the flow's extent, and ten worked examples is already more than a reader will follow; the
#: extent question is ``slice_forward``, which reports a ``total``.
DEFAULT_MAX_PATHS = 10


def check_max_paths(max_paths: int) -> int:
    """``max_paths`` must admit at least one path. Zero is refused for :func:`check_max_nodes`'s
    reason: an empty list whose ``truncated`` says "there were more" answers nothing, and it is
    indistinguishable at a glance from "there is no flow"."""
    if max_paths < 1:
        raise ValueError(f"max_paths must be at least 1, got {max_paths}")
    return max_paths


def check_distinct_endpoints(src: SliceNode, dst: SliceNode) -> None:
    """A path query must have two different endpoints.

    Neo4j's shortest-path search *refuses* a self-question outright ("the shortest path algorithm
    does not work when the start and end nodes are the same"), which would otherwise surface as a
    raw driver error from one backend and an empty list from the other. Both raise here instead,
    and neither answers ``[]``: for a node that genuinely sits on a cycle, ``[]`` would be
    indistinguishable from a proved absence of one, which is the ambiguous empty in another
    costume. ``reaches(x, x)`` is the accessor that answers the existence question, and it does
    terminate (measured: 0.03s, where the obvious ``EXISTS`` spelling never finished).

    Takes the *resolved* endpoints rather than their refs so the message speaks the caller's
    vocabulary (E6/E7): a value is named ``'kwargs' within '….configurator_apply'``, a callable
    by its signature, and the advice is a call that actually runs -- ``reaches`` takes callable
    names, so for a value the cycle question is asked of its enclosing callable.
    """
    if src.ref != dst.ref:
        return
    if src.kind == "callable":
        raise ValueError(f"paths from {src.callable!r} to itself are not answered; ask reaches({src.callable!r}, {src.callable!r}) whether a cycle exists")
    raise ValueError(
        f"paths from {src.name!r} to itself (within {src.callable!r}) are not answered; a value reaches itself only through "
        f"recursion, so ask reaches({src.callable!r}, {src.callable!r}) whether the callable is on a call cycle"
    )


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
#:
#: **Which accessors take it, and which deliberately do not.** The three *slices*
#: (``slice_backward``, ``slice_forward``, ``backward_cone``) default to it: a bounded slice is a
#: *complete* answer to a narrower question, and ``total`` says so. The two *predicates*
#: (``reaches``, ``flows_to_call``, ``flows_to_argument``) and the two *path* queries
#: (``paths_between``, ``call_paths_between``) default to ``None`` -- unbounded -- because a hop
#: budget on a boolean or a path list is not a smaller answer but a **wrong** one: "no flow" and
#: "no flow within five hops" collapse into the same ``False`` / ``[]`` with nothing in the result
#: to tell them apart. Measured on odoo-slim-19: ``flows_to_call("kwargs", "Website.create",
#: within="Website.configurator_apply")`` is ``False`` at five hops and ``True`` unbounded, and
#: the matching ``paths_between`` is ``[]`` at five hops and ten paths at eight. ``depth=`` stays
#: on all five as an explicit narrowing a caller can name; it is only the *default* that differs.
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
