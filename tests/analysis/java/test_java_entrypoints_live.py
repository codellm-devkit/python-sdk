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

r"""Live parity for entrypoints, the bulk projections and the J-7 leaf accessors (leg 3b, Task 3).

The offline suite proves the whole policy over the committed fixtures, because Task 3 answers from
the canonical :class:`JApplication` both backends hold. Two things only a server can prove are here:

* **the one statement this task adds** -- ``JNeo4jBackend._external_rows``, which projects
  ``:JExternal`` into ``JApplication.external_symbols``; and
* **the leaf accessors' real domain.** daytrader8 declares no enum and no record at all, so a green
  test there proves nothing about ``get_enums``/``get_records``. ThingsBoard declares 594
  interfaces, 192 enums and 35 records, measured on the graph.

Both applications live in the same database, so a scope leak shows up as a larger answer.

Same environment as ``test_java_addressing_live.py``::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    CLDK_TEST_NEO4J_APP=daytrader8 \
    CLDK_TEST_JAVA_PROJECT=/path/to/project \
    CLDK_TEST_JAVA_CACHE=/path/to/dir \
    uv run pytest tests/analysis/java/test_java_entrypoints_live.py

Read-only, like every other Neo4j suite here.
"""

import json
import logging
import os
from pathlib import Path

import pytest

from cldk.analysis.commons.results import EntrypointCoverage
from cldk.utils.exceptions import SelectorNotInGraph
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")
SCALE_APP = os.environ.get("CLDK_TEST_NEO4J_SCALE_APP", "thingsboard")

REFERENCE_LEVEL = "system_dependency_graph"

#: Measured on the reference graph, both applications, 2026-09-06.
DAYTRADER_ENTRYPOINT_CALLABLES = 133
DAYTRADER_ENTRYPOINT_TYPES = 66
DAYTRADER_CALLABLES = 1216
DAYTRADER_EXTERNALS = 1195
SCALE_INTERFACES, SCALE_ENUMS, SCALE_RECORDS = 594, 192, 35
#: 2,570 on a graph emitted by codeanalyzer-java 3.0.3; 3.1.0 adds one, and it is not a call target:
#: ``@external/org.springframework.beans.factory.annotation.Value/value()``, the ghost callee of
#: ThingsBoard's ``@Value`` config reads. ``J_READS_CONFIG_UNRESOLVED`` points at a ``:JExternal``,
#: so a read the analyzer could not resolve now mints one even where nothing calls it.
SCALE_EXTERNALS = 2571

TB_MSG_TYPE = "org.thingsboard.server.common.data.msg.TbMsgType"
TB_RECORD = "org.thingsboard.server.coapserver.TbCoapDtlsSessionKey"


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


# ---- entrypoints, over the whole application ---------------------------------------------------
def test_the_entrypoint_marks_agree_callable_for_callable(backends):
    ref, neo = backends
    assert {o.key for o in ref.get_entrypoints()} == {o.key for o in neo.get_entrypoints()}
    assert len(ref.get_entrypoints()) == DAYTRADER_ENTRYPOINT_CALLABLES
    assert sorted(ref.get_entrypoints(), key=lambda o: o.key) == sorted(neo.get_entrypoints(), key=lambda o: o.key), "field for field, spans included"


def test_the_entrypoint_types_agree_type_for_type(backends):
    ref, neo = backends
    assert len(ref.get_entrypoint_classes()) == DAYTRADER_ENTRYPOINT_TYPES
    assert sorted(ref.get_entrypoint_classes(), key=lambda c: c.qualified_name) == sorted(neo.get_entrypoint_classes(), key=lambda c: c.qualified_name)


def test_get_entrypoint_coverage_reads_the_report_off_a_real_graph(backends):
    """The ``:JApplication`` anchor really does carry the report -- asserted against the server, not
    against a fixture, because that is the fact the accessor rests on. codeanalyzer-java 3.1.0
    projects it as ``entrypoint_report_json``, the whole model as JSON, so the graph and the payload
    are equal object for object rather than merely both non-empty."""
    ref, neo = backends
    props = neo._run("MATCH (a:JApplication {name: $app}) RETURN keys(a) AS k", app=JAVA_APP)[0]["k"]
    # ``id`` joined this set with the can://<app>/<lang>/... grammar: the root merges on its
    # own id now rather than on the free-text --app-name, so two same-named applications
    # stop colliding. Asserted exactly, so a property appearing or vanishing is a failure.
    assert sorted(props) == ["analyzer_name", "analyzer_version", "entrypoint_frameworks", "entrypoint_report_json", "id", "name", "schema_version"]
    for backend in (ref, neo):
        coverage = backend.get_entrypoint_coverage()
        assert isinstance(coverage, EntrypointCoverage)
        assert coverage.diagnostics == []
        assert coverage.frameworks_detected == ["jakarta", "jaxrs", "spring"]
        assert coverage.rulesets == ["jakarta", "struts", "spring", "camel", "jaxrs"]
        assert coverage.unresolved == {} and coverage.errors == []
    assert ref.get_entrypoint_coverage() == neo.get_entrypoint_coverage()


# ---- the bulk projections, over the whole application ------------------------------------------
def test_the_callable_projection_is_the_same_domain_on_both_backends(backends):
    ref, neo = backends
    a, b = ref.get_callables_overview(), neo.get_callables_overview()
    assert len(a) == DAYTRADER_CALLABLES and {o.key for o in a} == {o.key for o in b}
    assert sorted(a, key=lambda o: o.key) == sorted(b, key=lambda o: o.key), "field for field, on all 1,216"


def test_get_decorated_callables_reads_the_annotation_edges(backends):
    """On the graph the annotation names come from ``J_ANNOTATED_BY`` (807 edges on daytrader8, 328
    of them ``@Override`` on a callable), threaded onto the callables by 3a's containment walk."""
    ref, neo = backends
    for markers, expected in ((["Override"], 328), (["@Override"], 328), (["java.lang.Override"], 328), (["Inject"], 12), (["Trace"], 0)):
        assert {o.key for o in ref.get_decorated_callables(markers)} == {o.key for o in neo.get_decorated_callables(markers)}, markers
        assert len(neo.get_decorated_callables(markers)) == expected, markers


def test_method_bodies_and_call_sites_are_keyed_the_same_way(backends):
    ref, neo = backends
    keys = sorted(o.key for o in ref.get_callables_overview())
    assert set(ref.get_method_bodies(keys)) == set(neo.get_method_bodies(keys)), "the two disagree about which callables have text"
    # The text itself differs by backend exactly as ``get_source`` does (codeanalyzer-java#176):
    # the graph's ``code`` is the whole declaration, which ends with the body block.
    assert all(neo.get_method_bodies(keys)[k].endswith(ref.get_method_bodies(keys)[k]) for k in ref.get_method_bodies(keys))
    a, b = ref.get_callsites_for(keys), neo.get_callsites_for(keys)
    assert set(a) == set(b) == set(keys)
    # Sorted, not in list order: the graph projects no **column** for a body node, so two calls on
    # one line cannot be put back in source order there and the two backends order them differently
    # within a line. The set is the contract; the order within a line is not (see the accessor).
    site = lambda v: sorted((s.method_name, s.callee_signature, s.start_line, s.receiver_type) for s in v)
    assert {k: site(v) for k, v in a.items()} == {k: site(v) for k, v in b.items()}
    assert sum(len(v) for v in a.values()) == sum(len(v) for v in b.values()) == 4006, "daytrader8 has 4,006 call body nodes"


# ---- external symbols: the one accessor whose two sources were asked different questions --------
def test_the_graph_homes_externals_and_the_local_run_was_never_asked(backends):
    ref, neo = backends
    external = neo.get_external_symbols()
    assert len(external) == DAYTRADER_EXTERNALS
    assert all(nid.startswith(f"can://{JAVA_APP}/@external/") for nid in external)
    assert all(s.signature and s.kind for s in external.values())
    with pytest.raises(CodeanalyzerExecutionException) as excinfo:
        ref.get_external_symbols()
    assert "--external-calls" in str(excinfo.value)


def test_externals_do_not_leak_between_the_two_applications(backends, scale):
    _, neo = backends
    assert len(scale.get_external_symbols()) == SCALE_EXTERNALS
    assert set(scale.get_external_symbols()).isdisjoint(neo.get_external_symbols())


# ---- the J-7 leaf accessors, on the corpus that actually has the kinds --------------------------
def test_the_leaf_accessors_on_the_scale_corpus(scale):
    interfaces, enums, records = scale.get_interfaces(), scale.get_enums(), scale.get_records()
    assert (len(interfaces), len(enums), len(records)) == (SCALE_INTERFACES, SCALE_ENUMS, SCALE_RECORDS)
    assert all(t.kind == "interface" for t in interfaces.values())
    assert all(t.kind == "enum" for t in enums.values())
    assert all(t.kind == "record" for t in records.values())
    assert set(interfaces).isdisjoint(enums) and set(enums).isdisjoint(records)
    assert set(interfaces) | set(enums) | set(records) <= set(scale.get_all_classes())


def test_get_enum_members_reads_a_real_enums_constants(scale):
    members = scale.get_enum_members(TB_MSG_TYPE)
    assert len(members) == 52 and members[0].name.isupper()
    assert scale.get_enum_members(TB_MSG_TYPE) == scale.get_enums()[TB_MSG_TYPE].enum_constants
    with pytest.raises(SelectorNotInGraph) as excinfo:
        scale.get_enum_members(TB_RECORD)
    assert excinfo.value.kind == "enum" and excinfo.value.missing == [TB_RECORD], "a record is not an enum, and saying so is not an empty list"


def test_the_leaf_accessors_answer_daytrader_honestly(backends):
    """Three interfaces, and genuinely no enum and no record -- which is why the counts above are
    measured on ThingsBoard instead."""
    ref, neo = backends
    assert set(ref.get_interfaces()) == set(neo.get_interfaces()) and len(ref.get_interfaces()) == 3
    assert ref.get_enums() == {} == neo.get_enums()
    assert ref.get_records() == {} == neo.get_records()


# ---- the artifact layer through the facade's own delegation ------------------------------------
def test_the_artifact_layer_and_config_readers_agree(backends):
    ref, neo = backends
    assert set(ref.get_artifacts()) == set(neo.get_artifacts())
    assert [(d.name, d.ecosystem, d.direct) for d in ref.get_dependencies()] == [(d.name, d.ecosystem, d.direct) for d in neo.get_dependencies()]
    assert set(ref.get_config_keys()) == set(neo.get_config_keys())
    assert all("@key/" in k and not k.startswith("can://") for k in ref.get_config_keys())
    # The code-to-config layer (codeanalyzer-java 3.1.0). The two sources agree on the resolved
    # edges exactly and diverge on the unresolved reads for one stated reason:
    # ``J_READS_CONFIG_UNRESOLVED`` is discriminated by ``(key, reason)`` and carries no site, so
    # the payload's 16 per-site entries are 8 edges. Presence, not count, is the contract.
    assert len(ref.get_config_uses()) == len(neo.get_config_uses()) == 13
    assert {(u.src, u.dst, tuple(u.prov)) for u in ref.get_config_uses()} == {(u.src, u.dst, tuple(u.prov)) for u in neo.get_config_uses()}
    assert {tuple(u.prov) for u in ref.get_config_uses()} == {("literal",)}, "every resolved use is a literal at the call site"
    assert len(ref.get_unresolved_config_reads()) == 16 and len(neo.get_unresolved_config_reads()) == 8
    assert {(r.callee, r.key, r.reason, tuple(r.prov)) for r in ref.get_unresolved_config_reads()} == {
        (r.callee, r.key, r.reason, tuple(r.prov)) for r in neo.get_unresolved_config_reads()
    }, "the graph collapses per-site duplicates and loses nothing else"
    # ``--emit neo4j`` forces level 4, and the reference is read at level 4 too, so both ran the
    # dataflow tier over the DDG -- and it still could not name these keys.
    assert {tuple(r.prov) for r in ref.get_unresolved_config_reads()} == {("literal", "dataflow")}
    assert all(r.site for r in ref.get_unresolved_config_reads()) and all(r.site == "" for r in neo.get_unresolved_config_reads())
    for backend in (ref, neo):
        readers = backend.get_config_readers("maxUsers")
        assert [r.key for r in readers] == ["com.ibm.websphere.samples.daytrader.web.servlet.TradeWebContextListener.contextInitialized(javax.servlet.ServletContextEvent)"]
        assert backend.get_config_readers("project.artifactId") == [], "a declared key nothing reads has no readers"
