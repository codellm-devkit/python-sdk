"""Live Neo4j parity: GoNeo4jBackend must answer identically to GoCodeanalyzer on the same
application. Skips unless a Go graph is reachable AND its matching analysis.json is given.

Set, pointing at a read-only graph a ``cango --emit neo4j`` run populated:

    CLDK_TEST_GO_NEO4J_URI   (e.g. bolt://localhost:7687)
    CLDK_TEST_GO_NEO4J_USER  (default: neo4j)
    CLDK_TEST_GO_NEO4J_PASSWORD
    CLDK_TEST_GO_NEO4J_APP   (the --app-name the graph was loaded with, e.g. go/cobra)
    CLDK_TEST_GO_ANALYSIS_JSON  (path to the SAME application's analysis.json, for the diff)

With the first four set but no analysis.json, only the standalone (non-parity) checks run.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.analysis.go.neo4j import GoNeo4jBackend

URI = os.environ.get("CLDK_TEST_GO_NEO4J_URI")
USER = os.environ.get("CLDK_TEST_GO_NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("CLDK_TEST_GO_NEO4J_PASSWORD")
APP = os.environ.get("CLDK_TEST_GO_NEO4J_APP")
ANALYSIS_JSON = os.environ.get("CLDK_TEST_GO_ANALYSIS_JSON")

pytestmark = pytest.mark.skipif(
    not (URI and PASSWORD and APP),
    reason="set CLDK_TEST_GO_NEO4J_URI / _PASSWORD / _APP to a read-only codeanalyzer-go graph",
)


@pytest.fixture(scope="module")
def neo4j_backend():
    b = GoNeo4jBackend(neo4j_uri=URI, neo4j_username=USER, neo4j_password=PASSWORD, application_name=APP)
    yield b
    b.close()


@pytest.fixture(scope="module")
def local_backend(tmp_path_factory):
    if not (ANALYSIS_JSON and Path(ANALYSIS_JSON).exists()):
        pytest.skip("set CLDK_TEST_GO_ANALYSIS_JSON to the same application's analysis.json for the parity diff")
    cache = tmp_path_factory.mktemp("go-parity")
    (cache / "go").mkdir()
    shutil.copy(ANALYSIS_JSON, cache / "go" / "analysis.json")
    return GoCodeanalyzer(project_dir="/unused", cache_dir=str(cache), analysis_level="call_graph", eager_analysis=False, target_files=None)


# -----[ standalone: the graph answers at all ]-----
def test_neo4j_backend_populated(neo4j_backend):
    assert neo4j_backend.get_all_classes() or neo4j_backend.get_functions()
    assert neo4j_backend.get_call_graph().number_of_edges() >= 0


def test_artifact_six_raise_on_neo4j_too(neo4j_backend):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    for name in ("get_artifacts", "get_dependencies", "get_config_keys", "get_config_uses", "get_unresolved_config_reads"):
        with pytest.raises(CodeanalyzerExecutionException):
            getattr(neo4j_backend, name)()


# -----[ parity: the two backends agree on the same application ]-----
def test_type_id_sets_match(local_backend, neo4j_backend):
    assert set(local_backend.get_all_classes()) == set(neo4j_backend.get_all_classes())


def test_function_signature_sets_match(local_backend, neo4j_backend):
    assert set(local_backend.get_functions()) == set(neo4j_backend.get_functions())


def test_call_graph_edge_counts_match(local_backend, neo4j_backend):
    assert local_backend.get_call_graph().number_of_edges() == neo4j_backend.get_call_graph().number_of_edges()


def test_symbol_table_module_counts_match(local_backend, neo4j_backend):
    assert len(local_backend.get_symbol_table()) == len(neo4j_backend.get_symbol_table())


def test_callees_match_on_a_real_callable(local_backend, neo4j_backend):
    edges = local_backend.get_application_view().call_graph
    if not edges:
        pytest.skip("no call edges in this application")
    cid = edges[0].src
    lo = sorted((e.src, e.dst, tuple(e.prov), e.weight) for e in local_backend.get_callees(cid))
    ne = sorted((e.src, e.dst, tuple(e.prov), e.weight) for e in neo4j_backend.get_callees(cid))
    assert lo == ne
