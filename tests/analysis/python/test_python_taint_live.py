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

"""``taint()``'s two Python walks, measured against a real graph and a real analysis.

The offline suite (``test_python_taint.py``) pins everything ``taint()`` does *around* a walk. This
one pins the walks themselves, and it needs both backends in one file because the only claim worth
making about two implementations of one hook is that they answer identically -- and "identically"
means the same hop chains and the same refuted pairs, not two truthy results.

**The fixture is small on purpose and its source is not free.** ``fixture/proj/app.py`` carries a
caller (``Handler.handle``) whose parameter reaches a sink through two routes, but a *caller's*
parameter is unusable as a source here: its ``@formal_in`` port and its ``@entry`` def-site are
disjoint upstream, so the port a selector resolves to reaches no argument at all
(codeanalyzer-python#204). Sourcing from ``("user_input", "handle")`` would therefore measure that
defect and record a wrong expectation as a passing assertion. The usable witnesses start at a
**callee's** parameter, and the 6-hop shape they take crosses two call boundaries in both
directions::

    formal_in -> body -> formal_out -[return]-> actual_out -> stmt -> actual_in -[argument]-> formal_in

Path counts on this graph are also **not route counts**: #204's secondary finding is that a
reaching-definition ``var`` names the *use* rather than the def, which inflates them. So the numbers
below are measured, and what they are asserted against is a hop chain wherever a chain will do.

Every ref is measured from the graph through ``resolve_value``. Nothing here hardcodes a ``can://``
id: the leg-4a ledger did, its fixture was regenerated, and those ids now name nothing.
"""

import os
from pathlib import Path

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

#: The fixture's own container, not the shared live graph the rest of this directory attaches to:
#: leg 4b needs an application whose *shape* is known callable-by-callable, and odoo-slim-19 is
#: neither small enough to enumerate nor stable enough to name a pair in. Defaults point at the
#: container ``fixture/README.md`` builds; 7687 is deliberately not among them.
TAINT_URI = os.environ.get("CLDK_TEST_TAINT_NEO4J_URI", "bolt://localhost:7697")
TAINT_USER = os.environ.get("CLDK_TEST_TAINT_NEO4J_USER", "neo4j")
TAINT_PASSWORD = os.environ.get("CLDK_TEST_TAINT_NEO4J_PASSWORD", "cldkleg4btest")
TAINT_APP = os.environ.get("CLDK_TEST_TAINT_NEO4J_APP", "leg4b")
PROJ = Path(__file__).resolve().parents[3] / ".superpowers" / "sdd" / "2026-09-09-leg-4b-taint" / "fixture" / "proj"


def _fixture_graph_present() -> bool:
    """True iff a server answers at ``TAINT_URI`` *and* holds ``TAINT_APP``.

    Connectivity alone is not enough, for ``test_e2e_neo4j_live.py``'s reason: a developer with
    another container on this port would otherwise see these assertions run against a graph that
    has none of the callables they name, and read the failures as defects.
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
                found = session.run("MATCH (a:PyApplication {name: $n}) RETURN count(a) AS c", n=TAINT_APP).single()
                return bool(found and found["c"])
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 - any connection/auth failure => skip, never fail
        return False


pytestmark = pytest.mark.skipif(
    not PROJ.is_dir() or not _fixture_graph_present(),
    reason=(f"no leg-4b taint fixture: needs {PROJ} on disk and Neo4j at {TAINT_URI} holding {TAINT_APP!r} " "(see fixture/README.md; set CLDK_TEST_TAINT_NEO4J_URI / _USER / _PASSWORD / _APP)"),
)

#: The two sources and the two sinks the fixture can actually witness, as a caller writes them.
SOURCES = [("raw", "scrub"), ("raw", "relay")]
SINKS = [("cleaned", "run_query"), ("note", "run_query")]

#: The one pair on this fixture with more than one witness, so the only pair whose *ordering* is
#: observable: ``raw@scrub -> cleaned@run_query`` has two, and they separate in
#: :func:`~cldk.analysis.python.backend.hop_sort_key` at hop 2 (``<return>`` against ``app::ch.*``).
CAP_PAIR = ([("raw", "scrub")], [("cleaned", "run_query")])


# ``.backend`` and not the facade: ``taint()`` is a backend method until the facade method lands
# (leg 4b Task 8 -- ``PythonAnalysis`` delegates one accessor at a time, and this is the last one),
# and a suite that waited for the delegation would leave the two walks untested in between.
@pytest.fixture(scope="module")
def graph():
    """The fixture graph, attached. Module-scoped: attaching runs three probes and a module load."""
    facade = CLDK.python(backend=Neo4jConnectionConfig(uri=TAINT_URI, username=TAINT_USER, password=TAINT_PASSWORD, application_name=TAINT_APP))
    yield facade.backend
    facade.backend.close()


@pytest.fixture(scope="module")
def local():
    """The same project analysed in process, at the level the SDG needs. Module-scoped for the same
    reason: the analyzer runs once per construction."""
    return CLDK.python(project_path=str(PROJ), analysis_level="system_dependency_graph").backend


def _chains(result):
    """A result's witnesses as sorted ``via``-chains -- the vocabulary a caller reads, and the one
    thing two backends can be compared in without comparing their ids.

    Sorted, and ``via`` only, so this is **blind to order by construction**: all six witnesses on
    this fixture share the chain ``("data","data","return","data","data","argument")``. Anything
    about *which* witness came first has to use :func:`_keys`.
    """
    return sorted(tuple(h.via for h in p.hops) for p in result.paths)


def _keys(result):
    """A result's witnesses in order, each collapsed to what ``hop_sort_key`` actually ranks on.

    ``(via, var, position)`` per hop -- the triple
    :func:`~cldk.analysis.python.backend.hop_sort_key` builds, which is what makes a tie-break
    disagreement between the local replay's ``sorted(..., key=(via, var, to))`` and Cypher's
    ``ORDER BY length(p), key`` visible. Unsorted, unlike :func:`_chains`: the order *is* the claim.

    The ``can://`` application segment is dropped because the two backends legitimately disagree
    about it and only about it -- a local run names the application after the project directory
    (``proj``), and the graph after the ``PyApplication`` it was imported as (``leg4b``) -- so
    ``ref.split("/", 3)[3]`` keeps the whole addressable position and discards the one field that is
    a naming fact rather than an ordering one. Everything below it is identical, measured.
    """
    return [[(h.via, h.var, h.to.ref.split("/", 3)[3]) for h in p.hops] for p in result.paths]


def test_both_backends_agree_on_the_witnesses_and_on_the_refutations(local, graph):
    """Agreeing on a predicate is not agreeing on a set: assert the hop chains and the exhausted
    pairs, not truthiness."""
    got = {}
    for name, backend in (("local", local), ("graph", graph)):
        r = backend.taint(SOURCES, SINKS, max_paths=10)
        got[name] = (_chains(r), sorted(r.exhausted), r.complete)
    assert got["local"] == got["graph"]
    assert len(got["local"][0]) == 6, "2 + 2 + 1 + 1, from the measured table"
    assert got["local"][1] == [], "every pair has a witness"
    assert got["local"][2] is True


def test_the_six_witnesses_all_cross_two_call_boundaries_in_both_directions(local, graph):
    """The shape, not just the count. A walk that stopped at a call boundary would still return
    *some* rows on this graph; only the chain says it went in and came back out."""
    for backend in (local, graph):
        assert _chains(backend.taint(SOURCES, SINKS, max_paths=10)) == [("data", "data", "return", "data", "data", "argument")] * 6


@pytest.mark.parametrize("backend_name", ["local", "graph"])
def test_the_walk_returns_one_row_past_the_cap_so_truncation_is_never_silent(request, backend_name):
    """A walk that caps at ``max_paths`` instead of ``max_paths + 1`` returns a full-looking result
    with ``complete=True`` -- a silent bound, which E5 exists to forbid. ``taint()`` cannot detect
    it, so each of the five implementations is tested here or nowhere."""
    backend = request.getfixturevalue(backend_name)
    at_one = backend.taint(*CAP_PAIR, max_paths=1)
    assert len(at_one.paths) == 1 and at_one.complete is False
    at_two = backend.taint(*CAP_PAIR, max_paths=2)
    assert len(at_two.paths) == 2 and at_two.complete is True
    # *Which* witness survived, not just how many. A cap that kept the other one passes every count
    # assertion above, and a caller who reads ``paths[0]`` as "the shortest route" reads a walk the
    # ordering never promised. ``<return>`` is measured, not guessed: hop 2 of the surviving witness
    # goes out through ``scrub``'s formal_out, and its sibling goes through the module-global
    # ``app::ch.*`` -- and ``'<'`` sorts before ``'a'``, which is the whole tie-break.
    assert at_one.paths[0].hops[1].var == "<return>"
    many = backend.taint(*CAP_PAIR, max_paths=5)
    assert at_one.paths == many.paths[:1], "the taint cap is not a prefix of one total order"
    assert len(many.paths) == 2 and many.complete is True


@pytest.mark.parametrize("backend_name", ["local", "graph"])
def test_a_prolific_pair_does_not_starve_a_sparse_one(request, backend_name):
    """Four pairs and six witnesses against ``max_paths=1``: a flat ``LIMIT $cap`` returns two rows
    for the whole batch, so two of the four pairs come back with nothing -- reported as no flow,
    which in triage closes a live alert."""
    backend = request.getfixturevalue(backend_name)
    r = backend.taint(SOURCES, SINKS, max_paths=1)
    pairs = {(p.hops[0].frm.ref, p.hops[-1].to.ref) for p in r.paths}
    assert len(pairs) == 4, "a flat cap cannot produce four pairs from a cap of two"
    assert r.complete is False, "the two-witness pairs truncated, and a bound is never silent (E5)"


@pytest.mark.parametrize("backend_name", ["local", "graph"])
def test_a_callable_sanitizer_cuts_its_own_pair_and_leaves_the_sibling_alone(request, backend_name):
    """Over-cutting is the one output this leg exists to refuse, so a cut that closed both pairs
    would pass a naive "the sanitizer worked" assertion while being the failure."""
    backend = request.getfixturevalue(backend_name)
    r = backend.taint(SOURCES, [("note", "run_query")], sanitizers=["scrub"])
    scrub_src = backend.resolve_value("raw", within="scrub").ref
    relay_src = backend.resolve_value("raw", within="relay").ref
    assert len(r.paths) == 1, "the relay route is not sanitized and must survive"
    assert r.paths[0].hops[0].frm.ref == relay_src
    assert all(scrub_src != h.frm.ref for p in r.paths for h in p.hops)
    # ``exhausted`` names a pair by the two SELECTORS the caller wrote (``taint_verdict``), so the
    # cut pair reads as the names, not the refs -- and both sources are spelled ``raw``, which is
    # exactly why ``roots`` is what tells two same-named pairs apart.
    assert r.exhausted == [("raw", "note")]
    assert r.complete is True, "one pair refuted and one witnessed is a clean batch"


def test_both_backends_rank_the_two_witnesses_the_same_way(local, graph):
    """The cap test above holds each backend to its own order; nothing yet holds the two to *each
    other's*.

    Two implementations of one ordering can each be internally consistent and still disagree about
    a tie -- the local replay sorts branches by ``(via, var, to)`` in Python, the graph sorts whole
    paths by ``path_order(P)`` in Cypher, and on a tie the two could hand a caller different
    ``paths[0]`` from the same question. Since ``taint_verdict`` truncates with
    ``witnesses[:max_paths]``, that disagreement is exactly a disagreement about which witness a
    capped result reports, and ``_chains`` cannot see it.
    """
    got = {name: (_keys(b.taint(*CAP_PAIR, max_paths=1)), _keys(b.taint(*CAP_PAIR, max_paths=5))) for name, b in (("local", local), ("graph", graph))}
    assert got["local"] == got["graph"], "the two walks rank the same two witnesses differently"
    capped, full = got["local"]
    assert len(full) == 2 and capped == full[:1]
