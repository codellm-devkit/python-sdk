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

"""The Java backend contract: both backends implement the same ABC, and that ABC is the generic
cross-language :class:`AnalysisBackend` (introspection only — no analyzer run, no live Neo4j)."""

import inspect
import re
from pathlib import Path

import pytest

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.neo4j import JNeo4jBackend

BACKENDS = [JCodeanalyzer, JNeo4jBackend]
GENERIC_METHODS = sorted(AnalysisBackend.__abstractmethods__)


def test_java_contract_parameterises_the_generic_abc():
    assert issubclass(JavaAnalysisBackend, AnalysisBackend)
    assert JavaAnalysisBackend.P == "J"
    assert JavaAnalysisBackend.N == "J"


def test_generic_methods_are_all_abstract_on_the_java_contract():
    """Inheriting the generic ABC must not quietly satisfy any of its methods with a stub."""
    assert set(GENERIC_METHODS) <= JavaAnalysisBackend.__abstractmethods__


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_subclass_the_contract(backend):
    assert issubclass(backend, JavaAnalysisBackend)


def test_contract_is_abstract():
    with pytest.raises(TypeError):
        JavaAnalysisBackend()


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_fully_implement_the_contract(backend):
    # No abstract methods left unimplemented — generic and Java-only alike.
    assert backend.__abstractmethods__ == frozenset()


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", GENERIC_METHODS)
def test_backend_implements_every_generic_method(backend, name):
    impl = getattr(backend, name, None)
    assert impl is not None and not getattr(impl, "__isabstractmethod__", False), f"{backend.__name__}.{name} is not implemented"


@pytest.mark.parametrize("backend", BACKENDS)
def test_signatures_match_the_contract(backend):
    """Every abstract method's parameters and defaults are preserved by each backend. Return
    annotations are not compared: the generic ABC's are type variables (``Dict[str, TypeT]``) that
    the Java backends narrow to concrete models."""
    for name, base_method in inspect.getmembers(JavaAnalysisBackend, predicate=inspect.isfunction):
        if getattr(base_method, "__isabstractmethod__", False):
            base_sig = inspect.signature(base_method).replace(return_annotation=inspect.Signature.empty)
            impl_sig = inspect.signature(getattr(backend, name)).replace(return_annotation=inspect.Signature.empty)
            assert impl_sig == base_sig, f"{backend.__name__}.{name} signature drifted: {impl_sig} != {base_sig}"


def test_contract_covers_every_method_the_facade_delegates():
    """Every ``self.backend.X`` the JavaAnalysis facade calls must be on the contract."""
    facade_src = (Path(__file__).resolve().parents[3] / "cldk" / "analysis" / "java" / "java_analysis.py").read_text()
    delegated = set(re.findall(r"self\.backend\.([a-zA-Z_]+)", facade_src))
    contract = {n for n in dir(JavaAnalysisBackend) if not n.startswith("__")}
    missing = delegated - contract
    assert not missing, f"facade delegates to backend methods absent from the contract: {sorted(missing)}"
