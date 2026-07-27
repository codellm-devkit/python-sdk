"""Level plumbing for L3/L4 (#270): the facade's AnalysisLevel maps to the analyzer's integer
``analysis_level`` option, and the backend reports the envelope's ``max_level`` — captured from
the SAME in-process run, never re-derived or sniffed. codeanalyzer-python >= 1.0.2 models are
schema-2.0-shaped (PyCallable.body/cfg/cdg/ddg/summary, PyApplication.param_in/param_out), so
``self.application`` doubles as the cpg source the graph provider mixin walks — no second parse.
"""
import json
from pathlib import Path

import pytest

from cldk.analysis import ANALYSIS_LEVEL_TO_INT, AnalysisLevel

RES = Path(__file__).parent.parent.parent / "resources" / "cpg"


def test_analysis_level_map_is_total():
    assert ANALYSIS_LEVEL_TO_INT == {
        AnalysisLevel.symbol_table: 1,
        AnalysisLevel.call_graph: 2,
        AnalysisLevel.program_dependency_graph: 3,
        AnalysisLevel.system_dependency_graph: 4,
    }


class _FakeEnvelope:
    """Stands in for the analyzer's schema-v2 Analysis envelope."""

    def __init__(self, payload_dict):
        from cldk.models.python import PyApplication

        self.schema_version = payload_dict["schema_version"]
        self.max_level = payload_dict["max_level"]
        self.application = PyApplication(**payload_dict["application"])


@pytest.fixture
def backend(monkeypatch, tmp_path):
    payload = json.loads((RES / "py-a4.json").read_text())
    captured = {}

    class _FakeCodeanalyzer:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def analyze(self):
            return _FakeEnvelope(payload)

    import cldk.analysis.python.codeanalyzer.codeanalyzer as mod

    monkeypatch.setattr(mod, "Codeanalyzer", _FakeCodeanalyzer)
    from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer

    b = PyCodeanalyzer(
        project_dir=tmp_path,
        analysis_level=AnalysisLevel.system_dependency_graph,
        analysis_json_path=None,
        eager_analysis=False,
    )
    return b, captured


def test_max_level_captured_from_same_run(backend):
    b, _ = backend
    assert b.max_level() == 4


def test_analyzer_receives_int_level(backend):
    _, captured = backend
    assert captured["options"].analysis_level == 4


def test_call_graph_built_at_level_ge_2(backend):
    b, _ = backend
    assert b.call_graph is not None
    # the pyfix sample's three internal callables all appear
    assert b.call_graph.number_of_edges() >= 3
