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

"""Regression tests for issue #246: ``get_method`` was blind to module-level functions.

``get_method(scope, name)`` used to delegate straight to ``get_all_methods_in_class(scope)``, so
any ``scope`` that names a *module* rather than a *class* came back empty — and since
``get_all_callers`` / ``get_all_callees`` call ``get_method`` internally, they silently reported
``{"caller_details": []}`` / ``{"callee_details": []}`` for module-level functions even when the
call graph knew the true edge.

These tests build a tiny fixture with a ``pkg.mod.entry -> pkg.mod.helper`` call edge and exercise
both backends:

* :class:`PyCodeanalyzer` — a hand-built in-memory ``PyApplication`` attached to a bare instance
  (same pattern as ``test_python_bulk_accessors.py``), no analyzer run needed.
* :class:`PyNeo4jBackend` — a bare instance with ``_run`` monkeypatched to a tiny in-memory Cypher
  stub, since no live Neo4j server is available in this environment. This exercises the backend's
  real dispatch/query-construction logic (which query gets issued and how the row is turned back
  into a ``PyCallable``); it does not touch the neo4j driver or a real graph.
"""

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyCallEdge, PyClass, PyModule

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend

# ----------------------------------------------------------------------------------------------
# Shared fixture data: module "pkg.mod" declares two top-level functions, entry -> helper.
# ----------------------------------------------------------------------------------------------
MODULE_NAME = "pkg.mod"
ENTRY_SIG = "pkg.mod.entry"
HELPER_SIG = "pkg.mod.helper"


def _local_backend():
    """A PyCodeanalyzer wired to a hand-built application, bypassing the analyzer run."""
    entry = PyCallable(name="entry", path="pkg/mod.py", signature=ENTRY_SIG, code="helper()")
    helper = PyCallable(name="helper", path="pkg/mod.py", signature=HELPER_SIG, code="return 1")
    module = PyModule(
        file_path="pkg/mod.py",
        module_name=MODULE_NAME,
        functions={"entry": entry, "helper": helper},
    )
    app = PyApplication(
        symbol_table={"pkg/mod.py": module},
        call_graph=[PyCallEdge(source=ENTRY_SIG, target=HELPER_SIG)],
    )

    backend = object.__new__(PyCodeanalyzer)
    backend.application = app
    backend.call_graph = None
    return backend


def _fake_cypher(classes, methods, modules, call_edges):
    """A minimal in-memory Cypher stub matching the query shapes PyNeo4jBackend issues.

    ``classes``: {signature: props} (top-level classes, matched via ``PyModule)-[:PY_DECLARES]->
    (c:PyClass``). ``methods``: {class_signature: [props, ...]}. ``modules``: {module_name:
    {"file_key": ..., "functions": [props, ...]}}. Anything else (attributes, inner
    classes/callables, call sites, local variables) yields no rows, matching a fixture with no
    such children — each check below is ordered most-specific first so it never falls through to
    a broader, wrong match.
    """

    def run(query, **params):
        if "PyModule)-[:PY_DECLARES]->(c:PyClass {signature: $sig})" in query:  # get_class (top-level only)
            props = classes.get(params["sig"])
            return [{"p": props}] if props else []
        if "PY_HAS_METHOD" in query:  # class -> methods
            return [{"p": p} for p in methods.get(params["sig"], [])]
        if "PyModule {module_name: $name})-[:PY_DECLARES]->(f:PyCallable)" in query:  # module -> functions
            mod = modules.get(params["name"])
            return [{"p": p} for p in mod["functions"]] if mod else []
        if "PY_CALLS" in query:
            return [{"src": e[0], "tgt": e[1], "p": {"weight": 1, "provenance": []}} for e in call_edges]
        return []  # attributes / inner classes / inner callables / call sites / local vars: none in this fixture

    return run


def _neo4j_backend():
    """A bare PyNeo4jBackend with ``_run`` stubbed (no live server, no __init__ side effects)."""
    entry_props = {"name": "entry", "signature": ENTRY_SIG, "path": "pkg/mod.py", "code": "helper()"}
    helper_props = {"name": "helper", "signature": HELPER_SIG, "path": "pkg/mod.py", "code": "return 1"}
    modules = {MODULE_NAME: {"file_key": "pkg/mod.py", "functions": [entry_props, helper_props]}}
    call_edges = [(ENTRY_SIG, HELPER_SIG)]

    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "test_app"
    backend._database = None
    backend._driver = None
    backend._session_obj = None
    backend._modules = ["pkg/mod.py"]
    backend._call_graph = None
    backend._run = _fake_cypher(classes={}, methods={}, modules=modules, call_edges=call_edges)
    return backend


BACKEND_FACTORIES = {"local": _local_backend, "neo4j": _neo4j_backend}


# ----------------------------------------------------------------------------------------------
# get_method
# ----------------------------------------------------------------------------------------------
def test_get_method_resolves_module_level_function_local():
    backend = _local_backend()
    method = backend.get_method(MODULE_NAME, "helper")
    assert method is not None
    assert method.signature == HELPER_SIG


def test_get_method_resolves_module_level_function_neo4j():
    backend = _neo4j_backend()
    method = backend.get_method(MODULE_NAME, "helper")
    assert method is not None
    assert method.signature == HELPER_SIG


def test_get_method_missing_module_function_returns_none():
    """Miss-shape semantics are unchanged: a genuinely absent function is still None."""
    for factory in BACKEND_FACTORIES.values():
        backend = factory()
        assert backend.get_method(MODULE_NAME, "does_not_exist") is None
        assert backend.get_method("no.such.module", "helper") is None


# ----------------------------------------------------------------------------------------------
# get_all_callers / get_all_callees
# ----------------------------------------------------------------------------------------------
def test_get_all_callers_finds_true_predecessor_for_module_function_local():
    backend = _local_backend()
    result = backend.get_all_callers(MODULE_NAME, "helper")
    assert result["target_method"] == HELPER_SIG
    assert [c["caller_signature"] for c in result["caller_details"]] == [ENTRY_SIG]


def test_get_all_callers_finds_true_predecessor_for_module_function_neo4j():
    backend = _neo4j_backend()
    result = backend.get_all_callers(MODULE_NAME, "helper")
    assert result["target_method"] == HELPER_SIG
    assert [c["caller_signature"] for c in result["caller_details"]] == [ENTRY_SIG]


def test_get_all_callees_finds_true_successor_for_module_function_local():
    backend = _local_backend()
    result = backend.get_all_callees(MODULE_NAME, "entry")
    assert result["source_method"] == ENTRY_SIG
    assert [c["callee_signature"] for c in result["callee_details"]] == [HELPER_SIG]


def test_get_all_callees_finds_true_successor_for_module_function_neo4j():
    backend = _neo4j_backend()
    result = backend.get_all_callees(MODULE_NAME, "entry")
    assert result["source_method"] == ENTRY_SIG
    assert [c["callee_signature"] for c in result["callee_details"]] == [HELPER_SIG]


def test_get_all_callers_missing_method_stays_false_empty():
    """Miss-shape semantics are unchanged: a genuinely absent method still yields the empty shape."""
    for factory in BACKEND_FACTORIES.values():
        backend = factory()
        assert backend.get_all_callers(MODULE_NAME, "does_not_exist") == {"caller_details": []}
        assert backend.get_all_callees(MODULE_NAME, "does_not_exist") == {"callee_details": []}


# ----------------------------------------------------------------------------------------------
# backend parity (fix contract #3: both backends fixed identically)
# ----------------------------------------------------------------------------------------------
def test_backend_parity_for_module_level_lookup():
    local, neo4j = _local_backend(), _neo4j_backend()

    assert local.get_method(MODULE_NAME, "helper").signature == neo4j.get_method(MODULE_NAME, "helper").signature
    assert local.get_all_callers(MODULE_NAME, "helper") == neo4j.get_all_callers(MODULE_NAME, "helper")
    assert local.get_all_callees(MODULE_NAME, "entry") == neo4j.get_all_callees(MODULE_NAME, "entry")


# ----------------------------------------------------------------------------------------------
# regression: class-scoped lookup keeps working (get_method must not become module-only)
# ----------------------------------------------------------------------------------------------
def test_get_method_still_resolves_class_methods_local():
    greet = PyCallable(name="greet", path="pkg/models.py", signature="pkg.models.Entity.greet", code="...")
    entity = PyClass(name="Entity", signature="pkg.models.Entity", methods={"greet": greet})
    module = PyModule(file_path="pkg/models.py", module_name="pkg.models", classes={"pkg.models.Entity": entity})
    app = PyApplication(symbol_table={"pkg/models.py": module})

    backend = object.__new__(PyCodeanalyzer)
    backend.application = app
    backend.call_graph = None

    method = backend.get_method("pkg.models.Entity", "greet")
    assert method is not None
    assert method.signature == "pkg.models.Entity.greet"
    assert backend.get_method("pkg.models.Entity", "nope") is None


def test_get_method_still_resolves_class_methods_neo4j():
    entity_props = {"name": "Entity", "signature": "pkg.models.Entity", "code": "class Entity: ..."}
    greet_props = {"name": "greet", "signature": "pkg.models.Entity.greet", "path": "pkg/models.py", "code": "..."}

    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "test_app"
    backend._database = None
    backend._driver = None
    backend._session_obj = None
    backend._modules = ["pkg/models.py"]
    backend._call_graph = None
    backend._run = _fake_cypher(
        classes={"pkg.models.Entity": entity_props},
        methods={"pkg.models.Entity": [greet_props]},
        modules={},
        call_edges=[],
    )

    method = backend.get_method("pkg.models.Entity", "greet")
    assert method is not None
    assert method.signature == "pkg.models.Entity.greet"
    assert backend.get_method("pkg.models.Entity", "nope") is None
