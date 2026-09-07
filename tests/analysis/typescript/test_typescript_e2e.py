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

"""End-to-end: the real ``codeanalyzer-typescript`` binary on the sample application, at every
level the SDK exposes. Skips cleanly when no binary resolves (``$CODEANALYZER_TS_BIN``, else the
``codeanalyzer-typescript`` wheel's ``bin_path()``)."""

import os
import shlex
import shutil
from pathlib import Path

import pytest
import toml

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.typescript.backend import CALL_GRAPH_NODE_KINDS

#: The documented vocabulary (backend module docstring, facade docstring, CHANGELOG) — pinned here so
#: the three cannot drift from what the index stores.
KINDS = {"module", "class", "interface", "enum", "type_alias", "namespace", "callable", "external"}


def _binary() -> str | None:
    env = os.environ.get("CODEANALYZER_TS_BIN")
    if env:
        exe = shlex.split(env)[0]
        return env if Path(exe).exists() or shutil.which(exe) else None
    try:
        import codeanalyzer_typescript

        path = Path(codeanalyzer_typescript.bin_path())
        return str(path) if path.exists() else None
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def _pinned_version() -> str:
    root = Path(__file__).resolve().parents[3]
    return toml.load(root / "pyproject.toml")["tool"]["backend-versions"]["codeanalyzer-typescript"]


pytestmark = pytest.mark.skipif(_binary() is None, reason="no codeanalyzer-typescript binary resolvable")


@pytest.fixture(scope="module", params=list(AnalysisLevel), ids=lambda lvl: lvl.name)
def analysis(request, typescript_application, tmp_path_factory):
    cache = tmp_path_factory.mktemp(f"ts-e2e-{request.param.name}")
    return CLDK.typescript(
        project_path=typescript_application,
        analysis_level=request.param,
        eager=True,
        backend=CodeAnalyzerConfig(cache_dir=str(cache)),
    )


def test_symbol_table_is_not_empty(analysis):
    symtab = analysis.get_symbol_table()
    assert symtab
    assert "src/models.ts" in symtab
    assert "src/models.User" in analysis.get_classes()


def test_call_graph_nodes_are_accessor_keys_and_kind_tagged(analysis):
    """Every node is a key some accessor returns (module file key, signature, ``"<module>.<name>"``)
    and never a raw ``can://`` id — nx would happily create a node from an unresolved endpoint,
    so "every endpoint is a node" proves nothing; this can go red."""
    graph = analysis.get_call_graph()
    known = set(analysis.get_symbol_table()) | set(analysis.get_classes()) | {c.signature for c in analysis.get_callables_overview()} | set(analysis.get_external_symbols())
    assert set(graph.nodes) <= known, set(graph.nodes) - known
    assert not any(n.startswith("can://") for n in graph.nodes)
    assert CALL_GRAPH_NODE_KINDS == KINDS
    for node, attrs in graph.nodes(data=True):
        assert attrs["kind"] in KINDS, (node, attrs)
        assert attrs["id"].startswith("can://"), (node, attrs)
    if analysis.analysis_level != AnalysisLevel.symbol_table:
        assert graph.number_of_edges() > 0
        assert graph.has_edge("src/index.main", "src/services.UserService.create")


def test_analyzer_version_is_the_pin(analysis):
    assert analysis.backend.analysis.analyzer.version == _pinned_version()
    assert (
        analysis.backend.analysis.max_level
        == {
            AnalysisLevel.symbol_table: 1,
            AnalysisLevel.call_graph: 2,
            AnalysisLevel.program_dependency_graph: 3,
            AnalysisLevel.system_dependency_graph: 4,
        }[AnalysisLevel(analysis.analysis_level)]
    )
