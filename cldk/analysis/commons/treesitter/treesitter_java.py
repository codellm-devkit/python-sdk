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

"""Java Tree-sitter queries and helpers module.

This module provides comprehensive utilities for parsing Java source code using
Tree-sitter and extracting various code elements. It serves as the foundation
for syntactic analysis in CLDK's Java support.

The module provides extraction for:
    - **Classes and interfaces**: Names, inheritance, implementations
    - **Methods**: Names, signatures, annotations, bodies
    - **Imports**: Package and type imports
    - **Invocations**: Method calls and type references
    - **Comments**: Block comments, line comments, and Javadoc

Key features:
    - S-expression query support for pattern matching
    - AST traversal utilities
    - Code transformation (comment removal, prettification)
    - Test method detection (JUnit annotations)

See Also:
    - :class:`~cldk.analysis.java.JavaAnalysis`: High-level Java analysis.
    - :class:`TreesitterPython`: Equivalent for Python parsing.
"""
import logging
from itertools import groupby
from typing import Dict, List, Set

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser, Query, Tree

from cldk.analysis.commons.treesitter.models import Captures

logger = logging.getLogger(__name__)

LANGUAGE: Language = Language(tsjava.language())
"""The Tree-sitter Language object for Java grammar."""

PARSER: Parser = Parser(LANGUAGE)
"""Global Tree-sitter parser instance configured for Java."""


# pylint: disable=too-many-public-methods
class TreesitterJava:
    """Tree-sitter helper class for Java source code parsing and analysis.

    This class provides comprehensive utilities for parsing Java source code
    using Tree-sitter. It offers methods for:
        - Syntax validation
        - AST generation and traversal
        - Code element extraction (classes, methods, imports)
        - Pattern matching via S-expression queries
        - Code transformation (comment removal)

    The class is stateless and uses module-level parser and language objects,
    making it thread-safe for concurrent use.

    Attributes:
        None. This class is stateless and provides only utility methods.

    See Also:
        - :class:`~cldk.analysis.java.JavaAnalysis`: High-level analysis facade.
        - :class:`TreesitterPython`: Equivalent for Python.
    """

    def __init__(self) -> None:
        """Initialize the TreesitterJava helper.

        Creates a new instance of the Java Tree-sitter helper. This class
        is stateless; initialization performs no setup as all parsing uses
        module-level parser and language objects.
        """
        pass

    def method_is_not_in_class(self, method_name: str, class_body: str) -> bool:
        """Check if a method is NOT declared in a class body.

        Searches for method declarations in the given class body and checks
        if the specified method name is absent.

        Args:
            method_name: The method name to check for (without parentheses
                or parameters).
            class_body: The Java class source code to search within.

        Returns:
            ``True`` if the method is NOT found in the class body,
            ``False`` if the method IS found.
        """
        methods_in_class = self.frame_query_and_capture_output("(method_declaration name: (identifier) @name)", class_body)

        return method_name not in {method.node.text.decode() for method in methods_in_class}

    def is_parsable(self, code: str) -> bool:
        """Check if the given code is syntactically valid Java.

        Parses the code using Tree-sitter and recursively checks for ERROR
        nodes in the resulting AST. Returns ``True`` only if the entire
        code parses without syntax errors.

        Args:
            code: A string containing Java source code to validate. Can be
                a complete compilation unit, a class, a method, or any
                syntactically valid Java fragment.

        Returns:
            ``True`` if the code parses without syntax errors, ``False``
            otherwise. Also returns ``False`` if parsing triggers a
            RecursionError (for extremely nested code).

        Note:
            This checks syntactic validity only, not semantic correctness.
            Code with undefined types or methods will still be "parsable".

        See Also:
            :meth:`get_raw_ast`: To obtain the AST for further analysis.
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
        """Parse code and return the Tree-sitter AST.

        Parses the provided Java source code using Tree-sitter and returns
        the resulting abstract syntax tree. The AST can be traversed to
        extract syntactic information about the code structure.

        Args:
            code: A string containing Java source code to parse.

        Returns:
            A Tree-sitter ``Tree`` object representing the parsed AST. The
            tree's ``root_node`` provides access to the entire syntax tree:
                - ``root_node.type``: Typically ``"program"`` for Java
                - ``root_node.children``: Top-level declarations
                - ``root_node.text``: Original source bytes

        Note:
            If the source contains syntax errors, Tree-sitter returns a tree
            with ERROR nodes at parse error locations. Use :meth:`is_parsable`
            to check for valid syntax first.

        See Also:
            :meth:`is_parsable`: To validate syntax before parsing.
        """
        return PARSER.parse(bytes(code, "utf-8"))

    # NOTE: Not used anywhere in the codebase. Does not return the new JImport model.
    # Update if wired into a real code path.
    def get_all_imports(self, source_code: str) -> Set[str]:
        """Return all import statements in the source.

        Args:
            source_code (str): Java source.

        Returns:
            set[str]: Import identifiers.
        """
        import_declarations: Captures = self.frame_query_and_capture_output(query="(import_declaration (scoped_identifier) @name)", code_to_process=source_code)
        return {capture.node.text.decode() for capture in import_declarations}

    # TODO: This typo needs to be fixed (i.e., package not pacakge)
    def get_pacakge_name(self, source_code: str) -> str:
        """Return the package name from the source code.

        Args:
            source_code (str): Java source.

        Returns:
            str: Package declaration value, or None if absent.
        """
        package_name: Captures = self.frame_query_and_capture_output(query="((package_declaration) @name)", code_to_process=source_code)
        if package_name:
            return package_name[0].node.text.decode().replace("package ", "").replace(";", "")
        return None

    def get_class_name(self, source_code: str) -> str:
        """Return the class name from the source code.

        Args:
            source_code (str): Java source.

        Returns:
            str: Class identifier.
        """
        class_name = self.frame_query_and_capture_output("(class_declaration name: (identifier) @name)", source_code)
        return class_name[0].node.text.decode()

    def get_superclass(self, source_code: str) -> str:
        """Return the superclass name if present.

        Args:
            source_code (str): Java source.

        Returns:
            str: Superclass identifier or empty string.
        """
        superclass: Captures = self.frame_query_and_capture_output(query="(class_declaration (superclass (type_identifier) @superclass))", code_to_process=source_code)

        if len(superclass) == 0:
            # In some cases where we have `class A extends B<C>`, the superclass is a generic type.
            superclass: Captures = self.frame_query_and_capture_output(query="(class_declaration (superclass (generic_type) @superclass))", code_to_process=source_code)

        if len(superclass) == 0:
            return ""

        return superclass[0].node.text.decode()

    def get_all_interfaces(self, source_code: str) -> Set[str]:
        """Return interfaces implemented by a class.

        Args:
            source_code (str): Java source.

        Returns:
            set[str]: Interface identifiers.
        """

        interfaces = self.frame_query_and_capture_output("(class_declaration (super_interfaces (type_list (type_identifier) @interface)))", code_to_process=source_code)
        return {interface.node.text.decode() for interface in interfaces}

    def frame_query_and_capture_output(self, query: str, code_to_process: str) -> Captures:
        """Execute a Tree-sitter query and return captured nodes.

        Parses the provided source code and runs the given S-expression
        query against the AST, returning all captured nodes.

        Args:
            query: A Tree-sitter S-expression query string defining the
                pattern to match and captures to extract. Captures are
                denoted with ``@name`` syntax.
            code_to_process: Java source code to parse and query.

        Returns:
            A :class:`~cldk.analysis.commons.treesitter.models.Captures`
            object containing all nodes matched by the query, with their
            capture names and node references.

        Note:
            The query syntax follows Tree-sitter's S-expression format.
            See Tree-sitter documentation for query syntax details.

        See Also:
            :class:`~cldk.analysis.commons.treesitter.models.Captures`:
                The return type for captured nodes.
        """
        framed_query: Query = LANGUAGE.query(query)
        tree = PARSER.parse(bytes(code_to_process, "utf-8"))
        return Captures(framed_query.captures(tree.root_node))

    def get_method_name_from_declaration(self, method_name_string: str) -> str:
        """Get the method name from the method signature."""
        captures: Captures = self.frame_query_and_capture_output("(method_declaration name: (identifier) @method_name)", method_name_string)

        return captures[0].node.text.decode()

    def get_method_name_from_invocation(self, method_invocation: str) -> str:
        """Extract the method name from a method invocation string."""

        captures: Captures = self.frame_query_and_capture_output("(method_invocation name: (identifier) @method_name)", method_invocation)
        return captures[0].node.text.decode()

    def get_identifier_from_arbitrary_statement(self, statement: str) -> str:
        """Get the identifier from an arbitrary statement."""
        captures: Captures = self.frame_query_and_capture_output("(identifier) @identifier", statement)
        return captures[0].node.text.decode()

    def safe_ascend(self, node: Node, ascend_count: int) -> Node:
        """Ascend parent pointers safely in the AST.

        Args:
            node (Node): Starting node.
            ascend_count (int): Levels to ascend.

        Returns:
            Node: Ancestor node after ascending.

        Raises:
            ValueError: If node is None or has no parent.
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
        """Return call targets referenced in a method body.

        Uses simple name resolution over the AST.

        Args:
            method_body (str): Method source.
            declared_methods (dict): Declared methods in the class.

        Returns:
            set[str]: Call target method names.
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
        """Return line numbers where the target method is called in the source method.

        Args:
            source_method_code (str): Source method code.
            target_method_name (str): Target method signature or name.

        Returns:
            list[int]: Line numbers within the source method.
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
        except Exception:
            logger.warning(f"Unable to get calling lines for {target_method_name} in {source_method_code}.")
            return []

        return target_call_lines

    def get_test_methods(self, source_class_code: str) -> Dict[str, str]:
        """Return methods annotated with @Test in a class.

        Args:
            source_class_code (str): Java class source.

        Returns:
            dict[str, str]: Map of method name to body.
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
        """Return methods grouped by annotation.

        Args:
            source_class_code (str): Java class source.
            annotations (list[str]): Annotation names to include.

        Returns:
            dict[str, list[dict]]: Mapping of annotation to list of method info dicts.
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
        """Return all type identifiers referenced in the source.

        Args:
            source_code (str): Java source.

        Returns:
            set[str]: Type identifiers.
        """
        type_references: Captures = self.frame_query_and_capture_output("(type_identifier) @type_id", source_code)
        return {type_id.node.text.decode() for type_id in type_references}

    def get_method_return_type(self, source_code: str) -> str:
        """Return the return type of a method.

        Args:
            source_code (str): Java method source.

        Returns:
            str: Return type identifier.
        """

        type_references: Captures = self.frame_query_and_capture_output("(method_declaration type: ((type_identifier) @type_id))", source_code)

        return type_references[0].node.text.decode()

    def get_lexical_tokens(self, code: str, filter_by_node_type: List[str] | None = None) -> List[str]:
        """Return lexical tokens from the code.

        Args:
            code (str): Java source code.
            filter_by_node_type (list[str] | None): Optional node type filter.

        Returns:
            list[str]: Collected token strings.
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
        """Return source code with all comments removed.

        Args:
            source_code (str): Java source.

        Returns:
            str: Source with comments removed.
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
        """Prettify the pruned code after comment removal.

        Args:
            pruned_code (str): Source after pruning.

        Returns:
            str: Prettified source code.
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
