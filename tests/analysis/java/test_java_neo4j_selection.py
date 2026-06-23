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

"""Backend-selection unit tests for the Java facade (no live Neo4j required).

The Neo4j backend is fully mocked here, so these run anywhere. They verify that passing a
``Neo4jConnectionConfig`` swaps the facade onto :class:`JNeo4jBackend` (read-only), and that
without one the in-process :class:`JCodeanalyzer` is used.
"""

from unittest.mock import patch

import pytest

from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, Neo4jConnectionConfig
from cldk.analysis.java.java_analysis import JavaAnalysis


def test_neo4j_config_selects_neo4j_backend():
    config = Neo4jConnectionConfig(uri="bolt://example:7687", username="neo4j", password="secret", application_name="myapp")
    with patch("cldk.analysis.java.java_analysis.JNeo4jBackend") as backend_cls, patch("cldk.analysis.java.java_analysis.JCodeanalyzer") as in_process_cls:
        backend = backend_cls.return_value

        # Read-only: no project_dir needed, the graph is loaded out of band.
        analysis = JavaAnalysis(project_dir=None, source_code=None, analysis_level="call_graph", target_files=None, eager_analysis=False, backend=config)

        _, kwargs = backend_cls.call_args
        assert kwargs["neo4j_uri"] == "bolt://example:7687"
        assert kwargs["neo4j_password"] == "secret"
        assert kwargs["application_name"] == "myapp"
        assert analysis.backend is backend
        assert isinstance(analysis.backend_config, Neo4jConnectionConfig)
        in_process_cls.assert_not_called()


def test_no_config_uses_in_process_backend(tmp_path):
    with patch("cldk.analysis.java.java_analysis.JCodeanalyzer") as backend_cls, patch("cldk.analysis.java.java_analysis.JNeo4jBackend") as neo4j_cls:
        analysis = JavaAnalysis(project_dir=str(tmp_path), source_code=None, analysis_level="symbol_table", target_files=None, eager_analysis=False)

        backend_cls.assert_called_once()
        neo4j_cls.assert_not_called()
        assert analysis.backend is backend_cls.return_value
        assert isinstance(analysis.backend_config, CodeAnalyzerConfig)


def test_missing_neo4j_driver_raises_helpful_error():
    """Without the optional ``neo4j`` driver, constructing the backend explains how to install it."""
    import builtins

    from cldk.analysis.java.neo4j import JNeo4jBackend
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    real_import = builtins.__import__

    def _no_neo4j(name, *args, **kwargs):
        if name == "neo4j":
            raise ModuleNotFoundError("No module named 'neo4j'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_no_neo4j):
        with pytest.raises(CodeanalyzerExecutionException, match="neo4j"):
            JNeo4jBackend(neo4j_uri="bolt://example:7687", neo4j_username="neo4j", neo4j_password="neo4j", application_name="app")
