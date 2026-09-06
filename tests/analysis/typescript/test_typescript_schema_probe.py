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

"""``TSNeo4jBackend``'s graph-schema probe (G5), no live Neo4j required.

A graph emitted by another codeanalyzer-typescript generation answers every statement here with
zero rows and no error -- the 0.4.3 vocabulary (``:Symbol``/``CALLS``/``HAS_CALLSITE``) has no
overlap with 1.2.0's at all. The probe catches that once, at attach: the relationship-type
fingerprint first, then the ``analyzer_version`` the ``:Application`` anchor stamps, against the
floor.
"""

import logging

import pytest

from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.utils.exceptions import GraphSchemaMismatch

REQUIRED = {"TS_HAS_MODULE", "TS_HAS_METHOD", "TS_HAS_BODY_NODE", "TS_CALLS"}
#: What codeanalyzer-typescript 0.4.3 projected (schema 1.0.0): nothing the backend queries.
V1_RELATIONSHIP_TYPES = {"HAS_MODULE", "DECLARES", "HAS_METHOD", "HAS_ATTRIBUTE", "HAS_CALLSITE", "CALLS", "RESOLVES_TO", "IMPORTS", "RE_EXPORTS", "DECORATED_BY", "DECLARES_VAR"}


def test_probe_refuses_a_0_4_3_graph(fake_driver):
    fake_driver.rel_types = V1_RELATIONSHIP_TYPES
    with pytest.raises(GraphSchemaMismatch) as e:
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert e.value.missing == REQUIRED
    assert "HAS_CALLSITE" in str(e.value)  # names what it found, not only what it wanted


def test_probe_refuses_an_empty_graph(fake_driver):
    fake_driver.rel_types = set()
    with pytest.raises(GraphSchemaMismatch):
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")


def test_probe_refuses_a_python_graph_naming_the_missing_ts_types(fake_driver):
    fake_driver.rel_types = {"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_BODY_NODE", "PY_CALLS", "HAS_ARTIFACT"}
    with pytest.raises(GraphSchemaMismatch) as e:
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert e.value.missing == REQUIRED
    assert "PY_CALLS" in str(e.value)


def test_probe_refuses_a_graph_below_the_analyzer_floor(fake_driver):
    """1.1.0 has the vocabulary but predates the id grammar and body-node shape this backend
    reads; refused, naming what was found and the floor."""
    fake_driver.analyzer_version = "1.1.0"
    with pytest.raises(GraphSchemaMismatch, match=r"1\.1\.0.*1\.2\.0 or newer"):
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")


@pytest.mark.parametrize(
    "raw, found",
    [(None, "has no :Application node"), ("garbage", "reports analyzer_version 'garbage'"), ("", "has an :Application node that carries no analyzer_version")],
    ids=["no-application", "unparsable", "empty"],
)
def test_probe_refuses_when_the_version_cannot_be_read(fake_driver, raw, found):
    """No ``:Application`` with that id, or a version that is not one, is *unknown* -- refused,
    because serving it would be the silent-empty defect with no signal -- and the message says
    which of the three it found."""
    fake_driver.analyzer_version = raw
    with pytest.raises(GraphSchemaMismatch, match="1.2.0 or newer") as e:
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert found in str(e.value)


@pytest.mark.parametrize("raw", ["1.2.0", "1.2.1", "1.3.0", "2.0.0"])
def test_probe_serves_every_generation_from_the_floor_up_silently(fake_driver, caplog, raw):
    fake_driver.analyzer_version = raw
    with caplog.at_level(logging.INFO, logger="cldk.analysis.typescript.neo4j.neo4j_backend"):
        backend = TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert backend._analyzer_version == tuple(int(x) for x in raw.split("."))
    assert not caplog.records, [r.getMessage() for r in caplog.records]


def test_probe_anchors_on_the_application_id_not_a_name(fake_driver):
    """The 1.2.0 anchor is ``:Application {id: can://typescript/<app>}``; there is no ``name``."""
    TSNeo4jBackend._from_driver(fake_driver, application_name="my-app")
    probe = next(s for s in fake_driver.statements if "analyzer_version" in s)
    assert "(a:Application {id: $app_id})" in probe and "count(a) AS n" in probe
    assert "{name:" not in probe


def test_application_name_is_required(fake_driver):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    with pytest.raises(CodeanalyzerExecutionException, match="application_name"):
        TSNeo4jBackend._from_driver(fake_driver, application_name=None)
