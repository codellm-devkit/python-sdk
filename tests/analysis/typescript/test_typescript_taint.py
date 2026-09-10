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

"""``taint()``'s ordered contract on the TypeScript backend ABC -- offline, no analyzer and no graph.

Everything ``taint()`` does before and after the walk is policy that lives on the ABC, so it is
testable against a backend whose walk only records what it was asked. What is pinned here is the
*order* of the checks (a malformed bound is judged before a name is looked up, a sanitizer before
the walk), the two deliberate divergences from ``paths_between`` (a same-position pair is skipped
rather than raised; a bounded ``depth`` yields no ``exhausted`` pair), and the three membership
conditions of ``exhausted``.

The last group is the **graph** backend's half of the walk, over a fake driver rather than a server.
There is no live TypeScript graph in this repo's verification set, so what a fake driver can prove is
bounded and worth saying plainly: the statement text is the one ``sdg_taint_query`` built, the
parameters are bound as the contract says (``cap = max_paths + 1``, the application scope, the two
cut lists), and a row is translated into a witness the way ``paths_between``'s rows are. It proves
nothing about what Cypher *does* with that statement -- that the cut inlines into ``ShortestPath``,
that ``allShortestPaths`` returns what the design assumes. Only
``tests/analysis/python/test_python_taint_live.py`` proves that, and only for Python.
"""

import pytest

from cldk.analysis.commons.results import Diagnostic, FlowPath, PathHop, SliceNode
from cldk.analysis.typescript.backend import SDG_REL_PATTERN, TSAnalysisBackend
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.utils.exceptions.exceptions import SelectorNotInGraph

from .conftest import FakeDriver


def _value(name: str, within: str) -> SliceNode:
    """A resolved value, addressed as this surface addresses one: a name plus the callable it enters."""
    return SliceNode(file="app.ts", line=1, callable=within, kind="parameter", name=name, ref=f"can://app/typescript/app.ts/{within}#{name}")


def _witness(frm: SliceNode, to: SliceNode) -> FlowPath:
    return FlowPath(hops=[PathHop(frm=frm, to=to, via="data", var="answer", prov=["reaching-defs"])])


class _Recording(TSAnalysisBackend):
    """The contract with only the four methods ``taint()`` touches, and a walk that records its call.

    ``__abstractmethods__`` is cleared below rather than the other fifty-odd methods being stubbed:
    what is under test is one concrete body, and anything else this backend could answer would only
    be a way for these tests to fail for an unrelated reason.
    """

    def __init__(self, rows=(), blocked=None, edge_vars=("answer",)):
        self._rows = list(rows)
        self._blocked = dict(blocked or {})
        self._edge_vars = set(edge_vars)
        self.resolved = []
        self.walks = []

    def resolve_value(self, name, *, within):
        self.resolved.append((name, within))
        return _value(name, within)

    def resolve_callable(self, name, *, in_class=None, in_module=None):
        return SliceNode(file="app.ts", line=1, callable=name, kind="callable", name=None, ref=f"can://app/typescript/app.ts/{name}")

    def _edge_vars_in(self, callable_id):
        return self._edge_vars

    def _taint_walk(self, srcs, dsts, *, cuts, cut_callables, depth, max_paths):
        self.walks.append({"srcs": [n.ref for n in srcs], "dsts": [n.ref for n in dsts], "cuts": cuts, "cut_callables": cut_callables, "depth": depth, "max_paths": max_paths})
        return self._rows, self._blocked


_Recording.__abstractmethods__ = frozenset()


def test_the_arguments_are_judged_before_any_resolution():
    """A malformed bound is a ``ValueError`` before a name is looked up, as on every sibling
    accessor: a typo in ``depth`` must not cost a round trip, and must not be reported second."""
    backend = _Recording()
    with pytest.raises(ValueError, match="depth"):
        backend.taint([("x", "f")], [("y", "g")], depth=0)
    with pytest.raises(ValueError, match="max_paths"):
        backend.taint([("x", "f")], [("y", "g")], max_paths=0)
    assert backend.resolved == [] and backend.walks == []


def test_empty_sources_or_sinks_is_refused_not_answered_empty():
    """``cone_sinks`` already refuses the two ways of naming nothing, and here the stakes are the
    reason: an empty answer would be indistinguishable from a refutation."""
    backend = _Recording()
    with pytest.raises(ValueError):
        backend.taint([], [("y", "g")])
    with pytest.raises(ValueError):
        backend.taint([("x", "f")], [])
    assert backend.walks == []


def test_a_bare_string_is_refused_rather_than_unpacked_into_a_pair():
    """``sources="xy"`` is a sequence -- of two characters -- so it would unpack to the pair
    ``("x", "y")`` and resolve a value nobody named. ``reject_bare_string`` is why it is a
    ``TypeError``."""
    backend = _Recording()
    with pytest.raises(TypeError):
        backend.taint("xy", [("y", "g")])
    with pytest.raises(TypeError):
        backend.taint([("x", "f")], "xy")


def test_a_found_flow_carries_its_witnesses_the_roots_and_the_audit_line():
    a, b = _value("x", "f"), _value("y", "g")
    backend = _Recording(rows=[(a.ref, b.ref, _witness(a, b))])
    result = backend.taint([("x", "f")], [("y", "g")])
    assert len(result.paths) == 1 and result.complete
    assert result.exhausted == [] and result.unresolved == []
    assert [n.ref for n in result.roots] == [a.ref, b.ref]
    assert result.resolved == "f parameter 'x', g parameter 'y'"
    assert backend.walks == [{"srcs": [a.ref], "dsts": [b.ref], "cuts": [], "cut_callables": [], "depth": None, "max_paths": 10}]


def test_a_pair_with_no_route_is_exhausted_only_when_the_search_was_unbounded():
    """Condition 1 of the spec's section 5, a rule and not a tendency: under a bound "no path found"
    is not evidence of absence, so the bounded call reports nothing rather than a refutation a
    caller could close a live alert on."""
    backend = _Recording()
    assert backend.taint([("x", "f")], [("y", "g")]).exhausted == [("x", "y")]
    assert backend.taint([("x", "f")], [("y", "g")], depth=5).exhausted == []


def test_the_same_position_pair_is_skipped_with_a_diagnostic_not_raised():
    """A deliberate divergence from ``paths_between``, which raises via ``check_distinct_endpoints``:
    raising would discard a forty-pair batch for one degenerate pair, and a caller assembling
    sources programmatically will hit that by accident."""
    backend = _Recording()
    result = backend.taint([("x", "f"), ("y", "h")], [("x", "f")])
    assert result.paths == []
    assert any("same position" in d.message for d in result.unresolved)
    assert [d.code for d in result.unresolved] == ["degenerate_pair"], "its own code: nothing was searched, so no match failed"
    assert result.exhausted == [("y", "x")], "the degenerate pair is skipped; the rest of the batch still answers"
    assert not result.complete


def test_a_pair_a_diagnostic_implicates_is_neither_proved_nor_refuted():
    """Condition 3: a pair whose route crossed an unresolved dispatch appears in neither list, and
    the association is the walk's to report -- ``exhausted`` is never computed by reading a message
    back out."""
    a, b = _value("x", "f"), _value("y", "g")
    blocked = Diagnostic(code="unresolved_dispatch", message="'x' in f to 'y' in g crosses an unresolved dispatch")
    backend = _Recording(blocked={(a.ref, b.ref): [blocked]})
    result = backend.taint([("x", "f")], [("y", "g")])
    assert result.exhausted == [] and result.unresolved == [blocked] and not result.complete


def test_a_ledger_entry_no_requested_pair_claims_is_still_reported():
    """An unresolved dispatch belongs to a *callable frontier*, not to one pair, so a walk may key it
    in a way this body does not look up -- a reversed pair, one arm of a frontier, a combination the
    caller never requested. Reading only the requested keys would drop the entry and hand the pair it
    named back as ``exhausted``: a certified refutation of a flow that was in fact blocked, which is
    the one output this accessor exists to refuse."""
    a, b = _value("x", "f"), _value("y", "g")
    stray = Diagnostic(code="unresolved_dispatch", message="'y' in g to 'x' in f crosses an unresolved dispatch")
    result = _Recording(blocked={(b.ref, a.ref): [stray]}).taint([("x", "f")], [("y", "g")])
    assert result.unresolved == [stray], "the entry survives a key no requested pair claimed"
    assert result.exhausted == [], "nothing can attribute a stray key to a pair, so no pair is certified"
    assert not result.complete


def test_paths_are_trimmed_per_pair_and_completeness_says_the_cap_fired():
    """The walk caps each pair at ``max_paths + 1``, so the extra row reports truncation without a
    second counting traversal -- and the trim is per pair, so a prolific pair cannot starve a
    sparse one out of its witness."""
    a, b, c = _value("x", "f"), _value("y", "g"), _value("z", "h")
    rows = [(a.ref, b.ref, _witness(a, b))] * 3 + [(a.ref, c.ref, _witness(a, c))]
    result = _Recording(rows=rows).taint([("x", "f")], [("y", "g"), ("z", "h")], max_paths=2)
    assert len(result.paths) == 3, "two of the prolific pair's three, and the sparse pair's one"
    assert not result.complete and result.exhausted == []
    whole = _Recording(rows=rows).taint([("x", "f")], [("y", "g"), ("z", "h")], max_paths=3)
    assert len(whole.paths) == 4 and whole.complete


def test_two_selectors_that_resolve_to_the_same_position_are_one_pair():
    """``max_paths`` is documented as most witnesses **per pair**, and a pair is a pair of resolved
    *positions*: a caller assembling sources programmatically duplicates an entry by the same
    accident that produces a degenerate pair, and counting it twice returns 2m witnesses for a cap of
    m. ``roots`` already dedups by ``ref``; the verdict does too, keeping the first spelling so
    ``exhausted`` still names what the caller wrote."""
    a, b = _value("x", "f"), _value("y", "g")
    rows = [(a.ref, b.ref, _witness(a, b))] * 3
    once = _Recording(rows=rows[:1]).taint([("x", "f"), ("x", "f")], [("y", "g")])
    assert len(once.paths) == 1 and once.complete, "one distinct pair, one witness"
    capped = _Recording(rows=rows).taint([("x", "f"), ("x", "f")], [("y", "g")], max_paths=2)
    assert len(capped.paths) == 2 and not capped.complete, "the cap holds per distinct pair"
    assert once.exhausted == [] and capped.exhausted == []


def test_the_sanitizer_selectors_are_resolved_through_the_edge_vars_hook():
    """Step 4 of the order: the two shapes reach the walk as ``$cuts`` and ``$cut_callables``, and a
    variable selector is checked against the vars on real SDG edges rather than ``resolve_value``,
    which addresses only parameters."""
    backend = _Recording()
    backend.taint([("x", "f")], [("y", "g")], sanitizers=[("answer", "f"), "scrub"])
    assert backend.walks[0]["cuts"] == [{"var": "answer", "prefix": "can://app/typescript/app.ts/f"}]
    assert backend.walks[0]["cut_callables"] == ["can://app/typescript/app.ts/scrub"]


def test_a_sanitizer_that_does_not_resolve_raises_before_the_walk():
    backend = _Recording(edge_vars=())
    with pytest.raises(SelectorNotInGraph):
        backend.taint([("x", "f")], [("y", "g")], sanitizers=[("answer", "f")])
    assert backend.walks == [], "sanitizers are resolved before the traversal, not applied after it"


def test_complete_is_the_batch_flag_so_one_skipped_pair_flips_it_and_a_bigger_cap_will_not_help():
    """``complete`` is ``not truncated and not ledger``: the whole batch's flag, not the trim's, so an
    otherwise clean batch whose only irregularity is one degenerate pair answers ``False``. A caller
    reading ``FlowPaths.complete``'s inherited text would re-run with a bigger ``max_paths`` and get
    the same flag, so the two are pinned together here -- nothing was truncated, so nothing about the
    cap can change it, and ``unresolved`` is where the reason is."""
    a, b = _value("x", "f"), _value("y", "g")
    rows = [(a.ref, b.ref, _witness(a, b))]
    result = _Recording(rows=rows).taint([("x", "f"), ("y", "g")], [("y", "g")])
    assert len(result.paths) == 1 and not result.complete
    assert [d.code for d in result.unresolved] == ["degenerate_pair"]
    bigger = _Recording(rows=rows).taint([("x", "f"), ("y", "g")], [("y", "g")], max_paths=99)
    assert len(bigger.paths) == 1 and not bigger.complete, "the cap never fired, so raising it answers the same"


def test_the_names_are_resolved_before_the_sanitizers():
    """Step 3 before step 4 of the ordered contract, which the docstring asserts and nothing pinned:
    swapping the two ``resolve_value`` loops with the ``resolve_sanitizers`` call passed the whole
    suite. It matters because a caller with a typo in a *source* would be told about their
    **sanitizer** instead -- and worse, a sanitizer selector is checked against the SDG edges of a
    callable whose own name has not been judged yet."""
    backend = _Recording()
    reached = []

    def _refuse(name, *, within):
        raise SelectorNotInGraph("value", [name], 1, detail=f"relative to within={within!r}")

    backend.resolve_value = _refuse
    backend._edge_vars_in = lambda callable_id: reached.append(callable_id) or set()
    with pytest.raises(SelectorNotInGraph):
        backend.taint([("x", "f")], [("y", "g")], sanitizers=[("answer", "f")])
    assert reached == [], "the sanitizer hook is not touched until every name has resolved"


def test_roots_are_deduplicated_by_ref_so_one_position_is_audited_once():
    """``roots`` is the audit line a caller reads a verdict against, and a duplicated selector must
    not make a position appear twice in it -- ``resolved`` is built from it, so the repetition would
    be visible in the sentence a report quotes. Unpinned until now: dropping the dedup to
    ``[*srcs, *dsts]`` passed the whole suite."""
    a, b = _value("x", "f"), _value("y", "g")
    result = _Recording(rows=[(a.ref, b.ref, _witness(a, b))]).taint([("x", "f"), ("x", "f")], [("y", "g")])
    assert [n.ref for n in result.roots] == [a.ref, b.ref], "two selectors, one position, one root"
    assert result.resolved == "f parameter 'x', g parameter 'y'"


def test_the_two_walk_hooks_are_abstract_methods():
    """Ruling G, discharged: the hooks shipped as concrete stubs because an abstract method would
    have made every backend un-instantiable until the last implementation landed, so a backend
    without one was refused when the walk was *called*. Every backend has one now, so the refusal
    moves to construction, where a missing implementation is cheaper to find."""
    assert {"_taint_walk", "_edge_vars_in"} <= TSAnalysisBackend.__abstractmethods__
    with pytest.raises(TypeError) as err:
        type("_NoWalk", (TSAnalysisBackend,), {})()
    assert "_taint_walk" in str(err.value) and "_edge_vars_in" in str(err.value)


# ==============================================================================================
# The graph backend's half, over a fake driver: statement text, parameter binding, row translation.
# ==============================================================================================
#: One module, so ``_slice_row``'s ``file`` can be verified against the application's module keys the
#: way it is against a real graph's -- a body-node id embeds the module key and the graph stores no
#: path to project instead.
_MODULE = "app.ts"
_HANDLE = f"can://app/typescript/{_MODULE}/handle"
_SINK = f"can://app/typescript/{_MODULE}/query"


def _row(src: str, dst: str, *, var: str = "answer") -> dict:
    """One witness as the statement projects it: ``ns`` per node, ``rs`` per hop, one fewer hop than
    nodes. The keys are :attr:`TSNeo4jBackend._TAINT`'s projection, which is
    :attr:`~TSNeo4jBackend._PATHS`' verbatim -- that shared projection is what makes a taint witness
    and a ``paths_between`` witness describe a node identically."""
    return {
        "src": src,
        "dst": dst,
        "ns": [
            {"ref": src, "kind": "formal_in", "of": "raw", "line": None, "callable": "app.handle", "c_line": 7},
            {"ref": dst, "kind": "formal_in", "of": "cleaned", "line": 12, "callable": "app.query", "c_line": 11},
        ],
        "rs": [{"via": "TS_DDG", "var": var, "prov": ["reaching-defs"]}],
    }


def _graph(rows=(), edge_vars=(), record=None):
    """A ``TSNeo4jBackend`` over a fake driver that answers the attach probes, the taint statement and
    the edge-variable statement, and records the parameters each was bound with."""

    def _responder(query, params):
        if record is not None:
            record.append((query, dict(params)))
        if "TS_HAS_MODULE" in query:
            return [{"k": _MODULE, "id": f"can://app/typescript/{_MODULE}"}]
        if "AS ok" in query:
            return [{"ok": True}]
        if "collect(DISTINCT r.var) AS vars" in query:
            return [{"vars": list(edge_vars)}]
        if "AS src, b.id AS dst" in query:
            return list(rows)
        return []

    return TSNeo4jBackend._from_driver(FakeDriver(responder=_responder), application_name="app")


def test_the_graph_walk_issues_the_generated_statement_and_binds_the_cap_one_past_max_paths():
    """The extra row is the whole truncation mechanism (Ruling H / E5): bind ``$cap`` to
    ``max_paths`` and ``taint()`` reports ``complete=True`` on a result it silently cut. The scope
    parameter is bound in the same call because :attr:`_TAINT` carries the interior predicate --
    an unbound ``$p`` is a Cypher error, so this is what says the two agree."""
    record = []
    graph = _graph(record=record)
    src, dst = _value("raw", "handle"), _value("cleaned", "query")
    graph._taint_walk([src], [dst], cuts=[{"var": "answer", "prefix": _HANDLE}], cut_callables=[_SINK], depth=None, max_paths=3)
    query, params = record[-1]
    assert query == TSNeo4jBackend._TAINT.format(rels=SDG_REL_PATTERN, depth="")
    assert params == {
        "srcs": [src.ref],
        "dsts": [dst.ref],
        "cuts": [{"var": "answer", "prefix": _HANDLE}],
        "cut_callables": [_SINK],
        "cap": 4,
        "p": "can://app/",
    }


def test_an_explicit_depth_reaches_the_statement_as_the_quantifiers_upper_bound():
    """``depth=None`` renders ``*1..`` and a bound renders ``*1..5``; the walk is the only place that
    substitution happens, so a backend that forgot it would answer every call unbounded -- and an
    unbounded answer to a bounded question is the direction that manufactures witnesses."""
    record = []
    _graph(record=record)._taint_walk([_value("raw", "handle")], [_value("cleaned", "query")], cuts=[], cut_callables=[], depth=5, max_paths=1)
    assert record[-1][0] == TSNeo4jBackend._TAINT.format(rels=SDG_REL_PATTERN, depth="5")
    assert "*1..5]->" in record[-1][0]


def test_the_graph_walk_returns_every_row_untrimmed_and_keyed_by_the_pair_the_server_reported():
    """Grouping and trimming are ``taint()``'s, so the walk hands back what it found -- including the
    ``max_paths + 1``-th row. Trimming here would put the cap in two places and make ``complete``
    unprovable from either.

    The pairing comes from the statement's own ``a.id AS src`` / ``b.id AS dst`` and never from the
    order the rows arrive in: the m*n batching is only useful if the grouping survives it."""
    src, dst = _value("raw", "handle"), _value("cleaned", "query")
    rows, blocked = _graph(rows=[_row(src.ref, dst.ref), _row(src.ref, dst.ref, var="second")])._taint_walk(
        [src], [dst], cuts=[], cut_callables=[], depth=None, max_paths=1
    )
    assert [(r[0], r[1]) for r in rows] == [(src.ref, dst.ref)] * 2, "two rows for a cap of one: the extra row is what reports truncation"
    assert [r[2].hops[0].var for r in rows] == ["answer", "second"]
    assert blocked == {}, "Ruling I: an empty ledger is a refusal to file, argued in the walk's docstring"


def test_a_graph_row_is_described_the_way_a_paths_between_row_is():
    """Same projection, same ``_slice_row``: ``file`` derived from the id's own module key (verified
    against the application's, never split), ``via`` translated through the shared ``VIA`` table, and
    a parameter-passing vertex with no span of its own borrowing the callable's first line."""
    src, dst = _value("raw", "handle"), _value("cleaned", "query")
    (row,) = _graph(rows=[_row(src.ref, dst.ref)])._taint_walk([src], [dst], cuts=[], cut_callables=[], depth=None, max_paths=5)[0]
    (hop,) = row[2].hops
    assert hop.via == "data" and hop.var == "answer" and hop.prov == ["reaching-defs"]
    assert (hop.frm.file, hop.frm.line, hop.frm.callable, hop.frm.kind, hop.frm.name) == (_MODULE, 7, "app.handle", "parameter", "raw")
    assert (hop.to.file, hop.to.line, hop.to.kind) == (_MODULE, 12, "parameter")


def test_the_graph_edge_variable_domain_is_scoped_by_the_callables_own_ref_and_drops_the_nulls():
    """``$callable_prefix`` holds a **callable's** ``can://`` ref rather than the application's, which
    is why it is spelled apart from ``$p``: what makes the statement application-scoped is a property
    of the value bound to it -- a callable id embeds the application name -- and the scope audit
    classifies it on that basis.

    ``collect(DISTINCT r.var)`` returns a ``null`` for every ``TS_CDG``/``TS_SUMMARY`` hop, which
    carry no ``var`` at all. Keeping it would put ``None`` in a set ``resolve_sanitizers`` tests
    membership against, and ``resolve_sanitizers`` has already refused a blank variable by then."""
    record = []
    graph = _graph(edge_vars=["answer", None, "raw"], record=record)
    assert graph._edge_vars_in(_HANDLE) == frozenset({"answer", "raw"})
    query, params = record[-1]
    assert query == TSNeo4jBackend._EDGE_VARS.format(rels=SDG_REL_PATTERN)
    assert params == {"callable_prefix": _HANDLE}
    assert "CanNode" not in query, "a STARTS WITH-scoped statement seeks best on the bare label (the measured seek rule)"
