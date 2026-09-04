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
``get_config_keys`` / ``get_config_uses``).

Same discipline as the ``locate``/``get_source`` fixtures in ``conftest.py``: ONE fixture project
description, rendered twice -- once as a real in-memory ``PyApplication`` for
:class:`PyCodeanalyzer`, once as canned Cypher rows for a fake-driver :class:`PyNeo4jBackend` --
so a parity test comparing the two backends is actually comparing answers about the same project.

Fixture project:
    * ``pyproject.toml`` -- a dependency-manifest artifact declaring ``flask``.
    * ``.env`` -- a config-bearing artifact defining one key, ``DB_URL``.
    * one resolved config-use edge: a body node in ``src/db.py``'s ``connect`` function reads
      ``DB_URL`` (``prov=["literal"]``).

This module defines its own ``py`` fixture (parametrized over both backends), which deliberately
shadows ``conftest.py``'s ``py`` (the Neo4j-only ``locate`` fixture) for tests in this file only --
plain pytest fixture scoping, not a conflict.
"""

from __future__ import annotations

from typing import Any

import pytest

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.models.python import PyApplication, PyArtifact, PyConfigKey, PyConfigUseEdge, PyDependency

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
    return PyApplication(
        symbol_table={},
        artifacts={_ART1_PATH: art1, _ART2_PATH: art2},
        dependencies=[dependency],
        config_uses=[use_edge],
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
        return [{"rel": _DEP_REL_PROPS, "name": _DEP_NAME, "ecosystem": _DEP_ECOSYSTEM, "declared_in": _ART1_ID}]
    if "DEFINES_CONFIG" in query:  # get_config_keys (get_artifacts is caught above first)
        return [{"p": _CONFIG_KEY_PROPS}]
    if "PY_USES_CONFIG" in query:  # get_config_uses
        if "key" in params and params["key"] != _CONFIG_KEY_KEY:
            return []
        return [{"src": _USE_SRC, "dst": _CONFIG_KEY_ID, "prov": list(_USE_PROV)}]
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
# parity between backends
# =====================================================================================
def test_artifacts_layer_parity_between_backends(py_local, py_neo4j):
    assert py_local.get_artifacts() == py_neo4j.get_artifacts()
    assert py_local.get_dependencies() == py_neo4j.get_dependencies()
    assert py_local.get_config_keys() == py_neo4j.get_config_keys()
    assert py_local.get_config_uses() == py_neo4j.get_config_uses()
    assert py_local.get_config_uses(key=_CONFIG_KEY_KEY) == py_neo4j.get_config_uses(key=_CONFIG_KEY_KEY)
