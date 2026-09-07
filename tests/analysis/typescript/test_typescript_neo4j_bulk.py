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
``get_callsites_for``, on the codeanalyzer-typescript 1.2.0 vocabulary.

``_run`` is the single seam every query method goes through, so it is stubbed here with canned
rows keyed on a distinguishing fragment of the query text -- no live Neo4j needed. Semantics must
match the in-memory impls (``TSCodeanalyzer._iter_callables`` et al.): the owner pair is the
``TS_HAS_METHOD`` owner's own ``kind`` (class/interface), namespace/nested callables get no owner
leg match at all (None/None falls out naturally), empty/absent-code bodies are omitted, and every
requested-and-existing signature gets a callsites entry (empty list if it has none). ``path`` is
derived from the callable's ``can://`` id against the application's module keys (F4), never
projected.
"""

from unittest.mock import patch

from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend, _scoped
from cldk.models.typescript import TSCallableOverview, TSCallsite

APP = "test-app"
PREFIX = f"can://typescript/{APP}/"
SCOPE = {"p1": PREFIX, "p2": f"can://javascript/{APP}/"}
OVERVIEW_MATCH = f"MATCH (c:TSCallable) WHERE {_scoped('c')} "


def _backend(modules=("src/models.ts", "src/util.ts", "src/controllers.ts")) -> TSNeo4jBackend:
    """A TSNeo4jBackend with __init__ (and its real driver connection) bypassed."""
    backend = object.__new__(TSNeo4jBackend)
    backend.application_name = APP
    backend._database = None
    backend._module_ids = {m: f"{PREFIX}{m}" for m in modules}
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


def _row(**over):
    row = {
        "id": f"{PREFIX}src/models.ts/User/recordLogin",
        "signature": "src/models.User.recordLogin",
        "name": "recordLogin",
        "kind": "method",
        "start_line": 52,
        "end_line": 55,
        "is_exported": False,
        "is_async": True,
        "is_static": False,
        "accessibility": None,
        "owner_signature": "src/models.User",
        "owner_kind": "class",
        "decorators": [],
    }
    row.update(over)
    return row


# -----[ get_callables_overview ]-----


def test_overview_builds_row_with_class_owner_and_derived_path():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({OVERVIEW_MATCH: [_row()]})):
        overview = backend.get_callables_overview()
    assert len(overview) == 1 and isinstance(overview[0], TSCallableOverview)
    o = overview[0]
    assert o.signature == "src/models.User.recordLogin"
    assert o.owner_signature == "src/models.User"
    assert o.owner_kind == "class"
    assert o.kind == "method"
    assert o.path == "src/models.ts"
    assert o.is_async is True
    assert o.is_exported is False


def test_overview_builds_row_with_interface_owner():
    backend = _backend()
    row = _row(signature="src/models.Named.describe", name="describe", owner_signature="src/models.Named", owner_kind="interface", id=f"{PREFIX}src/models.ts/Named/describe")
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({OVERVIEW_MATCH: [row]})):
        overview = backend.get_callables_overview()
    assert overview[0].owner_kind == "interface"
    assert overview[0].owner_signature == "src/models.Named"


def test_overview_namespace_or_module_owned_function_has_no_owner_leg_match():
    """RULING: namespace-owned (and module-level / nested) functions never match the TS_HAS_METHOD
    owner leg at all -- None/None falls straight out of the row."""
    backend = _backend()
    row = _row(signature="src/util.StringUtil.slug", name="slug", kind="function", owner_signature=None, owner_kind=None, id=f"{PREFIX}src/util.ts/StringUtil/slug")
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({OVERVIEW_MATCH: [row]})):
        overview = backend.get_callables_overview()
    assert overview[0].owner_signature is None
    assert overview[0].owner_kind is None
    assert overview[0].path == "src/util.ts"


def test_overview_collects_decorator_names():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({OVERVIEW_MATCH: [_row(decorators=["Get"])]})):
        overview = backend.get_callables_overview()
    assert overview[0].decorators == ["Get"]


def test_overview_scopes_query_to_this_applications_two_prefixes():
    captured = {}

    def _run(query, **params):
        captured.update(params)
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callables_overview() == []
    assert captured == SCOPE


def test_overview_query_shape_has_owner_leg_and_prefix_scoping():
    """Pins the Cypher shape itself: a dropped TS_HAS_METHOD/TS_DECORATED_BY OPTIONAL MATCH leg
    would still pass the row-construction tests above."""
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callables_overview() == []
    assert _scoped("c") in captured["query"]
    assert "TS_HAS_METHOD" in captured["query"]
    assert "o.kind AS owner_kind" in captured["query"]
    assert "TS_DECORATED_BY" in captured["query"]
    assert "c.id AS id" in captured["query"]


# -----[ get_method_bodies ]-----


def test_method_bodies_keyed_by_signature_unknowns_omitted():
    rows = [
        {"signature": "src/services.UserService.create", "code": "create() { ... }"},
        {"signature": "src/models.Named.describe", "code": "describe(): string;"},
    ]
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"c.code IS NOT NULL": rows})):
        bodies = backend.get_method_bodies(["src/services.UserService.create", "src/models.Named.describe", "src/does/not.exist"])
    assert bodies == {"src/services.UserService.create": "create() { ... }", "src/models.Named.describe": "describe(): string;"}


def test_method_bodies_empty_for_no_matches():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"c.code IS NOT NULL": []})):
        assert backend.get_method_bodies(["nope"]) == {}


def test_method_bodies_query_filters_empty_code_and_scopes_sigs():
    """The ``if c.code`` rule: an implicit constructor's ``code`` is ``""`` and is omitted, as
    in-memory."""
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured["sigs"] = params.get("sigs")
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        backend.get_method_bodies(["sig-a", "sig-b"])
    assert "c.code IS NOT NULL AND c.code <> ''" in captured["query"]
    assert "c.signature IN $sigs" in captured["query"]
    assert _scoped("c") in captured["query"]
    assert captured["sigs"] == ["sig-a", "sig-b"]


# -----[ get_decorated_callables ]-----


def test_decorated_callables_matches_marker_and_returns_overview():
    backend = _backend()
    row = _row(
        signature="src/controllers.UserController.show",
        name="show",
        owner_signature="src/controllers.UserController",
        decorators=["Get"],
        id=f"{PREFIX}src/controllers.ts/UserController/show",
    )
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"TS_DECORATED_BY]->(marker:TSDecorator)": [row]})):
        decorated = backend.get_decorated_callables(["Get"])
    assert len(decorated) == 1 and isinstance(decorated[0], TSCallableOverview)
    assert decorated[0].signature == "src/controllers.UserController.show"
    assert decorated[0].owner_kind == "class"
    assert decorated[0].decorators == ["Get"]
    assert decorated[0].path == "src/controllers.ts"


def test_decorated_callables_no_match_is_empty():
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"TS_DECORATED_BY]->(marker:TSDecorator)": []})):
        assert backend.get_decorated_callables(["NoSuchDecorator"]) == []


def test_decorated_callables_query_shape():
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured["markers"] = params.get("markers")
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_decorated_callables(["Get", "Post"]) == []
    assert captured["markers"] == ["Get", "Post"]
    assert _scoped("c") in captured["query"]
    assert "TS_DECORATED_BY]->(marker:TSDecorator)" in captured["query"]
    assert "marker.name IN $markers" in captured["query"]
    assert "TS_HAS_METHOD" in captured["query"]
    assert "TS_DECORATED_BY]->(d:TSDecorator)" in captured["query"]


# -----[ get_callsites_for ]-----


def test_callsites_for_groups_by_owner_and_keeps_empty_entry():
    rows = [
        {"owner": "src/services.UserService.create", "p": {"id": "x@1:1", "kind": "call", "start_line": 1, "end_line": 1}, "callee": "src/services.nextId"},
        {"owner": "src/services.UserService.create", "p": {"id": "x@2:1", "kind": "call", "start_line": 2, "end_line": 2}, "callee": None},
        {"owner": "src/models.Entity.constructor", "p": None, "callee": None},
    ]
    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run_keyed({"TS_HAS_BODY_NODE": rows})):
        result = backend.get_callsites_for(["src/services.UserService.create", "src/models.Entity.constructor", "src/does/not.exist"])
    assert set(result) == {"src/services.UserService.create", "src/models.Entity.constructor"}
    assert result["src/models.Entity.constructor"] == []
    assert all(isinstance(cs, TSCallsite) for cs in result["src/services.UserService.create"])
    assert [cs.callee_signature for cs in result["src/services.UserService.create"]] == ["src/services.nextId", None]
    assert [cs.start_line for cs in result["src/services.UserService.create"]] == [1, 2]


def test_callsites_for_query_shape_and_scope():
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured.update(params)
        return []

    backend = _backend()
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        assert backend.get_callsites_for(["sig-a"]) == {}
    assert "TS_HAS_BODY_NODE]->(s:TSBodyNode {kind: 'call'})" in captured["query"]
    assert "TS_RESOLVES_TO" in captured["query"]
    assert "coalesce(t.signature, t.module + '.' + t.name) AS callee" in captured["query"]
    assert captured["sigs"] == ["sig-a"]
    assert captured["p1"] == PREFIX and captured["p2"] == SCOPE["p2"]
