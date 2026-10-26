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

"""Rebuild :mod:`cldk.models.go` objects from the ``codeanalyzer-go`` Neo4j node property maps.

Pure functions: each takes the flat property dict the ``--emit neo4j`` projection wrote for one
node (``internal/neo4j/schema.go`` is the authority for what each label carries) and returns the
same pydantic object the local backend parses from ``analysis.json``. :class:`GoNeo4jBackend`
fetches the rows and these turn them back into models; the per-node shape lives here.

Two props are **JSON strings** on the wire, not nested maps (the projector flattens them so a Bolt
property stays scalar): ``span_json`` (every spanned node) and ``metrics_json`` (callables). They
are parsed here. The graph does **not** carry ``base_types`` on a ``:GoType`` (embeds are the
``GO_EMBEDS`` relationship, not a node property) nor ``parameters`` on a ``:GoCallable`` (not
projected), so a reconstructed type/callable has those at their empty default — the lazy backend
answers the few accessors that need them from the relationships instead.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from cldk.models.go import GoBodyNode, GoCallable, GoField, GoMetrics, GoModule, GoSpan, GoType

Props = Mapping[str, Any]


def _span(props: Props) -> Optional[GoSpan]:
    """Parse the ``span_json`` string into a :class:`GoSpan`; ``None`` when absent."""
    raw = props.get("span_json")
    if not raw:
        return None
    d = json.loads(raw)
    return GoSpan(start=tuple(d["start"]), end=tuple(d["end"]), bytes=tuple(d["bytes"]))


def _metrics(props: Props) -> Optional[GoMetrics]:
    raw = props.get("metrics_json")
    if not raw:
        return None
    return GoMetrics(**json.loads(raw))


def module(props: Props) -> GoModule:
    """A :class:`GoModule` from a ``:GoModule`` node (carries ``source``, so ``get_source`` works
    on this backend). ``imports``/``types``/``functions`` are filled by the caller from the
    containment relationships; a bare module reconstruct leaves them empty."""
    return GoModule(
        id=props["id"],
        span=_span(props),
        package=props.get("package", ""),
        source=props.get("source", ""),
        content_hash=props.get("content_hash"),
    )


def go_type(props: Props) -> GoType:
    """A :class:`GoType` from a ``:GoType`` node. ``callables``/``fields`` are filled by the caller
    from ``GO_HAS_METHOD``/``GO_HAS_FIELD``; ``base_types`` from ``GO_EMBEDS``."""
    return GoType(id=props["id"], kind=props["kind"], span=_span(props))


def callable_(props: Props) -> GoCallable:
    """A :class:`GoCallable` from a ``:GoCallable`` node. ``parameters``/``body``/``callables`` are
    not projected on the node; the caller fills ``body`` from ``GO_HAS_BODY_NODE`` where needed."""
    return GoCallable(
        id=props["id"],
        kind=props.get("kind", "function"),
        signature=props["signature"],
        return_type=props.get("return_type"),
        error_channel=list(props.get("error_channel") or []),
        metrics=_metrics(props),
        source_file=props.get("source_file"),
        span=_span(props),
    )


def field(props: Props) -> GoField:
    return GoField(id=props["id"], type=props.get("type"), span=_span(props))


def body_node(props: Props) -> GoBodyNode:
    return GoBodyNode(
        kind=props.get("kind", "call"),
        callee=props.get("callee"),
        is_goroutine=bool(props.get("is_goroutine", False)),
        span=_span(props),
    )
