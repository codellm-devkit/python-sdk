import pytest
from cldk.graph.capability import require, CapabilityError


class P:
    def __init__(self, lvl): self._l = lvl
    def max_level(self): return self._l


def test_satisfied_returns_none():
    assert require(3, P(4), strict=False, what="slice_backward") is None


def test_degrade_returns_note():
    note = require(4, P(3), strict=False, what="interprocedural flows_to")
    assert note["requested"] == 4 and note["available"] == 3
    assert "UNKNOWN, not safety" in note["gap"]


def test_strict_raises():
    with pytest.raises(CapabilityError) as e:
        require(4, P(3), strict=True, what="flows_to")
    assert "level 4" in str(e.value)
