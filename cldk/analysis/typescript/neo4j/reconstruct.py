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

"""Rebuild the ``cldk.models.typescript`` (schema v2) models from codeanalyzer-typescript
Neo4j node/edge property maps (1.4.0 additions -- ``parameters_json``, ``exports_json``,
``TS_IMPORTS`` -- decoded where present).

Pure functions: they take the flat property dictionaries the analyzer's Neo4j projection wrote
(``schema.neo4j.json`` at the pinned tag is the authority for what each label carries) and return
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
* ``TSCallable.comments``, ``type_parameters``, ``overload_signatures``, ``body``,
  ``cfg``/``cdg``/``ddg``/``summary`` -- ``:TSCallable`` projects none of them; the call view is
  answered from ``:TSBodyNode {kind:'call'}`` by the backend's call-site accessors, not stored on
  the callable. ``decorators`` **are** recoverable (``TS_DECORATED_BY``) and are, and so are
  ``parameters`` on a graph carrying ``parameters_json`` (see :func:`parameters`).
* ``TSField.value`` (enum member values), ``comments``, ``initializer``, ``scope``,
  ``declaration_kind`` and the boolean facets -- ``:TSField`` projects ``name``/``type``/lines.
* ``TSModule.comments`` -- no relationship type or property exists. ``exports`` come back in
  full from ``exports_json`` where the graph carries it (see :func:`exports`); ``imports`` stay
  empty on a rebuilt module -- they live on ``TS_IMPORTS`` edges the containment fetch does not
  walk, and the backend's :meth:`~cldk.analysis.typescript.neo4j.neo4j_backend.TSNeo4jBackend.get_imports`
  is what reads them.
* ``TSDecorator`` line/column -- the edge carries only the arguments.
* A call site's ``method_name``/receiver/argument facets and columns -- a ``call`` body node
  projects ``callee`` and its lines only.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from cldk.models.python import PyConfigRead
from cldk.models.typescript import (
    TSCallable,
    TSCallableParameter,
    TSCallableOverview,
    TSClassOverview,
    TSCallsite,
    TSClass,
    TSDecorator,
    TSEnum,
    TSExport,
    TSImport,
    TSExternalNode,
    TSField,
    TSInterface,
    TSModule,
    TSNamespace,
    TSSpan,
    TSSynthesizedNode,
    TSTypeAlias,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

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
def _json_list(raw: Any, prop: str) -> List[Any]:
    """A JSON-string property decoded to a list.

    A malformed value is **raised**, never swallowed into ``[]``: the analyzer writes these
    properties as ``null`` when the list is empty, so ``[]`` is a real answer ("no parameters",
    "no exports") and must not double as "the text on this node was garbage" -- the ambiguity
    the 1.4.0 uptake (#368) exists to close. The caller decides what ``null`` means; this only
    ever sees a value.
    """
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise CodeanalyzerExecutionException(f"The graph's {prop} is not valid JSON: {raw!r}") from e
    if not isinstance(decoded, list):
        raise CodeanalyzerExecutionException(f"The graph's {prop} decoded to a {type(decoded).__name__}, not a list: {raw!r}")
    return decoded


def parameters(props: Props) -> List[TSCallableParameter]:
    """A callable's parameters from ``:TSCallable.parameters_json`` (codeanalyzer-typescript 1.4.0,
    cants#182 D4) -- ``JSON.stringify(callable.parameters)`` verbatim, so this round-trips the
    in-memory list exactly, spans, decorators, ``@formal_in:N`` ids and all.

    Absent (``null``) on a callable that takes none, and absent on **every** callable of a graph
    emitted before 1.4.0. The two read the same here, deliberately: telling them apart is a
    question about the application, not about one node, and
    :attr:`~cldk.analysis.typescript.neo4j.neo4j_backend.TSNeo4jBackend._carries_bindings` is
    where it is asked.
    """
    raw = props.get("parameters_json")
    return [TSCallableParameter.model_validate(p) for p in _json_list(raw, "TSCallable.parameters_json")] if raw else []


def exports(raw: Any) -> List[TSExport]:
    """A module's export bindings from ``:TSModule.exports_json`` (cants#182 D3) --
    ``JSON.stringify(module.exports)`` verbatim, and so lossless: the same list the in-memory
    backend answers, spans included. ``null`` when the module exports nothing."""
    return [TSExport.model_validate(e) for e in _json_list(raw, "TSModule.exports_json")] if raw else []


def import_edge(props: Props) -> List[TSImport]:
    """The bindings one aggregated ``TS_IMPORTS`` edge stands for (cants#182 D2).

    The emitter folds **every** binding between an importer and one target module into one edge,
    each facet a sorted *set* -- ``spellings``, ``imported_names``, ``aliases``,
    ``type_only_names`` -- so what a name was written on, and what it was renamed to, are not
    recoverable from the edge. What is recoverable is rebuilt; the rest is left at the model
    default rather than guessed: ``alias`` is always ``None``, ``import_kind`` always the model's
    ``"named"``, and the span always ``-1``. A name is emitted against every spelling on the edge,
    which is exact for the overwhelmingly common one-spelling edge and an over-count when one
    module reaches another by two specifiers. An edge with no names at all is a side-effect import
    (``import "./styles.css"``) and becomes one entry naming the spelling itself, as the Python
    twin's ``PyNeo4jBackend._module_imports`` does.
    """
    names = list(props.get("imported_names") or [])
    type_only = set(props.get("type_only_names") or [])
    return [TSImport(module=spelling, name=name, is_type_only=name in type_only) for spelling in (props.get("spellings") or []) for name in (names or [spelling])]


def unresolved_config_read(props: Props, *, callee: str) -> PyConfigRead:
    """A :class:`PyConfigRead` from a ``TS_READS_CONFIG_UNRESOLVED`` edge and its target's id
    (cants#182 D5), which is python's shape verbatim -- and so is its ceiling, stated in full on
    :func:`cldk.analysis.python.neo4j.reconstruct.unresolved_config_read`: the edge runs
    application-to-target and never touches the reading body node, so ``site`` is always ``""``,
    and its discriminant is ``(key, reason)``, so several sites reading the same
    ``(callee, key, reason)`` collapse onto one edge. A count gap, never a presence one."""
    return PyConfigRead(site="", callee=callee, key=props.get("key"), reason=props.get("reason", "non-literal"), prov=list(props.get("prov") or []))


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


def class_overview(row: Props) -> TSClassOverview:
    """A projected class row (``get_entrypoint_classes``'s ``RETURN`` plus a derived ``path``).

    Defensive in the same way :func:`overview` is: the projection reads properties the graph is
    free not to carry, and a missing line is the model's ``-1`` sentinel rather than a
    ``ValidationError`` on a class the caller asked about."""
    return TSClassOverview(
        signature=row.get("signature", ""),
        name=row.get("name", ""),
        path=row["path"],
        start_line=row.get("start_line", -1),
        end_line=row.get("end_line", -1),
        decorators=[d for d in (row.get("decorators") or []) if d is not None],
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
            parameters=parameters(props),
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
        exports=exports(props.get("exports_json")),
        comments=[],
        types=types or {},
        functions=functions or {},
        fields=fields or {},
        is_tsx=bool(props.get("is_tsx", False)),
        is_declaration_file=bool(props.get("is_declaration_file", False)),
        content_hash=props.get("content_hash"),
    )
