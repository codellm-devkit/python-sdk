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

The floor is **1.5.2** as of #376, raised from 2.5b's 1.3.0. Everything the older floor was about
still holds -- a 1.2.0 graph carries the whole v2 relationship vocabulary and passes the
fingerprint, so nothing but the version stamp can tell it apart, and its L4 port lattice is
disconnected from the statement DDG (cants#169), its body nodes carry no ``id`` (#165) and it still
writes ``_module`` (#166) -- and 1.5.1 added a second, sharper case of the same thing: it moved the
application to the outermost segment of the ``can://`` grammar, so a 1.5.0 graph has every required
type, every required field, and ids the application-prefix scoping cannot match. Every statement
comes back empty, which reads as "this codebase has nothing".

**Why 1.5.2 and not 1.5.1**, the release that actually flipped the grammar: 1.5.1 shipped with its
``ANALYZER_VERSION`` constant left at ``"1.5.0"`` (cants f3e2ada), so a 1.5.1 graph *stamps itself
1.5.0* and is indistinguishable from a genuine old-grammar one. There is no version test that
admits 1.5.1 and refuses 1.5.0, so the floor sits at the first release whose stamp tells the truth.
Serving any of them would answer the query surface with empties that read as facts, so the version
check is the only thing standing between a caller and that.
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


@pytest.mark.parametrize("raw", ["1.1.0", "1.2.0", "1.2.9", "1.3.0", "1.4.0", "1.5.0", "1.5.1"])
def test_probe_refuses_a_graph_below_the_analyzer_floor(fake_driver, raw):
    """Every generation below 1.5.2 is refused, naming what was found and the floor.

    Three of these are the ones that matter, and each declares every relationship type the
    fingerprint asks for, so the fingerprint passes and the version stamp is the only signal:

    * ``1.2.0`` -- the reason the leg-2.5a container on bolt://7690 is kept. What it lacks is
      behavioural (the wired L4 lattice, the body-node ids, the retired ``_module``), which no
      schema probe can see.
    * ``1.5.0`` -- the last old-grammar release. Its ids are ``can://typescript/<app>/…``, so
      every application-prefix predicate matches nothing.
    * ``1.5.1`` -- the release that flipped the grammar but stamped itself ``"1.5.0"``. It is
      refused because the stamp is the only evidence there is, and this one is wrong. Pinned here
      so nobody "fixes" the floor down to 1.5.1 and re-admits 1.5.0 along with it."""
    fake_driver.analyzer_version = raw
    with pytest.raises(GraphSchemaMismatch, match=rf"{raw}.*1\.5\.2 or newer"):
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")


def test_the_1_2_0_refusal_survives_a_complete_relationship_fingerprint(fake_driver):
    """The 1.2.0 graph is refused on its version, not on a missing type: assert the fingerprint it
    presents is a superset of what is required, so the refusal cannot be credited to the wrong
    check."""
    fake_driver.analyzer_version = "1.2.0"
    assert REQUIRED <= fake_driver.rel_types
    with pytest.raises(GraphSchemaMismatch) as e:
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert e.value.missing == set()


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
    with pytest.raises(GraphSchemaMismatch, match="1.5.2 or newer") as e:
        TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert found in str(e.value)


@pytest.mark.parametrize("raw", ["1.5.2", "1.5.3", "1.6.0", "2.0.0"])
def test_probe_serves_every_generation_from_the_floor_up_silently(fake_driver, caplog, raw):
    """The other direction of the floor: 1.5.2 -- the first release whose stamp matches the grammar
    it emits -- is served, and so is anything above it."""
    fake_driver.analyzer_version = raw
    with caplog.at_level(logging.INFO, logger="cldk.analysis.typescript.neo4j.neo4j_backend"):
        backend = TSNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert backend._analyzer_version == tuple(int(x) for x in raw.split("."))
    assert not caplog.records, [r.getMessage() for r in caplog.records]


def test_probe_anchors_on_the_application_id_not_a_name(fake_driver):
    """The anchor is ``:Application {id: can://<app>}``; there is no ``name`` to match on."""
    TSNeo4jBackend._from_driver(fake_driver, application_name="my-app")
    probe = next(s for s in fake_driver.statements if "analyzer_version" in s)
    assert "(a:Application {id: $app_id})" in probe and "count(a) AS n" in probe
    assert "{name:" not in probe


def test_application_name_is_required(fake_driver):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    with pytest.raises(CodeanalyzerExecutionException, match="application_name"):
        TSNeo4jBackend._from_driver(fake_driver, application_name=None)
