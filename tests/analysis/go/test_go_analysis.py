"""Tests for the Go analysis facade with the cango subprocess mocked — no binary, no Neo4j.

One corpus, driven through the mocked subprocess (which writes the payload into the cache dir exactly
as cango would at ``-o``): ``tests/resources/go/analysis.json``, the committed real
``can://go/litestream`` corpus (55 modules, 959 edges).

* The deterministic level/cache/capture regression tests mutate its ``max_level`` and call graph to
  simulate L1-vs-L2 generations.
* ``go_analysis`` runs the basic accessor smoke tests over it, and ``litestream_facade`` runs the
  whole accessor surface, so callers/callees/source are proven against genuine cango output.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx
import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.models.go import GoType
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

#: the committed real corpus, via the parents[2]/"resources" convention the TS/Java tests use
GO_ANALYSIS = Path(__file__).resolve().parents[2] / "resources" / "go" / "analysis.json"

# real, stable ids from the litestream corpus (abs/replica_client.go)
LS_TYPE_ID = "can://go/litestream/abs/replica_client.go/github.com/benbjohnson/litestream/abs.ReplicaClient"
LS_METHOD_ID = f"{LS_TYPE_ID}/github.com/benbjohnson/litestream/abs.ReplicaClient.DeleteAll"
LS_FUNC_ID = "can://go/litestream/abs/replica_client.go/github.com/benbjohnson/litestream/abs.NewReplicaClient"
LS_FUNC_SIG = "github.com/benbjohnson/litestream/abs.NewReplicaClient"
LS_FUNC_CALLER_ID = "can://go/litestream/abs/replica_client.go/github.com/benbjohnson/litestream/abs.NewReplicaClientFromURL"


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)
    return _run


@pytest.fixture
def go_analysis(tmp_path, monkeypatch):
    payload = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setenv("CODEANALYZER_GO_BIN", "cango")
    monkeypatch.setattr("subprocess.run", _fake_run_writing_output(payload))
    return CLDK.go(
        project_path=str(tmp_path),
        analysis_level=AnalysisLevel.call_graph,
        eager=True,
        backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
    )


# -----[ regression: a cache shallower than the request is not reused ]-----
def _level_aware_run(call_count):
    """A subprocess.run mock that writes an analysis.json whose max_level matches the `-a` flag it
    was given, and counts how many times cango was invoked."""
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _run(cmd, *args, **kwargs):
        call_count.append(1)
        a = int(cmd[cmd.index("-a") + 1]) if "-a" in cmd else 2
        payload = dict(base)
        payload["max_level"] = a
        if a < 2:  # a level-1 payload carries no call graph
            payload["application"] = {**base["application"], "call_graph": []}
        text = json.dumps(payload)
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(text, encoding="utf-8")
        return MagicMock(stdout=text, returncode=0)

    return _run


def test_shallow_cache_is_not_reused_for_a_deeper_request(tmp_path, monkeypatch):
    """A level-1 cache must NOT satisfy a level-2 request: the backend re-runs cango. (The reuse
    check used to test only file existence, serving the stale shallower result.)"""
    calls = []
    monkeypatch.setenv("CODEANALYZER_GO_BIN", "cango")
    monkeypatch.setattr("subprocess.run", _level_aware_run(calls))

    # First: a level-1 run writes a level-1 cache.
    CLDK.go(project_path=str(tmp_path), analysis_level=AnalysisLevel.symbol_table, backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)))
    assert len(calls) == 1

    # Then: a level-2 request over that level-1 cache must re-run (not reuse).
    a2 = CLDK.go(project_path=str(tmp_path), analysis_level=AnalysisLevel.call_graph, backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)))
    assert len(calls) == 2, "level-2 request should have re-run cango over the stale level-1 cache"
    assert a2.backend.analysis.max_level == 2


def test_deeper_cache_is_reused_for_a_shallower_request(tmp_path, monkeypatch):
    """A level-2 cache satisfies a level-1 request without re-running — the schema is additive
    (L1 subset of L2), so the deeper cache already contains everything."""
    calls = []
    monkeypatch.setenv("CODEANALYZER_GO_BIN", "cango")
    monkeypatch.setattr("subprocess.run", _level_aware_run(calls))

    CLDK.go(project_path=str(tmp_path), analysis_level=AnalysisLevel.call_graph, backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)))
    assert len(calls) == 1
    CLDK.go(project_path=str(tmp_path), analysis_level=AnalysisLevel.symbol_table, backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)))
    assert len(calls) == 1, "level-1 request should reuse the deeper level-2 cache, not re-run"


# -----[ regression: the requested analysis level actually reaches cango ]-----
@pytest.mark.parametrize(
    "level, expected_a",
    [(AnalysisLevel.symbol_table, "1"), (AnalysisLevel.call_graph, "2")],
)
def test_requested_level_is_passed_to_cango(level, expected_a, tmp_path, monkeypatch):
    """A hand-rolled name->int dict silently defaulted every level to 1 (the enum's value is
    'call graph', not 'call_graph'), so `-a 2` never reached cango and no project ever got a call
    graph. Assert the integer `-a` flag matches the requested level."""
    captured = {}

    def _capture(cmd, *args, **kwargs):
        if "-a" in cmd:
            captured["a"] = cmd[cmd.index("-a") + 1]
        out = Path(cmd[cmd.index("-o") + 1]) if "-o" in cmd else None
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        return MagicMock(stdout=FIXTURE.read_text(encoding="utf-8"), returncode=0)

    monkeypatch.setenv("CODEANALYZER_GO_BIN", "cango")
    monkeypatch.setattr("subprocess.run", _capture)
    CLDK.go(project_path=str(tmp_path), analysis_level=level, eager=True, backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)))
    assert captured.get("a") == expected_a


# -----[ the public surface is type-centric, never class ]-----
def test_public_surface_has_no_class_named_method():
    """SDK1: Go is class-less. No public facade method mentions 'class'."""
    public = [m for m in dir(CLDK.go) if not m.startswith("_")]  # the facade methods on the type
    from cldk.analysis.go import GoAnalysis

    public = [m for m in dir(GoAnalysis) if not m.startswith("_")]
    assert not [m for m in public if "class" in m.lower()]
    for expected in ("get_types", "get_type", "get_functions", "get_methods_of", "get_method", "get_fields"):
        assert expected in public


# -----[ accessors over the fixture ]-----
def test_symbol_table_not_empty(go_analysis):
    assert go_analysis.get_symbol_table()


def test_get_types_returns_the_struct(go_analysis):
    types = go_analysis.get_types()
    assert types, "no types"
    assert all(isinstance(t, GoType) for t in types.values())
    assert any(t.kind == "struct" for t in types.values())


def test_get_functions_distinct_from_methods(go_analysis):
    funcs = go_analysis.get_functions()
    assert funcs, "no package-level functions"
    # the package function is not among any type's methods
    method_ids = {m.id for tid in go_analysis.get_types() for m in go_analysis.get_methods_of(tid).values()}
    assert all(f.id not in method_ids for f in funcs.values())


def test_methods_and_get_method_and_params(go_analysis):
    tid = next(iter(go_analysis.get_types()))
    methods = go_analysis.get_methods_of(tid)
    assert methods, "the struct has no methods"
    sig = next(iter(methods))
    assert go_analysis.get_method(tid, sig).signature == sig
    # params list is well-formed (may be empty)
    assert isinstance(go_analysis.get_method_parameters(tid, sig), list)


def test_call_graph_is_dangling_free(go_analysis):
    g = go_analysis.get_call_graph()
    assert isinstance(g, nx.DiGraph)
    for src, dst in g.edges():
        assert "id" in g.nodes[src] and "id" in g.nodes[dst]


def test_callers_callees_keyed_on_id(go_analysis):
    # the one call edge: its src has a callee, its dst has a caller
    edges = go_analysis.get_application_view().call_graph
    assert edges
    e = edges[0]
    assert any(x.dst == e.dst for x in go_analysis.get_callees(e.src))
    assert any(x.src == e.src for x in go_analysis.get_callers(e.dst))


def test_get_source_slices_the_method(go_analysis):
    tid = next(iter(go_analysis.get_types()))
    sig = next(iter(go_analysis.get_methods_of(tid)))
    src = go_analysis.get_source(go_analysis.get_method(tid, sig).id)
    assert "func" in src


def test_artifact_six_raise_on_local(go_analysis):
    for name in ("get_artifacts", "get_dependencies", "get_config_keys", "get_config_uses", "get_unresolved_config_reads"):
        with pytest.raises(CodeanalyzerExecutionException):
            getattr(go_analysis.backend, name)()


# -----[ the whole accessor surface over the real litestream corpus ]-----
@pytest.fixture(scope="module")
def litestream_facade(tmp_path_factory):
    """The facade over the committed real corpus, through the same mocked subprocess as
    ``go_analysis`` — no binary, but 55 modules and 959 edges instead of a one-edge toy."""
    import subprocess
    from unittest.mock import patch

    payload = GO_ANALYSIS.read_text(encoding="utf-8")
    proj = tmp_path_factory.mktemp("litestream-facade")
    with patch.dict("os.environ", {"CODEANALYZER_GO_BIN": "cango"}), patch.object(
        subprocess, "run", _fake_run_writing_output(payload)
    ):
        yield CLDK.go(
            project_path=str(proj),
            analysis_level=AnalysisLevel.call_graph,
            eager=True,
            backend=CodeAnalyzerConfig(cache_dir=str(proj)),
        )


def test_real_corpus_scale(litestream_facade):
    assert len(litestream_facade.get_symbol_table()) == 55
    assert len(litestream_facade.get_application_view().call_graph) == 959


def test_real_corpus_function_accessor(litestream_facade):
    funcs = litestream_facade.get_functions()
    assert LS_FUNC_SIG in funcs
    assert funcs[LS_FUNC_SIG].id == LS_FUNC_ID


def test_real_corpus_type_and_method(litestream_facade):
    ty = litestream_facade.get_type(LS_TYPE_ID)
    assert ty is not None and ty.kind == "struct"
    methods = litestream_facade.get_methods_of(LS_TYPE_ID)
    assert any(m.id == LS_METHOD_ID for m in methods.values())
    assert any(f.type == "string" for f in litestream_facade.get_fields(LS_TYPE_ID))


def test_real_corpus_callees_of_delete_all(litestream_facade):
    """DeleteAll calls exactly two callees in this corpus and is called by none (measured)."""
    callees = litestream_facade.get_callees(LS_METHOD_ID)
    assert len(callees) == 2
    assert all(e.src == LS_METHOD_ID for e in callees)
    assert litestream_facade.get_callers(LS_METHOD_ID) == []


def test_real_corpus_callers_of_constructor(litestream_facade):
    callers = litestream_facade.get_callers(LS_FUNC_ID)
    assert LS_FUNC_CALLER_ID in {e.src for e in callers}


def test_real_corpus_source_slices_the_method(litestream_facade):
    src = litestream_facade.get_source(LS_METHOD_ID)
    assert src.startswith("func") and "DeleteAll" in src


def test_real_corpus_call_graph_dangling_free(litestream_facade):
    """Every one of the 959 edges lands on a node that carries its id (no dangling endpoints)."""
    g = litestream_facade.get_call_graph()
    assert g.number_of_edges() == 959
    for src, dst in g.edges():
        assert "id" in g.nodes[src] and "id" in g.nodes[dst]
