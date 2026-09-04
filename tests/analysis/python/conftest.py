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

from typing import Any, Callable

import pytest

from cldk.analysis.python.neo4j import PyNeo4jBackend

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
        if self._driver.responder is not None:
            return [_FakeRecord(d) for d in self._driver.responder(query, params)]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stub Neo4j driver with a settable ``rel_types`` set, standing in for a real
    ``neo4j.GraphDatabase`` driver in unit tests.

    ``responder``, when set, answers every statement that isn't the schema probe — a tiny
    in-memory Cypher stub (query text, params) -> rows, the same shape as the ad hoc
    ``_fake_cypher`` helper in ``test_python_method_lookup.py``, but wired through the real
    ``FakeSession.run`` so ``QueryCounter`` still counts the round trips genuinely fired.
    """

    def __init__(
        self,
        rel_types: set[str] | frozenset[str] = _V2_RELATIONSHIP_TYPES,
        responder: Callable[[str, dict], list[dict]] | None = None,
    ) -> None:
        self.rel_types: set[str] = set(rel_types)
        self.statements: list[str] = []
        self.responder = responder

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


# --------------------------------------------------------------------------------------------
# ``py`` — a PyNeo4jBackend fixture for Task 6's locate()/locate_many() tests.
#
# One module, "src/app.py": a docstring at line 1, a ``Store`` class whose ``__init__`` spans
# lines 9-11 and whose ``key`` method spans lines 18-19, with a real gap (lines 12-17) between
# them — so line 17 lands inside the class body but outside both methods' spans, and line 1 lands
# at real module top level. "test/conftest.py" is deliberately absent from ``_MODULES`` (a file
# that exists on disk but was never analysed, per the brief's file_not_in_graph outcome).
# --------------------------------------------------------------------------------------------
_LOCATE_MODULE_PATH = "src/app.py"
_LOCATE_MODULE_SOURCE = (
    '"""Store module."""\n'
    "\n"
    "class Store:\n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    def __init__(self, key):\n"
    "        self._key = key\n"
    "        self._value = None\n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    def key(self):\n"
    "        return self._key\n"
)
_LOCATE_CLASS = {"signature": "src.app.Store", "name": "Store"}
_LOCATE_CALLABLES = [
    {
        "signature": "src.app.Store.__init__",
        "name": "__init__",
        "start_line": 9,
        "end_line": 11,
        "code": "    def __init__(self, key):\n        self._key = key\n        self._value = None\n",
        "class_signature": _LOCATE_CLASS["signature"],
    },
    {
        "signature": "src.app.Store.key",
        "name": "key",
        "start_line": 18,
        "end_line": 19,
        "code": "    def key(self):\n        return self._key\n",
        "class_signature": _LOCATE_CLASS["signature"],
    },
]


def _locate_responder(query: str, params: dict) -> list[dict]:
    """Answers ``_load_module_keys`` and ``PyNeo4jBackend._LOCATE_QUERY`` for the ``py`` fixture."""
    if "RETURN m.file_key AS k" in query:
        return [{"k": _LOCATE_MODULE_PATH}]
    if "UNWIND $positions AS pos" in query:
        rows = []
        for pos in params["positions"]:
            if pos["path"] != _LOCATE_MODULE_PATH:
                rows.append({"idx": pos["idx"], "module_props": None, "callable_props": None, "class_props": None})
                continue
            module_props = {"file_key": _LOCATE_MODULE_PATH, "module_name": "src.app", "source": _LOCATE_MODULE_SOURCE}
            matches = [c for c in _LOCATE_CALLABLES if c["start_line"] <= pos["line"] <= c["end_line"]]
            if not matches:
                rows.append({"idx": pos["idx"], "module_props": module_props, "callable_props": None, "class_props": None})
                continue
            best = min(matches, key=lambda c: c["end_line"] - c["start_line"])
            rows.append({"idx": pos["idx"], "module_props": module_props, "callable_props": best, "class_props": _LOCATE_CLASS})
        return rows
    return []


@pytest.fixture
def py(fake_driver: FakeDriver) -> PyNeo4jBackend:
    """A :class:`PyNeo4jBackend` over the tiny ``src/app.py`` fixture above, driven by
    ``fake_driver`` — the same instance :func:`query_counter` wraps, so a test taking both
    fixtures counts genuine round trips against the backend ``py`` actually queries.
    """
    fake_driver.responder = _locate_responder
    return PyNeo4jBackend._from_driver(fake_driver, application_name="app")
