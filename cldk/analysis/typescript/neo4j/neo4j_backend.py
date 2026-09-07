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

"""Neo4j-backed TypeScript analysis backend (read-only Cypher client) on the codeanalyzer-typescript
1.2.0 graph vocabulary.

A drop-in alternative to :class:`TSCodeanalyzer`: the same query surface, every method answered by
Cypher over a live graph that ``codeanalyzer-typescript --emit neo4j`` populated out of band. This
class never writes and needs neither the analyzer binary nor the sources.

**The graph it reads** (``schema.neo4j.json`` at the 1.2.0 tag; ``main`` renames nothing):
``:Application {id: can://typescript/<app>}`` anchors the application and stamps
``analyzer_version``; every project node carries a ``can://`` ``id`` under the merge label
``CanNode`` -- ``TSModule`` (``name`` holds the file key), ``TSClass``/``TSInterface``/``TSEnum``/
``TSTypeAlias``/``TSNamespace``, ``TSCallable`` (all seven kinds; anonymous ones also
``TSAnonymousCallable``), ``TSField``, ``TSBodyNode``, ``TSExternal``; containment is
``TS_HAS_MODULE`` / ``TS_DECLARES`` / ``TS_HAS_METHOD`` / ``TS_HAS_FIELD``; calls are ``TS_CALLS
{weight, prov}`` and a call site is a ``TSBodyNode {kind:'call'}`` under ``TS_HAS_BODY_NODE``
resolving over ``TS_RESOLVES_TO``.

**Scope (TS-3).** A signature is not application-stamped, so every statement that could match
another application's node carries the two-prefix predicate :func:`_scoped` spells --
``can://typescript/<app>/`` and ``can://javascript/<app>/`` -- or is keyed by an id that embeds the
application, or walks out from the ``:Application`` anchor. There is no ``_module`` property to
fall back on (retired on ``main``, #166).

**Seek labels (measured on the superset graph).** What decides the anchor is how narrow the
predicate is, not what shape it has. Statements scoped by the two *application* prefixes -- nearly
every node -- anchor on the specific label alone (``:TSCallable``): 11,085 callables scan in ~8 ms,
and ``:CanNode`` turns the two-prefix predicate into a slower range-seek union (``resolve_callable``
re-measured this at 24-28 ms bare against 44-51 ms on ``:CanNode``). Id-equality point lookups
anchor on ``:CanNode:<Label>``: the ``CanNode.id`` uniqueness constraint makes them a 1.5 ms
unique-index seek instead of a label scan. So does a **per-module** prefix, which is the same
narrowness by another route: ``locate_many`` over 40 positions costs 346-358 ms on ``:TSCallable``
and 97-102 ms on ``:CanNode:TSCallable`` (3.5x, same 113 rows). ``:TSCanNode`` was faster still
there and is refused on correctness -- it is the per-namespace marker, so it drops every
``can://javascript/<app>/`` module.

**Round trips.** A declaration's whole containment subtree is fetched in one statement
(``_SUBTREE``: a variable-length walk over the containment types from the anchored roots), so a
bulk accessor pays one root fetch plus one subtree fetch however many modules, classes and
callables it walks -- never one statement per parent.

**Lossiness** relative to the in-memory backend (the projection's, not this client's; see
:mod:`reconstruct` for the per-node detail): parameters, comments, type parameters, overloads,
bodies and the L3/L4 graphs are not on ``:TSCallable``; enum member values, imports and exports
are not projected at all; call sites keep lines and the resolved callee only; the anonymous-callable
index is keyed by the tree node's own id rather than the analyzer's older compatibility key;
``config_reads`` are not projected (see :meth:`get_unresolved_config_reads`); an unresolved
call site contributes ``""`` to :meth:`get_call_targets` where the in-memory backend contributes
the call's ``method_name``; the extends/implements split is read off ``TS_EXTENDS``/``TS_IMPLEMENTS``
rather than the never-written ``implements_types`` property, so it covers resolved in-repo bases
only and raises when the relationship type is absent (:meth:`_heritage`); and
:meth:`get_application_view` leaves the L4 ``param_in``/``param_out`` overlay empty because 2.5a
reads no dataflow at all (2.5b's Task 2 does). One more arrived with the addressing surface:
``:TSModule`` carries no ``source`` and ``:TSBodyNode`` no text, so :meth:`locate` at module scope
answers ``""`` plus a ``module_source_unavailable`` diagnostic and :meth:`get_source` refuses a
body-node id outright, where the local backend answers both.

**And one that is a value divergence rather than an absence**, which is why it is stated loudly:
codeanalyzer-typescript 1.3.0 projects ``:TSCallable.code`` **one line short of the callable's own
span** -- the graph text is the in-memory text minus its final ``"\\n}"`` (measured: 544 characters
against 546 for the sample app's ``src/index.main``), while ``start_line``/``end_line`` on the same
node are correct. Every accessor that hands a caller a callable's source over this backend is
affected: :meth:`get_source`, :meth:`get_method_bodies`, :meth:`describe`, :meth:`locate` /
:meth:`locate_many` and ``TSCallable.code`` on a rebuilt node. Filed upstream as
codellm-devkit/codeanalyzer-typescript#179; four ``xfail(strict=True)`` marks in
``tests/analysis/typescript/test_typescript_bulk_parity_live.py`` are pinned to it, so they fail
loudly the day it is fixed rather than passing quietly.

One thing is *less* lossy here than in the Python twin: a TypeScript ``call`` body node carries
``callee`` as a property, so ``LocateResult.body.callee`` is populated over Neo4j too.

One more is the emitter's: two declarations of one name (TypeScript
declaration merging -- ``const X = …`` + ``interface X``, ``const X = …`` + ``type X``, ``type X`` +
a field ``X``) share one id, so ``MERGE`` collapses them onto one node carrying both labels and the
``kind`` of whichever was written last. Such a node is rebuilt as the facet the containment edge
declares (``TS_DECLARES`` names a type or callable; its labels say which) and the other facet's
members under it are lost; a node whose labels cannot name the facet is raised as the defect it is
(three merged nodes on the superset graph).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from functools import cached_property
from typing import Any, Dict, FrozenSet, List, Sequence, Set, Tuple

import networkx as nx

from cldk.analysis.commons import artifacts as shared  # the artifact layer every analyzer projects identically
from cldk.analysis.commons.backend import semver
from cldk.analysis.commons.bounds import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_PATHS,
    DEFAULT_PAGE_SIZE,
    EdgeOrder,
    check_depth,
    check_distinct_endpoints,
    check_max_nodes,
    check_max_paths,
    check_page_size,
    cursor_params,
    encode_cursor,
    keyset_where,
)
from cldk.analysis.commons.graphs import cone_sinks, flow_path, slice_resolved
from cldk.analysis.commons.keys import body_key_column, module_key_of, resolve_module_key
from cldk.analysis.commons.resolve import CallableCandidate, resolve_callable_signature, resolve_value_name, resolve_within
from cldk.analysis.commons.results import (
    BodyRef,
    CallableRef,
    Diagnostic,
    EdgePage,
    EntrypointCoverage,
    FlowPaths,
    LocateResult,
    ModuleRef,
    Slice,
    SliceNode,
    Span,
    TypeRef,
)
from cldk.analysis.typescript.backend import (
    CDG_ORDER,
    CFG_ORDER,
    DDG_ORDER,
    SDG_REL_PATTERN,
    VIA,
    TSAnalysisBackend,
    ts_body_node_kind,
    ts_module_dotted,
)
from cldk.analysis.typescript.neo4j import reconstruct as R
from cldk.analysis.typescript.neo4j.reconstruct import CALLABLE_KINDS, TYPE_KINDS, TYPE_LABEL_KINDS
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
from cldk.models.typescript import (
    TSApplication,
    TSArtifact,
    TSCallable,
    TSCdgEdge,
    TSCfgEdge,
    TSDdgEdge,
    TSCallableOverview,
    TSCallGraphEdge,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSClassOverview,
    TSConfigUse,
    TSDecorator,
    TSDependency,
    TSEntrypointReport,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSVariableDeclaration,
)
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException, GraphSchemaMismatch

logger = logging.getLogger(__name__)


def _scoped(var: str) -> str:
    """The application-scope predicate for node variable ``var``, spelled once so it cannot drift:
    the two id prefixes as an ``OR`` (which plans as a seek union), never ``any(p IN $prefixes …)``
    (which plans as a label scan). Bound from :attr:`TSNeo4jBackend._scope_params`."""
    return f"({var}.id STARTS WITH $p1 OR {var}.id STARTS WITH $p2)"


def _vertex(var: str, *, escape: bool = False) -> str:
    """The projection every call-graph vertex is read back through, for node variable ``var``.

    Written once so a neighbour, a cone member and a path node describe the same vertex
    identically; :meth:`TSNeo4jBackend._call_vertex` is the one place that reads the row back.
    ``kind`` is what says which of TypeScript's three call-graph shapes a row is: a callable carries
    ``signature``/``start_line``, a module ``name`` (its file key) and ``start_line``, an external
    ``module``/``name`` and neither.

    ``escape`` doubles the braces, for a statement that is itself ``.format()``-ed later for its
    ``depth`` bound -- the alternative was two copies of the projection, which is the drift this
    helper exists to prevent.
    """
    open_, close = ("{{", "}}") if escape else ("{", "}")
    return f"{open_}kind: {var}.kind, signature: {var}.signature, name: {var}.name, ref: {var}.id, line: {var}.start_line, module: {var}.module{close}"


#: The kinds a method owner may have -- alongside the label, so a declaration-merged node carrying
#: ``TSClass``/``TSInterface`` with another declaration's kind is not an owner.
_OWNER_KINDS = ["class", "interface"]

#: A child row of the containment subtree: (relationship type, child properties, edge properties).
_Child = Tuple[str, Dict[str, Any], Dict[str, Any]]


class TSNeo4jBackend(TSAnalysisBackend):
    """Query the application view of a TypeScript project over Neo4j (Cypher), read-only.

    Args:
        neo4j_uri: Bolt URI of the Neo4j server.
        neo4j_username / neo4j_password: Credentials (read-only is sufficient).
        neo4j_database: Database name (None ⇒ server default).
        application_name: The ``--app-name`` the graph was emitted with; the anchor is
            ``:Application {id: can://typescript/<application_name>}`` on both namespaces.
    """

    #: Relationship types every supported graph has; a graph missing any was emitted by another
    #: generation (0.4.3 has none of them) and is refused at attach.
    _REQUIRED_RELATIONSHIP_TYPES: FrozenSet[str] = frozenset({"TS_HAS_MODULE", "TS_HAS_METHOD", "TS_HAS_BODY_NODE", "TS_CALLS"})
    #: The oldest codeanalyzer-typescript whose graph this backend serves. 1.2.0 introduced the
    #: ``can://`` id grammar and the body-node shape every statement here reads; the floor is
    #: **1.3.0** because that release is the first whose L4 port lattice is wired to the statement
    #: DDG (cants#169), whose body nodes and parameters carry ``id`` (#165) and which retired
    #: ``_module`` (#166) -- the three facts the query surface is built on. A 1.2.0 graph has the
    #: vocabulary but answers those statements with silent empties, so it is refused, not served.
    _ANALYZER_FLOOR = (1, 3, 0)
    #: Every relationship type the attached database declares, recorded by :meth:`_probe_schema`.
    #: A type absent from it is absent from the graph, so an accessor that can only be answered
    #: over that type raises naming the gap instead of returning an empty the caller would read as
    #: a fact (:meth:`_heritage`). The class-level default is for the ``object.__new__`` seam.
    _relationship_types: FrozenSet[str] = frozenset()
    #: Set by :meth:`_probe_schema`; the class-level ``None`` is for the ``object.__new__`` seam.
    _analyzer_version: Tuple[int, int, int] | None = None
    #: Set by :meth:`_probe_resolution_edges`; the class-level default is for the ``object.__new__``
    #: seam the unit tests build instances through.
    _has_resolution_edges: bool = False
    #: The Cypher generation the call-graph walks need. A **quantified path pattern** --
    #: ``(a) ((x)-[:R]->(y) WHERE ...){0,n} (m)``, the only way to put a predicate on *every* hop of
    #: a walk -- arrives in Neo4j 5.9. Recorded at attach (:meth:`_read_server_version`) and
    #: enforced at the calls that need it (:meth:`_require_quantified_paths`), never at attach, so
    #: an older server keeps serving every other accessor on this class.
    _QUANTIFIED_PATH_MIN_SERVER = (5, 9)
    #: Set by :meth:`_read_server_version`; ``None`` means *unknown*, which blocks nothing.
    _server_version: Tuple[int, ...] | None = None
    _call_graph: nx.DiGraph | None = None
    _module_ids: Dict[str, str] = {}

    def __init__(self, neo4j_uri: str, neo4j_username: str, neo4j_password: str, neo4j_database: str | None = None, application_name: str | None = None) -> None:
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as e:  # pragma: no cover - import guard
            raise CodeanalyzerExecutionException("The Neo4j backend requires the 'neo4j' driver. Install it with `pip install neo4j` (or `pip install cldk[neo4j]`).") from e
        self._init_with_driver(GraphDatabase.driver(neo4j_uri, auth=(neo4j_username, neo4j_password)), application_name=application_name, neo4j_database=neo4j_database)

    @classmethod
    def _from_driver(cls, driver: Any, *, application_name: str | None = None, neo4j_database: str | None = None) -> "TSNeo4jBackend":
        """Construct from an already-built driver -- the seam tests inject a fake driver through."""
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
        self._module_ids = self._load_module_keys()
        self._modules: List[str] = list(self._module_ids)
        self._has_resolution_edges = self._probe_resolution_edges()
        self._call_graph = None

    # -----[ scope ]-----
    @property
    def _app_id(self) -> str:
        return f"can://typescript/{self.application_name}"

    @property
    def _scope_prefixes(self) -> List[str]:
        """``can://typescript/<app>/`` and ``can://javascript/<app>/`` (TS-3); the trailing slash
        keeps ``app`` from matching ``app-b``."""
        return [f"can://typescript/{self.application_name}/", f"can://javascript/{self.application_name}/"]

    @property
    def _scope_params(self) -> Dict[str, str]:
        """The parameters :func:`_scoped` binds."""
        p1, p2 = self._scope_prefixes
        return {"p1": p1, "p2": p2}

    @cached_property
    def _module_set(self) -> FrozenSet[str]:
        return frozenset(self._modules)

    def _module_key(self, node_id: str) -> str:
        """The repo-relative module key a node's id embeds, verified against the application's
        module keys (F4) -- never split, never guessed. A miss reloads the keys once (the graph is
        not ours; a re-emit may have added a module) and then raises."""
        for _ in range(2):
            for prefix in self._scope_prefixes:
                if node_id.startswith(prefix):
                    try:
                        return module_key_of(node_id, prefix, self._module_set)
                    except KeyError:
                        break
            self._module_ids = self._load_module_keys()
            self._modules = list(self._module_ids)
            self.__dict__.pop("_module_set", None)
        raise CodeanalyzerExecutionException(
            f"The node {node_id!r} of application {self.application_name!r} belongs to none of the {len(self._module_set)} module keys the graph holds for it, "
            "even after reloading them. Re-attach to the graph."
        )

    # -----[ lifecycle ]-----
    def close(self) -> None:
        if self._session_obj is not None:
            try:
                self._session_obj.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            self._session_obj = None
        self._driver.close()

    def __enter__(self) -> "TSNeo4jBackend":
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
        """G5: the relationship-type fingerprint, then the analyzer generation the ``:Application``
        anchor stamps against :attr:`_ANALYZER_FLOOR`. Below the floor, absent, or unreadable is
        refused naming what was found -- a served mismatch would read as "no callables"."""
        found = {r["relationshipType"] for r in self._run("CALL db.relationshipTypes()")}
        missing = self._REQUIRED_RELATIONSHIP_TYPES - found
        if missing:
            raise GraphSchemaMismatch(expected=set(self._REQUIRED_RELATIONSHIP_TYPES), found=found, missing=missing)
        rows = self._run("OPTIONAL MATCH (a:Application {id: $app_id}) RETURN count(a) AS n, a.analyzer_version AS v", app_id=self._app_id)
        present = bool(rows and rows[0].get("n"))
        raw = rows[0].get("v") if rows else None
        version = semver(raw)
        floor = ".".join(map(str, self._ANALYZER_FLOOR))
        if version is None or version < self._ANALYZER_FLOOR:
            if not present:
                what = "has no :Application node"
            elif version:
                what = f"was emitted by codeanalyzer-typescript {raw}"
            elif raw:
                what = f"reports analyzer_version {raw!r}"
            else:
                what = "has an :Application node that carries no analyzer_version"
            raise GraphSchemaMismatch(
                expected=set(self._REQUIRED_RELATIONSHIP_TYPES),
                found=found,
                missing=set(),
                message=f"The graph for application {self.application_name!r} {what}; this backend needs a graph emitted by codeanalyzer-typescript {floor} or newer.",
            )
        self._analyzer_version = version
        self._relationship_types = frozenset(found)
        self._server_version = self._read_server_version()

    def _read_server_version(self) -> "Tuple[int, ...] | None":
        """The attached server's version as an int tuple, or ``None`` when it cannot be read.

        Read here because attach is the one place already talking to the server before any accessor
        runs, and deliberately **not** acted on here: see :attr:`_QUANTIFIED_PATH_MIN_SERVER`.

        ``None`` means *unknown*, and an unknown version blocks nothing: a server that will not
        answer ``dbms.components()`` (the fake driver the unit tests inject, a deployment that
        restricts the procedure) is not evidence of an old one, and refusing on that basis would be
        a guess dressed as a check. If such a server really is pre-5.9, its own parser reports the
        syntax error, which is the same outcome as before this check existed.
        """
        try:
            rows = self._run("CALL dbms.components() YIELD versions RETURN versions[0] AS v")
        except Exception:  # noqa: BLE001 - an unreadable version is "unknown", never fatal
            return None
        raw = rows[0].get("v") if rows else None
        if not isinstance(raw, str):
            return None
        return tuple(int(x) for x in raw.split("-", 1)[0].split(".") if x.isdigit()) or None

    def _require_quantified_paths(self, accessor: str) -> None:
        """Refuse, naming the accessor and the requirement, on a server too old for a quantified
        path pattern.

        The pattern is what lets a call-graph walk be labelled and application-scoped at *every*
        hop rather than only at its endpoints (see :attr:`_REACHES` and :attr:`_CONE`), and it
        arrives in Neo4j 5.9. Enforced per accessor rather than at attach, so a caller who never
        asks a call-graph walk of an older server keeps working. The reference server reports
        5.26.30, so this fires on no supported deployment; it exists for one that is not.
        """
        if self._server_version is not None and self._server_version < self._QUANTIFIED_PATH_MIN_SERVER:
            got = ".".join(str(n) for n in self._server_version)
            floor = ".".join(str(n) for n in self._QUANTIFIED_PATH_MIN_SERVER)
            raise CodeanalyzerExecutionException(
                f"{accessor} compiles to a quantified path pattern, which needs Neo4j server {floor} or newer; the attached "
                f"server reports {got}. Only the hop-scoped call-graph walks (reaches, backward_cone) need the newer "
                "pattern; every other accessor on this backend runs on any 5.x."
            )

    def _load_module_keys(self) -> Dict[str, str]:
        """``file key -> module id`` for the application's modules (``TSModule.name`` holds the key)."""
        rows = self._run("MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) RETURN m.name AS k, m.id AS id", app_id=self._app_id)
        return {r["k"]: r["id"] for r in rows}

    # =====================================================================================
    # Containment subtree: one statement for the roots, one for everything beneath them.
    # =====================================================================================
    #: Appended to ``MATCH <anchor>`` where the anchor binds ``root``: every containment edge
    #: beneath every root, at any depth, plus the decorator edges, as ``(parent id, child)`` rows.
    _SUBTREE = (
        "MATCH (root)-[:TS_DECLARES|TS_HAS_METHOD|TS_HAS_FIELD*0..]->(par)-[r:TS_DECLARES|TS_HAS_METHOD|TS_HAS_FIELD|TS_DECORATED_BY]->(n) "
        "RETURN par.id AS pk, type(r) AS rel, properties(n) AS p, properties(r) AS e, labels(n) AS labels"
    )

    def _fetch(self, anchor: str, **params: Any) -> Tuple[List[Dict[str, Any]], Dict[str, List[_Child]]]:
        """The properties of every ``root`` the anchor pattern binds, and the containment subtree
        beneath them indexed by parent id. ``anchor`` is a scoped ``MATCH`` body binding ``root``."""
        roots = [{**r["p"], "_labels": r["labels"]} for r in self._run(f"MATCH {anchor} RETURN properties(root) AS p, labels(root) AS labels", **params)]
        children: Dict[str, List[_Child]] = defaultdict(list)
        for r in self._run(f"MATCH {anchor} " + self._SUBTREE, **params):
            children[r["pk"]].append((r["rel"], {**r["p"], "_labels": r["labels"]}, r["e"] or {}))
        return roots, children

    def _decorators(self, node_id: str, children: Dict[str, List[_Child]]) -> List[TSDecorator]:
        return [R.decorator(p, e) for rel, p, e in children.get(node_id, []) if rel == "TS_DECORATED_BY"]

    def _fields(self, node_id: str, children: Dict[str, List[_Child]]) -> Dict[str, TSClassAttribute]:
        rows = sorted((p for rel, p, _ in children.get(node_id, []) if rel == "TS_HAS_FIELD"), key=lambda p: (p.get("start_line") or 0, p.get("name") or ""))
        return {R.child_key(node_id, p): R.field(p, self._decorators(p["id"], children)) for p in rows}

    def _declared(self, node_id: str, children: Dict[str, List[_Child]]) -> Tuple[Dict[str, Any], Dict[str, TSCallable]]:
        """The ``TS_DECLARES`` children split into ``(types, callables)``, each keyed the analyzer's way."""
        types: Dict[str, Any] = {}
        callables: Dict[str, TSCallable] = {}
        for rel, p, _ in children.get(node_id, []):
            if rel != "TS_DECLARES":
                continue
            kind = p["kind"]
            if kind not in TYPE_KINDS and kind not in CALLABLE_KINDS:
                # A declaration-merged node (see the module docstring): TS_DECLARES says type or
                # callable; the labels must name exactly one type facet, else it is a defect.
                facets = [TYPE_LABEL_KINDS[label] for label in p.get("_labels", []) if label in TYPE_LABEL_KINDS]
                if len(facets) != 1:
                    raise CodeanalyzerExecutionException(
                        self._merged_defect(p, "is declared by its parent but its kind is neither a type nor a callable kind, and its labels do not name one type facet")
                    )
                kind = facets[0]
            if kind in TYPE_KINDS:
                types[R.child_key(node_id, p)] = self._type({**p, "kind": kind}, children)
            else:
                callables[R.child_key(node_id, p)] = self._callable(p, children)
        return types, callables

    @staticmethod
    def _merged_defect(props: Dict[str, Any], what: str) -> str:
        """The message for a node the emitter merged from two declarations. Names the node by
        signature/name, labels and kind -- never by its id (E6)."""
        return f"node {props.get('signature') or props.get('name')!r} (labels {sorted(props.get('_labels', []))}, kind {props.get('kind')!r}) {what}: codeanalyzer-typescript minted one id for two declarations"

    def _methods(self, node_id: str, children: Dict[str, List[_Child]]) -> Dict[str, TSCallable]:
        return {R.child_key(node_id, p): self._callable(p, children) for rel, p, _ in children.get(node_id, []) if rel == "TS_HAS_METHOD"}

    def _callable(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> TSCallable:
        types, callables = self._declared(props["id"], children)
        return R.callable_(props, decorators=self._decorators(props["id"], children), callables=callables, types=types)

    def _type(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> Any:
        nid, kind = props["id"], props.get("kind")
        if kind == "class":
            return R.class_(props, callables=self._methods(nid, children), fields=self._fields(nid, children), decorators=self._decorators(nid, children))
        if kind == "interface":
            return R.interface(props, callables=self._methods(nid, children), fields=self._fields(nid, children))
        if kind == "enum":
            return R.enum(props, fields=self._fields(nid, children))
        if kind == "type_alias":
            return R.type_alias(props)
        if kind == "namespace":
            types, functions = self._declared(nid, children)
            return R.namespace(props, types=types, functions=functions, fields=self._fields(nid, children))
        raise CodeanalyzerExecutionException(self._merged_defect(props, "is matched as a type but its kind is none of the five type kinds"))

    def _build_module(self, props: Dict[str, Any], children: Dict[str, List[_Child]]) -> TSModule:
        types, functions = self._declared(props["id"], children)
        return R.module(props, types=types, functions=functions, fields=self._fields(props["id"], children))

    # The kind predicate alongside the label keeps a declaration-merged node (two labels, one kind)
    # out of the accessor for the facet it is not; ``cannode_kind`` is indexed on every generation.
    def _types_by_signature(self, label: str) -> Dict[str, Any]:
        roots, children = self._fetch(f"(root:{label}) WHERE {_scoped('root')} AND root.kind = $kind", kind=TYPE_LABEL_KINDS[label], **self._scope_params)
        return {p["signature"]: self._type(p, children) for p in roots}

    def _type_by_signature(self, label: str, signature: str) -> Any:
        roots, children = self._fetch(
            f"(root:{label} {{signature: $sig}}) WHERE {_scoped('root')} AND root.kind = $kind", sig=signature, kind=TYPE_LABEL_KINDS[label], **self._scope_params
        )
        return self._type(roots[0], children) if roots else None

    # =====================================================================================
    # application / whole-program
    # =====================================================================================
    def get_application_view(self) -> TSApplication:
        """The symbol table, the call graph as wire edges, the externals, the anonymous index and
        the repository-artifact layer.

        The artifact layer comes from the same three accessors a caller would reach for
        (:meth:`get_artifacts` -- config keys included -- :meth:`get_dependencies`,
        :meth:`get_config_uses`), retyped from the shared ``Py*`` models onto the wire's ``TS*``
        ones; a dependency's ``ecosystem`` has no field on ``TSDependency`` and is dropped here
        (:meth:`get_dependencies` keeps it).

        Three overlays stay at their model defaults, deliberately, and each for its own reason:

        * ``param_in``/``param_out`` -- the L4 dataflow overlay. Leg 2.5a reads **none** of it
          (no ``TS_PARAM_IN``/``TS_PARAM_OUT``/``TS_DDG``/``TS_SUMMARY`` statement exists on this
          class); the whole dataflow surface, this overlay with it, is leg 2.5b.
        * ``config_reads`` -- not projected at all (see :meth:`get_unresolved_config_reads`, which
          raises rather than answer ``[]`` here).
        * ``unresolved_imports`` -- ``TS_UNRESOLVED_IMPORT`` is in the projection but no accessor
          on this surface reads it, so the view does not invent one.
        """
        externals = {e.id: e for e in self.get_external_symbols().values()}
        return TSApplication(
            id=self._app_id,
            symbol_table=self.get_symbol_table(),
            call_graph=[TSCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or []), weight=r["weight"] or 1) for r in self._call_rows()],
            artifacts={a.path: TSArtifact.model_validate(a.model_dump()) for a in self.get_artifacts().values()},
            dependencies=[TSDependency.model_validate(d.model_dump(exclude={"ecosystem"})) for d in self.get_dependencies()],
            config_uses=[TSConfigUse.model_validate(u.model_dump()) for u in self.get_config_uses()],
            external_symbols=externals,
            synthesized_callables=self.get_synthesized_callables(),
        )

    def get_symbol_table(self) -> Dict[str, TSModule]:
        roots, children = self._fetch("(:Application {id: $app_id})-[:TS_HAS_MODULE]->(root:TSModule)", app_id=self._app_id)
        return {p["name"]: self._build_module(p, children) for p in roots}

    def get_modules(self) -> List[TSModule]:
        return list(self.get_symbol_table().values())

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        module_id = self._module_ids.get(file_path)
        if module_id is None:
            return None
        roots, children = self._fetch("(root:CanNode:TSModule {id: $id})", id=module_id)
        return self._build_module(roots[0], children) if roots else None

    def get_typescript_file(self, qualified_name: str) -> str | None:
        rows = self._run(
            f"MATCH (n:TSCallable|TSClass|TSInterface|TSEnum|TSTypeAlias|TSNamespace {{signature: $sig}}) WHERE {_scoped('n')} RETURN n.id AS id LIMIT 1",
            sig=qualified_name,
            **self._scope_params,
        )
        return self._module_key(rows[0]["id"]) if rows else None

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """The application's external *symbols* -- ``<app-id>/@external/<module>/<name>``, what
        ``analysis.json``'s ``external_symbols`` holds -- keyed ``"<module>.<name>"``. An external is
        homed on the application whichever module called it, so the application scope plus the
        ``@external`` segment is the scope. The scope is the **two** prefixes (TS-3), not the
        typescript one alone: superset-frontend homes all 3,171 of its externals under
        ``can://typescript/``, but nothing in the id grammar stops a ``.js`` caller's external
        landing under ``can://javascript/``, and a single-prefix reading would drop it silently.
        The graph also holds one nameless ``:TSExternal`` per *package* (``@external/<module>``, the
        target of ``TS_PROVIDES`` / ``TS_UNRESOLVED_IMPORT``); those are not symbols and are not
        returned here."""
        rows = self._run(f"MATCH (e:TSExternal) WHERE {_scoped('e')} AND e.id CONTAINS '/@external/' AND e.name IS NOT NULL RETURN properties(e) AS p", **self._scope_params)
        return {f"{r['p']['module']}.{r['p']['name']}": R.external(r["p"]) for r in rows}

    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """The application's anonymous callables (``:TSAnonymousCallable`` tree nodes), keyed by
        their own id: the analyzer's compatibility index (older key -> tree id) is JSON-only."""
        rows = self._run(f"MATCH (a:TSAnonymousCallable) WHERE {_scoped('a')} RETURN properties(a) AS p", **self._scope_params)
        return {r["p"]["id"]: R.synthesized(r["p"]) for r in rows}

    # =====================================================================================
    # call graph
    # =====================================================================================
    def _call_rows(self) -> List[Dict[str, Any]]:
        """Every ``TS_CALLS`` edge whose source is this application's, with what each endpoint needs
        to be keyed the way every other accessor keys it (see :meth:`_graph_key`)."""
        return self._run(
            f"MATCH (s)-[r:TS_CALLS]->(t) WHERE {_scoped('s')} AND {_scoped('t')} "
            "RETURN s.id AS src, s.kind AS src_kind, s.signature AS src_sig, s.module AS src_module, s.name AS src_name, "
            "t.id AS dst, t.kind AS dst_kind, t.signature AS dst_sig, t.module AS dst_module, t.name AS dst_name, r.weight AS weight, r.prov AS prov",
            **self._scope_params,
        )

    def _graph_key(self, node_id: str, kind: str | None, signature: str | None, module: str | None, name: str | None) -> Tuple[str, str]:
        """``(node key, kind)`` as the in-memory backend keys them: a module by its file key, an
        external by ``"<module>.<name>"``, a type or callable by signature."""
        if kind == "module":
            return self._module_key(node_id), "module"
        if kind == "external":
            if not name:
                raise CodeanalyzerExecutionException(
                    f"a TS_CALLS endpoint is the package-level external {module!r}, which has no member name to key it by: codeanalyzer-typescript emitted an endpoint this backend cannot address"
                )
            return f"{module}.{name}", "external"
        if not signature:
            raise CodeanalyzerExecutionException(
                f"a TS_CALLS endpoint of kind {kind!r} named {name!r} carries no signature to key it by: codeanalyzer-typescript emitted an endpoint this backend cannot address"
            )
        return signature, kind if kind in TYPE_KINDS else "callable"

    def get_call_graph(self) -> nx.DiGraph:
        """Cached. Nodes carry ``id`` and ``kind`` (module callers and class callees kept, TS-11);
        edges carry ``type="CALL_DEP"``, ``weight`` and ``provenance``."""
        if self._call_graph is not None:
            return self._call_graph
        graph = nx.DiGraph()
        for r in self._call_rows():
            src, src_kind = self._graph_key(r["src"], r["src_kind"], r["src_sig"], r["src_module"], r["src_name"])
            dst, dst_kind = self._graph_key(r["dst"], r["dst_kind"], r["dst_sig"], r["dst_module"], r["dst_name"])
            graph.add_node(src, id=r["src"], kind=src_kind)
            graph.add_node(dst, id=r["dst"], kind=dst_kind)
            graph.add_edge(src, dst, type="CALL_DEP", weight=r["weight"] or 1, provenance=tuple(r["prov"] or []))
        self._call_graph = graph
        return graph

    def get_call_graph_json(self) -> str:
        return self.get_application_view().model_dump_json()

    def _resolve_signature(self, class_or_sig: str, member: str | None = None) -> str:
        if member is None:
            return class_or_sig
        rows = self._run(
            f"MATCH (o:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('o')} AND o.kind IN $kinds MATCH (o)-[:TS_HAS_METHOD]->(m:TSCallable {{name: $name}}) RETURN m.signature AS sig LIMIT 1",
            sig=class_or_sig,
            name=member,
            kinds=_OWNER_KINDS,
            **self._scope_params,
        )
        return rows[0]["sig"] if rows else f"{class_or_sig}.{member}"

    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        graph = self.get_call_graph()
        target = self._resolve_signature(target_class_name, target_method_declaration)
        if target not in graph:
            return {"target_method": target, "caller_details": []}
        return {"target_method": target, "caller_details": [{"caller_signature": src, "edge": graph.get_edge_data(src, target)} for src in graph.predecessors(target)]}

    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        graph = self.get_call_graph()
        source = self._resolve_signature(source_class_name, source_method_declaration)
        if source not in graph:
            return {"source_method": source, "callee_details": []}
        return {"source_method": source, "callee_details": [{"callee_signature": tgt, "edge": graph.get_edge_data(source, tgt)} for tgt in graph.successors(source)]}

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        graph = self.get_call_graph()
        seeds = [method_signature] if method_signature is not None else [m.signature for m in self.get_all_methods_in_class(qualified_class_name).values()]
        seeds = [s for s in seeds if s in graph]
        return list(nx.edge_bfs(graph, seeds)) if seeds else []

    def get_class_hierarchy(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        rows = self._run(
            f"MATCH (n:TSClass|TSInterface) WHERE {_scoped('n')} AND n.kind IN $kinds RETURN n.signature AS sig, n.base_classes AS bases", kinds=_OWNER_KINDS, **self._scope_params
        )
        for r in rows:
            graph.add_node(r["sig"])
        for r in rows:
            for base in r["bases"] or []:
                graph.add_edge(r["sig"], base)
        return graph

    # =====================================================================================
    # call sites -- ``TSBodyNode {kind:'call'}`` under the callable, resolved over TS_RESOLVES_TO
    # =====================================================================================
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        # OPTIONAL MATCH so an existing callable with no call sites still gets an (empty) entry. The
        # resolved target is keyed as the call graph keys it: a signature, or "<module>.<name>" for
        # an external (which carries no signature); null when the analyzer left the call unresolved.
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.signature IN $sigs "
            "OPTIONAL MATCH (c)-[:TS_HAS_BODY_NODE]->(s:TSBodyNode {kind: 'call'}) "
            "OPTIONAL MATCH (s)-[:TS_RESOLVES_TO]->(t) "
            "RETURN c.signature AS owner, properties(s) AS p, coalesce(t.signature, t.module + '.' + t.name) AS callee "
            "ORDER BY s.start_line",
            sigs=list(signatures),
            **self._scope_params,
        )
        out: Dict[str, List[TSCallsite]] = {}
        for r in rows:
            sites = out.setdefault(r["owner"], [])
            if r["p"] is not None:
                sites.append(R.callsite(r["p"], r["callee"]))
        return out

    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        return self.get_callsites_for([qualified_callable_name]).get(qualified_callable_name, [])

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """Resolved callee keys of a callable's call sites. An unresolved site contributes ``""``:
        the graph keeps no ``method_name`` to fall back on."""
        return {cs.callee_signature or "" for cs in self.get_call_sites(source_signature)}

    def get_calling_lines(self, target_signature: str) -> List[int]:
        rows = self._run(
            f"MATCH (t:TSCallable|TSClass|TSExternal) WHERE {_scoped('t')} AND coalesce(t.signature, t.module + '.' + t.name) = $sig "
            "MATCH (s:TSBodyNode {kind: 'call'})-[:TS_RESOLVES_TO]->(t) WHERE s.start_line IS NOT NULL "
            "RETURN DISTINCT s.start_line AS line ORDER BY line",
            sig=target_signature,
            **self._scope_params,
        )
        return [r["line"] for r in rows]

    # =====================================================================================
    # classes / interfaces / enums / type aliases / namespaces
    # =====================================================================================
    def get_all_classes(self) -> Dict[str, TSClass]:
        return self._types_by_signature("TSClass")

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        return self._type_by_signature("TSClass", qualified_class_name)

    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        return self._types_by_signature("TSInterface")

    def get_all_enums(self) -> Dict[str, TSEnum]:
        return self._types_by_signature("TSEnum")

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        enum = self._type_by_signature("TSEnum", qualified_enum_name)
        return list(enum.members) if enum else []

    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        return self._types_by_signature("TSTypeAlias")

    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        """Always ``[]``, on this backend and in-memory alike -- not a projection gap. In the
        schema-v2 tree a class holds only ``callables`` and ``fields``: no ``types`` bucket, so no
        containment edge from a class to a type exists to walk. A class declared inside a
        *callable* survives as ``TSCallable.inner_classes``."""
        return []

    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        roots, children = self._fetch(f"(root:TSClass) WHERE {_scoped('root')} AND $sig IN root.base_classes", sig=qualified_class_name, **self._scope_params)
        return {p["signature"]: self._type(p, children) for p in roots}

    def _heritage(self, qualified_class_name: str, rel: str, verb: str) -> List[str]:
        """The signatures a class's ``rel`` edges point at, sorted.

        The split is read off the relationships, never off the properties. ``base_classes`` is the
        **union** of extends and implements, and ``implements_types`` -- the property the
        subtraction would need -- is written by no node in any graph this backend has been measured
        against (0 of 207 classes and 0 of 687 interfaces on superset-frontend), so subtracting it
        would silently return the interfaces as extended classes. ``TS_EXTENDS``/``TS_IMPLEMENTS``
        are drawn only for a heritage clause the analyzer **resolved in-repo**: a base that is a
        library type is in ``base_classes`` with no node to point at, and is not returned here.

        A graph that declares no ``rel`` at all cannot be asked this question -- ``[]`` would read
        as "extends nothing"/"implements nothing" -- so it raises naming the gap, as the four other
        projection gaps on this class do."""
        if rel not in self._relationship_types:
            raise CodeanalyzerExecutionException(
                f"The graph for application {self.application_name!r} declares no {rel} relationship type, so it cannot say what {qualified_class_name!r} {verb}; "
                "the projection carries only the extends-implements union in base_classes, and the split exists in analysis.json."
            )
        rows = self._run(
            f"MATCH (c:TSClass {{signature: $sig}}) WHERE {_scoped('c')} MATCH (c)-[:{rel}]->(b) WHERE {_scoped('b')} RETURN DISTINCT b.signature AS sig ORDER BY sig",
            sig=qualified_class_name,
            **self._scope_params,
        )
        return [r["sig"] for r in rows if r["sig"]]

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base classes ``TS_EXTENDS`` draws from this class -- resolved in-repo bases only.
        See :meth:`_heritage` for why the property is not read and when this raises."""
        return self._heritage(qualified_class_name, "TS_EXTENDS", "extends")

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """The interfaces ``TS_IMPLEMENTS`` draws from this class, on :meth:`_heritage`'s terms.
        The superset-frontend reference graph declares no ``TS_IMPLEMENTS`` at all, so this raises
        there rather than reporting that no class implements anything."""
        return self._heritage(qualified_class_name, "TS_IMPLEMENTS", "implements")

    # =====================================================================================
    # methods / functions / fields
    # =====================================================================================
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        roots, children = self._fetch(f"(root:TSClass|TSInterface) WHERE {_scoped('root')} AND root.kind IN $kinds", kinds=_OWNER_KINDS, **self._scope_params)
        return {p["signature"]: {m.name: m for m in self._methods(p["id"], children).values()} for p in roots}

    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        roots, children = self._fetch(
            f"(root:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('root')} AND root.kind IN $kinds", sig=qualified_class_name, kinds=_OWNER_KINDS, **self._scope_params
        )
        return {m.name: m for p in roots[:1] for m in self._methods(p["id"], children).values()}

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        """A method by (class/interface signature, short name); else a module/namespace function
        by exact signature (``qualified_method_name`` is the signature, the scope ignored), else by
        short name under ``<scope>.`` -- the in-memory backend's resolution order."""
        roots, children = self._fetch(
            f"(o:TSClass|TSInterface {{signature: $sig}}) WHERE {_scoped('o')} AND o.kind IN $kinds MATCH (o)-[:TS_HAS_METHOD]->(root:TSCallable {{name: $name}})",
            sig=qualified_class_name,
            name=qualified_method_name,
            kinds=_OWNER_KINDS,
            **self._scope_params,
        )
        if not roots:
            roots, children = self._fetch(
                f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable {{signature: $sig}}) WHERE {_scoped('root')}", sig=qualified_method_name, **self._scope_params
            )
        if not roots:
            roots, children = self._fetch(
                f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable {{name: $name}}) WHERE {_scoped('root')} AND root.signature STARTS WITH $sig_prefix",
                name=qualified_method_name,
                sig_prefix=f"{qualified_class_name}.",
                **self._scope_params,
            )
        return self._callable(roots[0], children) if roots else None

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """``[]`` for a missing method, as in-memory. For a **found** one this graph cannot answer:
        the 1.2.0 projection carries no parameters on ``:TSCallable`` (nor as nodes), and an empty
        list would read as "takes no parameters", so it raises naming the gap."""
        if self.get_method(qualified_class_name, qualified_method_name) is None:
            return []
        raise CodeanalyzerExecutionException(f"The codeanalyzer-typescript Neo4j projection carries no parameters for {qualified_method_name!r}; they exist only in analysis.json.")

    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return {name: m for name, m in self.get_all_methods_in_class(qualified_class_name).items() if m.kind == "constructor"}

    def get_all_functions(self) -> Dict[str, TSCallable]:
        roots, children = self._fetch(f"(p:TSModule|TSNamespace)-[:TS_DECLARES]->(root:TSCallable) WHERE {_scoped('root')}", **self._scope_params)
        return {p["signature"]: self._callable(p, children) for p in roots}

    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        cls = self.get_class(qualified_class_name)
        return list(cls.attributes.values()) if cls else []

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        it = self._type_by_signature("TSInterface", qualified_interface_name)
        return list(it.properties.values()) if it else []

    # =====================================================================================
    # imports / exports / variables
    # =====================================================================================
    def get_imports(self) -> Dict[str, List[TSImport]]:
        """The 1.2.0 projection carries no import bindings (no relationship type, no property), so
        this graph cannot say what a module imports; raises naming the gap rather than returning
        empty lists that would read as "imports nothing"."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no import bindings for application {self.application_name!r}; they exist only in analysis.json."
        )

    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        """As :meth:`get_imports`: the projection carries no export bindings."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no export bindings for application {self.application_name!r}; they exist only in analysis.json."
        )

    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        out: Dict[str, List[TSVariableDeclaration]] = {key: [] for key in self._modules}
        rows = self._run(
            "MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule)-[:TS_HAS_FIELD]->(f:TSField) RETURN m.name AS k, properties(f) AS p ORDER BY f.start_line, f.name",
            app_id=self._app_id,
        )
        for r in rows:
            out.setdefault(r["k"], []).append(R.field(r["p"]))
        return out

    # =====================================================================================
    # repository artifacts -- the unprefixed shared layer, the shared Py* models
    # =====================================================================================
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        result: Dict[str, PyArtifact] = {}
        for r in self._run(
            "MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact) OPTIONAL MATCH (a)-[:DEFINES_CONFIG]->(ck:ConfigKey) RETURN properties(a) AS p, collect(properties(ck)) AS cks",
            app_id=self._app_id,
        ):
            art = shared.artifact(r["p"], config_keys=[shared.config_key(p) for p in r["cks"] if p])
            result[art.path] = art
        return result

    def get_dependencies(self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None) -> List[PyDependency]:
        conditions: List[str] = []
        params: Dict[str, Any] = {"app_id": self._app_id}
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
            "MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(p:Package)"
            + where
            + " RETURN properties(r) AS rel, p.name AS name, p.ecosystem AS ecosystem, a.id AS declared_in"
        )
        return [shared.dependency(r["rel"], name=r["name"], ecosystem=r["ecosystem"], declared_in=r["declared_in"]) for r in self._run(query, **params)]

    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        rows = self._run("MATCH (:Application {id: $app_id})-[:HAS_ARTIFACT]->(:Artifact)-[:DEFINES_CONFIG]->(ck:ConfigKey) RETURN properties(ck) AS p", app_id=self._app_id)
        return {ck.id: ck for ck in (shared.config_key(r["p"]) for r in rows)}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """The ``TS_USES_CONFIG`` edges from a body node to the ``:ConfigKey`` it names.

        ``[]`` covers two states this graph cannot tell apart: a corpus in which the analyzer
        matched no config read, and a database that declares no ``TS_USES_CONFIG`` relationship
        type at all (the superset-frontend reference graph is the second). Unlike
        :meth:`get_unresolved_config_reads`, the empty is **not** raised: a project that genuinely
        reads no configuration is an ordinary project, and refusing it at attach -- which is what
        adding the type to :attr:`_REQUIRED_RELATIONSHIP_TYPES` would do -- would reject a valid
        graph. The count is therefore a floor, not a verdict on the corpus."""
        query = f"MATCH (bn:TSBodyNode)-[u:TS_USES_CONFIG]->(ck:ConfigKey) WHERE {_scoped('bn')}"
        params: Dict[str, Any] = dict(self._scope_params)
        if key is not None:
            query += " AND ck.key = $key"
            params["key"] = key
        query += " RETURN bn.id AS src, ck.id AS dst, u.prov AS prov"
        return [PyConfigUseEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or [])) for r in self._run(query, **params)]

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """The 1.2.0 projection does not carry ``config_reads`` (no relationship type or property
        in ``schema.neo4j.json``), so this graph cannot say whether a detector-matched read failed
        to resolve. Raising keeps that distinct from "every read resolved"."""
        raise CodeanalyzerExecutionException(
            f"The codeanalyzer-typescript Neo4j projection carries no unresolved config reads for application {self.application_name!r}; they exist only in analysis.json."
        )

    # =====================================================================================
    # decorators
    # =====================================================================================
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        rows = self._run(
            f"MATCH (c:TSCallable {{signature: $sig}}) WHERE {_scoped('c')} MATCH (c)-[r:TS_DECORATED_BY]->(d:TSDecorator) RETURN properties(d) AS node, properties(r) AS edge",
            sig=qualified_callable_name,
            **self._scope_params,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        rows = self._run(
            f"MATCH (c:TSClass {{signature: $sig}}) WHERE {_scoped('c')} MATCH (c)-[r:TS_DECORATED_BY]->(d:TSDecorator) RETURN properties(d) AS node, properties(r) AS edge",
            sig=qualified_class_name,
            **self._scope_params,
        )
        return [R.decorator(r["node"], r["edge"]) for r in rows]

    def _with_decorators(self, label: str, decorators: List[str]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {d: [] for d in decorators}
        rows = self._run(
            f"MATCH (c:{label})-[:TS_DECORATED_BY]->(d:TSDecorator) WHERE {_scoped('c')} AND d.name IN $names RETURN d.name AS dn, c.signature AS sig",
            names=list(decorators),
            **self._scope_params,
        )
        for r in rows:
            result[r["dn"]].append(r["sig"])
        return result

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        return self._with_decorators("TSCallable", decorators)

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        return self._with_decorators("TSClass", decorators)

    # =====================================================================================
    # bulk / projected accessors -- one statement each
    # =====================================================================================
    #: Appended to a scoped ``MATCH`` that bound ``c``; ``path`` is derived from ``id`` afterwards.
    _OVERVIEW_PROJECTION = (
        "OPTIONAL MATCH (o:TSClass|TSInterface)-[:TS_HAS_METHOD]->(c) "
        "OPTIONAL MATCH (c)-[:TS_DECORATED_BY]->(d:TSDecorator) "
        "RETURN c.id AS id, c.signature AS signature, c.name AS name, c.kind AS kind, c.start_line AS start_line, c.end_line AS end_line, "
        "c.is_exported AS is_exported, c.is_async AS is_async, c.is_static AS is_static, c.accessibility AS accessibility, "
        "o.signature AS owner_signature, o.kind AS owner_kind, collect(DISTINCT d.name) AS decorators"
    )

    def _overview(self, row: Dict[str, Any]) -> TSCallableOverview:
        return R.overview({**row, "path": self._module_key(row["id"])})

    def get_callables_overview(self) -> List[TSCallableOverview]:
        rows = self._run(f"MATCH (c:TSCallable) WHERE {_scoped('c')} " + self._OVERVIEW_PROJECTION, **self._scope_params)
        return [self._overview(r) for r in rows]

    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.signature IN $sigs AND c.code IS NOT NULL AND c.code <> '' RETURN c.signature AS signature, c.code AS code",
            sigs=list(signatures),
            **self._scope_params,
        )
        return {r["signature"]: r["code"] for r in rows}

    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        rows = self._run(
            f"MATCH (c:TSCallable)-[:TS_DECORATED_BY]->(marker:TSDecorator) WHERE {_scoped('c')} AND marker.name IN $markers WITH DISTINCT c " + self._OVERVIEW_PROJECTION,
            markers=list(markers),
            **self._scope_params,
        )
        return [self._overview(r) for r in rows]

    # =====================================================================================
    # entrypoints and the config readers (leg 2.5b, Task 3)
    # =====================================================================================
    def get_entrypoints(self) -> List[TSCallableOverview]:
        """Callables the 1.3.0 emitter stamped ``is_entrypoint`` (see
        :meth:`TSAnalysisBackend.get_entrypoints`). Prefix-scoped on the bare label, the measured
        rule for an application-wide prefix."""
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.is_entrypoint = true " + self._OVERVIEW_PROJECTION,
            **self._scope_params,
        )
        return [self._overview(r) for r in rows]

    def get_entrypoint_classes(self) -> List[TSClassOverview]:
        """Classes the 1.3.0 emitter stamped ``is_entrypoint`` (see
        :meth:`TSAnalysisBackend.get_entrypoint_classes`).

        ``kind = 'class'`` alongside the label is the declaration-merge guard this file uses
        everywhere: one id can carry ``:TSClass`` and ``:TSCallable`` both, and the node's ``kind``
        is the facet the last writer minted."""
        rows = self._run(
            f"MATCH (cl:TSClass) WHERE {_scoped('cl')} AND cl.is_entrypoint = true AND cl.kind = $kind "
            "OPTIONAL MATCH (cl)-[:TS_DECORATED_BY]->(d:TSDecorator) "
            "RETURN cl.id AS id, cl.signature AS signature, cl.name AS name, cl.start_line AS start_line, cl.end_line AS end_line, "
            "collect(DISTINCT d.name) AS decorators",
            kind=TYPE_LABEL_KINDS["TSClass"],
            **self._scope_params,
        )
        return [R.class_overview({**r, "path": self._module_key(r["id"])}) for r in rows]

    def get_entrypoint_coverage(self) -> EntrypointCoverage:
        """The entrypoint pass's coverage record, read off ``:Application.entrypoint_report_json``.

        Measured on the 1.3.0 reference graph: the anchor carries the whole ``TSEntrypointReport``
        as a JSON string property beside the derived ``entrypoint_frameworks`` list (which is that
        report's own ``frameworks_detected`` and is therefore not read separately), and there are
        no per-entrypoint nodes to rebuild it from. So it is parsed, and the local backend and this
        one answer with the same model and no lossiness between them.

        A graph without the property answers with ``entrypoint_report_unavailable`` rather than
        empty-but-clean-looking fields -- the same precedent as ``LocateResult``'s
        ``module_source_unavailable``."""
        # ``properties(a)`` rather than ``a.entrypoint_report_json``: naming a property key the
        # graph may not have makes the server log a warning per call.
        rows = self._run("MATCH (a:Application {id: $app_id}) RETURN properties(a) AS p", app_id=self._app_id)
        raw = rows[0]["p"].get("entrypoint_report_json") if rows else None
        if raw is None:
            return EntrypointCoverage(
                diagnostics=[
                    Diagnostic(
                        code="entrypoint_report_unavailable",
                        message=(
                            f"The :Application anchor for {self.application_name!r} carries no entrypoint_report_json property, so the "
                            "entrypoint pass's coverage (frameworks_detected/rulesets/unresolved/errors) cannot be reported. Use the "
                            "local codeanalyzer backend for it."
                        ),
                    )
                ]
            )
        report = TSEntrypointReport.model_validate_json(raw)
        return EntrypointCoverage(
            frameworks_detected=list(report.frameworks_detected),
            rulesets=list(report.rulesets),
            unresolved=dict(report.unresolved),
            errors=list(report.errors),
        )

    def get_config_readers(self, key: str) -> List[TSCallableOverview]:
        """Callables reading configuration key ``key`` (see
        :meth:`TSAnalysisBackend.get_config_readers`).

        The reading body node is walked back to its owner over ``TS_HAS_BODY_NODE`` -- the edge the
        emitter writes in the same step that mints the node -- rather than by splitting its id on
        ``@``, which an anonymous callable's own id already contains. ``DISTINCT`` because one
        callable can read the same key at several call sites. The superset-frontend reference graph
        declares no ``TS_USES_CONFIG`` relationship type at all, so this is ``[]`` there for the
        same reason :meth:`get_config_uses` is."""
        rows = self._run(
            f"MATCH (bn:TSBodyNode)-[:TS_USES_CONFIG]->(ck:ConfigKey) WHERE {_scoped('bn')} AND ck.key = $key "
            "MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(bn) WITH DISTINCT c " + self._OVERVIEW_PROJECTION,
            key=key,
            **self._scope_params,
        )
        return [self._overview(r) for r in rows]

    # =====================================================================================
    # The addressing surface (leg 2.5b, TS-2) -- over Cypher.
    #
    # SEEK LABELS, MEASURED ON THE SUPERSET GRAPH (PROFILE-shaped statements timed over the driver,
    # median of 5 with the first discarded, two runs; recorded in the leg plan). The two new
    # statement families land on OPPOSITE anchors, so 2.5a's "bare label for prefix statements" is
    # refined rather than repeated -- what decides it is how narrow the prefix is:
    #
    #   * ``locate_many`` seeks a per-MODULE id prefix, tens of nodes out of 150,443, so the
    #     ``CanNode.id`` index earns its seek: 357.9/346.2 ms on ``(c:TSCallable)`` against
    #     97.0/102.2 ms on ``(c:CanNode:TSCallable)``, same 113 rows -- 3.5x.
    #   * ``resolve_callable`` seeks the per-APPLICATION prefixes, nearly every node, so the same
    #     index is a full range walk and the 11,085-node label scan wins: 24-28 ms on
    #     ``(c:TSCallable)`` against 44-51 ms on ``(c:CanNode:TSCallable)``, across four names.
    #
    # ``:TSCanNode`` was measured faster still on locate (37.9/35.5 ms) and is refused on
    # CORRECTNESS: it is the per-namespace marker, so it drops every ``can://javascript/<app>/``
    # module -- 40 rows where the right answer is 113. Java's leg-3a ruling is not ported here, and
    # neither is 2.5a's, unexamined.
    # =====================================================================================
    #: Both layers of containment in one statement, so the whole resolution is one round trip: the
    #: **callable** by line containment over its own ``start_line``/``end_line`` (present at every
    #: analysis level), which treats a gap between two callables and a module-level line the same
    #: way -- nothing matches, so it falls through to module scope rather than snapping to a
    #: neighbour; then the **body node** by line containment under that same candidate. Synthetic
    #: dataflow vertices (``formal_in``/``formal_out``/``actual_*``: 55,778 of the 125,532) carry no
    #: lines at all, and the ``IS NOT NULL`` guard is what stops one being read as "contains
    #: everything". ``pos.module_prefix`` is the module's own id plus ``/``, so a same-valued
    #: ``name`` from another application cannot win and neither can a module whose key merely
    #: extends this one's spelling.
    _LOCATE_QUERY = (
        "UNWIND $positions AS pos "
        "OPTIONAL MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule {name: pos.path}) "
        "WITH pos, m "
        "OPTIONAL MATCH (c:CanNode:TSCallable) "
        "WHERE c.id STARTS WITH pos.module_prefix AND c.kind IN $callable_kinds "
        "AND c.start_line IS NOT NULL AND c.end_line IS NOT NULL "
        "AND c.start_line <= pos.line AND pos.line <= c.end_line "
        "WITH pos, m, c "
        "OPTIONAL MATCH (o:TSClass|TSInterface)-[:TS_HAS_METHOD]->(c) WHERE o.kind IN $owner_kinds "
        "WITH pos, m, c, o "
        "OPTIONAL MATCH (c)-[:TS_HAS_BODY_NODE]->(b:TSBodyNode) "
        "WHERE b.start_line IS NOT NULL AND b.end_line IS NOT NULL "
        "AND b.start_line <= pos.line AND pos.line <= b.end_line "
        "RETURN pos.idx AS idx, properties(m) AS module_props, properties(c) AS callable_props, "
        "properties(o) AS owner_props, properties(b) AS body_props"
    )

    @staticmethod
    def _line_span(start_line: int, end_line: int) -> Span:
        """A :class:`Span` over the only positional data the graph carries: line numbers.

        The projection writes ``start_line``/``end_line`` on ``:TSCallable`` and ``:TSBodyNode`` and
        nothing finer -- no columns, no offsets into the module source. The columns and ``bytes``
        here are therefore ``0`` placeholders, documented as meaningless on this backend rather
        than dressed up as real (see :class:`~cldk.analysis.commons.results.LocateResult`).
        """
        return Span(start=(start_line, 0), end=(end_line, 0), bytes=(0, 0))

    def _body_ref(self, rows: List[Dict[str, Any]], signature: str) -> BodyRef | None:
        """The tightest body node of ``signature`` the locate statement matched, or ``None``.

        ``None`` is a real outcome: a position on a declaration line or a blank line inside a
        callable is contained by the callable and by no body node, and the caller still gets the
        callable. The id is read straight off the node -- never composed -- and the tie between two
        nodes on one line breaks on the deeper column parsed out of the id's trailing key, the same
        rule the local backend applies to the same key
        (:func:`~cldk.analysis.commons.keys.body_key_column`), so both resolve a tie to the same
        node. ``callee`` is a property here, not a separate ``TS_RESOLVES_TO`` hop, so this backend
        fills it in exactly as the local one does.
        """
        matches = [r["body_props"] for r in rows if r["body_props"] is not None and r["callable_props"] is not None and r["callable_props"]["signature"] == signature]
        if not matches:
            return None

        def rank(b: Dict[str, Any]) -> Tuple[int, int, str]:
            key = str(b.get("id", "")).rsplit("@", 1)[-1]
            return (b["end_line"] - b["start_line"], -body_key_column(key), key)

        best = min(matches, key=rank)
        return BodyRef(id=best["id"], kind=best["kind"], span=self._line_span(best["start_line"], best["end_line"]), callee=best.get("callee"))

    def _locate_result(self, path: str, line: int, rows: List[Dict[str, Any]]) -> LocateResult:
        module_props = next((r["module_props"] for r in rows if r["module_props"] is not None), None)
        if module_props is None:
            return LocateResult(
                body=None,
                callable=None,
                type=None,
                module=ModuleRef(path=path),
                source="",
                span=self._line_span(line, line),
                diagnostics=[
                    Diagnostic(
                        code="file_not_in_graph",
                        message=(
                            f"{path} is not covered by any analysed module of application {self.application_name!r}. "
                            "This backend reads an attached graph and has no access to the project sources, so it "
                            "cannot tell a file that was never analysed from one that is not on disk."
                        ),
                    )
                ],
            )
        module_ref = ModuleRef(path=module_props.get("name", path))
        # Innermost callable = narrowest line span containing the position. Rows with a null
        # callable are the OPTIONAL MATCH misses; there is one row per (callable, body node) pair,
        # so the same callable repeats. Equal widths tie, and `min` would then be decided by
        # Cypher's row order -- nondeterministic, and different from the local walk's. Break it on
        # the longer signature, deeper first, exactly as the local backend does: a nested callable's
        # signature extends its owner's.
        best = min(
            (r for r in rows if r["callable_props"] is not None),
            key=lambda r: (
                r["callable_props"]["end_line"] - r["callable_props"]["start_line"],
                -len(r["callable_props"]["signature"]),
                r["callable_props"]["signature"],
            ),
            default=None,
        )
        if best is None:
            # Module scope is a real position, not an absence -- but the graph genuinely does not
            # carry module text (``:TSModule`` projects name/lines/content_hash/flags and no
            # source), so say so instead of inventing something. Reading the file from disk is not
            # an option: this backend attaches to a graph someone else built and may not have the
            # project checked out, and concatenating the callables' ``code`` would silently drop
            # every module-level statement.
            return LocateResult(
                body=None,
                callable=None,
                type=None,
                module=module_ref,
                source="",
                span=self._line_span(line, line),
                diagnostics=[
                    Diagnostic(code="module_scope", message=f"line {line} is at module scope in {module_ref.path}."),
                    Diagnostic(
                        code="module_source_unavailable",
                        message=(
                            "The attached graph does not carry module text: :TSModule nodes project "
                            "name/kind/lines/content_hash/is_tsx/is_declaration_file and no source. "
                            "The local codeanalyzer backend returns the module's text for this position."
                        ),
                    ),
                ],
            )
        c, owner = best["callable_props"], best["owner_props"]
        body = self._body_ref(rows, c["signature"])
        return LocateResult(
            body=body,
            node_id=body.id if body else None,
            callable=CallableRef(signature=c["signature"], name=c["name"], class_signature=owner["signature"] if owner else None),
            type=TypeRef(signature=owner["signature"], name=owner["name"]) if owner else None,
            module=module_ref,
            source=c.get("code") or "",
            span=self._line_span(c["start_line"], c["end_line"]),
            diagnostics=[],
        )

    def locate(self, path: str, line: int) -> LocateResult:
        """Resolve a source position to its enclosing callable (see :meth:`TSAnalysisBackend.locate`)."""
        return self.locate_many([(path, line)])[0]

    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in **one** Cypher round trip (see
        :meth:`TSAnalysisBackend.locate_many`) -- the position list travels as a single parameter
        via ``UNWIND``, never a loop over :meth:`locate`. Results come back in input order
        regardless of the order Neo4j returns rows in."""
        positions = list(positions)
        if not positions:
            return []
        # Whatever the caller's scanner printed ("./src/app.ts", an absolute path) is normalised to
        # the graph's module key before it becomes a Cypher parameter -- an unnormalised path would
        # match no :TSModule and read back as file_not_in_graph.
        keys = [resolve_module_key(path, self._module_ids) for path, _ in positions]
        # A key the application does not hold is never sent: its ``module_prefix`` would be the raw
        # path, which is not application-stamped and could seek outside this application. It has no
        # rows either way, and no rows is exactly the ``file_not_in_graph`` outcome.
        asked = [
            {"idx": i, "path": key, "module_prefix": self._module_ids[key] + "/", "line": line}
            for i, (key, (_, line)) in enumerate(zip(keys, positions))
            if key in self._module_ids
        ]
        rows = self._run(self._LOCATE_QUERY, app_id=self._app_id, callable_kinds=sorted(CALLABLE_KINDS), owner_kinds=_OWNER_KINDS, positions=asked) if asked else []
        by_idx: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_idx[r["idx"]].append(r)
        return [self._locate_result(key, line, by_idx.get(i, [])) for i, (key, (_, line)) in enumerate(zip(keys, positions))]

    #: The resolver's own predicate, not a coarse pre-filter that happens to be close to it:
    #: ``segment_match`` is *exactly* "equal, or ends with the separator plus the name", so this
    #: ``WHERE`` keeps precisely the rows
    #: :func:`~cldk.analysis.commons.resolve.resolve_callable_signature` would keep. Pushing it into
    #: Cypher narrows the *round trip*, not the *domain* -- the candidate set is the same one the
    #: local backend resolves over. The ``kind`` guard alongside the label is what keeps a
    #: declaration-merged node out of the facet it is not (see the module docstring): the domain is
    #: the kind, not the label, so a node the emitter collapsed onto a type's kind can never come
    #: back described as a callable.
    _RESOLVE_CALLABLE_QUERY = (
        f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.kind IN $callable_kinds "
        "AND (c.signature = $name OR c.signature ENDS WITH $dotted) "
        "OPTIONAL MATCH (o:TSClass|TSInterface)-[:TS_HAS_METHOD]->(c) WHERE o.kind IN $owner_kinds "
        "RETURN c.id AS id, c.signature AS signature, c.name AS name, c.start_line AS start_line, o.signature AS class_signature"
    )

    def resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode:
        """Resolve a callable name against the graph (see :meth:`TSAnalysisBackend.resolve_callable`)."""
        rows = self._run(
            self._RESOLVE_CALLABLE_QUERY,
            name=name,
            dotted="." + name,
            callable_kinds=sorted(CALLABLE_KINDS),
            owner_kinds=_OWNER_KINDS,
            **self._scope_params,
        )
        # Two callables sharing a signature would collapse into one entry and resolve arbitrarily;
        # recorded and raised on only if the name lands on one, so an unrelated duplicate cannot
        # break every unrelated resolution. Not reachable on the reference application (11,085
        # callables, 11,085 distinct signatures, none null).
        by_sig: Dict[str, Dict[str, Any]] = {}
        collisions: Set[str] = set()
        for r in rows:
            if r["signature"] in by_sig:
                collisions.add(r["signature"])
            by_sig[r["signature"]] = r
        candidates = [CallableCandidate(r["signature"], r["class_signature"], self._module_key(r["id"])) for r in by_sig.values()]
        sig = resolve_callable_signature(name, candidates, in_class=in_class, in_module=in_module, dotted=ts_module_dotted)
        if sig in collisions:
            raise CodeanalyzerExecutionException(f"{sig!r} is carried by more than one analysed callable; neither can be addressed unambiguously")
        row = by_sig[sig]
        return SliceNode(file=self._module_key(row["id"]), line=row["start_line"], callable=sig, kind="callable", name=row["name"], source=None, ref=row["id"])

    def resolve_value(self, name: str, *, within: str) -> SliceNode:
        """Resolve a value name inside a callable (see :meth:`TSAnalysisBackend.resolve_value`).

        Two round trips, not one: the callable is resolved first, because an ambiguous ``within``
        must raise naming *callables* rather than failing obscurely on a value search over a set of
        them -- through :func:`~cldk.analysis.commons.resolve.resolve_within`, so the advice it
        gives names a keyword ``resolve_value`` actually accepts.
        """
        owner = resolve_within(self.resolve_callable, within)
        rows = self._run(
            f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND c.signature = $sig "
            "MATCH (c)-[:TS_HAS_BODY_NODE]->(b:TSBodyNode {kind: 'formal_in'}) WHERE b.of IS NOT NULL "
            "RETURN b.of AS of, b.id AS id",
            sig=owner.callable,
            **self._scope_params,
        )
        # A list, not a dict keyed by name: two values that resolve to the same name are a genuine
        # ambiguity the policy must see and raise on, and a dict would silently keep the last row.
        entries = [(r["id"], r["of"]) for r in rows]
        chosen = resolve_value_name(name, [v for _, v in entries], within=owner.callable)
        node_id = next(i for i, v in entries if v == chosen)
        return SliceNode(file=owner.file, line=owner.line, callable=owner.callable, kind="parameter", name=chosen, source=None, ref=node_id)

    #: One statement, three arms, so ``describe`` costs one round trip whatever it is handed. A
    #: callable answers to both of its names; a body node and an external are *found* and textless
    #: -- which is what keeps "no text for this position" apart from "this ref names nothing".
    _SOURCES = (
        f"MATCH (c:TSCallable) WHERE {_scoped('c')} AND (c.id IN $refs OR c.signature IN $refs) AND c.kind IN $callable_kinds "
        "RETURN c.id AS id, c.signature AS sig, c.code AS code "
        f"UNION MATCH (b:TSBodyNode) WHERE {_scoped('b')} AND b.id IN $refs RETURN b.id AS id, null AS sig, null AS code "
        f"UNION MATCH (e:TSExternal) WHERE {_scoped('e')} AND e.id IN $refs RETURN e.id AS id, null AS sig, null AS code"
    )

    def _sources_for(self, refs: Sequence[str]) -> Dict[str, "str | None"]:
        """Source text for every ref this graph holds (see :meth:`TSAnalysisBackend._sources_for`).

        A callable's ``code`` is a real property; nothing below it is (``:TSBodyNode`` carries a
        line span and no text, ``:TSModule`` no source to slice one out of), so a body node maps to
        ``None`` here where the local backend fills it in. ``""`` maps to ``None`` too -- the 27
        callables the emitter writes no ``code`` for are the implicit constructors, which the local
        backend also has no text for.

        **The text this returns is one line short of the callable's span** on
        codeanalyzer-typescript 1.3.0 -- the graph's ``code`` is the source minus its final
        ``"\\n}"`` (codellm-devkit/codeanalyzer-typescript#179). Every caller of this seam --
        :meth:`get_source` and :meth:`describe` -- inherits it, as do :meth:`get_method_bodies` and
        :meth:`locate`, which read ``c.code`` by their own statements. See the module docstring.
        """
        wanted = set(refs)
        found: Dict[str, "str | None"] = {}
        rows = self._run(
            self._SOURCES,
            refs=list(wanted),
            callable_kinds=sorted(CALLABLE_KINDS),
            **self._scope_params,
        )
        for row in rows:
            # A callable answers to both of its names, exactly as ``get_source`` accepts either --
            # a ``SliceNode.ref`` is the ``can://`` id, but a caller holding a signature must not
            # get "names nothing" for a callable that plainly exists.
            for ref in (row["id"], row["sig"]):
                if ref in wanted:
                    found[ref] = row["code"] or None
        return found

    def get_source(self, node_id: str) -> str:
        """Source text for one node (see :meth:`TSAnalysisBackend.get_source`).

        Only a callable is answerable here. A body-node id names something the graph structurally
        cannot supply text for, so that case raises rather than silently substituting the enclosing
        callable's (far larger) text -- and the two are told apart by *asking the graph what the id
        names*, never by splitting the id: an anonymous callable's own id contains an ``@``, so
        ``partition("@")`` would mistake ``…/<anon@22:52>`` for a body node of ``…/<anon``.
        """
        found = self._sources_for([node_id])
        code = found.get(node_id)
        if code:
            return code
        if node_id in found:
            rows = self._run(f"MATCH (b:CanNode:TSBodyNode {{id: $id}}) WHERE {_scoped('b')} RETURN b.id AS id", id=node_id, **self._scope_params)
            if rows:
                raise NotImplementedError(
                    f"get_source({node_id!r}): the attached graph carries no source text below callable granularity -- "
                    ":TSBodyNode has a line span and no code property, and :TSModule has no source to slice one out of. "
                    "Only the local codeanalyzer backend can answer for a statement or call site."
                )
            raise KeyError(f"no recoverable source for {node_id!r} (the graph carries no text for it)")
        raise KeyError(f"no callable, body node or external symbol of application {self.application_name!r} is addressed by {node_id!r}")

    @property
    def has_resolution_edges(self) -> bool:
        """See :meth:`TSAnalysisBackend.has_resolution_edges`. Fixed at construction by
        :meth:`_probe_resolution_edges`."""
        return self._has_resolution_edges

    def _probe_resolution_edges(self) -> bool:
        """Whether this application has a single ``TS_RESOLVES_TO`` edge, asked once at attach.

        ``--emit neo4j`` is always full depth (level and ``--graphs`` cannot even be passed
        alongside it), so callee resolution always ran and this is expected to be ``True`` on any
        graph built that way -- 16,324 edges on the reference application. The probe is defensive
        against a graph built some other way (a hand-populated database, an older or forked
        emitter), not against a gap in the documented pipeline. This is information, not an error:
        it never raises the way :meth:`_probe_schema` does.
        """
        if "TS_RESOLVES_TO" not in self._relationship_types:
            return False
        return bool(self._run(f"MATCH (s:TSBodyNode)-[:TS_RESOLVES_TO]->() WHERE {_scoped('s')} RETURN s LIMIT 1", **self._scope_params))

    # =====================================================================================
    # The dataflow surface (leg 2.5b, Task 2) -- over Cypher.
    #
    # SEEK LABELS, MEASURED ON THE SUPERSET GRAPH (statements timed end-to-end over the driver,
    # median of 5 with the first discarded). Task 1's rule -- what decides the anchor is how narrow
    # the predicate is, not that there is one -- holds again, and the per-callable page is the
    # narrowest prefix on this surface, so the seek wins:
    #
    #   per-callable DDG page, 4 callables (1,933 / 461 / 399 / 366 edges)
    #     (s:TSBodyNode)          WHERE s.id STARTS WITH <callable id>@   82.8 / 56.6 / 61.6 / 44.3 ms
    #     (s:CanNode:TSBodyNode)  WHERE s.id STARTS WITH <callable id>@   42.8 / 11.7 / 10.8 /  9.2 ms  <- 2-6x
    #     the containment spelling on (c:TSCallable)                      55.5 / 19.4 / 17.6 / 16.8 ms
    #     the containment spelling on (c:CanNode:TSCallable)             636.8 /147.7 /118.4 /106.5 ms
    #
    #   slice seed (an id-equality point lookup), depth 5, three formal_in seeds
    #     (r:TSBodyNode {id})           42.1 / 42.3 / 39.9 ms
    #     (r:CanNode:TSBodyNode {id})    3.2 /  5.6 /  2.8 ms  <- 13x, the CanNode.id uniqueness index
    #
    # THE CONTAINMENT SPELLING IS ALSO WRONG, WHICH IS WHY IT IS NOT USED. The Python backend's
    # ``_OWN_EDGES`` shape -- ``(c)-[:HAS_BODY_NODE]->(s)-[r]->(d)<-[:HAS_BODY_NODE]-(c)`` -- binds
    # two ``TS_HAS_BODY_NODE`` relationships in one MATCH, and Cypher's relationship-uniqueness rule
    # forbids them from being the *same* relationship. So every **self-loop** edge is silently
    # dropped: measured on the reference graph, 173 ``TS_DDG`` and 2,511 ``TS_SUMMARY`` edges run
    # from a body node to itself, and ``drawGraph``'s DDG page came back 1,931 against the true
    # 1,933. The id-prefix spelling below has no such pattern and returns all of them.
    #
    # THE PREFIX IS EXACT, VERIFIED RATHER THAN ASSUMED: every one of the 125,532 body nodes' ids
    # starts with its owning callable's id plus ``@`` (0 exceptions), no body node has two owners or
    # none, and no callable id is another callable id plus ``@`` -- so ``<callable id>@`` selects
    # that callable's body nodes and nothing else. ``d.id STARTS WITH $bp`` keeps the "both
    # endpoints in one callable" restriction written rather than trusted (0 cross-callable edges of
    # 214,058 today), for the Python backend's reason: a graph built some other way must not be able
    # to widen the answer silently.
    # =====================================================================================
    _OWN_EDGES = "MATCH (s:CanNode:TSBodyNode)-[r:{rel}]->(d:TSBodyNode) WHERE s.id STARTS WITH $bp AND d.id STARTS WITH $bp "

    def _own_edges(self, name: str, in_class: str | None, rel: str, projection: str, order: EdgeOrder, page_size: int, cursor: str | None):
        """One page of a callable's own ``rel`` edges: ``(signature, rows, whole size, is there more)``.

        Resolution is :meth:`resolve_callable`'s, not a second path, so an ambiguous name raises
        listing candidates here exactly as it does there -- and its ``ref`` is the callable id the
        body-node prefix is built from, which is why this accessor pays no extra lookup for it.

        **Keyset, not ``SKIP``.** ``order.exprs`` is the canonical order written as Cypher -- the
        same components ``order.key`` produces in Python, ``coalesce``-d the way ``or ""`` / ``or []``
        normalise there -- and a cursor becomes a ``WHERE`` filter
        (:func:`~cldk.analysis.commons.bounds.keyset_where`) rather than an offset, which is flat in
        the page depth where an offset re-sorts a growing prefix.

        **Three round trips, not one.** ``resolve_callable`` costs one -- the price of the caller
        naming a callable instead of quoting a signature -- then a ``count`` for ``total`` and the
        page itself. ``total`` is not optional: without it a caller cannot see the size of what it
        is walking into from the first page, which is E5's whole point. It is re-counted per page
        rather than cached, because the alternative is a number that can go stale against a graph
        this backend does not own.

        The page asks for ``page_size + 1`` rows and reports ``more`` from whether it got them, so
        "there is more" is a fact about the data and not an inference from ``len(rows) ==
        page_size`` -- which is wrong exactly when the set ends on a page boundary.
        """
        check_page_size(page_size)
        node = self.resolve_callable(name, in_class=in_class)
        sig, params = node.callable, {"bp": node.ref + "@"}
        match = self._OWN_EDGES.format(rel=rel)
        total = self._run(match + "RETURN count(r) AS total", **params)[0]["total"]
        where = f"WHERE {keyset_where(order.exprs)} " if cursor is not None else ""
        rows = self._run(
            f"{match}WITH s.id AS src, d.id AS dst{projection} {where}RETURN * ORDER BY {', '.join(order.exprs)} LIMIT $lim",
            lim=page_size + 1,
            **params,
            **(cursor_params(cursor, sig, len(order.exprs)) if cursor is not None else {}),
        )
        return sig, rows[:page_size], total, len(rows) > page_size

    @staticmethod
    def _page(model, scope: str, edges: List, order: EdgeOrder, total: int, more: bool) -> EdgePage:
        """Wrap a page's edges, deriving ``next_cursor`` from the *same* sort key the local backend
        uses -- so a cursor minted here and one minted there name the same position."""
        return EdgePage[model](edges=edges, total=total, next_cursor=encode_cursor(scope, order.key(edges[-1])) if more and edges else None)

    def get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCfgEdge]:
        """One page of control flow within one callable (see :meth:`TSAnalysisBackend.get_cfg`)."""
        sig, rows, total, more = self._own_edges(callable, in_class, "TS_CFG_NEXT", ", r.kind AS kind", CFG_ORDER, page_size, cursor)
        return self._page(TSCfgEdge, sig, [TSCfgEdge(src=r["src"], dst=r["dst"], kind=r["kind"]) for r in rows], CFG_ORDER, total, more)

    def get_cdg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSCdgEdge]:
        """One page of control dependence within one callable (see :meth:`TSAnalysisBackend.get_cdg`)."""
        sig, rows, total, more = self._own_edges(callable, in_class, "TS_CDG", "", CDG_ORDER, page_size, cursor)
        return self._page(TSCdgEdge, sig, [TSCdgEdge(src=r["src"], dst=r["dst"]) for r in rows], CDG_ORDER, total, more)

    def get_ddg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[TSDdgEdge]:
        """One page of data dependence within one callable (see :meth:`TSAnalysisBackend.get_ddg`).

        ``prov`` is ``["reaching-defs"]`` on every one of the reference application's 119,384
        ``TS_DDG`` edges -- TypeScript's single tier. ``or []`` restores the model's default rather
        than failing validation on an edge that carries none, and the ``coalesce`` in the sort key
        does the same for the ordering: a null there would make the keyset filter drop the row
        silently rather than misplace it.
        """
        sig, rows, total, more = self._own_edges(callable, in_class, "TS_DDG", ", r.var AS var, r.prov AS prov", DDG_ORDER, page_size, cursor)
        edges = [TSDdgEdge(src=r["src"], dst=r["dst"], var=r["var"], prov=list(r["prov"] or [])) for r in rows]
        return self._page(TSDdgEdge, sig, edges, DDG_ORDER, total, more)

    # -----[ slicing ]-----
    #: Reverse (backward) and forward reachability over the SDG, as ONE variable-length match each.
    #: ``*0..`` rather than ``*1..`` so the seed is part of its own slice without being spliced in
    #: afterwards, which matters because ``total`` and the ``max_nodes`` prefix both have to be over
    #: the same set.
    #:
    #: ``total`` and the page come back from one statement: the ids are collected in id order,
    #: ``size()`` gives the whole slice's size, and only the first ``$cap`` are joined back to their
    #: callables for hydration -- collecting the *nodes* rather than their ids would put the whole
    #: closure in the transaction.
    #:
    #: **Every node on the walk carries the two-prefix predicate, not just its far end.** An SDG
    #: edge runs between two application-owned nodes -- which is exactly why the SDG types are
    #: absent from the audit's ``_KEEPS_SCOPE`` -- so an *interior* body node is no more provably
    #: this application's than a far endpoint is, on a graph this SDK did not emit. The path is
    #: named so ``all(n IN nodes(p) …)`` can reach the interior; the explicit predicate on ``m`` is
    #: kept because it filters before the path is built.
    _SLICE = (
        "MATCH p = (r:CanNode:TSBodyNode {{id:$id}}){left}[:{rels}*0..{depth}]{right}(m:TSBodyNode) "
        "WHERE " + _scoped("m") + " AND all(n IN nodes(p) WHERE " + _scoped("n") + ") "
        "WITH DISTINCT m.id AS nid ORDER BY nid "
        "WITH collect(nid) AS ids "
        "WITH size(ids) AS total, ids[0..$cap] AS page "
        "UNWIND page AS nid "
        "MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(b:CanNode:TSBodyNode {{id:nid}}) WHERE " + _scoped("c") + " "
        "RETURN total, b.id AS ref, b.kind AS kind, b.of AS of, b.start_line AS line, "
        "c.signature AS callable, c.start_line AS c_line"
    )

    def _slice_row(self, row: Dict[str, Any]) -> SliceNode:
        """One row of the slice or path query as a :class:`SliceNode`, in the caller's vocabulary.

        ``file`` is derived from the body node's own ``ref``: a body-node id is its callable's id
        plus ``@<key>``, so both embed the same module key, and the graph stores no path to project
        instead. ``kind``/``name`` go through
        :func:`~cldk.analysis.typescript.backend.ts_body_node_kind`, the same translation the local
        backend uses, so a vertex a caller addressed through ``resolve_value`` as a ``parameter``
        comes back from a slice labelled a ``parameter`` too. A parameter-passing vertex has no span
        of its own, so the *callable's* first line stands in.
        """
        kind, name = ts_body_node_kind(row["kind"], row["of"])
        return SliceNode(
            file=self._module_key(row["ref"]),
            line=row["line"] if row["line"] is not None else row["c_line"],
            callable=row["callable"],
            kind=kind,
            name=name,
            source=None,
            ref=row["ref"],
        )

    def _slice(self, src: str, within: str, depth: int | None, max_nodes: int, *, backward: bool) -> Slice:
        """One direction of :meth:`TSAnalysisBackend.slice_backward` / ``slice_forward``. The two
        differ only in which way the arrows point, so they share a query and a builder -- a second
        copy would be a second place for the node vocabulary to drift."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        root = self.resolve_value(src, within=within)
        query = self._SLICE.format(
            rels=SDG_REL_PATTERN,
            depth="" if depth is None else depth,
            left="<-" if backward else "-",
            right="-" if backward else "->",
        )
        rows = self._run(query, id=root.ref, cap=max_nodes, **self._scope_params)
        return Slice(nodes=[self._slice_row(r) for r in rows], roots=[root], resolved=slice_resolved([root]), total=rows[0]["total"] if rows else 0)

    def slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What affects this value (see :meth:`TSAnalysisBackend.slice_backward`)."""
        return self._slice(src, within, depth, max_nodes, backward=True)

    def slice_forward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """What this value affects (see :meth:`TSAnalysisBackend.slice_forward`)."""
        return self._slice(src, within, depth, max_nodes, backward=False)

    # -----[ the call graph ]-----
    def _call_vertex(self, row: Dict[str, Any]) -> SliceNode:
        """One call-graph vertex as a :class:`SliceNode` -- callable, module or external ghost.

        Which it is, is read off the row's ``kind`` rather than asked for in a second query. A
        **module** is a legitimate caller (TS-11) and is addressed by its file key, which is exactly
        what ``:TSModule.name`` holds. An **external** was never analysed, so it gets no position and
        its readable name is built from its own ``module``/``name`` properties -- the ``can://`` id
        stays in ``ref`` (E6). Its id sits under the application prefix but names no module, which is
        why the external arm never calls :meth:`_module_key`.
        """
        if row["kind"] == "module":
            return SliceNode(file=row["name"], line=row["line"], callable=row["name"], kind="module", name=row["name"], source=None, ref=row["ref"])
        if row["kind"] == "external":
            qualified = f"{row['module']}.{row['name']}" if row["module"] else row["name"]
            return SliceNode(file="", line=0, callable=qualified, kind="external", name=row["name"], source=None, ref=row["ref"])
        return SliceNode(file=self._module_key(row["ref"]), line=row["line"], callable=row["signature"], kind="callable", name=row["name"], source=None, ref=row["ref"])

    #: Every hop labelled and application-scoped, not just the endpoints. A plain variable-length
    #: ``-[:TS_CALLS*1..]->(m:TSCallable)`` labels only its *endpoint*, so an intermediate could be a
    #: module or an external ghost; the quantified pattern is what makes the walk exactly the
    #: callable-only edge set the local backend's ``_callable_call_graph`` view walks. The ``kind``
    #: guard alongside the label is Task 1's "the domain is the kind, not the label" rule, which is
    #: what keeps a declaration-merged node out of the facet it is not. Needs Neo4j 5.9+.
    _REACHES = (
        "MATCH (a:TSCallable {{signature:$a}}) WHERE " + _scoped("a") + " AND a.kind IN $callable_kinds "
        "MATCH (a) ((x:TSCallable)-[:TS_CALLS]->(y:TSCallable) WHERE "
        + _scoped("x")
        + " AND "
        + _scoped("y")
        + " AND x.kind IN $callable_kinds AND y.kind IN $callable_kinds){{1,{depth}}} (m:TSCallable) "
        "WITH DISTINCT m WHERE " + _scoped("m") + " AND m.signature = $b RETURN count(m) > 0 AS ok"
    )

    def reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool:
        """Is there a call path (see :meth:`TSAnalysisBackend.reaches`)?"""
        check_depth(depth)
        self._require_quantified_paths("reaches")
        a = self.resolve_callable(src).callable
        b = self.resolve_callable(dst).callable
        query = self._REACHES.format(depth="" if depth is None else depth)
        return bool(self._run(query, a=a, b=b, callable_kinds=sorted(CALLABLE_KINDS), **self._scope_params)[0]["ok"])

    #: ``{0,}`` so a sink with no callers is its own cone rather than an empty answer a caller could
    #: not tell from "this name is wrong" (D7). Unlike :attr:`_REACHES` the hop node is
    #: ``:TSCallable|TSModule``: a module is the caller of its own top-level code, so it is part of
    #: "what could get here" -- and it can only ever be the *last* node of such a walk, because it
    #: has no incoming ``TS_CALLS`` (verified: 0 on the reference graph). Properties are projected
    #: into maps *before* the cap so only ``$cap`` of them cross the wire.
    _CONE = (
        "MATCH (s:TSCallable) WHERE s.signature IN $sigs AND " + _scoped("s") + " AND s.kind IN $callable_kinds "
        "MATCH (s) (()<-[:TS_CALLS]-(x:TSCallable|TSModule) WHERE " + _scoped("x") + "){{0,{depth}}} (m:TSCallable|TSModule) "
        "WITH DISTINCT m ORDER BY m.id WHERE " + _scoped("m") + " "
        "WITH collect(" + _vertex("m", escape=True) + ") AS found "
        "RETURN size(found) AS total, found[0..$cap] AS page"
    )

    def backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice:
        """Everything that can reach these sinks (see :meth:`TSAnalysisBackend.backward_cone`)."""
        check_depth(depth)
        check_max_nodes(max_nodes)
        self._require_quantified_paths("backward_cone")
        roots = cone_sinks(self.resolve_callable, sinks)
        query = self._CONE.format(depth="" if depth is None else depth)
        row = self._run(query, sigs=[r.callable for r in roots], cap=max_nodes, callable_kinds=sorted(CALLABLE_KINDS), **self._scope_params)[0]
        return Slice(nodes=[self._call_vertex(n) for n in row["page"]], roots=roots, resolved=slice_resolved(roots), total=row["total"])

    #: ``(s:TSCallable|TSModule)`` on the caller side keeps TypeScript's module callers (TS-11) and
    #: still excludes a ghost, whose id sits under the same prefix; ``(t:TSCallable|TSExternal)`` on
    #: the callee side keeps the externals a caller tracing a sink is looking for. Both are ordered
    #: by id, which is the one total order the local backend can also compute -- without it a caller
    #: comparing the two backends would be comparing two arbitrary orders.
    # Both endpoints carry the scope, never just the one the signature pins: ``TS_CALLS`` runs
    # between two application-owned nodes, and this SDK attaches to graphs it did not emit.
    _CALLERS = (
        "MATCH (s:TSCallable|TSModule)-[:TS_CALLS]->(t:TSCallable {signature: $sig}) "
        f"WHERE {_scoped('s')} AND {_scoped('t')} AND t.kind IN $callable_kinds "
        "RETURN " + _vertex("s") + " AS v ORDER BY s.id"
    )
    _CALLEES = (
        "MATCH (s:TSCallable {signature: $sig})-[:TS_CALLS]->(t:TSCallable|TSExternal) "
        f"WHERE {_scoped('s')} AND {_scoped('t')} AND s.kind IN $callable_kinds "
        "RETURN " + _vertex("t") + " AS v ORDER BY t.id"
    )

    def callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """Who calls this, module callers included (see :meth:`TSAnalysisBackend.callers_of`)."""
        sig = self.resolve_callable(name, in_class=in_class, in_module=in_module).callable
        return [self._call_vertex(r["v"]) for r in self._run(self._CALLERS, sig=sig, callable_kinds=sorted(CALLABLE_KINDS), **self._scope_params)]

    def callees_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]:
        """What this calls, externals included (see :meth:`TSAnalysisBackend.callees_of`)."""
        sig = self.resolve_callable(name, in_class=in_class, in_module=in_module).callable
        return [self._call_vertex(r["v"]) for r in self._run(self._CALLEES, sig=sig, callable_kinds=sorted(CALLABLE_KINDS), **self._scope_params)]

    # -----[ paths and flow predicates ]-----
    #: The caller's word for a hop, computed in Cypher so the ORDER BY below sorts by the same
    #: vocabulary :func:`~cldk.analysis.commons.graphs.hop_sort_key` sorts by. Ordering by the raw
    #: ``type(r)`` instead would be just as deterministic and a *different* order, so the two
    #: backends would truncate ``max_paths`` to different witnesses.
    _VIA_CASE = "CASE type(relationships(p)[i]) " + " ".join(f"WHEN '{rel}' THEN '{word}'" for rel, word in VIA.items()) + " ELSE type(relationships(p)[i]) END"

    #: One string per path, ordered exactly as Python would order the tuple ``hop_sort_key`` builds.
    #: ``U+0001`` is the separator rather than ``|`` for one reason: string comparison agrees with
    #: field-by-field comparison **only** when the separator sorts below every character a field can
    #: hold, and ``|`` (0x7C) sorts *above* every lowercase letter. ``elementId`` is the last field
    #: of each hop and breaks the tie between parallel relationships a caller cannot tell apart.
    _PATH_ORDER = (
        "reduce(k = '', i IN range(0, length(p) - 1) | k + " + _VIA_CASE + " + '\\u0001' + coalesce(relationships(p)[i].var, '') "
        "+ '\\u0001' + nodes(p)[i + 1].id + '\\u0001' + elementId(relationships(p)[i]) + '\\u0001')"
    )

    #: ``allShortestPaths`` and not a plain variable-length match: a variable-length pattern
    #: enumerates *trails*, which does not terminate on a real dependence graph, while
    #: ``allShortestPaths`` is a bidirectional BFS. ``$cap`` is ``max_paths + 1`` so one extra row
    #: reports the truncation, rather than a second traversal for a number the caller cannot act on.
    #:
    #: ``all(n IN nodes(p) …)`` puts the two-prefix predicate on **every** node of the path, not
    #: only on the two the ids pin: the SDG types are deliberately outside the audit's
    #: ``_KEEPS_SCOPE``, so an interior node reached over one is not provably this application's.
    _PATHS = (
        "MATCH (a:CanNode:TSBodyNode {{id:$src}}) MATCH (b:CanNode:TSBodyNode {{id:$dst}}) "
        "MATCH p = allShortestPaths((a)-[:{rels}*1..{depth}]->(b)) WHERE all(n IN nodes(p) WHERE " + _scoped("n") + ") "
        "WITH p, " + _PATH_ORDER + " AS key ORDER BY length(p), key LIMIT $cap "
        "RETURN [n IN nodes(p) | {{ref: n.id, kind: n.kind, of: n.of, line: n.start_line, "
        "callable: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_line: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.start_line])}}] AS ns, "
        "[r IN relationships(p) | {{via: type(r), var: r.var, prov: r.prov}}] AS rs"
    )

    #: The same query over the call graph. The ``all()`` predicate carries **both** halves of what
    #: :attr:`_REACHES` puts on each of its hops: ``n:TSCallable`` keeps a module or a ghost off the
    #: *interior* of a path, and the two-prefix predicate keeps another application's callable off
    #: it -- ``TS_CALLS`` runs between two application-owned nodes, so the label alone lets a walk
    #: leave the application on any hop but the first and the last. Same edge set as
    #: :attr:`_REACHES`, so the paths cannot disagree with the boolean that summarises them; Neo4j
    #: inlines an ``all()`` node predicate into the shortest-path search itself.
    _CALL_PATHS = (
        "MATCH (a:TSCallable {{signature:$src}}) WHERE " + _scoped("a") + " "
        "MATCH (b:TSCallable {{signature:$dst}}) WHERE " + _scoped("b") + " "
        "MATCH p = allShortestPaths((a)-[:TS_CALLS*1..{depth}]->(b)) WHERE all(n IN nodes(p) WHERE n:TSCallable AND " + _scoped("n") + ") "
        "WITH p, " + _PATH_ORDER + " AS key ORDER BY length(p), key LIMIT $cap "
        "RETURN [n IN nodes(p) | " + _vertex("n", escape=True) + "] AS ns, "
        "[r IN relationships(p) | {{via: type(r), var: null, prov: null}}] AS rs"
    )

    def _paths(self, query: str, node_of, a: SliceNode, b: SliceNode, *, src: str, dst: str, max_paths: int) -> FlowPaths:
        """Run one of the two path queries and build the result. The two differ in what a node is
        and nothing else, so the ordering, the cap and the completeness flag live here once.
        ``a``/``b`` are the resolved endpoints (for the self-question's message); ``src``/``dst`` are
        the keys the query matches them by."""
        check_distinct_endpoints(a, b)
        rows = self._run(query, src=src, dst=dst, cap=max_paths + 1, **self._scope_params)
        paths = [flow_path([node_of(n) for n in r["ns"]], [(e["via"], e["var"], e["prov"]) for e in r["rs"]], via=VIA) for r in rows[:max_paths]]
        return FlowPaths(paths=paths, complete=len(rows) <= max_paths)

    # Argument validation precedes name resolution on every accessor below, as it does on the local
    # backend: a malformed ``depth``/``max_paths`` is a ``ValueError`` before any round trip,
    # whichever backend answers.
    def paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How a value reaches another value (see :meth:`TSAnalysisBackend.paths_between`)."""
        check_depth(depth)
        check_max_paths(max_paths)
        a = self.resolve_value(src, within=src_within)
        b = self.resolve_value(dst, within=dst_within)
        query = self._PATHS.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth)
        return self._paths(query, self._slice_row, a, b, src=a.ref, dst=b.ref, max_paths=max_paths)

    def call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths:
        """How one callable reaches another (see :meth:`TSAnalysisBackend.call_paths_between`)."""
        check_depth(depth)
        check_max_paths(max_paths)
        a = self.resolve_callable(src)
        b = self.resolve_callable(dst)
        query = self._CALL_PATHS.format(depth="" if depth is None else depth)
        return self._paths(query, self._call_vertex, a, b, src=a.callable, dst=b.callable, max_paths=max_paths)

    #: ``WITH DISTINCT m`` before the membership test is what makes this a pruning BFS instead of a
    #: trail enumeration. Both **endpoints** are pinned by an application-stamped id -- ``$src`` is
    #: a ``ref`` minted by :meth:`resolve_value` and ``$dsts`` are ids collected by the two-prefix
    #: scoped :attr:`_CALLEE_VALUES` -- so the ``all(n IN nodes(p) …)`` is what the endpoints do not
    #: give: the same interior predicate :attr:`_SLICE` and :attr:`_PATHS` carry, for the same
    #: reason (an SDG edge joins two application-owned nodes).
    _VALUE_REACHES = (
        "MATCH p = (a:CanNode:TSBodyNode {{id:$src}})-[:{rels}*1..{depth}]->(m:TSBodyNode) "
        "WHERE all(n IN nodes(p) WHERE " + _scoped("n") + ") "
        "WITH DISTINCT m WHERE m.id IN $dsts RETURN count(m) > 0 AS ok"
    )

    #: Every value that *enters* ``$sig`` -- in TypeScript, its parameters. Scoped, because a
    #: signature is not application-stamped the way an id is.
    _CALLEE_VALUES = f"MATCH (c:TSCallable {{signature:$sig}})-[:TS_HAS_BODY_NODE]->(b:TSBodyNode {{kind:'formal_in'}}) WHERE {_scoped('c')} RETURN collect(b.id) AS ids"

    def _value_reaches(self, src: str, dsts: List[str], depth: int | None) -> bool:
        """Does the value at ``src`` reach any of ``dsts``? The one predicate both flow queries run,
        which is what makes ``flows_to_argument`` implies ``flows_to_call`` a fact about their
        *targets* rather than an agreement between two pieces of Cypher."""
        if not dsts:
            return False
        query = self._VALUE_REACHES.format(rels=SDG_REL_PATTERN, depth="" if depth is None else depth)
        return bool(self._run(query, src=src, dsts=dsts, **self._scope_params)[0]["ok"])

    def flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach any argument of a call to ``callee``
        (see :meth:`TSAnalysisBackend.flows_to_call`)?"""
        check_depth(depth)
        root = self.resolve_value(src, within=within)
        sig = self.resolve_callable(callee).callable
        targets = [i for i in self._run(self._CALLEE_VALUES, sig=sig, **self._scope_params)[0]["ids"] if i != root.ref]
        return self._value_reaches(root.ref, targets, depth)

    def flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool:
        """Does this value reach ``callee``'s ``arg``
        (see :meth:`TSAnalysisBackend.flows_to_argument`)?"""
        check_depth(depth)
        root = self.resolve_value(src, within=within)
        target = self.resolve_value(arg, within=callee).ref
        return target != root.ref and self._value_reaches(root.ref, [target], depth)
