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

"""The six 1.x accessors #366 implemented, on **both** backends, offline.

``get_imports``, ``get_variables``, ``get_class_hierarchy``, ``get_methods_with_annotations``,
``get_call_targets`` and ``get_calling_lines`` raised ``NotImplementedError`` through leg 3; the
data they need was on the graph the whole time. Each is implemented **once**, on
:class:`~cldk.analysis.java.backend.JavaAnalysisBackend`, over accessors both backends already
answer — so this suite runs the shipped code twice, through the ``test_java_addressing.py``
harness, and **no new Cypher exists to audit**: the projection's own reconstruction already reads
``J_IMPORTS``, ``J_DECLARES_VAR``, ``J_EXTENDS``/``J_IMPLEMENTS``, ``J_ANNOTATED_BY`` and
``J_CALLS`` into the pydantic models.

Every expected number is measured off the fixtures, never off the implementation:

* **a1** (138 units, level 1): 268 distinct import targets, 1,216 callables of which 235 declare a
  local (854 locals in all), 170 types in the hierarchy joined by 103 edges (58 ``EXTENDS``, 45
  ``IMPLEMENTS``) of which 21 endpoints are outside the project and 43 types are isolated, 328
  callables annotated ``@Override``, and 53 of ``TradeDirect``'s declared names actually called.
  Level 1 carries **no call graph**, which is why ``get_calling_lines`` is exercised on a4.
* **a4** (4 units, level 4): a 100-node / 247-edge call graph, 36 call lines to ``getStatement``
  and 4 to ``cancelOrder``.

The last test compares the two backends field for field rather than relying on the parametrisation
to compare them through constants.
"""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.java.java_analysis import JavaAnalysis

from .test_java_addressing import _graph, _local

DIRECT_PKG = "com.ibm.websphere.samples.daytrader.impl.direct"
BEANS_PKG = "com.ibm.websphere.samples.daytrader.beans"
PRIMS_PKG = "com.ibm.websphere.samples.daytrader.web.prims"

TRADE_DIRECT = f"{DIRECT_PKG}.TradeDirect"
TRADE_SERVICES = "com.ibm.websphere.samples.daytrader.interfaces.TradeServices"
PING_SERVLET = f"{PRIMS_PKG}.PingServlet"
GET_STATEMENT = "getStatement"


@pytest.fixture(scope="module", params=["local", "graph"])
def both(request, analysis_json):
    """Both backends over **a1** — the whole application, at level 1."""
    return (_local if request.param == "local" else _graph)(analysis_json)


@pytest.fixture(scope="module", params=["local", "graph"])
def both_l4(request, analysis_json_a4):
    """Both backends over **a4** — four units at level 4, the fixture with a call graph."""
    return (_local if request.param == "local" else _graph)(analysis_json_a4)


# ---- get_imports: a set, and why -----------------------------------------------------------
def test_get_imports_is_the_distinct_sorted_set_of_import_targets(both):
    imports = both.get_imports()
    assert len(imports) == 268, "a1's distinct import targets"
    assert imports == sorted(set(imports)), "sorted and distinct"
    assert all(isinstance(i, str) for i in imports)


def test_get_imports_is_the_union_of_every_units_own_imports(both):
    """The set is not narrower than the per-file records: **file order is what is dropped**, not
    imports. The projection aggregates a module's imports of one target onto a single ``J_IMPORTS``
    edge, so order within a file is unrecoverable there and a list preserving it locally would be
    one the two backends disagree about."""
    per_file = {i.path for unit in both.get_symbol_table().values() for i in unit.import_declarations}
    assert set(both.get_imports()) == per_file
    assert sum(len(unit.import_declarations) for unit in both.get_symbol_table().values()) > len(per_file), "the same target is imported by several files"


# ---- get_variables: locals, keyed as the call graph keys ------------------------------------
def test_get_variables_keys_every_callable_by_its_j1_key(both):
    variables = both.get_variables()
    keys = {f"{klass}.{sig}" for klass, methods in both.get_all_methods_in_application().items() for sig in methods}
    assert set(variables) == keys, "one entry per callable, so an absent key is not an empty answer (D7)"
    assert len(variables) == 1216
    assert all("can://" not in key for key in variables)


def test_get_variables_reports_the_declares_var_layer(both):
    variables = both.get_variables()
    declaring = {key: v for key, v in variables.items() if v}
    assert len(declaring) == 235, "a1's callables that declare a local"
    assert sum(len(v) for v in variables.values()) == 854, "a1's local variable declarations"
    sample = variables[f"{BEANS_PKG}.MarketSummaryDataBean.toString()"]
    assert [v.name for v in sample] == ["ret", "it", "quoteData", "quoteData"]
    assert all(v.type for v in sample)


def test_get_variables_is_not_fields_and_not_parameters(both):
    """Three different things with three different homes; this one is ``J_DECLARES_VAR`` only."""
    variables = both.get_variables()
    fields = {f.name for f in both.get_all_fields(f"{BEANS_PKG}.MarketSummaryDataBean")}
    locals_ = {v.name for entry in variables.values() for v in entry}
    assert "summaryDate" in fields and "summaryDate" not in locals_


# ---- get_class_hierarchy ---------------------------------------------------------------------
def test_get_class_hierarchy_is_a_digraph_of_declared_types(both):
    graph = both.get_class_hierarchy()
    assert isinstance(graph, nx.DiGraph)
    assert set(both.get_all_classes()) <= set(graph.nodes), "every declared type is a node, isolated ones included"
    assert graph.number_of_nodes() == 170 and graph.number_of_edges() == 103
    assert sum(1 for n in graph.nodes if graph.degree(n) == 0) == 43


def test_get_class_hierarchy_keeps_extends_and_implements_apart(both):
    """Java projects the two as separate relationship types (``J_EXTENDS`` 1,197 / ``J_IMPLEMENTS``
    959 on the reference graph); collapsing them here would throw that away."""
    graph = both.get_class_hierarchy()
    kinds = [d["type"] for _, _, d in graph.edges(data=True)]
    assert kinds.count("EXTENDS") == 58 and kinds.count("IMPLEMENTS") == 45
    assert graph.edges[TRADE_DIRECT, TRADE_SERVICES]["type"] == "IMPLEMENTS"
    assert graph.edges[PING_SERVLET, "javax.servlet.http.HttpServlet"]["type"] == "EXTENDS"


def test_get_class_hierarchy_agrees_with_the_per_class_accessors(both):
    for name in (TRADE_DIRECT, PING_SERVLET, f"{BEANS_PKG}.MarketSummaryDataBean"):
        graph = both.get_class_hierarchy()
        out = {v: d["type"] for _, v, d in graph.out_edges(name, data=True)}
        assert {v for v, t in out.items() if t == "EXTENDS"} == set(both.get_extended_classes(name))
        assert {v for v, t in out.items() if t == "IMPLEMENTS"} == set(both.get_implemented_interfaces(name))


def test_get_class_hierarchy_names_out_of_project_supertypes_as_declared(both):
    """A library base becomes a node by being an edge endpoint, spelled exactly as the declaration
    wrote it — **type arguments included**, because that is what the analyzer carries."""
    graph = both.get_class_hierarchy()
    external = set(graph.nodes) - set(both.get_all_classes())
    assert len(external) == 21
    assert "javax.servlet.http.HttpServlet" in external
    assert "java.util.Comparator<com.ibm.websphere.samples.daytrader.entities.QuoteDataBean>" in external


# ---- get_methods_with_annotations -------------------------------------------------------------
def test_get_methods_with_annotations_reads_the_analyzers_own_annotations(both):
    found = both.get_methods_with_annotations(["Override"])
    assert len(found["Override"]) == 328, "a1's @Override-annotated callables"
    methods = both.get_all_methods_in_application()
    for entry in found["Override"]:
        assert set(entry) == {"class", "signature", "method_name", "body"}
        callable_ = methods[entry["class"]][entry["signature"]]
        assert entry["method_name"] == entry["signature"].partition("(")[0]
        assert entry["body"] == callable_.code
        assert "Override" in {d.name.rpartition(".")[2] for d in callable_.decorators}


def test_get_methods_with_annotations_keys_by_the_spelling_the_caller_passed(both):
    """The J-5 marker rule (``@`` stripped, compared after the last ``.``) matches; the *key* is
    the caller's own string, so ``result[a]`` works for every ``a`` that was asked about."""
    found = both.get_methods_with_annotations(["@Override", "org.junit.Override", "Inject"])
    assert set(found) == {"@Override", "org.junit.Override", "Inject"}
    assert len(found["@Override"]) == len(found["org.junit.Override"]) == 328
    assert len(found["Inject"]) == 12


def test_get_methods_with_annotations_omits_what_nothing_carries(both):
    """The 1.x shape. ``WebServlet`` is on 53 **types** in a1 and on no callable, so a
    callable-level filter must not report it — the same split
    :meth:`get_decorated_callables` has."""
    assert both.get_methods_with_annotations(["WebServlet", "Test"]) == {}
    assert both.get_methods_with_annotations([]) == {}


def test_get_methods_with_annotations_agrees_with_get_decorated_callables(both):
    """Two spellings of one filter, so they must not come to disagree about who carries a marker."""
    found = both.get_methods_with_annotations(["Override"])["Override"]
    assert {f"{e['class']}.{e['signature']}" for e in found} == {o.key for o in both.get_decorated_callables(["Override"])}


# ---- get_call_targets -------------------------------------------------------------------------
def test_get_call_targets_matches_declared_names_against_every_call_site(both):
    declared = both.get_all_methods_in_class(TRADE_DIRECT)
    targets = both.get_call_targets(declared)
    assert len(targets) == 53
    assert targets <= {sig.rpartition("(")[0] or sig for sig in declared}, "never a name the caller did not ask about"
    assert "cancelOrder" in targets


def test_get_call_targets_accepts_bare_names_as_well_as_signatures(both):
    declared = both.get_all_methods_in_class(TRADE_DIRECT)
    bare = {sig.rpartition("(")[0]: c for sig, c in declared.items()}
    assert both.get_call_targets(bare) == both.get_call_targets(declared)


def test_get_call_targets_of_nothing_is_nothing(both):
    assert both.get_call_targets({}) == set()
    assert both.get_call_targets({"noSuchMethodAnywhere()": None}) == set()


# ---- get_calling_lines (needs a call graph, so a4) --------------------------------------------
def test_get_calling_lines_reads_the_call_graphs_own_absolute_lines(both_l4):
    lines = both_l4.get_calling_lines(GET_STATEMENT)
    assert lines == sorted(set(lines)) and len(lines) == 36
    assert lines[0] == 205
    graph = both_l4.get_call_graph()
    on_edges = {ln for _, dst, d in graph.edges(data=True) for ln in d["calling_lines"] if graph.nodes[dst]["method_detail"].method.signature.partition("(")[0] == GET_STATEMENT}
    assert set(lines) == on_edges, "nothing is re-derived; CallingLines is the one place a call site becomes a file line"


def test_get_calling_lines_accepts_a_signature_and_cuts_it(both_l4):
    assert both_l4.get_calling_lines("getStatement(java.sql.Connection, java.lang.String)") == both_l4.get_calling_lines(GET_STATEMENT)
    assert len(both_l4.get_calling_lines("cancelOrder")) == 4


def test_get_calling_lines_of_an_uncalled_name_is_empty(both_l4):
    assert both_l4.get_calling_lines("noSuchMethodAnywhere") == []
    assert both_l4.get_calling_lines("") == []


# ---- the two backends, compared directly ------------------------------------------------------
def test_the_two_backends_agree_on_all_six(analysis_json, analysis_json_a4):
    for payload in (analysis_json, analysis_json_a4):
        local, graph = _local(payload), _graph(payload)
        assert local.get_imports() == graph.get_imports()
        assert local.get_variables() == graph.get_variables()
        assert nx.utils.graphs_equal(local.get_class_hierarchy(), graph.get_class_hierarchy())
        assert local.get_methods_with_annotations(["Override", "Inject"]) == graph.get_methods_with_annotations(["Override", "Inject"])
        declared = local.get_all_methods_in_class(TRADE_DIRECT)
        assert local.get_call_targets(declared) == graph.get_call_targets(declared)
        assert local.get_calling_lines(GET_STATEMENT) == graph.get_calling_lines(GET_STATEMENT)


# ---- the facade's own contribution: ``get_variables(**kwargs)`` --------------------------------
def _facade(test_fixture, analysis_json, tmp_path) -> JavaAnalysis:
    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        (tmp_path / "java").mkdir()
        (tmp_path / "java" / "analysis.json").write_text(analysis_json, encoding="utf-8")
        return JavaAnalysis(
            project_dir=test_fixture,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )


def test_get_variables_refuses_a_filter_it_does_not_have(test_fixture, analysis_json, tmp_path):
    """The frozen signature keeps ``**kwargs``; ignoring one would return an unfiltered answer that
    reads as a filtered one."""
    analysis = _facade(test_fixture, analysis_json, tmp_path)
    assert len(analysis.get_variables()) == 1216
    with pytest.raises(TypeError, match="unexpected keyword argument 'qualified_class_name'"):
        analysis.get_variables(qualified_class_name=TRADE_DIRECT)
