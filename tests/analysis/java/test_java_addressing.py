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

"""The Java addressing surface (leg 3b, Task 1) on **both** backends, offline.

Seven accessors -- ``locate``, ``locate_many``, ``resolve_callable``, ``resolve_value``,
``get_source``, ``describe``, ``has_resolution_edges`` -- in Python's signatures, over the
committed v2 fixtures.

**Why both backends can be exercised without a server.** Everything but one statement is answered
from the canonical :class:`JApplication`, which :class:`JNeo4jBackend` rebuilds from the graph and
then queries with the *same* code the in-memory backend runs (the leg-3a architecture). So a
backend seeded through the ``_application`` cache seam -- exactly what
``test_java_neo4j_lookup_miss.py`` does -- exercises the real implementation, not a stand-in. The
one statement that does reach the driver (the per-callable body-node fetch ``locate`` needs,
because 3a's reconstruction carries the ``call`` body nodes only) is answered here by a fake
responder over the *same fixture*, and for real on 7691 by ``test_java_addressing_live.py``. Fake
rows prove the wiring; the live suite proves the graph.

Fixture rule (recorded in the plan): ``a1`` is the whole application at level 1 -- 138 units, the
initializers, the 99 implicit callables and the four anonymous classes -- and is what anything
pinning a **signature spelling** asserts against, because ``a4``'s spellings are an artifact of its
pruning. ``a4`` is the four-unit level-4 copy, and is what carries the ``formal_in`` vertices.
"""

from typing import Dict, List, Sequence, Tuple

import pytest

from cldk.analysis.commons.results import LocateResult, SliceNode
from cldk.analysis.java.backend import java_body_node_id
from cldk.analysis.java.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java.models import JAnalysis, JApplication
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

DIRECT_PKG = "com.ibm.websphere.samples.daytrader.impl.direct"
BEANS_PKG = "com.ibm.websphere.samples.daytrader.beans"
UTIL_PKG = "com.ibm.websphere.samples.daytrader.util"
PRIMS_PKG = "com.ibm.websphere.samples.daytrader.web.prims"

TRADE_DIRECT_FILE = f"src/main/java/{DIRECT_PKG.replace('.', '/')}/TradeDirect.java"
MARKET_BEAN_FILE = f"src/main/java/{BEANS_PKG.replace('.', '/')}/MarketSummaryDataBean.java"
TRADE_CONFIG_FILE = f"src/main/java/{UTIL_PKG.replace('.', '/')}/TradeConfig.java"

CANCEL_INT_BOOL = f"{DIRECT_PKG}.TradeDirect.cancelOrder(java.lang.Integer, boolean)"
CANCEL_CONN_INT = f"{DIRECT_PKG}.TradeDirect.cancelOrder(java.sql.Connection, java.lang.Integer)"

#: J-6's three shapes, as they are really spelled in a1.
INITIALIZER = f"{UTIL_PKG}.TradeConfig.<clinit>$0()"
IMPLICIT = f"{DIRECT_PKG}.TradeDirect.<init>()"
ANON = f"{PRIMS_PKG}.PingManagedThread.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse).$anon$0"
ANON_RUN = f"{ANON}.run()"


# ---- the two backends, over one fixture --------------------------------------------------------
def _local(payload: str) -> JCodeanalyzer:
    """A :class:`JCodeanalyzer` over a fixture payload, with the analyzer run skipped."""
    backend = JCodeanalyzer.__new__(JCodeanalyzer)
    backend.analysis = JAnalysis.model_validate_json(payload)
    backend.analysis_level = "system_dependency_graph"
    backend.analyzer_diagnostics = []
    backend.application = backend.analysis.application
    backend._call_graph = None
    backend._index()
    return backend


class _BodyNodeResponder:
    """The one statement the graph backend issues here: the per-callable body-node fetch. Answers
    it out of the same fixture, in the graph's own vocabulary (a global ``id``, ``kind`` and a
    line-only span), so the rows are shaped like the projection's rather than like the model's."""

    def __init__(self, application: JApplication) -> None:
        self.rows: Dict[str, List[Dict[str, object]]] = {}
        self.calls = 0
        for _, unit in application.symbol_table.items():
            for t in unit.types.values():
                self._walk(t)

    def _walk(self, t) -> None:
        for c in t.callables.values():
            self.rows[c.id] = [
                {"id": java_body_node_id(c.id, key), "kind": n.kind, "s": n.start_line if n.span else None, "e": n.end_line if n.span else None} for key, n in c.body.items()
            ]
            for local in c.types.values():
                self._walk(local)
        for nested in t.types.values():
            self._walk(nested)

    def __call__(self, query: str, params: Dict[str, object]) -> List[Dict[str, object]]:
        if "JBodyNode" not in query:
            return []
        self.calls += 1
        out: List[Dict[str, object]] = []
        for prefix in params.get("prefixes") or []:
            out.extend(self.rows.get(str(prefix)[:-1], []))
        return out


def _graph(payload: str) -> JNeo4jBackend:
    from tests.analysis.java.conftest import FakeDriver

    application = JAnalysis.model_validate_json(payload).application
    backend = JNeo4jBackend.__new__(JNeo4jBackend)
    backend.application_name = "daytrader8"
    backend._database = None
    backend._session_obj = None
    backend._call_graph = None
    backend.__dict__["_application"] = application
    backend._relationship_types = frozenset({"J_HAS_MODULE", "J_HAS_METHOD", "J_HAS_BODY_NODE", "J_CALLS", "J_RESOLVES_TO"})
    backend._has_resolution_edges = True
    backend._driver = FakeDriver(responder=_BodyNodeResponder(application))
    return backend


@pytest.fixture(scope="module", params=["local", "graph"])
def both(request, analysis_json):
    """Both backends over **a1** -- the whole application, at level 1."""
    return (_local if request.param == "local" else _graph)(analysis_json)


@pytest.fixture(scope="module", params=["local", "graph"])
def both_l4(request, analysis_json_a4):
    """Both backends over **a4** -- four units, at level 4, the only fixture with ``formal_in``."""
    return (_local if request.param == "local" else _graph)(analysis_json_a4)


# ---- the signatures ----------------------------------------------------------------------------
def test_the_seven_signatures_mirror_pythons():
    import inspect

    from cldk.analysis.java.backend import JavaAnalysisBackend
    from cldk.analysis.python.backend import PythonAnalysisBackend

    for name in ("locate", "locate_many", "resolve_callable", "resolve_value", "get_source", "describe"):
        assert str(inspect.signature(getattr(JavaAnalysisBackend, name))) == str(inspect.signature(getattr(PythonAnalysisBackend, name))), name
    assert isinstance(JavaAnalysisBackend.has_resolution_edges, property), "Python spells it a property; Java must too"


# ---- locate ------------------------------------------------------------------------------------
def test_locate_inside_a_callable(both):
    """``cancelOrder(Integer, boolean)`` spans 645-676 in daytrader8's TradeDirect."""
    found = both.locate(TRADE_DIRECT_FILE, 650)
    assert found.callable is not None
    assert found.callable.signature == "cancelOrder(java.lang.Integer, boolean)"
    assert found.callable.class_signature == f"{DIRECT_PKG}.TradeDirect"
    assert found.type is not None and found.type.signature == f"{DIRECT_PKG}.TradeDirect"
    assert found.module.path == TRADE_DIRECT_FILE
    assert found.module.module_name == DIRECT_PKG
    assert found.diagnostics == []
    assert found.span.start[0] <= 650 <= found.span.end[0]


def test_locate_at_module_scope_says_so(both):
    """Line 1 is the package declaration: a real position with no enclosing callable."""
    found = both.locate(TRADE_DIRECT_FILE, 1)
    assert found.callable is None and found.body is None
    assert [d.code for d in found.diagnostics][0] == "module_scope"


def test_locate_in_an_unanalysed_file_says_so(both):
    found = both.locate("src/main/java/com/example/NoSuchFile.java", 3)
    assert found.callable is None
    assert [d.code for d in found.diagnostics] == ["file_not_in_graph"]
    assert found.module.path == "src/main/java/com/example/NoSuchFile.java"


def test_locate_normalises_the_path_a_scanner_prints(both):
    assert both.locate("./" + TRADE_DIRECT_FILE, 650).callable is not None
    assert both.locate("/checkout/" + TRADE_DIRECT_FILE, 650).callable is not None


def test_locate_finds_the_body_node_and_it_round_trips(both):
    """A call site inside ``getMarketSummary`` -- both backends address it by the analyzer's own
    ``<callable id>@<body key>`` id, and ``node_id`` is ``body.id``."""
    found = next(r for line in range(600, 700) if (r := both.locate(TRADE_DIRECT_FILE, line)).body is not None)
    assert found.node_id == found.body.id
    assert found.body.id.startswith("can://java/daytrader8/")
    assert "@" in found.body.id


def test_locate_many_answers_in_input_order(both):
    positions = [(TRADE_DIRECT_FILE, 650), ("nope.java", 1), (TRADE_DIRECT_FILE, 1)]
    results = both.locate_many(positions)
    assert [r.module.path for r in results] == [TRADE_DIRECT_FILE, "nope.java", TRADE_DIRECT_FILE]
    assert [bool(r.callable) for r in results] == [True, False, False]


def test_locate_many_is_one_round_trip(analysis_json):
    """The bulk form exists to save round trips; N positions must not cost N statements."""
    backend = _graph(analysis_json)
    backend.locate_many([(TRADE_DIRECT_FILE, 650), (MARKET_BEAN_FILE, 60), (TRADE_CONFIG_FILE, 40)])
    assert backend._driver.responder.calls == 1


# ---- resolve_callable --------------------------------------------------------------------------
def test_resolve_callable_returns_the_j1_key(both):
    """The exact J-1 key wins outright: a1 is the *whole* application, where four types declare
    ``cancelOrder(java.lang.Integer, boolean)`` (the interface and its three implementations), so
    the parameter tail alone does not identify one — the tail splits overloads, not implementors."""
    node = both.resolve_callable(CANCEL_INT_BOOL)
    assert node.callable == CANCEL_INT_BOOL
    assert node.kind == "callable"
    assert node.name == "cancelOrder"
    assert node.file == TRADE_DIRECT_FILE
    assert node.ref.startswith("can://java/daytrader8/")
    assert "can://" not in node.callable


def test_resolve_callable_ambiguity_lists_the_overloads(both):
    """Two overloads of one class: the pair ``in_class=`` provably cannot split, so the advice must
    not offer it and must offer the full signature instead (J-3)."""
    with pytest.raises(AmbiguousName) as e:
        both.resolve_callable("cancelOrder", in_class=f"{DIRECT_PKG}.TradeDirect")
    assert e.value.candidates == sorted([CANCEL_INT_BOOL, CANCEL_CONN_INT])
    assert "in_class=" not in e.value.message
    assert "full signature" in e.value.message
    assert "can://" not in e.value.message


def test_resolve_callable_miss_names_only_what_missed(both):
    with pytest.raises(SelectorNotInGraph) as e:
        both.resolve_callable("cancelOrders")
    assert e.value.kind == "callable"
    assert e.value.missing == ["cancelOrders"]
    assert "did you mean" not in str(e.value).lower()


def test_resolve_callable_scoping_keywords(both):
    assert both.resolve_callable("cancelOrder(java.lang.Integer, boolean)", in_class=f"{DIRECT_PKG}.TradeDirect").callable == CANCEL_INT_BOOL
    assert both.resolve_callable("cancelOrder(java.lang.Integer, boolean)", in_module=f"{DIRECT_PKG}.TradeDirect").callable == CANCEL_INT_BOOL
    with pytest.raises(SelectorNotInGraph) as e:
        both.resolve_callable("cancelOrder", in_module="com.ibm.nosuch")
    assert e.value.kind == "in_module"


# ---- J-6: whatever the analyzer emits as a callable is addressable ------------------------------
def test_an_initializer_resolves_and_behaves_like_a_method(both):
    node = both.resolve_callable("<clinit>$0()")
    assert node.callable == INITIALIZER
    assert node.kind == "callable"
    assert node.line > 0
    assert both.get_source(node.callable)


def test_an_implicit_callable_resolves_but_has_no_span_or_source(both):
    node = both.resolve_callable("<init>()", in_class=f"{DIRECT_PKG}.TradeDirect")
    assert node.callable == IMPLICIT
    assert node.line == -1, "an implicit callable has no span; -1 is the model's 'not known'"
    with pytest.raises(KeyError) as e:
        both.get_source(node.callable)
    assert "implicit" in str(e.value)


def test_an_anonymous_class_carries_its_declaring_callable(both):
    node = both.resolve_callable("onResult")
    assert ".$anon$0." in node.callable
    assert both.resolve_callable("run", in_class=ANON).callable == ANON_RUN


# ---- resolve_value -----------------------------------------------------------------------------
def test_resolve_value_names_a_parameter(both_l4):
    node = both_l4.resolve_value("openTSIA", within="MarketSummaryDataBean.setOpenTSIA")
    assert node.kind == "parameter"
    assert node.name == "openTSIA"
    assert node.callable.endswith("setOpenTSIA(java.math.BigDecimal)")
    assert node.ref.endswith("@formal_in:0")
    assert "formal_in" not in (node.name or "")


def test_resolve_value_miss_names_the_value(both_l4):
    with pytest.raises(SelectorNotInGraph) as e:
        both_l4.resolve_value("nosuchparam", within="MarketSummaryDataBean.setOpenTSIA")
    assert e.value.kind == "value"


def test_resolve_value_reraises_an_ambiguous_within_in_its_own_terms(both_l4):
    with pytest.raises(AmbiguousName) as e:
        both_l4.resolve_value("x", within="toString")
    assert "within=" in e.value.message


# ---- get_source --------------------------------------------------------------------------------
def test_get_source_takes_the_name_or_the_ref(both):
    node = both.resolve_callable(CANCEL_INT_BOOL)
    assert both.get_source(node.callable) == both.get_source(node.ref)
    assert "cancelOrder" in both.get_source(node.callable)


def test_get_source_miss_names_the_node(both):
    with pytest.raises(KeyError) as e:
        both.get_source("no.such.Thing.m()")
    assert "no.such.Thing.m()" in str(e.value)


# ---- describe ----------------------------------------------------------------------------------
def test_describe_hydrates_a_callable_on_both_backends(both):
    node = both.resolve_callable(CANCEL_INT_BOOL)
    described = both.describe([node])
    assert len(described) == 1 and described[0].source
    assert both.describe([]) == []


def test_describe_accepts_a_locate_result(both):
    """A ``locate`` result is addressed by its ``node_id``, so it hydrates only when the position
    landed on a body node — a module-scope or declaration-line result carries no address at all,
    and :func:`as_slice_node` says so rather than guessing one from the file and line."""
    found = next(r for line in range(600, 700) if (r := both.locate(TRADE_DIRECT_FILE, line)).body is not None)
    described = both.describe([found])
    assert described[0].ref == found.node_id
    assert described[0].kind == found.body.kind


def test_a_body_node_hydrates_only_where_the_text_exists(analysis_json):
    """The documented ``get_source`` divergence, on the finer grain: the local backend slices the
    statement out of the module's real text; the graph carries none below callable granularity, so
    the position is *found* and its source is ``None`` — never the enclosing declaration."""
    local, graph = _local(analysis_json), _graph(analysis_json)
    found = next(r for line in range(600, 700) if (r := local.locate(TRADE_DIRECT_FILE, line)).body is not None)
    assert local.describe([found])[0].source
    assert graph.describe([found])[0].source is None
    assert local.get_source(found.node_id)
    with pytest.raises(KeyError) as e:
        graph.get_source(found.node_id)
    assert "no recoverable source" in str(e.value)


def test_describe_raises_on_a_ref_naming_nothing(both):
    stale = SliceNode(file="a.java", line=1, callable="a.B.c()", kind="callable", name="c", ref="can://java/other/x")
    with pytest.raises(KeyError):
        both.describe([stale])


def test_describe_needs_an_address(both):
    with pytest.raises(TypeError):
        both.describe([object()])


# ---- has_resolution_edges ----------------------------------------------------------------------
def test_has_resolution_edges(both):
    assert both.has_resolution_edges is True


def test_the_graph_backend_reports_a_graph_without_resolution_edges(analysis_json):
    backend = _graph(analysis_json)
    backend._has_resolution_edges = False
    assert backend.has_resolution_edges is False


# ---- both backends answer identically ----------------------------------------------------------
def test_the_two_backends_agree_on_every_answer_here(analysis_json):
    local, graph = _local(analysis_json), _graph(analysis_json)
    positions: Sequence[Tuple[str, int]] = [(TRADE_DIRECT_FILE, 650), (TRADE_DIRECT_FILE, 1), ("nope.java", 9), (MARKET_BEAN_FILE, 60)]
    for a, b in zip(local.locate_many(positions), graph.locate_many(positions)):
        assert (a.callable, a.type, a.module.path, a.module.module_name) == (b.callable, b.type, b.module.path, b.module.module_name)
        assert [d.code for d in a.diagnostics] == [d.code for d in b.diagnostics]
        assert (a.node_id, a.body.kind if a.body else None) == (b.node_id, b.body.kind if b.body else None)
        assert a.span.start[0] == b.span.start[0] and a.span.end[0] == b.span.end[0]
    for name in (CANCEL_INT_BOOL, "<clinit>$0()", "onResult"):
        assert local.resolve_callable(name) == graph.resolve_callable(name)
    for name in ("cancelOrder", "cancelOrders"):
        with pytest.raises(Exception) as la:
            local.resolve_callable(name)
        with pytest.raises(Exception) as ga:
            graph.resolve_callable(name)
        assert type(la.value) is type(ga.value) and str(la.value) == str(ga.value)
