"""
Test cases for inheritance support in __raw_call_graph_using_symbol_table method.

These tests verify that the call graph correctly identifies inherited methods
when building the call graph using the symbol table.
"""

import pytest
from cldk import CLDK
from cldk.analysis import AnalysisLevel


class TestInheritanceCallGraphIntegration:
    """Test suite for inheritance support in call graph generation."""

    def test_call_graph_with_inherited_method_from_parent_class(self):
        """Test that call graph includes inherited methods from parent classes."""
        java_code = """
        package com.example;
        
        class Parent {
            public void inheritedMethod() {
                System.out.println("Parent method");
            }
        }
        
        class Child extends Parent {
            public void callerMethod() {
                this.inheritedMethod();
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get call graph using symbol table
        call_graph_edges = java_analysis.backend.get_class_call_graph_using_symbol_table(
            qualified_class_name="com.example.Child",
            method_signature="callerMethod()"
        )
        
        # Verify that the call to inheritedMethod is captured
        assert len(call_graph_edges) > 0
        
        # Find the edge for the inherited method call
        found_inherited_call = False
        for source, target in call_graph_edges:
            if (source.klass == "com.example.Child" and 
                source.method.signature == "callerMethod()" and
                target.method.signature == "inheritedMethod()"):
                # The target should be the Parent class where the method is defined
                assert target.klass == "com.example.Parent"
                found_inherited_call = True
                break
        
        assert found_inherited_call, "Call to inherited method not found in call graph"

    def test_call_graph_prefers_concrete_implementation_over_interface(self):
        """Test that concrete implementation is preferred over interface method."""
        java_code = """
        package com.example;
        
        interface MyInterface {
            void sharedMethod();
        }
        
        class ConcreteClass implements MyInterface {
            public void sharedMethod() {
                System.out.println("Concrete implementation");
            }
            
            public void callerMethod() {
                this.sharedMethod();
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get call graph using symbol table
        call_graph_edges = java_analysis.backend.get_class_call_graph_using_symbol_table(
            qualified_class_name="com.example.ConcreteClass",
            method_signature="callerMethod()"
        )
        
        # Verify that the call points to the concrete implementation, not the interface
        found_concrete_call = False
        for source, target in call_graph_edges:
            if (source.klass == "com.example.ConcreteClass" and 
                source.method.signature == "callerMethod()" and
                target.method.signature == "sharedMethod()"):
                # The target should be the concrete class, not the interface
                assert target.klass == "com.example.ConcreteClass"
                assert target.klass != "com.example.MyInterface"
                found_concrete_call = True
                break
        
        assert found_concrete_call, "Call to concrete implementation not found in call graph"

    def test_call_graph_with_multi_level_inheritance(self):
        """Test that call graph handles multi-level inheritance correctly with parameterized methods."""
        java_code = """
        package com.example;
        
        class Grandparent {
            public void grandparentMethod(String message, int count) {
                System.out.println("Grandparent method: " + message + " x " + count);
            }
        }
        
        class Parent extends Grandparent {
            public void parentMethod(String text) {
                System.out.println("Parent method: " + text);
            }
        }
        
        class Child extends Parent {
            public void callerMethod() {
                // Call inherited method from grandparent with parameters
                this.grandparentMethod("hello", 5);
                // Call inherited method from parent with parameter
                this.parentMethod("world");
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get call graph using symbol table
        call_graph_edges = java_analysis.backend.get_class_call_graph_using_symbol_table(
            qualified_class_name="com.example.Child",
            method_signature="callerMethod()"
        )
        
        # Verify both method calls are captured
        assert len(call_graph_edges) >= 2
        
        found_grandparent_call = False
        found_parent_call = False
        for source, target in call_graph_edges:
            if (source.klass == "com.example.Child" and
                source.method.signature == "callerMethod()"):
                if "grandparentMethod" in target.method.signature:
                    assert target.klass == "com.example.Grandparent"
                    found_grandparent_call = True
                elif "parentMethod" in target.method.signature:
                    assert target.klass == "com.example.Parent"
                    found_parent_call = True
        
        assert found_grandparent_call, "Call to grandparent method not found"
        assert found_parent_call, "Call to parent method not found"

    def test_get_all_callees_with_inherited_methods(self):
        """Test get_all_callees includes inherited method calls."""
        java_code = """
        package com.example;
        
        class Parent {
            public void inheritedMethod() {
                System.out.println("Parent method");
            }
        }
        
        class Child extends Parent {
            public void callerMethod() {
                this.inheritedMethod();
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get all callees using symbol table
        callees = java_analysis.backend.get_all_callees(
            source_class_name="com.example.Child",
            source_method_signature="callerMethod()",
            using_symbol_table=True
        )
        
        # Verify the inherited method is in the callees
        assert "callee_details" in callees
        assert len(callees["callee_details"]) > 0
        
        found_inherited_callee = False
        for callee in callees["callee_details"]:
            if callee["callee_method"].method.signature == "inheritedMethod()":
                # Should point to Parent class where method is defined
                assert callee["callee_method"].klass == "com.example.Parent"
                found_inherited_callee = True
                break
        
        assert found_inherited_callee, "Inherited method not found in callees"

    def test_get_all_callers_with_inherited_methods(self):
        """Test get_all_callers works with inherited method calls."""
        java_code = """
        package com.example;
        
        class Parent {
            public void targetMethod() {
                System.out.println("Target method");
            }
        }
        
        class Child extends Parent {
            public void callerMethod() {
                this.targetMethod();
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get all callers of the parent method using symbol table
        callers = java_analysis.backend.get_all_callers(
            target_class_name="com.example.Parent",
            target_method_signature="targetMethod()",
            using_symbol_table=True
        )
        
        # Verify the child class caller is found
        assert "caller_details" in callers
        assert len(callers["caller_details"]) > 0
        
        found_child_caller = False
        for caller in callers["caller_details"]:
            if (caller["caller_method"].klass == "com.example.Child" and
                caller["caller_method"].method.signature == "callerMethod()"):
                found_child_caller = True
                break
        
        assert found_child_caller, "Child class caller not found"

    def test_get_all_callers_with_multi_level_inheritance(self):
        """Test that get_all_callers works correctly with multi-level inheritance.
        
        This specifically tests the __raw_call_graph_using_symbol_table_target_method
        which is used when is_target_method=True.
        """
        java_code = """
        package com.example;
        
        class GrandParent {
            public void targetMethod() {
                System.out.println("GrandParent method");
            }
        }
        
        class Parent extends GrandParent {
            // Inherits targetMethod from GrandParent
        }
        
        class Child extends Parent {
            // Inherits targetMethod from Parent (which inherited from GrandParent)
            
            public void callInheritedMethod() {
                this.targetMethod();  // Calls inherited method from GrandParent
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get all callers of the grandparent method using symbol table
        # This should find Child.callInheritedMethod even though it's calling via inheritance
        callers = java_analysis.backend.get_all_callers(
            target_class_name="com.example.GrandParent",
            target_method_signature="targetMethod()",
            using_symbol_table=True
        )
        
        # Verify the caller is found
        assert "caller_details" in callers
        assert len(callers["caller_details"]) > 0
        
        found_child_caller = False
        for caller in callers["caller_details"]:
            if (caller["caller_method"].klass == "com.example.Child" and
                caller["caller_method"].method.signature == "callInheritedMethod()"):
                found_child_caller = True
                break
        
        assert found_child_caller, "Child class caller not found when calling inherited method"

    def test_get_all_callees_with_multi_level_inheritance(self):
        """Test get_all_callees with multi-level inheritance."""
        java_code = """
        package com.example;
        
        class Grandparent {
            public void grandparentMethod() {
                System.out.println("Grandparent method");
            }
        }
        
        class Parent extends Grandparent {
            public void parentMethod() {
                System.out.println("Parent method");
            }
        }
        
        class Child extends Parent {
            public void callerMethod() {
                this.grandparentMethod();  // Inherited from Grandparent
                this.parentMethod();        // Inherited from Parent
            }
        }
        """
        
        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )
        
        # Get all callees using symbol table
        callees = java_analysis.backend.get_all_callees(
            source_class_name="com.example.Child",
            source_method_signature="callerMethod()",
            using_symbol_table=True
        )
        
        # Verify both inherited methods are in the callees
        assert "callee_details" in callees
        assert len(callees["callee_details"]) >= 2
        
        found_grandparent_callee = False
        found_parent_callee = False
        
        for callee in callees["callee_details"]:
            if callee["callee_method"].method.signature == "grandparentMethod()":
                # Should point to Grandparent class where method is defined
                assert callee["callee_method"].klass == "com.example.Grandparent"
                found_grandparent_callee = True
            elif callee["callee_method"].method.signature == "parentMethod()":
                # Should point to Parent class where method is defined
                assert callee["callee_method"].klass == "com.example.Parent"
                found_parent_callee = True
        
        assert found_grandparent_callee, "Grandparent method not found in callees"
        assert found_parent_callee, "Parent method not found in callees"

    def test_interface_only_methods_not_in_call_graph(self):
        """Test that interface methods without concrete implementations are NOT included in call graph.

        This verifies that when only interface methods are available (no concrete implementation),
        they are excluded from the call graph.
        """
        java_code = """
        package com.example;

        interface MyInterface {
            void interfaceOnlyMethod();
        }

        class ImplementingClass implements MyInterface {
            // Does NOT implement interfaceOnlyMethod - it's abstract

            public void callerMethod() {
                // Attempting to call interface method without implementation
                this.interfaceOnlyMethod();
            }
        }
        """

        # Analyze the code
        java_analysis = CLDK(language="java").analysis(
            source_code=java_code,
            analysis_level=AnalysisLevel.symbol_table
        )

        # Get call graph using symbol table
        call_graph_edges = java_analysis.backend.get_class_call_graph_using_symbol_table(
            qualified_class_name="com.example.ImplementingClass",
            method_signature="callerMethod()"
        )

        # Verify that the call to interface-only method is NOT in the call graph
        # Since there's no concrete implementation, it should be excluded
        found_interface_call = False
        for source, target in call_graph_edges:
            if (source.klass == "com.example.ImplementingClass" and
                    source.method.signature == "callerMethod()" and
                    target.method.signature == "interfaceOnlyMethod()"):
                found_interface_call = True
                break

        assert not found_interface_call, "Interface-only method should NOT be in call graph"
        # The call graph should be empty or not contain the interface method
        assert len(call_graph_edges) == 0, "Call graph should be empty when only interface methods are available"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
