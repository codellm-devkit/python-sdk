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
from cldk.analysis.python.backend import DDG_ORDER, DEFAULT_PAGE_SIZE, cdg_sort_key, cfg_sort_key, ddg_sort_key, decode_cursor, edge_page, encode_cursor
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
