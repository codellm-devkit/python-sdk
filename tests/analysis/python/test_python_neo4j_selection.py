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

"""Backend-selection unit tests for the Python facade (no live Neo4j required).

The Neo4j backend is fully mocked here, so these run anywhere. They verify that passing a
``Neo4jConnectionConfig`` swaps the facade onto :class:`PyNeo4jBackend` (read-only), and that
without one the in-memory :class:`PyCodeanalyzer` is used, unchanged.
"""

from unittest.mock import MagicMock, patch

import pytest

from cldk.analysis.commons.backend_config import Neo4jConnectionConfig, PyCodeAnalyzerConfig
from cldk.analysis.python.python_analysis import PythonAnalysis


def test_neo4j_config_selects_neo4j_backend():
    config = Neo4jConnectionConfig(
        uri="bolt://example:7687",
        username="neo4j",
        password="secret",
        application_name="myapp",
    )
    with patch("cldk.analysis.python.python_analysis.PyNeo4jBackend") as backend_cls, patch("cldk.analysis.python.python_analysis.PyCodeanalyzer") as in_memory_cls:
        backend = backend_cls.return_value

        # Read-only: no project_dir needed, the graph is loaded out of band.
        analysis = PythonAnalysis(
            project_dir=None,
            analysis_level="call_graph",
            target_files=None,
            eager_analysis=False,
            backend=config,
        )

        # The (read-only) neo4j backend was constructed with the config's connection details...
        _, kwargs = backend_cls.call_args
        assert kwargs["neo4j_uri"] == "bolt://example:7687"
        assert kwargs["neo4j_password"] == "secret"
        assert kwargs["application_name"] == "myapp"
        assert analysis.backend is backend
        # ...and the in-memory backend was never built.
        in_memory_cls.assert_not_called()


def test_no_config_uses_in_memory_backend(tmp_path):
    with patch("cldk.analysis.python.python_analysis.PyCodeanalyzer") as backend_cls, patch("cldk.analysis.python.python_analysis.PyNeo4jBackend") as neo4j_cls:
        backend_cls.return_value = MagicMock()

        analysis = PythonAnalysis(
            project_dir=str(tmp_path),
            analysis_level="symbol_table",
            target_files=None,
            eager_analysis=False,
        )

        backend_cls.assert_called_once()
        neo4j_cls.assert_not_called()
        assert analysis.backend is backend_cls.return_value
        # The default config is the in-process codeanalyzer one.
        assert isinstance(analysis.backend_config, PyCodeAnalyzerConfig)


def test_no_config_keys_cache_dir_by_language(tmp_path):
    """The in-process backend caches under a language-keyed <cache_dir>/python subdir."""
    with patch("cldk.analysis.python.python_analysis.PyCodeanalyzer") as backend_cls:
        backend_cls.return_value = MagicMock()

        PythonAnalysis(
            project_dir=str(tmp_path),
            analysis_level="symbol_table",
            target_files=None,
            eager_analysis=False,
            backend=PyCodeAnalyzerConfig(cache_dir=str(tmp_path / "cache")),
        )

        _, kwargs = backend_cls.call_args
        assert kwargs["cache_dir"] == (tmp_path / "cache" / "python")


def test_missing_project_dir_without_config_raises():
    """Without a Neo4j config, project_dir is still required (no source_code mode for Python)."""
    with pytest.raises(ValueError, match="project_dir is required"):
        PythonAnalysis(
            project_dir=None,
            analysis_level="symbol_table",
            target_files=None,
            eager_analysis=False,
        )


def test_missing_neo4j_driver_raises_helpful_error():
    """Without the optional ``neo4j`` driver, constructing the backend explains how to install it."""
    import builtins

    from cldk.analysis.python.neo4j import PyNeo4jBackend
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    real_import = builtins.__import__

    def _no_neo4j(name, *args, **kwargs):
        if name == "neo4j":
            raise ModuleNotFoundError("No module named 'neo4j'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_no_neo4j):
        with pytest.raises(CodeanalyzerExecutionException, match="neo4j"):
            PyNeo4jBackend(
                neo4j_uri="bolt://example:7687",
                neo4j_username="neo4j",
                neo4j_password="neo4j",
                application_name="app",
            )
