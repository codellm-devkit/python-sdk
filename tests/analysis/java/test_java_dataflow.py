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

"""The Java dataflow surface (leg 3b, Task 2), offline.

Thirteen accessors in Python's signatures, over the committed **a4** fixture -- the four-unit,
level-4 copy, which is the only one carrying ``cfg``/``cdg``/``ddg``, the ``formal_in`` lattice and
a call graph (``a1`` is level 1, where all four are empty).

**What is proved here and what is proved live.** The call-graph half (``reaches``, ``callers_of``,
``callees_of``, ``backward_cone``, ``call_paths_between``) is answered by *one* implementation on
:class:`~cldk.analysis.java.backend.JavaAnalysisBackend`, over the ``get_call_graph()`` both
backends already build, so both are exercised here for real with no server and no fake rows. The
per-callable pages and the SDG walks reach Cypher on the graph backend, so those are the local
backend here and ``test_java_dataflow_live.py`` on 7691.

**The self-loop guard (python-sdk#349).** ``PyNeo4jBackend._OWN_EDGES`` binds the containment
relationship twice, which Cypher's relationship-uniqueness rule makes drop every self-loop -- 64,702
edges on the Python corpus, with ``total`` computed from the same MATCH so the page reports itself
complete. Java's ``J_DDG`` has 978 self-loops (133 in daytrader8, 845 in ThingsBoard) and 20 of them
are in this fixture, so ``get_ddg`` is asserted to contain them: the shape cannot ship here.
"""

import inspect

import pytest

from cldk.analysis.commons.bounds import DEFAULT_MAX_NODES, DEFAULT_MAX_PATHS
from cldk.analysis.commons.results import FlowPaths, Slice, SliceNode
from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.analysis.java.java_analysis import JavaAnalysis
from cldk.analysis.python.backend import PythonAnalysisBackend
from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.models.java.models import JCdgEdge, JCfgEdge, JDdgEdge
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, CodeanalyzerUsageException

from tests.analysis.java.test_java_addressing import _graph, _local

DIRECT = "com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect"
BEANS = "com.ibm.websphere.samples.daytrader.beans"

TO_JSON = f"{BEANS}.MarketSummaryDataBean.toJSON()"
CANCEL = f"{DIRECT}.cancelOrder(java.lang.Integer, boolean)"
SELL = f"{DIRECT}.sell(java.lang.String, java.lang.Integer, int)"
COMPLETE = f"{DIRECT}.completeOrder(java.sql.Connection, java.lang.Integer)"
GET_STATEMENT = f"{DIRECT}.getStatement(java.sql.Connection, java.lang.String)"
#: A real leaf of a4's call graph -- 26 of its 100 vertices have no caller.
PRINT = f"{BEANS}.MarketSummaryDataBean.print()"

#: The thirteen, in the order the plan's interface block spells them. (The plan's Task-2 heading
#: says "fourteen"; its own block lists thirteen, and thirteen is what Python's facade carries.)
SURFACE = (
    "get_cfg",
    "get_cdg",
    "get_ddg",
    "slice_backward",
    "slice_forward",
    "backward_cone",
    "reaches",
    "callers_of",
    "callees_of",
    "paths_between",
    "call_paths_between",
    "flows_to_call",
    "flows_to_argument",
)


@pytest.fixture(scope="module", params=["local", "graph"])
def both(request, analysis_json_a4):
    """Both backends over a4. The graph backend answers from the seeded ``_application`` -- which is
    the shipped code, since leg 3a makes it rebuild exactly that (see ``test_java_addressing.py``)."""
    return (_local if request.param == "local" else _graph)(analysis_json_a4)


@pytest.fixture(scope="module")
def ref(analysis_json_a4):
    """The in-memory backend alone, for the accessors that reach Cypher on the other one."""
    return _local(analysis_json_a4)


# ---- the signatures ----------------------------------------------------------------------------
def _parameters(owner, name):
    """A signature's parameters, keyword-only-ness and defaults -- everything but the return
    annotation, which is the one thing that legitimately differs: Java's pages carry Java's edge
    models (``EdgePage[JCfgEdge]`` against ``EdgePage[CfgEdge]``)."""
    return [(p.name, p.kind, p.default, str(p.annotation)) for p in inspect.signature(getattr(owner, name)).parameters.values()]


def test_the_thirteen_signatures_mirror_pythons():
    for name in SURFACE:
        assert _parameters(JavaAnalysisBackend, name) == _parameters(PythonAnalysisBackend, name), name


def test_the_facade_mirrors_pythons_too():
    for name in SURFACE:
        assert _parameters(JavaAnalysis, name) == _parameters(PythonAnalysis, name), name


def test_the_bounds_are_asymmetric_by_default():
    """E5, as a property of the signatures: the three slices default ``depth`` to a finite value,
    the two path queries and the three predicates default it to ``None``. A hop budget on a boolean
    is not a smaller answer but a wrong one."""
    finite = {name: inspect.signature(getattr(JavaAnalysisBackend, name)).parameters["depth"].default for name in ("slice_backward", "slice_forward", "backward_cone")}
    unbounded = {
        name: inspect.signature(getattr(JavaAnalysisBackend, name)).parameters["depth"].default
        for name in ("reaches", "paths_between", "call_paths_between", "flows_to_call", "flows_to_argument")
    }
    assert set(finite.values()) == {5}, finite
    assert set(unbounded.values()) == {None}, unbounded
    for name in ("slice_backward", "slice_forward", "backward_cone"):
        assert inspect.signature(getattr(JavaAnalysisBackend, name)).parameters["max_nodes"].default == DEFAULT_MAX_NODES
    for name in ("paths_between", "call_paths_between"):
        assert inspect.signature(getattr(JavaAnalysisBackend, name)).parameters["max_paths"].default == DEFAULT_MAX_PATHS


def test_paths_between_takes_two_scopes():
    """One scope can never find a cross-callable path, so there is no ``within=``."""
    parameters = inspect.signature(JavaAnalysisBackend.paths_between).parameters
    assert "src_within" in parameters and "dst_within" in parameters and "within" not in parameters
    assert parameters["src_within"].default is inspect.Parameter.empty
    assert parameters["dst_within"].default is inspect.Parameter.empty


# ---- the per-callable graphs -------------------------------------------------------------------
def test_get_cfg_returns_the_analyzers_own_edges(ref):
    page = ref.get_cfg(TO_JSON)
    assert page.total == 22 and len(page.edges) == 22 and page.complete
    assert all(isinstance(e, JCfgEdge) for e in page)
    assert {e.kind for e in page} <= {"fallthrough", "true", "false", "return", "loop_back", "exception", "break", "switch_case"}


def test_get_cdg_returns_the_analyzers_own_edges(ref):
    page = ref.get_cdg(TO_JSON)
    assert page.total == 12 and all(isinstance(e, JCdgEdge) for e in page)


def test_get_ddg_carries_the_variable_and_the_two_provenance_tiers(ref):
    """Java's DDG has exactly **two** tiers -- ``ssa`` and ``points-to`` -- where Python has three
    and TypeScript one. Measured across the whole fixture: 1,171 ``ssa`` and 320 ``points-to``."""
    page = ref.get_ddg(TO_JSON)
    assert page.total == 48 and all(isinstance(e, JDdgEdge) for e in page)
    tiers = {tuple(e.prov) for c in _every_callable(ref) for e in (c.ddg or [])}
    assert tiers == {("ssa",), ("points-to",)}


def _every_callable(backend):
    return [c for _, c in backend._callables.values()]


def test_the_endpoints_are_global_body_node_ids_that_get_source_accepts(ref):
    """``JCallable.cfg`` keys its endpoints by the *local* body key (``"66:9"``, ``"@entry"``);
    what a caller gets is the same global id ``locate`` hands back and ``get_source`` takes."""
    page = ref.get_cfg(TO_JSON)
    callable_id = ref.resolve_callable(TO_JSON).ref
    assert all(e.src.startswith(callable_id + "@") and e.dst.startswith(callable_id + "@") for e in page)
    statement = next(e.src for e in page if not e.src.endswith("@entry"))
    assert ref.get_source(statement)


def test_get_ddg_contains_the_self_loops(ref):
    """**The python-sdk#349 regression guard.** ``MarketSummaryDataBean.toJSON()`` has four
    ``J_DDG`` edges from a body node to itself, of 48. A page built the way ``PyNeo4jBackend``
    builds one -- binding the containment relationship twice -- would report 44 and call itself
    complete; this asserts the four are there and that ``total`` counts them."""
    page = ref.get_ddg(TO_JSON)
    loops = [e for e in page if e.src == e.dst]
    assert len(loops) == 4, [(e.src, e.dst) for e in page]
    assert page.total == len(page.edges) == 48 and page.complete


def test_the_whole_fixture_carries_twenty_self_loops(ref):
    """The corpus fact the guard above is one instance of, so a fixture regeneration that loses
    them fails here rather than quietly weakening the guard."""
    loops = sum(1 for c in _every_callable(ref) for e in (c.ddg or []) if e.src == e.dst)
    assert loops == 20


def test_an_implicit_callable_refuses_a_graph_rather_than_answering_empty(both):
    """J-6 halved: an implicit callable **resolves** -- it is a real call-graph endpoint, and the
    call-graph accessors answer about it -- and the accessors that would have to read a body
    refuse, naming the reason.

    ``total=0, edges=[], complete=True`` is the one answer they must not give. It is a well-formed
    page that says "this callable has no control flow", which is the ambiguous empty (D7) this
    surface refuses everywhere else, and there are 99 such callables in daytrader8.
    """
    key = f"{DIRECT}.<init>()"
    assert both.resolve_callable("<init>()", in_class=DIRECT).callable == key, "J-6: it still resolves"
    assert both.callers_of("<init>()", in_class=DIRECT) is not None, "and the call graph still answers about it"
    for accessor in (both.get_cfg, both.get_cdg, both.get_ddg):
        with pytest.raises(CodeanalyzerUsageException) as raised:
            accessor(key)
        assert "implicit" in str(raised.value) and "can://" not in str(raised.value), accessor.__name__


def test_an_implicit_callable_refuses_a_value_address_too(both):
    """The other half of J-6's list. ``within=`` an implicit callable used to fail as
    ``value not in graph: 'x'`` -- true, and it blames the name for what the callable is."""
    key = f"{DIRECT}.<init>()"
    with pytest.raises(CodeanalyzerUsageException) as raised:
        both.slice_backward("x", within=key)
    assert "implicit" in str(raised.value)
    with pytest.raises(CodeanalyzerUsageException):
        both.resolve_value("x", within=key)


# ---- paging ------------------------------------------------------------------------------------
def test_a_page_is_ordered_and_a_cursor_resumes_after_it(ref):
    whole = list(ref.get_ddg(TO_JSON))
    keys = [(e.src, e.dst, e.var or "", list(e.prov)) for e in whole]
    assert keys == sorted(keys), "the page is not in the canonical order"
    first = ref.get_ddg(TO_JSON, page_size=10)
    assert len(first.edges) == 10 and first.total == 48 and not first.complete and first.next_cursor
    second = ref.get_ddg(TO_JSON, page_size=10, cursor=first.next_cursor)
    assert list(second.edges) == whole[10:20]


def test_a_cursor_from_another_callable_or_another_accessor_is_refused(ref):
    cursor = ref.get_ddg(TO_JSON, page_size=10).next_cursor
    with pytest.raises(ValueError, match="not"):
        ref.get_ddg(CANCEL, page_size=10, cursor=cursor)
    with pytest.raises(ValueError, match="components"):
        ref.get_cdg(TO_JSON, page_size=10, cursor=cursor)


def test_page_size_must_ask_for_at_least_one_edge(ref):
    with pytest.raises(ValueError, match="page_size"):
        ref.get_ddg(TO_JSON, page_size=0)


def test_an_ambiguous_or_missing_callable_raises_rather_than_paging(ref):
    with pytest.raises(AmbiguousName):
        ref.get_cfg("cancelOrder")
    with pytest.raises(SelectorNotInGraph):
        ref.get_cfg("cancelOrders")


def test_below_the_dataflow_level_the_pages_refuse_rather_than_come_back_empty(analysis_json):
    """a1 is level 1, where the analyzer emits no cfg/cdg/ddg at all. An empty page there would be
    indistinguishable from a callable with no dependence (D7), so it raises naming both levels."""
    backend = _local(analysis_json)
    backend.analysis_level = "symbol_table"
    with pytest.raises(CodeanalyzerUsageException, match="program_dependency_graph"):
        backend.get_ddg(CANCEL)


# ---- the call graph: callers / callees ---------------------------------------------------------
def test_callers_of_is_one_hop_back_in_the_callers_vocabulary(both):
    callers = both.callers_of(GET_STATEMENT)
    assert len(callers) == 28
    assert all(isinstance(n, SliceNode) and n.kind == "callable" for n in callers)
    assert all("can://" not in n.callable for n in callers)
    assert [n.ref for n in callers] == sorted(n.ref for n in callers)
    assert COMPLETE in {n.callable for n in callers}


def test_callees_of_is_one_hop_forward(both):
    assert len(both.callees_of(SELL)) == 15
    assert COMPLETE in {n.callable for n in both.callees_of(SELL)}


def test_a_callable_with_no_call_edges_is_an_empty_list_not_a_raise(both):
    """``[]`` is unambiguous here because a name matching nothing raises: it means "nothing calls
    it", never "no such callable"."""
    assert both.callers_of(PRINT) == []
    with pytest.raises(SelectorNotInGraph):
        both.callers_of("noSuchMethod")


def test_callers_and_callees_take_the_scoping_keywords(both):
    assert both.callees_of("sell(java.lang.String, java.lang.Integer, int)", in_class=DIRECT)
    assert both.callees_of("sell(java.lang.String, java.lang.Integer, int)", in_module="TradeDirect")


# ---- reaches -----------------------------------------------------------------------------------
def test_reaches_is_unbounded_by_default_and_a_cutting_depth_says_no(both):
    """Both halves of E5's asymmetry on one real pair: ``sell`` reaches ``getStatement`` in two
    hops, so the default (unbounded) is ``True`` and ``depth=1`` is ``False``. A default that
    bounded the predicate would answer ``False`` for a flow that exists."""
    assert both.reaches(SELL, GET_STATEMENT) is True
    assert both.reaches(SELL, GET_STATEMENT, depth=2) is True
    assert both.reaches(SELL, GET_STATEMENT, depth=1) is False


def test_reaches_answers_the_cycle_question_the_self_path_refusal_points_at(ref):
    """``call_paths_between(x, x)`` refuses and says "ask ``reaches(X, X)`` whether a cycle
    exists". That advice has to work, and it did not: the in-memory rule asked a descendants set,
    which excludes its own source, so ``reaches(x, x)`` was ``False`` even for direct recursion --
    an E8 wrong answer reached *from the error path*, which is where a caller is least able to
    check it.

    daytrader8 has no recursive callable at all (0 self-loops, 0 cycles over a4's 100 call-graph
    vertices), so the cycle is added to the cached graph the accessor reads and removed again. The
    fixture is the reason the defect survived, not a reason to leave it untested.
    """
    graph = ref.get_call_graph()
    assert ref.reaches(PRINT, PRINT) is False, "and it is not vacuously true either"
    with pytest.raises(ValueError) as refusal:
        ref.call_paths_between(PRINT, PRINT)
    assert f"reaches({PRINT!r}, {PRINT!r})" in str(refusal.value)

    graph.add_edge(PRINT, PRINT, type="CALL_DEP", weight=1, calling_lines=[])
    try:
        assert ref.reaches(PRINT, PRINT) is True, "the advice, followed literally"
        assert ref.reaches(PRINT, PRINT, depth=1) is True
    finally:
        graph.remove_edge(PRINT, PRINT)


def test_reaches_refuses_a_malformed_depth(both):
    for depth in ("2", 2.5, 0, True):
        with pytest.raises(ValueError, match="depth"):
            both.reaches(SELL, GET_STATEMENT, depth=depth)


def test_reaches_resolves_both_names(both):
    with pytest.raises(SelectorNotInGraph):
        both.reaches(SELL, "noSuchMethod")


# ---- backward_cone -----------------------------------------------------------------------------
def test_backward_cone_is_bounded_by_default_and_says_how_much_it_left_out(both):
    """A slice's finite default is a *complete* answer to a narrower question, and ``total`` says
    so: 29 callables reach ``getStatement`` in one hop, 53 in any number."""
    assert len(both.backward_cone([GET_STATEMENT], depth=1).nodes) == 29
    whole = both.backward_cone([GET_STATEMENT], depth=None)
    assert whole.total == 53 and whole.complete
    capped = both.backward_cone([GET_STATEMENT], depth=None, max_nodes=10)
    assert len(capped.nodes) == 10 and capped.total == 53 and not capped.complete


def test_backward_cone_includes_its_sinks_and_reports_what_they_matched(both):
    cone = both.backward_cone([GET_STATEMENT], depth=1)
    assert GET_STATEMENT in {n.callable for n in cone.nodes}
    assert [r.callable for r in cone.roots] == [GET_STATEMENT]
    assert cone.resolved == GET_STATEMENT


def test_backward_cone_refuses_the_two_ways_of_naming_nothing(both):
    with pytest.raises(TypeError, match="sequence"):
        both.backward_cone(GET_STATEMENT)
    with pytest.raises(ValueError, match="sinks"):
        both.backward_cone([])
    with pytest.raises(ValueError, match="max_nodes"):
        both.backward_cone([GET_STATEMENT], max_nodes=0)


# ---- call_paths_between ------------------------------------------------------------------------
def test_call_paths_between_reports_the_shortest_paths_with_call_hops(both):
    paths = both.call_paths_between(SELL, GET_STATEMENT)
    assert isinstance(paths, FlowPaths) and paths.complete
    assert len(paths.paths) == 6
    for path in paths.paths:
        assert [h.via for h in path.hops] == ["call", "call"]
        assert all(h.var is None and h.prov == [] for h in path.hops)
        assert path.hops[0].frm.callable == SELL and path.hops[-1].to.callable == GET_STATEMENT
        assert path.hops[0].to is path.hops[1].frm


def test_call_paths_between_is_unbounded_by_default_and_a_cutting_depth_empties_it(both):
    assert both.call_paths_between(SELL, GET_STATEMENT, depth=1).paths == []
    assert both.call_paths_between(SELL, GET_STATEMENT, depth=2).paths


def test_call_paths_between_says_when_it_truncated(both):
    capped = both.call_paths_between(SELL, GET_STATEMENT, max_paths=3)
    assert len(capped.paths) == 3 and not capped.complete
    assert capped.paths == both.call_paths_between(SELL, GET_STATEMENT).paths[:3], "the cap is not a prefix of one total order"


def test_call_paths_between_refuses_a_self_question(both):
    with pytest.raises(ValueError, match="reaches"):
        both.call_paths_between(SELL, SELL)
    with pytest.raises(ValueError, match="max_paths"):
        both.call_paths_between(SELL, GET_STATEMENT, max_paths=0)


# ---- slice_backward ----------------------------------------------------------------------------
def test_slice_backward_from_a_parameter_reaches_the_arguments_that_feed_it(ref):
    """``J_PARAM_IN`` runs ``actual_in -> formal_in``, so a backward slice from a parameter is the
    seed plus the argument vertex at every call site that passes one: 34 for ``getStatement``'s
    ``conn``, one for ``cancelOrder``'s ``orderID``."""
    found = ref.slice_backward("conn", within=GET_STATEMENT, depth=None)
    assert found.total == 35 and found.complete
    assert {n.kind for n in found.nodes} == {"parameter", "argument"}
    assert [n.ref for n in found.nodes] == sorted(n.ref for n in found.nodes)
    assert found.roots[0].name == "conn" and found.resolved.endswith("parameter 'conn'")
    assert ref.slice_backward("orderID", within=CANCEL, depth=None).total == 2


def test_a_slice_reports_a_cap_rather_than_returning_less_in_silence(ref):
    capped = ref.slice_backward("conn", within=GET_STATEMENT, depth=None, max_nodes=5)
    assert len(capped.nodes) == 5 and capped.total == 35 and not capped.complete


def test_a_slice_node_names_the_parameter_without_an_ordinal(ref):
    """E7: the vertex id is ``@formal_in:<n>``; what the caller sees is the source identifier. The
    name is read off the parameter list, which round-trips through the Neo4j projection -- the
    graph carries no ``of`` property on a ``:JBodyNode`` at all (measured: 0 of daytrader8's
    11,436)."""
    seed = ref.slice_backward("conn", within=GET_STATEMENT).roots[0]
    assert (seed.kind, seed.name) == ("parameter", "conn")
    assert "formal_in" not in str(seed.name) and ":0" not in str(seed.name)


def test_slice_backward_resolves_its_value_and_its_scope(ref):
    with pytest.raises(SelectorNotInGraph):
        ref.slice_backward("noSuchParameter", within=GET_STATEMENT)
    with pytest.raises(SelectorNotInGraph):
        ref.slice_backward("conn", within="noSuchMethod")
    with pytest.raises(ValueError, match="depth"):
        ref.slice_backward("conn", within=GET_STATEMENT, depth=0)


# ---- the port-lattice gap ----------------------------------------------------------------------
#: The four accessors whose answer cannot vary while codeanalyzer-java emits no dependence edge out
#: of a ``formal_in`` vertex, with a call that is otherwise valid.
GUARDED = {
    "slice_forward": lambda b: b.slice_forward("conn", within=GET_STATEMENT),
    "paths_between": lambda b: b.paths_between("conn", "orderID", src_within=GET_STATEMENT, dst_within=CANCEL),
    "flows_to_call": lambda b: b.flows_to_call("conn", COMPLETE, within=GET_STATEMENT),
    "flows_to_argument": lambda b: b.flows_to_argument("conn", COMPLETE, "conn", within=GET_STATEMENT),
}


def test_the_port_lattice_carries_no_dependence_edge(ref):
    """The measurement the four refusals rest on, asserted rather than assumed: not one of the
    fixture's 225 ``formal_in`` vertices has an outgoing ``ddg``/``cdg``/``summary``/param edge, and
    no ``ddg`` or ``cdg`` edge touches a port vertex at either end. codeanalyzer-java 3.0.1 emits
    the L4 port lattice disconnected from the statement dependence graph; codeanalyzer-python does
    not (129,883 ``PY_DDG`` edges leave a ``formal_in`` on the reference graph)."""
    ports = {"formal_in", "actual_in", "formal_out", "actual_out"}
    touching = 0
    for c in _every_callable(ref):
        kinds = {k: n.kind for k, n in c.body.items()}
        for e in (c.ddg or []) + (c.cdg or []):
            touching += kinds.get(e.src) in ports or kinds.get(e.dst) in ports
    assert touching == 0
    assert not ref._ports_carry_dependence


@pytest.mark.parametrize("dangling, carries", [(True, False), (False, True)], ids=["target-never-emitted", "target-emitted"])
def test_the_port_probe_counts_only_an_edge_whose_target_is_a_node(dangling, carries):
    """One boolean, one definition. The Neo4j spelling matches
    ``(b:JBodyNode)-[…]->(m:JBodyNode)``, so it can only see an edge whose **target was emitted as a
    node**; the in-memory one counted any outgoing edge of a ``formal_in``, materialised target or
    not. codeanalyzer-java 3.0.2 emitted 87 of daytrader8's 5,434 ddg edges naming an endpoint it
    never emitted (#228, fixed in 3.0.3), which is exactly the shape that made the two disagree --
    and this boolean decides whether four accessors raise or answer.

    Driven off a seeded ``_sdg_cache`` rather than a payload, because the divergence needs an edge
    the released analyzer no longer emits.
    """
    from cldk.analysis.java.codeanalyzer import JCodeanalyzer

    port, target = "can://java/x/M.java/T/m()@formal_in:0", "can://java/x/M.java/T/m()@9:9"
    nodes = {port: ("formal_in", 1)} if dangling else {port: ("formal_in", 1), target: ("statement", 9)}
    backend = JCodeanalyzer.__new__(JCodeanalyzer)
    backend._sdg_cache = ({"forward": {port: {target: [("J_DDG", None, ())]}}, "backward": {target: {port: [("J_DDG", None, ())]}}}, nodes)
    assert backend._ports_carry_dependence is carries


@pytest.mark.parametrize("accessor", sorted(GUARDED))
def test_the_four_forward_value_accessors_refuse_rather_than_answer_a_constant(both, accessor):
    """D7 in its purest form: with no edge leaving a ``formal_in``, ``flows_to_call`` is ``False``
    for every input, ``paths_between`` empty for every input and ``slice_forward`` the seed alone --
    each indistinguishable from a proved absence of flow. Both backends raise the same type with
    the same message."""
    with pytest.raises(CodeanalyzerExecutionException) as e:
        GUARDED[accessor](both)
    assert "formal_in" in str(e.value) and accessor in str(e.value)
    assert "can://" not in str(e.value)


@pytest.mark.parametrize("accessor", sorted(GUARDED))
def test_the_refusal_comes_after_the_arguments_and_the_names_are_judged(both, accessor):
    """A malformed argument is a ``ValueError`` and a name that misses is
    ``SelectorNotInGraph`` -- the gap does not swallow a caller's own error."""
    with pytest.raises(ValueError, match="depth"):
        {
            "slice_forward": lambda: both.slice_forward("conn", within=GET_STATEMENT, depth=0),
            "paths_between": lambda: both.paths_between("conn", "orderID", src_within=GET_STATEMENT, dst_within=CANCEL, depth=0),
            "flows_to_call": lambda: both.flows_to_call("conn", COMPLETE, within=GET_STATEMENT, depth=0),
            "flows_to_argument": lambda: both.flows_to_argument("conn", COMPLETE, "conn", within=GET_STATEMENT, depth=0),
        }[accessor]()
    with pytest.raises(SelectorNotInGraph):
        {
            "slice_forward": lambda: both.slice_forward("nope", within=GET_STATEMENT),
            "paths_between": lambda: both.paths_between("nope", "orderID", src_within=GET_STATEMENT, dst_within=CANCEL),
            "flows_to_call": lambda: both.flows_to_call("nope", COMPLETE, within=GET_STATEMENT),
            "flows_to_argument": lambda: both.flows_to_argument("conn", COMPLETE, "nope", within=GET_STATEMENT),
        }[accessor]()


def test_the_slice_and_the_call_graph_accessors_are_not_guarded(both, ref):
    """The gap is stated exactly, not widened: the call-graph half and the backward slice answer."""
    assert isinstance(ref.slice_backward("conn", within=GET_STATEMENT), Slice)
    assert both.reaches(SELL, GET_STATEMENT) and both.callers_of(GET_STATEMENT) and both.backward_cone([GET_STATEMENT])
