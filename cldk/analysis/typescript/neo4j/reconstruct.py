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

"""Rebuild the ``cldk.models.typescript`` (schema v2) models from codeanalyzer-typescript 1.2.0
Neo4j node/edge property maps.

Pure functions: they take the flat property dictionaries the analyzer's Neo4j projection wrote
(``schema.neo4j.json`` at the 1.2.0 tag is the authority for what each label carries) and return
the same pydantic objects the in-memory backend returns. :class:`TSNeo4jBackend` fetches the rows
and assembles the containment tree; the per-node shape lives here.

What the projection does **not** carry, and therefore comes back at the model's empty default
(verified against the schema and the live graph, not assumed):

* ``TSModule.source`` -- the graph stores each node's own ``code`` text and its line span, never
  the module text or byte offsets. A reconstructed node's ``span`` is therefore line-only
  (columns ``0``) with ``bytes = (0, len(code))``, and its private ``_source`` is set to its own
  ``code`` so the model's ``code`` property reads the text the graph projected for that node
  (``None`` when the graph carries none). A module is assembled with ``model_construct`` so the
  module-level source threading (which would overwrite that with ``""``) does not run.
* ``TSCallable.parameters``, ``comments``, ``type_parameters``, ``overload_signatures``, ``body``,
  ``cfg``/``cdg``/``ddg``/``summary`` -- ``:TSCallable`` projects none of them; the call view is
  answered from ``:TSBodyNode {kind:'call'}`` by the backend's call-site accessors, not stored on
  the callable. ``decorators`` **are** recoverable (``TS_DECORATED_BY``) and are.
* ``TSField.value`` (enum member values), ``comments``, ``initializer``, ``scope``,
  ``declaration_kind`` and the boolean facets -- ``:TSField`` projects ``name``/``type``/lines.
* ``TSModule.imports`` / ``exports`` / ``comments`` -- no relationship type or property exists.
* ``TSDecorator`` line/column -- the edge carries only the arguments.
* A call site's ``method_name``/receiver/argument facets and columns -- a ``call`` body node
  projects ``callee`` and its lines only.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from cldk.models.typescript import (
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSClass,
    TSDecorator,
    TSEnum,
    TSExternalNode,
    TSField,
    TSInterface,
    TSModule,
    TSNamespace,
    TSSpan,
    TSSynthesizedNode,
    TSTypeAlias,
)

Props = Mapping[str, Any]

#: The ``kind`` values a ``TS_DECLARES`` child can carry that make it a type rather than a callable,
#: the seven callable kinds, and the type label each type kind is projected under.
TYPE_KINDS = frozenset({"class", "interface", "enum", "type_alias", "namespace"})
CALLABLE_KINDS = frozenset({"function", "method", "constructor", "getter", "setter", "arrow", "function_expression"})
TYPE_LABEL_KINDS = {"TSClass": "class", "TSInterface": "interface", "TSEnum": "enum", "TSTypeAlias": "type_alias", "TSNamespace": "namespace"}


def _span(props: Props) -> TSSpan:
    """Line-only span; ``bytes`` sized to the node's own ``code`` (see the module docstring)."""
    return TSSpan(start=(props.get("start_line", -1), 0), end=(props.get("end_line", -1), 0), bytes=(0, len(props.get("code") or "")))


def _with_code(node: Any, props: Props) -> Any:
    node._source = props.get("code")
    return node


def child_key(parent_id: str, props: Props) -> str:
    """The analyzer's container key for a child: the id segment under its parent, plus ``#get`` /
    ``#set`` for an accessor (a getter/setter pair shares the id). A child id is minted under its
    parent's by construction, so a mismatch is an emitter defect and is raised as such."""
    node_id = props["id"]
    if not node_id.startswith(parent_id + "/"):
        raise ValueError(f"child id is not minted under its parent: {node_id!r} under {parent_id!r}")
    key = node_id[len(parent_id) + 1 :]
    accessor = props.get("accessor_kind")
    return key + ("#get" if accessor == "getter" else "#set" if accessor == "setter" else "")


# ----------------------------------------------------------------------------------------------
# leaves
# ----------------------------------------------------------------------------------------------
def decorator(node: Props, edge: Props | None = None) -> TSDecorator:
    """A decorator from its ``:TSDecorator`` node (keyed by name) plus the ``TS_DECORATED_BY`` edge
    properties (``positional_arguments``, ``keyword_arguments_json``)."""
    edge = edge or {}
    raw = edge.get("keyword_arguments_json")
    try:
        keyword_arguments = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        keyword_arguments = {}
    return TSDecorator(
        name=node.get("name", ""), qualified_name=node.get("qualified_name"), positional_arguments=list(edge.get("positional_arguments") or []), keyword_arguments=keyword_arguments
    )


def field(props: Props, decorators: List[TSDecorator] | None = None) -> TSField:
    return TSField(
        id=props["id"],
        name=props["name"],
        type=props.get("type"),
        decorators=decorators or [],
        span=(
            TSSpan(start=(props["start_line"], 0), end=(props["end_line"], 0), bytes=(0, 0)) if props.get("start_line") is not None and props.get("end_line") is not None else None
        ),
    )


def callsite(props: Props, callee: str | None) -> TSCallsite:
    """The 1.x per-call record off a ``call`` body node: lines and the resolved callee (the graph
    key the target maps to -- a signature, or ``"<module>.<name>"`` for an external), nothing
    else -- the projection keeps no receiver/argument facets and no columns."""
    return TSCallsite(method_name="", callee_signature=callee, start_line=props.get("start_line", -1), end_line=props.get("end_line", -1))


def external(props: Props) -> TSExternalNode:
    return TSExternalNode(id=props["id"], kind=props["kind"], module=props["module"], name=props["name"])


def synthesized(props: Props) -> TSSynthesizedNode:
    """A ``:TSAnonymousCallable`` node as a synthesized-callable entry. The graph holds the tree
    node, not the compatibility index's older key, so the backend keys it by its own ``id``."""
    span = (
        TSSpan(start=(props["start_line"], props.get("start_column") or 0), end=(props["end_line"], 0), bytes=(0, 0))
        if props.get("start_line") is not None and props.get("end_line") is not None
        else None
    )
    return TSSynthesizedNode(id=props["id"], kind="callable", name=props.get("name"), path=props.get("path"), span=span)


def overview(row: Props) -> TSCallableOverview:
    """A projected callable row (the backend's ``_OVERVIEW_PROJECTION`` plus a derived ``path``).
    ``owner_kind`` is the owner node's own ``kind`` (``class``/``interface``), ``None`` when the
    ``TS_HAS_METHOD`` owner leg did not match."""
    return TSCallableOverview(
        signature=row.get("signature", ""),
        name=row.get("name", ""),
        owner_signature=row.get("owner_signature"),
        owner_kind=row.get("owner_kind") if row.get("owner_signature") is not None else None,
        kind=row.get("kind", "function"),
        path=row["path"],
        start_line=row.get("start_line", -1),
        end_line=row.get("end_line", -1),
        decorators=[d for d in (row.get("decorators") or []) if d is not None],
        is_exported=bool(row.get("is_exported", False)),
        is_async=bool(row.get("is_async", False)),
        is_static=bool(row.get("is_static", False)),
        accessibility=row.get("accessibility"),
    )


# ----------------------------------------------------------------------------------------------
# declarations (children supplied by the backend)
# ----------------------------------------------------------------------------------------------
def _flags(props: Props, *names: str) -> Dict[str, bool]:
    return {n: bool(props.get(n, False)) for n in names}


def callable_(props: Props, *, decorators: List[TSDecorator] | None = None, callables: Dict[str, TSCallable] | None = None, types: Dict[str, Any] | None = None) -> TSCallable:
    return _with_code(
        TSCallable(
            id=props["id"],
            span=_span(props),
            kind=props["kind"],
            name=props["name"],
            signature=props["signature"],
            decorators=decorators or [],
            return_type=props.get("return_type"),
            cyclomatic_complexity=props.get("cyclomatic_complexity", 0),
            accessibility=props.get("accessibility"),
            accessor_kind=props.get("accessor_kind"),
            callables=callables or {},
            types=types or {},
            is_entrypoint=props.get("is_entrypoint"),
            **_flags(props, "is_static", "is_abstract", "is_async", "is_generator", "is_exported", "is_ambient", "is_implicit"),
        ),
        props,
    )


def _type_kwargs(props: Props) -> Dict[str, Any]:
    return {"id": props["id"], "span": _span(props), "name": props["name"], "signature": props["signature"], **_flags(props, "is_exported", "is_ambient")}


def class_(props: Props, *, callables: Dict[str, TSCallable] | None = None, fields: Dict[str, TSField] | None = None, decorators: List[TSDecorator] | None = None) -> TSClass:
    return _with_code(
        TSClass(
            **_type_kwargs(props),
            callables=callables or {},
            fields=fields or {},
            decorators=decorators or [],
            base_classes=list(props.get("base_classes") or []),
            implements_types=list(props.get("implements_types") or []),
            is_abstract=bool(props.get("is_abstract", False)),
            is_entrypoint=props.get("is_entrypoint"),
        ),
        props,
    )


def interface(props: Props, *, callables: Dict[str, TSCallable] | None = None, fields: Dict[str, TSField] | None = None) -> TSInterface:
    return _with_code(TSInterface(**_type_kwargs(props), callables=callables or {}, fields=fields or {}, base_classes=list(props.get("base_classes") or [])), props)


def enum(props: Props, *, fields: Dict[str, TSField] | None = None) -> TSEnum:
    return _with_code(TSEnum(**_type_kwargs(props), fields=fields or {}, is_const=bool(props.get("is_const", False))), props)


def type_alias(props: Props) -> TSTypeAlias:
    return _with_code(TSTypeAlias(**_type_kwargs(props), aliased_type=props.get("aliased_type", "")), props)


def namespace(props: Props, *, types: Dict[str, Any] | None = None, functions: Dict[str, TSCallable] | None = None, fields: Dict[str, TSField] | None = None) -> TSNamespace:
    return _with_code(TSNamespace(**_type_kwargs(props), types=types or {}, functions=functions or {}, fields=fields or {}), props)


def module(props: Props, *, types: Dict[str, Any] | None = None, functions: Dict[str, TSCallable] | None = None, fields: Dict[str, TSField] | None = None) -> TSModule:
    """Assembled with ``model_construct``: the children are already-validated models, and the
    module's after-validator would thread its (absent) ``source`` over every node, erasing the
    per-node ``code`` the graph did project (see the module docstring)."""
    return TSModule.model_construct(
        id=props["id"],
        kind="module",
        span=_span(props),
        source="",
        imports=[],
        exports=[],
        comments=[],
        types=types or {},
        functions=functions or {},
        fields=fields or {},
        is_tsx=bool(props.get("is_tsx", False)),
        is_declaration_file=bool(props.get("is_declaration_file", False)),
        content_hash=props.get("content_hash"),
    )
