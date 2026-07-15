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

"""Unit tests for the TS Neo4j backend's graph-schema contract (no live Neo4j required).

Two things are exercised here without ever opening a Bolt connection:

* the fail-fast ``schema_version`` gate (``_check_schema_version``) the backend runs on first use;
* the accessors whose vocabulary is *not projected* into graph schema 2.0.0 (decorators,
  attributes/fields, module imports/exports, variables) — these must raise a clear
  ``NotImplementedError`` rather than silently returning wrong data.

Both are tested against a bare ``__new__`` instance (no ``__init__``, so no driver), because the
logic under test never needs a real session.
"""

import pytest

from cldk.analysis.typescript.neo4j import TSNeo4jBackend
from cldk.utils.exceptions.exceptions import CldkSchemaMismatchException


def _bare_backend() -> TSNeo4jBackend:
    """A backend instance with no live driver — enough to exercise pure query/guard logic."""
    return TSNeo4jBackend.__new__(TSNeo4jBackend)


# -----[ schema-version gate ]-----
def test_schema_version_mismatch_fails_fast():
    backend = _bare_backend()
    with pytest.raises(CldkSchemaMismatchException):
        backend._check_schema_version(expected="2.0.0", found="1.0.0")


def test_schema_version_match_passes():
    backend = _bare_backend()
    # An exact match is a no-op (returns None, raises nothing).
    assert backend._check_schema_version(expected="2.0.0", found="2.0.0") is None


def test_schema_version_queried_from_application_when_not_supplied(monkeypatch):
    backend = _bare_backend()
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [{"v": "2.0.0"}])
    # Reads (:Application).schema_version and finds the supported version ⇒ passes.
    assert backend._check_schema_version(expected="2.0.0") is None

    monkeypatch.setattr(backend, "_run", lambda *a, **k: [{"v": "1.0.0"}])
    with pytest.raises(CldkSchemaMismatchException):
        backend._check_schema_version(expected="2.0.0")


def test_schema_version_absent_fails_fast(monkeypatch):
    backend = _bare_backend()
    # No Application row at all (empty/foreign DB) ⇒ found is None ⇒ mismatch.
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [])
    with pytest.raises(CldkSchemaMismatchException):
        backend._check_schema_version(expected="2.0.0")


# -----[ accessors with no graph support in schema 2.0.0 ]-----
_FALLBACK_CALLS = [
    ("get_decorators", ("src/x.f",)),
    ("get_class_decorators", ("src/x.C",)),
    ("get_methods_with_decorators", (["Get"],)),
    ("get_classes_with_decorators", (["Controller"],)),
    ("get_all_fields", ("src/x.C",)),
    ("get_interface_properties", ("src/x.I",)),
    ("get_imports", ()),
    ("get_all_exports", ()),
    ("get_all_variables", ()),
]


@pytest.mark.parametrize("method_name,args", _FALLBACK_CALLS)
def test_unprojected_accessor_raises_not_implemented(method_name, args):
    backend = _bare_backend()
    with pytest.raises(NotImplementedError, match="graph schema 2.0.0"):
        getattr(backend, method_name)(*args)
