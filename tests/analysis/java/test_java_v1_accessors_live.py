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

r"""The six 1.x accessors (#366) against a real graph, on both backends.

The offline suite proves the policy over a fixture both backends are seeded from, which is exactly
what a fixture *cannot* prove: that the projection carries the same facts as ``analysis.json``.
This runs the real graph -- daytrader8 in a database that also holds ThingsBoard, so a scope leak
shows up as a larger answer -- against the level-4 reference cache, and compares the two answers.

**Three divergences are pinned here rather than hidden**, because each is a property of the
projection and not of these accessors:

* ``get_variables`` agrees on ``name``/``type``/``start_line`` and *not* on the span's columns or
  byte offsets: ``:JLocal`` carries a line-only span, as every other node on this surface does.
  That is also why the lists are sorted by ``(line, name)`` -- 6 of daytrader8's callables declare
  two variables on one line, and the graph fixes no order between them.
* ``get_methods_with_annotations`` agrees on ``class``/``signature``/``method_name`` and not on
  ``body``, which is :attr:`~cldk.models.java.models.JCallable.code` -- the body block off
  ``analysis.json`` and the whole declaration off the projection, the documented model property
  :meth:`JavaAnalysis.get_test_methods` also hands back.
* ``get_class_hierarchy`` is built from each declaration's own ``base_types``/``interfaces`` rather
  than from ``J_EXTENDS``/``J_IMPLEMENTS``, and the test below says what that is worth: the
  relationships join **8** of daytrader8's type pairs, the declarations **103**.

Same environment as ``test_java_addressing_live.py``::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    CLDK_TEST_NEO4J_APP=daytrader8 \
    CLDK_TEST_JAVA_PROJECT=/path/to/project \
    CLDK_TEST_JAVA_CACHE=/path/to/dir \        # a level-4 reference analysis.json
    uv run pytest tests/analysis/java/test_java_v1_accessors_live.py

Read-only, like every other Neo4j suite here.
"""

import json
import logging
import os
from pathlib import Path

import networkx as nx
import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")
SCALE_APP = os.environ.get("CLDK_TEST_NEO4J_SCALE_APP", "thingsboard")

REFERENCE_LEVEL = "system_dependency_graph"

DIRECT = "com.ibm.websphere.samples.daytrader.impl.direct"
TRADE_DIRECT = f"{DIRECT}.TradeDirect"
PING_SERVLET = "com.ibm.websphere.samples.daytrader.web.prims.PingServlet"

#: Measured on the reference graph (daytrader8, codeanalyzer-java 3.1.0), 2026-09-07.
DT_IMPORTS = 268
DT_CALLABLES = 1216
DT_DECLARING_LOCALS, DT_LOCALS = 235, 854
DT_HIERARCHY_NODES, DT_EXTENDS, DT_IMPLEMENTS = 170, 58, 45
DT_EXTERNAL_SUPERTYPES = 21
DT_OVERRIDE, DT_INJECT = 328, 12
DT_TRADE_DIRECT_TARGETS = 53
DT_GET_STATEMENT_LINES, DT_CANCEL_ORDER_LINES = 56, 5

#: The same six on ThingsBoard, which is the corpus with the kinds daytrader8 has none of.
TB_IMPORTS = 5607
TB_CALLABLES, TB_LOCALS = 28763, 29813
TB_HIERARCHY_NODES, TB_EXTENDS, TB_IMPLEMENTS = 6279, 2579, 1508
TB_OVERRIDE, TB_TEST = 6396, 3176


def _reference_cache_is_level_4() -> bool:
    if not JAVA_CACHE:
        return False
    try:
        return int(json.loads((Path(JAVA_CACHE) / "analysis.json").read_text(encoding="utf-8")).get("max_level", 0)) >= 4
    except (OSError, ValueError, AttributeError):
        return False


def _reachable() -> bool:
    if not (JAVA_APP and JAVA_PROJECT and _reference_cache_is_level_4()):
        return False
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="needs a pre-populated Neo4j Java graph + a level-4 reference cache (set CLDK_TEST_NEO4J_* / CLDK_TEST_JAVA_*)")


@pytest.fixture(scope="module")
def backends():
    from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
    from cldk.analysis.java.neo4j import JNeo4jBackend

    ref = JCodeanalyzer(project_dir=JAVA_PROJECT, analysis_json_path=JAVA_CACHE, analysis_level=REFERENCE_LEVEL, eager_analysis=False, target_files=None)
    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=JAVA_APP)
    yield ref, neo
    neo.close()


@pytest.fixture(scope="module")
def scale():
    from cldk.analysis.java.neo4j import JNeo4jBackend

    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=SCALE_APP)
    if not neo._run("MATCH (a:JApplication {name: $app}) RETURN a LIMIT 1", app=SCALE_APP):
        neo.close()
        pytest.skip(f"the graph does not hold application {SCALE_APP!r}")
    yield neo
    neo.close()


# ---- get_imports ------------------------------------------------------------------------------
def test_the_import_targets_agree_exactly(backends):
    ref, neo = backends
    assert ref.get_imports() == neo.get_imports(), "the projection's aggregated J_IMPORTS edges carry the same targets"
    assert len(ref.get_imports()) == DT_IMPORTS


def test_the_import_set_is_smaller_than_the_per_file_declarations(backends):
    """What the set costs: the same target imported by several files collapses, and file order is
    gone with it. What it buys: an answer the two backends can both give."""
    ref, _ = backends
    per_file = sum(len(unit.import_declarations) for unit in ref.get_symbol_table().values())
    assert per_file > DT_IMPORTS
    assert set(ref.get_imports()) == {i.path for unit in ref.get_symbol_table().values() for i in unit.import_declarations}


# ---- get_variables ----------------------------------------------------------------------------
def test_the_local_variables_agree_name_type_and_line(backends):
    """``:JVariable`` carries no column, so those stay placeholders over the graph exactly as they
    are everywhere else on this surface; its **byte offsets** are real since codeanalyzer-java 3.2.0
    (863 of daytrader8's 863 locals carry them) and are asserted equal. ``JLocalVariable.code``
    raises on **both** backends and is not asserted: ``_thread_type`` threads a type's fields,
    callables and body nodes into the compilation unit and not a callable's locals, so neither side
    has a unit to slice -- the same answer from the same model, not a projection gap."""
    ref, neo = backends
    local, graph = ref.get_variables(), neo.get_variables()
    assert set(local) == set(graph) and len(local) == DT_CALLABLES
    for key, declared in local.items():
        assert [(v.name, v.type, v.start_line, v.initializer) for v in declared] == [(v.name, v.type, v.start_line, v.initializer) for v in graph[key]], key
        assert [v.span.bytes for v in declared] == [v.span.bytes for v in graph[key]], f"{key}: the two backends place the same local at different bytes"
    assert sum(1 for v in local.values() if v) == DT_DECLARING_LOCALS
    assert sum(len(v) for v in local.values()) == DT_LOCALS


def test_the_ordering_is_fixed_because_the_graph_does_not_fix_it(backends):
    """6 of daytrader8's callables declare two variables on one line (``String htmlString,
    arrow;``). The projection has no column to order them by, so the accessor sorts and both
    backends agree; without the sort the multiset matched and the list did not."""
    ref, neo = backends
    local, graph = ref.get_variables(), neo.get_variables()
    same_line = [key for key, declared in local.items() if len({v.start_line for v in declared}) < len(declared)]
    assert len(same_line) == 6
    for key in same_line:
        assert [v.name for v in local[key]] == [v.name for v in graph[key]]
        assert [(v.start_line, v.name) for v in local[key]] == sorted((v.start_line, v.name) for v in local[key]), "(line, name), so a shared line has one order"


# ---- get_class_hierarchy ----------------------------------------------------------------------
def test_the_class_hierarchies_are_the_same_graph(backends):
    ref, neo = backends
    local, graph = ref.get_class_hierarchy(), neo.get_class_hierarchy()
    assert nx.utils.graphs_equal(local, graph)
    assert local.number_of_nodes() == DT_HIERARCHY_NODES
    kinds = [d["type"] for _, _, d in local.edges(data=True)]
    assert kinds.count("EXTENDS") == DT_EXTENDS and kinds.count("IMPLEMENTS") == DT_IMPLEMENTS
    assert graph.edges[PING_SERVLET, "javax.servlet.http.HttpServlet"]["type"] == "EXTENDS"


def test_reading_the_relationships_instead_would_lose_most_of_the_hierarchy(backends):
    """The reason the declaration's own ``base_types``/``interfaces`` are read and
    ``J_EXTENDS``/``J_IMPLEMENTS`` are not: a relationship needs a node at both ends, and almost
    every supertype daytrader8 names is a library type the projection has no node for."""
    _, neo = backends
    counted = {
        rel: neo._run(f"MATCH (:JApplication {{name: $app}})-[:J_HAS_MODULE]->(:JModule)-[:J_DECLARES*1..4]->(t:JType)-[e:{rel}]->() RETURN count(e) AS n", app=JAVA_APP)[0]["n"]
        for rel in ("J_EXTENDS", "J_IMPLEMENTS")
    }
    assert counted == {"J_EXTENDS": 1, "J_IMPLEMENTS": 7}
    assert sum(counted.values()) < neo.get_class_hierarchy().number_of_edges() // 10


def test_out_of_project_supertypes_are_nodes_spelled_as_declared(backends):
    ref, neo = backends
    external = set(neo.get_class_hierarchy().nodes) - set(neo.get_all_classes())
    assert external == set(ref.get_class_hierarchy().nodes) - set(ref.get_all_classes())
    assert len(external) == DT_EXTERNAL_SUPERTYPES
    assert "java.util.Comparator<com.ibm.websphere.samples.daytrader.entities.QuoteDataBean>" in external


# ---- get_methods_with_annotations --------------------------------------------------------------
def _without_bodies(found):
    return {marker: [{k: v for k, v in entry.items() if k != "body"} for entry in entries] for marker, entries in found.items()}


def test_the_annotated_callables_agree_entry_for_entry(backends):
    ref, neo = backends
    asked = ["Override", "@Inject", "WebServlet"]
    local, graph = ref.get_methods_with_annotations(asked), neo.get_methods_with_annotations(asked)
    assert _without_bodies(local) == _without_bodies(graph)
    assert set(local) == {"Override", "@Inject"}, "WebServlet is a type annotation here, so a callable filter omits it"
    assert len(local["Override"]) == DT_OVERRIDE and len(local["@Inject"]) == DT_INJECT


def test_the_body_is_the_documented_per_backend_code(backends):
    """Not a divergence this accessor introduces: ``body`` is
    :attr:`~cldk.models.java.models.JCallable.code`, which the model documents as the body block
    off ``analysis.json`` and the whole declaration off the projection. Pinned so that it is
    visible rather than surprising."""
    ref, neo = backends
    local = ref.get_methods_with_annotations(["Override"])["Override"]
    graph = neo.get_methods_with_annotations(["Override"])["Override"]
    assert all(entry["body"] for entry in local) and all(entry["body"] for entry in graph)
    assert all(entry["body"].lstrip().startswith("{") for entry in local), "the body block"
    assert sum(1 for entry in graph if not entry["body"].lstrip().startswith("{")) > DT_OVERRIDE // 2, "the declaration"


# ---- get_call_targets ---------------------------------------------------------------------------
def test_the_call_targets_agree(backends):
    ref, neo = backends
    declared = ref.get_all_methods_in_class(TRADE_DIRECT)
    assert ref.get_call_targets(declared) == neo.get_call_targets(neo.get_all_methods_in_class(TRADE_DIRECT))
    assert len(ref.get_call_targets(declared)) == DT_TRADE_DIRECT_TARGETS
    assert ref.get_call_targets({}) == neo.get_call_targets({}) == set()


# ---- get_calling_lines --------------------------------------------------------------------------
def test_the_calling_lines_agree_and_are_absolute_file_lines(backends):
    """Leg 3a made these agree rather than shift by a declaration prefix; this is the assertion
    that keeps them agreeing over a real projection. It was written when ``code`` and
    ``code_start_line`` still differed between the two backends, which is what made the shift
    possible; 3.2.0 makes both equal, and these lines are absolute file lines either way."""
    ref, neo = backends
    lines = ref.get_calling_lines("getStatement")
    assert lines == neo.get_calling_lines("getStatement")
    assert lines == sorted(set(lines)) and len(lines) == DT_GET_STATEMENT_LINES
    assert ref.get_calling_lines("cancelOrder") == neo.get_calling_lines("cancelOrder")
    assert len(ref.get_calling_lines("cancelOrder")) == DT_CANCEL_ORDER_LINES
    assert ref.get_calling_lines("noSuchMethodAnywhere") == neo.get_calling_lines("noSuchMethodAnywhere") == []


def test_a_calling_line_points_at_the_call_in_the_file(backends):
    """The number is an index into the file, not into ``JCallable.code`` -- so it can be checked
    against the source on disk."""
    ref, _ = backends
    path = Path(JAVA_PROJECT) / ref.get_java_file(TRADE_DIRECT)
    source = path.read_text(encoding="utf-8").splitlines()
    for line in ref.get_calling_lines("getStatement"):
        if line <= len(source) and "getStatement" in source[line - 1]:
            break
    else:  # pragma: no cover - a failure path
        pytest.fail("no reported line of TradeDirect.java holds a call to getStatement")


# ---- the scale corpus ----------------------------------------------------------------------------
def test_the_six_answer_on_the_scale_corpus(scale):
    """ThingsBoard, in the same database, so a scope leak reads as a larger answer. The corpus that
    actually has ``@Test`` methods -- daytrader8 has none."""
    assert len(scale.get_imports()) == TB_IMPORTS
    variables = scale.get_variables()
    assert len(variables) == TB_CALLABLES and sum(len(v) for v in variables.values()) == TB_LOCALS
    hierarchy = scale.get_class_hierarchy()
    kinds = [d["type"] for _, _, d in hierarchy.edges(data=True)]
    assert hierarchy.number_of_nodes() == TB_HIERARCHY_NODES
    assert kinds.count("EXTENDS") == TB_EXTENDS and kinds.count("IMPLEMENTS") == TB_IMPLEMENTS
    annotated = scale.get_methods_with_annotations(["Override", "Test"])
    assert len(annotated["Override"]) == TB_OVERRIDE and len(annotated["Test"]) == TB_TEST
