"""The Go backend contract: both backends implement the same ABC, that ABC parameterises the
generic cross-language :class:`AnalysisBackend`, and the Go-specific accessors are present on
both. No binary and no live Neo4j needed."""

import inspect

import pytest

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.go.backend import GoAnalysisBackend
from cldk.analysis.go.codeanalyzer.codeanalyzer import GoCodeanalyzer
from cldk.analysis.go.neo4j.neo4j_backend import GoNeo4jBackend

BACKENDS = [GoCodeanalyzer, GoNeo4jBackend]
GENERIC_METHODS = sorted(AnalysisBackend.__abstractmethods__)
#: The accessors the Go contract adds on top of the generic ABC (SDK1/SDK2/SDK3 leaf surface).
GO_METHODS = ["get_functions", "get_callers", "get_callees", "get_source", "get_call_graph_json"]


def test_go_contract_parameterises_the_generic_abc():
    assert issubclass(GoAnalysisBackend, AnalysisBackend)
    assert GoAnalysisBackend.N == "Go"
    assert GoAnalysisBackend.P == "GO_"


def test_generic_methods_are_all_abstract_on_the_go_contract():
    """Inheriting the generic ABC must not quietly satisfy any of its methods with a stub."""
    assert set(GENERIC_METHODS) <= GoAnalysisBackend.__abstractmethods__


def test_go_adds_its_own_abstract_methods():
    for name in GO_METHODS:
        m = getattr(GoAnalysisBackend, name)
        assert getattr(m, "__isabstractmethod__", False), f"{name} should be abstract on the contract"


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_subclass_the_contract(backend):
    assert issubclass(backend, GoAnalysisBackend)


def test_contract_is_abstract():
    with pytest.raises(TypeError):
        GoAnalysisBackend()


@pytest.mark.parametrize("backend", BACKENDS)
def test_backends_fully_implement_the_contract(backend):
    """No abstract methods left unimplemented — generic and Go-specific alike. This is the gate
    'GoAnalysisBackend instantiates' reduced to the class level."""
    assert backend.__abstractmethods__ == frozenset()


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", GENERIC_METHODS + GO_METHODS)
def test_backend_implements_every_method(backend, name):
    impl = getattr(backend, name, None)
    assert impl is not None and not getattr(impl, "__isabstractmethod__", False), f"{backend.__name__}.{name} is not implemented"


@pytest.mark.parametrize("backend", BACKENDS)
def test_signatures_match_the_contract(backend):
    """Every abstract method's parameters and defaults are preserved by each backend. Return
    annotations are not compared: the generic ABC's are type variables the Go backends narrow."""
    for name, base_method in inspect.getmembers(GoAnalysisBackend, predicate=inspect.isfunction):
        if getattr(base_method, "__isabstractmethod__", False):
            base_sig = inspect.signature(base_method).replace(return_annotation=inspect.Signature.empty)
            impl_sig = inspect.signature(getattr(backend, name)).replace(return_annotation=inspect.Signature.empty)
            assert impl_sig == base_sig, f"{backend.__name__}.{name} signature drifted: {impl_sig} != {base_sig}"
