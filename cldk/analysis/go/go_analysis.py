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

"""Go analysis facade.

Thin, read-only query layer over the canonical ``GoApplication`` a Go backend produces. Mirrors
the lifecycle vocabulary of the sibling facades (there is no shared base class — the facades match
by convention) and delegates all indexing and query work to its backend.

**Go is the SDK's first class-less, type-centric facade.** The public surface speaks ``get_types``
/ ``get_functions`` / ``get_methods_of`` — never ``class``. These forward to the backend's
inherited class-named slots (``get_all_classes`` / ``get_all_methods_in_class``), which exist only
to satisfy the shared ABC; the lie never reaches a caller. Package-level **functions** are kept
distinct from receiver **methods** (no module-as-class hack). Types, their methods and their fields
are addressed by the type's durable ``can://`` id (a Go type has no unique short name across
packages); callers/callees by the callable's ``can://`` id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.identity import resolve_identity
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, GoBackend, Neo4jConnectionConfig
from cldk.analysis.commons.bounds import DEFAULT_DEPTH, DEFAULT_MAX_NODES, DEFAULT_MAX_PATHS, DEFAULT_PAGE_SIZE
from cldk.analysis.commons.results import EdgePage, FlowPaths, Slice, SliceNode, TaintResult
from cldk.analysis.go.backend import GoAnalysisBackend
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.analysis.go.neo4j import GoNeo4jBackend
from cldk.models.go import (
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoCdgEdge,
    GoCfgEdge,
    GoDdgEdge,
    GoDegraded,
    GoExternalSymbol,
    GoField,
    GoModule,
    GoParam,
    GoType,
)


class GoAnalysis:
    """Analysis facade for Go projects.

    Delegates every query to a backend. Two interchangeable backends exist, both exposing the same
    method surface:

    * :class:`~cldk.analysis.go.codeanalyzer.codeanalyzer.GoCodeanalyzer` (default) — runs ``cango``
      and walks the in-memory ``GoApplication`` parsed from ``analysis.json``;
    * :class:`~cldk.analysis.go.neo4j.GoNeo4jBackend` — answers the *same* queries with lazy Cypher
      over the ``GO_``-prefixed graph a ``cango --emit neo4j`` run populated. Selected by passing a
      :class:`~cldk.analysis.commons.backend_config.Neo4jConnectionConfig`.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        backend: GoBackend | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.target_files = target_files
        self.eager_analysis = eager_analysis
        # The backend is selected by the *type* of the config: Neo4jConnectionConfig picks the
        # read-only Cypher backend, CodeAnalyzerConfig (the default) the in-process cango.
        self.backend_config: GoBackend = backend if backend is not None else CodeAnalyzerConfig()
        self.identity = resolve_identity(self.backend_config.application_name, self.backend_config.namespace, project_root=Path(project_dir) if project_dir is not None else None)
        self.backend: GoAnalysisBackend
        if isinstance(self.backend_config, Neo4jConnectionConfig):
            cfg = self.backend_config
            application_name = self.identity.application_name
            self.backend = GoNeo4jBackend(
                neo4j_uri=cfg.uri,
                neo4j_username=cfg.username,
                neo4j_password=cfg.password,
                neo4j_database=cfg.database,
                application_name=application_name,
                namespace=self.identity.namespace,
                source_root=project_dir,
            )
        else:
            self.backend = GoCodeanalyzer(
                application_name=self.identity.application_name,
                namespace=self.identity.namespace,
                project_dir=project_dir,
                cache_dir=self.backend_config.cache_dir,
                analysis_level=analysis_level,
                eager_analysis=eager_analysis,
                target_files=target_files,
            )
        self.application: GoApplication = self.backend.get_application_view()

    # -----[ Tier A: lifecycle / whole-program ]-----
    def get_application_view(self) -> GoApplication:
        """The whole application view (symbol table + call graph)."""
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, GoModule]:
        """The per-file symbol table, keyed by module file path."""
        return self.backend.get_symbol_table()

    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of the application's call edges, nodes keyed by ``can://`` callable id,
        edges carrying ``weight`` and ``provenance``."""
        return self.backend.get_call_graph()

    def get_call_graph_json(self) -> str:
        """The application's call graph as a JSON string."""
        return self.backend.get_call_graph_json()

    def get_callers(self, callable_id: str) -> List[GoCallGraphEdge]:
        """The call-graph edges whose ``dst`` is ``callable_id`` — the callables that call it. Full
        edges (``src``/``dst``/``prov``/``weight``); caller ids are ``[e.src for e in result]``."""
        return self.backend.get_callers(callable_id)

    def get_callees(self, callable_id: str) -> List[GoCallGraphEdge]:
        """The call-graph edges whose ``src`` is ``callable_id`` — the callables it calls."""
        return self.backend.get_callees(callable_id)

    # -----[ type-centric navigation (SDK1: structs + interfaces, never "class") ]-----
    def get_types(self) -> Dict[str, GoType]:
        """Every Go type (struct + interface), keyed by its durable ``can://`` id."""
        return self.backend.get_all_classes()

    def get_type(self, type_id: str) -> GoType | None:
        """A single type by its ``can://`` id."""
        return self.backend.get_class(type_id)

    def get_functions(self) -> Dict[str, GoCallable]:
        """Every package-level function, keyed by signature (kept distinct from receiver methods)."""
        return self.backend.get_functions()

    def get_methods_of(self, type_id: str) -> Dict[str, GoCallable]:
        """The receiver methods of the type ``type_id``, keyed by signature."""
        return self.backend.get_all_methods_in_class(type_id)

    def get_method(self, type_id: str, method_signature: str) -> GoCallable | None:
        """A single receiver method of ``type_id`` by its signature."""
        return self.backend.get_method(type_id, method_signature)

    def get_fields(self, type_id: str) -> List[GoField]:
        """The fields of the type ``type_id``."""
        return self.backend.get_all_fields(type_id)

    def get_method_parameters(self, type_id: str, method_signature: str) -> List[GoParam]:
        """The parameters of a receiver method of ``type_id``."""
        return self.backend.get_method_parameters(type_id, method_signature)

    # -----[ source ]-----
    def get_source(self, node_id: str) -> str:
        """The source text of ``node_id`` (a type, callable or field) — its span sliced from the
        owning module's source."""
        return self.backend.get_source(node_id)

    # -----[ Tier B: dataflow (L3/L4) — the accessors share PythonAnalysis's names and
    # signatures, so a caller's dataflow code ports between languages (python-sdk#436) ]-----
    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """The one callable ``name`` names, as an addressable position.

        Accepts the receiver forms a Go programmer writes — ``Query``, ``DB.Query``,
        ``(*DB).Query``, ``sql.DB.Query``, ``database/sql.DB.Query`` — and the **library stand-ins**
        a level-4 analysis synthesizes, which is how ``database/sql.DB.Query`` is addressable as a
        sink at all. Raises listing candidates rather than guessing when a name is ambiguous.
        """
        return self.backend.resolve_callable(name, in_class=in_class, in_module=in_module)

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """The one value of ``within`` that ``name`` names — a parameter, a method's **receiver**, a
        closure **capture**, or a package-level **global** the callable reads.

        A global is addressable by the spelling a Go programmer writes (``libflow.Last``) as well as
        by its full import-path form; a bare leaf name that several packages export raises as the
        ambiguity it is.
        """
        return self.backend.resolve_value(name, within=within)

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCfgEdge]:
        """One page of control flow within one callable (needs ``analysis_level`` 3 or deeper)."""
        return self.backend.get_cfg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCdgEdge]:
        """One page of control dependence within one callable."""
        return self.backend.get_cdg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoDdgEdge]:
        """One page of data dependence within one callable, each edge carrying the variable it
        flows and its provenance tier (``reaching-defs`` / ``ssa`` / ``points-to``)."""
        return self.backend.get_ddg(callable, in_class=in_class, page_size=page_size, cursor=cursor)

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` depends on (needs ``analysis_level`` 4)."""
        return self.backend.slice_backward(src, within=within, depth=depth, max_nodes=max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything the value ``src`` can affect."""
        return self.backend.slice_forward(src, within=within, depth=depth, max_nodes=max_nodes)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every callable that can reach these sinks, over the call graph — the coarse pass triage
        runs before asking for a value-level path."""
        return self.backend.backward_cone(sinks, depth=depth, max_nodes=max_nodes)

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path from the callable ``src`` to the callable ``dst``?"""
        return self.backend.reaches(src, dst, depth=depth)

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How the value ``src`` reaches the value ``dst`` — the witnesses, shortest first.

        **Proves a flow; does not refute one.** An empty result cannot be told from "the flow left
        the resolved graph" — :meth:`taint` is the accessor that reports that distinction.
        """
        return self.backend.paths_between(src, dst, src_within=src_within, dst_within=dst_within, depth=depth, max_paths=max_paths)

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does the value ``src`` reach **any** value entering a call to ``callee``?"""
        return self.backend.flows_to_call(src, callee, within=within, depth=depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does the value ``src`` reach ``callee``'s parameter ``arg`` specifically?"""
        return self.backend.flows_to_argument(src, callee, arg, within=within, depth=depth)

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

        m sources against n sinks in one traversal, reporting per pair: the witnesses in ``paths``,
        the pairs an absence claim can be built on in ``exhausted``, and everything else in
        ``unresolved``. The source/sink/sanitizer vocabulary is the caller's — no Go framework
        catalogue ships. A non-empty :meth:`get_degraded` voids every ``exhausted`` certification,
        because absence of a path is not a proof over an approximated graph.
        """
        return self.backend.taint(sources, sinks, sanitizers, depth=depth, max_paths=max_paths)

    def get_external_symbols(self) -> Dict[str, GoExternalSymbol]:
        """The library stand-ins a level-4 analysis synthesized, keyed by ``can://…/@external/…``
        id — where a path's external hops are explained. Not a dependency list."""
        return self.backend.get_external_symbols()

    def get_degraded(self) -> List[GoDegraded]:
        """This analysis's degradation markers — empty when nothing was approximated.

        **Read this before trusting an absence.** Non-empty means some L4 step fell back to a
        coarser result, so a path that *is* returned is still a real may-flow, but "no path" is not
        a proof over the packages and callables it names.
        """
        return self.backend.get_degraded()

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in ``source`` for these positions — a second call, because a slice answers *where*
        and source answers *what*, and source is the one field with no size ceiling."""
        return self.backend.describe(nodes)
