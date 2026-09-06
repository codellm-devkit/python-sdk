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

"""The TypeScript analysis backend contract.

:class:`TypeScriptAnalysis` is a thin façade that delegates every query to a *backend*. Two
interchangeable backends exist:

* :class:`~cldk.analysis.typescript.codeanalyzer.TSCodeanalyzer` — walks the in-memory pydantic
  ``TSApplication`` / a NetworkX call graph built from ``analysis.json``;
* :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend` — answers the *same* queries with
  Cypher over the graph ``codeanalyzer-typescript`` emits with ``--emit neo4j``.

The shape shared with every other language — application view, symbol table, call graph, the
class/method/field lookups and the repository-artifact layer — is inherited from the generic
:class:`~cldk.analysis.commons.backend.AnalysisBackend`; what is declared here is the
TypeScript-native remainder (interfaces, type aliases, enums, namespaces, decorators, the
1.x call-site accessors and the bulk projections). Both backends subclass it; the façade is typed
against it. Backend-specific lifecycle (e.g. the Neo4j driver's ``close()`` / context-manager
support) is intentionally *not* part of the contract.

The call graph both backends return keeps TypeScript's own endpoints (decision TS-11): cants emits
a module as the caller of its top-level code and a class as the callee of ``new X()``, and both
are kept, tagged with a ``kind`` node attribute (:data:`CALL_GRAPH_NODE_KINDS`) so a
caller wanting Python's callable-only shape filters in one line rather than the SDK erasing every
top-level call.
"""

from __future__ import annotations

from abc import abstractmethod
from functools import partial
from typing import ClassVar, Dict, List, Sequence, Set, Tuple

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.graphs import as_slice_node
from cldk.analysis.commons.keys import module_dotted
from cldk.analysis.commons.results import LocateResult, SliceNode
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSField,
    TSImport,
    TSInterface,
    TSModule,
    TSSynthesizedCallable,
    TSType,
    TSTypeAlias,
    TSVariableDeclaration,
)


#: The ``kind`` vocabulary of a call-graph node: what the id index holds — a module, any of the
#: five type kinds (a class is the callee of ``new X()``; the others are indexed and would be kept
#: if the analyzer ever emitted an edge to one), a callable, or an external.
CALL_GRAPH_NODE_KINDS = frozenset({"module", "class", "interface", "enum", "type_alias", "namespace", "callable", "external"})

#: Every source extension a TypeScript module key can end in, across both id prefixes. Used to
#: derive a module's dotted name from its key, so ``in_module=`` can be written either way.
TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

#: How TypeScript spells a module key as a dotted name. ``package_index=None`` is the ruling, not
#: an omission: :func:`~cldk.analysis.commons.keys.module_dotted` strips a trailing ``/__init__``
#: because that is how Python addresses a package, and nothing stops a TypeScript project having a
#: file called ``__init__.ts`` -- which is a module in its own right and must dot to
#: ``…__init__``. (superset-frontend has none, so this fires nowhere on the reference corpus; the
#: parameter is here so it cannot fire wrongly on a corpus that does.) TypeScript's own index
#: convention (``index.ts``) is deliberately *not* stripped either: ``src/foo/index.ts`` is
#: addressed as ``src/foo/index.ts`` everywhere else on this surface, so it dots to
#: ``src.foo.index``.
ts_module_dotted = partial(module_dotted, extensions=TS_EXTENSIONS, package_index=None)


class TSAnalysisBackend(AnalysisBackend[TSApplication, TSModule, TSType, TSCallable, TSField, str]):
    """Abstract base every TypeScript analysis backend implements.

    A backend owns *all* indexing and query logic for a TypeScript application; the
    :class:`TypeScriptAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.typescript`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so the two backends are behaviorally interchangeable.

    Inherited abstract (see :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "TS"
    N: ClassVar[str] = "TS"

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_modules(self) -> List[TSModule]:
        """All modules (compilation units)."""

    @abstractmethod
    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """Phantom (external) call targets — imported/required library members and builtins —
        keyed ``"<module>.<name>"``, the key the call graph uses for them; the wire's ``can://``
        id is on the value."""

    @abstractmethod
    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """The application's anonymous callables, each value carrying the ``can://`` tree id of
        the callable it stands for. Empty below level 2.

        **The key is backend-dependent**, and each backend's own docstring says which it uses: a
        backend reading ``analysis.json`` passes the analyzer's compatibility index through as
        emitted, so the key is the *older* anonymous id and the value's ``id`` is the tree id that
        replaced it (key != ``id``); a backend reading the Neo4j projection has the tree nodes and
        not the index, so it keys by the node's own id (key == ``id``). Do not key a cross-backend
        lookup on this map -- ask for the value's ``id``."""

    @abstractmethod
    def get_typescript_file(self, qualified_name: str) -> str | None:
        """The file path declaring the symbol with the given signature."""

    @abstractmethod
    def get_typescript_module(self, file_path: str) -> TSModule | None:
        """The module for a file path."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""

    @abstractmethod
    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base class."""

    # -----[ call sites ]-----
    @abstractmethod
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        """The syntactic call sites inside a callable — its ``body`` nodes of ``kind == "call"``,
        with the resolved callee mapped to its signature."""

    @abstractmethod
    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted source lines anywhere in the project where ``target_signature`` is invoked."""

    @abstractmethod
    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The call targets invoked from a callable, derived from its call sites."""

    # -----[ interfaces / enums / type-aliases ]-----
    @abstractmethod
    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        """Every interface, keyed by signature."""

    @abstractmethod
    def get_all_enums(self) -> Dict[str, TSEnum]:
        """Every enum, keyed by signature."""

    @abstractmethod
    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        """The members of an enum."""

    @abstractmethod
    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        """Every type alias, keyed by signature."""

    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        """The classes declared inside a class -- on schema v2 always ``[]``, on every backend: a
        class holds only ``callables`` and ``fields``, so no class nests a type. A class declared
        inside a *callable* survives as ``TSCallable.inner_classes``. Kept for the 1.x surface."""

    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        """Classes that extend/implement the given class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends (base classes minus implemented interfaces)."""

    @abstractmethod
    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """The interfaces a class implements."""

    # -----[ methods / functions / fields ]-----
    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        """All methods grouped by their owning class/interface signature."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        """The constructors of a class."""

    @abstractmethod
    def get_all_functions(self) -> Dict[str, TSCallable]:
        """Top-level (module/namespace) functions, keyed by signature."""

    @abstractmethod
    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        """The properties of an interface."""

    # -----[ imports / exports / variables ]-----
    @abstractmethod
    def get_imports(self) -> Dict[str, List[TSImport]]:
        """Per-file import bindings."""

    @abstractmethod
    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        """Per-file export bindings."""

    @abstractmethod
    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        """Per-file module-level variable declarations."""

    # -----[ decorators ]-----
    @abstractmethod
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        """Structured decorators applied to a callable."""

    @abstractmethod
    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        """Structured decorators applied to a class."""

    @abstractmethod
    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of callables carrying it."""

    @abstractmethod
    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of classes carrying it."""

    # -----[ bulk / projected accessors ]-----
    # Set-at-a-time, field-projected reads — one round-trip on the Neo4j backend, one symbol-table
    # walk in-process — for callers that enumerate the whole application and would otherwise pay the
    # per-entity reconstruction of get_all_methods_in_application.
    @abstractmethod
    def get_callables_overview(self) -> List[TSCallableOverview]:
        """A lightweight projection of every callable in the application (methods, module-level,
        namespace-level, and nested/inner functions), without the full :class:`TSCallable`
        reconstruction.

        Known limitation: a ``get x()``/``set x()`` accessor pair shares one ``signature``, so
        this (and the other bulk accessors) can diverge between backends on a paired accessor —
        see `#300 <https://github.com/codellm-devkit/python-sdk/issues/300>`_."""

    @abstractmethod
    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Source bodies for the given callable signatures, keyed by signature. Signatures with no
        matching callable are omitted, as are callables with no source text (an implicit
        constructor the analyzer synthesizes has an empty span, so its ``code`` is ``""``; 1.x
        carried ``None``) — every returned value is a real, non-empty ``str``."""

    @abstractmethod
    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        """Overviews of callables decorated with any of ``markers`` (matched against the decorator
        names)."""

    @abstractmethod
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        """Call sites of the given callable signatures, keyed by owning signature. Each existing
        signature gets an entry (an empty list if it has no call sites); signatures with no matching
        callable are omitted."""

    # =====================================================================================
    # The addressing surface (leg 2.5b, TS-2). Python's semantics are the contract: every
    # signature below is `cldk/analysis/python/backend.py`'s, keyword-for-keyword.
    #
    # A caller names things the way it already thinks of them; the SDK resolves. Nothing here takes
    # or returns a ``can://`` URI outside ``ref`` / ``node_id`` (E6), and nothing takes an ordinal
    # (E7). The resolution *policy* is not implemented per backend -- both route through
    # :mod:`cldk.analysis.commons.resolve`, so they cannot drift on what "ambiguous" means. What a
    # backend implements is only how it produces the candidates.
    # =====================================================================================
    @abstractmethod
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        Four outcomes, kept distinguishable rather than collapsed into an ambiguous empty: inside a
        callable (``callable`` set, and ``body`` set too when a body node is that precise); at
        module scope (a real position with no enclosing callable -- a ``module_scope`` diagnostic);
        in the gap between two callables (also module scope, and never silently snapped to the
        nearest callable); or in a file the analysis has no module for (``file_not_in_graph``).

        Args:
            path: The file path. Normalised against the backend's module keys
                (:func:`~cldk.analysis.commons.keys.resolve_module_key`), so a ``./``-prefixed or
                absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.
        """

    @abstractmethod
    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in one round trip, in input order.

        The bulk form, not an optimisation over :meth:`locate`: a scanner hands over a whole alert
        set at once, and round trips cost latency for a person and context for an agent.
        """

    @abstractmethod
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the callable it names.

        The **candidate domain is every callable in the analysed application** -- exactly the set
        :meth:`get_callables_overview` reports: module- and namespace-level functions, class and
        interface methods, and callables nested inside either. Both backends resolve against that
        same domain; a shared *predicate* over different *sets* is not parity.

        ``name`` is matched whole or as a dotted suffix on segment boundaries (``"show"`` names any
        ``….show``; ``"UserController.show"`` narrows), with an exact match winning outright.
        ``in_class`` / ``in_module`` disambiguate rather than scope -- a callable is the unit of
        address -- and are matched the same segment-wise way against the owning class's or
        interface's signature and against the module. ``in_module`` takes a module-key suffix
        (``"src/controllers.ts"``, ``"controllers.ts"``) **or** the dotted form
        (``"src.controllers"``, ``"controllers"``) that TypeScript signatures are spelled in; the
        two never cross, because a ``/`` spelling never matches a dotted candidate.

        **An anonymous callable is addressed by its signature, never by its name.** cants gives
        every one of them the name ``"(anonymous)"`` (7,044 on superset-frontend) and a *unique*
        signature ending in ``<anon@line:column>``, so ``resolve_callable("<anon@22:52>")`` is the
        address. The display name is not one: this resolver matches *signatures*, and no signature
        carries ``"(anonymous)"``, so that spelling misses outright rather than becoming a
        7,044-way ambiguity -- an honest "no such callable", not a list nobody could choose from.

        **A declaration-merged name resolves to the callable facet or to nothing, never to the
        wrong facet.** ``const X = () => …`` beside ``interface X`` shares one id, and the Neo4j
        emitter collapses the two onto a single node carrying both labels and one ``kind`` (three
        such nodes on superset-frontend, two of them callables). This accessor's domain is the
        ``kind``, not the label: a node whose ``kind`` is a callable kind is a candidate here and a
        node whose ``kind`` names a type facet is not, so a merged node can never come back
        described as something it is not.

        Returns:
            A :class:`~cldk.analysis.commons.results.SliceNode` with ``kind="callable"``, the
            callable's dotted signature in ``callable``, and its opaque graph id in ``ref``. That
            ``ref`` round-trips through :meth:`get_source` on either backend -- the one sanctioned
            use of an opaque id.

        Raises:
            AmbiguousName: More than one callable matched, listing every match and nothing else.
                The resolver never picks: a guess presented as an answer is the confident wrong
                answer this layer exists to prevent.
            SelectorNotInGraph: Nothing matched, naming the selector as the caller spelled it --
                or, when the name matched and a keyword excluded every match, naming that keyword.
                No near-miss suggestions: E8 puts typo-tolerant matching out of scope in the error
                path as much as in the resolver.
        """

    @abstractmethod
    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable to the position that carries it.

        A value name is scoped by its callable, so ``within`` is required and is itself resolved by
        :meth:`resolve_callable` -- ``within="UserController.show"`` is enough.

        The **candidate domain is the resolved callable's ``formal_in`` vertices**: every named
        value that *enters* it, which is what a backward slice seeds from. In TypeScript those are
        parameters and nothing else, so the answer's ``kind`` is always ``"parameter"`` and
        ``defined_in`` is always ``None`` -- unlike Python, where 84% of entering values are
        captured module globals and the analyzer marks them with a ``"<global>:mod::name"``
        grammar. cants emits no such grammar (measured on superset-frontend: of 10,465 ``formal_in``
        vertices none carries a marker prefix), so nothing is translated and the name a caller
        writes is the name the analyzer wrote.

        The name is the parameter's source text, which for a destructured parameter is a pattern
        (``"{ theme }"``, 948 of them on superset-frontend) rather than an identifier. That is
        reported as it is: inventing an identifier for a pattern would be a fabricated address.

        The domain is deliberately *not* every body node carrying a value: ``of`` is non-null only
        on the four parameter-passing kinds, and the same name also appears on the callable's
        ``formal_out`` vertex and at each call site's actuals, so collapsing them would make every
        parameter ambiguous with its own exit value. A local variable has no address here at all;
        :meth:`locate` is what addresses those positions.

        Note:
            The returned ``ref`` does **not** round-trip through :meth:`get_source` on either
            backend -- a ``formal_in`` vertex is a dataflow position with no span, so there is no
            text to return for one. Only a :meth:`resolve_callable` ``ref`` round-trips.

        Raises:
            AmbiguousName: ``within`` named more than one callable, or more than one value matched.
            SelectorNotInGraph: No such callable, or no such value in it.
        """

    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """Source text for one node, named by ``node_id``.

        Generalises :meth:`get_method_bodies` below callable granularity: ``node_id`` is a
        callable's signature, a callable's ``can://`` id, or the opaque body-node id
        :attr:`~cldk.analysis.commons.results.LocateResult.node_id` hands back -- so a caller can
        re-fetch the precise statement or call site :meth:`locate` found, not just the callable
        enclosing it. Round-tripped, never composed by the caller: a TypeScript body-node id is
        ``<callable id>@<body key>`` and a callable id may itself contain an ``@``
        (``…/<anon@22:52>``), so it cannot be taken apart by splitting on one.

        Raises:
            KeyError: Nothing carries that id or signature, or it carries no recoverable source
                (a ``formal_in`` vertex, or the implicit constructor cants synthesizes with an
                empty span -- both backends refuse rather than returning ``""`` as if it were a
                body).
            NotImplementedError: (Neo4j backend only) ``node_id`` names a body node. The graph
                projects per-callable text (``:TSCallable.code``) but nothing below it --
                ``:TSBodyNode`` carries a line span and no text, and ``:TSModule`` carries no
                source to slice one out of. Only the local backend, which holds the module text and
                the analyzer's offsets, can answer for a statement or call site.
        """

    @property
    @abstractmethod
    def has_resolution_edges(self) -> bool:
        """Whether this backend can resolve a call site's ``callee_signature`` at all right now.

        :meth:`get_callsites_for`'s per-site ``callee_signature`` is ``None`` both for "genuinely
        unresolved" and for "this view was built below the analysis level where callee resolution
        runs" -- :class:`~cldk.models.typescript.TSCallsite` has no field to carry the distinction.
        This is the disambiguator: ``False`` means every ``None`` from :meth:`get_callsites_for` is
        explained by that, not by individual call sites failing to resolve.

        Unlike Python's, the local TypeScript backend is **not** unconditionally ``True``: cants
        resolves callees in its own level-2 pass and writes ``callee: null`` on every call node
        below it (there is no Jedi-style resolver running regardless of level), so the honest
        answer is whether the analysis actually reached that level.
        """

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in :attr:`~cldk.analysis.commons.results.SliceNode.source` for these positions.

        A second call because addressing answers *where* and source answers *what*, and source is
        the one field with no size ceiling (E4). Returns the **same**
        :class:`~cldk.analysis.commons.results.SliceNode` type, so nothing downstream has to branch
        on whether a node has been through here, and accepts anything carrying an address -- slice
        nodes and :meth:`locate` results alike (see :func:`as_slice_node`).

        **One round trip regardless of node count.** Implemented here rather than in each backend
        precisely so that cannot drift: the whole batch resolves through a single
        :meth:`_sources_for` call.

        Afterwards ``source=None`` means exactly one thing: *this position exists and the backend
        has no text for it*. It never means "the lookup failed", because a ref naming nothing raises
        instead. Which positions have no text differs by backend, honestly: a ``kind="callable"``
        node hydrates on both; a ``formal_in`` vertex hydrates on neither (it has no span in the
        analyzer's own model); a statement or call site hydrates only locally, because the graph
        carries no text below callable granularity.

        Raises:
            KeyError: A ``ref`` names nothing this backend can find -- a ref comes from this SDK,
                so one that resolves to nothing means a stale or foreign address, which is worth
                stopping on rather than discovering three layers later. The message names the
                positions in the caller's vocabulary, never by ``ref`` (E6).
            TypeError: An element carries no ``ref`` (see :func:`as_slice_node`).
        """
        out = [as_slice_node(n) for n in nodes]
        if not out:
            return []
        sources = self._sources_for([n.ref for n in out])
        missing = [n for n in out if n.ref not in sources]
        if missing:
            named = [f"{n.callable} ({n.file}:{n.line})" if n.file else n.callable for n in missing[:5]]
            raise KeyError(f"{len(missing)} of {len(out)} positions name nothing in this application: {', '.join(named)}")
        return [n.model_copy(update={"source": sources[n.ref]}) for n in out]

    @abstractmethod
    def _sources_for(self, refs: Sequence[str]) -> Dict[str, "str | None"]:
        """``{ref: source or None}`` for every ref this backend can **find**, in one round trip.

        The seam :meth:`describe` is built on, and the reason its two kinds of "no source" stay
        distinguishable: a ref that exists but has no recoverable text maps to ``None``; a ref that
        names nothing is *absent from the mapping*, and :meth:`describe` raises on it.
        """
