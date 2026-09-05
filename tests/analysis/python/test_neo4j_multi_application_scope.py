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

``PyNeo4jBackend`` reconstructs a class's members two ways: the **bulk** path
(``get_all_classes`` inside ``_bulk()``, served from application-wide prefetches) and the
**per-parent** path (``get_class``, one statement per child collection). The live suite cannot
tell them apart because the graph it attaches to holds exactly one application — but the SDK's
premise is attaching to a graph someone else deployed, and a Unified Knowledge Graph holding
several applications is the expected deployment, not an exotic one.

So the two-application graph is a fake driver, not a real write: this suite, like every other
Neo4j test here, never emits ``CREATE``/``MERGE``/``SET``/``DELETE``.

The fixture is deliberately degenerate in the one way that matters: **both applications declare a
top-level class with the same signature**, one with a method ``alpha`` and the other with a method
``beta``. A backend scoped to the first must see ``alpha`` and only ``alpha``, whichever path it
takes. Before the per-parent statements grew their ``WHERE par._module IN $mods`` predicate,
``get_class`` returned both.
"""

from __future__ import annotations

from typing import Any, Dict, List

from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

SHARED_SIG = "shared.Widget"
APP_A_MODULE = "a/mod.py"
APP_B_MODULE = "b/mod.py"

# (props) — one :PyClass node per application, same signature, different owning module.
_CLASSES: List[Dict[str, Any]] = [
    {"signature": SHARED_SIG, "name": "Widget", "path": APP_A_MODULE, "_module": APP_A_MODULE},
    {"signature": SHARED_SIG, "name": "Widget", "path": APP_B_MODULE, "_module": APP_B_MODULE},
]

# (owner signature, owner's _module, method props)
_METHODS: List[tuple[str, str, Dict[str, Any]]] = [
    (SHARED_SIG, APP_A_MODULE, {"signature": "a.Widget.alpha", "name": "alpha", "path": APP_A_MODULE, "_module": APP_A_MODULE}),
    (SHARED_SIG, APP_B_MODULE, {"signature": "b.Widget.beta", "name": "beta", "path": APP_B_MODULE, "_module": APP_B_MODULE}),
]


def _fake_two_app_cypher(query: str, **params: Any) -> List[Dict[str, Any]]:
    """Answer the handful of statements ``get_class`` / ``get_all_classes`` issue, honestly.

    "Honestly" is the whole point: the ``IN $mods`` filter is applied **only when the query
    actually asks for it**, exactly as a real server would. An unscoped statement therefore sees
    both applications' rows, which is what makes this test fail without the fix.
    """
    mods = params.get("mods") or []

    def scope(rows, module_of):
        return [r for r in rows if module_of(r) in mods] if "IN $mods" in query else list(rows)

    if "PY_HAS_METHOD" in query:  # class -> methods, per-parent and bulk
        rows = [m for m in _METHODS if "$sig" not in query or m[0] == params["sig"]]
        rows = scope(rows, lambda m: m[1])
        if "AS pk" in query:  # bulk twin returns the parent key alongside the child
            return [{"pk": sig, "p": props} for sig, _, props in rows]
        return [{"p": props} for _, _, props in rows]
    # module -> top-level classes, per-parent (get_class) and whole-application (get_all_classes).
    # "AS pk" excludes the module_classes bulk bucket and "(ic:PyClass" the inner-class ones —
    # neither has rows in this fixture, so both must fall through to the empty tail.
    if "(c:PyClass" in query and "PY_DECLARES" in query and "(ic:PyClass" not in query and "AS pk" not in query:
        rows = [c for c in _CLASSES if "$sig" not in query or c["signature"] == params["sig"]]
        rows = scope(rows, lambda c: c["_module"])
        return [{"p": c} for c in rows]
    return []  # attributes / inner classes / call sites / locals / inner callables: none here


def _two_app_backend() -> PyNeo4jBackend:
    """A backend scoped to application A over a fake driver holding A and B."""
    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "app_a"
    backend._database = None
    backend._driver = None
    backend._session_obj = None
    backend._modules = [APP_A_MODULE]
    backend._call_graph = None
    backend._run = _fake_two_app_cypher
    return backend


def test_per_parent_path_does_not_leak_another_applications_children():
    """``get_class`` must not merge application B's methods into application A's class."""
    cls = _two_app_backend().get_class(SHARED_SIG)
    assert cls is not None
    assert set(cls.callables) == {"alpha"}, "per-parent child fetch leaked another application's members"


def test_scoped_and_bulk_paths_agree_across_applications():
    """The two paths must answer identically regardless of what else is in the database."""
    backend = _two_app_backend()
    scoped = backend.get_class(SHARED_SIG)
    bulk = backend.get_all_classes()[SHARED_SIG]
    assert scoped is not None
    assert set(scoped.callables) == set(bulk.callables) == {"alpha"}
