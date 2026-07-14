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

"""Integration tests for the read-only Neo4j-backed TypeScript analysis backend.

These exercise the *real* pipeline: the test harness loads the sample app's graph into a live
Neo4j out of band (``codeanalyzer-typescript --emit neo4j`` over Bolt — the same way a cloud
deployment would), and every assertion is then answered, read-only, by Cypher in
:class:`TSNeo4jBackend`. They mirror the in-memory backend's expectations from
``test_typescript_analysis.py`` so the two backends are proven to agree.

The whole module is skipped unless a Neo4j server is reachable. Point the tests at one with:

    CLDK_TEST_NEO4J_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=test \
    pytest tests/analysis/typescript/test_typescript_neo4j_backend.py

(e.g. `docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5`). The binary is resolved the
usual way: ``$CODEANALYZER_TS_BIN``, then the ``codeanalyzer-typescript`` wheel.
"""

import logging
import os
import shlex
import subprocess
from pathlib import Path

import networkx as nx
import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.typescript.neo4j import Neo4jConnectionConfig

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
APP_NAME = "application"


def _neo4j_reachable() -> bool:
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:  # noqa: BLE001 - any connection failure ⇒ skip
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason=f"no Neo4j reachable at {NEO4J_URI} (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD)",
)


def _codeanalyzer_ts_exec() -> list[str]:
    """Resolve the codeanalyzer-typescript executable: ``$CODEANALYZER_TS_BIN``, else the wheel."""
    env_bin = os.environ.get("CODEANALYZER_TS_BIN")
    if env_bin:
        return shlex.split(env_bin)
    import codeanalyzer_typescript

    return [str(codeanalyzer_typescript.bin_path())]


def _populate_neo4j(project_dir) -> None:
    """Load the sample app's graph into Neo4j out of band (what a cloud job would do).

    The SDK's Neo4j backend is read-only, so the test harness — not CLDK — runs the analyzer's
    ``--emit neo4j`` to push the graph over Bolt before the read-only assertions run.
    """
    args = _codeanalyzer_ts_exec() + [
        "-i",
        str(Path(project_dir)),
        "-a",
        "2",  # call_graph
        "--emit",
        "neo4j",
        "--neo4j-uri",
        NEO4J_URI,
        "--neo4j-user",
        NEO4J_USER,
        "--neo4j-password",
        NEO4J_PASSWORD,
        "--app-name",
        APP_NAME,
        "--eager",  # clean rebuild of this app's subgraph
    ]
    subprocess.run(args, capture_output=True, text=True, check=True)


@pytest.fixture(scope="module")
def ts_neo4j(typescript_application):
    """A read-only TypeScript facade over a Neo4j graph loaded out of band for the sample app."""
    _populate_neo4j(typescript_application)
    config = Neo4jConnectionConfig(
        uri=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASSWORD,
        application_name=APP_NAME,
    )
    analysis = CLDK(language="typescript").analysis(
        project_path=typescript_application,
        analysis_level=AnalysisLevel.call_graph,
        neo4j_config=config,
    )
    yield analysis
    analysis.backend.close()


def test_backend_is_neo4j(ts_neo4j):
    from cldk.analysis.typescript.neo4j import TSNeo4jBackend

    assert isinstance(ts_neo4j.backend, TSNeo4jBackend)


def test_symbol_table(ts_neo4j):
    symtab = ts_neo4j.get_symbol_table()
    assert len(symtab) == 5
    assert "src/models.ts" in symtab
    assert "src/controllers.ts" in symtab


def test_classes_interfaces_enums_type_aliases(ts_neo4j):
    classes = ts_neo4j.get_classes()
    assert "src/models.User" in classes
    assert "src/services.UserService" in classes
    assert set(ts_neo4j.get_interfaces()) >= {"src/models.Identifiable", "src/models.Named"}
    assert "src/models.Role" in ts_neo4j.get_enums()
    assert "src/models.UserId" in ts_neo4j.get_type_aliases()


def test_class_inheritance_split(ts_neo4j):
    user = ts_neo4j.get_class("src/models.User")
    assert "src/models.Entity" in user.base_classes
    assert ts_neo4j.get_implemented_interfaces("src/models.User") == ["src/models.Named"]
    assert "src/models.Entity" in ts_neo4j.get_extended_classes("src/models.User")
    assert user.is_abstract is False


def test_methods_and_constructor(ts_neo4j):
    methods = ts_neo4j.get_methods_in_class("src/models.User")
    assert "describe" in methods
    assert "recordLogin" in methods
    assert methods["recordLogin"].is_async is True
    constructors = ts_neo4j.get_constructors("src/models.User")
    assert any(c.kind == "constructor" for c in constructors.values())


def test_fields_and_parameters(ts_neo4j):
    fields = {f.name for f in ts_neo4j.get_fields("src/models.User")}
    assert {"name", "role"} <= fields
    params = ts_neo4j.get_method_parameters("src/services.UserService", "create")
    assert isinstance(params, list)


def test_get_method_resolves_module_level_function(ts_neo4j):
    # regression for #247: get_method used to be class-scope only, so "src/index.main" (a
    # module-level function that participates in a call edge, see test_callers_and_callees) was
    # unreachable through it.
    method = ts_neo4j.get_method("src/index", "main")
    assert method is not None
    assert method.signature == "src/index.main"


def test_get_method_parameters_module_level_function(ts_neo4j):
    params = ts_neo4j.get_method_parameters("src/index", "main")
    assert isinstance(params, list)


def test_structured_decorators(ts_neo4j):
    decorated = ts_neo4j.get_methods_with_decorators(["Controller", "Get"])
    assert any(sig.endswith("UserController.show") for sig in decorated["Get"])
    controller = ts_neo4j.get_class("src/controllers.UserController")
    assert [d.name for d in controller.decorators] == ["Controller"]
    assert controller.decorators[0].positional_arguments == ['"/users"']


def test_class_decorators_query(ts_neo4j):
    classes = ts_neo4j.get_classes_with_decorators(["Controller"])
    assert any(sig.endswith("UserController") for sig in classes["Controller"])


def test_call_graph_no_dangling_nodes(ts_neo4j):
    graph = ts_neo4j.get_call_graph()
    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_edges() > 0
    nodes = set(graph.nodes)
    for src, dst in graph.edges:
        assert src in nodes
        assert dst in nodes
    # edge metadata is surfaced just like the in-memory backend
    src, dst = next(iter(graph.edges))
    data = graph.get_edge_data(src, dst)
    assert data["type"] == "CALL_DEP"
    assert "provenance" in data and "tags" in data


def test_callers_and_callees(ts_neo4j):
    # bare-signature form (module-level function)
    callees = ts_neo4j.get_callees("src/index.main")
    callee_sigs = {c["callee_signature"] for c in callees["callee_details"]}
    assert "src/services.UserService.constructor" in callee_sigs

    # (class, method) form, with edge metadata surfaced
    callers = ts_neo4j.get_callers("src/services.UserService", "create")
    assert callers["target_method"] == "src/services.UserService.create"
    caller_sigs = {c["caller_signature"] for c in callers["caller_details"]}
    assert "src/index.main" in caller_sigs
    main_edge = next(c["edge"] for c in callers["caller_details"] if c["caller_signature"] == "src/index.main")
    assert "provenance" in main_edge and "tags" in main_edge


def test_call_sites(ts_neo4j):
    sites = ts_neo4j.get_call_sites("src/controllers.UserController.show")
    assert any(cs.callee_signature == "src/services.UserService.create" for cs in sites)
    create = next(cs for cs in sites if cs.callee_signature == "src/services.UserService.create")
    assert create.receiver_type == "UserService"
    assert create.start_line > 0

    lines = ts_neo4j.get_calling_lines("src/services.UserService.create")
    assert lines == sorted(lines)
    assert all(line > 0 for line in lines)

    targets = ts_neo4j.get_call_targets("src/controllers.UserController.show")
    assert "src/services.UserService.create" in targets


def test_class_call_graph(ts_neo4j):
    edges = ts_neo4j.get_class_call_graph("src/controllers.UserController")
    assert all(isinstance(e, tuple) and len(e) == 2 for e in edges)
    flat = {s for s, _ in edges} | {t for _, t in edges}
    assert any("UserController" in s for s in flat)


def test_class_hierarchy(ts_neo4j):
    hierarchy = ts_neo4j.get_class_hierarchy()
    assert isinstance(hierarchy, nx.DiGraph)
    assert hierarchy.has_edge("src/models.User", "src/models.Entity")


def test_enum_members(ts_neo4j):
    members = ts_neo4j.get_enum_members("src/models.Role")
    assert len(members) > 0
    assert all(m.name for m in members)


def test_typescript_file_lookup(ts_neo4j):
    assert ts_neo4j.get_typescript_file("src/models.User") == "src/models.ts"


def test_application_view_round_trips(ts_neo4j):
    app = ts_neo4j.get_application_view()
    assert set(app.symbol_table) == set(ts_neo4j.get_symbol_table())
    assert len(app.call_graph) == ts_neo4j.get_call_graph().number_of_edges()


def test_read_only_backend_queries_loaded_db(ts_neo4j):
    """The read-only backend can query an already-loaded DB with no project_dir / binary."""
    from cldk.analysis.typescript.neo4j import TSNeo4jBackend

    backend = TSNeo4jBackend(
        neo4j_uri=NEO4J_URI,
        neo4j_username=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
        application_name=APP_NAME,
    )
    try:
        assert len(backend.get_all_classes()) == 6
    finally:
        backend.close()
