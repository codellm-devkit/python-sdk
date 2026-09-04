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
from cldk.analysis.commons.results import EntrypointCoverage, LocateResult
from cldk.models.python import (
    PyApplication,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
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

    # -----[ source access ]-----
    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """Source text for one node, named by ``node_id``.

        Generalises body access below callable granularity: ``node_id`` is either a callable's
        signature (the same key :meth:`get_method_bodies` uses) or ``"<signature>@<body key>"``
        for one of that callable's body nodes — exactly the string :attr:`LocateResult.node_id`
        hands back alongside :attr:`LocateResult.node`, so a caller can re-fetch the precise
        statement or call site :meth:`locate` found, not just its enclosing callable.

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
