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
Java Tests
"""

import os
import json
from typing import Dict, List, Set, Tuple
from unittest.mock import patch, MagicMock

from tree_sitter import Tree
import pytest
import networkx as nx

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.java import JavaAnalysis
from cldk.models.java.models import JCallable, JCallableParameter, JComment, JCompilationUnit, JField, JMethodDetail, JApplication, JType
from pathlib import Path
import tempfile as _tempfile
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig

_CACHE_DIR = _tempfile.mkdtemp()
_BK = CodeAnalyzerConfig(cache_dir=_CACHE_DIR)
_BK4 = CodeAnalyzerConfig(cache_dir=_tempfile.mkdtemp())  # the -a 4 fixture (call graph)
TRADE_DIRECT = "com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect"


def _write_java_output(payload):
    """subprocess.run side effect: write analysis.json into the -o dir (caching on by default)."""

    def _run(cmd, *a, **kw):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


def test_get_symbol_table_is_not_null(test_fixture, analysis_json):
    """Should return a symbol table that is not null"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)

        # Initialize the CLDK object with the project directory, language, and analysis_backend
        cldk = CLDK(language="java")
        analysis = cldk.analysis(
            project_path=test_fixture,
            cache_dir=_CACHE_DIR,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
        )
        assert analysis.get_symbol_table() is not None

def test_get_imports(test_fixture, analysis_json):
    """Should return NotImplemented for get_imports()"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_imports()
        assert except_info.type == NotImplementedError


def test_get_variables(test_fixture, analysis_json):
    """Should return NotImplemented for get_variables()"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_variables()
        assert except_info.type == NotImplementedError


def test_get_service_entry_point_classes(test_fixture, analysis_json):
    """Should return NotImplemented for get_service_entry_point_classes()"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_service_entry_point_classes()
        assert except_info.type == NotImplementedError


def test_get_service_entry_point_methods(test_fixture, analysis_json):
    """Should return NotImplemented for get_service_entry_point_methods()"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_service_entry_point_methods()
        assert except_info.type == NotImplementedError


def test_get_application_view(test_fixture, analysis_json):
    """Should return the application view"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        app = java_analysis.get_application_view()
        assert app is not None
        assert isinstance(app, JApplication)
        assert isinstance(app.symbol_table, Dict)
        for _, compilation_unit in app.symbol_table.items():
            assert isinstance(compilation_unit, JCompilationUnit)



def test_get_symbol_table(test_fixture, analysis_json):
    """Should return the symbol table"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        symbol_table = java_analysis.get_symbol_table()
        assert symbol_table is not None
        assert isinstance(symbol_table, Dict)
        for _, compilation_unit in symbol_table.items():
            assert isinstance(compilation_unit, JCompilationUnit)


def test_get_compilation_units(test_fixture, analysis_json):
    """Should return the compilation units"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        assert java_analysis.get_compilation_units() != None


def test_get_class_hierarchy(test_fixture, analysis_json):
    """Should return the class hierarchy"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # When this is implemented please add a real test case
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_class_hierarchy()
        assert except_info.type == NotImplementedError


def test_is_parsable(test_fixture, analysis_json):
    """Should be parsable"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Get a test source file and send its contents
        filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
        with open(filename, "r", encoding="utf-8") as file:
            code = file.read()
            yes = java_analysis.is_parsable(code)
            assert yes is True


def test_get_raw_ast(test_fixture, analysis_json):
    """Should return the raw AST"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Get a test source file and send its contents
        filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
        with open(filename, "r", encoding="utf-8") as file:
            code = file.read()

        raw_ast = java_analysis.get_raw_ast(code)
        assert raw_ast is not None
        assert isinstance(raw_ast, Tree)
        assert raw_ast.root_node is not None


def test_get_call_graph(test_fixture, analysis_json_a4):
    """Should return the Call Graph"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json_a4)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK4,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        call_graph = java_analysis.get_call_graph()
        assert call_graph is not None
        assert isinstance(call_graph, nx.DiGraph)
        # check that the call graph is not empty, and keyed by "<type fqn>.<signature>" strings (J-1)
        assert len(call_graph.nodes) > 0
        assert len(call_graph.edges) > 0
        assert all(isinstance(node, str) and node.startswith("com.ibm.") for node in call_graph.nodes)


def test_get_call_graph_json(test_fixture, analysis_json_a4):
    """Should return the Call Graph as JSON"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json_a4)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK4,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        call_graph_json = java_analysis.get_call_graph_json()
        assert call_graph_json is not None
        assert isinstance(call_graph_json, str)
        assert len(call_graph_json) > 0
        # test if we can load it back into a list of dictionaries without errors
        call_graph = json.loads(call_graph_json)
        assert isinstance(call_graph, list)
        assert isinstance(call_graph[0], dict)


def test_get_callers(test_fixture, analysis_json_a4):
    """Should return the callers"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json_a4)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK4,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Test using call graph
        callers = java_analysis.get_callers(TRADE_DIRECT, "getConn()", False)
        assert callers is not None
        assert isinstance(callers, Dict)
        assert "caller_details" in callers
        assert len(callers["caller_details"]) == 24
        for method in callers["caller_details"]:
            assert isinstance(method["caller_method"], JMethodDetail)

        # Test using symbol table
        callers = java_analysis.get_callers(TRADE_DIRECT, "getConn()", True)
        assert callers is not None
        assert isinstance(callers, Dict)
        assert "caller_details" in callers
        assert len(callers["caller_details"]) > 0
        for method in callers["caller_details"]:
            assert isinstance(method["caller_method"], JMethodDetail)


def test_get_callees(test_fixture, analysis_json_a4):
    """Should return the callees"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json_a4)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK4,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Test with a method that has no callees
        callees = java_analysis.get_callees(TRADE_DIRECT, "getConn()", False)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) == 0

        # Test with a method that has callees
        sell = "sell(java.lang.String, java.lang.Integer, int)"
        callees = java_analysis.get_callees(TRADE_DIRECT, sell, False)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) == 15
        for method in callees["callee_details"]:
            assert isinstance(method["callee_method"], JMethodDetail)

        # Test using symbol table
        callees = java_analysis.get_callees(TRADE_DIRECT, sell, True)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) > 0


def test_get_methods(test_fixture, analysis_json):
    """Should return the methods"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        methods = java_analysis.get_methods()
        assert methods is not None
        assert isinstance(methods, Dict)
        assert len(methods) > 0
        for _, method in methods.items():
            assert isinstance(method, Dict)


def test_get_classes(test_fixture, analysis_json):
    """Should return the classes"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        classes = java_analysis.get_classes()
        assert classes is not None
        assert isinstance(classes, Dict)
        assert len(classes) > 0
        for _, a_class in classes.items():
            assert isinstance(a_class, JType)


def test_get_classes_by_criteria(test_fixture, analysis_json):
    """Should return the classes by criteria"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Note: There are 145 classes in the test data

        # Test no criteria returns nothing
        classes = java_analysis.get_classes_by_criteria()
        assert classes is not None
        assert isinstance(classes, Dict)
        assert len(classes) == 0

        # Test included 2 class returns 2
        included = ["com.ibm.websphere.samples.daytrader.util.Log", "com.ibm.websphere.samples.daytrader.web.websocket.ActionMessage"]
        classes = java_analysis.get_classes_by_criteria(inclusions=included)
        assert classes is not None
        assert isinstance(classes, Dict)
        assert len(classes) == 2

        # Test excluded one of the two returns 1
        excluded = ["com.ibm.websphere.samples.daytrader.util.Log"]
        classes = java_analysis.get_classes_by_criteria(inclusions=included, exclusions=excluded)
        assert classes is not None
        assert isinstance(classes, Dict)
        assert len(classes) == 1


def test_get_class(test_fixture, analysis_json):
    """Should return a single class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        the_class = java_analysis.get_class("com.ibm.websphere.samples.daytrader.util.Log")
        assert the_class is not None
        assert isinstance(the_class, JType)


def test_get_method(test_fixture, analysis_json):
    """Should return a single method"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        the_method = java_analysis.get_method("com.ibm.websphere.samples.daytrader.util.Log", "trace(java.lang.String)")
        assert the_method is not None
        assert isinstance(the_method, JCallable)
        assert the_method.declaration == "public static void trace(String message)"


def test_get_method_parameters(test_fixture, analysis_json):
    """Should return a method parameters"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        the_method_parameters = java_analysis.get_method_parameters("com.ibm.websphere.samples.daytrader.util.Log", "trace(java.lang.String)")
        assert the_method_parameters is not None
        assert isinstance(the_method_parameters, List)
        assert len(the_method_parameters) == 1
        the_method_parameter: JCallableParameter = the_method_parameters[0]
        the_method_parameter.start_line >= 0
        the_method_parameter.end_line >= 0
        the_method_parameter.start_column >= 0
        the_method_parameter.end_column >= 0


def test_get_java_file(test_fixture, analysis_json):
    """Should return the java file and compilation unit"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test returning the filename
        java_file = java_analysis.get_java_file("com.ibm.websphere.samples.daytrader.util.Log")
        assert java_file is not None
        assert isinstance(java_file, str)
        assert java_file == "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java"  # the symbol-table key: repo-relative

        # Test compilation unit for this file
        comp_unit = java_analysis.get_java_compilation_unit(java_file)
        assert comp_unit is not None
        assert isinstance(comp_unit, JCompilationUnit)


def test_get_methods_in_class(test_fixture, analysis_json):
    """Should return the methods in a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 29 methods in the Log class
        methods = java_analysis.get_methods_in_class("com.ibm.websphere.samples.daytrader.util.Log")
        assert methods is not None
        assert isinstance(methods, Dict)
        assert len(methods) == 29
        for method in methods:
            assert isinstance(methods[method], JCallable)


def test_get_fields(test_fixture, analysis_json):
    """Should return the fields for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 8 fields in the MarketSummaryDataBean class
        fields = java_analysis.get_fields("com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean")
        assert fields is not None
        assert isinstance(fields, List)
        assert len(fields) == 8
        for field in fields:
            assert isinstance(field, JField)
        by_name = {field.name: field for field in fields}
        assert by_name["serialVersionUID"].variable_initializers == {"serialVersionUID": "650652242288745600L"}
        assert by_name["TSIA"].variable_initializers is None


def test_get_nested_classes(test_fixture, analysis_json):
    """Should return the nested classes for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 0 nested classes in the MarketSummaryDataBean class
        nested = java_analysis.get_nested_classes("com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean")
        assert nested is not None
        assert isinstance(nested, List)
        assert len(nested) == 0
        # TODO: Test if we can get nested classes for known classes


def test_get_sub_classes(test_fixture, analysis_json):
    """Should return the subclasses for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there is 0 subclasses of MarketSummaryDataBean
        subclasses = java_analysis.get_sub_classes("com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean")
        assert subclasses is not None
        assert isinstance(subclasses, Dict)
        assert len(subclasses) == 0

        # Test that there is 15 subclasses of Serializable
        subclasses = java_analysis.get_sub_classes("java.io.Serializable")
        assert subclasses is not None
        assert isinstance(subclasses, Dict)
        assert len(subclasses) == 15
        for _, subclass in subclasses.items():
            assert isinstance(subclass, JType)


def test_get_extended_classes(test_fixture, analysis_json):
    """Should return the extended classes for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 0 extensions of the MarketSummaryDataBean class
        extended = java_analysis.get_extended_classes("com.ibm.websphere.samples.daytrader.beans.MarketSummaryDataBean")
        assert extended is not None
        assert isinstance(extended, List)
        assert len(extended) == 0

        # Test that there are 0 extensions of the PingServlet2TwoPhase class
        extended = java_analysis.get_extended_classes("com.ibm.websphere.samples.daytrader.web.prims.ejb3.PingServlet2TwoPhase")
        assert extended is not None
        assert isinstance(extended, List)
        assert len(extended) == 1
        for extend in extended:
            assert isinstance(extend, str)


def test_get_implemented_interfaces(test_fixture, analysis_json):
    """Should return the implemented interfaces classes for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 0 implemented interface for the PingBean class
        extended = java_analysis.get_implemented_interfaces("com.ibm.websphere.samples.daytrader.web.prims.PingBean")
        assert extended is not None
        assert isinstance(extended, List)
        assert len(extended) == 0

        # Test that there is 1 implemented interface for the ActionDecoder class
        extended = java_analysis.get_implemented_interfaces("com.ibm.websphere.samples.daytrader.web.websocket.ActionDecoder")
        assert extended is not None
        assert isinstance(extended, List)
        assert len(extended) == 1
        for extend in extended:
            assert isinstance(extend, str)


def test_get_class_call_graph(test_fixture, analysis_json_a4):
    """Should return the class call graph"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json_a4)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK4,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Call using call graph
        create_holding = "createHolding(java.sql.Connection, int, java.lang.String, double, java.math.BigDecimal)"
        call_graph = java_analysis.get_class_call_graph(TRADE_DIRECT, create_holding, False)
        assert call_graph is not None
        assert isinstance(call_graph, List)
        assert len(call_graph) == 3
        for graph in call_graph:
            assert isinstance(graph, Tuple)

        # Call using symbol table
        call_graph = java_analysis.get_class_call_graph(TRADE_DIRECT, create_holding, True)
        assert call_graph is not None
        assert isinstance(call_graph, List)
        assert len(call_graph) > 0
        for graph in call_graph:
            assert isinstance(graph, Tuple)


def test_get_entry_point_classes(test_fixture, analysis_json):
    """Should return the entry point classes"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        entry_point_classes = java_analysis.get_entry_point_classes()
        assert entry_point_classes is not None
        assert isinstance(entry_point_classes, Dict)
        assert len(entry_point_classes) >= 0
        for _, entry_point in entry_point_classes.items():
            assert isinstance(entry_point, JType)


def test_get_entry_point_methods(test_fixture, analysis_json):
    """Should return the entry point methods"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        entry_point_methods = java_analysis.get_entry_point_methods()
        assert entry_point_methods is not None
        assert isinstance(entry_point_methods, Dict)
        assert len(entry_point_methods) >= 64
        for _, entry_point in entry_point_methods.items():
            assert isinstance(entry_point, Dict)
            for _, method in entry_point.items():
                assert isinstance(method, JCallable)


def test_remove_all_comments(test_fixture, analysis_json):
    """remove all comments"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # J-10: the one 1.x accessor that only ever worked in single-file mode says so
        with pytest.raises(NotImplementedError, match="single-file source mode was removed in 2.0"):
            java_analysis.remove_all_comments()


def test_get_methods_with_annotations(test_fixture, analysis_json):
    """Should return methods with annotations"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # TODO: The code is broken. It requires Treesitter but JCodeanalyzer does not!

        annotations = ["WebServlet"]
        try:
            code_with_annotations = java_analysis.get_methods_with_annotations(annotations)
        except NotImplementedError:
            assert True
            return

        assert False, "Did not raise NotImplementedError"


def test_get_test_methods(test_fixture, analysis_json):
    """Should return test methods, read off the analyzer's own annotations."""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        test_methods = java_analysis.get_test_methods()
        assert test_methods == {}  # daytrader8 ships no @Test methods; the walk itself is what is exercised


def test_get_test_methods_reads_the_annotations_not_the_module_source(test_fixture, analysis_json, monkeypatch):
    """The mechanism, on a corpus with no ``@Test`` in it: swap the marker set for one daytrader8
    *does* carry and the same walk answers.

    Why it matters: the 1.x version re-parsed each ``JCompilationUnit.source`` with Tree-sitter,
    and a Neo4j-backed analysis has no module source at all, so it returned ``{}`` there whatever
    the corpus held. daytrader8 cannot witness that — it has zero ``@Test`` methods — which is why
    the real marker set is exercised on ThingsBoard in ``test_java_neo4j_scale.py``.
    """
    monkeypatch.setattr("cldk.analysis.java.java_analysis._TEST_ANNOTATIONS", frozenset({"Override"}))
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        marked = java_analysis.get_test_methods()
        # One entry per annotated callable — 328 ``@Override`` callables in the a1 fixture — keyed by
        # the J-1 call-graph node key, which is unique application-wide where a bare method name is not.
        assert len(marked) == 328
        methods = java_analysis.get_methods()
        for key, code in marked.items():
            assert "can://" not in key
            klass = key[: key.rindex("(")].rsplit(".", 1)[0]  # the documented split (CHANGELOG, J-1)
            assert code == methods[klass][key[len(klass) + 1 :]].code


def test_get_calling_lines(test_fixture, analysis_json):
    """Should return calling lines"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # TODO: The code is broken. It requires Treesitter but JCodeanalyzer does not!

        try:
            calling_lines = java_analysis.get_calling_lines("trace(String)")
            assert calling_lines is not None
            assert isinstance(calling_lines, List)
            assert len(calling_lines) > 0
        except NotImplementedError:
            assert True
            return

        assert False, "Did not raise NotImplementedError"


def test_get_call_targets(test_fixture, analysis_json):
    """Should return calling targets"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # TODO: The code is broken. It requires Treesitter but JCodeanalyzer does not!
        try:
            call_targets = java_analysis.get_call_targets("trace(String)")
            assert call_targets is not None
            assert isinstance(call_targets, Set)
            assert len(call_targets) > 0
        except NotImplementedError:
            assert True
            return

        assert False, "Did not raise NotImplementedError"


def test_get_all_comments(test_fixture, analysis_json):
    """Should return all comments"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        all_comments = java_analysis.get_all_comments()
        assert all_comments is not None
        assert isinstance(all_comments, Dict)
        assert len(all_comments) > 0
        for file_name, list_of_comments in all_comments.items():
            print(f"File name: {file_name}")
            assert isinstance(list_of_comments, List)
            assert len(list_of_comments) > 0
            for comment in list_of_comments:
                assert isinstance(comment, JComment)
                if comment.content:
                    print(f"Comment: {comment.content}")


def test_get_all_docstrings(test_fixture, analysis_json):
    """Should return all docstrings"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        all_docstrings = java_analysis.get_all_docstrings()
        assert all_docstrings is not None
        assert isinstance(all_docstrings, dict)
        assert len(all_docstrings) > 0
        for file_name, docstring in all_docstrings.items():
            print(f"File name: {file_name}")
            assert isinstance(docstring, List)
            for doc in docstring:
                assert isinstance(doc, JComment)
                if doc.content:
                    print(f"Docstring: {doc.content}")


# --------------------------------------------------------------------------------------------
# Miss-path tests (#248): lookups must return None/[] honestly on a miss, never crash.
# --------------------------------------------------------------------------------------------


def test_get_class_miss_returns_none(test_fixture, analysis_json):
    """A qualified class name that doesn't exist should return None, not fall off the end."""

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        assert java_analysis.get_class("com.example.NoSuchClass") is None


def test_get_method_miss_returns_none(test_fixture, analysis_json):
    """A method signature that doesn't exist should return None, not fall off the end."""

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Known class, typo'd signature.
        assert java_analysis.get_method("com.ibm.websphere.samples.daytrader.util.Log", "noSuchMethod()") is None
        # Unknown class altogether.
        assert java_analysis.get_method("com.example.NoSuchClass", "trace(java.lang.String)") is None


def test_get_java_file_miss_returns_none(test_fixture, analysis_json):
    """A qualified class name that doesn't exist should return None, not fall off the end."""

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        assert java_analysis.get_java_file("com.example.NoSuchClass") is None


def test_get_method_parameters_miss_returns_empty_list(test_fixture, analysis_json):
    """get_method_parameters must not crash with AttributeError when the method is missing.

    Before the fix, this raised: AttributeError: 'NoneType' object has no attribute 'parameters'.
    """

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        assert java_analysis.get_method_parameters("com.ibm.websphere.samples.daytrader.util.Log", "noSuchMethod()") == []
        assert java_analysis.get_method_parameters("com.example.NoSuchClass", "trace(java.lang.String)") == []


def test_get_comments_in_a_method_miss_returns_empty_list(test_fixture, analysis_json):
    """get_comments_in_a_method must not crash with AttributeError when the method is missing."""

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        assert java_analysis.backend.get_comments_in_a_method("com.ibm.websphere.samples.daytrader.util.Log", "noSuchMethod()") == []


def test_call_graph_target_method_miss_mid_construction_no_crash(test_fixture, analysis_json):
    """A get_method miss for the *target* method of a symbol-table call graph must not crash.

    Exercises JCodeanalyzer.__raw_call_graph_using_symbol_table_target_method (codeanalyzer.py:738),
    reached through the public get_all_callers(using_symbol_table=True) path.
    """

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        result = java_analysis.backend.get_all_callers(
            target_class_name="com.ibm.websphere.samples.daytrader.util.Log",
            target_method_signature="noSuchMethod()",
            using_symbol_table=True,
        )
        assert result == {}


def test_call_graph_source_method_miss_mid_construction_no_crash(test_fixture, analysis_json):
    """A get_method miss for a *candidate source* method mid-construction must be skipped, not crash.

    Exercises codeanalyzer.py:741 (and the crash it guards at the old :742 `.call_sites` dereference)
    by making a single (class, signature) pair momentarily miss while the target method is real,
    simulating a symbol table that disagrees with itself mid-construction.
    """

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )
        backend = java_analysis.backend
        original_get_method = backend.get_method
        flaky_class, flaky_signature = "com.ibm.websphere.samples.daytrader.util.Log", "log(java.lang.String)"

        def flaky_get_method(qualified_class_name, method_signature):
            if qualified_class_name == flaky_class and method_signature == flaky_signature:
                return None
            return original_get_method(qualified_class_name, method_signature)

        backend.get_method = flaky_get_method
        try:
            # Real target method; a real class/method pair in the enumeration loop is simulated missing.
            result = backend.get_all_callers(
                target_class_name="com.ibm.websphere.samples.daytrader.util.Log",
                target_method_signature="trace(java.lang.String)",
                using_symbol_table=True,
            )
        finally:
            backend.get_method = original_get_method

        assert isinstance(result, dict)


def test_get_comments_in_a_class_miss_returns_empty_list(test_fixture, analysis_json):
    """get_comments_in_a_class must not crash with AttributeError when the class is missing.

    Before the fix, this raised: AttributeError: 'NoneType' object has no attribute 'comments'.
    """

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.side_effect = _write_java_output(analysis_json)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            backend=_BK,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        assert java_analysis.backend.get_comments_in_a_class("com.example.NoSuchClass") == []
