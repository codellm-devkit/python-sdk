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

"""Connection settings for the Neo4j-backed TypeScript analysis backend."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Neo4jConnectionConfig:
    """How the TypeScript facade reaches (and, optionally, populates) the graph database.

    Attributes:
        uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        username: Neo4j username.
        password: Neo4j password.
        database: Database name (None ⇒ server default).
        application_name: The ``:Application`` anchor name to scope queries to. Defaults to the
            analyzed project directory's name (matching ``codeanalyzer-typescript``'s
            ``--app-name`` default).
        build_db: If True (default), first populate the database from the project — via
            :class:`~cldk.analysis.typescript.neo4j.TSNeo4jIngestor` running
            ``codeanalyzer-typescript --emit neo4j`` — then query it. This is a local/dev
            convenience that needs the analyzer binary and the sources on disk. Set False for a
            cloud deployment where the graph is loaded out of band and the SDK only polls it
            (read-only, no binary, ``project_dir`` may be None).
    """

    uri: str
    username: str = "neo4j"
    password: str = "neo4j"
    database: str | None = None
    application_name: str | None = None
    build_db: bool = True
