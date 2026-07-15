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

Four things are exercised here without ever opening a Bolt connection:

* the fail-fast ``schema_version`` gate (``_check_schema_version``) the backend runs on first use;
* the application-id resolution guard (``_resolve_application_id``) — the suffix match must bind
  exactly one ``:Application`` or raise, never silently merge two apps' module scopes;
* the accessors whose vocabulary is *not projected* into graph schema 2.0.0 (decorators,
  attributes/fields, module imports/exports, variables) — these must raise a clear
  ``NotImplementedError`` rather than silently returning wrong data;
* the constructed Cypher for the two riskiest 2.0.0 rewrites (the call-site body-node path and
  module `_module` scoping) — behavioral verification against a live graph is Task 9's e2e run.

All are tested against a bare ``__new__`` instance (no ``__init__``, so no driver), because the
logic under test never needs a real session.
"""

import pytest

from cldk.analysis.typescript.neo4j import TSNeo4jBackend
from cldk.utils.exceptions.exceptions import CldkSchemaMismatchException, CodeanalyzerUsageException


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


# -----[ application-id resolution guard ]-----
def test_resolve_application_id_unique_match(monkeypatch):
    backend = _bare_backend()
    backend.application_name = "frontend"
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [{"id": "can://repo-a/frontend"}])
    assert backend._resolve_application_id() == "can://repo-a/frontend"


def test_resolve_application_id_no_match_raises(monkeypatch):
    backend = _bare_backend()
    backend.application_name = "frontend"
    monkeypatch.setattr(backend, "_run", lambda *a, **k: [])
    with pytest.raises(CodeanalyzerUsageException, match="no :Application found"):
        backend._resolve_application_id()


def test_resolve_application_id_ambiguous_raises_naming_candidates(monkeypatch):
    backend = _bare_backend()
    backend.application_name = "frontend"
    monkeypatch.setattr(
        backend,
        "_run",
        lambda *a, **k: [{"id": "can://repo-a/frontend"}, {"id": "can://repo-b/frontend"}],
    )
    with pytest.raises(CodeanalyzerUsageException) as exc_info:
        backend._resolve_application_id()
    message = str(exc_info.value)
    assert "ambiguous" in message
    assert "can://repo-a/frontend" in message
    assert "can://repo-b/frontend" in message


# -----[ constructed-Cypher shape for the riskiest 2.0.0 rewrites ]-----
def _recording_backend(monkeypatch):
    """A bare backend whose ``_run`` records every (query, params) call and returns no rows."""
    backend = _bare_backend()
    backend._modules = ["src/mod.ts"]
    calls = []

    def _run(query, **params):
        calls.append((query, params))
        return []

    monkeypatch.setattr(backend, "_run", _run)
    return backend, calls


def test_call_site_query_uses_body_node_path(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    backend.get_call_sites("src/mod.Foo.bar")
    (query, params), = calls
    assert "-[:TS_HAS_BODY_NODE]->" in query
    assert "TSBodyNode {kind: 'call'}" in query
    assert params == {"sig": "src/mod.Foo.bar"}


def test_calling_lines_query_reads_callee_property(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    backend.get_calling_lines("src/mod.Foo.bar")
    (query, _), = calls
    assert "TSBodyNode {kind: 'call'}" in query
    assert "cs.callee = $sig" in query


def test_call_targets_query_reads_callee_property(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    backend.get_call_targets("src/mod.Foo.bar")
    (query, _), = calls
    assert "-[:TS_HAS_BODY_NODE]->" in query
    assert "cs.callee AS" in query


def test_external_symbols_query_resolves_via_body_nodes(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    backend.get_external_symbols()
    (query, _), = calls
    assert "-[:TS_CALLS]->(e:TSExternal)" in query
    assert "(cs:TSBodyNode {kind: 'call'})-[:TS_RESOLVES_TO]->(e:TSExternal)" in query


def test_module_lookup_query_keys_on_module_property(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    assert backend.get_typescript_module("src/mod.ts") is None  # no rows stubbed
    (query, params), = calls
    assert "TSModule {_module: $key}" in query
    assert params == {"key": "src/mod.ts"}


def test_module_keys_query_scoped_to_resolved_application_id(monkeypatch):
    backend, calls = _recording_backend(monkeypatch)
    backend._app_id = "can://repo-a/frontend"
    backend._load_module_keys()
    (query, params), = calls
    assert "Application {id: $app_id}" in query
    assert "-[:TS_HAS_MODULE]->" in query
    assert params == {"app_id": "can://repo-a/frontend"}


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
