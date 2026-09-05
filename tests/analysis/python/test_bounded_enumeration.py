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

"""The whole-application accessors must cost a *bounded* number of Cypher round trips — and be
avoidable altogether.

``get_symbol_table()`` used to rebuild each module by issuing one query per child collection, per
parent node, all the way down the nesting: 73,669 round trips for this graph's 1,626 modules —
45.3 per module at ~6 ms each, ~440 s of wall clock spent almost entirely waiting. ``get_classes()``
cost 62,435 round trips and 410 s the same way. The database was never slow; it was asked seventy-three thousand
questions.

What this module pins is the *shape* of the cost, not the clock: a bounded, constant number of
statements independent of how many modules, classes and callables the application has. A wall-clock
ceiling belongs in ``test_e2e_neo4j_live.py`` (and is there); a machine with a cold page cache can
make seconds lie, but it cannot make a query count lie.

The second half of the module covers the scoping keywords (leg 1.5 Task 2), which are the other
half of the same problem: collapsing the fan-out made whole-application enumeration affordable,
but ``roots=``/``depth=``/``paths=``/``module=`` are what make it unnecessary. Every keyword
defaults to the unscoped behaviour, and that is asserted too — a widening that changed an existing
answer would be a break, not a widening.

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
from cldk.utils.exceptions import SelectorNotInGraph

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


# =================================================================================================
# Scoping keywords — whole-application enumeration becomes the exception, not the default shape
# =================================================================================================
def test_symbol_table_scoped_to_paths(live_analysis):
    all_mods = live_analysis.get_symbol_table()
    one = next(iter(all_mods))

    scoped = live_analysis.get_symbol_table(paths=[one])

    assert set(scoped) == {one}
    assert scoped[one].model_dump() == all_mods[one].model_dump(), "scoping changed the module it returned"


def test_symbol_table_scoping_narrows_the_prefetch_not_just_the_result(live_analysis):
    """The saving has to be in what is *fetched*, not in what is thrown away afterwards.

    Filtering a full fetch in Python would satisfy the test above and cost exactly as much as the
    unscoped call. So this one measures: one module out of 1,626 must come back in a small
    fraction of the ~12 s the whole application takes. The ceiling is deliberately loose (a factor
    of six above what one module actually costs) because it is guarding an order of magnitude, not
    a stopwatch — a Python-side filter over a full fetch would blow it by 6x, not by 10%.
    """
    import time

    one = next(iter(live_analysis.get_symbol_table(paths=None)))

    started = time.monotonic()
    scoped = live_analysis.get_symbol_table(paths=[one])
    elapsed = time.monotonic() - started

    assert set(scoped) == {one}
    assert elapsed < 3.0, f"one module took {elapsed:.1f}s; the whole application takes ~12s, so this fetched too much"


def test_symbol_table_accepts_an_absolute_path(live_analysis):
    """``paths`` resolves leniently, the same way ``get_python_module`` does."""
    one = next(iter(live_analysis.get_symbol_table()))
    assert set(live_analysis.get_symbol_table(paths=["/somewhere/else/" + one])) == {one}


def test_symbol_table_scoped_to_an_unknown_path_raises(live_analysis):
    """It used to come back ``{}``, which is also what a real-but-empty selection looks like — the
    ambiguous empty the parent spec's D7 calls a defect. The message names the path and stops:
    E8 puts near-miss suggestions out of scope, in the error path as much as in the resolver."""
    with pytest.raises(SelectorNotInGraph, match=r"paths not in graph: 'no/such/module\.py'"):
        live_analysis.get_symbol_table(paths=["no/such/module.py"])


def test_symbol_table_partial_miss_raises_even_though_the_rest_matched(live_analysis):
    one = next(iter(live_analysis.get_symbol_table()))
    with pytest.raises(SelectorNotInGraph, match=r"1 of 2 paths not in graph"):
        live_analysis.get_symbol_table(paths=[one, "no/such/module.py"])


def test_classes_scoped_to_an_unknown_module_raises(live_analysis):
    with pytest.raises(SelectorNotInGraph, match="module not in graph"):
        live_analysis.get_classes(module="no/such/module.py")


def test_classes_scoped_to_a_module(live_analysis):
    table = live_analysis.get_symbol_table()
    path, module = max(table.items(), key=lambda kv: len(kv[1].types))

    scoped = live_analysis.get_classes(module=path)

    assert scoped, "expected the most class-heavy module to declare at least one class"
    assert set(scoped) == set(module.types), "scoped get_classes disagrees with the module's own types"
    assert len(scoped) < len(live_analysis.get_classes()), "one module should not hold every class"


def test_call_graph_scoped_to_roots(live_analysis):
    full = live_analysis.get_call_graph()
    root = max(full.nodes, key=full.out_degree)

    scoped = live_analysis.get_call_graph(roots=[root], depth=2)

    assert root in scoped
    assert scoped.number_of_nodes() > 1, "depth=2 should reach past the root"
    assert scoped.number_of_nodes() < full.number_of_nodes()
    assert set(scoped.edges) <= set(full.edges), "the bounded graph invented an edge"


def test_call_graph_depth_is_a_real_bound(live_analysis):
    """Two hops must reach strictly further than one, and no further than the unbounded answer."""
    full = live_analysis.get_call_graph()
    root = max(full.nodes, key=full.out_degree)

    one, two = live_analysis.get_call_graph(roots=[root], depth=1), live_analysis.get_call_graph(roots=[root], depth=2)

    assert set(one.nodes) <= set(two.nodes) <= set(live_analysis.get_call_graph(roots=[root]).nodes)
    assert set(one.nodes) == {root} | set(full.successors(root)), "depth=1 is not exactly the root's direct callees"


def test_call_graph_unknown_root_raises(live_analysis):
    """An empty graph is now a *meaningful* answer — a root that calls nothing (see below) — so an
    unknown root can no longer be allowed to share it."""
    with pytest.raises(SelectorNotInGraph, match=r"roots not in graph: 'no\.such\.callable'"):
        live_analysis.get_call_graph(roots=["no.such.callable"], depth=3)


def test_call_graph_root_that_calls_nothing_is_a_graph_of_one_node(live_analysis):
    """5,302 of this graph's 19,549 call-graph nodes have out-degree 0. Built from edge rows alone
    the Neo4j answer dropped every one of them, so a leaf root came back as an empty graph while
    the local backend returned the single node it is."""
    full = live_analysis.get_call_graph()
    leaf = next(n for n in full.nodes if full.out_degree(n) == 0)

    scoped = live_analysis.get_call_graph(roots=[leaf], depth=1)

    assert set(scoped.nodes) == {leaf}
    assert scoped.number_of_edges() == 0


def test_a_declared_callable_in_no_call_edge_is_still_a_valid_root(live_analysis):
    """A root is judged against the callable *inventory*, not against edge participation.

    2.9% of this application's callables — 444 of 15,549 — appear in no ``PY_CALLS`` edge in either
    direction, so they are not nodes of a graph built from edges. Validating a root against that
    graph made ``roots=[declared_but_edgeless]`` raise ``SelectorNotInGraph`` for a callable the
    same connection will happily describe, which is the previous fix round's mirror image of the
    ambiguous empty it set out to remove.
    """
    full = live_analysis.get_call_graph()
    isolated = next(o.signature for o in live_analysis.get_callables_overview() if o.signature not in full)

    scoped = live_analysis.get_call_graph(roots=[isolated])

    assert set(scoped.nodes) == {isolated}
    assert scoped.number_of_edges() == 0


def test_call_graph_bounded_answer_is_the_induced_subgraph_of_the_unbounded_one(live_analysis):
    """The whole-graph agreement, asserted against the graph itself rather than a recorded number:
    whatever the walk reaches, the edges among the reached set must be exactly the unbounded
    graph's. This is what fails if the walk continues *through* the external-ghost layer —
    ``:PyExternal`` carries no ``_module`` for a per-hop predicate to anchor on, and has 5,307
    outgoing ``PY_CALLS`` edges here, 5,108 of them to another ghost — because a node reached only
    through a ghost would appear with no edge to justify it. (Not a leak into a *neighbouring*
    application: every ghost id embeds the application name, so ghosts are not shared.)"""
    full = live_analysis.get_call_graph()
    root = max(full.nodes, key=full.out_degree)

    for depth in (1, 2):
        scoped = live_analysis.get_call_graph(roots=[root], depth=depth)
        induced = full.subgraph(scoped.nodes)
        assert set(scoped.edges) == set(induced.edges), f"depth={depth} is not the induced sub-graph"


def test_unscoped_calls_are_unaffected(live_analysis):
    """The widening must not change existing behaviour."""
    assert len(live_analysis.get_symbol_table()) == len(live_analysis.get_symbol_table(paths=None))
    assert len(live_analysis.get_classes()) == len(live_analysis.get_classes(module=None))
    assert live_analysis.get_call_graph().number_of_edges() == live_analysis.get_call_graph(roots=None, depth=None).number_of_edges()


# ---------------------------------------------------------------------------------------------
# Addressing (leg 1.5 Task 3, #320): an id the SDK hands out must name something, and the two
# vocabularies a caller is given for "where is this" must be the same vocabulary.
# ---------------------------------------------------------------------------------------------


def test_locate_node_id_joins_to_the_graph(live_analysis):
    """#320: the SDK composed ``signature@key``; the graph mints a ``can://`` path.

    Composing an id from ``signature`` reimplements the analyzer's id grammar with the wrong
    field — ``PyCallable`` carries both ``signature`` (dotted module path) and ``id`` (the
    ``can://`` containment path), and only the latter is what ``:PyBodyNode.id`` is built from.
    The two namespaces do not join, so a ``node_id`` from ``locate()`` named nothing.
    """
    r = live_analysis.locate("addons/onboarding/models/onboarding_onboarding_step.py", 51)
    assert r.node_id, "a position inside a callable has a node id"
    rows = live_analysis.backend._run("MATCH (b:PyBodyNode {id: $nid}) RETURN count(b) AS n", nid=r.node_id)
    assert rows[0]["n"] == 1, f"node_id {r.node_id!r} does not name a node in the graph"


@pytest.mark.xfail(
    reason=(
        "Leg 1.5 Task 3 step 4 stopped short of normalising PyCallableOverview.path: a "
        "pre-existing test — test_e2e_neo4j_live.test_overview_path_is_absolute_unlike_locate_"
        "and_class_overview — deliberately pins the absolute form as a recorded divergence, and "
        "docs/agent-api-reference.md documents it as a gotcha. Normalising the path means "
        "retiring that pin, which is a contract decision, not a test edit. Recorded here so the "
        "defect stays visible and this turns green the day it is made."
    ),
    strict=True,
)
def test_paths_share_one_vocabulary(live_analysis):
    """``PyCallable.path`` is absolute and host-specific; module paths are repo-relative.

    An overview path carrying ``/Users/<someone>/…`` cannot be joined against
    ``get_symbol_table()``'s keys or ``locate().module.path``, and does not exist on any machine
    but the one the analysis ran on.
    """
    ov = live_analysis.get_callables_overview()[0]
    assert not ov.path.startswith("/"), f"overview path {ov.path!r} is absolute — it embeds the analysis machine's layout"
    st = live_analysis.get_symbol_table()
    assert ov.path in st or any(m.endswith(ov.path) for m in st), "an overview path should address a module in the symbol table"
