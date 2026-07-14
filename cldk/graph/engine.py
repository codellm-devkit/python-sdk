# cldk/graph/engine.py
from __future__ import annotations
from typing import Iterable, List, Optional, Tuple
import networkx as nx
from cldk.graph.provider import ProgramGraphProvider, resolve_vertex
from cldk.graph.capability import require
from cldk.graph.result import SliceResult, FlowResult, FlowPath


def _filter_edges(g: nx.MultiDiGraph, families: Iterable[str]) -> nx.MultiDiGraph:
    fam = set(families)
    out = nx.MultiDiGraph()
    out.add_nodes_from(g.nodes(data=True))
    for u, v, k, d in g.edges(keys=True, data=True):
        if d.get("family") in fam:
            out.add_edge(u, v, key=k, **d)
    return out


_TIER_RANK = {"unresolved": 0, "structural": 1, "resolved": 2}
_RANK_TIER = {v: k for k, v in _TIER_RANK.items()}


def _ddg_tier(prov) -> str:
    if prov == ["points-to"]:
        return "resolved"
    if prov == ["ssa"]:
        return "structural"
    return "unresolved"


class Engine:
    def __init__(self, provider: ProgramGraphProvider):
        self.p = provider

    def _evidence(self, uris, seeds, roles=None, default_role="def"):
        # Seeds are always "seed"; other vertices take the verb's default_role
        # (slices/flows: "def", control_deps: "control", def_use: "use").
        roles = roles or {}
        ev = []
        for u in uris:
            fl, code = self.p.source_slice(u)
            ev.append({"uri": u, "file_line": fl, "code": code,
                       "role": "seed" if u in seeds else roles.get(u, default_role)})
        return ev

    def _intra(self, seed, edges, backward, strict, what, interprocedural=None,
               default_role="def") -> SliceResult:
        note = require(3, self.p, strict=strict, what=what)
        want_inter = interprocedural if interprocedural is not None else (self.p.max_level() >= 4)
        if interprocedural is True:
            inter_note = require(4, self.p, strict=strict, what=f"interprocedural {what}")
            if inter_note:
                note = inter_note
                want_inter = False
        # Only dataflow (param_in/param_out/summary) crosses callable boundaries, so the
        # sdg overlay is additionally gated on the ddg family being requested: a cfg- or
        # cdg-only slice never crosses, even on an L4 backend. want_inter is the single
        # source of truth — it both gates the overlay and feeds explain()["interprocedural"].
        want_inter = want_inter and self.p.max_level() >= 4 and "ddg" in set(edges)
        seeds = resolve_vertex(self.p, seed)
        g = _filter_edges(self.p.program_graph(self.p.callable_of(seeds[0])), edges)
        if want_inter:
            for e in self.p.sdg_edges():
                g.add_edge(e.src, e.dst, family="sdg", kind=getattr(e, "kind", None),
                           var=getattr(e, "var", None), prov=getattr(e, "prov", []))
        walk = g.reverse(copy=False) if backward else g
        reached = set(seeds)
        for s in seeds:
            if s in walk:
                reached |= nx.descendants(walk, s)
        # seed-consistency (Task 4 fix, carried here): evidence/uris must equal subgraph nodes,
        # and a seed is trivially in its own slice.
        sub = g.subgraph(reached & set(g.nodes())).copy()   # MultiDiGraph
        for s in seeds:
            if s not in sub:
                sub.add_node(s, kind="seed")
        ev_nodes = sorted(sub.nodes())   # deterministic; evidence set == subgraph nodes
        explain = {"seed": seeds, "direction": "backward" if backward else "forward",
                   "edges": list(edges), "level": self.p.max_level(),
                   "vertices": len(sub), "interprocedural": bool(want_inter)}
        if note:
            explain["degraded"] = note
        return SliceResult(subgraph=sub,
                           evidence=self._evidence(ev_nodes, set(seeds),
                                                   default_role=default_role),
                           _explain=explain)

    def slice_backward(self, seed, *, edges=("cfg", "cdg", "ddg"),
                       interprocedural: Optional[bool] = None, strict: bool = False) -> SliceResult:
        return self._intra(seed, edges, True, strict, "slice_backward", interprocedural)

    def slice_forward(self, seed, *, edges=("cfg", "cdg", "ddg"),
                      interprocedural: Optional[bool] = None, strict: bool = False) -> SliceResult:
        return self._intra(seed, edges, False, strict, "slice_forward", interprocedural)

    def control_deps(self, seed, *, strict: bool = False) -> SliceResult:
        # Control dependence is intraprocedural in this model — only dataflow (param/summary)
        # crosses boundaries. Force interprocedural=False so the sdg overlay is never merged
        # into a pure CDG slice, even on an L4 backend.
        return self._intra(seed, ("cdg",), backward=True, strict=strict,
                           what="control_deps", interprocedural=False,
                           default_role="control")

    def _dataflow_graph(self, *callable_uris) -> nx.MultiDiGraph:
        # Union of the given callables' intra ddg graphs, plus the summary/param_*
        # (inter) sdg overlay at L4; below L4 this is intraprocedural ddg only.
        g = nx.MultiDiGraph()
        for c in dict.fromkeys(callable_uris):          # dedupe, keep order
            cg = _filter_edges(self.p.program_graph(c), ("ddg",))
            g.add_nodes_from(cg.nodes(data=True))
            for u, v, k, d in cg.edges(keys=True, data=True):
                g.add_edge(u, v, key=k, **d)
        if self.p.max_level() >= 4:
            for e in self.p.sdg_edges():
                g.add_edge(e.src, e.dst, family="sdg", kind=getattr(e, "kind", None),
                           var=getattr(e, "var", None), prov=getattr(e, "prov", []))
        return g

    def flows_to(self, source_seed, sink_seed, *, strict: bool = False) -> FlowResult:
        # Full flows_to semantics are interprocedural (ddg + param_in/param_out/summary),
        # which is L4. Below that, non-strict degrades honestly: the note is attached and
        # the intra-only ddg witnesses that CAN be computed are still returned.
        note = require(4, self.p, strict=strict, what="flows_to")
        src = resolve_vertex(self.p, source_seed)[0]
        dst = resolve_vertex(self.p, sink_seed)[0]
        # A sink in a different callable is reachable via param_in/param_out/summary,
        # so the dataflow graph must span BOTH endpoint callables. (Multi-hop flows
        # through a THIRD callable's interior need the whole-program graph — deferred.)
        g = self._dataflow_graph(self.p.callable_of(src), self.p.callable_of(dst))
        paths: List[FlowPath] = []
        reached = set()
        if src in g and dst in g:
            # A MultiDiGraph enumerates a route once per parallel-edge combination, yielding
            # byte-identical duplicate witnesses. Enumerate over a plain-DiGraph VIEW (one path
            # per distinct node route) and read per-hop parallel evidence from the MultiDiGraph g.
            routes = nx.DiGraph(g)
            for path in nx.all_simple_paths(routes, src, dst, cutoff=64):
                hops, tiers = [], []
                for a, b in zip(path, path[1:]):
                    # MultiDiGraph: get_edge_data returns {key: attrdict} over parallel edges.
                    # Pick the strongest-confidence parallel edge as the hop's evidence (the step
                    # is as strong as its best evidence; the path is as weak as its weakest step).
                    parallels = g.get_edge_data(a, b)
                    best = max(parallels.values(),
                               key=lambda d: _TIER_RANK[_ddg_tier(d.get("prov", []))])
                    t = _ddg_tier(best.get("prov", []))
                    tiers.append(t)
                    # Intra edges report their family (cfg/cdg/ddg have no kind); sdg
                    # boundary edges report the concrete kind (param_in/param_out/summary).
                    hops.append({"from": a, "to": b,
                                 "kind": best.get("kind") or best.get("family"),
                                 "var": best.get("var"), "confidence": t})
                conf = _RANK_TIER[min(_TIER_RANK[t] for t in tiers)] if tiers else "unresolved"
                paths.append(FlowPath(source=src, sink=dst, hops=hops, confidence=conf))
                reached.update(path)
        explain = {"source": src, "sink": dst, "level": self.p.max_level(),
                   "paths": len(paths)}
        if note:
            explain["degraded"] = note
        sub = g.subgraph(reached).copy()
        return FlowResult(subgraph=sub, evidence=self._evidence(sorted(sub.nodes()), {src, dst}),
                          _explain=explain, paths=paths)

    def def_use(self, seed, *, strict: bool = False) -> FlowResult:
        note = require(3, self.p, strict=strict, what="def_use")
        s = resolve_vertex(self.p, seed)[0]
        # NOTE: currently scoped to the seed's callable plus sdg endpoints; uses inside
        # OTHER callables' interiors arrive with the whole-program dataflow graph (deferred).
        g = self._dataflow_graph(self.p.callable_of(s))
        reached = {s} | (nx.descendants(g, s) if s in g else set())
        sub = g.subgraph(reached & set(g.nodes())).copy()
        if s not in sub:                          # a seed is trivially in its own def-use result
            sub.add_node(s, kind="seed")
        explain = {"seed": s, "level": self.p.max_level(), "vertices": len(sub)}
        if note:
            explain["degraded"] = note
        return FlowResult(subgraph=sub,
                          evidence=self._evidence(sorted(sub.nodes()), {s},
                                                  default_role="use"),
                          _explain=explain, paths=[])
