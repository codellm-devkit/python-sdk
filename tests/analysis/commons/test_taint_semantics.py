# tests/analysis/commons/test_taint_semantics.py
from cldk.analysis.commons.graphs import shortest_walks, via_table
from cldk.analysis.commons.results import Diagnostic, TaintResult

VIA = via_table("PY")

#: The shape that separates a correct filter from a plausible one: the SHORTEST route is sanitized
#: and a LONGER one is clean. Filtering the replay alone returns nothing here.
ADJ = {
    "a": {"m": [("PY_DDG", "tainted", ["ssa"])], "n1": [("PY_DDG", "clean", ["ssa"])]},
    "m": {"b": [("PY_DDG", "tainted", ["ssa"])]},
    "n1": {"n2": [("PY_DDG", "clean", ["ssa"])]},
    "n2": {"b": [("PY_DDG", "clean", ["ssa"])]},
}


def test_unfiltered_finds_the_short_route():
    walks = shortest_walks(ADJ, "a", "b", None, 10, via=VIA)
    assert [len(w) for w in walks] == [2]


def test_a_sanitized_shortest_route_does_not_hide_a_clean_longer_one():
    """The local twin of the inlining result: the search must find the shortest *satisfying* walk,
    not filter the shortest walk. If this returns [], the predicate was applied to the replay only
    and every local taint refutation is unsound."""
    walks = shortest_walks(ADJ, "a", "b", None, 10, via=VIA, allow_edge=lambda rel, var: var != "tainted")
    assert [len(w) for w in walks] == [3], "the clean 3-hop route was not found"


def test_a_null_var_hop_is_not_cut_by_a_variable_sanitizer():
    """PARAM_IN carries no var. A predicate that treats None as "not equal to anything" is fine; one
    that treats it as unknown-and-therefore-excluded refutes every interprocedural flow."""
    adj = {"a": {"p": [("PY_DDG", "clean", ["ssa"])]}, "p": {"q": [("PY_PARAM_IN", None, None)]}, "q": {"b": [("PY_DDG", "clean", ["ssa"])]}}
    walks = shortest_walks(adj, "a", "b", None, 10, via=VIA, allow_edge=lambda rel, var: var != "tainted")
    assert [len(w) for w in walks] == [3]


def test_a_node_cut_removes_a_whole_callable():
    """The callable-granular sanitizer: every body node under the callable's id prefix is cut."""
    walks = shortest_walks(ADJ, "a", "b", None, 10, via=VIA, allow_node=lambda nid: not nid.startswith("m"))
    assert [len(w) for w in walks] == [3]


PARALLEL = {"a": {"b": [("PY_DDG", "tainted", ["ssa"]), ("PY_DDG", "clean", ["ssa"])]}}


def test_a_sanitized_parallel_edge_is_not_reported_as_evidence():
    """The mirror of ``test_a_sanitized_shortest_route_does_not_hide_a_clean_longer_one``, and the
    two only make sense read together: that test guards the BFS half against a false *refutation*
    (a sanitized short route hiding a clean long one); this one guards the DFS replay half against a
    false *confirmation*. Parallel edges between one pair are ordinary (see ``shortest_walks``'s own
    docstring: "one statement feeding one argument on several variables is several distinct paths"),
    so a var-sanitizer can cut one label of a pair and not its sibling. The clean label alone keeps
    ``dist[b]`` at 1 no matter which pass filters, so a BFS-only filter cannot fail this case -- only
    the replay's own filter keeps the sanitized label out of the walk it emits as taint evidence.
    """
    walks = shortest_walks(PARALLEL, "a", "b", None, 10, via=VIA, allow_edge=lambda rel, var: var != "tainted")
    assert [lab[1] for w in walks for _, lab in w] == ["clean"]


def test_a_cut_source_yields_no_walk():
    """A source inside a cut callable yields no walk at all -- checked against ``src`` up front,
    since ``src`` is never itself a ``steps()`` destination for the BFS/DFS filtering to catch. Also
    covers ``dst`` for free: ``b`` is only ever reached as a destination, so this graph's one walk
    disappearing when either endpoint is disallowed exercises both."""
    assert shortest_walks(ADJ, "a", "b", None, 10, via=VIA, allow_node=lambda n: n != "a") == []


def _pair(a="a", b="b"):
    return (a, b)


def test_empty_and_complete_is_an_exhausted_pair():
    """The verdict table's middle row: nothing found, and the search was whole."""
    r = TaintResult(paths=[], complete=True, exhausted=[_pair()], roots=[], resolved="", unresolved=[])
    assert not r                      # BoundedResult makes the payload's truth the list's
    assert _pair() in r.exhausted


def test_empty_and_incomplete_is_not_exhausted():
    """The bottom row: nothing found, but something stopped the search, so no pair may be listed."""
    d = Diagnostic(code="unresolved_dispatch", message="x")
    r = TaintResult(paths=[], complete=False, exhausted=[], roots=[], resolved="", unresolved=[d])
    assert not r
    assert r.exhausted == []


def test_a_result_behaves_as_its_path_list():
    """Inherited from BoundedResult: `for p in result` and `len(result)` are the paths, not fields."""
    r = TaintResult(paths=[], complete=True, exhausted=[], roots=[], resolved="", unresolved=[])
    assert list(r) == [] and len(r) == 0


def test_exhausted_survives_a_round_trip():
    """A triage caller writes the result to JSON and another process reads the verdict back."""
    r = TaintResult(paths=[], complete=True, exhausted=[_pair()], roots=[], resolved="", unresolved=[])
    assert TaintResult.model_validate(r.model_dump()).exhausted == [_pair()]
