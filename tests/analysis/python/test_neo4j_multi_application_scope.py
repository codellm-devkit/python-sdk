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

"""The two child-fetch paths must agree in a database holding more than one application.

``PyNeo4jBackend`` reconstructs a declaration's children two ways: the **bulk** path (inside
``_bulk()``, served from prefetches indexed by parent key) and the **per-parent** path (one
statement per child collection, naming the parent). The live suite cannot tell them apart because
the graph it attaches to holds exactly one application — but the SDK's premise is attaching to a
graph someone else deployed, and a Unified Knowledge Graph holding several applications is the
expected deployment, not an exotic one.

So the two-application graph is a fake driver, not a real write: this suite, like every other
Neo4j test here, never emits ``CREATE``/``MERGE``/``SET``/``DELETE``.

The fixture is deliberately degenerate in the one way that matters: **both applications declare
the same class signature, holding the same method signature**. Those signatures are the parent
keys every child fetch is addressed by — ``$sig`` on the per-parent statement, ``pk`` in the bulk
index — so a collision is exactly the condition under which an unscoped statement merges the two
applications' children. Every child below is named for its own application, so a leak is visible
by name rather than by count.

**The four module-keyed collections** (``module_classes``, ``module_functions``,
``module_variables``, ``module_imports``) are addressed by a module ``file_key`` rather than a
signature, and ``$mods`` (a list of module keys) cannot tell two applications apart when they
*share* a key -- ``src/__init__.py`` in both is the ordinary case, not an exotic one. So those
statements carry the application's id prefix too, and the second fixture below is exactly that
collision: one ``file_key`` declared by both applications, a function named for each. The audit
``test_every_child_statement_carries_the_application_scope`` catches the predicate being dropped
from any of the eleven.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Dict, List

import pytest
from codeanalyzer.schema.ids import module_id

from cldk.analysis.python.neo4j import neo4j_backend
from cldk.analysis.python.neo4j.neo4j_backend import _BULK_CHILD_QUERIES, PyNeo4jBackend

APP_A, APP_B = "app_a", "app_b"
APP_A_MODULE = "a/mod.py"
APP_B_MODULE = "b/mod.py"
_APP_OF = {APP_A_MODULE: APP_A, APP_B_MODULE: APP_B}

#: The colliding parent keys — the same class and method signature in both applications.
CLASS_SIG = "shared.Widget"
METHOD_SIG = "shared.Widget.render"


def _node(module: str, key: str, **props: Any) -> Dict[str, Any]:
    """A fixture node the way codeanalyzer-python 1.4.1 emits it: the application and the module
    live in the ``id`` (``can://python/<app>/<file_key>/...``) and nowhere else -- there is no
    ``_module`` property, which is exactly what the fake server has to be able to scope without."""
    return {"id": f"{module_id(_APP_OF[module], module)}/{key}", **props}


#: ``bucket -> (parent key, {application module -> that application's one child's properties})``.
#: Every child is named for its application, so ``alpha`` present and ``beta`` absent is the
#: assertion; ``beta`` present means the statement that fetched it was not application-scoped.
_CHILDREN: Dict[str, tuple[str, Dict[str, Dict[str, Any]]]] = {
    "class_methods": (
        CLASS_SIG,
        {
            APP_A_MODULE: _node(APP_A_MODULE, "Widget/render", signature=METHOD_SIG, name="alpha_method", path=APP_A_MODULE),
            APP_B_MODULE: _node(APP_B_MODULE, "Widget/render", signature=METHOD_SIG, name="beta_method", path=APP_B_MODULE),
        },
    ),
    "class_attributes": (
        CLASS_SIG,
        {APP_A_MODULE: _node(APP_A_MODULE, "Widget/alpha_attr", name="alpha_attr"), APP_B_MODULE: _node(APP_B_MODULE, "Widget/beta_attr", name="beta_attr")},
    ),
    "class_inner_classes": (
        CLASS_SIG,
        {
            APP_A_MODULE: _node(APP_A_MODULE, "Widget/Inner", signature="alpha.Inner", name="Inner", path=APP_A_MODULE),
            APP_B_MODULE: _node(APP_B_MODULE, "Widget/Inner", signature="beta.Inner", name="Inner", path=APP_B_MODULE),
        },
    ),
    "callable_callsites": (
        METHOD_SIG,
        {APP_A_MODULE: _node(APP_A_MODULE, "Widget/render@1:0", method_name="alpha_call"), APP_B_MODULE: _node(APP_B_MODULE, "Widget/render@1:0", method_name="beta_call")},
    ),
    "callable_inner_callables": (
        METHOD_SIG,
        {
            APP_A_MODULE: _node(APP_A_MODULE, "Widget/render/inner_fn", signature="alpha.inner_fn", name="alpha_inner_fn", path=APP_A_MODULE),
            APP_B_MODULE: _node(APP_B_MODULE, "Widget/render/inner_fn", signature="beta.inner_fn", name="beta_inner_fn", path=APP_B_MODULE),
        },
    ),
    "callable_inner_classes": (
        METHOD_SIG,
        {
            APP_A_MODULE: _node(APP_A_MODULE, "Widget/render/InnerInFn", signature="alpha.InnerInFn", name="InnerInFn", path=APP_A_MODULE),
            APP_B_MODULE: _node(APP_B_MODULE, "Widget/render/InnerInFn", signature="beta.InnerInFn", name="InnerInFn", path=APP_B_MODULE),
        },
    ),
    "callable_variables": (
        METHOD_SIG,
        {APP_A_MODULE: _node(APP_A_MODULE, "Widget/render/alpha_var", name="alpha_var"), APP_B_MODULE: _node(APP_B_MODULE, "Widget/render/beta_var", name="beta_var")},
    ),
}

#: ``bucket -> what the reconstructed class must contain, application A's child and nothing else``.
_EXPECTED: Dict[str, tuple[Any, set[str]]] = {
    "class_methods": (lambda c: set(c.callables), {"alpha_method"}),
    "class_attributes": (lambda c: set(c.attributes), {"alpha_attr"}),
    "class_inner_classes": (lambda c: set(c.types), {"alpha.Inner"}),
    "callable_callsites": (lambda c: {s.method_name for s in c.callables["alpha_method"].call_sites}, {"alpha_call"}),
    "callable_inner_callables": (lambda c: set(c.callables["alpha_method"].callables), {"alpha_inner_fn"}),
    "callable_inner_classes": (lambda c: set(c.callables["alpha_method"].types), {"alpha.InnerInFn"}),
    "callable_variables": (lambda c: {v.name for v in c.callables["alpha_method"].local_variables}, {"alpha_var"}),
}

#: The module-key collision: one ``file_key`` declared by **both** applications, each holding one
#: top-level function named for its application. ``module_id`` puts the application in the id and
#: nowhere else, exactly as for the class fixture above.
SHARED_MODULE = "src/__init__.py"
_SHARED_FUNCTIONS: Dict[str, Dict[str, Any]] = {
    app: {"id": f"{module_id(app, SHARED_MODULE)}/{name}", "signature": f"src.{name}", "name": name, "path": SHARED_MODULE}
    for app, name in ((APP_A, "alpha_fn"), (APP_B, "beta_fn"))
}

# One :PyClass node per application, same signature, different owning module — what ``get_class``
# and ``get_all_classes`` select on before any child fetch happens.
_CLASSES: List[Dict[str, Any]] = [
    _node(APP_A_MODULE, "Widget", signature=CLASS_SIG, name="Widget", path=APP_A_MODULE),
    _node(APP_B_MODULE, "Widget", signature=CLASS_SIG, name="Widget", path=APP_B_MODULE),
]

#: The three spellings of the application scope a statement may carry: the whole application
#: (``$prefix``); for a narrowed bulk fetch, a list of per-module prefixes (``$prefixes``); and for
#: ``locate``, one module's own prefix per position (``pos.module_prefix``, minted from the same
#: application name). ``file_key IN $mods`` is *not* one: a module key is not application-stamped.
_MATCHES_BY_PREFIX = re.compile(r"\.id STARTS WITH (\$prefix\b|pos\.module_prefix\b)|any\(p IN \$prefixes WHERE \w+\.id STARTS WITH p\)")


def _is_scoped(statement: str) -> bool:
    return bool(_MATCHES_BY_PREFIX.search(statement))


def _bucket_of(query: str) -> str | None:
    """Which child collection a statement is fetching — bulk and per-parent twins alike.

    The markers are the parts the two twins share: the relationship type, or the child's variable
    name where two collections share one (``PY_DECLARES`` carries inner classes *and* inner
    callables, from classes *and* from callables).
    """
    if "PY_HAS_METHOD" in query:
        return "class_methods"
    if "PY_HAS_ATTRIBUTE" in query:
        return "class_attributes"
    if "PY_HAS_BODY_NODE" in query:
        return "callable_callsites"
    if "PY_DECLARES_VAR" in query:
        return "callable_variables" if "PyCallable" in query else "module_variables"
    if "(ic:PyClass)" in query:
        return "class_inner_classes"
    if "(d:PyCallable)" in query:
        return "callable_inner_callables"
    if "(d:PyClass)" in query:
        return "callable_inner_classes"
    if "(f:PyCallable)" in query:
        return "module_functions"
    return None


def _in_scope(query: str, params: Dict[str, Any], props: Dict[str, Any]) -> bool:
    """Evaluate the statement's application-scope predicate the way a 1.4.1 server would: on the
    node's ``id`` prefix, and **only if the statement asks**. An unscoped statement sees both
    applications' rows, which is what makes these tests fail without the predicate. A statement
    still asking for ``_module IN $mods`` gets what a 1.4.1 graph gives it -- nothing."""
    if "STARTS WITH $prefix" in query:
        return props["id"].startswith(params["prefix"])
    if "$prefixes" in query:
        return any(props["id"].startswith(p) for p in params["prefixes"])
    if "_module IN $mods" in query:
        return props.get("_module") in (params.get("mods") or [])
    return True


def _fake_two_app_cypher(query: str, **params: Any) -> List[Dict[str, Any]]:
    """Answer the statements a class reconstruction issues, honestly (see :func:`_in_scope`)."""
    bucket = _bucket_of(query)
    if bucket == "module_functions":
        # Keyed by file_key: the per-parent twin names it (``$fk``), the bulk twin lists the scope
        # (``$mods``). Both applications declare SHARED_MODULE, so that filter alone admits both.
        wanted = [params["fk"]] if "fk" in params else (params.get("mods") or [])
        if SHARED_MODULE not in wanted:
            return []
        rows = [props for props in _SHARED_FUNCTIONS.values() if _in_scope(query, params, props)]
        if "AS pk" in query:
            return [{"pk": SHARED_MODULE, "p": props} for props in rows]
        return [{"p": props} for props in rows]
    if bucket in _CHILDREN:
        pk, by_module = _CHILDREN[bucket]
        if params.get("sig", pk) != pk:  # a per-parent statement about some other parent
            return []
        rows = [props for props in by_module.values() if _in_scope(query, params, props)]
        if "AS pk" in query:  # the bulk twin returns the parent key alongside the child
            return [{"pk": pk, "p": props} for props in rows]
        return [{"p": props} for props in rows]
    # module -> top-level classes, per-parent (get_class) and whole-application (get_all_classes).
    # "AS pk" excludes the module_classes bulk bucket, which has no rows in this fixture.
    if "(c:PyClass" in query and "PY_DECLARES" in query and "AS pk" not in query:
        rows = [c for c in _CLASSES if "$sig" not in query or c["signature"] == params["sig"]]
        return [{"p": c} for c in rows if _in_scope(query, params, c)]
    return []  # imports and the module-level buckets: none in this fixture


def _two_app_backend(record: List[str] | None = None) -> PyNeo4jBackend:
    """A backend scoped to application A over a fake driver holding A and B."""

    def run(query: str, **params: Any) -> List[Dict[str, Any]]:
        if record is not None:
            record.append(query)
        return _fake_two_app_cypher(query, **params)

    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = APP_A
    backend._database = None
    backend._driver = None
    backend._session_obj = None
    backend._modules = [APP_A_MODULE, SHARED_MODULE]
    backend._call_graph = None
    backend._run = run
    return backend


def test_the_fake_graph_carries_no_module_property():
    """What a 1.4.1 graph is: scope lives in the id, and there is no ``_module`` to fall back on.
    A fixture that grew the property back would let a ``_module``-scoped statement pass here while
    returning nothing on a real graph."""
    nodes = [c for _, by_module in _CHILDREN.values() for c in by_module.values()] + _CLASSES + list(_SHARED_FUNCTIONS.values())
    assert nodes and not any("_module" in n for n in nodes)
    assert all(n["id"].startswith(("can://python/app_a/", "can://python/app_b/")) for n in nodes)


def test_per_parent_path_does_not_leak_another_applications_children():
    """``get_class`` must not merge application B's methods into application A's class."""
    cls = _two_app_backend().get_class(CLASS_SIG)
    assert cls is not None, "the application's own class came back empty -- the statement scoped on a property the graph does not carry"
    assert set(cls.callables) == {"alpha_method"}, "per-parent child fetch leaked another application's members"


def test_scoped_and_bulk_paths_agree_across_applications():
    """The two paths must answer identically regardless of what else is in the database."""
    backend = _two_app_backend()
    scoped = backend.get_class(CLASS_SIG)
    bulk = backend.get_all_classes()[CLASS_SIG]
    assert scoped is not None
    assert set(scoped.callables) == set(bulk.callables) == {"alpha_method"}


@pytest.mark.parametrize("bucket", sorted(_EXPECTED))
@pytest.mark.parametrize("bulk", [False, True], ids=["per_parent", "bulk"])
def test_every_signature_keyed_child_collection_is_application_scoped(bucket: str, bulk: bool):
    """Each of the seven collections addressed by a *signature*, on both fetch paths.

    ``class_methods`` was the only one with a regression test when the application-scope predicate
    was added to the per-parent statements; the other six were fixed in the same change and pinned
    by nothing. They are all reachable from one class reconstruction, so one walk covers them and
    the parameter names which child is being judged.
    """
    backend = _two_app_backend()
    props = dict(_CLASSES[0])

    if bulk:
        with backend._bulk():
            cls = backend._class_full(props)
    else:
        cls = backend._class_full(props)

    extract, expected = _EXPECTED[bucket]
    assert extract(cls) == expected, f"{bucket} leaked another application's children"


@pytest.mark.parametrize("bulk", [False, True], ids=["per_parent", "bulk"])
def test_a_module_key_shared_by_two_applications_does_not_leak(bulk: bool):
    """``file_key IN $mods`` cannot separate two applications that both declare ``src/__init__.py``;
    only the id prefix can. Application A's module must come back with A's function and not B's,
    on both fetch paths."""
    backend = _two_app_backend()
    props = {"file_key": SHARED_MODULE, "module_name": "src"}
    if bulk:
        with backend._bulk():
            module = backend._module_full(props)
    else:
        module = backend._module_full(props)
    assert set(module.functions) == {"alpha_fn"}, "the module-keyed fetch leaked another application's function"


def test_every_child_statement_carries_the_application_scope():
    """The four module-keyed collections cannot be caught by the fixture above (see the module
    docstring), so they are caught here: every statement either path issues to fetch children is
    application-scoped, and a predicate silently dropped from any of the eleven fails this."""
    assert [b for b, q in _BULK_CHILD_QUERIES.items() if not _is_scoped(q)] == []

    issued: List[str] = []
    backend = _two_app_backend(record=issued)
    backend._module_full({"file_key": APP_A_MODULE, "module_name": "mod"})
    backend._class_full(dict(_CLASSES[0]))

    assert issued, "the walk issued no statements, so this asserts nothing"
    assert [q for q in issued if not _is_scoped(q)] == []


# ----------------------------------------------------------------------------------------------
# The class-level statements behind the leg-1.5 accessors -- the audit above sees only the child
# buckets, which is why six statements that matched by bare ``{signature: …}`` went unnoticed.
# ----------------------------------------------------------------------------------------------
_MATCHES_BY_SIGNATURE = re.compile(r"signature\s*[:=]\s*\$|\.signature IN \$")
_MATCHES_BY_ID = re.compile(r"\bid\s*:\s*\$|\.id IN \$")

#: Class-level strings that are Cypher but not a whole statement: appended to a scoped ``MATCH``
#: at each use, so their scope is judged at the use sites (below), not on the fragment.
_FRAGMENTS = {"_OVERVIEW_PROJECTION"}


def _class_level_statements() -> Dict[str, str]:
    """Every class attribute that starts a Cypher clause -- not only ``MATCH``: filtering on that
    alone silently skipped ``_OVERVIEW_PROJECTION`` (``OPTIONAL MATCH``) and ``_LOCATE_QUERY``
    (``UNWIND``), and an audit that cannot see a statement cannot protect it."""
    return {
        name: value
        for name, value in vars(PyNeo4jBackend).items()
        if isinstance(value, str) and re.match(r"(MATCH|OPTIONAL MATCH|UNWIND|CALL)\b", value.lstrip())
    }


def test_the_audit_sees_the_dataflow_statements_too():
    names = set(_class_level_statements())
    for expected in ("_REACHES", "_CONE", "_PATHS", "_CALL_PATHS", "_VALUE_REACHES", "_CALLEE_VALUES", "_SOURCES", "_SLICE", "_CALLERS", "_CALLEES", "_OWN_EDGES", "_LOCATE_QUERY", "_OVERVIEW_PROJECTION"):
        assert expected in names, f"{expected} is not a class-level statement any more; move it back or extend the audit"


@pytest.mark.parametrize("name", sorted(set(_class_level_statements()) - _FRAGMENTS))
def test_every_statement_is_application_scoped_or_keyed_by_an_application_stamped_id(name):
    """Two ways a statement stays inside one application, and every statement must use one.

    A **signature** is not application-stamped: two applications in one database can declare the
    same one, so any statement that matches a node by signature must also carry the application
    scope -- ``.id STARTS WITH $prefix`` (or, on a narrowed bulk fetch, the per-module prefixes).
    A body-node or ghost **id** embeds the application (``can://python/<app>/…``) and the emitter
    only ever links nodes from its own run, so a statement keyed *only* by id is scoped by
    construction and may omit the predicate -- ``_SLICE``, ``_PATHS`` and ``_VALUE_REACHES`` do,
    for the measured cost of testing 195,784 reached nodes against a list. A statement keyed by
    neither would be unscoped and fails here.
    """
    statement = _class_level_statements()[name]
    by_signature, by_id, scoped = bool(_MATCHES_BY_SIGNATURE.search(statement)), bool(_MATCHES_BY_ID.search(statement)), _is_scoped(statement)
    assert scoped or by_id, f"{name} carries no application scope and is not keyed by an application-stamped id"
    if by_signature:
        assert scoped, f"{name} matches by signature without the application scope"


def test_the_overview_projection_is_only_ever_appended_to_a_scoped_match():
    """``_OVERVIEW_PROJECTION`` binds no node of its own (``(c)`` is whatever the preceding
    ``MATCH`` bound), so it is scoped exactly when every statement it is appended to is."""
    source = inspect.getsource(neo4j_backend)
    uses = source.split("self._OVERVIEW_PROJECTION")[:-1]
    assert len(uses) >= 3, "the projection is used from fewer places than expected; did it move?"
    for before in uses:
        statement = before[before.rindex("self._run(") :]
        assert _is_scoped(statement), f"an unscoped MATCH feeds the overview projection: {statement[-200:]!r}"
