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

"""Python schema models.

Re-exports the canonical Python analysis schema from ``codeanalyzer-python``
so CLDK and the analyzer backend share a single source of truth for the
data model.
"""

from codeanalyzer.schema.py_schema import (
    PyApplication,
    PyCallEdge,
    PyCallable,
    PyCallableParameter,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyImport,
    PyModule,
    PySymbol,
    PyVariableDeclaration,
)

from .projections import PyCallableOverview

__all__ = [
    "PyApplication",
    "PyCallEdge",
    "PyCallable",
    "PyCallableOverview",
    "PyCallableParameter",
    "PyCallsite",
    "PyClass",
    "PyClassAttribute",
    "PyComment",
    "PyImport",
    "PyModule",
    "PySymbol",
    "PyVariableDeclaration",
]
