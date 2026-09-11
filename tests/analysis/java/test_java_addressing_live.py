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

r"""Live parity for the Java addressing surface (leg 3b, Task 1), against a real graph.

The offline suite proves the policy over fixtures; this one proves the half that only a server can:
that the body nodes ``locate`` reads out of the graph are the ones the analyzer wrote, that the
resolution probe reads a real ``J_RESOLVES_TO``, and that every answer matches the in-memory
backend over the **whole** daytrader8 application -- 1,216 callables and 138 modules, in a database
that also holds ThingsBoard, so a leak shows up as a larger answer rather than as nothing.

Same environment as ``test_java_neo4j_backend.py``::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    CLDK_TEST_NEO4J_APP=daytrader8 \
    CLDK_TEST_JAVA_PROJECT=/path/to/project \
    CLDK_TEST_JAVA_CACHE=/path/to/dir \        # a level-4 reference analysis.json
    uv run pytest tests/analysis/java/test_java_addressing_live.py

**The one tolerance, asserted rather than skipped:** every column is ``-1`` over Neo4j -- the
projection writes ``start_line`` / ``end_line`` and byte offsets, and no column anywhere -- so spans
are compared on their **lines**, and their byte offsets are compared for equality.

**Text is no longer a tolerance.** codeanalyzer-java 3.2.0 projects ``:JModule.source`` plus a
``start_byte``/``end_byte`` pair down to the body nodes, so ``LocateResult.source``,
:meth:`get_source` and :meth:`describe` are asserted **equal** on both backends -- the same slice of
the same file, at every granularity including a single statement. Before 3.2.0 the graph carried one
line range per callable and no ``body_span``, this suite asserted ``endswith`` on a callable, and a
body node's source was ``None`` over Neo4j (codeanalyzer-java#176). That is the divergence the 3.2.0
floor exists to have closed.

Read-only, like every other Neo4j suite here.
"""

import json
import logging
import os
import time
from pathlib import Path

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")
SCALE_APP = os.environ.get("CLDK_TEST_NEO4J_SCALE_APP", "thingsboard")

REFERENCE_LEVEL = "system_dependency_graph"


def _reference_cache_is_level_4() -> bool:
    if not JAVA_CACHE:
        return False
    try:
        return int(json.loads((Path(JAVA_CACHE) / "analysis.json").read_text(encoding="utf-8")).get("max_level", 0)) >= 4
    except (OSError, ValueError, AttributeError):
        return False


def _reachable() -> bool:
    if not (JAVA_APP and JAVA_PROJECT and _reference_cache_is_level_4()):
        return False
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="needs a pre-populated Neo4j Java graph + a level-4 reference cache (set CLDK_TEST_NEO4J_* / CLDK_TEST_JAVA_*)")


@pytest.fixture(scope="module")
def backends():
    from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
    from cldk.analysis.java.neo4j import JNeo4jBackend

    ref = JCodeanalyzer(project_dir=JAVA_PROJECT, analysis_json_path=JAVA_CACHE, analysis_level=REFERENCE_LEVEL, eager_analysis=False, target_files=None)
    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=JAVA_APP)
    yield ref, neo
    neo.close()


@pytest.fixture(scope="module")
def scale():
    from cldk.analysis.java.neo4j import JNeo4jBackend

    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=SCALE_APP)
    if not neo._run("MATCH (a:JApplication {name: $app}) RETURN a LIMIT 1", app=SCALE_APP):
        neo.close()
        pytest.skip(f"the graph does not hold application {SCALE_APP!r}")
    yield neo
    neo.close()


def _positions(ref):
    """One position inside every callable of the application that has a span, plus the first line
    of every module (which is the ``package`` statement -- module scope on every one of them)."""
    inside = [(ref.get_java_file(t.qualified_name), c.start_line + 1) for t, c in ref._callables.values() if c.span is not None]
    return inside + [(path, 1) for path in ref.get_symbol_table()]


# ---- locate ------------------------------------------------------------------------------------
def test_locate_many_agrees_on_every_callable_and_every_module(backends):
    """Every position of the application at once, on both backends. The callable, the type, the
    module and its dotted name, the diagnostics and the body node must be identical; the source and
    the span carry the two documented tolerances."""
    ref, neo = backends
    positions = _positions(ref)
    assert len(positions) > 1000, positions[:3]
    with_body = 0
    for (path, line), a, b in zip(positions, ref.locate_many(positions), neo.locate_many(positions)):
        where = f"{path}:{line}"
        assert a.callable == b.callable, where
        assert a.type == b.type, where
        assert (a.module.path, a.module.module_name) == (b.module.path, b.module.module_name), where
        assert [d.code for d in a.diagnostics] == [d.code for d in b.diagnostics] or [d.code for d in b.diagnostics] == [
            *[d.code for d in a.diagnostics],
            "module_source_unavailable",
        ], where
        assert a.node_id == b.node_id, where
        assert (a.body.kind, a.body.span.start[0], a.body.span.end[0]) == (b.body.kind, b.body.span.start[0], b.body.span.end[0]) if a.body else b.body is None, where
        # ``callee`` is CONTAINMENT, not equality, and the direction is fixed: ``--emit neo4j``
        # forces ``--external-calls`` and the reference run does not, so the graph homes the JDK
        # and library targets the payload leaves null (measured on daytrader8: 1,723 of 4,006 call
        # sites resolve in the payload, all 4,006 in the graph, the extra 2,283 to ``:JExternal``).
        # Wherever the payload resolved, the graph must name the same thing.
        assert a.body is None or a.body.callee is None or a.body.callee == b.body.callee, where
        assert (a.span.start[0], a.span.end[0]) == (b.span.start[0], b.span.end[0]), where
        if a.callable is not None:
            assert b.source == a.source, f"{where}: the two backends sliced different text out of the same file"
        with_body += a.body is not None
    assert with_body > 100, "no position landed on a body node; the comparison proved nothing about them"


def test_a_module_scope_result_carries_the_module_text_on_both_backends(backends):
    """``module_source_unavailable`` is a *data* diagnostic, not a backend one: it fires when the unit
    carries no ``source``, which on a 3.2.0 graph is no unit at all (141/141 carry it). A graph that
    lost a file's text would still say so here, and say it per module."""
    ref, neo = backends
    path = next(iter(ref.get_symbol_table()))
    assert [d.code for d in ref.locate(path, 1).diagnostics] == ["module_scope"]
    assert [d.code for d in neo.locate(path, 1).diagnostics] == ["module_scope"]
    assert neo.locate(path, 1).source == ref.locate(path, 1).source != ""


def test_the_body_node_ids_are_the_ones_the_graph_carries(backends):
    """The composed ids (:func:`java_body_node_id`) against the graph's own ``b.id``: every
    ``node_id`` ``locate`` hands back names a node that really exists."""
    ref, neo = backends
    ids = [r.node_id for r in neo.locate_many(_positions(ref)) if r.node_id]
    assert ids
    rows = neo._run("UNWIND $ids AS i MATCH (b:JBodyNode {id: i}) RETURN count(b) AS n", ids=sorted(set(ids)))
    assert rows[0]["n"] == len(set(ids)), "a node_id names nothing in the graph"


def test_the_graph_resolves_every_call_the_payload_does_and_the_externals_besides(backends):
    """``BodyRef.callee`` under the one relation that is true of it, stated as containment.

    Equality would be the wrong assertion and would have to be weakened to something untrue to
    pass: the two sources were asked different questions. ``--emit neo4j`` forces
    ``--external-calls``, so the graph homes every call target; the reference run does not pass the
    flag, so its ``callee`` is null on every call that leaves the project. What must hold is that
    the graph *agrees* wherever the payload resolved, and that everything it resolves besides is an
    external the caller can look up -- ``get_external_symbols`` is keyed by exactly those ids.
    """
    ref, neo = backends
    ids = sorted(ref._callables)
    mine = {node_id: n.callee for nodes in ref._body_nodes(ids).values() for node_id, n in nodes.items() if n.callee}
    theirs = {node_id: n.callee for nodes in neo._body_nodes(ids).values() for node_id, n in nodes.items() if n.callee}
    assert mine, "the reference payload resolved nothing; the containment would be vacuous"
    assert set(mine) < set(theirs), "the graph resolves strictly more, being the run that was asked for externals"
    assert {node_id: theirs[node_id] for node_id in mine} == mine, "the graph disagrees with the payload on a call both resolved"
    extra = [theirs[node_id] for node_id in set(theirs) - set(mine)]
    assert all("/@external/" in callee for callee in extra), "the graph resolved a project callable the payload did not"
    externals = neo.get_external_symbols()
    assert all(callee in externals for callee in extra), "an @external callee that get_external_symbols does not name"


# ---- resolve_callable / resolve_value ----------------------------------------------------------
def test_resolve_callable_agrees_on_every_callable(backends):
    ref, neo = backends
    keys = sorted(ref._addressing.by_key)
    assert len(keys) == 1216, len(keys)
    for key in keys:
        assert ref.resolve_callable(key) == neo.resolve_callable(key), key


def test_resolve_callable_miss_paths_agree(backends):
    ref, neo = backends
    for call in (
        lambda be: be.resolve_callable("noSuchMethodAnywhere"),
        lambda be: be.resolve_callable("cancelOrder"),
        lambda be: be.resolve_callable("cancelOrder", in_module="com.example.nosuch"),
        lambda be: be.resolve_callable("cancelOrder", in_class="NoSuchClass"),
        lambda be: be.get_source("no.such.Type.m()"),
        lambda be: be.resolve_value("nope", within="TradeDirect.cancelOrder(java.lang.Integer, boolean)"),
    ):
        with pytest.raises(Exception) as a:
            call(ref)
        with pytest.raises(Exception) as b:
            call(neo)
        assert type(a.value) is type(b.value), str(a.value)
        assert str(a.value) == str(b.value)
        assert "can://" not in str(a.value)


def test_resolve_value_agrees_on_every_parameter(backends):
    ref, neo = backends
    checked = 0
    for key, row in sorted(ref._addressing.by_key.items()):
        for parameter in row.callable.parameters:
            if not parameter.name:
                continue
            assert ref.resolve_value(parameter.name, within=key) == neo.resolve_value(parameter.name, within=key), f"{key}#{parameter.name}"
            checked += 1
    assert checked == 1166, checked


def test_a_parameter_ref_names_a_formal_in_vertex_the_graph_holds(backends):
    """The claim ``resolve_value``'s ``ref`` makes: ``<callable id>@formal_in:<n>`` is the
    analyzer's own vertex id, not a spelling this SDK invented."""
    ref, neo = backends
    refs = sorted({neo.resolve_value(p.name, within=key).ref for key, row in ref._addressing.by_key.items() for p in row.callable.parameters if p.name})
    rows = neo._run("UNWIND $ids AS i MATCH (b:JBodyNode {id: i, kind: 'formal_in'}) RETURN count(b) AS n", ids=refs)
    assert rows[0]["n"] == len(refs), "a parameter ref names no formal_in vertex in the graph"


# ---- J-6 -----------------------------------------------------------------------------------------
def test_the_three_shapes_j6_makes_addressable(backends):
    ref, neo = backends
    for be in (ref, neo):
        initializer = be.resolve_callable("<clinit>$0()")
        assert initializer.kind == "callable" and initializer.line > 0 and be.get_source(initializer.callable)

        implicit = [row for row in be._addressing.by_key.values() if row.callable.is_implicit]
        assert len(implicit) == 99, len(implicit)
        node = be.resolve_callable(implicit[0].key)
        assert node.line == -1
        with pytest.raises(KeyError, match="implicit"):
            be.get_source(node.callable)

        anonymous = sorted(k for k in be._addressing.by_key if ".$anon$" in k)
        assert len(anonymous) == 8, anonymous
        assert be.resolve_callable(anonymous[0]).callable == anonymous[0]


# ---- get_source / describe / has_resolution_edges -----------------------------------------------
def test_get_source_holds_the_documented_relation_on_every_callable(backends):
    ref, neo = backends
    compared = 0
    for key, row in sorted(ref._addressing.by_key.items()):
        if row.callable.is_implicit:
            continue
        assert neo.get_source(key) == ref.get_source(key), key
        compared += 1
    assert compared == 1117, compared


def test_describe_fills_the_same_text_on_both_backends(backends):
    """A callable and a single statement, both granularities: 3.2.0 puts byte offsets on the body
    nodes too, so ``describe`` no longer answers ``None`` over Neo4j where it answers text locally."""
    ref, neo = backends
    found = next(r for r in ref.locate_many(_positions(ref)) if r.body is not None)
    assert neo.describe([found])[0].source == ref.describe([found])[0].source != None
    node = ref.resolve_callable(found.callable.signature, in_class=found.type.signature)
    assert neo.describe([node])[0].source == ref.describe([node])[0].source


def test_has_resolution_edges_is_true_on_a_graph_emitted_the_documented_way(backends):
    ref, neo = backends
    assert ref.has_resolution_edges is True
    assert neo.has_resolution_edges is True
    assert neo._run("MATCH (b:JBodyNode)-[:J_RESOLVES_TO]->() WHERE b.id STARTS WITH $p RETURN count(*) AS n", p=neo._scope_prefix)[0]["n"] > 0


# ---- scale ---------------------------------------------------------------------------------------
def test_the_surface_answers_on_the_scale_corpus(scale):
    """ThingsBoard: 28,763 callables, 496,821 body nodes. The point is that a per-callable page
    does not scan the graph -- the body-node fetch is anchored on the callable's own id prefix."""
    keys = list(scale._addressing.by_key)
    assert len(keys) > 25000, len(keys)
    row = max(scale._addressing.by_key.values(), key=lambda r: r.callable.end_line - r.callable.start_line)
    started = time.perf_counter()
    found = scale.locate(row.path, row.callable.start_line + 1)
    elapsed = time.perf_counter() - started
    assert found.callable is not None and found.callable.signature == row.callable.signature
    assert elapsed < 5.0, f"one locate took {elapsed:.2f}s on the scale corpus"
    assert scale.resolve_callable(row.key).callable == row.key
