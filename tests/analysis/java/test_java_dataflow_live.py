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

r"""Live parity for the Java dataflow surface (leg 3b, Task 2), against a real graph.

The offline suite proves the policy over the four-unit fixture; this proves the half only a server
can: that the Cypher the graph backend issues returns the edges the analyzer wrote, over the
**whole** daytrader8 application in a database that also holds ThingsBoard — so a scope leak shows
up as a larger answer rather than as nothing.

**The self-loop test is the point of this file** (python-sdk#349). ``Log.printCollection`` carries
25 ``J_DDG`` edges, 11 of them from a body node to itself. The doubled-containment spelling Python's
backend uses returns **14** and calls itself complete; this suite runs both and asserts the
difference, so the shape cannot arrive here by a later refactor.

Since codeanalyzer-java 3.0.3 the port lattice is joined to the statement graph, so the four
forward value accessors answer here rather than refusing (python-sdk#354): the witness is
``cancelOrder``'s ``orderID`` reaching ``getStatement``'s ``sql`` across three call boundaries, run
over both backends and compared path for path. The refusal itself is pinned offline, against a
payload with the crossings subtracted.

Same environment as ``test_java_addressing_live.py``::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    CLDK_TEST_NEO4J_APP=daytrader8 \
    CLDK_TEST_JAVA_PROJECT=/path/to/project \
    CLDK_TEST_JAVA_CACHE=/path/to/dir \        # a level-4 reference analysis.json
    uv run pytest tests/analysis/java/test_java_dataflow_live.py

The one timed test runs under the repo's ``timed`` marker, which pauses the coverage tracer around
the call: a wall clock measured under instrumentation measures the tracer.

Read-only, like every other Neo4j suite here.
"""

import json
import logging
import os
import statistics
import time
from pathlib import Path

import pytest

from cldk.analysis.commons.results import SliceNode
from cldk.utils.exceptions import SelectorNotInGraph

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")
SCALE_APP = os.environ.get("CLDK_TEST_NEO4J_SCALE_APP", "thingsboard")

REFERENCE_LEVEL = "system_dependency_graph"

UTIL = "com.ibm.websphere.samples.daytrader.util"
DIRECT = "com.ibm.websphere.samples.daytrader.impl.direct"
#: The regression witness: 25 ``J_DDG`` edges, 11 of them self-loops (measured on the graph).
PRINT_COLLECTION = f"{UTIL}.Log.printCollection(java.util.Collection)"
GET_STATEMENT = f"{DIRECT}.TradeDirect.getStatement(java.sql.Connection, java.lang.String)"
SELL = f"{DIRECT}.TradeDirect.sell(java.lang.String, java.lang.Integer, int)"

#: A page's wall-clock ceiling on the scale corpus. Measured on ThingsBoard (496,821 body nodes),
#: median of 5 with the first discarded, through the driver: ``get_ddg`` 29.1 ms on the largest
#: callable (482 edges), ``get_cfg`` 12.2 ms, ``get_cdg`` 10.9 ms, ``callers_of`` 9.9 ms,
#: ``slice_backward`` 22.2 ms (604 nodes). The ceiling is two orders of magnitude above that: it is
#: here to catch a *scan*, which on this graph would be seconds, not to police tens of milliseconds
#: on someone else's laptop.
_WALL_CLOCK_CEILING = 2.0


def _reference_cache_is_level_4() -> bool:
    if not JAVA_CACHE:
        return False
    try:
        return int(json.loads((Path(JAVA_CACHE) / "analysis.json").read_text(encoding="utf-8")).get("max_level", 0)) >= 4
    except (OSError, ValueError, AttributeError):
        return False


def _reachable() -> bool:
    if not (JAVA_APP and JAVA_PROJECT and _reference_cache_is_level_4()):
        return False
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="needs a pre-populated Neo4j Java graph + a level-4 reference cache (set CLDK_TEST_NEO4J_* / CLDK_TEST_JAVA_*)")


@pytest.fixture(scope="module")
def backends():
    from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
    from cldk.analysis.java.neo4j import JNeo4jBackend

    ref = JCodeanalyzer(project_dir=JAVA_PROJECT, analysis_json_path=JAVA_CACHE, analysis_level=REFERENCE_LEVEL, eager_analysis=False, target_files=None)
    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=JAVA_APP)
    yield ref, neo
    neo.close()


@pytest.fixture(scope="module")
def scale():
    from cldk.analysis.java.neo4j import JNeo4jBackend

    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=SCALE_APP)
    if not neo._run("MATCH (a:JApplication {name: $app}) RETURN a LIMIT 1", app=SCALE_APP):
        neo.close()
        pytest.skip(f"the graph does not hold application {SCALE_APP!r}")
    yield neo
    neo.close()


def _with_bodies(ref, limit):
    """The J-1 keys of the callables carrying the most ``ddg`` edges — the ones worth comparing."""
    rows = sorted(((len(c.ddg or []), key) for key, row in ref._addressing.by_key.items() for c in [row.callable]), reverse=True)
    return [key for _, key in rows[:limit]]


def _dangling(ref):
    """The ``analysis.json`` ddg edges whose endpoint is **not** a body node of their own callable
    — the analyzer defect the next two tests measure. See
    :func:`test_the_two_backends_report_the_same_ddg_edge_for_edge`."""
    out = []
    for key, row in ref._addressing.by_key.items():
        c = row.callable
        body = set(c.body or {})
        out += [(key, e) for e in (c.ddg or []) if e.src not in body or e.dst not in body]
    return out


# ---- the per-callable graphs, edge for edge ----------------------------------------------------
def test_the_three_graphs_agree_edge_for_edge_on_the_busiest_callables(backends):
    """Not counts: the whole page, in the canonical order, on both backends.

    ``get_ddg`` is compared on the edges whose endpoints the analyzer actually emitted as body
    nodes. That filter was load-bearing up to codeanalyzer-java 3.0.2, which emitted edges naming
    endpoints it never emitted as nodes; on 3.0.3 it removes nothing (the next test asserts the two
    sides are equal as *sets*), and it stays because it is what makes this comparison a statement
    about the projection rather than about the analyzer.
    """
    ref, neo = backends
    keys = _with_bodies(ref, 40)
    assert len(keys) == 40
    seen = 0
    for key in keys:
        anchored = {ref.resolve_callable(key).ref + ("" if k.startswith("@") else "@") + k for k in ref._addressing.by_key[key].callable.body}
        for accessor in ("get_cfg", "get_cdg", "get_ddg"):
            a, b = getattr(ref, accessor)(key), getattr(neo, accessor)(key)
            kept = [e for e in a.edges if e.src in anchored and e.dst in anchored]
            assert kept == list(b.edges), f"{accessor} {key}"
            assert len(kept) == b.total, f"{accessor} {key}"
            assert a.complete and b.complete
            seen += len(kept)
    assert seen > 1000, f"the comparison covered only {seen} edges"


def test_the_two_backends_report_the_same_ddg_edge_for_edge(backends):
    """**The one place the two Java backends used to disagree, and it was the analyzer's.**

    codeanalyzer-java up to 3.0.2 emitted ddg edges naming an endpoint it never emitted as a body
    node — 87 of daytrader8's 5,434, over 38 distinct keys all of the shape ``<line>:0``, all on
    ``points-to`` edges — and the Neo4j emitter materialises nodes from the ``body{}`` map, so those
    edges could not be projected and the graph reported 5,347. 3.0.3 drops them
    (codeanalyzer-java#228).

    So this asserts the equality rather than the difference: **10,430 ddg edges on both sides, set
    for set**, with 0 dangling endpoints in the payload. The subtraction stays in both directions —
    a dangling endpoint coming back fails here, and it fails naming what it is rather than as an
    off-by-87 in an edge count.
    """
    ref, neo = backends
    from cldk.analysis.java.backend import java_body_node_id

    local = {(java_body_node_id(c.id, e.src), java_body_node_id(c.id, e.dst), e.var, tuple(e.prov)) for _, c in ref._callables.values() for e in (c.ddg or [])}
    rows = neo._run(
        "MATCH (a:JBodyNode)-[r:J_DDG]->(b:JBodyNode) WHERE a.id STARTS WITH $prefix AND b.id STARTS WITH $prefix RETURN a.id AS s, b.id AS d, r.var AS v, r.prov AS p",
        prefix=neo._scope_prefix,
    )
    graph = {(r["s"], r["d"], r["v"], tuple(r["p"] or ())) for r in rows}
    assert (len(local), len(graph)) == (10430, 10430)
    assert graph - local == set(), "the graph carries a ddg edge the analyzer's own payload does not"
    assert local - graph == set(), "the analyzer emitted a ddg edge the graph could not project"
    assert _dangling(ref) == [], "codeanalyzer-java#228 is back: a ddg endpoint that is not a body node"


def test_every_emitted_endpoint_names_a_body_node_the_graph_holds(backends):
    """The ids the in-memory backend composes (``<callable id>@<body key>``) against the graph's own
    ``b.id`` — the join that makes a page from either backend addressable by the other. Restricted
    to the endpoints the analyzer emitted as body nodes, for the reason above."""
    ref, neo = backends
    ids = set()
    for key in _with_bodies(ref, 40):
        c = ref._addressing.by_key[key].callable
        body = set(c.body or {})
        for e in c.ddg or []:
            if e.src in body and e.dst in body:
                ids.update((ref.resolve_callable(key).ref + ("" if k.startswith("@") else "@") + k) for k in (e.src, e.dst))
    assert len(ids) > 300
    rows = neo._run("UNWIND $ids AS i MATCH (b:JBodyNode {id: i}) RETURN count(b) AS n", ids=sorted(ids))
    assert rows[0]["n"] == len(ids), "an endpoint names nothing in the graph"


def test_the_page_contains_the_self_loops_and_the_349_shape_would_not(backends):
    """**The python-sdk#349 regression guard, on the graph that could carry the bug.**

    ``Log.printCollection`` has 25 ``J_DDG`` edges on codeanalyzer-java 3.0.3 (20 on 3.0.1, before
    the port crossings) and 11 of them run from a body node to itself. The doubled-containment
    spelling binds ``J_HAS_BODY_NODE`` twice, which Cypher's relationship-uniqueness rule forbids
    from matching one relationship twice, so it drops every self-loop *and* would compute ``total``
    from the same MATCH — reporting 14 of 14, complete. Both numbers are asserted, so the
    difference is a fact of this test and not of a comment.
    """
    ref, neo = backends
    page = neo.get_ddg(PRINT_COLLECTION)
    loops = [e for e in page if e.src == e.dst]
    assert (page.total, len(page.edges), len(loops)) == (25, 25, 11) and page.complete
    assert list(ref.get_ddg(PRINT_COLLECTION).edges) == list(page.edges)

    callable_id = neo.resolve_callable(PRINT_COLLECTION).ref
    doubled = "MATCH (c:JCallable {id:$c})-[:J_HAS_BODY_NODE]->(s:JBodyNode)-[r:J_DDG]->(d:JBodyNode)<-[:J_HAS_BODY_NODE]-(c) RETURN count(r) AS n"
    dropped = neo._run(doubled, c=callable_id)[0]["n"]
    assert dropped == 14, "the doubled-containment spelling no longer loses the self-loops; re-derive the guard"


def test_the_whole_application_carries_its_self_loops(backends):
    """The corpus fact, so a graph rebuild that loses them fails here: daytrader8 has 133 ``J_DDG``
    self-loops of the reference database's 978."""
    _, neo = backends
    rows = neo._run("MATCH (b:JBodyNode)-[r:J_DDG]->(b) WHERE b.id STARTS WITH $prefix RETURN count(r) AS n", prefix=neo._scope_prefix)
    assert rows[0]["n"] == 133


def test_paging_agrees_across_backends(backends):
    """A cursor minted by one backend names the same position for the other."""
    ref, neo = backends
    key = _with_bodies(ref, 1)[0]
    a, b = ref.get_ddg(key, page_size=5), neo.get_ddg(key, page_size=5)
    assert list(a.edges) == list(b.edges) and a.next_cursor == b.next_cursor and not a.complete
    assert list(ref.get_ddg(key, page_size=5, cursor=b.next_cursor).edges) == list(neo.get_ddg(key, page_size=5, cursor=a.next_cursor).edges)


def test_the_two_provenance_tiers_are_the_only_ones(backends):
    """Java's DDG has ``ssa`` and ``points-to`` and nothing else — 324,959 and 1,134 edges across
    the whole database. Neither backend may invent a third or collapse the two."""
    _, neo = backends
    rows = neo._run("MATCH ()-[r:J_DDG]->() RETURN r.prov AS prov, count(*) AS n ORDER BY n DESC")
    assert {tuple(r["prov"]) for r in rows} == {("ssa",), ("points-to",)}
    assert dict((tuple(r["prov"]), r["n"]) for r in rows) == {("ssa",): 324959, ("points-to",): 1134}


def test_the_body_node_kind_vocabulary_is_what_the_graph_holds(backends):
    """Twelve kinds, one of which (``switch``) is outside
    :attr:`~cldk.analysis.commons.results.SliceNode.KINDS` — that list was derived from
    codeanalyzer-python's vocabulary and Python has no switch statement. It is reported as the
    analyzer spells it rather than dropped or renamed."""
    _, neo = backends
    kinds = {r["k"] for r in neo._run("MATCH (b:JBodyNode) RETURN DISTINCT b.kind AS k")}
    assert kinds == {"actual_in", "actual_out", "branch", "call", "entry", "exit", "formal_in", "formal_out", "loop", "return", "statement", "switch"}
    assert "switch" not in SliceNode.KINDS and kinds - {"switch"} - {"formal_in", "actual_in", "formal_out", "actual_out"} <= SliceNode.KINDS


# ---- the call graph ----------------------------------------------------------------------------
def test_callers_and_callees_agree_on_every_callable(backends):
    """All 1,216 callables, both directions, node for node."""
    ref, neo = backends
    keys = sorted(ref._addressing.by_key)
    assert len(keys) == 1216
    edges = 0
    for key in keys:
        assert ref.callers_of(key) == neo.callers_of(key), key
        assert ref.callees_of(key) == neo.callees_of(key), key
        edges += len(ref.callees_of(key))
    assert edges > 1000, f"the comparison covered only {edges} call edges"


def test_reaches_agrees_and_the_bound_is_asymmetric(backends):
    """Both halves of E5 on a real pair: unbounded finds the flow, a cutting depth does not."""
    ref, neo = backends
    assert ref.reaches(SELL, GET_STATEMENT) is neo.reaches(SELL, GET_STATEMENT) is True
    assert ref.reaches(SELL, GET_STATEMENT, depth=1) is neo.reaches(SELL, GET_STATEMENT, depth=1) is False


def test_backward_cone_agrees_bounded_and_unbounded(backends):
    ref, neo = backends
    for depth in (1, 3, None):
        a, b = ref.backward_cone([GET_STATEMENT], depth=depth), neo.backward_cone([GET_STATEMENT], depth=depth)
        assert a.nodes == b.nodes and a.total == b.total and a.complete == b.complete, depth
    assert ref.backward_cone([GET_STATEMENT], depth=None).total > ref.backward_cone([GET_STATEMENT], depth=1).total


def test_call_paths_between_agrees_path_for_path(backends):
    ref, neo = backends
    a, b = ref.call_paths_between(SELL, GET_STATEMENT), neo.call_paths_between(SELL, GET_STATEMENT)
    assert a.paths and a.paths == b.paths and a.complete == b.complete
    assert all(h.via == "call" and h.var is None and h.prov == [] for p in a.paths for h in p.hops)
    assert ref.call_paths_between(SELL, GET_STATEMENT, depth=1).paths == neo.call_paths_between(SELL, GET_STATEMENT, depth=1).paths == []


# ---- slicing -----------------------------------------------------------------------------------
def test_slice_backward_agrees_on_every_parameter_of_the_busiest_callables(backends):
    """Node for node, including the ``kind``/``name`` translation — which over Neo4j is read off
    the parameter list, because the projection carries no ``of`` property at all."""
    ref, neo = backends
    checked = 0
    for key in _with_bodies(ref, 30):
        for parameter in ref._addressing.by_key[key].callable.parameters:
            if not parameter.name:
                continue
            a = ref.slice_backward(parameter.name, within=key, depth=None)
            b = neo.slice_backward(parameter.name, within=key, depth=None)
            assert a.nodes == b.nodes and a.total == b.total and a.resolved == b.resolved, f"{key} {parameter.name}"
            checked += 1
    assert checked > 30, f"only {checked} parameters compared"


def test_a_slice_names_its_parameters_without_an_ordinal(backends):
    ref, neo = backends
    for backend in (ref, neo):
        seed = backend.slice_backward("conn", within=GET_STATEMENT).roots[0]
        assert (seed.kind, seed.name) == ("parameter", "conn")
        assert "formal_in" not in str(seed.name)


def test_a_cap_is_reported_not_silent(backends):
    ref, neo = backends
    for backend in (ref, neo):
        whole = backend.slice_backward("conn", within=GET_STATEMENT, depth=None)
        capped = backend.slice_backward("conn", within=GET_STATEMENT, depth=None, max_nodes=5)
        assert whole.total > 5 and whole.complete
        assert len(capped.nodes) == 5 and capped.total == whole.total and not capped.complete


# ---- the port lattice, joined to the statement graph --------------------------------------------
def test_the_graph_carries_dependence_edges_on_its_port_vertices(backends):
    """The measurement the four accessors rest on, on the live graph and for **both** applications.
    codeanalyzer-java 3.0.3 (codeanalyzer-java#227) joins the port lattice to the statement graph,
    so this is the inverse of what leg 3b could assert: 5,083 of daytrader8's 10,430 ``J_DDG`` edges
    have a port vertex at an end, in all four directions, where 3.0.1 had zero.

    **Not "every port is attached"** — 139 of daytrader8's 1,166 ``formal_in`` vertices still have
    out-degree zero, and they are parameters nothing in the callable depends on. A probe demanding
    all of them would fail on correct output.
    """
    _, neo = backends
    ports = ["formal_in", "actual_in", "formal_out", "actual_out"]
    scoped = "WHERE a.id STARTS WITH $prefix AND b.id STARTS WITH $prefix"
    rows = neo._run(f"MATCH (a:JBodyNode)-[r:J_DDG]->(b:JBodyNode) {scoped} AND (a.kind IN $ports OR b.kind IN $ports) RETURN count(r) AS n", ports=ports, prefix=neo._scope_prefix)
    assert rows[0]["n"] == 5083
    directions = neo._run(
        f"MATCH (a:JBodyNode)-[r:J_DDG]->(b:JBodyNode) {scoped} AND (a.kind IN $ports OR b.kind IN $ports) RETURN a.kind AS s, b.kind AS d, count(r) AS n",
        ports=ports,
        prefix=neo._scope_prefix,
    )
    seen = {(r["s"], r["d"]): r["n"] for r in directions}
    assert seen[("formal_in", "call")] == 897 and seen[("call", "actual_in")] == 866
    assert seen[("return", "formal_out")] == 528 and seen[("actual_out", "statement")] == 537
    total, unattached = (
        neo._run("MATCH (b:JBodyNode {kind:'formal_in'}) WHERE b.id STARTS WITH $prefix RETURN count(b) AS n", prefix=neo._scope_prefix)[0]["n"],
        neo._run(
            "MATCH (b:JBodyNode {kind:'formal_in'}) WHERE b.id STARTS WITH $prefix AND NOT (b)-[:J_DDG|J_CDG|J_PARAM_IN|J_PARAM_OUT|J_SUMMARY]->() RETURN count(b) AS n",
            prefix=neo._scope_prefix,
        )[0]["n"],
    )
    assert (total, unattached) == (1166, 139)
    assert neo._ports_carry_dependence is True


#: The witness python-sdk#354 asks for, live: ``cancelOrder(Integer, boolean)``'s ``orderID``
#: reaching ``getStatement``'s ``sql`` across three call boundaries.
VALUE, VALUE_WITHIN, SINK, SINK_ARG = "orderID", f"{DIRECT}.TradeDirect.cancelOrder(java.lang.Integer, boolean)", GET_STATEMENT, "sql"


def test_a_value_crosses_three_call_boundaries_on_both_backends(backends):
    """The four accessors answer over the graph, and they answer what the payload answers.

    Nine hops, ``['data', 'data', 'argument'] * 3``, four frames — the caller's vocabulary, not the
    graph's ``J_DDG``/``J_PARAM_IN`` spelling — and the graph's paths equal the in-memory backend's
    node for node and label for label. That equality is the point: the Cypher walk and the Python
    walk are separate implementations of the same question.
    """
    ref, neo = backends
    for backend in (ref, neo):
        assert backend.flows_to_call(VALUE, SINK, within=VALUE_WITHIN)
        assert backend.flows_to_argument(VALUE, SINK, SINK_ARG, within=VALUE_WITHIN)
        paths = backend.paths_between(VALUE, SINK_ARG, src_within=VALUE_WITHIN, dst_within=SINK, depth=None)
        assert paths.complete and len(paths.paths) == 2
        shortest = min(paths.paths, key=lambda p: len(p.hops))
        assert [h.via for h in shortest.hops] == ["data", "data", "argument"] * 3
        frames = [h.frm.callable for h in shortest.hops] + [shortest.hops[-1].to.callable]
        assert frames[0] == VALUE_WITHIN and frames[-1] == SINK and len(dict.fromkeys(frames)) == 4
        assert backend.slice_forward(VALUE, within=VALUE_WITHIN).total == 45
    a = ref.paths_between(VALUE, SINK_ARG, src_within=VALUE_WITHIN, dst_within=SINK, depth=None)
    b = neo.paths_between(VALUE, SINK_ARG, src_within=VALUE_WITHIN, dst_within=SINK, depth=None)
    assert a.paths == b.paths and a.complete == b.complete


def test_a_forward_slice_agrees_node_for_node_on_both_backends(backends):
    """``slice_forward`` was refused on Java until codeanalyzer-java 3.0.3, so this is the direction
    leg 3b could only assert backwards: the two walks over the joined lattice return the same
    nodes, the same ``total`` and the same ``resolved``."""
    ref, neo = backends
    checked = 0
    for key in _with_bodies(ref, 15):
        for parameter in ref._addressing.by_key[key].callable.parameters:
            if not parameter.name:
                continue
            a = ref.slice_forward(parameter.name, within=key, depth=None)
            b = neo.slice_forward(parameter.name, within=key, depth=None)
            assert a.nodes == b.nodes and a.total == b.total and a.resolved == b.resolved, f"{key} {parameter.name}"
            checked += 1
    assert checked > 15, f"only {checked} parameters compared"


# ---- miss paths --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.get_ddg("noSuchMethod"),
        lambda b: b.callers_of("noSuchMethod"),
        lambda b: b.callees_of("noSuchMethod"),
        lambda b: b.reaches("noSuchMethod", GET_STATEMENT),
        lambda b: b.backward_cone(["noSuchMethod"]),
        lambda b: b.call_paths_between("noSuchMethod", GET_STATEMENT),
        lambda b: b.slice_backward("noSuchParameter", within=GET_STATEMENT),
    ],
    ids=["get_ddg", "callers_of", "callees_of", "reaches", "backward_cone", "call_paths_between", "slice_backward"],
)
def test_a_miss_raises_the_same_way_on_both_backends(backends, call):
    ref, neo = backends
    messages = []
    for backend in (ref, neo):
        with pytest.raises(SelectorNotInGraph) as e:
            call(backend)
        messages.append(str(e.value))
    assert messages[0] == messages[1]
    assert "can://" not in messages[0] and "did you mean" not in messages[0].lower()


# ---- scale -------------------------------------------------------------------------------------
def test_the_scale_corpus_answers_the_whole_surface(scale):
    """ThingsBoard — 28,763 callables, 496,821 body nodes — through the shipped accessors."""
    key = scale._addressing.by_id[_biggest_ddg_callable(scale)].key
    page = scale.get_ddg(key)
    assert page.total > 400 and page.complete and len(page.edges) == page.total
    assert scale.get_cfg(key).total > 0
    assert isinstance(scale.callers_of(key), list)
    assert scale.backward_cone([key], depth=1).total >= 1


def _biggest_ddg_callable(backend) -> str:
    row = backend._run(
        "MATCH (b:JBodyNode)-[r:J_DDG]->() WHERE b.id STARTS WITH $prefix WITH split(b.id,'@')[0] AS c, count(r) AS n RETURN c, n ORDER BY n DESC LIMIT 1",
        prefix=backend._scope_prefix,
    )[0]
    return row["c"]


@pytest.mark.timed  # the clock measures the query, not the tracer -- see _WALL_CLOCK_CEILING
def test_a_page_does_not_scan_the_scale_graph(scale):
    """496,821 body nodes; a page must seek. Median of 5 with the first discarded, so a cold cache
    is not what is measured."""
    key = scale._addressing.by_id[_biggest_ddg_callable(scale)].key
    scale.get_ddg(key)
    runs = []
    for _ in range(5):
        started = time.perf_counter()
        page = scale.get_ddg(key)
        runs.append(time.perf_counter() - started)
    assert page.total > 400
    assert statistics.median(runs) < _WALL_CLOCK_CEILING, f"a page took {statistics.median(runs):.2f}s on {len(runs)} runs"
