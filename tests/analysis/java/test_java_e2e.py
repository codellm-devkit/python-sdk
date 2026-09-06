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

"""End-to-end: the real codeanalyzer-java 3.0.1 jar on the daytrader8 sample application, at the
symbol-table and call-graph levels. The jar is not committed (python-sdk#339): point
``CLDK_CODEANALYZER_JAVA_JAR`` at the release asset, or the test skips — the jar bundled in the tree
is still 2.4.1 until the wheel-based exec path lands. The JDK comes from the SDK's own
``ensure_jdk`` (a cached Temurin, a ``$JAVA_HOME`` with ``jmods``, or a download); the analyzer
builds the project with Maven itself. Also skips when ``CLDK_SKIP_JAVA_E2E`` is set or no JDK can be
provisioned."""

import os
from pathlib import Path

import pytest
import toml

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.java.codeanalyzer._jdk import ensure_jdk

pytestmark = [
    pytest.mark.skipif(bool(os.environ.get("CLDK_SKIP_JAVA_E2E")), reason="CLDK_SKIP_JAVA_E2E is set"),
    pytest.mark.skipif(not os.environ.get("CLDK_CODEANALYZER_JAVA_JAR"), reason="CLDK_CODEANALYZER_JAVA_JAR is unset: the bundled jar predates schema v2"),
]

LEVELS = [AnalysisLevel.symbol_table, AnalysisLevel.call_graph]


def _pinned_version() -> str:
    root = Path(__file__).resolve().parents[3]
    return toml.load(root / "pyproject.toml")["tool"]["backend-versions"]["codeanalyzer-java"]


@pytest.fixture(scope="module", params=LEVELS, ids=lambda lvl: lvl.name)
def analysis(request, test_fixture, tmp_path_factory):
    cache = tmp_path_factory.mktemp(f"java-e2e-{request.param.name}")
    try:
        ensure_jdk(cache / "java")  # the same lookup the backend makes; a download failure is a skip, not a red
    except Exception as exc:  # noqa: BLE001 — network / platform failures are not this test's subject
        pytest.skip(f"no JDK could be provisioned: {exc}")
    return CLDK.java(
        project_path=test_fixture,
        analysis_level=request.param,
        eager=True,
        backend=CodeAnalyzerConfig(cache_dir=str(cache)),
    )


def test_symbol_table_has_every_daytrader8_unit(analysis):
    symtab = analysis.get_symbol_table()
    assert len(symtab) == 138
    assert "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java" in symtab
    assert "com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect" in analysis.get_classes()


def test_analyzer_version_is_the_pin(analysis):
    assert analysis.backend.analysis.analyzer.version == _pinned_version()
    assert analysis.backend.analysis.schema_version == "2.0.0"
    assert analysis.backend.analysis.max_level == {AnalysisLevel.symbol_table: 1, AnalysisLevel.call_graph: 2}[AnalysisLevel(analysis.analysis_level)]


def test_call_graph_nodes_are_fqn_dot_signature_strings(analysis):
    """J-1: every node is ``"<type fqn>.<signature>"`` — a string, never a tuple, never ``can://`` —
    and resolves through the accessors: ``signature in get_methods()[klass]``."""
    graph = analysis.get_call_graph()
    methods = analysis.get_methods()
    for node, attrs in graph.nodes(data=True):
        assert isinstance(node, str) and not node.startswith("can://"), node
        detail = attrs["method_detail"]
        assert node == f"{detail.klass}.{detail.method.signature}"
        assert attrs["kind"] == "callable"
        assert detail.method.signature in methods[detail.klass], node
    if AnalysisLevel(analysis.analysis_level) is AnalysisLevel.symbol_table:
        assert graph.number_of_edges() == 0
    else:
        assert graph.number_of_edges() > 0
        for _, _, data in graph.edges(data=True):
            assert data["type"] == "CALL_DEP" and isinstance(data["weight"], int) and isinstance(data["calling_lines"], list)
