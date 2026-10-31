"""Exercise live-suite scope gates offline, even when their modules are skipped."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from cldk.analysis.commons.identity import resolve_identity


def _load_live_suite(filename, monkeypatch, *, app_name=None):
    for name in ["CLDK_TEST_NEO4J_URI", "CLDK_TEST_NEO4J_WRITE_URI"]:
        monkeypatch.delenv(name, raising=False)
    if app_name is not None:
        monkeypatch.setenv("CLDK_TEST_NEO4J_APP", app_name)
    spec = importlib.util.spec_from_file_location("_offline_" + filename.removesuffix(".py"), Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "NEO4J_URI", "bolt://offline-test")
    return module


class Driver:
    def __init__(self, application_id):
        self.application_id = application_id
        self.calls = []
        self.closed = False

    def verify_connectivity(self):
        pass

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return SimpleNamespace(single=lambda: {"c": int(parameters.get("id") == self.application_id)})

    def close(self):
        self.closed = True


@pytest.mark.parametrize("raw_name", ["superset-frontend", "-svc %雪"])
def test_live_presence_gate_queries_the_encoded_canonical_application(raw_name, monkeypatch):
    GraphDatabase = pytest.importorskip("neo4j").GraphDatabase
    module = _load_live_suite("test_typescript_e2e_neo4j_live.py", monkeypatch, app_name=raw_name)
    expected = resolve_identity(raw_name, None)
    driver = Driver(expected.application_id)
    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: driver)
    assert module._live_application_present()
    assert driver.calls[0][1] == {"id": expected.application_id}
    assert module.SCOPE == {"p": expected.application_id + "/"}
    assert driver.closed


def test_live_cleanup_deletes_the_canonical_anchor_and_its_descendants(monkeypatch):
    GraphDatabase = pytest.importorskip("neo4j").GraphDatabase
    module = _load_live_suite("test_typescript_neo4j_backend.py", monkeypatch)
    expected = resolve_identity(module.APP_NAME, None)
    driver = Driver(expected.application_id)
    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: driver)
    module._teardown_application()
    query, parameters = driver.calls[0]
    assert "n.id STARTS WITH $p OR n.id = $app_id" in query
    assert parameters == {"p": expected.application_id + "/", "app_id": expected.application_id}
    assert driver.closed
