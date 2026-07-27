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

Reuses the live harness idiom of ``test_typescript_neo4j_backend.py`` verbatim: same env-var
gating, same ``_populate_neo4j`` out-of-band loader (``codeanalyzer-typescript --emit neo4j`` over
Bolt), same tracked sample app / app name. The whole module is skipped unless a Neo4j server is
reachable. Point the tests at one with:

    CLDK_TEST_NEO4J_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=test \
    pytest tests/analysis/typescript/test_typescript_bulk_parity_live.py

(e.g. `podman run -d -p 7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5`).
"""

import logging

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, Neo4jConnectionConfig

from .test_typescript_neo4j_backend import (
    APP_NAME,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    _neo4j_reachable,
    _populate_neo4j,
)

logging.getLogger("neo4j").setLevel(logging.ERROR)

pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason=f"no Neo4j reachable at {NEO4J_URI} (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD)",
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
