from __future__ import annotations
from typing import Tuple
from cldk.models.cpg.base import _NullSafeBase


class Span(_NullSafeBase):
    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]
