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

"""Stub tests for the four bulk/projected accessors (#298) on :class:`TSNeo4jBackend`:
``get_callables_overview`` / ``get_method_bodies`` / ``get_decorated_callables`` /
``get_callsites_for``.

``_run`` is the single seam every query method goes through, so it is stubbed here with canned
rows keyed on a distinguishing fragment of the query text -- no live Neo4j needed (mirrors the
house pattern in ``test_typescript_get_method_functions.py`` / ``test_typescript_external_reconstruct.py``).
Semantics must match Task 2's in-memory impls (``TSCodeanalyzer._iter_callables`` et al.) exactly:
owner pair derived from the owner node's labels (Class/Interface), namespace/nested callables get
no owner leg match at all (None/None falls out naturally), null-code bodies are omitted, and every
requested-and-existing signature gets a callsites entry (empty list if it has none).
"""

from unittest.mock import patch

from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.models.typescript import TSCallableOverview, TSCallsite


def _backend(modules=("app.ts",)) -> TSNeo4jBackend:
    """A TSNeo4jBackend with __init__ (and its real driver connection) bypassed."""
    backend = object.__new__(TSNeo4jBackend)
    backend.application_name = "test-app"
    backend._database = None
    backend._modules = list(modules)
    return backend


def _run_keyed(rows_by_fragment: dict):
    """A stub ``_run`` returning the canned rows for the first query fragment found in the text."""

    def _run(query: str, **params):
        for fragment, result in rows_by_fragment.items():
            if fragment in query:
                return result
        raise AssertionError(f"no canned rows for query: {query!r} (params={params})")

    return _run


# -----[ get_callables_overview ]-----


def test_overview_builds_row_with_class_owner_from_labels():
    row = {
        "signature": "src/models.User.recordLogin",
        "name": "recordLogin",
        "kind": "method",
        "path": "src/models.ts",
        "start_line": 52,
        "end_line": 55,
        "is_exported": False,
        "is_async": True,
        "is_static": False,
        "accessibility": None,
        "owner_signature": "src/models.User",
        "owner_labels": ["Symbol", "Class"],
        "decorators": [],
    }
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"MATCH (c:Callable) WHERE c._module IN $mods ": [row]})):
        overview = backend.get_callables_overview()
    assert len(overview) == 1
    assert isinstance(overview[0], TSCallableOverview)
    o = overview[0]
    assert o.signature == "src/models.User.recordLogin"
    assert o.owner_signature == "src/models.User"
    assert o.owner_kind == "class"
    assert o.kind == "method"
    assert o.is_async is True
    assert o.is_exported is False


def test_overview_builds_row_with_interface_owner_from_labels():
    row = {
        "signature": "src/models.Named.describe",
        "name": "describe",
        "kind": "method",
        "path": "src/models.ts",
        "start_line": 1,
        "end_line": 1,
        "is_exported": False,
        "is_async": False,
        "is_static": False,
        "accessibility": None,
        "owner_signature": "src/models.Named",
        "owner_labels": ["Symbol", "Interface"],
        "decorators": [],
    }
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"MATCH (c:Callable) WHERE c._module IN $mods ": [row]})):
        overview = backend.get_callables_overview()
    assert overview[0].owner_kind == "interface"
    assert overview[0].owner_signature == "src/models.Named"


def test_overview_namespace_or_module_owned_function_has_no_owner_leg_match():
    """RULING: namespace-owned (and module-level / nested) functions never match the HAS_METHOD
    owner leg at all -- None/None falls straight out of the row; there is no separate namespace
    owner leg to add."""
    row = {
        "signature": "src/util.StringUtil.slug",
        "name": "slug",
        "kind": "function",
        "path": "src/util.ts",
        "start_line": 10,
        "end_line": 12,
        "is_exported": False,
        "is_async": False,
        "is_static": False,
        "accessibility": None,
        "owner_signature": None,
        "owner_labels": None,
        "decorators": [],
    }
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"MATCH (c:Callable) WHERE c._module IN $mods ": [row]})):
        overview = backend.get_callables_overview()
    assert overview[0].owner_signature is None
    assert overview[0].owner_kind is None


def test_overview_collects_decorator_names():
    row = {
        "signature": "src/controllers.UserController.show",
        "name": "show",
        "kind": "method",
        "path": "src/controllers.ts",
        "start_line": 1,
        "end_line": 1,
        "is_exported": False,
        "is_async": False,
        "is_static": False,
        "accessibility": None,
        "owner_signature": "src/controllers.UserController",
        "owner_labels": ["Symbol", "Class"],
        "decorators": ["Get"],
    }
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"MATCH (c:Callable) WHERE c._module IN $mods ": [row]})):
        overview = backend.get_callables_overview()
    assert overview[0].decorators == ["Get"]


def test_overview_scopes_query_to_this_backends_modules():
    captured = {}

    def _run(query, **params):
        captured["mods"] = params.get("mods")
        return []

    backend = _backend(modules=["a.ts", "b.ts"])
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callables_overview() == []
    assert captured["mods"] == ["a.ts", "b.ts"]


def test_overview_query_shape_has_owner_leg_and_module_scoping():
    """Pins the Cypher shape itself: a `labels(c)`-for-`labels(o)` typo, or a silently dropped
    HAS_METHOD/DECORATED_BY OPTIONAL MATCH leg, would still pass the row-construction tests above
    (they hand `owner_labels` straight to the reconstructor) -- only asserting on the actual query
    text catches that class of bug."""
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callables_overview() == []
    assert "c._module IN $mods" in captured["query"]
    assert "HAS_METHOD" in captured["query"]
    assert "labels(o)" in captured["query"]
    assert "DECORATED_BY" in captured["query"]


# -----[ get_method_bodies ]-----


def test_method_bodies_keyed_by_signature_unknowns_omitted():
    rows = [
        {"signature": "src/services.UserService.create", "code": "create() { ... }"},
        {"signature": "src/models.Named.describe", "code": "describe(): string;"},
    ]
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"c.code IS NOT NULL": rows})):
        bodies = backend.get_method_bodies(
            [
                "src/services.UserService.create",
                "src/models.Named.describe",
                "src/does/not.exist",
            ]
        )
    assert bodies == {
        "src/services.UserService.create": "create() { ... }",
        "src/models.Named.describe": "describe(): string;",
    }


def test_method_bodies_empty_for_no_matches():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"c.code IS NOT NULL": []})):
        assert backend.get_method_bodies(["nope"]) == {}


def test_method_bodies_query_filters_null_code_and_scopes_sigs():
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured["sigs"] = params.get("sigs")
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        backend.get_method_bodies(["sig-a", "sig-b"])
    assert "c.code IS NOT NULL" in captured["query"]
    assert "c.signature IN $sigs" in captured["query"]
    assert captured["sigs"] == ["sig-a", "sig-b"]


# -----[ get_decorated_callables ]-----


def test_decorated_callables_matches_marker_and_returns_overview():
    row = {
        "signature": "src/controllers.UserController.show",
        "name": "show",
        "kind": "method",
        "path": "src/controllers.ts",
        "start_line": 1,
        "end_line": 1,
        "is_exported": False,
        "is_async": False,
        "is_static": False,
        "accessibility": None,
        "owner_signature": "src/controllers.UserController",
        "owner_labels": ["Symbol", "Class"],
        "decorators": ["Get"],
    }
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"DECORATED_BY]->(marker:Decorator)": [row]})):
        decorated = backend.get_decorated_callables(["Get"])
    assert len(decorated) == 1
    assert isinstance(decorated[0], TSCallableOverview)
    assert decorated[0].signature == "src/controllers.UserController.show"
    assert decorated[0].owner_kind == "class"
    assert decorated[0].decorators == ["Get"]


def test_decorated_callables_no_match_is_empty():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"DECORATED_BY]->(marker:Decorator)": []})):
        assert backend.get_decorated_callables(["NoSuchDecorator"]) == []


def test_decorated_callables_passes_markers_param():
    captured = {}

    def _run(query, **params):
        captured["markers"] = params.get("markers")
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        backend.get_decorated_callables(["Get", "Post"])
    assert captured["markers"] == ["Get", "Post"]


def test_decorated_callables_query_shape_has_marker_leg_owner_leg_and_module_scoping():
    """Same pinning concern as the overview query-shape test above: the marker-match leg
    (`DECORATED_BY]->(marker:Decorator)` + `marker.name IN $markers`) and the reused overview
    projection (owner leg via `labels(o)`, the separate `d:Decorator` collection leg) must both
    actually be in the Cypher, not just implied by the canned rows."""
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_decorated_callables(["Get"]) == []
    assert "c._module IN $mods" in captured["query"]
    assert "DECORATED_BY]->(marker:Decorator)" in captured["query"]
    assert "marker.name IN $markers" in captured["query"]
    assert "HAS_METHOD" in captured["query"]
    assert "labels(o)" in captured["query"]
    assert "DECORATED_BY]->(d:Decorator)" in captured["query"]


# -----[ get_callsites_for ]-----


def test_callsites_for_groups_by_owner_and_keeps_empty_entry():
    rows = [
        {
            "owner": "src/services.UserService.create",
            "p": {"method_name": "nextId", "callee_signature": "src/services.nextId", "start_line": 1, "start_column": 1},
        },
        {
            "owner": "src/services.UserService.create",
            "p": {"method_name": "push", "callee_signature": None, "start_line": 2, "start_column": 1},
        },
        {"owner": "src/models.Entity.constructor", "p": None},
    ]
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"HAS_CALLSITE": rows})):
        result = backend.get_callsites_for(
            [
                "src/services.UserService.create",
                "src/models.Entity.constructor",
                "src/does/not.exist",
            ]
        )
    assert set(result) == {"src/services.UserService.create", "src/models.Entity.constructor"}
    # existing-but-callsite-less callable gets an empty list, not omitted
    assert result["src/models.Entity.constructor"] == []
    assert all(isinstance(cs, TSCallsite) for cs in result["src/services.UserService.create"])
    create_targets = {cs.callee_signature or cs.method_name for cs in result["src/services.UserService.create"]}
    assert create_targets == {"src/services.nextId", "push"}


def test_callsites_for_scopes_sigs_and_mods():
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured["sigs"] = params.get("sigs")
        captured["mods"] = params.get("mods")
        return []

    backend = _backend(modules=["a.ts"])
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callsites_for(["sig-a"]) == {}
    assert "HAS_CALLSITE" in captured["query"]
    assert captured["sigs"] == ["sig-a"]
    assert captured["mods"] == ["a.ts"]
