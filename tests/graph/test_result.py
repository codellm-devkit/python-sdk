import networkx as nx
from cldk.graph.result import GraphResult, SliceResult, FlowResult, FlowPath


def _graph(*nodes):
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    return g


def test_graphresult_len_bool_uris():
    g = _graph("a", "b")
    r = SliceResult(subgraph=g, evidence=[{"uri": "a"}, {"uri": "b"}], _explain={"level": 3})
    assert len(r) == 2
    assert bool(r) is True
    assert r.uris() == ["a", "b"]
    assert r.explain() == {"level": 3}


def test_empty_result_is_falsy():
    r = SliceResult(subgraph=_graph(), evidence=[], _explain={})
    assert not r
    assert len(r) == 0


def test_flowresult_carries_paths_and_serializes():
    p = FlowPath(source="a", sink="c",
                 hops=[{"from": "a", "to": "b", "kind": "ddg", "var": "x", "confidence": "structural"}],
                 confidence="structural")
    r = FlowResult(subgraph=_graph("a", "b", "c"),
                   evidence=[{"uri": "a", "file_line": "m.py:1", "code": "x = 1", "role": "seed"}],
                   _explain={"level": 4}, paths=[p])
    assert r.paths[0].confidence == "structural"
    assert '"file_line": "m.py:1"' in r.to_json()


def test_flowresult_to_json_includes_paths():
    p = FlowPath(source="a", sink="c",
                 hops=[{"from": "a", "to": "b", "kind": "ddg", "var": "x", "confidence": "structural"}],
                 confidence="structural")
    r = FlowResult(subgraph=_graph("a", "b", "c"),
                   evidence=[{"uri": "a"}], _explain={"level": 4}, paths=[p])
    dumped = r.to_json()
    assert '"confidence": "structural"' in dumped
    assert '"var": "x"' in dumped
