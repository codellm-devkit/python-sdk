import os
from unittest import mock

import pytest

import cldk.analysis.java.codeanalyzer.codeanalyzer as _codeanalyzer


@pytest.fixture(autouse=True)
def _no_jdk_for_mocked_analyzer(monkeypatch, tmp_path):
    """Tests that patch ``subprocess.run`` never launch a JVM, so do not resolve one for them.

    ``_get_codeanalyzer_exec`` calls ``ensure_jdk`` before every analyzer run; without a
    ``$JAVA_HOME`` that carries ``jmods`` that is a Temurin download into a fresh cache dir
    (#328). Tests that run the real jar (``subprocess.run`` unpatched) still get the real lookup.
    ``JAVA_HOME`` is restored afterwards because ``_get_codeanalyzer_exec`` exports it.
    """
    real_ensure_jdk = _codeanalyzer.ensure_jdk

    def ensure_jdk(java_cache_dir):
        if isinstance(_codeanalyzer.subprocess.run, mock.Mock):
            return tmp_path / "jdk"
        return real_ensure_jdk(java_cache_dir)

    monkeypatch.setattr(_codeanalyzer, "ensure_jdk", ensure_jdk)
    if "JAVA_HOME" in os.environ:
        monkeypatch.setenv("JAVA_HOME", os.environ["JAVA_HOME"])
    else:
        monkeypatch.delenv("JAVA_HOME", raising=False)


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
            return [_FakeRecord({"n": 0, "v": None} if v is None else {"n": 1, "v": v})]
        if self._driver.responder is not None:
            return [_FakeRecord(d) for d in self._driver.responder(query, params)]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stands in for ``neo4j.GraphDatabase.driver``. ``analyzer_version=None`` means "no
    ``:JApplication`` with that name"."""

    def __init__(self, rel_types=V2_RELATIONSHIP_TYPES, responder=None, analyzer_version="3.0.1") -> None:
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
