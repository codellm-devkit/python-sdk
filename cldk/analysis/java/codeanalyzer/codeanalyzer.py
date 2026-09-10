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

"""Java Codeanalyzer backend.

Subprocess wrapper around the analyzer the ``codeanalyzer-java`` wheel carries (the ``java``
extra; pinned in ``pyproject.toml``, mirrored in ``[tool.backend-versions]``), run on the JVM that
same wheel bundles -- the SDK downloads no JDK and touches no JDK environment variable.
Reads the schema-v2 ``analysis.json`` envelope (:class:`JAnalysis`), keeps its ``application`` as
the queried :class:`JApplication`, and owns all query/indexing logic; the :class:`JavaAnalysis`
facade is a thin delegating shell over it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict, FrozenSet, Iterable, List, Sequence, Tuple, Union

import networkx as nx
from pydantic import ValidationError

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.bounds import DEFAULT_PAGE_SIZE, check_page_size, edge_page
from cldk.analysis.commons.graphs import flow_path, shortest_walks, slice_resolved, under_callable
from cldk.analysis.commons.levels import ANALYZER_LEVELS, LEVEL_NAMES, analyzer_level
from cldk.analysis.commons.results import Diagnostic, EdgePage, FlowPaths, Slice, SliceNode
from cldk.analysis.java.backend import (
    CDG_ORDER,
    CFG_ORDER,
    CRUD_UNAVAILABLE,
    DDG_ORDER,
    VIA,
    CallingLines,
    CRUDRow,
    JavaAnalysisBackend,
    duplicate_type_name,
    java_body_node_id,
    unhomed_endpoint,
)
from cldk.models.java import JGraphEdges
from cldk.models.java.models import (
    JAnalysis,
    JApplication,
    JBodyNode,
    JCallable,
    JCallableParameter,
    JCallSite,
    JCdgEdge,
    JCfgEdge,
    JComment,
    JCompilationUnit,
    JDdgEdge,
    JField,
    JMethodDetail,
    JType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, CodeanalyzerUsageException

logger = logging.getLogger(__name__)

#: The SDK's record of what the analyzer said about its own run, written **beside**
#: ``analysis.json`` in the cache directory the SDK owns. Never a field inside the payload: that
#: file's shape is codeanalyzer-java's schema, not ours.
VERDICT_FILE = "analyzer_diagnostics.json"

#: ANSI colour, which the analyzer's console appender writes around the timestamp and the level.
#: Stripped before matching so it is never read as content and never lands in a message.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: The shape codeanalyzer-java uses to declare that something it was asked for did not run: a WARN
#: line naming the capability that is ``unavailable`` and what it is ``emitting … only`` instead
#: (``RTA call graph unavailable (NullPointerException: null); emitting declared edges only``,
#: ``L4 semantic ddg unavailable (WALA build failed); emitting the derived SDG vertices and param
#: edges only``). Matched on that shape, not on the cause inside the parentheses, which differs per
#: failure and is the analyzer's to word.
_DEGRADED_LINE = re.compile(r"\[WARN\]\s*(?P<message>\S.*?\bunavailable\b.*\bemitting\b.*\bonly\b)\s*$")


def _degradations(log: str) -> List[Diagnostic]:
    """The analyzer's own degradation sentences, as :class:`Diagnostic`s, in the order it logged them.

    The analyzer degrades rather than failing — a build it cannot run costs it the RTA call graph
    and the points-to half of the SDG, and it still exits 0 and stamps ``max_level`` with the level
    it was asked for — so its log is the only authoritative signal (#341). The sentence is kept
    verbatim as the message: it names the cause (``NullPointerException: null``, ``WALA build
    failed``) better than a paraphrase would.

    ``code`` is ``level_too_low`` because that is what happened: the level actually computed is
    below the level the envelope reports. The payload's own proxies — no ``rta`` provenance on any
    call edge, no ``points-to`` provenance on any ddg edge — are **not** consulted, here or
    anywhere: a small project can legitimately have neither, so their absence is corroboration for
    a verdict that was recorded, never a substitute for one.
    """
    messages: Dict[str, None] = {}
    for line in log.splitlines():
        match = _DEGRADED_LINE.search(_ANSI.sub("", line))
        if match:
            messages[match.group("message")] = None
    return [Diagnostic(code="level_too_low", message=message) for message in messages]


def _payload_digest(analysis_json_file: Path) -> str | None:
    """The sha256 of the payload the verdict describes, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(analysis_json_file.read_bytes()).hexdigest()
    except OSError:
        return None


def _recorded_verdict(verdict_file: Path, analysis_json_file: Path) -> List[Diagnostic] | None:
    """The verdict recorded beside a cached ``analysis.json``, or ``None`` when there is none *for
    this payload*.

    ``None`` is a third state and not a synonym for "the analyzer reported no degradation": a cache
    written before this file existed, or an ``analysis.json`` dropped into the cache directory by
    something other than this backend, carries no verdict at all. Reporting that as clean is the
    ambiguous-empty defect (#341), so it stays distinguishable from ``[]``.

    **The verdict is bound to the payload it was written for** by that payload's sha256. A verdict
    file has no other relation to the ``analysis.json`` beside it, so pairing it with a payload it
    did not describe — the analyzer re-run by hand, an ``analysis.json`` copied in over one this
    backend wrote — would report a stale verdict as current. A digest that does not match is no
    verdict for this payload, which is ``None``.

    Never raises: every way this file can be wrong (missing, unreadable, not JSON, not the shape
    this backend writes, a ``Diagnostic`` that no longer validates) is a verdict that cannot be
    read, and turning a working cache hit into a constructor failure over the SDK's own sidecar is
    not a trade this makes.
    """
    try:
        recorded = json.loads(verdict_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(recorded, dict) or not isinstance(recorded.get("diagnostics"), list):
        return None
    if recorded.get("payload_sha256") != _payload_digest(analysis_json_file):
        return None
    try:
        return [Diagnostic.model_validate(entry) for entry in recorded["diagnostics"]]
    except ValidationError:
        return None


class JCodeanalyzer(JavaAnalysisBackend):
    """Build and query the application view of a Java project by invoking codeanalyzer-java.

    Args:
        project_dir: Path to the root of the Java project.
        analysis_json_path: Directory to persist ``analysis.json`` (the language-keyed cache dir,
            ``<cache>/java``). If None, the envelope is read from the subprocess stdout pipe.
        analysis_level: Any :class:`~cldk.analysis.AnalysisLevel` (or its name); sent to the
            analyzer as ``-a 1..4`` — the backend requests what the caller asked for.
        eager_analysis: If True, re-run the analyzer even if a compatible ``analysis.json`` is cached.
        target_files: Restrict analysis to these files (``-t``); always re-runs.

    Attributes:
        analysis: The whole ``analysis.json`` envelope — ``schema_version``, ``max_level``,
            ``analyzer.version`` — for callers that need to know what produced the view.
        application: ``analysis.application``, the queried view.
        analyzer_diagnostics: What the analyzer said about its own run, in three distinguishable
            states (#341): a **non-empty** list — it declared a degradation, one
            :class:`~cldk.analysis.commons.results.Diagnostic` per sentence it logged;
            **empty** — it declared none; **``None``** — no verdict is recorded, so whether the
            requested level was fully computed is *unknown*. ``None`` happens on a cache written
            before this backend recorded verdicts, on an ``analysis.json`` put in the cache
            directory from elsewhere, and in stdout-pipe mode (no ``analysis_json_path``), where the
            payload occupies the same pipe the log would.
    """

    def __init__(
        self,
        project_dir: Union[str, Path, None],
        analysis_json_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_json_path = analysis_json_path
        self.analysis_level = analysis_level
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.analyzer_diagnostics: List[Diagnostic] | None = None
        self.analysis: JAnalysis = self._init_codeanalyzer(analysis_level=analyzer_level(analysis_level))
        self._report_analyzer_diagnostics(analyzer_level(analysis_level))
        self.application: JApplication = self.analysis.application
        self._call_graph: nx.DiGraph | None = None
        self._sdg_cache: Any = None
        self._index()

    # -----[ driving the analyzer ]-----
    def _get_codeanalyzer_exec(self) -> List[str]:
        """``codeanalyzer_java.command()`` — ``[<jdk4py java>, -jar, <the wheel's jar>]``.

        The ``codeanalyzer-java`` wheel is the single source of both the jar and the JVM it runs
        on; 3.0.x reads its primordial scope from ``jrt:/`` inside that JVM, so the SDK has nothing
        to provision and no environment to point the analyzer at -- whatever JDK the machine has (or
        has not) is left alone. Imported here rather than at module import so ``import cldk`` (and
        ``cldk.analysis.java``) work without the ``java`` extra.
        """
        try:
            import codeanalyzer_java
        except ImportError as exc:
            raise CodeanalyzerExecutionException(
                'the Java analyzer is not installed: the codeanalyzer-java distribution (module "codeanalyzer_java") carries the analyzer jar and the JVM it runs on. Install it with: pip install "cldk[java]"'
            ) from exc
        return codeanalyzer_java.command()

    def _argv(self, analysis_level: int, output_dir: Path | None) -> List[str]:
        """The 3.0.x command line: ``-i <project> -a <1..4> [-o <dir> -c <dir>/cache -v] --app-name
        <project.name> [-t <file>]...``. The application name is what the analyzer stamps into every
        ``can://<app>/java/...`` id; without ``-o`` the analyzer prints the JSON to stdout.

        ``-v`` ("print logs to console") rides along with ``-o`` because the analyzer's log is the
        only place it declares that a capability it was asked for did not run (#341) — and only
        with ``-o``, since without it the payload occupies the same stdout the log would.
        """
        if self.project_dir is None:
            raise CodeanalyzerExecutionException("Cannot run codeanalyzer-java: no project directory.")
        args = self._get_codeanalyzer_exec()
        project = Path(self.project_dir)
        args += ["-i", str(project), "-a", str(analysis_level)]
        if output_dir is not None:
            args += ["-o", str(output_dir), "-c", str(output_dir / "cache"), "-v"]
        args += ["--app-name", project.name]
        for tf in self.target_files or []:
            args += ["-t", str(tf).strip()]
        return args

    @staticmethod
    def check_exisiting_analysis_file_level(analysis_json_path_file: Path, analysis_level: int) -> bool:
        """Whether a cached ``analysis.json`` can serve a request at ``analysis_level``.

        ``False`` (re-run) when the file is missing, unparsable, or was computed at a lower
        ``max_level`` than requested. A file without ``schema_version`` is a pre-v2 (2.x) artifact
        and is refused outright (J-9): the v2 models cannot read it and a silent re-run would hide
        that the cache directory holds a stale generation.
        """
        if not analysis_json_path_file.exists():
            return False
        try:
            data = json.loads(analysis_json_path_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(data, dict):
            return False
        if "schema_version" not in data:
            raise CodeanalyzerExecutionException(f"cached analysis.json at {analysis_json_path_file} predates schema v2 (no schema_version); delete it or pass eager_analysis=True")
        return int(data.get("max_level", 0)) >= analysis_level

    def _init_codeanalyzer(self, analysis_level: int) -> JAnalysis:
        """Run the analyzer (or reuse a compatible cache) and return the validated envelope."""
        if self.analysis_json_path is None:
            args = self._argv(analysis_level, None)
            try:
                logger.info(f"Running codeanalyzer-java: {' '.join(args)}")
                console_out: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
                return JAnalysis.model_validate_json(console_out.stdout)
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e

        output_dir = Path(self.analysis_json_path)
        analysis_json_file = output_dir / "analysis.json"
        verdict_file = output_dir / VERDICT_FILE
        needs_run = self.eager_analysis or bool(self.target_files) or not self.check_exisiting_analysis_file_level(analysis_json_file, analysis_level)
        if needs_run:
            args = self._argv(analysis_level, output_dir)
            try:
                logger.info(f"Running codeanalyzer-java: {' '.join(args)}")
                console_out: CompletedProcess[str] = subprocess.run(args, capture_output=True, text=True, check=True)
                if not analysis_json_file.exists():
                    raise CodeanalyzerExecutionException("codeanalyzer-java did not generate analysis.json.")
            except Exception as e:  # noqa: BLE001
                raise CodeanalyzerExecutionException(str(e)) from e
            # Persisted beside the payload, because the point of the cache is that the next run does
            # not invoke the analyzer — and a cached payload has no log to read the verdict off.
            self.analyzer_diagnostics = _degradations(console_out.stdout)
            verdict = {"payload_sha256": _payload_digest(analysis_json_file), "diagnostics": [d.model_dump() for d in self.analyzer_diagnostics]}
            verdict_file.write_text(json.dumps(verdict), encoding="utf-8")
        else:
            self.analyzer_diagnostics = _recorded_verdict(verdict_file, analysis_json_file)
        return JAnalysis.model_validate_json(analysis_json_file.read_text(encoding="utf-8"))

    def _report_analyzer_diagnostics(self, analysis_level: int) -> None:
        """Say, once per analysis and at ``WARNING``, what the analyzer said about its own run — or
        that nothing is recorded, which is *unknown* and never "fine".

        Never raises. A declared-only call graph is still the call graph, and every caller content
        with that answer keeps working; the caller who is not needs to be told, not stopped.

        Silent below the call graph: nothing the analyzer can degrade runs at ``-a 1``, so a
        verdict-less symbol table is not an unknown worth a warning.
        """
        if analysis_level < analyzer_level("call_graph"):
            return
        level_name = LEVEL_NAMES[analysis_level]
        if self.analyzer_diagnostics is None:
            logger.warning(f"codeanalyzer-java recorded no degradation verdict for this analysis: whether analysis_level={level_name} was fully computed is unknown")
            return
        for diagnostic in self.analyzer_diagnostics:
            logger.warning(f"codeanalyzer-java did not fully compute analysis_level={level_name}: {diagnostic.message}")

    # -----[ indexing ]-----
    def _index(self) -> None:
        """Flatten the containment tree once: every type (top-level, nested, local/anonymous) by
        its source-spelled qualified name, its file, and every callable by its ``can://`` id — the
        join that turns a wire call-graph endpoint into the ``"<type fqn>.<signature>"`` node key."""
        self._types: Dict[str, JType] = {}
        self._file_of: Dict[str, str] = {}
        self._callables: Dict[str, Tuple[JType, JCallable]] = {}
        for path, unit in self.application.symbol_table.items():
            for t in unit.types.values():
                self._add_type(t, path)

    def _add_type(self, t: JType, path: str) -> None:
        name = t.qualified_name
        if name in self._types:
            raise CodeanalyzerExecutionException(duplicate_type_name(name))
        self._types[name] = t
        self._file_of[name] = path
        for c in t.callables.values():
            self._callables[c.id] = (t, c)
            for lt in c.types.values():
                self._add_type(lt, path)
        for nt in t.types.values():
            self._add_type(nt, path)

    @staticmethod
    def _detail(klass: str, c: JCallable) -> JMethodDetail:
        return JMethodDetail(method_declaration=c.declaration, klass=klass, method=c)

    def _node_of(self, node_id: str) -> Tuple[str, JMethodDetail]:
        """The (node key, method detail) a call-graph endpoint id resolves to. Every endpoint the
        analyzer emits is homed on the tree; one that is not is the analyzer's defect, surfaced
        rather than skipped — named by the signature and module key its id spells, never by the id
        (E6), in the same words the Neo4j backend uses."""
        try:
            t, c = self._callables[node_id]
        except KeyError:
            raise CodeanalyzerExecutionException(unhomed_endpoint(node_id)) from None
        return f"{t.qualified_name}.{c.signature}", self._detail(t.qualified_name, c)

    def _is_external(self, node_id: str) -> bool:
        """An ``@external/…`` endpoint (a call target outside the project). 3a keeps the 1.x
        callable-only graph and drops edges to them."""
        return "@external/" in node_id or node_id in (self.application.external_symbols or {})

    # -----[ the addressing surface (leg 3b) — the three facts the shared implementation needs ]-----
    def _body_nodes(self, callable_ids: Sequence[str]) -> Dict[str, Dict[str, JBodyNode]]:
        """See :meth:`JavaAnalysisBackend._body_nodes`. In memory already, so "one round trip" is
        free here; the ids are composed the emitter's way (:func:`java_body_node_id`) so they are
        the same strings the Neo4j backend reads off ``b.id``.

        Which kinds are present is the *analysis level*, not this backend: at level 1 and 2 the
        analyzer emits the ``call`` nodes only, and the whole vertex set from level 3. So is
        :attr:`~cldk.analysis.commons.results.BodyRef.callee`, which is the analyzer's ``callee``
        verbatim: it arrives with the **call graph**, at level 2. At ``-a 1`` no call node carries
        one (0 of a1's 4,006) even though every one of them carries a ``callee_signature``, so a
        ``callee`` of ``None`` there is the level and not the site --- the one reading
        :attr:`has_resolution_edges` does *not* cover, since that flag is about the signature."""
        out: Dict[str, Dict[str, JBodyNode]] = {}
        for callable_id in callable_ids:
            found = self._callables.get(callable_id)
            if found is not None and found[1].body:
                out[callable_id] = {java_body_node_id(callable_id, key): node for key, node in found[1].body.items()}
        return out

    def _body_source(self, node: JBodyNode) -> str | None:
        """See :meth:`JavaAnalysisBackend._body_source`. This backend holds the module's real text
        and the analyzer's byte offsets, so a statement or call site slices out exactly (J-15)."""
        return node.code or None

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`JavaAnalysisBackend.has_resolution_edges`. Unconditionally ``True``:
        codeanalyzer-java writes ``callee_signature`` on a call node at every analysis level (all
        4,006 of daytrader8's are resolved at ``-a 1``), so an unresolved call site here is that
        site, never the level.

        This is a statement about the *signature*, and the id --
        :attr:`~cldk.analysis.commons.results.BodyRef.callee` -- is not covered by it: that field
        arrives with the call graph at ``-a 2`` (see :meth:`_body_nodes`)."""
        return True

    # =====================================================================================
    # The dataflow surface (leg 3b, Task 2) -- over the v2 models.
    #
    # THIS BACKEND ANSWERS INTERPROCEDURALLY, out of the same five lists ``--emit neo4j`` projects
    # as ``J_DDG`` / ``J_CDG`` / ``J_SUMMARY`` / ``J_PARAM_IN`` / ``J_PARAM_OUT``:
    #
    #   JCallable.ddg / .cdg / .summary   endpoints are LOCAL body keys -> joined by java_body_node_id
    #   JApplication.param_in / .param_out  endpoints are ALREADY global ids (checked on the fixture)
    #
    # So the index it lacks it can build, and the answer it gives is the graph's answer rather than
    # a narrower intraprocedural one dressed up as complete. Building it walks every callable once
    # and is cached for the life of the backend; the Neo4j backend pushes the same traversal into
    # Cypher instead.
    # =====================================================================================
    #: The analyzer level at which ``cfg``/``cdg``/``ddg`` first exist (``-a 3``); ``summary`` and
    #: the param lattice arrive at 4. One floor for all of them because they go dark together as
    #: far as this surface is concerned: at level 2 and below there is no body graph at all.
    _DATAFLOW_LEVEL = ANALYZER_LEVELS[AnalysisLevel.program_dependency_graph]

    #: The SDG index, built on first use and cached for the life of the backend. A class-level
    #: default so an instance built through ``object.__new__`` (the seam the offline suites use)
    #: reads it before ``__init__`` has run.
    _sdg_cache: Any = None

    def _require_dataflow(self) -> None:
        """See :meth:`JavaAnalysisBackend._require_dataflow`. Raises instead of returning empty: at
        a shallower level an empty answer would mean "not analysed" while looking exactly like "no
        dependence", and a caller cannot tell those apart (D7)."""
        level = analyzer_level(self.analysis_level)
        if level < self._DATAFLOW_LEVEL:
            raise CodeanalyzerUsageException(
                f"control and data flow need analysis_level='program_dependency_graph' or deeper "
                f"(analyzer level {self._DATAFLOW_LEVEL}); this analysis was built at "
                f"'{LEVEL_NAMES[level]}' (analyzer level {level}), where codeanalyzer-java emits no cfg/cdg/ddg at all. "
                "Returning an empty result would be indistinguishable from a callable that has no dependence, "
                "so this raises instead. Rebuild with CLDK.java(..., analysis_level='system_dependency_graph')."
            )

    def _graphs_of(self, name: str, in_class: str | None, page_size: int) -> Tuple[str, JCallable]:
        """The ``(J-1 key, callable)`` ``name`` resolves to, once this analysis is deep enough.

        ``page_size`` is validated **first**, before the level guard and before resolution, so a
        malformed argument is a ``ValueError`` before anything else — the order the Neo4j backend
        applies too, so the two cannot answer one bad call with different exceptions."""
        check_page_size(page_size)
        self._require_dataflow()
        key = self.resolve_callable(name, in_class=in_class).callable
        self._require_explicit(key, "it has no control or data flow to return")
        return key, self._addressing.by_key[key].callable

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCfgEdge]:
        """One page of control flow within one callable (see :meth:`JavaAnalysisBackend.get_cfg`).

        ``JCallable.cfg`` keys its endpoints by the *local* body key (``"66:9"``, ``"@entry"``);
        :func:`~cldk.analysis.java.backend.java_body_node_id` joins them to the callable id to give
        the same global spelling the graph writes on ``:JBodyNode.id``, so an endpoint from this
        backend is the one the Neo4j backend returns and the one :meth:`get_source` accepts. The
        join happens *before* the sort, because the order is over the ids a caller sees."""
        key, c = self._graphs_of(callable, in_class, page_size)
        edges = [JCfgEdge(src=java_body_node_id(c.id, e.src), dst=java_body_node_id(c.id, e.dst), kind=e.kind) for e in c.cfg or []]
        return edge_page(JCfgEdge, key, edges, CFG_ORDER, page_size, cursor)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCdgEdge]:
        """One page of control dependence within one callable (see :meth:`JavaAnalysisBackend.get_cdg`)."""
        key, c = self._graphs_of(callable, in_class, page_size)
        edges = [JCdgEdge(src=java_body_node_id(c.id, e.src), dst=java_body_node_id(c.id, e.dst)) for e in c.cdg or []]
        return edge_page(JCdgEdge, key, edges, CDG_ORDER, page_size, cursor)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JDdgEdge]:
        """One page of data dependence within one callable (see :meth:`JavaAnalysisBackend.get_ddg`).

        A **self-loop** (``e.src == e.dst``) is carried through like any other edge — there is no
        pattern here that could drop one, which is the whole difference from the doubled-containment
        Cypher of python-sdk#349."""
        key, c = self._graphs_of(callable, in_class, page_size)
        edges = [JDdgEdge(src=java_body_node_id(c.id, e.src), dst=java_body_node_id(c.id, e.dst), var=e.var, prov=list(e.prov or [])) for e in c.ddg or []]
        return edge_page(JDdgEdge, key, edges, DDG_ORDER, page_size, cursor)

    # -----[ the SDG, built once ]-----
    def _sdg(self) -> Tuple[Dict[str, Dict[str, Dict[str, list]]], Dict[str, Tuple[str, int]]]:
        """``(adjacency, {body-node id: (kind, first line)})`` over the application's SDG, built once.

        ``adjacency`` is ``{"forward": {src: {dst: [label]}}, "backward": {dst: {src: [label]}}}`` —
        both directions, because a backward slice is not derivable from a forward index without
        inverting it, and inverting it per call is the same work done repeatedly. A ``label`` is
        ``(relationship type, var, prov)``: what a path hop has to report, and what the graph carries
        on the corresponding relationship. It is a **list** per ``(src, dst)`` pair because parallel
        edges are ordinary — one statement feeding one argument on several variables is several
        distinct paths — and collapsing them would merge several pieces of evidence into one.
        """
        if self._sdg_cache is None:
            forward: Dict[str, Dict[str, list]] = {}
            backward: Dict[str, Dict[str, list]] = {}
            nodes: Dict[str, Tuple[str, int]] = {}

            def link(src: str, dst: str, label: tuple) -> None:
                forward.setdefault(src, {}).setdefault(dst, []).append(label)
                backward.setdefault(dst, {}).setdefault(src, []).append(label)

            for _, c in self._callables.values():
                for key, node in (c.body or {}).items():
                    nodes[java_body_node_id(c.id, key)] = (node.kind, node.start_line)
                for rel, edges in (("J_DDG", c.ddg), ("J_CDG", c.cdg), ("J_SUMMARY", c.summary)):
                    for e in edges or []:
                        link(java_body_node_id(c.id, e.src), java_body_node_id(c.id, e.dst), (rel, getattr(e, "var", None), tuple(getattr(e, "prov", None) or ())))
            # Endpoints here are already global (the analyzer's L4 overlay resolved them), so they
            # are used as-is: joining them again would mint ids that name nothing.
            for rel, edges in (("J_PARAM_IN", self.application.param_in), ("J_PARAM_OUT", self.application.param_out)):
                for e in edges or []:
                    # ``getattr``, not ``e.var``: ``JParamEdge`` gained the field in codeanalyzer-java
                    # 3.1.2 (codeanalyzer-java#250) and an older payload's edge carries none. Hard-coding ``None`` here (as
                    # this did) left ``_edge_vars_in`` blind to every call-crossing variable, so a
                    # real sanitizer was refused as nonexistent and ``allow_edge``'s ``var == c["var"]``
                    # could never cut at a call boundary -- the one place a taint cut most wants to.
                    link(e.src, e.dst, (rel, getattr(e, "var", None), tuple(getattr(e, "prov", None) or ())))
            self._sdg_cache = ({"forward": forward, "backward": backward}, nodes)
        return self._sdg_cache

    def _reach(self, ref: str, direction: str, depth: int | None) -> set:
        """The set of node ids reachable from ``ref`` in at most ``depth`` hops. Level by level
        rather than a plain stack, because ``depth`` is a hop budget and a depth-first walk cannot
        count hops without revisiting."""
        edges = self._sdg()[0][direction]
        seen, frontier, hops = {ref}, [ref], 0
        while frontier and (depth is None or hops < depth):
            nxt = [d for src in frontier for d in edges.get(src, ()) if d not in seen]
            seen.update(nxt)
            frontier = nxt
            hops += 1
        return seen

    def _value_slice(self, root: SliceNode, *, backward: bool, depth: int | None, max_nodes: int) -> Slice:
        """See :meth:`JavaAnalysisBackend._value_slice`. The whole closure is computed and then cut,
        because ``total`` has to be the size of the whole slice for the cap to be reportable."""
        nodes = self._sdg()[1]
        seen = self._reach(root.ref, "backward" if backward else "forward", depth)
        found = [self._body_slice_node(ref, *nodes[ref]) for ref in sorted(seen) if ref in nodes]
        return Slice(nodes=found[:max_nodes], roots=[root], resolved=slice_resolved([root]), total=len(found))

    def _value_paths(self, a: SliceNode, b: SliceNode, depth: int | None, max_paths: int) -> FlowPaths:
        """See :meth:`JavaAnalysisBackend._value_paths`."""
        adjacency, nodes = self._sdg()
        walks = shortest_walks(adjacency["forward"], a.ref, b.ref, depth, max_paths + 1, via=VIA)
        described = {ref: self._body_slice_node(ref, *nodes[ref]) for walk in walks for ref, _ in walk if ref in nodes}
        described[a.ref] = a
        paths = [flow_path([described[a.ref]] + [described[ref] for ref, _ in walk], [label for _, label in walk], via=VIA) for walk in walks[:max_paths]]
        return FlowPaths(paths=paths, complete=len(walks) <= max_paths)

    def _taint_walk(self, srcs, dsts, *, cuts, cut_callables, depth, max_paths):
        """The sanitized shortest walks, in process (see :meth:`JavaAnalysisBackend._taint_walk`).

        **No** ``self._require_dataflow()`` here, unlike Python's and TypeScript's local walks:
        Java's level gate and its port-lattice gate both live on
        :meth:`JavaAnalysisBackend.taint`, which opens them before the walk is entered, and asking
        again would answer a question already answered.

        One :func:`~cldk.analysis.commons.graphs.shortest_walks` call **per pair**, which is what
        makes ``max_paths + 1`` a per-pair cap here the way ``collect(p)[0..$cap]`` is one over
        Cypher -- a single walk over the flattened lists would let one prolific pair starve the rest.
        Pairs are deduplicated by resolved position first, for the reason
        :func:`~cldk.analysis.commons.graphs.taint_verdict` deduplicates the requested ones: two
        selectors naming one position are one pair, and walking it twice would report each witness
        twice and make a cap of *m* yield *2m*. The graph side gets that free from ``a.id IN $srcs``.

        Both cuts are :func:`~cldk.analysis.commons.graphs.shortest_walks`' predicates rather than a
        filter over what it returns, which is the property the design rests on: the breadth-first
        pass must measure the shortest *satisfying* distance, or a sanitized short route hides a
        clean longer one and the pair comes back refuted. ``allow_edge`` reads the hop's **start**
        node, mirroring the Cypher predicate's ``startNode(r)``, so a variable cut severs only the
        callable the caller named it in -- daytrader8 carries ``arg0`` under both ``buy`` and
        ``completeOrder``, and cutting one leaves the other's four witnesses standing. Both are
        ``None`` when nothing is sanitized: the documented "no filtering" default, and no per-node
        cost on the common call.

        :func:`~cldk.analysis.commons.graphs.under_callable`, never a bare ``startswith``: a Java
        ``can://`` callable id ends in ``)``, so a prefix collision needs a same-arity overload of a
        longer name and cannot happen -- but the predicate is shared with TypeScript, where it can
        (Ruling K), and one spelling on all three backends is what keeps that from being re-derived.

        The ledger comes back empty. Java's frontier signal exists in the payload -- a
        ``JCallSite`` whose ``callee_signature`` is empty -- but filing a diagnostic voids
        ``exhausted`` for the whole batch (Ruling I), and a signal that cannot be told apart from an
        ordinary call into the JDK would void every refutation in every application that makes one.
        Same consequence, equally uncatchable from in here: a pair whose flow leaves through an
        unresolved dispatch is certified ``exhausted``.
        """
        adjacency, nodes = self._sdg()
        allow_node = (lambda nid: not under_callable(nid, cut_callables)) if cut_callables else None
        allow_edge = (lambda frm, _rel, var: not any(var == c["var"] and under_callable(frm, (c["prefix"],)) for c in cuts)) if cuts else None
        pairs: Dict[Tuple[str, str], SliceNode] = {}
        for a in srcs:
            for b in dsts:
                pairs.setdefault((a.ref, b.ref), a)
        rows = []
        for (src_ref, dst_ref), a in pairs.items():
            walks = shortest_walks(adjacency["forward"], src_ref, dst_ref, depth, max_paths + 1, via=VIA, allow_edge=allow_edge, allow_node=allow_node)
            described = {ref: self._body_slice_node(ref, *nodes[ref]) for walk in walks for ref, _ in walk if ref in nodes}
            described[src_ref] = a
            rows.extend((src_ref, dst_ref, flow_path([described[src_ref]] + [described[ref] for ref, _ in walk], [label for _, label in walk], via=VIA)) for walk in walks)
        return rows, {}

    def _edge_vars_in(self, callable_id: str) -> FrozenSet[str]:
        """The edge variables scoped to this callable (see :meth:`JavaAnalysisBackend._edge_vars_in`).

        Off the adjacency :meth:`_sdg` already caches, so a variable sanitizer costs a scan of it and
        no second traversal. Edges *leaving* a node under ``callable_id`` -- the same ``startNode``
        scoping the cut itself uses, so this validates exactly the domain the cut can match.
        ``J_CDG`` and the two port relationships on a pre-3.1.2 payload carry no ``var``; those
        those ``None`` values are dropped, because ``resolve_sanitizers`` refuses a blank variable before it
        ever asks.
        """
        forward = self._sdg()[0]["forward"]
        return frozenset(var for src, outs in forward.items() if under_callable(src, (callable_id,)) for labels in outs.values() for _rel, var, _prov in labels if var)

    def _value_reaches(self, src: str, dsts: Sequence[str], depth: int | None) -> bool:
        """See :meth:`JavaAnalysisBackend._value_reaches`."""
        return not self._reach(src, "forward", depth).isdisjoint(set(dsts) - {src})

    @property
    def _ports_carry_dependence(self) -> bool:
        """See :meth:`JavaAnalysisBackend._ports_carry_dependence` — asked of the payload's own
        ``formal_in`` vertices, which is free once :meth:`_sdg` is built.

        ``dst in nodes`` is the whole of the parity with the Neo4j spelling, which matches
        ``(b:JBodyNode)-[…]->(m:JBodyNode)`` and so can only see an edge whose **target was emitted
        as a node**. codeanalyzer-java 3.0.2 emitted 87 of daytrader8's 5,434 ddg edges naming an
        endpoint it never emitted, and without this clause such an edge would count here and not
        there — one boolean, computed from two definitions, deciding whether four accessors raise
        or answer. 3.0.3 drops those edges (codeanalyzer-java#228; measured: 0 dangling endpoints
        on the whole of daytrader8, and 0 on the committed fixtures in both releases), so the
        clause has nothing to exclude today. It stays because the Neo4j floor is 3.0.1 and an
        older emitter's output is still attachable."""
        adjacency, nodes = self._sdg()
        return any(kind == "formal_in" and any(dst in nodes for dst in adjacency["forward"].get(ref, ())) for ref, (kind, _) in nodes.items())

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> JApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        return self.application.symbol_table

    def get_compilation_units(self) -> List[JCompilationUnit]:
        return list(self.application.symbol_table.values())

    def get_java_file(self, qualified_class_name: str) -> str | None:
        return self._file_of.get(qualified_class_name)

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        return self.application.symbol_table[file_path]

    def get_system_dependency_graph(self) -> list[JGraphEdges]:
        """The wire call graph (``JApplication.call_graph``), one :class:`JCallGraphEdge` per edge."""
        return self.application.call_graph

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) the call graph keyed by ``"<type fqn>.<signature>"`` (J-1): node attrs
        ``method_detail`` / ``kind="callable"``; edge attrs ``type="CALL_DEP"``, ``weight``,
        ``calling_lines``. Empty below level 2 (the wire carries no ``call_graph`` there)."""
        if self._call_graph is not None:
            return self._call_graph
        cg = nx.DiGraph()
        lines = CallingLines()
        for edge in self.application.call_graph:
            if self._is_external(edge.src) or self._is_external(edge.dst):
                continue
            src, src_detail = self._node_of(edge.src)
            dst, dst_detail = self._node_of(edge.dst)
            cg.add_node(src, method_detail=src_detail, kind="callable")
            cg.add_node(dst, method_detail=dst_detail, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=edge.weight, calling_lines=lines.of(src_detail.method, dst_detail.method))
        self._call_graph = cg
        return cg

    def get_call_graph_json(self) -> str:
        cg = self.get_call_graph()
        rows = []
        for source, target, calling_lines in cg.edges.data("calling_lines"):
            s: JMethodDetail = cg.nodes[source]["method_detail"]
            t: JMethodDetail = cg.nodes[target]["method_detail"]
            rows.append(
                {
                    "source_method_signature": s.method.signature,
                    "source_method_body": s.method.code,
                    "source_class": s.klass,
                    "target_method_signature": t.method.signature,
                    "target_method_body": t.method.code,
                    "target_class": t.klass,
                    "calling_lines": calling_lines,
                }
            )
        return json.dumps(rows)

    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        cg = self._symbol_table_call_graph(target_class_name, target_method_signature, is_target=True) if using_symbol_table else self.get_call_graph()
        key = f"{target_class_name}.{target_method_signature}"
        if key not in cg:
            return {}
        return {
            "caller_details": [{"caller_method": cg.nodes[s]["method_detail"], "calling_lines": d["calling_lines"]} for s, _, d in cg.in_edges(key, data=True)],
            "target_method": cg.nodes[key]["method_detail"],
        }

    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        cg = self._symbol_table_call_graph(source_class_name, source_method_signature) if using_symbol_table else self.get_call_graph()
        key = f"{source_class_name}.{source_method_signature}"
        if key not in cg:
            return {}
        return {
            "callee_details": [{"callee_method": cg.nodes[t]["method_detail"], "calling_lines": d["calling_lines"]} for _, t, d in cg.out_edges(key, data=True)],
            "source_method": cg.nodes[key]["method_detail"],
        }

    @staticmethod
    def _edges_out_of(cg: nx.DiGraph, qualified_class_name: str, method_signature: str | None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        if method_signature is None:
            seeds = [n for n, a in cg.nodes(data=True) if a["method_detail"].klass == qualified_class_name]
        else:
            key = f"{qualified_class_name}.{method_signature}"
            seeds = [key] if key in cg else []
        return [(cg.nodes[s]["method_detail"], cg.nodes[t]["method_detail"]) for s, t in cg.edges(seeds)]

    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        return self._edges_out_of(self.get_call_graph(), qualified_class_name, method_name)

    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Edges out of a class (or one method) resolved from its call sites through the symbol
        table alone — incomplete by construction: only receivers the symbol table can see, only
        concrete implementations up the ``extends`` chain."""
        return self._edges_out_of(self._symbol_table_call_graph(qualified_class_name, method_signature), qualified_class_name, method_signature)

    # -----[ symbol-table call graph (call sites → declarations) ]-----
    def _symbol_table_call_graph(self, qualified_class_name: str, method_signature: str | None, is_target: bool = False) -> nx.DiGraph:
        cg = nx.DiGraph()
        lines = CallingLines()
        edges = self._st_edges_into(qualified_class_name, method_signature) if is_target else self._st_edges_from(qualified_class_name, method_signature)
        for source, target in edges:
            src, dst = f"{source.klass}.{source.method.signature}", f"{target.klass}.{target.method.signature}"
            cg.add_node(src, method_detail=source, kind="callable")
            cg.add_node(dst, method_detail=target, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=1, calling_lines=lines.of(source.method, target.method))
        return cg

    def _st_edges_from(self, qualified_class_name: str, method_signature: str | None) -> Iterable[Tuple[JMethodDetail, JMethodDetail]]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return
        if method_signature is None:
            sources = list(klass.callables.values())
        else:
            source = self.get_method(qualified_class_name, method_signature)
            sources = [source] if source is not None else []
        for source in sources:
            for call_site in source.call_sites:
                target, target_class = self._resolve_call_site(qualified_class_name, call_site)
                if target is not None:
                    yield self._detail(qualified_class_name, source), self._detail(target_class, target)

    def _st_edges_into(self, target_class_name: str, target_method_signature: str) -> Iterable[Tuple[JMethodDetail, JMethodDetail]]:
        target = self.get_method(target_class_name, target_method_signature)
        if target is None:
            return
        for owner, source in self._callables.values():
            for call_site in source.call_sites:
                found, found_class = self._resolve_call_site(owner.qualified_name, call_site)
                if found is not None and found_class == target_class_name and call_site.callee_signature == target_method_signature:
                    yield self._detail(owner.qualified_name, source), self._detail(target_class_name, target)

    def _resolve_call_site(self, owner_class_name: str, call_site: JCallSite) -> Tuple[JCallable | None, str]:
        """The (declaration, declaring class) a call site names, or ``(None, "")``: an explicit
        receiver type is followed only when it is a project class; an implicit receiver means the
        owning class (and its ``extends`` chain)."""
        if not call_site.callee_signature:
            return None, ""
        if call_site.receiver_type:
            if self.get_class(call_site.receiver_type) is None:
                return None, ""
            return self._find_in_hierarchy(call_site.receiver_type, call_site.callee_signature)
        return self._find_in_hierarchy(owner_class_name, call_site.callee_signature)

    def _find_in_hierarchy(self, qualified_class_name: str, method_signature: str) -> Tuple[JCallable | None, str]:
        """The concrete declaration of ``method_signature`` on the class or up its ``extends``
        chain; interface declarations are not call-graph targets and are skipped."""
        klass = self.get_class(qualified_class_name)
        method = self.get_method(qualified_class_name, method_signature)
        if method is not None and klass is not None and not klass.is_interface:
            return method, qualified_class_name
        if klass is not None:
            for parent in klass.extends_list:
                found, found_class = self._find_in_hierarchy(parent, method_signature)
                if found is not None:
                    return found, found_class
        return None, ""

    # -----[ classes / methods / fields ]-----
    def get_all_classes(self) -> Dict[str, JType]:
        return dict(self._types)

    def get_class(self, qualified_class_name: str) -> JType | None:
        return self._types.get(qualified_class_name)

    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        return {name: t.callable_declarations for name, t in self._types.items()}

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, JCallable]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return {}
        return {sig: c for sig, c in klass.callables.items() if not c.is_constructor}

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        klass = self.get_class(qualified_class_name)
        if klass is None:
            return {}
        return {sig: c for sig, c in klass.callables.items() if c.is_constructor}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> JCallable | None:
        klass = self.get_class(qualified_class_name)
        return klass.callables.get(qualified_method_name) if klass is not None else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[JCallableParameter]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return method.parameters if method is not None else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, JType]:
        return {name: t for name, t in self._types.items() if qualified_class_name in t.extends_list or qualified_class_name in t.implements_list}

    def get_all_fields(self, qualified_class_name: str) -> List[JField]:
        klass = self.get_class(qualified_class_name)
        return klass.field_declarations if klass is not None else []

    def get_all_nested_classes(self, qualified_class_name: str) -> List[JType]:
        klass = self.get_class(qualified_class_name)
        return list(klass.types.values()) if klass is not None else []

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        klass = self.get_class(qualified_class_name)
        return klass.extends_list if klass is not None else []

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        klass = self.get_class(qualified_class_name)
        return klass.implements_list if klass is not None else []

    # -----[ entry points ]-----
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        result: Dict[str, Dict[str, JCallable]] = {}
        for name, methods in self.get_all_methods_in_application().items():
            entrypoints = {sig: c for sig, c in methods.items() if c.is_entrypoint}
            if entrypoints:
                result[name] = entrypoints
        return result

    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        return {name: t for name, t in self._types.items() if t.is_entrypoint_class}

    # -----[ CRUD (J-4) ]-----
    def get_all_crud_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_create_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_read_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_update_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    def get_all_delete_operations(self) -> List[CRUDRow]:
        raise CodeanalyzerExecutionException(CRUD_UNAVAILABLE)

    # -----[ repository artifacts — the shared Py* models, as the generic ABC promises ]-----
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Every non-code artifact (see :meth:`AnalysisBackend.get_artifacts`), keyed by repo-relative
        path as the wire keys them. ``JArtifact.text_truncated`` has no home on the shared model and
        is not carried; read it off ``JApplication.artifacts`` when it matters."""
        return {path: PyArtifact(**a.model_dump(exclude={"config_keys", "text_truncated"}), config_keys=[PyConfigKey(**ck.model_dump()) for ck in a.config_keys]) for path, a in self.application.artifacts.items()}

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        """Every declared dependency, optionally filtered (see :meth:`AnalysisBackend.get_dependencies`).
        The Maven ``group`` coordinate has no home on the shared model and is not carried; read it off
        ``JApplication.dependencies`` when ``name`` alone is ambiguous."""
        deps = [PyDependency(**d.model_dump(exclude={"group"})) for d in self.application.dependencies]
        if direct_only:
            deps = [d for d in deps if d.direct]
        if ecosystem is not None:
            deps = [d for d in deps if d.ecosystem == ecosystem]
        if declared_in is not None:
            deps = [d for d in deps if d.declared_in == declared_in]
        return deps

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Every configuration key flattened out of the config-bearing artifacts, keyed
        ``"<artifact repo-relative path>@key/<dotted key>"`` (``pom.xml@key/project.artifactId``).

        That key is the analyzer's own id with its ``can://<app>/artifact/`` prefix dropped: the
        application name belongs to the run, not to the key, so keying by the raw id made the two
        backends share **zero** keys whenever the graph was emitted under a different ``--app-name``
        than the local run passes (the SDK passes the project directory's name). ``can://`` ids also
        stay off the public surface (E6); the id is still on ``PyConfigKey.id``.
        """
        return {f"{path}@key/{ck.key}": PyConfigKey(**ck.model_dump()) for path, a in self.application.artifacts.items() for ck in a.config_keys}

    # -----[ comments ]-----
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        method = self.get_method(qualified_class_name, method_signature)
        return method.comments if method is not None else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        klass = self.get_class(qualified_class_name)
        return klass.comments if klass is not None else []

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        unit = self.application.symbol_table.get(file_path)
        if unit is None:
            raise CodeanalyzerExecutionException(f"File {file_path} not found in the symbol table.")
        return unit.comments

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        return {path: unit.comments for path, unit in self.application.symbol_table.items()}

    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        docstrings = {}
        for path, comments in self.get_all_comments().items():
            javadoc = [c for c in comments if c.is_javadoc]
            if javadoc:
                docstrings[path] = javadoc
        return docstrings

    def remove_all_comments(self, src_code: str) -> str:
        raise NotImplementedError("This function is not implemented yet.")
