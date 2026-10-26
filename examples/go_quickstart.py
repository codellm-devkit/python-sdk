#!/usr/bin/env python3
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

"""Runnable Go Quick Start — the README's Go snippet against a real Go module.

Go is CLDK's first type-centric, class-less facade: a type is a struct or interface, and
package-level functions are kept distinct from receiver methods, so the surface is
``get_types`` / ``get_functions`` / ``get_methods_of`` — never ``get_classes``.

Both `--backend` choices below run the same in-process `GoCodeanalyzer` over `CLDK.go(...)`;
they differ only in *where the cango binary comes from*, which is the one axis
`GoCodeanalyzer._get_codeanalyzer_exec` chooses on:

* ``cldk``  — the ``cango`` bundled in the pinned ``codeanalyzer-go`` wheel (resolved by
  ``codeanalyzer_go.bin_path()``). Nothing to install or point at; this is the default.
* ``local`` — a hand-built ``cango`` named by ``$CODEANALYZER_GO_BIN``. That env var *shadows*
  the wheel when set, so the ``cldk`` run unsets it for the duration to exercise the wheel honestly.

Usage:

    # cldk backend — the bundled wheel's cango; point at a Go MODULE (a directory with a go.mod):
    python examples/go_quickstart.py /path/to/go/module                 # --backend cldk (default)

    # local backend — your own cango build (env var shadows the wheel while set):
    export CODEANALYZER_GO_BIN=/path/to/cango
    python examples/go_quickstart.py --backend local /path/to/go/module

    # run both, back to back:
    python examples/go_quickstart.py --backend both /path/to/go/module

    # also run the read-only Neo4j backend against a graph a `cango --emit neo4j` run populated:
    export CLDK_GO_NEO4J_URI=bolt://localhost:7687
    export CLDK_GO_NEO4J_USER=neo4j                  # optional, defaults to neo4j
    export CLDK_GO_NEO4J_PASSWORD=...
    export CLDK_GO_NEO4J_APP=go/my-app               # the --app-name the graph was emitted with
    python examples/go_quickstart.py /path/to/go/module

The Neo4j section is skipped (with a note) when those env vars are not set.
"""

from __future__ import annotations

import argparse
import os

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

LIMIT = 10  # keep the console output readable on a large module

#: The analysis levels cango produces — 1 (symbol table) and 2 (symbol table + call graph) only.
#: Go has no L3/L4 (no CFG/CDG/DDG), so the other two AnalysisLevel members are intentionally not
#: offered here. Mapped from the CLI's integer choice to the enum.
_LEVELS = {1: AnalysisLevel.symbol_table, 2: AnalysisLevel.call_graph}


def _short(node_id: str) -> str:
    """The last path segment of a can:// id, for readable printing."""
    return node_id.rstrip("/").split("/")[-1]


def _run(project_path: str, level: AnalysisLevel, eager: bool, *, banner: str) -> None:
    """The in-process backend: run cango over the module and query the parsed analysis.json.

    The caller has already selected the cango binary source (wheel vs. ``$CODEANALYZER_GO_BIN``)
    before entering here — this body is identical whichever one resolved, because both land on the
    same ``GoCodeanalyzer``.
    """
    print("=" * 72)
    print(f"{banner} — {project_path}  [level: {level.value}{', eager' if eager else ''}]")
    print("=" * 72)

    analysis = CLDK.go(project_path=project_path, analysis_level=level, eager=eager)

    types = analysis.get_types()
    functions = analysis.get_functions()
    print(f"\n{len(types)} type(s), {len(functions)} package-level function(s)\n")

    # Walk the types (structs + interfaces) and their receiver methods, addressed by the type id.
    for type_id, go_type in list(types.items())[:LIMIT]:
        print(f"{go_type.kind} {_short(type_id)}")
        for sig, method in analysis.get_methods_of(type_id).items():
            params = ", ".join(f"{p.name} {p.type}" for p in method.parameters)
            print(f"    method {_short(sig)}({params}) -> {method.return_type or ''}")
    if len(types) > LIMIT:
        print(f"    ... and {len(types) - LIMIT} more type(s)")

    # Package-level functions are separate from methods — no "module-as-class" hack.
    print()
    for sig in list(functions)[:LIMIT]:
        print(f"func {_short(sig)}")
    if len(functions) > LIMIT:
        print(f"... and {len(functions) - LIMIT} more function(s)")

    # Source of one callable, sliced from its module's source by the node's span.
    a_callable = next(iter(functions.values()), None) or next(
        (m for tid in types for m in analysis.get_methods_of(tid).values()), None
    )
    if a_callable is not None:
        print(f"\n--- source of {_short(a_callable.signature)} ---")
        print(analysis.get_source(a_callable.id))

    # Call graph: who calls whom, with provenance and weight, keyed on the callable id. Only
    # populated at level 2 — at level 1 cango emits the symbol table alone, so the graph is empty
    # by construction, not because the project has no calls.
    if level is AnalysisLevel.symbol_table:
        print("\ncall graph: not computed at level 1 (symbol_table); re-run with --level 2")
        return
    cg = analysis.get_call_graph()
    print(f"\ncall graph: {cg.number_of_nodes()} node(s), {cg.number_of_edges()} edge(s)")
    shown = 0
    for func_id in functions:
        for edge in analysis.get_callees(func_id):
            print(f"  {_short(edge.src)} -> {_short(edge.dst)}  ({', '.join(edge.prov)}, weight={edge.weight})")
            shown += 1
            if shown >= LIMIT:
                break
        if shown >= LIMIT:
            break


def run_cldk(project_path: str, level: AnalysisLevel, eager: bool) -> None:
    """Analyze on the fly using the ``cango`` bundled in the pinned ``codeanalyzer-go`` wheel.

    ``$CODEANALYZER_GO_BIN`` shadows the wheel in ``_get_codeanalyzer_exec`` whenever it is set, so
    we pop it for the duration of this call — otherwise a stray env var would silently make the
    "cldk" run actually use someone's hand-built binary. It is restored on the way out.
    """
    saved = os.environ.pop("CODEANALYZER_GO_BIN", None)
    try:
        _run(project_path, level, eager, banner="CLDK backend (bundled codeanalyzer-go wheel)")
    finally:
        if saved is not None:
            os.environ["CODEANALYZER_GO_BIN"] = saved


def run_local(project_path: str, level: AnalysisLevel, eager: bool) -> None:
    """Analyze with a hand-built ``cango`` named by ``$CODEANALYZER_GO_BIN`` (set it first)."""
    if not os.environ.get("CODEANALYZER_GO_BIN"):
        print(
            "\n(--backend local needs $CODEANALYZER_GO_BIN pointing at a cango build; "
            "unset, so this would fall back to the wheel. Use --backend cldk, or set the env var.)"
        )
        return
    _run(project_path, level, eager, banner="LOCAL backend ($CODEANALYZER_GO_BIN cango)")


def run_neo4j() -> bool:
    """The read-only Neo4j backend, if the env is set. Returns True when it ran."""
    uri = os.environ.get("CLDK_GO_NEO4J_URI")
    password = os.environ.get("CLDK_GO_NEO4J_PASSWORD")
    app = os.environ.get("CLDK_GO_NEO4J_APP")
    if not (uri and password and app):
        print("\n(Neo4j backend skipped — set CLDK_GO_NEO4J_URI / _PASSWORD / _APP to run it.)")
        return False

    print("\n" + "=" * 72)
    print(f"NEO4J backend (read-only) — {uri}, app {app}")
    print("=" * 72)

    analysis = CLDK.go(
        backend=Neo4jConnectionConfig(
            uri=uri,
            username=os.environ.get("CLDK_GO_NEO4J_USER", "neo4j"),
            password=password,
            application_name=app,  # the graph is populated out of band by `cango --emit neo4j`
        ),
    )
    types = analysis.get_types()
    functions = analysis.get_functions()
    cg = analysis.get_call_graph()
    print(f"\n{len(types)} type(s), {len(functions)} function(s), {cg.number_of_edges()} call edge(s)")
    for type_id in list(types)[:LIMIT]:
        print(f"  type {_short(type_id)}: {len(analysis.get_methods_of(type_id))} method(s)")
    # The backend owns a driver; close it when done.
    close = getattr(analysis.backend, "close", None)
    if close:
        close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Go Quick Start against a Go module.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", help="path to a Go module (a directory containing go.mod)")
    parser.add_argument(
        "-b",
        "--backend",
        choices=("cldk", "local", "both"),
        default="cldk",
        help="which cango to analyze with: cldk = the bundled codeanalyzer-go wheel (default); "
        "local = your own build named by $CODEANALYZER_GO_BIN; both = run them in turn. Both "
        "choices use the same in-process GoCodeanalyzer — only the binary source differs.",
    )
    parser.add_argument(
        "-a",
        "--level",
        type=int,
        choices=(1, 2),
        default=2,
        help="analysis level for the local backend: 1 = symbol table, 2 = symbol table + call graph "
        "(default). cango produces only 1 and 2; Go has no L3/L4. The Neo4j graph is always "
        "full-depth, so this flag affects the local run only.",
    )
    parser.add_argument(
        "-e",
        "--eager",
        action="store_true",
        help="force a fresh cango run, ignoring any cached analysis.json under "
        "<project>/.codeanalyzer/go. Use this if a cache from an earlier, shallower level (e.g. a "
        "prior -a 1 run) is being reused for a deeper request — the cache is reused by existence, "
        "not by level.",
    )
    args = parser.parse_args()

    level = _LEVELS[args.level]
    if args.backend in ("cldk", "both"):
        run_cldk(args.project_path, level, args.eager)
    if args.backend in ("local", "both"):
        run_local(args.project_path, level, args.eager)
    run_neo4j()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
