# tests/graph/test_engine_flows.py
import networkx as nx
from cldk.graph.engine import Engine
from cldk.graph.provider import ProgramGraphProvider
from tests.graph.test_engine_slice import OneCallableProvider


class ParallelEdgeProvider(ProgramGraphProvider):
    # ONE node route s1 -> s2 -> s3, but the s1->s2 hop carries TWO parallel ddg edges
    # (var x via ssa, var y via points-to). A MultiDiGraph enumerates the route once per
    # parallel edge; the engine must collapse to one witness per distinct node route.
    def program_graph(self, callable_uri):
        g = nx.MultiDiGraph()
        for n in ["c@1:0", "c@2:0", "c@3:0"]:
            g.add_node(n, kind="statement", span=None)
        g.add_edge("c@1:0", "c@2:0", key="ddg:x", family="ddg", var="x", prov=["ssa"])
        g.add_edge("c@1:0", "c@2:0", key="ddg:y", family="ddg", var="y", prov=["points-to"])
        g.add_edge("c@2:0", "c@3:0", key="ddg", family="ddg", var="z", prov=["ssa"])
        return g

    def sdg_edges(self): return []
    def resolve_location(self, file, line, col=None): return [f"c@{line}:{col or 0}"]
    def source_slice(self, vertex_uri): return (f"m:{vertex_uri}", vertex_uri)
    def callable_of(self, vertex_uri): return "c"
    def max_level(self): return 3


def test_flows_to_finds_witness_with_min_confidence():
    e = Engine(OneCallableProvider())
    r = e.flows_to("m:1", "m:3")  # s1 -> s2 (ssa) -> s3 (points-to); min = structural
    assert bool(r) is True
    assert len(r.paths) == 1
    hops = r.paths[0].hops
    assert [h["from"] for h in hops] == ["c@1:0", "c@2:0"]
    assert [h["to"] for h in hops] == ["c@2:0", "c@3:0"]
    assert [h["kind"] for h in hops] == ["ddg", "ddg"]
    assert [h["var"] for h in hops] == ["x", "y"]
    assert [h["confidence"] for h in hops] == ["structural", "resolved"]
    assert r.paths[0].confidence == "structural"


def test_flows_to_no_path_is_falsy():
    e = Engine(OneCallableProvider())
    r = e.flows_to("m:3", "m:1")  # no forward ddg path s3 -> s1
    assert not r.paths
    assert bool(r) is False


def test_flows_to_dedups_parallel_edge_routes():
    # one node route, two parallel ddg edges on the first hop -> exactly one witness.
    e = Engine(ParallelEdgeProvider())
    r = e.flows_to("m:1", "m:3")
    assert len(r.paths) == 1
    p = r.paths[0]
    assert [h["from"] for h in p.hops] == ["c@1:0", "c@2:0"]
    assert [h["to"] for h in p.hops] == ["c@2:0", "c@3:0"]
    # per-hop confidence uses the BEST (max-tier) parallel: s1->s2 resolved (points-to
    # beats ssa), s2->s3 structural. Path confidence is the min over hops.
    assert [h["confidence"] for h in p.hops] == ["resolved", "structural"]
    assert p.confidence == "structural"


def test_def_use_returns_downstream_uses():
    e = Engine(OneCallableProvider())
    r = e.def_use("m:1")  # def at s1 flows to s2, s3
    assert set(r.uris()) == {"c@1:0", "c@2:0", "c@3:0"}


def test_def_use_seed_absent_is_consistent():
    # seed resolving to a vertex not in the dataflow graph is still in its own result;
    # uris()/evidence must equal the subgraph node set (no uris/bool contradiction).
    e = Engine(OneCallableProvider())
    r = e.def_use("m:99")  # c@99:0 is not a node in the ddg graph
    assert set(r.uris()) == set(r.subgraph.nodes())
    assert "c@99:0" in set(r.uris())
    assert bool(r) is True
    assert len(r) == r.subgraph.number_of_nodes()
