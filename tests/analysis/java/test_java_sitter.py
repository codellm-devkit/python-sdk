################################################################################
# Copyright IBM Corporation 2025
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
from typing import List, Set, Dict
from tree_sitter import Tree
import pytest

from cldk.analysis.commons.treesitter import TreesitterJava


def test_method_is_not_in_class(test_fixture):
    """not find the method in the class"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/beans/MarketSummaryDataBean.java")
    with open(filename, "r", encoding="utf-8") as file:
        class_body = file.read()

    # make sure an existing method returns false
    found = java_sitter.method_is_not_in_class("toString", class_body)
    assert found is False

    # make sure a missing method returns true
    notfound = java_sitter.method_is_not_in_class("foo", class_body)
    assert notfound is True


def test_is_parsable(test_fixture):
    """Should be able to parse the file"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/beans/MarketSummaryDataBean.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    # test with known parsable code
    yes = java_sitter.is_parsable(code)
    assert yes is True

    # test with bas code
    code = "-not pars*ble"
    yes = java_sitter.is_parsable(code)
    assert yes is False


def test_get_raw_ast(test_fixture):
    """Should return the raw AST"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    raw_ast = java_sitter.get_raw_ast(code)
    assert raw_ast is not None
    assert isinstance(raw_ast, Tree)
    assert raw_ast.root_node is not None


def test_get_all_imports(test_fixture):
    """Should return all of the imports"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    all_imports = java_sitter.get_all_imports(code)
    assert all_imports is not None
    assert isinstance(all_imports, Set)
    assert len(all_imports) == 4
    assert "java.util.Collection" in all_imports
    assert "java.util.Iterator" in all_imports
    assert "java.util.logging.Level" in all_imports
    assert "java.util.logging.Logger" in all_imports


def test_get_package_name(test_fixture):
    """Should return the package name"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    package_name = java_sitter.get_pacakge_name(code)
    assert package_name is not None
    assert isinstance(package_name, str)
    assert package_name == "com.ibm.websphere.samples.daytrader.util"


def test_get_class_name(test_fixture):
    """Should return the class name"""
    java_sitter = TreesitterJava()

    # Get a test source file and send its contents
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    class_name = java_sitter.get_class_name(code)
    assert class_name is not None
    assert isinstance(class_name, str)
    assert class_name == "Log"


def test_get_superclass(test_fixture):
    """Should return the superclass name"""
    java_sitter = TreesitterJava()

    # Get a test source file with no supper class
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    supper_class = java_sitter.get_superclass(code)
    assert supper_class is not None
    assert isinstance(supper_class, str)
    assert supper_class == ""

    # Get a test source file with supper class
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/KeyBlock.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    supper_class = java_sitter.get_superclass(code)
    assert supper_class is not None
    assert isinstance(supper_class, str)
    try:
        assert supper_class == "AbstractSequentialList"
    except AssertionError:
        return

    assert False, "This test should have failed"


def test_get_all_interfaces(test_fixture):
    """Should return all interfaces"""
    java_sitter = TreesitterJava()

    # Get a test source file with interfaces
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    interfaces = java_sitter.get_all_interfaces(code)
    assert interfaces is not None
    assert isinstance(interfaces, Set)
    assert len(interfaces) == 2
    assert "TradeServices" in interfaces
    assert "Serializable" in interfaces

    # Get a test source file without interfaces
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/Log.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    interfaces = java_sitter.get_all_interfaces(code)
    assert interfaces is not None
    assert isinstance(interfaces, Set)
    assert len(interfaces) == 0


def test_get_method_name_from_declaration():
    """Should return the method name from a declarations"""
    java_sitter = TreesitterJava()

    declaration = "public Future<?> submitOrder(Integer orderID, boolean twoPhase)"
    method_name = java_sitter.get_method_name_from_declaration(declaration)
    assert method_name is not None
    assert isinstance(method_name, str)
    assert method_name == "submitOrder"


def test_get_method_name_from_invocation():
    """Should return the method name from an invocation"""
    java_sitter = TreesitterJava()

    invocation = "asyncOrder.setProperties(orderID,twoPhase);"
    method_name = java_sitter.get_method_name_from_invocation(invocation)
    assert method_name is not None
    assert isinstance(method_name, str)
    assert method_name == "setProperties"


def test_get_identifier_from_arbitrary_statement():
    """Should return the method name from an arbitrary statement"""
    java_sitter = TreesitterJava()

    arbitrary_statement = "asyncOrder.setProperties(orderID,twoPhase);"
    identifier = java_sitter.get_identifier_from_arbitrary_statement(arbitrary_statement)
    assert identifier is not None
    assert isinstance(identifier, str)
    assert identifier == "asyncOrder"


def test_safe_ascend(test_fixture):
    """safely ascend the node tree"""
    java_sitter = TreesitterJava()

    # Test is catches if the node is None
    with pytest.raises(ValueError) as except_info:
        java_sitter.safe_ascend(None, 1)
    assert except_info.type == ValueError
    assert str(except_info.value) == "Node does not exist."

    # Get source code to test with
    filename = os.path.join(test_fixture, "src/main/java/com/ibm/websphere/samples/daytrader/util/KeyBlock.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    # generate an AST from the code
    raw_ast = java_sitter.get_raw_ast(code)
    assert raw_ast is not None
    assert isinstance(raw_ast, Tree)
    assert raw_ast.root_node is not None

    # Test the root node which doesn't have a parent
    with pytest.raises(ValueError) as except_info:
        java_sitter.safe_ascend(raw_ast.root_node, 1)
    assert except_info.type == ValueError
    assert str(except_info.value) == "Node has no parent."

    # Test with a child node
    root_node = raw_ast.root_node
    assert root_node is not None
    assert root_node.child_count > 0
    child_node = root_node.children[0]

    # When assent_count is 0 you should get the same node back
    parent_node = java_sitter.safe_ascend(child_node, 0)
    assert parent_node is child_node


def test_get_call_targets():
    """get the call targets"""
    java_sitter = TreesitterJava()

    # TODO: This test case needs to be written


def test_conditional_statement():
    """conditional statement"""
    java_sitter = TreesitterJava()

    source_method_code = """
    public static BigDecimal computeHoldingsTotal(Collection<?> holdingDataBeans) {
        BigDecimal holdingsTotal = new BigDecimal(0.0).setScale(SCALE);
        if (holdingDataBeans == null) {
            return holdingsTotal;
        }
        Iterator<?> it = holdingDataBeans.iterator();
        while (it.hasNext()) {
            HoldingDataBean holdingData = (HoldingDataBean) it.next();
            BigDecimal total = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
            holdingsTotal = holdingsTotal.add(total);
        }
        return holdingsTotal.setScale(SCALE);
    }
    """
    conditional_statements = java_sitter.get_all_conditional_statements(source_code=source_method_code)
    assert len(conditional_statements) > 0


def test_get_calling_lines():
    """get the calling lines"""
    java_sitter = TreesitterJava()

    source_method_code = """
    public static BigDecimal computeHoldingsTotal(Collection<?> holdingDataBeans) {
        BigDecimal holdingsTotal = new BigDecimal(0.0).setScale(SCALE);
        if (holdingDataBeans == null) {
            return holdingsTotal;
        }
        Iterator<?> it = holdingDataBeans.iterator();
        while (it.hasNext()) {
            HoldingDataBean holdingData = (HoldingDataBean) it.next();
            BigDecimal total = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
            holdingsTotal = holdingsTotal.add(total);
        }
        return holdingsTotal.setScale(SCALE);
    }
"""
    # test where call is found
    calling_lines = java_sitter.get_calling_lines(source_method_code, "hasNext")
    assert calling_lines is not None
    assert isinstance(calling_lines, List)
    assert len(calling_lines) == 1
    assert calling_lines[0] == 7

    # test where call is not found
    calling_lines = java_sitter.get_calling_lines(source_method_code, "foo")
    assert calling_lines is not None
    assert isinstance(calling_lines, List)
    assert len(calling_lines) == 0


def test_get_test_methods(test_fixture):
    """Should return the test methods"""
    java_sitter = TreesitterJava()

    # TODO: Need to find an example with test methods

    # Get a test source file with interfaces
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    test_methods = java_sitter.get_test_methods(code)
    assert test_methods is not None
    assert isinstance(test_methods, Dict)
    assert len(test_methods) == 0


def test_get_methods_with_annotations(test_fixture):
    """Should return methods with annotations"""
    java_sitter = TreesitterJava()

    # Get a test source file with annotations
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingJDBCRead2JSP.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    annotations = ["Override"]
    methods_with_annotations = java_sitter.get_methods_with_annotations(code, annotations)
    assert methods_with_annotations is not None
    assert isinstance(methods_with_annotations, Dict)

    # check that there a 4 methods with @Override
    overrides = methods_with_annotations["Override"]
    assert overrides is not None
    assert isinstance(overrides, List)
    assert len(overrides) == 4


def test_get_all_type_invocations(test_fixture):
    """Should return all of the type invocations"""
    java_sitter = TreesitterJava()

    # Get a test source file
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingJDBCRead2JSP.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    type_invocations = java_sitter.get_all_type_invocations(code)
    assert type_invocations is not None
    assert isinstance(type_invocations, Set)
    assert len(type_invocations) == 11
    assert "HttpServletResponse" in type_invocations
    assert "ServletConfig" in type_invocations
    assert "HttpServlet" in type_invocations


def test_get_method_return_type():
    """get the methods return type"""
    java_sitter = TreesitterJava()

    source_method_code = """
    public static BigDecimal computeHoldingsTotal(Collection<?> holdingDataBeans) {
        BigDecimal holdingsTotal = new BigDecimal(0.0).setScale(SCALE);
        if (holdingDataBeans == null) {
            return holdingsTotal;
        }
        Iterator<?> it = holdingDataBeans.iterator();
        while (it.hasNext()) {
            HoldingDataBean holdingData = (HoldingDataBean) it.next();
            BigDecimal total = holdingData.getPurchasePrice().multiply(new BigDecimal(holdingData.getQuantity()));
            holdingsTotal = holdingsTotal.add(total);
        }
        return holdingsTotal.setScale(SCALE);
    }
"""
    return_type = java_sitter.get_method_return_type(source_method_code)
    assert return_type is not None
    assert isinstance(return_type, str)
    assert return_type == "BigDecimal"


def test_get_lexical_tokens(test_fixture):
    """Should return the lexical tokens"""
    java_sitter = TreesitterJava()

    # Get a test source file
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingJDBCRead2JSP.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    lexical_tokens = java_sitter.get_lexical_tokens(code)
    assert lexical_tokens is not None
    assert isinstance(lexical_tokens, List)
    assert len(lexical_tokens) > 0
    assert "package" in lexical_tokens


def test_remove_all_comments(test_fixture):
    """remove all comments"""
    java_sitter = TreesitterJava()

    # Get a test source file
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingJDBCRead2JSP.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    assert "/*" in code
    no_comments = java_sitter.remove_all_comments(code)
    assert no_comments is not None
    assert isinstance(no_comments, str)
    assert len(no_comments) > 0
    assert "/*" not in no_comments


def test_make_pruned_code_prettier(test_fixture):
    """make pruned code prettier"""
    java_sitter = TreesitterJava()

    # Get a test source file
    filename = os.path.join(test_fixture,
                            "src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingJDBCRead2JSP.java")
    with open(filename, "r", encoding="utf-8") as file:
        code = file.read()

    assert "/*" in code
    pretty_code = java_sitter.make_pruned_code_prettier(code)
    assert pretty_code is not None
    assert isinstance(pretty_code, str)
    assert len(pretty_code) > 0
    assert "/*" not in pretty_code
