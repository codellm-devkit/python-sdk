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

"""Backend-selection unit tests for the TypeScript facade (no live Neo4j required).

The Neo4j backend is fully mocked here, so these run anywhere. They verify that passing a
``Neo4jConnectionConfig`` swaps the facade onto :class:`TSNeo4jBackend` and that the facade's
``get_*`` methods are thin delegations to whichever backend is wired in.
"""

from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, Neo4jConnectionConfig


def test_neo4j_config_selects_neo4j_backend(typescript_application):
    config = Neo4jConnectionConfig(
        uri="bolt://example:7687",
        username="neo4j",
        password="secret",
        application_name="myapp",
    )
    with patch("cldk.analysis.typescript.typescript_analysis.TSNeo4jBackend") as backend_cls:
        backend = backend_cls.return_value
        backend.get_application.return_value = MagicMock()

        analysis = CLDK.typescript(
            project_path=typescript_application,
            analysis_level=AnalysisLevel.call_graph,
            backend=config,
        )

        # The (read-only) neo4j backend was constructed with the config's connection details.
        _, kwargs = backend_cls.call_args
        assert kwargs["neo4j_uri"] == "bolt://example:7687"
        assert kwargs["neo4j_password"] == "secret"
        assert kwargs["application_name"] == "myapp"
        assert analysis.backend is backend

        # ...and a representative query delegates straight to it.
        analysis.get_call_graph()
        backend.get_call_graph.assert_called_once()
        analysis.get_classes()
        backend.get_all_classes.assert_called_once()


def test_no_config_uses_in_memory_backend(typescript_application):
    with patch("cldk.analysis.typescript.typescript_analysis.TSCodeanalyzer") as backend_cls, patch("cldk.analysis.typescript.typescript_analysis.TSNeo4jBackend") as neo4j_cls:
        backend_cls.return_value.get_application.return_value = MagicMock()

        analysis = CLDK.typescript(
            project_path=typescript_application,
            analysis_level=AnalysisLevel.symbol_table,
        )

        backend_cls.assert_called_once()
        neo4j_cls.assert_not_called()
        assert analysis.backend is backend_cls.return_value
        assert isinstance(analysis.backend_config, CodeAnalyzerConfig)
        # The in-process backend caches under a language-keyed <cache_dir>/typescript subdir.
        _, kwargs = backend_cls.call_args
        assert str(kwargs["analysis_json_path"]).endswith("/.codeanalyzer/typescript")


def test_missing_neo4j_driver_raises_helpful_error():
    """Without the optional ``neo4j`` driver, constructing the backend explains how to install it."""
    import builtins

    from cldk.analysis.typescript.neo4j import TSNeo4jBackend
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    real_import = builtins.__import__

    def _no_neo4j(name, *args, **kwargs):
        if name == "neo4j":
            raise ModuleNotFoundError("No module named 'neo4j'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_no_neo4j):
        with pytest.raises(CodeanalyzerExecutionException, match="neo4j"):
            TSNeo4jBackend(
                neo4j_uri="bolt://example:7687",
                neo4j_username="neo4j",
                neo4j_password="neo4j",
                application_name="app",
            )
