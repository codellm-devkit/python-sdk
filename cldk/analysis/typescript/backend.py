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
from typing import ClassVar, Collection, Dict, List, Mapping, Sequence, Set, Tuple

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    EdgeOrder,
    check_depth,
    check_max_paths,
    reject_bare_string,
)
from cldk.analysis.commons.graphs import as_slice_node, edge_sort_key, sdg_rel_pattern, sdg_rels, slice_resolved, taint_verdict, via_table
from cldk.analysis.commons.keys import module_dotted
from cldk.analysis.commons.resolve import resolve_sanitizers
from cldk.analysis.commons.results import Diagnostic, EdgePage, EntrypointCoverage, FlowPath, FlowPaths, LocateResult, Slice, SliceNode, TaintResult
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSCdgEdge,
    TSCfgEdge,
    TSClass,
    TSClassAttribute,
    TSClassOverview,
    TSDdgEdge,
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


# ----------------------------------------------------------------------------------------------
# The dataflow surface's shared vocabulary (leg 2.5b, Task 2). Each of these is the language-neutral
# ruling from ``cldk.analysis.commons`` bound to TypeScript's relationship prefix and edge models,
# once, here -- so the two backends cannot come to disagree about what a page's order, a slice's
# edge set or a hop's word is.

#: The canonical order of each per-callable graph, in the two spellings that have to agree: the
#: Python sort key (:func:`~cldk.analysis.commons.graphs.edge_sort_key`) and the Cypher
#: expressions. ``coalesce`` is ``or ""`` / ``or []``: an optional field's ``None`` raises in a
#: Python sort key and silently drops the row in Cypher. ``len(exprs)`` is also the order's arity,
#: which is how a cursor minted by one accessor is refused by another (3, 2 and 4).
CFG_ORDER = EdgeOrder(edge_sort_key("cfg"), ("src", "dst", "coalesce(kind,'')"))
CDG_ORDER = EdgeOrder(edge_sort_key("cdg"), ("src", "dst"))
DDG_ORDER = EdgeOrder(edge_sort_key("ddg"), ("src", "dst", "coalesce(var,'')", "coalesce(prov,[])"))

#: The five relationship types a slice follows, spelled with TypeScript's ``TS_`` prefix, and the
#: Cypher disjunction of them. ``TS_CFG_NEXT`` is deliberately absent: control *flow* says what runs
#: next, while a slice is about what a value or a decision depends on.
SDG_RELS = sdg_rels("TS")
SDG_REL_PATTERN = sdg_rel_pattern("TS")

#: The caller's word for each relationship a path hop can be justified by (E6). Both backends
#: translate through this one table, so a hop cannot be labelled ``data`` over Neo4j and ``ddg``
#: locally.
VIA = via_table("TS")


def ts_body_node_kind(kind: str, of: "str | None") -> Tuple[str, "str | None"]:
    """One body node's ``(kind, name)`` in the caller's vocabulary — TypeScript's own translation.

    :func:`~cldk.analysis.commons.resolve.body_node_kind` is the Python twin and is deliberately
    **not** reused: cants' ``of`` grammar shares no token with codeanalyzer-python's ``var``
    grammar, so routing TypeScript through it would put three different internal spellings into
    fields E6 reserves for the caller's vocabulary. Measured on superset-frontend (1.3.0):

    * a ``formal_in``'s ``of`` is the parameter's own source text on all 10,465 of them, with none
      of Python's ``"<global>:mod::name"`` / ``"<capture>:name"`` markers — so there is nothing to
      translate and the ``kind`` is always ``parameter``;
    * a ``formal_out``/``actual_out``'s ``of`` is the literal ``"$ret"`` where Python writes
      ``"<return>"`` — a marker, not a name, so it becomes ``name=None``;
    * an ``actual_in``'s ``of`` is ``"arg0"``, ``"arg1"``, … — a *position*, where Python names the
      parameter the argument binds to. Reporting it would put an ordinal in a return field (E7), so
      it too becomes ``name=None``; the argument's identity is recoverable from ``ref``, and
      :meth:`TSAnalysisBackend.flows_to_argument` addresses arguments by the callee's parameter
      name rather than by position for exactly this reason.

    Every other kind (``statement``, ``call``, ``entry``, ``exit``, ``config_access``) is already
    English and passes through with no name, as it does in Python.
    """
    if kind == "formal_in":
        return "parameter", of
    if kind == "actual_in":
        return "argument", None
    if kind in ("formal_out", "actual_out"):
        return "return", None
    return kind, None


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

    # -----[ entrypoints and the config readers (leg 2.5b, Task 3) ]-----
    # Declared here rather than on the generic cross-language ABC for the same reason their Python
    # twins are declared on ``PythonAnalysisBackend``: the return types are this language's own
    # projections, and each analyzer spells the entrypoint mark differently.
    @abstractmethod
    def get_entrypoints(self) -> List[TSCallableOverview]:
        """Overviews of every *callable* codeanalyzer-typescript marked as an entrypoint
        (``TSCallable.is_entrypoint``) — a CLI command, route handler, or other externally-invoked
        callable its entrypoint-detection pass already found.

        An empty list means the pass found no entrypoint *callables* — the ordinary "no
        entrypoints in this project" case, never a stand-in for the mark not existing (1.3.0
        carries ``is_entrypoint`` as a real boolean on every callable, and a graph emitted below
        1.3.0 is refused at attach, so it is never ambiguous at the property level).

        Two things this accessor alone cannot tell you, each answered by a sibling rather than by
        widening its frozen ``List[TSCallableOverview]`` return:

        * **Class-level entrypoints.** ``TSClass`` carries its own ``is_entrypoint``: a class the
          rulesets matched with no individually-marked method. This walk is callables-only; use
          :meth:`get_entrypoint_classes`.
        * **Whether the pass itself had gaps.** Detection under-approximates by design, so silence
          is its failure mode — an empty result here cannot distinguish "ran clean, found none"
          from "had gaps". Use :meth:`get_entrypoint_coverage`."""

    @abstractmethod
    def get_entrypoint_classes(self) -> List[TSClassOverview]:
        """Overviews of every *class* the analyzer marked as an entrypoint in its own right
        (``TSClass.is_entrypoint``) — the class-level sibling of :meth:`get_entrypoints`, which
        walks callables only. Same empty-vs-absent guarantee as :meth:`get_entrypoints`.

        **Classes only.** The 1.3.0 schema declares ``is_entrypoint`` on all five type kinds, but
        the Neo4j projection stamps it onto ``:TSCallable`` and ``:TSClass`` nodes only (measured
        on the reference graph: no other label carries the property at all), so widening this past
        classes would make the two backends answer differently. That is a gap in the projection,
        recorded rather than papered over — see :class:`~cldk.models.typescript.TSClassOverview`."""

    @abstractmethod
    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Coverage and failure record for the entrypoint-detection pass
        (``TSApplication.entrypoint_report``), so a caller can tell "the pass ran clean and found
        nothing" apart from "the pass had gaps" — a distinction :meth:`get_entrypoints`'s empty
        list alone cannot make.

        See :class:`~cldk.analysis.commons.results.EntrypointCoverage` for the field-by-field
        contract. Both TypeScript backends can normally supply it in full: the local backend
        passes ``entrypoint_report`` through, and the Neo4j backend parses the
        ``entrypoint_report_json`` string property 1.3.0 stamps on the ``:Application`` anchor
        (alongside the derived ``entrypoint_frameworks``). A source that carries neither answers
        with a ``diagnostics``-only result rather than fabricating empty-but-clean-looking
        coverage fields — the same "say so honestly" precedent as ``LocateResult``'s
        ``module_source_unavailable``."""

    @abstractmethod
    def get_config_readers(self, key: str) -> List[TSCallableOverview]:
        """Overviews of every callable that reads configuration key ``key``, resolved from
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_config_uses`'s edges.

        That generic accessor hands back ``PyConfigUseEdge.src`` as an opaque body-node id;
        resolving it to "which callable" is a containment walk (``TS_HAS_BODY_NODE``, or the
        callable's own ``body`` map in process), never a split on ``@`` — an anonymous callable's
        own id contains one. Empty means no callable reads this key, which is not the same as "a
        read exists but never resolved to a key": see
        :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_unresolved_config_reads`."""

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

    # =====================================================================================
    # The dataflow surface (leg 2.5b, Task 2). Python's semantics are the contract: every signature
    # below is `cldk/analysis/python/backend.py`'s, keyword-for-keyword, default-for-default.
    #
    # BOUNDS ARE ASYMMETRIC ON PURPOSE (E5). The three *slices* default ``depth`` to
    # :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH` and cap ``max_nodes``: a bounded traversal
    # is a *complete* answer to a narrower question, and ``total`` says how much was left out. The
    # two *predicates* and the two *path* queries default to ``depth=None`` -- unbounded -- because
    # a hop budget on a boolean or a path list is not a smaller answer but a **wrong** one: "no
    # flow" and "no flow within five hops" collapse into the same ``False`` / ``[]`` with nothing in
    # the result to tell them apart.
    #
    # ONE COMPLETENESS PROTOCOL. Truncation is reported by ``complete`` on ``EdgePage`` / ``Slice``
    # / ``FlowPaths``, never by silently returning less.
    #
    # ANALYSIS LEVEL. cfg/cdg/ddg exist from analyzer level 3 and the interprocedural overlays from
    # level 4. A backend attached to a shallower analysis MUST raise rather than return an empty
    # page: an empty there is indistinguishable from a callable that genuinely has no dependence
    # (D7). ``--emit neo4j`` is always full depth, so only the local backend can be below the line.
    #
    # THE CALL GRAPH'S VERTICES ARE TYPESCRIPT'S (TS-11). cants makes a *module* the caller of its
    # own top-level code -- 1,464 of superset-frontend's 17,712 ``TS_CALLS`` edges -- so
    # ``callers_of``, ``backward_cone`` and the call paths report a module vertex with
    # ``kind="module"``, a value outside :attr:`~cldk.analysis.commons.results.SliceNode.KINDS`'
    # Python-derived list. Dropping them would answer "nothing reaches this" where a module does.
    # A module is only ever a *source* (verified: 0 incoming ``TS_CALLS`` on the reference graph),
    # so it can never be the interior of a path; an external is only ever a *target*, so it cannot
    # either.
    # =====================================================================================
    @abstractmethod
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCfgEdge]:
        """One page of the control flow edges within one callable.

        Args:
            callable: The callable's name, resolved by :meth:`resolve_callable` — so an ambiguous
                name raises listing candidates rather than being guessed at.
            in_class: Disambiguate by owning class, as in :meth:`resolve_callable`.
            page_size: Most edges to return. See
                :data:`~cldk.analysis.commons.bounds.DEFAULT_PAGE_SIZE`.
            cursor: ``next_cursor`` from a previous page; ``None`` starts at the beginning.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.typescript.TSCfgEdge`, each carrying the analyzer's ``kind``
            (``fallthrough``, ``true``, ``false``, ``switch_case``, ``loop_back``, ``exception``,
            ``return``, ``break``, ``continue``, ``yield``, ``await_resume``) — a conditional's two
            successors stay two edges, discriminated by ``kind``, which is also why ``kind`` is part
            of the order (:data:`CFG_ORDER`). Endpoints are the body nodes' own ``can://`` ids, the
            spelling :meth:`get_source` accepts.

        Raises:
            AmbiguousName: ``callable`` named more than one callable.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or ``cursor`` not from a previous page of this
                accessor and this callable.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``.
        """

    @abstractmethod
    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCdgEdge]:
        """One page of the control dependence edges within one callable.

        ``src`` is the branching node a ``dst`` is control dependent on — post-dominance over the
        CFG :meth:`get_cfg` returns, computed by the analyzer, not re-derived here. Arguments,
        bounds and failures are :meth:`get_cfg`'s; the order is :data:`CDG_ORDER`.
        """

    @abstractmethod
    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSDdgEdge]:
        """One page of the data dependence edges within one callable.

        Each edge carries the variable it flows (``var``) and its evidence (``prov``).

        **TypeScript's DDG has exactly one provenance tier.** Every one of the 119,384 ``TS_DDG``
        edges on the reference application carries ``prov == ["reaching-defs"]`` — cants emits no
        ``ssa`` and no ``points-to`` tier, so Python's three-way certainty ranking
        (:func:`~cldk.analysis.commons.results.prov_rank`) collapses to a single value here. The
        field and the ranking helper are kept, because the analyzer reserves further tiers and a
        caller comparing two hops' certainty must keep working when it emits them; nothing here
        invents one.

        Arguments, bounds and failures are :meth:`get_cfg`'s; the order is :data:`DDG_ORDER`, which
        includes ``var`` and ``prov`` because the same statement pair legitimately appears more than
        once when it carries several variables, and collapsing those would drop dependences.
        """

    # -----[ slicing and reachability ]-----
    @abstractmethod
    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` depends on: reverse reachability over the SDG.

        The edge set is :data:`SDG_RELS` — data and control dependence within a callable, the two
        parameter-passing relationships across a call, and the callee summaries at a call site. All
        five point *with* the flow, so a backward slice follows them reversed.

        ``within`` is **required**: a value name is scoped by its callable and
        :meth:`resolve_value` cannot resolve one without it, so a ``None`` default would be a
        signature that raises on its own default.

        Args:
            src: The value's name, resolved by :meth:`resolve_value` — in TypeScript, a parameter.
            within: The callable to look inside, resolved as in :meth:`resolve_callable`.
            depth: Most hops from the seed. Defaults to
                :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH`; ``None`` for the whole cone.
            max_nodes: Most nodes in the result. A cap that fires is reported by
                :attr:`~cldk.analysis.commons.results.Slice.truncated` and quantified by
                :attr:`~cldk.analysis.commons.results.Slice.total`; it is never silent.

        Returns:
            A :class:`~cldk.analysis.commons.results.Slice` containing the seed, ordered by node
            id, with ``source`` unhydrated on every node (:meth:`describe` fills it in).

        Raises:
            AmbiguousName: ``within`` named more than one callable, or ``src`` more than one value.
            SelectorNotInGraph: No such callable, or no such value in it.
            ValueError: ``depth`` that is not a positive ``int``, or ``max_nodes`` below 1.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``.
        """

    @abstractmethod
    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` can affect: forward reachability over the same edges.

        The usually-interesting direction for a value entering a callable: nothing flows *into* a
        parameter except from its callers, so ``slice_backward`` from one is often the seed alone,
        while this follows it through the body and out through every call it feeds. Arguments,
        bounds and failures are :meth:`slice_backward`'s.
        """

    @abstractmethod
    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path from ``src`` to ``dst``?

        A **call-graph** question, over ``TS_CALLS`` — "can control get from here to there at all",
        the cheap check a caller makes before asking for the paths themselves. Both names go through
        :meth:`resolve_callable`, so an ambiguous one raises listing candidates rather than being
        guessed at, and both endpoints are therefore callables.

        Returns ``bool`` and nothing else: it is deliberately not a degenerate ``Slice``, because
        "is there a path" and "what is on it" are different questions with different costs.

        **``depth`` defaults to ``None`` here, unlike the three slices.** A default that bounds a
        *slice* trades size for a complete answer to a narrower question; a default that bounds a
        *boolean* would turn "there is no path" and "there is no path within 5 hops" into the same
        ``False``.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either name matched none.
            ValueError: ``depth`` that is not a positive ``int``.
            CodeanalyzerExecutionException: (Neo4j backend) the attached server predates the
                quantified path pattern this compiles to (Neo4j 5.9).
        """

    @abstractmethod
    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every vertex that can reach any of ``sinks`` — "what could get here".

        A **call-graph** cone, so its nodes are call-graph vertices, not body nodes: callables
        (``kind="callable"``) and the modules whose top-level code calls them (``kind="module"``,
        TS-11). The sinks themselves are in the result, and in
        :attr:`~cldk.analysis.commons.results.Slice.roots`.

        Args:
            sinks: The callables to walk back from, each resolved by :meth:`resolve_callable`.
            depth: Most call hops back. Defaults to
                :data:`~cldk.analysis.commons.bounds.DEFAULT_DEPTH`; ``None`` for the whole cone.
            max_nodes: Most nodes in the result.

        Raises:
            AmbiguousName: A sink name matched more than one callable.
            SelectorNotInGraph: A sink name matched none.
            TypeError: ``sinks`` is a bare string.
            ValueError: ``sinks`` is empty, ``depth`` is not a positive ``int``, or ``max_nodes``
                is below 1.
        """

    @abstractmethod
    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Who calls this — one hop back over ``TS_CALLS``, addressed by name.

        The name-based sibling of :meth:`get_all_callers`, which takes a class signature plus a
        method name and returns raw dicts. That one is a frozen leg-1 signature and is not touched;
        this one takes a name the caller already has and returns
        :class:`~cldk.analysis.commons.results.SliceNode` objects.

        **A module is a legitimate caller** (``kind="module"``): cants emits a module as the caller
        of its own top-level code, and dropping those would report "nothing calls it" for every
        function a module invokes at import time. An external ghost is never a caller — it was never
        analysed, so it has no body to call from, and the reference graph has no ``TS_CALLS``
        originating at one.

        An empty list is unambiguous: a name that matches nothing raises, so ``[]`` means "nothing
        calls it".

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """

    @abstractmethod
    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """What this calls — one hop forward over ``TS_CALLS``, addressed by name.

        **Externals are included**, with ``kind="external"``: they are 6,537 of the reference
        application's 17,712 call edges and they are what a caller tracing a sink is usually looking
        for. An external was never analysed, so it has no position: ``file`` is ``""`` and ``line``
        is ``0``, and ``kind`` is what says why rather than leaving two sentinels to be discovered.
        Its ``callable`` is the readable dotted name built from the node's own ``module`` and
        ``name`` — never its ``can://`` id, which stays in ``ref`` where an opaque handle belongs.

        Raises:
            AmbiguousName: ``name`` matched more than one callable.
            SelectorNotInGraph: Nothing matched.
        """

    # -----[ paths and flow predicates ]-----
    @abstractmethod
    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value — the *sequences*, where a slice is the set.

        Each :class:`~cldk.analysis.commons.results.FlowPath` is an ordered list of
        :class:`~cldk.analysis.commons.results.PathHop` values, and each hop says what justified it:
        the kind of edge (``data``/``control``/``argument``/``return``/``summary``), the variable
        the dependence is on, and the provenance the analyzer established it with — which in
        TypeScript is always ``["reaching-defs"]`` (see :meth:`get_ddg`).

        **Only shortest paths.** A search that enumerated every walk would not terminate on a real
        dependence graph, and the tenth-longest way a value can reach another is not evidence anyone
        wants. What comes back is the shortest hop-count, and every path of it up to ``max_paths``.

        **Two scopes, not one, and neither defaults to the other.** A value is addressed by a name
        plus the callable it enters, so two values need two callables — and a single scope could
        never find the cross-callable path this accessor exists for.

        Args:
            src: The value the flow starts at, named as a caller would.
            dst: The value it must reach.
            src_within: The callable ``src`` enters. Required.
            dst_within: The callable ``dst`` enters. Required, and not defaulted.
            depth: Most hops a path may take; ``None`` (the default) for no bound.
            max_paths: Most paths to return. The result's ``complete`` says whether more existed.

        Raises:
            AmbiguousName: ``src``, ``dst`` or either callable name matched more than one thing.
            SelectorNotInGraph: One of them matched nothing.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, or ``src``
                and ``dst`` resolve to the same position (see
                :func:`~cldk.analysis.commons.bounds.check_distinct_endpoints`).
        """

    @abstractmethod
    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How one callable reaches another — the same sequences, over the call graph.

        The evidence-carrying form of :meth:`reaches`: that answers *whether*, this answers *how*.
        Every hop is ``via="call"`` with no ``var`` and no ``prov``, because a ``TS_CALLS`` edge
        carries neither — a call is a syntactic fact, and saying so explicitly is better than
        inventing a provenance for it.

        Takes no ``within``: a callable is addressed by name alone. ``depth`` defaults to ``None``
        as :meth:`reaches`'s does.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either matched nothing.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, or ``src``
                and ``dst`` name the same callable.
        """

    @abstractmethod
    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach **any** argument of a call to ``callee``?

        The target is the set of ``callee``'s ``formal_in`` vertices — in TypeScript, its
        parameters. Those are enterable only through ``TS_PARAM_IN`` from a caller's argument, so
        reaching one means the value was passed into a real call, not merely that it sits in the
        same program.

        A value that only *control*-dominates a call site without feeding any of its arguments is
        deliberately **not** counted: "flows to" is a dataflow claim, and widening it to "was
        executed before" would make the answer true almost everywhere.

        **One ``within``, scoping ``src`` only.** :meth:`paths_between` takes two callables because
        it takes two *values*; here the second endpoint is ``callee``, a callable addressed by name
        alone, so a second scope would have nothing to scope. ``depth`` defaults to ``None``: a bare
        ``False`` on a boolean carries no signal that a bound fired.

        Raises:
            AmbiguousName: ``src`` or ``callee`` matched more than one thing.
            SelectorNotInGraph: Either matched nothing.
            ValueError: ``depth`` is not a positive ``int``.
        """

    @abstractmethod
    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach the argument ``arg`` of a call to ``callee``?

        A **different question** from :meth:`flows_to_call`, and kept a separate implementation on
        purpose: a tainted value routinely reaches a function without reaching the parameter that
        matters.

        ``arg`` is resolved to the parameter **by name**, through the same :meth:`resolve_value` the
        other accessors use, with ``within=callee`` — nothing here asks the caller to know which
        slot a parameter occupies (E7).

        **The implication ``flows_to_argument`` ⟹ ``flows_to_call`` holds by construction**, not by
        agreement between two queries: ``resolve_value(arg, within=callee)`` can only ever return
        one of ``callee``'s ``formal_in`` vertices, and that set is exactly what
        :meth:`flows_to_call` tests reachability of.

        Raises:
            AmbiguousName: A name matched more than one thing.
            SelectorNotInGraph: A name matched nothing — including ``arg`` naming no parameter of
                ``callee``, which is a caller error and not a ``False``.
            ValueError: ``depth`` is not a positive ``int``.
        """

    # -----[ taint: many sources, many sinks, one traversal ]-----
    def taint(
        self,
        sources: Sequence[Tuple[str, str]],
        sinks: Sequence[Tuple[str, str]],
        sanitizers: Sequence[Tuple[str, str] | str] = (),
        *,
        depth: int | None = None,
        max_paths: int = DEFAULT_MAX_PATHS,
    ) -> TaintResult:
        """Which of these sources reach which of these sinks, and what to make of the ones that do not.

        The verb triage needs, and the one nothing else on this surface stands in for.
        :meth:`paths_between` *proves* a flow; a caller who gets ``[]`` back from it cannot tell "no
        flow exists" from "the flow left the resolved graph". ``taint()`` runs m sources against n
        sinks in one traversal and reports that distinction **per pair**: the witnesses in
        :attr:`~cldk.analysis.commons.results.FlowPaths.paths`, the pairs an absence claim can be
        built on in :attr:`~cldk.analysis.commons.results.TaintResult.exhausted`, and everything
        else explained in :attr:`~cldk.analysis.commons.results.TaintResult.unresolved`.

        **Sources, sinks and sanitizers are the caller's to supply.** This SDK ships no framework
        catalogue and derives no default set: a per-language vocabulary of taint sources is policy
        that rots, and this accessor is the mechanism.

        **A sanitizer is two mechanisms wearing one word**, told apart by shape. A bare ``str`` cuts
        a *callable* on the path -- what a transforming sanitizer (``encodeURIComponent``, ``DOMPurify.sanitize``)
        is, since it sits on the data path and is naturally named as the function it is. A
        ``(name, within)`` pair cuts a *variable* inside that callable, which is the only thing that
        severs a *validating* guard, because a guard never appears on the data path at all. Both
        cuts are applied **inside** the search rather than to the rows it returns, so what comes back
        is the shortest *unsanitized* route: filtering afterwards would report nothing for a source
        whose ten shortest paths are sanitized and whose eleventh is not -- a false refutation, the
        one output this accessor must never produce, because in triage it closes a live alert.

        **Every hop's provenance is ``reaching-defs``** (see :meth:`get_ddg`), so every witness's
        ``weakest`` caps at the same tier here. That is a fact about what cants establishes, not a
        gap in this accessor, and it is why a TypeScript flow is argued from its *hops* rather than
        from a provenance comparison between two of them.

        **The order of the checks is part of the contract**, cheapest-to-be-wrong-about first: the
        bounds before any name, the names before any sanitizer, the sanitizers before the walk. A
        caller with a typo in ``depth`` hears about ``depth`` rather than about a name, and pays for
        no round trip to learn it.

        **Two deliberate divergences from :meth:`paths_between`.** A pair whose source and sink are
        the *same position* is skipped with a diagnostic rather than raised -- ``paths_between``
        raises there (:func:`~cldk.analysis.commons.bounds.check_distinct_endpoints`), and raising
        would discard a forty-pair batch over one degenerate pair that a caller assembling sources
        programmatically produces by accident. And an explicit ``depth`` yields **no** ``exhausted``
        pair, ever: a pair with no path within five hops is not refuted, it is unmeasured, and
        conflating the two is the bounded-boolean error.

        **The level gate belongs to the walk, not here.** ``_require_dataflow`` is a *local*
        backend's method -- a graph backend has no shallow mode to guard against -- so each
        :meth:`_taint_walk` opens with it rather than this body asking every backend a question two
        of them cannot answer.

        Args:
            sources: The values taint enters at, each ``(name, within)`` -- the addressing
                :meth:`resolve_value` and :meth:`paths_between` already use.
            sinks: The values it must not reach, addressed the same way.
            sanitizers: Bare names cut callables; ``(name, within)`` pairs cut variables (above).
            depth: Most hops a path may take; ``None`` (the default) for no bound, because a bound
                turns a refutation into an artefact of the budget -- and ``exhausted`` is empty
                whenever it is set.
            max_paths: Most witnesses **per pair**, not per call: with one sink and forty sources a
                flat cap lets one prolific pair starve the other thirty-nine, and in triage the
                per-source witness is the answer. A pair is a pair of *resolved positions*, so two
                selectors naming the same one are one pair and neither double the witnesses nor the
                cap.

        Returns:
            A :class:`~cldk.analysis.commons.results.TaintResult`: ``paths`` are the witnesses,
            ``exhausted`` the pairs searched to exhaustion with a clean ledger, ``roots`` and
            ``resolved`` what every name matched, ``unresolved`` the ledger, and ``complete`` is the
            whole batch's flag: ``True`` only when nothing was truncated **and** the ledger is empty,
            so one skipped or blocked pair makes it ``False`` however cleanly the rest answered --
            and where nothing was truncated, a bigger ``max_paths`` returns that same ``False``. A
            pair is named in
            ``exhausted`` by the two strings the caller passed, so two sources sharing a name in
            different callables read as one pair there -- ``roots`` is what tells them apart.

        Raises:
            AmbiguousName: A name, or a sanitizer's ``within``, matched more than one thing.
            SelectorNotInGraph: A name matched nothing, or a sanitizer's shape disagrees with what it
                resolves to.
            TypeError: ``sources`` or ``sinks`` is a bare string, which would unpack into a pair.
            ValueError: ``depth`` is not a positive ``int``, ``max_paths`` is below 1, ``sources`` or
                ``sinks`` is empty (refused, not answered ``[]``), or a sanitizer names a blank
                variable.
        """
        check_depth(depth)
        check_max_paths(max_paths)
        reject_bare_string("sources", sources)
        reject_bare_string("sinks", sinks)
        if not sources:
            raise ValueError("sources= names nothing to taint from; pass at least one (name, within) pair")
        if not sinks:
            raise ValueError("sinks= names nothing to taint to; pass at least one (name, within) pair")
        srcs = [self.resolve_value(name, within=within) for name, within in sources]
        dsts = [self.resolve_value(name, within=within) for name, within in sinks]
        cuts, cut_callables = resolve_sanitizers(sanitizers, resolve_callable=self.resolve_callable, edge_vars_in=self._edge_vars_in)
        rows, blocked = self._taint_walk(srcs, dsts, cuts=cuts, cut_callables=cut_callables, depth=depth, max_paths=max_paths)
        return taint_verdict(sources, sinks, srcs, dsts, rows=rows, blocked=blocked, depth=depth, max_paths=max_paths)

    @abstractmethod
    def _taint_walk(
        self,
        srcs: Sequence[SliceNode],
        dsts: Sequence[SliceNode],
        *,
        cuts: List[Dict[str, str]],
        cut_callables: List[str],
        depth: int | None,
        max_paths: int,
    ) -> Tuple[List[Tuple[str, str, FlowPath]], Mapping[Tuple[str, str], List[Diagnostic]]]:
        """The sanitized shortest walks between every source and every sink, and what stopped a pair.

        Rows are ``(source ref, sink ref, path)`` triples, shortest-first within a pair and in
        :func:`~cldk.analysis.commons.graphs.hop_sort_key` order among equals, capped at
        ``max_paths + 1`` **per pair** -- the extra row is what lets :meth:`taint` report truncation
        without a second counting traversal, and the per-pair cap is why one prolific pair cannot
        starve the rest. Grouping and trimming are :meth:`taint`'s, so a walk returns what it found.

        The second element is the frontier ledger, **keyed by the ``(source ref, sink ref)`` pair it
        implicates**. The key is the association, not the message: ``exhausted`` is decided from
        these keys, and recovering a pair by parsing prose back out of a ``Diagnostic`` would
        resurrect exactly the derivation that field is stored to avoid.

        **An unresolved dispatch is a property of a callable frontier, not of a pair**, and this
        mapping has no key meaning "every pair" -- so a walk that meets one files the same diagnostic
        under *each* ``(source ref, sink ref)`` key it affects, not under one of them and not under a
        key of its own devising. Filing under one arm is not equivalent and the difference is not
        laxity: :meth:`taint` reads every key it is handed, so nothing is dropped either way, but a
        key no requested pair claims costs **every** pair its ``exhausted`` certification, because
        nothing on the receiving side can attribute a stray key to a pair. Filing per affected pair
        is what keeps the verdict as precise as the walk's own knowledge; the signature cannot say
        so, which is why it is said here.

        A local backend opens with ``self._require_dataflow()``: the graph backends do not measure
        the analysis level (their attach probe never looks at the dependence relationships), so the
        gate lives in the implementations that can answer rather than in :meth:`taint`.

        ``@abstractmethod`` now that every backend has one (Ruling G): it shipped as a concrete stub
        so a backend without an implementation was refused when it was *called* rather than when it
        was constructed, and the last implementation closed that window.
        """

    @abstractmethod
    def _edge_vars_in(self, callable_id: str) -> Collection[str]:
        """The variable names carried by SDG edges scoped to this callable -- the domain a variable
        sanitizer is checked against.

        Not :meth:`resolve_value`, which addresses ``formal_in`` port vertices only: most real edge
        variables are locals (``cleaned``, ``answer``, ``result``), so validating a sanitizer through
        the resolver would refuse a legitimate one for not being a parameter. One ``DISTINCT r.var``
        query on the graph side, the adjacency already built on the local side.

        ``@abstractmethod`` for :meth:`_taint_walk`'s reason, and since the same commit.
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
