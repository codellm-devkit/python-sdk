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

"""TypeScript test fixtures.

Also overrides the heavy, network/zip-dependent session autouse fixtures from the top-level
``tests/conftest.py`` with no-ops, so the (fully mocked) TypeScript tests run in isolation
without downloading daytrader or extracting the Java/C sample zips.
"""

import json
from pathlib import Path

import pytest
import toml


def _testing_cfg() -> dict:
    root = Path(__file__).resolve().parents[3]
    return toml.load(root / "pyproject.toml")["tool"]["cldk"]["testing"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# --- neutralize the heavy autouse fixtures from the parent conftest for this subtree ---
@pytest.fixture(scope="session", autouse=True)
def test_fixture():  # noqa: D401 - override
    yield None


@pytest.fixture(scope="session", autouse=True)
def test_fixture_pbw():  # noqa: D401 - override
    yield None


# --- TypeScript-specific fixtures ---
@pytest.fixture(scope="session")
def typescript_application() -> Path:
    """Path to the sample TypeScript application fixture."""
    return (_repo_root() / _testing_cfg()["sample-typescript-application"]).resolve()


@pytest.fixture(scope="session")
def typescript_analysis_json() -> str:
    """The pre-computed analysis.json contents (as a JSON string) for the sample TS app."""
    path = _repo_root() / _testing_cfg()["sample-typescript-analysis-json"] / "v2" / "a4" / "analysis.json"
    with open(path, encoding="utf-8") as f:
        return json.dumps(json.load(f))


# --- a fake Neo4j driver, so TSNeo4jBackend can be constructed and probed in-process ---
#: The relationship types codeanalyzer-typescript 1.2.0 projects (``schema.neo4j.json`` at the
#: tag) -- a healthy graph by default, so fixtures that do not care about the schema probe need
#: not set ``rel_types`` themselves. ``main`` adds and removes none of them.
V2_RELATIONSHIP_TYPES = frozenset(
    {
        "TS_HAS_MODULE",
        "HAS_ARTIFACT",
        "DECLARES_DEPENDENCY",
        "LOCKS",
        "TS_PROVIDES",
        "TS_UNRESOLVED_IMPORT",
        "DEFINES_CONFIG",
        "TS_USES_CONFIG",
        "TS_DECLARES",
        "TS_HAS_METHOD",
        "TS_HAS_FIELD",
        "TS_DECORATED_BY",
        "TS_HAS_BODY_NODE",
        "TS_RESOLVES_TO",
        "TS_CALLS",
        "TS_CFG_NEXT",
        "TS_CDG",
        "TS_DDG",
        "TS_SUMMARY",
        "TS_PARAM_IN",
        "TS_PARAM_OUT",
        "TS_EXTENDS",
        "TS_IMPLEMENTS",
    }
)


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeSession:
    """Answers ``CALL db.relationshipTypes()`` from the driver's ``rel_types`` and the probe's
    ``analyzer_version`` read from ``analyzer_version``; every other statement goes to the
    driver's ``responder`` when set, else returns no rows."""

    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def run(self, query: str, **params):
        self._driver.statements.append(query)
        if "db.relationshipTypes" in query:
            return [_FakeRecord({"relationshipType": rt}) for rt in self._driver.rel_types]
        if "RETURN a.analyzer_version AS v" in query:
            return [] if self._driver.analyzer_version is None else [_FakeRecord({"v": self._driver.analyzer_version})]
        if self._driver.responder is not None:
            return [_FakeRecord(d) for d in self._driver.responder(query, params)]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stands in for ``neo4j.GraphDatabase.driver``. ``analyzer_version=None`` means "no
    ``:Application`` with that id"."""

    def __init__(self, rel_types=V2_RELATIONSHIP_TYPES, responder=None, analyzer_version="1.2.0") -> None:
        self.rel_types = set(rel_types)
        self.analyzer_version = analyzer_version
        self.responder = responder
        self.statements: list = []

    def session(self, database=None) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_driver() -> FakeDriver:
    return FakeDriver()
