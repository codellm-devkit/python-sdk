# tests/analysis/commons/test_taint_semantics.py
from cldk.analysis.commons.results import Diagnostic, TaintResult


def _pair(a="a", b="b"):
    return (a, b)


def test_empty_and_complete_is_an_exhausted_pair():
    """The verdict table's middle row: nothing found, and the search was whole."""
    r = TaintResult(paths=[], complete=True, exhausted=[_pair()], roots=[], resolved="", unresolved=[])
    assert not r                      # BoundedResult makes the payload's truth the list's
    assert _pair() in r.exhausted


def test_empty_and_incomplete_is_not_exhausted():
    """The bottom row: nothing found, but something stopped the search, so no pair may be listed."""
    d = Diagnostic(code="unresolved_dispatch", message="x")
    r = TaintResult(paths=[], complete=False, exhausted=[], roots=[], resolved="", unresolved=[d])
    assert not r
    assert r.exhausted == []


def test_a_result_behaves_as_its_path_list():
    """Inherited from BoundedResult: `for p in result` and `len(result)` are the paths, not fields."""
    r = TaintResult(paths=[], complete=True, exhausted=[], roots=[], resolved="", unresolved=[])
    assert list(r) == [] and len(r) == 0


def test_exhausted_survives_a_round_trip():
    """A triage caller writes the result to JSON and another process reads the verdict back."""
    r = TaintResult(paths=[], complete=True, exhausted=[_pair()], roots=[], resolved="", unresolved=[])
    assert TaintResult.model_validate(r.model_dump()).exhausted == [_pair()]
