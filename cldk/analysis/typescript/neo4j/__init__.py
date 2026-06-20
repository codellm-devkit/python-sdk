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

"""Neo4j-backed TypeScript analysis backend (Cypher queries over the codeanalyzer-ts graph)."""

from cldk.analysis.typescript.neo4j.config import Neo4jConnectionConfig
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.analysis.typescript.neo4j.neo4j_ingestor import TSNeo4jIngestor

__all__ = ["TSNeo4jBackend", "TSNeo4jIngestor", "Neo4jConnectionConfig"]
