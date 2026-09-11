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
"""The view-dispatch layer (codeanalyzer-java 3.3.0, python-sdk#404) on **both** backends, offline.

The committed fixtures are 3.1.0 payloads and carry no ``view_dispatches``, so the layer is exercised
over **a1 with the layer injected**: three resolved edges and one unresolved record shaped exactly
as codeanalyzer-java 3.3.3 writes them on daytrader8 (``PingServlet2Jsp.doGet`` forwards to
``PingServlet2Jsp.jsp``; ``PingJDBCRead2JSP.doGet`` includes ``quoteDataPrimitive.jsp``; a forward to
``/servlet/PingServlet2ServletRcv`` is a URL, not a file), re-addressed onto the fixture's own
callable and artifact ids. The second include comes from ``PingServlet2Session2Entity2JSP.doGet``,
the fixture's other ``include`` site — a1 is a 3.1.0 run over 138 units and has no
``PingJDBCWrite2JSP``. The a1 payload as committed is the
*refusal* case: an analysis that predates the pass must not answer ``[]``.
"""
import json
from typing import Dict

import pytest

from cldk.analysis.java.backend import JavaAnalysisBackend
from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java.models import JViewDispatch, JViewDispatchUnresolved
from cldk.models.java.projections import JCallableOverview
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

from .test_java_addressing import _graph, _local

DO_GET = "doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse)"


def _site(app: dict, unit: str, type_name: str, method_name: str) -> str:
    """The ordinal id of the first ``call`` body node invoking ``method_name`` in ``doGet``. The
    unit is found by basename: the two include sites live in different ``web/prims`` packages."""
    path = next(k for k in app["symbol_table"] if k.endswith(f"/{unit}.java"))
    callable_ = app["symbol_table"][path]["types"][type_name]["callables"][DO_GET]
    for local_id, node in callable_["body"].items():
        if node.get("method_name") == method_name:
            return f"{callable_['id']}@{local_id}"
    raise AssertionError(f"no {method_name} call in {type_name}.doGet")


def _with_layer(payload: str) -> str:
    doc = json.loads(payload)
    app = doc["application"]
    doc["analyzer"]["version"] = "3.3.3"
    art = {p: a["id"] for p, a in app["artifacts"].items()}
    app["view_dispatches"] = sorted(
        [
            {"src": _site(app, "PingServlet2Jsp", "PingServlet2Jsp", "forward"), "dst": art["src/main/webapp/PingServlet2Jsp.jsp"], "via": "forward", "prov": ["literal"]},
            {"src": _site(app, "PingJDBCRead2JSP", "PingJDBCRead2JSP", "include"), "dst": art["src/main/webapp/quoteDataPrimitive.jsp"], "via": "include", "prov": ["literal"]},
            {"src": _site(app, "PingServlet2Session2Entity2JSP", "PingServlet2Session2Entity2JSP", "include"), "dst": art["src/main/webapp/quoteDataPrimitive.jsp"], "via": "include", "prov": ["literal"]},
        ],
        key=lambda d: (d["src"], d["dst"]),
    )
    app["view_dispatches_unresolved"] = [
        {
            "site": _site(app, "PingServlet2Servlet", "PingServlet2Servlet", "forward"),
            "callee": "can://java/daytrader8/@external/javax.servlet.RequestDispatcher/forward(javax.servlet.ServletRequest, javax.servlet.ServletResponse)",
            "target": "/servlet/PingServlet2ServletRcv",
            "via": "forward",
            "reason": "no-such-artifact",
            "prov": ["literal"],
        }
    ]
    return json.dumps(doc)


def _seed(kind: str, payload: str) -> JavaAnalysisBackend:
    backend = (_local if kind == "local" else _graph)(payload)
    if kind == "graph":
        backend._analyzer_version = (3, 3, 3)
    return backend


@pytest.fixture(scope="module", params=["local", "graph"])
def both(request, analysis_json):
    return _seed(request.param, _with_layer(analysis_json))


@pytest.fixture(scope="module", params=["local", "graph"])
def both_before(request, analysis_json):
    """a1 as committed: codeanalyzer-java 3.1.0, no view-dispatch pass."""
    backend = (_local if request.param == "local" else _graph)(analysis_json)
    if request.param == "graph":
        backend._analyzer_version = (3, 1, 0)
    return backend


def test_get_view_dispatches_carries_the_edges_and_their_mechanism(both):
    edges = both.get_view_dispatches()
    assert len(edges) == 3 and all(isinstance(e, JViewDispatch) for e in edges)
    assert [(e.via, tuple(e.prov)) for e in edges] == [("include", ("literal",)), ("include", ("literal",)), ("forward", ("literal",))] or sorted((e.via, tuple(e.prov)) for e in edges) == [("forward", ("literal",)), ("include", ("literal",)), ("include", ("literal",))]
    assert all(e.src.startswith(e.src.rpartition("@")[0] + "@") for e in edges), "src is a body-node id"
    assert {e.dst.rsplit("/", 1)[1] for e in edges} == {"PingServlet2Jsp.jsp", "quoteDataPrimitive.jsp"}
    assert [(e.src, e.dst) for e in edges] == sorted((e.src, e.dst) for e in edges), "sorted by (src, dst)"


def test_get_view_dispatches_filters_by_the_views_path_suffix(both):
    two = both.get_view_dispatches("quoteDataPrimitive.jsp")
    assert len(two) == 2 and all(e.dst.endswith("/quoteDataPrimitive.jsp") for e in two)
    assert len(both.get_view_dispatches("src/main/webapp/quoteDataPrimitive.jsp")) == 2, "a longer suffix still matches"
    assert both.get_view_dispatches("DataPrimitive.jsp") == [], "segment-aligned, never a substring"
    assert both.get_view_dispatches("no/such.jsp") == []


def test_get_view_dispatchers_resolves_the_sites_to_their_callables(both):
    dispatchers = both.get_view_dispatchers("quoteDataPrimitive.jsp")
    assert all(isinstance(d, JCallableOverview) for d in dispatchers)
    assert sorted(d.key for d in dispatchers) == [
        f"com.ibm.websphere.samples.daytrader.web.prims.PingJDBCRead2JSP.{DO_GET}",
        f"com.ibm.websphere.samples.daytrader.web.prims.ejb3.PingServlet2Session2Entity2JSP.{DO_GET}",
    ]
    assert [d.key for d in both.get_view_dispatchers("PingServlet2Jsp.jsp")] == [f"com.ibm.websphere.samples.daytrader.web.prims.PingServlet2Jsp.{DO_GET}"]
    assert both.get_view_dispatchers("no/such.jsp") == []


def test_get_unresolved_view_dispatches_keeps_the_url_visible_on_the_local_backend(both):
    if isinstance(both, JNeo4jBackend):
        # JSON-only: the projection has no node for a target that resolved to nothing, so the
        # graph backend refuses rather than answering the ambiguous empty.
        with pytest.raises(CodeanalyzerExecutionException, match="analysis.json"):
            both.get_unresolved_view_dispatches()
        return
    unresolved = both.get_unresolved_view_dispatches()
    assert len(unresolved) == 1 and isinstance(unresolved[0], JViewDispatchUnresolved)
    u = unresolved[0]
    assert (u.via, u.reason, u.target, u.prov) == ("forward", "no-such-artifact", "/servlet/PingServlet2ServletRcv", ["literal"])
    assert u.site.startswith(u.site.rpartition("@")[0] + "@")


def test_a_payload_without_the_pass_refuses_rather_than_answering_empty(both_before):
    for call in (both_before.get_view_dispatches, both_before.get_unresolved_view_dispatches, lambda: both_before.get_view_dispatchers("x.jsp")):
        with pytest.raises(CodeanalyzerExecutionException, match="3.3.0"):
            call()


def test_a_newer_payload_with_no_dispatches_answers_empty(analysis_json):
    doc = json.loads(analysis_json)
    doc["analyzer"]["version"] = "3.3.3"
    local = _local(json.dumps(doc))
    assert local.get_view_dispatches() == [] and local.get_unresolved_view_dispatches() == []
    assert local.get_view_dispatchers("x.jsp") == []


def test_the_layer_is_answered_once_for_both_backends():
    """One implementation on the contract for the two edge accessors; the graph backend overrides
    only the unresolved one, to refuse what the projection does not carry."""
    for name in ("get_view_dispatches", "get_view_dispatchers"):
        shared = getattr(JavaAnalysisBackend, name)
        for backend in (JCodeanalyzer, JNeo4jBackend):
            assert getattr(backend, name) is shared, f"{backend.__name__} overrides {name}"
    assert JCodeanalyzer.get_unresolved_view_dispatches is JavaAnalysisBackend.get_unresolved_view_dispatches
    assert JNeo4jBackend.get_unresolved_view_dispatches is not JavaAnalysisBackend.get_unresolved_view_dispatches
