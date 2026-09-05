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

from cldk.analysis.python.backend import bounded_subgraph, call_graph_scope
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend

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


# ----------------------------------------------------------------------------------------------
# bounded_subgraph — the shape both backends must produce
# ----------------------------------------------------------------------------------------------
def _diamond() -> nx.DiGraph:
    """a -> b -> d, a -> c -> d, plus c -> b (a back-edge between two nodes at the same depth)
    and an unrelated x -> y component."""
    g = nx.DiGraph()
    g.add_edges_from([("a", "b"), ("b", "d"), ("a", "c"), ("c", "d"), ("c", "b"), ("x", "y")])
    return g


def test_depth_one_is_exactly_the_direct_callees():
    assert set(bounded_subgraph(_diamond(), ["a"], 1).nodes) == {"a", "b", "c"}


def test_depth_bounds_the_walk():
    assert set(bounded_subgraph(_diamond(), ["a"], 2).nodes) == {"a", "b", "c", "d"}


def test_unbounded_depth_is_everything_reachable():
    g = bounded_subgraph(_diamond(), ["a"], None)
    assert set(g.nodes) == {"a", "b", "c", "d"}
    assert "x" not in g, "an unrelated component leaked in"


def test_the_subgraph_is_induced_not_path_only():
    """``c -> b`` connects two nodes the caller can plainly see, so it must be present.

    A path-only answer (the edges traversed on the way out from the root) would drop it, and then
    ``graph.predecessors("b")`` would lie about a graph it had just returned. This is the exact
    shape the Neo4j backend's two-step Cypher is written to reproduce.
    """
    assert ("c", "b") in bounded_subgraph(_diamond(), ["a"], 1).edges


def test_a_root_absent_from_the_graph_contributes_nothing():
    assert bounded_subgraph(_diamond(), ["nope"], 3).number_of_nodes() == 0
    assert set(bounded_subgraph(_diamond(), ["nope", "x"], 1).nodes) == {"x", "y"}


def test_several_roots_union():
    assert set(bounded_subgraph(_diamond(), ["b", "x"], 1).nodes) == {"b", "d", "x", "y"}


def test_the_source_graph_is_not_mutated():
    g = _diamond()
    bounded_subgraph(g, ["a"], 1)
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
            functions={"go": PyCallable(name="go", path=path, signature=f"{name}.go")},
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


def test_local_symbol_table_unknown_path_is_empty():
    assert _local_backend().get_symbol_table(paths=["pkg/nope.py"]) == {}


def test_local_classes_scoped_to_a_module():
    backend = _local_backend()
    assert set(backend.get_all_classes()) == {"pkg.a.Alpha", "pkg.b.Beta"}
    assert set(backend.get_all_classes(module="pkg/a.py")) == {"pkg.a.Alpha"}


def test_local_call_graph_scoped_to_roots():
    backend = _local_backend()
    assert set(backend.get_call_graph(roots=["pkg.a.go"], depth=1).nodes) == {"pkg.a.go", "pkg.b.go"}
    assert set(backend.get_call_graph(roots=["pkg.a.go"]).nodes) == {"pkg.a.go", "pkg.b.go", "pkg.c.go"}


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


def test_neo4j_bounded_call_rows_interpolate_only_an_int():
    backend, seen = _recording_backend(["pkg/a.py"])
    backend.get_call_graph(roots=["pkg.a.go"], depth=3)
    query = seen[0]["query"]
    assert "[:PY_CALLS*0..3]" in query
    assert seen[0]["roots"] == ["pkg.a.go"], "roots must stay a parameter, never interpolated"


def test_neo4j_unbounded_depth_leaves_the_upper_bound_open():
    backend, seen = _recording_backend(["pkg/a.py"])
    backend.get_call_graph(roots=["pkg.a.go"])
    assert "[:PY_CALLS*0..]" in seen[0]["query"]


def test_neo4j_scoped_call_graph_does_not_poison_the_cache():
    backend, _ = _recording_backend(["pkg/a.py"])
    backend.get_call_graph(roots=["pkg.a.go"], depth=1)
    assert backend._call_graph is None, "a scoped result was cached as if it were the whole graph"
