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

"""The addressing surface at scale, **read-only**, against a live 1.3.0 graph someone else
deployed (leg 2.5b, Task 1).

The offline suite answers from a five-file sample app and a fake two-application graph. Neither can
show what 7,044 anonymous callables or a declaration-merged id do to a resolver — which is exactly
the shape of blocker Java's leg 3a shipped, because its corpus had no collisions. So every fixture
below is derived from the graph at run time by :func:`cypher`, and every expectation is the graph's
own count, never a string copied out of a file::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7692 \\
    CLDK_TEST_NEO4J_USER=neo4j \\
    CLDK_TEST_NEO4J_PASSWORD=... \\
    CLDK_TEST_NEO4J_APP=superset-frontend \\
    uv run pytest tests/analysis/typescript/test_typescript_addressing_live.py

The URI has no default: the module skips unless it is set and the named application is present.
Nothing here writes — not a node, relationship or property, not even in setup — and no emitter runs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.analysis.commons.results import BodyRef, SliceNode
from cldk.analysis.typescript.neo4j.reconstruct import CALLABLE_KINDS, TYPE_LABEL_KINDS
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

from .test_typescript_e2e_neo4j_live import APP_ID, APP_NAME, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, SCOPE, _live_application_present

logging.getLogger("neo4j").setLevel(logging.ERROR)

pytestmark = pytest.mark.skipif(
    not _live_application_present(),
    reason=f"set CLDK_TEST_NEO4J_URI (and _USER/_PASSWORD/_APP) to a server holding {APP_ID!r}; this module is read-only and has no default URI",
)


@pytest.fixture(scope="module")
def ts():
    analysis = CLDK.typescript(
        project_path=None,
        analysis_level="symbol_table",
        backend=Neo4jConnectionConfig(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD, application_name=APP_NAME),
    )
    yield analysis
    analysis.backend.close()


def cypher(query: str, **params: Any) -> List[Dict[str, Any]]:
    """One read statement against the live graph, through its own driver — never the SDK's."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            return [r.data() for r in session.run(query, app_id=APP_ID, **SCOPE, **params)]
    finally:
        driver.close()


SCOPED = "(x.id STARTS WITH $p1 OR x.id STARTS WITH $p2)"


@pytest.fixture(scope="module")
def a_method() -> Dict[str, Any]:
    """One class method with a body node the caller can land on, chosen deterministically."""
    rows = cypher(
        f"MATCH (o:TSClass)-[:TS_HAS_METHOD]->(x:TSCallable) WHERE {SCOPED} AND x.kind = 'method' AND x.code IS NOT NULL "
        "MATCH (x)-[:TS_HAS_BODY_NODE]->(b:TSBodyNode {kind: 'call'}) WHERE b.start_line IS NOT NULL "
        "RETURN x.id AS id, x.signature AS signature, x.name AS name, x.start_line AS start_line, x.code AS code, "
        "o.signature AS owner_signature, o.name AS owner_name, b.id AS body_id, b.kind AS body_kind, b.start_line AS body_line, b.callee AS callee "
        "ORDER BY x.id, b.id LIMIT 1"
    )
    assert rows, "the graph holds no class method with a call body node"
    return rows[0]


def module_key_of(node_id: str, modules: List[str]) -> str:
    for prefix in (SCOPE["p1"], SCOPE["p2"]):
        if node_id.startswith(prefix):
            parts = node_id[len(prefix) :].split("/")
            for n in range(len(parts), 0, -1):
                candidate = "/".join(parts[:n])
                if candidate in modules:
                    return candidate
    raise AssertionError(node_id)


@pytest.fixture(scope="module")
def modules() -> List[str]:
    return [r["k"] for r in cypher("MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m.name AS k")]


# ----------------------------------------------------------------------------------------- locate
def test_locate_resolves_a_real_position_to_its_method_owner_and_body_node(ts, a_method, modules):
    path = module_key_of(a_method["id"], modules)
    r = ts.locate(path, a_method["body_line"])
    assert r.module.path == path and r.diagnostics == []
    assert r.callable is not None and r.callable.signature == a_method["signature"]
    assert r.callable.class_signature == a_method["owner_signature"]
    assert r.type is not None and r.type.name == a_method["owner_name"]
    assert r.source == a_method["code"]
    assert isinstance(r.body, BodyRef) and r.node_id == r.body.id
    # Whatever body node wins the tie, it must be one of this callable's own and contain the line.
    own = {b["id"] for b in cypher("MATCH (c:TSCallable {id: $id})-[:TS_HAS_BODY_NODE]->(b) RETURN b.id AS id", id=a_method["id"])}
    assert r.body.id in own
    assert r.body.span.start[0] <= a_method["body_line"] <= r.body.span.end[0]
    # Line-only span: the projection carries no columns and no byte offsets.
    assert r.body.span.start[1] == 0 and r.body.span.bytes == (0, 0)


def test_locate_populates_the_callee_a_body_node_carries(ts, a_method, modules):
    """Unlike the Python graph, where callee resolution is a separate ``PY_RESOLVES_TO`` edge and
    ``BodyRef.callee`` is always ``None`` over Neo4j, a TypeScript ``call`` node carries ``callee``
    as a property — so this backend fills the field in exactly as the local one does."""
    resolved = cypher(
        f"MATCH (x:TSBodyNode) WHERE {SCOPED} AND x.kind = 'call' AND x.callee IS NOT NULL AND x.start_line IS NOT NULL "
        "MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(x) "
        "RETURN c.id AS cid, x.id AS id, x.start_line AS line, x.callee AS callee ORDER BY x.id LIMIT 1"
    )[0]
    path = module_key_of(resolved["cid"], modules)
    hits = [b for b in (ts.locate(path, resolved["line"]).body,) if b is not None]
    assert hits, "the position landed on no body node at all"
    # The innermost node at that line may be a tighter one than the call; when it *is* the call,
    # its callee must be the property the graph carries.
    if hits[0].id == resolved["id"]:
        assert hits[0].callee == resolved["callee"]


def test_locate_at_module_scope_says_the_graph_has_no_module_text(ts, modules):
    """A line past the end of every callable in a module. ``:TSModule`` projects no source, so the
    honest answer is an empty ``source`` plus a diagnostic that says why — never invented text."""
    row = cypher("MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m.name AS k, m.end_line AS end ORDER BY m.id LIMIT 1")[0]
    r = ts.locate(row["k"], row["end"] + 10_000)
    assert r.callable is None and r.body is None and r.source == ""
    assert [d.code for d in r.diagnostics] == ["module_scope", "module_source_unavailable"]


def test_locate_in_a_file_the_graph_has_no_module_for(ts):
    r = ts.locate("src/definitely/not/a/real/file.ts", 3)
    assert r.callable is None and [d.code for d in r.diagnostics] == ["file_not_in_graph"]
    assert "src/definitely/not/a/real/file.ts" in r.diagnostics[0].message


def test_locate_many_answers_every_position_in_input_order(ts, a_method, modules):
    path = module_key_of(a_method["id"], modules)
    out = ts.locate_many([(path, a_method["body_line"]), ("nope.ts", 1), (path, a_method["start_line"])])
    assert [r.callable.signature if r.callable else None for r in out] == [a_method["signature"], None, a_method["signature"]]


# -------------------------------------------------------------------------------- resolve_callable
def test_resolve_callable_by_full_signature_and_by_dotted_suffix(ts, a_method, modules):
    n = ts.resolve_callable(a_method["signature"])
    assert isinstance(n, SliceNode) and n.kind == "callable"
    assert n.callable == a_method["signature"] and n.ref == a_method["id"]
    assert n.file == module_key_of(a_method["id"], modules) and n.line == a_method["start_line"]
    assert ts.resolve_callable(a_method["signature"], in_class=a_method["owner_signature"]).ref == n.ref


def test_in_module_takes_a_key_suffix_or_the_dotted_form(ts, a_method, modules):
    path = module_key_of(a_method["id"], modules)
    dotted = path.rsplit(".", 1)[0].replace("/", ".")
    assert ts.resolve_callable(a_method["signature"], in_module=path).ref == a_method["id"]
    assert ts.resolve_callable(a_method["signature"], in_module=dotted).ref == a_method["id"]


def test_a_name_matching_many_callables_lists_them_and_nothing_else(ts):
    """The framework-method case, at scale: pick the most-repeated leaf name in the corpus."""
    row = cypher(f"MATCH (x:TSCallable) WHERE {SCOPED} AND x.name <> '(anonymous)' " "WITH x.name AS name, count(*) AS n WHERE n > 1 RETURN name, n ORDER BY n DESC, name LIMIT 1")[
        0
    ]
    with pytest.raises(AmbiguousName) as e:
        ts.resolve_callable(row["name"])
    assert len(e.value.candidates) > 1
    assert all(c == row["name"] or c.endswith("." + row["name"]) for c in e.value.candidates)
    assert "did you mean" not in e.value.message.lower()


def test_a_name_in_no_module_names_the_selector_the_caller_spelled(ts):
    with pytest.raises(SelectorNotInGraph) as e:
        ts.resolve_callable("noSuchCallableAnywhereInThisCorpus")
    assert "noSuchCallableAnywhereInThisCorpus" in str(e.value)


def test_a_keyword_that_excludes_every_match_is_blamed_on_that_keyword(ts, a_method):
    with pytest.raises(SelectorNotInGraph) as e:
        ts.resolve_callable(a_method["signature"], in_module="no/such/module.ts")
    assert "in_module" in str(e.value) and "no/such/module.ts" in str(e.value)


# ------------------------------------------------------- anonymous callables, at the corpus's scale
def test_the_corpus_really_does_carry_thousands_of_anonymous_callables():
    counts = cypher(f"MATCH (x:TSAnonymousCallable) WHERE {SCOPED} RETURN count(*) AS anon")[0]
    total = cypher(f"MATCH (x:TSCallable) WHERE {SCOPED} RETURN count(*) AS n, count(DISTINCT x.signature) AS distinct_sigs")[0]
    assert counts["anon"] > 1000, "this corpus cannot exercise the anonymous-callable ruling"
    # The whole ruling rests on this: every callable signature is distinct, anonymous ones included,
    # so a signature is an address even where a name is not.
    assert total["n"] == total["distinct_sigs"]


def test_an_anonymous_callable_is_addressed_by_its_signature_never_by_its_name(ts):
    row = cypher(
        f"MATCH (x:TSAnonymousCallable) WHERE {SCOPED} WITH x, split(x.signature, '.')[-1] AS leaf "
        "WITH leaf, collect(x)[0] AS x, count(*) AS n WHERE n = 1 "
        "RETURN x.id AS id, x.signature AS signature, x.name AS name, leaf AS leaf ORDER BY x.id LIMIT 1"
    )[0]
    assert row["name"] == "(anonymous)"
    assert ts.resolve_callable(row["leaf"]).ref == row["id"]
    assert ts.resolve_callable(row["signature"]).ref == row["id"]


def test_the_anonymous_display_name_is_not_an_address_at_all(ts):
    """7,044 callables share the name ``"(anonymous)"``. The resolver matches *signatures*, and no
    signature carries that string, so the spelling misses outright — an honest "no such callable"
    rather than a candidate list nobody could choose from (E8: nothing is guessed either way)."""
    assert cypher(f"MATCH (x:TSCallable) WHERE {SCOPED} AND x.name = '(anonymous)' RETURN count(*) AS n")[0]["n"] > 1000
    with pytest.raises(SelectorNotInGraph):
        ts.resolve_callable("(anonymous)")


def test_locate_inside_an_anonymous_callable_names_it_by_signature(ts, modules):
    row = cypher(
        f"MATCH (x:TSAnonymousCallable) WHERE {SCOPED} AND x.start_line IS NOT NULL AND x.end_line > x.start_line "
        "RETURN x.id AS id, x.signature AS signature, x.start_line AS start_line ORDER BY x.id LIMIT 1"
    )[0]
    r = ts.locate(module_key_of(row["id"], modules), row["start_line"] + 1)
    # A nested anonymous callable may be tighter still; whichever wins, the answer is a signature.
    assert r.callable is not None and "<anon@" in r.callable.signature


# ------------------------------------------------------------------------ declaration merging (#177)
def test_a_declaration_merged_node_resolves_to_the_facet_its_kind_names_or_to_nothing(ts):
    """cants mints one id for a value and a type of the same name, so ``MERGE`` collapses the two
    onto one node carrying both labels and one ``kind`` (cants#177). The domain of
    ``resolve_callable`` is the **kind**, not the label: a merged node whose kind is a callable kind
    resolves as the callable it is, and one whose kind names a type facet is not reachable through a
    callable accessor at all. A collision can therefore never resolve to the wrong facet — it
    resolves to the right one, or it misses."""
    type_labels = sorted(TYPE_LABEL_KINDS)
    merged = cypher(
        f"MATCH (x) WHERE {SCOPED} AND size([l IN labels(x) WHERE l IN $decl]) > 1 "
        "RETURN x.id AS id, x.signature AS signature, x.name AS name, x.kind AS kind, labels(x) AS labels ORDER BY x.id",
        decl=["TSCallable", "TSField", "TSModule", "TSExternal", *type_labels],
    )
    assert merged, "this corpus carries no declaration-merged node; the ruling is untested by it"
    for row in merged:
        if row["kind"] in CALLABLE_KINDS:
            assert "TSCallable" in row["labels"], "the emitter kept a callable kind on a node carrying no TSCallable label"
            assert ts.resolve_callable(row["signature"]).ref == row["id"]
        else:
            with pytest.raises(SelectorNotInGraph):
                ts.resolve_callable(row["signature"])


# ----------------------------------------------------------------------------------- resolve_value
def test_resolve_value_finds_a_parameter_of_the_named_callable(ts, modules):
    row = cypher(
        f"MATCH (x:TSCallable) WHERE {SCOPED} MATCH (x)-[:TS_HAS_BODY_NODE]->(b:TSBodyNode {{kind: 'formal_in'}}) "
        "WHERE b.of IS NOT NULL AND NOT b.of CONTAINS '{' "
        "WITH x, collect(b) AS ps WHERE size(ps) = 1 "
        "RETURN x.signature AS signature, ps[0].of AS of, ps[0].id AS id ORDER BY x.id LIMIT 1"
    )[0]
    n = ts.resolve_value(row["of"], within=row["signature"])
    # TypeScript's entering values are parameters and nothing else: cants emits none of the
    # "<global>:mod::name" grammar codeanalyzer-python marks captured globals with.
    assert n.kind == "parameter" and n.name == row["of"] and n.defined_in is None
    assert n.callable == row["signature"] and n.ref == row["id"]


def test_no_entering_value_in_this_corpus_carries_a_python_style_marker():
    """The reason ``resolve_value`` translates nothing: the marker grammar simply is not there."""
    row = cypher(f"MATCH (x:TSBodyNode) WHERE {SCOPED} AND x.kind = 'formal_in' AND (x.of STARTS WITH '<global>:' OR x.of STARTS WITH '<capture>:') RETURN count(*) AS n")[0]
    assert row["n"] == 0


def test_resolve_value_naming_no_value_of_the_callable_raises(ts, a_method):
    with pytest.raises(SelectorNotInGraph):
        ts.resolve_value("noSuchParameterAnywhere", within=a_method["signature"])


# --------------------------------------------------------------------------------------- get_source
def test_get_source_answers_for_a_callable_by_either_name(ts, a_method):
    assert ts.get_source(a_method["signature"]) == a_method["code"]
    assert ts.get_source(a_method["id"]) == a_method["code"]


def test_get_source_below_callable_granularity_says_the_graph_cannot(ts, a_method):
    with pytest.raises(NotImplementedError) as e:
        ts.get_source(a_method["body_id"])
    assert "TSBodyNode" in str(e.value)


def test_get_source_of_an_anonymous_callable_does_not_mistake_its_id_for_a_body_node(ts):
    """``…/<anon@22:52>`` contains an ``@``, so a backend that split the id on one would look for a
    body node of ``…/<anon`` and answer ``NotImplementedError`` for a callable that plainly has
    text. The id is looked up whole instead."""
    row = cypher(f"MATCH (x:TSAnonymousCallable) WHERE {SCOPED} AND x.code IS NOT NULL AND x.code <> '' RETURN x.id AS id, x.code AS code ORDER BY x.id LIMIT 1")[0]
    assert ts.get_source(row["id"]) == row["code"]


def test_get_source_of_nothing_raises(ts):
    with pytest.raises(KeyError):
        ts.get_source("no.such.callable.anywhere")


def test_get_source_of_a_callable_the_emitter_wrote_no_text_for_raises(ts):
    rows = cypher(f"MATCH (x:TSCallable) WHERE {SCOPED} AND (x.code IS NULL OR x.code = '') RETURN x.signature AS signature ORDER BY x.id LIMIT 1")
    if not rows:
        pytest.skip("every callable in this corpus carries text")
    with pytest.raises(KeyError):
        ts.get_source(rows[0]["signature"])


# ----------------------------------------------------------------------------------------- describe
def test_describe_hydrates_a_callable_and_leaves_the_positions_the_graph_has_no_text_for(ts, a_method, modules):
    located = ts.locate(module_key_of(a_method["id"], modules), a_method["body_line"])
    resolved = ts.resolve_callable(a_method["signature"])
    out = ts.describe([resolved, located])
    assert out[0].source == a_method["code"]
    assert out[1].source is None  # a body node: the graph carries no text below callable granularity
    assert ts.describe([]) == []


def test_describe_raises_on_a_ref_that_names_nothing(ts):
    stale = SliceNode(file="x.ts", line=1, callable="x", kind="callable", name="x", ref=f"{APP_ID}/no/such/node")
    with pytest.raises(KeyError):
        ts.describe([stale])


# -------------------------------------------------------------------------- has_resolution_edges
def test_has_resolution_edges_reports_this_applications_own_edges(ts):
    expected = bool(cypher(f"MATCH (x:TSBodyNode)-[:TS_RESOLVES_TO]->() WHERE {SCOPED} RETURN x LIMIT 1"))
    assert ts.has_resolution_edges is expected
