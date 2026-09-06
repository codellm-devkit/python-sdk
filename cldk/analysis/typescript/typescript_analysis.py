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

"""TypeScript analysis facade.

Thin, read-only query layer over the canonical ``TSApplication`` produced by the
codeanalyzer-typescript backend. Mirrors the method vocabulary of ``JavaAnalysis`` /
``PythonAnalysis`` (there is no shared base class — the facades match by convention) and, like
those, delegates all indexing and query work to its backend (:class:`TSCodeanalyzer`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

import networkx as nx

from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, Neo4jConnectionConfig, TSBackend, cache_subdir
from cldk.analysis.commons.bounds import DEFAULT_DEPTH, DEFAULT_MAX_NODES, DEFAULT_MAX_PATHS, DEFAULT_PAGE_SIZE
from cldk.analysis.commons.results import EdgePage, FlowPaths, LocateResult, Slice, SliceNode
from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.codeanalyzer import TSCodeanalyzer
from cldk.analysis.typescript.neo4j import TSNeo4jBackend
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSCdgEdge,
    TSCfgEdge,
    TSClass,
    TSClassAttribute,
    TSDdgEdge,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSVariableDeclaration,
)


class TypeScriptAnalysis:
    """Analysis facade for TypeScript projects.

    Delegates every query to a backend. Two interchangeable backends exist, both exposing the
    same method surface:

    * :class:`TSCodeanalyzer` (default) — walks the in-memory pydantic ``TSApplication`` / a
      NetworkX call graph built from ``analysis.json``;
    * :class:`TSNeo4jBackend` — answers the *same* ``get_*`` queries with Cypher over the graph
      ``codeanalyzer-typescript`` emits with ``--emit neo4j``. Selected by passing
      ``neo4j_config``.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        backend: TSBackend | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.target_files = target_files
        self.eager_analysis = eager_analysis
        # The backend is selected by the *type* of the config: Neo4jConnectionConfig picks the
        # read-only Cypher backend, CodeAnalyzerConfig (the default) the in-process analyzer.
        self.backend_config: TSBackend = backend if backend is not None else CodeAnalyzerConfig()
        self.backend: TSAnalysisBackend
        if isinstance(self.backend_config, Neo4jConnectionConfig):
            # Read-only: the graph is populated out of band; the SDK only polls it.
            cfg = self.backend_config
            application_name = cfg.application_name or (Path(project_dir).name if project_dir else None)
            self.backend = TSNeo4jBackend(
                neo4j_uri=cfg.uri,
                neo4j_username=cfg.username,
                neo4j_password=cfg.password,
                neo4j_database=cfg.database,
                application_name=application_name,
            )
        else:
            cache_path = cache_subdir(self.backend_config.cache_dir, project_dir, "typescript")
            if cache_path is not None:
                cache_path.mkdir(parents=True, exist_ok=True)
            self.backend = TSCodeanalyzer(
                project_dir=project_dir,
                analysis_json_path=cache_path,
                analysis_level=analysis_level,
                eager_analysis=eager_analysis,
                target_files=target_files,
                tsc_only=getattr(self.backend_config, "tsc_only", False),
            )
        self.application: TSApplication = self.backend.get_application_view()

    # -----[ Tier A: lifecycle / whole-program ]-----
    def get_application_view(self) -> TSApplication:
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, TSModule]:
        return self.backend.get_symbol_table()

    def get_modules(self) -> List[TSModule]:
        return self.backend.get_modules()

    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of the call edges, keyed as every other accessor keys things (module
        file key, type/callable signature, ``"<module>.<name>"`` for an external), each node
        tagged ``kind`` (``module | class | interface | enum | type_alias | namespace | callable | external``) and ``id``. TypeScript's own
        endpoints are kept: a module is the caller of its top-level code and a class the callee of
        ``new X()``; filter on ``kind == "callable"`` for Python's shape."""
        return self.backend.get_call_graph()

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """The phantom (external) call targets — imported/required library members and builtins
        the call graph points at — keyed ``"<module>.<name>"`` (e.g. ``node:fs.readFileSync``,
        ``(builtin).push``) as the call graph keys them, the wire's ``can://`` id on the value.
        Useful for source→sink reachability."""
        return self.backend.get_external_symbols()

    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """The synthesized anonymous-callback endpoints the call graph points at — Jelly-resolved
        callbacks the symbol table never names (keyed by their ``<host>:<line:col>`` signature).
        Empty under the ``tsc``-only resolver. Materialized so anonymous call edges don't dangle."""
        return self.backend.get_synthesized_callables()

    def get_call_graph_json(self) -> str:
        return self.backend.get_call_graph_json()

    def get_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata (``provenance`` /
        ``tags``). Pass a bare signature as the first argument for module-level functions or
        external (phantom) targets."""
        return self.backend.get_all_callers(target_class_name, target_method_declaration)

    def get_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""
        return self.backend.get_all_callees(source_class_name, source_method_declaration)

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base_class."""
        return self.backend.get_class_hierarchy()

    # -----[ call sites ]-----
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        """The rich, syntactic call sites *inside* a callable (receiver/argument types, resolved
        ``callee_signature``, source position)."""
        return self.backend.get_call_sites(qualified_callable_name)

    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted source lines anywhere in the project where ``target_signature`` is invoked."""
        return self.backend.get_calling_lines(target_signature)

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The call targets invoked from a callable, derived from its call sites."""
        return self.backend.get_call_targets(source_signature)

    # -----[ Tier B: navigation ]-----
    def get_classes(self) -> Dict[str, TSClass]:
        return self.backend.get_all_classes()

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        return self.backend.get_class(qualified_class_name)

    def get_classes_by_criteria(self, inclusions: List[str] | None = None, exclusions: List[str] | None = None) -> Dict[str, TSClass]:
        inclusions = inclusions or []
        exclusions = exclusions or []
        result: Dict[str, TSClass] = {}
        for sig, cls in self.backend.get_all_classes().items():
            selected = any(inc in sig for inc in inclusions)
            if any(exc in sig for exc in exclusions):
                selected = False
            if selected:
                result[sig] = cls
        return result

    def get_interfaces(self) -> Dict[str, TSInterface]:
        return self.backend.get_all_interfaces()

    def get_enums(self) -> Dict[str, TSEnum]:
        return self.backend.get_all_enums()

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        return self.backend.get_enum_members(qualified_enum_name)

    def get_type_aliases(self) -> Dict[str, TSTypeAlias]:
        return self.backend.get_all_type_aliases()

    def get_functions(self) -> Dict[str, TSCallable]:
        """Top-level (module/namespace) functions."""
        return self.backend.get_all_functions()

    def get_methods(self) -> Dict[str, Dict[str, TSCallable]]:
        """All methods grouped by class/interface signature."""
        return self.backend.get_all_methods_in_application()

    def get_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        return self.backend.get_method_parameters(qualified_class_name, qualified_method_name)

    def get_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return self.backend.get_all_constructors(qualified_class_name)

    def get_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        return self.backend.get_all_fields(qualified_class_name)

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        return self.backend.get_interface_properties(qualified_interface_name)

    def get_imports(self) -> Dict[str, List[TSImport]]:
        return self.backend.get_imports()

    def get_exports(self) -> Dict[str, List[TSExport]]:
        return self.backend.get_all_exports()

    def get_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        """Module-level variable declarations per file."""
        return self.backend.get_all_variables()

    def get_typescript_file(self, qualified_name: str) -> str | None:
        """File path declaring the class/interface/enum/callable with the given signature."""
        return self.backend.get_typescript_file(qualified_name)

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        return self.backend.get_typescript_module(file_path)

    def get_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        """Always ``[]`` on schema v2, on both backends -- permanently, not for want of data. A
        v2 class node holds only ``callables`` and ``fields``: the tree gives a class no ``types``
        bucket, so no class can nest a class. A class declared inside a *callable* is the surviving
        case and reads as ``TSCallable.inner_classes``. Kept because the 1.x surface had it (G3)."""
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        return self.backend.get_all_sub_classes(qualified_class_name)

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends (base_classes minus the implemented interfaces)."""
        return self.backend.get_extended_classes(qualified_class_name)

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        return self.backend.get_implemented_interfaces(qualified_class_name)

    # -----[ decorators ]-----
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        """Structured decorators (with arguments) applied to a callable."""
        return self.backend.get_decorators(qualified_callable_name)

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        """Structured decorators (with arguments) applied to a class."""
        return self.backend.get_class_decorators(qualified_class_name)

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of callables carrying it. TS
        decorators are captured structurally, so this is populatable at level 1."""
        return self.backend.get_methods_with_decorators(decorators)

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of classes carrying it."""
        return self.backend.get_classes_with_decorators(decorators)

    # -----[ bulk / projected accessors ]-----
    def get_callables_overview(self) -> List[TSCallableOverview]:
        """Return a lightweight overview of every callable in the project, in one bulk read.

        A field-projected alternative to :meth:`get_methods` for enumeration: each
        :class:`~cldk.models.typescript.TSCallableOverview` carries the callable's signature,
        owning class/interface (if any), native kind, location, and decorators — but not the full
        reconstruction (call sites, inner callables, locals). On the Neo4j backend this is a single
        Cypher query instead of the per-entity fan-out :meth:`get_methods` pays. Body-inspect the
        few you need afterwards via :meth:`get_method` or :meth:`get_method_bodies`.

        Returns:
            A flat list of :class:`~cldk.models.typescript.TSCallableOverview`, one per callable
            (class/interface methods, module- and namespace-level functions, and nested/inner
            callables).

        See Also:
            :meth:`get_decorated_callables`: The same projection filtered by decorator.
            :meth:`get_method_bodies`: Bulk source-body fetch for chosen signatures.

        Note:
            A ``get x()``/``set x()`` accessor pair shares one ``signature``, so this projection
            (and the other bulk accessors) can diverge between the local and Neo4j backends on a
            paired accessor — see `#300 <https://github.com/codellm-devkit/python-sdk/issues/300>`_.
        """
        return self.backend.get_callables_overview()

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Return source bodies for the given callable signatures, in one bulk read.

        Args:
            signatures: Callable signatures to fetch bodies for (e.g. from
                :meth:`get_callables_overview`).

        Returns:
            A dict mapping each signature to its source body. Signatures with no matching callable
            are omitted, as are callables whose ``code`` is ``None`` (e.g. implicit constructors
            the analyzer synthesizes with no source text) — every returned value is a real ``str``.
        """
        return self.backend.get_method_bodies(signatures)

    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        """Return overviews of callables decorated with any of the given markers, in one bulk read.

        Args:
            markers: Decorator names to match (e.g. ``["Get", "Controller"]``).

        Returns:
            A list of :class:`~cldk.models.typescript.TSCallableOverview` for every callable
            carrying at least one of ``markers`` as a decorator.

        See Also:
            :meth:`get_callables_overview`: The unfiltered projection.
        """
        return self.backend.get_decorated_callables(markers)

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        """Return the call sites of the given callables, keyed by signature, in one bulk read.

        Avoids the per-callable reconstruction fan-out when you need call sites for a specific
        frontier (e.g. dispatch-edge synthesis or external-reader detection).

        Args:
            signatures: Callable signatures to fetch call sites for.

        Returns:
            A dict mapping each existing signature to its list of
            :class:`~cldk.models.typescript.TSCallsite` (empty if the callable has no call sites).
            Signatures with no matching callable are omitted.
        """
        return self.backend.get_callsites_for(signatures)

    # -----[ addressing (leg 2.5b) ]-----
    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        The single most-needed query for triaging a scanner alert: an alert arrives as
        ``file:line`` and this resolves it to the enclosing callable in one call, rather than
        ``get_method``, falling back to ``get_callers``, falling back to scanning the symbol table
        by hand. Four outcomes stay distinguishable — see
        :class:`~cldk.analysis.commons.results.LocateResult`: inside a callable (``callable`` set,
        plus ``body`` when a body node is that precise), at real module scope (``module_scope``
        diagnostic), in the gap between two callables (also module scope, never snapped to the
        nearest callable), or in a file the analysis has no module for (``file_not_in_graph``).

        There is no ``col`` parameter. Column-level disambiguation would have to be honoured by
        both backends to mean anything, and the Neo4j graph projects only ``start_line`` /
        ``end_line`` on ``:TSCallable`` and ``:TSBodyNode`` — so a ``col`` would work in-process and
        be silently ignored over Neo4j. Better absent than documented and inert.

        Args:
            path: The file path. Normalised against the backend's module keys, so a ``./``-prefixed
                or absolute path resolves rather than reading back as ``file_not_in_graph``.
            line: The 1-based line number.

        Returns:
            A :class:`~cldk.analysis.commons.results.LocateResult` carrying the innermost body
            node, the enclosing callable, its owning class/interface, its module, and the source
            slice — never an ambiguous empty.

        See Also:
            :meth:`locate_many`: The bulk form — the point, not an optimisation.
        """
        return self.backend.locate(path, line)

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many ``(path, line)`` positions in one round trip, in input order.

        Args:
            positions: The ``(path, line)`` pairs to resolve, e.g. from a scanner's alert list.

        Returns:
            One :class:`~cldk.analysis.commons.results.LocateResult` per input position, in the
            same order.

        See Also:
            :meth:`locate`: The single-position form.
        """
        return self.backend.locate_many(positions)

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name to the one callable it names, in the caller's vocabulary.

        The addressing step every name-taking accessor performs, exposed so a caller can perform it
        once and keep the answer::

            node = ts.resolve_callable("show", in_class="UserController")
            node.callable   # the full dotted signature — what every other accessor keys by
            node.file, node.line

        ``name`` matches whole or as a dotted suffix; ``in_class`` is a dotted suffix of the owning
        class or interface, ``in_module`` a module key (``"src/controllers.ts"``) or the dotted form
        (``"src.controllers"``). An anonymous callable is addressed by its ``<anon@line:col>``
        signature, never by its name — cants calls every one of them ``"(anonymous)"``. Ambiguity
        raises with every candidate; nothing is guessed.

        Raises:
            AmbiguousName: More than one callable matched.
            SelectorNotInGraph: Nothing matched — naming the argument that missed.
        """
        return self.backend.resolve_callable(name, in_class=in_class, in_module=in_module)

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable — in TypeScript, a parameter — to the position
        that carries it.

        The same resolution the dataflow accessors perform on their ``src``, exposed so a caller can
        check what a name means before asking a question of it::

            ts.resolve_value("id", within="UserController.show").kind   # "parameter"

        Raises:
            AmbiguousName: ``within`` named more than one callable, or ``name`` more than one value.
            SelectorNotInGraph: No such callable, or no such value in it.
        """
        return self.backend.resolve_value(name, within=within)

    def get_source(self, node_id: str) -> str:
        """Return the source text named by ``node_id`` — a callable, or one of its body nodes.

        Generalises :meth:`get_method_bodies` below callable granularity: ``node_id`` is a
        callable's signature, a callable's opaque id, or the body-node id
        :attr:`~cldk.analysis.commons.results.LocateResult.node_id` hands back, so a statement or
        call site :meth:`locate` found can be re-fetched precisely.

        Args:
            node_id: A callable signature, or an id from :meth:`locate` / :meth:`resolve_callable` —
                passed back as received, not composed.

        Returns:
            The source text, never an ambiguous empty string.

        Raises:
            KeyError: Nothing matches ``node_id``, or it has no recoverable source.
            NotImplementedError: (Neo4j backend only) ``node_id`` names a body node — the attached
                graph carries no source text below callable granularity.
        """
        return self.backend.get_source(node_id)

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in ``source`` for these positions, in one round trip.

        Addressing answers *where*; this answers *what*, and it is a second call because source is
        the one field with no size ceiling. Takes anything carrying an address — slice nodes, a
        ``locate()`` result — and gives back the same
        :class:`~cldk.analysis.commons.results.SliceNode` shape with ``source`` filled.

        Afterwards, ``source=None`` means exactly one thing: **this position exists and there is no
        text for it.** A ref that names nothing raises instead.

        Args:
            nodes: The positions to hydrate. An empty sequence costs no round trip.

        Returns:
            The same positions, in the same order, with ``source`` filled where the backend has
            text for them.

        Raises:
            KeyError: A ref names nothing in this application.
            TypeError: An element carries no address to look up.
        """
        return self.backend.describe(nodes)

    @property
    def has_resolution_edges(self) -> bool:
        """Whether :meth:`get_callsites_for` can resolve call sites on this backend right now.

        ``False`` means every ``callee_signature=None`` it returns is explained by the view having
        been built below the level at which cants resolves callees, not by individual call sites
        failing to resolve. On the local backend that is ``analysis.max_level < 2``; over Neo4j it
        is a graph carrying no ``TS_RESOLVES_TO`` edge for this application.

        See Also:
            :meth:`get_callsites_for`: The accessor whose ``None`` this disambiguates.
        """
        return self.backend.has_resolution_edges

    # =====================================================================================
    # The dataflow surface (leg 2.5b, Task 2). Every signature is
    # :class:`~cldk.analysis.python.python_analysis.PythonAnalysis`'s, keyword-for-keyword.
    # =====================================================================================
    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCfgEdge]:
        """Return one page of the control flow inside one callable, addressed by name.

        The graph the analyzer built, not one re-derived here: a conditional's two successors stay
        two edges discriminated by ``kind``. Endpoints are the body nodes' own opaque ids, which
        :meth:`get_source` and :meth:`describe` both accept::

            page = ts.get_cfg("show", in_class="UserController")
            page.total          # the whole graph's size, on every page
            page.complete       # False when there is more, with page.next_cursor to fetch it

        Args:
            callable: The callable's name, resolved as by :meth:`resolve_callable`.
            in_class: Disambiguate by owning class or interface.
            page_size: Most edges to return.
            cursor: ``next_cursor`` from a previous page; ``None`` starts at the beginning.

        Returns:
            An :class:`~cldk.analysis.commons.results.EdgePage` of
            :class:`~cldk.models.typescript.TSCfgEdge`.

        Raises:
            AmbiguousName: More than one callable matched.
            SelectorNotInGraph: Nothing matched.
            ValueError: ``page_size`` below 1, or a cursor from another page, callable or accessor.
            CodeanalyzerUsageException: (local backend) built below
                ``analysis_level="program_dependency_graph"``.
        """
        return self.backend.get_cfg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCdgEdge]:
        """Return one page of the control *dependence* inside one callable.

        ``src`` is the branching node ``dst`` is control dependent on. Arguments, paging and
        failures are :meth:`get_cfg`'s.
        """
        return self.backend.get_cdg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSDdgEdge]:
        """Return one page of the data dependence inside one callable.

        Each edge carries the variable it flows and the evidence for it. **TypeScript has a single
        provenance tier:** every edge's ``prov`` is ``["reaching-defs"]``, where Python distinguishes
        ``ssa`` / ``reaching-defs`` / ``points-to``. Arguments, paging and failures are
        :meth:`get_cfg`'s.
        """
        return self.backend.get_ddg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return everything the value ``src`` depends on — reverse reachability over the SDG.

        ``depth`` defaults to a **finite** bound on purpose: a bounded traversal answers a narrower
        question *completely*, and ``total`` says how much was left out. ``depth=None`` asks for the
        whole cone::

            s = ts.slice_backward("id", within="UserController.show")
            s.total, s.truncated

        Args:
            src: The value's name — in TypeScript, a parameter.
            within: The callable to look inside. Required: a value name is scoped by its callable.
            depth: Most hops from the seed; ``None`` for the whole cone.
            max_nodes: Most nodes in the result; a cap that fires is reported, never silent.

        Returns:
            A :class:`~cldk.analysis.commons.results.Slice`, ordered by node id, with ``source``
            unhydrated (:meth:`describe` fills it in).

        Raises:
            AmbiguousName: ``within`` or ``src`` matched more than one thing.
            SelectorNotInGraph: No such callable, or no such value in it.
            ValueError: ``depth`` is not a positive ``int``, or ``max_nodes`` is below 1.
        """
        return self.backend.slice_backward(src, within=within, depth=depth, max_nodes=max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return everything the value ``src`` can affect — the same edges read forward.

        Usually the interesting direction for a parameter: nothing flows *into* one except from its
        callers. Arguments, bounds and failures are :meth:`slice_backward`'s.
        """
        return self.backend.slice_forward(src, within=within, depth=depth, max_nodes=max_nodes)

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Return whether control can get from one callable to another over the call graph.

        The cheap check before asking for the paths themselves. **``depth`` is unbounded by
        default**, unlike the slices: a bound on a boolean would collapse "there is no path" and
        "there is no path within five hops" into the same ``False``.

        Args:
            src: The calling callable's name.
            dst: The called callable's name.
            depth: Most call hops, or ``None`` for any distance.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either matched none.
            ValueError: ``depth`` is not a positive ``int``.
        """
        return self.backend.reaches(src, dst, depth=depth)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Return every call-graph vertex that can reach any of ``sinks`` — "what could get here".

        The accessor to reach for when the sink is a dangerous function and the question is which
        entry points lead to it. Its nodes are callables **and modules**: cants makes a module the
        caller of its own top-level code, so a cone without them would under-report.

        Args:
            sinks: The callables to walk back from; a bare string is refused.
            depth: Most call hops back; ``None`` for the whole cone.
            max_nodes: Most nodes in the result.

        Raises:
            AmbiguousName: A sink matched more than one callable.
            SelectorNotInGraph: A sink matched none.
            TypeError: ``sinks`` is a bare string.
            ValueError: ``sinks`` is empty, or a bound is out of range.
        """
        return self.backend.backward_cone(sinks, depth=depth, max_nodes=max_nodes)

    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Return who calls this — one hop back over the call graph, addressed by name.

        The name-based sibling of :meth:`get_callers`, returning
        :class:`~cldk.analysis.commons.results.SliceNode` objects rather than raw dicts. A module is
        a legitimate caller (``kind="module"``). ``[]`` is unambiguous: a name matching nothing
        raises.

        Raises:
            AmbiguousName: More than one callable matched.
            SelectorNotInGraph: Nothing matched.
        """
        return self.backend.callers_of(name, in_class=in_class, in_module=in_module)

    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Return what this calls — one hop forward, externals included (``kind="external"``).

        An external was never analysed, so it has no position: ``file=""`` and ``line=0``, with
        ``kind`` saying why. Its ``callable`` is the readable ``"<module>.<name>"``.

        Raises:
            AmbiguousName: More than one callable matched.
            SelectorNotInGraph: Nothing matched.
        """
        return self.backend.callees_of(name, in_class=in_class, in_module=in_module)

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """Return how one value reaches another — the *sequences*, where a slice is the set.

        Each hop says what justified it: the kind of edge (``data`` / ``control`` / ``argument`` /
        ``return`` / ``summary``), the variable, and the provenance — which in TypeScript is always
        ``["reaching-defs"]``. Only shortest paths are returned.

        **Two scopes, not one**: a value is addressed by a name plus the callable it enters, and a
        single scope could never find the cross-callable path this accessor exists for. ``depth`` is
        unbounded by default, for :meth:`reaches`'s reason.

        Args:
            src: The value the flow starts at.
            dst: The value it must reach.
            src_within: The callable ``src`` enters. Required.
            dst_within: The callable ``dst`` enters. Required.
            depth: Most hops a path may take; ``None`` for no bound.
            max_paths: Most paths to return; ``complete`` says whether more existed.

        Raises:
            AmbiguousName: A name matched more than one thing.
            SelectorNotInGraph: A name matched nothing.
            ValueError: A bound is out of range, or the two endpoints are the same position.
        """
        return self.backend.paths_between(src, dst, src_within=src_within, dst_within=dst_within, depth=depth, max_paths=max_paths)

    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """Return how one callable reaches another — the evidence-carrying form of :meth:`reaches`.

        Every hop is ``via="call"`` with no variable and no provenance: a call is a syntactic fact,
        and saying so is better than inventing a provenance for it. ``depth`` is unbounded by
        default.

        Raises:
            AmbiguousName: Either name matched more than one callable.
            SelectorNotInGraph: Either matched nothing.
            ValueError: A bound is out of range, or ``src`` and ``dst`` name the same callable.
        """
        return self.backend.call_paths_between(src, dst, depth=depth, max_paths=max_paths)

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Return whether this value reaches **any** argument of a call to ``callee``.

        A dataflow claim, not a control one: a value that merely runs before a call site and feeds
        none of its arguments is not counted. ``depth`` is unbounded by default — a bare ``False``
        carries no signal that a bound fired.

        Args:
            src: The value, named as a caller would.
            callee: The called callable.
            within: The callable ``src`` enters. Required; it scopes ``src`` only.
            depth: Most hops; ``None`` for no bound.

        Raises:
            AmbiguousName: A name matched more than one thing.
            SelectorNotInGraph: A name matched nothing.
            ValueError: ``depth`` is not a positive ``int``.
        """
        return self.backend.flows_to_call(src, callee, within=within, depth=depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Return whether this value reaches the argument ``arg`` of a call to ``callee``.

        The narrower question: a tainted value routinely reaches a function without reaching the
        parameter that matters. ``arg`` is resolved **by name**, never by position.

        Args:
            src: The value the flow starts at.
            callee: The called callable.
            arg: The callee's parameter, by name.
            within: The callable ``src`` enters. Required; ``arg`` is scoped by ``callee``.
            depth: Most hops; ``None`` for no bound.

        Raises:
            AmbiguousName: A name matched more than one thing.
            SelectorNotInGraph: A name matched nothing — including ``arg`` naming no parameter of
                ``callee``, which is a caller error and not a ``False``.
            ValueError: ``depth`` is not a positive ``int``.
        """
        return self.backend.flows_to_argument(src, callee, arg, within=within, depth=depth)
