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


"""The shared repository-artifact layer, rebuilt from the Neo4j projection: property maps →
``PyArtifact`` / ``PyConfigKey`` / ``PyDependency``.

Every codeanalyzer projects this layer identically and unprefixed (``:Artifact``, ``:ConfigKey``,
``:Package``; ``HAS_ARTIFACT``, ``DEFINES_CONFIG``, ``DECLARES_DEPENDENCY``, ``LOCKS``), and the
generic ABC (:mod:`cldk.analysis.commons.backend`) promises the same four ``Py*`` models from every
language, so the reconstructors live here once. Lifted verbatim from
``cldk/analysis/python/neo4j/reconstruct.py`` (leg 2.5a), which re-exports them.
"""

from __future__ import annotations

from typing import Any, List, Mapping

from cldk.models.python import PyArtifact, PyConfigKey, PyDependency, Span

Props = Mapping[str, Any]


def config_key(props: Props) -> PyConfigKey:
    """Rebuild a :class:`PyConfigKey` from a ``:ConfigKey`` node's properties.

    Line-only ``span`` (see :func:`body_node`): the projection writes ``start_line``/``end_line``
    and nothing finer, so the columns and byte offsets rehydrate as ``0``. ``span`` stays ``None``
    when the node carries no lines at all (best-effort extraction never located the key in the
    artifact's source).
    """
    lines = (props.get("start_line"), props.get("end_line"))
    return PyConfigKey(
        id=props.get("id", ""),
        key=props.get("key", ""),
        namespace=props.get("namespace", ""),
        value=props.get("value"),
        span=Span(start=(lines[0], 0), end=(lines[1], 0), bytes=(0, 0)) if None not in lines else None,
        references=list(props.get("references", []) or []),
    )


def artifact(props: Props, *, config_keys: List[PyConfigKey] | None = None) -> PyArtifact:
    """Rebuild a :class:`PyArtifact` from an ``:Artifact`` node's properties plus its fetched
    :class:`PyConfigKey` children (``[:DEFINES_CONFIG]``).

    ``kind`` is not a projected property — every ``PyArtifact`` the analyzer emits carries the
    model's own default (``"artifact"``; see ``codeanalyzer/artifacts/discovery.py``), so it is
    supplied here rather than queried for.
    """
    return PyArtifact(
        id=props.get("id", ""),
        kind="artifact",
        path=props.get("path", ""),
        format=props.get("format", ""),
        roles=list(props.get("roles", []) or []),
        size_bytes=props.get("size_bytes", 0),
        sha256=props.get("sha256", ""),
        source=props.get("source", ""),
        extraction=props.get("extraction", "none"),
        config_keys=config_keys or [],
    )


def dependency(props: Props, *, name: str, ecosystem: str, declared_in: str) -> PyDependency:
    """Rebuild a :class:`PyDependency` from a ``[:DECLARES_DEPENDENCY]`` edge's properties plus its
    endpoints (``name``/``ecosystem`` off the ``:Package`` node, ``declared_in`` off the
    ``:Artifact`` node). ``ecosystem`` is a real ``Package`` property (``neo4j/schema.py``'s
    ``Package`` node type carries it); ``"pypi"`` is only ever what the analyzer happens to write
    there today (its only ecosystem, per ``PyDependency.ecosystem``'s own docstring) — read off the
    node rather than hardcoded, so this doesn't silently go stale the day a second ecosystem ships.

    ``locked_version``/``provides_imports`` are projection-lossy here: the graph carries them on
    the separate ``[:LOCKS]``/``[:PY_PROVIDES]`` edges (per-package facts, not per-declaration), and
    no caller of this reconstruction chases those yet, so they come back at the model's own empty
    defaults — the same class of gap :func:`callsite` documents for ``argument_types``.
    """
    return PyDependency(
        name=name,
        ecosystem=ecosystem,
        spec=props.get("spec", ""),
        kind=props.get("kind", "runtime"),
        extras=list(props.get("extras", []) or []),
        declared_in=declared_in,
        direct=props.get("direct", True),
        provides_imports=[],
        prov=list(props.get("prov", []) or []),
    )
