# cldk/graph/engine.py
from __future__ import annotations
from typing import Iterable, List, Optional, Tuple
import networkx as nx
from cldk.graph.provider import ProgramGraphProvider, resolve_vertex
from cldk.graph.capability import require
from cldk.graph.result import SliceResult, FlowResult, FlowPath


def _filter_edges(g: nx.DiGraph, families: Iterable[str]) -> nx.DiGraph:
    fam = set(families)
    out = nx.DiGraph()
    out.add_nodes_from(g.nodes(data=True))
    for u, v, d in g.edges(data=True):
        if d.get("family") in fam:
            out.add_edge(u, v, **d)
    return out


class Engine:
    def __init__(self, provider: ProgramGraphProvider):
        self.p = provider

    def _evidence(self, uris, seeds, roles=None):
        roles = roles or {}
        ev = []
        for u in uris:
            fl, code = self.p.source_slice(u)
            ev.append({"uri": u, "file_line": fl, "code": code,
                       "role": "seed" if u in seeds else roles.get(u, "def")})
        return ev

    def _intra(self, seed, edges, backward, strict, what) -> SliceResult:
        note = require(3, self.p, strict=strict, what=what)
        seeds = resolve_vertex(self.p, seed)
        cal = self.p.callable_of(seeds[0])
        g = _filter_edges(self.p.program_graph(cal), edges)
        walk = g.reverse(copy=False) if backward else g
        reached = set(seeds)
        for s in seeds:
            if s in walk:
                reached |= nx.descendants(walk, s)
        sub = g.subgraph(reached).copy()
        explain = {"seed": seeds, "direction": "backward" if backward else "forward",
                   "edges": list(edges), "level": self.p.max_level(),
                   "vertices": len(reached), "interprocedural": False}
        if note:
            explain["degraded"] = note
        return SliceResult(subgraph=sub, evidence=self._evidence(reached, set(seeds)),
                           _explain=explain)

    def slice_backward(self, seed, *, edges=("cfg", "cdg", "ddg"),
                       interprocedural: Optional[bool] = None, strict: bool = False) -> SliceResult:
        return self._intra(seed, edges, backward=True, strict=strict, what="slice_backward")

    def slice_forward(self, seed, *, edges=("cfg", "cdg", "ddg"),
                      interprocedural: Optional[bool] = None, strict: bool = False) -> SliceResult:
        return self._intra(seed, edges, backward=False, strict=strict, what="slice_forward")

    def control_deps(self, seed, *, strict: bool = False) -> SliceResult:
        return self._intra(seed, ("cdg",), backward=True, strict=strict, what="control_deps")
