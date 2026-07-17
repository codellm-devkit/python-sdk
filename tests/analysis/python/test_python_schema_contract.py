################################################################################
# Copyright IBM Corporation 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Unit tests for the Python backends' schema-2.0.0 contract (no analyzer run, no live Neo4j).

Three things are exercised here:

* the in-process backend's fail-fast on the ``Analysis`` envelope's ``schema_version``
  (``_run_analyzer`` unwraps ``Analysis.application`` and refuses any other schema);
* the Neo4j backend's fail-fast ``schema_version`` gate (``_check_schema_version``), read from
  the scoped ``:PyApplication`` node — mirroring ``TSNeo4jBackend``;
* the call-graph CanNode-id translation: schema-2.0.0 ``PyCallEdge.src``/``dst`` are ``can://``
  ids, but the SDK's public call-graph vocabulary stays dotted signatures (externals keep their
  raw ``can://`` id).
"""

import pytest
from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyCallEdge, PyClass, PyModule

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.utils.exceptions.exceptions import CldkSchemaMismatchException


# -----[ Neo4j schema-version gate ]-----
def _bare_neo4j_backend() -> PyNeo4jBackend:
    """A backend instance with no live driver — enough to exercise the pure guard logic."""
    backend = PyNeo4jBackend.__new__(PyNeo4jBackend)
    backend.application_name = "test_app"
    return backend


def test_neo4j_schema_version_mismatch_fails_fast():
    with pytest.raises(CldkSchemaMismatchException):
        _bare_neo4j_backend()._check_schema_version(expected="2.0.0", found="1.0.0")


def test_neo4j_schema_version_match_passes():
    assert _bare_neo4j_backend()._check_schema_version(expected="2.0.0", found="2.0.0") is None


def test_neo4j_schema_version_queried_from_application_when_not_supplied(monkeypatch):
    backend = _bare_neo4j_backend()
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [{"v": "2.0.0"}])
    assert backend._check_schema_version(expected="2.0.0") is None

    monkeypatch.setattr(backend, "_run", lambda *a, **k: [{"v": "1.0.0"}])
    with pytest.raises(CldkSchemaMismatchException):
        backend._check_schema_version(expected="2.0.0")


def test_neo4j_schema_version_absent_fails_fast(monkeypatch):
    backend = _bare_neo4j_backend()
    # No :PyApplication row at all (empty/foreign DB, pre-2.0 emitter) ⇒ found is None ⇒ mismatch.
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [])
    with pytest.raises(CldkSchemaMismatchException):
        backend._check_schema_version(expected="2.0.0")


# -----[ in-process envelope gate ]-----
class _StubAnalysis:
    def __init__(self, schema_version, application=None):
        self.schema_version = schema_version
        self.application = application


class _StubCodeanalyzer:
    """Stands in for ``codeanalyzer.core.Codeanalyzer`` — returns a canned envelope."""

    envelope: _StubAnalysis = None

    def __init__(self, options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def analyze(self):
        return self.envelope


def _bare_local_backend() -> PyCodeanalyzer:
    """An instance with just the attributes ``_run_analyzer`` reads — no analysis at init."""
    backend = PyCodeanalyzer.__new__(PyCodeanalyzer)
    backend.project_dir = "unused"
    backend.analysis_json_path = None
    backend.use_ray = False
    backend.eager_analysis = False
    backend.cache_dir = None
    backend.target_files = None
    return backend


def test_local_backend_rejects_wrong_envelope_schema(monkeypatch):
    import cldk.analysis.python.codeanalyzer.codeanalyzer as mod

    _StubCodeanalyzer.envelope = _StubAnalysis(schema_version="3.0.0")
    monkeypatch.setattr(mod, "Codeanalyzer", _StubCodeanalyzer)
    with pytest.raises(CldkSchemaMismatchException):
        _bare_local_backend()._run_analyzer()


def test_local_backend_unwraps_matching_envelope(monkeypatch):
    import cldk.analysis.python.codeanalyzer.codeanalyzer as mod

    app = PyApplication(symbol_table={})
    _StubCodeanalyzer.envelope = _StubAnalysis(schema_version=PyCodeanalyzer.SUPPORTED_ANALYSIS_SCHEMA, application=app)
    monkeypatch.setattr(mod, "Codeanalyzer", _StubCodeanalyzer)
    assert _bare_local_backend()._run_analyzer() is app


# -----[ call-graph CanNode-id → signature translation ]-----
def test_call_graph_translates_can_ids_to_signatures():
    f = PyCallable(name="f", path="pkg/mod.py", signature="pkg.mod.f", id="can://python/proj/pkg/mod.py/f()")
    m = PyCallable(name="m", path="pkg/mod.py", signature="pkg.mod.A.m", id="can://python/proj/pkg/mod.py/A/m(self)")
    module = PyModule(
        file_path="pkg/mod.py",
        module_name="mod",
        types={"pkg.mod.A": PyClass(name="A", signature="pkg.mod.A", callables={"m": m})},
        functions={"f": f},
    )
    external_id = "can://python/proj/@external/os/getcwd"
    app = PyApplication(
        symbol_table={"pkg/mod.py": module},
        call_graph=[
            PyCallEdge(src=f.id, dst=m.id, weight=1, prov=["jedi"]),
            PyCallEdge(src=f.id, dst=external_id, weight=1, prov=["jedi"]),
        ],
    )

    backend = PyCodeanalyzer.__new__(PyCodeanalyzer)
    backend.application = app
    backend.call_graph = None

    graph = backend.get_call_graph()
    # Symbol-table callables appear under their dotted signatures; the external keeps its can:// id.
    assert set(graph.nodes) == {"pkg.mod.f", "pkg.mod.A.m", external_id}
    assert graph.has_edge("pkg.mod.f", "pkg.mod.A.m")
    assert graph.has_edge("pkg.mod.f", external_id)

    callers = backend.get_all_callers("pkg.mod.A", "m")
    assert [c["caller_signature"] for c in callers["caller_details"]] == ["pkg.mod.f"]
