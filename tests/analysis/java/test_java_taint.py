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

"""``taint()``'s ordered contract on the Java backend ABC -- offline, no analyzer and no graph.

Everything ``taint()`` does before and after the walk is policy that lives on the ABC, so it is
testable against a backend whose walk only records what it was asked. What is pinned here is the
*order* of the checks (a malformed bound is judged before a name is looked up, a name before the
port-lattice gate, a sanitizer before the walk), the two deliberate divergences from
``paths_between`` (a same-position pair is skipped rather than raised; a bounded ``depth`` yields no
``exhausted`` pair), and the three membership conditions of ``exhausted``.

Java's extra clause is the gate: ``_require_connected_ports`` sits *after* resolution, so a caller
with a typo hears about their typo and not about a gap in the analysis.
"""

import pytest

from cldk.analysis.commons.results import Diagnostic, FlowPath, PathHop, SliceNode
from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, SelectorNotInGraph

HANDLE = "com.acme.Svc.handle(java.lang.String)"
STORE = "com.acme.Dao.store(java.lang.String)"
LOG = "com.acme.Log.write(java.lang.String)"


def _value(name: str, within: str) -> SliceNode:
    """A resolved value, addressed as this surface addresses one: a parameter plus the callable it
    enters, whose ``ref`` is the ``formal_in`` vertex's own id."""
    return SliceNode(file="Svc.java", line=1, callable=within, kind="parameter", name=name, ref=f"can://java/acme/{within}@formal_in:0#{name}")


def _witness(frm: SliceNode, to: SliceNode) -> FlowPath:
    return FlowPath(hops=[PathHop(frm=frm, to=to, via="data", var="answer", prov=["ssa"])])


class _Recording(JavaAnalysisBackend):
    """The contract with only the methods ``taint()`` touches, and a walk that records its call.

    ``__abstractmethods__`` is cleared below rather than the other fifty-odd methods being stubbed:
    what is under test is one concrete body, and anything else this backend could answer would only
    be a way for these tests to fail for an unrelated reason. ``_ports_carry_dependence`` and
    ``_ports_carry_dependence`` stays a property because the real one is (a plain attribute would
    not shadow a data descriptor at all), and ``_application_name`` is a bare string because the real
    property reads an application view this fake does not have.
    """

    _application_name = "acme"

    def __init__(self, rows=(), blocked=None, edge_vars=("answer",), ports=True):
        self._rows = list(rows)
        self._blocked = dict(blocked or {})
        self._edge_vars = set(edge_vars)
        self._ports = ports
        self.resolved = []
        self.walks = []

    @property
    def _ports_carry_dependence(self):
        return self._ports

    def resolve_value(self, name, *, within):
        self.resolved.append((name, within))
        return _value(name, within)

    def resolve_callable(self, name, *, in_class=None, in_module=None):
        return SliceNode(file="Svc.java", line=1, callable=name, kind="callable", name=name.rpartition(".")[2].partition("(")[0], ref=f"can://java/acme/{name}")

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
        backend.taint([("in", HANDLE)], [("sql", STORE)], depth=0)
    with pytest.raises(ValueError, match="max_paths"):
        backend.taint([("in", HANDLE)], [("sql", STORE)], max_paths=0)
    assert backend.resolved == [] and backend.walks == []


def test_empty_sources_or_sinks_is_refused_not_answered_empty():
    """``cone_sinks`` already refuses the two ways of naming nothing, and here the stakes are the
    reason: an empty answer would be indistinguishable from a refutation."""
    backend = _Recording()
    with pytest.raises(ValueError):
        backend.taint([], [("sql", STORE)])
    with pytest.raises(ValueError):
        backend.taint([("in", HANDLE)], [])
    assert backend.walks == []


def test_a_bare_string_is_refused_rather_than_unpacked_into_a_pair():
    """``sources="xy"`` is a sequence -- of two characters -- so it would unpack to the pair
    ``("x", "y")`` and resolve a value nobody named. ``reject_bare_string`` is why it is a
    ``TypeError``."""
    backend = _Recording()
    with pytest.raises(TypeError):
        backend.taint("xy", [("sql", STORE)])
    with pytest.raises(TypeError):
        backend.taint([("in", HANDLE)], "xy")


def test_a_disconnected_port_lattice_refuses_after_resolution_and_before_the_walk():
    """The Java clause. ``taint`` joins the forward value accessors in refusing on a port lattice
    with no dependence edge (every pair would come back refuted for a reason that has nothing to do
    with the program), and it refuses in the same place they do: after the arguments and the names,
    before the traversal."""
    backend = _Recording(ports=False)
    with pytest.raises(CodeanalyzerExecutionException, match="taint"):
        backend.taint([("in", HANDLE)], [("sql", STORE)])
    assert backend.resolved == [("in", HANDLE), ("sql", STORE)], "the names are judged first, so a typo is reported as a typo"
    assert backend.walks == []
    with pytest.raises(ValueError):
        backend.taint([("in", HANDLE)], [("sql", STORE)], depth=0)


def test_a_found_flow_carries_its_witnesses_the_roots_and_the_audit_line():
    a, b = _value("in", HANDLE), _value("sql", STORE)
    backend = _Recording(rows=[(a.ref, b.ref, _witness(a, b))])
    result = backend.taint([("in", HANDLE)], [("sql", STORE)])
    assert len(result.paths) == 1 and result.complete
    assert result.exhausted == [] and result.unresolved == []
    assert [n.ref for n in result.roots] == [a.ref, b.ref]
    assert result.resolved == f"{HANDLE} parameter 'in', {STORE} parameter 'sql'"
    assert backend.walks == [{"srcs": [a.ref], "dsts": [b.ref], "cuts": [], "cut_callables": [], "depth": None, "max_paths": 10}]


def test_a_pair_with_no_route_is_exhausted_only_when_the_search_was_unbounded():
    """Condition 1 of the spec's section 5, a rule and not a tendency: under a bound "no path found"
    is not evidence of absence, so the bounded call reports nothing rather than a refutation a
    caller could close a live alert on."""
    backend = _Recording()
    assert backend.taint([("in", HANDLE)], [("sql", STORE)]).exhausted == [("in", "sql")]
    assert backend.taint([("in", HANDLE)], [("sql", STORE)], depth=5).exhausted == []


def test_the_same_position_pair_is_skipped_with_a_diagnostic_not_raised():
    """A deliberate divergence from ``paths_between``, which raises via ``check_distinct_endpoints``:
    raising would discard a forty-pair batch for one degenerate pair, and a caller assembling
    sources programmatically will hit that by accident."""
    backend = _Recording()
    result = backend.taint([("in", HANDLE), ("msg", LOG)], [("in", HANDLE)])
    assert result.paths == []
    assert any("same position" in d.message for d in result.unresolved)
    assert result.exhausted == [("msg", "in")], "the degenerate pair is skipped; the rest of the batch still answers"
    assert not result.complete


def test_a_pair_a_diagnostic_implicates_is_neither_proved_nor_refuted():
    """Condition 3: a pair whose route crossed an unresolved dispatch appears in neither list, and
    the association is the walk's to report -- ``exhausted`` is never computed by reading a message
    back out."""
    a, b = _value("in", HANDLE), _value("sql", STORE)
    blocked = Diagnostic(code="unresolved_dispatch", message=f"'in' in {HANDLE} to 'sql' in {STORE} crosses an unresolved dispatch")
    backend = _Recording(blocked={(a.ref, b.ref): [blocked]})
    result = backend.taint([("in", HANDLE)], [("sql", STORE)])
    assert result.exhausted == [] and result.unresolved == [blocked] and not result.complete


def test_a_ledger_entry_no_requested_pair_claims_is_still_reported():
    """An unresolved dispatch belongs to a *callable frontier*, not to one pair, so a walk may key it
    in a way this body does not look up -- a reversed pair, one arm of a frontier, a combination the
    caller never requested. Reading only the requested keys would drop the entry and hand the pair it
    named back as ``exhausted``: a certified refutation of a flow that was in fact blocked, which is
    the one output this accessor exists to refuse."""
    a, b = _value("in", HANDLE), _value("sql", STORE)
    stray = Diagnostic(code="unresolved_dispatch", message=f"'sql' in {STORE} to 'in' in {HANDLE} crosses an unresolved dispatch")
    result = _Recording(blocked={(b.ref, a.ref): [stray]}).taint([("in", HANDLE)], [("sql", STORE)])
    assert result.unresolved == [stray], "the entry survives a key no requested pair claimed"
    assert result.exhausted == [], "nothing can attribute a stray key to a pair, so no pair is certified"
    assert not result.complete

def test_paths_are_trimmed_per_pair_and_completeness_says_the_cap_fired():
    """The walk caps each pair at ``max_paths + 1``, so the extra row reports truncation without a
    second counting traversal -- and the trim is per pair, so a prolific pair cannot starve a
    sparse one out of its witness."""
    a, b, c = _value("in", HANDLE), _value("sql", STORE), _value("msg", LOG)
    rows = [(a.ref, b.ref, _witness(a, b))] * 3 + [(a.ref, c.ref, _witness(a, c))]
    result = _Recording(rows=rows).taint([("in", HANDLE)], [("sql", STORE), ("msg", LOG)], max_paths=2)
    assert len(result.paths) == 3, "two of the prolific pair's three, and the sparse pair's one"
    assert not result.complete and result.exhausted == []
    whole = _Recording(rows=rows).taint([("in", HANDLE)], [("sql", STORE), ("msg", LOG)], max_paths=3)
    assert len(whole.paths) == 4 and whole.complete


def test_the_sanitizer_selectors_are_resolved_through_the_edge_vars_hook():
    """Step 4 of the order: the two shapes reach the walk as ``$cuts`` and ``$cut_callables``, and a
    variable selector is checked against the vars on real SDG edges rather than ``resolve_value``,
    which addresses only parameters."""
    backend = _Recording()
    backend.taint([("in", HANDLE)], [("sql", STORE)], sanitizers=[("answer", HANDLE), "escapeHtml4"])
    assert backend.walks[0]["cuts"] == [{"var": "answer", "prefix": f"can://java/acme/{HANDLE}"}]
    assert backend.walks[0]["cut_callables"] == ["can://java/acme/escapeHtml4"]


def test_a_sanitizer_that_does_not_resolve_raises_before_the_walk():
    backend = _Recording(edge_vars=())
    with pytest.raises(SelectorNotInGraph):
        backend.taint([("in", HANDLE)], [("sql", STORE)], sanitizers=[("answer", HANDLE)])
    assert backend.walks == [], "sanitizers are resolved before the traversal, not applied after it"


def test_the_two_walk_hooks_are_stubs_rather_than_abstract_methods():
    """Ruling G: an abstract method here would make every concrete backend un-instantiable until the
    last implementation lands, so they raise instead. Task 7 flips them, and this test is what says
    the stub is still a stub."""
    assert not {"_taint_walk", "_edge_vars_in"} & JavaAnalysisBackend.__abstractmethods__
    with pytest.raises(NotImplementedError):
        JavaAnalysisBackend._taint_walk(None, [], [], cuts=[], cut_callables=[], depth=None, max_paths=1)
    with pytest.raises(NotImplementedError):
        JavaAnalysisBackend._edge_vars_in(None, f"can://java/acme/{HANDLE}")
