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

"""Go analysis facade.

This module provides :class:`GoAnalysis`, the primary interface for performing
static analysis on Go projects. It mirrors the method surface of
:class:`~cldk.analysis.java.JavaAnalysis` and
:class:`~cldk.analysis.python.PythonAnalysis` to provide a consistent
cross-language experience.

The facade delegates to :class:`~cldk.analysis.go.codeanalyzer.GoCodeanalyzer`,
which shells out to the ``codeanalyzer-go`` native binary.

Key capabilities:
    - Symbol table access (files, types, functions/methods)
    - Call graph construction as a NetworkX directed graph
    - Caller/callee relationship queries
    - Type and method lookup

See Also:
    - :class:`~cldk.analysis.java.JavaAnalysis`: Java equivalent.
    - :class:`~cldk.analysis.python.PythonAnalysis`: Python equivalent.
    - :class:`~cldk.analysis.go.codeanalyzer.GoCodeanalyzer`: Backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import GoBackend, cache_subdir
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.models.go.models import (
    GoApplication,
    GoCallEdge,
    GoCallable,
    GoFile,
    GoType,
)


class GoAnalysis:
    """Analysis facade for Go projects.

    Provides a unified interface for inspecting the symbol table and call graph
    produced by the ``codeanalyzer-go`` backend.

    Args:
        project_dir: Path to the root of the Go project (must contain ``go.mod``).
        analysis_level: ``"symbol_table"`` (default) or ``"call_graph"``.
        eager_analysis: When ``True``, always re-run the binary.
        backend: Backend configuration. Defaults to :class:`~cldk.analysis.commons.backend_config.GoCodeAnalyzerConfig`.
    """

    def __init__(
        self,
        project_dir: Path | None,
        analysis_level: str = AnalysisLevel.symbol_table,
        eager_analysis: bool = False,
        backend: GoBackend | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        cache_dir = backend.cache_dir if backend is not None else None
        analysis_json_path = cache_subdir(cache_dir, project_dir, "go")
        self._codeanalyzer: GoCodeanalyzer = GoCodeanalyzer(
            project_dir=self.project_dir,
            analysis_json_path=analysis_json_path,
            analysis_level=self.analysis_level,
            eager_analysis=self.eager_analysis,
        )
        self._call_graph: Optional[nx.DiGraph] = None

    # ── Application / symbol table ─────────────────────────────────────────────

    def get_application_view(self) -> GoApplication:
        """Return the complete analyzed application model."""
        return self._codeanalyzer.get_application()

    def get_symbol_table(self) -> Dict[str, GoFile]:
        """Return the symbol table mapping relative file paths to :class:`GoFile` objects."""
        return self._codeanalyzer.get_symbol_table()

    def get_file(self, file_path: str) -> Optional[GoFile]:
        """Return the :class:`GoFile` for a given relative file path, or ``None``."""
        return self._codeanalyzer.get_file(file_path)

    # ── Types ──────────────────────────────────────────────────────────────────

    def get_all_types(self) -> Dict[str, GoType]:
        """Return all named types (structs and interfaces) keyed by qualified name."""
        return self._codeanalyzer.get_all_types()

    def get_types_in_file(self, file_path: str) -> Dict[str, GoType]:
        """Return all named types defined in a specific file.

        Args:
            file_path: Relative file path as it appears in the symbol table.

        Returns:
            A ``{type_name: GoType}`` dict, or an empty dict if the file is not found.
        """
        go_file = self._codeanalyzer.get_file(file_path)
        if go_file is None:
            return {}
        return go_file.classes

    def get_type(self, file_path: str, type_name: str) -> Optional[GoType]:
        """Return a specific type by file path and type name.

        Args:
            file_path: Relative file path as it appears in the symbol table.
            type_name: Unqualified type name (e.g. ``"Server"``).

        Returns:
            The :class:`GoType`, or ``None`` if not found.
        """
        types = self.get_types_in_file(file_path)
        return types.get(type_name)

    # ── Callables ──────────────────────────────────────────────────────────────

    def get_all_callables(self) -> Dict[str, GoCallable]:
        """Return all functions and methods across all files, keyed by signature."""
        return self._codeanalyzer.get_all_callables()

    def get_callables_in_file(self, file_path: str) -> Dict[str, GoCallable]:
        """Return all top-level functions and type methods defined in a specific file.

        Args:
            file_path: Relative file path.

        Returns:
            A ``{signature: GoCallable}`` dict.
        """
        go_file = self._codeanalyzer.get_file(file_path)
        if go_file is None:
            return {}
        callables: Dict[str, GoCallable] = dict(go_file.functions)
        for go_type in go_file.classes.values():
            callables.update(go_type.methods)
        return callables

    def get_callable(self, signature: str) -> Optional[GoCallable]:
        """Return a callable by its fully-qualified signature, or ``None``."""
        return self._codeanalyzer.get_all_callables().get(signature)

    # ── Call graph ─────────────────────────────────────────────────────────────

    def get_call_graph(self) -> nx.DiGraph:
        """Return the project call graph as a NetworkX directed graph.

        Nodes are callable signatures (strings).  Edges carry the attributes
        from :class:`GoCallEdge` (``type``, ``weight``, ``provenance``).

        Returns:
            A ``networkx.DiGraph`` of method call relationships.
        """
        if self._call_graph is None:
            self._call_graph = self._build_call_graph()
        return self._call_graph

    def _build_call_graph(self) -> nx.DiGraph:
        cg = nx.DiGraph()
        for edge in self._codeanalyzer.get_application().call_graph:
            cg.add_edge(
                edge.source,
                edge.target,
                type=edge.type,
                weight=edge.weight,
                provenance=edge.provenance,
            )
        return cg

    def get_callers(self, target_signature: str) -> List[str]:
        """Return signatures of all callables that call ``target_signature``.

        Args:
            target_signature: Fully-qualified signature of the callee.

        Returns:
            List of caller signatures.
        """
        cg = self.get_call_graph()
        if target_signature not in cg:
            return []
        return list(cg.predecessors(target_signature))

    def get_callees(self, source_signature: str) -> List[str]:
        """Return signatures of all callables called by ``source_signature``.

        Args:
            source_signature: Fully-qualified signature of the caller.

        Returns:
            List of callee signatures.
        """
        cg = self.get_call_graph()
        if source_signature not in cg:
            return []
        return list(cg.successors(source_signature))

    def get_call_graph_edges(self) -> List[GoCallEdge]:
        """Return the raw call graph edge list from the analyzed application."""
        return self._codeanalyzer.get_application().call_graph
