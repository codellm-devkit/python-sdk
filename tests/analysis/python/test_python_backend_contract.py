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

"""The Python analysis backend contract (introspection only — no analyzer run needed)."""

import re
from pathlib import Path

import pytest

from cldk.analysis.python.backend import PythonAnalysisBackend
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend

# Both interchangeable backends must satisfy the same contract.
BACKENDS = [PyCodeanalyzer, PyNeo4jBackend]


@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_subclasses_contract(backend):
    assert issubclass(backend, PythonAnalysisBackend)


def test_contract_is_abstract():
    with pytest.raises(TypeError):
        PythonAnalysisBackend()


@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_fully_implements_contract(backend):
    assert backend.__abstractmethods__ == frozenset()


def test_contract_covers_every_method_the_facade_delegates():
    """Every ``self.backend.X`` the PythonAnalysis facade calls must be on the contract."""
    facade_src = (Path(__file__).resolve().parents[3] / "cldk" / "analysis" / "python" / "python_analysis.py").read_text()
    delegated = set(re.findall(r"self\.backend\.([a-zA-Z_]+)", facade_src))
    contract = {n for n in dir(PythonAnalysisBackend) if not n.startswith("__")}
    missing = delegated - contract
    assert not missing, f"facade delegates to backend methods absent from the contract: {sorted(missing)}"
