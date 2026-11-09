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
from functools import cached_property, partial
from pathlib import Path
from subprocess import CompletedProcess
from typing import Dict, FrozenSet, Iterator, List, Mapping, Sequence, Tuple, Union

import networkx as nx

from cldk.analysis.commons.identity import resolve_identity, validate_identity, cache_matches
from cldk.analysis.commons.backend_config import cache_subdir
from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    check_depth,
    check_distinct_endpoints,
    check_max_nodes,
    check_max_paths,
    check_page_size,
    edge_page,
)
from cldk.analysis.commons.graphs import call_reaches, cone_sinks, flow_path, shortest_walks, slice_resolved, under_callable
from cldk.analysis.commons.levels import analyzer_level
from cldk.analysis.commons.resolve import (
    CallableCandidate,
    body_node_kind,
    resolve_callable_signature,
    resolve_value_name,
    resolve_within,
)
from cldk.analysis.commons.results import Diagnostic, EdgePage, FlowPath, FlowPaths, Slice, SliceNode
from cldk.analysis.go.backend import (
    ARTIFACT_LAYER_UNAVAILABLE,
    CDG_ORDER,
    CFG_ORDER,
    DDG_ORDER,
    PORTS_DISCONNECTED,
    VIA,
    GoAnalysisBackend,
)
from cldk.analysis.go.ids import body_node_id, callable_match_names, external_signature, go_value_names, pick_declaration, split_body_node_id
from cldk.models.go import (
    GoAnalysis,
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoCdgEdge,
    GoCfgEdge,
    GoDdgEdge,
    GoDegraded,
    GoExternalSymbol,
    GoField,
    GoModule,
    GoParam,
    GoType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, CodeanalyzerUsageException


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
        *,
        application_name: str | None = None,
        namespace: str | None = None,
    ) -> None:
        self.identity = resolve_identity(application_name, namespace, project_root=Path(project_dir) if project_dir is not None else None)

        self.project_dir = project_dir
        self.cache_dir = cache_dir
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        # analyzer_level maps the AnalysisLevel enum/name/value to its integer, raising on an
        # unknown level rather than silently defaulting to 1 (the bug a hand-rolled dict caused).
        # _argv caps the result at _MAX_LEVEL, which is 4 since codeanalyzer-go 0.2.0.
        self._level: int = analyzer_level(analysis_level)
        self.analysis: GoAnalysis = self._init_codeanalyzer(self._level)
        validate_identity(self.analysis, self.identity)
        self.application: GoApplication = self.analysis.application
        self.application_name: str = self.identity.application_name
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

    #: The analysis level cango can be asked for. 4 since codeanalyzer-go 0.2.0, which added the
    #: L3 intraprocedural graphs and the L4 interprocedural SDG (``-a 3`` / ``-a 4``, PR
    #: codeanalyzer-go#36). Before it, every request was capped at 2 because nothing deeper existed
    #: to ask for; leaving that cap in place is what made a level-4 request silently yield a
    #: level-2 analysis, and then an empty dataflow answer indistinguishable from a proved absence.
    _MAX_LEVEL = 4

    #: The level at which ``cfg``/``cdg``/``ddg`` first exist, so :meth:`_require_dataflow` names it
    #: rather than hard-coding a bare 3 at each call site.
    _DATAFLOW_LEVEL = 3

    def _argv(self, analysis_level: int, output_dir: Path | None) -> List[str]:
        """The cango command line: ``-i <project> --app-name <name> --namespace <ns> -a <1..4>
        [-o <dir> --cache-dir <dir>] --skip-tests [--eager] [-t <file>]...``.

        The level is capped at :attr:`_MAX_LEVEL`, which is what the binary accepts, so a caller
        asking for more gets the deepest analysis available rather than a ``cango`` usage error.
        """
        project = Path(self.project_dir)
        args = self._get_codeanalyzer_exec() + [
            "-i",
            str(project),
            f"--app-name={self.identity.application_name}",
            f"--namespace={self.identity.namespace}",
            "-a",
            str(min(analysis_level, self._MAX_LEVEL)),
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
        at or above the requested level is reused as-is: the schema is additive
        (``L1 ⊆ L2 ⊆ L3 ⊆ L4``), so a deeper cache already contains everything a shallower request
        needs.
        """
        output_dir = cache_subdir(self.cache_dir, self.project_dir, "go", identity=self.identity)
        if output_dir is None:
            args = self._argv(analysis_level, None)
            try:
                proc: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                raise CodeanalyzerExecutionException(f"cango failed: {e.stderr or e}") from e
            return GoAnalysis.model_validate_json(proc.stdout)

        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_json_file = output_dir / "analysis.json"
        # A request above what cango can produce is satisfied by a cache at the cap (never re-runs
        # for a level it cannot produce). Compare the requested level, capped, against the cached.
        requested = min(analysis_level, self._MAX_LEVEL)
        cached_level = self._cached_level(analysis_json_file) if analysis_json_file.exists() else None
        stale = cached_level is None or cached_level < requested or not cache_matches(analysis_json_file, self.identity)
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
        """The source text of ``node_id`` — ``module.source`` sliced by ``node.span.bytes``.

        ``node_id`` is a durable-id node (type, callable, field) **or a body-node id**: a statement,
        a call site or a vertex, named by the analyzer's own
        ``"<callable can:// id>@<body key>"``. The body-node form is what every edge endpoint and
        every :class:`~cldk.analysis.commons.results.SliceNode` carries, so without it a caller
        could read an id off a ``get_ddg`` page and then not use it — and the Neo4j backend accepts
        it (its ``:GoCanNode`` lookup does not care which kind of node it found), which made this
        a parity gap rather than a shared limitation. Python's ``get_source`` names the same two
        forms in its own contract.

        ``span.bytes`` are **UTF-8 byte** offsets, so a non-ASCII module is sliced on the encoded
        bytes and decoded back (an ASCII module is indexed directly, paying nothing), the same way
        the TS ``_Spanned.code`` property does. Slicing the ``str`` by character index would drift
        on any file with a multibyte character before the span.
        """
        entry = self._source_of.get(node_id) or self._body_source_of(node_id)
        if entry is None:
            raise CodeanalyzerExecutionException(
                f"no source for {node_id!r}: not a type, callable, field or body node of application {self.application_name!r}"
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

    def _body_source_of(self, node_id: str) -> "tuple | None":
        """``(module, body node)`` for a body-node id, or ``None`` if nothing owns it.

        The owner is found with :func:`~cldk.analysis.go.ids.split_body_node_id` rather than by
        splitting on ``@`` — trap 1: a library stand-in's own id holds ``/@external/`` before the
        vertex ``@``. A stand-in has no module and so no text, which this reports as "not found"
        rather than as a module lookup that would fail further on.

        Not indexed eagerly: the body-node ids outnumber the durable ones by a wide margin (3,324
        against 319 on the analyzer's own corpus), and ``get_source`` on one is the uncommon case,
        so the prefix walk is paid per call instead of the index being paid per application.
        """
        hit = split_body_node_id(node_id, self._dataflow_owners)
        if hit is None:
            return None
        cid, key = hit
        owner, _tid, path = self._dataflow_owners[cid]
        if not path:  # a stand-in: synthesized, so there is no source file behind it
            return None
        node = (owner.body or {}).get(key) or (owner.body or {}).get(key.lstrip("@"))
        if node is None:
            return None
        entry = self._source_of.get(cid)
        return (entry[0], node) if entry else None

    # =====================================================================================
    # L3/L4 DATAFLOW. See :class:`~cldk.analysis.go.backend.GoAnalysisBackend` for the contract and
    # for the four Go-specific traps; the id/name grammar those traps need is in
    # :mod:`cldk.analysis.go.ids` and is shared with the Neo4j backend so the two cannot drift.
    #
    # THIS BACKEND ANSWERS INTERPROCEDURALLY, for the reason the Python local backend does: a
    # level-4 run carries the whole SDG in the payload, just split across five lists —
    #
    #   GoCallable.ddg / .cdg    endpoints are callable-LOCAL body keys -> joined by body_node_id
    #   GoCallable.summary       a callee's pass-through at a call site, also local
    #   GoApplication.param_in   actual_in -> formal_in, endpoints ALREADY absolute ids
    #   GoApplication.param_out  formal_out -> actual_out, likewise
    #
    # — which are exactly the five the Neo4j projector emits as GO_DDG / GO_CDG / GO_SUMMARY /
    # GO_PARAM_IN / GO_PARAM_OUT. So the cross-callable index this backend lacks it can BUILD out of
    # the data the graph is projected from, and the answer it gives is the graph's answer rather
    # than a narrower intraprocedural one presented as complete.
    #
    # The honest cost: building it walks every callable once and is cached for the backend's life.
    # For the projects a local analysis is run on that is the cheaper trade; at Kubernetes scale
    # (1.71M SDG nodes, 5.78M dependence edges) it is not, and the answer there is the Neo4j
    # backend, which pushes the same traversal into Cypher. That asymmetry is why this backend's
    # docstring warns about ``-a 4`` payload size.
    # =====================================================================================

    def _require_dataflow(self) -> None:
        """Refuse, naming both levels, when this analysis was built below the dataflow pass.

        One guard for the per-callable graphs and the slices: they come from the same analyzer pass
        and go dark together. It **raises** rather than returning empty because at a shallower level
        an empty answer would mean "not analyzed" while looking exactly like "no dependence" (D7).
        """
        if self._level < self._DATAFLOW_LEVEL:
            raise CodeanalyzerUsageException(
                f"control and data flow need analysis_level 3 or deeper (cango -a 3 for the "
                f"per-callable cfg/cdg/ddg, -a 4 for the interprocedural SDG); this analysis was "
                f"built at level {self._level}, where the analyzer emits no cfg/cdg/ddg at all. "
                "Returning an empty result would be indistinguishable from a callable that has no "
                "dependence, so this raises instead. Rebuild with "
                "CLDK.go(..., analysis_level='system_dependency_graph')."
            )

    @property
    def _ports_carry_dependence(self) -> bool:
        """Whether any ``formal_in`` vertex of this application has an outgoing SDG edge.

        Asked of the data (see the ABC), and answered off the adjacency this backend already builds,
        so the probe costs nothing a slice would not have paid anyway. ``param_in`` alone is not the
        question: an edge *into* a port says the caller side was emitted, while what a slice needs
        is an edge *leaving* one — which is what carries the flow onward through the callee.
        """
        if self._level < self._DATAFLOW_LEVEL:
            return False
        forward = self._sdg[0]["forward"]
        return any(forward.get(ref) for ref in self._port_refs)

    def _require_connected_ports(self, accessor: str) -> None:
        """Refuse ``accessor`` when this application's ports carry no dependence (see the ABC)."""
        self._require_dataflow()
        if not self._ports_carry_dependence:
            raise CodeanalyzerExecutionException(PORTS_DISCONNECTED.format(accessor=accessor, app=self.application_name))

    # -----[ the SDG index ]-----
    def _iter_callables(self) -> Iterator[Tuple[GoCallable, str | None, str]]:
        """Every callable with its owning type id and module path — the resolution domain.

        Nested closures included (``GoCallable.callables``), because the analyzer gives each its own
        id, body and dataflow, and a flow through a Go closure is an ordinary flow. The owning type
        id is ``None`` for a package-level function and for a closure nested in one.
        """
        for path, mod in self.application.symbol_table.items():
            for fn in mod.functions.values():
                yield from self._descend(fn, None, path)
            for ty in mod.types.values():
                for m in ty.callables.values():
                    yield from self._descend(m, ty.id, path)

    def _descend(self, c: GoCallable, type_id: str | None, path: str) -> Iterator[Tuple[GoCallable, str | None, str]]:
        yield c, type_id, path
        for inner in c.callables.values():
            yield from self._descend(inner, type_id, path)

    @cached_property
    def _dataflow_owners(self) -> Dict[str, Tuple[GoCallable, str | None, str]]:
        """``{callable id: (callable, owning type id, module path)}``, stand-ins included.

        The stand-ins are in here because a library callable's ``formal_in``/``formal_out`` vertices
        are real nodes on real paths at L4 — a flow through ``database/sql.DB.Query`` passes through
        them — so a walk that could not describe them would report a path with holes in it. They
        carry no module path (``""``), which is what tells :meth:`_slice_node` to describe them as
        externals rather than inventing a file.
        """
        owners: Dict[str, Tuple[GoCallable, str | None, str]] = {c.id: (c, tid, path) for c, tid, path in self._iter_callables()}
        for sym in self.application.external_symbols.values():
            owners.setdefault(sym.id, (sym, None, ""))
        return owners

    @cached_property
    def _sdg(self) -> Tuple[Dict[str, Dict[str, Dict[str, list]]], Dict[str, Tuple[object, str, str]]]:
        """``(adjacency, node index)`` over the whole application's SDG, built once and cached.

        ``adjacency`` is ``{"forward": {src: {dst: [label]}}, "backward": {dst: {src: [label]}}}`` —
        both directions, because a backward slice is not derivable from a forward index without
        inverting it, and inverting per call is the same work repeated.

        A ``label`` is ``(relationship type, var, prov)``: what a path hop has to report, and what
        the graph carries on the corresponding relationship. It is a **list** per ``(src, dst)``
        pair because parallel edges are ordinary — one statement feeding one argument on several
        variables is several distinct paths — and collapsing them would merge several pieces of
        evidence into one.

        ``node index`` maps an absolute body-node id to ``(owning callable, local body key, module
        path)``, which is everything :meth:`_slice_node` needs to describe a reached node without a
        second walk.
        """
        forward: Dict[str, Dict[str, list]] = {}
        backward: Dict[str, Dict[str, list]] = {}
        nodes: Dict[str, Tuple[object, str, str]] = {}

        def link(src: str, dst: str, label: tuple) -> None:
            forward.setdefault(src, {}).setdefault(dst, []).append(label)
            backward.setdefault(dst, {}).setdefault(src, []).append(label)

        for cid, (c, _tid, path) in self._dataflow_owners.items():
            for key in c.body or {}:
                nodes[body_node_id(cid, key)] = (c, key, path)
            # The relationship name each list is projected as, so a hop reports the same ``via``
            # here as it does over the graph — ``GoAnalysisBackend.VIA`` is the single translation
            # table and neither backend has its own.
            for rel, edges in (("GO_DDG", getattr(c, "ddg", None)), ("GO_CDG", getattr(c, "cdg", None)), ("GO_SUMMARY", getattr(c, "summary", None))):
                for e in edges or []:
                    link(body_node_id(cid, e.src), body_node_id(cid, e.dst), (rel, getattr(e, "var", None), tuple(getattr(e, "prov", None) or ())))
        # These two carry absolute ids already (the analyzer resolved them through its identity
        # map), so they are used as-is — joining them again would mint ids that name nothing.
        for rel, edges in (("GO_PARAM_IN", self.application.param_in), ("GO_PARAM_OUT", self.application.param_out)):
            for e in edges or []:
                link(e.src, e.dst, (rel, e.var, ()))
        return {"forward": forward, "backward": backward}, nodes

    @cached_property
    def _port_refs(self) -> Tuple[str, ...]:
        """Every ``formal_in`` vertex id in the application — the domain
        :attr:`_ports_carry_dependence` probes and :meth:`flows_to_call` targets."""
        return tuple(body_node_id(cid, key) for cid, (c, _t, _p) in self._dataflow_owners.items() for key, n in (c.body or {}).items() if n.kind == "formal_in")

    def _slice_node(self, ref: str) -> SliceNode | None:
        """One reached body node as a :class:`SliceNode`, or ``None`` for an id this app does not own.

        The Neo4j twin must describe a node identically; the two share
        :func:`~cldk.analysis.commons.resolve.body_node_kind` (which decides the caller-facing kind
        and name) and differ only in where the fields are read from. A parameter-passing vertex has
        no span of its own, so the *callable's* first line stands in.
        """
        entry = self._sdg[1].get(ref)
        if entry is None:
            return None
        c, key, path = entry
        node = (c.body or {})[key]
        kind, name, defined_in = body_node_kind(node.kind, node.of)
        line = node.span.start[0] if node.span else (c.span.start[0] if getattr(c, "span", None) else 0)
        return SliceNode(file=path, line=line, callable=c.signature or c.id, kind=kind, name=name, defined_in=defined_in, source=None, ref=ref)

    # -----[ addressing ]-----
    @cached_property
    def _candidates(self) -> Tuple[List[CallableCandidate], Dict[str, List[Tuple[object, str | None, str]]]]:
        """``(candidates, {signature: owner}, colliding signatures)`` for :meth:`resolve_callable`.

        **The library stand-ins are in the domain**, and they have to be: spec §7's own worked
        example is ``database/sql.DB.Query``, which is a stand-in, and ``flows_to_call(…,
        "DB.Query")`` is the question the HTTP→SQL fixture exists to ask. They resolve to the
        ``can://…/@external/…`` id, which is a real vertex of the L4 graph with real ``formal_in``
        ports — so a flow into one is a flow a slice can follow, not a boundary.

        A stand-in's reported signature is :func:`~cldk.analysis.go.ids.external_signature` and its
        ``path`` is ``""``: there is no file, and inventing one would put a fiction in a field a
        caller reads. An empty ``module_names`` (not ``None``) is deliberate — it means "this
        candidate supplies no dotted module name", so ``in_module=`` never matches a stand-in rather
        than matching one through a path derivation that would be meaningless here (the same
        distinction Java's J-2 records).

        **One candidate per declaration, not per signature.** A Go signature is not a unique
        address: several files in one package may each declare their own ``init()``, so
        ``example.com/x/initclosure.init`` names two different callables with different bodies (3
        such collisions on the analyzer's own corpus). Keying the candidate list by signature
        collapsed those into one entry, which hid the ambiguity from the shared policy — so it
        could neither report it (the Neo4j backend, whose rows are per declaration, raised
        ``AmbiguousName`` where this raised a bare ``ValueError``) nor let ``in_module=`` narrow it.
        Emitting one candidate per declaration is what makes the two backends hand the policy the
        same domain, which is the only way they can agree on both the answer and the failure.

        ``declarations`` therefore maps a signature to *every* callable carrying it, and
        :func:`~cldk.analysis.go.ids.pick_declaration` re-applies the caller's keywords to choose
        among them once the policy has resolved the signature.
        """
        declarations: Dict[str, List[Tuple[object, str | None, str]]] = {}
        for c, tid, path in self._iter_callables():
            declarations.setdefault(c.signature, []).append((c, tid, path))
        cands = [
            CallableCandidate(sig, tid, path, match_names=callable_match_names(sig), module_names=(pkg,) if (pkg := self._package_of(path)) else ())
            for sig, entries in declarations.items()
            for _c, tid, path in entries
        ]
        for sym in self.application.external_symbols.values():
            sig = external_signature(sym.module, sym.signature, sym.name)
            declarations.setdefault(sig, []).append((sym, None, ""))
            cands.append(CallableCandidate(sig, None, "", match_names=callable_match_names(sig), module_names=()))
        return cands, declarations

    def _package_of(self, path: str) -> str | None:
        """The ``package`` clause of the module at ``path`` — what ``in_module=`` matches a Go
        callable's module against, besides the path itself.

        Supplied explicitly rather than derived: the shared
        :func:`~cldk.analysis.commons.keys.module_dotted` strips a ``.py`` suffix and would leave
        ``httpsql/httpsql.go`` as a dotted ``httpsql.httpsql.go``, which names nothing (the same
        trap Java's J-2 records). A Go module's readable module name is its package clause.
        """
        mod = self.application.symbol_table.get(path)
        return mod.package if mod else None

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """The one callable ``name`` names (see :meth:`GoAnalysisBackend.resolve_callable`).

        Nothing is pre-filtered: this backend has the whole list in memory, so there is no round
        trip to save, and handing the shared policy the unfiltered domain is the strongest form of
        "both backends resolve over the same set". The Go receiver spellings come from
        :func:`~cldk.analysis.go.ids.callable_match_names`, which is shared with the Neo4j backend.
        """
        cands, declarations = self._candidates
        sig = resolve_callable_signature(name, cands, in_class=in_class, in_module=in_module, by_full_name="more of the import path")
        entries = declarations[sig]
        # A signature can name several declarations (same-package multi-file ``init()``), so the
        # keywords are re-applied to choose among them -- see ``pick_declaration`` for why the
        # policy's own answer is one step short of an address here.
        chosen = pick_declaration(sig, [(path, tid) for _c, tid, path in entries], in_class=in_class, in_module=in_module)
        c, _tid, path = next((obj, tid, p) for obj, tid, p in entries if p == chosen)
        if not c.id:
            raise KeyError(f"{sig!r} carries no usable address (id={c.id!r})")
        # A stand-in has no file and no span, so it is described as an external rather than given a
        # fabricated position — the same decision :meth:`_callable_node` makes for a path's vertices.
        span = getattr(c, "span", None)
        return SliceNode(
            file=path,
            line=span.start[0] if span else 0,
            callable=sig,
            kind="callable" if path else "external",
            name=sig.rpartition(".")[2],
            source=None,
            ref=c.id,
        )

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """The one value of ``within`` that ``name`` names (see
        :meth:`GoAnalysisBackend.resolve_value`).

        The domain is the resolved callable's ``formal_in`` vertices, read by their own ``of`` —
        never by ordinal into ``parameters``, because a method's ``formal_in:0`` is the receiver
        (trap 2). Each is given every spelling a Go caller may write it as, which is where the
        package-qualified form of a global comes from (trap 3,
        :func:`~cldk.analysis.go.ids.go_value_names`).

        A **list** of ``(key, name)`` pairs rather than a dict keyed by name: two values resolving
        to the same name are a genuine ambiguity the policy must see and raise on, and a dict would
        silently keep the last.
        """
        owner = resolve_within(self.resolve_callable, within)
        c = self._dataflow_owners[owner.ref][0]
        entries = [(key, nm) for key, node in (c.body or {}).items() if node.kind == "formal_in" and node.of for nm in go_value_names(node.of)]
        chosen = resolve_value_name(name, [nm for _k, nm in entries], within=owner.callable)
        key = next(k for k, nm in entries if nm == chosen)
        node = (c.body or {})[key]
        kind, leaf, defined_in = body_node_kind(node.kind, node.of)
        return SliceNode(file=owner.file, line=owner.line, callable=owner.callable, kind=kind, name=leaf, defined_in=defined_in, source=None, ref=body_node_id(c.id, key))

    # -----[ per-callable graphs ]-----
    def _graphs_of(self, name: str, in_class: str | None, page_size: int) -> Tuple[str, GoCallable | None]:
        """``(signature, callable)`` for ``name``, once this backend is deep enough to have dataflow.

        ``page_size`` is validated **first**, before the level guard and before resolution, so a
        malformed argument is a ``ValueError`` before anything else — the order the Neo4j backend
        applies too, so the two cannot answer the same bad call with different exceptions.

        **The callable is ``None`` for a library stand-in**, which is a real resolution target here
        (spec §7's own example, ``database/sql.DB.Query``, is one) and has no intraprocedural flow
        to report: the analyzer synthesizes only ``formal_in``/``formal_out`` ports for it, so
        :class:`~cldk.models.go.GoExternalSymbol` carries no ``cfg``/``cdg``/``ddg`` field at all.
        Returning ``None`` is what lets the three accessors answer with an **empty page**, which is
        both the honest answer — the pass ran and a stand-in has no statements — and the answer the
        Neo4j backend already gives, since its Cypher simply matches no rows on an external node.
        Reading the field blindly raised ``AttributeError`` instead, which was neither.

        This is **not** the D7 case the level gate guards: there, empty would be indistinguishable
        from "no dependence" because the pass never ran. Here the pass ran and the absence is a
        fact about what a stand-in *is*.
        """
        check_page_size(page_size)
        self._require_dataflow()
        node = self.resolve_callable(name, in_class=in_class)
        owner = self._dataflow_owners[node.ref][0]
        return node.callable, owner if isinstance(owner, GoCallable) else None

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCfgEdge]:
        """One page of control flow within one callable (see :meth:`GoAnalysisBackend.get_cfg`).

        ``GoCallable.cfg`` keys its endpoints by the *local* body key (``"10:25"``, ``"@entry"``);
        :func:`~cldk.analysis.go.ids.body_node_id` joins them to the callable id to give the same
        absolute spelling the graph merges ``:GoBodyNode`` on, so an endpoint from this backend is
        the one the Neo4j backend would return. The join happens *before* the sort, because the
        order is over the ids a caller sees, not over the local keys.
        """
        sig, c = self._graphs_of(callable, in_class, page_size)
        edges = [] if c is None else [GoCfgEdge(src=body_node_id(c.id, e.src), dst=body_node_id(c.id, e.dst), kind=e.kind) for e in c.cfg or []]
        return edge_page(GoCfgEdge, sig, edges, CFG_ORDER, page_size, cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoCdgEdge]:
        """One page of control dependence within one callable (see :meth:`GoAnalysisBackend.get_cdg`)."""
        sig, c = self._graphs_of(callable, in_class, page_size)
        edges = [] if c is None else [GoCdgEdge(src=body_node_id(c.id, e.src), dst=body_node_id(c.id, e.dst)) for e in c.cdg or []]
        return edge_page(GoCdgEdge, sig, edges, CDG_ORDER, page_size, cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[GoDdgEdge]:
        """One page of data dependence within one callable (see :meth:`GoAnalysisBackend.get_ddg`)."""
        sig, c = self._graphs_of(callable, in_class, page_size)
        edges = [] if c is None else [GoDdgEdge(src=body_node_id(c.id, e.src), dst=body_node_id(c.id, e.dst), var=e.var, prov=list(e.prov or [])) for e in c.ddg or []]
        return edge_page(GoDdgEdge, sig, edges, DDG_ORDER, page_size, cursor)

    # -----[ slicing and reachability ]-----
    def _reach(self, ref: str, direction: str, depth: int | None) -> set:
        """The node ids reachable from ``ref`` in at most ``depth`` hops.

        Level-by-level rather than a plain stack, because ``depth`` is a hop budget and a
        depth-first walk cannot count hops without revisiting. Shared by the slices and by the two
        flow predicates, so "reachable" means one thing on this backend.
        """
        edges = self._sdg[0][direction]
        seen, frontier, hops = {ref}, [ref], 0
        while frontier and (depth is None or hops < depth):
            nxt = [d for src in frontier for d in edges.get(src, ()) if d not in seen]
            seen.update(nxt)
            frontier = nxt
            hops += 1
        return seen

    def _slice_from(self, root: SliceNode, direction: str, depth: int | None, max_nodes: int) -> Slice:
        """:meth:`_reach`'s closure from ``root``, described and capped as the graph's is.

        The whole closure is computed and *then* cut: ``total`` has to be the size of the whole
        slice for the cap to be reportable, and there is nothing cheaper to compute it from — the
        same reason the Cypher counts before it pages.
        """
        found = [n for ref in sorted(self._reach(root.ref, direction, depth)) if (n := self._slice_node(ref)) is not None]
        return Slice(nodes=found[:max_nodes], roots=[root], resolved=slice_resolved([root]), total=len(found))

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What affects this value (see :meth:`GoAnalysisBackend.slice_backward`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_connected_ports("slice_backward")
        return self._slice_from(self.resolve_value(src, within=within), "backward", depth, max_nodes)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What this value affects (see :meth:`GoAnalysisBackend.slice_forward`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_connected_ports("slice_forward")
        return self._slice_from(self.resolve_value(src, within=within), "forward", depth, max_nodes)

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path (see :meth:`GoAnalysisBackend.reaches`)?

        Over :meth:`get_call_graph`, the same edge set :meth:`get_callers` / :meth:`get_callees`
        read, so the boolean cannot disagree with the neighbours a caller can enumerate. The Go
        call graph is keyed by callable **id**, not by signature, so the resolved ``ref`` is the
        vertex — which is also why this needs no symbol-table lookup afterwards.
        """
        check_depth(depth)
        a = self.resolve_callable(src).ref
        b = self.resolve_callable(dst).ref
        # ``call_reaches`` owns the zero-hop rule: no path is vacuous, so ``reaches(x, x)`` is the
        # cycle question rather than trivially True.
        return call_reaches(self.get_call_graph(), a, b, depth)

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Every callable that can reach these sinks (see :meth:`GoAnalysisBackend.backward_cone`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        roots = cone_sinks(self.resolve_callable, sinks)
        graph = self.get_call_graph()
        reached: set = set()
        for root in roots:
            reached.add(root.ref)
            if root.ref in graph:
                # ``ego_graph`` follows successors, so a backward question needs the reversed view.
                back = graph.reverse(copy=False)
                reached |= nx.ancestors(graph, root.ref) if depth is None else set(nx.ego_graph(back, root.ref, radius=depth, undirected=False).nodes)
        found = sorted((n for n in (self._callable_node(ref) for ref in reached) if n is not None), key=lambda n: n.ref)
        return Slice(nodes=found[:max_nodes], roots=roots, resolved=slice_resolved(roots), total=len(found))

    def _callable_node(self, callable_id: str) -> SliceNode | None:
        """The callable ``callable_id`` names, as a :class:`SliceNode`.

        A call-graph vertex the symbol table does not declare is a library stand-in; it is described
        from :meth:`get_external_symbols` rather than by parsing its id, which is the same decision
        the Neo4j backend makes off the ``:GoExternal`` node's properties.
        """
        entry = self._dataflow_owners.get(callable_id)
        if entry is not None:
            c, _tid, path = entry
            if path:
                return SliceNode(file=path, line=c.span.start[0] if c.span else 0, callable=c.signature, kind="callable", name=c.signature.rpartition(".")[2], source=None, ref=callable_id)
        sym = self.application.external_symbols.get(callable_id)
        if sym is not None:
            return SliceNode(file="", line=0, callable=f"{sym.module}.{sym.signature or sym.name}", kind="external", name=sym.name, source=None, ref=callable_id)
        return None

    # -----[ paths and the flow predicates ]-----
    #: Up to ``limit`` shortest walks over a ``{src: {dst: [label]}}`` adjacency, in the documented
    #: hop order. The ``via`` table is the one parameter: the algorithm is a graph walk over strings
    #: and knows no language.
    _shortest_walks = staticmethod(partial(shortest_walks, via=VIA))

    def _describe_walks(self, walks: list, a: SliceNode) -> List[FlowPath]:
        """``walks`` as :class:`FlowPath` objects, with every node described once."""
        described = {ref: n for walk in walks for ref, _ in walk if (n := self._slice_node(ref)) is not None}
        described[a.ref] = a
        return [flow_path([described[a.ref]] + [described[ref] for ref, _ in walk if ref in described], [label for _, label in walk], via=VIA) for walk in walks]

    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value (see :meth:`GoAnalysisBackend.paths_between`)."""
        check_depth(depth)
        check_max_paths(max_paths)
        self._require_connected_ports("paths_between")
        a = self.resolve_value(src, within=src_within)
        b = self.resolve_value(dst, within=dst_within)
        check_distinct_endpoints(a, b)
        walks = self._shortest_walks(self._sdg[0]["forward"], a.ref, b.ref, depth, max_paths + 1)
        return FlowPaths(paths=self._describe_walks(walks[:max_paths], a), complete=len(walks) <= max_paths)

    def _callee_ports(self, callable_id: str) -> List[str]:
        """The ids of every value *entering* this callable — its ``formal_in`` vertices.

        Read off ``body`` by ``kind``, not by ordinal into ``parameters``: on a method the receiver
        is one of these and is absent from ``parameters`` (trap 2), and a closure's captures are
        formals with no parameter at all.
        """
        entry = self._dataflow_owners.get(callable_id)
        if entry is None:
            return []
        c = entry[0]
        return [body_node_id(callable_id, key) for key, node in (c.body or {}).items() if node.kind == "formal_in"]

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach any value entering a call to ``callee``
        (see :meth:`GoAnalysisBackend.flows_to_call`)?"""
        check_depth(depth)
        self._require_connected_ports("flows_to_call")
        root = self.resolve_value(src, within=within)
        targets = self._callee_ports(self.resolve_callable(callee).ref)
        return bool(targets) and not self._reach(root.ref, "forward", depth).isdisjoint(set(targets) - {root.ref})

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach ``callee``'s ``arg``
        (see :meth:`GoAnalysisBackend.flows_to_argument`)?"""
        check_depth(depth)
        self._require_connected_ports("flows_to_argument")
        root = self.resolve_value(src, within=within)
        target = self.resolve_value(arg, within=callee).ref
        return target != root.ref and target in self._reach(root.ref, "forward", depth)

    # -----[ taint ]-----
    def _taint_walk(
        self,
        srcs: Sequence[SliceNode],
        dsts: Sequence[SliceNode],
        *,
        cuts: List[Dict[str, str]],
        cut_callables: List[str],
        depth: int | None,
        max_paths: int,
    ) -> Tuple[List[Tuple[str, str, FlowPath]], Mapping[Tuple[str, str], List[Diagnostic]]]:
        """The sanitized shortest walks, in process (see :meth:`GoAnalysisBackend._taint_walk`).

        ``_require_dataflow`` first, so a *direct* call to this hook is diagnosed by level rather
        than by an empty walk. Through :meth:`taint` it never fires: ``_require_connected_ports``
        runs there and subsumes it.

        One :func:`~cldk.analysis.commons.graphs.shortest_walks` call **per pair**, which is what
        makes ``max_paths + 1`` a per-pair cap here the way ``collect(p)[0..$cap]`` is one over
        Cypher — a single walk over the flattened lists would let one prolific pair starve the rest.
        Pairs are deduplicated by resolved position first: two selectors naming one position are one
        pair, and walking it twice would report each witness twice and make a cap of *m* yield *2m*.

        Both cuts are the walker's **predicates** rather than a filter over what it returns, which
        is the property the whole design rests on: the breadth-first pass must measure the shortest
        *satisfying* distance, or a sanitized short route hides a clean longer one and the pair comes
        back refuted.

        The ledger comes back empty. Go's unresolved-dispatch signal is the application-scope
        ``degraded[]``, which :meth:`taint` reads directly and files per pair — a per-frontier signal
        this walk could file instead does not exist in the payload, and inventing one from, say, a
        ``callee is None`` call node would void every refutation in every application that calls a
        library, which is the over-reporting leg 4b's Ruling I rejects.
        """
        self._require_dataflow()
        forward = self._sdg[0]["forward"]
        allow_node = (lambda nid: not under_callable(nid, cut_callables)) if cut_callables else None
        allow_edge = (lambda frm, _rel, var: not any(var == c["var"] and under_callable(frm, (c["prefix"],)) for c in cuts)) if cuts else None
        pairs: Dict[Tuple[str, str], SliceNode] = {}
        for a in srcs:
            for b in dsts:
                pairs.setdefault((a.ref, b.ref), a)
        rows: List[Tuple[str, str, FlowPath]] = []
        for (src_ref, dst_ref), a in pairs.items():
            walks = self._shortest_walks(forward, src_ref, dst_ref, depth, max_paths + 1, allow_edge=allow_edge, allow_node=allow_node)
            rows.extend((src_ref, dst_ref, p) for p in self._describe_walks(walks, a))
        return rows, {}

    def _edge_vars_in(self, callable_id: str) -> FrozenSet[str]:
        """The edge variables scoped to this callable (see :meth:`GoAnalysisBackend._edge_vars_in`).

        Off the adjacency this backend already caches, so a variable sanitizer costs a scan and no
        second traversal. Edges *leaving* a node under ``callable_id`` — the same ``startNode``
        scoping the cut itself uses, so this validates exactly the domain the cut can match.

        "Under ``callable_id``" is :func:`~cldk.analysis.commons.graphs.under_callable` and not
        ``startswith``: with a bare prefix test this domain would include every variable of a
        sibling callable whose id merely starts with this one — in Go, every closure of a
        same-named outer function — and a sanitizer naming one of those would be *accepted* here and
        then sever nothing in ``allow_edge``, which is delimited.
        """
        forward = self._sdg[0]["forward"]
        return frozenset(var for src, outs in forward.items() if under_callable(src, (callable_id,)) for labels in outs.values() for _rel, var, _prov in labels if var)

    # -----[ the honesty signals + hydration ]-----
    def get_external_symbols(self) -> Dict[str, GoExternalSymbol]:
        """The library stand-ins this analysis synthesized (see
        :meth:`GoAnalysisBackend.get_external_symbols`).

        Returned as-is rather than refused when empty: unlike Java's ``--external-calls``, Go emits
        the stand-ins as part of ``-a 4`` with no opt-in, so an empty map at level 4 genuinely means
        "this application calls nothing outside itself".
        """
        return self.application.external_symbols

    def get_degraded(self) -> List[GoDegraded]:
        """This analysis's degradation markers (see :meth:`GoAnalysisBackend.get_degraded`).

        Envelope scope, not application scope — ``degraded`` sits beside ``application`` on the
        wire (``internal/schema/v2/schema.go``), which is why this reads ``self.analysis`` and not
        ``self.application``.
        """
        return self.analysis.degraded

    def describe(self, nodes: Sequence[object]) -> List[SliceNode]:
        """Fill in ``source`` for these positions (see :meth:`GoAnalysisBackend.describe`).

        A body node's text is sliced out of the owning module by its span, so this fills in
        statements and call sites. A vertex with **no** span — the synthetic ``@entry``/``@exit`` and
        every ``formal_in``/``formal_out`` — gets ``None``: it is a dataflow position, not a region
        of the file, and there is nothing to read on either backend.
        """
        out: List[SliceNode] = []
        for n in nodes:
            node = n if isinstance(n, SliceNode) else self._slice_node(str(n))
            if node is None:
                continue
            out.append(node.model_copy(update={"source": self._node_source(node.ref)}))
        return out

    def _node_source(self, ref: str) -> str | None:
        """The text of ``ref``, or ``None`` when it has no span (so nothing to read)."""
        try:
            return self.get_source(ref)
        except (CodeanalyzerExecutionException, KeyError):
            entry = self._sdg[1].get(ref)
            if entry is None:
                return None
            c, key, _path = entry
            node = (c.body or {})[key]
            if node.span is None:
                return None
            mod = self._owning_module(c)
            if mod is None:
                return None
            b0, b1 = node.span.bytes
            return mod.source[b0:b1] if mod.source.isascii() else mod.source.encode("utf-8")[b0:b1].decode("utf-8")

    def _owning_module(self, c: object) -> GoModule | None:
        """The module declaring ``c``, for slicing a body node's span out of its source."""
        entry = self._source_of.get(getattr(c, "id", ""))
        return entry[0] if entry else None

    # -----[ artifact / dependency / config layer — not emitted by a local analysis ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        raise CodeanalyzerExecutionException(ARTIFACT_LAYER_UNAVAILABLE.format(app=self.application_name))
