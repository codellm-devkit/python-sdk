################################################################################
# Copyright IBM Corporation 2024, 2025
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

"""
Test Cases for JCodeanalyzer (codeanalyzer-java 3.0.1, schema v2). The analyzer subprocess is
mocked; ``analysis_json`` is daytrader8 at ``-a 1`` (no call graph), ``analysis_json_a4`` the
four-file ``-a 4`` slice (247 call edges).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch, MagicMock

import networkx as nx
import pytest

from cldk.analysis import AnalysisLevel
from cldk.analysis.java.backend import CRUD_UNAVAILABLE
from cldk.analysis.java.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.codeanalyzer import _jdk
from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java import JGraphEdges
from cldk.models.java.models import JAnalysis, JApplication, JCallGraphEdge, JComment, JType, JCallable, JCompilationUnit, JMethodDetail
from cldk.models.python import PyArtifact, PyConfigKey, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

_RUN = "cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run"
TRADE_DIRECT = "com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect"
LOG = "com.ibm.websphere.samples.daytrader.util.Log"


def _write_output(payload: str):
    """subprocess.run side effect: write analysis.json into the ``-o`` dir when one is given."""

    def _run(cmd, *a, **kw):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


def _analyzer(payload: str, level=AnalysisLevel.symbol_table, project_dir=".", analysis_json_path=None, eager=False, target_files=None):
    with patch(_RUN) as run_mock:
        run_mock.side_effect = _write_output(payload)
        analyzer = JCodeanalyzer(
            project_dir=project_dir,
            analysis_json_path=analysis_json_path,
            analysis_level=level,
            eager_analysis=eager,
            target_files=target_files,
        )
    return analyzer, run_mock


# -----[ driving the analyzer ]-----


@pytest.mark.parametrize("level, a", [(AnalysisLevel.symbol_table, "1"), (AnalysisLevel.call_graph, "2"), (AnalysisLevel.program_dependency_graph, "3"), (AnalysisLevel.system_dependency_graph, "4")])
def test_argv_is_the_301_command_line(test_fixture, analysis_json, tmp_path, level, a):
    """``-a`` carries the requested level (3 and 4 are new to Java); ``-o``/``-c`` only with a cache dir;
    ``--app-name`` is the project directory's name, which the analyzer stamps into every id."""
    analyzer, run_mock = _analyzer(analysis_json, level=level, project_dir=test_fixture, analysis_json_path=tmp_path, target_files=["a.java", "b.java"])
    argv = run_mock.call_args[0][0]
    assert argv[1] == "-jar" and argv[2] == str(analyzer._locate_jar())
    assert argv[3:] == ["-i", str(test_fixture), "-a", a, "-o", str(tmp_path), "-c", str(tmp_path / "cache"), "--app-name", test_fixture.name, "-t", "a.java", "-t", "b.java"]
    assert isinstance(analyzer.application, JApplication)


def test_pipe_mode_has_no_output_or_cache_flags(test_fixture, analysis_json):
    _, run_mock = _analyzer(analysis_json, project_dir=test_fixture)
    argv = run_mock.call_args[0][0]
    assert argv[3:] == ["-i", str(test_fixture), "-a", "1", "--app-name", test_fixture.name]


def test_unknown_level_raises_instead_of_defaulting(test_fixture, analysis_json):
    with pytest.raises(ValueError, match="unknown analysis_level"):
        _analyzer(analysis_json, level="call-graph", project_dir=test_fixture)


def test_envelope_and_application_are_kept(test_fixture, analysis_json):
    analyzer, _ = _analyzer(analysis_json, project_dir=test_fixture)
    assert analyzer.analysis.schema_version == "2.0.0"
    assert analyzer.analysis.analyzer.version == "3.0.1"
    assert analyzer.analysis.max_level == 1
    assert analyzer.application is analyzer.analysis.application
    assert analyzer.application.id == "can://java/daytrader8"


def test_cached_analysis_is_reused(test_fixture, analysis_json, tmp_path):
    (tmp_path / "analysis.json").write_text(analysis_json, encoding="utf-8")
    analyzer, run_mock = _analyzer(analysis_json, project_dir=test_fixture, analysis_json_path=tmp_path)
    assert not run_mock.called
    assert len(analyzer.get_symbol_table()) == 138


def test_v1_cache_is_refused_with_the_rerun_message(test_fixture, tmp_path):
    """J-9 (local half): a cached ``analysis.json`` without ``schema_version`` is a 2.x artifact."""
    cache_file = tmp_path / "analysis.json"
    cache_file.write_text(json.dumps({"symbol_table": {}, "version": "2.4.1"}), encoding="utf-8")
    message = f"cached analysis.json at {cache_file} predates schema v2 (no schema_version); delete it or pass eager_analysis=True"
    with pytest.raises(CodeanalyzerExecutionException, match=re.escape(message)):
        _analyzer("{}", project_dir=test_fixture, analysis_json_path=tmp_path)


def test_eager_analysis_overwrites_a_v1_cache(test_fixture, analysis_json, tmp_path):
    (tmp_path / "analysis.json").write_text(json.dumps({"symbol_table": {}, "version": "2.4.1"}), encoding="utf-8")
    analyzer, run_mock = _analyzer(analysis_json, project_dir=test_fixture, analysis_json_path=tmp_path, eager=True)
    assert run_mock.called
    assert analyzer.analysis.schema_version == "2.0.0"


def test_cache_below_the_requested_level_triggers_a_rerun(test_fixture, analysis_json, analysis_json_a4, tmp_path):
    (tmp_path / "analysis.json").write_text(analysis_json, encoding="utf-8")  # max_level 1
    analyzer, run_mock = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph, project_dir=test_fixture, analysis_json_path=tmp_path)
    assert run_mock.called
    assert analyzer.analysis.max_level == 4
    # and a deeper cache satisfies a shallower request without a run
    _, run_mock = _analyzer(analysis_json, level=AnalysisLevel.call_graph, project_dir=test_fixture, analysis_json_path=tmp_path)
    assert not run_mock.called


def test_unparsable_cache_triggers_a_rerun(test_fixture, analysis_json, tmp_path):
    (tmp_path / "analysis.json").write_text("{not-valid-json", encoding="utf-8")
    _, run_mock = _analyzer(analysis_json, project_dir=test_fixture, analysis_json_path=tmp_path)
    assert run_mock.called


def test_jar_override_is_honoured(tmp_path, monkeypatch, analysis_json):
    """``$CLDK_CODEANALYZER_JAVA_JAR`` points the backend at an uncommitted jar (the e2e's seam)."""
    jar = tmp_path / "codeanalyzer-x.jar"
    monkeypatch.setenv("CLDK_CODEANALYZER_JAVA_JAR", str(jar))
    with pytest.raises(CodeanalyzerExecutionException, match="is not a file"):
        _analyzer(analysis_json)
    jar.write_bytes(b"")
    _, run_mock = _analyzer(analysis_json)
    assert run_mock.call_args[0][0][2] == str(jar)


def test_no_cache_and_no_project_dir_is_refused_before_jdk_lookup():
    with pytest.raises(CodeanalyzerExecutionException, match="no cache directory and no project directory"):
        JCodeanalyzer(project_dir=None, analysis_json_path=None, analysis_level=AnalysisLevel.symbol_table, eager_analysis=False, target_files=None)


def test_ensure_jdk_reuses_the_nested_extracted_jdk_without_downloading(tmp_path, monkeypatch):
    """Should find the cached JDK in its extracted (nested) layout instead of re-downloading over it (#328)"""
    home = tmp_path / "jdk" / _jdk.JDK_RELEASE / _jdk.JDK_RELEASE / "Contents" / "Home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").touch()
    (home / "jmods").mkdir()
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(_jdk.JdkLoader, "download_and_extract", lambda *a, **k: pytest.fail("attempted a JDK download"))

    assert _jdk.ensure_jdk(tmp_path) == home.resolve()


# -----[ call graph (J-1) ]-----


def test_call_graph_nodes_are_fqn_dot_signature_strings(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    cg = analyzer.get_call_graph()
    assert isinstance(cg, nx.DiGraph)
    assert cg.number_of_edges() == 247
    assert cg is analyzer.get_call_graph()  # built once
    for node, attrs in cg.nodes(data=True):
        assert isinstance(node, str) and "can://" not in node
        detail: JMethodDetail = attrs["method_detail"]
        assert node == f"{detail.klass}.{detail.method.signature}"
        assert attrs["kind"] == "callable"
    get_conn = f"{TRADE_DIRECT}.getConn()"
    assert cg.in_degree(get_conn) == 24
    for _, _, data in cg.edges(data=True):
        assert data["type"] == "CALL_DEP"
        assert isinstance(data["weight"], int)
        assert isinstance(data["calling_lines"], list)


def test_call_graph_is_empty_below_level_2(analysis_json):
    analyzer, _ = _analyzer(analysis_json, level=AnalysisLevel.call_graph)
    assert analyzer.get_call_graph().number_of_edges() == 0


def test_unhomed_call_graph_endpoint_raises_naming_it(analysis_json_a4):
    payload = json.loads(analysis_json_a4)
    payload["application"]["call_graph"].append({"src": "can://java/daytrader8/nowhere/X.java/X/m()", "dst": payload["application"]["call_graph"][0]["dst"], "prov": ["declared"], "weight": 1})
    analyzer, _ = _analyzer(json.dumps(payload), level=AnalysisLevel.call_graph)
    with pytest.raises(CodeanalyzerExecutionException, match=re.escape("call-graph endpoint 'm()' in 'nowhere/X.java'")) as e:
        analyzer.get_call_graph()
    # E6: the defect is named by the signature and module key the id spells, never by the id.
    assert "can://" not in str(e.value)


def test_external_endpoints_are_dropped(analysis_json_a4):
    """3a keeps 1.x's callable-only graph: an edge to an ``@external/`` target is not a node (the
    externals arrive with ``get_external_symbols`` in 3b)."""
    payload = json.loads(analysis_json_a4)
    ext = "@external/java.io.PrintStream/println(java.lang.String)"
    payload["application"]["external_symbols"] = {ext: {"kind": "method", "signature": "println(java.lang.String)", "declaring_type": "java.io.PrintStream"}}
    payload["application"]["call_graph"].append({"src": payload["application"]["call_graph"][0]["src"], "dst": ext, "prov": ["declared"], "weight": 1})
    analyzer, _ = _analyzer(json.dumps(payload), level=AnalysisLevel.call_graph)
    cg = analyzer.get_call_graph()
    assert cg.number_of_edges() == 247
    assert not any("@external" in n for n in cg.nodes)


def test_get_system_dependency_graph_is_the_wire_call_graph(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    sdg = analyzer.get_system_dependency_graph()
    assert sdg is analyzer.application.call_graph
    assert len(sdg) == 247 and isinstance(sdg[0], JGraphEdges) and JGraphEdges is JCallGraphEdge


def test_get_call_graph_json(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    rows = json.loads(analyzer.get_call_graph_json())
    assert len(rows) == 247
    assert set(rows[0]) == {"source_method_signature", "source_method_body", "source_class", "target_method_signature", "target_method_body", "target_class", "calling_lines"}
    assert all(r["source_class"].startswith("com.ibm.") and "can://" not in r["source_class"] for r in rows)


def test_get_all_callers(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    callers = analyzer.get_all_callers(TRADE_DIRECT, "getConn()", False)
    assert len(callers["caller_details"]) == 24
    assert isinstance(callers["target_method"], JMethodDetail)
    for row in callers["caller_details"]:
        assert isinstance(row["caller_method"], JMethodDetail)
        assert isinstance(row["calling_lines"], list)
    assert analyzer.get_all_callers(TRADE_DIRECT, "noSuchMethod()", False) == {}
    # symbol-table path: same shape, resolved through call sites
    callers = analyzer.get_all_callers(TRADE_DIRECT, "getConn()", True)
    assert len(callers["caller_details"]) > 0
    assert all(isinstance(row["caller_method"], JMethodDetail) for row in callers["caller_details"])


def test_get_all_callees(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    sell = "sell(java.lang.String, java.lang.Integer, int)"
    callees = analyzer.get_all_callees(TRADE_DIRECT, sell, False)
    assert len(callees["callee_details"]) == 15
    assert isinstance(callees["source_method"], JMethodDetail)
    assert all(isinstance(row["callee_method"], JMethodDetail) for row in callees["callee_details"])
    assert analyzer.get_all_callees(TRADE_DIRECT, "getConn()", False)["callee_details"] == []
    callees = analyzer.get_all_callees(TRADE_DIRECT, sell, True)
    assert len(callees["callee_details"]) > 0


def test_get_class_call_graph(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4, level=AnalysisLevel.call_graph)
    edges = analyzer.get_class_call_graph(TRADE_DIRECT, "createHolding(java.sql.Connection, int, java.lang.String, double, java.math.BigDecimal)")
    assert len(edges) == 3
    for source, target in edges:
        assert isinstance(source, JMethodDetail) and isinstance(target, JMethodDetail)
        assert source.klass == TRADE_DIRECT
    assert len(analyzer.get_class_call_graph(TRADE_DIRECT, None)) > 3
    assert analyzer.get_class_call_graph("com.not.Found", None) == []


def test_get_class_call_graph_using_symbol_table(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4)
    edges = analyzer.get_class_call_graph_using_symbol_table(TRADE_DIRECT, None)
    assert isinstance(edges, List) and len(edges) > 0
    assert all(isinstance(s, JMethodDetail) and isinstance(t, JMethodDetail) for s, t in edges)
    assert analyzer.get_class_call_graph_using_symbol_table(TRADE_DIRECT, "noSuchMethod()") == []


# -----[ classes / methods / fields ]-----


def test_get_all_classes_is_keyed_by_qualified_name_including_nested_and_local(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    classes = analyzer.get_all_classes()
    assert len(classes) == 149
    assert all(isinstance(t, JType) for t in classes.values())
    assert TRADE_DIRECT in classes
    assert "com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean.quotePriceComparator" in classes
    assert "com.ibm.websphere.samples.daytrader.web.prims.PingManagedExecutor.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse).$anon$0" in classes


def _local_class_collision_payload() -> dict:
    """Two sibling callables of one type, each declaring an anonymous class the analyzer numbers
    ``$anon$0``. Hand-built, so the shape is catchable with no analyzer and no graph: on ThingsBoard
    it is 97 colliding names shadowing 381 of 5,102 type declarations."""
    module = "can://java/app/src/main/java/p/Outer.java"
    span = lambda a, b: {"start": [a, 1], "end": [b, 1], "bytes": [0, 0]}

    def method(signature: str) -> dict:
        node_id = f"{module}/Outer/{signature}"
        return {
            "id": node_id,
            "kind": "method",
            "signature": signature,
            "span": span(2, 4),
            "types": {"$anon$0": {"id": f"{node_id}/$anon$0", "kind": "class", "span": span(3, 3)}},
        }

    return {
        "schema_version": "2.0.0",
        "language": "java",
        "max_level": 1,
        "analyzer": {"name": "codeanalyzer-java", "version": "3.0.1"},
        "application": {
            "id": "can://java/app",
            "kind": "application",
            "symbol_table": {
                "src/main/java/p/Outer.java": {
                    "id": module,
                    "kind": "module",
                    "span": span(1, 9),
                    "package": "p",
                    "source": "",
                    "types": {
                        "Outer": {
                            "id": f"{module}/Outer",
                            "kind": "class",
                            "span": span(1, 9),
                            "callables": {"one()": method("one()"), "two()": method("two()")},
                        }
                    },
                }
            },
        },
    }


def test_a_local_class_is_keyed_by_its_declaring_callable_on_both_backends():
    """J-1 erratum: a local or anonymous class's qualified name carries the signature of the
    callable that declares it. ``$anon$N`` is numbered **per declaring callable**, so two sibling
    callables of one type each declare ``$anon$0``; without the callable segment the two spell one
    name — the second shadows the first on the in-memory backend and makes every ``_idx``-routed
    accessor on the graph backend raise."""
    payload = _local_class_collision_payload()
    expected = {"p.Outer", "p.Outer.one().$anon$0", "p.Outer.two().$anon$0"}

    analyzer, _ = _analyzer(json.dumps(payload))
    assert set(analyzer.get_all_classes()) == expected
    assert analyzer.get_class("p.Outer.one().$anon$0").id == "can://java/app/src/main/java/p/Outer.java/Outer/one()/$anon$0"
    assert analyzer.get_class("p.Outer.one().$anon$0").is_local_class

    neo = JNeo4jBackend.__new__(JNeo4jBackend)
    neo.application_name = "app"
    neo.__dict__["_application"] = JAnalysis.model_validate(payload).application
    assert set(neo.get_all_classes()) == expected


def test_get_class(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    assert isinstance(analyzer.get_class(TRADE_DIRECT), JType)
    assert analyzer.get_class("com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean.quotePriceComparator").is_nested_type
    assert analyzer.get_class("com.not.Found") is None


def test_get_method_and_parameters(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    sig = "publishQuotePriceChange(com.ibm.websphere.samples.daytrader.entities.QuoteDataBean, java.math.BigDecimal, java.math.BigDecimal, double)"
    method = analyzer.get_method(TRADE_DIRECT, sig)
    assert isinstance(method, JCallable) and method.signature == sig
    assert [p.type for p in analyzer.get_method_parameters(TRADE_DIRECT, sig)] == ["com.ibm.websphere.samples.daytrader.entities.QuoteDataBean", "java.math.BigDecimal", "java.math.BigDecimal", "double"]
    assert analyzer.get_method(TRADE_DIRECT, "noSuchMethod()") is None
    assert analyzer.get_method_parameters(TRADE_DIRECT, "noSuchMethod()") == []


def test_get_java_file_and_compilation_unit(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    java_file = analyzer.get_java_file(TRADE_DIRECT)
    assert java_file == "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java"
    unit = analyzer.get_java_compilation_unit(java_file)
    assert isinstance(unit, JCompilationUnit) and unit.file_path == java_file
    assert analyzer.get_java_file("com.not.Found") is None
    assert len(list(analyzer.get_compilation_units())) == 138


def test_get_all_methods_in_class_excludes_constructors(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    methods = analyzer.get_all_methods_in_class(TRADE_DIRECT)
    assert len(methods) > 0
    assert all(isinstance(m, JCallable) and not m.is_constructor for m in methods.values())
    assert analyzer.get_all_methods_in_class("com.not.Found") == {}


def test_get_all_constructors_includes_the_implicit_one(analysis_json):
    """J-6: the analyzer emits a class's implicit default constructor as a callable; the SDK does
    not hide it (1.x, on a 2.x analyzer, saw no constructor at all for such a class)."""
    analyzer, _ = _analyzer(analysis_json)
    ctors = analyzer.get_all_constructors("com.ibm.websphere.samples.daytrader.entities.AccountDataBean")
    assert len(ctors) == 3 and all(c.is_constructor and not c.is_implicit for c in ctors.values())
    ctors = analyzer.get_all_constructors("com.ibm.websphere.samples.daytrader.util.FinancialUtils")
    assert list(ctors) == ["<init>()"] and ctors["<init>()"].is_implicit


def test_get_all_sub_classes(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    subs = analyzer.get_all_sub_classes("javax.ws.rs.core.Application")
    assert list(subs) == ["com.ibm.websphere.samples.daytrader.jaxrs.JAXRSApplication"]


def test_get_all_fields(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    assert len(analyzer.get_all_fields("com.ibm.websphere.samples.daytrader.entities.AccountDataBean")) == 12
    assert analyzer.get_all_fields("com.not.Found") == []


def test_get_all_nested_classes(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    nested = analyzer.get_all_nested_classes("com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean")
    assert [t.name for t in nested] == ["quotePriceComparator"] and all(isinstance(t, JType) for t in nested)
    assert analyzer.get_all_nested_classes("com.not.Found") == []


def test_get_extended_classes_and_implemented_interfaces(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    assert analyzer.get_extended_classes("com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral") == ["javax.enterprise.util.AnnotationLiteral<com.ibm.websphere.samples.daytrader.interfaces.RuntimeMode>"]
    assert analyzer.get_extended_classes("com.ibm.websphere.samples.daytrader.entities.HoldingDataBean") == []
    assert set(analyzer.get_implemented_interfaces(TRADE_DIRECT)) == {"com.ibm.websphere.samples.daytrader.interfaces.TradeServices", "java.io.Serializable"}
    assert analyzer.get_implemented_interfaces("com.ibm.websphere.samples.daytrader.util.TradeConfig") == []
    assert analyzer.get_extended_classes("com.not.Found") == [] and analyzer.get_implemented_interfaces("com.not.Found") == []


def test_get_all_methods_in_application(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    methods = analyzer.get_all_methods_in_application()
    assert set(methods) == set(analyzer.get_all_classes())
    assert all(isinstance(c, JCallable) for per_class in methods.values() for c in per_class.values())


def test_entry_points(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    methods = analyzer.get_all_entry_point_methods()
    assert len(methods) > 0
    assert all(c.is_entrypoint for per_class in methods.values() for c in per_class.values())
    classes = analyzer.get_all_entry_point_classes()
    assert len(classes) > 0 and all(t.is_entrypoint_class for t in classes.values())


# -----[ CRUD (J-4) ]-----


@pytest.mark.parametrize("backend", [JCodeanalyzer, JNeo4jBackend])
@pytest.mark.parametrize("name", ["get_all_crud_operations", "get_all_create_operations", "get_all_read_operations", "get_all_update_operations", "get_all_delete_operations"])
def test_crud_accessors_raise_naming_the_upstream_issue(backend, name):
    """Called unbound: the raise precedes every data access, and JNeo4jBackend cannot be
    instantiated until Task 3 implements the artifact-layer five."""
    assert "codeanalyzer-java#187" in CRUD_UNAVAILABLE
    with pytest.raises(CodeanalyzerExecutionException, match=re.escape(CRUD_UNAVAILABLE)):
        getattr(backend, name)(None)


# -----[ repository artifacts: the shared Py* models ]-----


def test_artifact_layer_returns_the_shared_models(analysis_json_a4):
    analyzer, _ = _analyzer(analysis_json_a4)
    artifacts = analyzer.get_artifacts()
    assert len(artifacts) == 235 and all(isinstance(a, PyArtifact) for a in artifacts.values())
    assert artifacts["pom.xml"].id == "can://artifact/daytrader8/pom.xml" and len(artifacts["pom.xml"].config_keys) == 54
    keys = analyzer.get_config_keys()
    key = keys["can://artifact/daytrader8/pom.xml@key/project.artifactId"]
    assert isinstance(key, PyConfigKey) and key.value == "io.openliberty.sample.daytrader8" and key.namespace == "xml"
    deps = analyzer.get_dependencies()
    assert [d.name for d in deps] == ["derby", "javaee-api", "jaxb-api", "standard"]
    assert all(isinstance(d, PyDependency) and d.ecosystem == "maven" and d.declared_in == "can://artifact/daytrader8/pom.xml" for d in deps)
    assert len(analyzer.get_dependencies(direct_only=True)) == 4
    assert analyzer.get_dependencies(ecosystem="npm") == []
    assert [d.name for d in analyzer.get_dependencies(declared_in="can://artifact/daytrader8/pom.xml")] == ["derby", "javaee-api", "jaxb-api", "standard"]
    assert analyzer.get_dependencies(declared_in="can://artifact/daytrader8/nothing") == []
    assert analyzer.get_config_uses() == [] and analyzer.get_config_uses(key="project.artifactId") == []
    assert analyzer.get_unresolved_config_reads() == []


# -----[ comments ]-----


def test_comments(analysis_json):
    analyzer, _ = _analyzer(analysis_json)
    unit_path = "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java"
    in_file = analyzer.get_comment_in_file(unit_path)
    assert len(in_file) > 0 and all(isinstance(c, JComment) for c in in_file)
    assert analyzer.get_all_comments()[unit_path] == in_file
    docstrings = analyzer.get_all_docstrings()
    assert all(c.is_javadoc for cs in docstrings.values() for c in cs)
    assert isinstance(analyzer.get_comments_in_a_class(TRADE_DIRECT), list)
    assert analyzer.get_comments_in_a_class("com.not.Found") == []
    assert analyzer.get_comments_in_a_method(TRADE_DIRECT, "noSuchMethod()") == []
    with pytest.raises(CodeanalyzerExecutionException, match="not found in the symbol table"):
        analyzer.get_comment_in_file("no/such/File.java")


def test_facade_reuses_language_keyed_cache(analysis_json_fixture, tmp_path):
    """The facade reuses a cached analysis.json from the language-keyed cache dir (<cache>/java)."""
    import gzip
    import shutil

    from cldk import CLDK
    from cldk.analysis.commons.backend_config import CodeAnalyzerConfig

    keyed = tmp_path / "java"
    keyed.mkdir()
    with gzip.open(Path(analysis_json_fixture) / "analysis.json.gz", "rb") as src, open(keyed / "analysis.json", "wb") as dst:
        shutil.copyfileobj(src, dst)
    with patch(_RUN) as run_mock:
        analysis = CLDK.java(
            project_path=analysis_json_fixture,
            eager=False,
            analysis_level=AnalysisLevel.symbol_table,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )
    assert not run_mock.called
    assert len(analysis.get_symbol_table()) == 138
