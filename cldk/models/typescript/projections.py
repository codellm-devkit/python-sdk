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

"""CLDK-defined projection models for the TypeScript facade.

Unlike the rest of :mod:`cldk.models.typescript`, these are **not** part of the
``codeanalyzer-typescript`` schema — they are lightweight, field-projected views CLDK exposes so
callers can enumerate the application set-at-a-time without paying for the full per-callable
reconstruction. They map cleanly to a single Cypher ``RETURN`` on the Neo4j backend and to one
symbol-table walk in-process.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from .models import TSCallable


class TSCallableOverview(BaseModel):
    """A lightweight projection of one callable — enough to enumerate and filter without the full
    :class:`~cldk.models.typescript.TSCallable` reconstruction (call-sites, inner callables,
    locals).

    Returned set-at-a-time by ``TypescriptAnalysis.get_callables_overview`` /
    ``TypescriptAnalysis.get_decorated_callables``. Body-inspect only the few you need afterwards
    via ``TypescriptAnalysis.get_method``/``TypescriptAnalysis.get_method_bodies``.

    Attributes:
        signature: The callable's unique signature (the key the call graph references).
        name: The callable's short name.
        owner_signature: Signature of the class/interface/namespace that declares this callable,
            or ``None`` for a module-level function or arrow.
        owner_kind: The owner's node kind (e.g. ``"class"``, ``"interface"``, ``"namespace"``), or
            ``None`` when there is no owner. ``owner_kind`` is ``None`` iff ``owner_signature`` is
            ``None``.
        kind: The callable's native TS kind, passed through verbatim — one of ``function``,
            ``method``, ``constructor``, ``getter``, ``setter``, ``arrow``,
            ``function_expression``. Never derived; always ``TSCallable.kind`` as reported by the
            analyzer.
        path: Project-relative path of the declaring module.
        start_line / end_line: The callable's line span.
        decorators: The decorator names applied to the callable (``TSDecorator.name`` only).
        is_exported: Whether the callable (or its enclosing declaration) is exported.
        is_async: Whether the callable is declared ``async``.
        is_static: Whether the callable is a static class member.
        accessibility: The callable's declared accessibility (``public``/``protected``/``private``),
            or ``None`` when unspecified.
    """

    signature: str
    name: str
    owner_signature: Optional[str] = None
    owner_kind: Optional[str] = None
    kind: str
    path: str
    start_line: int
    end_line: int
    decorators: List[str] = []
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    accessibility: Optional[str] = None

    @classmethod
    def from_callable(
        cls,
        c: TSCallable,
        owner_signature: Optional[str],
        owner_kind: Optional[str],
    ) -> TSCallableOverview:
        """Project a :class:`~cldk.models.typescript.TSCallable` into a
        :class:`TSCallableOverview`.

        Args:
            c: The callable to project.
            owner_signature: Signature of the declaring class/interface/namespace, or ``None`` for
                a module-level function or arrow.
            owner_kind: The owner's node kind, or ``None`` when ``owner_signature`` is ``None``.

        Returns:
            The projected overview.
        """
        return cls(
            signature=c.signature,
            name=c.name,
            owner_signature=owner_signature,
            owner_kind=owner_kind,
            kind=c.kind,
            path=c.path,
            start_line=c.start_line,
            end_line=c.end_line,
            decorators=[d.name for d in c.decorators],
            is_exported=c.is_exported,
            is_async=c.is_async,
            is_static=c.is_static,
            accessibility=c.accessibility,
        )
