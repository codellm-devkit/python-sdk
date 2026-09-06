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

"""``JNeo4jBackend``'s graph-schema probe (J-9), no live Neo4j required.

A graph emitted by another codeanalyzer-java generation answers every statement this backend
issues with zero rows and no error: 2.4.1 wrote ``:JCompilationUnit`` under ``J_HAS_UNIT`` with
``<fqn>#<signature>`` ids and no ``can://`` anywhere, so nothing the 3.0.1 backend matches on
exists. The probe catches that once, at attach -- the relationship-type fingerprint first, then the
``analyzer_version`` the ``:JApplication`` anchor stamps, against the floor.
"""

import logging

import pytest

from cldk.analysis.java.neo4j.neo4j_backend import JNeo4jBackend
from cldk.utils.exceptions import GraphSchemaMismatch

REQUIRED = {"J_HAS_MODULE", "J_HAS_METHOD", "J_HAS_BODY_NODE", "J_CALLS"}

#: What codeanalyzer-java 2.4.1 projected: the compilation-unit vocabulary, none of which 3.0.1
#: writes and none of which this backend reads.
V1_RELATIONSHIP_TYPES = {
    "J_HAS_UNIT",
    "J_HAS_TYPE",
    "J_HAS_CALLABLE",
    "J_HAS_FIELD",
    "J_HAS_PARAMETER",
    "J_HAS_CALLSITE",
    "J_HAS_COMMENT",
    "J_HAS_INIT_BLOCK",
    "J_HAS_CRUD_OPERATION",
    "J_HAS_CRUD_QUERY",
    "J_DECLARES_VAR",
    "J_IMPORTS",
    "J_EXTENDS",
    "J_IMPLEMENTS",
    "J_CALLS",
}


def test_probe_refuses_a_2_4_1_shaped_graph(fake_driver):
    """The 2.4.1 vocabulary shares only ``J_CALLS``; the three containment types it lacks are
    named, and so is what it does have instead."""
    fake_driver.rel_types = V1_RELATIONSHIP_TYPES
    with pytest.raises(GraphSchemaMismatch) as e:
        JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")
    assert e.value.missing == {"J_HAS_MODULE", "J_HAS_METHOD", "J_HAS_BODY_NODE"}
    assert "J_HAS_UNIT" in str(e.value)  # names what it found, not only what it wanted


def test_probe_refuses_an_empty_graph(fake_driver):
    fake_driver.rel_types = set()
    with pytest.raises(GraphSchemaMismatch) as e:
        JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")
    assert e.value.missing == REQUIRED


def test_probe_refuses_a_python_graph_naming_the_missing_java_types(fake_driver):
    fake_driver.rel_types = {"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_BODY_NODE", "PY_CALLS", "HAS_ARTIFACT"}
    with pytest.raises(GraphSchemaMismatch) as e:
        JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")
    assert e.value.missing == REQUIRED
    assert "PY_CALLS" in str(e.value)


def test_probe_refuses_a_3_0_0_graph(fake_driver):
    """3.0.0 has the vocabulary but stamped contract 2.2.0 with a different body-node id grammar;
    3.0.1 is the floor (J-9), and the refusal names both the version found and the floor."""
    fake_driver.analyzer_version = "3.0.0"
    with pytest.raises(GraphSchemaMismatch, match=r"3\.0\.0.*3\.0\.1 or newer"):
        JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")


@pytest.mark.parametrize(
    "raw, found",
    [
        (None, "has no :JApplication node"),
        ("garbage", "reports analyzer_version 'garbage'"),
        ("", "has a :JApplication node that carries no analyzer_version"),
    ],
    ids=["absent-application", "garbage-version", "empty-version"],
)
def test_probe_refuses_when_the_version_cannot_be_read(fake_driver, raw, found):
    """No ``:JApplication`` with that name, or a version that is not one, is *unknown* -- refused,
    because serving it would be the silent-empty defect with no signal -- and the message says
    which of the three it found."""
    fake_driver.analyzer_version = raw
    with pytest.raises(GraphSchemaMismatch, match="3.0.1 or newer") as e:
        JNeo4jBackend._from_driver(fake_driver, application_name="daytrader9")
    assert found in str(e.value)


@pytest.mark.parametrize("raw", ["3.0.1", "3.0.2", "3.1.0", "4.0.0"])
def test_probe_serves_every_generation_from_the_floor_up_silently(fake_driver, caplog, raw):
    fake_driver.analyzer_version = raw
    with caplog.at_level(logging.INFO, logger="cldk.analysis.java.neo4j.neo4j_backend"):
        backend = JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")
    assert backend._analyzer_version == tuple(int(x) for x in raw.split("."))
    assert not caplog.records, [r.getMessage() for r in caplog.records]


def test_probe_anchors_on_the_application_name(fake_driver):
    """The 3.0.1 anchor is ``:JApplication {name}`` -- keyed by name, not by a ``can://`` id, and
    bound as a parameter. The fake answers the version *only* for the name it was told the graph
    holds, so a probe that matched on anything else -- an id, a file key, no key at all -- would
    attach to ``other-app`` instead of refusing it."""
    fake_driver.application_name = "my-app"
    JNeo4jBackend._from_driver(fake_driver, application_name="my-app")
    with pytest.raises(GraphSchemaMismatch, match="has no :JApplication node"):
        JNeo4jBackend._from_driver(fake_driver, application_name="other-app")
    assert not any("can://" in s for s in fake_driver.statements)


def test_attach_does_not_reconstruct_the_application(fake_driver):
    """Attach is the probe plus the module keys and nothing else: a graph the probe refuses must
    cost one round trip, not a whole-application fetch."""
    JNeo4jBackend._from_driver(fake_driver, application_name="daytrader8")
    assert len(fake_driver.statements) == 3, fake_driver.statements
    assert not any("J_HAS_METHOD" in s for s in fake_driver.statements)


def test_application_name_is_required(fake_driver):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    with pytest.raises(CodeanalyzerExecutionException, match="application_name"):
        JNeo4jBackend._from_driver(fake_driver, application_name=None)
