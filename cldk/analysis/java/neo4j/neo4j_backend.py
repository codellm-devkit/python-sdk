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

**The graph it reads** (``schema.neo4j.json`` at the 3.0.1 tag, contract ``2.0.0``, verified
against the reference graph): ``:JApplication`` is keyed by **``name``** and stamps
``analyzer_version``; every project-owned node carries a ``can://java/<app>/…`` ``id`` and the
marker label ``:JCanNode``. ``:JModule`` holds the repo-relative path in ``file_key``;
``:JType``/``:JCallable``/``:JExternal`` share the merge label ``:JSymbol`` and are told apart by
their own label plus ``kind``; ``:JField``, ``:JVariable``, ``:JEnumConstant``,
``:JRecordComponent`` and ``:JBodyNode`` are keyed by ``id``. Containment is ``J_HAS_MODULE`` /
``J_DECLARES`` / ``J_HAS_METHOD`` / ``J_HAS_FIELD`` / ``J_DECLARES_VAR`` /
``J_HAS_ENUM_CONSTANT`` / ``J_HAS_RECORD_COMPONENT``; annotations are ``J_ANNOTATED_BY``; a call
site is a ``:JBodyNode {kind:'call'}`` under ``J_HAS_BODY_NODE`` resolving over ``J_RESOLVES_TO``;
calls are ``J_CALLS {weight, prov}``. **There is no ``_module`` property anywhere**, and none of the
2.4.1 vocabulary this backend used to read (``:JCompilationUnit``, ``J_HAS_UNIT``,
``J_HAS_CALLABLE``, ``:JParameter``, ``:JCallSite``, ``:JComment``, the CRUD labels) exists — a
graph that still speaks it is refused at attach by :meth:`_probe_schema` (J-9).

**Scope.** Java has exactly one id namespace, so the application scope is the single prefix
``can://java/<app>/`` that :func:`_scoped` spells, or the ``:JApplication {name: $app}`` anchor a
statement walks out from. Nothing else distinguishes two applications in one database: a module
``file_key``, a qualified class name and a method signature are all shared vocabulary.

**Seek labels.** Every statement anchors on the bare specific label; ``:JCanNode`` is used
nowhere. Not because the bare label always seeks — ``:JCallable`` owns no id index at all (only a
range index on ``name`` and the ``code``/``docstring`` fulltext), so bare ``:JCallable`` plans a
label scan — but because of what the statements here actually are. The two prefix-scoped ones both
fan out over relationships from every matched callable, and measured on ThingsBoard the traversal
dominates: swapping the anchor moves the wall clock by under 1% while ``:JCanNode`` adds a quarter
again as many db hits (5.65M against 4.55M on the call sites, 1.89M against 0.73M on the call
edges). Everything else is anchored on ``(:JApplication {name: $app})`` and never scans at all.
``:JCanNode``'s own index is not a constraint and spans 615,329 nodes, so where it *is* the only
seek it still loses — 118 ms against 24 on a whole-application prefix; it wins only a per-module
prefix, which no statement here issues. See ``test_no_statement_anchors_on_the_marker_label`` and
the table in Task 3 of the leg-3a plan.

**Strategy.** Unlike the Python and TypeScript Neo4j backends, which answer each accessor with its
own statement, this one rebuilds the canonical :class:`JApplication` from the graph and then answers
every query with the *same* logic the in-memory backend runs over the same models. The application
is built on first use, not at attach, and cached — **nine round trips in all**: three at attach (the
relationship-type fingerprint, the version probe, the module fetch) and six on first use (one
containment-subtree traversal instead of one query per parent, then call sites, imports, call edges,
artifacts and dependencies).

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
callable-only graph and drops them (``get_external_symbols`` arrives in 3b).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from functools import cached_property
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple

import networkx as nx

from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.analysis.java.backend import CRUD_UNAVAILABLE, CRUDRow, JavaAnalysisBackend, duplicate_type_name, unhomed_endpoint
from cldk.analysis.java.neo4j import reconstruct as R
from cldk.models.java import JGraphEdges
from cldk.models.java.models import (
    JApplication,
    JBodyNode,
    JCallable,
    JCallableParameter,
    JCallGraphEdge,
    JCallSite,
    JComment,
    JCompilationUnit,
    JDecorator,
    JField,
    JMethodDetail,
    JType,
)
from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency
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
            ``:JApplication {name: <application_name>}`` and the id prefix is
            ``can://java/<application_name>/``.
    """

    #: Relationship types every supported graph has; a graph missing any was emitted by another
    #: generation (2.4.1 shares only ``J_CALLS``) and is refused at attach.
    _REQUIRED_RELATIONSHIP_TYPES: FrozenSet[str] = frozenset({"J_HAS_MODULE", "J_HAS_METHOD", "J_HAS_BODY_NODE", "J_CALLS"})
    #: The oldest codeanalyzer-java whose graph this backend serves: 3.0.0 stamped contract 2.2.0,
    #: 3.0.1 holds 2.0.0 — the ``can://`` id grammar and body-node shape every statement here reads.
    _ANALYZER_FLOOR = (3, 0, 1)
    #: Set by :meth:`_probe_schema`; the class-level ``None`` is for the ``object.__new__`` seam.
    _analyzer_version: Tuple[int, int, int] | None = None
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
        self._module_props: Dict[str, Dict[str, Any]] = self._load_modules()
        self._modules: List[str] = list(self._module_props)
        self._call_graph = None

    # -----[ scope ]-----
    @property
    def _scope_prefix(self) -> str:
        """``can://java/<app>/`` — the trailing slash keeps ``app`` from matching ``app-b``."""
        return f"can://java/{self.application_name}/"

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
        missing = self._REQUIRED_RELATIONSHIP_TYPES - found
        if missing:
            raise GraphSchemaMismatch(expected=set(self._REQUIRED_RELATIONSHIP_TYPES), found=found, missing=missing)
        rows = self._run("OPTIONAL MATCH (a:JApplication {name: $app}) RETURN count(a) AS n, a.analyzer_version AS v", app=self.application_name)
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

    def _load_modules(self) -> Dict[str, Dict[str, Any]]:
        """``file_key -> module properties`` for the application's modules."""
        rows = self._run(
            "MATCH (:JApplication {name: $app})-[:J_HAS_MODULE]->(m:JModule) RETURN m.file_key AS k, properties(m) AS p ORDER BY m.file_key", app=self.application_name
        )
        return {r["k"]: r["p"] for r in rows}

    # =====================================================================================
    # Reconstruction: eight statements, then the canonical JApplication.
    # =====================================================================================
    #: The whole containment subtree beneath the application's modules, in one statement: the
    #: ``*0..`` walk reaches every module, type (nested and local), callable and field, and the last
    #: hop yields each one's children as ``(parent id, relationship, child)`` rows. Anchored on the
    #: application, so it cannot leave it. ``J_HAS_FIELD`` is in the walk as well as in the child
    #: hop because a field is itself an annotation target (``J_ANNOTATED_BY`` runs from a type, a
    #: callable *or* a field), so it has to be reachable as a parent.
    _SUBTREE = (
        "MATCH (:JApplication {name: $app})-[:J_HAS_MODULE]->(root:JModule) "
        "MATCH (root)-[:J_DECLARES|J_HAS_METHOD|J_HAS_FIELD*0..]->(par)"
        "-[r:J_DECLARES|J_HAS_METHOD|J_HAS_FIELD|J_DECLARES_VAR|J_HAS_ENUM_CONSTANT|J_HAS_RECORD_COMPONENT|J_ANNOTATED_BY]->(n) "
        "RETURN par.id AS pk, type(r) AS rel, properties(n) AS p, properties(r) AS e, labels(n) AS labels "
        "ORDER BY n.start_line, n.name"
    )

    def _subtree_rows(self) -> Dict[str, List[_Child]]:
        rows = self._run(self._SUBTREE, app=self.application_name)
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
            "MATCH (:JApplication {name: $app})-[:J_HAS_MODULE]->(m:JModule)-[r:J_IMPORTS]->() RETURN m.file_key AS k, properties(r) AS e ORDER BY m.file_key",
            app=self.application_name,
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

    def _artifact_rows(self) -> List[Dict[str, Any]]:
        return self._run(
            "MATCH (:JApplication {name: $app})-[:HAS_ARTIFACT]->(a:Artifact) "
            "OPTIONAL MATCH (a)-[:DEFINES_CONFIG]->(ck:ConfigKey) "
            "RETURN properties(a) AS p, collect(properties(ck)) AS cks",
            app=self.application_name,
        )

    def _dependency_rows(self) -> List[Dict[str, Any]]:
        return self._run(
            "MATCH (:JApplication {name: $app})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(p:Package) "
            "RETURN properties(r) AS rel, properties(p) AS pkg, a.id AS declared_in ORDER BY p.name",
            app=self.application_name,
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
            id=f"can://java/{self.application_name}",
            symbol_table=symbol_table,
            call_graph=[JCallGraphEdge(src=r["src"], dst=r["dst"], prov=list(r["prov"] or []), weight=r["weight"] or 1) for r in self._call_edge_rows()],
            artifacts={
                a.path: a
                for a in (
                    R.artifact(r["p"], config_keys=[R.config_key(p) for p in sorted((c for c in r["cks"] if c), key=lambda c: c["id"])])
                    for r in sorted(self._artifact_rows(), key=lambda r: r["p"]["path"])
                )
            },
            dependencies=[R.dependency(r["rel"], r["pkg"], r["declared_in"]) for r in self._dependency_rows()],
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

    @staticmethod
    def _calling_lines(tsu: TreesitterJava, source: JCallable, target: JCallable) -> List[int]:
        return tsu.get_calling_lines(source.code, target.signature) if source.code else []

    def get_call_graph(self) -> nx.DiGraph:
        """Build (and cache) the call graph keyed by ``"<type fqn>.<signature>"`` (J-1): node attrs
        ``method_detail`` / ``kind="callable"``; edge attrs ``type="CALL_DEP"``, ``weight``,
        ``calling_lines``. Edges to external targets are dropped (see the module docstring)."""
        if self._call_graph is not None:
            return self._call_graph
        cg = nx.DiGraph()
        tsu = TreesitterJava()
        for edge in self.application.call_graph:
            src, src_detail = self._node_of(edge.src)
            dst, dst_detail = self._node_of(edge.dst)
            cg.add_node(src, method_detail=src_detail, kind="callable")
            cg.add_node(dst, method_detail=dst_detail, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=edge.weight, calling_lines=self._calling_lines(tsu, src_detail.method, dst_detail.method))
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
        tsu = TreesitterJava()
        edges = self._st_edges_into(qualified_class_name, method_signature) if is_target else self._st_edges_from(qualified_class_name, method_signature)
        for source, target in edges:
            src, dst = f"{source.klass}.{source.method.signature}", f"{target.klass}.{target.method.signature}"
            cg.add_node(src, method_detail=source, kind="callable")
            cg.add_node(dst, method_detail=target, kind="callable")
            cg.add_edge(src, dst, type="CALL_DEP", weight=1, calling_lines=self._calling_lines(tsu, source.method, target.method))
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
        return {ck.id: PyConfigKey(**ck.model_dump()) for a in self.application.artifacts.values() for ck in a.config_keys}

    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Always empty, and not a projection gap: codeanalyzer-java 3.0.1 emits no code-to-config
        edges at all (there is no such relationship type in the Java graph, and no ``config_uses``
        on the Java wire), so the in-memory backend answers the same way."""
        return []

    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Always empty, as on the in-memory backend: codeanalyzer-java 3.0.1 has no config-read
        detector."""
        return []

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
