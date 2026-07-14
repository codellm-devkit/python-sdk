# tests/graph/test_engine_slice.py
import networkx as nx
from cldk.graph.engine import Engine
from cldk.graph.provider import ProgramGraphProvider


def _callable_graph():
    # entry -> s1(x=1) -> s2(y=x) -> s3(return y); ddg x:s1->s2, y:s2->s3
    g = nx.DiGraph()
    for n in ["c@entry", "c@1:0", "c@2:0", "c@3:0"]:
        g.add_node(n, kind="statement", span=None)
    g.add_edge("c@entry", "c@1:0", family="cfg")
    g.add_edge("c@1:0", "c@2:0", family="cfg")
    g.add_edge("c@2:0", "c@3:0", family="cfg")
    g.add_edge("c@1:0", "c@2:0", family="ddg", var="x", prov=["ssa"])
    g.add_edge("c@2:0", "c@3:0", family="ddg", var="y", prov=["points-to"])
    return g


class OneCallableProvider(ProgramGraphProvider):
    def program_graph(self, callable_uri): return _callable_graph()
    def sdg_edges(self): return []
    def resolve_location(self, file, line, col=None): return [f"c@{line}:{col or 0}"]
    def source_slice(self, vertex_uri): return (f"m:{vertex_uri}", vertex_uri)
    def callable_of(self, vertex_uri): return "c"
    def max_level(self): return 3


def test_backward_slice_exact_set():
    e = Engine(OneCallableProvider())
    r = e.slice_backward("m:3", edges=("cfg", "ddg"))  # seed s3
    assert set(r.uris()) == {"c@3:0", "c@2:0", "c@1:0", "c@entry"}


def test_forward_slice_exact_set():
    e = Engine(OneCallableProvider())
    r = e.slice_forward("m:1", edges=("ddg",))  # seed s1, ddg only
    assert set(r.uris()) == {"c@1:0", "c@2:0", "c@3:0"}


def test_ddg_only_backward_from_s3():
    e = Engine(OneCallableProvider())
    r = e.slice_backward("m:3", edges=("ddg",))
    assert set(r.uris()) == {"c@3:0", "c@2:0", "c@1:0"}  # follows ddg chain, not entry
