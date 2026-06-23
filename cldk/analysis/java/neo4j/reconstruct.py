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

"""Pure rehydration: Neo4j property maps → ``analysis.json``-shaped dicts for ``cldk.models.java``.

:class:`~cldk.analysis.java.neo4j.JNeo4jBackend` bulk-fetches every node + relationship for an
application, groups children by parent, and feeds the grouped props here. Each function returns a
plain ``dict`` matching the corresponding pydantic model's field names, so the backend can assemble a
single ``analysis.json``-shaped payload and hand it to ``JApplication(**payload)`` — the exact same
constructor path the in-memory :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer` uses
(``_init_japplication``). That guarantees the reconstructed objects are identical.

The source graph is the one ``codeanalyzer-java`` (>= 2.4.0) emits with ``--emit neo4j`` — see its
``neo4j/GraphProjector.java`` / ``schema.neo4j.json`` for the property flattening these functions
invert. Java comments are first-class ``:JComment`` nodes (``J_HAS_COMMENT``), so unlike the Python
backend they round-trip losslessly.

Parity caveats (inherent to what the projection stores, not bugs): a ``JType``'s
``is_class_or_interface_declaration`` and ``is_concrete_class`` flags are not projected (only the
``kind`` discriminator is), so they rehydrate to their defaults; the order of ``call_graph`` edges
is sorted rather than original-insertion order.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

Props = Mapping[str, Any]


# -----[ helpers ]-----
def _arr(props: Props, key: str) -> List[str]:
    return list(props.get(key, []) or [])




def _kind_flags(kind: str | None) -> Dict[str, bool]:
    """Derive the type-discriminator booleans from the projected ``kind`` string."""
    return {
        "is_interface": kind == "interface",
        "is_enum_declaration": kind == "enum",
        "is_annotation_declaration": kind == "annotation",
        "is_record_declaration": kind == "record",
    }


# -----[ leaf nodes ]-----
def comment(props: Props) -> dict:
    return {
        "content": props.get("content"),
        "start_line": props.get("start_line", -1),
        "end_line": props.get("end_line", -1),
        "start_column": props.get("start_column", -1),
        "end_column": props.get("end_column", -1),
        "is_javadoc": props.get("is_javadoc", False),
    }


def parameter(props: Props) -> dict:
    return {
        "name": props.get("name"),
        "type": props.get("type", ""),
        "annotations": _arr(props, "annotations"),
        "modifiers": _arr(props, "modifiers"),
        "start_line": props.get("start_line", -1),
        "end_line": props.get("end_line", -1),
        "start_column": props.get("start_column", -1),
        "end_column": props.get("end_column", -1),
    }


def field(props: Props, *, comment_node: dict | None = None) -> dict:
    raw = props.get("variable_initializers_json")
    return {
        "comment": comment_node,
        "type": props.get("type", ""),
        "start_line": props.get("start_line", -1),
        "end_line": props.get("end_line", -1),
        "variables": _arr(props, "variables"),
        "modifiers": _arr(props, "modifiers"),
        "annotations": _arr(props, "annotations"),
        "variable_initializers": json.loads(raw) if raw else {},
    }


def variable(props: Props, *, comment_node: dict | None = None) -> dict:
    return {
        "comment": comment_node,
        "name": props.get("name", ""),
        "type": props.get("type", ""),
        "initializer": props.get("initializer", ""),
        "start_line": props.get("start_line", -1),
        "start_column": props.get("start_column", -1),
        "end_line": props.get("end_line", -1),
        "end_column": props.get("end_column", -1),
    }


def enum_constant(props: Props) -> dict:
    return {"name": props.get("name", ""), "arguments": _arr(props, "arguments")}


def record_component(props: Props, *, comment_node: dict | None = None) -> dict:
    return {
        "comment": comment_node,
        "name": props.get("name", ""),
        "type": props.get("type", ""),
        "modifiers": _arr(props, "modifiers"),
        "annotations": _arr(props, "annotations"),
        "default_value": props.get("default_value"),
        "is_var_args": props.get("is_var_args", False),
    }


def crud_operation(props: Props) -> dict:
    return {"line_number": props.get("line_number", -1), "operation_type": props.get("operation_type")}


def crud_query(props: Props) -> dict:
    return {
        "line_number": props.get("line_number", -1),
        "query_arguments": props.get("query_arguments"),
        "query_type": props.get("query_type"),
    }


def callsite(props: Props, *, comment_node: dict | None = None, crud_op: dict | None = None, crud_q: dict | None = None) -> dict:
    return {
        "comment": comment_node,
        "method_name": props.get("method_name", ""),
        "receiver_expr": props.get("receiver_expr", ""),
        "receiver_type": props.get("receiver_type", ""),
        "argument_types": _arr(props, "argument_types"),
        "argument_expr": _arr(props, "argument_expr"),
        "return_type": props.get("return_type", ""),
        "callee_signature": props.get("callee_signature", ""),
        "is_static_call": props.get("is_static_call"),
        "is_private": props.get("is_private"),
        "is_public": props.get("is_public"),
        "is_protected": props.get("is_protected"),
        "is_unspecified": props.get("is_unspecified"),
        "is_constructor_call": props.get("is_constructor_call", False),
        "crud_operation": crud_op,
        "crud_query": crud_q,
        "start_line": props.get("start_line", -1),
        "start_column": props.get("start_column", -1),
        "end_line": props.get("end_line", -1),
        "end_column": props.get("end_column", -1),
    }


# -----[ declarations ]-----
def init_block(
    props: Props,
    *,
    comments: List[dict] | None = None,
    call_sites: List[dict] | None = None,
    variable_declarations: List[dict] | None = None,
) -> dict:
    return {
        "file_path": props.get("file_path", ""),
        "comments": comments or [],
        "annotations": _arr(props, "annotations"),
        "thrown_exceptions": _arr(props, "thrown_exceptions"),
        "code": props.get("code", ""),
        "start_line": props.get("start_line", -1),
        "end_line": props.get("end_line", -1),
        "is_static": props.get("is_static", False),
        "referenced_types": _arr(props, "referenced_types"),
        "accessed_fields": _arr(props, "accessed_fields"),
        "call_sites": call_sites or [],
        "variable_declarations": variable_declarations or [],
        "cyclomatic_complexity": props.get("cyclomatic_complexity", 0),
    }


def callable_(
    props: Props,
    *,
    comments: List[dict] | None = None,
    parameters: List[dict] | None = None,
    call_sites: List[dict] | None = None,
    variable_declarations: List[dict] | None = None,
    crud_operations: List[dict] | None = None,
    crud_queries: List[dict] | None = None,
) -> dict:
    return {
        "signature": props.get("signature", ""),
        "is_implicit": props.get("is_implicit", False),
        "is_constructor": props.get("is_constructor", False),
        "comments": comments or [],
        "annotations": _arr(props, "annotations"),
        "modifiers": _arr(props, "modifiers"),
        "thrown_exceptions": _arr(props, "thrown_exceptions"),
        "declaration": props.get("declaration", ""),
        "parameters": parameters or [],
        "return_type": props.get("return_type"),
        "code": props.get("code", ""),
        "start_line": props.get("start_line", -1),
        "end_line": props.get("end_line", -1),
        "code_start_line": props.get("code_start_line", -1),
        "referenced_types": _arr(props, "referenced_types"),
        "accessed_fields": _arr(props, "accessed_fields"),
        "call_sites": call_sites or [],
        "is_entrypoint": props.get("is_entrypoint", False),
        "variable_declarations": variable_declarations or [],
        "crud_operations": crud_operations or [],
        "crud_queries": crud_queries or [],
        "cyclomatic_complexity": props.get("cyclomatic_complexity", 0),
    }


def type_(
    props: Props,
    *,
    comments: List[dict] | None = None,
    callable_declarations: Dict[str, dict] | None = None,
    field_declarations: List[dict] | None = None,
    enum_constants: List[dict] | None = None,
    record_components: List[dict] | None = None,
    initialization_blocks: List[dict] | None = None,
) -> dict:
    out = {
        "is_inner_class": props.get("is_inner_class", False),
        "is_local_class": props.get("is_local_class", False),
        "is_nested_type": props.get("is_nested_type", False),
        "comments": comments or [],
        "extends_list": _arr(props, "extends_list"),
        "implements_list": _arr(props, "implements_list"),
        "modifiers": _arr(props, "modifiers"),
        "annotations": _arr(props, "annotations"),
        "parent_type": props.get("parent_type", ""),
        "nested_type_declarations": _arr(props, "nested_type_declarations"),
        "callable_declarations": callable_declarations or {},
        "field_declarations": field_declarations or [],
        "enum_constants": enum_constants or [],
        "record_components": record_components or [],
        "initialization_blocks": initialization_blocks or [],
        "is_entrypoint_class": props.get("is_entrypoint_class", False),
    }
    out.update(_kind_flags(props.get("kind")))
    return out


def compilation_unit(
    props: Props,
    *,
    comments: List[dict] | None = None,
    import_declarations: List[dict] | None = None,
    type_declarations: Dict[str, dict] | None = None,
) -> dict:
    return {
        "file_path": props.get("file_path", props.get("file_key", "")),
        "package_name": props.get("package_name", ""),
        "comments": comments or [],
        "import_declarations": import_declarations or [],
        "type_declarations": type_declarations or {},
        "is_modified": props.get("is_modified", False),
    }


def call_edge(source: dict, target: dict, props: Props) -> dict:
    """A ``JGraphEdges``-shaped raw dict; endpoints resolve via JApplication's lookup table."""
    weight = props.get("weight")
    return {
        "source": source,
        "target": target,
        "type": props.get("type", "CALL_DEP"),
        "weight": str(weight) if weight is not None else "1",
        "source_kind": props.get("source_kind"),
        "destination_kind": props.get("destination_kind"),
    }
