# cldk/graph/_cpg_local.py
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional, Tuple
import networkx as nx


def _qualify(callable_id: str, local_key: str) -> str:
    """A body node's dict key is local to its callable ('3:8', '@entry', '@formal_in:0', ...).
    Application-level param_in/param_out already cross-reference these bodies as
    '<callable_id>@<local_key>', with the local key's own leading '@' (entry/exit/formal_*)
    doubling as the join separator (never '...)@@entry'). Reproducing that exact scheme is what
    lets provider-synthesized vertex ids agree with the ids the Application already uses."""
    return f"{callable_id}{local_key}" if local_key.startswith("@") else f"{callable_id}@{local_key}"


class CpgLocalProviderMixin:
    """Implements ProgramGraphProvider's five read primitives over an in-memory cpg Application
    on self.application. Language-neutral: the cpg models are shared across every analyzer, so
    one mixin serves every local backend. Concrete backends supply max_level() (Task 8).

    A body node is keyed by local position in Node.body and normally carries no `.id` of its own
    — only durable nodes (types/callables/functions/fields) are required to have one (see
    Node's docstring and tests/models/cpg/test_node.py::test_body_node_missing_id_parses). This
    mixin's canonical vertex id for a body node is `bn.id` when the analyzer did set one, else
    the synthesized `_qualify(callable_id, local_key)` — matching how real param_in/param_out
    already reference these nodes.
    """

    application: Any  # cpg Application

    # --- internal index (built lazily, cached on the instance) ---
    def _index(self) -> Dict[str, Any]:
        idx = getattr(self, "_cpg_idx", None)
        if idx is not None:
            return idx
        callables: Dict[str, Any] = {}             # callable id -> (callable Node, Module, path)
        canon_of: Dict[str, Dict[str, str]] = {}    # callable id -> {local key: canonical vertex id}
        n2c: Dict[str, str] = {}                    # canonical vertex id -> owning callable id
        nodes: Dict[str, Any] = {}                  # canonical vertex id -> (body Node, Module, path)

        def _add(c, mod, path):
            canon = {k: (bn.id if bn.id is not None else _qualify(c.id, k))
                     for k, bn in c.body.items()}
            canon_of[c.id] = canon
            callables[c.id] = (c, mod, path)
            for k, bn in c.body.items():
                vid = canon[k]
                n2c[vid] = c.id
                nodes[vid] = (bn, mod, path)

        for path, mod in self.application.symbol_table.items():
            for t in mod.types.values():
                for c in t.callables.values():
                    _add(c, mod, path)
            for f in mod.functions.values():
                _add(f, mod, path)

        idx = {"callables": callables, "canon": canon_of, "n2c": n2c, "nodes": nodes}
        self._cpg_idx = idx
        return idx

    def program_graph(self, callable_uri: str) -> nx.MultiDiGraph:
        c, _, _ = self._index()["callables"][callable_uri]
        canon = self._index()["canon"][callable_uri]
        g = nx.MultiDiGraph()
        for k, bn in c.body.items():
            g.add_node(canon[k], kind=bn.kind, span=bn.span)
        # No explicit edge key: 'family' alone does not identify a parallel edge uniquely (e.g. a
        # callable can have several ddg edges between the same pair, one per var, or two cfg
        # edges to the same successor with different kinds). provider.py's ABC docstring requires
        # such edges to stay distinct, so let MultiDiGraph auto-assign a fresh key per edge
        # instead of colliding same-family parallels onto one.
        for fam, edges in (("cfg", c.cfg), ("cdg", c.cdg), ("ddg", c.ddg)):
            for e in edges:
                g.add_edge(canon.get(e.src, e.src), canon.get(e.dst, e.dst),
                           family=fam, kind=e.kind, var=e.var, prov=e.prov)
        return g

    def sdg_edges(self) -> Iterable[Any]:
        # param_in/param_out are already '<callable_id>@<local_key>'-qualified at the Application
        # level; summary is per-callable and LOCAL like cfg/cdg/ddg, so it needs the same
        # qualification program_graph applies. All three are stamped with their own kind
        # ("param_in"/"param_out"/"summary"): real edges carry kind=None in the raw analysis, and
        # Engine.flows_to reports a boundary hop's bare family ("sdg") whenever kind is unset, so
        # leaving these untagged would surface every interprocedural hop as opaque "sdg".
        idx = self._index()
        out = [e.model_copy(update={"kind": "param_in"}) for e in self.application.param_in]
        out += [e.model_copy(update={"kind": "param_out"}) for e in self.application.param_out]
        for c, _, _ in idx["callables"].values():
            canon = idx["canon"][c.id]
            out += [e.model_copy(update={"src": canon.get(e.src, e.src),
                                          "dst": canon.get(e.dst, e.dst),
                                          "kind": "summary"})
                    for e in c.summary]
        return out

    def resolve_location(self, file: str, line: int, col: Optional[int] = None) -> List[str]:
        hits = []
        for vid, (bn, _mod, path) in self._index()["nodes"].items():
            if path != file and not path.endswith("/" + file):
                continue
            if bn.span is None or bn.span.start[0] != line:
                continue
            if col is not None and bn.span.start[1] != col:
                continue
            hits.append(vid)
        return hits

    def source_slice(self, vertex_uri: str) -> Tuple[Optional[str], Optional[str]]:
        node = self._index()["nodes"].get(vertex_uri)
        if node is None:
            return (None, None)
        bn, mod, path = node
        if bn.span is None:
            return (path, None)
        code = mod.source[bn.span.bytes[0]:bn.span.bytes[1]] if mod.source else None
        return (f"{path}:{bn.span.start[0]}", code)

    def callable_of(self, vertex_uri: str) -> Optional[str]:
        return self._index()["n2c"].get(vertex_uri, vertex_uri)
