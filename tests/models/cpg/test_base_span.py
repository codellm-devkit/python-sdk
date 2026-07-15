from cldk.models.cpg.models import Span
from cldk.models.cpg.base import _NullSafeBase
from pydantic import ConfigDict
from typing import Dict, List


class _M(_NullSafeBase):
    xs: List[int] = []
    d: Dict[str, int] = {}
    opt: int | None = None


def test_null_collections_coerce_to_defaults():
    m = _M(**{"xs": None, "d": None, "opt": None})
    assert m.xs == [] and m.d == {} and m.opt is None


def test_extra_fields_are_allowed_and_preserved():
    m = _M(**{"xs": [1], "is_tsx": True})       # language-specific extra
    assert m.xs == [1]
    assert m.model_extra.get("is_tsx") is True


def test_span_parses_byte_offsets():
    s = Span(**{"start": [1, 0], "end": [4, 2], "bytes": [0, 40]})
    assert s.start == (1, 0) and s.end == (4, 2) and s.bytes == (0, 40)
