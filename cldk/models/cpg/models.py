from __future__ import annotations
from typing import List, Optional, Tuple
from cldk.models.cpg.base import _NullSafeBase


class Span(_NullSafeBase):
    start: Tuple[int, int]
    end: Tuple[int, int]
    bytes: Tuple[int, int]


class Edge(_NullSafeBase):
    src: str
    dst: str
    kind: Optional[str] = None      # cfg edge kind; absent on identity edges
    var: Optional[str] = None       # ddg access path
    prov: List[str] = []            # ["jedi"|"pycg"|"tsc"|"jelly"] (call) / ["ssa"|"points-to"] (ddg)
    weight: int = 1


class Import(_NullSafeBase):
    name: str
    path: Optional[str] = None
    alias: Optional[str] = None
    span: Optional[Span] = None
