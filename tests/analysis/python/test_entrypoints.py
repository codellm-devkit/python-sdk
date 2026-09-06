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

"""Task 9: ``get_entrypoints()``, plus the review-round fixes for findings 1 and 2 (2026-09-04).

The analyzer's own entrypoint-detection pass already stamps ``PyCallable.is_entrypoint``; this
accessor just surfaces that mark instead of making a caller rediscover it. Verified against
``codeanalyzer/neo4j/project.py`` (``_callable_props``): the Neo4j projection emits
``is_entrypoint`` as a real boolean property on every ``:PyCallable`` node (``prune`` only drops
``None``, never ``False``), so ``False`` is present-and-queryable, not absent -- an empty result
from ``get_entrypoints()`` means "this project has no entrypoints", not "the graph doesn't carry
the mark".

Two gaps found in review, both fixed with new sibling accessors rather than widening
``get_entrypoints()``'s frozen ``List[PyCallableOverview]`` return:

* **Finding 2** -- ``get_entrypoints()`` walks ``PyCallable`` only, so a class marked
  ``is_entrypoint`` at the class level (``:PyClass`` carries the same mark, per
  ``_class_props``/``schema/py_schema.py``) with no individually-marked method is silently
  omitted. ``get_entrypoint_classes()`` is the sibling that answers for classes.
* **Finding 1** -- an empty ``get_entrypoints()`` cannot distinguish "ran clean, found none" from
  "the detection pass had gaps" (its own ``PyEntrypointReport`` docstring: "under-approximates by
  design, so silence is its failure mode"). ``get_entrypoint_coverage()`` surfaces that report.
  Over Neo4j the report is read off ``:PyApplication.entrypoint_report_json``, which
  codeanalyzer-python projects from 1.4.1 (#182) as the sorted-key JSON of the very same
  ``PyEntrypointReport`` the local backend passes through. A 1.4.0 graph has no such property
  (only the derived ``is_entrypoint``/``entrypoint_frameworks`` per-node marks), and there the
  Neo4j backend answers with a ``diagnostics``-only ``entrypoint_report_unavailable`` result
  rather than fabricating a clean-looking empty report.

The local backend is built the same way ``test_python_bulk_accessors.py`` builds its fixture
(``object.__new__`` + a hand-assembled ``PyApplication``); the Neo4j backend is built the same way
``test_typescript_neo4j_bulk.py`` builds its fixture (``object.__new__`` + a stubbed ``_run``) --
no live server, no FakeDriver machinery needed for a single filtered projection query.
"""

import json
from unittest.mock import patch

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass, PyEntrypointReport, PyModule

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
from cldk.models.python import PyCallableOverview, PyClassOverview


# -----[ local backend fixture ]-----
def _local_backend(*, any_entrypoints: bool = True) -> PyCodeanalyzer:
    """A ``PyCodeanalyzer`` over a small hand-built application: one entrypoint function, one
    entrypoint method, and one plain function that is never marked."""
    handler = PyCallable(name="handler", path="svc/app.py", signature="svc.app.handler", is_entrypoint=any_entrypoints)
    helper = PyCallable(name="helper", path="svc/app.py", signature="svc.app.helper", is_entrypoint=False)
    run = PyCallable(name="run", path="svc/app.py", signature="svc.app.Service.run", is_entrypoint=any_entrypoints)
    service = PyClass(name="Service", signature="svc.app.Service", callables={"run": run})
    module = PyModule(
        file_path="svc/app.py",
        module_name="svc.app",
        types={"svc.app.Service": service},
        functions={"handler": handler, "helper": helper},
    )
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"svc/app.py": module})
    return backend


def test_entrypoints_are_filtered_locally():
    eps = {o.signature: o for o in _local_backend().get_entrypoints()}
    assert set(eps) == {"svc.app.handler", "svc.app.Service.run"}
    assert all(isinstance(o, PyCallableOverview) for o in eps.values())
    assert eps["svc.app.Service.run"].class_signature == "svc.app.Service"
    assert eps["svc.app.Service.run"].kind == "method"
    assert eps["svc.app.handler"].kind == "function"


def test_entrypoints_are_a_subset_of_callables_locally():
    backend = _local_backend()
    sigs = {c.signature for c in backend.get_callables_overview()}
    assert {o.signature for o in backend.get_entrypoints()} <= sigs


def test_no_entrypoints_is_an_empty_list_not_none():
    """"This project has no entrypoints" must be a real, distinguishable empty -- not a null."""
    result = _local_backend(any_entrypoints=False).get_entrypoints()
    assert result == []


# -----[ Neo4j backend fixture ]-----
def _neo4j_backend(modules=("svc/app.py",)) -> PyNeo4jBackend:
    """A ``PyNeo4jBackend`` with ``__init__`` (and its real driver connection) bypassed."""
    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "app"
    backend._database = None
    backend._modules = list(modules)
    return backend


def _run_keyed(rows_by_fragment: dict):
    def _run(query: str, **params):
        for fragment, result in rows_by_fragment.items():
            if fragment in query:
                return result
        raise AssertionError(f"no canned rows for query: {query!r} (params={params})")

    return _run


def test_entrypoints_query_filters_on_is_entrypoint_property():
    row = {
        "signature": "svc.app.handler",
        "name": "handler",
        "decorators": [],
        "id": "can://python/app/svc/app.py/handler",
        "start_line": 1,
        "end_line": 2,
        "class_signature": None,
    }
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"c.is_entrypoint = true": [row]})) as run:
        eps = backend.get_entrypoints()
    assert [o.signature for o in eps] == ["svc.app.handler"]
    assert isinstance(eps[0], PyCallableOverview)
    query = run.call_args.args[0]
    assert "c.id STARTS WITH $prefix" in query
    assert "SET" not in query and "CREATE" not in query and "MERGE" not in query and "DELETE" not in query


def test_no_entrypoints_over_neo4j_is_an_empty_list_not_none():
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"c.is_entrypoint = true": []})):
        assert backend.get_entrypoints() == []


def test_entrypoints_parity_between_backends():
    """Same fixture, same signatures, on both backends."""
    local_sigs = {o.signature for o in _local_backend().get_entrypoints()}

    common = {"decorators": [], "start_line": 1, "end_line": 2}
    row_handler = {"signature": "svc.app.handler", "name": "handler", "id": "can://python/app/svc/app.py/handler", "class_signature": None, **common}
    row_run = {"signature": "svc.app.Service.run", "name": "run", "id": "can://python/app/svc/app.py/Service/run", "class_signature": "svc.app.Service", **common}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"c.is_entrypoint = true": [row_handler, row_run]})):
        neo4j_sigs = {o.signature for o in backend.get_entrypoints()}

    assert local_sigs == neo4j_sigs == {"svc.app.handler", "svc.app.Service.run"}


# =====================================================================================
# Finding 2 -- get_entrypoint_classes(): the class-level sibling get_entrypoints() cannot see
# =====================================================================================
def _local_backend_with_class_entrypoint() -> PyCodeanalyzer:
    """A class-based view marked ``is_entrypoint`` at the class itself, with only an *unmarked*
    method -- exactly the case ``get_entrypoints()`` (callables-only) is blind to."""
    dispatch = PyCallable(
        name="dispatch", path="svc/views.py", signature="svc.views.AdminView.dispatch", is_entrypoint=False
    )
    admin_view = PyClass(
        name="AdminView", signature="svc.views.AdminView", callables={"dispatch": dispatch}, is_entrypoint=True
    )
    module = PyModule(file_path="svc/views.py", module_name="svc.views", types={"svc.views.AdminView": admin_view})
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"svc/views.py": module})
    return backend


def test_entrypoint_classes_are_filtered_locally():
    classes = _local_backend_with_class_entrypoint().get_entrypoint_classes()
    assert [c.signature for c in classes] == ["svc.views.AdminView"]
    assert isinstance(classes[0], PyClassOverview)
    assert classes[0].path == "svc/views.py"


def test_class_entrypoint_with_unmarked_method_is_invisible_to_get_entrypoints():
    """The finding-2 gap, made concrete: a class marked at the class level with no individually
    marked method surfaces nowhere in get_entrypoints() -- only in its sibling."""
    backend = _local_backend_with_class_entrypoint()
    assert backend.get_entrypoints() == []
    assert backend.get_entrypoint_classes() != []


def test_no_entrypoint_classes_is_an_empty_list_not_none():
    module = PyModule(file_path="svc/app.py", module_name="svc.app")
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"svc/app.py": module})
    assert backend.get_entrypoint_classes() == []


def test_entrypoint_classes_query_filters_on_is_entrypoint_property():
    row = {
        "signature": "svc.views.AdminView",
        "name": "AdminView",
        "decorators": [],
        "id": "can://python/app/svc/views.py/AdminView",
        "start_line": 1,
        "end_line": 10,
    }
    backend = _neo4j_backend(modules=("svc/views.py",))  # the row's path is derived from its id and verified against these
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"cl.is_entrypoint = true": [row]})) as run:
        classes = backend.get_entrypoint_classes()
    assert [c.signature for c in classes] == ["svc.views.AdminView"]
    assert isinstance(classes[0], PyClassOverview)
    query = run.call_args.args[0]
    assert "cl.id STARTS WITH $prefix" in query
    assert "SET" not in query and "CREATE" not in query and "MERGE" not in query and "DELETE" not in query


def test_no_entrypoint_classes_over_neo4j_is_an_empty_list_not_none():
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"cl.is_entrypoint = true": []})):
        assert backend.get_entrypoint_classes() == []


def test_entrypoint_classes_parity_between_backends():
    local_sigs = {c.signature for c in _local_backend_with_class_entrypoint().get_entrypoint_classes()}

    row = {
        "signature": "svc.views.AdminView",
        "name": "AdminView",
        "decorators": [],
        "id": "can://python/app/svc/views.py/AdminView",
        "start_line": 1,
        "end_line": 10,
    }
    backend = _neo4j_backend(modules=("svc/views.py",))  # the row's path is derived from its id and verified against these
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"cl.is_entrypoint = true": [row]})):
        neo4j_sigs = {c.signature for c in backend.get_entrypoint_classes()}

    assert local_sigs == neo4j_sigs == {"svc.views.AdminView"}


# =====================================================================================
# Finding 1 -- get_entrypoint_coverage(): is an empty get_entrypoints() "clean" or "had gaps"?
# =====================================================================================
def test_entrypoint_coverage_surfaces_the_report_locally():
    report = PyEntrypointReport(
        frameworks_detected=["flask"],
        rulesets=["shipped"],
        unresolved={"flask": 2},
        errors=["timeout scanning svc/legacy.py"],
    )
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={}, entrypoint_report=report)
    coverage = backend.get_entrypoint_coverage()
    assert coverage.frameworks_detected == ["flask"]
    assert coverage.rulesets == ["shipped"]
    assert coverage.unresolved == {"flask": 2}
    assert coverage.errors == ["timeout scanning svc/legacy.py"]
    assert coverage.diagnostics == []


def test_entrypoint_coverage_over_neo4j_is_read_from_the_application_node():
    """A 1.4.1 graph carries the report as ``:PyApplication.entrypoint_report_json`` -- the same
    ``PyEntrypointReport`` the local backend passes through, dumped as sorted-key JSON
    (codeanalyzer/neo4j/project.py, #182) -- so both backends answer identically from one analysis."""
    report = PyEntrypointReport(
        frameworks_detected=["flask"],
        rulesets=["shipped"],
        unresolved={"flask": 2},
        errors=["timeout scanning svc/legacy.py"],
    )
    local = object.__new__(PyCodeanalyzer)
    local.application = PyApplication(symbol_table={}, entrypoint_report=report)
    row = {"p": {"analyzer_version": "1.4.1", "entrypoint_report_json": json.dumps(report.model_dump(mode="json"), sort_keys=True)}}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"RETURN properties(a) AS p": [row]})):
        coverage = backend.get_entrypoint_coverage()
    assert coverage == local.get_entrypoint_coverage()
    assert coverage.diagnostics == []


def test_entrypoint_coverage_over_a_graph_without_the_report_says_it_cannot_answer():
    """A 1.4.0 graph never had the property -- say so via a diagnostic rather than fabricate a
    clean-looking empty report."""
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"RETURN properties(a) AS p": [{"p": {"analyzer_version": "1.4.0"}}]})):
        coverage = backend.get_entrypoint_coverage()
    assert len(coverage.diagnostics) == 1
    assert coverage.diagnostics[0].code == "entrypoint_report_unavailable"
    assert coverage.frameworks_detected == []
    assert coverage.unresolved == {}
