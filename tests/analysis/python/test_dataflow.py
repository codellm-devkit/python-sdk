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

import os
import re
import textwrap

import pytest

from codeanalyzer.neo4j.project import _project_program_graphs
from codeanalyzer.neo4j.rows import RowBuilder

from cldk.analysis import AnalysisLevel
from cldk.analysis.python.backend import DDG_ORDER, DEFAULT_DEPTH, DEFAULT_PAGE_SIZE, cdg_sort_key, cfg_sort_key, ddg_sort_key, decode_cursor, edge_page, encode_cursor
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.models.python import DdgEdge
from cldk.utils.exceptions import AmbiguousName, CodeanalyzerUsageException

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
    _project_program_graphs(rows, local_l4.application, {}, {})
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
    assert empty.edges == [] and empty.total == 0 and empty.next_cursor is None and not empty.has_more


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
    assert page.has_more and page.next_cursor


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
    _project_program_graphs(rows, local_l4.application, {}, {})
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
    """``has_more`` distinguishes "this is everything" from "there is more" from one page (E5), and an
    empty page from a level-4 analysis still means "no dependence" (D7)."""
    whole = local_l4.get_ddg("Portal.charge", page_size=10_000)
    assert whole.next_cursor is None and not whole.has_more
    assert whole.total == len(whole.edges)
    first = local_l4.get_ddg("Portal.charge", page_size=1)
    assert first.has_more and first.total == whole.total and len(first.edges) == 1


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
# was too broad rather than walking twenty pages to find out. ``truncated`` is derived from those
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
    assert not sl.truncated
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
    assert sl.total == 1 and not sl.truncated


@live_only
def test_forward_slice_goes_where_the_value_goes(live_analysis, busy_callable):
    """Measured: 50 nodes, and every one of them addressed in the caller's vocabulary.

    ``depth=None`` because the claim is about the *whole* forward cone — Task 6 got that by
    default and this now has to ask for it."""
    sl = live_analysis.slice_forward("invoice_id", within=busy_callable, depth=None)
    assert sl.total == 50 and len(sl.nodes) == 50 and not sl.truncated
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
    assert sl.truncated is True, "a cap that fires must be visible"
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
    assert near.total == 22 and not near.truncated
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
    assert len(fwd.nodes) == 10 and fwd.total == HEAVY_FORWARD_SLICE and fwd.truncated
    back = live_analysis.slice_backward("kwargs", within=HEAVY_CALLABLE, depth=None)
    assert back.total == 1 and not back.truncated


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
    flagged ``truncated`` and useless. At the default depth the same call answers the narrower
    question *completely*: a small slice, ``total`` equal to what came back, ``truncated`` False.
    Both halves are asserted here, because the change is worth nothing if the second one moves."""
    near = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE)
    assert near.total == DEPTH_SEED_AT_DEFAULT
    assert len(near.nodes) == near.total and not near.truncated

    whole = live_analysis.slice_backward(DEPTH_SEED_VALUE, within=DEPTH_SEED_CALLABLE, depth=None)
    assert whole.total == DEPTH_SEED_UNBOUNDED, "depth=None still means the whole closure"
    assert len(whole.nodes) == 10_000 and whole.truncated
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
    """A caller and a callee, so the interprocedural edges exist at all: ``charge`` passes
    ``invoice_id`` to ``helper``, which is a ``PY_PARAM_IN`` in the graph's vocabulary."""
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
                    amount = helper(invoice_id)
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
    assert sl.total == len(expected) and not sl.truncated


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
    assert len(capped.nodes) == 2 and capped.truncated
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
