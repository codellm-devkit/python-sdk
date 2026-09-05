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

"""Scoping-keyword semantics, with no server and no analyzer run.

``tests/analysis/python/test_bounded_enumeration.py`` proves the keywords work against a real
graph, but it skips without one — so the parts that are pure logic are pinned here instead, where
they run in the default suite: the shared normaliser both backends route through, the induced
sub-graph shape both backends must produce, and the local backend's own filtering.

The Cypher push-down cannot be checked here (that needs a graph), but what *can* be checked here
is that the Neo4j backend narrows its **prefetch scope** and not merely its result — filtering a
full fetch in Python would be a keyword that costs exactly as much as no keyword at all.
"""

from __future__ import annotations

from typing import Any, Dict, List

import networkx as nx
import pytest
from codeanalyzer.schema.py_schema import PyApplication, PyCallable, PyClass, PyModule

from cldk.analysis.python.backend import bounded_subgraph, call_graph_scope, scope_paths
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.utils.exceptions import CodeanalyzerExecutionException, SelectorNotInGraph

# ----------------------------------------------------------------------------------------------
# call_graph_scope — the semantics both backends share
# ----------------------------------------------------------------------------------------------
def test_no_keywords_means_the_whole_application():
    assert call_graph_scope(None, None) is None


def test_roots_are_normalised_to_a_list():
    assert call_graph_scope(("a", "b"), None) == ["a", "b"]


def test_depth_without_roots_is_rejected_rather_than_ignored():
    """Silently returning 364,752 edges to a caller who asked for a bounded graph is the worst
    available answer — worse than the error, because it looks like it worked."""
    with pytest.raises(ValueError, match="requires roots"):
        call_graph_scope(None, 2)


@pytest.mark.parametrize("depth", [0, -1])
def test_depth_below_one_is_rejected(depth):
    with pytest.raises(ValueError, match="depth must be"):
        call_graph_scope(["a"], depth)


@pytest.mark.parametrize("depth", ["2", 2.5, 2.0, True, None.__class__])
def test_depth_that_is_not_an_int_is_rejected_as_a_value_error(depth):
    """``depth`` is range-checked *and* type-checked, and both failures read the same way.

    ``"2"`` used to raise ``TypeError`` from the ``<`` comparison — an error the accessor does not
    document — and ``2.5`` used to be accepted and silently truncated to 2 by the ego-graph radius
    and the Cypher quantifier alike, which is a bound the caller did not ask for. ``True`` is an
    ``int`` to Python and would have meant ``depth=1`` by accident.
    """
    with pytest.raises(ValueError, match="depth must be"):
        call_graph_scope(["a"], depth)


# ----------------------------------------------------------------------------------------------
# check_selector — an unresolvable selector raises on both backends, and names what missed
# ----------------------------------------------------------------------------------------------
def test_an_empty_roots_sequence_is_a_caller_bug_not_the_whole_application():
    """``roots=[]`` selected nothing while missing nothing; the argument that means "everything"
    is the argument omitted. Same ruling as ``depth=`` without ``roots=``."""
    with pytest.raises(ValueError, match="omit it"):
        call_graph_scope([], None)


def test_an_empty_paths_sequence_is_a_caller_bug_too():
    with pytest.raises(ValueError, match="omit it"):
        scope_paths([], ["pkg/a.py"])


def test_an_unknown_path_raises_naming_it():
    with pytest.raises(SelectorNotInGraph, match=r"1 of 1 paths not in graph: 'pkg/nope\.py'"):
        scope_paths(["pkg/nope.py"], ["pkg/a.py"])


def test_a_partial_miss_raises_and_says_which_of_how_many():
    """The strict reading: returning the half that matched makes a result whose size the caller
    cannot check against what it asked for."""
    with pytest.raises(SelectorNotInGraph, match=r"1 of 2 paths not in graph: 'gone\.py'"):
        scope_paths(["pkg/a.py", "gone.py"], ["pkg/a.py"])


def test_the_error_reports_the_keyword_the_caller_actually_wrote():
    """``get_classes(module=...)`` resolves through the path machinery but is not ``paths=``."""
    with pytest.raises(SelectorNotInGraph, match="module not in graph"):
        scope_paths(["nope"], ["pkg/a.py"], kind="module")


def test_the_error_carries_the_miss_as_data_not_only_as_prose():
    with pytest.raises(SelectorNotInGraph) as excinfo:
        scope_paths(["pkg/a.py", "gone.py"], ["pkg/a.py"])
    assert (excinfo.value.kind, excinfo.value.missing, excinfo.value.requested) == ("paths", ["gone.py"], 2)


def test_the_error_offers_no_near_miss_suggestions():
    """E8 puts typo-tolerant matching out of scope "not in the resolver, not in the error path" —
    a suggestion is a guess, and a guess presented as a correction is the failure mode this design
    exists to prevent. ``pkg/a.py`` is one character from the miss and must not be mentioned."""
    with pytest.raises(SelectorNotInGraph) as excinfo:
        scope_paths(["pkg/b.py"], ["pkg/a.py"])
    assert "pkg/a.py" not in str(excinfo.value)


def test_a_lenient_match_is_not_a_miss():
    assert scope_paths(["/abs/prefix/pkg/a.py"], ["pkg/a.py"]) == ["pkg/a.py"]


# ----------------------------------------------------------------------------------------------
# bounded_subgraph — the shape both backends must produce
# ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda: call_graph_scope("pkg.mod.fn", None),
        lambda: scope_paths("pkg/mod.py", ["pkg/mod.py"]),
    ],
    ids=["roots", "paths"],
)
def test_a_bare_string_selector_is_rejected_rather_than_iterated(call):
    """A string is a sequence — of characters — so ``paths='pkg/mod.py'`` used to come back as
    ``10 of 10 paths not in graph: 'p', 'k', 'g', '/', ...``. It is the likely mistake because the
    sibling keyword ``module=`` genuinely is single-valued."""
    with pytest.raises(TypeError, match="not a string"):
        call()


# ---- Fix 7: resolution is many-to-one ----------------------------------------------------------
def test_two_spellings_of_one_module_resolve_to_one_key_once():
    """Leniency is the point of :func:`resolve_module_key`, so two spellings of the same file are
    not a caller error — but the resolved list must not name that module twice and ask both
    backends to fetch it twice."""
    assert scope_paths(["pkg/a.py", "/abs/pkg/a.py"], ["pkg/a.py", "pkg/b.py"]) == ["pkg/a.py"]


def _diamond() -> nx.DiGraph:
    """a -> b -> d, a -> c -> d, plus c -> b (a back-edge between two nodes at the same depth)
    and an unrelated x -> y component."""
    g = nx.DiGraph()
    g.add_edges_from([("a", "b"), ("b", "d"), ("a", "c"), ("c", "d"), ("c", "b"), ("x", "y")])
    return g


def test_depth_one_is_exactly_the_direct_callees():
    assert set(bounded_subgraph(_diamond(), ["a"], 1, ()).nodes) == {"a", "b", "c"}


def test_depth_bounds_the_walk():
    assert set(bounded_subgraph(_diamond(), ["a"], 2, ()).nodes) == {"a", "b", "c", "d"}


def test_unbounded_depth_is_everything_reachable():
    g = bounded_subgraph(_diamond(), ["a"], None, ())
    assert set(g.nodes) == {"a", "b", "c", "d"}
    assert "x" not in g, "an unrelated component leaked in"


def test_the_subgraph_is_induced_not_path_only():
    """``c -> b`` connects two nodes the caller can plainly see, so it must be present.

    A path-only answer (the edges traversed on the way out from the root) would drop it, and then
    ``graph.predecessors("b")`` would lie about a graph it had just returned. This is the exact
    shape the Neo4j backend's two-step Cypher is written to reproduce.
    """
    assert ("c", "b") in bounded_subgraph(_diamond(), ["a"], 1, ()).edges


def test_a_root_absent_from_the_graph_raises_rather_than_returning_an_empty_graph():
    """It used to contribute nothing, which made "no such callable" and "a callable that calls
    nothing" the same empty graph — the ambiguous empty D7 calls a defect. A partial miss raises
    too, so a caller cannot read a short answer as a complete one."""
    with pytest.raises(SelectorNotInGraph, match=r"roots not in graph: 'nope'"):
        bounded_subgraph(_diamond(), ["nope"], 3, ())
    with pytest.raises(SelectorNotInGraph, match=r"1 of 2 roots not in graph"):
        bounded_subgraph(_diamond(), ["nope", "x"], 1, ())


def test_a_root_that_calls_nothing_is_a_graph_of_one_node():
    """The answer an unknown root no longer collides with: ``d`` is a real callable with no
    outgoing edges, and the honest answer is the one node it is, not nothing."""
    assert set(bounded_subgraph(_diamond(), ["d"], 3, ()).nodes) == {"d"}


def test_a_declared_callable_in_no_edge_is_still_a_valid_root():
    """The mirror image of the bug above, and the one the previous fix round introduced.

    ``_diamond()`` has no such node, because a graph built from edges cannot have one — which is
    precisely why nothing caught this. ``lonely`` is declared by the application and appears in no
    ``PY_CALLS`` edge in either direction (444 of the live graph's 15,549 in-scope callables), so
    validating against the graph raised for a callable that plainly exists, while the Neo4j
    backend — matching roots by node label — returned the one-node graph.
    """
    g = bounded_subgraph(_diamond(), ["lonely"], 3, {"lonely"})
    assert set(g.nodes) == {"lonely"}
    assert g.number_of_edges() == 0


def test_the_inventory_does_not_leak_into_the_returned_graph():
    """The graph stays edge-induced: only the roots asked for are added back, never the whole
    inventory. Seeding all declared callables would make the unbounded local graph disagree with
    Neo4j's node-for-node — a bigger parity defect than the one being fixed."""
    g = bounded_subgraph(_diamond(), ["a"], 1, {"lonely", "other", "a"})
    assert set(g.nodes) == {"a", "b", "c"}


def test_a_root_in_neither_the_graph_nor_the_inventory_still_raises():
    with pytest.raises(SelectorNotInGraph, match=r"roots not in graph: 'nope'"):
        bounded_subgraph(_diamond(), ["nope"], 3, {"lonely"})


def test_several_roots_union():
    assert set(bounded_subgraph(_diamond(), ["b", "x"], 1, ()).nodes) == {"b", "d", "x", "y"}


def test_the_source_graph_is_not_mutated():
    g = _diamond()
    bounded_subgraph(g, ["a"], 1, ())
    assert g.number_of_nodes() == 6


# ----------------------------------------------------------------------------------------------
# Local backend — filtering the in-memory structures
# ----------------------------------------------------------------------------------------------
def _local_backend() -> PyCodeanalyzer:
    """Two modules, one class each, bypassing the analyzer run."""

    def module(path: str, name: str, cls: str) -> PyModule:
        return PyModule(
            file_path=path,
            module_name=name,
            types={cls: PyClass(name=cls.rsplit(".", 1)[-1], signature=cls, path=path)},
            functions={
                "go": PyCallable(name="go", path=path, signature=f"{name}.go"),
                # Declared, and in no call edge in either direction — the 2.9% case (444 of the
                # live graph's 15,549 callables) that the edge-induced call graph has no node for.
                "lonely": PyCallable(name="lonely", path=path, signature=f"{name}.lonely"),
            },
        )

    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(
        symbol_table={
            "pkg/a.py": module("pkg/a.py", "pkg.a", "pkg.a.Alpha"),
            "pkg/b.py": module("pkg/b.py", "pkg.b", "pkg.b.Beta"),
        }
    )
    backend.call_graph = nx.DiGraph()
    backend.call_graph.add_edges_from([("pkg.a.go", "pkg.b.go"), ("pkg.b.go", "pkg.c.go")])
    return backend


def test_local_symbol_table_unscoped_returns_everything():
    assert set(_local_backend().get_symbol_table()) == {"pkg/a.py", "pkg/b.py"}


def test_local_symbol_table_scoped_to_paths():
    assert set(_local_backend().get_symbol_table(paths=["pkg/b.py"])) == {"pkg/b.py"}


def test_local_symbol_table_resolves_a_path_leniently():
    assert set(_local_backend().get_symbol_table(paths=["/abs/prefix/pkg/b.py"])) == {"pkg/b.py"}


def test_local_symbol_table_unknown_path_raises():
    with pytest.raises(SelectorNotInGraph, match="paths not in graph"):
        _local_backend().get_symbol_table(paths=["pkg/nope.py"])


def test_local_classes_unknown_module_raises_naming_the_module_keyword():
    with pytest.raises(SelectorNotInGraph, match="module not in graph"):
        _local_backend().get_all_classes(module="pkg/nope.py")


def test_local_classes_scoped_to_a_module():
    backend = _local_backend()
    assert set(backend.get_all_classes()) == {"pkg.a.Alpha", "pkg.b.Beta"}
    assert set(backend.get_all_classes(module="pkg/a.py")) == {"pkg.a.Alpha"}


def test_local_call_graph_scoped_to_roots():
    backend = _local_backend()
    assert set(backend.get_call_graph(roots=["pkg.a.go"], depth=1).nodes) == {"pkg.a.go", "pkg.b.go"}
    assert set(backend.get_call_graph(roots=["pkg.a.go"]).nodes) == {"pkg.a.go", "pkg.b.go", "pkg.c.go"}


def test_an_isolated_declared_callable_is_one_node_on_both_backends():
    """The parity this leg's previous fix round broke, asserted end to end rather than on the
    shared helper: ``pkg.a.lonely`` is declared, is in no call edge, and must come back as itself.

    The Neo4j half replays the row its Cypher really produces for such a root — the quantifier
    starts at 0, so the root matches by label and the ``OPTIONAL MATCH`` yields a null ``tgt``.
    """
    local = _local_backend().get_call_graph(roots=["pkg.a.lonely"])
    neo, _ = _rows_backend([{"src": "pkg.a.lonely", "tgt": None, "p": None}], ["pkg/a.py"])
    remote = neo.get_call_graph(roots=["pkg.a.lonely"])
    assert set(local.nodes) == set(remote.nodes) == {"pkg.a.lonely"}
    assert local.number_of_edges() == remote.number_of_edges() == 0


def test_an_undeclared_root_fails_identically_on_both_backends():
    """The other half of the same domain: a name neither backend knows raises the same way, with
    the same message. Only the *set* being checked changed, not the check."""
    neo, _ = _rows_backend([], ["pkg/a.py"])
    for call in (lambda: _local_backend().get_call_graph(roots=["pkg.a.nope"]), lambda: neo.get_call_graph(roots=["pkg.a.nope"])):
        with pytest.raises(SelectorNotInGraph, match=r"1 of 1 roots not in graph: 'pkg\.a\.nope'"):
            call()


def test_local_unscoped_call_graph_is_still_the_cached_object():
    """The unscoped call must keep returning the graph itself, not a copy — ``get_all_callers`` and
    friends read it, and a scoped call must not have replaced the cache with a subgraph."""
    backend = _local_backend()
    whole = backend.get_call_graph()
    backend.get_call_graph(roots=["pkg.b.go"], depth=1)
    assert backend.get_call_graph() is whole


# ----------------------------------------------------------------------------------------------
# Neo4j backend — the keywords must narrow the prefetch, not just the result
# ----------------------------------------------------------------------------------------------
def _recording_backend(modules: List[str]) -> tuple[PyNeo4jBackend, List[Dict[str, Any]]]:
    """A bare backend whose ``_run`` records every statement and returns no rows."""
    seen: List[Dict[str, Any]] = []

    def run(query: str, **params: Any) -> List[Dict[str, Any]]:
        seen.append({"query": query, **params})
        return []

    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "app"
    backend._database = None
    backend._driver = None
    backend._session_obj = None
    backend._modules = list(modules)
    backend._call_graph = None
    backend._server_version = None  # unknown: _bounded_call_rows must not gate on a version it never read
    backend._run = run
    return backend, seen


def test_neo4j_symbol_table_passes_the_resolved_paths_to_cypher():
    backend, seen = _recording_backend(["pkg/a.py", "pkg/b.py"])
    backend.get_symbol_table(paths=["/abs/pkg/b.py"])
    assert seen[0]["paths"] == ["pkg/b.py"], "the path was not resolved, or not pushed into the query"
    assert "m.file_key IN $paths" in seen[0]["query"]


def test_neo4j_unscoped_symbol_table_still_asks_for_everything():
    backend, seen = _recording_backend(["pkg/a.py"])
    backend.get_symbol_table()
    assert seen[0]["paths"] is None, "the unscoped call must not have grown a filter"


def test_neo4j_scoped_prefetch_does_not_fetch_the_whole_application():
    """The bulk child statements must be issued for the requested module only.

    This is the assertion that distinguishes a real scoping keyword from a Python-side filter over
    a full fetch: both return the same rows, but only one of them avoids dragging the other 1,625
    modules' call sites across the wire.
    """
    backend, seen = _recording_backend(["pkg/a.py", "pkg/b.py"])
    with backend._bulk(["pkg/b.py"]):
        backend._children("class_methods", "sig", "unused")
    assert [s["mods"] for s in seen] == [["pkg/b.py"]]


def test_neo4j_bulk_scope_is_the_application_by_default():
    backend, seen = _recording_backend(["pkg/a.py", "pkg/b.py"])
    with backend._bulk():
        backend._children("class_methods", "sig", "unused")
    assert seen[0]["mods"] == ["pkg/a.py", "pkg/b.py"]


def test_neo4j_nested_bulk_keeps_the_outer_scope():
    """An inner block must not renarrow: the outer block's buckets are already indexed as complete,
    and serving a half-filled one from them would silently drop children."""
    backend, seen = _recording_backend(["pkg/a.py", "pkg/b.py"])
    with backend._bulk():
        with backend._bulk(["pkg/b.py"]):
            backend._children("class_methods", "sig", "unused")
    assert seen[0]["mods"] == ["pkg/a.py", "pkg/b.py"]


def _rows_backend(rows: List[Dict[str, Any]], modules: List[str]) -> tuple[PyNeo4jBackend, List[Dict[str, Any]]]:
    """A bare backend whose ``_run`` records every statement and replays ``rows`` for the bounded
    call-graph query (recognised by its ``$roots`` parameter)."""
    backend, seen = _recording_backend(modules)
    recording = backend._run

    def run(query: str, **params: Any) -> List[Dict[str, Any]]:
        recording(query, **params)
        return rows if "roots" in params else []

    backend._run = run
    return backend, seen


def test_neo4j_bounded_call_rows_interpolate_only_an_int():
    backend, seen = _recording_backend(["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph):  # the fake driver returns no rows, so the root missed
        backend.get_call_graph(roots=["pkg.a.go"], depth=3)
    query = seen[0]["query"]
    assert "{0,3}" in query
    assert seen[0]["roots"] == ["pkg.a.go"], "roots must stay a parameter, never interpolated"


def test_neo4j_unbounded_depth_leaves_the_upper_bound_open():
    backend, seen = _recording_backend(["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph):
        backend.get_call_graph(roots=["pkg.a.go"])
    assert "{0,}" in seen[0]["query"]


def test_neo4j_walk_is_scoped_to_the_application_at_every_hop():
    """Not just at the endpoint. A ``*0..n`` variable-length pattern can only constrain where it
    lands, so the walk could step out through a ``:PyExternal`` ghost — which carries no
    ``_module`` for a per-hop predicate to test, and has 5,307 outgoing ``PY_CALLS`` edges on the
    live graph, 5,108 of them to another ghost — and spend the rest of its hop budget walking the
    ghost layer instead of this application's own callables. Not a hop into a *neighbouring*
    application: every ghost id embeds the application name, so ghosts are not shared."""
    backend, seen = _recording_backend(["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph):
        backend.get_call_graph(roots=["pkg.a.go"], depth=2)
    query = seen[0]["query"]
    assert "(a:PyCallable|PyExternal)-[:PY_CALLS]->(b:PyCallable|PyExternal) WHERE a._module IN $mods" in query
    assert "*0.." not in query, "a variable-length hop cannot carry a per-hop scope predicate"


def test_neo4j_returns_a_reached_node_that_has_no_outgoing_edge():
    """Built from edge rows alone the answer dropped every isolated node — 5,302 of the live
    graph's 19,549 call-graph nodes have out-degree 0 — so a leaf root came back as an *empty*
    graph while the local backend returned the one node it is. The ``OPTIONAL MATCH`` emits a null
    ``tgt`` row for such a node, and that row is what must become a node."""
    backend, _ = _rows_backend([{"src": "pkg.a.go", "tgt": None, "p": None}], ["pkg/a.py"])
    graph = backend.get_call_graph(roots=["pkg.a.go"], depth=1)
    assert set(graph.nodes) == {"pkg.a.go"}
    assert graph.number_of_edges() == 0


def test_neo4j_unknown_root_raises_rather_than_returning_an_empty_graph():
    """The same ruling as the local backend's, reached through the same ``check_selector``: a root
    the graph does not hold contributes no row at all, and no row is now distinguishable from an
    isolated node."""
    backend, _ = _rows_backend([], ["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph, match=r"roots not in graph: 'no\.such\.callable'"):
        backend.get_call_graph(roots=["no.such.callable"], depth=1)


def test_neo4j_unknown_path_raises_before_any_query_is_issued():
    backend, seen = _recording_backend(["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph, match="paths not in graph"):
        backend.get_symbol_table(paths=["pkg/nope.py"])
    assert seen == [], "the miss should be caught by the shared normaliser, not by an empty result"


def test_neo4j_unknown_module_raises_naming_the_module_keyword():
    backend, _ = _recording_backend(["pkg/a.py"])
    with pytest.raises(SelectorNotInGraph, match="module not in graph"):
        backend.get_all_classes(module="pkg/nope.py")


def test_an_old_server_fails_at_the_call_that_needs_5_9_not_at_attach():
    """The quantified path pattern is the *only* thing on this backend needing 5.9, so an older
    server is recorded at attach and refused here — a caller that never asks for a bounded call
    graph keeps working."""
    backend, seen = _recording_backend(["pkg/a.py"])
    backend._server_version = (5, 8, 0)
    with pytest.raises(CodeanalyzerExecutionException, match="5.9 or newer.*reports 5.8.0"):
        backend.get_call_graph(roots=["pkg.a.go"])
    assert seen == [], "the version gate must refuse before issuing the statement"


def test_an_unreadable_server_version_blocks_nothing():
    """``None`` means *unknown*, not *old*: a server that will not answer ``dbms.components()`` is
    not evidence of a pre-5.9 one, and refusing on that basis would be a guess dressed as a check.
    If it really is old, its own parser says so."""
    backend, seen = _rows_backend([{"src": "pkg.a.go", "tgt": None, "p": None}], ["pkg/a.py"])
    backend._server_version = None
    assert set(backend.get_call_graph(roots=["pkg.a.go"]).nodes) == {"pkg.a.go"}
    assert seen, "the statement should have been issued"


def test_neo4j_scoped_call_graph_does_not_poison_the_cache():
    backend, _ = _rows_backend([{"src": "pkg.a.go", "tgt": None, "p": None}], ["pkg/a.py"])
    backend.get_call_graph(roots=["pkg.a.go"], depth=1)
    assert backend._call_graph is None, "a scoped result was cached as if it were the whole graph"
