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

"""End-to-end tests for GoAnalysis against the cldk-e2e Go fixture.

Requires ``codeanalyzer-go`` on PATH (built from codeanalyzer-go repo and
installed to e.g. ~/.local/bin). Tests are skipped automatically when the
binary is absent, so CI without Go toolchain does not break.

Fixture: tests/resources/go/application/
  calc/calc.go      — Calculator struct, Operator interface, exported/unexported methods,
                      pointer receiver, multiple return types, embedded field
  calc/formatter.go — FormatResult (variadic), Describe (cross-file value-receiver method)
  pipeline/pipeline.go — Pipeline struct, Runner interface, goroutine callsite,
                         cyclomatic complexity > 1, variadic RunAll
  main.go           — entry point, cross-package calls

All assertions reference exact values from the JSON the binary emits.
"""

import shutil
from pathlib import Path

import pytest

from cldk.analysis.go import GoAnalysis
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import GoCodeAnalyzerConfig
from cldk.models.go.models import GoApplication


# ── Helpers ────────────────────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).parent.parent.parent / "resources" / "go" / "application"

pytestmark = pytest.mark.skipif(
    shutil.which("codeanalyzer-go") is None,
    reason="codeanalyzer-go not found on PATH — install from codeanalyzer-go repo",
)


def _analysis(tmp_path: Path, level: str = AnalysisLevel.symbol_table) -> GoAnalysis:
    return GoAnalysis(
        project_dir=FIXTURE_DIR,
        analysis_level=level,
        eager_analysis=True,
        backend=GoCodeAnalyzerConfig(cache_dir=tmp_path),
    )


# ── Symbol table structure ─────────────────────────────────────────────────────

def test_e2e_symbol_table_files(tmp_path):
    """All four source files must appear in the symbol table."""
    analysis = _analysis(tmp_path)
    st = analysis.get_symbol_table()
    for expected in ("calc/calc.go", "calc/formatter.go", "pipeline/pipeline.go", "main.go"):
        assert expected in st, f"missing file {expected!r} in symbol_table"


def test_e2e_symbol_table_keys_are_relative(tmp_path):
    """No key should be an absolute or CWD-relative path."""
    analysis = _analysis(tmp_path)
    for key in analysis.get_symbol_table():
        assert not key.startswith("/"), f"absolute path key: {key!r}"
        assert not key.startswith(".."), f"CWD-relative key: {key!r}"


def test_e2e_application_round_trips_pydantic(tmp_path):
    """GoApplication.model_validate must accept the raw analysis.json without errors."""
    import json
    _analysis(tmp_path)
    with open(tmp_path / "go" / "analysis.json") as f:
        raw = json.load(f)
    app = GoApplication(**raw)
    assert len(app.symbol_table) == 4


# ── Multi-file package ─────────────────────────────────────────────────────────

def test_e2e_multi_file_package_both_files_present(tmp_path):
    """calc/calc.go and calc/formatter.go must both be in the symbol table."""
    analysis = _analysis(tmp_path)
    st = analysis.get_symbol_table()
    assert "calc/calc.go" in st
    assert "calc/formatter.go" in st


def test_e2e_format_result_lives_in_formatter_file(tmp_path):
    """FormatResult must be keyed under calc/formatter.go, not calc/calc.go."""
    analysis = _analysis(tmp_path)
    fmtr = analysis.get_file("calc/formatter.go")
    assert fmtr is not None
    sigs = list(fmtr.functions.keys())
    assert any("FormatResult" in s for s in sigs), (
        f"FormatResult not in calc/formatter.go functions: {sigs}"
    )


# ── Types ──────────────────────────────────────────────────────────────────────

def test_e2e_operator_is_interface(tmp_path):
    """Operator must be detected as an interface."""
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    assert calc_file is not None
    operator = calc_file.classes.get("Operator")
    assert operator is not None, "GoType 'Operator' not found in calc/calc.go"
    assert operator.is_interface, "Operator.is_interface should be True"


def test_e2e_runner_is_interface(tmp_path):
    """Runner in pipeline package must be detected as an interface."""
    analysis = _analysis(tmp_path)
    pipe_file = analysis.get_file("pipeline/pipeline.go")
    assert pipe_file is not None
    runner = pipe_file.classes.get("Runner")
    assert runner is not None, "GoType 'Runner' not found in pipeline/pipeline.go"
    assert runner.is_interface


def test_e2e_calculator_is_not_interface(tmp_path):
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    calculator = calc_file.classes.get("Calculator")
    assert calculator is not None
    assert not calculator.is_interface
    assert calculator.is_exported


def test_e2e_base_is_unexported_type(tmp_path):
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    base = calc_file.classes.get("base")
    assert base is not None, "GoType 'base' not found"
    assert not base.is_exported, "base.is_exported should be False"


# ── Embedded field ─────────────────────────────────────────────────────────────

def test_e2e_calculator_has_embedded_field(tmp_path):
    """Calculator embeds base — at least one field must have is_embedded=True."""
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    calculator = calc_file.classes["Calculator"]
    embedded = [f for f in calculator.fields if f.is_embedded]
    assert embedded, f"Calculator has no embedded field; fields: {calculator.fields}"


# ── Multiple return types ──────────────────────────────────────────────────────

def test_e2e_add_has_two_return_types(tmp_path):
    """Calculator.Add returns (int, error) — must have exactly two return types."""
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    calculator = calc_file.classes["Calculator"]
    add_method = next(
        (m for m in calculator.methods.values() if m.name == "Add"),
        None,
    )
    assert add_method is not None, "Calculator.Add method not found"
    assert len(add_method.return_types) == 2, (
        f"Add.return_types should be ['int', 'error']; got {add_method.return_types}"
    )
    assert "error" in add_method.return_types


# ── Exported / unexported callables ───────────────────────────────────────────

def test_e2e_precision_value_is_unexported(tmp_path):
    """precisionValue must have is_exported=False."""
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    calculator = calc_file.classes["Calculator"]
    precision = next(
        (m for m in calculator.methods.values() if m.name == "precisionValue"),
        None,
    )
    assert precision is not None, "method 'precisionValue' not found in Calculator"
    assert not precision.is_exported, "precisionValue.is_exported should be False"


def test_e2e_execute_is_unexported(tmp_path):
    """Pipeline.execute must have is_exported=False."""
    analysis = _analysis(tmp_path)
    pipe_file = analysis.get_file("pipeline/pipeline.go")
    pipeline_type = pipe_file.classes.get("Pipeline")
    assert pipeline_type is not None
    execute = next(
        (m for m in pipeline_type.methods.values() if m.name == "execute"),
        None,
    )
    assert execute is not None, "method 'execute' not found in Pipeline"
    assert not execute.is_exported, "execute.is_exported should be False"


# ── Receiver type / name ───────────────────────────────────────────────────────

def test_e2e_add_pointer_receiver(tmp_path):
    """Calculator.Add has a pointer receiver — receiver_type must contain '*'."""
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    add = next(
        (m for m in calc_file.classes["Calculator"].methods.values() if m.name == "Add"),
        None,
    )
    assert add is not None
    assert add.receiver_type != "", "Add.receiver_type should be non-empty"
    assert "*" in add.receiver_type, (
        f"Add.receiver_type {add.receiver_type!r} should be a pointer receiver"
    )
    assert add.receiver_name != "", "Add.receiver_name should be non-empty"


def test_e2e_describe_value_receiver_cross_file(tmp_path):
    """Describe is a value receiver on Calculator, defined in formatter.go.

    The reconcileCrossFileMethods pass must attach it to Calculator in calc.go,
    while Describe.path must still point to formatter.go.
    """
    analysis = _analysis(tmp_path)
    calc_file = analysis.get_file("calc/calc.go")
    describe = next(
        (m for m in calc_file.classes["Calculator"].methods.values() if m.name == "Describe"),
        None,
    )
    assert describe is not None, (
        "Describe not found attached to Calculator in calc/calc.go"
    )
    assert "*" not in describe.receiver_type, (
        f"Describe.receiver_type {describe.receiver_type!r} should be a value receiver (no '*')"
    )
    assert "formatter.go" in describe.path, (
        f"Describe.path {describe.path!r} should point to formatter.go"
    )


# ── Variadic parameters ────────────────────────────────────────────────────────

def test_e2e_format_result_variadic(tmp_path):
    """FormatResult(value int, tags ...string) must have a variadic parameter."""
    analysis = _analysis(tmp_path)
    fmtr = analysis.get_file("calc/formatter.go")
    format_result = next(
        (fn for fn in fmtr.functions.values() if fn.name == "FormatResult"),
        None,
    )
    assert format_result is not None, "FormatResult not found in calc/formatter.go"
    variadic = [p for p in format_result.parameters if p.is_variadic]
    assert variadic, f"FormatResult has no variadic parameter; params: {format_result.parameters}"


def test_e2e_run_all_variadic(tmp_path):
    """Pipeline.RunAll(steps ...Step) must have a variadic parameter."""
    analysis = _analysis(tmp_path)
    pipe_file = analysis.get_file("pipeline/pipeline.go")
    run_all = next(
        (m for m in pipe_file.classes["Pipeline"].methods.values() if m.name == "RunAll"),
        None,
    )
    assert run_all is not None
    variadic = [p for p in run_all.parameters if p.is_variadic]
    assert variadic, f"RunAll has no variadic parameter; params: {run_all.parameters}"


# ── Goroutine call site ────────────────────────────────────────────────────────

def test_e2e_run_all_has_goroutine_callsite(tmp_path):
    """RunAll launches `go p.execute(...)` — must have a call site with is_goroutine=True."""
    analysis = _analysis(tmp_path)
    pipe_file = analysis.get_file("pipeline/pipeline.go")
    run_all = next(
        (m for m in pipe_file.classes["Pipeline"].methods.values() if m.name == "RunAll"),
        None,
    )
    assert run_all is not None
    goroutine_sites = [cs for cs in run_all.call_sites if cs.is_goroutine]
    assert goroutine_sites, f"RunAll has no goroutine call site; sites: {run_all.call_sites}"


# ── Cyclomatic complexity ──────────────────────────────────────────────────────

def test_e2e_execute_cyclomatic_complexity(tmp_path):
    """execute() has an `if err != nil` branch — cyclomatic_complexity must be >= 2."""
    analysis = _analysis(tmp_path)
    pipe_file = analysis.get_file("pipeline/pipeline.go")
    execute = next(
        (m for m in pipe_file.classes["Pipeline"].methods.values() if m.name == "execute"),
        None,
    )
    assert execute is not None
    assert execute.cyclomatic_complexity >= 2, (
        f"execute.cyclomatic_complexity should be >= 2; got {execute.cyclomatic_complexity}"
    )


# ── Call graph (level 2) ───────────────────────────────────────────────────────

def test_e2e_call_graph_edges_present(tmp_path):
    analysis = _analysis(tmp_path, level=AnalysisLevel.call_graph)
    edges = analysis.get_call_graph_edges()
    assert len(edges) > 0, "call graph must not be empty"


def test_e2e_specific_edge_main_to_calc_new(tmp_path):
    """main() calls calc.New — this named edge must exist in the call graph."""
    analysis = _analysis(tmp_path, level=AnalysisLevel.call_graph)
    targets = {e.target for e in analysis.get_call_graph_edges()}
    assert "example.com/cldk-e2e/calc.New" in targets, (
        f"call graph missing edge to calc.New; targets: {sorted(targets)}"
    )


def test_e2e_specific_edge_run_all_to_execute(tmp_path):
    """RunAll spawns a goroutine calling execute — that edge must exist."""
    analysis = _analysis(tmp_path, level=AnalysisLevel.call_graph)
    edges = {(e.source, e.target) for e in analysis.get_call_graph_edges()}
    assert (
        "example.com/cldk-e2e/pipeline.Pipeline.RunAll",
        "example.com/cldk-e2e/pipeline.Pipeline.execute",
    ) in edges, f"RunAll->execute edge missing; edges: {sorted(edges)}"


def test_e2e_cross_package_edges(tmp_path):
    """At least one edge must cross main→calc and one main→pipeline."""
    analysis = _analysis(tmp_path, level=AnalysisLevel.call_graph)
    sources = {e.source for e in analysis.get_call_graph_edges()}
    targets = {e.target for e in analysis.get_call_graph_edges()}
    calc_target = any("cldk-e2e/calc." in t for t in targets)
    pipeline_target = any("cldk-e2e/pipeline." in t for t in targets)
    assert calc_target, "no call-graph edge into the calc package"
    assert pipeline_target, "no call-graph edge into the pipeline package"


def test_e2e_no_dangling_call_graph_nodes(tmp_path):
    """Every edge endpoint must correspond to a signature in the symbol table."""
    analysis = _analysis(tmp_path, level=AnalysisLevel.call_graph)
    all_sigs = set()
    for go_file in analysis.get_symbol_table().values():
        for sig in go_file.functions:
            all_sigs.add(sig)
        for go_type in go_file.classes.values():
            for sig in go_type.methods:
                all_sigs.add(sig)

    dangling = []
    for edge in analysis.get_call_graph_edges():
        if edge.source not in all_sigs:
            dangling.append(f"source {edge.source!r}")
        if edge.target not in all_sigs:
            dangling.append(f"target {edge.target!r}")
    assert not dangling, f"dangling call-graph nodes: {dangling}"


# ── Caching (idempotency) ──────────────────────────────────────────────────────

def test_e2e_second_run_reuses_cache(tmp_path):
    """Running analysis twice with eager=False must not re-invoke the binary."""
    import time
    # First run (eager=True to seed the cache).
    GoAnalysis(
        project_dir=FIXTURE_DIR,
        analysis_level=AnalysisLevel.symbol_table,
        eager_analysis=True,
        backend=GoCodeAnalyzerConfig(cache_dir=tmp_path),
    )
    mtime_after_first = (tmp_path / "go" / "analysis.json").stat().st_mtime

    time.sleep(0.05)

    # Second run (eager=False) — must reuse the cached file.
    GoAnalysis(
        project_dir=FIXTURE_DIR,
        analysis_level=AnalysisLevel.symbol_table,
        eager_analysis=False,
        backend=GoCodeAnalyzerConfig(cache_dir=tmp_path),
    )
    mtime_after_second = (tmp_path / "go" / "analysis.json").stat().st_mtime

    assert mtime_after_first == mtime_after_second, (
        "analysis.json was rewritten on the second run despite eager=False"
    )
