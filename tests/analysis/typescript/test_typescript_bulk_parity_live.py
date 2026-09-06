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
    """
    _populate_neo4j(typescript_application)

    cache_dir = tmp_path_factory.mktemp("ts_bulk_parity_cache")
    ref = CLDK.typescript(
        project_path=typescript_application,
        eager=True,
        analysis_level=AnalysisLevel.call_graph,
        backend=CodeAnalyzerConfig(cache_dir=str(cache_dir)),
    )

    neo = CLDK.typescript(
        project_path=typescript_application,
        analysis_level=AnalysisLevel.call_graph,
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
def _same_raise(ref_call, neo_call):
    """Both backends refuse, with the same exception type and the same subject named."""
    with pytest.raises(Exception) as r:
        ref_call()
    with pytest.raises(Exception) as n:
        neo_call()
    assert type(r.value) is type(n.value), f"{type(r.value).__name__} locally, {type(n.value).__name__} over Neo4j"
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
    a, b = _same_raise(lambda: ref.resolve_callable("noSuchCallable"), lambda: neo.resolve_callable("noSuchCallable"))
    assert "noSuchCallable" in str(a) and "noSuchCallable" in str(b)
    a, b = _same_raise(lambda: ref.resolve_callable("describe"), lambda: neo.resolve_callable("describe"))
    assert a.candidates == b.candidates, "the two backends resolved over different candidate sets"
    a, b = _same_raise(
        lambda: ref.resolve_callable("describe", in_module="no/such/module.ts"),
        lambda: neo.resolve_callable("describe", in_module="no/such/module.ts"),
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


def test_resolve_value_parity_over_every_callable(typescript_application, tmp_path_factory, ts_dual):
    """Every named value entering every callable, resolved on both.

    The local reference here is a **level-4** one of its own: ``ts_dual``'s is built at
    ``call_graph`` for the bulk accessors, and ``formal_in`` vertices -- the whole domain of
    ``resolve_value`` -- first exist at level 4. ``--emit neo4j`` is always full depth, so the graph
    side already has them; comparing it against a level-2 reference would be comparing analysis
    levels, not backends. The parity assertion is on the *values*; the level is just what makes
    both sides hold them.
    """
    _, neo = ts_dual
    ref = CLDK.typescript(
        project_path=typescript_application,
        eager=True,
        analysis_level=AnalysisLevel.system_dependency_graph,
        backend=CodeAnalyzerConfig(cache_dir=str(tmp_path_factory.mktemp("ts_addressing_parity_cache"))),
    )
    seen = 0
    for o in ref.get_callables_overview():
        owner = o.owner_signature or o.signature.rsplit(".", 1)[0]
        callable_ = ref.get_method(owner, o.name)
        for value in sorted({n.of for n in (callable_.body if callable_ else {}).values() if n.kind == "formal_in" and n.of}):
            a, b = ref.resolve_value(value, within=o.signature), neo.resolve_value(value, within=o.signature)
            assert (a.kind, a.name, a.defined_in, a.callable, a.ref) == (b.kind, b.name, b.defined_in, b.callable, b.ref)
            seen += 1
        _same_raise(lambda: ref.resolve_value("noSuchValue", within=o.signature), lambda: neo.resolve_value("noSuchValue", within=o.signature))
    assert seen, "the level-4 reference carried no formal_in vertex at all; this test proved nothing"


def test_get_source_parity_and_the_one_documented_divergence(ts_dual):
    ref, neo = ts_dual
    for o in ref.get_callables_overview():
        try:
            expected = ref.get_source(o.signature)
        except KeyError:
            _same_raise(lambda: ref.get_source(o.signature), lambda: neo.get_source(o.signature))
            continue
        assert neo.get_source(o.signature) == expected
        assert neo.get_source(ref.resolve_callable(o.signature).ref) == expected
    _same_raise(lambda: ref.get_source("no.such.node"), lambda: neo.get_source("no.such.node"))
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
    stale = SliceNode(file="x.ts", line=1, callable="x", kind="callable", name="x", ref="can://typescript/nope/x")
    _same_raise(lambda: ref.describe([stale]), lambda: neo.describe([stale]))


def test_has_resolution_edges_agrees(ts_dual):
    ref, neo = ts_dual
    assert ref.has_resolution_edges is neo.has_resolution_edges is True
