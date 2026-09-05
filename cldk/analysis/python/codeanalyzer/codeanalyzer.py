################################################################################
# Copyright IBM Corporation 2024
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

"""Python analysis backend that wraps the ``codeanalyzer-python`` library.

This module provides the :class:`PyCodeanalyzer` class, which serves as the
in-process driver for Python static analysis. Unlike the Java backend (which
spawns an external JAR process), this backend imports and uses the
``codeanalyzer-python`` library directly within the same Python process.

The backend produces:
    - A :class:`~cldk.models.python.PyApplication` containing the full symbol
      table with modules, classes, methods, and their relationships.
    - A NetworkX :class:`~networkx.DiGraph` call graph derived from
      :class:`~cldk.models.python.PyCallEdge` records.

The analysis leverages:
    - **Jedi**: For semantic code understanding and symbol resolution.
    - **PyCG**: For call-graph construction.
    - **Tree-sitter**: For fast syntactic parsing.

Key features:
    - Symbol table extraction (classes, methods, functions, imports)
    - Call graph construction (inter- and intra-procedural)
    - Class hierarchy and inheritance analysis
    - Comment and docstring extraction

Note:
    This module is typically used internally by :class:`~cldk.analysis.python.PythonAnalysis`.
    Users should prefer the higher-level facade for most use cases.

See Also:
    - :class:`~cldk.analysis.python.PythonAnalysis`: High-level facade.
    - :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Java equivalent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple, Union

import networkx as nx

from codeanalyzer.core import Codeanalyzer
from codeanalyzer.options import AnalysisOptions, EmitTarget
from codeanalyzer.schema import Analysis, model_dump_json

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.results import CallableRef, Diagnostic, EntrypointCoverage, LocateResult, ModuleRef, TypeRef
from cldk.analysis.python.backend import PythonAnalysisBackend, body_key_column, bounded_subgraph, call_graph_scope, resolve_module_key, scope_paths
from cldk.models.python import (
    BodyNode,
    PyApplication,
    PyArtifact,
    PyCallEdge,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
    PyComment,
    PyConfigKey,
    PyConfigRead,
    PyConfigUseEdge,
    PyDependency,
    PyExternalSymbol,
    PyModule,
    Span,
)

logger = logging.getLogger(__name__)


def _overview(c: PyCallable, class_signature: str | None, kind: str) -> PyCallableOverview:
    """Project a :class:`PyCallable` into a lightweight :class:`PyCallableOverview`.

    ``c.decorators`` is 1.4.0's structured ``List[PyDecorator]`` (#128 upstream), not 0.3.x's
    flat ``List[str]`` — ``PyCallableOverview.decorators`` is CLDK's own model and keeps its
    ``List[str]`` shape (a frozen public contract), so decorators project down to a single string.
    Prefer ``qualified_name`` over ``name`` (falling back when Jedi couldn't resolve it): the
    Neo4j graph's flat ``decorators`` property is emitted as ``qualified_name or name``, so using
    bare ``name`` here would diverge from the Neo4j backend's ``get_callables_overview`` /
    ``get_decorated_callables`` for any decorator Jedi did resolve (``@lru_cache`` reads as
    ``"lru_cache"`` locally vs ``"functools.lru_cache"`` over Neo4j).
    """
    return PyCallableOverview(
        signature=c.signature,
        name=c.name,
        class_signature=class_signature,
        kind=kind,
        path=c.path,
        start_line=c.start_line,
        end_line=c.end_line,
        decorators=[d.qualified_name or d.name for d in c.decorators],
    )


def _class_overview(cls: PyClass, path: str) -> PyClassOverview:
    """Project a :class:`PyClass` into a lightweight :class:`PyClassOverview` (see :func:`_overview`
    for the callable equivalent). ``PyClass`` has no ``path`` field of its own (unlike
    ``PyCallable``), so the owning module's path is threaded in by the caller."""
    return PyClassOverview(
        signature=cls.signature,
        name=cls.name,
        path=path,
        start_line=cls.start_line,
        end_line=cls.end_line,
        decorators=[d.qualified_name or d.name for d in cls.decorators],
    )


def _slice(span: "Span | None", module_source: str) -> str | None:
    """The text ``span`` covers, sliced out of ``module_source`` by its UTF-8 byte offsets.

    ``span.bytes`` are UTF-8 byte offsets (see ``codeanalyzer.schema.py_schema.byte_offsets``), not
    character offsets, hence the encode/slice/decode instead of a plain string slice — a non-ASCII
    character anywhere earlier in the module would shift a naive character slice. Shared by
    :func:`_code_of` (a callable's own span) and :meth:`PyCodeanalyzer.get_source` (any node's
    span), so this byte-slicing logic exists exactly once.
    """
    if span is None or not module_source:
        return None
    start, end = span.bytes
    return module_source.encode("utf-8")[start:end].decode("utf-8")


def _code_of(c: PyCallable, module_source: str) -> str | None:
    """The callable's source text, sliced out of its owning module's source by ``span.bytes``.

    1.4.0 dropped ``PyCallable.code`` (the denormalized source-text field 0.3.x carried on every
    callable) in favor of ``span`` (byte offsets into ``PyModule.source``) — the Neo4j graph still
    projects a flat ``code`` property (computed at emit time), but the in-memory model no longer
    does, so this backend now reconstructs it the same way.
    """
    return _slice(c.span, module_source)


def _resolve_callee(
    cs: PyCallsite,
    body: Dict[str, BodyNode],
    id_to_sig: Dict[str, str],
    declared_sigs: set,
    reverse_externals: Dict[str, str],
) -> PyCallsite:
    """Resolve ``cs.callee_signature`` so a library/builtin target is addressable through
    :meth:`PyCodeanalyzer.get_external_symbols`, instead of staying in Jedi's raw, unaddressable
    dotted-name form (see :meth:`PythonAnalysisBackend.get_callsites_for`).

    Two resolution sources, the body node preferred when it exists:

    1. The call site's own body node (``body[f"{start_line}:{start_column}"]``) carries
       ``callee`` — already a canonical ``can://`` id, declared or ``@external`` — once the
       analysis ran at a level where the defuse-linker backfill runs (``-a 2``+). ``id_to_sig``
       turns a *declared* id back into its dotted signature; an external id is already the right
       shape and passes through unchanged.
    2. Otherwise, Jedi's own ``callee_signature`` (present regardless of level): already the right
       dotted signature for a declared target (it's a member of ``declared_sigs`` by
       construction), or rewritten to the external's ``can://…/@external/…`` id by reversing
       ``PyExternalSymbol.module``/``.name`` back into the dotted spelling that names it in
       ``reverse_externals`` — the *inverse* of how the analyzer minted that id, not a
       reimplementation of its private id-construction logic, so it can't drift out of step with
       it. A signature not in this map (the homing pass never saw it, or the target was dropped as
       lib→lib) is left as Jedi wrote it rather than guessed at further.

    A call site with neither — Jedi failed and no body-node resolution exists — keeps
    ``callee_signature`` as ``None``: genuinely unresolved, not a gap this closes.
    """
    node = body.get(f"{cs.start_line}:{cs.start_column}")
    if node is not None and node.callee:
        resolved = id_to_sig.get(node.callee, node.callee)
    elif cs.callee_signature and cs.callee_signature not in declared_sigs:
        resolved = reverse_externals.get(cs.callee_signature, cs.callee_signature)
    else:
        resolved = cs.callee_signature
    return cs if resolved == cs.callee_signature else cs.model_copy(update={"callee_signature": resolved})


def _find_innermost(module: PyModule, line: int) -> Tuple[PyCallable, "PyClass | None"] | None:
    """The callable (and its immediate owning class, if any) whose span most tightly contains
    ``line`` — innermost first, so a closure nested inside a method wins over the method itself.

    Only real callable spans count: a blank line, a comment, or a gap between two callables' spans
    contains no callable and must never snap to the nearest one (see :meth:`locate`).

    Equal line widths tie — ``def one(self): return lambda: 2`` nests two callables on one line — and
    lines are all the Neo4j projection carries, so the tie breaks on the *longer signature*: a nested
    callable's signature strictly extends its owner's (``...one.<locals>.<lambda>``), so longer is
    deeper. Without it the local walk keeps whichever it met first (the owner) and the Neo4j backend
    keeps whichever Cypher returned first, so the two backends could disagree — the exact divergence
    ``test_locate_parity_*`` exists to forbid.
    """
    best: Tuple[PyCallable, "PyClass | None"] | None = None
    best_rank: Tuple[int, int, str] | None = None

    def consider(c: PyCallable, owner: "PyClass | None") -> None:
        nonlocal best, best_rank
        if c.start_line < 0 or c.end_line < 0:
            return
        if not (c.start_line <= line <= c.end_line):
            return
        rank = (c.end_line - c.start_line, -len(c.signature), c.signature)
        if best_rank is None or rank < best_rank:
            best, best_rank = (c, owner), rank

    def walk_callable(c: PyCallable, owner: "PyClass | None") -> None:
        consider(c, owner)
        for inner in c.callables.values():
            walk_callable(inner, None)  # a nested (closure) callable has no owning class
        for inner_cls in c.types.values():
            walk_class(inner_cls)

    def walk_class(cls: PyClass) -> None:
        for m in cls.callables.values():
            walk_callable(m, cls)
        for inner_cls in cls.types.values():
            walk_class(inner_cls)

    for cls in module.types.values():
        walk_class(cls)
    for fn in module.functions.values():
        walk_callable(fn, None)

    return best


def _find_body_node(c: PyCallable, line: int) -> "Tuple[str, BodyNode] | None":
    """The innermost body node of ``c`` whose span contains ``line`` and its ``c.body`` key, or
    ``None``. The key rides along so a caller can build the node's :meth:`PyCodeanalyzer.get_source`
    id (``"<c.signature>@<key>"``) without a second walk over ``c.body``.

    ``c.body`` is keyed by local id (``"20:8"``, ``"@entry"``, ``"22:8/actual_in:0"``). A node with
    no ``span`` is a synthetic analysis vertex (``@entry`` / ``@exit`` / ``@formal_in:N``) modelling
    dataflow, not a source region — it can never contain a position, so it is skipped rather than
    treated as a match. Containment is by line, the only granularity the Neo4j backend's projection
    also carries (``:PyBodyNode`` gets ``start_line``/``end_line`` and nothing finer), so the two
    backends agree on which node is innermost. Two nodes spanning the same single line (``if x:
    return x``) tie on width, so the tie breaks on the key's start column, deeper first — see
    :func:`~cldk.analysis.python.backend.body_key_column` for why the key and not the span, and why
    it is parsed rather than string-compared. A body node's graph ``id`` is ``<callable can:// id>@<body
    key>``, so the Neo4j backend ranks on the same key this does.

    ``None`` is a real outcome, not an error: a position on the ``def`` line, on a blank line, or on
    a comment inside a callable is contained by the callable and by no body node, and the caller
    still gets the callable.
    """
    best: "BodyNode | None" = None
    best_key: Tuple[int, int, str] | None = None
    best_local_key: str | None = None
    for key, node in (c.body or {}).items():
        if node.span is None:
            continue
        if not (node.span.start[0] <= line <= node.span.end[0]):
            continue
        rank = (node.span.end[0] - node.span.start[0], -body_key_column(key), key)
        if best_key is None or rank < best_key:
            best, best_key, best_local_key = node, rank, key
    return (best_local_key, best) if best is not None else None


class PyCodeanalyzer(PythonAnalysisBackend):
    """In-process driver for the ``codeanalyzer-python`` analysis backend.

    This class serves as the primary interface to the codeanalyzer-python
    library, managing analysis execution, caching, and result retrieval.
    It runs entirely in-process, importing the codeanalyzer library directly
    rather than spawning external processes.

    The analyzer produces a :class:`~cldk.models.python.PyApplication` containing:
        - A complete symbol table mapping file paths to module objects
        - Class definitions with methods, attributes, and inheritance info
        - Function definitions with signatures and parameters
        - Import statements and their resolution
        - A call graph (when ``analysis_level`` is ``"call_graph"``)

    Attributes:
        project_dir (Path): Path to the Python project being analyzed.
        analysis_level (str): The depth of analysis performed.
        eager_analysis (bool): Whether to force regeneration of caches.
        target_files (List[str] | None): Specific files to analyze.
        cache_dir (Path | None): Cache directory for the backend.
        analysis_json_path (Path | None): Path for persisting analysis results.
        analysis (Analysis): The v2 envelope the analyzer returns — ``application`` plus
            ``schema_version`` / ``max_level`` / ``analyzer`` metadata later callers may need to
            tell "no result" apart from "unanswerable at this analysis level".
        application (PyApplication): The analyzed application model (``analysis.application``).
        call_graph (nx.DiGraph | None): The call graph (if analysis_level is call_graph).

    See Also:
        - :class:`~cldk.analysis.python.PythonAnalysis`: High-level facade.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        analysis_level: str,
        analysis_json_path: Union[str, Path, None],
        eager_analysis: bool,
        cache_dir: Union[str, Path, None] = None,
        target_files: List[str] | None = None,
        use_ray: bool = False,
    ) -> None:
        """Initialize the Python code analyzer and run analysis.

        Creates a new analyzer instance for the specified project. Analysis
        is performed immediately during initialization, with results cached
        for subsequent method calls.

        Args:
            project_dir: Absolute or relative path to the Python project root
                directory. This directory should contain Python source files
                to analyze. Required; cannot be ``None``.
            analysis_level: The depth of analysis to perform. Use
                ``"symbol_table"`` for basic symbol extraction or
                ``"call_graph"`` for full call graph construction.
                See :class:`~cldk.analysis.AnalysisLevel` for options.
            analysis_json_path: Path where the analysis results should be
                persisted. Forwarded directly to the codeanalyzer-python
                backend's ``output`` option. If ``None``, the backend
                uses its default location.
            eager_analysis: If ``True``, forces the backend to rebuild
                its analysis from scratch, ignoring any cached results.
                If ``False``, cached results are reused when available.
            cache_dir: Directory for codeanalyzer-python's caches, including
                its virtualenv and analysis cache files. If ``None``, defaults
                to ``<project_dir>/.codeanalyzer``.
            target_files: Optional list of specific files to analyze. Note
                that codeanalyzer-python currently supports only a single
                target file; if multiple are provided, only the first is
                used and a warning is logged.
            use_ray: If ``True``, enables Ray-based parallel processing for
                analysis. Recommended for very large projects where analysis
                would otherwise be slow. Requires Ray to be installed.
                Defaults to ``False``.

        Raises:
            ValueError: If ``project_dir`` is ``None``.

        Note:
            Analysis is performed synchronously during initialization.
            For large projects, this may take significant time.
        """
        if project_dir is None:
            raise ValueError("project_dir is required for Python analysis.")
        # Expand ~ and resolve to absolute path for robustness
        self.project_dir = Path(project_dir).expanduser().resolve()
        if not self.project_dir.is_dir():
            raise ValueError(f"project_dir does not exist or is not a directory: {self.project_dir}")
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.use_ray = use_ray

        # codeanalyzer-python owns all caching. CLDK forwards these paths
        # verbatim; when cache_dir is None the backend defaults it to
        # <project_dir>/.codeanalyzer.
        self.cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else None
        self.analysis_json_path = Path(analysis_json_path).expanduser().resolve() if analysis_json_path else None

        # codeanalyzer-python 1.4.0's analyze() returns the v2 envelope (schema_version,
        # max_level, analyzer, application) rather than a bare application. Unwrap it here, once,
        # so every downstream `self.application.xxx` read below keeps working unchanged; the
        # envelope itself is kept on `self.analysis` rather than discarded, since schema_version /
        # max_level are facts a later, interprocedural-aware facade needs to tell "no result"
        # apart from "unanswerable at this analysis level".
        self.analysis: Analysis = self._run_analyzer()
        self.application: PyApplication = self.analysis.application
        # Class-signature → file path lookup, built once.
        self._class_to_file: Dict[str, str] = {}
        for file_path, module in self.application.symbol_table.items():
            for class_sig in module.types:
                self._class_to_file[class_sig] = file_path

        if analysis_level == AnalysisLevel.call_graph:
            self.call_graph: nx.DiGraph | None = self._build_call_graph(self.application.call_graph, self._id_to_signature())
        else:
            self.call_graph = None

    # ----------------------------------------------------------------- core
    def _run_analyzer(self) -> Analysis:
        """Execute the codeanalyzer-python analysis and return the v2 envelope.

        Configures and runs the codeanalyzer-python backend with the options
        specified during initialization. The backend handles all caching
        internally.

        Returns:
            The :class:`~codeanalyzer.schema.Analysis` envelope (``schema_version``,
            ``max_level``, ``analyzer``, ``application``) — see the caller in
            :meth:`__init__`, which unwraps ``.application`` for every other accessor on this
            class.

        Note:
            If ``target_files`` contains multiple files, only the first
            is used (with a warning logged) as codeanalyzer-python currently
            supports single-file targeting only.
        """
        target_file = None
        if self.target_files:
            if len(self.target_files) > 1:
                logger.warning("codeanalyzer-python supports only a single target file; using the first.")
            target_file = Path(self.target_files[0])

        options = AnalysisOptions(
            input=self.project_dir,
            output=self.analysis_json_path,
            emit=EmitTarget.JSON,
            using_ray=self.use_ray,
            rebuild_analysis=self.eager_analysis,
            skip_tests=True,
            file_name=target_file,
            cache_dir=self.cache_dir,
            clear_cache=False,
            verbosity=0,
        )

        with Codeanalyzer(options) as analyzer:
            return analyzer.analyze()

    def _id_to_signature(self) -> Dict[str, str]:
        """Map every declared callable's canonical ``can://`` id to its signature.

        1.4.0's ``PyCallEdge.src``/``.dst`` are ids, not signatures, but every other accessor on
        this backend (and the Neo4j backend, which keys its own call graph by
        ``s.signature``/``t.signature`` straight off Cypher) keys the call graph by signature —
        so edges are resolved back to that identity once here, rather than a dual-backend
        divergence where the local graph is id-keyed and the Neo4j one isn't.
        """
        return {c.id: c.signature for c, _, _, _ in self._iter_callables()}

    @staticmethod
    def _build_call_graph(edges: List[PyCallEdge], id_to_signature: Dict[str, str]) -> nx.DiGraph:
        """Convert a list of call edges into a NetworkX directed graph.

        Transforms the flat list of :class:`PyCallEdge` objects from the
        analysis results into a NetworkX directed graph structure for
        efficient graph queries.

        Args:
            edges: List of :class:`~cldk.models.python.PyCallEdge` objects
                representing call relationships between methods/functions.
            id_to_signature: Maps a declared callable's ``id`` to its ``signature`` (see
                :meth:`_id_to_signature`) — used to resolve ``edge.src``/``.dst`` (1.4.0's are
                ``can://`` ids) back to the signature every other accessor keys by. A target that
                isn't in the map (an ``@external`` id, not a declared callable) keeps its raw id
                rather than the edge being dropped.

        Returns:
            A ``networkx.DiGraph`` where:
                - Nodes are method/function signatures, or a raw ``@external`` id when unresolved
                - Edges represent call relationships from caller to callee
                - Edge attributes are ``type`` (constant ``"CALL_DEP"``), ``weight`` and
                  ``provenance``. 1.4.0's ``PyCallEdge`` payload itself has no ``type`` field
                  (canonical v2's rule: the edge list's own name IS the type) — but that governs
                  the schema payload, not this networkx projection, which is CLDK's own and keeps
                  its own ``"CALL_DEP"`` convention: the Neo4j backend's ``_build_call_graph``
                  hardcodes the same constant, and Java/TypeScript both assert it too
                  (``test_jcodeanalyzer.py``, ``test_typescript_neo4j_backend.py``).
        """
        graph = nx.DiGraph()
        for edge in edges:
            src = id_to_signature.get(edge.src, edge.src)
            dst = id_to_signature.get(edge.dst, edge.dst)
            graph.add_edge(src, dst, type="CALL_DEP", weight=edge.weight, provenance=tuple(edge.prov))
        return graph

    # --------------------------------------------------------- application
    def get_application_view(self) -> PyApplication:
        """Return the complete analyzed application model.

        Returns:
            The :class:`~cldk.models.python.PyApplication` object containing
            all analysis results for the project.
        """
        return self.application

    def get_symbol_table(self, *, paths: Sequence[str] | None = None) -> Dict[str, PyModule]:
        """Return the symbol table mapping file paths to modules.

        Args:
            paths: Restrict to these modules, named by symbol-table key. ``None`` (the default)
                returns the whole table — the same object it always returned.

        Returns:
            A dictionary where keys are file paths (strings) and values
            are :class:`~cldk.models.python.PyModule` objects.

        Raises:
            ValueError: ``paths`` is an empty sequence — omit it to enumerate everything.
            SelectorNotInGraph: a path names no module in this application
                (see :func:`~cldk.analysis.python.backend.scope_paths`).
        """
        table = self.application.symbol_table
        keys = scope_paths(paths, table.keys())
        return table if keys is None else {k: table[k] for k in keys}

    def get_modules(self) -> List[PyModule]:
        """Return all analyzed modules as a list.

        Returns:
            A list of :class:`~cldk.models.python.PyModule` objects,
            one for each analyzed Python file.
        """
        return list(self.application.symbol_table.values())

    def get_call_graph(self, *, roots: Sequence[str] | None = None, depth: int | None = None) -> nx.DiGraph:
        """Return the call graph as a NetworkX directed graph.

        Lazily builds the whole call graph from edge data if not already constructed, then — when
        ``roots`` is given — carves the reachable sub-graph out of it. The full graph is already
        in memory here, so there is nothing to push down: the cache is built once and every scoped
        call is served from it (see :func:`~cldk.analysis.python.backend.bounded_subgraph`; the
        Neo4j backend reaches the same shape through Cypher instead).

        Args:
            roots: Restrict to the sub-graph reachable from these callables, by signature.
                ``None`` (the default) returns the whole graph, the cached object itself.
            depth: Maximum call hops from a root; ``None`` is unbounded.

        Returns:
            A ``networkx.DiGraph`` representing method/function call
            relationships across the project.

        Raises:
            ValueError: ``depth`` below 1, or ``depth`` without ``roots``.
            SelectorNotInGraph: a root that is neither declared by this application nor a node of
                the call graph.
        """
        scope = call_graph_scope(roots, depth)
        if self.call_graph is None:
            self.call_graph = self._build_call_graph(self.application.call_graph, self._id_to_signature())
        if scope is None:
            return self.call_graph
        # The inventory, not the graph, is what a root is checked against: the graph is built from
        # call edges alone, so a declared callable in no edge is not a node in it (444 of odoo's
        # 15,549) and checking membership here would raise for a callable that plainly exists,
        # where Neo4j — matching roots by label — returns the one-node graph. See bounded_subgraph.
        return bounded_subgraph(self.call_graph, scope, depth, (c.signature for c, _, _, _ in self._iter_callables()))

    def get_call_graph_json(self) -> str:
        """Return the complete application model serialized as JSON.

        Returns:
            A JSON string containing the full analysis results,
            suitable for persistence or external tool consumption.
        """
        return model_dump_json(self.application, indent=None)

    def get_python_module(self, file_path: str) -> PyModule | None:
        """Return the module object for a specific file path.

        Args:
            file_path: The path to the Python file.

        Returns:
            The :class:`~cldk.models.python.PyModule` for the file,
            or ``None`` if not found.
        """
        return self.application.symbol_table.get(str(file_path))

    def get_python_file(self, qualified_class_name: str) -> str | None:
        """Return the file path containing a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            The file path as a string, or ``None`` if the class is not found.
        """
        return self._class_to_file.get(qualified_class_name)

    # ----------------------------------------------------------- classes
    def get_all_classes(self, *, module: str | None = None) -> Dict[str, PyClass]:
        """Return all classes from all modules in the project.

        Aggregates class definitions from all analyzed modules into a
        single dictionary for convenient access.

        Args:
            module: Restrict to one module's classes, named by symbol-table key (not a dotted
                module name). ``None`` (the default) aggregates the whole application.

        Returns:
            A dictionary mapping qualified class names to
            :class:`~cldk.models.python.PyClass` objects.

        Raises:
            SelectorNotInGraph: ``module`` names no module in this application. It resolves
                through the same :func:`~cldk.analysis.python.backend.scope_paths` as ``paths=``
                but reports itself as ``module``, so the error names the keyword the caller wrote.
        """
        table = self.application.symbol_table
        keys = scope_paths(None if module is None else [module], table.keys(), kind="module")
        result: Dict[str, PyClass] = {}
        for mod in table.values() if keys is None else (table[k] for k in keys):
            result.update(mod.types)
        return result

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        """Return a specific class by its qualified name.

        Args:
            qualified_class_name: The fully qualified class name
                (e.g., ``"mypackage.models.User"``).

        Returns:
            The :class:`~cldk.models.python.PyClass` object,
            or ``None`` if not found.
        """
        return self.get_all_classes().get(qualified_class_name)

    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """Return inner classes defined within a specific class.

        Args:
            qualified_class_name: The fully qualified name of the outer class.

        Returns:
            A list of :class:`~cldk.models.python.PyClass` objects for
            each nested class. Empty list if no nested classes or class
            not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.types.values()) if cls else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Return all classes that inherit from a specific class.

        Searches all classes in the project for those that extend the
        specified base class, using both short and qualified name matching.

        Args:
            qualified_class_name: The fully qualified name of the base class.

        Returns:
            A dictionary mapping qualified names to
            :class:`~cldk.models.python.PyClass` objects for all subclasses.
            Returns empty dict if the base class is not found.
        """
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return {}
        short_name = cls.name
        result: Dict[str, PyClass] = {}
        for sig, candidate in self.get_all_classes().items():
            if sig == qualified_class_name:
                continue
            for base in candidate.base_classes:
                if base == short_name or base == qualified_class_name or base.endswith("." + short_name):
                    result[sig] = candidate
                    break
        return result

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """Return the base class names for a specific class.

        Args:
            qualified_class_name: The fully qualified name of the class.

        Returns:
            A list of base class names as strings. Returns empty list
            if the class is not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.base_classes) if cls else []

    # ----------------------------------------------------------- methods
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return all methods in the project grouped by class.

        Aggregates all methods from all classes, plus module-level functions,
        into a nested dictionary structure.

        Returns:
            A nested dictionary with structure::

                {
                    "qualified.class.Name": {
                        "method_signature": PyCallable,
                        ...
                    },
                    "module.name": {  # for module-level functions
                        "function_signature": PyCallable,
                        ...
                    },
                    ...
                }

        Note:
            Module-level functions are included under the module name as
            the outer key, allowing unified access to all callables.
        """
        result: Dict[str, Dict[str, PyCallable]] = {}
        for module in self.application.symbol_table.values():
            for class_sig, cls in module.types.items():
                result[class_sig] = dict(cls.callables)
            if module.functions:
                result.setdefault(module.module_name, {}).update(module.functions)
        return result

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return all methods defined in a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A dictionary mapping method signatures to
            :class:`~cldk.models.python.PyCallable` objects.
            Returns empty dict if class not found.

        Note:
            Returned callables' call sites are the raw ``PyApplication.symbol_table`` ones, not
            resolved through :func:`_resolve_callee`: a call to an external target keeps Jedi's
            raw, unaddressable dotted guess instead of the ``@external`` can-id
            :meth:`get_callsites_for` resolves it to. Use :meth:`get_callsites_for` when that
            resolution matters.
        """
        cls = self.get_class(qualified_class_name)
        return dict(cls.callables) if cls else {}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> PyCallable | None:
        """Return a specific method or module-level function by scope and name.

        ``qualified_class_name`` is looked up the same way as
        :meth:`get_all_methods_in_application`'s outer keys: a class signature resolves to that
        class's methods, and a module name (``PyModule.module_name``) resolves to that module's
        top-level functions. Supports both fully qualified method names and simple method names;
        when a simple name is provided, falls back to matching by the callable's ``name``
        attribute.

        Note:
            Callables nested inside another callable (``inner_callables``) are not reachable via
            this lookup — only top-level class methods and top-level module functions are.

        Note:
            If a class signature ever equals a module's name (pathological but constructible,
            e.g. class ``pkg.User`` vs file ``pkg/User.py``), this backend merges both under one
            key in :meth:`get_all_methods_in_application`, while the Neo4j backend resolves
            class-first — a resolution-order asymmetry between the two backends.

        Note:
            The returned callable's call sites are the raw ``PyApplication.symbol_table`` ones,
            not resolved through :func:`_resolve_callee`: a call to an external target keeps
            Jedi's raw, unaddressable dotted guess instead of the ``@external`` can-id
            :meth:`get_callsites_for` resolves it to.

        Args:
            qualified_class_name: The fully qualified class name, or a module name for
                module-level functions.
            qualified_method_name: The method name or signature to find.

        Returns:
            The :class:`~cldk.models.python.PyCallable` object,
            or ``None`` if not found.
        """
        methods = self.get_all_methods_in_application().get(qualified_class_name, {})
        if qualified_method_name in methods:
            return methods[qualified_method_name]
        # Fallback: match by short name when only the simple name is given.
        for sig, callable_ in methods.items():
            if callable_.name == qualified_method_name:
                return callable_
        return None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """Return parameter names for a specific method.

        Args:
            qualified_class_name: The fully qualified class name.
            qualified_method_name: The method name or signature.

        Returns:
            A list of parameter names as strings. Returns empty list
            if the method is not found.
        """
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return the ``__init__`` methods of a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A dictionary mapping constructor signatures to
            :class:`~cldk.models.python.PyCallable` objects.
            Typically contains at most one ``__init__`` method.

        Note:
            Routed through :meth:`get_all_methods_in_class`, so the same caveat applies: call
            sites are not resolved through :func:`_resolve_callee` here. Use
            :meth:`get_callsites_for` when that resolution matters.
        """
        return {sig: c for sig, c in self.get_all_methods_in_class(qualified_class_name).items() if c.name == "__init__"}

    def get_all_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        """Return class attributes for a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A list of :class:`~cldk.models.python.PyClassAttribute` objects.
            Returns empty list if class not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    # ----------------------------------------------------------- bulk / projected accessors
    def _iter_callables(self) -> Iterator[Tuple[PyCallable, "str | None", str, str]]:
        """Yield ``(callable, class_signature, kind, module_source)`` for every callable in the
        application.

        Walks the in-memory symbol table the same way the Neo4j backend's ``MATCH (c:PyCallable)``
        sees nodes: a callable is a ``"method"`` only when a class declares it directly (mirroring
        ``PY_HAS_METHOD``); module-level functions and functions nested inside a callable are
        ``"function"`` with a ``None`` class signature. The two backends therefore enumerate the
        same set. ``module_source`` (the owning module's full source text) rides along so callers
        needing the callable's own source can slice it via :func:`_code_of` — a callable's
        ``span`` offsets are always relative to its top-level module, however deeply nested.
        """

        def from_callable(c: PyCallable, source: str):
            for inner in c.callables.values():
                yield inner, None, "function", source
                yield from from_callable(inner, source)
            for inner_cls in c.types.values():
                yield from from_class(inner_cls, source)

        def from_class(cls: PyClass, source: str):
            for m in cls.callables.values():
                yield m, cls.signature, "method", source
                yield from from_callable(m, source)
            for inner_cls in cls.types.values():
                yield from from_class(inner_cls, source)

        for module in self.application.symbol_table.values():
            for cls in module.types.values():
                yield from from_class(cls, module.source)
            for fn in module.functions.values():
                yield fn, None, "function", module.source
                yield from from_callable(fn, module.source)

    def _iter_classes(self) -> Iterator[Tuple[PyClass, str]]:
        """Yield ``(class, module_path)`` for every class in the application, including classes
        nested inside classes or inside callables (a class defined in a function body) — the same
        containment tree :meth:`_iter_callables` walks for callables, so this backend enumerates
        the same flat set of ``:PyClass`` nodes a ``MATCH (cl:PyClass)`` sees over Neo4j, with no
        nesting blind spot. ``module_path`` is the symbol table's own key (matches the graph's
        ``_module`` property) — unlike ``PyCallable``, ``PyClass`` carries no ``path`` field of its
        own.
        """

        def from_class(cls: PyClass, path: str):
            yield cls, path
            for inner in cls.types.values():
                yield from from_class(inner, path)
            for m in cls.callables.values():
                yield from from_callable(m, path)

        def from_callable(c: PyCallable, path: str):
            for inner_cls in c.types.values():
                yield from from_class(inner_cls, path)
            for inner in c.callables.values():
                yield from from_callable(inner, path)

        for path, module in self.application.symbol_table.items():
            for cls in module.types.values():
                yield from from_class(cls, path)
            for fn in module.functions.values():
                yield from from_callable(fn, path)

    def get_callables_overview(self) -> List[PyCallableOverview]:
        """Return a lightweight overview of every callable in the application (see
        :meth:`PythonAnalysisBackend.get_callables_overview`)."""
        return [_overview(c, class_sig, kind) for c, class_sig, kind, _ in self._iter_callables()]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Return ``{signature: code}`` for the requested signatures that exist and have a body
        (omits callables whose ``code`` is ``None``)."""
        wanted = set(signatures)
        out: Dict[str, str] = {}
        for c, _, _, source in self._iter_callables():
            if c.signature not in wanted:
                continue
            code = _code_of(c, source)
            if code is not None:
                out[c.signature] = code
        return out

    def get_source(self, node_id: str) -> str:
        """Return the source text named by ``node_id`` (see
        :meth:`PythonAnalysisBackend.get_source`) — a callable, or one of its body nodes, sliced
        out of the owning module's text by ``span.bytes`` via :func:`_slice`, the same
        byte-accurate helper :meth:`get_method_bodies` uses for the callable case.

        The callable half of the id is matched against **both** of a callable's names: its
        ``signature`` (the dotted module path, which is what every other accessor keys by and what
        callers have passed since leg 1) and its ``id`` (the ``can://`` containment path, which is
        what :meth:`locate` now returns inside a body-node id — #320). Accepting either is what
        keeps ``get_source(locate(...).node_id)`` working across that change without asking the
        caller to know which vocabulary it is holding.
        """
        sig, sep, body_key = node_id.partition("@")
        for c, _, _, source in self._iter_callables():
            if sig not in (c.signature, c.id):
                continue
            if not sep:
                code = _code_of(c, source)
            else:
                node = (c.body or {}).get(body_key)
                if node is None:
                    raise KeyError(f"{sig!r} has no body node keyed {body_key!r} (from node_id {node_id!r})")
                code = _slice(node.span, source)
            if code is None:
                raise KeyError(f"no recoverable source for node_id {node_id!r} (no span)")
            return code
        raise KeyError(f"no callable with signature {sig!r} (from node_id {node_id!r})")

    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        """Return overviews of callables decorated with any of ``markers``."""
        marker_set = set(markers)
        return [
            _overview(c, class_sig, kind)
            for c, class_sig, kind, _ in self._iter_callables()
            if marker_set.intersection(d.qualified_name or d.name for d in c.decorators)
        ]

    def get_entrypoints(self) -> List[PyCallableOverview]:
        """Return overviews of every callable marked ``is_entrypoint`` (see
        :meth:`PythonAnalysisBackend.get_entrypoints`)."""
        return [_overview(c, class_sig, kind) for c, class_sig, kind, _ in self._iter_callables() if c.is_entrypoint]

    def get_entrypoint_classes(self) -> List[PyClassOverview]:
        """Return overviews of every class marked ``is_entrypoint`` (see
        :meth:`PythonAnalysisBackend.get_entrypoint_classes`)."""
        return [_class_overview(cls, path) for cls, path in self._iter_classes() if cls.is_entrypoint]

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """Return the entrypoint-detection pass's coverage/failure record (see
        :meth:`PythonAnalysisBackend.get_entrypoint_coverage`) -- a direct passthrough of
        ``PyApplication.entrypoint_report``, which the local backend has in full."""
        r = self.application.entrypoint_report
        return EntrypointCoverage(
            frameworks_detected=list(r.frameworks_detected),
            rulesets=list(r.rulesets),
            unresolved=dict(r.unresolved),
            errors=list(r.errors),
        )

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`PythonAnalysisBackend.has_resolution_edges`. Unconditionally ``True``: the
        local backend attempts Jedi resolution on every call site regardless of analysis level,
        unlike the Neo4j backend's ``PY_RESOLVES_TO`` edge, which is only backfilled at higher
        analysis levels."""
        return True

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        """Return ``{signature: call_sites}`` for the requested signatures that exist, with each
        call site's ``callee_signature`` resolved (see :meth:`PythonAnalysisBackend.get_callsites_for`
        and :func:`_resolve_callee`)."""
        wanted = set(signatures)
        id_to_sig = self._id_to_signature()
        declared_sigs = set(id_to_sig.values())
        reverse_externals = {
            (f"{ext.module}.{ext.name}" if ext.module else ext.name): ext.id
            for ext in self.application.external_symbols.values()
        }
        return {
            c.signature: [_resolve_callee(cs, c.body, id_to_sig, declared_sigs, reverse_externals) for cs in c.call_sites]
            for c, _, _, _ in self._iter_callables()
            if c.signature in wanted
        }

    def get_external_symbols(self) -> Dict[str, PyExternalSymbol]:
        """Return every ``@external`` ghost symbol the application's call graph homed (see
        :meth:`PythonAnalysisBackend.get_external_symbols`) — a direct passthrough of
        ``PyApplication.external_symbols``, which the local backend has in full."""
        return dict(self.application.external_symbols)

    # ----------------------------------------------------------- repository artifacts
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Return every non-code artifact (see :meth:`AnalysisBackend.get_artifacts`).

        ``PyApplication.artifacts`` is already keyed by repo-relative path (the analyzer's own
        ``project.py`` iterates it the same way), so this is a direct passthrough."""
        return dict(self.application.artifacts)

    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        """Return every declared dependency, optionally filtered (see
        :meth:`AnalysisBackend.get_dependencies`)."""
        deps = self.application.dependencies
        if direct_only:
            deps = [d for d in deps if d.direct]
        if ecosystem is not None:
            deps = [d for d in deps if d.ecosystem == ecosystem]
        if declared_in is not None:
            deps = [d for d in deps if d.declared_in == declared_in]
        return list(deps)

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Return every configuration key (see :meth:`AnalysisBackend.get_config_keys`).

        ``PyApplication`` carries no flat ``config_keys`` collection — each key nests under the
        artifact that defines it (``PyArtifact.config_keys``, mirroring the graph's
        ``DEFINES_CONFIG`` containment edge) — so this flattens them, keyed by id."""
        return {ck.id: ck for artifact in self.application.artifacts.values() for ck in artifact.config_keys}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Return resolved code-to-config edges (see :meth:`AnalysisBackend.get_config_uses`)."""
        edges = list(self.application.config_uses)
        if key is None:
            return edges
        matching_ids = {ck.id for ck in self.get_config_keys().values() if ck.key == key}
        return [e for e in edges if e.dst in matching_ids]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Return every detector-matched config read that never resolved to a declared key (see
        :meth:`AnalysisBackend.get_unresolved_config_reads`) -- a direct passthrough."""
        return list(self.application.config_reads_unresolved)

    def get_config_readers(self, key: str) -> List[PyCallableOverview]:
        """Return overviews of every callable reading configuration key ``key`` (see
        :meth:`PythonAnalysisBackend.get_config_readers`).

        Resolves :meth:`get_config_uses`'s opaque ``src`` (a GLOBAL ordinal id,
        ``<callable-id>@<local-id>``) back to the owning callable by its ``id`` -- the same split
        Task 8's report documented as the caller's job, done here instead.
        """
        reading_ids = {e.src.rsplit("@", 1)[0] for e in self.get_config_uses(key)}
        if not reading_ids:
            return []
        return [_overview(c, class_sig, kind) for c, class_sig, kind, _ in self._iter_callables() if c.id in reading_ids]

    # ----------------------------------------------------------- locate
    def _not_analysed(self, path: str, line: int) -> LocateResult:
        """The ``file_not_in_graph`` outcome, with the one distinction this backend *can* draw.

        Unlike the Neo4j backend (which attaches to a graph and may not have the project checked
        out), this one runs against the project directory, so it can tell "the file is there and
        was not analysed" — a ``--target-files`` narrowing, an excluded directory, a syntax error
        the analyzer skipped — from "there is no such file". The code stays ``file_not_in_graph``
        either way; the distinction rides in the message, which is the field an agent reads.
        """
        project_dir = getattr(self, "project_dir", None)
        on_disk = Path(path).is_file() or bool(project_dir and (Path(project_dir) / path).is_file())
        why = "the file exists but no analysed module covers it" if on_disk else "no such file in the analysed project"
        return LocateResult(
            node=None,
            callable=None,
            type=None,
            module=ModuleRef(path=str(path)),
            source="",
            span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
            diagnostics=[Diagnostic(code="file_not_in_graph", message=f"{path} is not covered by any analysed module ({why}).")],
        )

    def _locate_one(self, path: str, line: int) -> LocateResult:
        # Whatever the caller's scanner printed ("./src/app.py", an absolute path) is normalised to
        # the symbol-table key first; an unnormalised path would otherwise read as file_not_in_graph.
        key = resolve_module_key(str(path), self.application.symbol_table.keys())
        module = self.application.symbol_table.get(key)
        if module is None:
            return self._not_analysed(key, line)
        module_ref = ModuleRef(path=module.file_path, module_name=module.module_name)
        found = _find_innermost(module, line)
        if found is None:
            return LocateResult(
                node=None,
                callable=None,
                type=None,
                module=module_ref,
                source=module.source,
                span=Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
                diagnostics=[Diagnostic(code="module_scope", message=f"line {line} is at module scope in {module.file_path}.")],
            )
        c, owner = found
        found_body = _find_body_node(c, line)
        # The id is composed from ``c.id`` (the ``can://`` containment path), never ``c.signature``
        # (the dotted module path): the emitter mints ``:PyBodyNode.id`` as
        # ``f"{callable.id}@{local_body_key}"`` (``codeanalyzer/neo4j/project.py``'s ``_body_ref``),
        # so this composition reproduces the graph's id exactly and the two backends' ids join
        # (#320). ``BodyNode`` itself carries no ``id`` field to read instead
        # (codeanalyzer-python#176), which is why this one is still composed.
        node, node_id = (found_body[1], f"{c.id}@{found_body[0]}") if found_body else (None, None)
        return LocateResult(
            node=node,
            node_id=node_id,
            callable=CallableRef(signature=c.signature, name=c.name, class_signature=owner.signature if owner else None),
            type=TypeRef(signature=owner.signature, name=owner.name) if owner else None,
            module=module_ref,
            source=_code_of(c, module.source) or "",
            span=c.span or Span(start=(line, 0), end=(line, 0), bytes=(0, 0)),
            diagnostics=[],
        )

    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable (see
        :meth:`PythonAnalysisBackend.locate`)."""
        return self._locate_one(path, line)

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions (see :meth:`PythonAnalysisBackend.locate_many`). Purely in
        memory here — there is no round trip to batch — but the results still come back in input
        order, matching the Neo4j backend's contract."""
        return [self._locate_one(path, line) for path, line in positions]

    # ----------------------------------------------------------- callers/callees
    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        """Return all methods that call a specific target method.

        Queries the call graph to find all predecessor nodes (callers)
        of the specified method.

        Args:
            target_class_name: The fully qualified class name containing
                the target method.
            target_method_declaration: The method name or signature to
                find callers for.

        Returns:
            A dictionary with structure::

                {
                    "target_method": "method.signature",
                    "caller_details": [
                        {
                            "caller_signature": "caller.sig",
                            "edge": {...edge attributes...}
                        },
                        ...
                    ]
                }

            Returns ``{"caller_details": []}`` if the method is not found
            or has no callers.
        """
        graph = self.get_call_graph()
        method = self.get_method(target_class_name, target_method_declaration)
        if method is None or method.signature not in graph:
            return {"caller_details": []}
        callers = [
            {"caller_signature": src, "edge": graph.get_edge_data(src, method.signature)}
            for src in graph.predecessors(method.signature)
        ]
        return {"target_method": method.signature, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        """Return all methods called by a specific source method.

        Queries the call graph to find all successor nodes (callees)
        of the specified method.

        Args:
            source_class_name: The fully qualified class name containing
                the source method.
            source_method_declaration: The method name or signature to
                find callees for.

        Returns:
            A dictionary with structure::

                {
                    "source_method": "method.signature",
                    "callee_details": [
                        {
                            "callee_signature": "callee.sig",
                            "edge": {...edge attributes...}
                        },
                        ...
                    ]
                }

            Returns ``{"callee_details": []}`` if the method is not found
            or has no callees.
        """
        graph = self.get_call_graph()
        method = self.get_method(source_class_name, source_method_declaration)
        if method is None or method.signature not in graph:
            return {"callee_details": []}
        callees = [
            {"callee_signature": tgt, "edge": graph.get_edge_data(method.signature, tgt)}
            for tgt in graph.successors(method.signature)
        ]
        return {"source_method": method.signature, "callee_details": callees}

    def get_class_call_graph(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[str, str]]:
        """Return call graph edges reachable from a class or method.

        Performs a depth-first traversal of the call graph starting from
        the specified class's methods (or a specific method).

        Args:
            qualified_class_name: The fully qualified class name to start from.
            method_signature: Optional specific method to start from. If
                ``None``, traversal starts from all methods in the class.

        Returns:
            A list of ``(caller, callee)`` tuples representing edges in
            the reachable subgraph. Returns empty list if class/method
            not found.
        """
        graph = self.get_call_graph()
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return []
        if method_signature is not None:
            method = self.get_method(qualified_class_name, method_signature)
            if method is None:
                return []
            return list(nx.edge_dfs(graph, source=method.signature))
        edges: List[Tuple[str, str]] = []
        for method in cls.callables.values():
            if method.signature in graph:
                edges.extend(nx.edge_dfs(graph, source=method.signature))
        return edges

    # ----------------------------------------------------------- comments
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[PyComment]:
        """Return comments contained within a specific method.

        Args:
            qualified_class_name: The fully qualified class name.
            method_signature: The method name or signature.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found within the method body. Returns empty list if method
            not found.
        """
        method = self.get_method(qualified_class_name, method_signature)
        return list(method.comments) if method else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[PyComment]:
        """Return comments contained within a specific class.

        Args:
            qualified_class_name: The fully qualified class name.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found within the class body. Returns empty list if class
            not found.
        """
        cls = self.get_class(qualified_class_name)
        return list(cls.comments) if cls else []

    def get_comment_in_file(self, file_path: str) -> List[PyComment]:
        """Return all comments in a specific file.

        Args:
            file_path: The path to the Python file.

        Returns:
            A list of :class:`~cldk.models.python.PyComment` objects
            found in the file. Returns empty list if file not found.
        """
        module = self.get_python_module(file_path)
        return list(module.comments) if module else []

    def get_all_comments(self) -> Dict[str, List[PyComment]]:
        """Return all comments in the project grouped by file.

        Returns:
            A dictionary mapping file paths to lists of
            :class:`~cldk.models.python.PyComment` objects.
        """
        return {fp: list(module.comments) for fp, module in self.application.symbol_table.items()}

    def get_all_docstrings(self) -> Dict[str, List[PyComment]]:
        """Return all docstrings in the project grouped by file.

        Filters comments to include only those marked as docstrings
        (comments at the beginning of modules, classes, or functions).

        Returns:
            A dictionary mapping file paths to lists of
            :class:`~cldk.models.python.PyComment` objects where
            ``is_docstring`` is ``True``.
        """
        return {
            fp: [c for c in module.comments if c.is_docstring]
            for fp, module in self.application.symbol_table.items()
        }
