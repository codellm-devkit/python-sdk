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

"""Per-callable control and data flow: ``get_cfg`` / ``get_cdg`` / ``get_ddg`` (leg 1.5, Task 5).

Three groups, separated by what evidence each is worth:

* **Live graph.** The Neo4j backend answered by a real application (odoo-slim-19), under the same
  environment variables as ``test_e2e_neo4j_live.py`` — see ``conftest.py``'s ``live_analysis``.
  Strictly read-only::

      CLDK_TEST_NEO4J_URI=bolt://localhost:7688 \
      CLDK_TEST_NEO4J_USER=neo4j \
      CLDK_TEST_NEO4J_PASSWORD=cldkleg1test \
      CLDK_TEST_NEO4J_APP=odoo-slim-19 \
      uv run pytest tests/analysis/python/test_dataflow.py

* **Real analyzer run.** The local backend over a three-callable project analysed at level 4. The
  local path only became capable of dataflow at all when ``analysis_level`` started reaching the
  analyzer (see ``test_analysis_level.py``), so a mock here would assert the plumbing that was
  already wrong once.

* **The level contract.** Dataflow exists only at ``program_dependency_graph`` and deeper. A
  backend built below that must say so, because ``[]`` there would be indistinguishable from a
  callable that genuinely has no data dependence — the ambiguous empty D7 forbids.
"""

from __future__ import annotations

import inspect
import os
import re
import textwrap

import pytest

from codeanalyzer.neo4j.project import _project_program_graphs
from codeanalyzer.neo4j.rows import RowBuilder
from codeanalyzer.schema.ids import application_id

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.resolve import value_candidate
from cldk.analysis.commons.resolve import body_node_kind
from cldk.analysis.commons.results import EdgePage, FlowPath, FlowPaths, PathHop, Slice, SliceNode, prov_rank
from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.analysis.python.backend import (
    DDG_ORDER,
    DEFAULT_DEPTH,
    DEFAULT_MAX_PATHS,
    PythonAnalysisBackend,
    DEFAULT_PAGE_SIZE,
    cdg_sort_key,
    cfg_sort_key,
    ddg_sort_key,
    decode_cursor,
    edge_page,
    encode_cursor,
    hop_sort_key,
)
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.models.python import DdgEdge, PyCallEdge
from cldk.utils.exceptions import AmbiguousName, CodeanalyzerUsageException, SelectorNotInGraph

live_only = pytest.mark.skipif(
    not os.environ.get("CLDK_TEST_NEO4J_URI"),
    reason="no live Neo4j (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)",
)


# ----------------------------------------------------------------------------------------------
# The three the plan names, over the live graph.
# ----------------------------------------------------------------------------------------------
@live_only
def test_ddg_for_a_real_callable(live_analysis, busy_callable):
    edges = live_analysis.get_ddg(busy_callable).edges
    assert edges, "a busy callable has data dependence"
    assert all(e.src and e.dst for e in edges)
    assert any(e.var for e in edges), "DDG edges carry the variable"


@live_only
def test_ddg_prov_distinguishes_evidence(live_analysis, busy_callable):
    """``prov`` separates syntactic from alias-aware evidence."""
    provs = {p for e in live_analysis.get_ddg(busy_callable).edges for p in (e.prov or [])}
    assert provs, "every edge carries provenance"
    assert provs <= {"ssa", "reaching-defs", "points-to"}, f"unexpected prov: {provs}"


@live_only
def test_cfg_is_bounded_to_one_callable(live_analysis, busy_callable):
    """A CFG fits in one page and always has: the largest on this application is 402 edges, well
    under :data:`DEFAULT_PAGE_SIZE`, so ``total`` is the whole answer and ``next_cursor`` is None."""
    page = live_analysis.get_cfg(busy_callable)
    assert page.total < DEFAULT_PAGE_SIZE and page.next_cursor is None


# ----------------------------------------------------------------------------------------------
# Live: the domain, stated and then checked. One callable's own edges, and nothing else's.
# ----------------------------------------------------------------------------------------------
@live_only
def test_every_endpoint_belongs_to_the_named_callable(live_analysis, busy_callable):
    """The bound is structural, not a cap: every endpoint id is prefixed by the resolved
    callable's own id, so no edge can reach a body node another callable owns."""
    node = live_analysis.backend.resolve_callable(busy_callable)
    for page in (live_analysis.get_cfg(busy_callable), live_analysis.get_cdg(busy_callable), live_analysis.get_ddg(busy_callable)):
        assert page.edges
        assert all(e.src.startswith(node.ref + "@") and e.dst.startswith(node.ref + "@") for e in page.edges)


@live_only
def test_cfg_edges_carry_their_kind(live_analysis, busy_callable):
    kinds = {e.kind for e in live_analysis.get_cfg(busy_callable).edges}
    assert kinds and all(kinds), "a CFG edge without a kind cannot be read"


@live_only
def test_an_ambiguous_callable_name_raises_rather_than_guessing(live_analysis):
    """Resolution is Task 4's, not a second path: ``write`` is ambiguous here exactly as it is
    for ``resolve_callable``, and ``in_class=`` is the documented way out."""
    with pytest.raises(AmbiguousName):
        live_analysis.get_ddg("write")
    assert live_analysis.get_ddg("write", in_class="AccountMove").edges


# ----------------------------------------------------------------------------------------------
# The local backend, over a real level-4 analyzer run.
# ----------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tiny_project(tmp_path_factory):
    """Small enough to analyse at level 4 in a test, with the shapes dataflow is about: a
    parameter, a branch, a loop and a module global read from inside a method."""
    root = tmp_path_factory.mktemp("dataflow")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            LIMIT = 100


            class Portal:
                def charge(self, invoice_id):
                    amount = invoice_id * 2
                    for _ in range(3):
                        amount = amount + 1
                    if amount > LIMIT:
                        amount = LIMIT
                    return amount
            """
        ).lstrip()
    )
    return root


def _backend(project, cache, level) -> PyCodeanalyzer:
    return PyCodeanalyzer(
        project_dir=project,
        analysis_level=level,
        analysis_json_path=None,
        eager_analysis=False,
        cache_dir=cache,
    )


@pytest.fixture(scope="module")
def local_l4(tiny_project, tmp_path_factory) -> PyCodeanalyzer:
    return _backend(tiny_project, tmp_path_factory.mktemp("cache-l4"), AnalysisLevel.system_dependency_graph)


@pytest.fixture(scope="module")
def local_l2(tiny_project, tmp_path_factory) -> PyCodeanalyzer:
    return _backend(tiny_project, tmp_path_factory.mktemp("cache-l2"), AnalysisLevel.call_graph)


def test_local_backend_answers_all_three_graphs(local_l4):
    assert local_l4.get_cfg("Portal.charge").edges
    assert local_l4.get_cdg("Portal.charge").edges
    ddg = local_l4.get_ddg("Portal.charge").edges
    assert ddg and any(e.var == "amount" for e in ddg)
    assert {p for e in ddg for p in e.prov} <= {"ssa", "reaching-defs", "points-to"}


def test_the_local_answer_is_edge_for_edge_what_the_graph_would_hold(local_l4):
    """The parity anchor, and the reason it is shaped this way.

    Twice in this leg two backends agreed on a *predicate* while disagreeing about the *set* it
    ran against. Comparing the two backends directly would need a second populated database, and
    this repository's live graph is read-only. So compare against the thing that stands between
    them instead: ``codeanalyzer.neo4j.project`` is the emitter that produces the very
    ``PY_CFG_NEXT`` / ``PY_CDG`` / ``PY_DDG`` rows the Neo4j backend's Cypher reads back. If the
    local backend's answer equals the emitter's rows edge for edge — endpoints, ``kind``, ``var``
    and ``prov`` included — then the two backends are reporting one set in one vocabulary.

    What this does *not* establish is that the Cypher matches every row it should on a real
    database; the live tests above are what covers that.
    """
    rows = RowBuilder()
    # 1.4.1 anchors ghost ids on the application's can:// id; the analyzer names the app after
    # the project directory (codeanalyzer/core.py: `app_name or project_dir.name`).
    _project_program_graphs(rows, local_l4.application, {}, {}, application_id(local_l4.project_dir.name))
    edges = rows.finish().edges

    def emitted(rel, *props):
        return sorted((e.from_ref.value, e.to_ref.value, *(e.props.get(k) for k in props)) for e in edges if e.type == rel)

    assert sorted((e.src, e.dst, e.kind) for e in local_l4.get_cfg("Portal.charge").edges) == emitted("PY_CFG_NEXT", "kind")
    assert sorted((e.src, e.dst) for e in local_l4.get_cdg("Portal.charge").edges) == emitted("PY_CDG")
    assert sorted((e.src, e.dst, e.var, e.prov) for e in local_l4.get_ddg("Portal.charge").edges) == emitted("PY_DDG", "var", "prov")


def test_local_endpoints_round_trip_through_get_source(local_l4):
    """A statement endpoint is an address, not an opaque token: it is the same ``node_id``
    ``get_source`` takes.

    Only the real statements — the synthetic bookends (``…@entry``, ``…@exit``) are body nodes
    with no span, and ``get_source`` raises on those by design rather than inventing text. The two
    are told apart by the shape of the key after the last ``@``: ``line:col`` for a statement, a
    word for a synthetic vertex (``codeanalyzer.neo4j.project._global_ordinal``).
    """
    stmts = [e.src for e in local_l4.get_cfg("Portal.charge").edges if re.fullmatch(r"\d+:\d+", e.src.rsplit("@", 1)[-1])]
    assert stmts
    assert all(local_l4.get_source(s).strip() for s in stmts)


def test_a_callable_with_no_dependence_is_an_honest_empty(local_l4, local_l2):
    """An empty answer at level 4 means "no data dependence", and must stay distinguishable from
    "this analysis has no dataflow at all" now that the return type is a page rather than a list.

    Both sides are checked: below level 3 the backend raises (naming both levels), and an empty
    edge set at level 4 becomes a page that reports ``total == 0`` and no continuation — an answer,
    not a shrug. The empty set is built through the shared pager rather than hunted for in the
    fixture, because whether any callable in a three-line project happens to have zero DDG edges is
    an accident of the analyzer, and the claim under test is about the page.
    """
    assert local_l4.get_cfg("Portal.charge").edges  # the callable is analysed
    assert isinstance(local_l4.get_ddg("Portal.charge").edges, list)

    with pytest.raises(CodeanalyzerUsageException):
        local_l2.get_ddg("Portal.charge")
    empty = edge_page(DdgEdge, "src.pay.Portal.nothing", [], DDG_ORDER, DEFAULT_PAGE_SIZE, None)
    assert empty.edges == [] and empty.total == 0 and empty.next_cursor is None and empty.complete


# ----------------------------------------------------------------------------------------------
# The level contract (D7): below level 3 there is no dataflow, and an empty list would lie.
# ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("accessor", ["get_cfg", "get_cdg", "get_ddg"])
def test_below_level_three_the_backend_refuses_rather_than_returning_empty(local_l2, accessor):
    with pytest.raises(CodeanalyzerUsageException) as e:
        getattr(local_l2, accessor)("Portal.charge")
    assert "program_dependency_graph" in str(e.value), "the error names the level required"
    assert "call_graph" in str(e.value), "the error names the level in use"


# ----------------------------------------------------------------------------------------------
# Pagination (E5). Per-callable is a *scoping* bound, not a *size* one: measured on odoo-slim-19
# one callable's DDG is 1,386,918 edges, 27% of the whole application's 5,134,655. The bound that
# was missing is on the response, and the ruling is to paginate rather than truncate — truncation
# throws away edges a caller may need, a page keeps every one of them reachable.
#
# Pagination is only well defined over a TOTAL order, and the same one on both backends, or page
# two on Neo4j is not page two locally. The order is stated once in
# ``cldk.analysis.python.backend`` (``cfg_sort_key`` / ``cdg_sort_key`` / ``ddg_sort_key``) and
# both backends use it: the local one sorts with it in Python, the Cypher writes the same
# expressions into its ``ORDER BY``. The tests below are what stops that from being an assertion.
# ----------------------------------------------------------------------------------------------
HEAVY_CALLABLE = "addons.website.models.website.Website.configurator_apply"
HEAVY_DDG_EDGES = 1_386_918


@live_only
def test_a_page_of_the_worst_callable_is_bounded_and_says_what_remains(live_analysis):
    """The case pagination exists for: 1.39M edges asked for, ``page_size`` edges delivered, and
    the caller can tell from the page alone that it has not been given everything."""
    page = live_analysis.get_ddg(HEAVY_CALLABLE, page_size=1_000)
    assert len(page.edges) == 1_000
    assert page.total == HEAVY_DDG_EDGES
    assert not page.complete and page.next_cursor


@live_only
def test_neo4j_orders_a_page_exactly_as_python_would(live_analysis):
    """The crux, checked rather than asserted.

    Neo4j returns rows in no order unless told, and the local backend returns analyzer emission
    order; the only thing making page N the same on both is that the Cypher's ``ORDER BY`` and the
    Python sort key are one order. This runs the real Cypher over 10,000 real rows and checks the
    server's ordering is exactly what ``ddg_sort_key`` produces — including how it orders the
    ``prov`` list, which is the one component whose comparison semantics differ between languages
    often enough to be worth doubting.
    """
    edges = live_analysis.get_ddg(HEAVY_CALLABLE, page_size=10_000).edges
    keys = [ddg_sort_key(e) for e in edges]
    assert keys == sorted(keys), "the server's ORDER BY is not the SDK's sort key"


@live_only
def test_walking_the_pages_yields_the_whole_set_once_each(live_analysis, busy_callable):
    """No gap, no repeat, no reordering: 17 pages of ten reassemble into the one page of 10,000."""
    whole = live_analysis.get_ddg(busy_callable, page_size=10_000)
    assert whole.next_cursor is None and whole.total == len(whole.edges) > 100

    walked, cursor = [], None
    while True:
        page = live_analysis.get_ddg(busy_callable, page_size=10, cursor=cursor)
        assert page.total == whole.total, "every page reports the same whole"
        walked += page.edges
        cursor = page.next_cursor
        if cursor is None:
            break
    assert [e.model_dump() for e in walked] == [e.model_dump() for e in whole.edges]


@live_only
@pytest.mark.parametrize("accessor", ["get_cfg", "get_cdg", "get_ddg"])
def test_all_three_accessors_page_the_same_way(live_analysis, busy_callable, accessor):
    """Three sibling methods returning three shapes is a defect generator; CFG and CDG are small
    today, which is not a reason for them to answer differently."""
    get = getattr(live_analysis, accessor)
    whole = get(busy_callable, page_size=10_000)
    first = get(busy_callable, page_size=3)
    assert first.total == whole.total
    assert first.edges == whole.edges[:3]
    assert (first.next_cursor is None) == (whole.total <= 3)
    if first.next_cursor:
        assert get(busy_callable, page_size=3, cursor=first.next_cursor).edges == whole.edges[3:6]


@live_only
def test_a_page_size_below_one_is_refused(live_analysis, busy_callable):
    with pytest.raises(ValueError):
        live_analysis.get_ddg(busy_callable, page_size=0)


def test_local_pages_are_the_emitter_rows_sliced_by_the_same_order(local_l4):
    """The local half of the boundary proof.

    ``test_the_local_answer_is_edge_for_edge_what_the_graph_would_hold`` established that the local
    backend's edge *set* equals the rows the emitter writes to Neo4j. This adds the ordering: sort
    those rows by the same key and cut them into pages, and the local backend's pages are cut in
    exactly the same places. Together with the live test above — that the server's ``ORDER BY`` is
    that same key — the two backends agree page for page.
    """
    rows = RowBuilder()
    # 1.4.1 anchors ghost ids on the application's can:// id; the analyzer names the app after
    # the project directory (codeanalyzer/core.py: `app_name or project_dir.name`).
    _project_program_graphs(rows, local_l4.application, {}, {}, application_id(local_l4.project_dir.name))
    emitted = sorted((e.from_ref.value, e.to_ref.value, e.props.get("var") or "", list(e.props.get("prov") or [])) for e in rows.finish().edges if e.type == "PY_DDG")
    walked, cursor, pages = [], None, []
    while True:
        page = local_l4.get_ddg("Portal.charge", page_size=3, cursor=cursor)
        assert page.total == len(emitted)
        pages.append([ddg_sort_key(e) for e in page.edges])
        walked += page.edges
        cursor = page.next_cursor
        if cursor is None:
            break
    assert pages == [emitted[i : i + 3] for i in range(0, len(emitted), 3)]
    assert len(walked) == len(emitted)


def test_the_local_sort_key_is_total_on_a_real_analyzer_run(local_l4):
    """Keyset paging resumes *after* a key, so a repeated key would drop its twin. Both backends
    are safe only while the key is unique; on the live graph it is (measured: 0 duplicate tuples
    across all 5,134,655 PY_DDG, 247,906 PY_CFG_NEXT and 139,065 PY_CDG edges, and the emitter's
    MERGE makes it structurally so there), and this is the same claim for the local path, where
    nothing dedupes."""
    for get, key in ((local_l4.get_cfg, cfg_sort_key), (local_l4.get_cdg, cdg_sort_key), (local_l4.get_ddg, ddg_sort_key)):
        edges = get("Portal.charge", page_size=10_000).edges
        keys = [str(key(e)) for e in edges]
        assert len(set(keys)) == len(keys)


def test_a_whole_answer_says_so_and_a_partial_one_does_not(local_l4):
    """``complete`` distinguishes "this is everything" from "there is more" from one page (E5), and an
    empty page from a level-4 analysis still means "no dependence" (D7)."""
    whole = local_l4.get_ddg("Portal.charge", page_size=10_000)
    assert whole.next_cursor is None and whole.complete
    assert whole.total == len(whole.edges)
    first = local_l4.get_ddg("Portal.charge", page_size=1)
    assert not first.complete and first.total == whole.total and len(first.edges) == 1


def test_a_cursor_from_somewhere_else_is_refused_rather_than_answered(local_l4):
    """A cursor is a position in *one* callable's *one* order, and using it anywhere else is a
    silent wrong answer waiting to happen: body-node ids sort by callable id, so another
    callable's cursor would not error, it would skip everything or nothing and look like a page.

    So the cursor carries the callable it was minted for, and its length identifies the accessor —
    the three orders have arities 3, 2 and 4, so no cursor is quietly valid for the wrong graph.
    """
    sig = local_l4.resolve_callable("Portal.charge").callable
    with pytest.raises(ValueError, match="components"):  # a CFG cursor has 3; the DDG order has 4
        local_l4.get_ddg("Portal.charge", cursor=local_l4.get_cfg("Portal.charge", page_size=1).next_cursor)

    mine = local_l4.get_ddg("Portal.charge", page_size=1).next_cursor
    stolen = encode_cursor("other.module.Other.method", decode_cursor(mine, sig, len(DDG_ORDER.exprs)))
    with pytest.raises(ValueError, match="other.module.Other.method"):
        local_l4.get_ddg("Portal.charge", cursor=stolen)


# ==============================================================================================
# Task 6 — slices and reachability.
#
# WHAT THE MEASUREMENT DECIDED. The sibling accessors above paginate; these cap. The reason is
# the measured shape of a slice on odoo-slim-19, over the five SDG relationship types
# (PY_DDG 5,134,655 / PY_CDG 139,065 / PY_PARAM_IN 229,035 / PY_PARAM_OUT 133,267 /
# PY_SUMMARY 453,398 -- all five present, verified against ``CALL db.relationshipTypes()``):
#
#   slice_backward over 200 random ``formal_in`` seeds:  median 1, p95 195,790, max 196,117
#   slice_backward over 150 seeds that HAVE callers:     median 195,786, p95 195,830, max 196,180
#   slice_forward  over the same 200 random seeds:       median 1, p95 440,270, max 440,662
#
# of 885,218 body nodes total. The distribution is BIMODAL, not long-tailed: a slice is either a
# handful of nodes or a fifth (backward) to a half (forward) of the whole application, with
# almost nothing in between. A page is not a useful unit over that shape -- page 3 of 20 of a
# 196,000-node cone answers no question anyone asked -- and, unlike an EdgePage cursor, a slice
# cursor cannot be cheap: an edge page is a keyset position in an order the database already
# maintains, while a slice IS the traversal, so every page would re-run the whole closure.
#
# So the cap stays, and ``total`` is what makes it honest: the whole slice's size is reported on
# a capped result, so "here are 10,000 of 195,784" is one call and the caller learns the question
# was too broad rather than walking twenty pages to find out. ``complete`` is derived from those
# two rather than stored, so they cannot disagree.
# ==============================================================================================
HEAVY_STATEMENT_SLICE = 195_785  # backward slice of any statement in configurator_apply
HEAVY_FORWARD_SLICE = 440_270   # forward slice of its ``kwargs`` -- half the application


@live_only
def test_backward_slice_of_a_parameter(live_analysis, busy_callable):
    sl = live_analysis.slice_backward("invoice_id", within=busy_callable)
    assert sl.nodes, "a used parameter has a backward slice"
    assert sl.root.name == "invoice_id"
    assert sl.resolved, "the result says what the name matched"
    assert sl.complete
    assert all("can://" not in n.callable for n in sl.nodes)


@live_only
def test_a_parameter_of_an_uncalled_callable_has_only_itself_behind_it(live_analysis, busy_callable):
    """The exact set, not merely a non-empty one.

    ``invoice_transaction`` is an HTTP route: measured on odoo-slim-19 it has no ``PY_CALLS``
    predecessors, so no ``actual_in`` feeds its ``formal_in`` and the backward slice of every one
    of its ten entering values is the seed alone. A bug returning the whole application, or the
    forward slice by mistake, fails here; ``assert sl.nodes`` would pass either.
    """
    sl = live_analysis.slice_backward("invoice_id", within=busy_callable)
    assert [n.ref for n in sl.nodes] == [sl.root.ref]
    assert sl.total == 1 and sl.complete


@live_only
def test_forward_slice_goes_where_the_value_goes(live_analysis, busy_callable):
    """Measured: 50 nodes, and every one of them addressed in the caller's vocabulary.

    ``depth=None`` because the claim is about the *whole* forward cone — Task 6 got that by
    default and this now has to ask for it."""
    sl = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None)
    assert sl.total == 50 and len(sl.nodes) == 50 and sl.complete
    assert all("can://" not in n.callable and "can://" not in (n.name or "") for n in sl.nodes)
    assert sl.root in sl.nodes, "a slice contains its seed"


@live_only
def test_slice_respects_max_nodes_and_says_what_it_dropped(live_analysis, busy_callable):
    """The plan wrote this against ``slice_backward``; that seed's backward slice is one node
    (the test above measures it), so a cap could never fire on it. The direction with something
    to cap is the forward one, and the claim under test — a cap that fires is visible, and the
    result says how much it left behind — is the same either way."""
    sl = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None, max_nodes=2)
    assert len(sl.nodes) <= 2
    assert sl.complete is False, "a cap that fires must be visible"
    assert sl.total == 50, "and the result says how big the whole answer was"


@live_only
def test_a_capped_slice_is_a_prefix_of_the_uncapped_one(live_analysis, busy_callable):
    """Which nodes a cap keeps is stated, not incidental: the slice is ordered by node id — the
    one total order both backends can compute — and the cap takes a prefix of it. Without that,
    two calls with the same arguments could return different subsets of the same slice."""
    whole = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None)
    capped = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None, max_nodes=5)
    assert [n.ref for n in capped.nodes] == [n.ref for n in whole.nodes][:5]


@live_only
def test_depth_bounds_a_slice_without_capping_it(live_analysis, busy_callable):
    """``depth`` is the bound that gives a *complete* answer to a narrower question; ``max_nodes``
    only ever gives a partial answer to the broad one. A caller who hits the cap is meant to
    reach for this."""
    near = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=2)
    whole = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None)
    assert near.total == 22 and near.complete
    assert {n.ref for n in near.nodes} < {n.ref for n in whole.nodes}


@live_only
def test_the_pathological_slice_is_bounded_and_reports_its_true_size(live_analysis):
    """The case the cap exists for, on the callable this leg keeps returning to.

    ``kwargs`` of ``configurator_apply`` reaches 440,270 nodes — **half** of the application's
    885,218 body nodes — and 27% of its DDG edges live in this one callable. Ten nodes come back,
    and the result says how many there were, in one call and in about a second. Backward from the
    same value is one node, because nothing calls it: the two directions of the same seed differ
    by five orders of magnitude, which is the distribution the cap exists for."""
    fwd = live_analysis.slice_forward("kwargs", within=HEAVY_CALLABLE, depth=None, max_nodes=10)
    assert len(fwd.nodes) == 10 and fwd.total == HEAVY_FORWARD_SLICE and not fwd.complete
    back = live_analysis.slice_backward("kwargs", within=HEAVY_CALLABLE, depth=None)
    assert back.total == 1 and back.complete


#: A *global* the callable reads, in a callable nothing about this test needs to be heavy: its
#: backward slice is 195,790 nodes unbounded and 76 at the default depth. The callable name is
#: unique in the application, so the seed is addressable the way a caller would say it.
DEPTH_SEED_CALLABLE = "odoo.tools.mail.email_domain_extract"
DEPTH_SEED_VALUE = "found_email"
DEPTH_SEED_AT_DEFAULT = 76
DEPTH_SEED_UNBOUNDED = 195_790


@live_only
def test_the_default_depth_answers_completely_where_unbounded_truncates(live_analysis):
    """The reason the default is finite (Task 6.1). Unbounded, this seed's backward slice is a
    fifth of the application and the caller gets 10,000 arbitrary nodes of it — 5% of a closure,
    flagged incomplete and useless. At the default depth the same call answers the narrower
    question *completely*: a small slice, ``total`` equal to what came back, ``complete`` True.
    Both halves are asserted here, because the change is worth nothing if the second one moves."""
    near = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE)
    assert near.total == DEPTH_SEED_AT_DEFAULT
    assert len(near.nodes) == near.total and near.complete

    whole = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE, depth=None)
    assert whole.total == DEPTH_SEED_UNBOUNDED, "depth=None still means the whole closure"
    assert len(whole.nodes) == 10_000 and not whole.complete
    assert {n.ref for n in near.nodes} <= {n.ref for n in whole.nodes} or near.total < whole.total


@live_only
def test_the_default_depth_is_five_hops_and_says_so(live_analysis):
    """``DEFAULT_DEPTH`` is not a private constant: a caller who wants the same bound explicitly,
    or who wants to step out one hop from it, has to be able to name it."""
    assert DEFAULT_DEPTH == 5
    explicit = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE, depth=DEFAULT_DEPTH)
    implicit = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE)
    assert [n.ref for n in explicit.nodes] == [n.ref for n in implicit.nodes]


@live_only
def test_a_slice_stays_inside_the_application(live_analysis, busy_callable):
    """The traversal is not scoped by ``_module`` the way the per-callable accessors are, because
    a body-node id is stamped with its application and the emitter only ever links nodes from one
    run — so an edge cannot leave the application. Checked rather than asserted."""
    sl = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None)
    known = set(live_analysis.backend._modules)
    assert {n.file for n in sl.nodes} <= known


@live_only
def test_an_ambiguous_within_raises_rather_than_slicing_one_of_them(live_analysis):
    with pytest.raises(AmbiguousName):
        live_analysis.slice_backward("vals", within="write")


@live_only
def test_reaches_is_boolean_and_cheap(live_analysis, busy_callable):
    assert live_analysis.reaches(busy_callable, busy_callable) in (True, False)


@live_only
def test_reaches_agrees_with_the_call_graph_it_summarises(live_analysis, busy_callable):
    """``reaches`` is a call-path question (spec § 6: callable → callable), so it must agree with
    the call graph a caller could walk itself — a boolean that disagrees with the edges is worse
    than no boolean."""
    declared = [c.callable for c in live_analysis.callees_of(busy_callable) if c.kind == "callable"]
    assert declared, "the busy callable calls something declared"
    for callee in declared:  # every one, not whichever the graph happened to return first
        assert live_analysis.reaches(busy_callable, callee)
        assert live_analysis.reaches(busy_callable, callee, depth=1), "a direct callee is one hop away"


@live_only
def test_backward_cone_from_sinks(live_analysis, busy_callable):
    cone = live_analysis.backward_cone([busy_callable])
    assert cone.nodes
    assert all(n.kind in ("callable", "external") for n in cone.nodes)


@live_only
def test_a_cone_contains_its_sinks_and_everything_that_reaches_them(live_analysis):
    """Measured: nothing calls ``invoice_transaction`` (it is an HTTP route), so its cone is
    itself; ``_process_transaction``, which it calls, has at least that one caller in its cone."""
    alone = live_analysis.backward_cone(["PaymentPortal.invoice_transaction"])
    assert [n.callable for n in alone.nodes] == [n.callable for n in alone.roots]

    reached = live_analysis.backward_cone(["PaymentPortal._process_transaction"])
    assert "addons.account_payment.controllers.payment.PaymentPortal.invoice_transaction" in {n.callable for n in reached.nodes}


@live_only
def test_a_cone_of_an_ambiguous_sink_raises(live_analysis):
    with pytest.raises(AmbiguousName):
        live_analysis.backward_cone(["write"])


@live_only
def test_callers_of_takes_a_bare_name(live_analysis):
    cs = live_analysis.callers_of("action_validate_step")
    assert all(isinstance(c.callable, str) for c in cs)
    assert all("can://" not in c.callable for c in cs)


@live_only
def test_callers_of_ambiguous_name_raises(live_analysis):
    with pytest.raises(AmbiguousName):
        live_analysis.callers_of("write")


@live_only
def test_callees_of_reports_external_callees_as_external(live_analysis, busy_callable):
    """A call to something outside the application is 10% of this graph's call edges (38,585 of
    370,110) and is exactly what a caller tracing a sink is looking for, so dropping it would be
    the ambiguous empty again. It comes back ``kind="external"`` with a readable dotted name built
    from the node's own ``module``/``name`` properties — never a ``can://`` id — and with no
    position, because an external was never analysed."""
    cs = live_analysis.callees_of(busy_callable)
    kinds = {c.kind for c in cs}
    assert kinds == {"callable", "external"}
    assert all("can://" not in c.callable for c in cs)
    ext = [c for c in cs if c.kind == "external"]
    assert any(c.callable == "odoo.exceptions.ValidationError.__init__" for c in ext)
    assert all(c.file == "" and c.line == 0 for c in ext), "an external has no position, and says so"


@live_only
def test_callees_of_and_callers_of_are_inverses(live_analysis, busy_callable):
    """Two accessors over one relationship: if A is a callee of B then B is a caller of A."""
    sig = live_analysis.backend.resolve_callable(busy_callable).callable
    for callee in [c for c in live_analysis.callees_of(busy_callable) if c.kind == "callable"]:
        assert sig in {c.callable for c in live_analysis.callers_of(callee.callable)}


# ----------------------------------------------------------------------------------------------
# The local backend answers the same questions -- interprocedurally, which was not obvious.
#
# It holds cfg/cdg/ddg per callable and has no cross-callable index, so the honest expectation was
# a Diagnostic. But a level-4 run carries the whole SDG in the model: PyApplication.param_in /
# param_out (endpoints already global ids) and PyCallable.summary, the very lists
# codeanalyzer.neo4j.project projects as PY_PARAM_IN / PY_PARAM_OUT / PY_SUMMARY. So the index it
# lacks it can BUILD, from the same data the graph is emitted from, and the answer is the graph's.
# ----------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def two_callable_project(tmp_path_factory):
    """A caller and a callee, so the interprocedural edges exist at all: ``charge`` derives a value
    from ``invoice_id`` and passes it to ``helper``, which is a ``PY_PARAM_IN`` in the graph's
    vocabulary.

    The argument is ``total`` and not ``invoice_id`` itself for a measured reason. When a parameter
    is forwarded *verbatim* the analyzer attributes the argument's reaching definition to the
    callable's ``@entry`` vertex rather than to the ``formal_in`` one -- so ``formal_in:1`` has no
    forward edge to the ``actual_in`` at all, and a forward interprocedural path from the parameter
    does not exist to be found. (The backward direction still crosses, which is why Task 6's slice
    tests never noticed.) One derived statement in between restores the shape the live graph has,
    where ``invoice_id`` -> statement -> ``actual_in`` -> ``formal_in`` is three hops."""
    root = tmp_path_factory.mktemp("slice")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            LIMIT = 100


            def helper(x):
                return x + 1


            def alone(a, b):
                total = a + b
                if total > LIMIT:
                    total = total - 1
                return total


            class Portal:
                def charge(self, invoice_id):
                    total = invoice_id * 2
                    amount = helper(total)
                    return amount
            """
        ).lstrip()
    )
    return root


@pytest.fixture(scope="module")
def slice_l4(two_callable_project, tmp_path_factory) -> PyCodeanalyzer:
    return _backend(two_callable_project, tmp_path_factory.mktemp("cache-slice"), AnalysisLevel.system_dependency_graph)


def test_a_forward_slice_is_exactly_the_reachable_set_of_the_published_edges(slice_l4):
    """The correctness anchor, computed from the accessors a caller already has rather than from
    the implementation.

    ``alone`` calls nothing, so its only dataflow is its own DDG and CDG — both published by
    ``get_ddg`` / ``get_cdg``. The forward slice of ``a`` must therefore be *exactly* the forward
    reachable set of those two edge lists from ``a``'s vertex, no more and no fewer. A slice that
    leaked into the rest of the module, or that returned the callable's whole body, fails here.
    """
    ddg = slice_l4.get_ddg("alone", page_size=100_000).edges
    cdg = slice_l4.get_cdg("alone", page_size=100_000).edges
    succ: dict[str, set[str]] = {}
    for e in [*ddg, *cdg]:
        succ.setdefault(e.src, set()).add(e.dst)

    root = slice_l4.resolve_value("a", within="alone")
    expected, frontier = {root.ref}, [root.ref]
    while frontier:
        nxt = [d for s in frontier for d in succ.get(s, ()) if d not in expected]
        expected.update(nxt)
        frontier = nxt

    sl = slice_l4.slice_forward("a", within="alone", depth=None)
    assert {n.ref for n in sl.nodes} == expected
    assert sl.total == len(expected) and sl.complete


def test_a_backward_slice_of_an_uncalled_parameter_is_the_seed_alone(slice_l4):
    """The other exact set. ``alone`` has no callers, so nothing feeds its ``formal_in``: the
    answer is one node, and any leak shows up as more."""
    sl = slice_l4.slice_backward("a", within="alone")
    assert [n.ref for n in sl.nodes] == [sl.root.ref]
    assert sl.total == 1


def test_the_local_backend_crosses_callables(slice_l4):
    """What the graph does with PY_PARAM_IN, the local backend does with ``PyApplication.param_in``
    — the same edges, before they were emitted. ``helper``'s parameter is fed by ``charge``'s
    argument, so the backward slice of ``x`` leaves ``helper``."""
    sl = slice_l4.slice_backward("x", within="helper")
    assert len(sl.nodes) > 1, "a called parameter has its callers behind it"
    assert {n.callable for n in sl.nodes} == {"src.pay.helper", "src.pay.Portal.charge"}
    # The argument vertex is named for the *formal* it binds, not for the expression written at
    # the call site — that is ``BodyNode.of`` on an ``actual_in``, and it is the analyzer's, not a
    # choice made here. Worth pinning: a reader expecting "invoice_id" is reading it wrong.
    assert any(n.kind == "argument" and n.name == "x" for n in sl.nodes)


def test_no_analyzer_marker_reaches_the_caller(slice_l4):
    """E6, on the fields a slice actually fills. ``<return>`` is the ``var`` of every
    ``formal_out``/``actual_out`` vertex — 33,223 of them on a real application — and it is the
    analyzer's spelling, not a name; a returned value has none, and ``kind="return"`` is what
    identifies it. ``<global>:`` and ``<capture>:`` are translated by the same function."""
    nodes = [*slice_l4.slice_forward("a", within="alone").nodes, *slice_l4.slice_backward("x", within="helper").nodes]
    assert nodes
    assert not [n for n in nodes if n.name and n.name.startswith("<")]
    assert any(n.kind == "return" and n.name is None for n in nodes)


def test_local_slices_are_capped_and_say_so(slice_l4):
    whole = slice_l4.slice_forward("a", within="alone")
    capped = slice_l4.slice_forward("a", within="alone", max_nodes=2)
    assert len(capped.nodes) == 2 and not capped.complete
    assert capped.total == whole.total
    assert [n.ref for n in capped.nodes] == [n.ref for n in whole.nodes][:2]


def test_local_call_graph_questions(slice_l4):
    assert [c.callable for c in slice_l4.callers_of("helper")] == ["src.pay.Portal.charge"]
    assert "src.pay.helper" in {c.callable for c in slice_l4.callees_of("Portal.charge")}
    assert slice_l4.reaches("Portal.charge", "helper")
    assert not slice_l4.reaches("helper", "Portal.charge")
    cone = slice_l4.backward_cone(["helper"])
    assert {n.callable for n in cone.nodes} == {"src.pay.helper", "src.pay.Portal.charge"}


@pytest.mark.parametrize("call", [
    lambda b: b.slice_backward("invoice_id", within="Portal.charge"),
    lambda b: b.slice_forward("invoice_id", within="Portal.charge"),
])
def test_slices_need_dataflow_and_reuse_the_one_level_guard(local_l2, call):
    """Slicing needs the same analyzer pass ``get_ddg`` needs, so it raises through the same
    guard rather than a second one that could drift from it — and it raises *before* resolving,
    so a level-2 analysis says "no dataflow here" rather than "no such value"."""
    with pytest.raises(CodeanalyzerUsageException) as e:
        call(local_l2)
    assert "program_dependency_graph" in str(e.value)


@pytest.mark.parametrize("call", [
    lambda b: b.callers_of("Portal.charge"),
    lambda b: b.callees_of("Portal.charge"),
    lambda b: b.reaches("Portal.charge", "Portal.charge"),
    lambda b: b.backward_cone(["Portal.charge"]),
])
def test_the_call_graph_questions_do_not_need_dataflow(local_l2, call):
    """The other half of the guard, and the reason it is not on all six: ``callers_of`` /
    ``callees_of`` / ``reaches`` / ``backward_cone`` are call-graph questions, and the call graph
    exists from level 2. Guarding them on the dataflow level would refuse an analysis that can
    answer perfectly well."""
    assert call(local_l2) is not None


def test_a_slice_is_a_set_not_a_sequence(slice_l4):
    """E2. No duplicates, and the order is the id order the cap is a prefix of — not a traversal
    order a caller could mistake for a path."""
    sl = slice_l4.slice_forward("a", within="alone")
    refs = [n.ref for n in sl.nodes]
    assert len(set(refs)) == len(refs)
    assert refs == sorted(refs)


def test_a_bounded_cone_walks_backwards_like_the_unbounded_one(slice_l4):
    """The direction of a *bounded* cone, which nothing pinned while the default was unbounded.
    ``charge`` calls ``helper``, so it is one hop **back** from it; a bounded walk that followed
    successors instead would return the sink alone and look like a small honest answer."""
    assert {n.callable for n in slice_l4.backward_cone(["helper"], depth=1).nodes} == {"src.pay.helper", "src.pay.Portal.charge"}
    assert {n.callable for n in slice_l4.backward_cone(["helper"]).nodes} == {"src.pay.helper", "src.pay.Portal.charge"}
    assert slice_l4.backward_cone(["Portal.charge"], depth=1).total == 1, "nothing calls charge"


def test_a_multi_sink_cone_has_no_single_root(slice_l4):
    """``root`` is the singular convenience for the single-seed accessors; a cone over several
    sinks has ``roots`` and asking it for one raises rather than silently answering with the
    first."""
    cone = slice_l4.backward_cone(["helper", "alone"])
    assert len(cone.roots) == 2
    with pytest.raises(ValueError):
        cone.root


def test_a_bad_depth_is_refused(slice_l4):
    for bad in (0, -1, 2.5, True, "2"):
        with pytest.raises(ValueError):
            slice_l4.slice_forward("a", within="alone", depth=bad)


def test_max_nodes_below_one_is_refused(slice_l4):
    with pytest.raises(ValueError):
        slice_l4.slice_forward("a", within="alone", max_nodes=0)


# ----------------------------------------------------------------------------------------------
# Task 7: paths, mixed flow queries, hydration.
#
# The plan wrote four tests against a code shape the graph does not have, and both mistakes are
# recorded here rather than papered over, because each names a real property of the answer:
#
#   * ``paths_between("invoice_id", "kwargs", within=<one callable>)`` cannot find anything and
#     never could. The only dataflow edge into a ``formal_in`` is ``PY_PARAM_IN`` from a *caller's*
#     argument (229,035 of them on this graph, and no other kind), so two values entering the same
#     callable are joined only through a cycle of calls. The real question is the cross-callable
#     one, which is why ``paths_between`` grew ``dst_within``; the plan's own ``if paths:`` guard
#     was the tell.
#   * ``_create_transaction`` has no parameter called ``invoice_id`` (it has eighteen entering
#     values and that is not one), so the plan's ``flows_to_argument`` call raises rather than
#     answering ``False`` -- correctly, because a mistyped argument name is a caller error and not
#     a negative result.
#
# Both replacements are read off the live graph, never written by hand.
# ----------------------------------------------------------------------------------------------

#: A flow that exists, measured: ``invoice_id`` enters the HTTP route ``invoice_transaction``, is
#: used by the statement at line 27, and is passed as ``invoice_ids`` to ``_process_transaction``.
#: Six distinct shortest paths of three hops each -- they differ only in which parallel ``PY_DDG``
#: edge carries the middle hop, which is exactly why the adjacency has to keep parallel edges.
FLOW_SRC, FLOW_DST = "invoice_id", "invoice_ids"
FLOW_FROM, FLOW_TO = "PaymentPortal.invoice_transaction", "PaymentPortal._process_transaction"
FLOW_PATHS, FLOW_HOPS = 6, 3


@live_only
def test_paths_carry_ordered_hops_with_evidence(live_analysis):
    paths = live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO, max_paths=3)
    assert paths, "a flow that exists comes back as paths"
    p = paths[0]
    assert p.hops, "a path is a sequence of hops"
    assert all(h.via for h in p.hops), "every hop says what justified it"
    assert p.weakest in p.hops


@live_only
def test_a_known_flow_matches_hop_for_hop(live_analysis):
    """The correctness anchor, and the reason ``assert paths`` above is not the test.

    Every hop of the shortest path is pinned -- the edge kind in the caller's vocabulary, the
    variable, the provenance, and what each endpoint *is*. A path that reached the right node by
    the wrong route, or that reported ``PY_PARAM_IN`` as ``data``, fails here.
    """
    p = live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO)[0]
    assert [(h.via, h.var, tuple(h.prov)) for h in p.hops] == [
        ("data", "invoice_id", ("reaching-defs",)),
        ("data", "invoice_id", ("reaching-defs",)),
        ("argument", None, ()),
    ]
    assert [(h.to.kind, h.to.name) for h in p.hops] == [("statement", None), ("argument", "invoice_ids"), ("parameter", "invoice_ids")]
    assert p.hops[0].frm.kind == "parameter" and p.hops[0].frm.name == "invoice_id"
    assert p.hops[-1].to.callable.endswith("._process_transaction"), "the last hop crosses into the callee"


@live_only
def test_a_path_is_a_joined_sequence(live_analysis):
    """E2, structurally: consecutive hops share an endpoint, so the hops are a *walk* and not a
    bag of edges that happen to mention the same nodes."""
    for p in live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO):
        assert len(p.hops) == FLOW_HOPS
        assert all(a.to.ref == b.frm.ref for a, b in zip(p.hops, p.hops[1:]))


@live_only
def test_max_paths_is_a_reproducible_prefix_and_says_when_it_cut(live_analysis):
    """E5 on a path list. The order is ``hop_sort_key``'s -- shortest first, then hop by hop on
    ``(via, var, to.ref)`` -- so a cap takes a *prefix* of a stated order rather than whichever
    paths the database returned first, and ``complete`` says whether a cap fired."""
    whole = live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO, max_paths=100)
    assert len(whole) == FLOW_PATHS and whole.complete
    capped = live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO, max_paths=2)
    assert capped.complete is False
    keys = [hop_sort_key(p.hops) for p in whole]
    assert [hop_sort_key(p.hops) for p in capped] == keys[:2]
    assert keys == sorted(keys), "the order is hop_sort_key's, not an arrival order"


@live_only
def test_a_self_question_is_refused_on_the_graph_too(live_analysis):
    """The same refusal, and the reason it is the SDK's and not the driver's: unguarded, Neo4j
    answers this with a raw ``DatabaseError`` ("the shortest path algorithm does not work when the
    start and end nodes are the same")."""
    with pytest.raises(ValueError) as e:
        live_analysis.paths_between(FLOW_SRC, FLOW_SRC, src_within=FLOW_FROM, dst_within=FLOW_FROM)
    assert "reaches" in str(e.value)
    with pytest.raises(ValueError):
        live_analysis.call_paths_between(FLOW_FROM, FLOW_FROM)


@live_only
def test_depth_bounds_a_path_query_and_the_bound_is_nameable(live_analysis):
    """A bounded search that found nothing must be distinguishable from no flow, which is why
    ``depth`` is an argument and not a constant inside the query."""
    assert not live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO, depth=2)
    assert live_analysis.paths_between(FLOW_SRC, FLOW_DST, src_within=FLOW_FROM, dst_within=FLOW_TO, depth=FLOW_HOPS)


@live_only
def test_call_paths_carry_the_call_graph_route(live_analysis):
    """The same shape over ``PY_CALLS``: hops are ``call``, carry no variable and no provenance,
    and the route agrees with ``reaches``."""
    paths = live_analysis.call_paths_between(FLOW_FROM, "AccountMove.write")
    assert paths
    for p in paths:
        assert {h.via for h in p.hops} == {"call"}
        assert all(h.var is None and h.prov == [] for h in p.hops)
        assert all("can://" not in h.to.callable for h in p.hops)
        for hop in p.hops:  # every edge on the route is one the call graph publishes
            assert live_analysis.reaches(hop.frm.callable, hop.to.callable, depth=1)
    assert live_analysis.reaches(FLOW_FROM, "AccountMove.write")


@live_only
def test_flows_to_call_and_argument_are_different_questions(live_analysis):
    """Reaching a callee and reaching a named argument are not the same.

    Measured: ``invoice_id`` reaches six of ``_process_transaction``'s seven entering values and
    not ``kwargs``. An implementation that collapsed the two questions would report ``kwargs`` as
    reached, which is the over-report the plan asked to be prevented by construction.
    """
    assert live_analysis.flows_to_call(FLOW_SRC, FLOW_TO, within=FLOW_FROM) is True
    assert live_analysis.flows_to_argument(FLOW_SRC, FLOW_TO, arg=FLOW_DST, within=FLOW_FROM) is True
    assert live_analysis.flows_to_argument(FLOW_SRC, FLOW_TO, arg="kwargs", within=FLOW_FROM) is False


@live_only
def test_reaching_an_argument_implies_reaching_the_call(live_analysis):
    """The implication, checked over **every** value ``_process_transaction`` takes rather than the
    one that happens to be interesting -- and checked to be structural, not coincidental: the
    argument's resolved node is one of the callable's own entry vertices, which is exactly the set
    ``flows_to_call`` tests reachability of."""
    entering = _formal_ins(live_analysis, FLOW_TO)
    assert len(entering) == 7
    reaches_call = live_analysis.flows_to_call(FLOW_SRC, FLOW_TO, within=FLOW_FROM)
    for row in entering:
        arg = live_analysis.backend.resolve_value(value_candidate(row["var"]).name, within=FLOW_TO)
        assert arg.ref in {r["id"] for r in entering}, "an argument resolves to one of the callee's own entry vertices"
        if live_analysis.flows_to_argument(FLOW_SRC, FLOW_TO, arg=value_candidate(row["var"]).name, within=FLOW_FROM):
            assert reaches_call, "reaching an argument implies reaching the call"


def _formal_ins(analysis, callable_name):
    """The callee's entry vertices, read off the graph rather than assumed."""
    sig = analysis.backend.resolve_callable(callable_name).callable
    return analysis.backend._run(
        "MATCH (c:PyCallable {signature:$sig})-[:PY_HAS_BODY_NODE]->(b:PyBodyNode {kind:'formal_in'}) RETURN b.var AS var, b.id AS id ORDER BY b.id",
        sig=sig,
    )


@live_only
def test_an_argument_that_names_nothing_raises_rather_than_answering_false(live_analysis):
    """``_create_transaction`` has no ``invoice_id`` (the plan assumed it did). A mistyped argument
    is a caller error; answering ``False`` would let it look like a proved absence of flow."""
    with pytest.raises(SelectorNotInGraph):
        live_analysis.flows_to_argument(FLOW_SRC, "_create_transaction", arg="invoice_id", within=FLOW_FROM)


@live_only
def test_describe_populates_source_only_when_asked(live_analysis):
    """A slice does not carry code, and ``describe`` fills in what the backend has text for.

    The plan hydrated a *slice* and expected text; over Neo4j there is none to have, because every
    node of that slice is a value vertex or a statement and the graph carries no text below
    callable granularity. So the "fills it in" half is asserted on callable-granularity nodes --
    a ``backward_cone`` -- and the value half is asserted to stay ``None`` on purpose.
    """
    sl = live_analysis.slice_backward(FLOW_SRC, within=FLOW_FROM)
    assert all(n.source is None for n in sl.nodes), "a slice does not carry code"
    assert all(n.source is None for n in live_analysis.describe(sl.nodes)), "a value vertex has no source to fill"

    cone = live_analysis.backward_cone([FLOW_TO])
    assert all(n.source is None for n in cone.nodes)
    hydrated = live_analysis.describe(cone.nodes)
    assert any(n.source for n in hydrated), "describe fills it in"
    assert [n.ref for n in hydrated] == [n.ref for n in cone.nodes], "same nodes, same order, same type"


@live_only
def test_describe_is_one_round_trip(live_analysis, count_round_trips):
    sl = live_analysis.slice_forward(FLOW_SRC, within=FLOW_FROM, depth=None)
    assert len(sl.nodes) > 1
    n = count_round_trips(live_analysis)
    live_analysis.describe(sl.nodes)
    assert n["c"] == 1, f"describe took {n['c']} round trips for {len(sl.nodes)} nodes"


@live_only
def test_describe_accepts_a_locate_result(live_analysis):
    """"Anything carrying a ref" is not a slogan: ``locate()`` returns a different type with a
    different field name, and converting between shapes to hydrate one is the friction that gets
    worked around with string surgery."""
    found = live_analysis.locate("addons/account_payment/controllers/payment.py", 27)
    assert found.node_id
    described = live_analysis.describe([found])
    assert [n.ref for n in described] == [found.node_id]
    assert described[0].callable == found.callable.signature


@live_only
def test_describe_raises_on_a_ref_that_names_nothing(live_analysis):
    """The other half of "source=None means exactly one thing". A ref comes from this SDK, so one
    that resolves to nothing was minted against a different application -- a defect to stop on, not
    a ``None`` to be discovered three layers later."""
    good = live_analysis.backward_cone([FLOW_TO]).nodes[0]
    with pytest.raises(KeyError):
        live_analysis.describe([good, good.model_copy(update={"ref": "can://python/nope/nothing.py/nope"})])


@live_only
def test_an_empty_describe_costs_nothing(live_analysis, count_round_trips):
    n = count_round_trips(live_analysis)
    assert live_analysis.describe([]) == []
    assert n["c"] == 0


@live_only
def test_prov_is_a_singleton_on_every_ddg_edge(live_analysis):
    """The measurement ``weakest`` rests on, re-checked against the graph rather than trusted.

    If a future analyzer generation started emitting several provenances on one edge, "the weakest
    hop" would become a question about how to *combine* a set, and :func:`prov_rank`'s conservative
    choice would start being observable.
    """
    rows = live_analysis.backend._run("MATCH ()-[r:PY_DDG]->() RETURN size(r.prov) AS n, count(*) AS c ORDER BY n")
    assert [(r["n"], r["c"]) for r in rows] == [(1, 5_134_655)]


def test_weakest_is_the_most_approximate_hop_not_the_alphabetically_first():
    """The ordering, pinned so it cannot be re-introduced backwards.

    ``ssa`` is exact def-use, ``reaching-defs`` over-approximates along the CFG, ``points-to`` is
    alias analysis and the most approximate of the three. So the *weakest* hop -- the one that caps
    how strongly a caller can state a flow -- is the ``points-to`` one, even though ``points-to``
    sorts last alphabetically and ``ssa`` would sort last of the three as a string.
    """
    assert prov_rank(["points-to"]) < prov_rank(["reaching-defs"]) < prov_rank(["ssa"]) < prov_rank([])

    def hop(prov):
        n = SliceNode(file="f.py", line=1, callable="m.f", kind="statement", name=None, ref=f"r{prov}")
        return PathHop(frm=n, to=n, via="data", var="x", prov=[prov] if prov else [])

    strong, mid, weak, structural = hop("ssa"), hop("reaching-defs"), hop("points-to"), hop(None)
    assert FlowPath(hops=[strong, weak, mid]).weakest is weak
    assert FlowPath(hops=[structural, strong]).weakest is strong, "an unlabelled hop claims no approximation"
    assert FlowPath(hops=[structural, structural]).weakest is structural, "all-structural: the first, deterministically"
    assert FlowPath(hops=[mid, strong, mid]).weakest is FlowPath(hops=[mid, strong, mid]).hops[0], "ties break on position"


# ----------------------------------------------------------------------------------------------
# The local backend answers the same five, over a real level-4 analyzer run.
# ----------------------------------------------------------------------------------------------
def test_local_paths_carry_the_interprocedural_hop(slice_l4):
    """``charge`` derives a value from ``invoice_id`` and passes it to ``helper``'s ``x``.

    Three hops, ending on the ``argument`` one -- the local backend's ``PyApplication.param_in`` is
    the graph's ``PY_PARAM_IN``, and both must report it in the caller's word. The same shape the
    live graph gives for ``invoice_id`` -> ``_process_transaction``, which is the point: the two
    backends are not agreeing on a predicate while walking different edges."""
    paths = slice_l4.paths_between("invoice_id", "x", src_within="Portal.charge", dst_within="helper")
    assert paths and paths.complete
    p = paths[0]
    assert [h.via for h in p.hops] == ["data", "data", "argument"]
    assert p.hops[0].frm.name == "invoice_id" and p.hops[-1].to.name == "x"
    assert p.hops[-1].to.callable == "src.pay.helper"
    assert all(a.to.ref == b.frm.ref for a, b in zip(p.hops, p.hops[1:]))


def test_local_paths_agree_with_the_edges_the_accessors_publish(slice_l4):
    """Correctness from the outside: every hop is an edge some accessor already hands the caller,
    so a path cannot claim a dependence the published edges deny.

    The intraprocedural hops must be in ``charge``'s own ``get_ddg``/``get_cdg``; the interprocedural
    one must be in ``PyApplication.param_in``, which is what the graph projects as ``PY_PARAM_IN``.
    """
    published = {(e.src, e.dst) for e in slice_l4.get_ddg("Portal.charge", page_size=100_000).edges}
    published |= {(e.src, e.dst) for e in slice_l4.get_cdg("Portal.charge", page_size=100_000).edges}
    crossing = {(e.src, e.dst) for e in slice_l4.application.param_in or []}
    paths = slice_l4.paths_between("invoice_id", "x", src_within="Portal.charge", dst_within="helper", depth=None)
    assert paths
    for p in paths:
        for h in p.hops:
            assert (h.frm.ref, h.to.ref) in (crossing if h.via == "argument" else published)

    assert len(slice_l4.paths_between("a", "b", src_within="alone", dst_within="alone")) == 0, "two parameters of one callable are not joined"
    assert [hop_sort_key(p.hops) for p in paths] == sorted(hop_sort_key(p.hops) for p in paths), "the same order as the graph's"


def test_local_call_paths_are_the_call_graph(slice_l4):
    paths = slice_l4.call_paths_between("Portal.charge", "helper")
    assert [[h.to.callable for h in p.hops] for p in paths] == [["src.pay.helper"]]
    assert [h.via for h in paths[0].hops] == ["call"]
    assert len(slice_l4.call_paths_between("helper", "Portal.charge")) == 0, "and it is directed"


@pytest.mark.parametrize("call", [
    lambda b: b.paths_between("a", "a", src_within="alone", dst_within="alone"),
    lambda b: b.call_paths_between("helper", "helper"),
])
def test_a_path_from_a_node_to_itself_is_refused_not_answered_empty(slice_l4, call):
    """Neo4j's shortest-path search refuses a self-question outright, so answering ``[]`` locally
    would be a backend disagreement *and* an ambiguous empty -- a node genuinely on a cycle would
    be reported the same as one that is not. ``reaches(x, x)`` is the accessor for that."""
    with pytest.raises(ValueError) as e:
        call(slice_l4)
    assert "reaches" in str(e.value)


def test_local_mixed_queries_separate_the_two_questions(slice_l4):
    assert slice_l4.flows_to_call("invoice_id", "helper", within="Portal.charge") is True
    assert slice_l4.flows_to_argument("invoice_id", "helper", arg="x", within="Portal.charge") is True
    assert slice_l4.flows_to_call("a", "helper", within="alone") is False, "alone calls nothing"


def test_local_describe_fills_in_what_the_graph_cannot(slice_l4):
    """The honest parity difference. A statement has a span in the model, so this backend can slice
    its text out of the module; the graph carries no per-statement text and returns ``None``. A
    value vertex has no span at all and is ``None`` on both."""
    sl = slice_l4.slice_forward("a", within="alone", depth=None)
    hydrated = slice_l4.describe(sl.nodes)
    assert [n.ref for n in hydrated] == [n.ref for n in sl.nodes]
    by_kind = {n.kind: n.source for n in hydrated}
    assert by_kind.get("statement"), "a statement has a span, and the local backend reads it"
    assert by_kind.get("parameter") is None, "a value vertex is not a region of the file"
    assert slice_l4.describe(slice_l4.callers_of("helper"))[0].source.startswith("def charge")


def test_local_describe_raises_on_a_ref_that_names_nothing(slice_l4):
    node = slice_l4.callers_of("helper")[0]
    with pytest.raises(KeyError):
        slice_l4.describe([node.model_copy(update={"ref": "can://python/nope/x.py/nope"})])


def test_describe_refuses_something_with_no_address(slice_l4):
    with pytest.raises(TypeError):
        slice_l4.describe(["src.pay.helper"])


def test_local_paths_need_dataflow_and_reuse_the_one_level_guard(local_l2):
    for call in (
        lambda b: b.paths_between("invoice_id", "x", src_within="Portal.charge", dst_within="helper"),
        lambda b: b.flows_to_call("invoice_id", "helper", within="Portal.charge"),
        lambda b: b.flows_to_argument("invoice_id", "helper", arg="x", within="Portal.charge"),
    ):
        with pytest.raises(CodeanalyzerUsageException) as e:
            call(local_l2)
        assert "program_dependency_graph" in str(e.value)


def test_local_call_paths_do_not_need_dataflow(local_l2):
    """``call_paths_between`` is a call-graph question and the call graph exists from level 2 --
    the same split ``reaches``/``callers_of`` already make."""
    with pytest.raises(ValueError) as e:  # resolved and walked, then refused for being a self-question
        local_l2.call_paths_between("Portal.charge", "Portal.charge")
    assert "reaches" in str(e.value) and "program_dependency_graph" not in str(e.value)


@pytest.mark.parametrize("call", [
    lambda b: b.paths_between("a", "b", src_within="alone", dst_within="alone", max_paths=0),
    lambda b: b.call_paths_between("Portal.charge", "helper", max_paths=0),
])
def test_max_paths_below_one_is_refused(slice_l4, call):
    with pytest.raises(ValueError):
        call(slice_l4)


@pytest.mark.parametrize("call", [
    lambda b: b.paths_between("a", "b", src_within="alone", dst_within="alone", depth=0),
    lambda b: b.call_paths_between("Portal.charge", "helper", depth=-1),
    lambda b: b.flows_to_call("a", "helper", within="alone", depth="2"),
])
def test_a_bad_depth_is_refused_by_the_path_accessors(slice_l4, call):
    with pytest.raises(ValueError):
        call(slice_l4)


# ----------------------------------------------------------------------------------------------
# Fix round: which accessors bound themselves by default, and why that is one rule, not five.
# ----------------------------------------------------------------------------------------------
BOUNDED_BY_DEFAULT = ("slice_backward", "slice_forward", "backward_cone")
UNBOUNDED_BY_DEFAULT = ("reaches", "paths_between", "call_paths_between", "flows_to_call", "flows_to_argument")


@pytest.mark.parametrize("cls", [PythonAnalysisBackend, PythonAnalysis, PyNeo4jBackend, PyCodeanalyzer])
def test_predicates_and_paths_are_unbounded_by_default_and_slices_are_not(cls):
    """A slice bounded at five hops is a complete answer to a narrower question; a predicate or
    a path list bounded at five hops is a wrong answer with no signal. Pinned on the ABC, the
    facade and both backends, so a default cannot drift on one of the four surfaces."""
    for name in UNBOUNDED_BY_DEFAULT:
        assert inspect.signature(getattr(cls, name)).parameters["depth"].default is None, f"{cls.__name__}.{name}"
    for name in BOUNDED_BY_DEFAULT:
        assert inspect.signature(getattr(cls, name)).parameters["depth"].default == DEFAULT_DEPTH, f"{cls.__name__}.{name}"


def test_the_rule_is_stated_once_and_cited_by_all_five():
    """The reasoning ``reaches`` gave for its unbounded default now lives on ``DEFAULT_DEPTH`` and
    names every accessor it applies to; each of the five points back at it rather than restating
    (or forgetting) it."""
    import cldk.analysis.python.backend as backend_module

    source = inspect.getsource(backend_module)
    rule = source[: source.index("\nDEFAULT_DEPTH = 5")]
    rule = rule[rule.rindex("#: Hops from the seed when the caller does not say") :]
    for name in UNBOUNDED_BY_DEFAULT + BOUNDED_BY_DEFAULT:
        assert name in rule, f"the DEFAULT_DEPTH rule does not name {name}"
    assert "wrong" in rule and "complete" in rule
    for name in UNBOUNDED_BY_DEFAULT:
        assert "DEFAULT_DEPTH" in getattr(PythonAnalysisBackend, name).__doc__, f"{name} does not cite the rule"


#: The flow that decided it: measured ``False`` at five hops and ``True`` unbounded.
HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, HEAVY_FLOW_WITHIN, HEAVY_FLOW_ARG = "kwargs", "Website.create", "Website.configurator_apply", "vals_list"


@live_only
def test_the_default_flow_answer_is_the_unbounded_one(live_analysis):
    """Before this round the default answered ``False`` for a flow that exists. The default must
    now equal ``depth=None``, and the slices' bound must be demonstrably the wrong answer here."""
    kw = dict(within=HEAVY_FLOW_WITHIN)
    assert live_analysis.flows_to_call(HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, **kw) is True
    assert live_analysis.flows_to_call(HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, depth=None, **kw) is True
    assert live_analysis.flows_to_call(HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, depth=DEFAULT_DEPTH, **kw) is False, "the slices' bound is a wrong answer on a boolean"
    assert live_analysis.flows_to_argument(HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, arg=HEAVY_FLOW_ARG, **kw) is True
    assert live_analysis.flows_to_argument(HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, arg=HEAVY_FLOW_ARG, **kw) == live_analysis.flows_to_argument(
        HEAVY_FLOW_SRC, HEAVY_FLOW_CALLEE, arg=HEAVY_FLOW_ARG, depth=None, **kw
    )


@live_only
def test_the_default_path_answer_is_the_unbounded_one(live_analysis):
    """The same flow as paths: ``[]`` at five hops with ``complete=True`` looked like a proved
    absence. The default is now the unbounded shortest paths, ten of them, flagged incomplete."""
    kw = dict(src_within=HEAVY_FLOW_WITHIN, dst_within=HEAVY_FLOW_CALLEE)
    default = live_analysis.paths_between(HEAVY_FLOW_SRC, HEAVY_FLOW_ARG, **kw)
    assert len(default) == DEFAULT_MAX_PATHS and not default.complete
    unbounded = live_analysis.paths_between(HEAVY_FLOW_SRC, HEAVY_FLOW_ARG, depth=None, **kw)
    assert [hop_sort_key(p.hops) for p in default] == [hop_sort_key(p.hops) for p in unbounded]
    bounded = live_analysis.paths_between(HEAVY_FLOW_SRC, HEAVY_FLOW_ARG, depth=DEFAULT_DEPTH, **kw)
    assert len(bounded) == 0 and bounded.complete, "the slices' bound would have reported no flow"
    assert inspect.signature(live_analysis.call_paths_between).parameters["depth"].default is None


# ----------------------------------------------------------------------------------------------
# Fix round: the caller's vocabulary in every message, and composition across accessors.
# ----------------------------------------------------------------------------------------------
def _follow_reaches_advice(analysis, message: str) -> bool:
    """The advice in a self-question refusal must be a call that runs: extract it and run it."""
    found = re.search(r"reaches\('([^']+)', '([^']+)'\)", message)
    assert found, f"no runnable reaches(...) advice in: {message}"
    return analysis.reaches(found.group(1), found.group(2))


@live_only
def test_a_self_question_is_refused_in_the_callers_vocabulary_on_the_graph(live_analysis):
    with pytest.raises(ValueError) as value_case:
        live_analysis.paths_between(FLOW_SRC, FLOW_SRC, src_within=FLOW_FROM, dst_within=FLOW_FROM)
    msg = str(value_case.value)
    assert "can://" not in msg and "formal_in" not in msg, msg
    assert repr(FLOW_SRC) in msg and "recursion" in msg
    assert isinstance(_follow_reaches_advice(live_analysis, msg), bool)
    with pytest.raises(ValueError) as callable_case:
        live_analysis.call_paths_between(FLOW_FROM, FLOW_FROM)
    msg = str(callable_case.value)
    assert "can://" not in msg and isinstance(_follow_reaches_advice(live_analysis, msg), bool)


def test_a_self_question_is_refused_in_the_callers_vocabulary_locally(slice_l4):
    with pytest.raises(ValueError) as value_case:
        slice_l4.paths_between("a", "a", src_within="alone", dst_within="alone")
    msg = str(value_case.value)
    assert "can://" not in msg and "formal_in" not in msg and "'a'" in msg and "src.pay.alone" in msg
    assert _follow_reaches_advice(slice_l4, msg) is False, "alone does not recurse"
    with pytest.raises(ValueError) as callable_case:
        slice_l4.call_paths_between("helper", "helper")
    assert _follow_reaches_advice(slice_l4, str(callable_case.value)) is False


@live_only
def test_describe_composes_with_callees_of(live_analysis, busy_callable):
    """``callees_of`` deliberately returns externals; ``describe`` takes anything with a ref. The
    composition used to raise ``KeyError`` on the five externals. An external has no source by
    definition, so it comes back found-with-``None`` -- not as a failed lookup."""
    callees = live_analysis.callees_of(busy_callable)
    assert any(c.kind == "external" for c in callees), "the fixture callable calls out of the project"
    described = live_analysis.describe(callees)
    assert [n.ref for n in described] == [n.ref for n in callees]
    assert all(n.source is None for n in described if n.kind == "external")
    assert any(n.source for n in described if n.kind == "callable"), "declared callees still hydrate"


def test_local_describe_composes_with_callees_of(local_l4):
    callees = local_l4.callees_of("Portal.charge")
    assert callees and all(c.kind == "external" for c in callees), "charge calls only range()"
    described = local_l4.describe(callees)
    assert [n.ref for n in described] == [n.ref for n in callees]
    assert all(n.source is None for n in described)


@live_only
def test_a_stale_ref_is_reported_by_position_not_by_ref(live_analysis):
    good = live_analysis.backward_cone([FLOW_TO]).nodes[0]
    with pytest.raises(KeyError) as e:
        live_analysis.describe([good, good.model_copy(update={"ref": "can://python/nope/nothing.py/nope"})])
    assert "can://" not in str(e.value) and good.callable in str(e.value) and f"{good.file}:{good.line}" in str(e.value)


def test_a_local_stale_ref_is_reported_by_position_not_by_ref(slice_l4):
    node = slice_l4.callers_of("helper")[0]
    with pytest.raises(KeyError) as e:
        slice_l4.describe([node.model_copy(update={"ref": "can://python/nope/x.py/nope"})])
    assert "can://" not in str(e.value) and "src.pay.Portal.charge" in str(e.value)


# ----------------------------------------------------------------------------------------------
# Fix round: parity of the call-graph walks -- no route through a ghost, on either backend.
# ----------------------------------------------------------------------------------------------
#: A real ``callable -> ghost -> callable`` chain on odoo-slim-19 (there are two). With an
#: intermediate-unconstrained walk, ``reaches`` answered ``True`` here although no all-callable
#: route exists, and ``call_paths_between`` returned a path with an ``external`` interior node.
GHOST_CHAIN_SRC = "odoo.addons.base.models.ir_actions_report.IrActionsReport._run_wkhtmltoimage"
GHOST_CHAIN_DST = "odoo.tools.parse_version.chk"


@live_only
def test_the_call_graph_walks_never_route_through_a_ghost(live_analysis):
    backend = live_analysis.backend
    via_ghost = backend._run(
        "MATCH (a:PyCallable {signature:$a})-[:PY_CALLS]->(g:PyExternal)-[:PY_CALLS]->(t:PyCallable {signature:$t}) RETURN count(g) AS c",
        a=GHOST_CHAIN_SRC, t=GHOST_CHAIN_DST,
    )[0]["c"]
    if not via_ghost:
        pytest.skip("this graph no longer carries the callable -> ghost -> callable chain the test is about")
    assert live_analysis.reaches(GHOST_CHAIN_SRC, GHOST_CHAIN_DST) is False, "a ghost has no body, so it is not a hop control can take"
    paths = live_analysis.call_paths_between(GHOST_CHAIN_SRC, GHOST_CHAIN_DST)
    assert len(paths) == 0 and paths.complete
    cone = live_analysis.backward_cone([GHOST_CHAIN_DST], depth=None)
    assert GHOST_CHAIN_SRC not in {n.callable for n in cone.nodes}
    assert all(n.kind == "callable" for n in cone.nodes)
    assert GHOST_CHAIN_SRC not in {c.callable for c in live_analysis.callers_of(GHOST_CHAIN_DST)}, "callers_of already agreed; the walks now do too"


@live_only
def test_every_interior_node_of_a_call_path_is_a_callable(live_analysis):
    for p in live_analysis.call_paths_between(FLOW_FROM, "AccountMove.write"):
        assert all(h.frm.kind == "callable" and h.to.kind == "callable" for h in p.hops)


def test_the_local_call_graph_keeps_only_declared_origin_edges_to_callables_or_externals():
    """The shape the graph backend's ``_call_rows`` produces, pinned on the local builder with an
    edge of each kind it must drop: one originating at a ghost, one landing on a class node."""
    declared = {"can://p/m.py/f()": "m.f", "can://p/m.py/g()": "m.g"}
    class_ids = {"can://p/m.py/K"}
    edges = [
        PyCallEdge(src="can://p/m.py/f()", dst="can://p/m.py/g()", weight=1, prov=[]),
        PyCallEdge(src="can://p/m.py/f()", dst="can://p/@external/builtins/print", weight=1, prov=[]),
        PyCallEdge(src="can://p/@external/lib/callback", dst="can://p/m.py/g()", weight=1, prov=[]),
        PyCallEdge(src="can://p/m.py/g()", dst="can://p/m.py/K", weight=1, prov=[]),
    ]
    graph = PyCodeanalyzer._build_call_graph(edges, declared, class_ids)
    assert set(graph.edges) == {("m.f", "m.g"), ("m.f", "can://p/@external/builtins/print")}
    assert "can://p/m.py/K" not in graph and "can://p/@external/lib/callback" not in graph


def test_a_real_local_call_graph_has_the_graph_backends_shape(local_l4):
    declared = {c.signature for c, _, _, _, _ in local_l4._iter_callables()}
    externals = set(local_l4.get_external_symbols())
    graph = local_l4.get_call_graph()
    assert graph.number_of_edges() > 0
    for src, dst in graph.edges:
        assert src in declared, f"{src} originates outside the application"
        assert dst in declared or dst in externals, f"{dst} is neither a callable nor a known external"


def test_a_local_cone_is_ordered_by_ref_like_the_graphs(slice_l4):
    """``Slice.nodes`` documents ref order; the local cone sorted by signature, which is a
    different key. Both backends now take the cap's prefix from the same order."""
    cone = slice_l4.backward_cone(["helper"], depth=None)
    refs = [n.ref for n in cone.nodes]
    assert len(refs) == 2 and refs == sorted(refs)
    assert [n.ref for n in slice_l4.backward_cone(["helper"], depth=None, max_nodes=1).nodes] == refs[:1]


# ----------------------------------------------------------------------------------------------
# Fix round: one vocabulary for files and kinds, and one validation order.
# ----------------------------------------------------------------------------------------------
def test_local_locate_speaks_the_repo_relative_path(slice_l4):
    """``LocateResult.module.path`` was the absolute analysis-machine path locally and the
    repo-relative key over Neo4j; the join test that would have caught it was Neo4j-only."""
    key = "src/pay.py"
    assert key in slice_l4.application.symbol_table
    inside = slice_l4.locate(key, 5)
    assert inside.callable and inside.callable.name == "helper"
    assert inside.module.path == key and not os.path.isabs(inside.module.path)
    assert slice_l4.describe([inside])[0].file == key, "as_slice_node carries the same vocabulary"
    assert slice_l4.resolve_callable("helper").file == key, "SliceNode.file shares it"
    scope = slice_l4.locate(key, 1)
    assert scope.module.path == key and scope.diagnostics[0].code == "module_scope"
    assert key in scope.diagnostics[0].message and str(slice_l4.project_dir) not in scope.diagnostics[0].message


@live_only
def test_slice_node_kinds_are_the_graphs_vocabulary_translated(live_analysis):
    """``SliceNode.KINDS`` is pinned against the graph's own ``:PyBodyNode.kind`` values, each
    translated the way both backends translate it, plus the two call-graph kinds."""
    graph_kinds = {r["k"] for r in live_analysis.backend._run("MATCH (b:PyBodyNode) WHERE b.id STARTS WITH $prefix RETURN DISTINCT b.kind AS k", prefix=live_analysis.backend._scope_prefix)}
    assert graph_kinds, "no body nodes?"
    translated = set()
    for kind in graph_kinds:
        for var in (["x", "<global>:m::g", "<capture>:c"] if kind == "formal_in" else ["x", None, "<return>"]):
            translated.add(body_node_kind(kind, var)[0])
    assert translated | {"callable", "external"} == SliceNode.KINDS
    for kind in SliceNode.KINDS:
        assert f"``{kind}``" in SliceNode.__doc__, f"the docstring does not list {kind}"


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.get_cfg("no_such_callable", page_size=0),
        lambda b: b.get_ddg("no_such_callable", page_size=0),
        lambda b: b.paths_between("a", "b", src_within="no_such", dst_within="no_such", depth=0),
        lambda b: b.call_paths_between("no_such", "no_such_either", max_paths=0),
        lambda b: b.flows_to_call("a", "no_such", within="no_such", depth=0),
        lambda b: b.flows_to_argument("a", "no_such", arg="x", within="no_such", depth=0),
    ],
)
def test_argument_validation_precedes_name_resolution_on_both_backends(py_either, call):
    """``page_size=0`` plus a bad name raised ``ValueError`` over Neo4j and ``SelectorNotInGraph``
    locally. Now the cheap argument check comes first on both, before any round trip."""
    with pytest.raises(ValueError) as e:
        call(py_either)
    assert not isinstance(e.value, SelectorNotInGraph), str(e.value)
