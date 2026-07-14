# tests/graph/test_engine_flows.py
import networkx as nx
import pytest
from cldk.graph.engine import Engine
from cldk.graph.provider import ProgramGraphProvider
from cldk.graph.capability import CapabilityError
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


def test_flows_to_on_l3_degrades_but_still_returns_intra_paths():
    # C1: full flows_to semantics are interprocedural (ddg + param_in/param_out/summary),
    # which is L4. On an L3 backend the non-strict call must attach a degraded note AND
    # still return the intraprocedural ddg witnesses it can compute — honest degrade,
    # not silent completeness and not a refusal.
    e = Engine(OneCallableProvider())          # L3 backend
    r = e.flows_to("m:1", "m:3")
    assert "degraded" in r.explain()
    assert r.explain()["degraded"]["requested"] == 4
    assert len(r.paths) == 1                   # intra ddg witness still computed


def test_flows_to_strict_on_l3_raises():
    e = Engine(OneCallableProvider())          # L3 backend
    with pytest.raises(CapabilityError):
        e.flows_to("m:1", "m:3", strict=True)


class DiamondProvider(ProgramGraphProvider):
    # TWO distinct node routes: c@s -> c@a -> c@t and c@s -> c@b -> c@t.
    def program_graph(self, callable_uri):
        g = nx.MultiDiGraph()
        g.add_edge("c@s", "c@a", key="ddg", family="ddg", var="x", prov=["ssa"])
        g.add_edge("c@a", "c@t", key="ddg", family="ddg", var="x", prov=["ssa"])
        g.add_edge("c@s", "c@b", key="ddg", family="ddg", var="x", prov=["ssa"])
        g.add_edge("c@b", "c@t", key="ddg", family="ddg", var="x", prov=["ssa"])
        return g
    def sdg_edges(self): return []
    def resolve_location(self, file, line, col=None): return [f"c@{line}"]
    def source_slice(self, vertex_uri): return (vertex_uri, vertex_uri)
    def callable_of(self, vertex_uri): return "c"
    def max_level(self): return 4


def test_flows_to_sets_truncated_when_path_cap_hit(monkeypatch):
    # I5: witness enumeration is bounded; when the cap drops paths the result must SAY
    # so via explain()["truncated"], instead of silently presenting a partial set as
    # complete. Shrink the cap to 1 so the diamond's second route is dropped.
    import cldk.graph.engine as eng
    monkeypatch.setattr(eng, "_MAX_PATHS", 1)
    e = Engine(DiamondProvider())
    class S: id = "c@s"
    class T: id = "c@t"
    r = e.flows_to(S(), T())
    assert len(r.paths) == 1                     # capped at _MAX_PATHS
    assert r.explain()["truncated"] is True


def test_flows_to_not_truncated_within_bounds():
    e = Engine(DiamondProvider())
    class S: id = "c@s"
    class T: id = "c@t"
    r = e.flows_to(S(), T())
    assert len(r.paths) == 2                     # both diamond routes enumerated
    assert r.explain()["truncated"] is False


def test_def_use_returns_downstream_uses():
    e = Engine(OneCallableProvider())
    r = e.def_use("m:1")  # def at s1 flows to s2, s3
    assert set(r.uris()) == {"c@1:0", "c@2:0", "c@3:0"}


def test_def_use_evidence_role_is_use():
    # I4: downstream vertices in a def_use result are USES of the seed's definition.
    e = Engine(OneCallableProvider())
    r = e.def_use("m:1")
    roles = {ev["uri"]: ev["role"] for ev in r.evidence}
    assert roles["c@1:0"] == "seed"
    assert roles["c@2:0"] == "use"
    assert roles["c@3:0"] == "use"


def test_def_use_seed_absent_is_consistent():
    # seed resolving to a vertex not in the dataflow graph is still in its own result;
    # uris()/evidence must equal the subgraph node set (no uris/bool contradiction).
    e = Engine(OneCallableProvider())
    r = e.def_use("m:99")  # c@99:0 is not a node in the ddg graph
    assert set(r.uris()) == set(r.subgraph.nodes())
    assert "c@99:0" in set(r.uris())
    assert bool(r) is True
    assert len(r) == r.subgraph.number_of_nodes()
