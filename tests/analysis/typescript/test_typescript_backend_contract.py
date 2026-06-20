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

"""The TypeScript backend contract: both backends implement the same ABC (no live Neo4j needed)."""

import inspect

import pytest

from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.codeanalyzer.codeanalyzer import TSCodeanalyzer
from cldk.analysis.typescript.neo4j import TSNeo4jBackend


def test_backends_subclass_the_contract():
    assert issubclass(TSCodeanalyzer, TSAnalysisBackend)
    assert issubclass(TSNeo4jBackend, TSAnalysisBackend)


def test_contract_is_abstract():
    with pytest.raises(TypeError):
        TSAnalysisBackend()


@pytest.mark.parametrize("backend", [TSCodeanalyzer, TSNeo4jBackend])
def test_backends_fully_implement_the_contract(backend):
    # No abstract methods left unimplemented ⇒ the class is concrete/instantiable.
    assert backend.__abstractmethods__ == frozenset()


@pytest.mark.parametrize("backend", [TSCodeanalyzer, TSNeo4jBackend])
def test_signatures_match_the_contract(backend):
    """Every abstract method's signature is preserved by each backend (params + defaults)."""
    for name, base_method in inspect.getmembers(TSAnalysisBackend, predicate=inspect.isfunction):
        if getattr(base_method, "__isabstractmethod__", False):
            base_sig = inspect.signature(base_method)
            impl_sig = inspect.signature(getattr(backend, name))
            assert impl_sig == base_sig, f"{backend.__name__}.{name} signature drifted: {impl_sig} != {base_sig}"
