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

from typing import Callable, Collection, Dict, Iterable, List, Literal, Mapping, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.bounds import check_selector, reject_bare_string
from cldk.analysis.commons.results import Diagnostic, FlowPath, LocateResult, PathHop, SliceNode, TaintResult


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


def via_case(P: str) -> str:
    """The Cypher ``CASE`` mapping a hop's relationship type to the caller's word for it (E6).

    Computed in Cypher rather than in Python because :func:`path_order`'s ``ORDER BY`` sorts by the
    same vocabulary :func:`hop_sort_key` sorts by. Ordering by the raw ``type(r)`` instead would be
    just as deterministic and a *different* order (``PY_CDG`` before ``PY_DDG`` before
    ``PY_PARAM_IN``, against ``argument`` before ``control`` before ``data``), so two backends of one
    language would truncate ``max_paths`` to different witnesses.
    """
    return "CASE type(relationships(p)[i]) " + " ".join(f"WHEN '{rel}' THEN '{word}'" for rel, word in via_table(P).items()) + " ELSE type(relationships(p)[i]) END"


def path_order(P: str) -> str:
    """One sort key per path, ordered exactly as Python would order the tuple :func:`hop_sort_key`
    builds -- so a truncation at ``max_paths`` is a prefix of the documented total order rather than
    whichever paths the database happened to return first.

    The separator is ``\\u0001`` rather than ``|`` for one reason and only that reason: string
    comparison agrees with field-by-field comparison **only** when the separator sorts below every
    character a field can hold, and ``|`` (0x7C) sorts *above* every lowercase letter, which would
    order a variable ``x`` after ``xy``.

    ``coalesce(relationships(p)[i].var, '')`` is load-bearing, not defensive: of the five SDG
    relationship types only ``{P}_DDG`` carries ``var``, so the bare property is ``null`` on every
    control, argument, return and summary hop -- and a ``null`` term would make the whole key
    ``null`` and the ordering arbitrary.

    ``elementId`` is each hop's last field and breaks the tie between parallel relationships a caller
    cannot tell apart. It is stable for repeated calls against one database and means nothing outside
    it, which is why it is last and why nothing above depends on it.
    """
    return (
        "reduce(k = '', i IN range(0, length(p) - 1) | k + " + via_case(P) + " + '\\u0001' + coalesce(relationships(p)[i].var, '') "
        "+ '\\u0001' + nodes(p)[i + 1].id + '\\u0001' + elementId(relationships(p)[i]) + '\\u0001')"
    )


def sdg_path_query(P: str, *, node_label: str, endpoint_scope: Callable[[str], str] | None = None, interior_scope: Callable[[str], str] | None = None, projection: str, rel_var: str = "r") -> str:
    """The shortest-path statement every Neo4j backend issues for ``paths_between``.

    ``allShortestPaths`` and not a plain variable-length match. A variable-length pattern enumerates
    *trails*, the shape that does not terminate on a real dependence graph -- ``EXISTS { (a)-[:
    PY_CALLS*1..]->(a) }`` ran 600 s without terminating on odoo-slim-19; ``allShortestPaths`` is a
    bidirectional BFS and answers the pathological cases in milliseconds -- 0.08 s for an unreachable
    pair seeded at ``Website.configurator_apply``'s ``kwargs`` (the 440,270-node forward cone), 0.06 s
    for a reachable one with 405 distinct shortest paths. ``$cap`` is ``max_paths + 1`` at the call
    site, so one extra row reports the truncation rather than a second ``count(p)`` traversal for a
    number the caller cannot act on.

    Five things differ between the three backends, and all five are parameters:

    * ``node_label`` -- ``PyBodyNode`` / ``JBodyNode`` / ``CanNode:TSBodyNode``.
    * ``endpoint_scope`` and ``interior_scope`` -- callables taking the node variable's name and
      returning the Cypher predicate for it, ``None`` for a backend that does not write one.
      **None is not an oversight.** Python's statements are keyed by a body-node ``id``, which
      embeds the application, and
      ``tests/analysis/python/test_neo4j_multi_application_scope.py`` sanctions id-keying as one of
      four scope kinds for a measured reason: the predicate there would mean testing 195,784 reached
      nodes against a list. Java writes it anyway under a stricter rule -- the audit judges the
      predicate that is *written*, not the graph that happens to be attached -- for a measured ~4%.
      Two standards, each measured on its own corpus.
    * ``projection`` -- the per-node map body without its braces. Python adds ``n.var``, TypeScript
      also ``n.of``, Java neither, because Java recovers the owner from the id prefix instead of
      joining a callable back.
    * ``rel_var`` -- ``r`` everywhere but Java, which spells it ``e``. Inert, and a parameter only so
      this generator can reproduce all three byte-identically instead of normalising one of them.

    Returns a ``.format()`` template still carrying ``{rels}`` and ``{depth}``, so the runner methods
    are unchanged.
    """
    a_scope = f" WHERE {endpoint_scope('a')}" if endpoint_scope else ""
    b_scope = f" WHERE {endpoint_scope('b')}" if endpoint_scope else ""
    interior = f" WHERE all(n IN nodes(p) WHERE {interior_scope('n')})" if interior_scope else ""
    return (
        f"MATCH (a:{node_label} {{{{id:$src}}}}){a_scope} "
        f"MATCH (b:{node_label} {{{{id:$dst}}}}){b_scope} "
        "MATCH p = allShortestPaths((a)-[:{rels}*1..{depth}]->(b))" + interior + " "
        "WITH p, " + path_order(P) + " AS key ORDER BY length(p), key LIMIT $cap "
        f"RETURN [n IN nodes(p) | {{{{{projection}}}}}] AS ns, "
        f"[{rel_var} IN relationships(p) | {{{{via: type({rel_var}), var: {rel_var}.var, prov: {rel_var}.prov}}}}] AS rs"
    )


def under_callable(node_id: str, callable_ids: Collection[str]) -> bool:
    """Whether ``node_id`` names one of ``callable_ids``, one of its body nodes, or one of a
    callable nested inside it -- the Python mirror of :func:`sdg_taint_query`'s ``$cut_callables``
    predicate, and the one place either half of that predicate is written.

    **A bare ``node_id.startswith(q)`` is wrong, and only visibly wrong in one language.** A Python
    callable id ends in ``)`` and so does a Java one, so a prefix test on those is self-delimiting by
    accident -- measured on the committed daytrader8 level-4 fixture: of 444 delimiter-free ids every
    callable-shaped one ends ``)``, and ``<init>()`` is not a prefix of
    ``<init>(java.math.BigDecimal, ...)`` because the ``)`` falls where the other has ``j``. A
    TypeScript callable id carries no closing delimiter at all
    (``can://slim/typescript/src/services.ts/UserService/create``), so a bare prefix test written for
    ``create`` also matches every id belonging to ``createGuest`` -- 13 of them in the committed
    level-4 TypeScript fixture, alongside a second collision (``User`` / ``UserId``). That over-cuts:
    paths a caller never sanitized disappear, the pair joins ``exhausted``, and ``exhausted`` is a
    *certificate that no flow exists*. Over-cutting is therefore a false refutation -- the one output
    ``taint()`` exists to refuse -- while under-cutting merely over-reports. The two directions are
    not symmetric, so do not simplify this back to one ``startswith``.

    Three disjuncts, each earning its place. The joiner histogram over every longer id starting with
    a delimiter-free id in that TypeScript fixture is ``{'/': 920, '@': 254, 'G': 13, 'I': 1}``: the
    two joiners are what the disjuncts accept, and the ``G`` and ``I`` are exactly the collisions
    they now reject.

    * ``node_id == q`` -- the callable's own node. Unreachable under a body-node ``MATCH`` and free,
      but it is what "under this callable" means, so it is written rather than assumed away.
    * ``q + "@"`` -- its body nodes. On the leg-4b Python fixture all 69 ``PY_HAS_BODY_NODE`` children
      join with ``@`` and none joins any other way, so on Python this predicate accepts exactly what
      the bare prefix test accepted. codeanalyzer-typescript's graph was measured the same at 125,532
      body nodes with 0 exceptions (see ``TSNeo4jBackend._OWN_EDGES``, whose ``$bp`` is
      ``node.ref + "@"`` for exactly this reason).
    * ``q + "/"`` -- the body nodes of a callable *nested inside* ``q``, since a child id is
      ``parent + "/" + key`` (``reconstruct.child_key`` raises if it is not). This is not a
      speculative disjunct, and it is the ``@`` disjunct that shows why it is needed separately: a
      call site's own port sub-nodes (``...create@26:5/actual_in:1``) start with ``create@`` and are
      already accepted above, but a nested arrow function's are not. Measured on the committed
      level-4 TypeScript fixture: of the 33 ids that own body nodes, **5 have a ``q + "/"``
      descendant**, every one an anonymous nested callable --
      ``...controllers.ts/Controller/<anon@5:10>@entry`` and its four siblings. Dropping this
      disjunct would leave a callable cut covering the callable but not the closures written inside
      it, which under-cuts, so it is here to **preserve** the bare prefix test's reach, exactly as
      ``resolve_sanitizers`` documents a callable cut ("every body node under it").
    """
    return any(node_id == q or node_id.startswith(q + "@") or node_id.startswith(q + "/") for q in callable_ids)


def sdg_taint_query(P: str, *, node_label: str, endpoint_scope: Callable[[str], str] | None = None, interior_scope: Callable[[str], str] | None = None, projection: str, rel_var: str = "r") -> str:
    """The multi-source, multi-sink shortest-path statement behind ``taint()``.

    Differs from :func:`sdg_path_query` in four ways, each load-bearing:

    * ``$srcs`` / ``$dsts`` are lists, not a single ``$src`` / ``$dst``, so m sources against n
      sinks is one round trip and one traversal per pair rather than m*n statements.
    * A sanitizer cut lives **inside** the pattern -- ``$cut_callables`` (a list of ``can://``
      callable ids, tested with the three disjuncts :func:`under_callable` documents rather than a
      bare ``STARTS WITH``, so a cut named ``create`` cannot also sever ``createGuest``; no join is
      needed either way, because a body node's id is minted under its callable's) and ``$cuts``
      (a list of ``{var, prefix}`` maps) -- rather
      than being applied to the rows a first, unfiltered match returns. Measured on Neo4j 5.26.30:
      an ``all()`` over ``relationships(p)`` inlines into the ``ShortestPath`` operator, so the
      search itself returns the shortest *unsanitized* path. Filtering afterwards would report no
      flow for a source whose shortest paths are all sanitized and whose next one is not -- a false
      refutation, which in this design closes a live security alert. The predicate has to stay in
      an inlinable form: a subquery or an aggregate here reintroduces the exhaustive fallback,
      which is trail enumeration and does not terminate on a real dependence graph.
    * ``$cuts`` is **scoped**, not a flat list of variable names: each member names the callable
      the caller wrote the sanitizer's ``within=`` as (its ``prefix``, a ``can://`` id) alongside
      the variable (``var``). A pair sanitizer ``("cleaned", "Handler.handle")`` therefore only
      cuts the hops whose ``var`` is ``"cleaned"`` *and* whose start node's id falls under
      ``Handler.handle``'s prefix, not every ``"cleaned"`` in the application. Variable names like
      ``result``, ``answer`` and ``token`` recur across callables in any real program; an unscoped
      cut on one of them would sever flows the caller never named, and over-cutting produces false
      refutations -- the one output this design exists to refuse. Measured to still inline into
      ``ShortestPath`` with no separate ``Filter`` and no ``ExhaustiveShortestPath``, which is the
      only reason the flat form was tempting.

      ``startNode({rel_var})`` and not either endpoint is deliberate. A ``PARAM_IN`` edge starts in
      the caller and ends in the callee, so a cut named for the *callee's* formal will not sever
      that crossing edge -- the cut under-scopes rather than over-scopes at a call boundary. That
      under-cuts, and under-cutting only over-reports: the caller investigates a flow that was in
      fact sanitized. Scoping on either endpoint would over-cut -- closing a live alert -- so the
      safe direction is the one written here, on purpose, not the one that reads more symmetric.
    * ``coalesce({rel_var}.var, '')`` is mandatory, and for three reasons that all still hold even
      though the emitter has improved: ``CDG`` and ``SUMMARY`` carry no properties at all;
      ``PARAM_IN``/``PARAM_OUT`` are written by the emitter as ``prune({{"var": e.var}})``, so the
      key is simply absent whenever the formal has none; and both Neo4j backends attach to graphs
      emitted below the current floor, where measurement found ``PY_PARAM_IN`` 0 of 4 and
      ``PY_PARAM_OUT`` 0 of 6 carrying ``var`` at all (a floor-raising codeanalyzer-python 1.5.1 /
      java 3.1.2 / typescript 1.5.3 measured 4 of 4 and 6 of 6 -- better, not universal, and no
      floor-raise touches ``CDG``/``SUMMARY``, which carry no properties by construction). A bare
      ``{rel_var}.var <> $v`` is ``null`` on any hop missing the property, ``all()`` over a ``null``
      term is ``null``, and the path is silently excluded -- the same false-refutation failure mode
      as above, reached a different way.

      **The empty string must never be a legal member of any ``$cuts`` entry's ``var``.**
      ``coalesce`` maps a null ``var`` to ``''``, so if ``''`` reached ``$cuts`` the predicate would
      read as "cut every hop whose var is null, inside that prefix" -- which, given the paragraph
      above, is most control and summary hops in the named callable, and (below the current floor)
      its param hops too. One malformed sanitizer would silently sever most of a callable's graph
      and turn found flows into confident refutations. This function does not enforce the
      rejection; a caller upstream of it must (:func:`~cldk.analysis.commons.resolve.resolve_sanitizers`
      does).
    * The cap is **per pair** -- ``collect(p)[0..$cap]`` after an ordered ``WITH a, b`` -- rather
      than a flat ``LIMIT``, because with one sink and forty sources a flat cap lets one prolific
      pair starve the other thirty-nine, and in triage the per-source witness is the answer.

    Returns ``a.id AS src`` and ``b.id AS dst`` alongside the path projection, so a caller can tell
    which source reached which sink -- the m*n batching above is only useful if the grouping survives
    it.

    Same injection points as :func:`sdg_path_query` -- ``node_label``, ``endpoint_scope``,
    ``interior_scope``, ``projection``, ``rel_var`` -- and the same ``.format()`` template still
    carrying ``{rels}`` and ``{depth}``.
    """
    a_scope = f" AND {endpoint_scope('a')}" if endpoint_scope else ""
    b_scope = f" AND {endpoint_scope('b')}" if endpoint_scope else ""
    interior = f" AND all(n IN nodes(p) WHERE {interior_scope('n')})" if interior_scope else ""
    return (
        f"MATCH (a:{node_label}) WHERE a.id IN $srcs{a_scope} "
        f"MATCH (b:{node_label}) WHERE b.id IN $dsts{b_scope} "
        "MATCH p = allShortestPaths((a)-[:{rels}*1..{depth}]->(b)) "
        "WHERE all(n IN nodes(p) WHERE NOT any(q IN $cut_callables WHERE "
        "n.id = q OR n.id STARTS WITH q + '@' OR n.id STARTS WITH q + '/'))"
        + interior + " "
        f"AND all({rel_var} IN relationships(p) WHERE NOT any(c IN $cuts WHERE "
        f"coalesce({rel_var}.var, '') = c.var AND (startNode({rel_var}).id = c.prefix "
        f"OR startNode({rel_var}).id STARTS WITH c.prefix + '@' "
        f"OR startNode({rel_var}).id STARTS WITH c.prefix + '/'))) "
        "WITH a, b, p, " + path_order(P) + " AS key ORDER BY length(p), key "
        "WITH a, b, collect(p)[0..$cap] AS ps "
        "UNWIND ps AS p "
        f"RETURN a.id AS src, b.id AS dst, [n IN nodes(p) | {{{{{projection}}}}}] AS ns, "
        f"[{rel_var} IN relationships(p) | {{{{via: type({rel_var}), var: {rel_var}.var, prov: {rel_var}.prov}}}}] AS rs"
    )


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


def shortest_walks(
    edges: Mapping[str, Mapping[str, Sequence[tuple]]],
    src: str,
    dst: str,
    depth: int | None,
    limit: int,
    *,
    via: Mapping[str, str],
    allow_edge: Callable[[str, str, "str | None"], bool] | None = None,
    allow_node: Callable[[str], bool] | None = None,
) -> List[list]:
    """Up to ``limit`` shortest ``src``->``dst`` walks over ``edges``, in the documented order.

    The local backends' twin of the graph's ``allShortestPaths``, and only shortest walks for its
    reason: enumerating every walk does not terminate on a real dependence graph.

    ``edges`` is the ``{src: {dst: [label]}}`` adjacency each local backend builds, where a label is
    ``(relationship type, var, prov)``. A **list** per ``(src, dst)`` pair because parallel edges are
    ordinary -- one statement feeding one argument on several variables is several distinct paths --
    and collapsing them would merge several pieces of evidence into one.

    ``allow_edge`` and ``allow_node`` are the taint sanitizer cut: ``allow_edge(start node id,
    relationship type, var)`` keeps a label, ``allow_node(node id)`` keeps a node (checked against
    ``src`` itself too, so a source inside a cut callable yields no walk at all). Both default to
    ``None``, meaning no filtering, so every caller that predates taint is unaffected.

    **The start node is a parameter because a variable cut is scoped**, and it is scoped to the
    *start* node exactly as the Cypher predicate is (corrected Ruling B --
    :func:`sdg_taint_query`'s ``startNode({rel_var}).id STARTS WITH c.prefix``). A cut is
    ``{var, prefix}``: the variable, and the ``can://`` id of the callable the caller wrote
    ``within=`` as. Without the start node a local predicate could only compare the name, cutting
    every ``result``/``answer``/``token`` hop in the application when the caller named one
    callable's -- and over-cutting produces false refutations, the one output this design refuses.
    ``startNode`` and not either endpoint is deliberate there and here: a parameter-passing edge
    starts in the caller, so a cut named for the callee's formal under-cuts rather than over-cuts,
    and under-cutting only over-reports. **They must be applied before the
    breadth-first pass computes ``dist``, not only in the depth-first replay** -- see :func:`steps`
    below, which both passes call. Filtering the replay alone would leave ``dist`` describing the
    unfiltered graph: a sanitized 2-hop route would still pin ``dist[dst]`` to 2, and a clean 3-hop
    route would never be visited, returning no walk at all for a pair that genuinely flows -- a false
    refutation the Neo4j backend does not share, because its planner inlines the predicate into the
    shortest-path search itself.

    Two passes. The first is a breadth-first level walk keeping the hop count each node was *first*
    reached at; the second is a depth-first replay that only ever steps to a node whose recorded
    distance is exactly one more than the walk so far, so it visits shortest walks and nothing else.

    The replay's branch order is ``(via, var, to)`` -- exactly the per-hop key
    :func:`hop_sort_key` documents -- and every walk found has the same length, so a pre-order
    depth-first traversal emits them already sorted. That is what makes ``limit`` a *prefix* of a
    total order rather than whichever ``limit`` walks the recursion happened to find first. ``via``
    is the backend's :func:`via_table`, so the branch order is the caller's vocabulary and not the
    graph's relationship-type spelling.

    Lifted out of ``PyCodeanalyzer`` (leg 2.5b) unchanged except for the ``via`` parameter: it is
    a graph algorithm over an adjacency of strings and knows no language, and a second copy would be
    a second place for the two backends of a language -- or the backends of two languages -- to
    drift on what "shortest, in order" means.
    """
    if allow_node is not None and not allow_node(src):
        return []

    def steps(node: str):
        """The ``(destination, labels)`` pairs the search may step to from ``node``, filtered.

        Used by **both** passes, and that is the whole point: filtering only the depth-first replay
        would leave the breadth-first ``dist`` describing the unfiltered graph (see the module
        docstring above for why that is a false refutation, not a performance shortcut). Filtering
        here instead makes ``dist`` the shortest *satisfying* distance.

        ``node`` is what a scoped variable cut needs and is only available here, which is why
        ``allow_edge`` takes it: this is the one place in either pass that knows which node a label
        is leaving.
        """
        for d, labels in edges.get(node, {}).items():
            if allow_node is not None and not allow_node(d):
                continue
            kept = [lab for lab in labels if allow_edge is None or allow_edge(node, lab[0], lab[1])]
            if kept:
                yield d, kept

    dist, frontier, hops = {src: 0}, [src], 0
    while frontier and dst not in dist and (depth is None or hops < depth):
        hops += 1
        nxt = []
        for s in frontier:
            for d, _ in steps(s):
                if d not in dist:
                    dist[d] = hops
                    nxt.append(d)
        frontier = nxt
    if dst not in dist or dist[dst] == 0:
        return []
    target, out = dist[dst], []

    def walk(node: str, walked: list) -> None:
        if len(walked) == target:
            if node == dst:
                out.append(list(walked))
            return
        options = sorted((via[rel], var or "", d, (rel, var, prov)) for d, labels in steps(node) if dist.get(d) == len(walked) + 1 for rel, var, prov in labels)
        for _, _, d, label in options:
            walked.append((d, label))
            walk(d, walked)
            walked.pop()
            if len(out) >= limit:
                return

    walk(src, [])
    return out


def call_reaches(graph: "nx.DiGraph", a: str, b: str, depth: "int | None") -> bool:
    """Whether a call path of **at least one hop** runs from ``a`` to ``b``, within ``depth`` hops.

    One function for the three in-memory backends because ``reaches(x, x)`` is the case they all
    got wrong in the same way, and because the advice that points at it is shared too
    (:func:`~cldk.analysis.commons.bounds.check_distinct_endpoints` tells a caller to "ask
    ``reaches(X, X)`` whether a cycle exists").

    ``nx.descendants`` and ``ego_graph(...) - {a}`` both **exclude the source**, even when the
    source has a self-loop or sits on a cycle — that is what "descendants" means — so asking them
    ``b in reachable`` for ``b == a`` answered ``False`` for every input. Both Neo4j backends
    already answer the cycle question, because their quantified pattern is ``{1,depth}`` and lands
    back on the source like any other node, so this was also a backend divergence and not only a
    wrong docstring.

    The self-question is asked of the predecessors instead: ``a`` is on a cycle exactly when
    something that reaches ``a`` is reachable *from* ``a`` — including ``a`` itself, which is the
    direct self-loop. Bounded, the two halves have to add up to ``depth``, so the reachable half is
    one hop shorter.
    """
    if a not in graph or b not in graph:
        return False
    reachable = nx.descendants(graph, a) if depth is None else set(nx.ego_graph(graph, a, radius=depth).nodes) - {a}
    if a != b:
        return b in reachable
    inner = {a} | reachable if depth is None else set(nx.ego_graph(graph, a, radius=depth - 1).nodes)
    return any(predecessor in inner for predecessor in graph.predecessors(a))


def _no_body_node(found: LocateResult) -> str:
    """Why a :class:`~cldk.analysis.commons.results.LocateResult` has no ``node_id``, in the
    caller's own vocabulary.

    Three distinguishable reasons and three sentences, because they call for different next steps: a
    position inside a callable but on no emitted vertex (a declaration line, a blank line, a comment
    -- the common case), a position at module scope, and a file the analysis does not cover. Each
    ends with something that actually runs on the value the caller already has (E8: advice must be
    followable), and none of them spells a ``can://`` id (E6)."""
    where = f"{found.module.path}:{found.span.start[0]}"
    if found.callable is not None:
        return (
            f"the locate() result for {where} landed inside {found.callable.signature} but on no statement, call or branch "
            "the analyzer emitted, so it carries no ref for describe() to look up. Its enclosing callable does: pass "
            f"resolve_callable({found.callable.signature!r}) instead, or read the text off the result's own .source."
        )
    reasons = ", ".join(d.code for d in found.diagnostics) or "no enclosing callable"
    return (
        f"the locate() result for {where} is not inside any callable ({reasons}), so it carries no ref for describe() "
        "to look up. Nothing below the module is addressable at that position; locate() a line inside a callable, or name "
        "a callable with resolve_callable()."
    )


def as_slice_node(node: object) -> SliceNode:
    """The :class:`~cldk.analysis.commons.results.SliceNode` for anything carrying an address.

    :meth:`PythonAnalysisBackend.describe` takes "anything with a ``ref``" — slice nodes, the
    endpoints of a :class:`~cldk.analysis.commons.results.PathHop`, a
    :class:`~cldk.analysis.commons.results.LocateResult` — because the addressing layer hands a
    caller three shapes and asking them to convert between shapes to hydrate one is the kind of
    friction that gets worked around with string surgery.

    A ``SliceNode`` passes through untouched. A ``LocateResult`` is re-expressed as one, keeping
    the vocabulary it already speaks: ``module.path`` is the file, ``callable.signature`` the
    enclosing callable, ``body.kind`` the position's kind.

    Raises:
        TypeError: ``node`` carries neither a ``ref`` nor a ``node_id``, so there is nothing to
            look up. Guessing an address from a file and a line is what ``locate`` is for. A
            :class:`~cldk.analysis.commons.results.LocateResult` that landed on **no body node** is
            the common way to arrive here — measured on daytrader8, 187 of 300 random in-callable
            positions have ``body is None`` — so it is refused in its own words: naming the type
            among the accepted ones and then refusing it reads as a bug in the accessor.
    """
    if isinstance(node, SliceNode):
        return node
    ref = getattr(node, "node_id", None)
    if ref is None:
        if isinstance(node, LocateResult):
            raise TypeError(_no_body_node(node))
        raise TypeError(f"describe() needs something carrying a ref (a SliceNode, a path hop endpoint, a locate() result); got {type(node).__name__}")
    module, callable_ref, body = node.module, node.callable, getattr(node, "body", None)
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


def taint_verdict(
    sources: Sequence[Tuple[str, str]],
    sinks: Sequence[Tuple[str, str]],
    srcs: Sequence[SliceNode],
    dsts: Sequence[SliceNode],
    *,
    rows: Sequence[Tuple[str, str, FlowPath]],
    blocked: Mapping[Tuple[str, str], List[Diagnostic]],
    depth: int | None,
    max_paths: int,
) -> TaintResult:
    """What a walk found, turned into the answer ``taint()`` returns: witnesses, verdicts, ledger.

    One implementation for all three languages, because every edit this assembly has needed has been
    one edit times three files, and the first such edit applied to two of them is drift of exactly
    the kind that ships a wrong ``exhausted``. The *contract* stays on each ABC -- the docstring, the
    ordered checks, the resolution, Java's two gates -- and only the arithmetic after the walk is
    here, in the module that already owns ``sdg_taint_query`` and ``slice_resolved``.

    Four rules live in this body and nowhere else:

    * **The verdict is assembled by looking each requested pair up**, never by consuming ``rows``.
      A walk sees flat source and sink lists, so its m*n cross product can contain a combination no
      requested pair names (a value that is both a source of one pair and a sink of another), and a
      row for one of those is simply never read.
    * **A pair is a pair of resolved positions.** The requested pairs are deduplicated by
      ``(src ref, dst ref)`` in first-seen order, which is the rule ``roots`` follows too: without it
      a duplicated selector repeats its witnesses and lets a cap of m yield 2m, contradicting
      ``max_paths``' own "per pair". The first spelling wins, so ``exhausted`` names what the caller
      wrote.
    * **No key in ``blocked`` can vanish.** The pair loop reads one key per pair and claims it; every
      key left unclaimed is swept into the ledger afterwards. A diagnostic keyed any other way -- a
      reversed pair, one arm of a callable frontier, a combination this caller did not request --
      would otherwise be read by nobody, and the pair it named would come back ``exhausted`` with a
      clean ledger: a certified refutation of a flow that was in fact blocked. Nothing here can
      attribute a stray key to a pair, so ``exhausted`` is emptied rather than trusted; refusing to
      certify is the direction that cannot close a live alert, and ``unresolved`` says why.
    * **``complete`` is the whole batch's flag**, ``True`` only when the cap cut nothing *and* the
      ledger is empty. It is deliberately conservative and deliberately coarse: one degenerate pair
      makes a forty-pair call ``False``, and raising ``max_paths`` will not change that. ``exhausted``
      is what carries a per-pair verdict.

    Args:
        sources: The ``(name, within)`` selectors as the caller wrote them -- what ``exhausted`` and
            the diagnostics are named by.
        sinks: The same, for the sinks.
        srcs: What ``sources`` resolved to, positionally aligned with it.
        dsts: What ``sinks`` resolved to, positionally aligned with it.
        rows: The walk's ``(src ref, dst ref, path)`` triples, shortest-first within a pair and
            capped at ``max_paths + 1`` per pair, which is what reports truncation without a second
            counting traversal.
        blocked: The walk's frontier ledger, keyed by the pair each diagnostic implicates.
        depth: The bound the call ran under; ``exhausted`` is empty whenever it is not ``None``,
            because a pair with no path within five hops is unmeasured rather than refuted.
        max_paths: Most witnesses per pair.

    Returns:
        The :class:`~cldk.analysis.commons.results.TaintResult` the accessor hands back.
    """
    found: Dict[Tuple[str, str], List[FlowPath]] = {}
    for src_ref, dst_ref, path in rows:
        found.setdefault((src_ref, dst_ref), []).append(path)
    pairs: Dict[Tuple[str, str], Tuple[str, str, SliceNode]] = {}
    for (source, _), a in zip(sources, srcs):
        for (sink, _), b in zip(sinks, dsts):
            pairs.setdefault((a.ref, b.ref), (source, sink, a))
    paths: List[FlowPath] = []
    exhausted: List[Tuple[str, str]] = []
    ledger: List[Diagnostic] = []
    truncated = False
    claimed: set[Tuple[str, str]] = set()
    for (src_ref, dst_ref), (source, sink, a) in pairs.items():
        if src_ref == dst_ref:
            # Its own code, because the two neighbouring ones would both be false: ``no_match`` says a
            # search found nothing and nothing was searched here, and ``unresolved_dispatch`` would
            # implicate the call frontier, which is the one signal ``exhausted`` reduces to.
            ledger.append(
                Diagnostic(
                    code="degenerate_pair",
                    message=(
                        f"{source!r} and {sink!r} name the same position within {a.callable!r}, so that pair is skipped rather than "
                        f"searched; a value reaches itself only through recursion, which reaches({a.callable!r}, {a.callable!r}) answers"
                    ),
                )
            )
            continue
        claimed.add((src_ref, dst_ref))
        stopped = list(blocked.get((src_ref, dst_ref), []))
        witnesses = found.get((src_ref, dst_ref), [])
        ledger.extend(stopped)
        paths.extend(witnesses[:max_paths])
        truncated = truncated or len(witnesses) > max_paths
        # The three conditions, in one place: unbounded search, no witness, clean ledger for this
        # pair. The pair association comes from ``blocked``'s key and never from reading a
        # diagnostic's message back out.
        if depth is None and not witnesses and not stopped:
            exhausted.append((source, sink))
    unclaimed = [d for key, stopped in blocked.items() if key not in claimed for d in stopped]
    if unclaimed:
        ledger.extend(unclaimed)
        exhausted = []
    roots = list({node.ref: node for node in [*srcs, *dsts]}.values())
    return TaintResult(paths=paths, complete=not truncated and not ledger, exhausted=exhausted, roots=roots, resolved=slice_resolved(roots), unresolved=ledger)
