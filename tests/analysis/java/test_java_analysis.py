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


def test_get_symbol_table_is_not_null(test_fixture, analysis_json):
    """Should return a symbol table that is not null"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)

        # Initialize the CLDK object with the project directory, language, and analysis_backend
        cldk = CLDK(language="java")
        analysis = cldk.analysis(
            project_path=test_fixture,
            analysis_backend_path=None,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
        )
        assert analysis.get_symbol_table() is not None

def test_get_symbol_table_source_code(java_code):
    """Should return a symbol table for source analysis with expected class/method count"""

    # Initialize the CLDK object with the project directory, language, and analysis_backend
    cldk = CLDK(language="java")
    analysis = cldk.analysis(
        source_code=java_code,
        analysis_backend_path=None,
        eager=True,
        analysis_level=AnalysisLevel.symbol_table,
    )

    # assert on expected class name and method count in the symbol table
    expected_class_name = "com.acme.modres.WeatherServlet"
    assert analysis.get_symbol_table() is not None
    assert len(analysis.get_symbol_table().keys()) == 1
    assert expected_class_name in analysis.get_methods().keys()
    assert len(analysis.get_methods().get(expected_class_name).keys()) == 9

def test_get_imports(test_fixture, analysis_json):
    """Should return NotImplemented for get_imports()"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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

        # Test that with source code is not implemented yet
        java_analysis.source_code = "TradeAction.java"
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_application_view()
        assert except_info.type == NotImplementedError


def test_get_symbol_table(test_fixture, analysis_json):
    """Should return the symbol table"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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


def test_get_call_graph(test_fixture, analysis_json):
    """Should return the Call Graph"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        call_graph = java_analysis.get_call_graph()
        assert call_graph is not None
        assert isinstance(call_graph, nx.DiGraph)
        # check that the call graph is not empty
        assert len(call_graph.nodes) > 0
        assert len(call_graph.edges) > 0


def test_get_call_graph_json(test_fixture, analysis_json):
    """Should return the Call Graph as JSON"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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


def test_get_callers(test_fixture, analysis_json):
    """Should return the callers"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Test using call graph
        callers = java_analysis.get_callers("com.ibm.websphere.samples.daytrader.util.Log", "log(String)", False)
        assert callers is not None
        assert isinstance(callers, Dict)
        assert "caller_details" in callers
        assert len(callers["caller_details"]) == 18
        for method in callers["caller_details"]:
            assert isinstance(method["caller_method"], JMethodDetail)

        # TODO: This code doesn't work because
        # it is looking for `is_target_method_a_constructor`
        # Uncomment this next test section when fixed

        # Test using symbol table
        callers = java_analysis.get_callers("com.ibm.websphere.samples.daytrader.util.Log", "log(String)", True)
        assert callers is not None
        assert isinstance(callers, Dict)
        assert "caller_details" in callers
        assert len(callers["caller_details"]) == 18
        for method in callers["caller_details"]:
            assert isinstance(method["caller_method"], JMethodDetail)

        # Test using code parameter
        java_analysis.source_code = "dummy code"
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_callers("com.ibm.websphere.samples.daytrader.util.Log", "log(String)", False)
        assert except_info.type == NotImplementedError


def test_get_callees(test_fixture, analysis_json):
    """Should return the callees"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Test with a class that has no callees
        callees = java_analysis.get_callees("com.ibm.websphere.samples.daytrader.util.Log", "log(String)", False)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) == 0

        # Test with a class that has callees
        callees = java_analysis.get_callees("com.ibm.websphere.samples.daytrader.web.websocket.ActionMessage", "doDecoding(String)", False)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) == 2
        for method in callees["callee_details"]:
            assert isinstance(method["callee_method"], JMethodDetail)

        # TODO: This code doesn't work because
        # it is looking for `is_target_method_a_constructor`
        # Uncomment this next test section when fixed

        # # Test using symbol table
        callees = java_analysis.get_callees("com.ibm.websphere.samples.daytrader.web.websocket.ActionMessage", "doDecoding(String)", True)
        assert callees is not None
        assert isinstance(callees, Dict)
        assert "callee_details" in callees
        assert len(callees["callee_details"]) == 2

        # Test using code parameter
        java_analysis.source_code = "dummy code"
        with pytest.raises(NotImplementedError) as except_info:
            java_analysis.get_callees("com.ibm.websphere.samples.daytrader.util.Log", "log(String)", False)
        assert except_info.type == NotImplementedError


def test_get_methods(test_fixture, analysis_json):
    """Should return the methods"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        the_method = java_analysis.get_method("com.ibm.websphere.samples.daytrader.util.Log", "trace(String)")
        assert the_method is not None
        assert isinstance(the_method, JCallable)
        assert the_method.declaration == "public static void trace(String message)"


def test_get_method_parameters(test_fixture, analysis_json):
    """Should return a method parameters"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        the_method_parameters = java_analysis.get_method_parameters("com.ibm.websphere.samples.daytrader.util.Log", "trace(String)")
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test returning the filename
        java_file = java_analysis.get_java_file("com.ibm.websphere.samples.daytrader.util.Log")
        assert java_file is not None
        assert isinstance(java_file, str)
        relative_file = java_file.split("/src/")[1]
        assert relative_file == "main/java/com/ibm/websphere/samples/daytrader/util/Log.java"

        # Test compilation unit for this file
        comp_unit = java_analysis.get_java_compilation_unit(java_file)
        assert comp_unit is not None
        assert isinstance(comp_unit, JCompilationUnit)


def test_get_methods_in_class(test_fixture, analysis_json):
    """Should return the methods in a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
        )

        # Test that there are 30 methods in the Log class
        methods = java_analysis.get_methods_in_class("com.ibm.websphere.samples.daytrader.util.Log")
        assert methods is not None
        assert isinstance(methods, Dict)
        assert len(methods) == 30
        for method in methods:
            assert isinstance(methods[method], JCallable)


def test_get_fields(test_fixture, analysis_json):
    """Should return the fields for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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


def test_get_nested_classes(test_fixture, analysis_json):
    """Should return the nested classes for a class"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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


def test_get_class_call_graph(test_fixture, analysis_json):
    """Should return the class call graph"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # Call using call graph
        call_graph = java_analysis.get_class_call_graph("com.ibm.websphere.samples.daytrader.impl.direct.TradeDirectDBUtils", "buildDB(java.io.PrintWriter, InputStream)", False)
        assert call_graph is not None
        assert isinstance(call_graph, List)
        assert len(call_graph) >= 0
        for graph in call_graph:
            assert isinstance(graph, Tuple)

        # Call using symbol table
        call_graph = java_analysis.get_class_call_graph("com.ibm.websphere.samples.daytrader.impl.direct.TradeDirectDBUtils", "buildDB(java.io.PrintWriter, InputStream)", True)
        assert call_graph is not None
        assert isinstance(call_graph, List)
        assert len(call_graph) >= 0
        for graph in call_graph:
            assert isinstance(graph, Tuple)


def test_get_entry_point_classes(test_fixture, analysis_json):
    """Should return the entry point classes"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        # TODO: The code is broken. It requires Treesitter but JCodeanalyzer does not!

        try:
            java_analysis.remove_all_comments()
        except NotImplementedError:
            assert True
            return

        assert False, "Did not raise NotImplementedError"


def test_get_methods_with_annotations(test_fixture, analysis_json):
    """Should return methods with annotations"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
    """Should return test methods"""
    java_code_with_test_annotations = """package com.ibm.websphere.samples.daytrader.web.prims.ejb3;    
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
public class TradeDirectDBUtilsTest {
    @Test
    public void testBuildDB() {
        assertEquals(1, 1);
    }
}
"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=java_code_with_test_annotations,
            analysis_backend_path=None,
            analysis_json_path=None,
            analysis_level=AnalysisLevel.call_graph,
            target_files=None,
            eager_analysis=False,
        )

        test_methods = java_analysis.get_test_methods()
        assert test_methods is not None
        assert isinstance(test_methods, Dict)


def test_get_calling_lines(test_fixture, analysis_json):
    """Should return calling lines"""

    # Patch subprocess so that it does not run codeanalyzer
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        java_analysis = JavaAnalysis(
            project_dir=test_fixture,
            source_code=None,
            analysis_backend_path=None,
            analysis_json_path=None,
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
