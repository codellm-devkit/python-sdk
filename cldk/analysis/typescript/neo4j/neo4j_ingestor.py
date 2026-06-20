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

"""Populate a Neo4j graph from a TypeScript project (the *writer* half of the Neo4j integration).

:class:`TSNeo4jIngestor` shells out to the ``codeanalyzer-typescript`` binary with
``--emit neo4j --neo4j-uri ...`` to push a project's graph into a live Neo4j over Bolt. It is a
**local / dev convenience**: it needs the analyzer binary *and* the project sources on disk, and it
writes to the database.

It is deliberately separate from :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`, the
read-only Cypher query client. In a cloud deployment the graph is populated out of band — e.g. a
third-party job inside Kubernetes runs the analyzer — and the SDK only *polls* that database for
analytics. There you construct a :class:`TSNeo4jBackend` directly (no binary, no ``project_dir``,
read-only credentials) and never touch this ingestor. Use this class only when you also want CLDK
to build the graph for you, typically on a developer's machine.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import List, Union

from cldk.analysis import AnalysisLevel
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class TSNeo4jIngestor:
    """Build (populate) the application view of a TypeScript project into Neo4j.

    Args:
        project_dir: Root of the TypeScript project to analyze (required).
        analysis_backend_path: Directory containing the ``codeanalyzer-typescript`` binary. If
            None, falls back to ``$CODEANALYZER_TS_BIN``, then the ``codeanalyzer-typescript``
            PyPI package (``pip install codeanalyzer-typescript``).
        analysis_level: ``AnalysisLevel.symbol_table`` (1) or ``AnalysisLevel.call_graph`` (2).
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Write credentials.
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:Application`` anchor name. Defaults to the project directory name,
            matching ``codeanalyzer-typescript``'s ``--app-name`` default.
        eager_analysis: If True, force a clean rebuild of the graph even if this application's
            ``:Application`` anchor already exists in the database.
        target_files: Restrict analysis to these files (incremental push).
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_backend_path: Union[str, Path, None],
        analysis_level: str,
        neo4j_uri: str,
        neo4j_username: str,
        neo4j_password: str,
        neo4j_database: str | None = None,
        application_name: str | None = None,
        eager_analysis: bool = False,
        target_files: List[str] | None = None,
    ) -> None:
        if project_dir is None:
            raise CodeanalyzerExecutionException("project_dir is required to build a Neo4j graph.")
        self.project_dir = project_dir
        self.analysis_backend_path = analysis_backend_path
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.application_name = application_name or Path(project_dir).name
        self._neo4j_conn = (neo4j_uri, neo4j_username, neo4j_password)
        self._database = neo4j_database

    # -----[ binary resolution ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """Resolve the codeanalyzer-typescript executable command (mirrors TSCodeanalyzer)."""
        if self.analysis_backend_path:
            backend = Path(self.analysis_backend_path)
            binary = next(
                (p for p in backend.rglob("codeanalyzer-typescript*") if p.is_file()),
                None,
            ) or next((p for p in backend.rglob("codeanalyzer-ts*") if p.is_file()), None)
            if binary is None:
                raise CodeanalyzerExecutionException("codeanalyzer-typescript binary not found in the provided analysis_backend_path.")
            return [str(binary)]

        env_bin = os.environ.get("CODEANALYZER_TS_BIN")
        if env_bin:
            return shlex.split(env_bin)

        # Prebuilt binary from the `codeanalyzer-typescript` PyPI package (platform wheel).
        try:
            import codeanalyzer_typescript

            return [str(codeanalyzer_typescript.bin_path())]
        except (ModuleNotFoundError, FileNotFoundError):
            pass

        raise CodeanalyzerExecutionException(
            "codeanalyzer-typescript binary not found. Install it with `pip install codeanalyzer-typescript`, "
            "pass analysis_backend_path=<dir containing the binary>, or set $CODEANALYZER_TS_BIN."
        )

    # -----[ DB population ]-----
    def build(self) -> None:
        """Push this project's graph into Neo4j via ``--emit neo4j --neo4j-uri`` (Bolt).

        Lazy by default: if the ``:Application`` anchor already exists and ``eager_analysis`` is
        False (and this is not a targeted/incremental run), the push is skipped.
        """
        if not self.eager_analysis and not self.target_files and self._application_exists():
            logger.info("Neo4j already has application '%s'; skipping rebuild (lazy).", self.application_name)
            return

        uri, user, password = self._neo4j_conn
        level = 1 if self.analysis_level == AnalysisLevel.symbol_table else 2
        args = self._get_codeanalyzer_exec() + [
            "-i",
            str(Path(self.project_dir)),
            "-a",
            str(level),
            "--emit",
            "neo4j",
            "--neo4j-uri",
            uri,
            "--neo4j-user",
            user,
            "--neo4j-password",
            password,
            "--app-name",
            self.application_name,
        ]
        if self._database:
            args += ["--neo4j-database", self._database]
        if self.eager_analysis:
            args += ["--eager"]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]

        try:
            logger.info("Running codeanalyzer-typescript (neo4j emit): %s", " ".join(args))
            subprocess.run(args, capture_output=True, text=True, check=True)
        except Exception as e:  # noqa: BLE001
            raise CodeanalyzerExecutionException(str(e)) from e

    def _application_exists(self) -> bool:
        """Whether this application's ``:Application`` anchor is already loaded in the database."""
        from neo4j import GraphDatabase

        uri, user, password = self._neo4j_conn
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session(database=self._database) as session:
                record = session.run(
                    "MATCH (a:Application {name: $app}) RETURN count(a) AS c",
                    app=self.application_name,
                ).single()
                return bool(record and record["c"] > 0)
        finally:
            driver.close()
