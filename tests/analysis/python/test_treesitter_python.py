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

"""Tree-sitter Python helper tests."""

from tree_sitter import Tree

from cldk.analysis.commons.treesitter import TreesitterPython


def test_is_parsable_accepts_valid_code():
    assert TreesitterPython().is_parsable("def f(): return 1")


def test_is_parsable_rejects_invalid_code():
    assert not TreesitterPython().is_parsable("def f(): pass if")


def test_get_raw_ast_returns_tree():
    ast = TreesitterPython().get_raw_ast("x = 1")
    assert isinstance(ast, Tree)
