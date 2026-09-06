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

"""Rebuild the ``cldk.models.java`` (schema v2) models from codeanalyzer-java 3.0.1 Neo4j node and
edge property maps.

Pure functions: they take the flat property dictionaries the analyzer's Neo4j projection wrote
(``schema.neo4j.json`` at the 3.0.1 tag is the authority for what each label carries) and return
the same pydantic objects the in-memory :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`
returns. :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` fetches the rows and assembles the
containment tree; the per-node shape lives here.

**Booleans.** The projection writes a boolean property only when it is ``True`` (verified:
``text_truncated`` exists on 16 of 5,007 ``:Artifact`` nodes, ``is_implicit`` on 99 of 1,216
daytrader8 callables, ``is_wildcard`` only on wildcard imports). An absent boolean therefore *is*
``False`` in the contract, and reading one with a ``False`` default is not a default hiding drift.
Every non-boolean property the contract declares on a label is read with ``props[...]``.

What the projection does **not** carry, and therefore comes back at the model's own empty default
(measured against the live graph, not assumed):

* **``JModule.source``** -- the graph stores each *callable's* own text in ``JCallable.code`` and
  nothing else, so a reconstructed :class:`JCompilationUnit` has ``source=""``. Its ``span`` is
  unknown too (``:JModule`` carries no lines at all), so it rehydrates as the model's own ``-1``.
  A callable is pointed at the text the graph did project through :func:`thread_code`; every other
  node's ``code`` is ``""``.
* **every column and every byte offset** -- the projection writes ``start_line`` and ``end_line``
  and no position within a line, and no offset into a ``source`` it does not carry. Both are
  reported as :data:`_UNKNOWN` (``-1``), the model's own "not known", on every node: a ``0`` would
  read as column one and offset zero, which is a position, and a wrong one. **One exception, and it
  is deliberate:** a :class:`JCallableParameter` comes back with the analyzer's own columns *and*
  byte offsets, because the projection serialises the whole parameter list into
  ``:JCallable.parameters_json`` and it round-trips exactly (see :func:`parameters`). Those byte
  offsets index the module ``source`` the graph does not carry, so they locate the parameter in the
  file on disk and nothing this backend can hand you; ``JCallableParameter.code`` is unreachable on
  either backend (a parameter is never threaded to its compilation unit, so slicing raises rather
  than returning a silent empty).
* **``JCallable.body_span``** -- the graph projects one line range per callable, the *declaration*
  span. So ``JCallable.code`` here is the whole declaration (``public void f() {…}``), where the
  local backend's is the body block (``{…}``); ``code_start_line`` is the declaration's first line,
  which is the body block's first line too except where the opening brace sits on a later line.
* **comments** -- there are no ``:JComment`` nodes (0 in the reference graph). A type, callable,
  field, enum constant and record component carries a single ``docstring`` property holding its
  javadoc, rebuilt here as a one-element ``comments`` list; a non-javadoc comment on a declaration,
  and every file-level comment, is not projected at all.
* **``JCallable.body``** -- only the ``call`` nodes are rebuilt (what ``call_sites`` is a view
  over), which is roughly **30%** of what the graph holds (4,006 of daytrader8's 13,436
  ``:JBodyNode``); the ``entry``/``exit``/``statement``/``branch``/``loop``/``return`` nodes and the
  parameter lattice are not. A call site's ``arguments`` (body-key references) and both columns are
  not projected either.
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
  ``external_symbols``, and a decorator's / import's / enum constant's / record component's span.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

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


class _ProjectedText:
    """Stands in for the owning :class:`JCompilationUnit` on a callable, so its ``code`` view reads
    the text the graph projected for it.

    ``:JModule`` carries no ``source``, so a reconstructed unit can slice nothing; ``:JCallable``
    carries its own ``code``. The models reach the unit only through the private ``_unit``
    back-reference, and only for :meth:`JCompilationUnit.slice` and ``package`` -- so pointing a
    callable at one of these is what turns ``JCallable.code`` from an empty slice into that text
    (see :func:`thread_code`).
    """

    __slots__ = ("_code", "package")

    def __init__(self, code: str, package: str) -> None:
        self._code, self.package = code, package

    def slice(self, span: JSpan) -> str:
        """The callable's whole projected text: the graph keeps one text per callable, not the
        module source the span would index into."""
        return self._code


def thread_code(unit: JCompilationUnit, code: Mapping[str, str]) -> None:
    """Point every callable in ``unit`` at its projected text, keyed by node id.

    Runs after the unit is validated, because :meth:`JCompilationUnit.model_post_init` threads
    itself onto every node it owns and would otherwise win.
    """

    def walk(t: JType) -> None:
        for c in t.callables.values():
            c._unit = _ProjectedText(code.get(c.id, ""), unit.package)
            for local in c.types.values():
                walk(local)
        for nested in t.types.values():
            walk(nested)

    for top in unit.types.values():
        walk(top)


# ----------------------------------------------------------------------------------------------
# leaves
# ----------------------------------------------------------------------------------------------
#: The model's own "not known". The graph stores ``start_line``/``end_line`` and nothing else, so
#: every column, every byte offset, and the whole span of a node it carries no lines for, are *not
#: projected* -- reported as this rather than as a ``0``, which would read as "column one, offset
#: zero" and index into a ``source`` that is ``""``.
_UNKNOWN = -1


def span(props: Props) -> Optional[JSpan]:
    """The line-only span the projection carries, or ``None`` when it carries no lines (an implicit
    callable). Both columns and both byte offsets are :data:`_UNKNOWN`: the graph projects neither
    (see the module docstring)."""
    start, end = props.get("start_line"), props.get("end_line")
    return None if start is None or end is None else JSpan(start=(start, _UNKNOWN), end=(end, _UNKNOWN), bytes=(_UNKNOWN, _UNKNOWN))


def _unknown_span() -> JSpan:
    """The span for a node the model requires one on and the projection carries no lines for."""
    return JSpan(start=(_UNKNOWN, _UNKNOWN), end=(_UNKNOWN, _UNKNOWN), bytes=(_UNKNOWN, _UNKNOWN))


def docstring(props: Props) -> List[JComment]:
    """The node's javadoc as the one-element ``comments`` list it stands in for (see the module
    docstring); empty when the declaration carries none."""
    text = props.get("docstring")
    return [JComment(content=text, is_javadoc=True)] if text is not None else []


def decorator(node: Props, edge: Props) -> JDecorator:
    """An annotation use from its ``:JAnnotation`` node (keyed by name) and the ``J_ANNOTATED_BY``
    edge's ``arguments`` (the source spellings)."""
    return JDecorator(name=node["name"], args=list(edge.get("arguments") or []))


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

    **Columns are the model's own ``-1``, not the body key's.** The key a body node's id ends with
    (``@65:28``) spells a *different* position from the node's span: measured over daytrader8's
    4,006 call nodes, the key column equals the ``span.start`` column on only 629 of them, and on
    the rest the difference runs from 1 to **110** columns, most often **4** (910 nodes). So the key
    is used for what it is -- the ``body`` dict key -- and the column is reported as not projected
    rather than as a number that would be wrong. (The graph carries no ``start_column`` on a
    ``:JBodyNode`` at all; those figures are measured on the same analyzer's JSON, where the spans
    the key would have to agree with do exist.)
    """
    start, end = props.get("start_line"), props.get("end_line")
    return JBodyNode(
        kind=props["kind"],
        span=None if start is None or end is None else JSpan(start=(start, _UNKNOWN), end=(end, _UNKNOWN), bytes=(_UNKNOWN, _UNKNOWN)),
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
        span=span(props),
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
        # ``span`` is required on a type and the projection always carries its lines.
        span=span(props) or _unknown_span(),
    )


def compilation_unit(props: Props, *, import_declarations: List[JImport], types: Dict[str, JType]) -> JCompilationUnit:
    return JCompilationUnit(
        id=props["id"],
        package=props["package"],
        source="",
        content_hash=props.get("content_hash"),
        imports=import_declarations,
        types=types,
        span=_unknown_span(),
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
