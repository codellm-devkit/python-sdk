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

"""The generic cross-language AnalysisBackend contract (introspection only).

Abstract-method completeness (every concrete backend leaves ``__abstractmethods__`` empty) is
owned by ``tests/analysis/python/test_python_backend_contract.py::test_backend_fully_implements_contract``
— don't re-add that check here.
"""

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.python.backend import PythonAnalysisBackend
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend


def test_python_backend_parameterises_the_generic_abc():
    assert issubclass(PythonAnalysisBackend, AnalysisBackend)


def test_prefixes_declared():
    assert PyNeo4jBackend.P == "PY"
    assert PyNeo4jBackend.N == "Py"
