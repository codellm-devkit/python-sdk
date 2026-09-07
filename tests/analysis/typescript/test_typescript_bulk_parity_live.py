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

"""Live dual-backend parity for the four bulk/projected accessors (#298):
``get_callables_overview`` / ``get_method_bodies`` / ``get_decorated_callables`` /
``get_callsites_for``.

This is the acceptance bar the spec names for this feature: the in-memory backend
(:class:`TSCodeanalyzer`) and the read-only Neo4j backend (:class:`TSNeo4jBackend`) must answer
these four queries identically over the *same* tracked sample app
(``tests/resources/typescript/application``) — never the slim ``analysis.json`` fixture used by
the rest of this package's (mocked-subprocess) tests, so the emit and the in-memory reference
describe the exact same code.

Reuses the live harness idiom of ``test_typescript_neo4j_backend.py`` verbatim -- and therefore
**WRITES the graph** exactly as that module does: the same ``_populate_neo4j`` out-of-band loader
(``codeanalyzer-typescript --emit neo4j`` over Bolt), the same teardown scoped to the application's
two id prefixes, and the same gate on ``CLDK_TEST_NEO4J_WRITE_URI`` / ``_WRITE_USER`` /
``_WRITE_PASSWORD`` with no defaults (#324). It never runs against a graph someone else deployed:

    CLDK_TEST_NEO4J_WRITE_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_WRITE_USER=neo4j \
    CLDK_TEST_NEO4J_WRITE_PASSWORD=test \
    pytest tests/analysis/typescript/test_typescript_bulk_parity_live.py

(e.g. `podman run -d -p 7691:7687 -e NEO4J_AUTH=neo4j/test neo4j:5`).
"""

import logging

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, Neo4jConnectionConfig
from cldk.analysis.commons.results import SliceNode

from .test_typescript_neo4j_backend import (
    APP_NAME,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    _neo4j_reachable,
    _populate_neo4j,
    _teardown_application,
)

logging.getLogger("neo4j").setLevel(logging.ERROR)

pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason=(
        "this module WRITES the graph (it runs codeanalyzer-typescript --emit neo4j and deletes the "
        "application afterwards); set CLDK_TEST_NEO4J_WRITE_URI / _WRITE_USER / _WRITE_PASSWORD to a "
        "disposable server to run it -- there are no defaults"
    ),
)


def _overview_tuple(o):
    """A hashable, order-independent projection of one ``TSCallableOverview`` row.

    ``decorators`` is compared as a *sorted* tuple, not as-is: the Neo4j side collects decorator
    names with ``collect(DISTINCT d.name)``, which carries no row-order guarantee, while the
    in-memory side preserves declaration order — so decorator order is deliberately not part of
    the parity contract, only the set of names is. Every other field is a plain scalar, so the
    remaining tuple positions already compare exactly.
    """
    return (
        o.signature,
        o.name,
        o.owner_signature,
        o.owner_kind,
        o.kind,
        o.path,
        o.start_line,
        o.end_line,
        tuple(sorted(o.decorators)),
        o.is_exported,
        o.is_async,
        o.is_static,
        o.accessibility,
    )


@pytest.fixture(scope="module")
def ts_dual(typescript_application, tmp_path_factory):
    """``(ref, neo)``: the in-memory backend and a Neo4j backend, both over the SAME tracked
    sample app (``tests/resources/typescript/application``) — the emit and the in-memory reference
    must describe identical code, so neither side may fall back to the slim fixture JSON.

    **The local reference is level 4** (leg 2.5b). It was ``call_graph`` while the emit was run with
    ``-a 2``; codeanalyzer-typescript 1.3.0 refuses a level alongside ``--emit neo4j`` and always
    projects at full depth, so a level-2 reference now compares two *analysis levels* rather than
    two backends — measured: ``locate("src/controllers.ts", 4)`` returned ``body=None`` locally
    (level 2 emits no body nodes at all) against the graph's ``@entry``. The level is what makes
    both sides hold the same facts; the parity assertions are unchanged.
    """
    _populate_neo4j(typescript_application)

    cache_dir = tmp_path_factory.mktemp("ts_bulk_parity_cache")
    ref = CLDK.typescript(
        project_path=typescript_application,
        eager=True,
        analysis_level=AnalysisLevel.system_dependency_graph,
        backend=CodeAnalyzerConfig(cache_dir=str(cache_dir)),
    )

    neo = CLDK.typescript(
        project_path=typescript_application,
        analysis_level=AnalysisLevel.system_dependency_graph,
        backend=Neo4jConnectionConfig(
            uri=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD,
            application_name=APP_NAME,
        ),
    )
    yield ref, neo
    neo.backend.close()
    _teardown_application()


#: Four tests below used to carry a ``CODE_ONE_LINE_SHORT`` ``xfail(strict=True)``:
#: codeanalyzer-typescript 1.3.0 and 1.4.0 projected ``:TSCallable.code`` one line short of the
#: callable's own span (the graph text was the local text minus its final ``"\n}"``), so every
#: source-text accessor truncated over Neo4j. 1.5.0 fixed it as part of cants#179, and the marks
#: are gone rather than left behind: with the pin at 1.5.0 all four **XPASS(strict)**, which is
#: what ``strict=True`` was there to make loud. The four are now ordinary parity assertions.
#:
#: The projection's second gap, and a different one: the decorator call site
#: ``@Param("id")`` reaches the graph carrying only its lines and its resolved callee
#: (``method_name=''``, empty ``argument_types``, ``return_type=None``, columns ``-1``) -- the
#: call-site lossiness ``TSNeo4jBackend``'s module docstring already records.
CALLSITE_FACETS_ABSENT = pytest.mark.xfail(
    strict=True,
    reason="the Neo4j projection carries no call-site facets for a decorator call site (method_name, argument_types, return_type, columns); documented lossiness, not a regression",
)


def test_callables_overview_parity(ts_dual):
    ref, neo = ts_dual
    ref_rows = {_overview_tuple(o) for o in ref.get_callables_overview()}
    neo_rows = {_overview_tuple(o) for o in neo.get_callables_overview()}
    assert ref_rows, "sample app should have at least one callable"
    assert ref_rows == neo_rows


def test_method_bodies_parity(ts_dual):
    ref, neo = ts_dual
    sigs = [o.signature for o in ref.get_callables_overview()]
    assert ref.get_method_bodies(sigs) == neo.get_method_bodies(sigs)
    # unknown signatures are omitted identically on both backends
    assert ref.get_method_bodies(["nope.not.here"]) == neo.get_method_bodies(["nope.not.here"]) == {}


def test_decorated_callables_parity(ts_dual):
    ref, neo = ts_dual
    markers = sorted({d for o in ref.get_callables_overview() for d in o.decorators})
    assert markers, "sample app fixture should carry at least one decorator (e.g. Controller/Get)"

    ref_rows = {_overview_tuple(o) for o in ref.get_decorated_callables(markers)}
    neo_rows = {_overview_tuple(o) for o in neo.get_decorated_callables(markers)}
    assert ref_rows, "at least one callable should match the markers actually in use"
    assert ref_rows == neo_rows

    # a marker nothing carries yields an identical empty result on both backends
    assert ref.get_decorated_callables(["__no_such_decorator__"]) == neo.get_decorated_callables(["__no_such_decorator__"]) == []


def _callsite_tuple(cs):
    """A hashable, fully-fielded projection of one ``TSCallsite`` -- used to compare call-site
    lists content-for-content, order-independent (see the comment on ``test_callsites_parity``
    for why order is deliberately not part of this comparison).
    """
    return (
        cs.method_name,
        cs.receiver_expr,
        cs.receiver_type,
        tuple(cs.argument_types),
        tuple(cs.type_arguments),
        cs.return_type,
        cs.callee_signature,
        cs.is_constructor_call,
        cs.is_optional_chain,
        cs.start_line,
        cs.start_column,
        cs.end_line,
        cs.end_column,
    )


@CALLSITE_FACETS_ABSENT
def test_callsites_parity(ts_dual):
    ref, neo = ts_dual
    sigs = [o.signature for o in ref.get_callables_overview()]
    cs_ref = ref.get_callsites_for(sigs)
    cs_neo = neo.get_callsites_for(sigs)
    assert set(cs_ref) == set(cs_neo)
    for sig in cs_ref:
        # Content equality as a multiset, NOT list-order equality. `TSAnalysisBackend.get_callsites_for`
        # (backend.py) never contracts an order beyond "each existing signature gets an entry" --
        # and a live run surfaced a real case where order genuinely differs: for a receiver chain
        # (sample app's `builder.add("a").add("b").build()`), the outer call's span *starts* at the
        # same (start_line, start_column) as its own receiver sub-expression's call, so
        # TSNeo4jBackend's `ORDER BY cs.start_line, cs.start_column` cannot disambiguate them and
        # returns the tied pair in the opposite relative order from the in-memory backend (which
        # preserves the analyzer's own analysis.json array order). No CallSite property (graph or
        # JSON) carries a stable ordinal to reconstruct the "true" order for such ties, so recovering
        # it would need an upstream codeanalyzer-typescript emitter change (out of scope here) --
        # matching the existing precedent in test_typescript_neo4j_bulk.py
        # (test_callsites_for_groups_by_owner_and_keeps_empty_entry), which also only ever asserts
        # callsite *sets*, never list order.
        ref_multiset = [_callsite_tuple(c) for c in cs_ref[sig]]
        neo_multiset = [_callsite_tuple(c) for c in cs_neo[sig]]
        assert sorted(ref_multiset, key=str) == sorted(neo_multiset, key=str), f"call sites for {sig} differ"

    # unknown signatures are omitted identically on both backends
    assert ref.get_callsites_for(["nope.not.here"]) == neo.get_callsites_for(["nope.not.here"]) == {}


# =====================================================================================
# Leg 2.5b Task 1: the addressing surface answers identically on both backends -- including on
# the miss paths, where the two must raise the same exception type naming the same subject.
#
# This is the only harness in the repo that runs *both* backends over the same code, so it is
# where the parity claim belongs. It writes the graph (see the module docstring), so it skips
# unless CLDK_TEST_NEO4J_WRITE_* names a disposable server; the read-only live suite on the
# reference graph exercises the Neo4j half at scale, and the offline suite the local half.
# =====================================================================================
def _same_raise(ref_call, neo_call, subject):
    """Both backends refuse, with the same exception type **and** the same subject named.

    ``subject`` is required and asserted (leg 2.5b review, finding 2). The docstring promised it
    from the start and the body only ever checked the type, which is how ``get_source``'s two miss
    messages came to name two different things -- ``'can://typescript/application'`` locally against
    ``'application'`` over Neo4j -- with this harness green. What a caller reads is the message; a
    parity harness that never reads one cannot see a message diverge.
    """
    with pytest.raises(Exception) as r:
        ref_call()
    with pytest.raises(Exception) as n:
        neo_call()
    assert type(r.value) is type(n.value), f"{type(r.value).__name__} locally, {type(n.value).__name__} over Neo4j"
    assert subject in str(r.value), f"the local message does not name {subject!r}: {str(r.value)!r}"
    assert subject in str(n.value), f"the Neo4j message does not name {subject!r}: {str(n.value)!r}"
    return r.value, n.value


def _positions(ref):
    """Every ``(module key, line)`` a callable's first line gives, plus a module-scope line and a
    file the analysis has no module for -- the four ``locate`` outcomes over the whole sample app."""
    out = [(o.path, o.start_line) for o in ref.get_callables_overview() if o.start_line > 0]
    out += [(path, 1) for path in ref.get_symbol_table()]
    return out + [("src/definitely-not-here.ts", 3)]


def test_locate_many_parity_over_every_callable_and_every_module(ts_dual):
    ref, neo = ts_dual
    positions = _positions(ref)
    ref_out, neo_out = ref.locate_many(positions), neo.locate_many(positions)
    assert len(ref_out) == len(neo_out) == len(positions)
    for (path, line), a, b in zip(positions, ref_out, neo_out):
        where = f"{path}:{line}"
        assert a.module.path == b.module.path, where
        assert (a.callable and a.callable.signature) == (b.callable and b.callable.signature), where
        assert (a.type and a.type.signature) == (b.type and b.type.signature), where
        assert (a.body and a.body.id) == (b.body and b.body.id), where
        assert (a.body and a.body.kind) == (b.body and b.body.kind), where
        assert (a.body and a.body.callee) == (b.body and b.body.callee), where
        assert a.node_id == b.node_id, where
        # The one honest divergence, documented on both backends: the graph carries no module text,
        # so a module-scope position is `""` plus a second diagnostic there and the module's source
        # locally. Everywhere else the diagnostics agree exactly.
        if [d.code for d in a.diagnostics] == ["module_scope"]:
            assert [d.code for d in b.diagnostics] == ["module_scope", "module_source_unavailable"], where
            assert b.source == "" and a.source, where
        else:
            assert [d.code for d in a.diagnostics] == [d.code for d in b.diagnostics], where
            assert a.source == b.source, where
        # Lines are real on both; columns and byte offsets are placeholders over Neo4j only.
        assert a.span.start[0] == b.span.start[0] and a.span.end[0] == b.span.end[0], where


def test_resolve_callable_parity_over_every_callable(ts_dual):
    ref, neo = ts_dual
    for o in ref.get_callables_overview():
        a, b = ref.resolve_callable(o.signature), neo.resolve_callable(o.signature)
        assert (a.callable, a.kind, a.name, a.file, a.line, a.ref) == (b.callable, b.kind, b.name, b.file, b.line, b.ref)


def test_resolve_callable_miss_paths_agree(ts_dual):
    ref, neo = ts_dual
    _same_raise(lambda: ref.resolve_callable("noSuchCallable"), lambda: neo.resolve_callable("noSuchCallable"), "noSuchCallable")
    a, b = _same_raise(lambda: ref.resolve_callable("describe"), lambda: neo.resolve_callable("describe"), "describe")
    assert a.candidates == b.candidates, "the two backends resolved over different candidate sets"
    a, b = _same_raise(
        lambda: ref.resolve_callable("describe", in_module="no/such/module.ts"),
        lambda: neo.resolve_callable("describe", in_module="no/such/module.ts"),
        "no/such/module.ts",
    )
    assert "in_module" in str(a) and "in_module" in str(b)


def test_resolve_callable_scoping_keywords_agree(ts_dual):
    ref, neo = ts_dual
    for o in ref.get_callables_overview():
        if o.owner_signature:
            assert ref.resolve_callable(o.name, in_class=o.owner_signature).ref == neo.resolve_callable(o.name, in_class=o.owner_signature).ref
        dotted = o.path.rsplit(".", 1)[0].replace("/", ".")
        assert ref.resolve_callable(o.signature, in_module=dotted).ref == neo.resolve_callable(o.signature, in_module=dotted).ref
        assert ref.resolve_callable(o.signature, in_module=o.path).ref == neo.resolve_callable(o.signature, in_module=o.path).ref


def test_resolve_value_parity_over_every_callable(ts_dual):
    """Every named value entering every callable, resolved on both.

    ``formal_in`` vertices -- the whole domain of ``resolve_value`` -- first exist at analysis
    level 4, which is the level ``ts_dual`` now builds both sides at (leg 2.5b; it used to build a
    second level-4 backend of its own here). The parity assertion is on the *values*; the level is
    just what makes both sides hold them.
    """
    ref, neo = ts_dual
    seen = 0
    for o in ref.get_callables_overview():
        owner = o.owner_signature or o.signature.rsplit(".", 1)[0]
        callable_ = ref.get_method(owner, o.name)
        for value in sorted({n.of for n in (callable_.body if callable_ else {}).values() if n.kind == "formal_in" and n.of}):
            a, b = ref.resolve_value(value, within=o.signature), neo.resolve_value(value, within=o.signature)
            assert (a.kind, a.name, a.defined_in, a.callable, a.ref) == (b.kind, b.name, b.defined_in, b.callable, b.ref)
            seen += 1
        _same_raise(lambda: ref.resolve_value("noSuchValue", within=o.signature), lambda: neo.resolve_value("noSuchValue", within=o.signature), "noSuchValue")
    assert seen, "the level-4 reference carried no formal_in vertex at all; this test proved nothing"


def test_get_source_parity_and_the_one_documented_divergence(ts_dual):
    ref, neo = ts_dual
    for o in ref.get_callables_overview():
        try:
            expected = ref.get_source(o.signature)
        except KeyError:
            _same_raise(lambda: ref.get_source(o.signature), lambda: neo.get_source(o.signature), o.signature)
            continue
        assert neo.get_source(o.signature) == expected
        assert neo.get_source(ref.resolve_callable(o.signature).ref) == expected
    # Below callable granularity the two differ, deliberately and loudly: the local backend slices
    # the module text, the graph has none to slice and says so with a distinct exception type.
    body = next((r.node_id for r in ref.locate_many(_positions(ref)) if r.node_id), None)
    assert body, "no position in the sample app landed on a body node"
    assert ref.get_source(body)
    with pytest.raises(NotImplementedError):
        neo.get_source(body)


def test_describe_parity(ts_dual):
    ref, neo = ts_dual
    names = [o.signature for o in ref.get_callables_overview()]
    ref_nodes = [ref.resolve_callable(n) for n in names]
    neo_nodes = [neo.resolve_callable(n) for n in names]
    assert [n.ref for n in ref_nodes] == [n.ref for n in neo_nodes]
    assert [n.source for n in ref.describe(ref_nodes)] == [n.source for n in neo.describe(neo_nodes)]
    assert ref.describe([]) == neo.describe([]) == []



def test_the_addressing_miss_paths_name_the_same_subject_on_both_backends(ts_dual):
    """The miss halves of ``get_source`` and ``describe``, split out from the success paths.

    It was split out because an ``xfail(strict=True)`` swallows every assertion in the test it
    marks, so while codeanalyzer-typescript#179 was open a miss-path divergence inside one would
    have been invisible. #179 is fixed and the marks are gone, but the split stays: this is where
    finding 2's fix is verified: the local backend used to name the subject
    ``'can://typescript/application'`` where the graph named ``'application'``.
    """
    ref, neo = ts_dual
    a, b = _same_raise(lambda: ref.get_source("no.such.node"), lambda: neo.get_source("no.such.node"), "no.such.node")
    assert str(a) == str(b), "the two backends name different subjects for the same miss"
    stale = SliceNode(file="x.ts", line=1, callable="x", kind="callable", name="x", ref="can://typescript/nope/x")
    # The subject is the readable position, never ``stale.ref`` -- a ``describe`` miss names the
    # node the caller can see (``x (x.ts:1)``) and keeps the ``can://`` id out of the message (E6).
    _same_raise(lambda: ref.describe([stale]), lambda: neo.describe([stale]), "x (x.ts:1)")


def test_has_resolution_edges_agrees(ts_dual):
    ref, neo = ts_dual
    assert ref.has_resolution_edges is neo.has_resolution_edges is True


# =====================================================================================
# Leg 2.5b Task 2: the dataflow surface answers identically on both backends.
#
# cfg/cdg/ddg first exist at analyzer level 3 and the SDG overlays at level 4, and ``--emit neo4j``
# is always full depth -- which is why ``ts_dual``'s local reference is now built at level 4 too
# (see its docstring). Comparing a shallower local view against the graph would be comparing
# analysis levels, not backends.
# =====================================================================================
@pytest.fixture(scope="module")
def ref_l4(ts_dual):
    """The level-4 local backend ``ts_dual`` already builds — named for what the dataflow tests
    need from it, so a reader does not have to check the level at every call site."""
    return ts_dual[0]


def _signatures(ref_l4):
    return [o.signature for o in ref_l4.get_callables_overview()]


def _entering_values(ref_l4, signature):
    """Every named value entering ``signature``, read off the level-4 model rather than through the
    accessor under test."""
    overview = next(o for o in ref_l4.get_callables_overview() if o.signature == signature)
    owner = overview.owner_signature or signature.rsplit(".", 1)[0]
    callable_ = ref_l4.get_method(owner, overview.name)
    return sorted({n.of for n in (callable_.body if callable_ else {}).values() if n.kind == "formal_in" and n.of})


@pytest.mark.parametrize("accessor,key", [("get_cfg", lambda e: (e.src, e.dst, e.kind)), ("get_cdg", lambda e: (e.src, e.dst)), ("get_ddg", lambda e: (e.src, e.dst, e.var, tuple(e.prov)))])
def test_the_per_callable_graphs_are_identical_on_both_backends(ref_l4, ts_dual, accessor, key):
    """Every callable of the sample app, every edge, in the accessor's canonical order — the list,
    not the set, because the order is what makes a *page* mean the same thing on both backends."""
    _, neo = ts_dual
    seen = 0
    for sig in _signatures(ref_l4):
        a, b = getattr(ref_l4, accessor)(sig), getattr(neo, accessor)(sig)
        assert a.total == b.total, sig
        assert [key(e) for e in a.edges] == [key(e) for e in b.edges], sig
        assert a.next_cursor == b.next_cursor and a.complete == b.complete, sig
        seen += a.total
    assert seen, "the sample app carried no edges at all; this test proved nothing"


def test_paging_agrees_edge_for_edge_and_cursor_for_cursor(ref_l4, ts_dual):
    """A cursor minted on one backend names the same position on the other, which is only true if
    both sort by the same key — the point of ``EdgeOrder`` holding the two spellings together."""
    _, neo = ts_dual
    sig = max(_signatures(ref_l4), key=lambda s: ref_l4.get_ddg(s).total)
    assert ref_l4.get_ddg(sig).total > 2, "no callable in the sample app is big enough to page"
    cursor, pages = None, 0
    while True:
        a = ref_l4.get_ddg(sig, page_size=2, cursor=cursor)
        b = neo.get_ddg(sig, page_size=2, cursor=cursor)
        assert [(e.src, e.dst, e.var, tuple(e.prov)) for e in a.edges] == [(e.src, e.dst, e.var, tuple(e.prov)) for e in b.edges]
        assert a.next_cursor == b.next_cursor and a.total == b.total
        pages += 1
        cursor = a.next_cursor
        if cursor is None:
            break
    assert pages > 1, "the chosen callable fitted in one page"


def test_slices_are_identical_over_every_entering_value(ref_l4, ts_dual):
    _, neo = ts_dual
    seen = 0
    for sig in _signatures(ref_l4):
        for value in _entering_values(ref_l4, sig):
            for depth in (None, 5, 1):
                a = ref_l4.slice_forward(value, within=sig, depth=depth)
                b = neo.slice_forward(value, within=sig, depth=depth)
                assert a.total == b.total, f"{value} in {sig} at depth {depth}"
                assert [(n.ref, n.kind, n.name, n.callable, n.file, n.line) for n in a.nodes] == [(n.ref, n.kind, n.name, n.callable, n.file, n.line) for n in b.nodes]
                assert a.resolved == b.resolved and a.complete == b.complete
            back_a, back_b = ref_l4.slice_backward(value, within=sig, depth=None), neo.slice_backward(value, within=sig, depth=None)
            assert back_a.total == back_b.total
            assert [n.ref for n in back_a.nodes] == [n.ref for n in back_b.nodes]
            seen += 1
    assert seen, "the sample app carried no entering values; this test proved nothing"


def test_the_call_graph_accessors_are_identical_over_every_callable(ref_l4, ts_dual):
    _, neo = ts_dual
    tuples = lambda ns: [(n.ref, n.kind, n.callable, n.name, n.file, n.line) for n in ns]  # noqa: E731
    for sig in _signatures(ref_l4):
        assert tuples(ref_l4.callers_of(sig)) == tuples(neo.callers_of(sig)), f"callers_of({sig})"
        assert tuples(ref_l4.callees_of(sig)) == tuples(neo.callees_of(sig)), f"callees_of({sig})"
        for depth in (None, 5, 1):
            a, b = ref_l4.backward_cone([sig], depth=depth), neo.backward_cone([sig], depth=depth)
            assert a.total == b.total and tuples(a.nodes) == tuples(b.nodes), f"backward_cone([{sig}], depth={depth})"


def test_reaches_agrees_on_every_pair_of_callables(ref_l4, ts_dual):
    _, neo = ts_dual
    sigs = _signatures(ref_l4)
    trues = 0
    for a in sigs:
        for b in sigs:
            if a == b:
                continue
            for depth in (None, 2):
                got = ref_l4.reaches(a, b, depth=depth)
                assert got is neo.reaches(a, b, depth=depth), f"reaches({a}, {b}, depth={depth})"
                trues += got and depth is None
    assert trues, "no pair of callables reached another; this test proved nothing"


def test_the_path_queries_agree_hop_for_hop(ref_l4, ts_dual):
    _, neo = ts_dual
    sigs = _signatures(ref_l4)
    hops = lambda p: [(h.frm.ref, h.to.ref, h.via, h.var, tuple(h.prov)) for h in p.hops]  # noqa: E731
    call_paths = 0
    for a in sigs:
        for b in sigs:
            if a == b or not ref_l4.reaches(a, b):
                continue
            x, y = ref_l4.call_paths_between(a, b), neo.call_paths_between(a, b)
            assert x.complete == y.complete
            assert [hops(p) for p in x.paths] == [hops(p) for p in y.paths], f"call_paths_between({a}, {b})"
            call_paths += len(x.paths)
    assert call_paths, "no call path was found at all; this test proved nothing"

    value_paths = 0
    for src_sig in sigs:
        for src in _entering_values(ref_l4, src_sig):
            for dst_sig in sigs:
                for dst in _entering_values(ref_l4, dst_sig):
                    if (src, src_sig) == (dst, dst_sig):
                        continue
                    x = ref_l4.paths_between(src, dst, src_within=src_sig, dst_within=dst_sig)
                    y = neo.paths_between(src, dst, src_within=src_sig, dst_within=dst_sig)
                    assert x.complete == y.complete
                    assert [hops(p) for p in x.paths] == [hops(p) for p in y.paths], f"paths_between({src}@{src_sig}, {dst}@{dst_sig})"
                    value_paths += len(x.paths)
    assert value_paths, "no value flow was found at all; this test proved nothing"


def test_the_flow_predicates_agree_including_where_a_bound_cuts(ref_l4, ts_dual):
    """Both halves of the asymmetry, on both backends: a flow that is ``True`` unbounded must be
    ``True`` on both, and ``False`` at a cutting depth on both."""
    _, neo = ts_dual
    sigs = _signatures(ref_l4)
    trues = 0
    for src_sig in sigs:
        for src in _entering_values(ref_l4, src_sig):
            for callee in sigs:
                for depth in (None, 1):
                    got = ref_l4.flows_to_call(src, callee, within=src_sig, depth=depth)
                    assert got is neo.flows_to_call(src, callee, within=src_sig, depth=depth), f"flows_to_call({src}, {callee}, depth={depth})"
                    trues += got and depth is None
                for arg in _entering_values(ref_l4, callee):
                    for depth in (None, 1):
                        got = ref_l4.flows_to_argument(src, callee, arg, within=src_sig, depth=depth)
                        assert got is neo.flows_to_argument(src, callee, arg, within=src_sig, depth=depth), f"flows_to_argument({src}, {callee}, {arg}, depth={depth})"
    assert trues, "no value flowed into any call; this test proved nothing"


def test_the_dataflow_miss_paths_raise_the_same_way(ref_l4, ts_dual):
    _, neo = ts_dual
    sig = _signatures(ref_l4)[0]
    # The second element is the subject both messages must name -- the whole point of a miss path
    # is that the caller is told *what* missed. It used to be an unused ``None`` beside an
    # assertion that ``or type(a) is type(b)`` made vacuously true (leg 2.5b review, finding 2).
    for call, subject in (
        (lambda b: b.get_cfg("noSuchCallable"), "noSuchCallable"),
        (lambda b: b.get_ddg(sig, page_size=0), "page_size"),
        (lambda b: b.get_ddg(sig, cursor="not-a-cursor"), "not-a-cursor"),
        (lambda b: b.slice_forward("noSuchValue", within=sig), "noSuchValue"),
        (lambda b: b.slice_forward("x", within=sig, depth=0), "depth"),
        (lambda b: b.backward_cone([]), "sinks"),
        (lambda b: b.backward_cone("notAList"), "sinks"),
        (lambda b: b.reaches("noSuchCallable", sig), "noSuchCallable"),
        (lambda b: b.callers_of("noSuchCallable"), "noSuchCallable"),
        (lambda b: b.call_paths_between(sig, sig), sig),
        (lambda b: b.flows_to_call("noSuchValue", sig, within=sig), "noSuchValue"),
    ):
        _same_raise(lambda: call(ref_l4), lambda: call(neo), subject)


# =====================================================================================
# Leg 2.5b Task 3: entrypoints and the artifact/config layer answer identically on both backends.
#
# The sample app marks no entrypoint at all, which is the interesting case rather than a dull one:
# the two backends must agree that the mark is absent *and* agree on the coverage record that says
# whether that silence is clean.
# =====================================================================================
def test_entrypoints_parity(ts_dual):
    ref, neo = ts_dual
    assert {_overview_tuple(o) for o in ref.get_entrypoints()} == {_overview_tuple(o) for o in neo.get_entrypoints()}


def test_entrypoint_classes_parity(ts_dual):
    ref, neo = ts_dual
    shape = lambda o: (o.signature, o.name, o.path, o.start_line, o.end_line, tuple(sorted(o.decorators)))
    assert {shape(o) for o in ref.get_entrypoint_classes()} == {shape(o) for o in neo.get_entrypoint_classes()}


def test_entrypoint_coverage_parity(ts_dual):
    """The local backend passes ``TSApplication.entrypoint_report`` through; the Neo4j backend
    parses the ``entrypoint_report_json`` property off the anchor. Same report, no lossiness — so
    neither may report a ``diagnostics``-only result here."""
    ref, neo = ts_dual
    a, b = ref.get_entrypoint_coverage(), neo.get_entrypoint_coverage()
    assert a.diagnostics == [] and b.diagnostics == [], "one backend could not supply the report at all"
    assert (a.frameworks_detected, a.rulesets, a.unresolved, a.errors) == (b.frameworks_detected, b.rulesets, b.unresolved, b.errors)
    assert a.unresolved, "the sample app's pass records near-misses; an empty one means the report was not read"


def test_artifacts_parity(ts_dual):
    ref, neo = ts_dual
    a, b = ref.get_artifacts(), neo.get_artifacts()
    assert set(a) == set(b) and a, "the sample app declares package.json and tsconfig.json"
    for path in a:
        assert (a[path].path, a[path].format, sorted(a[path].roles), a[path].sha256) == (b[path].path, b[path].format, sorted(b[path].roles), b[path].sha256), path
        assert {ck.id for ck in a[path].config_keys} == {ck.id for ck in b[path].config_keys}, path


def test_dependencies_and_config_keys_parity(ts_dual):
    ref, neo = ts_dual
    shape = lambda d: (d.name, d.ecosystem, d.direct, d.declared_in)
    assert sorted(shape(d) for d in ref.get_dependencies()) == sorted(shape(d) for d in neo.get_dependencies())
    assert sorted(shape(d) for d in ref.get_dependencies(direct_only=True)) == sorted(shape(d) for d in neo.get_dependencies(direct_only=True))
    assert {k: (v.key, v.namespace, v.value) for k, v in ref.get_config_keys().items()} == {k: (v.key, v.namespace, v.value) for k, v in neo.get_config_keys().items()}


def test_config_uses_and_readers_parity(ts_dual):
    ref, neo = ts_dual
    assert sorted((e.src, e.dst) for e in ref.get_config_uses()) == sorted((e.src, e.dst) for e in neo.get_config_uses())
    for key in sorted({v.key for v in ref.get_config_keys().values()})[:5]:
        assert {_overview_tuple(o) for o in ref.get_config_readers(key)} == {_overview_tuple(o) for o in neo.get_config_readers(key)}, key


def test_unresolved_config_reads_parity(ts_dual):
    """Was the one documented divergence -- the projection carried no ``config_reads`` and that
    backend refused rather than answer ``[]``. codeanalyzer-typescript 1.4.0 projects them
    (``TS_READS_CONFIG_UNRESOLVED``, #368), so this is parity. The sample app matches no config
    read at all, which makes both sides ``[]``: presence/absence is what the edge guarantees, and
    a count divergence would only appear on a corpus whose sites collapse onto one edge."""
    ref, neo = ts_dual
    assert ref.get_unresolved_config_reads() == neo.get_unresolved_config_reads() == []


def test_import_export_and_parameter_bindings_parity(ts_dual):
    """The other three #368 accessors, over the same sample app on both backends.

    Imports are compared on the facets the aggregate edge can carry: a ``TS_IMPORTS`` edge folds
    every binding between a pair into sorted sets, so an alias, an ``import_kind`` and a span
    survive only in ``analysis.json`` (see ``reconstruct.import_edge``). Exports and parameters
    ride JSON-encoded properties and are compared whole.
    """
    ref, neo = ts_dual
    binding = lambda d: {k: sorted((i.module, i.name, i.is_type_only) for i in v) for k, v in d.items()}
    assert binding(ref.get_imports()) == binding(neo.get_imports())
    assert any(v for v in ref.get_imports().values()), "the sample app imports something; an all-empty parity is vacuous"
    assert ref.get_exports() == neo.get_exports()
    params = lambda d: {sig: [p.name for p in c.parameters] for sig, c in d.items()}
    assert params(ref.get_functions()) == params(neo.get_functions())
    assert ref.get_method_parameters("src/services.UserService", "create") == neo.get_method_parameters("src/services.UserService", "create") == ["name", "role"]
