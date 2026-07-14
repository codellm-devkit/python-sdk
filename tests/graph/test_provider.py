import pytest
from cldk.graph.provider import resolve_vertex, ProgramGraphProvider


class FakeProvider(ProgramGraphProvider):
    def program_graph(self, callable_uri): ...
    def sdg_edges(self): return []
    def resolve_location(self, file, line, col=None):
        return [f"can://x/{file}/f@{line}:{col or 0}"]
    def source_slice(self, vertex_uri): return ("m.py:1", "code")
    def callable_of(self, vertex_uri): return "can://x/f"
    def max_level(self): return 4


def test_resolve_location_string():
    p = FakeProvider()
    assert resolve_vertex(p, "src/m.py:42") == ["can://x/src/m.py/f@42:0"]
    assert resolve_vertex(p, "src/m.py:42:5") == ["can://x/src/m.py/f@42:5"]


def test_resolve_can_id_passthrough():
    p = FakeProvider()
    assert resolve_vertex(p, "can://x/src/m.py/f@42:5") == ["can://x/src/m.py/f@42:5"]


def test_resolve_node_object_uses_id():
    p = FakeProvider()
    class N: id = "can://x/src/m.py/f@42:5"
    assert resolve_vertex(p, N()) == ["can://x/src/m.py/f@42:5"]


def test_resolve_rejects_garbage():
    p = FakeProvider()
    with pytest.raises(ValueError):
        resolve_vertex(p, 12345)
