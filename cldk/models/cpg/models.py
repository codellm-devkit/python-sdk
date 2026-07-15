from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
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


class Node(_NullSafeBase):
    id: Optional[str] = None        # absent on body-node facets (keyed by position/tag instead)
    kind: str
    span: Optional[Span] = None
    parent: Optional[str] = None
    # type facet
    base_types: List[str] = []
    interfaces: List[str] = []
    modifiers: List[str] = []
    decorators: List[Any] = []
    callables: Dict[str, "Node"] = {}
    fields: Dict[str, "Node"] = {}
    # callable facet
    signature: Optional[str] = None
    parameters: List[Any] = []
    return_type: Optional[str] = None
    error_channel: List[str] = []
    metrics: Dict[str, Any] = {}
    refs: Dict[str, Any] = {}
    body: Dict[str, "Node"] = {}
    cfg: List[Edge] = []
    cdg: List[Edge] = []
    ddg: List[Edge] = []
    summary: List[Edge] = []
    # field / body-node facet
    type: Optional[str] = None
    callee: Optional[str] = None
    arguments: List[str] = []
    of: Optional[str] = None
    # open vocab
    tags: Dict[str, str] = {}


class Module(_NullSafeBase):
    id: str
    kind: str = "module"
    package: Optional[str] = None
    source: str = ""
    imports: List[Import] = []
    types: Dict[str, Node] = {}
    functions: Dict[str, Node] = {}
    content_hash: Optional[str] = None


class Application(_NullSafeBase):
    id: str
    kind: str = "application"
    symbol_table: Dict[str, Module] = {}
    call_graph: List[Edge] = []
    param_in: List[Edge] = []
    param_out: List[Edge] = []


class Analyzer(_NullSafeBase):
    name: str
    version: Optional[str] = None


class AnalysisPayload(_NullSafeBase):
    schema_version: str
    language: str
    max_level: int
    k_limit: Optional[int] = None
    analyzer: Optional[Analyzer] = None
    application: Application
