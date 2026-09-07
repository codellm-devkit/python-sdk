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

"""Entrypoints and the artifact/config layer on the TypeScript facade (leg 2.5b, Task 3) —
offline, over the v2 fixtures and a fake driver.

Nine accessors, mirroring ``PythonAnalysis``'s signatures exactly: ``get_entrypoints`` /
``get_entrypoint_classes`` / ``get_entrypoint_coverage``, plus the five repository-artifact
getters (which already existed on the *backends* since leg 2.5a and are put on the facade here)
and ``get_config_readers``.

The tracked sample app is the interesting case for the first three, not a dull one: its
``entrypoint_report`` names **no** framework and **no** marked callable, yet records three
unresolved near-misses (``Get`` twice, ``Controller`` once) — the detection pass's own
"under-approximates by design, so silence is its failure mode". So ``get_entrypoints() == []``
and ``get_entrypoint_coverage()`` disagreeing about whether that silence is clean is exactly the
distinction :class:`~cldk.analysis.commons.results.EntrypointCoverage` exists to draw.

Every expected value below was read off
``tests/resources/typescript/analysis_json/v2/a4/analysis.json`` directly, never by running the
implementation and copying its output.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.commons.results import EntrypointCoverage
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.models.python import PyArtifact, PyConfigKey
from cldk.models.typescript import TSCallableOverview, TSClassOverview

from .conftest import FakeDriver

#: The sample app's own report, read off the fixture: no framework recognized, the shipped ruleset
#: consulted, three near-misses that never resolved to an entrypoint, no hard failure.
FIXTURE_REPORT = {"frameworks_detected": [], "rulesets": ["shipped"], "unresolved": {"Get": 2, "Controller": 1}, "errors": []}


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


@pytest.fixture
def ts(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """A local-backend facade over the a4 (``-a 4``) fixture of the tracked sample app."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_fake_run_writing_output(typescript_analysis_json)):
        return CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.system_dependency_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )


# ----------------------------------------------------------------------------------- entrypoints
def test_the_sample_app_marks_no_entrypoint_callable(ts):
    """``[]`` here is a fact about the corpus, not a stand-in for "the mark does not exist": the
    fixture carries ``is_entrypoint`` on its callables and every one of them is unmarked."""
    assert ts.get_entrypoints() == []
    assert any(c.is_entrypoint is not None for c in ts.backend._callables.values()), "the fixture should carry the mark at all"


def test_the_sample_app_marks_no_entrypoint_class(ts):
    assert ts.get_entrypoint_classes() == []


def test_entrypoint_coverage_reports_the_near_misses_the_empty_list_cannot(ts):
    """The whole reason the coverage record is a separate accessor: ``get_entrypoints()`` is empty
    *and* the pass had three unresolved near-misses. One accessor cannot say both."""
    coverage = ts.get_entrypoint_coverage()
    assert isinstance(coverage, EntrypointCoverage)
    assert coverage.frameworks_detected == FIXTURE_REPORT["frameworks_detected"]
    assert coverage.rulesets == FIXTURE_REPORT["rulesets"]
    assert coverage.unresolved == FIXTURE_REPORT["unresolved"]
    assert coverage.errors == FIXTURE_REPORT["errors"]
    assert coverage.diagnostics == [], "the local backend has the report in full; nothing to report as missing"


def test_a_marked_callable_and_class_come_back_as_overviews(ts):
    """The fixture marks nothing, so the marks are set on the in-memory tree and the two walks are
    asked again — the domain they walk is what is under test, not the analyzer's own verdict."""
    callable_ = ts.backend._callables["src/controllers.UserController.show"]
    class_ = ts.backend._classes["src/controllers.UserController"]
    callable_.is_entrypoint = True
    class_.is_entrypoint = True

    (marked,) = ts.get_entrypoints()
    assert isinstance(marked, TSCallableOverview)
    assert (marked.signature, marked.name, marked.kind, marked.path) == ("src/controllers.UserController.show", "show", "method", "src/controllers.ts")
    assert marked.owner_signature == "src/controllers.UserController" and marked.owner_kind == "class"

    (klass,) = ts.get_entrypoint_classes()
    assert isinstance(klass, TSClassOverview)
    assert (klass.signature, klass.name, klass.path) == ("src/controllers.UserController", "UserController", "src/controllers.ts")
    assert klass.start_line == class_.start_line and klass.end_line == class_.end_line
    assert klass.decorators == [d.name for d in class_.decorators]


def test_a_missing_report_is_said_rather_than_fabricated(ts):
    """``TSApplication.entrypoint_report`` is optional on the model (1.3.0 always emits it; the
    graph-backed application view does not carry it as a structured field). When it is absent the
    answer says so in the model's own vocabulary — the shared ``entrypoint_report_unavailable``
    code — rather than returning empty-but-clean-looking coverage fields."""
    ts.backend.application.entrypoint_report = None
    coverage = ts.get_entrypoint_coverage()
    assert [d.code for d in coverage.diagnostics] == ["entrypoint_report_unavailable"]
    assert coverage.frameworks_detected == [] and coverage.unresolved == {} and coverage.errors == []


# ---------------------------------------------------------------------- the artifact/config layer
def test_the_facade_reaches_the_artifacts(ts):
    artifacts = ts.get_artifacts()
    assert set(artifacts) == {"package.json", "tsconfig.json"}
    assert all(isinstance(a, PyArtifact) for a in artifacts.values())
    assert artifacts["package.json"].roles == ["dependency-manifest", "tool-config"]
    assert artifacts == ts.backend.get_artifacts()


def test_the_facade_reaches_the_dependencies_and_its_filters(ts):
    assert ts.get_dependencies() == []
    assert ts.get_dependencies(direct_only=True) == ts.get_dependencies(ecosystem="npm") == ts.get_dependencies(declared_in="can://artifact/slim/package.json") == []


def test_the_facade_reaches_the_config_keys(ts):
    keys = ts.get_config_keys()
    assert all(isinstance(v, PyConfigKey) for v in keys.values())
    assert {v.key for v in keys.values()} >= {"compilerOptions.target", "compilerOptions.strict"}
    # A boolean in the artifact is rendered as its JSON text, since PyConfigKey.value is a string.
    assert next(v.value for v in keys.values() if v.key == "compilerOptions.strict") == "true"


def test_the_facade_reaches_the_config_uses_and_the_unresolved_reads(ts):
    assert ts.get_config_uses() == []
    assert ts.get_config_uses("compilerOptions.target") == []
    assert ts.get_unresolved_config_reads() == []


def test_config_readers_of_a_key_nothing_reads_is_empty(ts):
    assert ts.get_config_readers("compilerOptions.target") == []
    assert ts.get_config_readers("no.such.key") == []


# ------------------------------------------------------------------- the Neo4j coverage two paths
def _anchor_backend(properties: dict) -> TSNeo4jBackend:
    """A Neo4j backend whose only answer is the application anchor's property map."""

    def responder(query, params):
        if "properties(a) AS p" in query:
            return [{"p": {"id": "can://typescript/app", "name": "app", **properties}}]
        return []

    return TSNeo4jBackend._from_driver(FakeDriver(responder=responder), application_name="app")


def test_the_graph_carries_the_report_as_a_json_string_property_and_it_is_parsed():
    """Measured on the 1.3.0 reference graph: ``:Application`` carries ``entrypoint_report_json``
    (a JSON string of the whole ``TSEntrypointReport``) beside ``entrypoint_frameworks``. It is
    parsed, not rebuilt from nodes — there are no per-entrypoint nodes to rebuild it from."""
    coverage = _anchor_backend(
        {
            "entrypoint_frameworks": ["commander"],
            "entrypoint_report_json": json.dumps({"frameworks_detected": ["commander"], "rulesets": ["shipped"], "unresolved": {"x.get": 3}, "errors": []}),
        }
    ).get_entrypoint_coverage()
    assert coverage.frameworks_detected == ["commander"]
    assert coverage.rulesets == ["shipped"]
    assert coverage.unresolved == {"x.get": 3}
    assert coverage.errors == [] and coverage.diagnostics == []


def test_a_graph_without_the_property_says_so_in_the_shared_vocabulary():
    coverage = _anchor_backend({"entrypoint_frameworks": []}).get_entrypoint_coverage()
    assert [d.code for d in coverage.diagnostics] == ["entrypoint_report_unavailable"]
    assert "entrypoint_report_json" in coverage.diagnostics[0].message
    assert coverage.frameworks_detected == [] and coverage.unresolved == {}
