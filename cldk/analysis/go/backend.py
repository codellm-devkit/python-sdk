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

"""The Go analysis backend contract.

:class:`GoAnalysis` is a thin façade that delegates every query to a *backend*.
Today the only backend is :class:`~cldk.analysis.go.codeanalyzer.GoCodeanalyzer`
(in-process subprocess driver over ``analysis.json``); this ABC formalizes the
surface the façade depends on so an alternative backend (e.g. a Neo4j/Cypher
backend, mirroring :class:`~cldk.analysis.python.backend.PythonAnalysisBackend`)
can be dropped in and selected via the ``backend=`` configuration object without
touching the façade.

The contract is enforced by the type system and at instantiation time.
Backend-specific lifecycle (caches, subprocess management) is intentionally not
part of it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from cldk.models.go.models import GoApplication, GoCallable, GoFile, GoType


class GoAnalysisBackend(ABC):
    """Abstract base every Go analysis backend implements.

    A backend owns all indexing and query logic for a Go application; the
    :class:`~cldk.analysis.go.GoAnalysis` façade is a one-line-delegation shim
    over it. Implementations must return the canonical ``cldk.models.go`` Pydantic
    objects so backends are behaviorally interchangeable.
    """

    # ── application / whole-program ───────────────────────────────────────────

    @abstractmethod
    def get_application(self) -> GoApplication:
        """The complete application model (symbol table + call graph)."""

    @abstractmethod
    def get_symbol_table(self) -> Dict[str, GoFile]:
        """The per-file symbol table, keyed by file path relative to the project root."""

    @abstractmethod
    def get_all_files(self) -> Dict[str, GoFile]:
        """All analyzed files (alias for :meth:`get_symbol_table`)."""

    @abstractmethod
    def get_file(self, file_path: str) -> Optional[GoFile]:
        """The :class:`~cldk.models.go.GoFile` for a given relative file path, or ``None``."""

    # ── types ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_all_types(self) -> Dict[str, GoType]:
        """All named types (structs and interfaces) across all files, keyed by qualified name."""

    # ── callables ─────────────────────────────────────────────────────────────

    @abstractmethod
    def get_all_callables(self) -> Dict[str, GoCallable]:
        """All top-level functions and type methods across all files, keyed by signature."""
