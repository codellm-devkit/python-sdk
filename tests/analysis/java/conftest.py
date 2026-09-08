import os
from unittest import mock

import pytest

import cldk.analysis.java.codeanalyzer.codeanalyzer as _codeanalyzer


# --- a fake Neo4j driver, so JNeo4jBackend can be constructed and probed in-process ---
#: The relationship types codeanalyzer-java 3.0.1 projects (``schema.neo4j.json`` at the release
#: tag) -- a healthy graph by default, so fixtures that do not care about the schema probe need not
#: set ``rel_types`` themselves. ``LOCKS`` is in the contract but is emitted only for a lockfile.
V2_RELATIONSHIP_TYPES = frozenset(
    {
        "J_HAS_MODULE",
        "J_DECLARES",
        "J_HAS_METHOD",
        "J_HAS_FIELD",
        "J_DECLARES_VAR",
        "J_HAS_ENUM_CONSTANT",
        "J_HAS_RECORD_COMPONENT",
        "J_HAS_BODY_NODE",
        "J_RESOLVES_TO",
        "J_CALLS",
        "J_EXTENDS",
        "J_IMPLEMENTS",
        "J_IMPORTS",
        "J_ANNOTATED_BY",
        "J_CFG_NEXT",
        "J_CDG",
        "J_DDG",
        "J_PARAM_IN",
        "J_PARAM_OUT",
        "J_SUMMARY",
        "HAS_ARTIFACT",
        "DEFINES_CONFIG",
        "DECLARES_DEPENDENCY",
        "LOCKS",
        # codeanalyzer-java 3.1.0's code-to-config layer (codeanalyzer-java#233/#237). In the
        # default set because the fake driver stands in for a graph emitted by the pinned analyzer;
        # a test that wants a 3.0.x-shaped graph subtracts them.
        "J_USES_CONFIG",
        "J_READS_CONFIG_UNRESOLVED",
    }
)


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeSession:
    """Answers ``CALL db.relationshipTypes()`` from the driver's ``rel_types`` and the probe's
    ``analyzer_version`` from ``analyzer_version``; every other statement goes to the driver's
    ``responder`` when set, else returns no rows."""

    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def run(self, query: str, **params):
        self._driver.statements.append(query)
        if "db.relationshipTypes" in query:
            return [_FakeRecord({"relationshipType": rt}) for rt in self._driver.rel_types]
        if "RETURN count(a) AS n, a.analyzer_version AS v" in query:
            v = self._driver.analyzer_version
            named = self._driver.application_name
            if v is None or (named is not None and params.get("app") != named):
                return [_FakeRecord({"n": 0, "v": None})]
            return [_FakeRecord({"n": 1, "v": v})]
        if self._driver.responder is not None:
            return [_FakeRecord(d) for d in self._driver.responder(query, params)]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stands in for ``neo4j.GraphDatabase.driver``. ``analyzer_version=None`` means "no
    ``:JApplication`` at all"; ``application_name``, when set, is the *only* name the graph holds --
    a probe bound to any other ``$app`` gets "no such application", which is what makes the
    ``:JApplication {name}`` anchor testable by behaviour rather than by grepping the statement."""

    def __init__(self, rel_types=V2_RELATIONSHIP_TYPES, responder=None, analyzer_version="3.0.1", application_name=None) -> None:
        self.rel_types = set(rel_types)
        self.analyzer_version = analyzer_version
        self.application_name = application_name
        self.responder = responder
        self.statements: list = []

    def session(self, database=None) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_driver() -> FakeDriver:
    return FakeDriver()


def pytest_configure(config):
    config.addinivalue_line("markers", "timed: the test asserts on a wall clock; coverage is paused around its call")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Pause the coverage tracer around a ``timed`` test's call -- when there is one to pause.

    The same hook ``tests/analysis/python/conftest.py`` and its TypeScript twin carry, and for the
    same measured reason: leg 1.5 saw about five seconds of tracer overhead on one large accessor,
    so a wall-clock assertion run under instrumentation measures the tracer, not the query.
    pytest-cov's own ``no_cover`` marker does the same, but its hook (through 7.1.0) dereferences
    ``cov_controller`` unguarded, and under ``--no-cov`` that is ``None``. This checks for the
    plugin *and* a live controller.
    """
    cov = item.config.pluginmanager.get_plugin("_cov")
    controller = getattr(cov, "cov_controller", None)
    if item.get_closest_marker("timed") and controller is not None:
        controller.pause()
        try:
            yield
        finally:
            controller.resume()
    else:
        yield
