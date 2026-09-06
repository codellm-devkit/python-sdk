################################################################################
# Copyright IBM Corporation 2026
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

import pytest
from cldk import CLDK
from cldk.analysis.python.python_analysis import PythonAnalysis


def test_c_factory_is_gone():
    assert not hasattr(CLDK, "c")


def test_c_modules_are_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("cldk.analysis.c")
    with pytest.raises(ModuleNotFoundError):
        __import__("cldk.models.c")


def test_legacy_shim_rejects_c():
    with pytest.raises(NotImplementedError):
        CLDK(language="c").analysis(project_path=".")


RAISING = [
    "get_class_hierarchy", "get_service_entry_point_classes",
    "get_service_entry_point_methods", "get_entry_point_classes",
    "get_entry_point_methods", "get_implemented_interfaces",
    "get_methods_with_decorators", "get_test_methods", "get_calling_lines",
    "get_call_targets", "get_all_crud_operations", "get_all_create_operations",
    "get_all_read_operations", "get_all_update_operations", "get_all_delete_operations",
]


@pytest.mark.parametrize("name", RAISING)
def test_stub_accessor_is_gone(name):
    assert not hasattr(PythonAnalysis, name), f"{name} raises unconditionally; it should not exist"


def test_no_public_accessor_only_raises():
    """Every remaining public accessor must do something."""
    import ast
    import inspect
    import textwrap

    for name, fn in inspect.getmembers(PythonAnalysis, inspect.isfunction):
        if name.startswith("_"):
            continue
        # Parse rather than split on ":" — the first colon in ``def f(self, x: int)`` is an
        # annotation, not the signature terminator, so the naive split left the signature (and,
        # worse, the docstring) inside the searched text and would flag a docstring that merely
        # mentions ``raise NotImplementedError``. This is the statements of the body, no more.
        node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        statements = node.body[1:] if ast.get_docstring(node) else node.body
        body = "\n".join(ast.unparse(s) for s in statements)
        assert "raise NotImplementedError" not in body, f"{name} only raises"
