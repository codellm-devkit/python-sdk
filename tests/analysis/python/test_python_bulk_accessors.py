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

from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyCallsite, PyClass, PyDecorator, PyModule, Span

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer


def _backend():
    """A PyCodeanalyzer wired to a hand-built application, bypassing the analyzer run.

    1.4.0 shape notes (schema v2): ``PyCallable``/``PyClass`` no longer have a ``code`` field —
    source text lives on ``PyModule.source``, sliced per-callable via ``span.bytes`` (UTF-8 byte
    offsets) — so each callable's code snippet is appended to a running module source and its
    span recorded, mirroring what the real analyzer emits. ``decorators`` is now structured
    ``List[PyDecorator]``, not flat strings. Containment fields are also renamed: ``PyModule.
    classes`` -> ``types``, ``PyClass.methods`` -> ``callables``, ``*.inner_classes`` -> ``types``,
    ``PyCallable.inner_callables`` -> ``callables``.
    """
    source_parts: list[str] = []

    def add_code(code: str) -> Span | None:
        if not code:
            return None
        start = len("".join(source_parts).encode("utf-8"))
        source_parts.append(code + "\n")
        return Span(start=(0, 0), end=(0, 0), bytes=(start, start + len(code.encode("utf-8"))))

    def decorator(d):
        # A plain string is name-only; a (name, qualified_name) pair models a decorator Jedi
        # actually resolved -- e.g. Jedi turns `@route` into (name="route",
        # qualified_name="app.route"). Real analyzer output almost always has both, so at least
        # one fixture decorator should too, rather than every one collapsing name==qualified_name.
        return PyDecorator(name=d[0], qualified_name=d[1]) if isinstance(d, tuple) else PyDecorator(name=d)

    def callable_(name, signature, *, code="", decorators=None, inner_callables=None, inner_classes=None, call_sites=None):
        return PyCallable(
            name=name,
            path="pkg/models.py",
            signature=signature,
            span=add_code(code),
            decorators=[decorator(d) for d in (decorators or [])],
            callables=inner_callables or {},
            types=inner_classes or {},
            call_sites=call_sites or [],
        )

    def class_(name, signature, *, methods=None, inner_classes=None):
        return PyClass(name=name, signature=signature, callables=methods or {}, types=inner_classes or {})

    # Non-ASCII body text: exercises add_code()/_code_of()'s UTF-8 encode-slice-decode path
    # (every other fixture snippet here is plain ASCII, which can't tell a byte-offset bug from a
    # character-offset one).
    decorate = callable_("_decorate", "pkg.models.greet.<locals>._decorate", code="return s.upper()  # café")
    greet = callable_(
        "greet",
        "pkg.models.greet",
        code="def greet(who): ...",
        decorators=[("route", "app.route")],
        inner_callables={"_decorate": decorate},
    )
    meta = class_(
        "Meta",
        "pkg.models.Entity.Meta",
        methods={"m": callable_("m", "pkg.models.Entity.Meta.m", code="return 1")},
    )
    entity = class_(
        "Entity",
        "pkg.models.Entity",
        methods={
            "__init__": callable_("__init__", "pkg.models.Entity.__init__", code="self.x = 1"),
            "describe": callable_(
                "describe",
                "pkg.models.Entity.describe",
                code="return self.x",
                decorators=["property"],
                call_sites=[PyCallsite(method_name="greet", start_line=7, start_column=4)],
            ),
        },
        inner_classes={"pkg.models.Entity.Meta": meta},
    )
    module = PyModule(
        file_path="pkg/models.py",
        module_name="pkg.models",
        types={"pkg.models.Entity": entity},
        functions={"greet": greet},
        source="".join(source_parts),
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


def test_method_bodies_round_trips_non_ascii_source():
    """get_method_bodies slices span.bytes (UTF-8 byte offsets) out of the module source -- a
    multi-byte character before a callable would shift a naive character-offset slice, so this
    checks the exact non-ASCII text comes back, not just that something does."""
    bodies = _backend().get_method_bodies(["pkg.models.greet.<locals>._decorate"])
    assert bodies == {"pkg.models.greet.<locals>._decorate": "return s.upper()  # café"}


def test_decorated_callables_matches_by_qualified_name():
    """Parity with the Neo4j backend, whose flat graph property already carries the qualified
    spelling: get_decorated_callables (and get_callables_overview) must match/report a resolved
    decorator's qualified_name, not its bare name."""
    backend = _backend()
    routed = backend.get_decorated_callables(["app.route"])
    assert [o.signature for o in routed] == ["pkg.models.greet"]
    assert routed[0].decorators == ["app.route"]

    # the bare (unqualified) name alone no longer matches, now that it has a distinct qualified_name
    assert backend.get_decorated_callables(["route"]) == []
