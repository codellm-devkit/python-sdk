################################################################################
# Copyright IBM Corporation 2024
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

"""Java test sanitization helpers using tree-sitter.

Utilities to analyze Java test classes and methods for deduplication,
merging, and extraction of assertions, fields, and annotations.
"""

import re
from copy import deepcopy
from typing import Dict, List, Any, LiteralString

from cldk.analysis.commons.treesitter.treesitter_java import TreesitterJava
from cldk.analysis.commons.treesitter.models import Captures

java_sitter = TreesitterJava()


def _replace_in_source(
    source_class_code: str,
    original_test_method_dict: dict,
    modified_test_method_dict: dict,
):
    """Return source with original methods replaced by modified ones.

    Args:
        source_class_code (str): Java class source code.
        original_test_method_dict (dict): Original test methods by name.
        modified_test_method_dict (dict): Modified test methods by name.

    Returns:
        str: Source after removing duplicates and merging decomposed tests.

    Examples:
        >>> src = 'class T { void a(){} void b(){} }'
        >>> original = {'a': 'void a(){}'}
        >>> modified = {'a': 'void a(){int x=1;}'}
        >>> out = _replace_in_source(src, original, modified)
        >>> 'int x=1' in out
        True
    """
    modified_source = deepcopy(source_class_code)
    for _, body in original_test_method_dict.items():
        modified_source = modified_source.replace(body, "")
    modified_source = modified_source[: modified_source.rfind("}")]
    for _, body in modified_test_method_dict.items():
        modified_source = modified_source + "\n" + body
    modified_source = modified_source + "\n}"
    return modified_source


def separate_assertions(source_method_code: str) -> tuple[str, str]:
    """Split a test method body into assertions and non-assertions.

    Args:
        source_method_code (str): Test method body.

    Returns:
        tuple[str, str]: A pair of strings: (assertions_block, non_assertions_block).

    Examples:
        >>> body = 'int x=1; assertTrue(x>0); return;'
        >>> asserts, rest = separate_assertions(body)
        >>> 'assertTrue' in asserts and 'assertTrue' not in rest
        True
    """
    code_split = source_method_code.splitlines()
    assert_block = ""
    code_block_without_assertions = ""
    assertion_part = []
    query = """
                            (method_invocation 
                                name: (identifier) @method_name
                            )
                    """
    captures: Captures = java_sitter.frame_query_and_capture_output(query, source_method_code)
    for capture in captures:
        if "method_name" in capture.name:
            call_site_method_name = capture.node.text.decode()
            if "assert" in call_site_method_name:
                method_node = java_sitter.safe_ascend(capture.node, 1)
                assertion_start_line = method_node.start_point[0]
                assertion_end_line = method_node.end_point[0]
                for i in range(assertion_start_line, assertion_end_line + 1):
                    assertion_part.append(i)

    for i in range(len(code_split)):
        if i not in assertion_part:
            code_block_without_assertions += code_split[i].strip() + "\n"
        else:
            assert_block += code_split[i].strip() + "\n"
    return assert_block, code_block_without_assertions


def is_empty_test_class(source_class_code: str) -> bool:
    """Return True if no @Test methods exist in the class.

    Args:
        source_class_code (str): Java class source code.

    Returns:
        bool: True if class has no tests, False otherwise.

    Examples:
        >>> is_empty_test_class('class A { @Test void t(){} }')
        False
        >>> is_empty_test_class('class A { void t(){} }')
        True
    """
    test_methods_dict = java_sitter.get_test_methods(source_class_code)
    print(test_methods_dict)
    return not bool(test_methods_dict)


def get_all_field_access(source_class_code: str) -> Dict[str, list[list[int]]]:
    """Return field access positions in a class.

    Args:
        source_class_code (str): Java class source.

    Returns:
        dict[str, list[list[int]]]: Field name to [[start_line, start_col], [end_line, end_col]].

    Examples:
        >>> src = 'class A { int x; void f(){ this.x = 1; } }'
        >>> isinstance(get_all_field_access(src), dict)
        True
    """
    query = """
                (field_access 
                    field:(identifier) @field_name
                    )
            """
    captures: Captures = java_sitter.frame_query_and_capture_output(query, source_class_code)
    field_dict = {}
    for capture in captures:
        if capture.name == "field_name":
            field_name = capture.node.text.decode()
            field_node = java_sitter.safe_ascend(capture.node, 2)
            start_line = field_node.start_point[0]
            start_column = field_node.start_point[1]
            end_line = field_node.end_point[0]
            end_column = field_node.end_point[1]
            start_list = [start_line, start_column]
            end_list = [end_line, end_column]
            position = [start_list, end_list]
            field_dict[field_name] = position
    return field_dict


def get_all_fields_with_annotations(source_class_code: str) -> Dict[str, Dict]:
    """Return fields with annotations and bodies.

    Args:
        source_class_code (str): Java class source.

    Returns:
        dict[str, dict]: Field name to {'annotation': str | None, 'body': str}.

    Examples:
        >>> src = '@Deprecated class A { @Inject int x; }'
        >>> isinstance(get_all_fields_with_annotations(src), dict)
        True
    """
    query = """
                (field_declaration 
                    (variable_declarator
                        name: (identifier) @field_name
                    )
                )
            """
    captures: Captures = java_sitter.frame_query_and_capture_output(query, source_class_code)
    field_dict = {}
    for capture in captures:
        if capture.name == "field_name":
            field_name = capture.node.text.decode()
            inner_dict = {}
            annotation = None
            field_node = java_sitter.safe_ascend(capture.node, 2)
            body = field_node.text.decode()
            for fc in field_node.children:
                if fc.type == "modifiers":
                    for mc in fc.children:
                        if mc.type == "marker_annotation":
                            annotation = mc.text.decode()
            inner_dict["annotation"] = annotation
            inner_dict["body"] = body
            field_dict[field_name] = inner_dict
    return field_dict


def get_all_methods_with_test_with_lines(source_class_code: str) -> Dict[str, List[int]]:
    """Return @Test methods with their start and end lines.

    Args:
        source_class_code (str): Java class source.

    Returns:
        dict[str, list[int]]: Map of test method name to [start_line, end_line].

    Examples:
        >>> src = 'class A { @Test void t(){} }'
        >>> get_all_methods_with_test_with_lines(src)
        {'t': [0, 0]}  # doctest: +ELLIPSIS
    """
    query = """
                (method_declaration
                    (modifiers
                        (marker_annotation
                        name: (identifier) @annotation)
                    )
                )
            """
    captures: Captures = java_sitter.frame_query_and_capture_output(query, source_class_code)
    annotation_method_dict = {}
    for capture in captures:
        if capture.name == "annotation":
            annotation = capture.node.text.decode()
            if "Test" in annotation:
                method_node = java_sitter.safe_ascend(capture.node, 3)
                method_start_line = method_node.start_point[0]
                method_end_line = method_node.end_point[0]
                method_name = method_node.children[2].text.decode()
                annotation_method_dict[method_name] = [method_start_line, method_end_line]
    return annotation_method_dict


def _remove_duplicates_empties(test_method_dict: dict) -> tuple[dict[Any, Any], int | Any, int | Any]:
    """Remove duplicate and empty test methods.

    Args:
        test_method_dict (dict): Map of test name to body.

    Returns:
        tuple[dict, int, int]: (deduped_methods, num_duplicates_removed, num_empty_removed).
    """
    methods_kept = []
    num_dup_methods_removed = 0
    num_empty_methods_removed = 0
    block_set = set()
    for test in test_method_dict.keys():
        block = test_method_dict[test]
        # capture everything between opening and closing braces
        block = block[block.find("{") + 1 : block.rfind("}")]
        # remove white spaces,tabs and new lines
        block = re.sub(r"[\n\t\s]*", "", block)
        if not block:  # empty method
            num_empty_methods_removed = num_empty_methods_removed + 1
        if block in block_set:
            num_dup_methods_removed = num_dup_methods_removed + 1
        else:
            block_set.add(block)
            methods_kept.append(test)
    deduped_method_dict = {key: test_method_dict[key] for key in test_method_dict if key in methods_kept}
    return (
        deduped_method_dict,
        num_dup_methods_removed,
        num_empty_methods_removed,
    )


def _compose_decomposed(self, test_method_dict: dict) -> tuple[dict, int]:
    """Merge methods that differ only by assertions.

    Args:
        test_method_dict (dict): Map of test name to body.

    Returns:
        tuple[dict, int]: (merged_methods, num_merged).
    """
    composed_test_method_dict = {}
    num_merged_methods = 0
    block_minus_assert_dict = {}
    for test in test_method_dict.keys():
        block = test_method_dict[test]
        # capture everything between opening and closing braces
        block = block[block.find("{") + 1 : block.rfind("}")]
        # remove assertions and keep them aside
        assert_block, block_without_assertions = self._separate_assertions(block)
        if block_without_assertions in block_minus_assert_dict:
            existing_test = block_minus_assert_dict[block_without_assertions]
            composed_test_method_dict[existing_test] = composed_test_method_dict[existing_test] + "\n" + assert_block
            num_merged_methods = num_merged_methods + 1
        else:
            block_minus_assert_dict[block_without_assertions] = test
            composed_test_method_dict[test] = block
    # now add back opening and closing braces
    composed_test_method_dict = {k: "@Test\npublic void " + k + "()\n{" + v + "\n}" for k, v in composed_test_method_dict.items()}
    return composed_test_method_dict, num_merged_methods


def _separate_assertions(code_block: str) -> tuple[str, str]:
    """Separate assertions from the rest of the code block.

    Args:
        code_block (str): Test method body (without surrounding braces).

    Returns:
        tuple[str, str]: (assert_block, code_block_without_assertions).
    """
    code_block_lines = code_block.split(";")
    # remove new lines and tabs, but not spaces within lines (need to keep them for assertions)
    code_block_lines = [re.sub(r"[\n\t]*", "", x) for x in code_block_lines]
    # strip starting and trailing spaces
    code_block_lines[:] = [x.strip() for x in code_block_lines]
    # remove any empty lines
    code_block_lines[:] = [x for x in code_block_lines if x]
    code_block_lines_without_assertions = []
    code_block_without_assertions = ""
    assert_lines = []
    for line in code_block_lines:
        if line.startswith("assert"):
            assert_lines.append(line)
        else:
            # now we can remove spaces within lines
            line = re.sub(r"[\s]*", "", line)
            code_block_lines_without_assertions.append(line)
    # put back the assertion block like it should be
    assert_block = ";\n".join(assert_lines) + ";"
    if len(code_block_lines_without_assertions) > 0:
        code_block_without_assertions = ";".join(code_block_lines_without_assertions) + ";"
    return assert_block, code_block_without_assertions


def dedup_and_merge(self, source_class_code: str) -> tuple[LiteralString | Any, Any, Any, int]:
    """Return source after deduping and merging test methods.

    Args:
        source_class_code (str): Java class source.

    Returns:
        tuple[str, int, int, int]: (modified_source, num_dups_removed, num_empty_removed, num_merged).
    """

    test_method_dict = java_sitter.get_test_methods(source_class_code)
    (
        deduped_method_dict,
        num_dup_methods_removed,
        num_empty_methods_removed,
    ) = _remove_duplicates_empties(test_method_dict)
    merged_method_dict, num_methods_merged = _compose_decomposed(deduped_method_dict)
    modified_source = _replace_in_source(test_class, test_method_dict, merged_method_dict)
    return (
        modified_source,
        num_dup_methods_removed,
        num_empty_methods_removed,
        num_methods_merged,
    )


# TODO: This has to be moved to the test file!
if __name__ == "__main__":
    assert_code_check = """
        {
        StringBuffer result = helpFormatter.renderOptions(sb, width, options, leftPad, descPad);
    assertEquals(result.toString(), "StringBuffer sb = new StringBuffer();\n" +
            "int width = 100;\n" +
            "int leftPad = 1;\n" +
            "int descPad = 1;\n" +
            "StringBuffer result = helpFormatter.renderOptions(sb, width, options, leftPad, descPad);\n" +
            "assertEquals(result.toString(), \"StringBuffer sb = new StringBuffer();\");\n");
    }
    """
    java_code = """
    public class HelloWorld {
        public static void main(String[] args) {
            System.out.println("Hello, World!");
        }
    }
    """
    # tokens = ts1.get_lexical_tokens(code=java_code, filter_by_node_type=['identifier'])
    # print(tokens)
    generated_test_code = """
        @Test
        public void feedDog_mergecandidate(){
            myDog.setWeight(myDog.name);
            myDog.eat();
            assertEquals("Error2 when eating", 10, myDog.getWeight());
        }
                            """
    source_method = """ public void method1() {
                            System.out.println("Start of method1");
                            // Call Method2 at random places
                            method2();
                            System.out.println("Called method2 first time");
                            method2();
                            System.out.println("Called method2 second time");
                            method2();
                            System.out.println("Called method2 last time");
                        }
                    """
    test_class = """   package acd;
                       import a;
                        public class DogTest {
                            private ModResortsCustomerInformation modResortsCustomerInformation;
                            @mock
                            private DataSource dataSource;
                            @mock
                            private Connection connection;
                            private PreparedStatement preparedStatement;
                            private ResultSet resultSet;
                            Dog myDog;
                            @Before
                            public void setUp(){
                                System.out.println("This is run before");
                                myDog = new Dog("Jimmy", "Beagle");
                            }
                            @After
                            public void tearDown(){
                                System.out.println("This is run after");
                            }
                            @Test
                            public void createNewDog(){
                                assertEquals("Error in creating a dog", "Jimmy", myDog.getName());
                            }
                            @Test
                            public void feedDog(){
                                myDog.setWeight(5);
                                myDog.eat();
                                assertEquals("Error when eating", 10, myDog.getWeight());
                            }
                             @Test
                            public void feedDog_mergecandidate(){
                                myDog.setWeight(5);
                                myDog.eat();
                                assertEquals("Error2 when eating", 10, myDog.getWeight());
                            }
                            @Test
                            public void createNewDog_mergecandidate(){
                                assertEquals("another error in creating a dog", "Jimmy", myDog.getName());
                            }
                            @Test
                            public void feedDog2(){
                                myDog.setWeight(5);

                                myDog.eat();
                                assertEquals("Error when eating", 10, myDog.getWeight());
                            }
                            @Test
                            public void feedDog3(){
                                myDog.setWeight(5);       
                                myDog.eat();
                                assertEquals("Error when eating", 10, myDog.getWeight());
                            }
                            @Test
                            public void createNewDog2(){

                                assertEquals("Error in creating a dog", "Jimmy", myDog.getName());
                            }
                            @Test
                            public void emptyTest(){


                            }
                    }
                 """
    empty_test_class = """
                            public class DogTest {
                                private ModResortsCustomerInformation modResortsCustomerInformation;
                                @mock
                                private DataSource dataSource;
                                @mock
                                private Connection connection;
                                private PreparedStatement preparedStatement;
                                private ResultSet resultSet;
                                Dog myDog;
                                @Before
                                public void setUp(){
                                    System.out.println("This is run before");
                                    myDog = new Dog("Jimmy", "Beagle");
                                }
                                @After
                                public void tearDown(){
                                    System.out.println("This is run after");
                            }
                       """
    # target_method_name = "public void method2()"
    print(separate_assertions(source_method_code=assert_code_check))
    # print(ts.get_calling_lines(source_method,target_method_name))
    # print(ts.get_all_methods_with_test_with_lines(test_class))
    # print(ts.get_test_methods(test_class))
    # print(ts.get_all_field_access(generated_test_code))
    # test_method_dict = ts.get_test_methods(test_class)
    # deduped_method_dict, num_methods_removed = ts._remove_duplicates(test_method_dict)
    # print("num_methods_removed ",num_methods_removed)
    # merged_method_dict, num_merged_methods = ts._compose_decomposed(deduped_method_dict)
    # print("merged_methods ",num_merged_methods)
    # print(test_method_dict)
    # print(ts._replace_in_source(test_class,test_method_dict,merged_method_dict))
    # print(ts.get_all_methods_with_annotations(test_class, ["Test", "Before"]))
    # print(ts.get_all_fields_with_annotations(test_class))
    # print(ts.dedup_and_merge(test_class))
    # print(ts.is_empty_test_class(empty_test_class))
