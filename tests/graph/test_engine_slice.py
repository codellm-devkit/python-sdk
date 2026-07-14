# tests/graph/test_engine_slice.py
import networkx as nx
from cldk.graph.engine import Engine
from cldk.graph.provider import ProgramGraphProvider


def _callable_graph():
    # entry -> s1(x=1) -> s2(y=x) -> s3(return y); ddg x:s1->s2, y:s2->s3.
    # MultiDiGraph so the cfg fallthrough (s1->s2, s2->s3) and the ddg edges on the
    # same statement pairs stay as DISTINCT parallel edges, each keeping its attrs.
    g = nx.MultiDiGraph()
    for n in ["c@entry", "c@1:0", "c@2:0", "c@3:0"]:
        g.add_node(n, kind="statement", span=None)
    g.add_edge("c@entry", "c@1:0", key="cfg", family="cfg")
    g.add_edge("c@1:0", "c@2:0", key="cfg", family="cfg")
    g.add_edge("c@2:0", "c@3:0", key="cfg", family="cfg")
    g.add_edge("c@1:0", "c@2:0", key="ddg", family="ddg", var="x", prov=["ssa"])
    g.add_edge("c@2:0", "c@3:0", key="ddg", family="ddg", var="y", prov=["points-to"])
    assert g.number_of_edges() == 5  # genuine parallel edges, not overwrites
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


def test_family_scoped_slices_differ():
    # cfg and ddg share the endpoint pairs s1->s2 and s2->s3; a MultiDiGraph keeps
    # them as distinct parallel edges, so family-scoped slices must NOT collapse.
    e = Engine(OneCallableProvider())
    ddg_set = set(e.slice_backward("m:3", edges=("ddg",)).uris())
    cfg_set = set(e.slice_backward("m:3", edges=("cfg",)).uris())
    assert "c@entry" not in ddg_set        # entry reachable only via the cfg chain
    assert "c@entry" in cfg_set            # cfg fallthrough reaches entry
    assert ddg_set != cfg_set              # families are distinct, not merged


def test_seed_absent_from_graph_is_consistent():
    # a seed resolving to a vertex not in the callable graph is still in its own
    # slice; uris()/evidence must equal the subgraph's node set (no contradiction).
    e = Engine(OneCallableProvider())
    r = e.slice_backward("m:99", edges=("cfg", "ddg"))  # c@99:0 is not a node
    assert set(r.uris()) == set(r.subgraph.nodes())
    assert len(r) == r.subgraph.number_of_nodes()
    assert "c@99:0" in set(r.uris())       # seed present in its own slice
    assert bool(r) is True                 # non-empty; uris() and bool() agree
