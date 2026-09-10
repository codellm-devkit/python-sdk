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
import json
import re
from collections import Counter

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
    and TypeScript one. Measured across the whole fixture: 2,038 ``ssa`` and 320 ``points-to``.
    codeanalyzer-java 3.0.3's port crossings are all ``ssa`` (1,171 before, +867), which is why only
    that tier moved."""
    page = ref.get_ddg(TO_JSON)
    assert page.total == 49 and all(isinstance(e, JDdgEdge) for e in page)
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
    ``J_DDG`` edges from a body node to itself, of 49. A page built the way ``PyNeo4jBackend``
    builds one -- binding the containment relationship twice -- would report 45 and call itself
    complete; this asserts the four are there and that ``total`` counts them."""
    page = ref.get_ddg(TO_JSON)
    loops = [e for e in page if e.src == e.dst]
    assert len(loops) == 4, [(e.src, e.dst) for e in page]
    assert page.total == len(page.edges) == 49 and page.complete


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
    assert len(first.edges) == 10 and first.total == 49 and not first.complete and first.next_cursor
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
def test_slice_backward_from_a_parameter_reaches_the_statements_behind_its_arguments(ref):
    """``J_PARAM_IN`` runs ``actual_in -> formal_in``, so a backward slice from a parameter reaches
    the argument vertex at every call site that passes one -- and, since codeanalyzer-java 3.0.3
    joins the port lattice to the statement graph, on through the statement that computed each
    argument and into its caller's own parameters. ``getStatement``'s ``conn`` was 35 nodes of two
    kinds on 3.0.2 (the seed plus 34 arguments); it is 465 nodes across 44 callables now, and the
    thing that changed is the analyzer's output, not this walk."""
    found = ref.slice_backward("conn", within=GET_STATEMENT, depth=None)
    assert found.total == 465 and found.complete
    assert {n.kind for n in found.nodes} == {"parameter", "argument", "statement", "call", "return", "branch", "loop", "entry"}
    assert len({n.callable for n in found.nodes}) == 44
    assert [n.ref for n in found.nodes] == sorted(n.ref for n in found.nodes)
    assert found.roots[0].name == "conn" and found.resolved.endswith("parameter 'conn'")
    assert ref.slice_backward("orderID", within=CANCEL, depth=None).total == 15


def test_a_slice_reports_a_cap_rather_than_returning_less_in_silence(ref):
    capped = ref.slice_backward("conn", within=GET_STATEMENT, depth=None, max_nodes=5)
    assert len(capped.nodes) == 5 and capped.total == 465 and not capped.complete


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


# ---- the port lattice --------------------------------------------------------------------------
#: The four accessors that refuse while no dependence edge leaves a ``formal_in`` vertex, with a
#: call that is otherwise valid. codeanalyzer-java 3.0.3 joins the two layers, so on this fixture
#: they answer -- and the refusal below is asserted against a payload with the crossings taken back
#: out, because the graph floor is 3.0.1 and an older emitter's output is still attachable.
#: All four are asked about the *same* flow -- ``cancelOrder``'s ``orderID`` reaching
#: ``getStatement``'s ``sql`` -- so the pair of tests below is one question answered twice: refused
#: on the disconnected payload, answered on the committed one.
GUARDED = {
    "slice_forward": lambda b: b.slice_forward("orderID", within=CANCEL),
    "paths_between": lambda b: b.paths_between("orderID", "sql", src_within=CANCEL, dst_within=GET_STATEMENT),
    "flows_to_call": lambda b: b.flows_to_call("orderID", GET_STATEMENT, within=CANCEL),
    "flows_to_argument": lambda b: b.flows_to_argument("orderID", GET_STATEMENT, "sql", within=CANCEL),
}

#: The four synthetic vertices of the L4 port lattice, in the analyzer's spelling.
PORTS = {"formal_in", "actual_in", "formal_out", "actual_out"}


def _without_port_crossings(payload: str) -> str:
    """The same fixture with every ``ddg`` edge that touches a port vertex removed.

    Which is exactly what codeanalyzer-java emitted before 3.0.3: the 3.0.2 copy of this fixture
    and this payload agree edge for edge (1,491 ``ddg`` edges; the 867 that 3.0.3 added are all
    crossings and all ``ssa``). Built by subtraction from the real thing rather than hand-written,
    so the refused shape is a shape the analyzer really emitted, and asserted on rather than
    assumed -- the count below fails if a regeneration ever changes what is being subtracted.

    ``cdg`` and ``summary`` are left alone: no ``cdg`` edge touches a port in either release, and
    every ``summary`` edge does in both (``actual_in -> actual_out``, which leaves no ``formal_in``
    and so was never what the probe measured).
    """
    payload_json = json.loads(payload)

    def walk(type_declaration):
        yield type_declaration
        for nested in (type_declaration.get("types") or {}).values():
            yield from walk(nested)

    kept = 0
    for unit in payload_json["application"]["symbol_table"].values():
        for top in (unit.get("types") or {}).values():
            for declaration in walk(top):
                for c in (declaration.get("callables") or {}).values():
                    kinds = {key: node.get("kind") for key, node in (c.get("body") or {}).items()}
                    c["ddg"] = [e for e in (c.get("ddg") or []) if kinds.get(e["src"]) not in PORTS and kinds.get(e["dst"]) not in PORTS]
                    kept += len(c["ddg"])
    assert kept == 1491, f"the pre-3.0.3 shape is {kept} ddg edges, not 1,491"
    return json.dumps(payload_json)


@pytest.fixture(scope="module", params=["local", "graph"])
def disconnected(request, analysis_json_a4):
    """Both backends over a payload whose port lattice carries no dependence edge."""
    return (_local if request.param == "local" else _graph)(_without_port_crossings(analysis_json_a4))


def test_the_port_lattice_carries_dependence_edges_in_both_directions(ref):
    """The measurement the four accessors rest on, asserted rather than assumed. codeanalyzer-java
    3.0.3 (codeanalyzer-java#227) emits ``@formal_in:k -> use``, ``return -> @formal_out``,
    ``statement -> <call>/actual_in:i`` and ``<call>/actual_out -> statement``, so the port layer is
    joined to the statement graph both ways: 867 of the fixture's 2,358 ``ddg`` edges touch a port,
    where 3.0.2 had none. ``cdg`` still touches none, in either release."""
    crossing, cdg_touching = Counter(), 0
    for c in _every_callable(ref):
        kinds = {k: n.kind for k, n in c.body.items()}
        for e in c.ddg or []:
            if kinds.get(e.src) in PORTS or kinds.get(e.dst) in PORTS:
                crossing[kinds.get(e.src), kinds.get(e.dst)] += 1
        cdg_touching += sum(1 for e in c.cdg or [] if kinds.get(e.src) in PORTS or kinds.get(e.dst) in PORTS)
    assert sum(crossing.values()) == 867 and cdg_touching == 0
    assert crossing["formal_in", "call"] == 272 and crossing["statement", "actual_in"] == 129
    assert crossing["return", "formal_out"] == 77 and crossing["actual_out", "statement"] == 76
    assert ref._ports_carry_dependence


def test_every_ddg_endpoint_is_a_body_node(ref):
    """codeanalyzer-java 3.0.2 emitted 87 of daytrader8's 5,434 ``ddg`` edges naming an endpoint it
    never emitted as a body node -- the only measured disagreement between ``analysis.json`` and
    the Neo4j projection, which materialises nodes from ``body{}``. 3.0.3 drops them
    (codeanalyzer-java#228). It was 0 on this fixture in both releases -- the defect was
    whole-project only -- so this is not a regression *fix* here but the guard that would fail if
    dangling endpoints came back, on the fixture the offline suite can afford to walk."""
    dangling = [(c.id, e.src, e.dst) for c in _every_callable(ref) for e in (c.ddg or []) + (c.cdg or []) + (c.cfg or []) if e.src not in c.body or e.dst not in c.body]
    assert dangling == []


@pytest.mark.parametrize("dangling, carries", [(True, False), (False, True)], ids=["target-never-emitted", "target-emitted"])
def test_the_port_probe_counts_only_an_edge_whose_target_is_a_node(dangling, carries):
    """One boolean, one definition. The Neo4j spelling matches
    ``(b:JBodyNode)-[…]->(m:JBodyNode)``, so it can only see an edge whose **target was emitted as a
    node**; the in-memory one counted any outgoing edge of a ``formal_in``, materialised target or
    not. codeanalyzer-java 3.0.2 emitted 87 of daytrader8's 5,434 ddg edges naming an endpoint it
    never emitted (#228, fixed in 3.0.3), which is exactly the shape that made the two disagree --
    and this boolean decides whether four accessors raise or answer. The clause stays with the
    analyzer fixed, because the graph floor is 3.0.1 and an older emitter's output is still
    attachable.

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
def test_the_four_forward_value_accessors_answer_once_the_ports_carry_dependence(ref, accessor):
    """The lift, on the committed fixture and with no SDK change: what refused on 3.0.2 now returns
    a real answer that varies with the program. Asserted as a shape, not a constant -- a
    ``slice_forward`` that reached only its seed, or an empty path list, would be the ambiguous
    empty the refusal existed to prevent."""
    answered = GUARDED[accessor](ref)
    assert {
        "slice_forward": lambda r: r.total > 1 and len({n.callable for n in r.nodes}) > 1,
        "paths_between": lambda r: bool(r.paths),
        "flows_to_call": lambda r: r is True,
        "flows_to_argument": lambda r: r is True,
    }[accessor](answered), answered


@pytest.mark.parametrize("accessor", sorted(GUARDED))
def test_the_four_refuse_on_a_payload_whose_ports_carry_nothing(disconnected, accessor):
    """D7 in its purest form, pinned in the direction that still matters: with no edge leaving a
    ``formal_in``, ``flows_to_call`` is ``False`` for every input, ``paths_between`` empty for every
    input and ``slice_forward`` the seed alone -- each indistinguishable from a proved absence of
    flow. The graph floor is 3.0.1, so a graph emitted by 3.0.1 or 3.0.2 is still attachable and
    still needs this, and ``--l3-engine wala`` leaves ``formal_in`` unattached even on 3.0.3. Both
    backends raise the same type with the same message, and the decision reads the data -- there is
    no analyzer version anywhere in it."""
    assert not disconnected._ports_carry_dependence
    with pytest.raises(CodeanalyzerExecutionException) as e:
        GUARDED[accessor](disconnected)
    assert "formal_in" in str(e.value) and accessor in str(e.value)
    assert "can://" not in str(e.value)


@pytest.mark.parametrize("accessor", sorted(GUARDED))
def test_the_refusal_comes_after_the_arguments_and_the_names_are_judged(disconnected, accessor):
    """A malformed argument is a ``ValueError`` and a name that misses is
    ``SelectorNotInGraph`` -- the gap does not swallow a caller's own error."""
    both = disconnected
    with pytest.raises(ValueError, match="depth"):
        {
            "slice_forward": lambda: both.slice_forward("orderID", within=CANCEL, depth=0),
            "paths_between": lambda: both.paths_between("orderID", "sql", src_within=CANCEL, dst_within=GET_STATEMENT, depth=0),
            "flows_to_call": lambda: both.flows_to_call("orderID", GET_STATEMENT, within=CANCEL, depth=0),
            "flows_to_argument": lambda: both.flows_to_argument("orderID", GET_STATEMENT, "sql", within=CANCEL, depth=0),
        }[accessor]()
    with pytest.raises(SelectorNotInGraph):
        {
            "slice_forward": lambda: both.slice_forward("nope", within=CANCEL),
            "paths_between": lambda: both.paths_between("nope", "sql", src_within=CANCEL, dst_within=GET_STATEMENT),
            "flows_to_call": lambda: both.flows_to_call("nope", GET_STATEMENT, within=CANCEL),
            "flows_to_argument": lambda: both.flows_to_argument("orderID", GET_STATEMENT, "nope", within=CANCEL),
        }[accessor]()


def test_a_parameter_reaches_a_callees_parameter_three_frames_down(ref):
    """The witness python-sdk#354 asks for, and the one thing that could not be written before it:
    ``cancelOrder(Integer, boolean)``'s ``orderID`` reaches ``getStatement``'s ``sql`` across
    **three** call boundaries -- through ``cancelOrder(Connection, Integer)`` and
    ``updateOrderStatus`` -- and the path says how, hop by hop, in the caller's vocabulary rather
    than as a boolean.

    The shape is asserted, not just the answer: nine hops alternating ``data`` inside a frame and
    ``argument`` across one, every ``data`` hop carrying a variable and ``ssa`` provenance, every
    ``argument`` hop landing on the next callable's parameter, and consecutive hops joining up.
    ``flows_to_call`` and ``flows_to_argument`` agree with it, which is what makes them a
    statement about the same walk.
    """
    paths = ref.paths_between("orderID", "sql", src_within=CANCEL, dst_within=GET_STATEMENT, depth=None)
    assert paths.complete and len(paths.paths) == 2
    shortest = min(paths.paths, key=lambda p: len(p.hops))
    assert [h.via for h in shortest.hops] == ["data", "data", "argument"] * 3
    frames = [h.frm.callable for h in shortest.hops] + [shortest.hops[-1].to.callable]
    assert frames[0] == CANCEL and frames[-1] == GET_STATEMENT
    assert len(dict.fromkeys(frames)) == 4, frames
    assert all(shortest.hops[i].to.ref == shortest.hops[i + 1].frm.ref for i in range(len(shortest.hops) - 1))
    for hop in shortest.hops:
        if hop.via == "data":
            assert hop.var and hop.prov == ["ssa"]
        else:
            assert hop.to.kind == "parameter" and hop.var is None and hop.prov == []
    assert shortest.weakest.prov == ["ssa"]
    assert ref.flows_to_call("orderID", GET_STATEMENT, within=CANCEL)
    assert ref.flows_to_argument("orderID", GET_STATEMENT, "sql", within=CANCEL)


def test_the_slice_and_the_call_graph_accessors_are_not_guarded(both, ref):
    """The gap is stated exactly, not widened: the call-graph half and the backward slice answer."""
    assert isinstance(ref.slice_backward("conn", within=GET_STATEMENT), Slice)
    assert both.reaches(SELL, GET_STATEMENT) and both.callers_of(GET_STATEMENT) and both.backward_cone([GET_STATEMENT])


# ----------------------------------------------------------------------------------------------
# Leg 4b, Task 7: the local ``taint`` walk, offline.
#
# ``taint()`` is a refutation instrument: its ``exhausted`` list certifies "no flow exists between
# this source and this sink", so **over-cutting is far worse than under-cutting**. Cutting more than
# the caller named removes paths, removing paths adds pairs to ``exhausted``, and a wrong
# ``exhausted`` closes an alert on a live flow. Under-cutting merely over-reports. Every count below
# was measured off this fixture through ``shortest_walks`` directly, never read back out of the
# implementation, and every cut is asserted to leave its *siblings* standing.
#
# Sources are a **callee's** ``formal_in`` (Ruling J), for the reason the Python fixture documents:
# a caller's own parameter and its def-site are disjoint upstream.
# ----------------------------------------------------------------------------------------------
BUY = f"{DIRECT}.buy(java.lang.String, java.lang.String, double, int)"
COMPLETE_ORDER = f"{DIRECT}.completeOrder(java.lang.Integer, boolean)"
SET_IN_GLOBAL_TXN = f"{DIRECT}.setInGlobalTxn(boolean)"
ROLL_BACK = f"{DIRECT}.rollBack(java.sql.Connection, java.lang.Exception)"

#: As a caller writes them: four pairs with 2 + 4 + 1 + 2 = 9 witnesses at unbounded depth.
TAINT_SOURCES = [("orderProcessingMode", BUY), ("orderID", COMPLETE_ORDER)]
TAINT_SINKS = [("inGlobalTxn", SET_IN_GLOBAL_TXN), ("conn", ROLL_BACK)]
#: One pair, 2 witnesses -- the only shape a cap of 1 can be measured on.
TAINT_PAIR = ([("orderProcessingMode", BUY)], [("inGlobalTxn", SET_IN_GLOBAL_TXN)])


def test_the_local_java_walk_does_not_re_ask_the_level_gate(ref):
    """Ruling F **as amended**: Python's and TypeScript's local walks open with
    ``self._require_dataflow()``, and Java's must not -- Java carries that gate on the ABC's
    ``taint()``, which asks it before the walk is entered. The hook is called directly on a backend
    whose ``analysis_level`` says level 1: it answers rather than raising, because by the time a walk
    runs the question has been settled, and ``taint()`` is asserted to raise on the same backend.

    Two gates, not one: the port-lattice refusal is also ``taint()``'s, in the same place the five
    sibling flow accessors ask for it."""
    ref.analysis_level = "symbol_table"
    try:
        assert ref._taint_walk([], [], cuts=[], cut_callables=[], depth=None, max_paths=1) == ([], {})
        with pytest.raises(CodeanalyzerUsageException, match="program_dependency_graph"):
            ref.taint(TAINT_SOURCES, TAINT_SINKS)
    finally:
        ref.analysis_level = "system_dependency_graph"


def test_taint_refuses_while_the_port_lattice_carries_no_dependence_edge(disconnected):
    """The sixth guarded accessor. On a pre-3.0.3 emission every pair would come back refuted for a
    reason that has nothing to do with the program -- an ``exhausted`` list that is an artefact of
    the analyzer -- which is exactly the output this leg must never produce. Both backends refuse,
    and the graph backend refuses before it would have reached Cypher."""
    with pytest.raises(CodeanalyzerExecutionException, match="port"):
        disconnected.taint(TAINT_SOURCES, TAINT_SINKS)


def _with_param_vars(payload: str) -> str:
    """The same fixture with a ``var`` on every ``param_in``/``param_out`` edge -- the shape
    codeanalyzer-java 3.1.2 (codeanalyzer-java#250) emits and this fixture, at 3.1.0, does not.

    Built by **addition** the way :func:`_without_port_crossings` is built by subtraction, and for
    the same reason: the shape under test has to come from the real payload rather than from a
    hand-written one, so a regeneration that changes what is being added fails the count below.

    The name written on each edge is the formal position its own endpoint already spells --
    ``@formal_in:0`` becomes ``p0``, ``@formal_out`` becomes ``ret`` -- so a label that reached the
    adjacency off the *wrong* edge shows up as the wrong name rather than as a name that is merely
    present. All 355 endpoints spell one, asserted rather than assumed.
    """
    payload_json = json.loads(payload)
    application = payload_json["application"]
    named = 0
    for edge in application["param_in"]:
        edge["var"] = "p" + re.search(r"@formal_in:(\d+)$", edge["dst"]).group(1)
        named += 1
    for edge in application["param_out"]:
        assert edge["src"].endswith("@formal_out"), f"a param_out edge starting somewhere other than a formal_out: {edge['src']}"
        edge["var"] = "ret"
        named += 1
    assert named == 355, f"the 3.1.2 shape names all 355 param edges, not {named}"
    return json.dumps(payload_json)


@pytest.fixture(scope="module")
def param_vars(analysis_json_a4):
    """The local backend over a payload shaped like codeanalyzer-java 3.1.2's."""
    return _local(_with_param_vars(analysis_json_a4))


def test_a_java_param_edge_carries_the_variable_the_analyzer_put_on_it(ref, param_vars):
    """``J_PARAM_IN``/``J_PARAM_OUT`` must reach the adjacency with whatever ``var`` the payload put
    on them. This backend hardcoded ``None``, which was true until codeanalyzer-java 3.1.2
    (codeanalyzer-java#250) added the property, and became a lie that cost two things:
    ``_edge_vars_in`` could not see a call-crossing variable, so ``resolve_sanitizers`` refused a
    real one as nonexistent (Ruling A exists to prevent that); and ``allow_edge``'s
    ``var == c["var"]`` could never match a param edge, so a scoped variable cut was structurally
    incapable of cutting at a call boundary.

    **This fixture cannot witness the fix**, so the fix is witnessed on a payload built from it:
    a4 was emitted by 3.1.0, whose 258 ``param_in`` and 97 ``param_out`` edges carry no ``var`` key
    at all, and :func:`_with_param_vars` writes the ones 3.1.2 would. What runs is the real
    ``getattr(e, "var", None)`` at ``JCodeanalyzer._sdg``, not a constructed label: the count and the
    name of every param edge in the adjacency come back out of the traversal, and the consumer the
    hardcoded ``None`` blinded -- ``_edge_vars_in`` -- gains exactly the two crossing names ``sell``
    scopes and nothing else. Whether the pinned analyzer writes ``var`` in practice is unverified in
    this repo: there is no jar and no JVM.
    """
    old_labels = [(rel, var) for outs in ref._sdg()[0]["forward"].values() for labels in outs.values() for rel, var, _prov in labels if rel.startswith("J_PARAM")]
    assert len(old_labels) == 355, "a4 was emitted by 3.1.0: 258 param_in + 97 param_out, none carrying a var"
    assert all(var is None for _rel, var in old_labels), "this fixture's param edges carry no var; what follows is asserted on the 3.1.2 shape"

    new_labels = [(rel, var) for outs in param_vars._sdg()[0]["forward"].values() for labels in outs.values() for rel, var, _prov in labels if rel.startswith("J_PARAM")]
    assert Counter(new_labels) == {
        ("J_PARAM_IN", "p0"): 157,
        ("J_PARAM_IN", "p1"): 83,
        ("J_PARAM_IN", "p2"): 8,
        ("J_PARAM_IN", "p3"): 6,
        ("J_PARAM_IN", "p4"): 4,
        ("J_PARAM_OUT", "ret"): 97,
    }, "every param edge reaches the adjacency under its own formal's name"

    scope = ref.resolve_callable(SELL).ref
    assert param_vars._edge_vars_in(scope) - ref._edge_vars_in(scope) == {"p0", "p1"}, "sell's 12 crossings bind two distinct formals, and a sanitizer can now name either"


def test_a_java_variable_cut_severs_a_call_boundary_and_only_the_scope_that_named_it(ref, param_vars):
    """The payoff, end to end: the scoped variable cut over a hop that *is* a call boundary. On the
    3.1.0 shape ``("p0", BUY)`` is refused as nonexistent -- ``_edge_vars_in`` cannot see a name that
    reached the adjacency as ``None`` -- which is Ruling A refusing a real dataflow variable, the
    exact failure the hardcoded ``None`` caused. On the 3.1.2 shape the same cut severs ``buy``'s
    ``J_PARAM_IN`` crossings and takes both of its pairs from 6 witnesses to ``exhausted``.

    ``completeOrder``'s 3 witnesses are untouched, and cutting ``p0`` *under* ``completeOrder``
    changes nothing at all: ``allow_edge`` reads the hop's start node, so a cut severs only the
    callable the caller scoped it to. That is what separates a scoped cut from a cut on every param
    hop in the application -- and over-cutting is the one error this instrument must not make."""
    with pytest.raises(SelectorNotInGraph, match="'p0'"):
        ref.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("p0", BUY)], max_paths=10)

    assert len(param_vars.taint(TAINT_SOURCES, TAINT_SINKS, max_paths=10).paths) == 9, "naming the param vars changes no unsanitized answer"
    cut = param_vars.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("p0", BUY)], max_paths=10)
    assert len(cut.paths) == 3 and {p.hops[0].frm.callable for p in cut.paths} == {COMPLETE_ORDER}
    assert cut.exhausted == [("orderProcessingMode", "inGlobalTxn"), ("orderProcessingMode", "conn")] and cut.complete is True

    elsewhere = param_vars.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("p0", COMPLETE_ORDER)], max_paths=10)
    assert len(elsewhere.paths) == 9 and elsewhere.exhausted == [], "the same name under another scope cuts nothing"


def test_the_local_java_walk_finds_the_measured_witnesses_and_refutes_nothing(ref):
    """The anchor the rest of this group narrows: 9 witnesses over four pairs, each ending on a
    ``J_PARAM_IN`` crossing into the sink callable's parameter. A walk that stopped at a call
    boundary would still return rows; only the last hop says it crossed one."""
    r = ref.taint(TAINT_SOURCES, TAINT_SINKS, max_paths=10)
    assert len(r.paths) == 9 and r.exhausted == [] and r.complete is True
    assert all(p.hops[-1].via == "argument" and p.hops[-1].to.kind == "parameter" for p in r.paths)
    assert Counter(tuple(h.via for h in p.hops) for p in r.paths) == {
        ("data", "control", "control", "data", "argument"): 4,
        ("data", "control", "data", "argument"): 3,
        ("data", "data", "argument"): 2,
    }, "a control hop is a real dependence and the four-hop chain through it is the majority here"


def test_the_local_java_walk_returns_one_row_past_the_cap_so_truncation_is_never_silent(ref):
    """A walk that capped at ``max_paths`` rather than ``max_paths + 1`` would return a full-looking
    result with ``complete=True`` -- a silent bound, which E5 forbids. ``taint()`` cannot detect
    that from the rows it is handed, so the walk is tested here or nowhere."""
    at_one = ref.taint(*TAINT_PAIR, max_paths=1)
    assert len(at_one.paths) == 1 and at_one.complete is False
    at_two = ref.taint(*TAINT_PAIR, max_paths=2)
    assert len(at_two.paths) == 2 and at_two.complete is True


def test_the_local_java_cap_keeps_a_prefix_of_one_total_order(ref):
    """*Which* witness survives is stated, not incidental: ``shortest_walks``' replay sorts equal
    length branches by ``(via, var, to)`` -- the components ``hop_sort_key`` documents, and what
    Cypher's ``ORDER BY length(p), key`` produces -- so a cap is a prefix of a total order rather
    than whichever branch the recursion reached first. Both witnesses of this pair are three hops
    ``orderProcessingMode`` then ``arg0`` then the parameter crossing; they differ further in."""
    one, many = ref.taint(*TAINT_PAIR, max_paths=1), ref.taint(*TAINT_PAIR, max_paths=5)
    assert one.paths == many.paths[:1], "the local taint cap is not a prefix of one total order"
    assert [h.var for h in one.paths[0].hops] == ["orderProcessingMode", "arg0", None]


def test_the_local_java_walk_runs_once_per_distinct_pair_not_once_per_selector(ref):
    """Two selectors naming one position are one pair. Walking it twice would report every witness
    twice and make a cap of *m* yield *2m* -- the graph side gets this free from ``a.id IN $srcs``,
    so the local side has to deduplicate to match."""
    once = ref.taint(*TAINT_PAIR, max_paths=10)
    twice = ref.taint([("orderProcessingMode", BUY), ("orderProcessingMode", BUY)], [("inGlobalTxn", SET_IN_GLOBAL_TXN)], max_paths=10)
    assert len(twice.paths) == len(once.paths) == 2


def test_a_local_java_callable_cut_severs_its_own_pairs_and_leaves_the_siblings_alone(ref):
    """Cutting ``setInGlobalTxn`` refutes the two pairs that end in it and leaves the ``rollBack``
    pairs' 4 + 2 witnesses untouched. A cut that closed all four would pass a naive "the sanitizer
    worked" assertion while being the failure this leg is built to avoid."""
    r = ref.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[SET_IN_GLOBAL_TXN], max_paths=10)
    assert len(r.paths) == 6, "the two rollBack pairs survive: 4 + 2 witnesses"
    assert r.exhausted == [("orderProcessingMode", "inGlobalTxn"), ("orderID", "inGlobalTxn")]
    assert r.complete is True


def test_a_local_java_callable_cut_that_contains_the_source_yields_no_walk(ref):
    """``allow_node`` is checked against ``src`` up front, because ``src`` is never itself a
    ``steps()`` destination for either pass to filter. Without that check a source inside a cut
    callable would still emit its first hop -- and a witness through a callable the caller declared
    sanitized is a false positive with the sanitizer's own name on it."""
    r = ref.taint([("orderProcessingMode", BUY)], TAINT_SINKS, sanitizers=[BUY], max_paths=10)
    assert r.paths == []
    assert r.exhausted == [("orderProcessingMode", "inGlobalTxn"), ("orderProcessingMode", "conn")]
    assert r.complete is True


def test_a_local_java_variable_cut_severs_a_call_boundary_and_only_the_pairs_that_cross_it(ref):
    """``arg0`` is the formal that ``buy``'s calls bind across a ``J_PARAM_IN``, so cutting it inside
    ``buy`` is a cut *at* a call boundary -- the thing the hardcoded ``None`` above made impossible.
    Both of ``buy``'s pairs are refuted and both of ``completeOrder``'s keep every witness."""
    r = ref.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("arg0", BUY)], max_paths=10)
    assert len(r.paths) == 3, "completeOrder's two pairs survive: 1 + 2 witnesses"
    assert {p.hops[0].frm.callable for p in r.paths} == {COMPLETE_ORDER}
    assert r.exhausted == [("orderProcessingMode", "inGlobalTxn"), ("orderProcessingMode", "conn")]
    assert r.complete is True


def test_a_local_java_variable_cut_is_scoped_to_the_callable_it_names(ref):
    """The decisive scoping witness: ``arg0`` is a real edge variable under **both** ``buy`` and
    ``completeOrder`` (so Ruling A admits either), and cutting it under ``completeOrder`` severs
    nothing at all -- all 9 witnesses stand and nothing is refuted. An unscoped cut on a name that
    recurs like this would sever flows the caller never named: over-cut, false refutation."""
    assert "arg0" in ref._edge_vars_in(ref.resolve_callable(COMPLETE_ORDER).ref)
    r = ref.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("arg0", COMPLETE_ORDER)], max_paths=10)
    assert len(r.paths) == 9 and r.exhausted == []


def test_a_local_java_variable_sanitizer_that_names_nothing_still_raises(ref):
    """Ruling A widened the domain to edge variables; it did not remove the check. A typo is refused
    loudly rather than silently cutting nothing -- a sanitizer that cuts nothing is the over-report
    direction, but a caller who believes it cut something is the over-cut direction one step later."""
    with pytest.raises(SelectorNotInGraph):
        ref.taint(TAINT_SOURCES, TAINT_SINKS, sanitizers=[("nosuchvar", BUY)])
