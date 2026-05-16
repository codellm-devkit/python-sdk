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

"""Python tree-sitter helpers.

Lightweight wrappers around the tree-sitter Python grammar. Symbol-table
and class/method extraction now live in the ``codeanalyzer-python``
backend; this module is kept for source-level parsing utilities used by
:class:`PythonAnalysis`.
"""

from tree_sitter import Language, Parser, Tree
import tree_sitter_python as tspython

LANGUAGE: Language = Language(tspython.language())
PARSER: Parser = Parser(LANGUAGE)


class TreesitterPython:
    """Tree-sitter helpers for Python."""

    def is_parsable(self, code: str) -> bool:
        """Return True when ``code`` parses as Python without syntax errors."""

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
        """Return the raw tree-sitter AST for ``code``."""
        return PARSER.parse(bytes(code, "utf-8"))
