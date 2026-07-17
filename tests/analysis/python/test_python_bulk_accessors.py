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

"""Offline unit tests for the in-process bulk/projected accessors.

These build a small in-memory ``PyApplication`` and attach it to a bare ``PyCodeanalyzer`` (no
analyzer run, no Neo4j), so they exercise ``get_callables_overview`` / ``get_method_bodies`` /
``get_decorated_callables`` and the ``_iter_callables`` walk without any external dependency. The
Neo4j backend is checked for byte-for-byte parity against this same logic in
``test_python_neo4j_backend.py`` when a server is available.
"""

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyCallsite, PyClass, PyModule, Span

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer


def _callable(name, signature, *, decorators=None, callables=None, types=None, call_sites=None):
    return PyCallable(
        name=name,
        path="pkg/models.py",
        signature=signature,
        decorators=decorators or [],
        callables=callables or {},
        types=types or {},
        call_sites=call_sites or [],
    )


def _class(name, signature, *, callables=None, types=None):
    return PyClass(name=name, signature=signature, callables=callables or {}, types=types or {})


def _stamp_source(module, snippets):
    """Assemble ``module.source`` from per-callable code snippets and stamp byte-offset spans.

    Schema 2.0.0 stores source once on the module; each callable carries a ``Span`` whose
    ``bytes`` slice into it (the analyzer's shape — see ``_code_of`` in the backend). One
    snippet per line keeps the offsets trivial.
    """
    offset = 0
    lines = []
    for lineno, (c, code) in enumerate(snippets, start=1):
        c.span = Span(start=(lineno, 0), end=(lineno, len(code)), bytes=(offset, offset + len(code.encode("utf-8"))))
        lines.append(code)
        offset += len(code.encode("utf-8")) + 1  # + newline
    module.source = "\n".join(lines) + "\n"


def _backend():
    """A PyCodeanalyzer wired to a hand-built application, bypassing the analyzer run."""
    decorate = _callable("_decorate", "pkg.models.greet.<locals>._decorate")
    greet = _callable(
        "greet",
        "pkg.models.greet",
        decorators=["app.route"],
        callables={"_decorate": decorate},
    )
    meta_m = _callable("m", "pkg.models.Entity.Meta.m")
    meta = _class("Meta", "pkg.models.Entity.Meta", callables={"m": meta_m})
    init = _callable("__init__", "pkg.models.Entity.__init__")
    describe = _callable(
        "describe",
        "pkg.models.Entity.describe",
        decorators=["property"],
        call_sites=[PyCallsite(method_name="greet", start_line=7, start_column=4)],
    )
    entity = _class(
        "Entity",
        "pkg.models.Entity",
        callables={"__init__": init, "describe": describe},
        types={"pkg.models.Entity.Meta": meta},
    )
    module = PyModule(
        file_path="pkg/models.py",
        module_name="pkg.models",
        types={"pkg.models.Entity": entity},
        functions={"greet": greet},
    )
    _stamp_source(
        module,
        [
            (decorate, "return s.upper()"),
            (greet, "def greet(who): ..."),
            (meta_m, "return 1"),
            (init, "self.x = 1"),
            (describe, "return self.x"),
        ],
    )
    app = PyApplication(symbol_table={"pkg/models.py": module})

    backend = object.__new__(PyCodeanalyzer)
    backend.application = app
    return backend


def test_callables_overview_enumerates_all_callables():
    overviews = {o.signature: o for o in _backend().get_callables_overview()}
    # methods, the module function, the inner class method, and the nested function are all present
    assert set(overviews) == {
        "pkg.models.Entity.__init__",
        "pkg.models.Entity.describe",
        "pkg.models.Entity.Meta.m",
        "pkg.models.greet",
        "pkg.models.greet.<locals>._decorate",
    }


def test_overview_kind_and_owning_class():
    overviews = {o.signature: o for o in _backend().get_callables_overview()}

    describe = overviews["pkg.models.Entity.describe"]
    assert describe.kind == "method"
    assert describe.class_signature == "pkg.models.Entity"
    assert describe.decorators == ["property"]

    inner_method = overviews["pkg.models.Entity.Meta.m"]
    assert inner_method.kind == "method"
    assert inner_method.class_signature == "pkg.models.Entity.Meta"

    greet = overviews["pkg.models.greet"]
    assert greet.kind == "function"
    assert greet.class_signature is None

    nested = overviews["pkg.models.greet.<locals>._decorate"]
    assert nested.kind == "function"
    assert nested.class_signature is None


def test_method_bodies_returns_only_requested_existing():
    bodies = _backend().get_method_bodies(["pkg.models.greet", "pkg.models.Entity.describe", "does.not.exist"])
    assert bodies == {
        "pkg.models.greet": "def greet(who): ...",
        "pkg.models.Entity.describe": "return self.x",
    }


def test_decorated_callables_filters_by_marker():
    backend = _backend()
    routed = backend.get_decorated_callables(["app.route"])
    assert [o.signature for o in routed] == ["pkg.models.greet"]

    props = backend.get_decorated_callables(["property"])
    assert [o.signature for o in props] == ["pkg.models.Entity.describe"]

    assert backend.get_decorated_callables(["nonexistent"]) == []


def test_callsites_for_keys_existing_signatures_only():
    backend = _backend()
    sites = backend.get_callsites_for(
        ["pkg.models.Entity.describe", "pkg.models.greet", "does.not.exist"]
    )
    # both existing callables get a key; the one with no call sites maps to an empty list
    assert set(sites) == {"pkg.models.Entity.describe", "pkg.models.greet"}
    assert [s.method_name for s in sites["pkg.models.Entity.describe"]] == ["greet"]
    assert sites["pkg.models.greet"] == []
