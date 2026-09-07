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

"""Entrypoints, the bulk projections, the artifact layer and the J-7 leaf accessors (leg 3b,
Task 3), on **both** backends, offline.

Same harness as ``test_java_addressing.py`` and for the same reason: leg 3a made
:class:`JNeo4jBackend` rebuild the canonical :class:`JApplication` and answer from it, so a backend
seeded through the ``_application`` cache seam runs the shipped code. **Nothing in this task issues
Cypher of its own except one statement** -- the ``:JExternal`` projection -- so what is asserted
here is the whole policy, and the live suite proves that one statement against a real graph.

Every expected number is measured off the fixture (or off the reference graph, for the counts the
fixture cannot carry), never off the implementation:

* a1 (138 units, level 1): 1,216 callables, **133** marked ``is_entrypoint``, **66** types marked
  ``is_entrypoint_class``, 136 classes / 10 annotations / **3 interfaces** / 0 enums / 0 records,
  and 807 annotation uses -- of which ``Override``'s 328 and ``Inject``'s 12 are on **callables**
  and ``Trace``'s 16 and ``WebServlet``'s 53 on **types**, which is why the callable-level filter
  sees the first two and not the last two.
* ThingsBoard, which is where the enum and record kinds exist at all (594 interfaces, 192 enums,
  35 records) -- the live suite's job, not this one's.
"""

import inspect
from typing import Dict, List

import pytest

from cldk.analysis.commons.results import EntrypointCoverage
from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.models.java.models import JCallSite, JEnumConstant, JType
from cldk.models.java.projections import JCallableOverview, JClassOverview
from cldk.utils.exceptions import SelectorNotInGraph
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer

from .test_java_addressing import _graph, _local

DIRECT_PKG = "com.ibm.websphere.samples.daytrader.impl.direct"
UTIL_PKG = "com.ibm.websphere.samples.daytrader.util"
PRIMS_PKG = "com.ibm.websphere.samples.daytrader.web.prims"

CANCEL_INT_BOOL = f"{DIRECT_PKG}.TradeDirect.cancelOrder(java.lang.Integer, boolean)"
IMPLICIT = f"{DIRECT_PKG}.TradeDirect.<init>()"
INITIALIZER = f"{UTIL_PKG}.TradeConfig.<clinit>$0()"
TRADE_DIRECT_FILE = f"src/main/java/{DIRECT_PKG.replace('.', '/')}/TradeDirect.java"


@pytest.fixture(scope="module", params=["local", "graph"])
def both(request, analysis_json):
    """Both backends over **a1** -- the whole application, at level 1."""
    return (_local if request.param == "local" else _graph)(analysis_json)


@pytest.fixture(scope="module", params=["local", "graph"])
def both_l4(request, analysis_json_a4):
    return (_local if request.param == "local" else _graph)(analysis_json_a4)


# ---- the signatures mirror Python's ------------------------------------------------------------
def test_the_bulk_accessors_mirror_pythons_signatures():
    from cldk.analysis.python.backend import PythonAnalysisBackend

    for name in (
        "get_callables_overview",
        "get_method_bodies",
        "get_decorated_callables",
        "get_entrypoints",
        "get_entrypoint_classes",
        "get_entrypoint_coverage",
        "get_callsites_for",
        "get_external_symbols",
        "get_config_readers",
    ):
        java = inspect.signature(getattr(JavaAnalysisBackend, name))
        python = inspect.signature(getattr(PythonAnalysisBackend, name))
        assert list(java.parameters) == list(python.parameters), name
        assert [p.default for p in java.parameters.values()] == [p.default for p in python.parameters.values()], name


# ---- entrypoints, honestly (J-4) ---------------------------------------------------------------
def test_get_entrypoints_projects_the_analyzers_own_mark(both):
    entrypoints = both.get_entrypoints()
    assert len(entrypoints) == 133, "a1 carries 133 callables with is_entrypoint"
    assert all(isinstance(e, JCallableOverview) and e.is_entrypoint for e in entrypoints)
    assert {e.key for e in entrypoints} <= {o.key for o in both.get_callables_overview()}
    assert all("can://" not in e.key and "can://" not in e.owner for e in entrypoints)


def test_get_entrypoints_agrees_with_the_legacy_accessor(both):
    """``get_all_entry_point_methods`` keeps its 1.x shape (J-4); the two must not disagree about
    which callables are marked."""
    legacy = {f"{klass}.{sig}" for klass, methods in both.get_all_entry_point_methods().items() for sig in methods}
    assert {e.key for e in both.get_entrypoints()} == legacy


def test_get_entrypoint_classes_is_the_class_level_sibling(both):
    classes = both.get_entrypoint_classes()
    assert len(classes) == 66, "a1 carries 66 types with is_entrypoint_class"
    assert all(isinstance(c, JClassOverview) and c.is_entrypoint_class for c in classes)
    assert {c.qualified_name for c in classes} == set(both.get_all_entry_point_classes())
    assert all(c.path in both.get_symbol_table() for c in classes)


def test_get_entrypoint_coverage_reads_the_report(both):
    """codeanalyzer-java 3.1.0 (codeanalyzer-java#235) emits the entrypoint pass's own coverage
    record, so the accessor reads it rather than saying there is none. Measured on a1: three of the
    five shipped rulesets matched, nothing unresolved, no errors. It still never synthesises one
    out of the ``is_entrypoint`` booleans (J-4, D7) -- the counts below are 66/133 and appear
    nowhere in the report."""
    coverage = both.get_entrypoint_coverage()
    assert isinstance(coverage, EntrypointCoverage)
    assert coverage.diagnostics == []
    assert coverage.frameworks_detected == ["jakarta", "jaxrs", "spring"]
    assert coverage.rulesets == ["jakarta", "struts", "spring", "camel", "jaxrs"]
    assert coverage.unresolved == {} and coverage.errors == []


def test_the_coverage_diagnostic_is_the_same_on_both_backends(analysis_json):
    local, graph = _local(analysis_json), _graph(analysis_json)
    assert local.get_entrypoint_coverage() == graph.get_entrypoint_coverage()


# ---- the bulk projections ----------------------------------------------------------------------
def test_get_callables_overview_covers_every_callable_the_analyzer_emitted(both):
    """J-6: whatever the analyzer emits as a callable is addressable, so the projection is the
    addressing domain exactly -- initializers, implicit constructors and the callables of local and
    anonymous classes included."""
    overview = both.get_callables_overview()
    assert len(overview) == 1216
    assert {o.key for o in overview} == set(both._addressing.by_key)
    keys = {o.key: o for o in overview}
    assert keys[IMPLICIT].is_implicit and keys[IMPLICIT].start_line == -1, "an implicit callable carries no span"
    assert keys[INITIALIZER].kind == "initializer"
    cancel = keys[CANCEL_INT_BOOL]
    assert (cancel.signature, cancel.name, cancel.kind) == ("cancelOrder(java.lang.Integer, boolean)", "cancelOrder", "method")
    assert cancel.owner == f"{DIRECT_PKG}.TradeDirect" and cancel.owner_kind == "class"
    assert cancel.path == TRADE_DIRECT_FILE and (cancel.start_line, cancel.end_line) == (646, 665)


def test_get_method_bodies_omits_what_has_no_source_text(both):
    bodies = both.get_method_bodies([CANCEL_INT_BOOL, IMPLICIT, "no.such.Type.m()"])
    assert set(bodies) == {CANCEL_INT_BOOL}, "the implicit callable has no text and the miss is omitted, not None"
    assert isinstance(bodies[CANCEL_INT_BOOL], str) and bodies[CANCEL_INT_BOOL].strip()
    overview = both.get_callables_overview()
    everything = both.get_method_bodies([o.key for o in overview])
    assert all(isinstance(v, str) and v for v in everything.values())
    # The omitted set is exactly the 99 implicit callables -- **not** the 101 whose ``declaration``
    # is ``None``. The two ``<clinit>$N()`` initializers are in the second set and not the first:
    # they have no declaration text to slice but they do carry a body block, so they come back.
    assert len(everything) == 1117
    assert {o.key for o in overview} - set(everything) == {o.key for o in overview if o.is_implicit}
    assert all(f"{o.owner}.{o.signature}" in everything for o in overview if "<clinit>" in o.signature)


def test_get_method_bodies_is_keyed_by_the_key_get_callables_overview_hands_back(both):
    keys = [o.key for o in both.get_callables_overview() if not o.is_implicit][:50]
    assert set(both.get_method_bodies(keys)) <= set(keys)


def test_get_decorated_callables_matches_a_marker_three_ways(both):
    """J-5: simple name, a leading ``@`` ignored, or an exact fully-qualified name."""
    plain = {o.key for o in both.get_decorated_callables(["Override"])}
    assert len(plain) == 328, "a1 carries 328 @Override annotation uses"
    assert plain == {o.key for o in both.get_decorated_callables(["@Override"])}
    assert plain == {o.key for o in both.get_decorated_callables(["java.lang.Override"])}
    assert all("Override" in o.decorators for o in both.get_decorated_callables(["Override"]))
    # The 16 ``@Trace`` uses in a1 are all on **types**, so a callable filter must not see them --
    # ``get_decorated_callables`` is the callable-level projection, not "anything annotated".
    assert both.get_decorated_callables(["Trace"]) == []


def test_get_decorated_callables_unions_its_markers_and_never_guesses(both):
    inject = {o.key for o in both.get_decorated_callables(["Inject"])}
    override = {o.key for o in both.get_decorated_callables(["Override"])}
    assert len(inject) == 12, "a1 carries 12 @Inject annotation uses on callables"
    assert {o.key for o in both.get_decorated_callables(["Inject", "Override"])} == inject | override
    assert both.get_decorated_callables(["Overide"]) == [], "no fuzzy matching, anywhere (E8)"
    assert both.get_decorated_callables([]) == []


def test_get_callsites_for_keys_by_the_callables_that_exist(both):
    sites = both.get_callsites_for([CANCEL_INT_BOOL, IMPLICIT, "no.such.Type.m()"])
    assert set(sites) == {CANCEL_INT_BOOL, IMPLICIT}, "a miss is omitted; a callable with no call sites is an empty list"
    assert sites[IMPLICIT] == []
    assert all(isinstance(s, JCallSite) for s in sites[CANCEL_INT_BOOL])
    assert sites[CANCEL_INT_BOOL] == both._addressing.by_key[CANCEL_INT_BOOL].callable.call_sites


# ---- external symbols: the one thing the two sources were not asked the same question about -----
def test_get_external_symbols_refuses_when_the_run_did_not_home_them(both):
    """``external_symbols`` is emitted only under ``--external-calls``, which ``--emit neo4j``
    forces and a plain ``-a N`` run does not, so ``None`` on the application means "this analysis
    was never asked", which is not "this project calls nothing outside itself" (D7)."""
    with pytest.raises(CodeanalyzerExecutionException) as excinfo:
        both.get_external_symbols()
    assert "--external-calls" in str(excinfo.value) and "can://" not in str(excinfo.value)


def test_get_external_symbols_answers_when_the_source_homed_them(analysis_json):
    from cldk.models.java.models import JExternalSymbol

    backend = _local(analysis_json)
    symbol = JExternalSymbol(kind="method", signature="println(java.lang.String)", declaring_type="java.io.PrintStream")
    backend.application.external_symbols = {"can://java/daytrader8/@external/java.io.PrintStream/println(java.lang.String)": symbol}
    assert backend.get_external_symbols() == {"can://java/daytrader8/@external/java.io.PrintStream/println(java.lang.String)": symbol}
    backend.application.external_symbols = None


# ---- the J-7 leaf accessors --------------------------------------------------------------------
def test_the_leaf_accessors_select_by_the_analyzers_own_kind(both):
    """daytrader8 has three interfaces and **no** enum or record at all, which is why the counts
    that matter are measured on ThingsBoard by the live suite (594 / 192 / 35)."""
    interfaces = both.get_interfaces()
    assert len(interfaces) == 3 and all(isinstance(t, JType) and t.kind == "interface" for t in interfaces.values())
    assert set(interfaces) <= set(both.get_all_classes())
    assert both.get_enums() == {} and both.get_records() == {}
    # 149 types in three kinds; the three accessors partition the two that exist and never overlap.
    kinds = {t.kind for t in both.get_all_classes().values()}
    assert kinds == {"class", "interface", "annotation"}, "an annotation type has no leaf accessor and stays in get_all_classes (J-7)"


def test_get_enum_members_names_what_missed_rather_than_answering_empty(both):
    """An enum with no constants and a name that is not an enum are different answers (D7); the
    second raises, naming the value the caller wrote and nothing else (E8)."""
    with pytest.raises(SelectorNotInGraph) as excinfo:
        both.get_enum_members(f"{DIRECT_PKG}.TradeDirect")
    assert excinfo.value.kind == "enum" and excinfo.value.missing == [f"{DIRECT_PKG}.TradeDirect"]
    with pytest.raises(SelectorNotInGraph):
        both.get_enum_members("no.such.Enum")


def test_get_enum_members_returns_the_constants_of_a_real_enum(both):
    """Synthesised, because daytrader8 declares no enum: the accessor reads ``JType.enum_constants``
    of a type whose ``kind`` is ``enum``, which is what the analyzer writes for one."""
    enum = JType(
        id="can://java/daytrader8/src/main/java/x/Color.java/Color",
        kind="enum",
        span={"start": (1, 1), "end": (4, 1), "bytes": (0, 10)},
        enum_constants=[JEnumConstant(name="RED"), JEnumConstant(name="GREEN")],
    )
    both._types["x.Color"] = enum
    try:
        assert [c.name for c in both.get_enum_members("x.Color")] == ["RED", "GREEN"]
        assert both.get_enums() == {"x.Color": enum}
    finally:
        del both._types["x.Color"]


# ---- the code-to-config layer (codeanalyzer-java 3.1.0) ----------------------------------------
def test_get_config_uses_carries_the_resolved_edges_and_their_tier(both):
    """13 edges on a1, every one of them ``["literal"]`` -- a string literal at the call site
    (codeanalyzer-java#233). ``prov`` is surfaced, not flattened: the dataflow tier (#237) is a
    weaker answer and has to stay distinguishable from this one."""
    uses = both.get_config_uses()
    assert len(uses) == 13
    assert {tuple(u.prov) for u in uses} == {("literal",)}
    assert {u.dst.rpartition("@key/")[2] for u in uses} == {
        "displayOrderAlerts",
        "listQuotePriceChangeFrequency",
        "longRun",
        "marketSummaryInterval",
        "maxQuotes",
        "maxUsers",
        "orderProcessingMode",
        "primIterations",
        "publishQuotePriceChange",
        "runtimeMode",
        "webInterface",
    }
    assert all(u.src.startswith(u.src.rpartition("@")[0] + "@") for u in uses), "src is a body-node id"


def test_get_config_uses_filters_by_key_exactly(both):
    """``key`` is matched against the declared key's own ``key``, never fuzzily (E8), and a key
    nothing reads is an empty list rather than an unfiltered one."""
    one = both.get_config_uses("maxUsers")
    assert len(one) == 2 and all(u.dst.endswith("@key/maxUsers") for u in one), "two call sites read maxUsers"
    assert both.get_config_uses("maxuser") == [] and both.get_config_uses("project.artifactId") == []


def test_get_config_readers_resolves_the_edges_to_their_callables(both):
    """All 13 of a1's reads are in one callable, so the 13 edges resolve to **one** overview: a
    callable reading a key at several call sites appears once."""
    readers = both.get_config_readers("maxUsers")
    assert [r.key for r in readers] == ["com.ibm.websphere.samples.daytrader.web.servlet.TradeWebContextListener.contextInitialized(javax.servlet.ServletContextEvent)"]
    assert all(isinstance(r, JCallableOverview) for r in readers)
    assert both.get_config_readers("project.artifactId") == [], "a declared key nothing reads has no readers"
    assert both.get_config_readers("no.such.key") == []


def test_get_unresolved_config_reads_keeps_the_untraceable_ones_visible(both):
    """16 reads on a1 whose key matched no declared key -- every one of them a decoded literal
    (``reason="undefined-key"``, the environment variables ``System.getenv`` reads), so ``key``
    carries the text. ``prov`` is every tier *attempted*: a1 is a level-1 analysis, where there is
    no DDG for the dataflow tier to run over, so it is ``["literal"]`` alone -- on the level-4
    reference graph the same reads carry ``["literal", "dataflow"]``."""
    reads = both.get_unresolved_config_reads()
    assert len(reads) == 16
    assert {r.reason for r in reads} == {"undefined-key"}
    assert {tuple(r.prov) for r in reads} == {("literal",)}
    assert {r.key for r in reads} == {
        "DISPLAY_ORDER_ALERTS",
        "LIST_QUOTE_PRICE_CHANGE_FREQUENCY",
        "MAX_QUOTES",
        "MAX_USERS",
        "ORDER_PROCESSING_MODE",
        "PUBLISH_QUOTES",
        "RUNTIME_MODE",
        "WEB_INTERFACE",
    }
    assert all(r.site and r.callee.startswith("can://java/daytrader8/@external/") for r in reads)


def test_a_clean_run_that_reads_nothing_is_an_empty_answer_and_not_a_refusal(both_l4):
    """a4 is a level-4 3.1.0 analysis of a pruned tree that reads no configuration at all. It
    carries the entrypoint report and **neither config key** -- the analyzer writes those two only
    when non-empty -- which is exactly why the overlay probe cannot be the config layer's own
    absence. The three accessors answer empty here; the next test is what refusing looks like."""
    assert both_l4.get_config_uses() == []
    assert both_l4.get_unresolved_config_reads() == []
    assert both_l4.get_config_readers("maxUsers") == []
    assert both_l4.get_entrypoint_coverage().frameworks_detected == ["jakarta"]


def test_an_analysis_without_the_overlays_refuses_rather_than_answering_empty(analysis_json):
    """The refusal, measured from the data and never from a version string (the same ruling as the
    port probe). A 3.0.x payload and a 3.0.x graph carry none of the three overlays, and both are
    still servable -- a cached ``analysis.json`` at a sufficient ``max_level`` is reused whatever
    wrote it, and the Neo4j floor is 3.0.1 -- so answering ``[]`` would say "this application reads
    no configuration" where the truth is that nothing looked."""
    for backend in (_local(analysis_json), _graph(analysis_json)):
        stripped = backend.get_application_view().model_copy(update={"entrypoint_report": None, "config_uses": None, "config_reads_unresolved": None})
        # Each backend's own seam: the in-memory one holds the application on an attribute, the
        # graph one behind the ``_application`` cache its ``application`` property reads.
        backend.__dict__["application" if isinstance(backend, JCodeanalyzer) else "_application"] = stripped
        for call in (backend.get_config_uses, backend.get_unresolved_config_reads, lambda: backend.get_config_readers("maxUsers")):
            with pytest.raises(CodeanalyzerExecutionException) as excinfo:
                call()
            message = str(excinfo.value)
            assert "daytrader8" in message and "3.1.0" in message and "can://" not in message
        # The entrypoint report is the same absence, reported rather than raised -- the shared
        # model's own vocabulary, as a Python graph without the report uses.
        coverage = backend.get_entrypoint_coverage()
        assert [d.code for d in coverage.diagnostics] == ["entrypoint_report_unavailable"]
        assert coverage.frameworks_detected == [] and coverage.rulesets == [] and coverage.unresolved == {} and coverage.errors == []
        assert coverage.diagnostics[0].suggestions == [] and "can://" not in coverage.diagnostics[0].message
        # get_config_keys() is unaffected: what a config artifact declares is read at every
        # generation, and only the code-to-config edges are the 3.1.0 addition.
        assert len(backend.get_config_keys()) == 336


# ---- the artifact layer reaches the facade ------------------------------------------------------
def test_the_artifact_five_are_the_backends_own_answers(both):
    artifacts = both.get_artifacts()
    assert len(artifacts) == 235 and "pom.xml" in artifacts
    assert len(both.get_dependencies()) == 4
    assert both.get_dependencies(ecosystem="pypi") == [], "a filter that matches nothing is empty, not unfiltered"
    keys = both.get_config_keys()
    assert keys and all(k.startswith(tuple(artifacts)) and "@key/" in k for k in keys), "the key is artifact-relative (python-sdk#346 keeps it that way)"
    assert len(both.get_unresolved_config_reads()) == 16


def test_the_l4_fixture_answers_the_same_shapes(both_l4):
    assert len(both_l4.get_callables_overview()) == 128
    assert len(both_l4.get_entrypoints()) == 13
    assert both_l4.get_entrypoint_classes() == []
    assert both_l4.get_interfaces() == {} and both_l4.get_enums() == {} and both_l4.get_records() == {}


# ---- the facade delegates, it does not reimplement ------------------------------------------------
def test_the_facade_delegates_every_task_three_accessor():
    from unittest.mock import MagicMock

    from cldk.analysis.java.java_analysis import JavaAnalysis

    facade = JavaAnalysis.__new__(JavaAnalysis)
    facade.backend = MagicMock(spec=JavaAnalysisBackend)
    calls: Dict[str, tuple] = {
        "get_callables_overview": (),
        "get_method_bodies": ([CANCEL_INT_BOOL],),
        "get_decorated_callables": (["Override"],),
        "get_entrypoints": (),
        "get_entrypoint_classes": (),
        "get_entrypoint_coverage": (),
        "get_callsites_for": ([CANCEL_INT_BOOL],),
        "get_external_symbols": (),
        "get_artifacts": (),
        "get_config_keys": (),
        "get_unresolved_config_reads": (),
        "get_interfaces": (),
        "get_enums": (),
        "get_enum_members": ("x.Color",),
        "get_records": (),
    }
    for name, args in calls.items():
        assert getattr(facade, name)(*args) is getattr(facade.backend, name).return_value, name
        getattr(facade.backend, name).assert_called_once_with(*args)
    assert facade.get_config_uses("k") is facade.backend.get_config_uses.return_value
    facade.backend.get_config_uses.assert_called_once_with("k")
    assert facade.get_config_readers("k") is facade.backend.get_config_readers.return_value
    assert facade.get_dependencies(direct_only=True) is facade.backend.get_dependencies.return_value
    facade.backend.get_dependencies.assert_called_once_with(direct_only=True, ecosystem=None, declared_in=None)


def test_overviews_carry_no_can_uri_and_no_ordinal(both):
    """E6/E7 on the two projection models: nothing here is a ``can://`` id or an ordinal."""
    for overview in both.get_callables_overview()[:200] + both.get_entrypoints():
        assert "can://" not in overview.model_dump_json()
    for klass in both.get_entrypoint_classes():
        assert "can://" not in klass.model_dump_json()


def test_the_projection_models_forbid_extra_fields():
    from pydantic import ValidationError

    for model in (JCallableOverview, JClassOverview):
        assert model.model_config.get("extra") == "forbid", model.__name__
    with pytest.raises(ValidationError):
        JClassOverview(qualified_name="a.B", name="B", kind="class", path="a/B.java", start_line=1, end_line=2, nope=1)
