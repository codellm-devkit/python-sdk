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

"""Rebuild the ``cldk.models.java`` (schema v2) models from codeanalyzer-java Neo4j node and edge
property maps.

Pure functions: they take the flat property dictionaries the analyzer's Neo4j projection wrote
(``schema.neo4j.json`` at the 3.2.0 tag -- the attach floor -- is the authority for what each label
carries; schema v2 itself landed at 3.0.1) and return
the same pydantic objects the in-memory :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`
returns. :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` fetches the rows and assembles the
containment tree; the per-node shape lives here.

**Booleans.** The projection writes a boolean property only when it is ``True`` (verified:
``text_truncated`` exists on 16 of 5,007 ``:Artifact`` nodes, ``is_implicit`` on 99 of 1,216
daytrader8 callables, ``is_wildcard`` only on wildcard imports). An absent boolean therefore *is*
``False`` in the contract, and reading one with a ``False`` default is not a default hiding drift.
Every non-boolean property the contract declares on a label is read with ``props[...]``.

**Text is one blob per file, sliced by byte offsets** -- the canonical schema's model since v2, and
what codeanalyzer-java 3.2.0 finally projects. ``:JModule.source`` carries the whole file (141 of 141
modules on the 3.2.0 reference graph) and every other node's ``code`` is
``source[start_byte:end_byte]``, which is what :meth:`JCompilationUnit.slice` does. Offsets are
projected on ``:JField`` (659/659), ``:JVariable`` (863/863), ``:JType`` (152/152), ``:JCallable``
(1,127/1,229, plus ``body_start_byte`` on 1,097) and ``:JBodyNode`` (6,726/13,544); the annotation
*application*'s offsets ride ``J_ANNOTATED_BY`` (817 edges). The 102 callables without offsets are
**exactly** the synthesized implicit ``<init>()`` -- never written, so there is no text to want --
which is why no callable's text is reachable through a projected ``:JCallable.code`` and not through
a slice: this module reads that property no longer, and :func:`span` is the only text path.

What the projection does **not** carry, and therefore comes back at the model's own empty default
(measured against the live graph, not assumed):

* **every column** -- the projection writes ``start_line``/``end_line`` and no position within a
  line, so both columns are :data:`_UNKNOWN` (``-1``) on every node: a ``0`` would read as column
  one, which is a position and a wrong one. **One exception, and it is deliberate:** a
  :class:`JCallableParameter` comes back with the analyzer's own columns, because the projection
  serialises the whole parameter list into ``:JCallable.parameters_json`` and it round-trips exactly
  (see :func:`parameters`). Its byte offsets index the same module ``source`` as everything else, but
  a parameter is never threaded to its compilation unit, so ``JCallableParameter.code`` raises on
  either backend rather than returning a silent empty.
* **a module without ``source``** -- an unreadable or non-UTF-8 file. Nothing in the reference graph
  is one, and the degradation is per *file* rather than per node: every node in that module slices
  an empty string and reports ``""`` (see :func:`compilation_unit`).
* **comments** -- there are no ``:JComment`` nodes (0 in the reference graph). A type, callable,
  field, enum constant and record component carries a single ``docstring`` property holding its
  javadoc, rebuilt here as a one-element ``comments`` list; a non-javadoc comment on a declaration,
  and every file-level comment, is not projected at all.
* **``JCallable.body``** -- only the ``call`` nodes are rebuilt (what ``call_sites`` is a view
  over), which is roughly **30%** of what the graph holds (4,006 of daytrader8's 13,436
  ``:JBodyNode``); the ``entry``/``exit``/``statement``/``branch``/``loop``/``return`` nodes and the
  parameter lattice are not. A call site's ``arguments`` (body-key references) are not projected
  either. The 6,818 synthetic vertices of the port lattice carry no offsets and want none: none of
  them stands for source text.
* **``JBodyNode.callee``** (226 populated on the committed daytrader8 ``-a 4`` fixture, 0 here) -- the
  ``can://`` id of the resolved callee. The projection puts that edge on ``J_RESOLVES_TO``, which
  this module reads into ``callee_signature`` instead, and an id has no home on the public surface
  (E6) anyway. **It is not an "unresolved" signal here:** ``node.callee is None`` classifies every
  call on this backend as unresolved while ``callee_signature`` beside it is fully populated --
  test that instead.
* **``JCallSite.comment``** (47 on the same fixture, 0 here) -- the comment attached
  to a call site. It follows from the comment gap above: the graph has no comment node to attach.
* ``cfg`` / ``cdg`` / ``ddg`` / ``summary`` (``None``: 3b reads them per callable on demand),
  ``type_parameters``, ``JCompilationUnit.comments``, ``JApplication.param_in`` / ``param_out`` /
  ``external_symbols``, and an import's / enum constant's / record component's span.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cldk.models.java.models import (
    JArtifact,
    JBodyNode,
    JCallable,
    JCallableParameter,
    JComment,
    JCompilationUnit,
    JConfigKey,
    JDecorator,
    JDependency,
    JField,
    JImport,
    JLocalVariable,
    JEnumConstant,
    JRecordComponent,
    JSpan,
    JType,
)

Props = Mapping[str, Any]


# ----------------------------------------------------------------------------------------------
# leaves
# ----------------------------------------------------------------------------------------------
#: The model's own "not known". The graph stores no column anywhere, and no byte offset on the
#: nodes listed in the module docstring -- reported as this rather than as a ``0``, which would read
#: as "column one, offset zero", a position and a wrong one.
_UNKNOWN = -1


def _byte_offsets(props: Props) -> Tuple[int, int]:
    """``(start_byte, end_byte)`` -- the slice of the owning module's ``source`` this node's text is
    -- or ``(-1, -1)`` when the projection carries no offsets for it.

    **Both or neither.** A half-known pair is the one shape that reads as data rather than as a gap:
    ``(b0, -1)`` slices from ``b0`` to one byte before the end of the file, a wrong answer where
    ``(-1, -1)`` is an empty one.
    """
    b0, b1 = props.get("start_byte"), props.get("end_byte")
    return (_UNKNOWN, _UNKNOWN) if b0 is None or b1 is None else (b0, b1)


def span(props: Props) -> Optional[JSpan]:
    """The node's span, or ``None`` when the projection places it nowhere at all (an implicit
    callable, which was never written).

    Lines are the graph's own; **columns are never projected** and stay :data:`_UNKNOWN`; byte
    offsets come from ``start_byte``/``end_byte``, which codeanalyzer-java 3.2.0 writes on every
    node whose text is a slice of its module's ``source``. Offsets without lines still make a span
    -- that is the ``J_ANNOTATED_BY`` edge, which locates the annotation *application* the shared
    ``:JAnnotation`` node cannot (see :func:`decorator`).
    """
    start, end = props.get("start_line"), props.get("end_line")
    offsets = _byte_offsets(props)
    if start is None or end is None:
        if offsets == (_UNKNOWN, _UNKNOWN):
            return None
        start = end = _UNKNOWN
    return JSpan(start=(start, _UNKNOWN), end=(end, _UNKNOWN), bytes=offsets)


def body_span(props: Props) -> Optional[JSpan]:
    """The callable's **body block**: ``body_start_byte`` opens it and the callable's own
    ``end_byte`` closes it.

    ``None`` when either is absent -- an abstract or interface method has no body, an implicit
    ``<init>()`` was never written -- which is what makes :attr:`JCallable.code` fall back to the
    declaration span, exactly as it does off ``analysis.json``.

    *The closing offset is measured, not assumed.* The projection carries no ``body_end_byte``, and
    across both committed fixtures (``analysis_json/v2/{a1,a4}``, 1,182 callables carrying both
    spans) there is **no callable** whose ``body_span.bytes[1]`` differs from its ``span.bytes[1]``
    -- a Java method declaration's last character *is* its body's closing brace. Lines:
    ``body_start_line`` where the projection carries one, else :data:`_UNKNOWN`, and
    :attr:`JCallable.code_start_line` falls back to the declaration's first line then; the end line
    is the declaration's, which closes that same brace.
    """
    b0, b1 = props.get("body_start_byte"), props.get("end_byte")
    if b0 is None or b1 is None:
        return None
    return JSpan(start=(props.get("body_start_line", _UNKNOWN), _UNKNOWN), end=(props.get("end_line", _UNKNOWN), _UNKNOWN), bytes=(b0, b1))


def _unknown_span() -> JSpan:
    """The span for a node the model requires one on and the projection carries no lines for."""
    return JSpan(start=(_UNKNOWN, _UNKNOWN), end=(_UNKNOWN, _UNKNOWN), bytes=(_UNKNOWN, _UNKNOWN))


def docstring(props: Props) -> List[JComment]:
    """The node's javadoc as the one-element ``comments`` list it stands in for (see the module
    docstring); empty when the declaration carries none."""
    text = props.get("docstring")
    return [JComment(content=text, is_javadoc=True)] if text is not None else []


def decorator(node: Props, edge: Props) -> JDecorator:
    """An annotation use, from its ``:JAnnotation`` node (keyed by name) and the ``J_ANNOTATED_BY``
    edge's own properties: the ``arguments`` (the source spellings) and the application's position.

    The span rides the **edge** because the node cannot hold it: ``:JAnnotation`` is shared
    vocabulary, deduped across every use site in the application, so it has no single position. The
    edge carries the offsets and no lines, which is why :func:`span` builds a span from offsets
    alone."""
    return JDecorator(name=node["name"], args=list(edge.get("arguments") or []), span=span(edge))


def field(props: Props, decorators: List[JDecorator]) -> JField:
    return JField(
        id=props["id"],
        name=props["name"],
        type=props["type"],
        modifiers=list(props.get("modifiers") or []),
        decorators=decorators,
        comments=docstring(props),
        initializer=props.get("initializer"),
        span=span(props),
    )


def variable(props: Props) -> JLocalVariable:
    return JLocalVariable(name=props["name"], type=props["type"], initializer=props.get("initializer"), span=span(props))


def enum_constant(props: Props) -> JEnumConstant:
    return JEnumConstant(name=props["name"], arguments=list(props.get("arguments") or []), comments=docstring(props))


def record_component(props: Props) -> JRecordComponent:
    return JRecordComponent(
        name=props["name"],
        type=props["type"],
        modifiers=list(props.get("modifiers") or []),
        comments=docstring(props),
        is_variadic=bool(props.get("is_variadic", False)),
    )


def parameters(props: Props) -> List[JCallableParameter]:
    """``JCallable.parameters_json`` -- the analyzer's own serialisation of the parameter list, so
    the parameters (names, types, spans with byte offsets, modifiers, annotations, variadic flag)
    round-trip exactly. Absent on a callable that takes none."""
    raw = props.get("parameters_json")
    return [JCallableParameter.model_validate(p) for p in json.loads(raw)] if raw else []


def body_node(props: Props, callee_signature: Optional[str]) -> JBodyNode:
    """A ``call`` body node. ``callee_signature`` is the ``signature`` of whatever the node's
    ``J_RESOLVES_TO`` edge points at -- a project callable or an external -- and ``None`` when the
    analyzer left the call unresolved.

    ``callee`` -- the *id* of that same target, which is what
    :attr:`~cldk.analysis.commons.results.BodyRef.callee` carries -- comes off ``props`` rather than
    off a second parameter, because the graph writes it on no node: it is the ``t.id`` of the same
    edge, projected into the row by :attr:`JNeo4jBackend._BODY_NODES`. A caller of this function
    that does not project it (the call-site reconstruction, which needs the signature) leaves it
    ``None``, which is what it was before it was ever projected.

    **Columns are the model's own ``-1``, not the body key's.** The key a body node's id ends with
    (``@65:28``) spells a *different* position from the node's span: measured over daytrader8's
    4,006 call nodes, the key column equals the ``span.start`` column on only 629 of them, and on
    the rest the difference runs from 1 to **110** columns, most often **4** (910 nodes). So the key
    is used for what it is -- the ``body`` dict key -- and the column is reported as not projected
    rather than as a number that would be wrong. (The graph carries no ``start_column`` on a
    ``:JBodyNode`` at all; those figures are measured on the same analyzer's JSON, where the spans
    the key would have to agree with do exist.)
    """
    return JBodyNode(
        kind=props["kind"],
        span=span(props),
        callee=props.get("callee"),
        method_name=props.get("method_name"),
        receiver_expr=props.get("receiver_expr"),
        receiver_type=props.get("receiver_type"),
        return_type=props.get("return_type"),
        accessibility=props.get("accessibility"),
        argument_types=list(props.get("argument_types") or []),
        argument_expr=list(props.get("argument_expr") or []),
        callee_signature=callee_signature,
        is_static_call=props.get("is_static_call"),
        is_constructor_call=bool(props.get("is_constructor_call", False)),
    )


def imports(edge: Props) -> List[JImport]:
    """One :class:`JImport` per spelling on a ``J_IMPORTS`` edge. The projection aggregates every
    import of a module that resolves to the same target onto one edge carrying their full dotted
    ``spellings``, so the simple name is the last dotted segment and the source order within a file
    is not recoverable."""
    static, wildcard = bool(edge.get("is_static", False)), bool(edge.get("is_wildcard", False))
    return [JImport(name=s.rsplit(".", 1)[-1], path=s, is_static=static, is_wildcard=wildcard) for s in (edge.get("spellings") or [])]


# ----------------------------------------------------------------------------------------------
# declarations
# ----------------------------------------------------------------------------------------------
def callable_(
    props: Props,
    *,
    decorators: List[JDecorator],
    body: Dict[str, JBodyNode],
    local_variables: List[JLocalVariable],
    types: Dict[str, JType],
) -> JCallable:
    metrics = props.get("cyclomatic_complexity")
    # ``refs`` is a whole-object absence on the wire, not an empty one, and exactly for an implicit
    # callable -- there is no body to analyse (measured: 99 of daytrader8's 1,216 callables carry no
    # ``refs``, and all 99 are the implicit ones, while 225 non-implicit ones carry two empty
    # lists). The graph omits both properties in either case, so ``is_implicit`` is what tells the
    # two apart; deriving it from the properties' absence would report 225 as "not computed".
    implicit = bool(props.get("is_implicit", False))
    return JCallable(
        id=props["id"],
        kind=props["kind"],
        signature=props["signature"],
        declaration=props.get("declaration"),
        return_type=props.get("return_type"),
        parameters=parameters(props),
        modifiers=list(props.get("modifiers") or []),
        error_channel=list(props.get("error_channel") or []),
        decorators=decorators,
        comments=docstring(props),
        metrics=None if metrics is None else {"cyclomatic": metrics},
        refs=None if implicit else {"types": list(props.get("referenced_types") or []), "fields": list(props.get("accessed_fields") or [])},
        local_variables=local_variables,
        body=body,
        types=types,
        is_implicit=implicit,
        is_entrypoint=bool(props.get("is_entrypoint", False)),
        entrypoint_frameworks=list(props.get("entrypoint_frameworks") or []),
        span=span(props),
        body_span=body_span(props),
    )


def type_(
    props: Props,
    *,
    decorators: List[JDecorator],
    fields: Dict[str, JField],
    callables: Dict[str, JCallable],
    types: Dict[str, JType],
    enum_constants: List[JEnumConstant],
    record_components: List[JRecordComponent],
) -> JType:
    return JType(
        id=props["id"],
        kind=props["kind"],
        modifiers=list(props.get("modifiers") or []),
        base_types=list(props.get("base_types") or []),
        interfaces=list(props.get("interfaces") or []),
        decorators=decorators,
        comments=docstring(props),
        enum_constants=enum_constants,
        record_components=record_components,
        fields=fields,
        callables=callables,
        types=types,
        is_entrypoint_class=bool(props.get("is_entrypoint", False)),
        entrypoint_frameworks=list(props.get("entrypoint_frameworks") or []),
        # ``span`` is required on a type and the projection always carries its lines.
        span=span(props) or _unknown_span(),
    )


def compilation_unit(props: Props, *, import_declarations: List[JImport], types: Dict[str, JType]) -> JCompilationUnit:
    """The file, and with it the one text blob every node under it slices its own ``code`` out of
    (:meth:`JCompilationUnit.slice`).

    ``source`` is read with ``.get`` -- **the deliberate exception** to this module's rule that every
    non-boolean property the contract declares is read with ``props[...]``. A module the analyzer
    could not read or could not decode carries no text, and that has to mean "no text for this whole
    file", every node in it reporting ``""``, rather than an attach that raises over one file.
    """
    return JCompilationUnit(
        id=props["id"],
        package=props["package"],
        source=props.get("source") or "",
        content_hash=props.get("content_hash"),
        imports=import_declarations,
        types=types,
        span=span(props) or _unknown_span(),
    )


# ----------------------------------------------------------------------------------------------
# the repository-artifact layer (unprefixed labels; the Java models, not the shared Py* ones --
# the five ABC accessors convert, exactly as JCodeanalyzer does off the wire)
# ----------------------------------------------------------------------------------------------
def config_key(props: Props) -> JConfigKey:
    return JConfigKey(
        id=props["id"],
        key=props["key"],
        namespace=props["namespace"],
        value=props.get("value"),
        references=list(props.get("references") or []),
        span=span(props),
    )


def artifact(props: Props, *, config_keys: List[JConfigKey]) -> JArtifact:
    return JArtifact(
        id=props["id"],
        path=props["path"],
        format=props["format"],
        roles=list(props.get("roles") or []),
        size_bytes=props["size_bytes"],
        sha256=props["sha256"],
        source=props["source"],
        text_truncated=bool(props.get("text_truncated", False)),
        extraction=props["extraction"],
        config_keys=config_keys,
    )


def dependency(edge: Props, package: Props, declared_in: str) -> JDependency:
    """A declared dependency from the ``DECLARES_DEPENDENCY`` edge plus its endpoints: the
    coordinate off the ``:Package`` node, the declaring manifest's id off the ``:Artifact``.
    ``locked_version`` rides a separate ``LOCKS`` edge (a per-package fact, and no relationship of
    that type exists in a Maven projection) and stays ``None``."""
    return JDependency(
        group=package.get("group"),
        name=package["name"],
        ecosystem=package["ecosystem"],
        spec=edge["spec"],
        kind=edge["kind"],
        extras=list(edge.get("extras") or []),
        declared_in=declared_in,
        direct=bool(edge.get("direct", False)),
        prov=list(edge.get("prov") or []),
    )
