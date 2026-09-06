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

"""The scale corpus: ``JNeo4jBackend`` on ThingsBoard, where daytrader8 is too small to show the
defect.

daytrader8 declares four local classes and no two of them collide, so every accessor answers there
whatever the qualified-name rule is. ThingsBoard declares 5,102 types, 381 of them anonymous
classes whose ``$anon$N`` numbering repeats across the sibling callables of one type — 97 names
that spell one qualified name each unless the declaring callable is part of the name (the J-1
erratum). Under the old rule the *first* accessor to build the index raised, so the whole backend
was dead on this corpus; this suite is the net for that.

Skipped unless pointed at a graph that holds the application::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    uv run pytest tests/analysis/java/test_java_neo4j_scale.py

The application name defaults to ``thingsboard`` and can be overridden with
``CLDK_TEST_NEO4J_JAVA_SCALE_APP``. Read-only, like every other Neo4j suite here.
"""

import logging
import os
import re

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD")
SCALE_APP = os.environ.get("CLDK_TEST_NEO4J_JAVA_SCALE_APP", "thingsboard")

#: A local class's qualified name ends with the declaring callable's signature, then the class's
#: own simple name -- ``$anon$N`` for an anonymous one, a real name for a named local class.
_LOCAL_CLASS = re.compile(r"\)\.[^.]+$")


def _graph_holds_the_application() -> bool:
    if not (NEO4J_URI and NEO4J_PASSWORD):
        return False
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        with GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                return bool(session.run("MATCH (a:JApplication {name: $app}) RETURN count(a) AS n", app=SCALE_APP).single()["n"])
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _graph_holds_the_application(),
    reason=f"needs a Neo4j Java graph holding application {SCALE_APP!r} (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD)",
)


@pytest.fixture(scope="module")
def backend():
    from cldk.analysis.java.neo4j import JNeo4jBackend

    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=SCALE_APP)
    yield neo
    neo.close()


def test_the_index_routed_accessors_answer_on_the_scale_corpus(backend):
    """``get_all_classes`` builds the index, which is where the collision used to surface: one
    qualified name per declaration, every local class keyed by the callable that declares it."""
    classes = backend.get_all_classes()
    assert len(classes) == len({t.id for t in classes.values()}), "two declarations share a qualified name"
    locals_ = {name: t for name, t in classes.items() if t.is_local_class}
    assert locals_, "the corpus declares no local classes; it cannot witness the erratum"
    for name, t in locals_.items():
        assert _LOCAL_CLASS.search(name), f"{name} does not carry its declaring callable"
        assert t.id.endswith("/" + name.rsplit(".", 1)[-1]), name
        assert "can://" not in name


def test_get_class_resolves_a_local_class_by_its_qualified_name(backend):
    """The key `get_all_classes` reports is the key `get_class` accepts — and it is the *only* one:
    the shorter spelling that used to collide resolves to nothing."""
    name, local = next((n, t) for n, t in backend.get_all_classes().items() if t.is_local_class)
    assert backend.get_class(name) is local
    enclosing, _, simple = name.rpartition(".")
    assert backend.get_class(f"{enclosing.rpartition('.')[0]}.{simple}") is not local


def test_docstrings_cover_the_declaration_kinds_daytrader8_has_none_of(backend):
    """``get_all_docstrings`` harvests every declaration the projection documents — including enum
    constants and record components, which daytrader8 declares none of, so the parity suite cannot
    see whether they are in the loop."""
    documented = {c.content for comments in backend.get_all_docstrings().values() for c in comments}
    assert documented
    for kind in ("JEnumConstant", "JRecordComponent"):
        rows = backend._run(
            f"MATCH (n:{kind}) WHERE n.id STARTS WITH $prefix AND n.docstring IS NOT NULL RETURN n.docstring AS d",
            prefix=backend._scope_prefix,
        )
        assert all(r["d"] in documented for r in rows), f"a {kind} docstring the projection carries is not reported"


def test_the_call_graph_builds_and_is_keyed_by_strings(backend):
    """Every ``J_CALLS`` endpoint homes on the index, so the graph builds at all — and a callable
    declared inside a local class keys under that class's full name."""
    graph = backend.get_call_graph()
    assert graph.number_of_edges() > 0
    assert all(isinstance(n, str) and "can://" not in n for n in graph.nodes)
    assert all(graph.nodes[n]["kind"] == "callable" for n in graph.nodes)
    assert any(_LOCAL_CLASS.search(graph.nodes[n]["method_detail"].klass) for n in graph.nodes), "no local class is a call-graph endpoint"
