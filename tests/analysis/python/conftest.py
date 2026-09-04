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

"""Python analysis test fixtures.

``FakeDriver``/``FakeSession`` stand in for the real ``neo4j`` driver — no container, no
network — so :class:`~cldk.analysis.python.neo4j.neo4j_backend.PyNeo4jBackend` can be
constructed and probed in-process. Build a backend from one via
``PyNeo4jBackend._from_driver(fake_driver, application_name=...)`` — the private constructor
seam that exists precisely so the public ``__init__`` (a real ``bolt://`` URI + credentials)
never has to accept anything but a URI string.
"""

from typing import Any

import pytest

# The full vocabulary codeanalyzer-python 1.4.0 emits (see cldk/analysis/python/neo4j/neo4j_backend
# .py's module docstring / the leg-1 brief) — a reasonable "healthy v2 graph" default so fixtures
# that don't care about the schema probe (e.g. a future round-trip-counting test) don't have to set
# rel_types themselves.
_V2_RELATIONSHIP_TYPES = frozenset(
    {
        "PY_CALLS",
        "PY_CDG",
        "PY_CFG_NEXT",
        "PY_DDG",
        "PY_DECLARES",
        "PY_DECLARES_VAR",
        "PY_DECORATED_BY",
        "PY_EXTENDS",
        "PY_HAS_ATTRIBUTE",
        "PY_HAS_BODY_NODE",
        "PY_HAS_METHOD",
        "PY_HAS_MODULE",
        "PY_IMPORTS",
        "PY_PARAM_IN",
        "PY_PARAM_OUT",
        "PY_PROVIDES",
        "PY_READS_CONFIG_UNRESOLVED",
        "PY_RESOLVES_TO",
        "PY_SUMMARY",
        "PY_UNRESOLVED_IMPORT",
        "PY_USES_CONFIG",
        "DECLARES_DEPENDENCY",
        "DEFINES_CONFIG",
        "HAS_ARTIFACT",
        "LOCKS",
    }
)


class _FakeRecord:
    """Stands in for ``neo4j.Record``: ``PyNeo4jBackend._run`` calls ``.data()`` on every row."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeSession:
    """Stub Neo4j session: answers ``CALL db.relationshipTypes()`` from the driver's settable
    ``rel_types``; every other statement gets an empty result, which is enough for the schema
    probe and for construction to complete without a real graph behind it.
    """

    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def run(self, query: str, **params: Any) -> list[_FakeRecord]:
        self._driver.statements.append(query)
        if "db.relationshipTypes" in query:
            return [_FakeRecord({"relationshipType": rt}) for rt in self._driver.rel_types]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stub Neo4j driver with a settable ``rel_types`` set, standing in for a real
    ``neo4j.GraphDatabase`` driver in unit tests.
    """

    def __init__(self, rel_types: set[str] | frozenset[str] = _V2_RELATIONSHIP_TYPES) -> None:
        self.rel_types: set[str] = set(rel_types)
        self.statements: list[str] = []

    def session(self, database: str | None = None) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_driver() -> FakeDriver:
    """A fresh :class:`FakeDriver`, defaulting to a healthy v2 vocabulary.

    Set ``fake_driver.rel_types`` before constructing a backend to simulate a different
    analyzer generation (or an empty graph).
    """
    return FakeDriver()


class QueryCounter:
    """Counts every Cypher statement actually executed against a driver.

    Not a stub: it wraps the driver's real ``session().run`` (via ``FakeDriver.statements``,
    which every ``FakeSession.run`` call appends to), so ``.count`` reflects genuine round
    trips. A counter that can't go wrong when the code under test fires N queries instead of
    one would defeat the point of asserting round-trip counts (see Task 6's ``locate_many``).
    """

    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    @property
    def count(self) -> int:
        return len(self.driver.statements)

    def reset(self) -> None:
        self.driver.statements.clear()


@pytest.fixture
def query_counter(fake_driver: FakeDriver) -> QueryCounter:
    """A :class:`QueryCounter` over a fresh, schema-healthy :class:`FakeDriver`."""
    return QueryCounter(fake_driver)
