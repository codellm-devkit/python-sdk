from __future__ import annotations
import re
from abc import ABC, abstractmethod
from typing import List, Tuple, Iterable, Optional, Any
import networkx as nx

_LOC = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?$")


class ProgramGraphProvider(ABC):
    """The per-backend data seam the shared engine consumes. Implemented by local backends
    (from cpg models) and Neo4j backends (from Cypher). Traversal lives in the engine, not here."""

    @abstractmethod
    def program_graph(self, callable_uri: str) -> nx.DiGraph: ...
    @abstractmethod
    def sdg_edges(self) -> Iterable[Any]: ...
    @abstractmethod
    def resolve_location(self, file: str, line: int, col: Optional[int] = None) -> List[str]: ...
    @abstractmethod
    def source_slice(self, vertex_uri: str) -> Tuple[Optional[str], Optional[str]]: ...
    @abstractmethod
    def callable_of(self, vertex_uri: str) -> Optional[str]: ...
    @abstractmethod
    def max_level(self) -> int: ...


def resolve_vertex(provider: ProgramGraphProvider, seed: Any) -> List[str]:
    """Normalize a polymorphic seed to vertex ids: a BodyNode-like object (has .id), a can:// id
    string, or a 'file:line[:col]' location string."""
    if hasattr(seed, "id"):
        return [seed.id]
    if isinstance(seed, str):
        if seed.startswith("can://"):
            return [seed]
        m = _LOC.match(seed)
        if m:
            col = int(m["col"]) if m["col"] is not None else None
            return provider.resolve_location(m["file"], int(m["line"]), col)
    raise ValueError(f"cannot resolve seed to a vertex: {seed!r} "
                     f"(expected 'file:line[:col]', a can:// id, or a body node)")
