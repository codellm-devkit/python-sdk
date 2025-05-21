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
Python Tests
"""
import os
from typing import List
from tree_sitter import Tree
import pytest

from cldk.analysis.python import PythonAnalysis
from cldk.models.python.models import PyClass, PyImport, PyMethod, PyModule

PYTHON_CODE = """
import os
from typing import List
from math import *

def env(env_var: str): -> str
  return os.getenv(env_var)

class Calculator():
  '''Calculator Class'''

  def __init__(self):
    self._total = 0

  @property
  def total(self):
    return self._total

  @total.setter
  def total(self, value):
    self._total = value

  def add(self, a, b):
    total += a + b
    return a + b

  def subtract(self, a, b):
    total += a - b
    return a - b

  def multiply(self, a, b):
    total += (a * b)
    return a * b

  def divide(self, a, b):
    total += (a / b)
    return a / b
"""


def test_get_methods():
    """Should return all of the methods"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    all_methods = python_analysis.get_methods()
    assert all_methods is not None
    assert isinstance(all_methods, List)
    assert len(all_methods) == 7
    for method in all_methods:
        assert isinstance(method, PyMethod)


def test_get_functions():
    """Should return all of the functions"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    all_functions = python_analysis.get_functions()
    assert all_functions is not None
    assert isinstance(all_functions, List)
    assert len(all_functions) == 1
    for method in all_functions:
        assert isinstance(method, PyMethod)


def test_get_all_modules(tmp_path):
    """Should return all of the modules"""
    python_analysis = PythonAnalysis(project_dir=tmp_path, source_code=None)

    # set up some temporary modules
    temp_file_path = os.path.join(tmp_path, "hello.py")
    with open(temp_file_path, "w", encoding="utf-8") as hello_module:
        hello_module.write('print("Hello, world!")')
    temp_file_path = os.path.join(tmp_path, "bye.py")
    with open(temp_file_path, "w", encoding="utf-8") as bye_module:
        bye_module.write('print("Goodbye, world!")')

    all_modules = python_analysis.get_modules()
    assert all_modules is not None
    assert isinstance(all_modules, List)
    assert len(all_modules) == 2
    for module in all_modules:
        assert isinstance(module, PyModule)


def test_get_method_details():
    """Should return the method details"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    method_details = python_analysis.get_method_details("add(self, a, b)")
    assert method_details is not None
    assert isinstance(method_details, PyMethod)
    assert method_details.full_signature == "add(self, a, b)"


def test_is_parsable():
    """Should be able to parse the code"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    code = "def is_parsable(self, code: str) -> bool: return True"
    is_parsable = python_analysis.is_parsable(code)
    assert is_parsable is True

    code = "def is_not_parsable(self, code: str) -> bool: return True if True else"
    is_parsable = python_analysis.is_parsable(code)
    assert is_parsable is False


def test_get_raw_ast():
    """Should return the raw AST"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    raw_ast = python_analysis.get_raw_ast(PYTHON_CODE)
    assert raw_ast is not None
    assert isinstance(raw_ast, Tree)
    assert raw_ast.root_node is not None


def test_get_imports():
    """Should return all of the imports"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    all_imports = python_analysis.get_imports()
    assert all_imports is not None
    assert isinstance(all_imports, List)
    assert len(all_imports) == 3
    for py_import in all_imports:
        assert isinstance(py_import, PyImport)


def test_get_variables():
    """Should return all of the variables"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_variables()
    assert except_info.type == NotImplementedError


def test_get_classes():
    """Should return all of the classes"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    all_classes = python_analysis.get_classes()
    assert all_classes is not None
    assert isinstance(all_classes, List)
    assert len(all_classes) == 1
    assert isinstance(all_classes[0], PyClass)
    assert all_classes[0].class_name == "Calculator"
    assert len(all_classes[0].methods) == 7


def test_get_classes_by_criteria():
    """Should return all of the classes that match the criteria"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_classes_by_criteria()
    assert except_info.type == NotImplementedError


def test_get_sub_classes():
    """Should return all of the subclasses"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_sub_classes()
    assert except_info.type == NotImplementedError


def test_get_nested_classes():
    """Should return all of the nested classes"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_nested_classes()
    assert except_info.type == NotImplementedError


def test_get_constructors():
    """Should return all of the constructors"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_constructors()
    assert except_info.type == NotImplementedError


def test_get_methods_in_class():
    """Should return all of the methods in the class"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_methods_in_class()
    assert except_info.type == NotImplementedError


def test_get_fields():
    """Should return all of the fields in the class"""
    python_analysis = PythonAnalysis(project_dir=None, source_code=PYTHON_CODE)

    with pytest.raises(NotImplementedError) as except_info:
        python_analysis.get_fields()
    assert except_info.type == NotImplementedError
