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

"""End-to-end, **read-only**, against a live codeanalyzer-typescript graph someone else deployed.

Every other Neo4j assertion for TypeScript runs against a fake whose rows the tests supply; that
harness cannot fail when the SDK reads a property the emitter never writes. This module is the
counterweight: a real graph of a real application (superset-frontend, 2,184 ``.ts/.tsx`` files
plus its JavaScript), queried over Bolt through the public facade. It never writes -- not a node,
relationship or property, not even in setup -- and it does not run an emitter.

    CLDK_TEST_NEO4J_URI=bolt://localhost:7690 \\
    CLDK_TEST_NEO4J_USER=neo4j \\
    CLDK_TEST_NEO4J_PASSWORD=... \\
    CLDK_TEST_NEO4J_APP=superset-frontend \\
    uv run pytest tests/analysis/typescript/test_typescript_e2e_neo4j_live.py

The URI has **no default**: the whole module skips unless it is set and the named application is
present on that server. Nothing below hardcodes a signature, path or count read off a file by hand:
every fixture is derived from the graph at run time by :func:`cypher` (deterministically), and
every expected count is the graph's own, so the suite proves the SDK against the graph rather than
against itself.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.analysis.typescript.backend import CALL_GRAPH_NODE_KINDS
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.models.typescript import TSCallable, TSClass, TSEnum, TSInterface, TSTypeAlias
from cldk.utils.exceptions import GraphSchemaMismatch
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
APP_NAME = os.environ.get("CLDK_TEST_NEO4J_APP", "superset-frontend")
APP_ID = f"can://{APP_NAME}"
#: The application scope, spelled here independently of the backend's helper on purpose.
SCOPE = {"p": f"can://{APP_NAME}/"}
SCOPED = "(x.id STARTS WITH $p)"
REQUIRED_RELATIONSHIP_TYPES = {"TS_HAS_MODULE", "TS_HAS_METHOD", "TS_HAS_BODY_NODE", "TS_CALLS"}


def _live_application_present() -> bool:
    if not NEO4J_URI:
        return False
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        try:
            driver.verify_connectivity()
            with driver.session() as session:
                found = session.run("MATCH (a:Application {id: $id}) RETURN count(a) AS c", id=APP_ID).single()
                return bool(found and found["c"])
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 - any connection/auth failure ⇒ skip, never fail
        return False


pytestmark = pytest.mark.skipif(
    not _live_application_present(), reason=f"no Neo4j at CLDK_TEST_NEO4J_URI={NEO4J_URI!r} holding application {APP_NAME!r} (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)"
)


# =====================================================================================
# Fixtures -- one attached facade, plus a raw read-only session used ONLY to derive fixtures
# and independent counts.
# =====================================================================================
@pytest.fixture(scope="module")
def cypher():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    session = driver.session()

    def run(query: str, **params: Any) -> List[Dict[str, Any]]:
        return [r.data() for r in session.run(query, **params)]

    yield run
    session.close()
    driver.close()


@pytest.fixture(scope="module")
def analysis():
    facade = CLDK.typescript(project_path=None, backend=Neo4jConnectionConfig(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD, application_name=APP_NAME))
    yield facade
    facade.backend.close()


@pytest.fixture(scope="module")
def count(cypher):
    def one(query: str, **params: Any) -> int:
        return cypher(query, **SCOPE, **params)[0]["n"]

    return one


@pytest.fixture(scope="module")
def sample(cypher) -> Dict[str, Any]:
    """A real class with a real method that has call sites, chosen deterministically."""
    rows = cypher(
        f"MATCH (c:TSClass) WHERE {SCOPED.replace('x.', 'c.')} MATCH (c)-[:TS_HAS_METHOD]->(m:TSCallable)-[:TS_HAS_BODY_NODE]->(s:TSBodyNode {{kind: 'call'}}) "
        "WITH c, m, count(s) AS calls WHERE m.code IS NOT NULL AND m.code <> '' "
        "RETURN c.signature AS class_sig, c.id AS class_id, m.signature AS method_sig, m.name AS method_name, m.code AS code, m.id AS method_id, calls ORDER BY calls DESC, m.signature LIMIT 1",
        **SCOPE,
    )
    assert rows, "no class method with call sites in scope"
    return rows[0]


# =====================================================================================
# attach
# =====================================================================================
def test_schema_probe_passed_on_merit(analysis, cypher):
    found = {r["relationshipType"] for r in cypher("CALL db.relationshipTypes()")}
    assert REQUIRED_RELATIONSHIP_TYPES <= found
    assert "HAS_CALLSITE" not in found and "CALLS" not in found  # the 0.4.3 vocabulary is gone
    assert analysis.backend._analyzer_version >= TSNeo4jBackend._ANALYZER_FLOOR


def test_attaching_to_an_absent_application_is_refused_not_served_empty():
    floor = ".".join(map(str, TSNeo4jBackend._ANALYZER_FLOOR))
    with pytest.raises(GraphSchemaMismatch, match=f"{floor} or newer"):
        CLDK.typescript(project_path=None, backend=Neo4jConnectionConfig(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD, application_name=f"{APP_NAME}-b"))


def test_graph_really_has_no_module_property(cypher):
    """What the scoping relies on: nothing to fall back to but the id."""
    assert cypher("MATCH (n:CanNode) WHERE n._module IS NOT NULL RETURN count(n) AS n")[0]["n"] == 0


# =====================================================================================
# symbol table / types
# =====================================================================================
def test_symbol_table_keys_are_the_graphs_module_names_on_both_prefixes(analysis, cypher):
    expected = {r["k"] for r in cypher("MATCH (:Application {id: $id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m.name AS k", id=APP_ID)}
    table = analysis.get_symbol_table()
    assert set(table) == expected and expected
    js = {r["k"] for r in cypher("MATCH (:Application {id: $id})-[:TS_HAS_MODULE]->(m:TSModule) WHERE m.id STARTS WITH $js RETURN m.name AS k", id=APP_ID, js=f"{SCOPE['p']}javascript/")}
    assert js and js <= set(table), "JavaScript modules are in scope (TS-3)"
    assert all(k.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")) for k in table)


def test_symbol_table_rebuilds_every_declared_node(analysis, count):
    """The var-length subtree fetch loses nothing: every type, callable and field the modules
    contain, at any depth, is somewhere in the rebuilt tree."""
    table = analysis.get_symbol_table()
    seen_ids: set = set()

    def walk(node: Any) -> None:
        for c in getattr(node, "functions", {}).values():
            seen_ids.add(c.id)
            walk(c)
        for c in getattr(node, "callables", {}).values():
            seen_ids.add(c.id)
            walk(c)
        for t in getattr(node, "types", {}).values():
            seen_ids.add(t.id)
            walk(t)
        for f in getattr(node, "fields", {}).values():
            seen_ids.add(f.id)

    for m in table.values():
        walk(m)
    reachable = count("MATCH (:Application {id: $id})-[:TS_HAS_MODULE]->(:TSModule)-[:TS_DECLARES|TS_HAS_METHOD|TS_HAS_FIELD*1..]->(x) RETURN count(DISTINCT x) AS n", id=APP_ID)
    # The emitter mints one id for a value and a type of the same name (TypeScript declaration
    # merging: `const TableOption = () => …` + `interface TableOption {…}`), so MERGE collapses
    # them onto one CanNode whose `kind` is one of the two. The tree holds it as what its kind
    # says, and the other declaration's members under it (an interface's fields under an arrow)
    # have no home -- the graph's loss, accounted for exactly rather than tolerated loosely.
    orphaned = count(
        f"MATCH (m:TSCallable) WHERE {SCOPED.replace('x.', 'm.')} AND NOT m.kind IN ['class', 'interface', 'enum', 'type_alias', 'namespace'] MATCH (m)-[:TS_HAS_FIELD|TS_HAS_METHOD]->(x) RETURN count(DISTINCT x) AS n"
    )
    assert len(seen_ids) == reachable - orphaned
    assert orphaned <= 3, "more merged-label collisions than the graph had when this was written"


def test_type_accessors_match_the_graphs_label_and_kind_counts(analysis, count):
    """Count **and** element type: a node carrying a type label but another declaration's ``kind``
    (a declaration-merged node) belongs to neither accessor, and nothing comes out as the wrong
    class."""
    for accessor, label, kind, model in (
        (analysis.get_classes, "TSClass", "class", TSClass),
        (analysis.get_interfaces, "TSInterface", "interface", TSInterface),
        (analysis.get_enums, "TSEnum", "enum", TSEnum),
        (analysis.get_type_aliases, "TSTypeAlias", "type_alias", TSTypeAlias),
    ):
        got = accessor()
        assert len(got) == count(f"MATCH (x:{label}) WHERE {SCOPED} AND x.kind = $kind RETURN count(DISTINCT x.signature) AS n", kind=kind), label
        assert all(type(v) is model and v.kind == kind and v.signature == k for k, v in got.items()), label


#: The declaration labels a node can carry; one apiece since codeanalyzer-typescript 1.5.0.
DECLARATION_LABELS = ["TSCallable", "TSClass", "TSInterface", "TSEnum", "TSTypeAlias", "TSNamespace", "TSField"]


@pytest.fixture(scope="module")
def merged(cypher) -> List[Dict[str, Any]]:
    """Declaration merging as 1.5.0 projects it (cants#177): **one id per facet**, so a signature
    two declarations share names two nodes rather than one node carrying both labels. One row per
    such signature, with every facet on it."""
    return cypher(
        f"MATCH (m:CanNode) WHERE {SCOPED.replace('x.', 'm.')} AND m.signature IS NOT NULL AND size([l IN labels(m) WHERE l IN $decl]) > 0 "
        "WITH m.signature AS sig, collect({id: m.id, kind: m.kind, labels: [l IN labels(m) WHERE l IN $decl]}) AS facets "
        "WHERE size(facets) > 1 RETURN sig, facets ORDER BY sig",
        decl=DECLARATION_LABELS,
        **SCOPE,
    )


def test_declaration_merging_mints_one_id_per_facet_and_every_facet_is_served(analysis, merged, cypher):
    """The fix in cants#177, and the behaviour change it buys: under 1.3.0/1.4.0 the two
    declarations collapsed onto one node carrying both labels and the last writer's ``kind``, and
    the SDK could serve only the facet that ``kind`` named — the other was lost. Now each facet is
    its own node and **both are reachable**, each through the accessor its kind names.

    Two assertions, in order: no node is left carrying two declaration labels, and every facet of
    every shared signature is served. Both read off the graph, so the day a corpus stops merging
    the second fails loudly rather than passing vacuously."""
    assert cypher(f"MATCH (m:CanNode) WHERE {SCOPED.replace('x.', 'm.')} AND size([l IN labels(m) WHERE l IN $decl]) > 1 RETURN count(m) AS n", decl=DECLARATION_LABELS, **SCOPE)[0][
        "n"
    ] == 0, "a node still carries two declaration labels; cants#177 has regressed"
    assert merged, "this corpus carries no declaration shared by two facets; retire this test"
    by_kind = {"class": analysis.get_classes(), "interface": analysis.get_interfaces(), "enum": analysis.get_enums(), "type_alias": analysis.get_type_aliases()}
    functions = analysis.get_functions()
    for row in merged:
        for facet in row["facets"]:
            assert len(facet["labels"]) == 1, f"{facet['id']} carries {facet['labels']}"
            served = by_kind.get(facet["kind"], functions)
            assert row["sig"] in served, f"{row['sig']} has a {facet['kind']} facet the matching accessor does not serve"
            assert served[row["sig"]].kind == facet["kind"], f"{row['sig']} was served as the wrong facet"


def test_class_method_and_code_round_trip(analysis, sample, cypher):
    cls = analysis.get_class(sample["class_sig"])
    assert cls is not None and cls.id == sample["class_id"]
    methods = analysis.get_methods_in_class(sample["class_sig"])
    assert sample["method_name"] in methods
    method = analysis.get_method(sample["class_sig"], sample["method_name"])
    assert method is not None and method.id == sample["method_id"]
    assert method.code == sample["code"], "the graph's own code text reaches the model's code property"
    # #368: parameters_json on the node is the callable's own list, JSON-encoded verbatim, and it
    # reaches both the rebuilt model and the accessor. Read back off the same node rather than
    # named here, so this compares the SDK to the graph and not to a string in this file.
    encoded = cypher("MATCH (c:CanNode:TSCallable {id: $id}) RETURN c.parameters_json AS j", id=sample["method_id"])[0]["j"]
    expected = [p["name"] for p in json.loads(encoded)] if encoded else []
    assert [p.name for p in method.parameters] == expected
    assert analysis.get_method_parameters(sample["class_sig"], sample["method_name"]) == expected
    assert analysis.get_method_parameters(sample["class_sig"], "no_such_method_here") == []
    assert analysis.get_method_bodies([sample["method_sig"]]) == {sample["method_sig"]: sample["code"]}


def test_typescript_file_is_the_module_key_derived_from_the_id(analysis, sample, cypher):
    key = analysis.get_typescript_file(sample["class_sig"])
    assert key is not None and sample["class_id"].startswith("can://") and f"/{key}/" in sample["class_id"]
    module = analysis.get_typescript_module(key)
    assert module is not None and module.id.endswith("/" + key)
    assert sample["class_sig"] in {t.signature for t in module.classes.values()} | {t.signature for ns in module.namespaces.values() for t in ns.classes.values()}


def test_functions_and_methods_in_application_match_the_graph(analysis, count):
    assert len(analysis.get_functions()) == count(f"MATCH (:TSModule|TSNamespace)-[:TS_DECLARES]->(x:TSCallable) WHERE {SCOPED} RETURN count(DISTINCT x.signature) AS n")
    grouped = analysis.get_methods()
    assert len(grouped) == count(f"MATCH (x:TSClass|TSInterface) WHERE {SCOPED} AND x.kind IN ['class', 'interface'] RETURN count(DISTINCT x.signature) AS n")
    assert sum(len(v) for v in grouped.values()) <= count(f"MATCH (x:TSClass|TSInterface)-[:TS_HAS_METHOD]->(m) WHERE {SCOPED} RETURN count(m) AS n")


def test_imports_are_every_binding_the_aggregate_edges_stand_for(analysis, cypher, count):
    """#368: ``TS_IMPORTS`` answers ``get_imports`` where the 1.3.0 graph could only refuse.

    The edge folds every binding between a pair into sorted sets, so the expected *count* is what
    the aggregate implies -- one entry per (spelling, name), and one naming the spelling itself on
    a side-effect edge that carries no names. Computed by the graph, so this is not the rebuild
    checking its own arithmetic against a copy of itself."""
    scoped_m = SCOPED.replace("x.", "m.")
    assert count(f"MATCH (m:TSModule)-[i:TS_IMPORTS]->() WHERE {scoped_m} RETURN count(i) AS n") > 0, "the reference graph carries import bindings"
    imports = analysis.get_imports()
    assert set(imports) == set(analysis.get_symbol_table()), "every module key answers, importing or not"
    implied = cypher(
        f"MATCH (m:TSModule)-[i:TS_IMPORTS]->() WHERE {scoped_m} "
        "RETURN sum(size(coalesce(i.spellings, [])) * CASE WHEN size(coalesce(i.imported_names, [])) = 0 THEN 1 ELSE size(i.imported_names) END) AS n",
        **SCOPE,
    )[0]["n"]
    assert sum(len(v) for v in imports.values()) == implied
    row = cypher(
        f"MATCH (m:TSModule)-[i:TS_IMPORTS]->() WHERE {scoped_m} AND size(coalesce(i.type_only_names, [])) > 0 "
        "RETURN m.name AS k, i.type_only_names AS names ORDER BY m.name, i.type_only_names LIMIT 1",
        **SCOPE,
    )
    if row:  # a corpus with no `import type` anywhere would make this vacuous, not wrong
        assert set(row[0]["names"]) <= {i.name for i in imports[row[0]["k"]] if i.is_type_only}


def test_exports_are_the_modules_own_encoded_list(analysis, cypher):
    """The other half of #368, and the lossless one: ``exports_json`` is the in-memory export list
    serialized verbatim, ``null`` on a module that exports nothing."""
    scoped_m = SCOPED.replace("x.", "m.")
    carried = cypher(f"MATCH (m:TSModule) WHERE {scoped_m} AND m.exports_json IS NOT NULL RETURN m.name AS k, m.exports_json AS j ORDER BY m.name", **SCOPE)
    assert carried, "the reference graph carries export bindings"
    exports = analysis.get_exports()
    assert set(exports) == set(analysis.get_symbol_table())
    assert sum(len(v) for v in exports.values()) == sum(len(json.loads(r["j"])) for r in carried)
    for r in carried[:20]:
        assert [e.name for e in exports[r["k"]]] == [e["name"] for e in json.loads(r["j"])], r["k"]
    assert all(not exports[k] for k in set(exports) - {r["k"] for r in carried}), "a module with no exports_json answers [], the honest empty"


def test_extends_comes_from_the_relationship_and_implements_names_the_absent_type(analysis, cypher):
    """The split comes from the relationships, not from the never-written ``implements_types``."""
    # Subtracting implements_types from base_classes would have returned a class's interfaces as
    # its extended classes. This graph declares no TS_IMPLEMENTS at all, so that half raises
    # naming the type rather than reporting that nothing implements anything.
    assert cypher("MATCH (c:TSClass|TSInterface) RETURN count(c.implements_types) AS n")[0]["n"] == 0
    assert "TS_IMPLEMENTS" not in {r["relationshipType"] for r in cypher("CALL db.relationshipTypes()")}
    scoped_c = SCOPED.replace("x.", "c.")
    row = cypher(f"MATCH (c:TSClass)-[:TS_EXTENDS]->(b) WHERE {scoped_c} RETURN c.signature AS sig, collect(DISTINCT b.signature) AS bases LIMIT 1", **SCOPE)[0]
    assert analysis.get_extended_classes(row["sig"]) == sorted(row["bases"])
    with pytest.raises(CodeanalyzerExecutionException, match="TS_IMPLEMENTS"):
        analysis.get_implemented_interfaces(row["sig"])


def test_variables_and_hierarchy(analysis, count):
    variables = analysis.get_variables()
    assert set(variables) == set(analysis.get_symbol_table())
    assert sum(len(v) for v in variables.values()) == count("MATCH (:Application {id: $id})-[:TS_HAS_MODULE]->(:TSModule)-[:TS_HAS_FIELD]->(x) RETURN count(x) AS n", id=APP_ID)
    hierarchy = analysis.get_class_hierarchy()
    assert hierarchy.number_of_nodes() >= count(f"MATCH (x:TSClass|TSInterface) WHERE {SCOPED} AND x.kind IN ['class', 'interface'] RETURN count(DISTINCT x.signature) AS n")


# =====================================================================================
# call graph / call sites
# =====================================================================================
def test_call_graph_matches_the_graphs_edges_and_keys_every_node_by_its_accessor_key(analysis, count):
    graph = analysis.get_call_graph()
    assert graph.number_of_edges() == count(
        f"MATCH (x)-[r:TS_CALLS]->() WHERE {SCOPED} RETURN count(r) AS n"
    ), "one nx edge per TS_CALLS edge (no signature collisions on this graph)"
    kinds = {d["kind"] for _, d in graph.nodes(data=True)}
    assert kinds <= CALL_GRAPH_NODE_KINDS and {"module", "callable", "external"} <= kinds
    assert not any(str(n).startswith("can://") for n in graph.nodes), "a node key is never a raw id (E6)"
    table_keys = set(analysis.get_symbol_table())
    callables = {o.signature for o in analysis.get_callables_overview()}
    externals = set(analysis.get_external_symbols())
    classes = set(analysis.get_classes())
    for key, data in graph.nodes(data=True):
        home = {"module": table_keys, "callable": callables, "external": externals, "class": classes}.get(data["kind"])
        assert home is None or key in home, f"{data['kind']} node {key!r} is not a key an accessor returns"
    src, dst = next(iter(graph.edges))
    assert graph.get_edge_data(src, dst)["type"] == "CALL_DEP" and isinstance(graph.get_edge_data(src, dst)["provenance"], tuple)


def test_callers_callees_and_class_call_graph(analysis, sample):
    callees = analysis.get_callees(sample["method_sig"])
    assert callees["source_method"] == sample["method_sig"]
    callers = analysis.get_callers(sample["class_sig"], sample["method_name"])
    assert callers["target_method"] == sample["method_sig"]
    edges = analysis.get_class_call_graph(sample["class_sig"])
    assert all(isinstance(e, tuple) and len(e) == 2 for e in edges)


def test_call_sites_come_from_body_nodes_and_resolve_over_ts_resolves_to(analysis, sample, cypher):
    sites = analysis.get_call_sites(sample["method_sig"])
    assert len(sites) == sample["calls"]
    resolved = cypher(
        "MATCH (m:CanNode:TSCallable {id: $id})-[:TS_HAS_BODY_NODE]->(s:TSBodyNode {kind: 'call'}) OPTIONAL MATCH (s)-[:TS_RESOLVES_TO]->(t) RETURN count(t) AS n",
        id=sample["method_id"],
    )[0]["n"]
    assert sum(cs.callee_signature is not None for cs in sites) == resolved
    assert all(cs.start_line > 0 and cs.method_name == "" for cs in sites), "lines survive; the receiver/argument facets are not projected"
    assert analysis.get_call_targets(sample["method_sig"]) == {cs.callee_signature or "" for cs in sites}
    for_many = analysis.get_callsites_for([sample["method_sig"], "nope.not.here"])
    assert set(for_many) == {sample["method_sig"]} and len(for_many[sample["method_sig"]]) == sample["calls"]


def test_calling_lines_of_a_real_callee(analysis, cypher):
    row = cypher(
        f"MATCH (t:TSCallable) WHERE {SCOPED.replace('x.', 't.')} MATCH (s:TSBodyNode {{kind: 'call'}})-[:TS_RESOLVES_TO]->(t) RETURN t.signature AS sig, collect(DISTINCT s.start_line) AS lines ORDER BY size(lines) DESC, sig LIMIT 1",
        **SCOPE,
    )[0]
    assert analysis.get_calling_lines(row["sig"]) == sorted(row["lines"])


# =====================================================================================
# bulk accessors / externals / anonymous
# =====================================================================================
def test_callables_overview_covers_every_callable_with_a_verified_path(analysis, count):
    overview = analysis.get_callables_overview()
    assert len(overview) == count(f"MATCH (x:TSCallable) WHERE {SCOPED} RETURN count(x) AS n")
    keys = set(analysis.get_symbol_table())
    assert {o.path for o in overview} <= keys
    assert {o.owner_kind for o in overview} <= {"class", "interface", None}
    assert sum(o.owner_signature is not None for o in overview) == count(f"MATCH (:TSClass|TSInterface)-[:TS_HAS_METHOD]->(x:TSCallable) WHERE {SCOPED} RETURN count(x) AS n")


def test_method_bodies_apply_the_if_code_rule(analysis, count):
    sigs = [o.signature for o in analysis.get_callables_overview()]
    bodies = analysis.get_method_bodies(sigs)
    assert len(bodies) == count(f"MATCH (x:TSCallable) WHERE {SCOPED} AND x.code IS NOT NULL AND x.code <> '' RETURN count(x) AS n")
    assert all(bodies.values())


def test_decorated_callables_are_empty_because_the_graph_has_no_decorator_edges(analysis, cypher):
    assert cypher("MATCH ()-[r:TS_DECORATED_BY]->() RETURN count(r) AS n")[0]["n"] == 0, "this graph grew decorator edges; tighten this test"
    assert analysis.get_decorated_callables(["Component", "Injectable"]) == []
    assert analysis.get_methods_with_decorators(["Component"]) == {"Component": []}


def test_external_symbols_are_keyed_module_dot_name(analysis, count):
    externals = analysis.get_external_symbols()
    assert len(externals) == count("MATCH (x:TSExternal) WHERE x.id STARTS WITH $prefix AND x.name IS NOT NULL RETURN count(x) AS n", prefix=f"{APP_ID}/@external/")
    assert (
        count("MATCH (x:TSExternal) WHERE x.id STARTS WITH $prefix AND x.name IS NULL RETURN count(x) AS n", prefix=f"{APP_ID}/@external/") > 0
    ), "the package-level externals this excludes exist on the graph"
    key, node = next(iter(externals.items()))
    assert key == f"{node.module}.{node.name}" and node.id.startswith(f"{APP_ID}/@external/")


def test_synthesized_callables_are_the_anonymous_tree_nodes(analysis, count):
    synth = analysis.get_synthesized_callables()
    assert len(synth) == count(f"MATCH (x:TSAnonymousCallable) WHERE {SCOPED} RETURN count(x) AS n")
    assert all(k == v.id for k, v in synth.items()), "keyed by the tree node's own id (the older compatibility key is JSON-only)"


# =====================================================================================
# artifact layer -- reached through the facade since leg 2.5b Task 3 put it there
# =====================================================================================
def test_artifact_layer_matches_the_graph(analysis, cypher):
    backend = analysis
    assert len(backend.get_artifacts()) == cypher("MATCH (:Application {id: $id})-[:HAS_ARTIFACT]->(a) RETURN count(a) AS n", id=APP_ID)[0]["n"]
    deps = backend.get_dependencies()
    assert len(deps) == cypher("MATCH (:Application {id: $id})-[:HAS_ARTIFACT]->()-[r:DECLARES_DEPENDENCY]->() RETURN count(r) AS n", id=APP_ID)[0]["n"]
    assert {d.ecosystem for d in deps} == {"npm"}
    assert len(backend.get_dependencies(direct_only=True)) == sum(d.direct for d in deps)
    assert len(backend.get_config_keys()) == cypher("MATCH (:Application {id: $id})-[:HAS_ARTIFACT]->()-[:DEFINES_CONFIG]->(k) RETURN count(k) AS n", id=APP_ID)[0]["n"]
    assert len(backend.get_config_uses()) == cypher(f"MATCH (x:TSBodyNode)-[u:TS_USES_CONFIG]->() WHERE {SCOPED} RETURN count(u) AS n", **SCOPE)[0]["n"]
    # #368: TS_READS_CONFIG_UNRESOLVED reaches the graph from 1.4.0 on, so this answers instead of
    # refusing -- one entry per edge, however many sites collapsed onto it, and `site` always ""
    # (the edge runs application-to-target and never touches the reading body node).
    reads = backend.get_unresolved_config_reads()
    assert len(reads) == cypher("MATCH (:Application {id: $id})-[u:TS_READS_CONFIG_UNRESOLVED]->() RETURN count(u) AS n", id=APP_ID)[0]["n"]
    assert all(r.site == "" and r.callee for r in reads)


# =====================================================================================
# entrypoints (leg 2.5b Task 3) -- every expectation read off the graph at run time
# =====================================================================================
def test_entrypoints_are_exactly_the_callables_the_graph_marks(analysis, cypher):
    marked = {r["sig"] for r in cypher(f"MATCH (x:TSCallable) WHERE {SCOPED} AND x.is_entrypoint = true RETURN x.signature AS sig", **SCOPE)}
    assert marked, "the reference graph marks at least one entrypoint callable"
    entrypoints = analysis.get_entrypoints()
    assert {o.signature for o in entrypoints} == marked
    # every overview carries a module key verified against the application's own (F4), not a split id
    assert all(o.path in analysis.get_symbol_table() for o in entrypoints)


def test_entrypoint_classes_are_exactly_the_classes_the_graph_marks(analysis, cypher):
    """This corpus marks none, which is the case worth pinning: the mark exists on
    ``:TSClass`` nodes (they all carry ``is_entrypoint`` as a real boolean), and none is true."""
    marked = {r["sig"] for r in cypher(f"MATCH (x:TSClass) WHERE {SCOPED} AND x.is_entrypoint = true AND x.kind = 'class' RETURN x.signature AS sig", **SCOPE)}
    assert {o.signature for o in analysis.get_entrypoint_classes()} == marked
    assert cypher(f"MATCH (x:TSClass) WHERE {SCOPED} AND x.is_entrypoint IS NOT NULL RETURN count(x) AS n", **SCOPE)[0]["n"] > 0, "the mark is projected onto classes at all"


def test_entrypoint_coverage_is_the_anchors_own_report_parsed(analysis, cypher):
    """1.3.0 stamps the whole ``TSEntrypointReport`` on ``:Application`` as a JSON **string**
    property; there are no per-entrypoint nodes to rebuild it from, so it is parsed. The sibling
    ``entrypoint_frameworks`` list is that report's own ``frameworks_detected``."""
    row = cypher("MATCH (a:Application {id: $id}) RETURN a.entrypoint_report_json AS j, a.entrypoint_frameworks AS f", id=APP_ID)[0]
    assert row["j"], "the anchor carries no entrypoint_report_json; the floor should have refused this graph"
    report = json.loads(row["j"])
    coverage = analysis.get_entrypoint_coverage()
    assert coverage.diagnostics == []
    assert coverage.frameworks_detected == report["frameworks_detected"] == list(row["f"] or [])
    assert (coverage.rulesets, coverage.unresolved, coverage.errors) == (report["rulesets"], report["unresolved"], report["errors"])
    # The whole point of the separate accessor, on this corpus: a framework *was* detected and the
    # pass left near-misses behind, neither of which a one-element get_entrypoints() list can say.
    assert coverage.frameworks_detected and coverage.unresolved


def test_config_readers_agrees_with_the_config_use_edges(analysis, cypher):
    """``TS_USES_CONFIG`` is not a relationship type on this corpus at all, so both this and
    ``get_config_uses`` are empty for the same reason -- a floor, not a verdict (see
    ``TSNeo4jBackend.get_config_uses``). Asserted against the graph rather than hardcoded, so the
    day a corpus carries the edges this compares readers to edges instead."""
    keys = [r["key"] for r in cypher(f"MATCH (x:TSBodyNode)-[:TS_USES_CONFIG]->(k:ConfigKey) WHERE {SCOPED} RETURN DISTINCT k.key AS key ORDER BY key LIMIT 3", **SCOPE)]
    if not keys:
        assert analysis.get_config_uses() == [] and analysis.get_config_readers("any.key") == []
        return
    for key in keys:
        owners = {
            r["sig"]
            for r in cypher(
                f"MATCH (x:TSBodyNode)-[:TS_USES_CONFIG]->(k:ConfigKey {{key: $key}}) WHERE {SCOPED} MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(x) RETURN DISTINCT c.signature AS sig",
                key=key,
                **SCOPE,
            )
        }
        assert {o.signature for o in analysis.get_config_readers(key)} == owners, key


# =====================================================================================
# the whole view
# =====================================================================================
def test_application_view_round_trips(analysis, count):
    app = analysis.get_application_view()
    assert app.id == APP_ID
    assert set(app.symbol_table) == set(analysis.get_symbol_table())
    assert len(app.call_graph) == count(f"MATCH (x)-[r:TS_CALLS]->() WHERE {SCOPED} RETURN count(r) AS n")
    assert len(app.external_symbols) == len(analysis.get_external_symbols())
    # The artifact layer is in the view, not only behind the dedicated accessors (which is what a
    # caller reading app.dependencies sees). param_in/param_out stay empty: 2.5a reads no dataflow.
    backend = analysis.backend
    assert set(app.artifacts) == set(backend.get_artifacts())
    assert sum(len(a.config_keys) for a in app.artifacts.values()) == len(backend.get_config_keys())
    assert len(app.dependencies) == len(backend.get_dependencies())
    assert len(app.config_uses) == len(backend.get_config_uses())
    assert app.param_in == [] and app.param_out == [] and app.config_reads == [] and app.unresolved_imports == []
    assert app.model_dump_json()
