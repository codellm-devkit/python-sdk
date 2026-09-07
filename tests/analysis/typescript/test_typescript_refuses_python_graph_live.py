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

"""Attaching the TypeScript Neo4j backend to a **Python** graph is refused, live and read-only.

The fake-driver probe matrix pins the same refusal in-process; this is the counterweight against a
real codeanalyzer-python graph, whose relationship vocabulary (``PY_*`` plus the shared artifact
layer) overlaps the TypeScript backend's on the artifact types only. Gated on its own variables,
with **no defaults**, because the TypeScript live suite points ``CLDK_TEST_NEO4J_URI`` at a
TypeScript graph:

    CLDK_TEST_NEO4J_PYTHON_URI=bolt://localhost:7689 \\
    CLDK_TEST_NEO4J_PYTHON_USER=neo4j \\
    CLDK_TEST_NEO4J_PYTHON_PASSWORD=... \\
    CLDK_TEST_NEO4J_PYTHON_APP=odoo-slim-19 \\
    uv run pytest tests/analysis/typescript/test_typescript_refuses_python_graph_live.py

Nothing here writes: one ``CALL db.relationshipTypes()`` to confirm the graph is a Python one, and
the backend's own attach.
"""

from __future__ import annotations

import logging
import os

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.utils.exceptions import GraphSchemaMismatch

logging.getLogger("neo4j").setLevel(logging.ERROR)

URI = os.environ.get("CLDK_TEST_NEO4J_PYTHON_URI")
USER = os.environ.get("CLDK_TEST_NEO4J_PYTHON_USER")
PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PYTHON_PASSWORD")
APP = os.environ.get("CLDK_TEST_NEO4J_PYTHON_APP", "odoo-slim-19")
REQUIRED = {"TS_HAS_MODULE", "TS_HAS_METHOD", "TS_HAS_BODY_NODE", "TS_CALLS"}

pytestmark = pytest.mark.skipif(not (URI and USER and PASSWORD), reason="set CLDK_TEST_NEO4J_PYTHON_URI / _PYTHON_USER / _PYTHON_PASSWORD to a codeanalyzer-python graph (read-only)")


def test_attaching_to_a_python_graph_is_refused_naming_the_missing_ts_types():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        with driver.session() as s:
            found = {r["relationshipType"] for r in s.run("CALL db.relationshipTypes()")}
    finally:
        driver.close()
    if "PY_HAS_MODULE" not in found:
        pytest.skip(f"{URI} does not hold a codeanalyzer-python graph")

    with pytest.raises(GraphSchemaMismatch) as e:
        CLDK.typescript(project_path=None, backend=Neo4jConnectionConfig(uri=URI, username=USER, password=PASSWORD, application_name=APP))
    assert e.value.missing == REQUIRED
    assert e.value.found == found
    for rel in REQUIRED:
        assert rel in str(e.value)
