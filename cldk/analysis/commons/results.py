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

"""Structured, language-agnostic results for the agent-facing query facade.

:class:`Diagnostic` is the "absence is never null" primitive: when a lookup can't produce the
value an agent asked for, it returns a ``Diagnostic`` naming *why* (one of a fixed set of
``code``s) instead of ``None`` or an empty collection — so a caller can tell "no such callable"
apart from "the graph doesn't speak this vocabulary" apart from "ambiguous, pick one".

:class:`LocateResult` (and the ``CallableRef`` / ``TypeRef`` / ``ModuleRef`` handles it carries)
answers the single most-needed query: a scanner alert arrives as ``file:line`` and the caller
needs the enclosing callable *and its source* in one round trip (see
:meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.locate`). ``node``/``span`` are typed
against ``codeanalyzer-python``'s models for this leg, so ``locate`` is declared on the *Python*
backend ABC rather than the generic cross-language one — a shared declaration typed on one
language's models is a contract no other language can satisfy. A later leg generalises ``node`` /
``span`` and hoists the declaration once Java or TypeScript needs the same shape.
"""

from typing import Literal

from pydantic import BaseModel

from cldk.models.python import BodyNode, Span


class Diagnostic(BaseModel):
    """A structured, non-``None`` explanation for why a query came back empty or uncertain.

    Attributes:
        code: The fixed vocabulary of reasons a query can fail to produce a definite answer.
        message: A human-readable explanation, safe to surface directly to an agent or user.
        suggestions: Optional near-miss candidates (e.g. for ``did_you_mean``); empty when none
            apply.
    """

    code: Literal[
        "no_match",
        "ambiguous",
        "unknown_callable",
        "unknown_param",
        "did_you_mean",
        "level_too_low",
        "module_scope",
        "file_not_in_graph",
        "module_source_unavailable",
        "unresolved_dispatch",
        "graph_schema_mismatch",
    ]
    message: str
    suggestions: list[str] = []


class ModuleRef(BaseModel):
    """A lightweight handle on a module — enough to name it and fetch it again.

    Always present on a :class:`LocateResult`, even when the position couldn't be resolved any
    further (a ``file_not_in_graph`` result still echoes the path the caller asked about).
    """

    path: str
    module_name: str | None = None


class TypeRef(BaseModel):
    """A lightweight handle on a class/type — the ``callable``'s owner, when it has one."""

    signature: str
    name: str


class CallableRef(BaseModel):
    """A lightweight handle on a callable — enough to call back into ``get_method`` or
    ``get_method_bodies`` without re-walking the symbol table."""

    signature: str
    name: str
    class_signature: str | None = None


class LocateResult(BaseModel):
    """Resolve a ``file:line`` position to its enclosing callable, source in hand.

    The outcomes below must stay distinguishable — an ambiguous empty is a defect (see the module
    docstring): a caller must be able to tell "inside a callable" apart from "a real position at
    module scope" apart from "the gap between two callables" (also module scope, and never snapped
    to the nearest callable) apart from "this file was never analysed".

    Attributes:
        node: The innermost body node containing the position, if the graph has one that precise.
            ``None`` does not mean "not found" — see ``callable``/``diagnostics`` for that.
        node_id: ``node``'s identifier for :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_source`
            (``"<callable signature>@<body key>"``), or ``None`` exactly when ``node`` is ``None``.
            A callable itself needs no such id — its own signature (``callable.signature``) already
            names it, the same key :meth:`get_method_bodies` uses.
        callable: The enclosing callable, or ``None`` if the position is not inside one (module
            scope) or the file isn't in the graph at all.
        type: The class owning ``callable``, or ``None`` for a module-level function/callable
            with no owning class.
        module: The module the position was asked about — always present, even when unresolved.
        source: The slice a caller will ask for next: the enclosing callable's text, or the
            module's text when there is no enclosing callable. Never ``None`` — "absence is never
            null" (see :class:`Diagnostic`); an unrecoverable body is an empty string, not a
            missing field. Backends differ on one case and say so rather than faking it: the
            Neo4j backend cannot produce *module* text (``:PyModule`` carries no ``source``
            property, only ``file_key`` / ``module_name`` / ``content_hash`` / ``last_modified`` /
            ``file_size``), so a module-scope result there is ``""`` plus a
            ``module_source_unavailable`` diagnostic. Callable text is available on both.
        span: The span the ``source`` slice covers. Which of its fields are meaningful depends on
            the backend, because they carry different data: the local backend returns the
            analyzer's real :class:`~cldk.models.python.Span` (1-based line, 0-based column, and
            UTF-8 byte offsets into the module source), while the Neo4j graph projects only
            ``start_line`` / ``end_line`` on ``:PyCallable`` and ``:PyBodyNode`` — so over Neo4j
            the line components are real and the columns and ``bytes`` are ``0`` placeholders,
            never offsets to slice with.
        diagnostics: Empty when the position resolved inside a callable; ``module_scope`` for a
            real position with no enclosing callable (joined by ``module_source_unavailable`` when
            the backend cannot supply the module text); ``file_not_in_graph`` when the path names
            no analysed module.
    """

    node: BodyNode | None
    node_id: str | None = None
    callable: CallableRef | None
    type: TypeRef | None
    module: ModuleRef
    source: str
    span: Span
    diagnostics: list[Diagnostic] = []
