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

"""The dataflow surface at scale, **read-only**, against a live 1.3.0 graph someone else deployed
(leg 2.5b, Task 2).

The offline suite answers from a five-file sample app whose whole SDG is 52 nodes wide. Nothing
there can show what 125,532 body nodes and 119,384 ``TS_DDG`` edges do to a per-callable page, so
every fixture below is derived from the graph at run time by :func:`cypher` and every expectation is
the graph's own count — never a number copied out of a file::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7692 \\
    CLDK_TEST_NEO4J_USER=neo4j \\
    CLDK_TEST_NEO4J_PASSWORD=... \\
    CLDK_TEST_NEO4J_APP=superset-frontend \\
    uv run pytest tests/analysis/typescript/test_typescript_dataflow_live.py

The URI has no default: the module skips unless it is set and the named application is present.
Nothing here writes — not a node, relationship or property, not even in setup — and no emitter runs.

**Scale is the point of the per-callable pages**, so one test asserts a wall clock. It carries the
repo's ``timed`` marker, which pauses the coverage tracer around the call: leg 1.5 measured about
five seconds of instrumentation overhead on a large accessor, and a timing assertion taken under
the tracer measures the tracer.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.analysis.commons.results import SliceNode
from cldk.analysis.typescript.backend import SDG_RELS

from .test_typescript_e2e_neo4j_live import APP_ID, APP_NAME, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, SCOPE, _live_application_present

logging.getLogger("neo4j").setLevel(logging.ERROR)

pytestmark = pytest.mark.skipif(
    not _live_application_present(),
    reason=f"set CLDK_TEST_NEO4J_URI (and _USER/_PASSWORD/_APP) to a server holding {APP_ID!r}; this module is read-only and has no default URI",
)

#: The per-callable page's wall-clock ceiling, in seconds. The measured worst case on the reference
#: application (``drawGraph``, 1,933 DDG edges: resolve + count + page = three round trips) is well
#: under 0.2 s; the ceiling is generous because the point of the assertion is that the page **does
#: not scan the graph** — a whole-label walk of 125,532 body nodes is seconds, not milliseconds —
#: not to pin a particular millisecond count on someone else's hardware.
_PAGE_CEILING_SECONDS = 3.0


@pytest.fixture(scope="module")
def ts():
    analysis = CLDK.typescript(
        project_path=None,
        analysis_level="symbol_table",
        backend=Neo4jConnectionConfig(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD, application_name=APP_NAME),
    )
    yield analysis
    analysis.backend.close()


def cypher(query: str, **params: Any) -> List[Dict[str, Any]]:
    """One read statement against the live graph, through its own driver — never the SDK's."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            return [r.data() for r in session.run(query, app_id=APP_ID, **SCOPE, **params)]
    finally:
        driver.close()


SCOPED = "(x.id STARTS WITH $p1 OR x.id STARTS WITH $p2)"


def _biggest(rel: str) -> Dict[str, Any]:
    """The callable with the most ``rel`` edges, and how many — the page's worst case."""
    return cypher(
        f"MATCH (x:TSCallable)-[:TS_HAS_BODY_NODE]->()-[r:{rel}]->() WHERE {SCOPED} "
        "RETURN x.signature AS signature, x.id AS id, count(r) AS n ORDER BY n DESC, x.id LIMIT 1"
    )[0]


@pytest.fixture(scope="module")
def biggest_ddg() -> Dict[str, Any]:
    return _biggest("TS_DDG")


# ============================================================ the three per-callable graphs
@pytest.mark.parametrize("rel,accessor", [("TS_CFG_NEXT", "get_cfg"), ("TS_CDG", "get_cdg"), ("TS_DDG", "get_ddg")])
def test_a_page_is_exactly_the_callables_own_edges(ts, rel, accessor):
    """``total`` is the graph's own count for that callable, and every endpoint is one of its body
    nodes. The count is taken over the *containment* relationship while the accessor is written as
    an id-prefix match, so the two spellings are checked against each other on real data."""
    worst = _biggest(rel)
    page = getattr(ts, accessor)(worst["signature"])
    assert page.total == worst["n"], f"{accessor} total disagrees with the graph"
    own = {r["id"] for r in cypher("MATCH (c:TSCallable {id: $id})-[:TS_HAS_BODY_NODE]->(b) RETURN b.id AS id", id=worst["id"])}
    assert own, "the worst callable has no body nodes?"
    for e in page.edges:
        assert e.src in own and e.dst in own, "an edge left the callable it was asked about"


def test_the_page_returns_self_loops_the_containment_spelling_would_drop(ts):
    """A ``TS_DDG`` edge from a body node to *itself* is real — 173 of them on this application —
    and the obvious containment pattern
    ``(c)-[:TS_HAS_BODY_NODE]->(s)-[r]->(d)<-[:TS_HAS_BODY_NODE]-(c)`` silently drops every one,
    because Cypher's relationship-uniqueness rule forbids the two ``TS_HAS_BODY_NODE``
    relationships from being the same one. This is why the accessor is written as an id-prefix
    match instead."""
    loops = cypher(f"MATCH (x:TSBodyNode)-[r:TS_DDG]->(x) WHERE {SCOPED} MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(x) RETURN c.signature AS sig, x.id AS id ORDER BY x.id LIMIT 1")
    if not loops:
        pytest.skip("this application has no self-loop TS_DDG edge to check")
    row = loops[0]
    page = ts.get_ddg(row["sig"], page_size=100_000)
    assert any(e.src == row["id"] and e.dst == row["id"] for e in page.edges), "a self-loop dependence went missing from the page"


def test_the_biggest_page_is_paged_and_the_cursor_walks_it_without_repeating(ts, biggest_ddg):
    size = max(2, biggest_ddg["n"] // 3)
    first = ts.get_ddg(biggest_ddg["signature"], page_size=size)
    assert first.total == biggest_ddg["n"] and len(first.edges) == size
    assert first.complete is False and first.next_cursor is not None
    seen, cursor = list(first.edges), first.next_cursor
    while cursor is not None:
        page = ts.get_ddg(biggest_ddg["signature"], page_size=size, cursor=cursor)
        assert page.total == biggest_ddg["n"]
        seen.extend(page.edges)
        cursor = page.next_cursor
    assert len(seen) == biggest_ddg["n"]
    assert len({(e.src, e.dst, e.var, tuple(e.prov)) for e in seen}) == len(seen), "a page repeated an edge"
    assert [(e.src, e.dst, e.var or "", list(e.prov)) for e in seen] == sorted((e.src, e.dst, e.var or "", list(e.prov)) for e in seen)


@pytest.mark.timed  # the clock measures the query, not the tracer -- see the module docstring
def test_a_per_callable_page_does_not_scan_the_graph(ts, biggest_ddg):
    """125,532 body nodes and 119,384 DDG edges; a page of the worst callable must cost a seek."""
    start = time.perf_counter()
    page = ts.get_ddg(biggest_ddg["signature"])
    elapsed = time.perf_counter() - start
    assert page.total == biggest_ddg["n"]
    assert elapsed < _PAGE_CEILING_SECONDS, f"the worst per-callable DDG page took {elapsed:.2f}s"


def test_typescript_ddg_has_exactly_one_provenance_tier(ts, biggest_ddg):
    """Every ``TS_DDG`` edge on this graph carries ``["reaching-defs"]``: cants emits no ``ssa`` and
    no ``points-to`` tier, so Python's three-way certainty ranking collapses to a single value. The
    graph is asked for the whole distinct set, so this fails the day the analyzer emits a second
    tier rather than quietly agreeing with a stale docstring."""
    tiers = {tuple(r["p"] or []) for r in cypher(f"MATCH (x:TSBodyNode)-[r:TS_DDG]->() WHERE {SCOPED} RETURN DISTINCT r.prov AS p")}
    assert tiers == {("reaching-defs",)}
    assert {tuple(e.prov) for e in ts.get_ddg(biggest_ddg["signature"]).edges} == {("reaching-defs",)}


# ================================================================================== the slices
@pytest.fixture(scope="module")
def a_parameter() -> Dict[str, Any]:
    """One entering value with a non-trivial forward closure, chosen deterministically."""
    rows = cypher(
        f"MATCH (x:TSCallable)-[:TS_HAS_BODY_NODE]->(b:TSBodyNode {{kind: 'formal_in'}}) WHERE {SCOPED} AND b.of IS NOT NULL "
        f"MATCH (b)-[:{'|'.join(SDG_RELS)}]->() "
        "WITH x, b, count(*) AS out WHERE out > 1 "
        "RETURN x.signature AS signature, b.of AS name, b.id AS ref ORDER BY b.id LIMIT 1"
    )
    assert rows, "no entering value with outgoing dataflow on this application"
    return rows[0]


def _closure(ref: str, depth: int | None, backward: bool) -> int:
    arrow = f"<-[:{'|'.join(SDG_RELS)}*0..{'' if depth is None else depth}]-" if backward else f"-[:{'|'.join(SDG_RELS)}*0..{'' if depth is None else depth}]->"
    return cypher(f"MATCH (r:TSBodyNode {{id: $id}}){arrow}(m:TSBodyNode) RETURN count(DISTINCT m) AS n", id=ref)[0]["n"]


def test_the_slice_default_is_finite_and_none_asks_for_the_whole_cone(ts, a_parameter):
    bounded = ts.slice_forward(a_parameter["name"], within=a_parameter["signature"])
    assert bounded.total == _closure(a_parameter["ref"], 5, backward=False)
    whole = ts.slice_forward(a_parameter["name"], within=a_parameter["signature"], depth=None)
    assert whole.total == _closure(a_parameter["ref"], None, backward=False)
    assert whole.total >= bounded.total
    assert ts.slice_backward(a_parameter["name"], within=a_parameter["signature"], depth=None).total == _closure(a_parameter["ref"], None, backward=True)


def test_a_slice_describes_its_nodes_in_the_callers_vocabulary(ts, a_parameter):
    s = ts.slice_forward(a_parameter["name"], within=a_parameter["signature"], depth=None)
    assert s.roots[0].kind == "parameter" and s.roots[0].name == a_parameter["name"]
    assert s.nodes == sorted(s.nodes, key=lambda n: n.ref)
    for n in s.nodes:
        assert isinstance(n, SliceNode) and n.source is None
        assert not n.file.startswith("can://") and not n.callable.startswith("can://")
        # cants' internal markers must not reach a return field: `$ret` and `arg0` are a marker and
        # an ordinal, never a name (E6/E7).
        assert n.name != "$ret" and not (n.name or "").startswith("arg")


def test_max_nodes_caps_and_says_so(ts, a_parameter):
    whole = ts.slice_forward(a_parameter["name"], within=a_parameter["signature"], depth=None)
    if whole.total < 2:
        pytest.skip("the chosen seed's closure is too small to truncate")
    capped = ts.slice_forward(a_parameter["name"], within=a_parameter["signature"], depth=None, max_nodes=1)
    assert len(capped.nodes) == 1 and capped.total == whole.total and capped.complete is False


# ============================================================== the call graph at application scale
@pytest.fixture(scope="module")
def a_call_edge() -> Dict[str, Any]:
    """One callable→callable call edge, deterministically chosen."""
    rows = cypher(
        f"MATCH (x:TSCallable)-[:TS_CALLS]->(t:TSCallable) WHERE {SCOPED} AND (t.id STARTS WITH $p1 OR t.id STARTS WITH $p2) "
        "RETURN x.signature AS src, t.signature AS dst ORDER BY x.id, t.id LIMIT 1"
    )
    assert rows, "no callable-to-callable call edge on this application"
    return rows[0]


def test_reaches_is_true_over_a_real_edge_and_false_at_zero_useful_depth(ts, a_call_edge):
    assert ts.reaches(a_call_edge["src"], a_call_edge["dst"]) is True
    assert ts.reaches(a_call_edge["src"], a_call_edge["dst"], depth=1) is True


def test_the_server_is_new_enough_for_the_quantified_path_pattern(ts):
    """``reaches`` and ``backward_cone`` compile to a quantified path pattern, which needs Neo4j
    5.9+. The version is read at attach and enforced at those two calls, never at attach, so an
    older server keeps serving every other accessor. Recorded here so the requirement is a fact
    about a measured server and not a claim."""
    version = ts.backend._server_version
    assert version is not None, "the reference server would not report dbms.components()"
    assert version >= ts.backend._QUANTIFIED_PATH_MIN_SERVER, f"the attached server reports {version}"


def test_callers_and_callees_agree_with_the_graphs_own_edges(ts, a_call_edge):
    callers = ts.callers_of(a_call_edge["dst"])
    expected = {
        r["ref"]
        for r in cypher(
            "MATCH (s)-[:TS_CALLS]->(t:TSCallable {signature: $sig}) WHERE (s:TSCallable OR s:TSModule) AND (s.id STARTS WITH $p1 OR s.id STARTS WITH $p2) RETURN s.id AS ref",
            sig=a_call_edge["dst"],
        )
    }
    assert {n.ref for n in callers} == expected
    assert {n.kind for n in callers} <= {"callable", "module"}
    callees = ts.callees_of(a_call_edge["src"])
    assert a_call_edge["dst"] in {n.callable for n in callees}
    for n in callees:
        if n.kind == "external":
            assert n.file == "" and n.line == 0 and not n.callable.startswith("can://")


def test_a_module_caller_is_reported_as_a_module(ts):
    """cants makes a module the caller of its own top-level code — 1,464 such edges here. Dropping
    them would report "nothing calls it" for a function a module invokes at import time."""
    row = cypher(f"MATCH (x:TSModule)-[:TS_CALLS]->(t:TSCallable) WHERE {SCOPED} RETURN t.signature AS sig, x.id AS ref, x.name AS name ORDER BY t.id LIMIT 1")
    assert row, "this application has no module-originated call edge"
    callers = {n.ref: n for n in ts.callers_of(row[0]["sig"])}
    assert row[0]["ref"] in callers
    module = callers[row[0]["ref"]]
    assert module.kind == "module" and module.callable == row[0]["name"] and module.file == row[0]["name"]


def test_backward_cone_and_call_paths_agree_with_reaches(ts, a_call_edge):
    cone = ts.backward_cone([a_call_edge["dst"]], depth=1)
    assert a_call_edge["src"] in {n.callable for n in cone.nodes}
    assert a_call_edge["dst"] in {r.callable for r in cone.roots}
    paths = ts.call_paths_between(a_call_edge["src"], a_call_edge["dst"], depth=1)
    assert paths.paths and all(h.via == "call" for p in paths.paths for h in p.hops)
    assert paths.paths[0].hops[0].frm.callable == a_call_edge["src"]
    assert paths.paths[0].hops[-1].to.callable == a_call_edge["dst"]


# ================================================================== the flow predicates, unbounded
@pytest.fixture(scope="module")
def a_real_flow() -> Dict[str, Any]:
    """A value that reaches a *different* callable's entering value, and how many hops the graph
    itself says that takes — the shape that makes the unbounded/bounded asymmetry checkable.

    Found **structurally**, not by an unbounded path search: the canonical cross-callable flow is
    ``formal_in -[:TS_DDG]-> actual_in -[:TS_PARAM_IN]-> formal_in``, which is two hops by
    construction and costs 0.3 s here, where a ``shortestPath`` over every entering value did not
    finish. Both endpoint names are required to be unique among their callable's entering values,
    so ``resolve_value`` cannot raise ``AmbiguousName`` on a fixture chosen for a different reason.
    The hop count is then *confirmed* against ``allShortestPaths``, so the number the assertions cut
    at is the graph's and not this query's shape.
    """
    rows = cypher(
        f"MATCH (x:TSCallable)-[:TS_HAS_BODY_NODE]->(a:TSBodyNode {{kind: 'formal_in'}}) WHERE {SCOPED} AND a.of IS NOT NULL "
        "MATCH (a)-[:TS_DDG]->(:TSBodyNode {kind: 'actual_in'})-[:TS_PARAM_IN]->(b:TSBodyNode {kind: 'formal_in'}) "
        "MATCH (y:TSCallable)-[:TS_HAS_BODY_NODE]->(b) WHERE y <> x AND b.of IS NOT NULL "
        "MATCH (x)-[:TS_HAS_BODY_NODE]->(sib:TSBodyNode {kind: 'formal_in'}) WHERE sib.of = a.of "
        "MATCH (y)-[:TS_HAS_BODY_NODE]->(sob:TSBodyNode {kind: 'formal_in'}) WHERE sob.of = b.of "
        "WITH x, a, y, b, count(DISTINCT sib) AS srcs, count(DISTINCT sob) AS dsts WHERE srcs = 1 AND dsts = 1 "
        "RETURN x.signature AS src_within, a.of AS src, a.id AS src_ref, y.signature AS callee, b.of AS arg, b.id AS arg_ref "
        "ORDER BY a.id, b.id LIMIT 1"
    )
    assert rows, "no cross-callable value flow on this application"
    flow = dict(rows[0])
    lengths = {
        r["n"]
        for r in cypher(
            f"MATCH (a:TSBodyNode {{id: $a}}) MATCH (b:TSBodyNode {{id: $b}}) MATCH p = allShortestPaths((a)-[:{'|'.join(SDG_RELS)}*1..]->(b)) RETURN DISTINCT length(p) AS n",
            a=flow["src_ref"],
            b=flow["arg_ref"],
        )
    }
    assert len(lengths) == 1, f"allShortestPaths disagrees with itself: {lengths}"
    flow["hops"] = lengths.pop()
    assert flow["hops"] >= 2, "a one-hop flow cannot be cut, so it proves nothing about the bound"
    return flow


def test_the_predicates_are_unbounded_by_default_and_a_cutting_depth_returns_false(ts, a_real_flow):
    """The whole reason the five predicate and path accessors default to ``depth=None``: the same
    call is ``True`` unbounded and ``False`` at a depth that cuts the flow, and a bare ``False``
    carries nothing to say which happened. Leg 1.5 shipped a real bug by inheriting the slice's
    finite default onto a predicate."""
    cut = a_real_flow["hops"] - 1
    assert ts.flows_to_call(a_real_flow["src"], a_real_flow["callee"], within=a_real_flow["src_within"]) is True
    assert ts.flows_to_call(a_real_flow["src"], a_real_flow["callee"], within=a_real_flow["src_within"], depth=cut) is False
    assert ts.flows_to_argument(a_real_flow["src"], a_real_flow["callee"], a_real_flow["arg"], within=a_real_flow["src_within"]) is True
    assert ts.flows_to_argument(a_real_flow["src"], a_real_flow["callee"], a_real_flow["arg"], within=a_real_flow["src_within"], depth=cut) is False


def test_paths_between_is_unbounded_by_default_and_a_cutting_depth_empties_it(ts, a_real_flow):
    found = ts.paths_between(a_real_flow["src"], a_real_flow["arg"], src_within=a_real_flow["src_within"], dst_within=a_real_flow["callee"])
    assert found.paths, "the flow the graph reports came back with no witness"
    hops = found.paths[0].hops
    assert len(hops) == a_real_flow["hops"]
    assert {h.via for h in hops} <= {"data", "control", "argument", "return", "summary"}
    for i in range(len(hops) - 1):
        assert hops[i].to.ref == hops[i + 1].frm.ref
    cut = ts.paths_between(
        a_real_flow["src"], a_real_flow["arg"], src_within=a_real_flow["src_within"], dst_within=a_real_flow["callee"], depth=a_real_flow["hops"] - 1
    )
    assert cut.paths == []


def test_flows_to_argument_implies_flows_to_call(ts, a_real_flow):
    """By construction, not by agreement between two queries: ``resolve_value(arg, within=callee)``
    can only return one of ``callee``'s entering values, and that set is exactly what
    ``flows_to_call`` tests reachability of."""
    assert ts.flows_to_argument(a_real_flow["src"], a_real_flow["callee"], a_real_flow["arg"], within=a_real_flow["src_within"])
    assert ts.flows_to_call(a_real_flow["src"], a_real_flow["callee"], within=a_real_flow["src_within"])
