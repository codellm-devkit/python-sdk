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

"""End-to-end: the real codeanalyzer-java analyzer on the daytrader8 sample application, at the
symbol-table and call-graph levels. Both the jar and the JVM it runs on come from the pinned
``codeanalyzer-java`` wheel (the ``java`` extra), so *starting* the analyzer needs no JDK on the
machine and no ``JAVA_HOME``.

**The full answer still needs Maven.** The analyzer builds the project itself, and its auto-build
shells out to ``mvn``, which brings its own JDK; on a ``PATH`` with neither, codeanalyzer-java
degrades rather than failing — exit 0, the level still stamped, and a call graph of **1,391
declared-only** edges where the built run has **1,862**. So the call-graph assertions here require
``analyzer_diagnostics == []``: ``number_of_edges() > 0`` alone passes on the degraded graph, which
is how a Java-less runner would go green on a partial answer (#341, and
``test_java_degradation.py`` for the degraded path itself).

Skips only when ``CLDK_SKIP_JAVA_E2E`` is set."""

import os
from pathlib import Path

import pytest
import toml

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig

pytestmark = pytest.mark.skipif(bool(os.environ.get("CLDK_SKIP_JAVA_E2E")), reason="CLDK_SKIP_JAVA_E2E is set")

LEVELS = [AnalysisLevel.symbol_table, AnalysisLevel.call_graph]


def _pinned_version() -> str:
    root = Path(__file__).resolve().parents[3]
    return toml.load(root / "pyproject.toml")["tool"]["backend-versions"]["codeanalyzer-java"]


@pytest.fixture(scope="module", params=LEVELS, ids=lambda lvl: lvl.name)
def analysis(request, test_fixture, tmp_path_factory):
    cache = tmp_path_factory.mktemp(f"java-e2e-{request.param.name}")
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
        assert analysis.backend.analyzer_diagnostics == [], "the analyzer degraded (no build tool?); this is not the full call graph"
        assert graph.number_of_edges() > 0
        for _, _, data in graph.edges(data=True):
            assert data["type"] == "CALL_DEP" and isinstance(data["weight"], int) and isinstance(data["calling_lines"], list)
            # J-8/#8: absolute file lines, sorted — not offsets into ``JCallable.code``, which is a
            # different string on each backend.
            assert data["calling_lines"] == sorted(data["calling_lines"]) and all(n > 0 for n in data["calling_lines"])


def test_view_dispatches_reach_daytrader8s_jsps(analysis):
    """codeanalyzer-java 3.3.x (python-sdk#404): at level 1 and 2 the literal tier closes 3 sites
    and the table tier (3.3.1) 34 more -- every page in ``TradeConfig.webUI`` that exists on disk,
    from ``TradeConfigServlet``'s two sites -- so 37 edges reach 19 of daytrader8's 23 JSPs. The
    call-graph tier that binds ``TradeServletAction.requestDispatch`` runs at level 4 only, which
    these two levels do not reach. Measured off the 3.3.3 jar, not off the SDK."""
    edges = analysis.get_view_dispatches()
    assert len(edges) == 37
    assert {(e.via, tuple(e.prov)) for e in edges} == {("forward", ("literal",)), ("include", ("literal",)), ("include", ("table",))}
    assert len({e.dst for e in edges}) == 19
    assert [d.key for d in analysis.get_view_dispatchers("PingServlet2Jsp.jsp")] == [
        "com.ibm.websphere.samples.daytrader.web.prims.PingServlet2Jsp.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse)"
    ]
    unresolved = analysis.get_unresolved_view_dispatches()
    assert {u.reason for u in unresolved} == {"no-such-artifact", "non-literal"}
    assert {u.target for u in unresolved if u.reason == "no-such-artifact"} == {"welcome.faces", "/servlet/PingServlet2IncludeRcv", "/servlet/PingServlet2ServletRcv"}
    views = {p for p, a in analysis.get_artifacts().items() if "view-template" in a.roles}
    assert len(views) == 38 and all(p.endswith((".jsp", ".xhtml")) for p in views)

