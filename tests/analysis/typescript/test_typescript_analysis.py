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

"""Tests for the TypeScript analysis facade (backend subprocess mocked)."""

from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.utils.exceptions import CldkInitializationException


@pytest.fixture
def ts_analysis(typescript_application, typescript_analysis_json, monkeypatch):
    """Build a TypeScriptAnalysis with the codeanalyzer-typescript subprocess mocked to return
    the pre-computed analysis.json fixture."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=typescript_analysis_json, returncode=0)
        return CLDK(language="typescript").analysis(
            project_path=typescript_application,
            analysis_backend_path=None,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
        )


def test_symbol_table_is_not_empty(ts_analysis):
    symtab = ts_analysis.get_symbol_table()
    assert symtab is not None
    assert len(symtab) == 6
    assert "src/models.ts" in symtab


def test_call_graph_has_no_dangling_nodes(ts_analysis):
    graph = ts_analysis.get_call_graph()
    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_edges() > 0
    # every edge endpoint is a node — internal callable OR phantom external symbol
    nodes = set(graph.nodes)
    for src, dst in graph.edges:
        assert src in nodes
        assert dst in nodes


def test_phantom_external_nodes(ts_analysis):
    # imported Node-builtin calls become phantom (external) nodes, not dropped edges
    ext = ts_analysis.get_external_symbols()
    assert "node:crypto.createHash" in ext
    assert ext["node:crypto.createHash"].module == "node:crypto"
    assert ext["node:crypto.createHash"].is_external is True
    assert "node:path.extname" in ext

    graph = ts_analysis.get_call_graph()
    assert graph.has_edge("src/external.fingerprint", "node:crypto.createHash")
    data = graph.get_edge_data("src/external.fingerprint", "node:crypto.createHash")
    assert data["tags"].get("ts.external") == "true"
    assert data["provenance"] == ["import"]
    assert graph.nodes["node:crypto.createHash"]["external"] is True
    # internal callers can be found via callees
    callees = ts_analysis.get_callees("src/external.fingerprint")
    assert "node:crypto.createHash" in {c["callee_signature"] for c in callees["callee_details"]}


def test_classes_interfaces_enums_type_aliases(ts_analysis):
    classes = ts_analysis.get_classes()
    assert "src/models.User" in classes
    assert "src/services.UserService" in classes
    assert set(ts_analysis.get_interfaces()) >= {"src/models.Identifiable", "src/models.Named"}
    assert "src/models.Role" in ts_analysis.get_enums()
    assert "src/models.UserId" in ts_analysis.get_type_aliases()


def test_class_inheritance_split(ts_analysis):
    user = ts_analysis.get_class("src/models.User")
    assert "src/models.Entity" in user.base_classes
    assert ts_analysis.get_implemented_interfaces("src/models.User") == ["src/models.Named"]
    assert "src/models.Entity" in ts_analysis.get_extended_classes("src/models.User")
    assert user.is_abstract is False


def test_methods_and_constructor(ts_analysis):
    methods = ts_analysis.get_methods_in_class("src/models.User")
    assert "describe" in methods
    assert "recordLogin" in methods
    assert methods["recordLogin"].is_async is True
    constructors = ts_analysis.get_constructors("src/models.User")
    assert any(c.kind == "constructor" for c in constructors.values())


def test_structured_decorators(ts_analysis):
    decorated = ts_analysis.get_methods_with_decorators(["Controller", "Get"])
    assert any(sig.endswith("UserController.show") for sig in decorated["Get"])
    controller = ts_analysis.get_class("src/controllers.UserController")
    assert [d.name for d in controller.decorators] == ["Controller"]
    assert controller.decorators[0].positional_arguments == ['"/users"']


def test_callers_and_callees(ts_analysis):
    # bare-signature form (module-level function)
    callees = ts_analysis.get_callees("src/index.main")
    callee_sigs = {c["callee_signature"] for c in callees["callee_details"]}
    assert "src/services.UserService.constructor" in callee_sigs

    # (class, method) form, with edge metadata surfaced
    callers = ts_analysis.get_callers("src/services.UserService", "create")
    assert callers["target_method"] == "src/services.UserService.create"
    caller_sigs = {c["caller_signature"] for c in callers["caller_details"]}
    assert "src/index.main" in caller_sigs
    # the connecting edge carries provenance/tags
    main_edge = next(c["edge"] for c in callers["caller_details"] if c["caller_signature"] == "src/index.main")
    assert "provenance" in main_edge and "tags" in main_edge


def test_call_sites(ts_analysis):
    # rich syntactic call sites inside a callable
    sites = ts_analysis.get_call_sites("src/controllers.UserController.show")
    assert any(cs.callee_signature == "src/services.UserService.create" for cs in sites)
    create = next(cs for cs in sites if cs.callee_signature == "src/services.UserService.create")
    assert create.receiver_type == "UserService"
    assert create.start_line > 0

    # project-wide calling lines for a target
    lines = ts_analysis.get_calling_lines("src/services.UserService.create")
    assert lines == sorted(lines)
    assert create.start_line in lines

    # call targets derived from a callable's call sites
    targets = ts_analysis.get_call_targets("src/controllers.UserController.show")
    assert "src/services.UserService.create" in targets


def test_entrypoints_not_implemented(ts_analysis):
    # entrypoint detection is a stub placeholder in the analyzer; methods exist for parity but raise
    with pytest.raises(NotImplementedError):
        ts_analysis.get_entry_point_methods()
    with pytest.raises(NotImplementedError):
        ts_analysis.get_service_entry_point_methods()


def test_enum_members_and_interface_properties(ts_analysis):
    members = ts_analysis.get_enum_members("src/models.Role")
    assert [m.name for m in members] == ["Admin", "Member", "Guest"]
    props = ts_analysis.get_interface_properties("src/models.Named")
    assert [p.name for p in props] == ["name"]


def test_exports_and_variables(ts_analysis):
    exports = ts_analysis.get_exports()
    variables = ts_analysis.get_variables()
    # keyed by every analyzed file, even when empty
    assert set(exports) == set(ts_analysis.get_symbol_table())
    assert set(variables) == set(ts_analysis.get_symbol_table())


def test_class_decorators(ts_analysis):
    decos = ts_analysis.get_class_decorators("src/controllers.UserController")
    assert [d.name for d in decos] == ["Controller"]
    by_name = ts_analysis.get_classes_with_decorators(["Controller"])
    assert "src/controllers.UserController" in by_name["Controller"]
    method_decos = ts_analysis.get_decorators("src/controllers.UserController.show")
    assert any(d.name == "Get" for d in method_decos)


def test_rta_subtype_expansion(ts_analysis):
    graph = ts_analysis.get_call_graph()
    announce = "src/services.announce"
    targets = {dst: data for _, dst, data in graph.out_edges(announce, data=True)}
    # declared-type edge to the interface method + RTA-expanded edges to the implementers
    assert "src/models.Named.describe" in targets
    assert targets["src/models.User.describe"]["tags"].get("ts.dispatch") == "rta"
    assert targets["src/models.Robot.describe"]["tags"].get("ts.dispatch") == "rta"


def test_class_hierarchy_graph(ts_analysis):
    hierarchy = ts_analysis.get_class_hierarchy()
    assert hierarchy.has_edge("src/models.User", "src/models.Entity")
    assert hierarchy.has_edge("src/models.User", "src/models.Named")


def test_namespace_members(ts_analysis):
    classes = ts_analysis.get_classes()
    assert "src/util.StringUtil.Builder" in classes
    functions = ts_analysis.get_functions()
    assert "src/util.StringUtil.repeat" in functions


def test_source_code_mode_rejected(typescript_application):
    with pytest.raises(CldkInitializationException):
        CLDK(language="typescript").analysis(source_code="const x = 1;")


def test_python_only_kwargs_rejected(typescript_application):
    with pytest.raises(CldkInitializationException):
        CLDK(language="typescript").analysis(project_path=typescript_application, cache_dir="/tmp/x")
