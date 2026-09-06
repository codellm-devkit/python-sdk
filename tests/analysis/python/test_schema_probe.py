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

"""Unit tests for PyNeo4jBackend's graph-schema probe (no live Neo4j required).

A graph built by a different codeanalyzer-python generation answers every query with zero rows
and no error — indistinguishable from "this codebase has no callables". These tests assert that
the mismatch is instead caught loudly, once, at connection time.
"""

import pytest

from cldk.utils.exceptions import GraphSchemaMismatch
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

REQUIRED = {"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_BODY_NODE", "PY_CALLS"}


def test_probe_raises_on_v1_graph(fake_driver):
    """A graph built by codeanalyzer-python 0.3.x has PY_HAS_CALLSITE, not PY_HAS_BODY_NODE."""
    fake_driver.rel_types = {"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_CALLSITE", "PY_CALLS"}
    with pytest.raises(GraphSchemaMismatch) as e:
        PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert "PY_HAS_BODY_NODE" in e.value.missing
    assert "PY_HAS_CALLSITE" in str(e.value)  # names what it found, not just what it wanted


def test_probe_passes_on_v2_graph(fake_driver):
    fake_driver.rel_types = REQUIRED | {"PY_DDG", "PY_PARAM_IN"}
    PyNeo4jBackend._from_driver(fake_driver, application_name="app")  # no raise


def test_probe_raises_on_empty_graph(fake_driver):
    """An asset-only graph must not look like an application with no code."""
    fake_driver.rel_types = set()
    with pytest.raises(GraphSchemaMismatch):
        PyNeo4jBackend._from_driver(fake_driver, application_name="app")
