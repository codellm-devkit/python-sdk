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

"""Rebuild pydantic TypeScript models from Neo4j node/edge property maps.

These are *pure* functions: they take the flat property dictionaries that
``codeanalyzer-typescript``'s Neo4j projection wrote (see
``codeanalyzer-ts/src/build/neo4j/project.ts``) and re-hydrate the same
``cldk.models.typescript`` pydantic objects the in-memory backend returns. The
backend (:class:`TSNeo4jBackend`) fetches the related child rows (call sites,
decorators, methods, ...) over Cypher and hands them in here for assembly.

Lossy fields (the projection flattens or drops them, so a perfect round-trip is
impossible) are reconstructed best-effort and called out inline:

* ``comments`` collapse to a single synthetic docstring ``TSComment`` (only the
  joined docstring text survives the projection).
* ``type_parameters`` keep only their ``name`` (constraints/defaults dropped).
* module-level ``imports`` / ``exports`` are aggregated per module-pair into the
  ``IMPORTS`` / ``RE_EXPORTS`` edges, so individual bindings are synthesized.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from cldk.models.typescript import (
    TSCallable,
    TSCallableParameter,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSComment,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExternalSymbol,
    TSInterface,
    TSModule,
    TSNamespace,
    TSSymbol,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSTypeParameter,
    TSVariableDeclaration,
)

Props = Mapping[str, Any]


# ----------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------
def _comments(props: Props) -> List[TSComment]:
    """Re-hydrate the (lossy) docstring the projection stored as a flat ``docstring`` string."""
    doc = props.get("docstring")
    return [TSComment(content=doc, is_docstring=True)] if doc else []


def _type_params(props: Props) -> List[TSTypeParameter]:
    """The projection keeps only the parameter *names* (``type_parameter_names``)."""
    return [TSTypeParameter(name=n) for n in props.get("type_parameter_names", []) or []]


def _json_list(props: Props, key: str) -> List[dict]:
    """Decode a ``*_json`` property (``parameters_json`` / ``accessed_symbols_json``)."""
    raw = props.get(key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _entrypoint(props: Props) -> Dict[str, Any]:
    """Map the flattened entrypoint props back onto ``TSCallable``'s two entrypoint fields."""
    if "framework" not in props:
        return {}
    return {"is_entrypoint": True, "entrypoint_framework": props.get("framework")}


# ----------------------------------------------------------------------------------------------
# leaf nodes
# ----------------------------------------------------------------------------------------------
def callsite(props: Props) -> TSCallsite:
    return TSCallsite(
        method_name=props.get("method_name", ""),
        receiver_expr=props.get("receiver_expr"),
        receiver_type=props.get("receiver_type"),
        argument_types=list(props.get("argument_types", []) or []),
        type_arguments=list(props.get("type_arguments", []) or []),
        return_type=props.get("return_type"),
        callee_signature=props.get("callee_signature"),
        is_constructor_call=props.get("is_constructor_call", False),
        is_optional_chain=props.get("is_optional_chain", False),
        start_line=props.get("start_line", -1),
        start_column=props.get("start_column", -1),
        end_line=props.get("end_line", -1),
        end_column=props.get("end_column", -1),
    )


def decorator(node: Props, edge: Props | None = None) -> TSDecorator:
    """A decorator from its canonical ``:Decorator`` node + the ``DECORATED_BY`` edge props."""
    edge = edge or {}
    kwargs_raw = edge.get("keyword_arguments_json")
    keyword_arguments: Dict[str, str] = {}
    if kwargs_raw:
        try:
            keyword_arguments = json.loads(kwargs_raw)
        except (TypeError, ValueError):
            keyword_arguments = {}
    return TSDecorator(
        name=node.get("name", ""),
        qualified_name=node.get("qualified_name"),
        positional_arguments=list(edge.get("positional_arguments", []) or []),
        keyword_arguments=keyword_arguments,
        start_line=edge.get("start_line", -1),
        end_line=edge.get("end_line", -1),
    )


def attribute(props: Props, decorators: List[TSDecorator] | None = None) -> TSClassAttribute:
    return TSClassAttribute(
        name=props.get("name", ""),
        type=props.get("type"),
        comments=_comments(props),
        decorators=decorators or [],
        initializer=props.get("initializer"),
        accessibility=props.get("accessibility"),
        is_static=props.get("is_static", False),
        is_readonly=props.get("is_readonly", False),
        is_optional=props.get("is_optional", False),
        is_abstract=props.get("is_abstract", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def variable(props: Props) -> TSVariableDeclaration:
    return TSVariableDeclaration(
        name=props.get("name", ""),
        type=props.get("type"),
        initializer=props.get("initializer"),
        scope=props.get("scope", "module"),
        declaration_kind=props.get("declaration_kind", "unknown"),
        is_readonly=props.get("is_readonly", False),
        is_exported=props.get("is_exported", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def enum_member(name: str, value: str | None) -> TSEnumMember:
    # The projection stores "" for a memberless value (Neo4j arrays cannot hold null).
    return TSEnumMember(name=name, value=value if value else None)


def external(props: Props) -> TSExternalSymbol:
    return TSExternalSymbol(
        signature=props.get("signature", ""),
        name=props.get("name", ""),
        module=props.get("module", ""),
        kind=props.get("kind", "unknown"),
    )


def synthesized(props: Props) -> TSSynthesizedCallable:
    return TSSynthesizedCallable(
        name=props.get("name", "<anonymous>"),
        path=props.get("path", ""),
        start_line=props.get("start_line", -1),
        start_column=props.get("start_column", -1),
    )


# ----------------------------------------------------------------------------------------------
# declaration nodes (children supplied by the backend)
# ----------------------------------------------------------------------------------------------
def callable_(
    props: Props,
    *,
    decorators: List[TSDecorator] | None = None,
    call_sites: List[TSCallsite] | None = None,
    inner_callables: Dict[str, TSCallable] | None = None,
    inner_classes: Dict[str, TSClass] | None = None,
) -> TSCallable:
    def _params() -> List[TSCallableParameter]:
        out: List[TSCallableParameter] = []
        for p in _json_list(props, "parameters_json"):
            try:
                out.append(TSCallableParameter.model_validate(p))
            except Exception:  # noqa: BLE001 - tolerate analyzer/SDK schema drift
                out.append(TSCallableParameter(name=p.get("name", "")))
        return out

    def _accessed() -> List[TSSymbol]:
        out: List[TSSymbol] = []
        for s in _json_list(props, "accessed_symbols_json"):
            try:
                out.append(TSSymbol.model_validate(s))
            except Exception:  # noqa: BLE001
                pass
        return out

    return TSCallable(
        name=props.get("name", ""),
        path=props.get("path", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        decorators=decorators or [],
        parameters=_params(),
        type_parameters=_type_params(props),
        return_type=props.get("return_type"),
        code=props.get("code"),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
        code_start_line=props.get("code_start_line", -1),
        accessed_symbols=_accessed(),
        call_sites=call_sites or [],
        inner_callables=inner_callables or {},
        inner_classes=inner_classes or {},
        cyclomatic_complexity=props.get("cyclomatic_complexity", 0),
        kind=props.get("kind", "function"),
        accessibility=props.get("accessibility"),
        accessor_kind=props.get("accessor_kind"),
        is_static=props.get("is_static", False),
        is_abstract=props.get("is_abstract", False),
        is_async=props.get("is_async", False),
        is_generator=props.get("is_generator", False),
        is_optional=props.get("is_optional", False),
        is_readonly=props.get("is_readonly", False),
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        is_implicit=props.get("is_implicit", False),
        **_entrypoint(props),
    )


def class_(
    props: Props,
    *,
    decorators: List[TSDecorator] | None = None,
    methods: Dict[str, TSCallable] | None = None,
    attributes: Dict[str, TSClassAttribute] | None = None,
    inner_classes: Dict[str, TSClass] | None = None,
) -> TSClass:
    return TSClass(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        code=props.get("code"),
        decorators=decorators or [],
        base_classes=list(props.get("base_classes", []) or []),
        implements_types=list(props.get("implements_types", []) or []),
        type_parameters=_type_params(props),
        methods=methods or {},
        attributes=attributes or {},
        inner_classes=inner_classes or {},
        is_abstract=props.get("is_abstract", False),
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def interface(
    props: Props,
    *,
    methods: Dict[str, TSCallable] | None = None,
    properties: Dict[str, TSClassAttribute] | None = None,
) -> TSInterface:
    return TSInterface(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        code=props.get("code"),
        base_classes=list(props.get("base_classes", []) or []),
        type_parameters=_type_params(props),
        methods=methods or {},
        properties=properties or {},
        call_signatures=list(props.get("call_signatures", []) or []),
        index_signatures=list(props.get("index_signatures", []) or []),
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def enum(props: Props) -> TSEnum:
    names = props.get("member_names", []) or []
    values = props.get("member_values", []) or []
    members = [enum_member(n, values[i] if i < len(values) else None) for i, n in enumerate(names)]
    return TSEnum(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        code=props.get("code"),
        members=members,
        is_const=props.get("is_const", False),
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def type_alias(props: Props) -> TSTypeAlias:
    return TSTypeAlias(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        code=props.get("code"),
        aliased_type=props.get("aliased_type", ""),
        type_parameters=_type_params(props),
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def namespace(
    props: Props,
    *,
    classes: Dict[str, TSClass] | None = None,
    interfaces: Dict[str, TSInterface] | None = None,
    enums: Dict[str, TSEnum] | None = None,
    type_aliases: Dict[str, TSTypeAlias] | None = None,
    functions: Dict[str, TSCallable] | None = None,
    namespaces: Dict[str, TSNamespace] | None = None,
    variables: List[TSVariableDeclaration] | None = None,
) -> TSNamespace:
    return TSNamespace(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=_comments(props),
        classes=classes or {},
        interfaces=interfaces or {},
        enums=enums or {},
        type_aliases=type_aliases or {},
        functions=functions or {},
        variables=variables or [],
        namespaces=namespaces or {},
        is_exported=props.get("is_exported", False),
        is_ambient=props.get("is_ambient", False),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def module(props: Props, **children: Any) -> TSModule:
    return TSModule(
        file_path=props.get("file_key", props.get("file_path", "")),
        module_name=props.get("module_name", ""),
        is_tsx=props.get("is_tsx", False),
        is_declaration_file=props.get("is_declaration_file", False),
        content_hash=props.get("content_hash"),
        last_modified=props.get("last_modified"),
        file_size=props.get("file_size"),
        **children,
    )
