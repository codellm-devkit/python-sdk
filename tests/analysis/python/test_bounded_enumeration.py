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

"""The whole-application accessors must cost a *bounded* number of Cypher round trips.

``get_symbol_table()`` used to rebuild each module by issuing one query per child collection, per
parent node, all the way down the nesting: 73,669 round trips for this graph's 1,626 modules —
45.3 per module at ~6 ms each, ~440 s of wall clock spent almost entirely waiting. ``get_classes()``
cost 62,435 round trips and 410 s the same way. The database was never slow; it was asked seventy-three thousand
questions.

What this module pins is the *shape* of the cost, not the clock: a bounded, constant number of
statements independent of how many modules, classes and callables the application has. A wall-clock
ceiling belongs in ``test_e2e_neo4j_live.py`` (and is there); a machine with a cold page cache can
make seconds lie, but it cannot make a query count lie.

Runs only against a live, pre-loaded graph — the same one, and the same environment variables, as
``test_e2e_neo4j_live.py`` (see ``conftest.py``'s ``live_analysis``). Strictly read-only::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7688 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=cldkleg1test \
    CLDK_TEST_NEO4J_APP=odoo-slim-19 \
    uv run pytest tests/analysis/python/test_bounded_enumeration.py
"""

from __future__ import annotations

import os
from typing import Iterator, List

import pytest

from cldk.models.python import PyCallable, PyClass

pytestmark = pytest.mark.skipif(
    not os.environ.get("CLDK_TEST_NEO4J_URI"),
    reason="no live Neo4j (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)",
)

# Eleven child collections plus the one driving query is the collapsed cost; twenty leaves room for
# a query to be added without a false alarm while still being three orders of magnitude below the
# N+1 it replaces. This is a *shape* assertion — if it ever needs raising by more than a couple,
# something has started scaling with the application again.
_ROUND_TRIP_CEILING = 20


def _walk_callables(callables: dict[str, PyCallable]) -> Iterator[PyCallable]:
    """Every callable in a declaration tree, nested ones included."""
    for c in callables.values():
        yield c
        yield from _walk_callables(c.callables)
        yield from _walk_classes(c.types)


def _walk_classes(classes: dict[str, PyClass]) -> Iterator[PyClass]:
    """Every class in a declaration tree, inner ones included."""
    for c in classes.values():
        yield c
        yield from _walk_classes(c.types)
        for m in c.callables.values():
            yield from _walk_classes(m.types)


def _assert_children_survived(classes: List[PyClass], callables: List[PyCallable]) -> None:
    """A bounded query count is worthless if it is bounded because nothing came back.

    Each of these witnesses one of the collapsed child collections at a *different* depth of the
    reconstruction, so a bucket that silently returned no rows — or was keyed on the wrong parent
    property — fails here rather than passing as a very fast empty answer.
    """
    assert any(c.attributes for c in classes), "no class attributes survived the collapse"
    assert any(c.callables for c in classes), "no methods survived the collapse"
    assert any(m.call_sites for m in callables), "no call sites survived the collapse"
    assert any(m.local_variables for m in callables), "no local variables survived the collapse"
    assert any(m.callables for m in callables), "no nested callables survived the collapse"


def test_symbol_table_is_not_n_plus_one(live_analysis, count_round_trips):
    n = count_round_trips(live_analysis)
    table = live_analysis.get_symbol_table()

    assert len(table) > 1000, "expected a real application"
    assert n["c"] < _ROUND_TRIP_CEILING, f"symbol table cost {n['c']} round trips; was 73,669 before the collapse"

    classes = [c for m in table.values() for c in _walk_classes(m.types)]
    callables = [f for m in table.values() for f in _walk_callables(m.functions)]
    callables += [m for c in classes for m in _walk_callables(c.callables)]
    _assert_children_survived(classes, callables)
    assert any(m.imports for m in table.values()), "no imports survived the collapse"
    assert any(m.variables for m in table.values()), "no module variables survived the collapse"


def test_classes_is_not_n_plus_one(live_analysis, count_round_trips):
    n = count_round_trips(live_analysis)
    classes = live_analysis.get_classes()

    assert len(classes) > 1000
    assert n["c"] < _ROUND_TRIP_CEILING, f"get_classes cost {n['c']} round trips; was 62,435 before the collapse"

    all_classes = [c for top in classes.values() for c in _walk_classes({top.signature: top})]
    _assert_children_survived(all_classes, [m for c in all_classes for m in _walk_callables(c.callables)])


def test_scoped_and_bulk_paths_reconstruct_identically(live_analysis, count_round_trips):
    """The two child-fetch paths must produce the same object.

    ``get_classes`` reads its children from the application-wide prefetch; ``get_class`` reads the
    same children with the per-parent scoped queries, because prefetching every call site in the
    application to answer about one class would trade an N+1 for a much larger constant. That is
    two sources feeding one reconstruction, so this pins them to the same answer — on the most
    deeply populated class in the graph, which exercises methods, attributes, call sites, locals
    and nesting at once — and pins the scoped path to a cost bounded by that one class's own
    children rather than by the application's.
    """
    from_bulk = max(live_analysis.get_classes().values(), key=lambda c: len(c.callables))

    n = count_round_trips(live_analysis)
    from_scoped = live_analysis.get_class(from_bulk.signature)

    assert from_scoped is not None
    assert from_scoped.model_dump() == from_bulk.model_dump(), "the scoped path and the bulk path disagree"

    # One lookup, then at most four child queries per node of this one class's own declaration
    # tree — bounded by the class, never by the application.
    tree = list(_walk_classes({from_bulk.signature: from_bulk}))
    tree_size = len(tree) + sum(len(list(_walk_callables(c.callables))) for c in tree)
    assert n["c"] <= 1 + 4 * tree_size, f"the scoped path cost {n['c']} round trips for a {tree_size}-node class"
