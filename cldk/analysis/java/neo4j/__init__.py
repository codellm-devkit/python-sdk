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

"""Read-only Neo4j-backed Java analysis backend (Cypher queries over the codeanalyzer-java graph)."""

from cldk.analysis.java.neo4j.config import Neo4jConnectionConfig
from cldk.analysis.java.neo4j.neo4j_backend import JNeo4jBackend

__all__ = ["JNeo4jBackend", "Neo4jConnectionConfig"]
