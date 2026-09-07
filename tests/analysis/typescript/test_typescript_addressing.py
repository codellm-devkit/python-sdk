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

"""The TypeScript addressing surface (leg 2.5b, Task 1) — offline, over the v2 fixtures.

Seven accessors, mirroring ``PythonAnalysis``'s signatures exactly: ``locate`` / ``locate_many``
/ ``resolve_callable`` / ``resolve_value`` / ``get_source`` / ``describe`` /
``has_resolution_edges``.

Every expected value below was derived by reading
``tests/resources/typescript/analysis_json/v2/a{1,2,4}/analysis.json`` and
``tests/resources/typescript/application/src/*.ts`` directly, never by running the implementation
and copying its output.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.commons.results import BodyRef, LocateResult, SliceNode, Span
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

#: The ids the 1.3.0 fixture mints for the two positions ``locate`` is asserted on. Read off the
#: fixture, passed back as opaque handles — never composed by the caller (E6).
SHOW = "can://typescript/slim/src/controllers.ts/UserController/show"


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


def _analysis(app_path, payload, cache, level):
    with patch("cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_fake_run_writing_output(payload)):
        return CLDK.typescript(project_path=app_path, eager=True, analysis_level=level, backend=CodeAnalyzerConfig(cache_dir=str(cache)))


@pytest.fixture
def ts(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """A local-backend facade over the a4 (``-a 4``) fixture of the tracked sample app."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    return _analysis(typescript_application, typescript_analysis_json, tmp_path, AnalysisLevel.system_dependency_graph)


def _leveled(app_path, tmp_path, monkeypatch, n, level):
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    root = Path(__file__).resolve().parents[3]
    payload = json.dumps(json.load(open(root / f"tests/resources/typescript/analysis_json/v2/a{n}/analysis.json", encoding="utf-8")))
    return _analysis(app_path, payload, tmp_path, level)


# ----------------------------------------------------------------------------------------- locate
def test_locate_finds_the_callable_its_type_module_and_the_statement_at_the_line(ts):
    r = ts.locate("src/controllers.ts", 20)
    assert isinstance(r, LocateResult)
    assert r.module.path == "src/controllers.ts"
    assert r.callable is not None and r.callable.signature == "src/controllers.UserController.show"
    assert r.callable.name == "show" and r.callable.class_signature == "src/controllers.UserController"
    assert r.type is not None and r.type.signature == "src/controllers.UserController" and r.type.name == "UserController"
    assert r.diagnostics == []
    # The call at 20:18 is nested inside the statement at 20:5; the innermost wins.
    assert isinstance(r.body, BodyRef) and r.body.kind == "call"
    assert r.body.id == f"{SHOW}@20:18" and r.node_id == r.body.id
    assert isinstance(r.body.span, Span) and r.body.span.start == (20, 18)
    # 1.3.0 resolves this call, and the local analyzer puts the callee on the node itself.
    assert r.body.callee == "can://typescript/slim/src/services.ts/UserService/create"
    assert r.source.startswith("@Get") and "return user.describe();" in r.source


def test_locate_normalises_the_path_the_caller_printed(ts):
    assert ts.locate("./src/controllers.ts", 20).callable.signature == "src/controllers.UserController.show"


def test_locate_at_module_scope_keeps_the_module_and_says_so(ts):
    r = ts.locate("src/controllers.ts", 1)  # the import statement
    assert r.callable is None and r.type is None and r.body is None and r.node_id is None
    assert r.module.path == "src/controllers.ts"
    assert [d.code for d in r.diagnostics] == ["module_scope"]
    # The local backend holds the module text, so a module-scope position still gets source.
    assert r.source.startswith("import { UserService }")


def test_locate_off_the_end_of_a_file_is_module_scope_not_a_raise(ts):
    r = ts.locate("src/controllers.ts", 9999)
    assert r.callable is None
    assert [d.code for d in r.diagnostics] == ["module_scope"]


def test_locate_in_an_unanalysed_file_names_the_path_it_was_asked_about(ts):
    r = ts.locate("src/nope.ts", 3)
    assert r.callable is None and r.body is None
    assert r.module.path == "src/nope.ts"
    assert [d.code for d in r.diagnostics] == ["file_not_in_graph"]
    assert "src/nope.ts" in r.diagnostics[0].message


def test_locate_many_answers_in_input_order(ts):
    out = ts.locate_many([("src/controllers.ts", 26), ("src/nope.ts", 1), ("src/controllers.ts", 20)])
    assert [r.callable.signature if r.callable else None for r in out] == [
        "src/controllers.UserController.list",
        None,
        "src/controllers.UserController.show",
    ]


def test_locate_many_of_nothing_is_nothing(ts):
    assert ts.locate_many([]) == []


# -------------------------------------------------------------------------------- resolve_callable
def test_resolve_callable_by_dotted_suffix(ts):
    n = ts.resolve_callable("UserController.show")
    assert isinstance(n, SliceNode)
    assert n.kind == "callable" and n.callable == "src/controllers.UserController.show"
    assert n.name == "show" and n.file == "src/controllers.ts" and n.line == 18
    assert n.ref == SHOW and n.source is None


def test_resolve_callable_by_bare_name(ts):
    assert ts.resolve_callable("show").callable == "src/controllers.UserController.show"


def test_an_ambiguous_name_lists_the_candidates_and_nothing_else(ts):
    with pytest.raises(AmbiguousName) as e:
        ts.resolve_callable("describe")
    assert set(e.value.candidates) == {
        "src/models.Entity.describe",
        "src/models.Named.describe",
        "src/models.Robot.describe",
        "src/models.User.describe",
    }
    assert "did you mean" not in e.value.message.lower()  # E8: no suggestions, ever


def test_in_class_and_in_module_disambiguate(ts):
    assert ts.resolve_callable("describe", in_class="User").callable == "src/models.User.describe"
    assert ts.resolve_callable("show", in_module="src/controllers.ts").callable == "src/controllers.UserController.show"


def test_in_module_takes_the_typescript_dotted_form(ts):
    assert ts.resolve_callable("show", in_module="src.controllers").callable == "src/controllers.UserController.show"
    assert ts.resolve_callable("show", in_module="controllers").callable == "src/controllers.UserController.show"


def test_a_name_in_no_module_names_the_selector_the_caller_spelled(ts):
    with pytest.raises(SelectorNotInGraph) as e:
        ts.resolve_callable("noSuchThing")
    assert "noSuchThing" in str(e.value)


def test_a_keyword_that_excludes_every_match_is_blamed_on_that_keyword(ts):
    with pytest.raises(SelectorNotInGraph) as e:
        ts.resolve_callable("show", in_module="src/models.ts")
    assert "in_module" in str(e.value) and "src/models.ts" in str(e.value)


def test_an_anonymous_callable_is_addressed_by_its_signature_not_its_name(ts):
    """``name`` is ``"(anonymous)"`` on every one of them, so the *signature* is the address —
    the analyzer mints ``<anon@line:col>``, which is unique inside its module. The display name is
    not an address at all: the resolver matches signatures, and no signature carries it, so it
    misses outright rather than becoming a four-way ambiguity."""
    n = ts.resolve_callable("<anon@5:10>")
    assert n.callable == "src/controllers.Controller.<anon@5:10>" and n.name == "(anonymous)"
    assert n.ref.endswith("/Controller/<anon@5:10>")
    assert ts.resolve_callable("Controller.<anon@5:10>").callable == n.callable
    with pytest.raises(SelectorNotInGraph):
        ts.resolve_callable("(anonymous)")


# ----------------------------------------------------------------------------------- resolve_value
def test_resolve_value_finds_a_parameter_of_the_named_callable(ts):
    n = ts.resolve_value("id", within="UserController.show")
    assert n.kind == "parameter" and n.name == "id"
    assert n.callable == "src/controllers.UserController.show"
    assert n.file == "src/controllers.ts" and n.line == 18
    assert n.ref == f"{SHOW}@formal_in:0" and n.defined_in is None


def test_resolve_value_in_an_unnamed_value_raises(ts):
    with pytest.raises(SelectorNotInGraph):
        ts.resolve_value("nope", within="UserController.show")


def test_resolve_value_re_raises_an_ambiguous_within_in_terms_of_within(ts):
    with pytest.raises(AmbiguousName) as e:
        ts.resolve_value("id", within="describe")
    assert "within=" in e.value.message


# --------------------------------------------------------------------------------------- get_source
def test_get_source_by_signature_and_by_callable_id(ts):
    by_sig = ts.get_source("src/controllers.UserController.show")
    assert by_sig == ts.get_source(SHOW)
    assert "this.service.create(id)" in by_sig


def test_get_source_by_body_node_id_returns_the_exact_slice(ts):
    assert ts.get_source(f"{SHOW}@20:18") == "this.service.create(id)"
    assert ts.get_source(f"{SHOW}@20:5") == "const user = this.service.create(id);"


def test_get_source_of_a_spanless_vertex_raises(ts):
    with pytest.raises(KeyError):
        ts.get_source(f"{SHOW}@formal_in:0")


def test_get_source_of_nothing_raises(ts):
    with pytest.raises(KeyError):
        ts.get_source("no.such.callable")


def test_get_source_of_a_callable_with_no_text_raises(ts):
    """The implicit constructor the analyzer synthesizes for ``StringUtil.Builder`` has an empty
    span, so there is no text — and the graph writes no ``code`` for it either. Both backends
    refuse rather than returning ``""`` as if it were a body."""
    with pytest.raises(KeyError):
        ts.get_source("src/util.StringUtil.Builder.constructor")


# ----------------------------------------------------------------------------------------- describe
def test_describe_accepts_ids_slice_nodes_and_locate_results(ts):
    located = ts.locate("src/controllers.ts", 20)
    resolved = ts.resolve_callable("UserController.show")
    out = ts.describe([located, resolved])
    assert [type(n) for n in out] == [SliceNode, SliceNode]
    assert out[0].source == "this.service.create(id)"
    assert out[1].source is not None and "return user.describe();" in out[1].source


def test_describe_of_a_value_vertex_is_found_with_no_text(ts):
    value = ts.resolve_value("id", within="UserController.show")
    assert ts.describe([value])[0].source is None


def test_describe_of_nothing_costs_nothing(ts):
    assert ts.describe([]) == []


def test_describe_raises_on_a_ref_that_names_nothing(ts):
    stale = SliceNode(file="src/controllers.ts", line=1, callable="x", kind="callable", name="x", ref="can://typescript/slim/nope")
    with pytest.raises(KeyError):
        ts.describe([stale])


# -------------------------------------------------------------------------- has_resolution_edges
def test_has_resolution_edges_is_true_once_the_analyzer_has_run_the_call_graph(ts):
    assert ts.has_resolution_edges is True


def test_has_resolution_edges_is_false_below_the_level_that_resolves_callees(typescript_application, tmp_path, monkeypatch):
    a1 = _leveled(typescript_application, tmp_path, monkeypatch, 1, AnalysisLevel.symbol_table)
    assert a1.has_resolution_edges is False
    a2 = _leveled(typescript_application, tmp_path / "two", monkeypatch, 2, AnalysisLevel.call_graph)
    assert a2.has_resolution_edges is True
