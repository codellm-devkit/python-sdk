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

"""Neo4j-backed Java analysis backend (read-only Cypher client) on the codeanalyzer-java 3.0.1
graph vocabulary.

A drop-in alternative to :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: the same query
surface, answered over a live graph that ``codeanalyzer-java --emit neo4j`` populated out of band.
This class never writes and needs neither the analyzer JAR, a JDK, nor the project sources.

**The graph it reads** (``schema.neo4j.json`` at the 3.1.1 tag, contract ``2.0.0``, verified
against the reference graph): ``:JApplication`` is keyed by **``id``** (``can://<app>``; ``name``
survives as a display property with no uniqueness constraint) and stamps
``analyzer_version``; every project-owned node carries a ``can://<app>/java/…`` ``id`` and the
marker label ``:JCanNode``. ``:JModule`` holds the repo-relative path in ``file_key``;
``:JType``/``:JCallable``/``:JExternal`` share the merge label ``:JSymbol`` and are told apart by
their own label plus ``kind``; ``:JField``, ``:JVariable``, ``:JEnumConstant``,
``:JRecordComponent`` and ``:JBodyNode`` are keyed by ``id``. Containment is ``J_HAS_MODULE`` /
``J_DECLARES`` / ``J_HAS_METHOD`` / ``J_HAS_FIELD`` / ``J_DECLARES_VAR`` /
``J_HAS_ENUM_CONSTANT`` / ``J_HAS_RECORD_COMPONENT``; annotations are ``J_ANNOTATED_BY``; a call
site is a ``:JBodyNode {kind:'call'}`` under ``J_HAS_BODY_NODE`` resolving over ``J_RESOLVES_TO``;
calls are ``J_CALLS {weight, prov}``. **There is no ``_module`` property anywhere**, and none of the
pre-3.0.1 (schema v1) vocabulary this backend used to read (``:JCompilationUnit``, ``J_HAS_UNIT``,
``J_HAS_CALLABLE``, ``:JParameter``, ``:JCallSite``, ``:JComment``, the CRUD labels) exists — a
graph that still speaks it is refused at attach by :meth:`_probe_schema` (J-9).

**Scope.** The application scope is the single prefix ``can://<app>/`` that :func:`_scoped`
spells, or the ``:JApplication {id: $app_id}`` anchor a statement walks out from. Nothing else
distinguishes two applications in one database: a module ``file_key``, a qualified class name and a
method signature are all shared vocabulary.

3.1.1 put the **application outermost** and the language inside it (``can://<app>/java/<file>/…``),
so the scope is deliberately the *application* prefix rather than the narrower ``can://<app>/java/``
one. Two of the shared namespaces sit outside the language segment and inside the application:
``@external`` ghosts (``can://<app>/@external/<binary-type>/<signature>``, language-neutral in every
analyzer since 3.1.1) and artifacts (``can://<app>/artifact/<path>``). Scoping on the language
prefix would return no external symbols at all, silently. Nothing is over-admitted by the wider
prefix: a sibling analyzer's nodes over the same repository share it but carry no ``J*`` label, and
every statement here pins one.

**Seek labels.** Every statement anchors on the bare specific label; ``:JCanNode`` is used
nowhere. Not because the bare label always seeks — ``:JCallable`` owns no id index at all (only a
range index on ``name`` and the ``code``/``docstring`` fulltext), so bare ``:JCallable`` plans a
label scan — but because of what the statements here actually are. The two prefix-scoped ones both
fan out over relationships from every matched callable, and measured on ThingsBoard the traversal
dominates: swapping the anchor moves the wall clock by under 1% while ``:JCanNode`` adds a quarter
again as many db hits (5.65M against 4.55M on the call sites, 1.89M against 0.73M on the call
edges). Everything else is anchored on ``(:JApplication {id: $app_id})`` and never scans at all.
``:JCanNode``'s own index is not a constraint and spans 615,329 nodes, so where it *is* the only
seek it still loses — 118 ms against 24 on a whole-application prefix; it wins only a per-module
prefix, which no statement here issues. See ``test_no_statement_anchors_on_the_marker_label`` and
the table in Task 3 of the leg-3a plan.

**Which labels actually own an id constraint** (``SHOW CONSTRAINTS`` / ``SHOW INDEXES`` on 7691,
read 2026-09-07 — it is not "each keyed label", which is what the leg-3a plan assumed before the
graph existed). ``:JBodyNode`` owns a uniqueness constraint on ``id`` (``j_body_node_id``) and is
the **only** anchor on this surface that owns one directly — a ``:JBodyNode`` carries no
``:JSymbol``, only the ``:JCanNode`` marker, whose id index is not a constraint.
``:JCallable``, ``:JType`` and ``:JExternal`` own **no id index at all**: ``:JCallable`` has a range
index on ``name`` plus the ``code``/``docstring`` fulltext, ``:JType`` a range index on ``name``,
``:JExternal`` nothing. Their *nodes* also carry the merge label ``:JSymbol``, whose ``id``
uniqueness constraint is the only way to seek one by id — which is why the ``:JSymbol`` variants
were measured (they are a wash on the traversals and lose a signature lookup, 31.6 ms against 22.4)
and why the ``J_HAS_BODY_NODE`` hop anchored on ``:JCallable`` reads 6.7× the db hits. So the seek
rule holds not because every keyed label is indexed but because the hot statements anchor on the
one label that is.

**Strategy.** Unlike the Python and TypeScript Neo4j backends, which answer each accessor with its
own statement, this one rebuilds the canonical :class:`JApplication` from the graph and then answers
every query with the *same* logic the in-memory backend runs over the same models. The application
is built on first use, not at attach, and cached — **fourteen round trips in all**: four at attach
(the relationship-type fingerprint, the version probe, the resolution probe -- which reuses the
fingerprint -- and the module fetch) and ten on first use (the anchor's properties, then one
containment-subtree traversal instead of one query per parent, then call sites, imports, call edges,
externals, artifacts, dependencies, and the two codeanalyzer-java 3.1.0 config-overlay statements).
On a 3.0.x graph it is twelve: the anchor carries no entrypoint report, so the two config statements
are not issued at all (:meth:`_overlay_rows`).

**Lossiness** relative to the in-memory backend (the projection's, not this client's; see
:mod:`reconstruct` for the per-node detail): a module carries no ``source`` and no span, so
``JCompilationUnit.code`` is ``""`` and only a *callable's* text survives — as its whole
declaration, where the local backend's ``code`` is the body block; comments exist only as one
``docstring`` per declaration, so file-level comments are not projected at all
(:meth:`get_all_comments` and :meth:`get_comment_in_file` raise rather than answer with a smaller
set claiming to be every comment); ``JCallable.body`` holds the ``call`` nodes only, without their
``arguments`` or end columns; ``cfg``/``cdg``/``ddg``/``summary``, ``param_in``/``param_out`` and
``type_parameters`` are not rebuilt in 3a. Parameters, by contrast, round-trip exactly:
``JCallable.parameters_json`` is the analyzer's own serialisation of the list.

``--emit neo4j`` always runs at level 4 with external calls forced, so this graph carries ``J_CALLS``
edges to ``:JExternal`` targets that no ``analysis.json`` holds. :meth:`get_call_graph` keeps the 1.x
callable-only graph and drops them; :meth:`get_external_symbols` is what projects them.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from functools import cached_property
from typing import Any, Dict, FrozenSet, Iterable, List, Sequence, Tuple

import networkx as nx

from cldk.analysis.commons.bounds import DEFAULT_PAGE_SIZE, EdgeOrder, check_page_size, cursor_params, encode_cursor, keyset_where
from cldk.analysis.commons.graphs import flow_path, slice_resolved
from cldk.analysis.commons.results import EdgePage, FlowPaths, Slice, SliceNode
from cldk.analysis.java.backend import (
    CDG_ORDER,
    CFG_ORDER,
    CRUD_UNAVAILABLE,
    DDG_ORDER,
    SDG_REL_PATTERN,
    VIA,
    CallingLines,
    CRUDRow,
    JavaAnalysisBackend,
    duplicate_type_name,
    unhomed_endpoint,
)
from cldk.analysis.java.neo4j import reconstruct as R
from cldk.models.java import JGraphEdges
from cldk.models.java.models import (
    JApplication,
    JBodyNode,
    JCallable,
    JCallableParameter,
    JCallGraphEdge,
    JCallSite,
    JCdgEdge,
    JCfgEdge,
    JComment,
    JCompilationUnit,
    JConfigRead,
    JConfigUse,
    JDdgEdge,
    JDecorator,
    JEntrypointReport,
    JExternalSymbol,
    JField,
    JMethodDetail,
    JType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyDependency
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, GraphSchemaMismatch

logger = logging.getLogger(__name__)


def _scoped(var: str) -> str:
    """The application-scope predicate for node variable ``var``, spelled once here so it cannot
    drift: Java has a single id namespace, so it is one ``STARTS WITH`` against the prefix bound
    from :attr:`JNeo4jBackend._scope_prefix` — never ``any(p IN $prefixes …)``, which would plan as
    a label scan."""
    return f"{var}.id STARTS WITH $prefix"


def _semver(raw: Any) -> Tuple[int, int, int] | None:
    """``"3.0.1"`` (or ``"3.0.1-rc1"``) as ``(3, 0, 1)``; ``None`` for anything that does not start
    with three dotted integers, so an unparsable version is *unknown*, never silently zero."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", raw) if isinstance(raw, str) else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


#: A child row of the containment subtree: (relationship type, child properties, edge properties).
_Child = Tuple[str, Dict[str, Any], Dict[str, Any]]

#: The message for a comment accessor the projection cannot serve (D7): there are no ``:JComment``
#: nodes, so "every comment in this file" has no answer, and the docstrings that *are* projected
#: are a strictly smaller set that must not be returned as if it were the whole one.
_COMMENTS_UNAVAILABLE = (
    "The codeanalyzer-java Neo4j projection carries no comment nodes for application {app!r}: a type, "
    "callable or field keeps only its javadoc, in a docstring property, and a file-level comment is not "
    "projected at all. Read the declarations' javadoc with get_all_docstrings(), or the full comment set "
    "from analysis.json."
)


class JNeo4jBackend(JavaAnalysisBackend):
    """Query the application view of a Java project over Neo4j (Cypher), read-only.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``--app-name`` the graph was emitted with; the anchor is
            ``:JApplication {id: can://<application_name>}`` and the id prefix is
            ``can://<application_name>/``.
    """

    #: Relationship types every supported graph has; a graph missing any was emitted by another
    #: generation (a schema-v1 graph shares only ``J_CALLS``) and is refused at attach.
    _REQUIRED_RELATIONSHIP_TYPES: FrozenSet[str] = frozenset({"J_HAS_MODULE", "J_HAS_METHOD", "J_HAS_BODY_NODE", "J_CALLS"})
    #: The oldest codeanalyzer-java whose graph this backend serves. **3.1.1 exactly**, not
    #: "3.0.1 or newer": 3.1.1 moved the application to the outermost segment of the ``can://``
    #: grammar every statement here scopes on, and 3.1.0 — which is in the wild — emits the old
    #: ``can://java/<app>/…`` one. A 3.1.0 graph carries every relationship type
    #: :meth:`_probe_schema` looks for and the contract-2.0.0 body-node shape, so it attaches
    #: cleanly and then answers every prefix-scoped statement with zero rows. Refusing it by version
    #: is the only thing between a caller and that silent empty. (3.0.0 stamped contract 2.2.0;
    #: 3.0.1 holds 2.0.0 — the body-node shape every statement here reads.)
    _ANALYZER_FLOOR = (3, 1, 1)
    #: Set by :meth:`_probe_schema`; the class-level ``None`` is for the ``object.__new__`` seam.
    _analyzer_version: Tuple[int, int, int] | None = None
    #: The database's relationship types, read once by :meth:`_probe_schema` and reused by
    #: :meth:`_probe_resolution_edges`; the class-level default is for the same seam.
    _relationship_types: FrozenSet[str] = frozenset()
    #: Set by :meth:`_probe_resolution_edges` (see :attr:`has_resolution_edges`).
    _has_resolution_edges: bool = False
    _call_graph: nx.DiGraph | None = None

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_username: str,
        neo4j_password: str,
        neo4j_database: str | None = None,
        application_name: str | None = None,
    ) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with `pip install neo4j` (or `pip install cldk[neo4j]`).") from e
        self._init_with_driver(GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password)), application_name=application_name, neo4j_database=neo4j_database)

    @classmethod
    def _from_driver(cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> "JNeo4jBackend":
        """Construct from an already-built driver — the seam tests inject a fake driver through."""
        self = cls.__new__(cls)
        self._init_with_driver(driver, application_name=application_name, neo4j_database=neo4j_database)
        return self

    def _init_with_driver(self, driver: Any, *, application_name: str | None, neo4j_database: str | None) -> None:
        if not application_name:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        self.application_name = application_name
        self._database = neo4j_database
        self._driver = driver
        self._session_obj: Any | None = None
        self._probe_schema()
        self._has_resolution_edges = self._probe_resolution_edges()
        self._module_props: Dict[str, Dict[str, Any]] = self._load_modules()
        self._modules: List[str] = list(self._module_props)
        self._call_graph = None

    # -----[ scope ]-----
    @property
    def _application_id(self) -> str:
        """``can://<app>`` — the ``:JApplication`` root's own merge key since 3.1.1."""
        return f"can://{self.application_name}"

    @property
    def _scope_prefix(self) -> str:
        """``can://<app>/`` — the trailing slash keeps ``app`` from matching ``app-b``.

        The *application* prefix, not the narrower ``can://<app>/java/`` code one: an ``@external``
        ghost and an artifact both sit inside the application and outside the language segment (see
        the module docstring's **Scope**), and ``_external_rows`` would return nothing at all under
        the code prefix."""
        return f"{self._application_id}/"

    # -----[ lifecycle ]-----
    def close(self) -> None:
        """Close the underlying Neo4j driver."""
        if self._session_obj is not None:
            try:
                self._session_obj.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._session_obj = None
        self._driver.close()

    def __enter__(self) -> "JNeo4jBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        """Run one read statement over a reused session; drop the session on failure."""
        if self._session_obj is None:
            self._session_obj = self._driver.session(database=self._database)
        try:
            return [record.data() for record in self._session_obj.run(query, **params)]
        except Exception:
            self._session_obj = None
            raise

    # -----[ attach ]-----
    def _probe_schema(self) -> None:
        """J-9: the relationship-type fingerprint, then the analyzer generation the
        ``:JApplication`` anchor stamps, against :attr:`_ANALYZER_FLOOR`.

        A graph built by another codeanalyzer-java generation answers every statement here with
        zero rows and no error — indistinguishable from "this application has no callables". Below
        the floor, absent, or unreadable is refused, naming what was found.
        """
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        # Kept: :meth:`_probe_resolution_edges` asks whether ``J_RESOLVES_TO`` exists at all, and
        # this statement has already answered that. One fingerprint, two questions, one round trip.
        self._relationship_types = frozenset(found)
        missing = self._REQUIRED_RELATIONSHIP_TYPES - found
        if missing:
            raise GraphSchemaMismatch(expected=set(self._REQUIRED_RELATIONSHIP_TYPES), found=found, missing=missing)
        rows = self._run("OPTIONAL MATCH (a:JApplication {id: $app_id}) RETURN count(a) AS n, a.analyzer_version AS v", app_id=self._application_id)
        present = bool(rows and rows[0].get("n"))
        raw = rows[0].get("v") if rows else None
        version = _semver(raw)
        floor = ".".join(map(str, self._ANALYZER_FLOOR))
        if version is None or version < self._ANALYZER_FLOOR:
            if not present:
                what = "has no :JApplication node"
            elif version:
                what = f"was emitted by codeanalyzer-java {raw}"
            elif raw:
                what = f"reports analyzer_version {raw!r}"
            else:
                what = "has a :JApplication node that carries no analyzer_version"
            raise GraphSchemaMismatch(
                expected=set(self._REQUIRED_RELATIONSHIP_TYPES),
                found=found,
                missing=set(),
                message=f"The graph for application {self.application_name!r} {what}; this backend needs a graph emitted by codeanalyzer-java {floor} or newer.",
            )
        self._analyzer_version = version

    def _probe_resolution_edges(self) -> bool:
        """Whether **this application's** graph carries a single ``J_RESOLVES_TO`` edge, asked once
        at attach (see :attr:`has_resolution_edges`).

        The relationship-type check is free — :meth:`_probe_schema` already read the fingerprint —
        and short-circuits the statement on a database that has no such edge anywhere. The
        statement itself is scoped, because a database can hold several applications and one of
        them lacking resolution is not a fact about another.

        ``--emit neo4j`` always runs at full depth, so this is expected to be ``True`` on any graph
        built the documented way (133,423 edges on the reference database); the probe is defensive
        against a graph built some other way. It is information, not an error: unlike
        :meth:`_probe_schema` it never raises.
        """
        if "J_RESOLVES_TO" not in self._relationship_types:
            return False
        return bool(self._run(f"MATCH (b:JBodyNode)-[:J_RESOLVES_TO]->() WHERE {_scoped('b')} RETURN b LIMIT 1", prefix=self._scope_prefix))

    def _load_modules(self) -> Dict[str, Dict[str, Any]]:
        """``file_key -> module properties`` for the application's modules."""
        rows = self._run(
            "MATCH (:JApplication {id: $app_id})-[:J_HAS_MODULE]->(m:JModule) RETURN m.file_key AS k, properties(m) AS p ORDER BY m.file_key", app_id=self._application_id
        )
        return {r["k"]: r["p"] for r in rows}

    # =====================================================================================
    # Reconstruction: ten statements (eight on a 3.0.x graph), then the canonical JApplication.
    # =====================================================================================
    #: The whole containment subtree beneath the application's modules, in one statement: the
    #: ``*0..`` walk reaches every module, type (nested and local), callable and field, and the last
    #: hop yields each one's children as ``(parent id, relationship, child)`` rows. Anchored on the
    #: application, so it cannot leave it. ``J_HAS_FIELD`` is in the walk as well as in the child
    #: hop because a field is itself an annotation target (``J_ANNOTATED_BY`` runs from a type, a
    #: callable *or* a field), so it has to be reachable as a parent.
    _SUBTREE = (
        "MATCH (:JApplication {id: $app_id})-[:J_HAS_MODULE]->(root:JModule) "
        "MATCH (root)-[:J_DECLARES|J_HAS_METHOD|J_HAS_FIELD*0..]->(par)"
        "-[r:J_DECLARES|J_HAS_METHOD|J_HAS_FIELD|J_DECLARES_VAR|J_HAS_ENUM_CONSTANT|J_HAS_RECORD_COMPONENT|J_ANNOTATED_BY]->(n) "
        "RETURN par.id AS pk, type(r) AS rel, properties(n) AS p, properties(r) AS e, labels(n) AS labels "
        "ORDER BY n.start_line, n.name"
    )

    def _subtree_rows(self) -> Dict[str, List[_Child]]:
        rows = self._run(self._SUBTREE, app_id=self._application_id)
        children: Dict[str, List[_Child]] = defaultdict(list)
        for r in rows:
            children[r["pk"]].append((r["rel"], {**r["p"], "_labels": r["labels"]}, r["e"] or {}))
        return children

    def _call_site_rows(self) -> Dict[str, List[Tuple[Dict[str, Any], str | None]]]:
        """Each callable's ``call`` body nodes with the signature its ``J_RESOLVES_TO`` edge names
        (a project callable's or an external's), grouped by owning callable id."""
        rows = self._run(
            f"MATCH (c:JCallable)-[:J_HAS_BODY_NODE]->(b:JBodyNode {{kind: 'call'}}) WHERE {_scoped('c')} "
            "OPTIONAL MATCH (b)-[:J_RESOLVES_TO]->(t) "
            "RETURN c.id AS owner, properties(b) AS p, t.signature AS callee ORDER BY b.start_line, b.id",
            prefix=self._scope_prefix,
        )
        out: Dict[str, List[Tuple[Dict[str, Any], str | None]]] = defaultdict(list)
        for r in rows:
            out[r["owner"]].append((r["p"], r["callee"]))
        return out

    def _import_rows(self) -> Dict[str, List[Dict[str, Any]]]:
        rows = self._run(
            "MATCH (:JApplication {id: $app_id})-[:J_HAS_MODULE]->(m:JModule)-[r:J_IMPORTS]->() RETURN m.file_key AS k, properties(r) AS e ORDER BY m.file_key",
            app_id=self._application_id,
        )
        out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            out[r["k"]].append(r["e"])
        return out

    def _call_edge_rows(self) -> List[Dict[str, Any]]:
        """Every ``J_CALLS`` edge between two of this application's callables. Both endpoints carry
        the scope: an edge is only this application's when both ends are."""
        return self._run(
            f"MATCH (s:JCallable)-[r:J_CALLS]->(t:JCallable) WHERE {_scoped('s')} AND {_scoped('t')} " "RETURN s.id AS src, t.id AS dst, r.weight AS weight, r.prov AS prov",
            prefix=self._scope_prefix,
        )

    def _external_rows(self) -> List[Dict[str, Any]]:
        """Every out-of-project call target this application homed (leg 3b, Task 3).

        Prefix-scoped rather than anchored: an ``:JExternal`` hangs off no containment edge from the
        application -- it is reached only by the ``J_CALLS`` edges that name it -- so its own id is
        the whole scope. 1,195 rows on daytrader8, 2,570 on ThingsBoard, both emitted because
        ``--emit neo4j`` forces ``--external-calls``; a payload from a plain ``-a`` run has none and
        :meth:`JavaAnalysisBackend.get_external_symbols` says so rather than answering ``{}``.
        """
        return self._run(f"MATCH (e:JExternal) WHERE {_scoped('e')} RETURN properties(e) AS p ORDER BY e.id", prefix=self._scope_prefix)

    def _artifact_rows(self) -> List[Dict[str, Any]]:
        return self._run(
            "MATCH (:JApplication {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact) "
            "OPTIONAL MATCH (a)-[:DEFINES_CONFIG]->(ck:ConfigKey) "
            "RETURN properties(a) AS p, collect(properties(ck)) AS cks",
            app_id=self._application_id,
        )

    def _overlay_rows(self) -> Tuple[JEntrypointReport | None, List[JConfigUse], List[JConfigRead]]:
        """The three application-scope overlays codeanalyzer-java 3.1.0 added, in two statements.

        The report is the whole ``JEntrypointReport`` as sorted-key JSON on the anchor, exactly as
        codeanalyzer-python projects ``PyApplication.entrypoint_report``, so it parses back into the
        model with no lossiness. ``properties(a)`` rather than naming the key: a 3.0.x graph has no
        such property at all, and naming one statically makes the server log a warning per call.

        **The report is also the overlay probe** (see :data:`~cldk.analysis.java.backend.CONFIG_OVERLAY_UNAVAILABLE`):
        a 3.1.0 graph carries it whatever the application reads, whereas ``J_USES_CONFIG`` and
        ``J_READS_CONFIG_UNRESOLVED`` are declared as relationship types only once an edge of that
        type exists. So the config lists are ``None`` — "nothing looked" — exactly when the report
        is absent, and a real (possibly empty) list otherwise.

        Both endpoints of ``J_USES_CONFIG`` carry the scope, and each carries a different one,
        because the edge is the one place the two id spaces meet: the **key** is anchored through
        the artifact layer (``can://<app>/artifact/…``, reached only by walking out from the
        application anchor) and the **source** by the id prefix, since the schema roots that edge
        on a body node, a
        callable, a field or a type — none of which the application anchor reaches in one hop.
        ``J_READS_CONFIG_UNRESOLVED`` runs from the anchor itself and carries no ``site``, which is
        the lossiness :meth:`~cldk.analysis.java.backend.JavaAnalysisBackend.get_unresolved_config_reads`
        states.
        """
        rows = self._run("MATCH (a:JApplication {id: $app_id}) RETURN properties(a) AS p", app_id=self._application_id)
        raw = rows[0]["p"].get("entrypoint_report_json") if rows else None
        if raw is None:
            return None, [], []
        uses = self._run(
            "MATCH (:JApplication {id: $app_id})-[:HAS_ARTIFACT]->(:Artifact)-[:DEFINES_CONFIG]->(ck:ConfigKey)<-[u:J_USES_CONFIG]-(src) "
            f"WHERE {_scoped('src')} RETURN src.id AS src, ck.id AS dst, u.prov AS prov ORDER BY src.id, ck.id",
            app_id=self._application_id,
            prefix=self._scope_prefix,
        )
        reads = self._run(
            "MATCH (:JApplication {id: $app_id})-[u:J_READS_CONFIG_UNRESOLVED]->(ghost) RETURN properties(u) AS p, ghost.id AS callee ORDER BY u.key, ghost.id",
            app_id=self._application_id,
        )
        return (
            JEntrypointReport.model_validate_json(raw),
            [JConfigUse(src=r["src"], dst=r["dst"], prov=list(r["prov"] or [])) for r in uses],
            [JConfigRead(site="", callee=r["callee"], key=r["p"].get("key"), reason=r["p"].get("reason", "non-literal"), prov=list(r["p"].get("prov") or [])) for r in reads],
        )

    def _dependency_rows(self) -> List[Dict[str, Any]]:
        return self._run(
            "MATCH (:JApplication {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(p:Package) "
            "RETURN properties(r) AS rel, properties(p) AS pkg, a.id AS declared_in ORDER BY p.name",
            app_id=self._application_id,
        )

    # -----[ the containment tree ]-----
    @staticmethod
    def _child_key(parent_id: str, props: Dict[str, Any]) -> str:
        """A declared type's container key: the id segment under its parent, which is its simple
        name. A child id is minted under its parent's by construction, so a mismatch is an emitter
        defect, named by the declaration rather than by either id (E6)."""
        node_id = props["id"]
        if not node_id.startswith(parent_id + "/"):
            raise CodeanalyzerExecutionException(
                f"declaration {props.get('name') or props.get('signature')!r} is reached from a parent that did not mint its id: "
                f"codeanalyzer-java emitted a containment edge this backend cannot key"
            )
        return node_id[len(parent_id) + 1 :]

    def _decorators(self, node_id: str, children: Dict[str, List[_Child]]) -> List[JDecorator]:
        return [R.decorator(p, e) for rel, p, e in children.get(node_id, []) if rel == "J_ANNOTATED_BY"]

    def _body(self, callable_id: str, sites: Dict[str, List[Tuple[Dict[str, Any], str | None]]]) -> Dict[str, JBodyNode]:
        """The ``call`` entries of a callable's ``body`` map, keyed the analyzer's way: the ``L:C``
        the node id's ``@`` suffix spells (which is a key, not a position -- see
        :func:`reconstruct.body_node`)."""
        return {props["id"][len(callable_id) + 1 :]: R.body_node(props, callee) for props, callee in sites.get(callable_id, [])}

    def _callable(self, props: Dict[str, Any], children: Dict[str, List[_Child]], sites: Dict[str, List[Tuple[Dict[str, Any], str | None]]]) -> JCallable:
        node_id = props["id"]
        rows = children.get(node_id, [])
        return R.callable_(
            props,
            decorators=self._decorators(node_id, children),
            body=self._body(node_id, sites),
            local_variables=[R.variable(p) for rel, p, _ in rows if rel == "J_DECLARES_VAR"],
            types={self._child_key(node_id, p): self._type(p, children, sites) for rel, p, _ in rows if rel == "J_DECLARES"},
        )

    def _type(self, props: Dict[str, Any], children: Dict[str, List[_Child]], sites: Dict[str, List[Tuple[Dict[str, Any], str | None]]]) -> JType:
        node_id = props["id"]
        rows = children.get(node_id, [])
        callables: Dict[str, JCallable] = {}
        types: Dict[str, JType] = {}
        for rel, p, _ in rows:
            if rel == "J_HAS_METHOD":
                callables[p["signature"]] = self._callable(p, children, sites)
            elif rel == "J_DECLARES":
                types[self._child_key(node_id, p)] = self._type(p, children, sites)
        return R.type_(
            props,
            decorators=self._decorators(node_id, children),
            fields={p["name"]: R.field(p, self._decorators(p["id"], children)) for rel, p, _ in rows if rel == "J_HAS_FIELD"},
            callables=callables,
            types=types,
            enum_constants=[R.enum_constant(p) for rel, p, _ in rows if rel == "J_HAS_ENUM_CONSTANT"],
            record_components=[R.record_component(p) for rel, p, _ in rows if rel == "J_HAS_RECORD_COMPONENT"],
        )

    def _reconstruct(self) -> JApplication:
        """The canonical :class:`JApplication` for this application, rebuilt from the graph."""
        report, uses, reads = self._overlay_rows()
        children = self._subtree_rows()
        sites = self._call_site_rows()
        imports = self._import_rows()
        symbol_table: Dict[str, JCompilationUnit] = {}
        for key, props in self._module_props.items():
            module_id = props["id"]
            types: Dict[str, JType] = {}
            for rel, p, _ in children.get(module_id, []):
                if rel != "J_DECLARES":
                    continue
                # A module declares types only; ``kind`` is a ``Literal`` on :class:`JType`, so a
                # row that is not one is refused by the model.
                types[self._child_key(module_id, p)] = self._type(p, children, sites)
            unit = R.compilation_unit(props, import_declarations=[i for e in imports.get(key, []) for i in R.imports(e)], types=types)
            R.thread_code(unit, self._projected_code(children, module_id))
            symbol_table[key] = unit
        return JApplication(
            id=self._application_id,
            symbol_table=symbol_table,
            call_graph=[JCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or []), weight=r["weight"] or 1) for r in self._call_edge_rows()],
            # ``{}`` and never ``None``: ``--emit neo4j`` forces ``--external-calls``, so a graph
            # this backend can attach to was *always* asked, and no rows therefore means "homed
            # them, found none" -- a real answer about a project that calls nothing outside itself.
            # ``None`` is the local payload's own value, for the run that was never asked, and it is
            # the one ``get_external_symbols`` raises on (D7). Coercing an empty map to it here made
            # the ``{}`` both that method and the facade document unreachable on either backend.
            external_symbols={r["p"]["id"]: JExternalSymbol(**{k: v for k, v in r["p"].items() if k != "id"}) for r in self._external_rows()},
            artifacts={
                a.path: a
                for a in (
                    R.artifact(r["p"], config_keys=[R.config_key(p) for p in sorted((c for c in r["cks"] if c), key=lambda c: c["id"])])
                    for r in sorted(self._artifact_rows(), key=lambda r: r["p"]["path"])
                )
            },
            dependencies=[R.dependency(r["rel"], r["pkg"], r["declared_in"]) for r in self._dependency_rows()],
            entrypoint_report=report,
            # ``None`` and ``[]`` are different answers here: see :meth:`_overlay_rows`.
            config_uses=None if report is None else uses,
            config_reads_unresolved=None if report is None else reads,
        )

    @staticmethod
    def _projected_code(children: Dict[str, List[_Child]], module_id: str) -> Dict[str, str]:
        """``callable id -> code`` for one module's subtree — what :func:`reconstruct.thread_code`
        threads onto the callables so their ``code`` view reads the graph's text."""
        out: Dict[str, str] = {}
        stack = [module_id]
        while stack:
            for rel, p, _ in children.get(stack.pop(), []):
                if rel in ("J_DECLARES", "J_HAS_METHOD"):
                    stack.append(p["id"])
                    if rel == "J_HAS_METHOD":
                        out[p["id"]] = p.get("code") or ""
        return out

    # =====================================================================================
    # The reconstructed view and its index (both built on first use)
    # =====================================================================================
    @cached_property
    def _application(self) -> JApplication:
        """The application view, rebuilt from the graph on first use and cached. Private because
        :attr:`_idx` and :attr:`_call_graph` are derived from it and cached beside it: rebinding it
        would leave them answering from the object it replaced. Tests that need a seeded view
        without a server write ``backend.__dict__["_application"]``, which is exactly what this
        ``cached_property`` would have stored."""
        return self._reconstruct()

    @property
    def application(self) -> JApplication:
        """The application view (read-only; see :attr:`_application`)."""
        return self._application

    @cached_property
    def _idx(self) -> Tuple[Dict[str, JType], Dict[str, str], Dict[str, Tuple[JType, JCallable]]]:
        """The containment tree flattened once: every type (top-level, nested, local/anonymous) by
        its source-spelled qualified name, its file, and every callable by its ``can://`` id — the
        join that turns a call-graph endpoint into the ``"<type fqn>.<signature>"`` node key.
        Mirrors :meth:`JCodeanalyzer._index`."""
        types: Dict[str, JType] = {}
        file_of: Dict[str, str] = {}
        callables: Dict[str, Tuple[JType, JCallable]] = {}

        def add(t: JType, path: str) -> None:
            name = t.qualified_name
            if name in types:
                raise CodeanalyzerExecutionException(duplicate_type_name(name))
            types[name] = t
            file_of[name] = path
            for c in t.callables.values():
                callables[c.id] = (t, c)
                for local in c.types.values():
                    add(local, path)
            for nested in t.types.values():
                add(nested, path)

        for path, unit in self._application.symbol_table.items():
            for t in unit.types.values():
                add(t, path)
        return types, file_of, callables

    @property
    def _types(self) -> Dict[str, JType]:
        return self._idx[0]

    @property
    def _file_of(self) -> Dict[str, str]:
        return self._idx[1]

    @property
    def _callables(self) -> Dict[str, Tuple[JType, JCallable]]:
        return self._idx[2]

    # -----[ the addressing surface (leg 3b) — the three facts the shared implementation needs ]-----
    #: The one statement leg 3b's Task 1 adds. Anchored on the **bare** ``:JBodyNode`` label and a
    #: per-callable id prefix, which is the narrowest predicate on this surface.
    #:
    #: SEEK MEASURED, not ported (PROFILE, median of 5 with the first discarded, over the driver,
    #: on ThingsBoard — 598,413 nodes, 496,821 of them body nodes):
    #:
    #:   8 callables / 4,004 body nodes:  bare :JBodyNode  86.6 ms, 16,024 db hits
    #:                                    :JCanNode        86.8 ms, 20,028 db hits
    #:                                    J_HAS_BODY_NODE 204.2 ms, 503,741 db hits
    #:   1 callable / 617 body nodes:     bare :JBodyNode  13.53 ms, 2,469 db hits
    #:                                    :JCanNode        13.56 ms, 3,086 db hits
    #:
    #: The bare label owns an id range index of its own (``j_body_node_id``), so it seeks; the
    #: marker label seeks too and reads a quarter again as many db hits for the same rows, which is
    #: leg 3a's finding on a narrower prefix rather than leg 2.5b's. The containment hop is the
    #: outlier and the reason it is not used: ``:JCallable`` has **no** id index (only ``name`` and
    #: the ``code``/``docstring`` fulltext), so anchoring on the callable scans the label.
    #:
    #: ``UNWIND`` rather than ``any(p IN $prefixes …)``: one indexed range seek per prefix, where
    #: the ``any`` form plans as a label scan (the same trap :func:`_scoped` exists to avoid).
    #: ``OPTIONAL MATCH`` and not a second statement: a ``call`` node's ``J_RESOLVES_TO`` target is
    #: what :attr:`~cldk.analysis.commons.results.BodyRef.callee` *is*, and reading it here keeps
    #: :meth:`locate` at one round trip. ``OPTIONAL`` because most body nodes are not calls (4,006
    #: of daytrader8's 13,436 are) and an unresolved call is a real outcome; at most one edge leaves
    #: any body node (checked: 0 nodes with two, on both applications), so no row is duplicated.
    #: Measured cost of adding it (PROFILE, median of 5 with the first discarded, ThingsBoard, 8
    #: callables / 2,579 body nodes): 70.65 ms against 57.91 without, 22,401 db hits against 10,324
    #: -- one expand per body node, paid inside the round trip it saves.
    #:
    #: **An ``@external`` target is a ``callee``.** ``--emit neo4j`` forces ``--external-calls``, so
    #: 2,283 of daytrader8's 4,006 call sites resolve to a ``:JExternal`` whose id is
    #: application-scoped (``can://daytrader8/@external/…``). The contract is "the id of what it
    #: resolves to", and that id is one: :meth:`get_external_symbols` keys its map by exactly these
    #: strings, so the caller already has the vocabulary to look one up. Withholding it would mint
    #: the ``None`` that means "never resolved" for a call that plainly did.
    _BODY_NODES = (
        "UNWIND $prefixes AS p MATCH (b:JBodyNode) WHERE b.id STARTS WITH p "
        "OPTIONAL MATCH (b)-[:J_RESOLVES_TO]->(t) WHERE " + _scoped("t") + " "
        "RETURN b.id AS id, b.kind AS kind, b.start_line AS s, b.end_line AS e, t.id AS callee"
    )

    def _body_nodes(self, callable_ids: Sequence[str]) -> Dict[str, Dict[str, JBodyNode]]:
        """See :meth:`JavaAnalysisBackend._body_nodes` — read from the graph, which holds every
        body node, rather than from the reconstruction, which rebuilds the ``call`` ones only.

        Rows are grouped back onto their callables at the first ``@``, which is exact because a
        Java ``can://`` id carries none (checked: 0 of daytrader8's 1,216 and ThingsBoard's 28,763
        callables). That recovers the *callable*, which is all that is needed here; it does not
        recover the local body key, and nothing tries to.
        """
        ids = list(dict.fromkeys(callable_ids))
        if not ids:
            return {}
        out: Dict[str, Dict[str, JBodyNode]] = {}
        for row in self._run(self._BODY_NODES, prefixes=[f"{i}@" for i in ids], prefix=self._scope_prefix):
            node_id = row["id"]
            out.setdefault(node_id.partition("@")[0], {})[node_id] = R.body_node({"kind": row["kind"], "start_line": row["s"], "end_line": row["e"], "callee": row["callee"]}, None)
        return out

    def _body_source(self, node: JBodyNode) -> str | None:
        """See :meth:`JavaAnalysisBackend._body_source`. Always ``None``: ``:JBodyNode`` carries a
        line range and no text, and ``:JModule`` carries no ``source`` to slice one out of, so this
        projection has nothing below callable granularity. Substituting the enclosing callable's
        declaration would be a wrong answer rather than a missing one."""
        return None

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`JavaAnalysisBackend.has_resolution_edges`. Fixed at construction by
        :meth:`_probe_resolution_edges`."""
        return self._has_resolution_edges

    # =====================================================================================
    # The dataflow surface (leg 3b, Task 2) -- over Cypher.
    #
    # ONLY THE BODY-NODE HALF IS HERE. The call-graph accessors (``reaches``, ``callers_of``,
    # ``callees_of``, ``backward_cone``, ``call_paths_between``) are answered by the shared
    # implementation on :class:`JavaAnalysisBackend`, over the ``get_call_graph()`` this backend
    # already projects out of ``J_CALLS``; what is left is what leg 3a's reconstruction does *not*
    # rebuild -- ``cfg``/``cdg``/``ddg``/``summary`` and the ``J_PARAM_IN``/``J_PARAM_OUT`` lattice.
    #
    # SEEK LABELS, MEASURED ON THINGSBOARD, NOT PORTED (PROFILE, median of 5 with the first
    # discarded, over the driver; 598,413 nodes, 496,821 of them body nodes). Task 1's per-callable
    # prefix is the narrowest predicate on this surface and leg 2.5b found narrowness decides the
    # anchor, so the DDG page was re-measured against it here:
    #
    #   per-callable DDG page, 8 callables (2,729 edges)
    #     UNWIND $prefixes / (s:JBodyNode)           76.71 ms   19,407 db hits
    #     UNWIND $prefixes / (s:JCanNode:JBodyNode)  79.02 ms   22,136 db hits
    #   one callable (482 edges)
    #     $bp / (s:JBodyNode)                        17.81 ms    3,195 db hits
    #     (c:JCallable {id})-[:J_HAS_BODY_NODE]->(s) 19.31 ms   21,435 db hits
    #     (c:JSymbol   {id})-[:J_HAS_BODY_NODE]->(s) 18.78 ms   21,435 db hits
    #   one callable, per graph (bare label / marker label)
    #     J_CFG_NEXT   3.12 / 3.15 ms      J_CDG   2.59 / 2.95 ms      J_DDG  14.79 / 15.27 ms
    #
    # The bare ``:JBodyNode`` wins on both, as it did in Task 1 and unlike TypeScript: it owns an id
    # range index of its own (``j_body_node_id``) so it seeks, and ``:JCanNode`` seeks too while
    # reading 14-21% more db hits for the same rows. The containment hop is within noise on the wall
    # clock and reads **6.7x** the db hits, because ``:JCallable`` owns no id index at all.
    #
    # AND THE CONTAINMENT SPELLING IS ALSO WRONG, WHICH IS THE REASON IT IS NOT USED.
    # ``PyNeo4jBackend._OWN_EDGES`` binds the containment relationship twice --
    # ``(c)-[:HAS_BODY_NODE]->(s)-[r]->(d)<-[:HAS_BODY_NODE]-(c)`` -- and Cypher's
    # relationship-uniqueness rule forbids the two from being the same relationship, so every
    # **self-loop** is silently dropped *and* ``total``, computed from the same MATCH, reports the
    # page complete. Java has 978 ``J_DDG`` self-loops. Measured on this graph:
    # ``Log.printCollection(java.util.Collection)`` has 20 DDG edges, 11 of them self-loops, and the
    # doubled spelling returns 9; ThingsBoard's ``updateState(java.util.Set, …)`` has 84 and returns
    # 78. The id-prefix spelling below has no such pattern and returns all of them (python-sdk#349).
    #
    # THE PREFIX IS EXACT, VERIFIED RATHER THAN ASSUMED: every body-node id is its owning callable's
    # id plus ``@`` (0 exceptions on both applications), and no callable id is another callable id
    # plus ``@``, so ``<callable id>@`` selects that callable's body nodes and nothing else. Both
    # endpoints carry it, which keeps "both ends in one callable" written rather than trusted.
    # =====================================================================================
    #: One callable's own edges of one kind. ``UNWIND $prefixes AS p`` rather than a bare ``$bp``
    #: parameter for the reason Task 1's :attr:`_BODY_NODES` uses it: it is the spelling the
    #: multi-application audit reads as the *narrow* scope, and whose bound values ``_responder``
    #: checks. Measured cost of the spelling itself: 4.21 ms against 3.85 for the same single
    #: prefix, same db hits.
    _OWN_EDGES = "UNWIND $prefixes AS p MATCH (s:JBodyNode)-[r:{rel}]->(d:JBodyNode) WHERE s.id STARTS WITH p AND d.id STARTS WITH p "

    def _own_edges(self, name: str, in_class: str | None, rel: str, projection: str, order: EdgeOrder, page_size: int, cursor: str | None):
        """One page of a callable's own ``rel`` edges: ``(key, rows, whole size, is there more)``.

        Resolution is :meth:`resolve_callable`'s, not a second path, so an ambiguous name raises
        listing candidates here exactly as it does there — and its ``ref`` is the callable id the
        body-node prefix is built from, which is why this accessor pays no extra lookup for it.

        **Keyset, not ``SKIP``.** ``order.exprs`` is the canonical order written as Cypher — the
        same components ``order.key`` produces in Python, ``coalesce``-d the way ``or ""`` / ``or []``
        normalise there — and a cursor becomes a ``WHERE`` filter
        (:func:`~cldk.analysis.commons.bounds.keyset_where`) rather than an offset, which is flat in
        the page depth where an offset re-sorts a growing prefix.

        **Two statements, not one.** ``total`` is not optional: without it a caller cannot see the
        size of what it is walking into from the first page, which is E5's whole point. It is
        re-counted per page rather than cached, because the alternative is a number that can go
        stale against a graph this backend does not own. The page asks for ``page_size + 1`` rows and
        reports ``more`` from whether it got them, so "there is more" is a fact about the data rather
        than an inference from ``len(rows) == page_size`` — which is wrong exactly when the set ends
        on a page boundary.
        """
        check_page_size(page_size)
        node = self.resolve_callable(name, in_class=in_class)
        self._require_explicit(node.callable, "it has no control or data flow to return")
        key, params = node.callable, {"prefixes": [node.ref + "@"]}
        match = self._OWN_EDGES.format(rel=rel)
        total = self._run(match + "RETURN count(r) AS total", **params)[0]["total"]
        where = f"WHERE {keyset_where(order.exprs)} " if cursor is not None else ""
        rows = self._run(
            f"{match}WITH s.id AS src, d.id AS dst{projection} {where}RETURN * ORDER BY {', '.join(order.exprs)} LIMIT $lim",
            lim=page_size + 1,
            **params,
            **(cursor_params(cursor, key, len(order.exprs)) if cursor is not None else {}),
        )
        return key, rows[:page_size], total, len(rows) > page_size

    @staticmethod
    def _page(model, scope: str, edges: List, order: EdgeOrder, total: int, more: bool) -> EdgePage:
        """Wrap a page's edges, deriving ``next_cursor`` from the *same* sort key the in-memory
        backend uses — so a cursor minted here and one minted there name the same position."""
        return EdgePage[model](edges=edges, total=total, next_cursor=encode_cursor(scope, order.key(edges[-1])) if more and edges else None)

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCfgEdge]:
        """One page of control flow within one callable (see :meth:`JavaAnalysisBackend.get_cfg`)."""
        key, rows, total, more = self._own_edges(callable, in_class, "J_CFG_NEXT", ", r.kind AS kind", CFG_ORDER, page_size, cursor)
        return self._page(JCfgEdge, key, [JCfgEdge(src=r["src"], dst=r["dst"], kind=r["kind"]) for r in rows], CFG_ORDER, total, more)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCdgEdge]:
        """One page of control dependence within one callable (see :meth:`JavaAnalysisBackend.get_cdg`)."""
        key, rows, total, more = self._own_edges(callable, in_class, "J_CDG", "", CDG_ORDER, page_size, cursor)
        return self._page(JCdgEdge, key, [JCdgEdge(src=r["src"], dst=r["dst"]) for r in rows], CDG_ORDER, total, more)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JDdgEdge]:
        """One page of data dependence within one callable (see :meth:`JavaAnalysisBackend.get_ddg`).

        ``prov`` is one of Java's two tiers — ``ssa`` on 133,608 of the reference graph's edges and
        ``points-to`` on 1,134. ``or []`` restores the model's default rather than failing validation
        on an edge that carries none, and the ``coalesce`` in the sort key does the same for the
        ordering: a null there would make the keyset filter drop the row silently."""
        key, rows, total, more = self._own_edges(callable, in_class, "J_DDG", ", r.var AS var, r.prov AS prov", DDG_ORDER, page_size, cursor)
        edges = [JDdgEdge(src=r["src"], dst=r["dst"], var=r["var"], prov=list(r["prov"] or [])) for r in rows]
        return self._page(JDdgEdge, key, edges, DDG_ORDER, total, more)

    # -----[ slicing ]-----
    #: Reverse (backward) and forward reachability over the SDG, as ONE variable-length match.
    #: ``*0..`` rather than ``*1..`` so the seed is part of its own slice without being spliced in
    #: afterwards, which matters because ``total`` and the ``max_nodes`` prefix both have to be over
    #: the same set. ``total`` and the page come back from one statement: the rows are collected in
    #: id order, ``size()`` gives the whole slice's size, and only the first ``$cap`` cross the wire.
    #:
    #: The seed carries the application prefix as well as its id. Redundant against a ``$id`` this
    #: SDK minted, and written anyway: this backend attaches to graphs it did not emit, and the
    #: scope audit judges the predicate that is *written*, never the one a caller can be trusted to
    #: have satisfied. No callable is joined back: :meth:`JavaAnalysisBackend._body_slice_node`
    #: recovers the owner, the file and the parameter names from the id prefix and the index this
    #: backend already holds, which is both cheaper than a ``J_HAS_BODY_NODE`` hop and the reason the
    #: two backends describe a vertex identically.
    #:
    #: ``all(n IN nodes(p) …)`` is the **interior**, and it is here for :attr:`_PATHS`'s reason
    #: rather than by analogy with it: a variable-length pattern binds only its two endpoints, so
    #: scoping those leaves every node between them free and a walk may leave the application and
    #: come back. There are 0 cross-application SDG edges in the reference graph today, which makes
    #: this latent and not a bug report -- and the rule of this leg is that the audit judges the
    #: predicate that is written, not the graph that happens to be attached. The far endpoint keeps
    #: its own ``STARTS WITH`` alongside, redundantly and deliberately, exactly as :attr:`_PATHS`
    #: keeps both of its: the audit judges **per bound variable**, and a variable whose only excuse
    #: is a predicate over an unnamed path reads to it as unscoped. Cost, re-measured on the shipped
    #: statements (ThingsBoard, the largest ``formal_in`` backward cone -- 604 nodes at depth 5 --
    #: the two forms interleaved, median of 5 with the first round discarded, three sessions):
    #: **9.6 / 9.9 / 9.9 ms with the predicate against 9.2 / 9.5 / 9.1 without**, identical rows.
    #: Keeping the endpoint predicate is what makes it that cheap: Neo4j inlines the ``all()`` into
    #: the same expansion instead of re-planning around it.
    _SLICE = (
        "MATCH (r:JBodyNode {{id:$id}}) WHERE r.id STARTS WITH $prefix "
        "MATCH p = (r){left}[:{rels}*0..{depth}]{right}(m:JBodyNode) WHERE m.id STARTS WITH $prefix AND all(n IN nodes(p) WHERE n.id STARTS WITH $prefix) "
        "WITH DISTINCT m.id AS ref, m.kind AS kind, m.start_line AS line ORDER BY ref "
        "WITH collect({{ref: ref, kind: kind, line: line}}) AS found "
        "RETURN size(found) AS total, found[0..$cap] AS page"
    )

    def _value_slice(self, root: SliceNode, *, backward: bool, depth: int | None, max_nodes: int) -> Slice:
        """See :meth:`JavaAnalysisBackend._value_slice`. The two directions differ only in which way
        the arrows point, so they share a query and a builder."""
        query = self._SLICE.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth, left="<-" if backward else "-", right="-" if backward else "->")
        row = self._run(query, id=root.ref, cap=max_nodes, prefix=self._scope_prefix)[0]
        nodes = [self._body_slice_node(n["ref"], n["kind"], n["line"]) for n in row["page"]]
        return Slice(nodes=nodes, roots=[root], resolved=slice_resolved([root]), total=row["total"])

    # -----[ paths and the flow predicate ]-----
    #: The caller's word for a hop, computed in Cypher so the ORDER BY below sorts by the same
    #: vocabulary :func:`~cldk.analysis.commons.graphs.hop_sort_key` sorts by. Ordering by the raw
    #: ``type(rel)`` instead would be just as deterministic and a *different* order, so the two
    #: backends would truncate ``max_paths`` to different witnesses.
    _VIA_CASE = "CASE type(relationships(p)[i]) " + " ".join(f"WHEN '{rel}' THEN '{word}'" for rel, word in VIA.items()) + " ELSE type(relationships(p)[i]) END"

    #: One string per path, ordered exactly as Python would order the tuple ``hop_sort_key`` builds.
    #: ``U+0001`` is the separator rather than ``|`` for one reason: string comparison agrees with
    #: field-by-field comparison **only** when the separator sorts below every character a field can
    #: hold, and ``|`` (0x7C) sorts *above* every lowercase letter. ``elementId`` is the last field
    #: of each hop and breaks the tie between parallel relationships a caller cannot tell apart.
    #:
    #: **The per-callable graph orders do not have this tie-break, and that asymmetry is deliberate
    #: but not free.** ``CFG_ORDER``/``CDG_ORDER``/``DDG_ORDER`` (``cldk/analysis/java/backend.py``)
    #: end at ``coalesce(kind,'')`` / ``dst`` / ``coalesce(prov,[])`` — no ``elementId``, because
    #: the key has to be *the same key the in-memory backend sorts by*, and there is no element id
    #: in ``analysis.json``. Two edges with an identical full sort key straddling a page boundary
    #: would therefore lose the second to the keyset ``WHERE`` while ``total``, counted from the
    #: same MATCH, still counted both. Measured 2026-09-07, and it cannot fire today: **0** fully
    #: identical parallel ``J_CFG_NEXT``/``J_CDG``/``J_DDG`` edges across both applications of the
    #: reference graph, and 0 in ``analysis.json`` — the committed a1 and a4 fixtures and the whole
    #: 22.5 MB daytrader8 ``-a 4`` payload (6,984 ``cfg``, 4,416 ``cdg`` and 5,434 ``ddg`` edges,
    #: every one with a distinct key within its callable). If an analyzer ever emits one, the fix
    #: is a fourth component both backends can compute, not an ``elementId`` only one of them has.
    _PATH_ORDER = (
        "reduce(k = '', i IN range(0, length(p) - 1) | k + " + _VIA_CASE + " + '\\u0001' + coalesce(relationships(p)[i].var, '') "
        "+ '\\u0001' + nodes(p)[i + 1].id + '\\u0001' + elementId(relationships(p)[i]) + '\\u0001')"
    )

    #: ``allShortestPaths`` and not a plain variable-length match: a variable-length pattern
    #: enumerates *trails*, which does not terminate on a real dependence graph, while
    #: ``allShortestPaths`` is a bidirectional BFS. ``$cap`` is ``max_paths + 1`` so one extra row
    #: reports the truncation, rather than a second traversal for a number the caller cannot act on.
    #:
    #: ``all(n IN nodes(p) WHERE …)`` is the **interior** scope, and it is not optional: without it
    #: only the two endpoints carry the application prefix and a path could route through another
    #: application's nodes and come back. Leg 2.5b found exactly that leak twice in its own path
    #: enumerators; Neo4j inlines an ``all()`` node predicate into the shortest-path search itself,
    #: so it is a correctness win at no cost.
    _PATHS = (
        "MATCH (a:JBodyNode {{id:$src}}) WHERE a.id STARTS WITH $prefix "
        "MATCH (b:JBodyNode {{id:$dst}}) WHERE b.id STARTS WITH $prefix "
        "MATCH p = allShortestPaths((a)-[:{rels}*1..{depth}]->(b)) WHERE all(n IN nodes(p) WHERE n.id STARTS WITH $prefix) "
        "WITH p, " + _PATH_ORDER + " AS key ORDER BY length(p), key LIMIT $cap "
        "RETURN [n IN nodes(p) | {{ref: n.id, kind: n.kind, line: n.start_line}}] AS ns, "
        "[e IN relationships(p) | {{via: type(e), var: e.var, prov: e.prov}}] AS rs"
    )

    def _value_paths(self, a: SliceNode, b: SliceNode, depth: int | None, max_paths: int) -> FlowPaths:
        """See :meth:`JavaAnalysisBackend._value_paths`."""
        query = self._PATHS.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth)
        rows = self._run(query, src=a.ref, dst=b.ref, cap=max_paths + 1, prefix=self._scope_prefix)
        paths = [
            flow_path(
                [self._body_slice_node(n["ref"], n["kind"], n["line"]) for n in r["ns"]],
                [(e["via"], e["var"], e["prov"]) for e in r["rs"]],
                via=VIA,
            )
            for r in rows[:max_paths]
        ]
        return FlowPaths(paths=paths, complete=len(rows) <= max_paths)

    #: ``WITH DISTINCT m`` before the membership test is what makes this a pruning BFS instead of a
    #: trail enumeration. Every hop is inside the application, by the same whole-path predicate
    #: :attr:`_SLICE` and :attr:`_PATHS` carry -- which this claimed before it had one, while
    #: scoping only its seed and its far endpoint. Measured on ThingsBoard the same way as
    #: :attr:`_SLICE`: **1.6 / 1.5 / 1.8 ms with the predicate against 1.8 / 2.0 / 1.7 without** at
    #: depth 5, i.e. inside the noise, and unbounded from the largest forward cone found (2,361
    #: nodes) 9 ms either way. Binding the path does not cost the pruning.
    _VALUE_REACHES = (
        "MATCH (a:JBodyNode {{id:$src}}) WHERE a.id STARTS WITH $prefix "
        "MATCH p = (a)-[:{rels}*1..{depth}]->(m:JBodyNode) WHERE m.id STARTS WITH $prefix AND all(n IN nodes(p) WHERE n.id STARTS WITH $prefix) "
        "WITH DISTINCT m WHERE m.id IN $dsts RETURN count(m) > 0 AS ok"
    )

    def _value_reaches(self, src: str, dsts: Sequence[str], depth: int | None) -> bool:
        """See :meth:`JavaAnalysisBackend._value_reaches`."""
        query = self._VALUE_REACHES.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth)
        return bool(self._run(query, src=src, dsts=[d for d in dsts if d != src], prefix=self._scope_prefix)[0]["ok"])

    #: Whether this application's parameter vertices have any outgoing SDG edge — the measurement
    #: the four forward value accessors refuse on
    #: (:data:`~cldk.analysis.java.backend.PORTS_DISCONNECTED`). Costs 6.5 ms on daytrader8 and
    #: 185.4 ms on ThingsBoard, once per backend, and only when one of those four is called: the
    #: "no" answer is the expensive one, because it has to look at every ``formal_in``.
    _PORTS_CARRY_DEPENDENCE = (
        "MATCH (b:JBodyNode)-[r:J_DDG|J_CDG|J_PARAM_IN|J_PARAM_OUT|J_SUMMARY]->(m:JBodyNode) "
        "WHERE b.id STARTS WITH $prefix AND m.id STARTS WITH $prefix AND b.kind = 'formal_in' "
        "RETURN count(r) > 0 AS ok"
    )

    @cached_property
    def _ports_carry_dependence(self) -> bool:
        """See :meth:`JavaAnalysisBackend._ports_carry_dependence`. Asked of the attached graph on
        first use rather than at attach: a caller who never asks a value-flow question never pays
        for it, and a caller who does pays once."""
        return bool(self._run(self._PORTS_CARRY_DEPENDENCE, prefix=self._scope_prefix)[0]["ok"])

    # -----[ application / whole-program ]-----
    def get_application_view(self) -> JApplication:
        return self.application

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        return self.application.symbol_table

    def get_compilation_units(self) -> List[JCompilationUnit]:
        return list(self.application.symbol_table.values())

    def get_java_file(self, qualified_class_name: str) -> str | None:
        return self._idx[1].get(qualified_class_name)

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        return self.application.symbol_table[file_path]

    def get_system_dependency_graph(self) -> list[JGraphEdges]:
        """The wire call graph (``JApplication.call_graph``), one :class:`JCallGraphEdge` per edge."""
        return self.application.call_graph

    # -----[ call graph ]-----
    @staticmethod
    def _detail(klass: str, c: JCallable) -> JMethodDetail:
        return JMethodDetail(method_declaration=c.declaration, klass=klass, method=c)

    def _node_of(self, node_id: str) -> Tuple[str, JMethodDetail]:
        """The (node key, method detail) a call-graph endpoint id resolves to. Every endpoint the
        projection writes is homed on the tree; one that is not is a defect, surfaced rather than
        skipped — named by the signature and module key its id spells, never by the id (E6), in the
        same words the in-memory backend uses."""
        try:
            t, c = self._idx[2][node_id]
        except KeyError:
            raise CodeanalyzerExecutionException(unhomed_endpoint(node_id)) from None
        return f"{t.qualified_name}.{c.signature}", self._detail(t.qualified_name, c)

    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) the call graph keyed by ``"<type fqn>.<signature>"`` (J-1): node attrs
        ``method_detail`` / ``kind="callable"``; edge attrs ``type="CALL_DEP"``, ``weight``,
        ``calling_lines``. Edges to external targets are dropped (see the module docstring)."""
        if self._call_graph is not None:
            return self._call_graph
        cg = nx.DiGraph()
        lines = CallingLines()
        for edge in self.application.call_graph:
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
    # The same resolution the in-memory backend runs (``JCodeanalyzer._symbol_table_call_graph``
    # and friends), over the same models: it reads nothing but ``get_class`` / ``get_method`` and
    # the callable index, so the two must agree edge for edge on the same symbol table.
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
        for owner, source in self._idx[2].values():
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
        """The callable, or ``None``. Two fields differ from the in-memory backend's, because the
        projection differs: ``code`` is the whole **declaration** (the graph carries one line range
        per callable and no ``body_span``), where the in-memory backend's is the body block; and
        ``body`` holds the ``call`` nodes **only** — about 30% of the graph's body nodes (4,006 of
        daytrader8's 13,436) — which is what ``call_sites`` is a view over."""
        klass = self.get_class(qualified_class_name)
        return klass.callables.get(qualified_method_name) if klass is not None else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[JCallableParameter]:
        """The parameters the callable's ``parameters_json`` carries — the analyzer's own
        serialisation, so these round-trip exactly (there is no ``:JParameter`` node in 3.0.1, and
        none is needed)."""
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
        """Every non-code artifact, keyed by repo-relative path. ``JArtifact.text_truncated`` has no
        home on the shared model and is not carried; read it off ``JApplication.artifacts``."""
        return {
            path: PyArtifact(**a.model_dump(exclude={"config_keys", "text_truncated"}), config_keys=[PyConfigKey(**ck.model_dump()) for ck in a.config_keys])
            for path, a in self.application.artifacts.items()
        }

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        """Every declared dependency, optionally filtered. The Maven ``group`` coordinate has no home
        on the shared model and is not carried; read it off ``JApplication.dependencies``."""
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
        """The method's javadoc — **narrower than the ABC's "the comments in a method"**: the graph
        keeps one ``docstring`` per declaration and no other comment, so a non-javadoc comment in
        the body is not here (see the module docstring). A javadoc-only subset is still a real
        answer under this name, which is why this one narrows where the two file-keyed accessors
        refuse (J-16). ``[]`` both for a method with no javadoc and for a missing one, as on the
        in-memory backend."""
        method = self.get_method(qualified_class_name, method_signature)
        return method.comments if method is not None else []

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """The class's javadoc, narrower than the ABC's "the comments in a class" in exactly the
        way :meth:`get_comments_in_a_method` is (J-16)."""
        klass = self.get_class(qualified_class_name)
        return klass.comments if klass is not None else []

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """Raises: the projection carries no file-level comments at all, so every answer would be
        an empty list reading as "this file has no comments" (D7)."""
        raise CodeanalyzerExecutionException(_COMMENTS_UNAVAILABLE.format(app=self.application_name))

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """Raises, as :meth:`get_comment_in_file` does: the docstrings that *are* projected are a
        strictly smaller set than "every comment", and returning them under this name would be a
        silent partial rather than an empty one."""
        raise CodeanalyzerExecutionException(_COMMENTS_UNAVAILABLE.format(app=self.application_name))

    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        """The javadoc of each file's *declarations* — every declaration the projection gives a
        ``docstring``: types, their callables, fields, enum constants and record components. That
        is the only comment text in the graph. The in-memory backend reads the compilation unit's
        own comment list instead, which additionally holds every file-level javadoc (a licence
        header, say) and nothing per declaration; the two therefore report different sets for the
        same file.
        """
        out: Dict[str, List[JComment]] = {}
        for name, t in self._types.items():
            path = self._idx[1][name]
            javadoc = list(t.comments)
            javadoc += [c for member in t.callables.values() for c in member.comments]
            javadoc += [c for f in t.fields.values() for c in f.comments]
            javadoc += [c for k in t.enum_constants for c in k.comments]
            javadoc += [c for rc in t.record_components for c in rc.comments]
            if javadoc:
                out.setdefault(path, []).extend(javadoc)
        return out

    def remove_all_comments(self, src_code: str) -> str:
        raise NotImplementedError("This function is not implemented yet.")
