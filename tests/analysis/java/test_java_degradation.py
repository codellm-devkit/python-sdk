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

"""#341: what the SDK says when codeanalyzer-java degrades the analysis it was asked for.

L3/L4 on Java need compiled classes — the analyzer builds the project itself, and when that build
cannot run it emits a declared-only call graph and an SDG with no ``points-to`` provenance, exits
0, and still stamps ``max_level`` with the level it was asked for. The only authoritative signal
is the analyzer's own WARN log, so the first two tests drive the real analyzer with and without
Maven on ``PATH`` and read the verdict off the backend. The third needs no analyzer: it pins the
state that has no verdict at all, which must read as *unknown* and never as clean.
"""

import json
import logging
import os
import shutil
from pathlib import Path

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.java.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.codeanalyzer.codeanalyzer import VERDICT_FILE

#: Marked the way ``test_java_e2e.py`` is: these two runs need the real analyzer.
needs_analyzer = pytest.mark.skipif(bool(os.environ.get("CLDK_SKIP_JAVA_E2E")), reason="CLDK_SKIP_JAVA_E2E is set")

LOGGER = "cldk.analysis.java.codeanalyzer.codeanalyzer"

#: A ``PATH`` with no build tool on it, so the analyzer's auto-build cannot run. Set with
#: ``monkeypatch`` for the duration of one test, so only the analyzer subprocess that test starts
#: inherits it.
NO_BUILD_TOOL_PATH = os.pathsep.join(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER and record.levelno >= logging.WARNING]


@needs_analyzer
def test_l4_without_compiled_classes_reports_the_analyzers_own_words(test_fixture, tmp_path, monkeypatch, caplog):
    """A copy of daytrader8 with no ``target/`` and no Maven reachable: the analyzer degrades, and
    the SDK reports the sentences it logged — still returning the declared-only graph."""
    project = tmp_path / "daytrader8-unbuilt"
    shutil.copytree(test_fixture, project)
    shutil.rmtree(project / "target", ignore_errors=True)
    cache = tmp_path / "cache"
    monkeypatch.setenv("PATH", NO_BUILD_TOOL_PATH)
    assert shutil.which("mvn") is None and shutil.which("gradle") is None

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        analysis = CLDK.java(
            project_path=project,
            analysis_level=AnalysisLevel.system_dependency_graph,
            eager=True,
            backend=CodeAnalyzerConfig(cache_dir=str(cache)),
        )

    diagnostics = analysis.backend.analyzer_diagnostics
    assert diagnostics, "the analyzer logged its degradation; the backend recorded nothing"
    assert {d.code for d in diagnostics} == {"level_too_low"}
    messages = [d.message for d in diagnostics]
    assert any(m.startswith("RTA call graph unavailable") and m.endswith("emitting declared edges only") for m in messages), messages
    assert any(m.startswith("L4 semantic ddg unavailable") for m in messages), messages
    assert all("\x1b" not in m and "[WARN]" not in m for m in messages), messages

    # Surfaced once, at WARNING, naming the level that was asked for and quoting the analyzer.
    logged = _warnings(caplog)
    assert logged == [f"codeanalyzer-java did not fully compute analysis_level=system_dependency_graph: {m}" for m in messages]

    # The graph is still returned: a declared-only call graph is the L2 answer, not an error.
    assert analysis.get_call_graph().number_of_edges() > 0
    assert analysis.backend.analysis.max_level == 4

    # The verdict lives beside analysis.json in the SDK's cache, not inside the analyzer's payload.
    verdict_file = cache / "java" / VERDICT_FILE
    assert json.loads(verdict_file.read_text(encoding="utf-8")) == [d.model_dump() for d in diagnostics]
    assert "analyzer_diagnostics" not in (cache / "java" / "analysis.json").read_text(encoding="utf-8")

    # And a cache hit — no analyzer run, so no log to read — still knows.
    cached = CLDK.java(
        project_path=project,
        analysis_level=AnalysisLevel.system_dependency_graph,
        eager=False,
        backend=CodeAnalyzerConfig(cache_dir=str(cache)),
    )
    assert cached.backend.analyzer_diagnostics == diagnostics


@needs_analyzer
@pytest.mark.skipif(shutil.which("mvn") is None, reason="the analyzer's auto-build needs Maven on PATH")
def test_l4_with_compiled_classes_is_silent(test_fixture, tmp_path, caplog):
    """The same project, built: the analyzer reports no degradation, so the SDK says nothing —
    ``[]`` is "the analyzer reported none", the state that must stay distinguishable from
    ``None``."""
    project = tmp_path / "daytrader8-built"
    shutil.copytree(test_fixture, project)
    cache = tmp_path / "cache"

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        analysis = CLDK.java(
            project_path=project,
            analysis_level=AnalysisLevel.system_dependency_graph,
            eager=True,
            backend=CodeAnalyzerConfig(cache_dir=str(cache)),
        )

    assert (project / "target" / "classes").is_dir(), "the analyzer's auto-build did not produce classes"
    assert analysis.backend.analyzer_diagnostics == []
    assert _warnings(caplog) == []
    assert json.loads((cache / "java" / VERDICT_FILE).read_text(encoding="utf-8")) == []
    assert any("rta" in edge.prov for edge in analysis.backend.application.call_graph)


def test_an_analysis_json_with_no_recorded_verdict_reads_as_unknown(test_fixture, analysis_json_a4, tmp_path, caplog):
    """The third state, and the one #341 is about one level up: an ``analysis.json`` this backend
    did not write — a cache from before the verdict file existed, or a payload dropped into the
    cache directory from elsewhere — carries no verdict. ``None``, never ``[]``, and said out loud.
    """
    (tmp_path / "analysis.json").write_text(analysis_json_a4, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        backend = JCodeanalyzer(
            project_dir=test_fixture,
            analysis_json_path=tmp_path,
            analysis_level=AnalysisLevel.system_dependency_graph,
            eager_analysis=False,
            target_files=None,
        )

    assert backend.analyzer_diagnostics is None
    assert backend.analyzer_diagnostics != []
    assert _warnings(caplog) == [
        "codeanalyzer-java recorded no degradation verdict for this analysis: whether analysis_level=system_dependency_graph was fully computed is unknown"
    ]
    assert not (tmp_path / VERDICT_FILE).exists()
    assert backend.analysis.max_level == 4


def test_symbol_table_does_not_warn_about_a_missing_verdict(test_fixture, analysis_json, tmp_path, caplog):
    """Nothing the analyzer can degrade runs below the call graph, so a verdict-less L1 cache hit is
    not an unknown worth a warning — the attribute still says ``None``."""
    (tmp_path / "analysis.json").write_text(analysis_json, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        backend = JCodeanalyzer(
            project_dir=test_fixture,
            analysis_json_path=tmp_path,
            analysis_level=AnalysisLevel.symbol_table,
            eager_analysis=False,
            target_files=None,
        )

    assert backend.analyzer_diagnostics is None
    assert _warnings(caplog) == []
