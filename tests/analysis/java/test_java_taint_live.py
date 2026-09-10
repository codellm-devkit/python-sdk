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

r"""``taint()``'s Java graph walk, and what a **sanitizer cut** does on a real application.

``test_java_taint.py`` pins the policy offline over a hand-built payload. Task 7 shipped this walk
without ever running it against a server, and the leg's own review said so. What only a server can
answer is the part this leg exists for: whether the cut inlined into the ``allShortestPaths`` pattern
severs the flows the caller named **and no others**, at 326,086-edge scale, in a database that also
holds ThingsBoard — so an unscoped cut shows up as a smaller answer rather than as nothing.

**The witness, and why it is this one.** Step 1 of Task 8 searched daytrader8's whole vocabulary for
the two sanitizer shapes ``taint()`` distinguishes. The validating-guard shape is present::

    LoginValidator.validate(FacesContext, UIComponent, Object)   # parameter 'value'

which is a genuine guard — read off ``JCallable.code`` on the graph, it logs ``value.toString()``,
then ``matcher = pattern.matcher(value.toString()); if (!matcher.matches()) throw new
ValidatorException(msg);`` — and its signature is **unique in the application** (``count(c) = 1`` over
``JCallable.signature``, measured), so it does not trip the ``AmbiguousName`` that a bare Java
signature shared across classes raises.

The *transforming*-sanitizer shape is **absent** from this corpus, and that is a finding rather than a
gap in this file. ``JsonEncoder.encode(JsonMessage)`` is the only real transform in the vocabulary and
it has **0** mid-path witnesses; both ``checkDBProductName()`` matches likewise 0. Of 4,000 sampled
witnessed ``formal_in -> formal_in`` pairs only ``validate`` (12) and ``doFilter`` (16) appear at all,
and both appear in the *source* position, never in the middle. So the bare-``str`` **callable** cut is
exercised below against the guard and against the sink, which is the same predicate on the same
graph — what is not available on daytrader8 is a callable that is *semantically* a sanitizer sitting
mid-flow, and no assertion here pretends otherwise.

**C4a, confirmed in a live path.** Every witness's final hop is a ``J_PARAM_IN`` crossing whose ``var``
is ``None`` (``[('data', 'value'), ('data', 'arg0'), ('argument', None)]``). daytrader8's analyzer is
one release below the ``var``-on-param-edge fix, where ``J_*_PARAM_IN`` carries ``var`` on **0 of
76,791** edges. A variable cut therefore cannot sever a Java parameter crossing at all — it
*under*-cuts, which only over-reports — and :func:`test_a_variable_cut_does_not_reach_the_parameter_crossing`
pins that direction rather than leaving it as prose.

**Also measured here, against ``CLAUDE.md``**: daytrader8's port lattice is **connected**
(``_ports_carry_dependence`` is ``True``), so ``taint`` answers on this graph rather than raising
``PORTS_DISCONNECTED``. The refusal is a verdict on the *data*, not on the analyzer's version, and
this file is the live half of that claim.

Graph backend only. The local backend would need the daytrader8 source checkout and a JDK, neither of
which is in this repo, so the cross-backend agreement Python's live taint suite asserts has no
counterpart here — the offline suite carries the parity policy instead.

Its own environment namespace, and **7687 is deliberately not among the defaults**::

    CLDK_TEST_JTAINT_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_JTAINT_NEO4J_USER=neo4j \
    CLDK_TEST_JTAINT_NEO4J_PASSWORD=... \
    CLDK_TEST_JTAINT_NEO4J_APP=daytrader8 \
    uv run --all-groups --extra neo4j pytest tests/analysis/java/test_java_taint_live.py

``test_java_dataflow_live.py`` shares ``CLDK_TEST_NEO4J_*`` with three other modules and defaults its
URI to ``bolt://localhost:7687``, which on a developer machine is as likely to be an ssh tunnel as a
graph. A separate namespace is the whole reason this file does not join it.

Read-only, like every other Neo4j suite here.
"""

import logging
import os

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

TAINT_URI = os.environ.get("CLDK_TEST_JTAINT_NEO4J_URI", "bolt://localhost:7691")
TAINT_USER = os.environ.get("CLDK_TEST_JTAINT_NEO4J_USER", "neo4j")
TAINT_PASSWORD = os.environ.get("CLDK_TEST_JTAINT_NEO4J_PASSWORD", "cldkleg3test")
TAINT_APP = os.environ.get("CLDK_TEST_JTAINT_NEO4J_APP", "daytrader8")


def _graph_present() -> bool:
    """True iff a server answers at ``TAINT_URI`` *and* holds ``TAINT_APP``.

    Connectivity alone is not enough: this file names four callables by signature, and a developer
    with a different application on this port would read every resulting ``SelectorNotInGraph`` as a
    defect in ``taint()``. ``JApplication.name`` is NULL on these graphs, so the probe matches on
    ``id`` — the same reason the addressing suites do.
    """
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(TAINT_URI, auth=(TAINT_USER, TAINT_PASSWORD))
        try:
            driver.verify_connectivity()
            with driver.session() as session:
                found = session.run("MATCH (a:JApplication) WHERE a.id CONTAINS $n RETURN count(a) AS c", n=TAINT_APP).single()
                return bool(found and found["c"])
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 - any connection/auth failure => skip, never fail
        return False


pytestmark = pytest.mark.skipif(
    not _graph_present(),
    reason=(f"no live Java taint corpus: needs Neo4j at {TAINT_URI} holding {TAINT_APP!r} " "(set CLDK_TEST_JTAINT_NEO4J_URI / _USER / _PASSWORD / _APP)"),
)

#: The guard. Spelled as a bare signature because it is unique in daytrader8 — see the module
#: docstring for the ``count(c) = 1`` measurement, and :func:`test_the_guard_signature_is_unique`
#: for the assertion, which is what keeps this spelling honest if the corpus ever grows a sibling.
GUARD = "validate(javax.faces.context.FacesContext, javax.faces.component.UIComponent, java.lang.Object)"

#: Two overloads of one logger, which is what makes a *differentiated* cut observable: the two
#: three-hop witnesses end in ``trace(String, Object)`` and the two six-hop ones pass *through* it on
#: their way to ``trace(String)``.
TRACE_2 = "trace(java.lang.String, java.lang.Object)"
TRACE_1 = "trace(java.lang.String)"

SOURCES = [("value", GUARD)]
SINKS = [("message", TRACE_2), ("parm1", TRACE_2), ("message", TRACE_1)]


@pytest.fixture(scope="module")
def graph():
    """The corpus graph, attached. Module-scoped: attaching runs the version probe and a projection."""
    from cldk import CLDK
    from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

    facade = CLDK.java(backend=Neo4jConnectionConfig(uri=TAINT_URI, username=TAINT_USER, password=TAINT_PASSWORD, application_name=TAINT_APP))
    yield facade.backend
    facade.backend.close()


def _lens(result):
    """Witness lengths, sorted. The one summary a cut moves that a count does not: a cut that took
    the two long routes and a cut that took the two short ones both drop ``len(paths)`` from 4 to 2.
    """
    return sorted(len(p.hops) for p in result.paths)


def test_the_guard_signature_is_unique_in_the_application(graph):
    """``GUARD`` is spelled as a bare signature, which Java's ``resolve_callable`` matches across
    classes.

    daytrader8 has ``decode(java.lang.String)`` on both ``ActionDecoder`` and ``JsonDecoder``, and
    two ``doFilter(...)``, so a bare signature is ambiguous more often than not here. This pins the
    premise the rest of the file rests on, so a corpus that grew a second ``validate`` overload would
    fail *here*, with a reason, rather than as an ``AmbiguousName`` inside an unrelated assertion.
    """
    rows = graph._run("MATCH (c:JCallable) WHERE c.signature = $s RETURN count(c) AS n", s=GUARD)
    assert rows[0]["n"] == 1, "GUARD must stay unambiguous, or every selector below needs the dotted form"


def test_the_ports_are_connected_so_taint_answers_rather_than_refusing(graph):
    """The live half of ``CLAUDE.md``'s "four accessors raise" note.

    That refusal is asked of the *data* — ``_ports_carry_dependence``, never a version string — and on
    daytrader8 the answer is that they are connected. So the note describes a property of a graph, not
    of Java, and ``taint()``'s docstring is right to make the refusal conditional. A future graph that
    lost its crossings would fail the assertions below with ``PORTS_DISCONNECTED``, which is why this
    is stated once, here, instead of being caught eight times as a confusing error.
    """
    assert graph._ports_carry_dependence is True
    graph._require_connected_ports("taint")  # must not raise


def test_the_guard_reaches_four_sinks_at_two_distinct_lengths(graph):
    """The baseline every cut below is a delta against.

    Four witnesses over three pairs at two lengths — and the *lengths* are the claim, because a walk
    that stopped at the first call boundary would still return the two three-hop rows and look
    healthy. The six-hop routes are the ones that leave ``Log.trace(String, Object)`` and arrive at
    ``Log.trace(String)``, i.e. two crossings in the same direction.
    """
    r = graph.taint(SOURCES, SINKS)
    assert _lens(r) == [3, 3, 6, 6]
    assert r.complete is True
    assert r.exhausted == [], "every requested pair has a witness"
    assert r.unresolved == []
    assert len(r.roots) == 4, "one source and three sinks, and two of the sinks share a callable"


def test_a_variable_cut_does_not_reach_the_parameter_crossing(graph):
    """C4a, as an assertion rather than a note — and the direction of the harm.

    Every witness's last hop is a ``J_PARAM_IN`` crossing carrying no ``var`` (0 of 76,791 on this
    analyzer's output). ``coalesce(r.var, '')`` maps that to ``''``, which matches no legal cut, so a
    variable cut aimed at a crossing fires on nothing. That is why the three-hop witness whose *own
    sink* is ``message`` survives a cut on ``message``: the hop arriving at it is the crossing.

    Under-cutting only over-reports — the caller investigates a flow that was in fact sanitized —
    where over-cutting would certify a refutation and close a live alert. This test exists to fail if
    someone "fixes" the asymmetry in the unsafe direction.

    **It is also the only behavioural check anywhere on the ``coalesce`` in the cut predicate**, and
    the reason the offline suite cannot be. ``tests/analysis/commons/test_taint_semantics.py`` answers
    from the local replay, which filters in Python; deleting ``coalesce(r.var, '')`` from
    ``sdg_taint_query`` fails **none** of its 23 tests, because none of them runs the Cypher. Measured
    here by mutation: with a bare ``r.var = c.var`` this assertion drops from ``[3, 3, 6]`` to
    ``[3, 3]``, and the eight other tests in this file stay green.

    The mechanism is three-valued logic, and the direction of the damage is the dangerous one. The
    predicate is ``NOT (var-match AND under-callable)``. On a null-``var`` edge whose start node *is*
    under the cut's callable, ``NULL AND true`` is ``NULL``, ``NOT NULL`` is ``NULL``, and Neo4j drops
    the relationship — so the walk severs a crossing the caller never named. ``coalesce`` maps that
    ``NULL`` to ``''``, which equals no legal cut, and the edge survives. Without it the cut
    **over**-cuts: one more witness gone, and a pair one witness away from being certified as refuted.
    """
    on_message = graph.taint(SOURCES, SINKS, [("message", TRACE_2)])
    assert _lens(on_message) == [3, 3, 6], "one six-hop route travelled on 'message' and is gone"
    assert on_message.exhausted == [], "no pair lost every witness, so nothing is certified"
    assert all(h.var is None for p in graph.taint(SOURCES, SINKS).paths for h in p.hops if h.via == "argument")


def test_a_variable_cut_on_the_guard_refutes_every_pair(graph):
    """Shape (a): the guard's own parameter is the source, so cutting it severs at the first hop.

    All three pairs go to zero *and are certified*, which is the strongest output ``taint()`` has and
    the one that closes an alert. The certificate is only legitimate beside an empty ledger, so that
    is asserted rather than assumed.
    """
    r = graph.taint(SOURCES, SINKS, [("value", GUARD)])
    assert r.paths == []
    assert sorted(r.exhausted) == [("value", "message"), ("value", "message"), ("value", "parm1")]
    assert r.unresolved == []
    assert r.complete is True, "three pairs refuted cleanly is a complete batch"


def test_the_same_variable_name_in_another_callable_cuts_nothing(graph):
    """Scoping, which is the property that separates a cut from a censor.

    daytrader8 has **ten** ``formal_in`` ports named ``value``. An unscoped cut on the string would
    sever every one of them; the ``$cuts`` entries carry a ``prefix`` precisely so that a caller who
    wrote ``within="setConfigParam(...)"`` gets that callable's ``value`` and not the guard's.

    A cut that silently applied application-wide would pass ``test_a_variable_cut_on_the_guard...``
    above just as well, which is why this is a separate test and not a second assertion in it.

    A cut aimed at another callable is also the case that shows the ``coalesce`` is *not* what keeps
    this test green: no edge on these paths starts under ``setConfigParam``, so the ``prefix``
    conjunct is already ``false`` and three-valued logic never gets a chance to matter. The test that
    does see it is :func:`test_a_variable_cut_does_not_reach_the_parameter_crossing` -- measured, not
    predicted; see its docstring.
    """
    r = graph.taint(SOURCES, SINKS, [("value", "setConfigParam(java.lang.String, java.lang.String)")])
    assert _lens(r) == [3, 3, 6, 6], "a cut aimed at another callable's 'value' must change nothing"
    assert r.exhausted == [] and r.complete is True


def test_a_callable_cut_refutes_the_pairs_that_end_in_it(graph):
    """The bare-``str`` shape, on the sink side.

    Both ``trace(String, Object)`` pairs go to zero, and so does the ``trace(String)`` pair, because
    every route to it passes through the cut callable — which is the whole point of putting the
    predicate inside ``ShortestPath`` rather than filtering returned rows.

    ``exhausted`` lists ``('value', 'message')`` **twice**. That is not a bug and not a duplicate
    pair: a pair is named by the two *selector names* the caller wrote, and two different sinks here
    are both spelled ``message``. ``roots`` is what tells them apart, and this assertion is written to
    document that rather than to be tidied into a set.
    """
    r = graph.taint(SOURCES, SINKS, [TRACE_2])
    assert r.paths == []
    assert sorted(r.exhausted) == [("value", "message"), ("value", "message"), ("value", "parm1")]
    assert r.exhausted.count(("value", "message")) == 2, "two distinct sinks, one selector name"
    assert r.complete is True


def test_an_unrelated_callable_cut_leaves_the_batch_untouched(graph):
    """Over-cutting is the one output this leg refuses, so a cut that is *supposed* to do nothing is
    worth a test of its own.

    ``under_callable``'s three disjuncts (``n.id = q``, ``+ '@'``, ``+ '/'``) exist so that a prefix
    match cannot spill into a sibling whose id merely starts with the same characters. A bare
    ``STARTS WITH q`` passes every assertion above and fails this one.
    """
    r = graph.taint(SOURCES, SINKS, ["getMAX_USERS()"])
    assert _lens(r) == [3, 3, 6, 6]
    assert r.exhausted == [] and r.complete is True


def test_a_bounded_search_finds_the_short_routes_and_certifies_nothing(graph):
    """``depth`` is a bound on the search, and ``exhausted`` is empty whenever it is set.

    ``depth=3`` keeps the two three-hop witnesses and drops the two six-hop ones — which is a real
    result, not a refutation — and the ``trace(String)`` pair now has no witness *and no certificate*.
    A field that certified it here would tell triage that a flow this call never looked for does not
    exist.
    """
    r = graph.taint(SOURCES, SINKS, depth=3)
    assert _lens(r) == [3, 3], "the six-hop routes are out of bounds, not absent"
    assert r.exhausted == [], "depth is not None"
    assert r.complete is True, "a bounded search that truncated nothing is a complete answer"
    cut = graph.taint(SOURCES, SINKS, [TRACE_2], depth=3)
    assert cut.paths == [] and cut.exhausted == [], "a cut under a bound still certifies nothing"


def test_the_cap_is_per_pair_and_truncation_is_never_silent(graph):
    """``collect(p)[0..$cap]`` against a flat ``LIMIT``, on a real graph.

    Three pairs and four witnesses. At ``max_paths=1`` a per-pair cap returns **three** rows — one for
    each pair — and reports ``complete=False`` because the ``trace(String)`` pair had two. A flat
    ``LIMIT 1`` returns one row, and the two pairs it starved come back with no witness: reported as
    no flow, which in triage closes a live alert. At ``max_paths=2`` nothing is cut and the flag goes
    back to ``True``, so the ``False`` above is truncation and not the ledger.
    """
    at_one = graph.taint(SOURCES, SINKS, max_paths=1)
    assert _lens(at_one) == [3, 3, 6], "one witness per pair, three pairs"
    assert at_one.complete is False, "a bound is never silent (E5)"
    assert at_one.exhausted == [], "a truncated pair is not a refuted one"
    at_two = graph.taint(SOURCES, SINKS, max_paths=2)
    assert _lens(at_two) == [3, 3, 6, 6] and at_two.complete is True
