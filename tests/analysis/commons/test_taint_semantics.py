# tests/analysis/commons/test_taint_semantics.py
import pytest

from cldk.analysis.commons.graphs import shortest_walks, via_table
from cldk.analysis.commons.resolve import resolve_sanitizers
from cldk.analysis.commons.results import Diagnostic, TaintResult
from cldk.utils.exceptions.exceptions import SelectorNotInGraph

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
    walks = shortest_walks(ADJ, "a", "b", None, 10, via=VIA, allow_edge=lambda frm, rel, var: var != "tainted")
    assert [len(w) for w in walks] == [3], "the clean 3-hop route was not found"


def test_a_null_var_hop_is_not_cut_by_a_variable_sanitizer():
    """PARAM_IN carries no var. A predicate that treats None as "not equal to anything" is fine; one
    that treats it as unknown-and-therefore-excluded refutes every interprocedural flow."""
    adj = {"a": {"p": [("PY_DDG", "clean", ["ssa"])]}, "p": {"q": [("PY_PARAM_IN", None, None)]}, "q": {"b": [("PY_DDG", "clean", ["ssa"])]}}
    walks = shortest_walks(adj, "a", "b", None, 10, via=VIA, allow_edge=lambda frm, rel, var: var != "tainted")
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
    walks = shortest_walks(PARALLEL, "a", "b", None, 10, via=VIA, allow_edge=lambda frm, rel, var: var != "tainted")
    assert [lab[1] for w in walks for _, lab in w] == ["clean"]


#: The same variable name on two different start nodes -- ``a`` is inside the cut's scope, ``s`` is
#: not. Corrected Ruling B is only observable on a graph like this one; on ``ADJ`` a scoped and an
#: unscoped predicate agree.
SCOPED = {
    "s": {"a": [("PY_DDG", "answer", ["ssa"])]},
    "a": {"b": [("PY_DDG", "answer", ["ssa"])]},
}


def test_a_variable_cut_is_scoped_to_the_start_nodes_it_names():
    """The local mirror of the Cypher predicate's ``startNode(r).id STARTS WITH c.prefix``: a cut on
    ``answer`` written for one callable must not sever the same name elsewhere. Names like
    ``answer``, ``result`` and ``token`` recur across callables in any real program, so an unscoped
    local predicate would over-cut -- and over-cutting is a false refutation, which in triage closes
    a live alert. Only the ``a -> b`` hop is cut here, so the ``s -> b`` walk is 1 hop shorter than
    it can be reached in, i.e. there is no walk at all."""
    kept = shortest_walks(SCOPED, "s", "b", None, 10, via=VIA, allow_edge=lambda frm, rel, var: not (var == "answer" and frm.startswith("a")))
    assert kept == [], "the in-scope hop is the only route, so cutting it leaves nothing"
    survives = shortest_walks(SCOPED, "s", "b", None, 10, via=VIA, allow_edge=lambda frm, rel, var: not (var == "answer" and frm.startswith("z")))
    assert [len(w) for w in survives] == [2], "a cut scoped to a callable this walk never enters must sever nothing"


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


# ----------------------------------------------------------------------------------------------
# Leg 4b, Task 4: resolve_sanitizers() -- T6's shape/resolution agreement, and the four rulings
# that amend the brief (variable existence is checked against edge vars, never resolve_value; the
# variable cut is scoped to `within`; an empty/whitespace variable is refused; shape mismatches
# raise rather than falling back to the other resolver).
# ----------------------------------------------------------------------------------------------


def _node(ref, name):
    from cldk.analysis.commons.results import SliceNode

    return SliceNode(file="h.py", line=3, callable=name, kind="callable", name=name, ref=ref)


def _raises(*_a, **_k):
    raise SelectorNotInGraph("callable", [_a[0] if _a else "?"], 1)


def _unused(*_a, **_k):
    raise AssertionError("this resolver must not be called for this selector's shape")


#: `resolve_callable` resolves `Handler.handle` (the pair's `within`) and `html.escape` (the bare
#: cut); `edge_vars_in` stands in for the domain Ruling A checks against -- the vars an amended
#: `sdg_taint_query` predicate can actually match on edges scoped to a callable, not
#: `resolve_value`'s parameter-only domain.
_HANDLE_REF = "can://app/python/h.py/handle@3:4"
_ESCAPE_REF = "can://app/python/h.py/escape"


def _resolve_callable(name, **_kw):
    if name == "Handler.handle":
        return _node(ref=_HANDLE_REF, name=name)
    if name == "html.escape":
        return _node(ref=_ESCAPE_REF, name=name)
    raise SelectorNotInGraph("callable", [name], 1)


def _edge_vars_in(prefix):
    assert prefix == _HANDLE_REF
    # 'cleaned' is a real PY_DDG edge var that is NOT a formal_in parameter, so it would be missed
    # by resolve_value (measured on the live graph -- see Ruling A). 'token' is on the same graph.
    return {"token", "cleaned", "handler::query"}


def test_a_pair_cuts_a_variable_and_a_bare_name_cuts_a_callable():
    cuts, callables = resolve_sanitizers(
        [("token", "Handler.handle"), "html.escape"],
        resolve_callable=_resolve_callable,
        edge_vars_in=_edge_vars_in,
    )
    assert cuts == [{"var": "token", "prefix": _HANDLE_REF}]
    assert callables == [_ESCAPE_REF]


def test_ruling_a_a_variable_absent_from_resolve_value_but_present_on_an_edge_resolves():
    """'cleaned' is not a formal_in parameter (resolve_value would miss it), but it is a real edge
    var. This is Ruling A's whole point: validating through resolve_value would raise here, and
    that would be a legitimate sanitizer told it does not exist."""
    cuts, _callables = resolve_sanitizers(
        [("cleaned", "Handler.handle")],
        resolve_callable=_resolve_callable,
        edge_vars_in=_edge_vars_in,
    )
    assert cuts == [{"var": "cleaned", "prefix": _HANDLE_REF}]


def test_ruling_b_the_cut_carries_the_within_callables_prefix_for_scoping():
    """The cut is not a bare variable name -- it carries the resolved callable's ref as `prefix`,
    which is what lets the amended predicate scope `startNode(r).id STARTS WITH c.prefix` instead
    of cutting the name everywhere in the application."""
    cuts, _callables = resolve_sanitizers(
        [("token", "Handler.handle")],
        resolve_callable=_resolve_callable,
        edge_vars_in=_edge_vars_in,
    )
    assert cuts == [{"var": "token", "prefix": _HANDLE_REF}]


def test_a_bare_name_that_is_not_a_callable_raises_rather_than_cutting_a_variable():
    """T6. One signature carries two semantics, so the accident of omitting `within` must be loud
    -- silently cutting the other thing is how a caller gets a confident wrong answer."""
    with pytest.raises(SelectorNotInGraph):
        resolve_sanitizers(["token"], resolve_callable=_raises, edge_vars_in=_unused)


def test_a_pair_whose_name_is_a_callable_raises_too():
    """The mirror of the above: a pair is resolved as a pair, never silently treated as a bare
    callable name just because its first element happens to also be one."""
    with pytest.raises(SelectorNotInGraph):
        resolve_sanitizers([("html.escape", "Handler.handle")], resolve_callable=_resolve_callable, edge_vars_in=lambda _p: set())


def test_ruling_c_an_empty_variable_selector_raises():
    """The predicate's `coalesce(r.var, '')` makes '' cut every hop with no var in that callable --
    most control/summary edges. Refused rather than passed through."""
    with pytest.raises(ValueError):
        resolve_sanitizers([("", "Handler.handle")], resolve_callable=_resolve_callable, edge_vars_in=_unused)


def test_ruling_c_a_whitespace_only_variable_selector_raises():
    with pytest.raises(ValueError):
        resolve_sanitizers([("   ", "Handler.handle")], resolve_callable=_resolve_callable, edge_vars_in=_unused)


def test_a_variable_absent_from_every_edge_in_scope_raises():
    """A variable that is not a parameter AND not on any edge scoped to `within` is genuinely
    unresolvable -- not everything is Ruling A's exception."""
    with pytest.raises(SelectorNotInGraph):
        resolve_sanitizers([("nonexistent", "Handler.handle")], resolve_callable=_resolve_callable, edge_vars_in=_edge_vars_in)
