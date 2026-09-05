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

"""Neo4j-backed Python analysis backend (read-only Cypher client).

A drop-in alternative to :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`: it exposes the
**same query method surface** (the 21 methods of :class:`PythonAnalysisBackend`) so the
:class:`~cldk.analysis.python.PythonAnalysis` facade can delegate to either one, but every method
answers by running **Cypher over a live Neo4j graph** instead of walking the in-memory
pydantic / NetworkX structures. Mirrors :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`.

This class is purely a **query client**: it never builds the graph and has no dependency on the
``codeanalyzer-python`` library or the project sources. It assumes the database is already
populated and just polls it — the shape a cloud deployment wants, where a job loads the graph out
of band and the SDK only reads it.

The graph is the one ``codeanalyzer-python`` (>= 0.2.0) emits with ``--emit neo4j`` (in-process:
``codeanalyzer.neo4j.emit.emit_neo4j``). Populating it always happens out of band — never from this
backend.

Identity model (must match the in-memory backend; see ``codeanalyzer/neo4j/project.py``):

* a class/callable/external is keyed by ``id``, under its specific label — ``:PyClass`` /
  ``:PyCallable`` / ``:PyExternal`` — which is all this backend ever matches on: the producer also
  stamps a shared secondary label across all three (a declared symbol is id-keyed there too, not
  signature-keyed, unlike 0.3.x — the one exception is an unresolved ``PY_EXTENDS`` base-class
  ghost, still merged by ``signature``, irrelevant to every query below), but the specific labels
  already uniquely identify these nodes so the shared one goes unqueried;
* a module is a ``:PyModule`` keyed by ``file_key`` (which equals the original ``PyModule.file_path``
  and the symbol-table key);
* call-graph edges are ``(:PyCallable|:PyExternal)-[:PY_CALLS {weight, prov}]->(...)`` with a
  constant ``CALL_DEP`` type;
* class inheritance is ``(:PyClass)-[:PY_EXTENDS]->(:PyClass)`` (plus a ``base_classes`` property);
* every project-owned node carries a ``_module`` provenance property, so a single database may hold
  several applications — all queries here are scoped to this backend's application, anchored on
  ``(:PyApplication {name})-[:PY_HAS_MODULE]->(:PyModule)``.

In-memory dict keys this backend reproduces exactly (the projection stores nodes by ``signature``
only, so the keys are rebuilt from node properties): ``module.types`` / a class's own ``types`` →
``signature``; ``module.functions`` / a class's own ``callables`` / a callable's own ``callables``
→ short ``name``; ``attributes`` → ``name``. ``get_all_classes`` / ``get_class`` return
**top-level** classes only
(``PyModule-[:PY_DECLARES]->PyClass``), matching the in-memory backend.

Parity: verified against a real 57-module project — every node and edge **present in the graph**
reconstructs identically to the in-memory ``PyCodeanalyzer`` (3169/3200 checks; on the call edges
present in both, zero weight/provenance mismatches). The residual gap is not in this backend:

* **Upstream emitter gap (not recoverable here):** ``codeanalyzer-python``'s projection drops call
  edges whose target is a bare module name that is *also* imported (e.g. a call to ``os`` /
  ``re`` / ``json`` when ``import os`` is present) — its ``RowBuilder`` keys ``:PyPackage`` names
  and call-target signatures in the same id namespace as declared symbols, so the edge gets a
  dangling reference and is silently dropped by the writer. Those edges never reach Neo4j, so the
  call graph here can
  be missing a small fraction of external-target edges. This is a producer bug, not a query bug.
* **Projection-lossy fields** (inherent to what the graph stores — see :mod:`reconstruct`): comments
  collapse to a single docstring (module-level comments dropped); ``PyVariableDeclaration.value`` and
  its column span, plus per-binding import detail, are not recoverable; the order of ``call_graph``
  edges and a callable's ``call_sites`` / ``local_variables`` is positional, not insertion order.

Everything else round-trips identically to ``PyCodeanalyzer``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Dict, List, Sequence, Tuple

import networkx as nx
from codeanalyzer.schema import model_dump_json
from codeanalyzer.schema.ids import application_id

from cldk.analysis.commons.resolve import CallableCandidate, resolve_callable_signature, resolve_value_name, resolve_within, value_candidate
from cldk.analysis.commons.results import CallableRef, Diagnostic, EntrypointCoverage, LocateResult, ModuleRef, SliceNode, TypeRef
from cldk.analysis.python.backend import PythonAnalysisBackend, body_key_column, call_graph_scope, check_selector, resolve_module_key, scope_paths
from cldk.analysis.python.neo4j import reconstruct as R
from cldk.models.python import (
    BodyNode,
    PyApplication,
    PyArtifact,
    PyCallEdge,
    PyCallable,
    PyCallableOverview,
    PyCallsite,
    PyClass,
    PyClassAttribute,
    PyClassOverview,
    PyConfigKey,
    PyConfigRead,
    PyConfigUseEdge,
    PyDependency,
    PyExternalSymbol,
    PyModule,
    Span,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, GraphSchemaMismatch

logger = logging.getLogger(__name__)

# One statement per parent->child collection, each fetching that whole collection for the *entire*
# application in a single round trip and returning the parent's key as ``pk``. These are the bulk
# twins of the per-parent statements inlined in ``PyNeo4jBackend._callable_full`` / ``_class_full``
# / ``_module_full``, and reproduce those statements' row shapes exactly so either source can feed
# the same reconstruction code (see ``PyNeo4jBackend._children``).
#
# Scoping: the module-level buckets key on ``m.file_key``, the rest on the parent's ``_module``
# provenance property -- which the emitter indexes for every module-owned label (see
# ``codeanalyzer/neo4j/schema.py``'s ``INDEXES``). Both confine the result to this backend's
# application. The per-parent statements carry the *same* ``IN $mods`` predicate on the parent
# (``PyNeo4jBackend._children`` supplies ``mods`` to every unprimed run), because a bare
# ``{signature: $sig}`` would also match a same-signature node belonging to another application
# in a shared database -- and a Unified Knowledge Graph holding several applications is the
# expected deployment. Without it the two paths provably disagree there: ``get_class`` would
# merge another application's methods while ``get_all_classes`` would not.
_BULK_CHILD_QUERIES: Dict[str, str] = {
    # module -> its own top-level declarations
    "module_classes": "MATCH (m:PyModule)-[:PY_DECLARES]->(c:PyClass) WHERE m.file_key IN $mods RETURN m.file_key AS pk, properties(c) AS p",
    "module_functions": "MATCH (m:PyModule)-[:PY_DECLARES]->(f:PyCallable) WHERE m.file_key IN $mods RETURN m.file_key AS pk, properties(f) AS p",
    "module_variables": (
        "MATCH (m:PyModule)-[:PY_DECLARES_VAR]->(v:PyVariable) WHERE m.file_key IN $mods "
        "RETURN m.file_key AS pk, properties(v) AS p ORDER BY v.start_line, v.name"
    ),
    "module_imports": (
        "MATCH (m:PyModule)-[e:PY_IMPORTS]->(pkg:PyPackage) WHERE m.file_key IN $mods "
        "RETURN m.file_key AS pk, pkg.name AS module, e.imported_names AS names"
    ),
    # class -> its members
    "class_methods": "MATCH (c:PyClass)-[:PY_HAS_METHOD]->(m:PyCallable) WHERE c._module IN $mods RETURN c.signature AS pk, properties(m) AS p",
    "class_attributes": "MATCH (c:PyClass)-[:PY_HAS_ATTRIBUTE]->(a:PyAttribute) WHERE c._module IN $mods RETURN c.signature AS pk, properties(a) AS p",
    "class_inner_classes": "MATCH (c:PyClass)-[:PY_DECLARES]->(ic:PyClass) WHERE c._module IN $mods RETURN c.signature AS pk, properties(ic) AS p",
    # callable -> its body and nested declarations
    "callable_callsites": (
        "MATCH (f:PyCallable)-[:PY_HAS_BODY_NODE]->(s:PyBodyNode {kind: 'call'}) WHERE f._module IN $mods "
        "RETURN f.signature AS pk, properties(s) AS p ORDER BY s.start_line"
    ),
    "callable_inner_callables": "MATCH (f:PyCallable)-[:PY_DECLARES]->(d:PyCallable) WHERE f._module IN $mods RETURN f.signature AS pk, properties(d) AS p",
    "callable_inner_classes": "MATCH (f:PyCallable)-[:PY_DECLARES]->(d:PyClass) WHERE f._module IN $mods RETURN f.signature AS pk, properties(d) AS p",
    "callable_variables": (
        "MATCH (f:PyCallable)-[:PY_DECLARES_VAR]->(v:PyVariable) WHERE f._module IN $mods "
        "RETURN f.signature AS pk, properties(v) AS p ORDER BY v.start_line, v.name"
    ),
}


class PyNeo4jBackend(PythonAnalysisBackend):
    """Query the application view of a Python project over Neo4j (Cypher), read-only.

    The graph must already be loaded out of band — e.g. a job running
    ``codeanalyzer-python --emit neo4j``. This backend never writes and needs neither the
    ``codeanalyzer-python`` library nor the project sources on disk.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``:PyApplication`` anchor name to scope every query to. Matches the
            ``--app-name`` the graph was loaded with (defaults to the project directory name).

    ``has_resolution_edges`` (see :meth:`PythonAnalysisBackend.has_resolution_edges`) is ``True``
    iff this application's graph has at least one ``PY_RESOLVES_TO`` edge — probed once at
    construction (see :meth:`_probe_resolution_edges`).

    **Server version:** ``get_call_graph(roots=...)`` compiles to a quantified path pattern, so it
    needs **Neo4j 5.9 or newer**; every other accessor here runs on any 5.x. See
    :meth:`_bounded_call_rows` for why the walk cannot be expressed as a variable-length pattern.
    """

    #: Neo4j relationship-type and node-label prefixes for codeanalyzer-python's graph vocabulary
    #: (see :class:`~cldk.analysis.commons.backend.AnalysisBackend`).
    P = "PY"
    N = "Py"

    #: Relationship types every supported graph must have. A graph missing any of these was
    #: built by a different codeanalyzer-python generation (see ``_probe_schema``); their
    #: absence is not exercised individually, they are just the cheapest reliable fingerprint.
    #: ``HAS_ARTIFACT`` is deliberately NOT added here even though it is what
    #: get_artifacts()/get_dependencies()/get_config_keys()/get_config_uses() query: it was
    #: introduced in the very same codeanalyzer-python generation as ``PY_HAS_BODY_NODE`` (both
    #: Task 6/#152/#162, both 1.4.0-only), so a pre-1.4.0 graph is already caught by the existing
    #: fingerprint. Adding it too would make a *genuinely* artifact-less v2 project (no non-.py
    #: files at all) fail schema probing outright, for a layer that was never in that project to
    #: begin with -- the wrong kind of "absence is never null".
    _REQUIRED_RELATIONSHIP_TYPES: frozenset[str] = frozenset({"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_BODY_NODE", "PY_CALLS"})

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_username: str | None = None,
        neo4j_password: str | None = None,
        neo4j_database: str | None = None,
        application_name: str | None = None,
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException(
                "The Neo4j backend requires the 'neo4j' driver. Install it with "
                "`pip install neo4j` (or `pip install cldk[neo4j]`)."
            ) from e
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password))
        self._init_with_driver(driver, application_name=application_name, neo4j_database=neo4j_database)

    @classmethod
    def _from_driver(cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> "PyNeo4jBackend":
        """Construct directly from an already-built driver — exists for tests injecting a fake driver."""
        self = cls.__new__(cls)
        self._init_with_driver(driver, application_name=application_name, neo4j_database=neo4j_database)
        return self

    def _init_with_driver(self, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> None:
        if not application_name:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        self.application_name = application_name
        self._database = neo4j_database
        self._driver = driver
        # One long-lived read session reused across queries (see _run). Reconstruction is an N+1
        # fan-out, so reopening a session per query added real per-call overhead. Created lazily.
        self._session_obj: Any | None = None

        # The attached server's version, filled in by _probe_schema; None means "not readable",
        # which blocks nothing (see _read_server_version).
        self._server_version: Tuple[int, ...] | None = None

        # Fail fast if the attached graph doesn't speak this backend's vocabulary — one round
        # trip, run once per connection, before any query can silently come back empty.
        self._probe_schema()

        # The application's module file_keys, used to scope every query to this app.
        self._modules: List[str] = self._load_module_keys()
        # Whether this application's graph carries any per-callsite resolution data at all --
        # probed once here, same pattern as _probe_schema (see _probe_resolution_edges).
        self._has_resolution_edges: bool = self._probe_resolution_edges()
        # Lazily-built call graph cache (mirrors PyCodeanalyzer.call_graph).
        self._call_graph: nx.DiGraph | None = None

    # The Cypher generation the bounded call graph needs. A quantified path pattern —
    # ``(a)-[:R]->(b) WHERE ...){0,n}``, the only way to put a predicate on *every* hop of a walk —
    # arrives in Neo4j 5.9. Recorded at attach (below), enforced at the one call that needs it
    # (:meth:`_bounded_call_rows`), so an older server keeps serving every other accessor.
    _QUANTIFIED_PATH_MIN_SERVER = (5, 9)

    def _probe_schema(self) -> None:
        """Verify the connected graph's vocabulary once, at connection time, and record the
        server's version while the connection is already open.

        A graph built by a different ``codeanalyzer-python`` generation (or an empty/asset-only
        database) answers every one of this backend's Cypher queries with zero rows — no error,
        nothing to distinguish it from "this codebase has no callables". This runs
        ``CALL db.relationshipTypes()`` (one round trip, read-only) and compares the result
        against :attr:`_REQUIRED_RELATIONSHIP_TYPES`, raising :class:`GraphSchemaMismatch` if any
        are absent.

        The server version is read here for the same reason — attach is the one place already
        talking to the server before any accessor runs — but is deliberately **not** acted on here:
        see :attr:`_QUANTIFIED_PATH_MIN_SERVER`.
        """
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        missing = self._REQUIRED_RELATIONSHIP_TYPES - found
        if missing:
            raise GraphSchemaMismatch(expected=set(self._REQUIRED_RELATIONSHIP_TYPES), found=found, missing=missing)
        self._server_version = self._read_server_version()

    def _read_server_version(self) -> Tuple[int, ...] | None:
        """The attached server's version as an int tuple, or ``None`` when it cannot be read.

        ``None`` means *unknown*, and an unknown version blocks nothing: a server that will not
        answer ``dbms.components()`` (a stub driver in a test, a deployment that restricts the
        procedure) is not evidence of an old one, and refusing to run on that basis would be a
        guess dressed as a check. If such a server really is pre-5.9, its own parser reports the
        syntax error, which is the same outcome as before this check existed.
        """
        try:
            rows = self._run("CALL dbms.components() YIELD versions RETURN versions[0] AS v")
        except Exception:  # noqa: BLE001 - an unreadable version is "unknown", never fatal
            return None
        raw = rows[0].get("v") if rows else None
        if not isinstance(raw, str):
            return None
        parts = raw.split("-", 1)[0].split(".")
        return tuple(int(x) for x in parts if x.isdigit()) or None

    def _probe_resolution_edges(self) -> bool:
        """Whether this application's graph carries any ``PY_RESOLVES_TO`` edge at all — probed
        once here, same "one round trip at construction" pattern as :meth:`_probe_schema`.

        ``get_callsites_for``'s per-site ``callee_signature`` is ``None`` both for "genuinely
        unresolved" and, in principle, for "this graph was populated at an analysis level below the
        one where the defuse-linker backfill runs, so ``PY_RESOLVES_TO`` doesn't exist at all" —
        ``PyCallsite`` is the analyzer's own frozen model with no field to carry that distinction
        (see :meth:`PythonAnalysisBackend.get_callsites_for`). ``:PyApplication`` carries no
        ``max_level``/provenance marker either (its projected properties are ``name``,
        ``schema_version``, ``analyzer_name``, ``analyzer_version``, ``repo_uri``,
        ``source_revision``, ``repo_dirty`` — verified against ``codeanalyzer/neo4j/schema.py``,
        nothing else), so this probe is the only way this backend could learn which situation a
        caller is in, if that second situation were ever real.

        It is not, on the documented ingestion path: ``codeanalyzer/__main__.py`` forces
        ``analysis_level = 4`` unconditionally for ``--emit neo4j`` (level/``--graphs`` cannot even
        be passed alongside it — a ``typer.Exit`` if you try), so ``backfill_callees`` (gated on
        ``analysis_level >= 2``) always runs and ``PY_RESOLVES_TO`` always exists on any graph
        built that way. ``has_resolution_edges`` is expected to be ``True`` on every such graph;
        this probe is defensive against a graph built some other way (a hand-populated database, an
        older/forked emitter generation) rather than a gap in the documented pipeline — a corrected
        finding from this leg's own review ledger, which had assumed the SDK's own local-backend
        default analysis level (1) also applied to Neo4j ingestion.

        This is information, not an error — this never raises the way :meth:`_probe_schema` does.
        """
        rows = self._run(
            "MATCH (s:PyBodyNode)-[:PY_RESOLVES_TO]->() WHERE s._module IN $mods RETURN s LIMIT 1",
            mods=self._modules,
        )
        return bool(rows)

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`PythonAnalysisBackend.has_resolution_edges`. Fixed at construction by
        :meth:`_probe_resolution_edges`."""
        return self._has_resolution_edges

    # -----[ lifecycle ]-----
    # Set — to a lazily filled ``bucket -> parent key -> rows`` index — only inside
    # :meth:`_bulk`; ``None`` means "unprimed", i.e. every child collection is fetched with
    # its own scoped query. A class attribute rather than an ``__init__`` assignment so an
    # instance built through the ``object.__new__`` seam the unit tests use sees it too.
    _prefetch: Dict[str, Dict[str, List[Dict[str, Any]]]] | None = None

    #: The module keys the live prefetch buckets are scoped to. Set alongside ``_prefetch`` by
    #: :meth:`_bulk`, and normally the whole application — but a scoped accessor
    #: (``get_symbol_table(paths=...)``, ``get_all_classes(module=...)``) narrows it, so asking for
    #: one module does not prefetch the application's other 77,000 call sites to answer.
    _prefetch_scope: List[str] | None = None

    def close(self) -> None:
        """Close the reused session (if any) and the underlying Neo4j driver."""
        self._close_session()
        self._driver.close()

    def _close_session(self) -> None:
        if self._session_obj is not None:
            try:
                self._session_obj.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._session_obj = None

    def __enter__(self) -> "PyNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _session(self) -> Any:
        """The reused read session, opened lazily on first use."""
        if self._session_obj is None:
            self._session_obj = self._driver.session(database=self._database)
        return self._session_obj

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run a Cypher statement and return the records as plain dicts (nodes/rels → prop maps).

        Reuses one long-lived session across calls. If a query fails the session may be left in a
        bad state, so it is dropped before re-raising and the next call reopens a fresh one.
        """
        try:
            return [record.data() for record in self._session().run(query, **params)]
        except Exception:
            self._close_session()
            raise

    def _load_module_keys(self) -> List[str]:
        """The application's module ``file_key``s — the scope key for every other query."""
        rows = self._run(
            "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule) RETURN m.file_key AS k",
            app=self.application_name,
        )
        return [r["k"] for r in rows]

    # =====================================================================================
    # Reconstruction helpers — fetch a node's children over Cypher, then assemble via R.
    #
    # Rebuilding a module walks module → class → method → nested-anything, and each child
    # collection below used to cost one round trip **per parent node**: 73,669 of them to rebuild
    # a 1,626-module application (45.3 per module at ~4.9 ms each, ~363 s wall clock) — a textbook
    # N+1 against a database answering every individual query in five milliseconds. Inside
    # :meth:`_bulk` each collection is instead fetched **once for the whole application** and
    # served from a by-parent index (:data:`_BULK_CHILD_QUERIES`), so a bulk accessor pays one
    # round trip per collection it actually reads — at most eleven — however many modules, classes
    # and callables it walks.
    #
    # **On nesting depth:** there is none to bound. The recursion is real (an inner class has
    # methods, a nested callable has call sites), but it never happens *in Cypher*: every bulk
    # statement is a single flat hop scoped by the parent's ``_module`` provenance property, which
    # every projected node carries at every nesting depth — ``codeanalyzer/neo4j/project.py``
    # threads the module's ``file_key`` down through ``_project_class`` / ``_project_callable``'s
    # own recursion, so a class nested five levels deep appears in the ``class_inner_classes`` rows
    # exactly like a top-level one. The tree is then rebuilt in Python by the same recursive calls
    # as before, to whatever depth the graph actually has. No variable-length path, no depth
    # ceiling, and therefore no depth at which a deeply nested declaration would be silently
    # truncated.
    # =====================================================================================
    def _children(self, bucket: str, key: str, query: str, **params: Any) -> List[Dict[str, Any]]:
        """The child rows of one parent node — from the bulk index when primed, one query when not.

        Unprimed (the default, and what every single-node accessor pays) runs ``query``: the
        statement naming this one parent, application-scoped by the ``mods`` parameter this
        method supplies, exactly as its bulk twin is scoped. Primed
        (inside :meth:`_bulk`) answers from ``_BULK_CHILD_QUERIES[bucket]``, fetched
        lazily on first use so an accessor is never charged for a collection it does not read —
        ``get_all_classes`` never touches the four module-level buckets. Both paths yield the same
        row shape, so the reconstruction below cannot tell them apart.

        A single-node accessor (``get_class``, ``get_method``, ``get_python_module``) deliberately
        stays unprimed: prefetching an application's every call site to answer about one class
        would trade an N+1 for a much larger constant.
        """
        if self._prefetch is None:
            return self._run(query, mods=self._modules, **params)
        index = self._prefetch.get(bucket)
        if index is None:
            index = self._prefetch[bucket] = self._collect(bucket)
        return index.get(key, [])

    def _collect(self, bucket: str) -> Dict[str, List[Dict[str, Any]]]:
        """One whole child collection for this application, in one round trip, grouped by ``pk``."""
        index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in self._run(_BULK_CHILD_QUERIES[bucket], mods=self._prefetch_scope):
            index[row["pk"]].append(row)
        return index

    @contextmanager
    def _bulk(self, mods: Sequence[str] | None = None) -> Any:
        """Serve child collections from prefetches for the duration of the block.

        ``mods`` is the module scope the buckets are fetched for — the whole application by
        default, or the subset a scoped accessor asked for, so ``get_symbol_table(paths=[one])``
        prefetches one module's children rather than the application's.

        Re-entrant: an inner block reuses (and does not discard) an outer block's index *and its
        scope* — narrowing inside an already-primed block would serve a half-filled bucket as if
        it were complete.
        """
        outer, outer_scope = self._prefetch, self._prefetch_scope
        if outer is None:
            self._prefetch = {}
            self._prefetch_scope = list(mods) if mods is not None else self._modules
        try:
            yield
        finally:
            self._prefetch, self._prefetch_scope = outer, outer_scope

    def _callable_full(self, props: Dict[str, Any]) -> PyCallable:
        """Rebuild a full :class:`PyCallable` (call sites, inner callables/classes, locals).

        Call sites are built with ``R.callsite(r["p"])`` — no ``callee_signature`` keyword — so
        every call site reconstructed this way carries ``callee_signature=None`` *forever*, not
        merely for the genuinely-unresolved case: this method never follows ``PY_RESOLVES_TO`` at
        all. Every public accessor that bottoms out here (directly or via :meth:`_class_full` /
        :meth:`_module_full`) inherits that — ``get_method``, ``get_all_methods_in_class``,
        ``get_all_constructors``, ``get_all_methods_in_application``, ``get_class`` /
        ``get_all_classes`` and their siblings, ``get_symbol_table`` / ``get_python_module``.
        :meth:`get_callsites_for` is the only accessor on this backend that resolves the identical
        underlying call site — use it when a caller needs ``callee_signature`` populated.
        """
        sig = props["signature"]
        call_sites = [
            R.callsite(r["p"])
            for r in self._children(
                "callable_callsites",
                sig,
                "MATCH (par:PyCallable {signature: $sig})-[:PY_HAS_BODY_NODE]->(s:PyBodyNode {kind: 'call'}) "
                "WHERE par._module IN $mods RETURN properties(s) AS p ORDER BY s.start_line",
                sig=sig,
            )
        ]
        inner_callables: Dict[str, PyCallable] = {}
        for r in self._children(
            "callable_inner_callables",
            sig,
            "MATCH (par:PyCallable {signature: $sig})-[:PY_DECLARES]->(d:PyCallable) WHERE par._module IN $mods RETURN properties(d) AS p",
            sig=sig,
        ):
            ic = self._callable_full(r["p"])
            inner_callables[ic.name] = ic  # inner_callables keyed by short name
        inner_classes: Dict[str, PyClass] = {}
        for r in self._children(
            "callable_inner_classes",
            sig,
            "MATCH (par:PyCallable {signature: $sig})-[:PY_DECLARES]->(d:PyClass) WHERE par._module IN $mods RETURN properties(d) AS p",
            sig=sig,
        ):
            ic2 = self._class_full(r["p"])
            inner_classes[ic2.signature] = ic2  # inner_classes keyed by signature
        local_variables = [
            R.variable(r["p"])
            for r in self._children(
                "callable_variables",
                sig,
                "MATCH (par:PyCallable {signature: $sig})-[:PY_DECLARES_VAR]->(v:PyVariable) "
                "WHERE par._module IN $mods RETURN properties(v) AS p ORDER BY v.start_line, v.name",
                sig=sig,
            )
        ]
        return R.callable_(props, call_sites=call_sites, inner_callables=inner_callables, inner_classes=inner_classes, local_variables=local_variables)

    def _class_full(self, props: Dict[str, Any]) -> PyClass:
        """Rebuild a full :class:`PyClass` (methods, attributes, inner classes)."""
        sig = props["signature"]
        methods: Dict[str, PyCallable] = {}
        for r in self._children(
            "class_methods",
            sig,
            "MATCH (par:PyClass {signature: $sig})-[:PY_HAS_METHOD]->(m:PyCallable) WHERE par._module IN $mods RETURN properties(m) AS p",
            sig=sig,
        ):
            m = self._callable_full(r["p"])
            methods[m.name] = m  # methods keyed by short name
        attributes: Dict[str, PyClassAttribute] = {}
        for r in self._children(
            "class_attributes",
            sig,
            "MATCH (par:PyClass {signature: $sig})-[:PY_HAS_ATTRIBUTE]->(a:PyAttribute) WHERE par._module IN $mods RETURN properties(a) AS p",
            sig=sig,
        ):
            a = R.attribute(r["p"])
            attributes[a.name] = a  # attributes keyed by name
        inner_classes: Dict[str, PyClass] = {}
        for r in self._children(
            "class_inner_classes",
            sig,
            "MATCH (par:PyClass {signature: $sig})-[:PY_DECLARES]->(ic:PyClass) WHERE par._module IN $mods RETURN properties(ic) AS p",
            sig=sig,
        ):
            ic = self._class_full(r["p"])
            inner_classes[ic.signature] = ic  # inner_classes keyed by signature
        return R.class_(props, methods=methods, attributes=attributes, inner_classes=inner_classes)

    def _module_full(self, props: Dict[str, Any]) -> PyModule:
        """Rebuild a full :class:`PyModule` (top-level classes, functions, variables, imports)."""
        file_key = props["file_key"]
        classes: Dict[str, PyClass] = {}
        for r in self._children(
            "module_classes",
            file_key,
            "MATCH (par:PyModule {file_key: $fk})-[:PY_DECLARES]->(c:PyClass) WHERE par.file_key IN $mods RETURN properties(c) AS p",
            fk=file_key,
        ):
            c = self._class_full(r["p"])
            classes[c.signature] = c  # module.types keyed by signature
        functions: Dict[str, PyCallable] = {}
        for r in self._children(
            "module_functions",
            file_key,
            "MATCH (par:PyModule {file_key: $fk})-[:PY_DECLARES]->(f:PyCallable) WHERE par.file_key IN $mods RETURN properties(f) AS p",
            fk=file_key,
        ):
            fn = self._callable_full(r["p"])
            functions[fn.name] = fn  # module.functions keyed by short name
        variables = [
            R.variable(r["p"])
            for r in self._children(
                "module_variables",
                file_key,
                "MATCH (par:PyModule {file_key: $fk})-[:PY_DECLARES_VAR]->(v:PyVariable) "
                "WHERE par.file_key IN $mods RETURN properties(v) AS p ORDER BY v.start_line, v.name",
                fk=file_key,
            )
        ]
        imports = self._module_imports(file_key)
        return R.module(props, file_key=file_key, classes=classes, functions=functions, variables=variables, imports=imports)

    def _module_imports(self, file_key: str) -> List[Any]:
        """Best-effort :class:`PyImport` list from the aggregated ``PY_IMPORTS`` edges."""
        out: List[Any] = []
        for r in self._children(
            "module_imports",
            file_key,
            "MATCH (par:PyModule {file_key: $fk})-[e:PY_IMPORTS]->(pkg:PyPackage) "
            "WHERE par.file_key IN $mods RETURN pkg.name AS module, e.imported_names AS names",
            fk=file_key,
        ):
            names = r.get("names") or []
            if names:
                out.extend(R.import_(r["module"], n) for n in names)
            else:
                out.append(R.import_(r["module"], r["module"]))
        return out

    def _call_rows(self) -> List[Dict[str, Any]]:
        """Raw ``PY_CALLS`` edge rows scoped to this application (by source module).

        ``PY_CALLS`` only ever connects declared callables and external-symbol ghosts (see
        ``codeanalyzer.neo4j.schema.REL_TYPES``), so matching either label directly is both
        sufficient and cheaper than matching on the shared secondary label 1.4.0 also stamps these
        nodes with (a declared symbol is id-keyed there, not signature-keyed, so this Cypher
        doesn't depend on it at all).

        ``t`` may land on a ``:PyExternal`` ghost (a call to a builtin or a library member), and
        ``:PyExternal`` carries no ``signature`` property at all -- only ``id``/``name``/``module``.
        ``coalesce(t.signature, t.id)`` resolves it to its addressable ``@external`` can-id instead
        of projecting ``None``, same idiom ``get_callsites_for`` already uses for the identical
        situation one screen below. ``s`` is never external here: ``:PyExternal`` carries no
        ``_module`` property, so ``s._module IN $mods`` is already false for it -- a call
        *originating* at an external ghost exists in the raw graph (5,307 edges on the live Odoo
        graph) but is filtered out by this scoping before it ever reaches the RETURN, so ``s``
        needs no coalesce.
        """
        return self._run(
            "MATCH (s:PyCallable|PyExternal)-[r:PY_CALLS]->(t:PyCallable|PyExternal) WHERE s._module IN $mods "
            "RETURN s.signature AS src, coalesce(t.signature, t.id) AS tgt, properties(r) AS p",
            mods=self._modules,
        )

    def _bounded_call_rows(self, roots: List[str], depth: int | None) -> List[Dict[str, Any]]:
        """``PY_CALLS`` rows for the sub-graph reachable from ``roots``, within ``depth`` hops.

        Pushed into Cypher rather than filtered out of a full fetch in Python: this application's
        graph has 364,752 call edges, which is not an answer to a question about one function, and
        materialising all of them to keep a few hundred would make the keyword decorative.

        Two matches, deliberately. The first walks out from the roots and collects the **node set**
        reached; the second returns every ``PY_CALLS`` edge *among that set*. Collecting the
        relationships traversed by the first match would have been one match shorter and wrong in a
        specific way: it yields only the edges lying on a root-anchored path, so an edge between two
        nodes the caller can plainly see — a sibling calling back towards the root — would be
        missing, and ``graph.predecessors()`` would lie about a node in the graph it returned. The
        induced shape is what :func:`~cldk.analysis.python.backend.bounded_subgraph` gives on the
        local backend, so this is also what keeps the two backends answering identically.

        The second match is an ``OPTIONAL MATCH``, which is what puts a **reached but isolated**
        node in the result. Built from edge rows alone, the answer silently dropped every node with
        no outgoing call edge inside the reached set -- 5,302 of this graph's 19,549 call-graph
        nodes have out-degree 0, so a root that is itself a leaf came back as an *empty graph*,
        while the local backend's ``graph.subgraph(nodes)`` returned the one node it is. Empty and
        one-isolated-node are different answers to "what does this call?", and the null ``tgt`` row
        an ``OPTIONAL MATCH`` produces is how the reconstruction (:meth:`_build_call_graph`) tells
        them apart.

        The walk is **application-scoped at every hop**, which is why it is a quantified path
        pattern (Cypher 5.9+) rather than a ``*0..n`` variable-length one: a variable-length
        pattern can constrain only its endpoint, so the walk could step out through an external
        ghost. ``:PyExternal`` carries no ``_module``, so no per-hop module predicate can be
        expressed about it, and it has 5,307 outgoing ``PY_CALLS`` edges on this graph, 5,108 of
        them landing on another ghost. The leak is traversal **through** the ghost layer *inside*
        this one application — a two-hop budget spent walking ghost-to-ghost instead of through the
        application's own callables — not a hop into a neighbouring application: every ghost id
        embeds the application name (``can://python/odoo-slim-19/@external/IPython/start_ipython``),
        so a ghost is not in fact shared. Requiring ``a._module IN $mods`` of every hop's *source* makes
        the traversed edge set exactly :meth:`_call_rows`'s, so a ghost is still reached (it is a
        legitimate callee, and the local backend has it too) but is never traversed *through*, and
        the two backends agree node-for-node and edge-for-edge. The node labels repeat
        :meth:`_call_rows`'s for the same reason: ``PY_CALLS`` also lands on 51 ``:PyClass`` nodes
        on this graph, which the unscoped call graph does not contain.

        (Expressing the same constraint as ``all(x IN nodes(p) ...)`` over a bound path is correct
        and unusable: binding the path defeats Neo4j's pruning expansion, and an unbounded walk
        that answers in 0.2s as written did not finish in three minutes that way.)

        ``depth`` is interpolated into the pattern because Cypher does not accept a parameter as a
        quantifier bound. It is an ``int`` validated by
        :func:`~cldk.analysis.python.backend.call_graph_scope` and re-coerced here, so nothing
        caller-controlled reaches the statement as text; ``roots`` stays a parameter.

        The quantifier starts at ``0``, so a root with no outgoing calls still contributes itself.
        The first ``MATCH`` is therefore also what defines the **domain a root is validated
        against**: a ``:PyCallable`` this application declares (by signature) or a ``:PyExternal``
        ghost (by id) — *declaration*, not edge participation, which is why a callable in no
        ``PY_CALLS`` edge at all (444 of this graph's 15,549 in-scope callables) comes back as the
        one-node graph it is. :func:`~cldk.analysis.python.backend.bounded_subgraph` validates
        against that same union on the local backend, where it has to be passed in explicitly
        because the local call graph is built from edges alone. Anything outside the domain
        contributes no row at all, which :meth:`get_call_graph` turns into
        :class:`~cldk.utils.exceptions.SelectorNotInGraph` — see there.

        Raises:
            CodeanalyzerExecutionException: the attached server is older than Neo4j 5.9, which is
                where the quantified path pattern below arrives. Recorded at attach by
                :meth:`_probe_schema` and enforced here rather than there, so a caller who never
                asks for a bounded call graph keeps working against an older server — every other
                accessor on this backend runs on any 5.x.
        """
        if self._server_version is not None and self._server_version < self._QUANTIFIED_PATH_MIN_SERVER:
            got = ".".join(str(n) for n in self._server_version)
            raise CodeanalyzerExecutionException(
                f"get_call_graph(roots=...) compiles to a quantified path pattern, which needs Neo4j server "
                f"5.9 or newer; the attached server reports {got}. Every other accessor on this backend runs "
                f"on any 5.x — only the bounded call graph needs the newer pattern."
            )
        hops = "" if depth is None else str(int(depth))
        return self._run(
            "MATCH (root:PyCallable|PyExternal) WHERE coalesce(root.signature, root.id) IN $roots "
            "AND (root._module IS NULL OR root._module IN $mods) "
            f"MATCH (root) ((a:PyCallable|PyExternal)-[:PY_CALLS]->(b:PyCallable|PyExternal) WHERE a._module IN $mods){{0,{hops}}} (n) "
            "WITH collect(DISTINCT n) AS ns "
            "UNWIND ns AS s "
            "OPTIONAL MATCH (s)-[r:PY_CALLS]->(t) WHERE t IN ns AND s._module IN $mods "
            "RETURN coalesce(s.signature, s.id) AS src, coalesce(t.signature, t.id) AS tgt, properties(r) AS p",
            roots=list(roots),
            mods=self._modules,
        )

    # =====================================================================================
    # PythonAnalysisBackend — application / whole-program
    # =====================================================================================
    def get_application_view(self) -> PyApplication:
        return PyApplication(symbol_table=self.get_symbol_table(), call_graph=self._call_edges())

    def get_symbol_table(self, *, paths: Sequence[str] | None = None) -> Dict[str, PyModule]:
        # ``paths`` narrows in Cypher (the WHERE below) *and* narrows the prefetch scope, which is
        # where the saving actually is: without the second the eleven bulk statements would still
        # drag the whole application's children back to describe one module.
        keys = self._resolve_paths(paths)
        result: Dict[str, PyModule] = {}
        with self._bulk(keys):  # every module's children in eleven queries, not 45 per module
            for r in self._run(
                "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule) "
                "WHERE $paths IS NULL OR m.file_key IN $paths RETURN properties(m) AS p",
                app=self.application_name,
                paths=keys,
            ):
                mod = self._module_full(r["p"])
                result[mod.file_path] = mod  # symbol_table keyed by file_path (== file_key)
        return result

    def _resolve_paths(self, paths: Sequence[str] | None, kind: str = "paths") -> List[str] | None:
        """Requested module paths as graph ``file_key``s — ``None`` passes through as "everything".

        Delegates to :func:`~cldk.analysis.python.backend.scope_paths`, which is also what the
        local backend calls, so the lenient resolution (an absolute path or one with native
        separators finds its module) and the strictness (a path naming no module raises
        :class:`~cldk.utils.exceptions.SelectorNotInGraph` rather than quietly selecting no rows)
        are the same on both.
        """
        return scope_paths(paths, self._modules, kind)

    def get_modules(self) -> List[PyModule]:
        return list(self.get_symbol_table().values())

    def get_python_module(self, file_path: str) -> PyModule | None:
        rows = self._run(
            "MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule {file_key: $fk}) RETURN properties(m) AS p LIMIT 1",
            app=self.application_name,
            fk=str(file_path),
        )
        return self._module_full(rows[0]["p"]) if rows else None

    def get_python_file(self, qualified_class_name: str) -> str | None:
        # Only top-level classes are in the in-memory _class_to_file map (module.types).
        rows = self._run(
            "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass {signature: $sig}) WHERE c._module IN $mods RETURN c._module AS fk LIMIT 1",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return rows[0]["fk"] if rows else None

    # =====================================================================================
    # call graph
    # =====================================================================================
    def _call_edges(self) -> List[PyCallEdge]:
        """The application's call edges as ``PyCallEdge`` records (``PyApplication.call_graph``).

        ``PyCallEdge`` itself is a v2 model (fields ``src``/``dst``/``weight``/``prov`` — 0.3.x's
        ``source``/``target``/``provenance`` don't exist on it), and the Cypher property carrying
        provenance on the graph is ``prov``, not ``provenance``. Same vocabulary migration as the
        label/relationship rename above, just one level down (model kwargs / property keys).
        """
        return [
            PyCallEdge(
                src=r["src"],
                dst=r["tgt"],
                weight=r["p"].get("weight", 1),
                prov=list(r["p"].get("prov", []) or []),
            )
            for r in self._call_rows()
        ]

    def _build_call_graph(self, rows: List[Dict[str, Any]] | None = None) -> nx.DiGraph:
        # A null ``tgt`` is a node the bounded walk reached that has no outgoing call edge inside
        # the reached set (see _bounded_call_rows). It is a node, not an edge, and dropping it is
        # what made a leaf root return an empty graph. _call_rows() never produces one.
        graph = nx.DiGraph()
        for r in self._call_rows() if rows is None else rows:
            if r["tgt"] is None:
                graph.add_node(r["src"])
                continue
            p = r["p"]
            graph.add_edge(r["src"], r["tgt"], type="CALL_DEP", weight=p.get("weight", 1), provenance=tuple(p.get("prov", []) or []))
        return graph

    def get_call_graph(self, *, roots: Sequence[str] | None = None, depth: int | None = None) -> nx.DiGraph:
        # Only the unscoped graph is cached: it is the one every other accessor here reuses
        # (get_all_callers / get_all_callees / get_class_call_graph), and caching a scoped result
        # under the same attribute would hand the next unscoped caller a subgraph.
        scope = call_graph_scope(roots, depth)
        if scope is not None:
            graph = self._build_call_graph(self._bounded_call_rows(scope, depth))
            # Every root the domain holds contributes at least its own row (the quantifier starts
            # at 0), so absence from the result is exactly absence from the domain — the same check
            # bounded_subgraph() makes locally, through the same function, against the same set.
            # Not free: the miss is only visible *after* the traversal, so a caller who mistypes
            # one of two roots pays for the surviving root's whole walk first. Measured on the odoo
            # graph: 5.11s for an unbounded walk out of its busiest callable plus one typo, against
            # 0.02s when every root misses and there is nothing left to expand.
            check_selector("roots", scope, [r for r in scope if r not in graph])
            return graph
        if self._call_graph is None:
            self._call_graph = self._build_call_graph()
        return self._call_graph

    def get_call_graph_json(self) -> str:
        return model_dump_json(self.get_application_view(), indent=None)

    def get_all_callers(self, target_class_name: str, target_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(target_class_name, target_method_declaration)
        if method is None or method.signature not in graph:
            return {"caller_details": []}
        callers = [{"caller_signature": src, "edge": graph.get_edge_data(src, method.signature)} for src in graph.predecessors(method.signature)]
        return {"target_method": method.signature, "caller_details": callers}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str) -> Dict:
        graph = self.get_call_graph()
        method = self.get_method(source_class_name, source_method_declaration)
        if method is None or method.signature not in graph:
            return {"callee_details": []}
        callees = [{"callee_signature": tgt, "edge": graph.get_edge_data(method.signature, tgt)} for tgt in graph.successors(method.signature)]
        return {"source_method": method.signature, "callee_details": callees}

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        graph = self.get_call_graph()
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return []
        if method_signature is not None:
            method = self.get_method(qualified_class_name, method_signature)
            if method is None:
                return []
            return list(nx.edge_dfs(graph, source=method.signature))
        edges: List[Tuple[str, str]] = []
        for method in cls.callables.values():
            if method.signature in graph:
                edges.extend(nx.edge_dfs(graph, source=method.signature))
        return edges

    # =====================================================================================
    # classes
    # =====================================================================================
    def get_all_classes(self, *, module: str | None = None) -> Dict[str, PyClass]:
        # The statement was already scoped by ``$mods``, so narrowing to one module is just a
        # shorter list -- and the same list narrows the prefetch, so the seven bulk statements
        # fetch one module's members instead of the application's.
        scope = self._resolve_paths(None if module is None else [module], kind="module") or self._modules
        result: Dict[str, PyClass] = {}
        with self._bulk(scope):  # every class's members in seven queries, not one per child collection
            for r in self._run(
                "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass) WHERE c._module IN $mods RETURN properties(c) AS p",
                mods=scope,
            ):
                c = self._class_full(r["p"])
                result[c.signature] = c
        return result

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        # Top-level classes only, matching get_all_classes().get(...) on the in-memory backend.
        rows = self._run(
            "MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass {signature: $sig}) WHERE c._module IN $mods RETURN properties(c) AS p LIMIT 1",
            sig=qualified_class_name,
            mods=self._modules,
        )
        return self._class_full(rows[0]["p"]) if rows else None

    def get_all_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        cls = self.get_class(qualified_class_name)
        return list(cls.types.values()) if cls else []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        cls = self.get_class(qualified_class_name)
        if cls is None:
            return {}
        short_name = cls.name
        result: Dict[str, PyClass] = {}
        for sig, candidate in self.get_all_classes().items():
            if sig == qualified_class_name:
                continue
            for base in candidate.base_classes:
                if base == short_name or base == qualified_class_name or base.endswith("." + short_name):
                    result[sig] = candidate
                    break
        return result

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        cls = self.get_class(qualified_class_name)
        return list(cls.base_classes) if cls else []

    # =====================================================================================
    # methods / fields
    # =====================================================================================
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, PyCallable]]:
        result: Dict[str, Dict[str, PyCallable]] = {}
        for module in self.get_symbol_table().values():
            for class_sig, cls in module.types.items():
                result[class_sig] = dict(cls.callables)
            if module.functions:
                result.setdefault(module.module_name, {}).update(module.functions)
        return result

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """The methods of a class (see :meth:`~cldk.analysis.commons.backend.AnalysisBackend.get_all_methods_in_class`).

        Every returned ``PyCallable``'s call sites have ``callee_signature=None`` — this
        reconstruction never follows ``PY_RESOLVES_TO`` (see :meth:`_callable_full`). Use
        :meth:`get_callsites_for` for the identical call sites with resolved signatures.
        """
        cls = self.get_class(qualified_class_name)
        return dict(cls.callables) if cls else {}

    def _get_module_functions(self, module_name: str) -> Dict[str, PyCallable]:
        """Fetch a module's top-level functions by ``module_name`` (not ``file_key``) — the scope
        key ``get_method`` accepts for module-level lookups, mirroring
        ``get_all_methods_in_application``'s module outer key. A single scoped query, so it stays
        as cheap as the class path instead of paying the whole-symbol-table fan-out.
        """
        rows = self._run(
            "MATCH (m:PyModule {module_name: $name})-[:PY_DECLARES]->(f:PyCallable) "
            "WHERE m.file_key IN $mods RETURN properties(f) AS p",
            name=module_name,
            mods=self._modules,
        )
        return {fn.name: fn for fn in (self._callable_full(r["p"]) for r in rows)}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> PyCallable | None:
        """Return a specific method or module-level function by scope and name (see
        :meth:`PythonAnalysisBackend.get_method`).

        ``qualified_class_name`` resolves as a class signature first; if no such class exists it
        is treated as a module name and resolved against that module's top-level functions.

        The returned ``PyCallable``'s call sites have ``callee_signature=None`` — this
        reconstruction never follows ``PY_RESOLVES_TO`` (see :meth:`_callable_full`). Use
        :meth:`get_callsites_for` for the identical call sites with resolved signatures.
        """
        cls = self.get_class(qualified_class_name)
        methods = dict(cls.callables) if cls is not None else self._get_module_functions(qualified_class_name)
        if qualified_method_name in methods:
            return methods[qualified_method_name]
        for sig, callable_ in methods.items():
            if callable_.name == qualified_method_name:
                return callable_
        return None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        method = self.get_method(qualified_class_name, qualified_method_name)
        return [p.name for p in method.parameters] if method else []

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """The constructors of a class (see :meth:`~cldk.analysis.python.backend.PythonAnalysisBackend.get_all_constructors`).

        Routed through :meth:`get_all_methods_in_class`, so the same caveat applies: call sites
        have ``callee_signature=None`` here. Use :meth:`get_callsites_for` for resolved signatures.
        """
        return {sig: c for sig, c in self.get_all_methods_in_class(qualified_class_name).items() if c.name == "__init__"}

    def get_all_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    # =====================================================================================
    # PythonAnalysisBackend — bulk / projected accessors (one round-trip each)
    # =====================================================================================
    # Field-projected RETURNs that sidestep the per-entity reconstruction fan-out: each is a single
    # Cypher statement, not the N+1 walk get_symbol_table()/get_all_methods_in_application() pays.
    #
    # ``path`` comes from ``c._module``, not ``c.path``: the latter is the absolute path on the
    # machine that ran the analysis (``/Users/…/checkout/addons/…``), which joins to nothing a
    # caller holds -- not ``locate().module.path``, not ``PyClassOverview.path`` (already
    # ``cl._module``), not ``get_symbol_table()``'s keys, and not any path on another host.
    # ``_module`` is the repo-relative module key, i.e. the one vocabulary the whole facade speaks.
    _OVERVIEW_PROJECTION = (
        "OPTIONAL MATCH (owner:PyClass)-[:PY_HAS_METHOD]->(c) "
        "RETURN c.signature AS signature, c.name AS name, c.decorators AS decorators, "
        "c._module AS path, c.start_line AS start_line, c.end_line AS end_line, "
        "owner.signature AS class_signature"
    )

    def get_callables_overview(self) -> List[PyCallableOverview]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods " + self._OVERVIEW_PROJECTION,
            mods=self._modules,
        )
        return [R.overview(r) for r in rows]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.signature IN $sigs AND c.code IS NOT NULL "
            "RETURN c.signature AS signature, c.code AS code",
            mods=self._modules,
            sigs=list(signatures),
        )
        return {r["signature"]: r["code"] for r in rows}

    def get_source(self, node_id: str) -> str:
        """Return the source text named by ``node_id`` (see
        :meth:`PythonAnalysisBackend.get_source`).

        Only a callable-granularity ``node_id`` (no ``"@"``) is answerable here: ``:PyCallable.code``
        is a real, precomputed property. It is matched against **both** of a callable's names — its
        ``signature`` and its ``can://`` ``id`` — exactly as the local backend does, so the opaque
        ``ref`` a ``SliceNode`` carries round-trips through ``get_source`` on either backend rather
        than only on the one that happens to hold the sources. A body-node id names something the graph structurally
        cannot supply text for — no per-statement ``code`` property exists, and ``:PyModule`` has no
        source to slice one out of either — so that case raises rather than silently substituting
        the enclosing callable's (too much) text.
        """
        sig, sep, _ = node_id.partition("@")
        if sep:
            raise NotImplementedError(
                f"get_source({node_id!r}): the attached graph carries no source text below callable "
                "granularity -- :PyBodyNode has a line span and no code/text property, and :PyModule "
                "has no source to slice one out of. Only the local codeanalyzer backend can answer "
                "for a statement or call site."
            )
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND (c.signature = $sig OR c.id = $sig) AND c.code IS NOT NULL "
            "RETURN c.code AS code",
            mods=self._modules,
            sig=sig,
        )
        if not rows:
            raise KeyError(f"no callable with signature {sig!r} (or it has no code)")
        return rows[0]["code"]

    # -----[ addressing ]-----
    _RESOLVE_CALLABLE_QUERY = (
        "MATCH (c:PyCallable) WHERE c._module IN $mods AND (c.signature = $name OR c.signature ENDS WITH $dotted) "
        "OPTIONAL MATCH (owner:PyClass)-[:PY_HAS_METHOD]->(c) "
        "RETURN c.signature AS signature, c.name AS name, c.id AS id, c._module AS path, "
        "c.start_line AS start_line, owner.signature AS class_signature"
    )

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name against the graph (see :meth:`PythonAnalysisBackend.resolve_callable`).

        The ``WHERE`` clause is the resolver's own predicate, not a coarse pre-filter that happens
        to be close to it: ``segment_match`` is *exactly* "equal, or ends with the separator plus
        the name", so ``c.signature = $name OR c.signature ENDS WITH $dotted`` keeps precisely the
        rows :func:`~cldk.analysis.commons.resolve.resolve_callable_signature` would keep. Pushing
        it into Cypher is therefore a narrowing of the *round trip*, not of the *domain* — the
        candidate set this resolves over is the same one the local backend, which filters an
        in-memory list, resolves over. Two backends agreeing on a predicate while running it
        against different sets is the defect this construction avoids; running the same predicate
        twice (once in Cypher, once in the shared policy) is the cheap price of avoiding it.
        """
        rows = self._run(self._RESOLVE_CALLABLE_QUERY, mods=self._modules, name=name, dotted="." + name)
        # Two callables sharing a signature would collapse into one entry and resolve arbitrarily;
        # recorded and raised on only if the name lands on one, so an unrelated duplicate cannot
        # break every resolution. Not reachable on a real application (15,549 distinct signatures).
        by_sig: dict = {}
        collisions: set[str] = set()
        for r in rows:
            if r["signature"] in by_sig:
                collisions.add(r["signature"])
            by_sig[r["signature"]] = r
        candidates = [CallableCandidate(r["signature"], r["class_signature"], r["path"]) for r in by_sig.values()]
        sig = resolve_callable_signature(name, candidates, in_class=in_class, in_module=in_module)
        if sig in collisions:
            raise ValueError(f"{sig!r} is carried by more than one analysed callable; neither can be addressed unambiguously")
        row = by_sig[sig]
        return SliceNode(
            file=row["path"],
            line=row["start_line"],
            callable=row["signature"],
            kind="callable",
            name=row["name"],
            source=None,
            ref=row["id"],
        )

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable (see :meth:`PythonAnalysisBackend.resolve_value`).

        Two round trips, not one: the callable is resolved first, because an ambiguous ``within``
        must raise naming *callables*, not fail obscurely on a value search over a set of them —
        through :func:`~cldk.analysis.commons.resolve.resolve_within`, so the advice it gives names
        a keyword ``resolve_value`` actually accepts.

        ``b.var`` is the analyzer's vocabulary, not the caller's:
        :func:`~cldk.analysis.commons.resolve.value_candidate` translates it, the same function the
        local backend uses, so the two cannot label the same vertex differently.
        """
        owner = resolve_within(self.resolve_callable, within)
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.signature = $sig "
            "MATCH (c)-[:PY_HAS_BODY_NODE]->(b:PyBodyNode) WHERE b.kind = 'formal_in' "
            "RETURN b.var AS var, b.id AS id",
            mods=self._modules,
            sig=owner.callable,
        )
        # A list, not a dict keyed by name: two values resolving to the same name are a genuine
        # ambiguity the policy must see, and a dict would silently keep the last row.
        entries = [(r["id"], value_candidate(r["var"])) for r in rows if r["var"]]
        chosen = resolve_value_name(name, [v.name for _, v in entries], within=owner.callable)
        node_id, value = next((i, v) for i, v in entries if v.name == chosen)
        return SliceNode(
            file=owner.file,
            line=owner.line,
            callable=owner.callable,
            kind=value.kind,
            name=value.leaf,
            defined_in=value.defined_in,
            source=None,
            ref=node_id,
        )

    def get_decorated_callables(self, markers: List[str]) -> List[PyCallableOverview]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods "
            "AND any(d IN c.decorators WHERE d IN $markers) " + self._OVERVIEW_PROJECTION,
            mods=self._modules,
            markers=list(markers),
        )
        return [R.overview(r) for r in rows]

    def get_entrypoints(self) -> List[PyCallableOverview]:
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.is_entrypoint = true " + self._OVERVIEW_PROJECTION,
            mods=self._modules,
        )
        return [R.overview(r) for r in rows]

    def get_entrypoint_classes(self) -> List[PyClassOverview]:
        rows = self._run(
            "MATCH (cl:PyClass) WHERE cl._module IN $mods AND cl.is_entrypoint = true "
            "RETURN cl.signature AS signature, cl.name AS name, cl.decorators AS decorators, "
            "cl._module AS path, cl.start_line AS start_line, cl.end_line AS end_line",
            mods=self._modules,
        )
        return [R.class_overview(r) for r in rows]

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        # codeanalyzer-python's neo4j/project.py projects only the derived is_entrypoint /
        # entrypoint_frameworks properties onto :PyCallable/:PyClass -- it never projects
        # PyApplication.entrypoint_report (frameworks_detected/rulesets/unresolved/errors) onto the
        # graph at all (confirmed: no such property or node anywhere in neo4j/project.py). Say so
        # rather than fabricate empty-but-clean-looking coverage fields -- same precedent as
        # LocateResult's module_source_unavailable for the module-text gap.
        return EntrypointCoverage(
            diagnostics=[
                Diagnostic(
                    code="entrypoint_report_unavailable",
                    message=(
                        "The Neo4j projection does not carry PyApplication.entrypoint_report "
                        "(frameworks_detected/rulesets/unresolved/errors) -- only the derived "
                        "is_entrypoint/entrypoint_frameworks properties are projected onto "
                        ":PyCallable/:PyClass nodes. Use the local codeanalyzer backend for "
                        "entrypoint-pass coverage."
                    ),
                )
            ]
        )

    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[PyCallsite]]:
        # OPTIONAL MATCH so a requested callable with no call sites still yields a row (p is null),
        # giving it an empty-list entry — parity with the in-process backend, which keys every
        # existing signature. ORDER mirrors _callable_full's call-site ordering. The second
        # OPTIONAL MATCH follows PY_RESOLVES_TO to the call's resolved target: a declared
        # :PyCallable (carries `signature`) or a :PyExternal ghost (no `signature`, only
        # `id`/`name`/`module`) -- coalesce picks whichever property the target actually has, so
        # an external target resolves to its addressable @external can-id (see
        # PythonAnalysisBackend.get_callsites_for). Absent (null) when the call is genuinely
        # unresolved, or when the graph was populated at an analysis level below the one where the
        # defuse-linker backfill runs (see that same docstring's caveat).
        rows = self._run(
            "MATCH (c:PyCallable) WHERE c._module IN $mods AND c.signature IN $sigs "
            "OPTIONAL MATCH (c)-[:PY_HAS_BODY_NODE]->(s:PyBodyNode {kind: 'call'}) "
            "OPTIONAL MATCH (s)-[:PY_RESOLVES_TO]->(t) "
            "RETURN c.signature AS owner, properties(s) AS p, coalesce(t.signature, t.id) AS callee "
            "ORDER BY s.start_line",
            mods=self._modules,
            sigs=list(signatures),
        )
        out: Dict[str, List[PyCallsite]] = {}
        for r in rows:
            sites = out.setdefault(r["owner"], [])
            if r["p"] is not None:
                sites.append(R.callsite(r["p"], callee_signature=r["callee"]))
        return out

    def get_external_symbols(self) -> Dict[str, PyExternalSymbol]:
        # :PyExternal carries no `_module` property (it isn't owned by one module -- see this
        # file's module docstring on MODULE_OWNED_LABELS), so it can't be scoped the way every
        # other query here is. Its id embeds this application's own can:// id by construction
        # (`<app-id>/@external/<module>/<name>`), which is app-scoping enough on its own -- a
        # second application in the same database mints a disjoint id prefix.
        prefix = f"{application_id(self.application_name)}/@external/"
        rows = self._run(
            "MATCH (e:PyExternal) WHERE e.id STARTS WITH $prefix RETURN properties(e) AS p",
            prefix=prefix,
        )
        return {r["p"]["id"]: R.external_symbol(r["p"]) for r in rows}

    # =====================================================================================
    # PythonAnalysisBackend — repository artifacts (Artifact/ConfigKey/Package, unprefixed —
    # see cldk/analysis/commons/backend.py's module docstring)
    # =====================================================================================
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        result: Dict[str, PyArtifact] = {}
        for r in self._run(
            "MATCH (:PyApplication {name: $app})-[:HAS_ARTIFACT]->(a:Artifact) "
            "OPTIONAL MATCH (a)-[:DEFINES_CONFIG]->(ck:ConfigKey) "
            "RETURN properties(a) AS p, collect(properties(ck)) AS cks",
            app=self.application_name,
        ):
            art = R.artifact(r["p"], config_keys=[R.config_key(p) for p in r["cks"]])
            result[art.path] = art
        return result

    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        conditions: list[str] = []
        params: Dict[str, Any] = {"app": self.application_name}
        if direct_only:
            conditions.append("r.direct = true")
        if ecosystem is not None:
            conditions.append("p.ecosystem = $ecosystem")
            params["ecosystem"] = ecosystem
        if declared_in is not None:
            conditions.append("a.id = $declared_in")
            params["declared_in"] = declared_in
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (
            "MATCH (:PyApplication {name: $app})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(p:Package)"
            + where
            + " RETURN properties(r) AS rel, p.name AS name, p.ecosystem AS ecosystem, a.id AS declared_in"
        )
        return [
            R.dependency(r["rel"], name=r["name"], ecosystem=r["ecosystem"], declared_in=r["declared_in"])
            for r in self._run(query, **params)
        ]

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        result: Dict[str, PyConfigKey] = {}
        for r in self._run(
            "MATCH (:PyApplication {name: $app})-[:HAS_ARTIFACT]->(:Artifact)-[:DEFINES_CONFIG]->(ck:ConfigKey) "
            "RETURN properties(ck) AS p",
            app=self.application_name,
        ):
            ck = R.config_key(r["p"])
            result[ck.id] = ck
        return result

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        # PY_USES_CONFIG (the one prefixed edge in this layer, per the leg-1 brief) connects
        # (:PyBodyNode)-->(:ConfigKey) directly, so its endpoints ARE src/dst -- no reconstruction
        # helper needed, unlike artifact()/dependency()/config_key() above. Scoped like every other
        # body-node query in this file (`bn._module IN $mods`), not via the Artifact/ConfigKey path,
        # since a config key can be read from a module outside this application's declared modules
        # only if it were mis-scoped -- $mods is the same guard get_method_bodies/_call_rows use.
        query = "MATCH (bn:PyBodyNode)-[u:PY_USES_CONFIG]->(ck:ConfigKey) WHERE bn._module IN $mods"
        params: Dict[str, Any] = {"mods": self._modules}
        if key is not None:
            query += " AND ck.key = $key"
            params["key"] = key
        query += " RETURN bn.id AS src, ck.id AS dst, u.prov AS prov"
        return [PyConfigUseEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or [])) for r in self._run(query, **params)]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        # PY_READS_CONFIG_UNRESOLVED (app.config_reads_unresolved) DOES reach the graph -- verified
        # against codeanalyzer/neo4j/project.py's _project_config_uses -- but two things are lossy
        # here relative to the local backend's list, both documented rather than silently eaten:
        # (1) PyConfigRead.site (the reading call's own body-node id) is not an edge property at
        #     all; the edge runs (:PyApplication)-[:PY_READS_CONFIG_UNRESOLVED]->(:PyExternal ghost)
        #     with key/reason/prov as edge properties, no site. `site` comes back "" here.
        # (2) the edge's own discriminant is `_k=(key, reason)` (per project.py's comment: "does
        #     not per-site discriminate the non-literal bucket"), so several distinct call sites
        #     reading the same (callee, key, reason) collapse into ONE edge under MERGE -- a count
        #     mismatch against the local backend's one-entry-per-occurrence list is expected, not a
        #     bug. Presence/absence still agrees: any unresolved read for a (callee, key, reason)
        #     triple guarantees at least one edge, so "no rows" here still means "no unresolved
        #     reads," never a false negative -- unlike finding 1's entrypoint_report, which the
        #     graph doesn't carry at all.
        rows = self._run(
            "MATCH (:PyApplication {name: $app})-[u:PY_READS_CONFIG_UNRESOLVED]->(ghost:PyExternal) "
            "RETURN properties(u) AS p, ghost.id AS callee",
            app=self.application_name,
        )
        return [R.unresolved_config_read(r["p"], callee=r["callee"]) for r in rows]

    def get_config_readers(self, key: str) -> List[PyCallableOverview]:
        # PyConfigUseEdge.src IS the reading call's :PyBodyNode.id (see get_config_uses's own
        # comment), and _project_program_graphs adds PY_HAS_BODY_NODE from a callable to every
        # PyBodyNode it creates in the same loop iteration that creates the node -- so any
        # PyBodyNode a PY_USES_CONFIG edge points at is guaranteed to already have that edge to its
        # owner. DISTINCT because one callable can read the same key at several call sites.
        rows = self._run(
            "MATCH (bn:PyBodyNode)-[:PY_USES_CONFIG]->(ck:ConfigKey) WHERE bn._module IN $mods AND ck.key = $key "
            "MATCH (reader:PyCallable)-[:PY_HAS_BODY_NODE]->(bn) "
            "OPTIONAL MATCH (cls:PyClass)-[:PY_HAS_METHOD]->(reader) "
            "RETURN DISTINCT reader.signature AS signature, reader.name AS name, reader.decorators AS decorators, "
            "reader._module AS path, reader.start_line AS start_line, reader.end_line AS end_line, "
            "cls.signature AS class_signature",
            mods=self._modules,
            key=key,
        )
        return [R.overview(r) for r in rows]

    # =====================================================================================
    # locate / locate_many — one round trip, UNWIND over the position list
    # =====================================================================================
    # Two layers of containment, both in the same statement so the whole resolution is still one
    # round trip:
    #
    # * the **callable** comes from PyCallable's own start_line/end_line (present at every analysis
    #   level, unlike PyBodyNode, which only exists from L1 up) — the smallest line span containing
    #   the position is the innermost callable, which naturally treats a gap between two callables
    #   (or a module top-level line) the same way: no callable matches, so it falls through to
    #   module_scope rather than snapping to a neighbour. PY_HAS_METHOD is walked reversed for the
    #   owning class (``type``);
    # * the **body node** comes from PY_HAS_BODY_NODE off that same candidate callable, again by
    #   line containment, innermost first. Synthetic vertices (@entry / @exit / @formal_in:N) carry
    #   no span, so the emitter prunes their start_line/end_line away entirely — the
    #   ``IS NOT NULL`` guard is what stops a span-less vertex being read as "contains everything".
    #
    # Every other query in this file is scoped to the application with ``_module IN $mods``, and so
    # is this one: a database may hold several applications, and a same-valued ``file_key`` from a
    # different application would otherwise win the ``{_module: pos.path}`` match.
    _LOCATE_QUERY = (
        "UNWIND $positions AS pos "
        "OPTIONAL MATCH (:PyApplication {name: $app})-[:PY_HAS_MODULE]->(m:PyModule {file_key: pos.path}) "
        "WITH pos, m "
        "OPTIONAL MATCH (c:PyCallable {_module: pos.path}) "
        "WHERE c._module IN $mods "
        "AND c.start_line IS NOT NULL AND c.end_line IS NOT NULL "
        "AND c.start_line <= pos.line AND pos.line <= c.end_line "
        "WITH pos, m, c "
        "OPTIONAL MATCH (cls:PyClass)-[:PY_HAS_METHOD]->(c) "
        "WITH pos, m, c, cls "
        "OPTIONAL MATCH (c)-[:PY_HAS_BODY_NODE]->(b:PyBodyNode) "
        "WHERE b.start_line IS NOT NULL AND b.end_line IS NOT NULL "
        "AND b.start_line <= pos.line AND pos.line <= b.end_line "
        "RETURN pos.idx AS idx, properties(m) AS module_props, properties(c) AS callable_props, "
        "properties(cls) AS class_props, properties(b) AS body_props"
    )

    @staticmethod
    def _line_span(start_line: int, end_line: int) -> Span:
        """A :class:`Span` over the only positional data the graph carries: line numbers.

        ``codeanalyzer-python``'s projection writes ``start_line`` / ``end_line`` on ``:PyCallable``
        and ``:PyBodyNode`` and nothing finer — no columns, no UTF-8 byte offsets into the module
        source (see ``codeanalyzer/neo4j/project.py``'s ``_callable_props`` /
        ``_project_program_graphs``). The columns and ``bytes`` here are therefore ``0``
        placeholders, not offsets: they are documented as meaningless on this backend rather than
        dressed up as real (see :class:`~cldk.analysis.commons.results.LocateResult`).
        """
        return Span(start=(start_line, 0), end=(end_line, 0), bytes=(0, 0))

    def _locate_result(self, path: str, line: int, rows: List[Dict[str, Any]]) -> LocateResult:
        module_props = next((r["module_props"] for r in rows if r["module_props"] is not None), None)
        if module_props is None:
            return LocateResult(
                node=None,
                callable=None,
                type=None,
                module=ModuleRef(path=path),
                source="",
                span=self._line_span(line, line),
                diagnostics=[
                    Diagnostic(
                        code="file_not_in_graph",
                        message=(
                            f"{path} is not covered by any analysed module of application "
                            f"{self.application_name!r}. This backend reads an attached graph and has no "
                            f"access to the project sources, so it cannot tell a file that was never "
                            f"analysed from one that is not on disk."
                        ),
                    )
                ],
            )
        module_ref = ModuleRef(path=module_props.get("file_key", path), module_name=module_props.get("module_name"))
        # Innermost callable = smallest line span containing the position. Rows with a null
        # callable are the OPTIONAL MATCH misses; there is one row per (callable, body node) pair,
        # so the same callable can repeat. Equal widths (a lambda inside a one-line def) tie, and
        # `min` would then be decided by Cypher's row order — nondeterministic here and different
        # from the local walk's order. Break it on the longer signature, deeper first, exactly as
        # _find_innermost does: a nested callable's signature extends its owner's.
        best_row = min(
            (r for r in rows if r["callable_props"] is not None),
            key=lambda r: (
                r["callable_props"]["end_line"] - r["callable_props"]["start_line"],
                -len(r["callable_props"]["signature"]),
                r["callable_props"]["signature"],
            ),
            default=None,
        )
        if best_row is None:
            # Module scope is a real position, not an absence — but the graph genuinely does not
            # carry module text, so say so instead of returning something invented. Reading the
            # file from disk is not an option (this backend attaches to a graph someone else
            # built and may not have the project checked out), and concatenating the callables'
            # ``code`` would silently drop every module-level statement.
            return LocateResult(
                node=None,
                callable=None,
                type=None,
                module=module_ref,
                source="",
                span=self._line_span(line, line),
                diagnostics=[
                    Diagnostic(code="module_scope", message=f"line {line} is at module scope in {path}."),
                    Diagnostic(
                        code="module_source_unavailable",
                        message=(
                            "The attached graph does not carry module text: :PyModule nodes project "
                            "file_key/module_name/content_hash/last_modified/file_size and no source. "
                            "The local codeanalyzer backend returns the module's text for this position."
                        ),
                    ),
                ],
            )
        cprops, clsprops = best_row["callable_props"], best_row["class_props"]
        found_body = self._innermost_body_node(rows, cprops["signature"])
        node, node_id = (found_body[1], found_body[0]) if found_body else (None, None)
        return LocateResult(
            node=node,
            node_id=node_id,
            callable=CallableRef(signature=cprops["signature"], name=cprops["name"], class_signature=clsprops["signature"] if clsprops else None),
            type=TypeRef(signature=clsprops["signature"], name=clsprops["name"]) if clsprops else None,
            module=module_ref,
            source=cprops.get("code") or "",
            span=self._line_span(cprops["start_line"], cprops["end_line"]),
            diagnostics=[],
        )

    @staticmethod
    def _innermost_body_node(rows: List[Dict[str, Any]], signature: str) -> "Tuple[str, BodyNode] | None":
        """The tightest ``:PyBodyNode`` of ``signature`` the query matched, plus its graph ``id``,
        or ``None``. The id rides along because it *is* the node's address: it is read straight off
        the node rather than composed here (#320 — the SDK used to build ``"<signature>@<key>"``,
        which joined to nothing because the emitter mints ``"<callable can:// id>@<key>"``).

        ``None`` is a real outcome, not an error: a position on a callable's ``def`` line or on a
        blank line inside it is contained by the callable and by no body node, and the caller still
        gets the callable. Ties break on the trailing local key of the node's global ``id``
        (``<callable can:// id>@<body key>``) — the same key the local backend's ``body`` dict is
        keyed by, ranked the same way (deeper column first, see
        :func:`~cldk.analysis.python.backend.body_key_column`), so both backends resolve a tie to the
        same node.
        """
        matches = [r["body_props"] for r in rows if r["body_props"] is not None and r["callable_props"] is not None and r["callable_props"]["signature"] == signature]
        if not matches:
            return None

        def rank(b: Dict[str, Any]) -> Tuple[int, int, str]:
            key = str(b.get("id", "")).rsplit("@", 1)[-1]
            return (b["end_line"] - b["start_line"], -body_key_column(key), key)

        best = min(matches, key=rank)
        return (str(best["id"]), R.body_node(best))

    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable (see
        :meth:`PythonAnalysisBackend.locate`)."""
        return self.locate_many([(path, line)])[0]

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in **one** Cypher round trip (see
        :meth:`PythonAnalysisBackend.locate_many`) — the position list travels as a single
        parameter via ``UNWIND``, never a loop over :meth:`locate`. Results come back in input
        order regardless of the order Neo4j returns rows in."""
        positions = list(positions)
        if not positions:
            return []
        # Whatever the caller's scanner printed ("./src/app.py", an absolute path) is normalised to
        # the graph's file_key before it becomes a Cypher parameter — an unnormalised path would
        # match no :PyModule and read back as file_not_in_graph.
        keys = [resolve_module_key(path, self._modules) for path, _ in positions]
        rows = self._run(
            self._LOCATE_QUERY,
            app=self.application_name,
            mods=self._modules,
            positions=[{"idx": i, "path": key, "line": line} for i, (key, (_, line)) in enumerate(zip(keys, positions))],
        )
        by_idx: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_idx[r["idx"]].append(r)
        return [self._locate_result(key, line, by_idx.get(i, [])) for i, (key, (_, line)) in enumerate(zip(keys, positions))]
