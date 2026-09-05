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

"""The Python analysis backend contract.

:class:`PythonAnalysis` is a thin façade that delegates every query to a *backend*. Today the only
backend is :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer` (in-memory pydantic /
NetworkX over ``analysis.json``); this ABC formalizes the surface the façade depends on so an
alternative backend (e.g. a forthcoming Neo4j/Cypher backend, mirroring the TypeScript
:class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`) can be dropped in and selected without
touching the façade.

The contract is enforced by the type system and at instantiation time rather than matching only
by convention. Backend-specific lifecycle (caches, drivers) is intentionally not part of it.
"""

from __future__ import annotations

import os
import posixpath
from abc import abstractmethod
from typing import Dict, Iterable, List, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.results import EntrypointCoverage, LocateResult, SliceNode
from cldk.utils.exceptions import SelectorNotInGraph
from cldk.models.python import (
    PyApplication,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
    PyExternalSymbol,
    PyModule,
)


def resolve_module_key(path: str, keys: Iterable[str]) -> str:
    """The symbol-table / graph ``file_key`` naming ``path``, or ``path`` unchanged if none does.

    A caller of :meth:`PythonAnalysisBackend.locate` hands over whatever its scanner printed —
    ``./src/app.py``, ``src/../src/app.py``, or an absolute path from the machine the scan ran on —
    while both backends are keyed by the project-relative path the analyzer saw. Exact key first,
    then the normalised form, then the longest known key the normalised path *ends on a segment
    boundary* of (which is what an absolute path is). Returning ``path`` unchanged when nothing
    matches is deliberate: the caller then gets ``file_not_in_graph`` naming the path it asked
    about, not a silently substituted neighbour.
    """
    keys = list(keys)
    if path in keys:
        return path
    norm = posixpath.normpath(str(path).replace(os.sep, "/"))
    if norm in keys:
        return norm
    suffix_matches = [k for k in keys if norm.endswith("/" + k)]
    return max(suffix_matches, key=len) if suffix_matches else path


def body_key_column(key: str) -> int:
    """The start column encoded in a body node's local key (``"21:12"`` -> ``12``), or ``-1``.

    Both backends need one tie-break for two body nodes that span the *same* line — ``if x: return x``
    emits an ``if`` and a ``return`` each spanning one line — and line numbers are the only positional
    data the Neo4j projection carries, so the span cannot break it. The local *key* can: it is
    ``<line>:<col>`` (sometimes suffixed, as in ``"22:8/actual_in:0"``), it exists on both sides
    (locally the ``body`` dict key, over Neo4j the trailing segment of ``<callable id>@<key>``), and a
    larger column is the more deeply nested statement. Comparing the keys as *strings* instead would
    order ``"29:10"`` before ``"29:4"`` and pick the outer node, so the column is parsed as an int.

    ``-1`` for a key with no column (the synthetic ``@entry`` / ``@exit`` vertices) — they carry no
    span, so they are filtered out before ranking and never reach this.
    """
    _, _, col = key.split("/", 1)[0].partition(":")
    return int(col) if col.isdigit() else -1


def reject_bare_string(kind: str, values: object) -> None:
    """Refuse a single string where a sequence of names is required.

    ``paths='pkg/mod.py'`` is not a type error to Python — a string *is* a sequence, of ten
    characters — so it used to reach :func:`check_selector` as ten requested paths and come back as
    ``10 of 10 paths not in graph: 'p', 'k', 'g', '/', …``. The mistake is the likely one because
    the sibling keyword ``module=`` genuinely is single-valued, so both spellings look plausible.

    Raises:
        TypeError: ``values`` is a ``str``.
    """
    if isinstance(values, str):
        raise TypeError(f"{kind}= takes a sequence of names, not a string; pass [{values!r}] to select just that one")


def check_selector(kind: str, requested: Sequence[str], missing: Sequence[str]) -> None:
    """The one place a scoping keyword's *selection* is judged, for both backends.

    Every scoped accessor — ``get_symbol_table(paths=)``, ``get_classes(module=)``,
    ``get_call_graph(roots=)`` — narrows a whole-application enumeration to what the caller named.
    Two ways of naming nothing must not both come back as an empty result:

    * **an empty sequence** (``paths=[]``, ``roots=[]``) selected nothing while missing nothing. It
      is a caller bug — the argument to omit is the argument that means "everything" — and it
      raises the same :class:`ValueError` ``depth=`` without ``roots=`` already does.
    * **values that match nothing** are the ambiguous empty the parent spec's D7 calls a defect:
      a mistyped path and a module that genuinely declares no classes were the same ``{}``. They
      raise :class:`~cldk.utils.exceptions.SelectorNotInGraph`, which names them and stops. It
      offers no near-miss candidates on purpose — leg 1.5's E8 puts typo-tolerant matching out of
      scope "not in the resolver, not in the error path".

    A **partial** miss raises too. Returning the values that did match would make a result whose
    size the caller cannot check against what it asked for, which is the same silence one step
    quieter.

    Args:
        kind: The keyword's name, as it appears in the caller's own call — ``"paths"``,
            ``"module"`` or ``"roots"``.
        requested: Everything the keyword named, in the caller's spelling.
        missing: The subset of ``requested`` that matched nothing. Callers with no membership
            information to bring (``call_graph_scope``, which has not seen the graph yet) pass an
            empty sequence and get only the empty-selection check.

    Raises:
        ValueError: ``requested`` is empty.
        SelectorNotInGraph: ``missing`` is non-empty.
    """
    if not requested:
        raise ValueError(f"{kind}= selected nothing; omit it to enumerate the whole application")
    if missing:
        raise SelectorNotInGraph(kind, list(missing), len(requested))


def scope_paths(paths: Sequence[str] | None, keys: Iterable[str], kind: str = "paths") -> List[str] | None:
    """Resolve requested module paths to symbol-table keys, or ``None`` for "the whole application".

    Both backends route their ``paths=`` / ``module=`` keywords through here, so the lenient
    resolution (:func:`resolve_module_key` — an absolute path or one with native separators finds
    its module) and the strictness (:func:`check_selector` — a path naming no module raises) cannot
    drift apart between them.

    Args:
        paths: What the caller named, or ``None`` for the unscoped call.
        keys: The symbol-table keys that exist — ``symbol_table.keys()`` locally, the
            application's module ``file_key``s over Neo4j.
        kind: The keyword's name for the error message; ``"module"`` for ``get_classes``, whose
            single-valued keyword routes through here as a one-element sequence.

    **Resolution is many-to-one, and the result is de-duplicated.** Leniency is the whole point of
    :func:`resolve_module_key` — ``"pkg/a.py"`` and ``"/abs/pkg/a.py"`` are two spellings a scanner
    may plausibly hand over for the *same* module — so two requested paths legitimately collapse to
    one key and the caller gets one entry back. Raising on the collapse would punish the very
    caller the leniency exists for; de-duplicating explicitly is what keeps the returned list from
    naming the same module twice and asking both backends to fetch it twice.

    Raises:
        TypeError: ``paths`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``paths`` is an empty sequence.
        SelectorNotInGraph: a path names no module in this application.
    """
    reject_bare_string(kind, paths)
    if paths is None:
        return None
    known = list(keys)
    resolved = [resolve_module_key(p, known) for p in paths]
    check_selector(kind, list(paths), [p for p, r in zip(paths, resolved) if r not in known])
    return list(dict.fromkeys(resolved))


def call_graph_scope(roots: Sequence[str] | None, depth: int | None) -> List[str] | None:
    """Normalise :meth:`PythonAnalysisBackend.get_call_graph`'s scoping keywords.

    Returns the roots as a list, or ``None`` for "the whole application" — the unscoped call,
    which must keep behaving exactly as it did before the keywords existed.

    Both backends route through this so the two cannot drift apart on what a keyword combination
    means (the failure mode Fix 1 of leg 1.5 had to go back and repair on the child-fetch paths).
    Whether each root *exists* is checked later, by whichever backend has the graph in hand, but
    through the same :func:`check_selector` — see :func:`bounded_subgraph`.

    Raises:
        TypeError: ``roots`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``depth`` that is not a positive ``int``, ``depth`` without ``roots``, or an
            empty ``roots``. A hop budget with no origin to count from has no meaning, and quietly
            returning all 364,752 edges would be the worst of the available answers — the caller
            asked for a bounded graph and would be handed an unbounded one with no signal.
            ``depth`` is type-checked rather than merely range-checked because the two ways of
            getting it wrong are silent otherwise: ``depth="2"`` raised ``TypeError`` from the
            comparison, and ``depth=2.5`` was accepted and truncated to 2 by the Cypher/ego-graph
            radius. ``bool`` is rejected for the same reason — ``depth=True`` is ``1`` by accident.
    """
    if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 1):
        raise ValueError(f"depth must be an int >= 1, got {depth!r}")
    reject_bare_string("roots", roots)
    if roots is None:
        if depth is not None:
            raise ValueError("depth= requires roots=; a hop budget needs an origin to count from")
        return None
    check_selector("roots", list(roots), ())
    return list(roots)


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


class PythonAnalysisBackend(AnalysisBackend[PyApplication, PyModule, PyClass, PyCallable, PyClassAttribute, str]):
    """Abstract base every Python analysis backend implements.

    A backend owns all indexing and query logic for a Python application; the
    :class:`PythonAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.python`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so backends are behaviorally interchangeable.

    The application/symbol-table/call-graph/class/method/field/parameter accessors are inherited
    from :class:`~cldk.analysis.commons.backend.AnalysisBackend`; everything below is Python-specific.
    """

    # -----[ bounded enumeration ]-----
    # These three are declared on the generic AnalysisBackend without keywords; Python widens them
    # with defaulted, keyword-only scoping arguments so whole-application enumeration is the
    # exception a caller asks for rather than the default shape. Every keyword defaults to the
    # pre-existing behaviour, so no existing call site changes and no public signature moves.
    # Redeclared here rather than left to the generic base because a reader of *this* contract
    # would otherwise see the unwidened signature and believe it.
    @abstractmethod
    def get_symbol_table(self, *, paths: Sequence[str] | None = None) -> Dict[str, PyModule]:
        """The symbol table, keyed by file path.

        Args:
            paths: Restrict to these modules, named by symbol-table key (equivalently, the module's
                file path). Resolved leniently through :func:`resolve_module_key`, so an absolute
                path or one with native separators finds its module. A path naming no module in
                the application raises :class:`~cldk.utils.exceptions.SelectorNotInGraph`, and an
                empty sequence raises ``ValueError`` — see :func:`scope_paths`. ``None`` (the
                default) returns every module.
        """

    @abstractmethod
    def get_all_classes(self, *, module: str | None = None) -> Dict[str, PyClass]:
        """Top-level classes, keyed by signature.

        Args:
            module: Restrict to the classes declared by one module, named the same way
                :meth:`get_symbol_table`'s ``paths`` names one — a symbol-table key, not a dotted
                module name, and a key naming no module raises the same way. ``None`` (the default)
                returns the whole application's classes.
        """

    @abstractmethod
    def get_call_graph(self, *, roots: Sequence[str] | None = None, depth: int | None = None) -> nx.DiGraph:
        """The call graph, as a NetworkX ``DiGraph`` keyed by callable signature.

        Args:
            roots: Restrict to the sub-graph reachable from these callables, named by signature
                (or, for an external ghost, by its ``@external`` can-id — the same strings that
                appear as graph nodes). ``None`` (the default) returns the whole application.
            depth: Maximum number of call hops from a root. ``None`` means unbounded.

        The result is the **induced** sub-graph over the reached nodes (see
        :func:`bounded_subgraph`), identically on both backends.

        Raises:
            ValueError: ``depth`` that is not an ``int`` >= 1, ``depth`` given without ``roots``,
                or an empty ``roots`` (see :func:`call_graph_scope`).
            SelectorNotInGraph: a root that is neither a callable this application declares nor a
                node of the call graph. "No such callable" and "a callable that calls nothing" are
                different answers; the second is a graph of one node, including when the callable
                has no call edge at all (see :func:`bounded_subgraph` for the domain both backends
                validate against).
        """

    @abstractmethod
    def get_modules(self) -> List[PyModule]:
        """All modules."""

    @abstractmethod
    def get_python_module(self, file_path: str) -> PyModule | None:
        """The module for a file path."""

    @abstractmethod
    def get_python_file(self, qualified_class_name: str) -> str | None:
        """The file path declaring the given symbol."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""

    # -----[ classes ]-----
    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """The classes declared inside a class."""

    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Classes that extend the given class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends."""

    # -----[ methods / fields ]-----
    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        """All methods grouped by their owning class signature."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """The constructors of a class.

        Note:
            This accessor (and :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_method`
            / :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_all_methods_in_class`)
            does not resolve call-site ``callee_signature`` the way :meth:`get_callsites_for`
            does for the identical call sites: over the Neo4j backend it is always ``None`` here
            (that reconstruction never follows ``PY_RESOLVES_TO``); over the local backend an
            external target keeps Jedi's raw, unaddressable dotted guess instead of the resolved
            ``@external`` can-id. Use :meth:`get_callsites_for` when resolved call sites matter.
        """

    # -----[ bulk / projected accessors ]-----
    # Set-at-a-time, field-projected reads — one round-trip on the Neo4j backend, one symbol-table
    # walk in-process — for callers that enumerate the whole application and would otherwise pay the
    # per-entity reconstruction of get_all_methods_in_application.
    @abstractmethod
    def get_callables_overview(self) -> List[PyCallableOverview]:
        """A lightweight projection of every callable in the application (methods, module-level and
        nested functions), without the full :class:`PyCallable` reconstruction."""

    @abstractmethod
    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Source bodies for the given callable signatures, keyed by signature. Signatures with no
        matching callable are omitted, as are callables whose ``code`` is ``None`` (e.g. synthesized
        callables the analyzer emits with no source text) — every returned value is a real ``str``."""

    @abstractmethod
    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        """Overviews of callables decorated with any of ``markers`` (matched against the decorator
        names)."""

    @abstractmethod
    def get_entrypoints(self) -> List[PyCallableOverview]:
        """Overviews of every *callable* the analyzer marked as an entrypoint (``PyCallable.
        is_entrypoint``) — a route handler, CLI command, or other externally-invoked callable the
        entrypoint-detection pass already found. An empty list means the pass found no entrypoint
        *callables*, the ordinary "no entrypoints in this project" case for this accessor — never a
        stand-in for the mark not existing at all (the graph carries ``is_entrypoint`` as a real
        boolean, never dropped, so it's never ambiguous at the property level).

        Two things this accessor alone cannot tell you, each answered by a sibling instead of by
        widening this one's frozen ``List[PyCallableOverview]`` return:

        * **Class-level entrypoints.** ``PyClass`` carries its own ``is_entrypoint``/``entrypoints``
          (a class-based view — a Django/Flask CBV, say — marked at the class with no individually
          marked method). This walk is callables-only and never sees those; use
          :meth:`get_entrypoint_classes` for them. A ``PyClass`` is not a callable, so it is not
          folded into this list under a synthetic ``kind`` — that would misrepresent what
          ``PyCallableOverview`` means.
        * **Whether the pass itself had gaps.** The analyzer's own ``PyEntrypointReport`` docstring
          says detection "under-approximates by design, so silence is its failure mode" — an empty
          (or any) result here cannot distinguish "ran clean, found none" from "had gaps" on its
          own. Use :meth:`get_entrypoint_coverage` for that signal.

        Declared here rather than on the generic cross-language ABC: Java stamps a ``JEntrypoint``
        marker label and TypeScript carries them on ``TSApplication.entrypoints`` — a third
        spelling of the same idea — and unifying that vocabulary across languages is out of scope
        for this change."""

    @abstractmethod
    def get_entrypoint_classes(self) -> List[PyClassOverview]:
        """Overviews of every *class* the analyzer marked as an entrypoint in its own right
        (``PyClass.is_entrypoint``) — the class-level sibling of :meth:`get_entrypoints`, which
        walks callables only and so never sees a class-based view marked at the class with no
        individually-marked method. Same empty-vs-absent guarantee as :meth:`get_entrypoints`."""

    @abstractmethod
    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Coverage and failure record for the entrypoint-detection pass (``PyEntrypointReport``),
        so a caller can tell "the pass ran clean and found nothing" apart from "the pass had gaps"
        — a distinction :meth:`get_entrypoints`'s empty list alone cannot make. See
        :class:`~cldk.analysis.commons.results.EntrypointCoverage` for the field-by-field contract,
        including the per-backend availability caveat (the Neo4j projection does not carry this
        report at all; that backend answers with a ``diagnostics``-only result rather than
        fabricating empty-but-clean-looking coverage fields)."""

    @property
    @abstractmethod
    def has_resolution_edges(self) -> bool:
        """Whether this backend can resolve call-site ``callee_signature`` at all right now.

        ``get_callsites_for``'s per-site ``callee_signature`` is ``None`` both for "genuinely
        unresolved" and, on the Neo4j backend, for "this graph was populated at an analysis level
        below the one where the defuse-linker backfill runs, so ``PY_RESOLVES_TO`` doesn't exist
        at all" — ``PyCallsite`` is the analyzer's own frozen model with no field to carry that
        distinction. This is the disambiguator: ``False`` means every ``None`` from
        ``get_callsites_for`` is explained by that, not by individual call sites failing to
        resolve.

        The local backend always attempts resolution via Jedi regardless of analysis level (see
        :meth:`get_callsites_for`'s local-vs-Neo4j caveat), so it is unconditionally ``True``
        there. The Neo4j backend probes for at least one ``PY_RESOLVES_TO`` edge once at
        connection time.
        """

    @abstractmethod
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        """Call sites of the given callable signatures, keyed by owning signature. Each existing
        signature gets an entry (an empty list if it has no call sites); signatures with no matching
        callable are omitted.

        Every returned :class:`~cldk.models.python.PyCallsite`'s ``callee_signature`` is resolved
        through the union of declared callables and :meth:`get_external_symbols` rather than left
        in Jedi's raw, unaddressable dotted-name form for a library/builtin target (field data:
        602 recorded incidents were ``callee_signature=None`` for exactly this case) — a call to a
        declared callable keeps its dotted signature; a call to something outside the project
        resolves to the ``can://…/@external/…`` id under which :meth:`get_external_symbols` files
        it, so ``get_external_symbols()[site.callee_signature]`` finds it. ``None`` still means the
        call site is genuinely unresolved (Jedi failed and no backfilled resolution exists) — the
        one case this cannot and must not manufacture an answer for.

        One caveat, inherent to what each backend can see rather than a bug: the local backend can
        always attempt this (Jedi's own guess is present regardless of analysis level), but the
        Neo4j backend can only follow a call's ``PY_RESOLVES_TO`` edge — present only when the
        graph was populated at an analysis level where the defuse-linker backfill ran (``-a 2`` or
        higher). A ``None`` from the Neo4j backend can therefore mean either "genuinely
        unresolved" or "this graph doesn't carry per-site resolution at all" — ``PyCallsite`` is
        the analyzer's own frozen model with no field to carry that distinction, so it cannot be
        disambiguated here the way an accessor's own empty return could be. Partial mitigation:
        :attr:`has_resolution_edges` is ``False`` exactly when the Neo4j backend's attached graph
        has *no* ``PY_RESOLVES_TO`` edge anywhere — in that case every ``None`` here is explained
        by the graph's analysis level, not by individual call sites failing to resolve. (The
        local backend is always ``True`` here — see :attr:`has_resolution_edges`.)
        """

    @abstractmethod
    def get_external_symbols(self) -> Dict[str, PyExternalSymbol]:
        """Every call-graph endpoint outside the analyzed project — an imported library or builtin
        member — keyed by its ``can://…/@external/…`` id, the ``@external`` can-id
        :class:`~cldk.models.python.PyExternalSymbol` is filed under. The analyzer mints one of
        these ghost symbols for every call target that isn't a declared class/callable, precisely
        so no call-graph edge dangles; this is how a caller resolves one, and how
        :meth:`get_callsites_for` addresses a resolved external ``callee_signature``.

        Declared here rather than on the generic cross-language ABC: ``PyExternalSymbol`` is
        ``codeanalyzer-python``'s own model, with no cross-language equivalent yet (mirrors why
        :meth:`get_entrypoints` stays Python-specific despite a shared-looking return type).

        An empty dict means this project's call graph has no calls outside itself — a real,
        unambiguous fact on both backends, not a stand-in for "can't tell": external symbols are
        homed from the aggregate call graph, which every analysis level and the Neo4j projection
        both carry unconditionally (unlike the per-call-site backfill :meth:`get_callsites_for`'s
        docstring caveats)."""

    @abstractmethod
    def get_config_readers(self, key: str) -> List[PyCallableOverview]:
        """Overviews of every callable that reads configuration key ``key``, resolved from
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_config_uses`'s edges.

        That generic accessor hands back ``PyConfigUseEdge.src`` as an opaque GLOBAL ordinal id
        (``<callable-id>@<local-id>``) — resolving it to "which callable" requires knowing
        ``codeanalyzer-python``'s id grammar, so this stays Python-specific even though its return
        type (``PyCallableOverview``) is the same shared projection :meth:`get_entrypoints` and
        :meth:`get_callables_overview` already return. Empty means no callable reads this key,
        which is not the same as "a read exists but never resolved to a key" — see
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_unresolved_config_reads` for
        that case.
        """

    # -----[ locate ]-----
    # The v2 query-facade spec's D3. Declared here rather than on the generic cross-language ABC
    # because LocateResult carries codeanalyzer-python's BodyNode/Span — see
    # cldk/analysis/commons/backend.py's module docstring for why that stays out of the shared
    # contract until a second language implements it.
    @abstractmethod
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        Four outcomes, kept distinguishable rather than collapsed into an ambiguous empty: inside a
        callable (``callable`` set, and ``node`` set too when a body node is that precise); at module
        scope (a real position with no enclosing callable — a ``module_scope`` diagnostic); in the
        gap between two callables (also module scope, and never silently snapped to the nearest
        callable); or in a file the graph has no module for (``file_not_in_graph``).

        Args:
            path: The file path. Normalised against the backend's module keys, so a ``./``-prefixed
                or absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.
        """

    @abstractmethod
    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in one round trip, in input order.

        The bulk form, not an optimisation over :meth:`locate`: a scanner hands over a whole alert
        set at once, and round trips cost latency for a person and context for an agent.
        """

    # -----[ addressing ]-----
    # A caller names things the way it already thinks of them; the SDK resolves. Nothing here takes
    # or returns a ``can://`` URI (E6), and nothing takes an ordinal (E7). The resolution *policy*
    # is not implemented per backend -- both route through
    # :mod:`cldk.analysis.commons.resolve`, so they cannot drift on what "ambiguous" means. What a
    # backend implements is only how it produces the candidates.
    @abstractmethod
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the callable it names.

        The **candidate domain is every callable in the analysed application** -- exactly the set
        :meth:`get_callables_overview` reports: module-level functions, class methods, and
        callables nested inside either, in the modules belonging to this application. Both backends
        must resolve against that same domain; a shared *predicate* over different *sets* is not
        parity.

        ``name`` is matched whole or as a dotted suffix on segment boundaries (``"execute"`` names
        any ``….execute``; ``"cursor.execute"`` narrows), with an exact match winning outright.
        ``in_class`` / ``in_module`` disambiguate rather than scope -- a callable is the unit of
        address, so there is nothing to scope it *to* -- and are matched the same segment-wise way
        against the owning class's signature and the module's repo-relative path.

        Args:
            name: The callable name, whole or a dotted suffix of its signature.
            in_class: Keep only callables owned by the class this names.
            in_module: Keep only callables in the module this names.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind="callable"``, the
            callable's dotted signature in ``callable``, and its opaque graph id in ``ref``. That
            ``ref`` round-trips through :meth:`get_source` on either backend -- the one sanctioned
            use of an opaque id.

        Raises:
            AmbiguousName: More than one callable matched, listing every match. The resolver never
                picks: 86% of leaf names in a real application are unique and the rest are
                framework methods, where a guess is a confident wrong answer.
            SelectorNotInGraph: Nothing matched. No near-miss suggestions -- E8 puts typo-tolerant
                matching out of scope in the error path as much as in the resolver.
        """

    @abstractmethod
    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable to the position that carries it.

        A value name is scoped by its callable (spec § 5.2), so ``within`` is required and is
        itself resolved by :meth:`resolve_callable` -- ``within="PaymentPortal.invoice_transaction"``
        is enough; the full signature is not needed.

        The **candidate domain is the resolved callable's ``formal_in`` vertices**: every named
        value that *enters* it, which is what a backward slice seeds from. Three things do, and
        they are not all parameters -- on a real application 84% of them are captured module
        globals, with a small tail of closure captures -- so the answer carries
        ``kind="parameter"``, ``"global"`` or ``"capture"``, always addressed by name with no
        ordinal anywhere in it (E7).

        The domain is deliberately *not* every body node carrying a variable. Two reasons, both
        measured: the same name also appears on the callable's ``formal_out`` vertex and at each of
        its call sites' actuals, so collapsing those into one namespace would make every mutated
        parameter ambiguous with its own exit value; and **a local variable has no address here at
        all** -- ``var`` is non-null only on the four parameter-passing kinds (``formal_in``,
        ``formal_out``, ``actual_in``, ``actual_out``: 680,321 vertices on a real application, all
        with a ``var``), while every other kind carries ``var = NULL`` without exception
        (``statement``, ``call``, ``return``, ``branch``, ``loop``, ``raise``, ``handler``,
        ``entry``, ``exit``: 204,897 vertices, none with one). A name that is only ever assigned
        and read inside the body is not resolvable through this method; ``locate(path, line)`` is
        what addresses those positions.

        A parameter or a capture is named by its bare name. A **global** is named
        ``"<module_name>.<name>"``, matched by the same segment rule as a callable signature, so
        ``"AccessError"`` names it and ``"payment.AccessError"`` narrows when the callable captures
        that name from several modules (measured: 14,432 such (callable, leaf name) pairs, and no
        two values whose qualified names collide -- so the qualified spelling always resolves).

        Args:
            name: The value's name, as written in the source; for a global, optionally qualified
                by its defining module.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`. It takes
                no ``in_class=`` / ``in_module=``: ``within`` is matched segment-wise against the
                whole signature, so naming more of the dotted path narrows by class and module
                already, and that is what an ambiguity raised here advises.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind`` one of
            ``"parameter"`` / ``"global"`` / ``"capture"``, ``name`` set to the readable identifier
            (never the analyzer's ``"<global>:payment::AccessError"`` spelling), ``defined_in`` set
            to the defining module for a global, ``file``/``line`` pointing at the *callable's*
            definition (these are dataflow vertices with no span of their own), and the vertex's
            opaque graph id in ``ref``.

        Note:
            Because these vertices have no span, ``ref`` does **not** round-trip through
            :meth:`get_source` on either backend -- there is no source text to return. Only a
            :meth:`resolve_callable` ``ref`` does.

        Raises:
            AmbiguousName: ``within`` named more than one callable, or more than one value matched.
            SelectorNotInGraph: No such callable, or no such value in it.
        """

    # -----[ source access ]-----
    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """Source text for one node, named by ``node_id``.

        Generalises body access below callable granularity: ``node_id`` is either a callable's
        signature (the same key :meth:`get_method_bodies` uses) or the opaque body-node id
        :attr:`LocateResult.node_id` hands back alongside :attr:`LocateResult.node`, so a caller
        can re-fetch the precise statement or call site :meth:`locate` found, not just its
        enclosing callable. The body-node form is the analyzer's own id
        (``"<callable can:// id>@<body key>"``) — round-tripped, never composed by the caller.

        Raises:
            KeyError: No callable has that signature, no body node has that key, or the node
                exists but carries no recoverable source (no span — e.g. an abstract stub).
            NotImplementedError: (Neo4j backend only) ``node_id`` names a body node. The graph
                projects per-callable text (``:PyCallable.code``) but nothing below that —
                ``:PyBodyNode`` carries a line span and no text to slice, and ``:PyModule`` carries
                no source either (see :class:`~cldk.analysis.commons.results.LocateResult`). Only
                the local codeanalyzer backend, which holds the module's real text and byte
                offsets, can answer for a statement or call site.
        """
