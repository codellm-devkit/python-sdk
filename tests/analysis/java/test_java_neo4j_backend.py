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

"""Integration parity tests: the read-only Java Neo4j backend vs the analysis.json backend.

These assert that :class:`JNeo4jBackend` answers every query **identically** to the canonical
:class:`JCodeanalyzer` (analysis.json) backend on the same project — the definition of the
"1-to-1 map".

Because the Java graph is populated out of band by the analyzer JAR (which needs a JDK and a Maven
build of the target for a level-2 call graph), this test does **not** populate inline. It is skipped
unless you point it at an already-populated Neo4j and a matching reference analysis cache:

    CLDK_TEST_NEO4J_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=test \
    CLDK_TEST_NEO4J_JAVA_APP=daytrader \      # the --app-name the graph was loaded with
    CLDK_TEST_JAVA_PROJECT=/path/to/project \  # the reference project dir
    CLDK_TEST_JAVA_CACHE=/path/to/cache/java \ # dir containing the reference analysis.json
    pytest tests/analysis/java/test_java_neo4j_backend.py

Populate the graph with: ``codeanalyzer-java -i <project> --analysis-level 2 --emit neo4j
--neo4j-uri ... --app-name <app>``, and produce the reference with ``... --analysis-level 2 -o
<cache>``.

Parity is asserted modulo the projection's documented-lossy fields (see
``cldk.analysis.java.neo4j.reconstruct``): a ``JType``'s ``is_class_or_interface_declaration`` /
``is_concrete_class`` flags are not projected (only ``kind`` is).
"""

import json
import logging
import os

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_JAVA_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")

LOSSY_TYPE = {"is_class_or_interface_declaration", "is_concrete_class"}


def _neo4j_reachable() -> bool:
    if not (JAVA_APP and JAVA_PROJECT and JAVA_CACHE):
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


pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason="needs a pre-populated Neo4j Java graph + reference cache (set CLDK_TEST_NEO4J_* / CLDK_TEST_JAVA_*)",
)


def _norm(o):
    if hasattr(o, "model_dump"):
        o = o.model_dump()
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items() if k not in LOSSY_TYPE}
    if isinstance(o, list):
        items = [_norm(x) for x in o]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except Exception:
            return items
    return o


@pytest.fixture(scope="module")
def backends():
    from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
    from cldk.analysis.java.neo4j import JNeo4jBackend

    ref = JCodeanalyzer(project_dir=JAVA_PROJECT, source_code=None, analysis_json_path=JAVA_CACHE, analysis_level="call_graph", eager_analysis=False, target_files=None)
    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=JAVA_APP)
    yield ref, neo
    neo.close()


def test_symbol_table_and_classes_parity(backends):
    ref, neo = backends
    assert sorted(ref.get_symbol_table()) == sorted(neo.get_symbol_table())
    ac_ref, ac_neo = ref.get_all_classes(), neo.get_all_classes()
    assert sorted(ac_ref) == sorted(ac_neo)
    for cls in ac_ref:
        assert _norm(ac_ref[cls]) == _norm(ac_neo[cls]), f"class {cls} differs"


def test_methods_fields_hierarchy_parity(backends):
    ref, neo = backends
    for cls in ref.get_all_classes():
        assert _norm(ref.get_all_fields(cls)) == _norm(neo.get_all_fields(cls))
        assert ref.get_extended_classes(cls) == neo.get_extended_classes(cls)
        assert ref.get_implemented_interfaces(cls) == neo.get_implemented_interfaces(cls)
        assert sorted(ref.get_all_sub_classes(cls)) == sorted(neo.get_all_sub_classes(cls))
        mc = ref.get_all_methods_in_class(cls)
        assert sorted(mc) == sorted(neo.get_all_methods_in_class(cls))
        for sig in mc:
            assert _norm(ref.get_method(cls, sig)) == _norm(neo.get_method(cls, sig)), f"{cls}::{sig} differs"


def test_call_graph_parity(backends):
    ref, neo = backends
    gr, gn = ref.get_call_graph(), neo.get_call_graph()

    def edgeset(g):
        return sorted([list(u), list(v), g[u][v].get("type"), str(g[u][v].get("weight"))] for u, v in g.edges)

    assert edgeset(gr) == edgeset(gn)
    assert sorted([list(n) for n in gr.nodes]) == sorted([list(n) for n in gn.nodes])


def test_entrypoints_and_comments_parity(backends):
    ref, neo = backends
    assert sorted(ref.get_all_entry_point_classes()) == sorted(neo.get_all_entry_point_classes())
    assert sorted(ref.get_all_entry_point_methods()) == sorted(neo.get_all_entry_point_methods())
    assert sorted(ref.get_all_comments()) == sorted(neo.get_all_comments())
    assert sorted(ref.get_all_docstrings()) == sorted(neo.get_all_docstrings())
