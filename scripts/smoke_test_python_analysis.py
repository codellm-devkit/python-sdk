"""Smoke test for PythonAnalysis against a real project.

Runs every public method on :class:`cldk.analysis.python.python_analysis.PythonAnalysis`
against a project directory (default: /home/rkrsn/workspace/odoo) and prints a
PASS / NOT_IMPLEMENTED / FAIL summary for each.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/smoke_test_python_analysis.py [project_dir]
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from cldk import CLDK
from cldk.analysis import AnalysisLevel


PASS, SKIP, FAIL = "PASS", "NOT_IMPL", "FAIL"


def _short(value: Any, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def run(label: str, fn: Callable[[], Any]) -> tuple[str, str, float]:
    start = time.perf_counter()
    try:
        result = fn()
    except NotImplementedError as exc:
        return SKIP, str(exc) or "NotImplementedError", time.perf_counter() - start
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=2).splitlines()[-1]
        return FAIL, f"{type(exc).__name__}: {exc} ({tb})", time.perf_counter() - start
    summary = _summarize(result)
    return PASS, summary, time.perf_counter() - start


def _summarize(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, dict):
        keys = list(value.keys())
        head = ", ".join(_short(k, 30) for k in keys[:3])
        return f"dict(len={len(value)}, head=[{head}])"
    if isinstance(value, (list, tuple, set)):
        head = ", ".join(_short(v, 30) for v in list(value)[:3])
        return f"{type(value).__name__}(len={len(value)}, head=[{head}])"
    if isinstance(value, str):
        return f"str(len={len(value)})"
    name = type(value).__name__
    if hasattr(value, "number_of_nodes"):
        return f"DiGraph(nodes={value.number_of_nodes()}, edges={value.number_of_edges()})"
    return f"{name}({_short(value, 50)})"


def pick_class_and_method(analysis) -> tuple[str | None, str | None]:
    classes = analysis.get_classes()
    if not classes:
        return None, None
    for class_sig, cls in classes.items():
        if cls.methods:
            method_sig = next(iter(cls.methods))
            return class_sig, method_sig
    return next(iter(classes)), None


def main() -> int:
    project_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/rkrsn/workspace/odoo")
    if not project_dir.exists():
        print(f"project_dir does not exist: {project_dir}")
        return 2

    print(f"== Initializing CLDK Python analysis on: {project_dir}")
    init_start = time.perf_counter()
    analysis = CLDK(language="python").analysis(
        project_path=project_dir,
        analysis_level=AnalysisLevel.call_graph,
        eager=False,
    )
    init_elapsed = time.perf_counter() - init_start
    print(f"   initialized in {init_elapsed:.1f}s\n")

    class_sig, method_sig = pick_class_and_method(analysis)
    print(f"== Probe target: class={class_sig!r}, method={method_sig!r}\n")

    sample_module_path: str | None = None
    symbol_table = analysis.get_symbol_table()
    if symbol_table:
        sample_module_path = next(iter(symbol_table))

    sample_source = "def f(x):\n    return x + 1\n"

    cases: list[tuple[str, Callable[[], Any]]] = [
        # treesitter passthrough
        ("is_parsable", lambda: analysis.is_parsable(sample_source)),
        ("get_raw_ast", lambda: analysis.get_raw_ast(sample_source)),
        # application view
        ("get_application_view", analysis.get_application_view),
        ("get_symbol_table", analysis.get_symbol_table),
        ("get_modules", analysis.get_modules),
        ("get_python_file", lambda: analysis.get_python_file(class_sig or "")),
        ("get_python_module", lambda: analysis.get_python_module(sample_module_path or "")),
        # imports
        ("get_imports", analysis.get_imports),
        # call graph
        ("get_call_graph", analysis.get_call_graph),
        ("get_call_graph_json", analysis.get_call_graph_json),
        ("get_callers", lambda: analysis.get_callers(class_sig or "", method_sig or "")),
        ("get_callees", lambda: analysis.get_callees(class_sig or "", method_sig or "")),
        ("get_class_call_graph", lambda: analysis.get_class_call_graph(class_sig or "", method_sig)),
        # methods
        ("get_methods", analysis.get_methods),
        ("get_methods_in_class", lambda: analysis.get_methods_in_class(class_sig or "")),
        ("get_method", lambda: analysis.get_method(class_sig or "", method_sig or "")),
        ("get_method_parameters", lambda: analysis.get_method_parameters(class_sig or "", method_sig or "")),
        ("get_constructors", lambda: analysis.get_constructors(class_sig or "")),
        # classes
        ("get_classes", analysis.get_classes),
        ("get_class", lambda: analysis.get_class(class_sig or "")),
        ("get_classes_by_criteria", lambda: analysis.get_classes_by_criteria(inclusions=["odoo"], exclusions=["test"])),
        ("get_fields", lambda: analysis.get_fields(class_sig or "")),
        ("get_nested_classes", lambda: analysis.get_nested_classes(class_sig or "")),
        ("get_sub_classes", lambda: analysis.get_sub_classes(class_sig or "")),
        ("get_extended_classes", lambda: analysis.get_extended_classes(class_sig or "")),
        # unsupported / parity stubs (expected NOT_IMPL)
        ("get_class_hierarchy", analysis.get_class_hierarchy),
        ("get_service_entry_point_classes", analysis.get_service_entry_point_classes),
        ("get_service_entry_point_methods", analysis.get_service_entry_point_methods),
        ("get_entry_point_classes", analysis.get_entry_point_classes),
        ("get_entry_point_methods", analysis.get_entry_point_methods),
        ("get_implemented_interfaces", lambda: analysis.get_implemented_interfaces(class_sig or "")),
        ("get_methods_with_decorators", lambda: analysis.get_methods_with_decorators(["staticmethod"])),
        ("get_test_methods", analysis.get_test_methods),
        ("get_calling_lines", lambda: analysis.get_calling_lines(method_sig or "")),
        ("get_call_targets", lambda: analysis.get_call_targets({})),
        ("get_all_crud_operations", analysis.get_all_crud_operations),
        ("get_all_create_operations", analysis.get_all_create_operations),
        ("get_all_read_operations", analysis.get_all_read_operations),
        ("get_all_update_operations", analysis.get_all_update_operations),
        ("get_all_delete_operations", analysis.get_all_delete_operations),
    ]

    width = max(len(name) for name, _ in cases)
    counts = {PASS: 0, SKIP: 0, FAIL: 0}
    failures: list[tuple[str, str]] = []
    for name, fn in cases:
        status, detail, elapsed = run(name, fn)
        counts[status] += 1
        print(f"{status:8s} {name:<{width}}  {elapsed*1000:7.1f}ms  {detail}")
        if status == FAIL:
            failures.append((name, detail))

    total = sum(counts.values())
    print()
    print(f"== {total} APIs exercised — PASS={counts[PASS]}  NOT_IMPL={counts[SKIP]}  FAIL={counts[FAIL]}")
    if failures:
        print("\n== Failures ==")
        for name, detail in failures:
            print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
