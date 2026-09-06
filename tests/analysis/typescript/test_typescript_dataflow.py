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

"""The TypeScript dataflow surface (leg 2.5b, Task 2) — offline, over the v2 fixtures.

Fourteen accessors, mirroring ``PythonAnalysis``'s signatures exactly: the three per-callable
:class:`~cldk.analysis.commons.results.EdgePage`\\ s, the three slices, ``reaches``, the two
call-graph neighbour lists, the two path queries and the two flow predicates.

**Every expected number below was derived from
``tests/resources/typescript/analysis_json/v2/a4/analysis.json`` directly** — by walking its
``cfg``/``cdg``/``ddg``/``summary`` lists and the application's ``param_in``/``param_out`` overlays
with a throwaway script — never by running the implementation and copying its output. The
sample app's SDG, seeded at ``UserController.show``'s ``id`` parameter, reaches 52 body nodes
unbounded, 23 at depth 5, 13 at depth 3 and 3 at depth 1; the backward closure of
``nextId``'s ``n`` is 65.

The **asymmetry of the bounds is the point** and is asserted in both directions: a slice defaults
to a finite ``depth`` because a bounded traversal answers a narrower question *completely*, while
``reaches`` / ``paths_between`` / ``call_paths_between`` / ``flows_to_call`` /
``flows_to_argument`` default to ``depth=None`` because a bounded predicate returns a *wrong*
answer rather than a small one. Leg 1.5 shipped a real bug by inheriting the slice default onto a
predicate, so each predicate is asserted true unbounded **and** false at a depth that cuts the
known flow.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.commons.results import EdgePage, FlowPaths, Slice, SliceNode
from cldk.utils.exceptions import CodeanalyzerUsageException, SelectorNotInGraph

SHOW = "src/controllers.UserController.show"
CREATE = "src/services.UserService.create"
NEXT_ID = "src/services.nextId"
ENTITY_CTOR = "src/models.Entity.constructor"


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


def _analysis(app_path, payload, cache, level):
    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_fake_run_writing_output(payload)):
        return CLDK.typescript(project_path=app_path, eager=True, analysis_level=level, backend=CodeAnalyzerConfig(cache_dir=str(cache)))


@pytest.fixture
def ts(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """A local-backend facade over the a4 (``-a 4``) fixture of the tracked sample app."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    return _analysis(typescript_application, typescript_analysis_json, tmp_path, AnalysisLevel.system_dependency_graph)


@pytest.fixture
def ts_a2(typescript_application, tmp_path, monkeypatch):
    """The same app at ``-a 2``: no cfg/cdg/ddg at all, which must raise rather than answer ``[]``."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    root = Path(__file__).resolve().parents[3]
    payload = json.dumps(json.load(open(root / "tests/resources/typescript/analysis_json/v2/a2/analysis.json", encoding="utf-8")))
    return _analysis(typescript_application, payload, tmp_path, AnalysisLevel.call_graph)


# =============================================================== the three per-callable graphs
def test_get_cfg_returns_the_callables_own_edges_with_global_endpoints(ts):
    page = ts.get_cfg("show")
    assert isinstance(page, EdgePage)
    assert page.total == 5 and len(page.edges) == 5
    assert page.complete and page.next_cursor is None
    for e in page.edges:
        assert e.src.startswith("can://typescript/slim/") and e.dst.startswith("can://typescript/slim/")
        assert e.kind in {"fallthrough", "true", "false", "switch_case", "loop_back", "exception", "return", "break", "continue", "yield", "await_resume"}
    assert page.edges == sorted(page.edges, key=lambda e: (e.src, e.dst, e.kind or ""))


def test_get_cdg_and_get_ddg_return_the_fixtures_counts(ts):
    assert ts.get_cdg("show").total == 2
    ddg = ts.get_ddg("show")
    assert ddg.total == 9 and len(ddg.edges) == 9


def test_typescript_ddg_has_exactly_one_provenance_tier(ts):
    """Every ``TS_DDG`` edge carries ``["reaching-defs"]`` — cants emits no ``ssa`` or
    ``points-to`` tier, so Python's three-way certainty ranking collapses to one value here."""
    provs = {tuple(e.prov) for e in ts.get_ddg("show").edges}
    assert provs == {("reaching-defs",)}


def test_a_page_is_bounded_and_the_cursor_walks_the_rest_without_repeating(ts):
    first = ts.get_cfg("show", page_size=2)
    assert len(first.edges) == 2 and first.total == 5
    assert first.complete is False and first.next_cursor is not None
    seen = list(first.edges)
    cursor = first.next_cursor
    while cursor is not None:
        page = ts.get_cfg("show", page_size=2, cursor=cursor)
        assert page.total == 5
        seen.extend(page.edges)
        cursor = page.next_cursor
    assert len(seen) == 5 and len({(e.src, e.dst, e.kind) for e in seen}) == 5


def test_page_size_zero_and_a_bad_cursor_raise_naming_the_argument(ts):
    with pytest.raises(ValueError, match="page_size"):
        ts.get_cfg("show", page_size=0)
    with pytest.raises(ValueError, match="not a cursor"):
        ts.get_cfg("show", cursor="not-a-cursor")


def test_a_cursor_from_another_accessor_or_another_callable_is_refused(ts):
    cfg_cursor = ts.get_cfg("show", page_size=1).next_cursor
    with pytest.raises(ValueError, match="components"):
        ts.get_cdg("show", cursor=cfg_cursor)
    with pytest.raises(ValueError, match="is from a page of"):
        ts.get_cfg("list", cursor=cfg_cursor)


def test_the_graphs_refuse_below_the_dataflow_level_rather_than_answering_empty(ts_a2):
    with pytest.raises(CodeanalyzerUsageException, match="program_dependency_graph"):
        ts_a2.get_ddg("show")
    with pytest.raises(CodeanalyzerUsageException):
        ts_a2.slice_forward("id", within="show")


# ================================================================================ the slices
def test_slice_forward_defaults_to_a_finite_depth_and_none_asks_for_the_whole_cone(ts):
    bounded = ts.slice_forward("id", within=SHOW)
    assert isinstance(bounded, Slice)
    assert bounded.total == 23 and bounded.complete
    assert ts.slice_forward("id", within=SHOW, depth=1).total == 3
    assert ts.slice_forward("id", within=SHOW, depth=3).total == 13
    assert ts.slice_forward("id", within=SHOW, depth=None).total == 52


def test_a_slice_carries_its_seed_and_the_audit_line(ts):
    s = ts.slice_forward("id", within=SHOW)
    assert [r.callable for r in s.roots] == [SHOW]
    assert s.roots[0].kind == "parameter" and s.roots[0].name == "id"
    assert s.resolved == f"{SHOW} parameter 'id'"
    assert s.nodes == sorted(s.nodes, key=lambda n: n.ref)
    assert all(isinstance(n, SliceNode) and n.source is None for n in s.nodes)


def test_max_nodes_caps_and_says_so(ts):
    s = ts.slice_forward("id", within=SHOW, depth=None, max_nodes=2)
    assert len(s.nodes) == 2 and s.total == 52
    assert s.complete is False


def test_slice_backward_is_the_same_edges_read_the_other_way(ts):
    assert ts.slice_backward("n", within=NEXT_ID, depth=None).total == 65


def test_slice_bounds_are_type_checked(ts):
    with pytest.raises(ValueError, match="depth"):
        ts.slice_forward("id", within=SHOW, depth=0)
    with pytest.raises(ValueError, match="max_nodes"):
        ts.slice_forward("id", within=SHOW, max_nodes=0)


# ================================================================= call graph: reach and cone
def test_reaches_is_unbounded_by_default_and_a_cutting_depth_returns_false(ts):
    assert ts.reaches(SHOW, ENTITY_CTOR) is True
    assert ts.reaches(SHOW, ENTITY_CTOR, depth=2) is False
    assert ts.reaches(SHOW, ENTITY_CTOR, depth=3) is True
    assert ts.reaches(ENTITY_CTOR, SHOW) is False


def test_backward_cone_walks_back_over_calls_and_reaches_the_module_caller(ts):
    cone = ts.backward_cone([NEXT_ID], depth=None)
    assert {n.callable for n in cone.nodes} == {
        NEXT_ID,
        CREATE,
        SHOW,
        "src/index.main",
        "src/services.UserService.createGuest",
        "src/index.ts",
    }
    # cants makes a module the caller of its own top-level code (TS-11), so a cone that dropped
    # module vertices would answer "nothing else reaches this" where a module does.
    assert {n.kind for n in cone.nodes} == {"callable", "module"}
    assert ts.backward_cone([NEXT_ID], depth=1).total == 2


def test_backward_cone_refuses_the_two_ways_of_naming_nothing(ts):
    with pytest.raises(TypeError):
        ts.backward_cone(NEXT_ID)
    with pytest.raises(ValueError, match="sinks"):
        ts.backward_cone([])


def test_callers_and_callees_speak_the_callers_vocabulary(ts):
    callers = {(n.callable, n.kind) for n in ts.callers_of(CREATE)}
    assert callers == {(SHOW, "callable"), ("src/index.main", "callable"), ("src/services.UserService.createGuest", "callable")}
    callees = {(n.callable, n.kind) for n in ts.callees_of(CREATE)}
    assert callees == {("(builtin).push", "external"), ("src/models.User.constructor", "callable"), (NEXT_ID, "callable")}
    for n in ts.callees_of(CREATE):
        assert not n.ref.startswith("can://") or n.kind != "external" or n.file == ""


def test_a_module_caller_is_reported_as_one(ts):
    callers = ts.callers_of("src/index.main")
    assert [(n.callable, n.kind, n.file) for n in callers] == [("src/index.ts", "module", "src/index.ts")]


# ======================================================================== paths and predicates
def test_paths_between_takes_two_scopes_and_reports_the_hops(ts):
    paths = ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE)
    assert isinstance(paths, FlowPaths) and paths.paths and paths.complete
    hops = paths.paths[0].hops
    assert hops[0].frm.callable == SHOW and hops[-1].to.callable == CREATE
    assert {h.via for h in hops} <= {"data", "control", "argument", "return", "summary"}
    assert any(h.via == "argument" for h in hops), "the flow must cross a call boundary"
    for i in range(len(hops) - 1):
        assert hops[i].to.ref == hops[i + 1].frm.ref


def test_paths_between_is_unbounded_by_default_and_a_cutting_depth_empties_it(ts):
    assert ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE).paths
    assert ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE, depth=1).paths == []


def test_max_paths_truncation_is_reported(ts):
    paths = ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE, max_paths=1)
    assert len(paths.paths) <= 1
    with pytest.raises(ValueError, match="max_paths"):
        ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE, max_paths=0)


def test_a_path_to_itself_is_refused_rather_than_answered_empty(ts):
    with pytest.raises(ValueError, match="itself"):
        ts.paths_between("id", "id", src_within=SHOW, dst_within=SHOW)
    with pytest.raises(ValueError, match="itself"):
        ts.call_paths_between(SHOW, SHOW)


def test_call_paths_between_says_how_where_reaches_says_whether(ts):
    paths = ts.call_paths_between(SHOW, ENTITY_CTOR)
    assert len(paths.paths) == 1 and paths.complete
    hops = paths.paths[0].hops
    assert [h.via for h in hops] == ["call", "call", "call"]
    assert [h.frm.callable for h in hops] == [SHOW, CREATE, "src/models.User.constructor"]
    assert hops[-1].to.callable == ENTITY_CTOR
    assert all(h.var is None and h.prov == [] for h in hops)
    assert ts.call_paths_between(SHOW, ENTITY_CTOR, depth=2).paths == []


def test_flows_to_call_is_unbounded_by_default(ts):
    assert ts.flows_to_call("id", CREATE, within=SHOW) is True
    assert ts.flows_to_call("id", CREATE, within=SHOW, depth=1) is False
    assert ts.flows_to_call("id", "src/util.StringUtil.slug", within=SHOW) is False


def test_flows_to_argument_is_the_narrower_question(ts):
    assert ts.flows_to_argument("id", CREATE, "name", within=SHOW) is True
    assert ts.flows_to_argument("id", CREATE, "name", within=SHOW, depth=1) is False
    # role is create's other parameter; the flow reaching the callable does not reach it.
    assert ts.flows_to_argument("id", CREATE, "role", within=SHOW) is False


def test_an_argument_naming_no_value_of_the_callee_raises_rather_than_answering_false(ts):
    with pytest.raises(SelectorNotInGraph):
        ts.flows_to_argument("id", CREATE, "noSuchParameter", within=SHOW)


def test_every_predicate_and_path_accessor_type_checks_depth(ts):
    for call in (
        lambda: ts.reaches(SHOW, CREATE, depth="2"),
        lambda: ts.paths_between("id", "name", src_within=SHOW, dst_within=CREATE, depth=0),
        lambda: ts.call_paths_between(SHOW, CREATE, depth=True),
        lambda: ts.flows_to_call("id", CREATE, within=SHOW, depth=2.5),
        lambda: ts.flows_to_argument("id", CREATE, "name", within=SHOW, depth=-1),
    ):
        with pytest.raises(ValueError, match="depth"):
            call()
