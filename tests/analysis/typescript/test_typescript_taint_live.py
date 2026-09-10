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

r"""``taint()``'s TypeScript graph walk, and the one thing offline tests cannot show: a **cut that
fires on one pair while its sibling survives**.

The offline suite pins the policy over a hand-built payload; Task 7 shipped this walk without ever
running the Cypher. superset-frontend is where it has to hold up — 125,532 body nodes, 119,384
``TS_DDG`` edges, and a genuine sanitizer in its actual source, not one written for a test.

**The witness.** ``packages/superset-ui-core/src/utils/html.tsx`` has::

    export function sanitizeHtmlIfNeeded(htmlString: string) {
      return isProbablyHTML(htmlString) ? sanitizeHtml(htmlString) : htmlString;
    }

which is the validating-guard shape ``taint()`` cuts by *variable*: the parameter ``htmlString`` is on
the flow, and it reaches **two** sinks at length 2 — ``sanitizeHtml``'s own ``htmlString`` and
``isProbablyHTML``'s ``text``. Two pairs off one source is what makes a differentiated cut visible:
:func:`test_a_callable_cut_takes_one_pair_and_leaves_its_sibling` cuts one of them and asserts the
other is *still witnessed in the same call*. A cut implemented as a post-filter on returned rows, or
one applied application-wide, passes a single-pair test and fails that one.

**The transforming-sanitizer shape is absent here, and that is the honest answer.** Step 1 of Task 8
swept superset's whole vocabulary — ``sanitiz|sanitis|escape|encode|scrub|strip|validate|verify``, 45
candidates — for a *third* callable sitting mid-flow between two others. There is none.
``sanitizeHtmlIfNeeded`` is itself a **root**: a backward expand into its parameter port,

    MATCH (z)-[:TS_DDG|TS_PARAM_IN|TS_PARAM_OUT|TS_CALL_RET*1..4]->
              (a:TSBodyNode {id: '…/html.tsx/sanitizeHtmlIfNeeded@formal_in:0'}) RETURN z LIMIT 25

returns **zero** rows, and it has no incoming ``TS_CALLS`` at all. Of the necessary-condition sweep's
12 survivors every one was the same ``TranslatorSingleton.t()`` call/return coupling — a translation
lookup, not a taint route. ``filter`` and ``check`` were deliberately left out of that vocabulary:
in superset they are data-plane words with 200+ hits and no sanitizer among them, and pretending
otherwise would have manufactured a witness. So the bare-``str`` **callable** cut is exercised below
against callables that are genuinely on these flows; what does not exist on this corpus is a callable
that is *semantically* a transforming sanitizer in the middle of one.

**Two live addressing facts this file depends on**, both worth knowing before reading a failure here:

* ``sanitizeHtml`` is **ambiguous** in superset — ``packages/superset-ui-core/src/utils/html`` and
  ``plugins/plugin-chart-echarts/src/utils/series`` both export one — so the sink is spelled with its
  dotted module path. :func:`test_the_bare_sink_name_is_ambiguous_and_says_so` pins that the SDK
  *raises* rather than silently picking one, because picking one would put a flow in a module the
  caller never asked about.
* TypeScript's emitter names a parameter port in ``of``, not ``var`` (0 of 10,495 ``formal_in`` nodes
  carry ``var``), which is why the argument hop below reports ``var=None`` and a variable cut lands on
  the ``TS_DDG`` hop instead of on the crossing.

Graph backend only — the superset-frontend checkout is not in this repo, so there is no local run to
agree with; the offline suite carries the cross-backend parity policy.

Its own environment namespace, and **7687 is deliberately not among the defaults**::

    CLDK_TEST_TSTAINT_NEO4J_URI=bolt://localhost:7692 \
    CLDK_TEST_TSTAINT_NEO4J_USER=neo4j \
    CLDK_TEST_TSTAINT_NEO4J_PASSWORD=... \
    CLDK_TEST_TSTAINT_NEO4J_APP=superset-frontend \
    uv run --all-groups --extra neo4j pytest tests/analysis/typescript/test_typescript_taint_live.py

The other TypeScript live modules share ``CLDK_TEST_NEO4J_*``. Keeping this one separate means
pointing it at a graph cannot re-point them, and it means no variable read here has a default that
resolves to a port a developer is likely to be tunnelling.

Read-only, like every other Neo4j suite here.
"""

from __future__ import annotations

import logging
import os

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

TAINT_URI = os.environ.get("CLDK_TEST_TSTAINT_NEO4J_URI", "bolt://localhost:7692")
TAINT_USER = os.environ.get("CLDK_TEST_TSTAINT_NEO4J_USER", "neo4j")
TAINT_PASSWORD = os.environ.get("CLDK_TEST_TSTAINT_NEO4J_PASSWORD", "cldkleg25btest")
TAINT_APP = os.environ.get("CLDK_TEST_TSTAINT_NEO4J_APP", "superset-frontend")

#: The guard's parameter. One source.
SOURCES = [("htmlString", "sanitizeHtmlIfNeeded")]

#: Its two sinks. The first needs the dotted module path (``sanitizeHtml`` alone is ambiguous in
#: superset); the second is unique, and is spelled bare on purpose so the file exercises both forms.
SANITIZE_HTML = "packages/superset-ui-core/src/utils/html.sanitizeHtml"
SINKS = [("htmlString", SANITIZE_HTML), ("text", "isProbablyHTML")]

#: The pair keys ``exhausted`` uses. A pair is named by the two *selector names* the caller wrote,
#: never by the resolved node — which is why these read as bare identifiers.
TO_SANITIZE_HTML = ("htmlString", "htmlString")
TO_IS_PROBABLY_HTML = ("htmlString", "text")


def _graph_present() -> bool:
    """True iff a server answers at ``TAINT_URI`` *and* holds ``TAINT_APP``.

    Connectivity is not the question — a graph holding some *other* application would turn every
    selector below into a ``SelectorNotInGraph`` that reads like a defect in ``taint()``.
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
                found = session.run("MATCH (a:Application {id: $id}) RETURN count(a) AS c", id=f"can://{TAINT_APP}").single()
                return bool(found and found["c"])
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 - any connection/auth failure => skip, never fail
        return False


pytestmark = pytest.mark.skipif(
    not _graph_present(),
    reason=(f"no live TypeScript taint corpus: needs Neo4j at {TAINT_URI} holding {TAINT_APP!r} " "(set CLDK_TEST_TSTAINT_NEO4J_URI / _USER / _PASSWORD / _APP)"),
)


@pytest.fixture(scope="module")
def graph():
    """The corpus graph, attached. Module-scoped: attaching runs the ≥ 1.5.2 version probe."""
    from cldk import CLDK
    from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

    facade = CLDK.typescript(
        project_path=None,
        backend=Neo4jConnectionConfig(uri=TAINT_URI, username=TAINT_USER, password=TAINT_PASSWORD, application_name=TAINT_APP),
    )
    yield facade.backend
    facade.backend.close()


#: The two sinks as the walk reports them, which is what tells the pairs apart. ``exhausted`` keys on
#: the *selector names* the caller wrote, and both pairs here are sourced from ``htmlString``, so the
#: witness's own endpoint is the only unambiguous name for "which pair answered".
AT_SANITIZE_HTML = "packages/superset-ui-core/src/utils/html.sanitizeHtml"
AT_IS_PROBABLY_HTML = "packages/superset-ui-core/src/utils/html.isProbablyHTML"


def _answered(result):
    """Which requested pair each witness answers, read off the walk's own endpoints.

    ``paths`` is a flat list, so a test that only counted it could not tell "the cut took the sibling"
    from "the cut took the pair I aimed at" -- both leave one row. The last hop's ``to`` is the sink
    position and its ``callable`` is the dotted signature, so this names the pair even where the two
    selector names cannot.
    """
    return sorted(p.hops[-1].to.callable for p in result.paths)


def test_the_guard_reaches_both_of_its_sinks(graph):
    """The baseline every cut below is a delta against.

    Two witnesses, one per pair, each 2 hops: a ``TS_DDG`` step carrying ``htmlString`` and then the
    ``TS_PARAM_IN`` crossing into the callee's port. Nothing exhausted, so every pair is answered.
    """
    r = graph.taint(SOURCES, SINKS)
    assert sorted(len(p.hops) for p in r.paths) == [2, 2]
    assert _answered(r) == sorted([AT_SANITIZE_HTML, AT_IS_PROBABLY_HTML])
    assert r.exhausted == []
    assert r.unresolved == []
    assert r.complete is True
    assert len(r.roots) == 3, "one source and two sinks"


def test_the_crossing_carries_no_variable_but_the_data_hop_does(graph):
    """Where a TypeScript variable cut can and cannot land — the fact the next test rests on.

    ``TS_PARAM_IN`` reports ``var`` as ``None`` here (the emitter puts the parameter's name in the
    port's ``of``), so ``coalesce(r.var, '')`` makes a crossing uncuttable by name. The ``TS_DDG`` hop
    *does* carry ``htmlString``, which is why cutting the source variable works at all — it severs the
    step *before* the crossing, not the crossing.

    Stated as an assertion rather than a comment so that an emitter that starts populating ``var`` on
    crossings shows up here as a deliberate re-measurement instead of as prose that quietly went stale.
    """
    hops = [h for p in graph.taint(SOURCES, SINKS).paths for h in p.hops]
    assert {(h.via, h.var) for h in hops} == {("data", "htmlString"), ("argument", None)}


def test_a_variable_cut_on_the_guards_parameter_refutes_both_pairs(graph):
    """Shape (a): the guard's own parameter is the source, so the cut severs at the first hop.

    Both pairs go to zero **and are certified** — the strongest thing ``taint()`` says, and the one
    that closes an alert — so the certificate's preconditions are asserted beside it: no witness, no
    diagnostic, and ``depth`` was never set.
    """
    r = graph.taint(SOURCES, SINKS, [("htmlString", "sanitizeHtmlIfNeeded")])
    assert r.paths == []
    assert sorted(r.exhausted) == sorted([TO_SANITIZE_HTML, TO_IS_PROBABLY_HTML])
    assert r.unresolved == []
    assert r.complete is True


def test_a_callable_cut_takes_one_pair_and_leaves_its_sibling(graph):
    """The assertion that needed a real graph, and the reason this file exists.

    One ``taint()`` call, two pairs, a cut aimed at exactly one of them. The ``isProbablyHTML`` pair
    is refuted and certified; the ``sanitizeHtml`` pair still has its witness **in the same result**.

    This is what a post-filter on returned rows cannot do (it would drop the row but never book the
    refutation) and what an application-wide cut cannot do either (it would take both). ``complete``
    stays ``True`` because a refuted pair is a finished pair, not a truncated one.
    """
    r = graph.taint(SOURCES, SINKS, ["isProbablyHTML"])
    assert _answered(r) == [AT_SANITIZE_HTML], "the sibling pair must survive the cut"
    assert r.exhausted == [TO_IS_PROBABLY_HTML]
    assert r.complete is True
    other = graph.taint(SOURCES, SINKS, [SANITIZE_HTML])
    assert _answered(other) == [AT_IS_PROBABLY_HTML], "and the cut is symmetric"
    assert other.exhausted == [TO_SANITIZE_HTML]


def test_an_unrelated_callable_cut_leaves_the_batch_untouched(graph):
    """Over-cutting is the one output this leg refuses, so a cut that must do nothing gets a test.

    ``validateNonEmpty`` is a real superset validator on no path between these three callables.
    ``under_callable``'s three disjuncts (``n.id = q``, ``+ '@'``, ``+ '/'``) exist so a prefix match
    cannot spill into a sibling whose id merely starts with the same characters — at 45 ``validate*``
    candidates in this corpus, a bare ``STARTS WITH`` would have plenty to spill onto.
    """
    r = graph.taint(SOURCES, SINKS, ["validateNonEmpty"])
    assert sorted(len(p.hops) for p in r.paths) == [2, 2]
    assert r.exhausted == [] and r.complete is True


def test_a_bounded_search_certifies_nothing_even_when_it_finds_nothing(graph):
    """``depth`` bounds the search; ``exhausted`` is empty whenever it is set (Ruling F).

    ``depth=1`` is one hop short of both witnesses, so this is the case that matters most: zero paths
    and zero certificates. A ``taint()`` that filled ``exhausted`` here would tell triage that a flow
    it never looked far enough to see does not exist — which on this very pair would be wrong twice
    over. ``depth=2`` finds both, proving the bound and not the graph was the reason.
    """
    short = graph.taint(SOURCES, SINKS, depth=1)
    assert short.paths == []
    assert short.exhausted == [], "no witness under a bound is not a refutation"
    assert short.complete is True, "a bounded search that truncated nothing is complete"
    assert graph.taint(SOURCES, SINKS, depth=2).paths, "one more hop and both pairs witness"
    cut = graph.taint(SOURCES, SINKS, ["isProbablyHTML"], depth=2)
    assert len(cut.paths) == 1 and cut.exhausted == [], "a cut under a bound still certifies nothing"


def test_the_cap_is_per_pair_so_two_pairs_both_answer_at_one(graph):
    """``collect(p)[0..$cap]`` against a flat ``LIMIT``, on a real graph.

    ``max_paths=1`` returns **two** rows here — one per pair — and ``complete`` stays ``True`` because
    neither pair had a second witness to drop. A flat ``LIMIT 1`` returns one row and reports the
    other pair as unwitnessed. That is the shape of the harm the per-pair cap exists to prevent, and
    it is invisible on a single-pair fixture.
    """
    r = graph.taint(SOURCES, SINKS, max_paths=1)
    assert _answered(r) == sorted([AT_SANITIZE_HTML, AT_IS_PROBABLY_HTML])
    assert r.complete is True, "nothing was truncated, so nothing is flagged"
    assert r.exhausted == []


def test_the_bare_sink_name_is_ambiguous_and_says_so(graph):
    """Addressing, not traversal — but the failure mode it prevents is a taint result in the wrong
    module.

    superset exports ``sanitizeHtml`` twice (``superset-ui-core/src/utils/html`` and
    ``plugin-chart-echarts/src/utils/series``). The SDK refuses the bare name rather than choosing,
    and the message names both candidates, which is what lets a caller write the dotted form the rest
    of this file uses. A resolver that picked the first match would return a flow into a chart plugin
    for a question asked about the core utility.
    """
    from cldk.utils.exceptions.exceptions import AmbiguousName

    with pytest.raises(AmbiguousName) as excinfo:
        graph.taint(SOURCES, [("htmlString", "sanitizeHtml")])
    assert "packages/superset-ui-core/src/utils/html.sanitizeHtml" in str(excinfo.value)
    assert "plugins/plugin-chart-echarts/src/utils/series.sanitizeHtml" in str(excinfo.value)
