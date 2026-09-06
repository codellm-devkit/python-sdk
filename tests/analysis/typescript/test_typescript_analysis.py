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

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig, TSCodeAnalyzerConfig
from cldk.utils.exceptions import CldkInitializationException


def _fake_run_writing_output(payload):
    """A subprocess.run side effect that writes ``analysis.json`` into the ``-o`` directory.

    Caching is on by default now (the facade always passes a language-keyed cache dir), so the
    backend runs in disk mode rather than reading the stdout pipe. This mirrors the analyzer
    writing its output where ``-o`` points.
    """

    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


@pytest.fixture
def ts_analysis(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """Build a TypeScriptAnalysis with the codeanalyzer-typescript subprocess mocked to write the
    pre-computed analysis.json fixture into the language-keyed cache directory."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(typescript_analysis_json),
    ):
        return CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )


def test_symbol_table_is_not_empty(ts_analysis):
    symtab = ts_analysis.get_symbol_table()
    assert symtab is not None
    assert len(symtab) == 5
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
    # builtin/library calls become phantom (external) nodes, not dropped edges; keyed as the graph
    # keys them ("<module>.<name>"), with the wire id as a node attribute (TS-10)
    ext = ts_analysis.get_external_symbols()
    assert "(builtin).log" in ext
    assert ext["(builtin).log"].module == "(builtin)"
    assert ext["(builtin).log"].name == "log"
    assert ext["(builtin).log"].id == "can://typescript/slim/@external/(builtin)/log"

    graph = ts_analysis.get_call_graph()
    assert graph.has_edge("src/index.main", "(builtin).log")
    data = graph.get_edge_data("src/index.main", "(builtin).log")
    assert data["type"] == "CALL_DEP"
    assert data["provenance"] == ("import",)
    assert graph.nodes["(builtin).log"] == {"id": "can://typescript/slim/@external/(builtin)/log", "kind": "external"}
    assert graph.nodes["src/index.main"]["kind"] == "callable"
    # internal callers can be found via callees
    callees = ts_analysis.get_callees("src/index.main")
    assert "(builtin).log" in {c["callee_signature"] for c in callees["callee_details"]}


def test_call_graph_keeps_module_callers_and_tags_kinds(ts_analysis):
    """TS-11: top-level code is a caller (the module node), kept with ``kind="module"`` rather than
    dropped by Python's rule; a one-line filter recovers Python's shape."""
    graph = ts_analysis.get_call_graph()
    assert graph.has_edge("src/index.ts", "src/index.main")
    assert graph.nodes["src/index.ts"] == {"id": "can://typescript/slim/src/index.ts", "kind": "module"}
    kinds = {attrs["kind"] for _, attrs in graph.nodes(data=True)}
    assert kinds == {"module", "callable", "external"}
    callable_only = graph.subgraph(n for n, a in graph.nodes(data=True) if a["kind"] == "callable")
    assert callable_only.number_of_nodes() == 30
    assert graph.number_of_nodes() == 40 and graph.number_of_edges() == 45


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
    # the connecting edge carries the wire's provenance and weight
    main_edge = next(c["edge"] for c in callers["caller_details"] if c["caller_signature"] == "src/index.main")
    assert main_edge == {"type": "CALL_DEP", "weight": 1, "provenance": ("tsc",)}


def test_get_method_resolves_module_level_function(ts_analysis):
    # regression for #247: get_method used to be class-scope only, so "src/index.main" (a
    # module-level function that participates in a call edge, see test_callers_and_callees) was
    # unreachable through it.
    method = ts_analysis.get_method("src/index", "main")
    assert method is not None
    assert method.signature == "src/index.main"


def test_get_method_parameters_module_level_function(ts_analysis):
    # "main" is declared as `function main(): void` (see index.ts), so it takes no parameters —
    # this exercises the module-level fallback path in get_method_parameters/get_method, not just
    # that some list comes back.
    params = ts_analysis.get_method_parameters("src/index", "main")
    assert params == []


def test_call_sites(ts_analysis):
    # rich syntactic call sites inside a callable
    sites = ts_analysis.get_call_sites("src/controllers.UserController.show")
    assert any(cs.callee_signature == "src/services.UserService.create" for cs in sites)
    create = next(cs for cs in sites if cs.callee_signature == "src/services.UserService.create")
    assert create.receiver_type == "UserService"
    assert create.method_name == "create"
    assert (create.start_line, create.start_column) == (20, 18)

    # project-wide calling lines for a target
    lines = ts_analysis.get_calling_lines("src/services.UserService.create")
    assert lines == sorted(lines)
    assert create.start_line in lines

    # call targets derived from a callable's call sites
    targets = ts_analysis.get_call_targets("src/controllers.UserController.show")
    assert "src/services.UserService.create" in targets


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


def test_exports_and_variables_are_parsed(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """Behavioral: a real export + module-level const must be parsed and surfaced (the slim
    fixture carries none, so inject them into the analyzed JSON)."""
    data = json.loads(typescript_analysis_json)
    models = data["application"]["symbol_table"]["src/models.ts"]
    models["exports"].append({"name": "User", "module": None, "alias": None, "is_type_only": False, "export_kind": "named"})
    models["fields"]["DEFAULT_ROLE"] = {
        "id": "can://typescript/slim/src/models.ts/DEFAULT_ROLE",
        "kind": "field",
        "name": "DEFAULT_ROLE",
        "type": "Role",
        "initializer": "Role.Member",
        "scope": "module",
        "declaration_kind": "const",
        "is_exported": True,
    }

    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(json.dumps(data)),
    ):
        analysis = CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )

    exported = analysis.get_exports()["src/models.ts"]
    assert [e.name for e in exported] == ["User"]
    assert exported[0].export_kind == "named"

    variables = analysis.get_variables()["src/models.ts"]
    assert [v.name for v in variables] == ["DEFAULT_ROLE"]
    assert variables[0].declaration_kind == "const"
    assert variables[0].initializer == "Role.Member"
    assert variables[0].is_exported is True


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
    # declared-type edge to the interface method + RTA-expanded edges to the implementers; the v2
    # wire carries no dispatch tag, only the resolver that produced each edge
    assert set(targets) == {"src/models.Named.describe", "src/models.User.describe", "src/models.Robot.describe"}
    assert targets["src/models.User.describe"]["provenance"] == ("tsc",)


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


def test_cache_dir_now_accepted(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """cache_dir is no longer Python-only; TypeScript accepts it and keys the cache by language."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(typescript_analysis_json),
    ) as run_mock:
        CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.symbol_table,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path / "cache")),
        )

    args = run_mock.call_args.args[0]
    assert args[args.index("-o") + 1] == str(tmp_path / "cache" / "typescript")


# -----[ facade -> backend wiring / subprocess invocation ]-----
def test_subprocess_args_default_keyed_output_dir(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """The facade forwards target_files + analysis_level to the backend, which builds the right
    subprocess command. Caching is on by default, so output goes to ``-o <cache_dir>/typescript``."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(typescript_analysis_json),
    ) as run_mock:
        CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
            target_files=["src/models.ts", "src/services.ts"],
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )

    args = run_mock.call_args.args[0]
    assert args[0] == "codeanalyzer-typescript"  # resolved from $CODEANALYZER_TS_BIN
    assert args[args.index("-i") + 1] == str(typescript_application)
    assert args[args.index("-a") + 1] == "2"  # call_graph -> level 2
    # each target file forwarded with its own -t
    assert args.count("-t") == 2
    assert "src/models.ts" in args and "src/services.ts" in args
    # caching on by default: output written to the language-keyed cache dir
    assert args[args.index("-o") + 1] == str(tmp_path / "typescript")


def test_subprocess_args_output_dir(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """The backend passes ``-o <cache_dir>/typescript`` and reads analysis.json back from disk.
    Exercises the output-dir branch and level-1 (symbol_table) mapping."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    cache = tmp_path / "out"

    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(typescript_analysis_json),
    ) as run_mock:
        analysis = CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.symbol_table,
            backend=CodeAnalyzerConfig(cache_dir=str(cache)),
        )

    args = run_mock.call_args.args[0]
    assert args[args.index("-a") + 1] == "1"  # symbol_table -> level 1
    assert args[args.index("-o") + 1] == str(cache / "typescript")
    # the disk read-back path produced a usable application
    assert len(analysis.get_symbol_table()) == 5


def test_cached_analysis_json_skips_subprocess(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """When a cached analysis.json already exists (in the language-keyed dir) and eager is False,
    the backend must reuse it and not invoke the analyzer subprocess."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    keyed = tmp_path / "out" / "typescript"
    keyed.mkdir(parents=True)
    (keyed / "analysis.json").write_text(typescript_analysis_json, encoding="utf-8")

    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        analysis = CLDK.typescript(
            project_path=typescript_application,
            eager=False,
            analysis_level=AnalysisLevel.call_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path / "out")),
        )

    run_mock.assert_not_called()
    assert len(analysis.get_symbol_table()) == 5


# -----[ the argv the backend builds for codeanalyzer-typescript 1.2.0 ]-----


def _captured_argv(typescript_application, typescript_analysis_json, tmp_path, monkeypatch, level, **kw):
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    seen = []
    inner = _fake_run_writing_output(typescript_analysis_json)

    def _run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return inner(cmd, *args, **kwargs)

    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_run):
        CLDK.typescript(project_path=typescript_application, analysis_level=level, eager=True, backend=kw.pop("backend", CodeAnalyzerConfig(cache_dir=str(tmp_path))), **kw)
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize("level,expected", [(lvl, n) for n, lvl in enumerate(AnalysisLevel, start=1)], ids=lambda x: getattr(x, "name", x))
def test_argv_maps_every_level_to_an_integer_and_drops_tsc_only(typescript_application, typescript_analysis_json, tmp_path, monkeypatch, level, expected):
    cmd = _captured_argv(typescript_application, typescript_analysis_json, tmp_path, monkeypatch, level)
    assert cmd[0] == "codeanalyzer-typescript"
    assert cmd[cmd.index("-a") + 1] == str(expected)
    assert cmd[cmd.index("--app-name") + 1] == Path(typescript_application).name
    assert cmd[cmd.index("-i") + 1] == str(typescript_application)
    out = cmd[cmd.index("-o") + 1]
    assert cmd[cmd.index("--cache-dir") + 1] == out and out.startswith(str(tmp_path))
    assert "--skip-tests" in cmd and "--eager" in cmd
    assert "--tsc-only" not in cmd


def test_argv_member_name_spelling_is_accepted(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    cmd = _captured_argv(typescript_application, typescript_analysis_json, tmp_path, monkeypatch, "system_dependency_graph")
    assert cmd[cmd.index("-a") + 1] == "4"


def test_tsc_only_is_a_deprecated_no_op(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    with pytest.warns(DeprecationWarning, match="1.0.0"):
        cmd = _captured_argv(
            typescript_application,
            typescript_analysis_json,
            tmp_path,
            monkeypatch,
            AnalysisLevel.call_graph,
            backend=TSCodeAnalyzerConfig(cache_dir=str(tmp_path), tsc_only=True),
        )
    assert "--tsc-only" not in cmd


def test_backend_keeps_the_envelope(ts_analysis):
    analysis = ts_analysis.backend.analysis
    assert analysis.analyzer.version == "1.2.0"
    assert analysis.max_level == 4
    assert analysis.application is ts_analysis.get_application_view()
