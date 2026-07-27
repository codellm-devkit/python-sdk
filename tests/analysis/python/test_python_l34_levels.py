"""Level plumbing for L3/L4 (#270): the facade's AnalysisLevel maps to the analyzer's integer
``analysis_level`` option, and the backend reports the envelope's ``max_level`` — captured from
the SAME in-process run, never re-derived or sniffed. codeanalyzer-python >= 1.0.2 models are
schema-2.0-shaped (PyCallable.body/cfg/cdg/ddg/summary, PyApplication.param_in/param_out), so
``self.application`` doubles as the cpg source the graph provider mixin walks — no second parse.
"""
import json
from pathlib import Path

import pytest

from cldk.analysis import ANALYSIS_LEVEL_TO_INT, AnalysisLevel, to_analysis_level

RES = Path(__file__).parent.parent.parent / "resources" / "cpg"


def test_analysis_level_map_is_total():
    assert ANALYSIS_LEVEL_TO_INT == {
        AnalysisLevel.symbol_table: 1,
        AnalysisLevel.call_graph: 2,
        AnalysisLevel.program_dependency_graph: 3,
        AnalysisLevel.system_dependency_graph: 4,
    }


def test_to_analysis_level_accepts_enum():
    """to_analysis_level passes through enum values unchanged."""
    assert to_analysis_level(AnalysisLevel.call_graph) is AnalysisLevel.call_graph


def test_to_analysis_level_accepts_value_form():
    """to_analysis_level accepts the enum's value (space-separated)."""
    assert to_analysis_level("call graph") is AnalysisLevel.call_graph


def test_to_analysis_level_accepts_name_form():
    """to_analysis_level accepts the enum's name (underscore form)."""
    assert to_analysis_level("call_graph") is AnalysisLevel.call_graph
    assert to_analysis_level("symbol_table") is AnalysisLevel.symbol_table
    assert to_analysis_level("program_dependency_graph") is AnalysisLevel.program_dependency_graph
    assert to_analysis_level("system_dependency_graph") is AnalysisLevel.system_dependency_graph


def test_to_analysis_level_rejects_garbage():
    """to_analysis_level raises on unknown values."""
    with pytest.raises(ValueError, match="unknown analysis level"):
        to_analysis_level("garbage")


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


def test_backend_accepts_underscore_analysis_level(monkeypatch, tmp_path):
    """PyCodeanalyzer accepts analysis_level as underscore form (e.g. "call_graph")."""
    payload = json.loads((RES / "py-a4.json").read_text())

    class _FakeCodeanalyzer:
        def __init__(self, options):
            self.captured_options = options

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def analyze(self):
            return _FakeEnvelope(payload)

    import cldk.analysis.python.codeanalyzer.codeanalyzer as mod

    monkeypatch.setattr(mod, "Codeanalyzer", _FakeCodeanalyzer)
    from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer

    # Construct with underscore form
    b = PyCodeanalyzer(
        project_dir=tmp_path,
        analysis_level="call_graph",
        analysis_json_path=None,
        eager_analysis=False,
    )
    assert b.max_level() == 4
    assert b.call_graph is not None
