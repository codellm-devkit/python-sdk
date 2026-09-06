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

"""Task 8: the repository-artifact layer (``get_artifacts`` / ``get_dependencies`` /
``get_config_keys`` / ``get_config_uses``), plus the review-round additions (2026-09-04):
``get_config_readers`` (resolve ``get_config_uses``'s opaque ids to callables) and
``get_unresolved_config_reads`` (the failure case ``get_config_uses`` can't show).

Same discipline as the ``locate``/``get_source`` fixtures in ``conftest.py``: ONE fixture project
description, rendered twice -- once as a real in-memory ``PyApplication`` for
:class:`PyCodeanalyzer`, once as canned Cypher rows for a fake-driver :class:`PyNeo4jBackend` --
so a parity test comparing the two backends is actually comparing answers about the same project.

Fixture project:
    * ``pyproject.toml`` -- a dependency-manifest artifact declaring ``flask``.
    * ``.env`` -- a config-bearing artifact defining one key, ``DB_URL``.
    * ``src/db.py`` -- one function, ``connect``, that reads ``DB_URL`` (``prov=["literal"]``);
      this is the callable ``get_config_readers`` must resolve the resolved edge back to.

This module defines its own ``py`` fixture (parametrized over both backends), which deliberately
shadows ``conftest.py``'s ``py`` (the Neo4j-only ``locate`` fixture) for tests in this file only --
plain pytest fixture scoping, not a conflict.
"""

from __future__ import annotations

from typing import Any

import pytest

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.models.python import (
    PyApplication,
    PyArtifact,
    PyCallable,
    PyCallableOverview,
    PyConfigKey,
    PyConfigRead,
    PyConfigUseEdge,
    PyDependency,
    PyModule,
)

# -----[ shared identifiers -- both renderings below are built from these, not re-typed ]-----
_APP = "app"
_ART1_ID = f"can://artifact/{_APP}/pyproject.toml"
_ART1_PATH = "pyproject.toml"
_ART1_FORMAT = "toml"
_ART1_ROLES = ["dependency-manifest"]
_ART1_SIZE = 128
_ART1_SHA = "aaa111"
_ART1_SOURCE = '[project]\nname = "app"\ndependencies = ["flask>=2.0,<3"]\n'

_ART2_ID = f"can://artifact/{_APP}/.env"
_ART2_PATH = ".env"
_ART2_FORMAT = "properties"
_ART2_ROLES = ["config"]
_ART2_SIZE = 20
_ART2_SHA = "bbb222"
_ART2_SOURCE = "DB_URL=postgres://x\n"

_CONFIG_KEY_ID = f"{_ART2_ID}@key/DB_URL"
_CONFIG_KEY_KEY = "DB_URL"
_CONFIG_KEY_NAMESPACE = "env"
_CONFIG_KEY_VALUE = "postgres://x"

_DEP_NAME = "flask"
_DEP_ECOSYSTEM = "pypi"
_DEP_SPEC = ">=2.0,<3"
_DEP_KIND = "runtime"
_DEP_PROV = ["declared"]

_MODULE_PATH = "src/db.py"
_CALLABLE_ID = f"can://function/{_APP}/src/db.py#connect"
_BODY_KEY = "5:11"
_USE_SRC = f"{_CALLABLE_ID}@{_BODY_KEY}"
_USE_PROV = ["literal"]


# -----[ the local backend's view: a real in-memory PyApplication ]-----
def _artifacts_application() -> PyApplication:
    config_key = PyConfigKey(
        id=_CONFIG_KEY_ID,
        key=_CONFIG_KEY_KEY,
        namespace=_CONFIG_KEY_NAMESPACE,
        value=_CONFIG_KEY_VALUE,
    )
    art1 = PyArtifact(
        id=_ART1_ID,
        path=_ART1_PATH,
        format=_ART1_FORMAT,
        roles=list(_ART1_ROLES),
        size_bytes=_ART1_SIZE,
        sha256=_ART1_SHA,
        source=_ART1_SOURCE,
        extraction="full",
    )
    art2 = PyArtifact(
        id=_ART2_ID,
        path=_ART2_PATH,
        format=_ART2_FORMAT,
        roles=list(_ART2_ROLES),
        size_bytes=_ART2_SIZE,
        sha256=_ART2_SHA,
        source=_ART2_SOURCE,
        extraction="full",
        config_keys=[config_key],
    )
    dependency = PyDependency(
        name=_DEP_NAME,
        ecosystem=_DEP_ECOSYSTEM,
        spec=_DEP_SPEC,
        kind=_DEP_KIND,
        declared_in=_ART1_ID,
        direct=True,
        prov=list(_DEP_PROV),
    )
    use_edge = PyConfigUseEdge(src=_USE_SRC, dst=_CONFIG_KEY_ID, prov=list(_USE_PROV))
    unresolved_read = PyConfigRead(
        site=f"{_CALLABLE_ID}@12:4",
        callee=_UNRESOLVED_READ_CALLEE,
        key=_UNRESOLVED_READ_KEY,
        reason=_UNRESOLVED_READ_REASON,
        prov=list(_UNRESOLVED_READ_PROV),
    )
    # ``path`` is deliberately the absolute spelling the analyzer emits, while the symbol table is
    # keyed by _MODULE_PATH: get_config_readers must project the *key*, so both backends agree.
    connect = PyCallable(name="connect", path="/analysis-machine/checkout/" + _MODULE_PATH, signature="src.db.connect", id=_CALLABLE_ID)
    module = PyModule(file_path=_MODULE_PATH, module_name="src.db", functions={"connect": connect})
    return PyApplication(
        symbol_table={_MODULE_PATH: module},
        artifacts={_ART1_PATH: art1, _ART2_PATH: art2},
        dependencies=[dependency],
        config_uses=[use_edge],
        config_reads_unresolved=[unresolved_read],
    )


@pytest.fixture
def py_local() -> PyCodeanalyzer:
    backend = object.__new__(PyCodeanalyzer)
    backend.application = _artifacts_application()
    return backend


# -----[ the Neo4j backend's view: the same project, as canned Cypher rows ]-----
_ART1_PROPS = {
    "id": _ART1_ID,
    "path": _ART1_PATH,
    "format": _ART1_FORMAT,
    "roles": list(_ART1_ROLES),
    "size_bytes": _ART1_SIZE,
    "sha256": _ART1_SHA,
    "source": _ART1_SOURCE,
    "extraction": "full",
}
_ART2_PROPS = {
    "id": _ART2_ID,
    "path": _ART2_PATH,
    "format": _ART2_FORMAT,
    "roles": list(_ART2_ROLES),
    "size_bytes": _ART2_SIZE,
    "sha256": _ART2_SHA,
    "source": _ART2_SOURCE,
    "extraction": "full",
}
# No start_line/end_line: this fixture's key was never span-located (best-effort extraction), so
# both backends must agree the reconstructed PyConfigKey.span is None -- not a fabricated Span.
_CONFIG_KEY_PROPS = {
    "id": _CONFIG_KEY_ID,
    "key": _CONFIG_KEY_KEY,
    "namespace": _CONFIG_KEY_NAMESPACE,
    "value": _CONFIG_KEY_VALUE,
    "references": [],
}
_DEP_REL_PROPS = {"spec": _DEP_SPEC, "kind": _DEP_KIND, "extras": [], "prov": list(_DEP_PROV), "direct": True}
_CONNECT_OVERVIEW_ROW = {
    "signature": "src.db.connect",
    "name": "connect",
    "decorators": [],
    "path": _MODULE_PATH,
    "start_line": 5,
    "end_line": 11,
    "class_signature": None,
}

# -----[ an unresolved config read, for get_unresolved_config_reads ]-----
_UNRESOLVED_READ_CALLEE = f"can://app/{_APP}/@external/os/getenv"
_UNRESOLVED_READ_KEY = None
_UNRESOLVED_READ_REASON = "non-literal"
_UNRESOLVED_READ_PROV = ["dataflow"]
_UNRESOLVED_READ_PROPS = {"key": _UNRESOLVED_READ_KEY, "reason": _UNRESOLVED_READ_REASON, "prov": list(_UNRESOLVED_READ_PROV)}


def _artifacts_responder(query: str, params: dict) -> list[dict[str, Any]]:
    """Answers the four Cypher statements ``PyNeo4jBackend`` sends for this layer, plus
    ``_load_module_keys`` (construction always calls it)."""
    if "RETURN m.file_key AS k" in query:
        return [{"k": _MODULE_PATH}]
    if "collect(properties(ck))" in query:  # get_artifacts
        return [
            {"p": _ART1_PROPS, "cks": []},
            {"p": _ART2_PROPS, "cks": [_CONFIG_KEY_PROPS]},
        ]
    if "DECLARES_DEPENDENCY" in query:  # get_dependencies
        # The fixture's one dependency is direct=True/ecosystem=pypi/declared_in=_ART1_ID, so
        # emulate the WHERE clause for real rather than ignoring params -- otherwise a filter test
        # against this shared fixture would pass over Neo4j for the wrong reason (a responder that
        # always answers regardless of the query it was actually asked).
        if params.get("ecosystem") not in (None, _DEP_ECOSYSTEM):
            return []
        if params.get("declared_in") not in (None, _ART1_ID):
            return []
        return [{"rel": _DEP_REL_PROPS, "name": _DEP_NAME, "ecosystem": _DEP_ECOSYSTEM, "declared_in": _ART1_ID}]
    if "DEFINES_CONFIG" in query:  # get_config_keys (get_artifacts is caught above first)
        return [{"p": _CONFIG_KEY_PROPS}]
    if "PY_HAS_BODY_NODE" in query:  # get_config_readers (checked before PY_USES_CONFIG below --
        # get_config_readers's own query text also contains "PY_USES_CONFIG")
        if params.get("key") != _CONFIG_KEY_KEY:
            return []
        return [_CONNECT_OVERVIEW_ROW]
    if "PY_USES_CONFIG" in query:  # get_config_uses
        if "key" in params and params["key"] != _CONFIG_KEY_KEY:
            return []
        return [{"src": _USE_SRC, "dst": _CONFIG_KEY_ID, "prov": list(_USE_PROV)}]
    if "PY_READS_CONFIG_UNRESOLVED" in query:  # get_unresolved_config_reads
        return [{"p": _UNRESOLVED_READ_PROPS, "callee": _UNRESOLVED_READ_CALLEE}]
    return []


@pytest.fixture
def py_neo4j(fake_driver) -> PyNeo4jBackend:
    fake_driver.responder = _artifacts_responder
    return PyNeo4jBackend._from_driver(fake_driver, application_name=_APP)


@pytest.fixture(params=["neo4j", "local"])
def py(request, py_neo4j, py_local):
    """Both backends -- shadows conftest.py's Neo4j-only ``py`` for this module only."""
    return py_neo4j if request.param == "neo4j" else py_local


# =====================================================================================
# get_artifacts
# =====================================================================================
def test_artifacts_indexed_by_path(py):
    arts = py.get_artifacts()
    assert _ART1_PATH in arts
    assert arts[_ART1_PATH].format == _ART1_FORMAT


def test_artifact_carries_its_flattened_config_keys(py):
    arts = py.get_artifacts()
    keys = arts[_ART2_PATH].config_keys
    assert [k.id for k in keys] == [_CONFIG_KEY_ID]
    assert keys[0].span is None  # no start_line/end_line in this fixture -- see _CONFIG_KEY_PROPS


# =====================================================================================
# get_dependencies
# =====================================================================================
def test_dependencies_record_their_declaring_manifest(py):
    deps = py.get_dependencies()
    assert any(d.name == _DEP_NAME and d.declared_in.endswith(".toml") for d in deps)


def test_dependency_ecosystem_is_read_off_the_package_node(fake_driver):
    """Regression for a hardcoded ``ecosystem="pypi"``: the query must read ``Package.ecosystem``
    off the graph, not fabricate it -- prove it with a non-default value in the canned row."""

    def responder(query: str, params: dict) -> list[dict[str, Any]]:
        if "RETURN m.file_key AS k" in query:
            return [{"k": _MODULE_PATH}]
        if "DECLARES_DEPENDENCY" in query:
            return [{"rel": _DEP_REL_PROPS, "name": _DEP_NAME, "ecosystem": "conda-forge", "declared_in": _ART1_ID}]
        return []

    fake_driver.responder = responder
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name=_APP)
    deps = backend.get_dependencies()
    assert deps[0].ecosystem == "conda-forge"


def test_dependencies_direct_only_filters_out_transitive_pins(fake_driver):
    """direct_only=True excludes lockfile-only transitive pins, on both backends. Self-contained
    (not the shared single-dependency fixture above) so a genuine direct/transitive pair exists to
    filter."""
    transitive = PyDependency(name="click", ecosystem="pypi", declared_in=_ART1_ID, direct=False)
    direct = PyDependency(
        name=_DEP_NAME, ecosystem=_DEP_ECOSYSTEM, spec=_DEP_SPEC, kind=_DEP_KIND, declared_in=_ART1_ID,
        direct=True, prov=list(_DEP_PROV),
    )
    local = object.__new__(PyCodeanalyzer)
    local.application = PyApplication(symbol_table={}, dependencies=[direct, transitive])
    assert {d.name for d in local.get_dependencies()} == {_DEP_NAME, "click"}
    assert [d.name for d in local.get_dependencies(direct_only=True)] == [_DEP_NAME]

    def responder(query: str, params: dict) -> list[dict[str, Any]]:
        if "RETURN m.file_key AS k" in query:
            return [{"k": _MODULE_PATH}]
        if "DECLARES_DEPENDENCY" in query:
            assert "r.direct = true" in query  # the filter must run in Cypher, not in Python
            return [{"rel": _DEP_REL_PROPS, "name": _DEP_NAME, "ecosystem": _DEP_ECOSYSTEM, "declared_in": _ART1_ID}]
        return []

    fake_driver.responder = responder
    neo4j = PyNeo4jBackend._from_driver(fake_driver, application_name=_APP)
    assert [d.name for d in neo4j.get_dependencies(direct_only=True)] == [_DEP_NAME]


def test_dependencies_filters_are_pure_widening(py):
    """The original zero-argument call keeps returning everything -- adding defaulted keywords
    changed nothing for an existing caller."""
    assert py.get_dependencies() == py.get_dependencies(direct_only=False, ecosystem=None, declared_in=None)


def test_dependencies_ecosystem_filter(py):
    assert [d.name for d in py.get_dependencies(ecosystem=_DEP_ECOSYSTEM)] == [_DEP_NAME]
    assert py.get_dependencies(ecosystem="conda-forge") == []


def test_dependencies_declared_in_filter(py):
    assert [d.name for d in py.get_dependencies(declared_in=_ART1_ID)] == [_DEP_NAME]
    assert py.get_dependencies(declared_in="can://artifact/app/nonexistent.toml") == []


# =====================================================================================
# get_config_keys
# =====================================================================================
def test_config_keys_indexed_by_id(py):
    keys = py.get_config_keys()
    assert _CONFIG_KEY_ID in keys
    assert keys[_CONFIG_KEY_ID].key == _CONFIG_KEY_KEY


# =====================================================================================
# get_config_uses
# =====================================================================================
# NB: the brief's own draft test asserted ``u.key`` / ``u.callable`` on the returned edges --
# codeanalyzer-python's PyConfigUseEdge has neither field (only src/dst/prov; see
# cldk/analysis/commons/backend.py's get_config_uses docstring, and codeanalyzer's
# schema/py_schema.py). "Which callable reads this key" is recoverable from ``src`` (the callable's
# can:// id, ordinal-suffixed with "@<body key>") but this layer does not resolve it for the
# caller -- there is no new model to carry it without inventing one the analyzer doesn't emit.
def test_config_uses_filters_by_key(py):
    uses = py.get_config_uses(key=_CONFIG_KEY_KEY)
    assert uses
    assert all(u.dst == _CONFIG_KEY_ID for u in uses)
    assert all(u.src.startswith(_CALLABLE_ID) for u in uses)  # the reading callable, ordinal-suffixed


def test_config_uses_key_filter_excludes_non_matches(py):
    assert py.get_config_uses(key="NO_SUCH_KEY") == []


def test_config_uses_default_returns_every_edge(py):
    assert len(py.get_config_uses()) == 1


# =====================================================================================
# get_config_readers -- resolves get_config_uses's opaque ids to callables
# =====================================================================================
def test_config_readers_resolves_the_reading_callable(py):
    readers = py.get_config_readers(_CONFIG_KEY_KEY)
    assert [r.signature for r in readers] == ["src.db.connect"]
    assert isinstance(readers[0], PyCallableOverview)


def test_config_readers_key_filter_excludes_non_matches(py):
    assert py.get_config_readers("NO_SUCH_KEY") == []


# =====================================================================================
# get_unresolved_config_reads -- the failure case get_config_uses can't show
# =====================================================================================
def test_unresolved_config_reads_surfaces_the_failure_locally(py_local):
    reads = py_local.get_unresolved_config_reads()
    assert len(reads) == 1
    assert reads[0].callee == _UNRESOLVED_READ_CALLEE
    assert reads[0].reason == _UNRESOLVED_READ_REASON
    assert reads[0].key is None  # reason="non-literal" -- never closed on a literal at all
    assert reads[0].site  # the local backend has the real call site


def test_unresolved_config_reads_over_neo4j_carries_key_and_reason_but_not_site(fake_driver):
    """The graph does carry PY_READS_CONFIG_UNRESOLVED (unlike finding 1's entrypoint_report) --
    but its edge has no site property (see reconstruct.unresolved_config_read's docstring), so
    that field always comes back "" over this backend rather than a fabricated value."""
    fake_driver.responder = _artifacts_responder
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name=_APP)
    reads = backend.get_unresolved_config_reads()
    assert len(reads) == 1
    assert reads[0].callee == _UNRESOLVED_READ_CALLEE
    assert reads[0].reason == _UNRESOLVED_READ_REASON
    assert reads[0].site == ""


def test_no_unresolved_config_reads_is_an_empty_list_not_none(fake_driver):
    def responder(query: str, params: dict) -> list[dict[str, Any]]:
        if "RETURN m.file_key AS k" in query:
            return [{"k": _MODULE_PATH}]
        return []

    fake_driver.responder = responder
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name=_APP)
    assert backend.get_unresolved_config_reads() == []

    local = object.__new__(PyCodeanalyzer)
    local.application = PyApplication(symbol_table={})
    assert local.get_unresolved_config_reads() == []


# =====================================================================================
# parity between backends
# =====================================================================================
def test_artifacts_layer_parity_between_backends(py_local, py_neo4j):
    assert py_local.get_artifacts() == py_neo4j.get_artifacts()
    assert py_local.get_dependencies() == py_neo4j.get_dependencies()
    assert py_local.get_config_keys() == py_neo4j.get_config_keys()
    assert py_local.get_config_uses() == py_neo4j.get_config_uses()
    assert py_local.get_config_uses(key=_CONFIG_KEY_KEY) == py_neo4j.get_config_uses(key=_CONFIG_KEY_KEY)
    # Path included: this projection also returns PyCallableOverview, so it has to speak the same
    # repo-relative path vocabulary as every other overview accessor -- on both backends.
    assert [(r.signature, r.path) for r in py_local.get_config_readers(_CONFIG_KEY_KEY)] == [
        (r.signature, r.path) for r in py_neo4j.get_config_readers(_CONFIG_KEY_KEY)
    ] == [("src.db.connect", _MODULE_PATH)]
    # Deliberately not full equality: the Neo4j reconstruction always has site="" (see
    # reconstruct.unresolved_config_read's docstring) while the local backend has a real one --
    # documented projection loss, not a bug. key/reason/callee/prov still agree exactly.
    local_reads, neo4j_reads = py_local.get_unresolved_config_reads(), py_neo4j.get_unresolved_config_reads()
    assert len(local_reads) == len(neo4j_reads) == 1
    assert (local_reads[0].key, local_reads[0].reason, local_reads[0].callee, local_reads[0].prov) == (
        neo4j_reads[0].key,
        neo4j_reads[0].reason,
        neo4j_reads[0].callee,
        neo4j_reads[0].prov,
    )
