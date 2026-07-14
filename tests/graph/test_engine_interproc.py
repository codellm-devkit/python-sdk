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
