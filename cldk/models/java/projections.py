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

"""CLDK-defined projection models for the Java facade.

Unlike the rest of :mod:`cldk.models.java`, these are **not** part of the ``codeanalyzer-java``
schema — they are lightweight, field-projected views CLDK exposes so callers can enumerate an
application set-at-a-time without paying for the full per-callable reconstruction. They mirror
:mod:`cldk.models.typescript.projections` and ``cldk.models.python``'s ``PyCallableOverview`` /
``PyClassOverview``, because the accessors returning them mirror Python's.

**Both carry the addressable name, not the ``can://`` id** (E6). For a callable that is the J-1 key
``"<type fqn>.<signature>"`` — what :meth:`~cldk.analysis.java.java_analysis.JavaAnalysis.resolve_callable`
returns and what :meth:`~cldk.analysis.java.java_analysis.JavaAnalysis.get_source` accepts — kept in
``key`` rather than in ``signature``, because a Java ``signature`` is a real and *different* thing
(``cancelOrder(java.lang.Integer, boolean)``), unique only within its declaring type.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from .models import JCallable, JType


class JCallableOverview(BaseModel):
    """A lightweight projection of one callable — enough to enumerate and filter without the full
    :class:`~cldk.models.java.models.JCallable` reconstruction (body nodes, call sites, local
    classes, per-callable graphs).

    Returned set-at-a-time by ``JavaAnalysis.get_callables_overview`` /
    ``get_decorated_callables`` / ``get_entrypoints``. Body-inspect only the few you need afterwards
    via ``JavaAnalysis.get_method`` / ``get_method_bodies``.

    Attributes:
        key: The J-1 name, ``"<type fqn>.<signature>"`` — the application-unique address every
            other accessor on this surface accepts. A local or anonymous class's segment carries
            the signature of the callable that declares it (the J-1 erratum).
        signature: The analyzer's own signature, parameter tail and all, exactly as it spells it
            (``cancelOrder(java.lang.Integer, boolean)``). **Not a normal form**: the tail is
            whatever the analyzer could resolve, so the same method reads
            ``setTopLosers(java.util.Collection)`` in one run and
            ``setTopLosers(Collection<QuoteDataBean>)`` in another where the type was unresolvable.
            Unique within ``owner``, not across the application — which is why ``key`` exists.
        name: The callable's short name (``cancelOrder``, ``<init>``, ``<clinit>$0``).
        owner: Qualified name of the declaring type. Never ``None``: every Java callable is
            declared by a type.
        owner_kind: The declaring type's kind — ``class``, ``interface``, ``enum``, ``annotation``
            or ``record``, passed through verbatim.
        kind: The callable's own kind — ``method``, ``constructor`` or ``initializer``. Never
            derived; always ``JCallable.kind`` as the analyzer reported it.
        path: Repo-relative path of the declaring compilation unit (the symbol-table key).
        start_line / end_line: The callable's line span, **-1 for an implicit callable**, which
            carries no span at all (99 of daytrader8's 1,216) — never 0, which would read as a
            real line.
        modifiers: The declared modifiers (``public``, ``static``, …), in the analyzer's order.
        decorators: The annotation names applied to the callable (``JDecorator.name`` only, so
            ``Override``, not ``@Override(...)``), **sorted**. Source order is not recoverable
            across backends -- the graph's containment walk orders a callable's children by
            ``(start_line, name)``, so two annotations on one line come back in a different order
            than ``analysis.json`` lists them -- and a projection whose order depends on which
            backend answered is not one answer. Read
            :attr:`~cldk.models.java.models.JCallable.annotations` off the full callable for source
            order and the argument spellings.
        is_entrypoint: The analyzer's own entrypoint mark for this callable.
        is_implicit: Whether the analyzer synthesised the callable (a default constructor), in
            which case it has no span, body, parameters, metrics or declaration text.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    signature: str
    name: str
    owner: str
    owner_kind: str
    kind: str
    path: str
    start_line: int
    end_line: int
    modifiers: List[str] = []
    decorators: List[str] = []
    is_entrypoint: bool = False
    is_implicit: bool = False

    @classmethod
    def of(cls, key: str, owner: JType, c: JCallable, *, path: str) -> JCallableOverview:
        """Project one callable, given the J-1 key and declaring type its backend's index already
        holds (neither is on :class:`~cldk.models.java.models.JCallable` itself)."""
        return cls(
            key=key,
            signature=c.signature,
            name=c.signature.partition("(")[0],
            owner=owner.qualified_name,
            owner_kind=owner.kind,
            kind=c.kind,
            path=path,
            start_line=c.start_line,
            end_line=c.end_line,
            modifiers=list(c.modifiers),
            decorators=sorted(d.name for d in c.decorators),
            is_entrypoint=c.is_entrypoint,
            is_implicit=c.is_implicit,
        )


class JClassOverview(BaseModel):
    """A lightweight projection of one type — the type-level counterpart to
    :class:`JCallableOverview`, for the types codeanalyzer-java marked as entrypoints in their own
    right (``JType.is_entrypoint_class``), independently of any individual method.

    Returned by ``JavaAnalysis.get_entrypoint_classes``. It mirrors
    :class:`~cldk.models.python.PyClassOverview` and
    :class:`~cldk.models.typescript.projections.TSClassOverview`, because the accessor that returns
    it mirrors ``PythonAnalysis.get_entrypoint_classes``.

    Attributes:
        qualified_name: The type's source-spelled qualified name — the key ``get_class`` and
            ``get_all_classes`` use, and what ``in_class=`` accepts.
        name: The type's simple name.
        kind: ``class``, ``interface``, ``enum``, ``annotation`` or ``record``. An entrypoint type
            is not necessarily a class, so the kind is carried rather than assumed.
        path: Repo-relative path of the declaring compilation unit.
        start_line / end_line: The type's line span.
        modifiers: The declared modifiers, in the analyzer's order.
        decorators: The annotation names applied to the type (``JDecorator.name`` only), sorted
            for the reason :class:`JCallableOverview`'s are.
        is_entrypoint_class: The analyzer's own mark. Always ``True`` on what
            ``get_entrypoint_classes`` returns; carried so the projection stays readable when it is
            passed on alone.
    """

    model_config = ConfigDict(extra="forbid")

    qualified_name: str
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    modifiers: List[str] = []
    decorators: List[str] = []
    is_entrypoint_class: bool = False

    @classmethod
    def of(cls, t: JType, *, path: str, qualified_name: Optional[str] = None) -> JClassOverview:
        """Project one type, given the module key its backend's index already holds."""
        return cls(
            qualified_name=qualified_name or t.qualified_name,
            name=t.name,
            kind=t.kind,
            path=path,
            start_line=t.start_line,
            end_line=t.end_line,
            modifiers=list(t.modifiers),
            decorators=sorted(d.name for d in t.decorators),
            is_entrypoint_class=t.is_entrypoint_class,
        )
