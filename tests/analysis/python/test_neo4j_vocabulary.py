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

"""D4 gate: the Neo4j Cypher speaks codeanalyzer-python 1.4.0's vocabulary, not 0.3.x's."""

import re
import pathlib

SRC = pathlib.Path("cldk/analysis/python/neo4j/neo4j_backend.py").read_text()

RETIRED = ["PyCallSite", "PY_HAS_CALLSITE", "PySymbol"]


def test_no_retired_labels_in_cypher():
    for name in RETIRED:
        assert name not in SRC, f"{name} is not emitted by codeanalyzer-python 1.4.0"


def test_call_sites_query_uses_body_nodes():
    assert "PY_HAS_BODY_NODE" in SRC
    assert re.search(r"PyBodyNode\s*\{?\s*kind", SRC), "call sites are body nodes with kind='call'"
