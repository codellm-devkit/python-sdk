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

"""
TreesitterJava module
"""
import logging
from itertools import groupby
from typing import List, Set, Dict
from tree_sitter import Language, Node, Parser, Query, Tree
import tree_sitter_java as tsjava
from cldk.analysis.commons.treesitter.models import Captures

logger = logging.getLogger(__name__)

LANGUAGE: Language = Language(tsjava.language())
PARSER: Parser = Parser(LANGUAGE)


# pylint: disable=too-many-public-methods
class TreesitterJava:
    """
    Treesitter for Java usecases.
    """

    def __init__(self) -> None:
        pass

    def method_is_not_in_class(self, method_name: str, class_body: str) -> bool:
        """Check if a method is in a class.

        Parameters
        ----------
        method_name : str
            The name of the method to check.
        class_body : str
            The body of the class to check.

        Returns
        -------
        bool
            True if the method is in the class, False otherwise.
        """
        methods_in_class = self.frame_query_and_capture_output("(method_declaration name: (identifier) @name)", class_body)

        return method_name not in {method.node.text.decode() for method in methods_in_class}

    def is_parsable(self, code: str) -> bool:
        """
        Check if the code is parsable
        Args:
            code: source code

        Returns:
            True if the code is parsable, False otherwise
        """

        def syntax_error(node):
            if node.type == "ERROR":
                return True
            try:
                for child in node.children:
                    if syntax_error(child):
                        return True
            except RecursionError:
                return True

            return False

        tree = PARSER.parse(bytes(code, "utf-8"))
        if tree is not None:
            return not syntax_error(tree.root_node)
        return False

    def get_raw_ast(self, code: str) -> Tree:
        """
        Get the raw AST
        Args:
            code: source code

        Returns:
            Tree: the raw AST
        """
        return PARSER.parse(bytes(code, "utf-8"))

    def get_all_imports(self, source_code: str) -> Set[str]:
        """Get a list of all the imports in a class.

        Args:
            source_code (str): The source code to process.

        Returns:
            Set[str]: A set of all the imports in the class.
        """
        import_declarations: Captures = self.frame_query_and_capture_output(query="(import_declaration (scoped_identifier) @name)", code_to_process=source_code)
        return {capture.node.text.decode() for capture in import_declarations}

    # TODO: This typo needs to be fixed (i.e., package not pacakge)
    def get_pacakge_name(self, source_code: str) -> str:
        """Get the package name from the source code.

        Args:
            source_code (str): The source code to process.

        Returns:
            str: The package name.
        """
        package_name: Captures = self.frame_query_and_capture_output(query="((package_declaration) @name)", code_to_process=source_code)
        if package_name:
            return package_name[0].node.text.decode().replace("package ", "").replace(";", "")
        return None

    def get_class_name(self, source_code: str) -> str:
        """Get the class name from the source code.

        Args:
            source_code (str): The source code to process.

        Returns:
            str: The class name.
        """
        class_name = self.frame_query_and_capture_output("(class_declaration name: (identifier) @name)", source_code)
        return class_name[0].node.text.decode()

    def get_superclass(self, source_code: str) -> str:
        """Get a list of all the superclasses in a class.

        Args:
            source_code (str): The source code to process.

        Returns:
            Set[str]: A set of all the superclasses in the class.
        """
        superclass: Captures = self.frame_query_and_capture_output(query="(class_declaration (superclass (type_identifier) @superclass))", code_to_process=source_code)

        if len(superclass) == 0:
            # In some cases where we have `class A extends B<C>`, the superclass is a generic type.
            superclass: Captures = self.frame_query_and_capture_output(query="(class_declaration (superclass (generic_type) @superclass))", code_to_process=source_code)

        if len(superclass) == 0:
            return ""

        return superclass[0].node.text.decode()

    def get_all_interfaces(self, source_code: str) -> Set[str]:
        """Get a set of interfaces implemented by a class.

        Args:
            source_code (str): The source code to process.

        Returns:
            Set[str]: A set of all the interfaces implemented by the class.
        """

        interfaces = self.frame_query_and_capture_output("(class_declaration (super_interfaces (type_list (type_identifier) @interface)))", code_to_process=source_code)
        return {interface.node.text.decode() for interface in interfaces}

    def frame_query_and_capture_output(self, query: str, code_to_process: str) -> Captures:
        """Frame a query for the tree-sitter parser.

        Parameters
        ----------
        query : str
            The query to frame.
        code_to_process : str
            The code to process.
        """
        framed_query: Query = LANGUAGE.query(query)
        tree = PARSER.parse(bytes(code_to_process, "utf-8"))
        return Captures(framed_query.captures(tree.root_node))

    def get_method_name_from_declaration(self, method_name_string: str) -> str:
        """Get the method name from the method signature."""
        captures: Captures = self.frame_query_and_capture_output("(method_declaration name: (identifier) @method_name)", method_name_string)

        return captures[0].node.text.decode()

    def get_method_name_from_invocation(self, method_invocation: str) -> str:
        """
        Using the tree-sitter query, extract the method name from the method invocation.
        """

        captures: Captures = self.frame_query_and_capture_output("(method_invocation name: (identifier) @method_name)", method_invocation)
        return captures[0].node.text.decode()

    def get_identifier_from_arbitrary_statement(self, statement: str) -> str:
        """Get the identifier from an arbitrary statement."""
        captures: Captures = self.frame_query_and_capture_output("(identifier) @identifier", statement)
        return captures[0].node.text.decode()

    def safe_ascend(self, node: Node, ascend_count: int) -> Node:
        """Safely ascend the tree. If the node does not exist or if it has no parent, raise an error.

        Parameters
        ----------
        node : Node
            The node to ascend from.
        ascend_count : int
            The number of times to ascend the tree.

        Returns
        -------
        Node
            The node at the specified level of the tree.

        Raises
        ------
        ValueError
            If the node has no parent.
        """
        if node is None:
            raise ValueError("Node does not exist.")
        if node.parent is None:
            raise ValueError("Node has no parent.")
        if ascend_count == 0:
            return node
        else:
            return self.safe_ascend(node.parent, ascend_count - 1)

    def get_call_targets(self, method_body: str, declared_methods: dict) -> Set[str]:
        """Should generate a list of call targets from the method body.

        Uses simple name resolution for finding the call targets. Nothing sophisticated here. Just a simple search
        over the AST.

        Parameters
        ----------
        method_body : Node
            The method body.
        declared_methods : dict
            A dictionary of all declared methods in the class.

        Returns
        -------
        List[str]
            A list of call targets (methods).
        """

        select_test_method_query = "(method_invocation name: (identifier) @method)"
        captures: Captures = self.frame_query_and_capture_output(select_test_method_query, method_body)

        call_targets = set(
            map(
                # x is a capture, x.node is the node, x.node.text is the text of the node (in this case, the method
                # name)
                lambda x: x.node.text.decode(),
                filter(  # Filter out the calls to methods that are not declared in the class
                    lambda capture: capture.node.text.decode() in declared_methods,
                    captures,
                ),
            )
        )
        return call_targets

    def get_calling_lines(self, source_method_code: str, target_method_name: str) -> List[int]:
        """
        Returns a list of line numbers in source method where target method is called.

        Parameters:
        -----------
        source_method_code : str
            source method code

        target_method_code : str

        Returns:
        --------
        List[int]
            List of line numbers within in source method code block.
        """
        if not source_method_code:
            return []
        query = "(object_creation_expression (type_identifier) @object_name) (object_creation_expression type: (scoped_type_identifier (type_identifier) @type_name)) (method_invocation name: (identifier) @method_name)"

        # if target_method_name is a method signature, get the method name
        # if it is not a signature, we will just keep the passed method name

        target_method_name = target_method_name.split("(")[0]  # remove the arguments from the constructor name
        try:
            captures: Captures = self.frame_query_and_capture_output(query, source_method_code)
            # Find the line numbers where target method calls happen in source method
            target_call_lines = []
            for c in captures:
                method_name = c.node.text.decode()
                if method_name == target_method_name:
                    target_call_lines.append(c.node.start_point[0])
        except:
            logger.warning(f"Unable to get calling lines for {target_method_name} in {source_method_code}.")
            return []

        return target_call_lines

    def get_test_methods(self, source_class_code: str) -> Dict[str, str]:
        """
        Returns a dictionary of method names and method bodies.

        Parameters:
        -----------
        source_class_code : str
            String containing code for a java class.

        Returns:
        --------
        Dict[str,str]
            Dictionary of method names and method bodies.
        """
        query = """
                    (method_declaration
                        (modifiers
                            (marker_annotation
                            name: (identifier) @annotation)
                        )
                    )
                """

        captures: Captures = self.frame_query_and_capture_output(query, source_class_code)
        test_method_dict = {}
        for capture in captures:
            if capture.name == "annotation":
                if capture.node.text.decode() == "Test":
                    method_node = self.safe_ascend(capture.node, 3)
                    method_name = method_node.children[2].text.decode()
                    test_method_dict[method_name] = method_node.text.decode()
        return test_method_dict

    def get_methods_with_annotations(self, source_class_code: str, annotations: List[str]) -> Dict[str, List[Dict]]:
        """
        Returns a dictionary of method names and method bodies.

        Parameters:
        -----------
        source_class_code : str
            String containing code for a java class.
        annotations : List[str]
            List of annotation strings.
        Returns:
        --------
        Dict[str,List[Dict]]
            Dictionary with annotations as keys and
            a list of dictionaries containing method names and bodies, as values.
        """
        query = """
                    (method_declaration
                        (modifiers
                            (marker_annotation
                            name: (identifier) @annotation)
                        )
                    )
                """
        captures: Captures = self.frame_query_and_capture_output(query, source_class_code)
        annotation_method_dict = {}
        for capture in captures:
            if capture.name == "annotation":
                annotation = capture.node.text.decode()
                if annotation in annotations:
                    method = {}
                    method_node = self.safe_ascend(capture.node, 3)
                    method["body"] = method_node.text.decode()
                    method["method_name"] = method_node.children[2].text.decode()
                    if annotation in annotation_method_dict.keys():
                        annotation_method_dict[annotation].append(method)
                    else:
                        annotation_method_dict[annotation] = [method]
        return annotation_method_dict

    def get_all_type_invocations(self, source_code: str) -> Set[str]:
        """
        Given the source code, get all the type invocations in the source code.

        Parameters
        ----------
        source_code : str
            The source code to process.

        Returns
        -------
        Set[str]
            A set of all the type invocations in the source code.
        """
        type_references: Captures = self.frame_query_and_capture_output("(type_identifier) @type_id", source_code)
        return {type_id.node.text.decode() for type_id in type_references}

    def get_method_return_type(self, source_code: str) -> str:
        """Get the return type of a method.

        Parameters
        ----------
        source_code : str
            The source code to process.

        Returns
        -------
        str
            The return type of the method.
        """

        type_references: Captures = self.frame_query_and_capture_output("(method_declaration type: ((type_identifier) @type_id))", source_code)

        return type_references[0].node.text.decode()

    def get_lexical_tokens(self, code: str, filter_by_node_type: List[str] | None = None) -> List[str]:
        """
        Get the lexical tokens given the code

        Parameters
        ----------
        filter_by_node_type: If needed, filter the type of the node
        code: Java code

        Returns
        -------
        List[str]
        List of lexical tokens

        """
        tree = PARSER.parse(bytes(code, "utf-8"))
        root_node = tree.root_node
        lexical_tokens = []

        def collect_leaf_token_values(node):
            if len(node.children) == 0:
                if filter_by_node_type is not None:
                    if node.type in filter_by_node_type:
                        lexical_tokens.append(code[node.start_byte : node.end_byte])
                else:
                    lexical_tokens.append(code[node.start_byte : node.end_byte])
            else:
                for child in node.children:
                    collect_leaf_token_values(child)

        collect_leaf_token_values(root_node)
        return lexical_tokens

    def remove_all_comments(self, source_code: str) -> str:
        """
        Remove all comments from the source code.

        Parameters
        ----------
        source_code : str
            The source code to process.

        Returns
        -------
        str
            The source code with all comments removed.
        """

        # Remove any prefix comments/content before the package declaration
        lines_of_code = source_code.split("\n")
        for i, line in enumerate(lines_of_code):
            if line.strip().startswith("package"):
                break

        source_code = "\n".join(lines_of_code[i:])

        pruned_source_code = self.make_pruned_code_prettier(source_code)

        # Remove all comment lines: the comment lines start with / (for // and /*) or * (for multiline comments).
        comment_blocks: Captures = self.frame_query_and_capture_output(query="((block_comment) @comment_block)", code_to_process=source_code)

        comment_lines: Captures = self.frame_query_and_capture_output(query="((line_comment) @comment_line)", code_to_process=source_code)

        for capture in comment_blocks:
            pruned_source_code = pruned_source_code.replace(capture.node.text.decode(), "")

        for capture in comment_lines:
            pruned_source_code = pruned_source_code.replace(capture.node.text.decode(), "")

        return self.make_pruned_code_prettier(pruned_source_code)

    def make_pruned_code_prettier(self, pruned_code: str) -> str:
        """Make the pruned code prettier.

        Parameters
        ----------
        pruned_code : str
            The pruned code.

        Returns
        -------
        str
            The prettified pruned code.
        """
        # First remove remaining block comments
        block_comments: Captures = self.frame_query_and_capture_output(query="((block_comment) @comment_block)", code_to_process=pruned_code)

        for capture in block_comments:
            pruned_code = pruned_code.replace(capture.node.text.decode(), "")

        # Split the source code into lines and remove trailing whitespaces. rstip() removes the trailing whitespaces.
        new_source_code_as_list = list(map(lambda x: x.rstrip(), pruned_code.split("\n")))

        # Remove all comment lines. In java the comment lines start with / (for // and /*) or * (for multiline
        # comments).
        new_source_code_as_list = [line for line in new_source_code_as_list if not line.lstrip().startswith(("/", "*"))]

        # Remove multiple contiguous empty lines. This is done using the groupby function from itertools.
        # groupby returns a list of tuples where the first element is the key and the second is an iterator over the
        # group. We only need the key, so we take the first element of each tuple. The iterator is essentially a
        # generator that contains the elements of the group. We don't need it, so we discard it. The key is the line
        # itself, so we can use it to remove contiguous empty lines.
        new_source_code_as_list = [key for key, _ in groupby(new_source_code_as_list)]

        # Join the lines back together
        prettified_pruned_code = "\n".join(new_source_code_as_list)

        return prettified_pruned_code.strip()
