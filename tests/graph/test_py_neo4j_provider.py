"""ProgramGraphProvider conformance for the read-only Neo4j Python backend (#270).

Stub-based: fakes ``_run`` so no live Neo4j server is required. Verifies the identity
translation and that every query stays scoped to ``application_name`` the same way the file's
existing accessors do.

``PyCFGNode.id`` rows below mirror the REAL codeanalyzer-python 1.0.2 emitter, confirmed against
a live Neo4j instance in #270 Task 6: ``n.id`` is already the fully-qualified ``can://...@key``
vertex id, with no ``#`` separator anywhere (see issue #295, which this stub previously masked by
hard-coding the dotted-sig ``"#"``-separated form the analyzer repo's ``schema.py`` comment
describes but that has never actually been observed on the wire). ``test_to_uri_...hash_form``
below is the one deliberately-kept case exercising ``_to_uri``'s defensive ``#`` fallback branch.
"""

import pytest

from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
from cldk.graph.provider import ProgramGraphProvider

SIG2 = "pkg.mod.ResUsers.reset_password"
C2 = "can://python/pyfix/pkg/mod.py/ResUsers/reset_password(self,login)"
SIG3 = "pkg.mod.ResUsers._action_reset_password"
C3 = "can://python/pyfix/pkg/mod.py/ResUsers/_action_reset_password(self,ids)"


@pytest.fixture
def backend(monkeypatch):
    b = PyNeo4jBackend.__new__(PyNeo4jBackend)
    b.application_name = "pyfix"   # real attribute name (neo4j_backend.py:134)

    def fake_run(query, **params):
        q = " ".join(query.split())
        if "PY_PARAM_IN|PY_PARAM_OUT|PY_SUMMARY" in q and "LIMIT 1" in q:
            return [{"one": 1}]
        if "MATCH (c:PyCallable)" in q and "RETURN c.signature" in q:
            # Both endpoints resolved — a param_in/out edge's dst (a @formal_in/@formal_out
            # vertex on the CALLEE) must not silently fall through the sig_is_None guard in
            # source_slice/callable_of; that would mask a parse bug on the synthetic-key path.
            return [{"sig": SIG2, "id": C2}, {"sig": SIG3, "id": C3}]
        if "PY_HAS_CFG_NODE" in q and "RETURN n.id" in q:
            # Real emitter form: n.id is already the minted can:// URI, no "#".
            return [{"id": f"{C2}@entry", "kind": "entry", "sl": None, "el": None},
                    {"id": f"{C2}@3:8", "kind": "return", "sl": 3, "el": 3}]
        if "r:PY_CFG_NEXT" in q:
            return [{"src": f"{C2}@entry", "dst": f"{C2}@3:8", "kind": "fallthrough",
                     "var": None, "prov": None}]
        if "r:PY_CDG" in q or "r:PY_DDG" in q:
            return []
        if "PY_PARAM_IN" in q:
            return [{"src": f"{C2}@3:8/actual_in:1",
                     "dst": f"{C3}@formal_in:1", "var": None}]
        if "PY_PARAM_OUT" in q or "PY_SUMMARY" in q:
            return []
        if "n.start_line = $line" in q:
            return [{"id": f"{C2}@3:8", "mod": "pkg/mod.py", "sl": 3}]
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


def test_source_slice_degrades_on_synthetic_formal_in_vertex(backend):
    # A resolved callable (sig_is_None guard doesn't hide it) whose vertex is a synthetic
    # @formal_in:N body node — no start_line of its own, so this must degrade to (None, None)
    # rather than crash trying to int()-parse "formal_in" as a line number (#270 review fix).
    assert backend.source_slice(f"{C3}@formal_in:1") == (None, None)


def test_callable_of_partitions_at_first_at(backend):
    assert backend.callable_of(f"{C2}@3:8/actual_in:1") == C2


def test_to_uri_translates_dotted_sig_hash_form_defensively(backend):
    # The analyzer repo's schema.py comment (and this file's original brief) documents a
    # "#"-separated dotted-sig PyCFGNode.id form; never observed on a real graph (issue #295 —
    # the real emitter stores the can:// id directly, exercised by every other test above), but
    # _to_uri keeps translating it correctly as a defensive fallback should some emitter version
    # ever actually produce it.
    assert backend._to_uri(f"{SIG2}#3:8") == f"{C2}@3:8"
    assert backend._to_uri(f"{SIG2}#@entry") == f"{C2}@entry"
