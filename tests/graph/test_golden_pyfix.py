"""Engine goldens over the pyfix L4 sample + the REAL duck-typed backend end-to-end:
PyCodeanalyzer's own upstream application (codeanalyzer-python 1.0.2 models) drives the
shared Engine through the mixin — the exact object graph production uses."""
import json
from pathlib import Path

import pytest

from cldk.analysis import AnalysisLevel
from cldk.graph._cpg_local import CpgLocalProviderMixin
from cldk.graph.engine import Engine
from cldk.models.cpg import AnalysisPayload

RES = Path(__file__).parent.parent / "resources" / "cpg"
MOD = "pkg/mod.py"
ENTRY = "can://python/pyfix/pkg/mod.py/entry()"
C2 = "can://python/pyfix/pkg/mod.py/ResUsers/reset_password(self,login)"
C3 = "can://python/pyfix/pkg/mod.py/ResUsers/_action_reset_password(self,ids)"


class _Local(CpgLocalProviderMixin):
    def __init__(self, application, level=4):
        self.application = application
        self._level = level

    def max_level(self):
        return self._level


@pytest.fixture(scope="module")
def eng():
    app = AnalysisPayload(**json.loads((RES / "py-a4.json").read_text())).application
    return Engine(_Local(app))


def test_backward_slice_from_location_multi_seed(eng):
    r = eng.slice_backward(f"{MOD}:5")
    assert set(r.uris()) == {f"{C3}@entry", f"{C3}@5:8", f"{C3}@5:15"}
    assert r.explain()["level"] == 4 and "degraded" not in r.explain()


def test_control_deps_exact(eng):
    r = eng.control_deps(f"{C2}@3:8")
    assert set(r.uris()) == {f"{C2}@entry", f"{C2}@3:8"}


def test_def_use_exact(eng):
    r = eng.def_use(f"{C2}@entry")
    assert set(r.uris()) == {f"{C2}@entry", f"{C2}@3:8"}


def test_flows_to_via_summary(eng):
    r = eng.flows_to(f"{C2}@3:8/actual_in:1", f"{C2}@3:8/actual_out")
    assert len(r.paths) == 1
    assert [h["kind"] for h in r.paths[0].hops] == ["summary"]


def test_flows_to_no_route_is_empty_not_error(eng):
    r = eng.flows_to(f"{C3}@5:8", f"{ENTRY}@7:4")
    assert len(r.paths) == 0 and not r


def test_real_backend_is_a_provider_end_to_end(monkeypatch, tmp_path):
    # Build a REAL PyCodeanalyzer (upstream models, not cldk cpg models) via the fake-analyzer
    # pattern from tests/analysis/python/test_python_l34_levels.py, then drive the Engine
    # through it — this is the production object graph, and it is what catches upstream-model
    # slimness (BodyNode without id, CfgEdge without var/prov).
    import cldk.analysis.python.codeanalyzer.codeanalyzer as mod
    from cldk.models.python import PyApplication

    payload = json.loads((RES / "py-a4.json").read_text())

    class _Env:
        schema_version = payload["schema_version"]
        max_level = payload["max_level"]
        application = PyApplication(**payload["application"])

    class _Fake:
        def __init__(self, options): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def analyze(self): return _Env()

    monkeypatch.setattr(mod, "Codeanalyzer", _Fake)
    b = mod.PyCodeanalyzer(project_dir=tmp_path,
                           analysis_level=AnalysisLevel.system_dependency_graph,
                           analysis_json_path=None, eager_analysis=False)
    assert isinstance(b, CpgLocalProviderMixin) and b.max_level() == 4
    # exact same golden as the cpg-model path:
    r = Engine(b).slice_backward(f"{MOD}:5")
    assert set(r.uris()) == {f"{C3}@entry", f"{C3}@5:8", f"{C3}@5:15"}
    flows = Engine(b).flows_to(f"{C2}@3:8/actual_in:1", f"{C3}@formal_in:1")
    assert len(flows.paths) == 1 and flows.paths[0].hops[0]["kind"] == "param_in"
