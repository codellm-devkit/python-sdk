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

"""The local Go analysis backend: run ``cango`` as a subprocess, read the ``analysis.json`` v2
envelope, validate it into :class:`~cldk.models.go.GoAnalysis`, and answer every
:class:`~cldk.analysis.go.backend.GoAnalysisBackend` method from the in-memory tree.

The binary ships (post-release) with the ``codeanalyzer-go`` PyPI dependency; ``$CODEANALYZER_GO_BIN``
is the out-of-band override used today, before the wheel is pinned.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, List, Union

import networkx as nx

from cldk.analysis.commons.backend_config import cache_subdir
from cldk.analysis.commons.levels import analyzer_level
from cldk.analysis.go.backend import ARTIFACT_LAYER_UNAVAILABLE, GoAnalysisBackend
from cldk.models.go import (
    GoAnalysis,
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoField,
    GoModule,
    GoParam,
    GoType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

class GoCodeanalyzer(GoAnalysisBackend):
    """Run ``cango`` and answer queries over the parsed ``analysis.json``.

    Attributes:
        analysis: The whole ``analysis.json`` v2 envelope (``schema_version``, ``max_level``,
            ``analyzer``, ``application``).
        application: The application root — symbol table + call graph.
        application_name: The ``--app-name`` anchor the analyzer stamped into every id, read back
            off the ``can://<app>`` root id. Spelled the same as the Neo4j backend's
            ``application_name`` so a raised message names the application identically whichever
            backend raised it.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        cache_dir: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
    ) -> None:
        self.project_dir = project_dir
        self.cache_dir = cache_dir
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        # analyzer_level maps the AnalysisLevel enum/name/value to its integer, raising on an
        # unknown level rather than silently defaulting to 1 (the bug a hand-rolled dict caused).
        # cango has no L3/L4, so _argv caps the result at 2.
        self.analysis: GoAnalysis = self._init_codeanalyzer(analyzer_level(analysis_level))
        self.application: GoApplication = self.analysis.application
        self.application_name: str = self.application.id.removeprefix("can://")
        self._call_graph: nx.DiGraph | None = None
        self._index()

    # -----[ binary resolution ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """Resolve the ``cango`` executable command.

        ``$CODEANALYZER_GO_BIN`` is the out-of-band override (a locally built binary); otherwise the
        binary ships inside the ``codeanalyzer-go`` PyPI package (platform wheel), mirroring the TS
        backend. The wheel arm lights up once the analyzer release is pinned.
        """
        env_bin = os.environ.get("CODEANALYZER_GO_BIN")
        if env_bin:
            return shlex.split(env_bin)
        try:
            import codeanalyzer_go  # type: ignore[import-not-found]

            return [str(codeanalyzer_go.bin_path())]
        except (ModuleNotFoundError, FileNotFoundError, AttributeError) as e:
            raise CodeanalyzerExecutionException(
                "codeanalyzer-go binary not found: $CODEANALYZER_GO_BIN is unset and the "
                f"`codeanalyzer-go` wheel is not importable or carries no binary for this platform ({e}). "
                "Install it with `pip install codeanalyzer-go`, or set $CODEANALYZER_GO_BIN."
            ) from e

    def _argv(self, analysis_level: int, output_dir: Path | None) -> List[str]:
        """The cango command line: ``-i <project> --app-name <project.name> -a <1|2>
        [-o <dir> --cache-dir <dir>] --skip-tests [--eager] [-t <file>]...``.

        . The level is capped at 2 — cango produces no L3/L4.
        """
        project = Path(self.project_dir)
        args = self._get_codeanalyzer_exec() + [
            "-i",
            str(project),
            "--app-name",
            project.name,
            "-a",
            str(min(analysis_level, 2)),
        ]
        if output_dir is not None:
            args += ["-o", str(output_dir), "--cache-dir", str(output_dir)]
        args += ["--skip-tests"]
        if self.eager_analysis:
            args += ["--eager"]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]
        return args

    @staticmethod
    def _cached_level(analysis_json_file: Path) -> int | None:
        """The ``max_level`` of a cached ``analysis.json``, or ``None`` if it is absent, unreadable,
        or malformed — in which case the caller re-runs rather than trusting it."""
        try:
            data = json.loads(analysis_json_file.read_text(encoding="utf-8"))
            level = data.get("max_level")
            return int(level) if isinstance(level, int) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _init_codeanalyzer(self, analysis_level: int) -> GoAnalysis:
        """Run cango and return the validated v2 envelope.

        With no cache directory the output is read from the subprocess stdout pipe; with one, the
        analysis.json is persisted under ``<cache>/go`` and reused unless ``eager_analysis``,
        ``target_files``, or a cache **shallower than the requested level** force a re-run. A cache
        at or above the requested level is reused as-is: the schema is additive (``L1 ⊆ L2``), so a
        deeper cache already contains everything a shallower request needs.
        """
        output_dir = cache_subdir(self.cache_dir, self.project_dir, "go")
        if output_dir is None:
            args = self._argv(analysis_level, None)
            try:
                proc: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                raise CodeanalyzerExecutionException(f"cango failed: {e.stderr or e}") from e
            return GoAnalysis.model_validate_json(proc.stdout)

        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_json_file = output_dir / "analysis.json"
        # cango caps at 2, so a request above 2 is satisfied by a level-2 cache (never re-runs for a
        # level it cannot produce). Compare the requested level, capped, against the cached level.
        requested = min(analysis_level, 2)
        cached_level = self._cached_level(analysis_json_file) if analysis_json_file.exists() else None
        stale = cached_level is None or cached_level < requested
        needs_run = self.eager_analysis or bool(self.target_files) or stale
        if needs_run:
            args = self._argv(analysis_level, output_dir)
            try:
                subprocess.run(args, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                raise CodeanalyzerExecutionException(f"cango failed: {e.stderr or e}") from e
            if not analysis_json_file.exists():
                raise CodeanalyzerExecutionException("cango did not generate analysis.json.")
        return GoAnalysis.model_validate_json(analysis_json_file.read_text(encoding="utf-8"))

    # -----[ indexing ]-----
    def _index(self) -> None:
        """Flatten the symbol table into lookups and an id → (module, node) index for source
        slicing. Types are keyed by their durable ``id`` (a Go ``type`` carries no ``signature``,
        and its ``types{}`` map key is a bare short name that is not unique across packages);
        callables by their ``signature``. Go's call-graph endpoints are full ``can://`` ids that
        are themselves the callable ids, so no id → key indirection is needed (unlike TS)."""
        self._types: Dict[str, GoType] = {}  # type id -> type
        self._functions: Dict[str, GoCallable] = {}  # signature -> function
        self._methods_by_type: Dict[str, Dict[str, GoCallable]] = {}  # type id -> {method sig -> method}
        self._callables: Dict[str, GoCallable] = {}  # id -> callable
        self._file_of: Dict[str, str] = {}  # type id / callable signature -> module file path
        #: durable node id -> (owning module, node) for get_source slicing.
        self._source_of: Dict[str, tuple] = {}
        #: owning module id -> its source encoded UTF-8 once, for a non-ASCII module (byte-offset
        #: slicing); ASCII modules are absent and indexed directly. Encoded lazily in get_source.
        self._module_bytes: Dict[str, bytes] = {}

        for fp, mod in self.application.symbol_table.items():
            for fn in mod.functions.values():
                self._functions[fn.signature] = fn
                self._add_callable(fn, mod, fp)
            for ty in mod.types.values():
                self._types[ty.id] = ty
                self._file_of[ty.id] = fp
                self._source_of[ty.id] = (mod, ty)
                methods: Dict[str, GoCallable] = {}
                for m in ty.callables.values():
                    self._add_callable(m, mod, fp)
                    methods[m.signature] = m
                self._methods_by_type[ty.id] = methods
                for fld in ty.fields.values():
                    self._source_of[fld.id] = (mod, fld)

    def _add_callable(self, c: GoCallable, mod: GoModule, fp: str) -> None:
        self._callables[c.id] = c
        self._file_of[c.signature] = fp
        self._source_of[c.id] = (mod, c)
        for inner in c.callables.values():
            self._add_callable(inner, mod, fp)

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> GoApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, GoModule]:
        return self.application.symbol_table

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """The application's call graph as a NetworkX DiGraph: nodes keyed by ``can://`` callable
        id, edges carrying ``weight`` and ``provenance`` (as the sibling backends do)."""
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for edge in self.application.call_graph:
            graph.add_node(edge.src, id=edge.src)
            graph.add_node(edge.dst, id=edge.dst)
            graph.add_edge(edge.src, edge.dst, type="CALL_DEP", weight=edge.weight, provenance=tuple(edge.prov))
        self._call_graph = graph
        return graph

    def get_call_graph_json(self) -> str:
        return self.application.model_dump_json()

    def get_callers(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``dst`` is ``callable_id`` — the callables that call it."""
        return [e for e in self.application.call_graph if e.dst == callable_id]

    def get_callees(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``src`` is ``callable_id`` — the callables it calls."""
        return [e for e in self.application.call_graph if e.src == callable_id]

    # -----[ types (class-named base slots; Go has no class — see backend module docstring) ]-----
    def get_all_classes(self) -> Dict[str, GoType]:
        """Every Go type (struct + interface), keyed by its durable ``can://`` id. The base ABC's
        class-named slot; the facade exposes this as ``get_types``."""
        return self._types

    def get_class(self, qualified_class_name: str) -> GoType | None:
        return self._types.get(qualified_class_name)

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, GoCallable]:
        """The receiver methods of a type, keyed by signature. The facade exposes this as
        ``get_methods_of``."""
        return self._methods_by_type.get(qualified_class_name, {})

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> GoCallable | None:
        return self._methods_by_type.get(qualified_class_name, {}).get(qualified_method_name)

    def get_all_fields(self, qualified_class_name: str) -> List[GoField]:
        ty = self._types.get(qualified_class_name)
        return list(ty.fields.values()) if ty else []

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[GoParam]:
        m = self.get_method(qualified_class_name, qualified_method_name)
        return list(m.parameters) if m else []

    # -----[ functions + source ]-----
    def get_functions(self) -> Dict[str, GoCallable]:
        return self._functions

    def get_source(self, node_id: str) -> str:
        """The source text of ``node_id`` — ``module.source`` sliced by ``node.span.bytes`` — for a
        durable-id node (type, callable, field).

        ``span.bytes`` are **UTF-8 byte** offsets, so a non-ASCII module is sliced on the encoded
        bytes and decoded back (an ASCII module is indexed directly, paying nothing), the same way
        the TS ``_Spanned.code`` property does. Slicing the ``str`` by character index would drift
        on any file with a multibyte character before the span.
        """
        entry = self._source_of.get(node_id)
        if entry is None:
            raise CodeanalyzerExecutionException(
                f"no source for {node_id!r}: not a type, callable or field of application "
                f"{self.application_name!r}"
            )
        mod, node = entry
        if node.span is None:
            raise CodeanalyzerExecutionException(f"node {node_id!r} carries no span, so it has no source text")
        b0, b1 = node.span.bytes
        if mod.source.isascii():
            return mod.source[b0:b1]
        encoded = self._module_bytes.get(mod.id)
        if encoded is None:
            encoded = mod.source.encode("utf-8")
            self._module_bytes[mod.id] = encoded
        return encoded[b0:b1].decode("utf-8")

    # -----[ artifact / dependency / config layer — not emitted by a local L1/L2 analysis ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))
