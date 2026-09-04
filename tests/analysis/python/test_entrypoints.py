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

"""Task 9: ``get_entrypoints()``.

The analyzer's own entrypoint-detection pass already stamps ``PyCallable.is_entrypoint``; this
accessor just surfaces that mark instead of making a caller rediscover it. Verified against
``codeanalyzer/neo4j/project.py`` (``_callable_props``): the Neo4j projection emits
``is_entrypoint`` as a real boolean property on every ``:PyCallable`` node (``prune`` only drops
``None``, never ``False``), so ``False`` is present-and-queryable, not absent -- an empty result
from ``get_entrypoints()`` means "this project has no entrypoints", not "the graph doesn't carry
the mark".

The local backend is built the same way ``test_python_bulk_accessors.py`` builds its fixture
(``object.__new__`` + a hand-assembled ``PyApplication``); the Neo4j backend is built the same way
``test_typescript_neo4j_bulk.py`` builds its fixture (``object.__new__`` + a stubbed ``_run``) --
no live server, no FakeDriver machinery needed for a single filtered projection query.
"""

from unittest.mock import patch

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass, PyModule

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
from cldk.models.python import PyCallableOverview


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
        "path": "svc/app.py",
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
    assert "c._module IN $mods" in query
    assert "SET" not in query and "CREATE" not in query and "MERGE" not in query and "DELETE" not in query


def test_no_entrypoints_over_neo4j_is_an_empty_list_not_none():
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"c.is_entrypoint = true": []})):
        assert backend.get_entrypoints() == []


def test_entrypoints_parity_between_backends():
    """Same fixture, same signatures, on both backends."""
    local_sigs = {o.signature for o in _local_backend().get_entrypoints()}

    row_handler = {"signature": "svc.app.handler", "name": "handler", "decorators": [], "path": "svc/app.py", "start_line": 1, "end_line": 2, "class_signature": None}
    row_run = {"signature": "svc.app.Service.run", "name": "run", "decorators": [], "path": "svc/app.py", "start_line": 3, "end_line": 4, "class_signature": "svc.app.Service"}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"c.is_entrypoint = true": [row_handler, row_run]})):
        neo4j_sigs = {o.signature for o in backend.get_entrypoints()}

    assert local_sigs == neo4j_sigs == {"svc.app.handler", "svc.app.Service.run"}
