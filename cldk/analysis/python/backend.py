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

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.analysis.commons.results import LocateResult
from cldk.models.python import (
    PyApplication,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
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


class PythonAnalysisBackend(AnalysisBackend[PyApplication, PyModule, PyClass, PyCallable, PyClassAttribute, str]):
    """Abstract base every Python analysis backend implements.

    A backend owns all indexing and query logic for a Python application; the
    :class:`PythonAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.python`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so backends are behaviorally interchangeable.

    The application/symbol-table/call-graph/class/method/field/parameter accessors are inherited
    from :class:`~cldk.analysis.commons.backend.AnalysisBackend`; everything below is Python-specific.
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
        """The constructors of a class."""

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
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        """Call sites of the given callable signatures, keyed by owning signature. Each existing
        signature gets an entry (an empty list if it has no call sites); signatures with no matching
        callable are omitted."""

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
