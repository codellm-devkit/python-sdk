################################################################################
# Copyright IBM Corporation 2024
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

"""Mocked tests for the Go analysis facade.

End-to-end tests require ``codeanalyzer-go`` to be installed; these tests
verify the SDK public API contract and data-model round-trip without invoking
the real binary.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis.go import GoAnalysis
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.models.go.models import (
    GoApplication,
    GoCallEdge,
    GoCallable,
    GoFile,
    GoType,
    GoParameter,
)
from cldk.utils.exceptions import CldkInitializationException


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_minimal_app() -> GoApplication:
    """Build a minimal GoApplication that exercises the main schema paths."""
    param = GoParameter(name="x", type="int", is_variadic=False)
    fn = GoCallable(
        name="Add",
        signature="example.com/demo.Add",
        parameters=[param],
        return_types=["int"],
        return_type="int",
        is_exported=True,
    )
    method = GoCallable(
        name="String",
        signature="example.com/demo.MyStruct.String",
        receiver_type="*MyStruct",
        receiver_name="s",
        return_types=["string"],
        return_type="string",
        is_exported=True,
    )
    go_type = GoType(
        name="MyStruct",
        is_interface=False,
        is_exported=True,
        methods={"example.com/demo.MyStruct.String": method},
    )
    go_file = GoFile(
        file_path="pkg/demo.go",
        module_name="demo",
        classes={"MyStruct": go_type},
        functions={"example.com/demo.Add": fn},
    )
    edge = GoCallEdge(
        source="example.com/demo.main",
        target="example.com/demo.Add",
        type="CALL_DEP",
        weight=1,
        provenance=["go/types"],
    )
    return GoApplication(
        symbol_table={"pkg/demo.go": go_file},
        call_graph=[edge],
    )


@pytest.fixture
def mock_backend(monkeypatch):
    """Monkeypatch GoCodeanalyzer to return a minimal application."""
    app = _make_minimal_app()

    class FakeCodeanalyzer:
        def __init__(self, **kwargs):
            self._app = app

        def get_application(self):
            return self._app

        def get_symbol_table(self):
            return self._app.symbol_table

        def get_file(self, path):
            return self._app.symbol_table.get(path)

        def get_all_types(self):
            result = {}
            for f in self._app.symbol_table.values():
                for name, t in f.classes.items():
                    result[f"{f.module_name}.{name}"] = t
            return result

        def get_all_callables(self):
            result = {}
            for f in self._app.symbol_table.values():
                result.update(f.functions)
                for t in f.classes.values():
                    result.update(t.methods)
            return result

    monkeypatch.setattr("cldk.analysis.go.go_analysis.GoCodeanalyzer", FakeCodeanalyzer)
    return app


# ── CLDK factory ──────────────────────────────────────────────────────────────

def test_go_analysis_rejects_source_code_mode():
    with pytest.raises(CldkInitializationException):
        CLDK(language="go").analysis(source_code="package main")


def test_go_analysis_requires_inputs():
    with pytest.raises(CldkInitializationException):
        CLDK(language="go").analysis()


# ── Symbol table ──────────────────────────────────────────────────────────────

def test_get_symbol_table_non_empty(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    symtab = analysis.get_symbol_table()
    assert len(symtab) > 0


def test_get_file_returns_correct_file(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    go_file = analysis.get_file("pkg/demo.go")
    assert go_file is not None
    assert go_file.module_name == "demo"


def test_get_file_missing_returns_none(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    assert analysis.get_file("nonexistent.go") is None


# ── Types ──────────────────────────────────────────────────────────────────────

def test_get_types_in_file(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    types = analysis.get_types_in_file("pkg/demo.go")
    assert "MyStruct" in types
    assert not types["MyStruct"].is_interface


def test_get_type_by_name(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    t = analysis.get_type("pkg/demo.go", "MyStruct")
    assert t is not None
    assert t.is_exported


# ── Callables ──────────────────────────────────────────────────────────────────

def test_get_callables_in_file(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    callables = analysis.get_callables_in_file("pkg/demo.go")
    assert "example.com/demo.Add" in callables
    assert "example.com/demo.MyStruct.String" in callables


def test_get_callable_by_signature(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="symbol_table",
        eager_analysis=False,
    )
    fn = analysis.get_callable("example.com/demo.Add")
    assert fn is not None
    assert fn.is_exported
    assert len(fn.parameters) == 1


# ── Call graph ──────────────────────────────────────────────────────────────────

def test_get_call_graph_has_edges(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="call_graph",
        eager_analysis=False,
    )
    cg = analysis.get_call_graph()
    assert cg.number_of_edges() > 0


def test_get_callees(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="call_graph",
        eager_analysis=False,
    )
    callees = analysis.get_callees("example.com/demo.main")
    assert "example.com/demo.Add" in callees


def test_get_callers(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="call_graph",
        eager_analysis=False,
    )
    callers = analysis.get_callers("example.com/demo.Add")
    assert "example.com/demo.main" in callers


def test_get_callers_unknown_node(mock_backend, tmp_path):
    analysis = GoAnalysis(
        project_dir=tmp_path,
        analysis_level="call_graph",
        eager_analysis=False,
    )
    assert analysis.get_callers("no.such.sig") == []


# ── Pydantic model round-trip ──────────────────────────────────────────────────

def test_go_application_round_trip():
    """GoApplication must deserialize cleanly from the JSON the Go binary emits."""
    app = _make_minimal_app()
    raw = app.model_dump_json()
    restored = GoApplication.model_validate_json(raw)
    assert "pkg/demo.go" in restored.symbol_table
    assert len(restored.call_graph) == 1


def test_go_file_type_alias():
    """GoFile.types property must alias GoFile.classes."""
    go_file = GoFile(
        file_path="x.go",
        module_name="x",
        classes={"MyType": GoType(name="MyType")},
    )
    assert go_file.types is go_file.classes


def test_go_file_package_name_alias():
    """GoFile.package_name property must alias GoFile.module_name."""
    go_file = GoFile(file_path="x.go", module_name="mypkg")
    assert go_file.package_name == "mypkg"


def test_go_callable_receiver_fields():
    method = GoCallable(
        name="Do",
        receiver_type="*MyStruct",
        receiver_name="s",
    )
    assert method.receiver_type == "*MyStruct"
    assert method.receiver_name == "s"


# ── Binary invocation flags ────────────────────────────────────────────────────

def _make_subprocess_stub(output_dir_arg_index: int = None):
    """Return a fake subprocess.run that writes analysis.json to the --output dir."""
    minimal = '{"symbol_table": {}, "call_graph": [], "entrypoints": {}}'

    def fake_run(args, **kwargs):
        out_idx = args.index("--output") + 1
        out = Path(args[out_idx])
        out.mkdir(parents=True, exist_ok=True)
        (out / "analysis.json").write_text(minimal)
        return MagicMock(returncode=0)

    return fake_run


def test_eager_flag_passed_to_binary(tmp_path):
    """GoCodeanalyzer must append --eager to the subprocess args when eager_analysis=True."""
    with patch("cldk.analysis.go.codeanalyzer.codeanalyzer.shutil.which", return_value="/bin/codeanalyzer-go"):
        with patch("cldk.analysis.go.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_make_subprocess_stub()) as mock_run:
            GoCodeanalyzer(
                project_dir=tmp_path,
                analysis_json_path=None,
                analysis_level="symbol_table",
                eager_analysis=True,
            )
            invoked_args = mock_run.call_args[0][0]
            assert "--eager" in invoked_args


def test_eager_flag_absent_when_not_eager(tmp_path):
    """GoCodeanalyzer must NOT pass --eager when eager_analysis=False."""
    with patch("cldk.analysis.go.codeanalyzer.codeanalyzer.shutil.which", return_value="/bin/codeanalyzer-go"):
        with patch("cldk.analysis.go.codeanalyzer.codeanalyzer.subprocess.run", side_effect=_make_subprocess_stub()) as mock_run:
            GoCodeanalyzer(
                project_dir=tmp_path,
                analysis_json_path=None,
                analysis_level="symbol_table",
                eager_analysis=False,
            )
            invoked_args = mock_run.call_args[0][0]
            assert "--eager" not in invoked_args
