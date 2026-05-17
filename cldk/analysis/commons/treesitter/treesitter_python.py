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

"""Python Tree-sitter helpers module.

This module provides lightweight wrappers around the Tree-sitter Python grammar
for parsing Python source code and performing syntactic analysis. It serves as
the foundational parsing layer for Python code analysis in CLDK.

The module provides:
    - **Syntax validation**: Check if Python code parses without errors
    - **AST generation**: Parse code into Tree-sitter AST for traversal

Note:
    Symbol-table extraction and class/method analysis now live in the
    ``codeanalyzer-python`` backend. This module is kept for source-level
    parsing utilities that don't require semantic analysis.

See Also:
    - :class:`~cldk.analysis.python.PythonAnalysis`: High-level Python analysis.
    - :class:`TreesitterJava`: Equivalent for Java parsing.
"""

from tree_sitter import Language, Parser, Tree
import tree_sitter_python as tspython

LANGUAGE: Language = Language(tspython.language())
"""The Tree-sitter Language object for Python grammar."""

PARSER: Parser = Parser(LANGUAGE)
"""Global Tree-sitter parser instance configured for Python."""


class TreesitterPython:
    """Tree-sitter helper class for Python source code parsing.

    This class provides utility methods for parsing Python source code using
    Tree-sitter. It offers syntax validation and raw AST generation for
    further analysis.

    The class is stateless and thread-safe - it uses module-level parser
    and language objects.
    """

    def is_parsable(self, code: str) -> bool:
        """Check if the given code is syntactically valid Python.

        Parses the code using Tree-sitter and recursively checks for ERROR
        nodes in the resulting AST. Returns ``True`` only if the entire
        code parses without syntax errors.

        Args:
            code: A string containing Python source code to validate.
                Can be a complete module, a function, a class, or any
                valid Python code fragment.

        Returns:
            ``True`` if the code parses without syntax errors, ``False``
            otherwise. Also returns ``False`` if parsing triggers a
            RecursionError (for extremely nested code).

        Note:
            This only checks syntactic validity, not semantic correctness.
            Code with undefined variables or type errors will still be
            considered "parsable".

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
            except RecursionError as err:
                print(err)
                return True
            return False

        tree = PARSER.parse(bytes(code, "utf-8"))
        if tree is not None:
            return not syntax_error(tree.root_node)
        return False

    def get_raw_ast(self, code: str) -> Tree:
        """Parse code and return the Tree-sitter AST.

        Parses the provided Python source code using Tree-sitter and returns
        the resulting abstract syntax tree. The AST can be traversed to
        extract syntactic information about the code structure.

        Args:
            code: A string containing Python source code to parse.

        Returns:
            A Tree-sitter ``Tree`` object representing the parsed AST. The
            tree's ``root_node`` provides access to the entire syntax tree:
                - ``root_node.type``: Always ``"module"`` for valid Python
                - ``root_node.children``: Top-level statements
                - ``root_node.text``: Original source bytes

        Note:
            If the source code contains syntax errors, Tree-sitter will still
            return a tree but with ERROR nodes at the locations of parse errors.
            Use :meth:`is_parsable` to check for valid syntax first.

        See Also:
            :meth:`is_parsable`: To validate syntax before parsing.
        """
        return PARSER.parse(bytes(code, "utf-8"))
