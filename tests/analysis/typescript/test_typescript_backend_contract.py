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

"""The TypeScript backend contract: both backends implement the same ABC, and that ABC is the
generic cross-language :class:`AnalysisBackend` (no live Neo4j needed)."""

import inspect

import pytest

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.codeanalyzer.codeanalyzer import TSCodeanalyzer
from cldk.analysis.typescript.neo4j import TSNeo4jBackend

BACKENDS = [TSCodeanalyzer, TSNeo4jBackend]
GENERIC_METHODS = sorted(AnalysisBackend.__abstractmethods__)


def test_typescript_contract_parameterises_the_generic_abc():
    assert issubclass(TSAnalysisBackend, AnalysisBackend)
    assert TSAnalysisBackend.P == "TS"
    assert TSAnalysisBackend.N == "TS"


def test_generic_methods_are_all_abstract_on_the_typescript_contract():
    """Inheriting the generic ABC must not quietly satisfy any of its methods with a stub."""
    assert set(GENERIC_METHODS) <= TSAnalysisBackend.__abstractmethods__


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_subclass_the_contract(backend):
    assert issubclass(backend, TSAnalysisBackend)


def test_contract_is_abstract():
    with pytest.raises(TypeError):
        TSAnalysisBackend()


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_fully_implement_the_contract(backend):
    # No abstract methods left unimplemented — generic and TypeScript-only alike.
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
    the TypeScript backends narrow to concrete models."""
    for name, base_method in inspect.getmembers(TSAnalysisBackend, predicate=inspect.isfunction):
        if getattr(base_method, "__isabstractmethod__", False):
            base_sig = inspect.signature(base_method).replace(return_annotation=inspect.Signature.empty)
            impl_sig = inspect.signature(getattr(backend, name)).replace(return_annotation=inspect.Signature.empty)
            assert impl_sig == base_sig, f"{backend.__name__}.{name} signature drifted: {impl_sig} != {base_sig}"
