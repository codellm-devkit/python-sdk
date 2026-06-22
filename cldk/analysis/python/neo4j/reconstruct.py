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

"""Pure rehydration: Neo4j property maps → ``cldk.models.python`` pydantic objects.

These are stateless functions. :class:`~cldk.analysis.python.neo4j.PyNeo4jBackend` fetches a node's
own properties and its child rows over Cypher, then passes them here for assembly — producing the
same objects the in-memory :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer` returns, so the
two backends are interchangeable.

The source graph is the one ``codeanalyzer-python`` (>= 0.2.0) emits with ``--emit neo4j`` — see its
``codeanalyzer/neo4j/project.py`` for the authoritative property flattening these functions invert.

Parity caveats (inherent to what the projection stores, not bugs):

* Comments collapse to a single ``docstring`` string. We rebuild one ``PyComment(is_docstring=True)``;
  non-docstring comments, multiplicity and positions are not recoverable. Module-level comments are
  not projected at all, so ``PyModule.comments`` comes back empty.
* ``PyVariableDeclaration.value`` and ``start_column`` / ``end_column`` are not projected (only
  ``initializer`` and the line span are), so they rehydrate as ``None`` / ``-1``.
* ``PyModule.imports`` are reconstructed from the *aggregated* ``PY_IMPORTS`` edges (per-binding
  alias pairing and positions are lost).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

from cldk.models.python import (
    PyCallable,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyImport,
    PyModule,
)
from cldk.models.python import (
    PyCallableParameter,
    PyCallsite,
    PySymbol,
    PyVariableDeclaration,
)

Props = Mapping[str, Any]


# -----[ helpers ]-----
def comments(props: Props) -> List[PyComment]:
    """Rebuild the (lossy) comment list from the single ``docstring`` property."""
    doc = props.get("docstring")
    if not doc:
        return []
    return [PyComment(content=doc, is_docstring=True)]


def _json_list(props: Props, key: str) -> List[Any]:
    """Decode a ``*_json`` property (a JSON-encoded list stored as a string) to a list of dicts."""
    raw = props.get(key)
    if not raw:
        return []
    return json.loads(raw)


def parameters(props: Props) -> List[PyCallableParameter]:
    """Rebuild ``PyCallable.parameters`` from the ``parameters_json`` property."""
    return [PyCallableParameter(**d) for d in _json_list(props, "parameters_json")]


def accessed_symbols(props: Props) -> List[PySymbol]:
    """Rebuild ``PyCallable.accessed_symbols`` from the ``accessed_symbols_json`` property."""
    return [PySymbol(**d) for d in _json_list(props, "accessed_symbols_json")]


# -----[ leaf nodes ]-----
def attribute(props: Props) -> PyClassAttribute:
    """Rebuild a :class:`PyClassAttribute` from a ``:PyAttribute`` node's properties."""
    return PyClassAttribute(
        name=props.get("name", ""),
        type=props.get("type"),
        comments=comments(props),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def variable(props: Props) -> PyVariableDeclaration:
    """Rebuild a :class:`PyVariableDeclaration` from a ``:PyVariable`` node's properties."""
    return PyVariableDeclaration(
        name=props.get("name", ""),
        type=props.get("type"),
        initializer=props.get("initializer"),
        value=None,  # not projected to the graph
        scope=props.get("scope", "module"),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def callsite(props: Props) -> PyCallsite:
    """Rebuild a :class:`PyCallsite` from a ``:PyCallSite`` node's properties."""
    return PyCallsite(
        method_name=props.get("method_name", ""),
        receiver_expr=props.get("receiver_expr"),
        receiver_type=props.get("receiver_type"),
        argument_types=list(props.get("argument_types", []) or []),
        return_type=props.get("return_type"),
        callee_signature=props.get("callee_signature"),
        is_constructor_call=props.get("is_constructor_call", False),
        start_line=props.get("start_line", -1),
        start_column=props.get("start_column", -1),
        end_line=props.get("end_line", -1),
        end_column=props.get("end_column", -1),
    )


def import_(module: str, name: str, alias: str | None = None) -> PyImport:
    """Rebuild a (best-effort) :class:`PyImport` from an aggregated ``PY_IMPORTS`` edge binding."""
    return PyImport(module=module, name=name, alias=alias)


# -----[ declarations ]-----
def callable_(
    props: Props,
    *,
    call_sites: List[PyCallsite] | None = None,
    inner_callables: Dict[str, PyCallable] | None = None,
    inner_classes: Dict[str, PyClass] | None = None,
    local_variables: List[PyVariableDeclaration] | None = None,
) -> PyCallable:
    """Rebuild a :class:`PyCallable` from a ``:PyCallable`` node plus its fetched children."""
    return PyCallable(
        name=props.get("name", ""),
        path=props.get("path", ""),
        signature=props.get("signature", ""),
        comments=comments(props),
        decorators=list(props.get("decorators", []) or []),
        parameters=parameters(props),
        return_type=props.get("return_type"),
        code=props.get("code"),
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
        code_start_line=props.get("code_start_line", -1),
        accessed_symbols=accessed_symbols(props),
        call_sites=call_sites or [],
        inner_callables=inner_callables or {},
        inner_classes=inner_classes or {},
        local_variables=local_variables or [],
        cyclomatic_complexity=props.get("cyclomatic_complexity", 0),
    )


def class_(
    props: Props,
    *,
    methods: Dict[str, PyCallable] | None = None,
    attributes: Dict[str, PyClassAttribute] | None = None,
    inner_classes: Dict[str, PyClass] | None = None,
) -> PyClass:
    """Rebuild a :class:`PyClass` from a ``:PyClass`` node plus its fetched children."""
    return PyClass(
        name=props.get("name", ""),
        signature=props.get("signature", ""),
        comments=comments(props),
        code=props.get("code"),
        base_classes=list(props.get("base_classes", []) or []),
        methods=methods or {},
        attributes=attributes or {},
        inner_classes=inner_classes or {},
        start_line=props.get("start_line", -1),
        end_line=props.get("end_line", -1),
    )


def module(
    props: Props,
    *,
    file_key: str,
    classes: Dict[str, PyClass] | None = None,
    functions: Dict[str, PyCallable] | None = None,
    variables: List[PyVariableDeclaration] | None = None,
    imports: List[PyImport] | None = None,
) -> PyModule:
    """Rebuild a :class:`PyModule` from a ``:PyModule`` node plus its fetched children.

    ``file_key`` is the node's key property and equals the original ``PyModule.file_path`` (the
    symbol-table key). Module-level comments are not projected, so ``comments`` is always empty.
    """
    return PyModule(
        file_path=file_key,
        module_name=props.get("module_name", ""),
        imports=imports or [],
        comments=[],
        classes=classes or {},
        functions=functions or {},
        variables=variables or [],
        content_hash=props.get("content_hash"),
        last_modified=props.get("last_modified"),
        file_size=props.get("file_size"),
    )
