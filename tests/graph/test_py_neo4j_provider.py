"""ProgramGraphProvider conformance for the read-only Neo4j Python backend (#270).

Stub-based: fakes ``_run`` so no live Neo4j server is required. Verifies the identity
translation (dotted ``PyCFGNode.id`` -> minted ``can://...@key`` URI, via the app-scoped
``PyCallable`` signature -> can:// id map) and that every query stays scoped to
``application_name`` the same way the file's existing accessors do.
"""

import pytest

from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
from cldk.graph.provider import ProgramGraphProvider

SIG2 = "pkg.mod.ResUsers.reset_password"
C2 = "can://python/pyfix/pkg/mod.py/ResUsers/reset_password(self,login)"


@pytest.fixture
def backend(monkeypatch):
    b = PyNeo4jBackend.__new__(PyNeo4jBackend)
    b.application_name = "pyfix"   # real attribute name (neo4j_backend.py:134)

    def fake_run(query, **params):
        q = " ".join(query.split())
        if "PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY" in q and "LIMIT 1" in q:
            return [{"one": 1}]
        if "MATCH (c:PyCallable)" in q and "RETURN c.signature" in q:
            return [{"sig": SIG2, "id": C2}]
        if "PY_HAS_CFG_NODE" in q and "RETURN n.id" in q:
            return [{"id": f"{SIG2}#@entry", "kind": "entry", "sl": None, "el": None},
                    {"id": f"{SIG2}#3:8", "kind": "return", "sl": 3, "el": 3}]
        if "r:PY_CFG_NEXT" in q:
            return [{"src": f"{SIG2}#@entry", "dst": f"{SIG2}#3:8", "kind": "fallthrough",
                     "var": None, "prov": None}]
        if "r:PY_CDG" in q or "r:PY_DDG" in q:
            return []
        if "PY_PARAM_IN" in q:
            return [{"src": f"{SIG2}#3:8/actual_in:1",
                     "dst": "pkg.mod.ResUsers._action_reset_password#@formal_in:1", "var": None}]
        if "PY_PARAM_OUT" in q or "PY_SUMMARY" in q:
            return []
        if "n.start_line = $line" in q:
            return [{"id": f"{SIG2}#3:8", "mod": "pkg/mod.py", "sl": 3}]
        raise AssertionError(f"unstubbed query: {q}")

    monkeypatch.setattr(b, "_run", fake_run)
    return b


def test_is_a_provider(backend):
    assert isinstance(backend, ProgramGraphProvider)


def test_max_level_derived_from_overlay(backend):
    assert backend.max_level() == 4


def test_program_graph_translates_to_can_uris(backend):
    g = backend.program_graph(C2)
    assert set(g.nodes) == {f"{C2}@entry", f"{C2}@3:8"}
    (d,) = g.get_edge_data(f"{C2}@entry", f"{C2}@3:8").values()
    assert d["family"] == "cfg" and d["kind"] == "fallthrough"


def test_sdg_edges_translated_and_kinded(backend):
    edges = list(backend.sdg_edges())
    assert edges and edges[0].kind == "param_in"
    assert edges[0].src == f"{C2}@3:8/actual_in:1"


def test_resolve_location_orders_by_parsed_col(backend):
    assert backend.resolve_location("pkg/mod.py", 3) == [f"{C2}@3:8"]


def test_source_slice_lossy_code_none(backend):
    fl, code = backend.source_slice(f"{C2}@3:8")
    assert fl == "pkg/mod.py:3" and code is None


def test_callable_of_partitions_at_first_at(backend):
    assert backend.callable_of(f"{C2}@3:8/actual_in:1") == C2
