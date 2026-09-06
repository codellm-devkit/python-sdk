################################################################################
# Copyright IBM Corporation 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Graph walks and path assembly: the language-neutral half of slicing and reachability.

Lifted out of the Python backend (leg 2.5a, G4) unchanged except for three parameters: the
relationship-type prefix (``sdg_rels(P)`` / ``via_table(P)`` where the Python backend had
``PY_``-spelled tables) and the ``via`` map :func:`flow_path` translates through. The per-language
backend binds each once and hands the bound object down.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Literal, Mapping, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.bounds import check_selector, reject_bare_string
from cldk.analysis.commons.results import FlowPath, PathHop, SliceNode


def bounded_subgraph(graph: nx.DiGraph, roots: List[str], depth: int | None, declared: Iterable[str]) -> nx.DiGraph:
    """The sub-call-graph reachable from ``roots``, within ``depth`` hops when given.

    **Induced**, not path-only: every edge between two reached nodes is kept, including one
    pointing back towards a root. A path-only answer would let ``graph.predecessors(n)`` lie about
    a node the caller can see, which is a worse defect than the extra edges are a cost. The Neo4j
    backend's Cypher is written to produce the same induced shape rather than the cheaper
    edges-along-the-path shape, for exactly this reason.

    **The domain a root is judged against — stated here because both backends must judge against
    the same one — is the callable inventory, not this graph.** ``graph`` is built from call
    *edges* alone, so a callable that neither calls nor is called by anything is not a node in it:
    444 of the live odoo application's 15,549 in-scope callables, 2.9%. Checking membership of
    ``graph`` therefore raised for a callable that plainly exists, while the Neo4j backend — whose
    Cypher matches a root by node *label*, not by edge participation — returned the one-node graph
    it is. ``declared`` closes that gap: it carries every callable the application declares, and a
    root is valid when it is **in the inventory or is a node of the graph**. The second disjunct is
    not redundant — an ``@external`` ghost is a legitimate root, is a graph node, and is not a
    declared callable — and the union is exactly what the Neo4j root match accepts (a
    ``:PyCallable`` of this application, or a ``:PyExternal``).

    A root outside that domain raises (:func:`check_selector`) rather than contributing nothing:
    "no such callable" and "a callable that calls nothing" are different answers, and before this
    they were the same empty graph.

    The returned graph stays **edge-induced**. An isolated root is added back as a lone node —
    which is the answer, and the one Neo4j gives — but nothing else the inventory knows about is
    seeded into it. Seeding all declared callables would make the unbounded local graph disagree
    with Neo4j's node-for-node, trading one parity defect for a larger one.
    """
    inventory = set(declared)
    check_selector("roots", roots, [r for r in roots if r not in graph and r not in inventory])
    nodes: set = set()
    isolated: set = set()
    for root in roots:
        if root not in graph:
            isolated.add(root)  # declared, but in no call edge: its own one-node graph
        elif depth is None:
            nodes |= nx.descendants(graph, root) | {root}
        else:
            nodes |= set(nx.ego_graph(graph, root, radius=depth).nodes)
    sub = graph.subgraph(nodes).copy()
    sub.add_nodes_from(isolated)
    return sub


# The structural half of the per-callable graph orders. The components and their sequence are
# what make a page mean the same thing on both backends (see the paging block in ``bounds``); the
# per-language backend binds each to its own edge model -- ``cfg_sort_key(edge: CfgEdge)`` in the
# Python backend -- so the typed name a reader greps for stays where the type lives.
_EDGE_KEYS: dict[str, Callable[[object], Tuple]] = {
    "cfg": lambda e: (e.src, e.dst, e.kind or ""),
    "cdg": lambda e: (e.src, e.dst),
    "ddg": lambda e: (e.src, e.dst, e.var or "", list(e.prov or [])),
}


def edge_sort_key(kind: Literal["cfg", "cdg", "ddg"]) -> Callable[[object], Tuple]:
    """The canonical sort key for one per-callable graph kind, over the edge's own fields.

    ``cfg``: source, target, kind. ``cdg``: source, target. ``ddg``: source, target, variable,
    provenance. ``or ""`` / ``or []`` because an optional field's ``None`` in a sort key raises in
    Python and silently drops the row in Cypher; the Cypher spells it ``coalesce``.
    """
    return _EDGE_KEYS[kind]


# ----------------------------------------------------------------------------------------------
# Slicing and reachability (E2, E3, E5).
#
# THE FIVE RELATIONSHIP TYPES A SLICE FOLLOWS, verified against codeanalyzer's own
# ``neo4j/schema.py`` REL_TYPES and against ``CALL db.relationshipTypes()`` on odoo-slim-19 rather
# than copied from a plan -- the names in this leg's plan have been wrong before (PY_CFG_NEXT is
# not PY_CFG). All five exist, with these edge counts on that application:
#
#   PY_DDG        5,134,655   data dependence, within a callable  (var, prov)
#   PY_CDG          139,065   control dependence, within a callable
#   PY_PARAM_IN     229,035   actual_in -> formal_in     : an argument entering a callee
#   PY_PARAM_OUT    133,267   formal_out -> actual_out   : a value coming back to the caller
#   PY_SUMMARY      453,398   actual_in -> actual_out    : a callee's pass-through, at the call site
#
# All five point WITH the flow -- verified on the live graph, where every PY_PARAM_IN runs
# actual_in -> formal_in and every PY_PARAM_OUT runs formal_out -> actual_out, with no exceptions
# in 362,302 edges. So a forward slice follows them and a backward slice follows them reversed;
# there is no per-type direction table to keep straight, which is why they can share one match.
#
# PY_CFG_NEXT is deliberately NOT here. Control *flow* says what runs next; a slice is about what
# a value or a decision depends on, and following successor edges would pull in every later
# statement whether or not it depends on anything -- the "returns the whole callable" bug that a
# non-emptiness assertion cannot catch.
#
# The table is a function of the backend's relationship prefix (``AnalysisBackend.P``): the five
# kinds and their meaning are the analyzer family's, the ``PY_`` / ``TS_`` spelling is one language's.


def sdg_rels(P: str) -> tuple[str, ...]:
    """The five relationship types a slice follows, spelled with the language's prefix ``P``."""
    return (f"{P}_DDG", f"{P}_CDG", f"{P}_PARAM_IN", f"{P}_PARAM_OUT", f"{P}_SUMMARY")


def sdg_rel_pattern(P: str) -> str:
    """The Cypher spelling of :func:`sdg_rels` for a relationship-type disjunction."""
    return "|".join(sdg_rels(P))


#: The caller's word for each relationship a path hop can be justified by (E6). The graph's own
#: ``PY_DDG``/``PY_PARAM_IN`` spelling never leaves the backend; both backends translate through
#: this one table so a hop cannot be labelled ``data`` over Neo4j and ``ddg`` locally.
#:
#: ``argument`` and ``return`` are the two interprocedural edges, and they are deliberately not
#: both called "parameter": ``PY_PARAM_IN`` binds a caller's argument to a callee's formal, and
#: ``PY_PARAM_OUT`` binds a callee's result back into the caller. A reader following a path needs
#: to know which way it just crossed a call boundary.
def via_table(P: str) -> dict[str, str]:
    """The relationship-type -> caller's-word table above, for the language whose prefix is ``P``."""
    return {
        f"{P}_DDG": "data",
        f"{P}_CDG": "control",
        f"{P}_PARAM_IN": "argument",
        f"{P}_PARAM_OUT": "return",
        f"{P}_SUMMARY": "summary",
        f"{P}_CALLS": "call",
    }


def hop_sort_key(hops: Sequence[PathHop]) -> Tuple:
    """The order two paths are compared in, in the caller's *own* vocabulary.

    E2 makes a path a sequence, which only means something if the *list* of paths is stable too:
    ``max_paths`` truncates, and a truncation of a non-deterministic order is not reproducible.
    So paths are ordered shortest first, then hop by hop on ``(via, var, to.ref)`` — every term of
    which the caller can see in the result it gets back.

    Two hops that are indistinguishable in that vocabulary (parallel edges of the same kind, on
    the same variable, between the same two nodes) are left to a backend-local tie-break: the
    Neo4j backend appends the relationship's ``elementId``, the local backend keeps the order the
    analyzer emitted them in. Either is stable for repeated calls against one graph; neither is
    meaningful to a caller, which is why it is last and why nothing above depends on it.
    """
    return (len(hops), tuple((h.via, h.var or "", h.to.ref) for h in hops))


def flow_path(nodes: Sequence[SliceNode], edges: Sequence[Tuple[str, "str | None", "Sequence[str] | None"]], *, via: Mapping[str, str]) -> FlowPath:
    """Join a walk's ``n`` nodes and its ``n - 1`` edges into a :class:`FlowPath`.

    Both backends build paths through here, which is what makes the joining invariant
    (``hops[i].to is hops[i + 1].frm``) a property of the construction rather than something each
    backend has to be trusted to preserve. ``edges`` are the graph's own relationship types; they
    are translated to the caller's word through ``via`` (the backend's :func:`via_table`) exactly once, here.

    Raises:
        KeyError: A relationship type with no word in ``via`` — a new edge kind from a future
            analyzer generation, which must be named before it can be reported rather than passed
            through in the graph's spelling.
    """
    return FlowPath(hops=[PathHop(frm=nodes[i], to=nodes[i + 1], via=via[rel], var=var, prov=list(prov or [])) for i, (rel, var, prov) in enumerate(edges)])


def as_slice_node(node: object) -> SliceNode:
    """The :class:`~cldk.analysis.commons.results.SliceNode` for anything carrying an address.

    :meth:`PythonAnalysisBackend.describe` takes "anything with a ``ref``" — slice nodes, the
    endpoints of a :class:`~cldk.analysis.commons.results.PathHop`, a
    :class:`~cldk.analysis.commons.results.LocateResult` — because the addressing layer hands a
    caller three shapes and asking them to convert between shapes to hydrate one is the kind of
    friction that gets worked around with string surgery.

    A ``SliceNode`` passes through untouched. A ``LocateResult`` is re-expressed as one, keeping
    the vocabulary it already speaks: ``module.path`` is the file, ``callable.signature`` the
    enclosing callable, ``node.kind`` the position's kind.

    Raises:
        TypeError: ``node`` carries neither a ``ref`` nor a ``node_id``, so there is nothing to
            look up. Guessing an address from a file and a line is what ``locate`` is for.
    """
    if isinstance(node, SliceNode):
        return node
    ref = getattr(node, "node_id", None)
    if ref is None:
        raise TypeError(f"describe() needs something carrying a ref (a SliceNode, a path hop endpoint, a locate() result); got {type(node).__name__}")
    module, callable_ref, body = node.module, node.callable, getattr(node, "node", None)
    return SliceNode(
        file=module.path,
        line=node.span.start[0],
        callable=callable_ref.signature if callable_ref else "",
        kind=body.kind if body else "callable",
        name=callable_ref.name if callable_ref else None,
        source=node.source or None,
        ref=ref,
    )


def cone_sinks(resolve: Callable[[str], SliceNode], sinks: Sequence[str]) -> List[SliceNode]:
    """Resolve ``backward_cone``'s sinks, refusing the two ways of naming nothing.

    The same discipline :func:`check_selector` applies to ``roots=`` and ``paths=``: a bare string
    is ten one-character sinks and is refused as a type error, and an empty sequence is refused
    because "everything" is the argument omitted, not the argument emptied — and there is no
    "everything" here to fall back to. Each surviving name goes through ``resolve``, so an
    ambiguous sink raises listing candidates instead of one of them being picked.

    Duplicates are collapsed by resolved signature, not by the string the caller wrote: naming the
    same callable twice, once bare and once qualified, is one sink.
    """
    reject_bare_string("sinks", sinks)
    if not sinks:
        raise ValueError("sinks= names nothing to walk back from; pass at least one callable")
    resolved = {node.callable: node for node in (resolve(s) for s in sinks)}
    return list(resolved.values())


def slice_resolved(roots: List[SliceNode]) -> str:
    """The audit line on a :class:`~cldk.analysis.commons.results.Slice`: what the caller's names
    matched, in the caller's vocabulary.

    Both backends build it here rather than each formatting its own, so a caller comparing two
    results is comparing answers and not two spellings of one.
    """
    return ", ".join(f"{r.callable} {r.kind} {r.name!r}" if r.kind != "callable" else r.callable for r in roots)
