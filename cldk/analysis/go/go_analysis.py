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
from typing import Dict, List

import networkx as nx

from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, GoBackend, Neo4jConnectionConfig
from cldk.analysis.go.backend import GoAnalysisBackend
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.analysis.go.neo4j import GoNeo4jBackend
from cldk.models.go import GoApplication, GoCallable, GoCallGraphEdge, GoField, GoModule, GoParam, GoType


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
        self.backend: GoAnalysisBackend
        if isinstance(self.backend_config, Neo4jConnectionConfig):
            cfg = self.backend_config
            application_name = cfg.application_name or (Path(project_dir).name if project_dir else None)
            self.backend = GoNeo4jBackend(
                neo4j_uri=cfg.uri,
                neo4j_username=cfg.username,
                neo4j_password=cfg.password,
                neo4j_database=cfg.database,
                application_name=application_name,
            )
        else:
            self.backend = GoCodeanalyzer(
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
