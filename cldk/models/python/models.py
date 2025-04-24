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
Models module
"""

from typing import List
from pydantic import BaseModel


class PyArg(BaseModel):
    arg_name: str
    arg_type: str


class PyImport(BaseModel):
    from_statement: str
    # code_body: str
    imports: List[str]


class PyCallSite(BaseModel):
    method_name: str
    declaring_object: str
    arguments: List[str]
    start_line: int
    start_column: int
    end_line: int
    end_column: int


class PyMethod(BaseModel):
    code_body: str
    method_name: str
    full_signature: str
    num_params: int
    modifier: str
    is_constructor: bool
    is_static: bool
    formal_params: List[PyArg]
    call_sites: List[PyCallSite]
    return_type: str
    class_signature: str
    start_line: int
    end_line: int


class PyClass(BaseModel):
    code_body: str
    full_signature: str
    super_classes: List[str]
    is_test_class: bool
    class_name: str = None
    methods: List[PyMethod]


class PyModule(BaseModel):
    qualified_name: str
    functions: List[PyMethod]
    classes: List[PyClass]
    imports: List[PyImport]
    # expressions: str


class PyBuildAttributes(BaseModel):
    """Handles all the project build tool (requirements.txt/poetry/setup.py) attributes"""


class PyConfig(BaseModel):
    """Application configuration information"""
