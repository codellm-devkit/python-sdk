# tests/graph/test_engine_interproc.py
import networkx as nx
from cldk.graph.engine import Engine
from cldk.graph.provider import ProgramGraphProvider
from cldk.graph.capability import CapabilityError
import pytest


class _Edge:
    def __init__(self, src, dst): self.src, self.dst, self.var, self.prov = src, dst, "a", ["points-to"]


class TwoCallableProvider(ProgramGraphProvider):
    # caller c: c@call --param_in--> callee d; d@ret --param_out--> c@after
    def program_graph(self, callable_uri):
        g = nx.MultiDiGraph()   # engine's _filter_edges iterates edges(keys=True)
        if callable_uri == "c":
            g.add_edge("c@call", "c@after", key="ddg", family="ddg", var="a", prov=["points-to"])
        else:
            g.add_node("d@ret", kind="statement", span=None)
        return g
    def sdg_edges(self): return [_Edge("c@call", "d@in"), _Edge("d@ret", "c@after")]
    def resolve_location(self, file, line, col=None): return [f"c@{line}"]
    def source_slice(self, vertex_uri): return (vertex_uri, vertex_uri)
    def callable_of(self, vertex_uri): return vertex_uri.split("@")[0]
    def max_level(self): return 4


def test_interproc_none_crosses_at_l4():
    e = Engine(TwoCallableProvider())
    # resolve_vertex only accepts a .id-bearing object, a can:// id, or a file:line[:col]
    # string (see test_provider.py::test_resolve_node_object_uses_id for the same pattern) —
    # "c@call" is a raw vertex id, so it must go through the .id-object path.
    class Seed: id = "c@call"
    r = e.slice_forward(Seed(), edges=("ddg",), interprocedural=None)
    assert "d@in" in set(r.uris())  # crossed the param_in boundary


def test_explicit_interproc_on_l3_strict_raises():
    class L3(TwoCallableProvider):
        def max_level(self): return 3
    with pytest.raises(CapabilityError):
        Engine(L3()).slice_forward("c@call", interprocedural=True, strict=True)


class _SDGEdge:
    def __init__(self, src, dst, kind):
        self.src, self.dst, self.kind = src, dst, kind
        self.var, self.prov = "a", ["points-to"]


class CrossCallableFlowProvider(ProgramGraphProvider):
    # caller c: c@src --ddg--> c@call;  sdg: c@call --param_in--> d@in;
    # callee d: d@in --ddg--> d@sink.  The flow c@src -> d@sink exists only if the
    # dataflow graph spans BOTH endpoint callables plus the sdg overlay.
    def program_graph(self, callable_uri):
        g = nx.MultiDiGraph()
        if callable_uri == "c":
            g.add_edge("c@src", "c@call", key="ddg", family="ddg", var="a", prov=["ssa"])
        else:
            g.add_edge("d@in", "d@sink", key="ddg", family="ddg", var="a", prov=["ssa"])
        return g
    def sdg_edges(self): return [_SDGEdge("c@call", "d@in", "param_in")]
    def resolve_location(self, file, line, col=None): return [f"c@{line}"]
    def source_slice(self, vertex_uri): return (vertex_uri, vertex_uri)
    def callable_of(self, vertex_uri): return vertex_uri.split("@")[0]
    def max_level(self): return 4


def test_flows_to_crosses_callable_boundary():
    # C2: a sink in a DIFFERENT callable (reachable via param_in into the callee's
    # interior) must be found. Building the dataflow graph from the source's callable
    # alone loses the callee's intra ddg edges and yields a false "no flow".
    e = Engine(CrossCallableFlowProvider())
    class Src: id = "c@src"
    class Snk: id = "d@sink"
    r = e.flows_to(Src(), Snk())
    assert len(r.paths) >= 1                   # a real cross-callable flow, not empty
    p = r.paths[0]
    assert [h["from"] for h in p.hops] == ["c@src", "c@call", "d@in"]
    assert [h["to"] for h in p.hops] == ["c@call", "d@in", "d@sink"]


def test_flow_boundary_hop_reports_sdg_kind():
    # I6: a hop crossing the callable boundary must report WHICH sdg edge carried the
    # flow (param_in/param_out/summary), not the opaque family name "sdg". Intra hops
    # keep reporting their family ("ddg").
    e = Engine(CrossCallableFlowProvider())
    class Src: id = "c@src"
    class Snk: id = "d@sink"
    p = e.flows_to(Src(), Snk()).paths[0]
    kinds = [h["kind"] for h in p.hops]
    assert kinds[0] == "ddg"                                # intra hop: family
    assert kinds[1] in {"param_in", "param_out", "summary"}  # boundary hop: sdg kind
    assert kinds[1] == "param_in"
    assert kinds[2] == "ddg"


def test_family_scoped_slice_has_no_sdg_overlay_at_l4():
    # C3: the sdg (dataflow: param_in/param_out/summary) overlay must be gated on the
    # ddg family being REQUESTED, not just on level/interprocedural intent. A cfg-only
    # backward slice on an L4 backend must not pull in dataflow vertices from other
    # callables — only dataflow crosses boundaries, and no dataflow family was asked for.
    class CFGProvider(TwoCallableProvider):
        def program_graph(self, callable_uri):
            g = nx.MultiDiGraph()
            g.add_edge("c@1", "c@2", key="cfg", family="cfg")
            g.add_edge("c@2", "c@3", key="cfg", family="cfg")
            return g
        def sdg_edges(self): return [_Edge("d@in", "c@2")]  # foreign DATAFLOW vertex
    e = Engine(CFGProvider())
    class Seed: id = "c@3"
    r = e.slice_backward(Seed(), edges=("cfg",))
    assert "d@in" not in set(r.uris())               # no dataflow contamination
    assert set(r.uris()) == {"c@1", "c@2", "c@3"}
    assert r.explain()["interprocedural"] is False   # no dataflow family => no crossing


def test_control_deps_stays_intraprocedural_at_l4():
    # Control dependence has NO interprocedural notion in this model — only dataflow
    # (param_in/param_out/summary) crosses callable boundaries. control_deps must force
    # interprocedural=False; otherwise, on an L4 backend, _intra defaults to want_inter=True and
    # merges the sdg dataflow overlay into a pure CDG slice, and the backward walk pulls in
    # dataflow-reachable vertices from other callables with no control-dependence relation.
    #
    # control_deps is a BACKWARD slice, so a leaking sdg edge must be a forward-ANCESTOR edge of
    # the seed: d@in -> c@body means d@in reaches c@body, so a backward slice from c@body WOULD
    # pull in d@in if the overlay were applied (verified: it leaks against the unfixed code).
    class CDGProvider(TwoCallableProvider):
        def program_graph(self, callable_uri):
            g = nx.MultiDiGraph()                          # only a control-dependence edge
            g.add_edge("c@guard", "c@body", key="cdg", family="cdg")
            return g
        def sdg_edges(self): return [_Edge("d@in", "c@body")]   # cross-callable DATAFLOW
    e = Engine(CDGProvider())
    class Seed: id = "c@body"
    r = e.control_deps(Seed())
    assert set(r.uris()) == {"c@guard", "c@body"}   # only intra cdg reachability, no d@in
    assert "d@in" not in set(r.uris())              # sdg dataflow did NOT cross the boundary
    assert r.explain()["interprocedural"] is False  # control_deps is always intraprocedural
