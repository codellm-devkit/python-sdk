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

"""Every statement ``JNeo4jBackend`` issues stays inside one application (the leg-1.6 audit, for
Java).

The SDK attaches to a graph someone else deployed, and a database holding several applications is
the expected deployment -- the leg-3 reference graph holds daytrader8 and ThingsBoard side by side.
A **qualified name** is not application-stamped: two applications can declare ``shared.Widget`` in
a file with the same repo-relative path. On a 3.0.1 graph the only scope is the ``can://`` id
prefix and the ``:JApplication {name}`` anchor: there is no ``_module`` property anywhere, and Java
has exactly **one** prefix, ``can://java/<app>/``, spelled by :func:`_scoped` as
``x.id STARTS WITH $prefix``.

Two nets, as in the Python and TypeScript twins:

* a **fake two-application graph** in the 3.0.1 vocabulary carrying no ``_module``, whose two
  applications declare the same module key, the same qualified class name and the same method
  signature -- every child is named for its own application, so a leak is visible by name, not by
  count; and
* an **audit** that harvests every Cypher statement on the class -- class-level constants and the
  ones written inline at each ``self._run(`` site -- and judges each one.

Like every other Neo4j test here this suite never emits ``CREATE``/``MERGE``/``SET``/``DELETE``.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from cldk.analysis.java.neo4j import neo4j_backend
from cldk.analysis.java.neo4j.neo4j_backend import JNeo4jBackend

from .conftest import FakeDriver

APP_A, APP_B = "app_a", "app_b"
SHARED_MODULE = "src/main/java/shared/Widget.java"
OTHER_MODULE = "src/main/java/shared/Helper.java"
CLASS_FQN = "shared.Widget"
METHOD_SIG = "render(java.lang.String)"


# =====================================================================================
# The fixture graph: two applications, colliding keys and names, no ``_module`` anywhere.
# =====================================================================================
class _Graph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Tuple[set, Dict[str, Any]]] = {}
        self.edges: List[Tuple[str, str, str, Dict[str, Any]]] = []

    def node(self, node_id: str, labels: Sequence[str], **props: Any) -> str:
        unprefixed = {"JApplication", "JAnnotation", "JPackage", "Artifact", "Package", "ConfigKey"}
        marked = set(labels) if unprefixed & set(labels) else set(labels) | {"JCanNode"}
        self.nodes[node_id] = (marked, {"id": node_id, **props})
        return node_id

    def edge(self, src: str, rel: str, dst: str, **props: Any) -> None:
        if not any(e[:3] == (src, rel, dst) for e in self.edges):  # MERGE: one edge per (src, type, dst)
            self.edges.append((src, rel, dst, props))


_PARAMS_JSON = '[{"name":"%s","type":"java.lang.String","modifiers":[],"decorators":[],"is_variadic":false}]'


def _build() -> _Graph:
    g = _Graph()
    g.node("Named", ["JAnnotation"], name="Named")
    g.node("java.util", ["JPackage"], name="java.util")
    for app, tag in ((APP_A, "alpha"), (APP_B, "beta")):
        app_id = g.node(f"can://java/{app}", ["JApplication"], name=app, schema_version="2.0.0", analyzer_name="codeanalyzer-java", analyzer_version="3.0.1")
        # Both applications declare the same two repo-relative paths -- the key collision.
        mod = g.node(f"can://java/{app}/{SHARED_MODULE}", ["JModule"], file_key=SHARED_MODULE, package="shared", content_hash=f"{tag}hash")
        g.edge(app_id, "J_HAS_MODULE", mod)
        g.edge(mod, "J_IMPORTS", "java.util", spellings=[f"java.util.{tag.title()}List"], is_static=None)
        helper_mod = g.node(f"can://java/{app}/{OTHER_MODULE}", ["JModule"], file_key=OTHER_MODULE, package="shared", content_hash=f"{tag}helper")
        g.edge(app_id, "J_HAS_MODULE", helper_mod)

        # shared.Widget in both applications, same qualified name, different members.
        cls = g.node(
            f"{mod}/Widget",
            ["JSymbol", "JType"],
            name="Widget",
            kind="class",
            modifiers=["public"],
            base_types=["shared.Base"],
            interfaces=["shared.Face"],
            docstring=f"{tag} class doc",
            start_line=3,
            end_line=40,
        )
        g.edge(mod, "J_DECLARES", cls)
        g.edge(cls, "J_ANNOTATED_BY", "Named", arguments=[f'"{tag}"'])
        method = g.node(
            f"{cls}/{METHOD_SIG}",
            ["JSymbol", "JCallable"],
            name="render",
            signature=METHOD_SIG,
            kind="method",
            declaration=f"public String render(String {tag})",
            return_type="java.lang.String",
            parameters_json=_PARAMS_JSON % tag,
            modifiers=["public"],
            code=f"public String render(String {tag}) {{ return helper(); }}",
            docstring=f"{tag} method doc",
            cyclomatic_complexity=1,
            referenced_types=[f"shared.{tag.title()}"],
            accessed_fields=[f"shared.Widget.{tag}_attr"],
            start_line=6,
            end_line=8,
        )
        g.edge(cls, "J_HAS_METHOD", method)
        ctor = g.node(f"{cls}/<init>()", ["JSymbol", "JCallable"], name="<init>", signature="<init>()", kind="constructor", is_implicit=True)
        g.edge(cls, "J_HAS_METHOD", ctor)
        field = g.node(f"{cls}#field#{tag}_attr", ["JField"], name=f"{tag}_attr", type="int", modifiers=["private"], start_line=4, end_line=4)
        g.edge(cls, "J_HAS_FIELD", field)
        nested = g.node(f"{cls}/Inner", ["JSymbol", "JType"], name="Inner", kind="class", modifiers=["static"], start_line=20, end_line=30)
        g.edge(cls, "J_DECLARES", nested)
        nested_m = g.node(f"{nested}/ping()", ["JSymbol", "JCallable"], name="ping", signature="ping()", kind="method", code=f"{tag} inner code", start_line=21, end_line=22)
        g.edge(nested, "J_HAS_METHOD", nested_m)
        local = g.node(f"{method}/$anon$0", ["JSymbol", "JType"], name="$anon$0", kind="class", start_line=7, end_line=7)
        g.edge(method, "J_DECLARES", local)
        # A *sibling* callable declaring its own ``$anon$0`` -- the numbering is per declaring
        # callable, so this collides with the one above unless the callable is part of the key.
        ctor_local = g.node(f"{ctor}/$anon$0", ["JSymbol", "JType"], name="$anon$0", kind="class", start_line=5, end_line=5)
        g.edge(ctor, "J_DECLARES", ctor_local)
        var = g.node(f"{method}#{tag}_var@7", ["JVariable"], name=f"{tag}_var", type="int", initializer="1", start_line=7, end_line=7)
        g.edge(method, "J_DECLARES_VAR", var)

        # A helper the method calls, in the other module, plus one external target.
        helper_cls = g.node(f"{helper_mod}/Helper", ["JSymbol", "JType"], name="Helper", kind="class", start_line=1, end_line=9)
        g.edge(helper_mod, "J_DECLARES", helper_cls)
        helper = g.node(f"{helper_cls}/help()", ["JSymbol", "JCallable"], name="help", signature="help()", kind="method", code=f"{tag} help", start_line=2, end_line=3)
        g.edge(helper_cls, "J_HAS_METHOD", helper)
        call = g.node(
            f"{method}@7:12",
            ["JBodyNode"],
            kind="call",
            method_name="help",
            receiver_expr="helper",
            receiver_type="shared.Helper",
            return_type="java.lang.String",
            accessibility="public",
            is_static_call=False,
            argument_types=[],
            argument_expr=[],
            start_line=7,
            end_line=7,
        )
        g.edge(method, "J_HAS_BODY_NODE", call)
        g.edge(call, "J_RESOLVES_TO", helper)
        entry = g.node(f"{method}@entry", ["JBodyNode"], kind="entry")
        g.edge(method, "J_HAS_BODY_NODE", entry)
        g.edge(method, "J_CALLS", helper, weight=1, prov=["declared", "rta"])
        ext = g.node(
            f"can://java/{app}/@external/java.io.PrintStream/println{'A' if app == APP_A else 'B'}(java.lang.String)",
            ["JSymbol", "JExternal"],
            kind="method",
            signature=f"println{'A' if app == APP_A else 'B'}(java.lang.String)",
            declaring_type="java.io.PrintStream",
        )
        g.edge(method, "J_CALLS", ext, weight=1, prov=["rta"])

        # The artifact layer: unprefixed labels, reached only through the application anchor.
        art = g.node(
            f"can://artifact/{app}/pom.xml",
            ["Artifact"],
            path="pom.xml",
            format="xml",
            roles=["manifest"],
            size_bytes=10,
            sha256=f"{tag}sha",
            source=f"<{tag}/>",
            extraction="none",
        )
        g.edge(app_id, "HAS_ARTIFACT", art)
        ck = g.node(f"{art}@key/{tag}.key", ["ConfigKey"], key=f"{tag}.key", namespace="pom", value=tag, references=[])
        g.edge(art, "DEFINES_CONFIG", ck)
        pkg = g.node(f"pkg:maven/org.{tag}/{tag}-core", ["Package"], ecosystem="maven", group=f"org.{tag}", name=f"{tag}-core")
        g.edge(art, "DECLARES_DEPENDENCY", pkg, spec="1.0", kind="runtime", extras=[], prov=["declared"], direct=True)
    return g


GRAPH = _build()


# =====================================================================================
# A small evaluator for the linear-chain statements the backend issues. It honours a scope
# predicate **only when the statement carries it** -- an unscoped statement sees both applications.
# =====================================================================================
_NODE = re.compile(r"\((\w*)(?::([\w|:]+))?(?: \{([^}]*)\})?\)")
_HOP = re.compile(r"(<)?-\[(\w*)(?::([\w|]+))?(\*0\.\.)?\]-(>)?")
_COND = re.compile(r"(\w+)\.(\w+) (STARTS WITH|IN|=|<>) (\$\w+|'[^']*')|(\$\w+) IN (\w+)\.(\w+)|(\w+)\.(\w+) IS NOT NULL")


def _value(token: str, params: Dict[str, Any]) -> Any:
    return params[token[1:]] if token.startswith("$") else token.strip("'")


def _node_ok(node_id: str, labels: str | None, props: str | None, params: Dict[str, Any]) -> bool:
    node_labels, node_props = GRAPH.nodes[node_id]
    if labels and not any(set(alt.split(":")) <= node_labels for alt in labels.split("|")):
        return False
    for item in filter(None, (props or "").split(", ")):
        key, token = item.split(": ")
        if node_props.get(key) != _value(token, params):
            return False
    return True


def _walk(src: str, rels: set, back: bool, var_len: bool) -> List[Tuple[str, Dict[str, Any]]]:
    step = lambda n: [((s if back else d), {"_type": r, **p}) for s, r, d, p in GRAPH.edges if r in rels and (d if back else s) == n]
    if not var_len:
        return step(src)
    seen, out, frontier = {src}, [(src, {})], [src]
    while frontier:
        frontier = [n for cur in frontier for n, _ in step(cur) if n not in seen]
        seen.update(frontier)
        out += [(n, {}) for n in frontier]
    return out


def _tokens(pattern: str) -> List[Tuple[str, Any]]:
    """A linear chain as ``[("node", (var, labels, props)), ("hop", (rvar, rels, back, var_len)), …]``."""
    out, pos = [], 0
    while pos < len(pattern):
        m = _NODE.match(pattern, pos)
        out.append(("node", (m.group(1) or f"_{pos}", m.group(2), m.group(3))))
        pos = m.end()
        h = _HOP.match(pattern, pos)
        if not h:
            break
        out.append(("hop", (h.group(2), set((h.group(3) or "").split("|")), bool(h.group(1)), bool(h.group(4)))))
        pos = h.end()
    return out


def _match(pattern: str, rows: List[Dict[str, Any]], params: Dict[str, Any], optional: bool) -> List[Dict[str, Any]]:
    tokens = _tokens(pattern)
    new_vars = [t[0] for kind, t in tokens if kind == "node"] + [t[0] for kind, t in tokens if kind == "hop" and t[0]]
    out: List[Dict[str, Any]] = []
    for row in rows:
        cur = [dict(row)]
        prev_var = None
        for kind, t in tokens:
            if kind == "node":
                var, labels, props = t
                nxt = []
                for b in cur:
                    if var in b:
                        landed = b.pop("_next", b[var])
                        if b[var] is not None and landed == b[var] and _node_ok(b[var], labels, props, params):
                            nxt.append(b)
                    elif "_next" in b:
                        n = b.pop("_next")
                        if _node_ok(n, labels, props, params):
                            nxt.append({**b, var: n})
                    else:
                        nxt += [{**b, var: n} for n in GRAPH.nodes if _node_ok(n, labels, props, params)]
                cur, prev_var = nxt, var
            else:
                rvar, rels, back, var_len = t
                cur = [{**b, "_next": n, **({rvar: e} if rvar else {})} for b in cur for n, e in _walk(b[prev_var], rels, back, var_len)]
        if cur:
            out += cur
        elif optional:
            out.append({**row, **{v: None for v in new_vars if v not in row}})
    return out


def _where(clause: str, rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    def ok(b: Dict[str, Any]) -> bool:
        for m in _COND.finditer(clause):
            if m.group(1):
                actual = GRAPH.nodes[b[m.group(1)]][1].get(m.group(2))
                op, expected = m.group(3), _value(m.group(4), params)
                if not {
                    "STARTS WITH": lambda: str(actual).startswith(expected),
                    "IN": lambda: actual in expected,
                    "=": lambda: actual == expected,
                    "<>": lambda: actual != expected,
                }[op]():
                    return False
            elif m.group(5):
                if _value(m.group(5), params) not in (GRAPH.nodes[b[m.group(6)]][1].get(m.group(7)) or []):
                    return False
            elif GRAPH.nodes[b[m.group(8)]][1].get(m.group(9)) is None:
                return False
        return True

    return [b for b in rows if ok(b)]


def _split_top(text: str, sep: str) -> List[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        cur += ch
        if depth == 0 and cur.endswith(sep):
            parts.append(cur[: -len(sep)])
            cur = ""
    parts.append(cur)
    return parts


def _expr(e: str, b: Dict[str, Any], params: Dict[str, Any]) -> Any:
    e = e.strip()
    if e.startswith("properties("):
        bound = b.get(e[11:-1])
        if isinstance(bound, dict):  # a relationship variable: its edge properties
            return {k: v for k, v in bound.items() if k != "_type" and v is not None}
        return {k: v for k, v in GRAPH.nodes[bound][1].items() if v is not None} if bound else None
    if e.startswith("labels("):
        return sorted(GRAPH.nodes[b[e[7:-1]]][0]) if b.get(e[7:-1]) else None
    if e.startswith("type("):
        return b[e[5:-1]]["_type"]
    if e.startswith("coalesce("):
        return next((v for v in (_expr(a, b, params) for a in _split_top(e[9:-1], ", ")) if v is not None), None)
    if e.startswith("'"):
        return e.strip("'")
    var, _, prop = e.partition(".")
    if b.get(var) is None:
        return None
    return b[var].get(prop) if isinstance(b[var], dict) else GRAPH.nodes[b[var]][1].get(prop)


def _return(clause: str, rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    order = re.search(r" ORDER BY (.*?)(?= LIMIT|$)", clause)
    clause = re.sub(r" ORDER BY .*?(?= LIMIT|$)", "", clause)
    limit = re.search(r" LIMIT (\d+)$", clause)
    clause = clause[: limit.start()] if limit else clause
    distinct = clause.startswith("DISTINCT ")
    items = [(i.rsplit(" AS ", 1) if " AS " in i else (i, i)) for i in _split_top(clause[9:] if distinct else clause, ", ")]
    plain = [(e, a) for e, a in items if not e.startswith("collect(")]
    aggs = [(e, a) for e, a in items if e.startswith("collect(")]
    if order:
        keys = [k.strip() for k in order.group(1).split(",")]
        rows = sorted(rows, key=lambda b: tuple((v is None, v if v is not None else 0) for v in (_expr(k, b, params) for k in keys)))
    if aggs:
        groups: Dict[tuple, Dict[str, Any]] = {}
        for b in rows:
            key = tuple(repr(_expr(e, b, params)) for e, _ in plain)
            g = groups.setdefault(key, {a: _expr(e, b, params) for e, a in plain} | {a: [] for _, a in aggs})
            for e, a in aggs:
                inner = e[len("collect(") : -1]
                v = _expr(inner[len("DISTINCT ") :] if inner.startswith("DISTINCT ") else inner, b, params)
                if v is not None and v not in g[a]:
                    g[a].append(v)
        out = list(groups.values())
    else:
        out = [{a: _expr(e, b, params) for e, a in items} for b in rows]
    if distinct:
        seen, deduped = set(), []
        for r in out:
            if repr(r) not in seen:
                seen.add(repr(r))
                deduped.append(r)
        out = deduped
    return out[: int(limit.group(1))] if limit else out


def fake_cypher(query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate one read statement against :data:`GRAPH` -- honestly (see the module docstring)."""
    rows: List[Dict[str, Any]] = [{}]
    clauses = re.split(r"(?<!STARTS)(?<!OPTIONAL) (?=MATCH |OPTIONAL MATCH |WHERE |WITH |RETURN )", query.strip())
    for clause in clauses:
        kw, _, body = clause.partition(" ")
        if kw == "OPTIONAL":
            rows = _match(body[len("MATCH ") :], rows, params, optional=True)
        elif kw == "MATCH":
            rows = _match(body, rows, params, optional=False)
        elif kw == "WHERE":
            rows = _where(body, rows, params)
        elif kw == "RETURN":
            return _return(body, rows, params)
    raise AssertionError(f"statement has no RETURN: {query!r}")


def _responder(query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """:func:`fake_cypher`, after asserting every prefix parameter bound at run time is application
    A's -- the audit below judges spellings; this judges the values."""
    if "prefix" in params:
        assert params["prefix"].startswith(f"can://java/{APP_A}/"), f"$prefix bound to {params['prefix']!r}, outside application A's scope"
    if "app" in params:
        assert params["app"] == APP_A, f"$app bound to {params['app']!r}"
    return fake_cypher(query, params)


def _backend() -> JNeo4jBackend:
    """A backend scoped to application A over a fake driver holding A and B."""
    return JNeo4jBackend._from_driver(FakeDriver(responder=_responder), application_name=APP_A)


# =====================================================================================
# The fake graph is what a 3.0.1 graph is
# =====================================================================================
def test_the_fake_graph_carries_no_module_property_and_no_v1_vocabulary():
    assert GRAPH.nodes and not any("_module" in props for _, props in GRAPH.nodes.values())
    assert not any(rel in {"J_HAS_UNIT", "J_HAS_CALLABLE", "J_HAS_PARAMETER", "J_HAS_CALLSITE", "J_HAS_COMMENT"} for _, rel, _, _ in GRAPH.edges)
    prefixes = tuple(f"can://java/{app}/" for app in (APP_A, APP_B))
    assert all(nid.startswith(prefixes) for nid, (labels, _) in GRAPH.nodes.items() if "JCanNode" in labels)


def test_the_scope_is_one_prefix_and_the_application_name():
    backend = _backend()
    assert backend._scope_prefix == f"can://java/{APP_A}/"
    assert backend.application_name == APP_A
    assert sorted(backend._modules) == [OTHER_MODULE, SHARED_MODULE]


# =====================================================================================
# Leak tests: application A's answers and nothing of B's
# =====================================================================================
def test_symbol_table_honours_a_shared_module_key():
    table = _backend().get_symbol_table()
    assert set(table) == {SHARED_MODULE, OTHER_MODULE}, "the module key collision leaked or lost a module"
    unit = table[SHARED_MODULE]
    assert unit.file_path == SHARED_MODULE and unit.package == "shared"
    assert unit.content_hash == "alphahash", "application B's module answered for the shared key"
    assert set(unit.types) == {"Widget"}
    assert [i.path for i in unit.import_declarations] == ["java.util.AlphaList"]


def test_get_class_does_not_leak_another_applications_children():
    cls = _backend().get_class(CLASS_FQN)
    assert cls is not None, "the application's own class came back empty -- the statement scoped on something the graph does not carry"
    assert cls.qualified_name == CLASS_FQN
    assert set(cls.callables) == {METHOD_SIG, "<init>()"}
    assert cls.callables[METHOD_SIG].declaration == "public String render(String alpha)"
    assert [f.name for f in cls.field_declarations] == ["alpha_attr"]
    assert cls.annotations == ['@Named("alpha")']
    assert [c.content for c in cls.comments] == ["alpha class doc"]
    assert set(cls.types) == {"Inner"}


def test_get_all_classes_covers_nested_and_local_types_of_this_application_only():
    backend = _backend()
    expected = {CLASS_FQN, "shared.Widget.Inner", f"shared.Widget.{METHOD_SIG}.$anon$0", "shared.Widget.<init>().$anon$0", "shared.Helper"}
    assert set(backend.get_all_classes()) == expected, "a local class is not keyed by the callable that declares it (J-1 erratum)"
    assert backend.get_all_classes()[CLASS_FQN] == backend.get_class(CLASS_FQN)
    assert backend.get_java_file(CLASS_FQN) == SHARED_MODULE
    assert backend.get_java_file("shared.NoSuchClass") is None


def test_get_method_and_parameters_resolve_inside_the_application_only():
    backend = _backend()
    method = backend.get_method(CLASS_FQN, METHOD_SIG)
    assert method is not None and method.code.endswith("return helper(); }")
    assert "alpha" in method.code and "beta" not in method.code
    assert [p.name for p in backend.get_method_parameters(CLASS_FQN, METHOD_SIG)] == ["alpha"]
    assert backend.get_method(CLASS_FQN, "noSuchMethod()") is None
    assert backend.get_all_methods_in_class(CLASS_FQN) == {METHOD_SIG: method}
    assert set(backend.get_all_constructors(CLASS_FQN)) == {"<init>()"}


def test_call_sites_come_from_this_applications_call_body_nodes():
    sites = _backend().get_method(CLASS_FQN, METHOD_SIG).call_sites
    assert [(s.method_name, s.callee_signature, s.receiver_type, s.start_line) for s in sites] == [("help", "help()", "shared.Helper", 7)]
    assert sites[0].is_public is True
    assert (sites[0].start_column, sites[0].end_column) == (-1, -1), "a body node's columns are not projected"


def test_call_graph_keys_by_fqn_and_signature_and_drops_externals():
    graph = _backend().get_call_graph()
    assert set(graph.edges) == {(f"{CLASS_FQN}.{METHOD_SIG}", "shared.Helper.help()")}
    assert all("can://" not in n for n in graph.nodes)
    assert graph.nodes[f"{CLASS_FQN}.{METHOD_SIG}"]["method_detail"].method.code.startswith("public String render(String alpha)")
    edge = graph.edges[f"{CLASS_FQN}.{METHOD_SIG}", "shared.Helper.help()"]
    assert edge["type"] == "CALL_DEP" and edge["weight"] == 1


def test_hierarchy_and_entrypoints_are_application_scoped():
    backend = _backend()
    assert backend.get_extended_classes(CLASS_FQN) == ["shared.Base"]
    assert backend.get_implemented_interfaces(CLASS_FQN) == ["shared.Face"]
    assert [t.name for t in backend.get_all_nested_classes(CLASS_FQN)] == ["Inner"]
    assert backend.get_all_entry_point_classes() == {}
    assert backend.get_all_entry_point_methods() == {}


def test_the_artifact_layer_is_reached_only_through_the_application_anchor():
    backend = _backend()
    assert set(backend.get_artifacts()) == {"pom.xml"}
    assert backend.get_artifacts()["pom.xml"].source == "<alpha/>", "application B's pom.xml leaked"
    assert [d.name for d in backend.get_dependencies()] == ["alpha-core"]
    assert [ck.key for ck in backend.get_config_keys().values()] == ["alpha.key"]
    assert backend.get_config_uses() == [] and backend.get_unresolved_config_reads() == []


def test_docstrings_are_this_applications():
    backend = _backend()
    assert [c.content for c in backend.get_comments_in_a_method(CLASS_FQN, METHOD_SIG)] == ["alpha method doc"]
    assert backend.get_all_docstrings() == {SHARED_MODULE: [c for c in backend.get_comments_in_a_class(CLASS_FQN)] + backend.get_comments_in_a_method(CLASS_FQN, METHOD_SIG)}


def test_a_module_row_that_is_not_a_type_is_refused_by_the_model():
    """A module declares types only. ``JType.kind`` is a ``Literal``, so a row naming any other
    kind is refused there -- with no id in the message (E6) -- and the backend needs no dispatch of
    its own."""
    from pydantic import ValidationError

    from cldk.analysis.java.neo4j import reconstruct as R

    with pytest.raises(ValidationError) as e:
        R.type_({"id": "can://java/app_a/x/Y.java/Y", "name": "Y", "kind": "widget"}, decorators=[], fields={}, callables={}, types={}, enum_constants=[], record_components=[])
    assert "kind" in str(e.value) and "can://" not in str(e.value)


# =====================================================================================
# The audit: every statement, class-level and inline, carries the application scope
# =====================================================================================
_SCOPED_VAR = re.compile(r"\b(\w+)\.id STARTS WITH \$prefix\b")
_INTROSPECTION = re.compile(r"^\s*CALL (db|dbms)\.")

#: Relationship types that cannot leave the application: each runs from the application node, a
#: module or a declaration to something the same emitter minted under the same id prefix. A
#: variable reached from an in-scope one over these is in scope too, without a predicate of its own.
_CONTAINMENT = frozenset(
    {
        "J_HAS_MODULE",
        "J_DECLARES",
        "J_HAS_METHOD",
        "J_HAS_FIELD",
        "J_DECLARES_VAR",
        "J_HAS_ENUM_CONSTANT",
        "J_HAS_RECORD_COMPONENT",
        "J_HAS_BODY_NODE",
        "HAS_ARTIFACT",
        "DEFINES_CONFIG",
    }
)

#: Cross-reference types whose target is shared vocabulary *by construction*: an ``:JAnnotation``
#: keyed by name, a ``:JPackage``, a ``:Package`` coordinate, or a ``J_RESOLVES_TO`` callee that may
#: be a ``:JExternal`` and so belongs to no application at all. A statement may reach these without
#: a prefix because there is no per-application node to reach. **``J_CALLS`` is deliberately not
#: here**: both its endpoints are application-owned callables, so both must carry the prefix.
_SHARED_TARGET = frozenset({"J_ANNOTATED_BY", "J_IMPORTS", "DECLARES_DEPENDENCY", "J_RESOLVES_TO"})

_KEEPS_SCOPE = _CONTAINMENT | _SHARED_TARGET


def _match_clauses(statement: str) -> List[str]:
    """The pattern of each ``MATCH`` / ``OPTIONAL MATCH`` clause, split as :func:`fake_cypher` does."""
    clauses = re.split(r"(?<!STARTS)(?<!OPTIONAL) (?=MATCH |OPTIONAL MATCH |WHERE |WITH |RETURN )", statement.strip())
    return [re.sub(r"^(?:OPTIONAL )?MATCH ", "", c) for c in clauses if re.match(r"(?:OPTIONAL )?MATCH ", c)]


def _unscoped_variables(statement: str) -> List[str]:
    """The node variables the statement binds that are **not** provably inside one application.

    A variable is inside when it carries ``id STARTS WITH $prefix``, when it *is* the
    ``(:JApplication {name: $app})`` anchor, or when the pattern reaches it from an inside variable
    over :data:`_KEEPS_SCOPE`. Judging per variable is the point: a presence check ("does the text
    contain a prefix predicate?") passes ``MATCH (s:JCallable)-[:J_CALLS]->(t:JCallable) WHERE
    s.id STARTS WITH $prefix``, which leaks through ``t``. Anonymous pattern nodes are skipped --
    they bind nothing, so no clause can read one.
    """
    prefixed = set(_SCOPED_VAR.findall(statement))
    inside: Dict[str, bool] = {}
    bound: List[str] = []
    for clause in _match_clauses(statement):
        previous, rels = False, None
        for kind, token in _tokens(clause):
            if kind == "hop":
                rels = token[1]
                continue
            var, labels, props = token
            anchor = labels == "JApplication" and props == "name: $app"
            here = var in prefixed or anchor or inside.get(var, False) or (previous and rels is not None and rels <= _KEEPS_SCOPE)
            if not var.startswith("_"):
                bound.append(var)
            inside[var] = inside.get(var, False) or here
            previous, rels = inside[var], None
    return sorted({v for v in bound if not inside[v]})


def _scope_kind(statement: str) -> str | None:
    if _INTROSPECTION.match(statement):
        return "introspection"
    if _unscoped_variables(statement):
        return None
    return "prefix" if _SCOPED_VAR.search(statement) else "application"


def _class_level_statements() -> Dict[str, str]:
    return {name: value for name, value in vars(JNeo4jBackend).items() if isinstance(value, str) and re.match(r"(MATCH|OPTIONAL MATCH|UNWIND|CALL)\b", value.lstrip())}


def _inline_statements() -> Dict[str, str]:
    """The Cypher at every ``self._run(`` site, keyed ``<method>@<line>``, reassembled from the
    class's own source: f-strings and ``+`` concatenations are joined, a class-level constant
    referenced through ``self`` is inlined, a call to the scope helper inside an f-string is
    replaced by the predicate it spells, and anything else becomes ``{…}``."""
    class_strings = {name: value for name, value in vars(JNeo4jBackend).items() if isinstance(value, str)}
    out: Dict[str, str] = {}
    for fn in ast.walk(ast.parse(inspect.getsource(JNeo4jBackend))):
        if not isinstance(fn, ast.FunctionDef):
            continue
        parameters = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        assigned: Dict[str, List[ast.expr]] = defaultdict(list)
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id].append(node.value)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                assigned[node.target.id].append(node.value)

        def text(e: ast.expr, depth: int = 0) -> str:
            if depth > 8:
                return "{…}"
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                return e.value
            if isinstance(e, ast.JoinedStr):
                return "".join(text(v.value if isinstance(v, ast.FormattedValue) else v, depth + 1) for v in e.values)
            if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
                return text(e.left, depth + 1) + text(e.right, depth + 1)
            if isinstance(e, ast.Call) and getattr(e.func, "id", getattr(e.func, "attr", None)) == "_scoped" and e.args and isinstance(e.args[0], ast.Constant):
                return neo4j_backend._scoped(e.args[0].value)
            if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id == "self" and e.attr in class_strings:
                return class_strings[e.attr]
            if isinstance(e, ast.Name) and e.id in assigned:
                return "".join(text(v, depth + 1) for v in assigned[e.id])
            if isinstance(e, ast.Name) and e.id in parameters:
                return f"<{e.id}>"
            return "{…}"

        for call in ast.walk(fn):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_run"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"
                and call.args
            ):
                out.setdefault(f"{fn.name}@{call.lineno}", text(call.args[0]))
    return out


def _every_statement() -> Dict[str, str]:
    return {**_class_level_statements(), **_inline_statements()}


#: What the backend is allowed to touch on the driver and on the session it opens. The harvester
#: only follows Cypher passed to ``self._run(``, so anything that reaches the server another way is
#: invisible to it: ``self._driver.execute_query(...)`` would satisfy a ``.run(`` count of one and
#: be harvested zero times. Judging the *surface* instead makes a new driver API a deliberate act --
#: adding it here, with the harvester taught to follow it.
_DRIVER_SURFACE = frozenset({"session", "close"})
_SESSION_SURFACE = frozenset({"run", "close"})


def _attribute_uses(target: str) -> Dict[str, List[str]]:
    """``<method>@<line> -> [attribute, ...]`` for every ``self.<target>.<attr>`` in the class."""
    out: Dict[str, List[str]] = defaultdict(list)
    for fn in ast.walk(ast.parse(inspect.getsource(JNeo4jBackend))):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == target
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
            ):
                out[f"{fn.name}@{node.lineno}"].append(node.attr)
    return out


@pytest.mark.parametrize("target, allowed", [("_driver", _DRIVER_SURFACE), ("_session_obj", _SESSION_SURFACE)])
def test_the_backend_reaches_the_server_only_through_the_harvested_surface(target, allowed):
    """Every attribute the backend touches on the driver and on its session is in a small
    allow-list, so no statement can reach Neo4j by a route the audit does not read."""
    for where, attributes in _attribute_uses(target).items():
        assert set(attributes) <= allowed, f"{where} uses self.{target}.{sorted(set(attributes) - allowed)}, outside the audited surface"


def test_the_audit_sees_every_inline_statement_too():
    """One harvested statement per ``self._run(`` site, every one fully reassembled -- no statement
    reaches the driver through a variable the harvester cannot follow."""
    source = inspect.getsource(JNeo4jBackend)
    inline = _inline_statements()
    assert len(inline) == source.count("self._run("), "a statement site the harvester did not see"
    assert [name for name, s in inline.items() if "{…}" in s] == [], "a statement the harvester could not reassemble"
    for expected in ("_probe_schema", "_load_modules", "_subtree_rows", "_call_site_rows", "_import_rows", "_call_edge_rows", "_artifact_rows", "_dependency_rows"):
        assert any(name.startswith(expected + "@") for name in inline), f"{expected}'s statement is not harvested"


def test_no_statement_names_retired_vocabulary():
    """The 2.4.1 labels, relationship types and properties the backend was migrated off. A
    statement naming any of them matches nothing on a 3.0.1 graph."""
    for name, s in _every_statement().items():
        for retired in (
            "._module",
            "_module IN",
            ":JCompilationUnit",
            ":JCallSite",
            ":JParameter",
            ":JComment",
            ":JInitializationBlock",
            ":JCrudOperation",
            ":JCrudQuery",
            "J_HAS_UNIT",
            "J_HAS_CALLABLE",
            "J_HAS_PARAMETER",
            "J_HAS_CALLSITE",
            "J_HAS_COMMENT",
            "J_HAS_INIT_BLOCK",
            "J_HAS_CRUD",
            "#param#",
            "t.fqn",
        ):
            assert retired not in s, f"{name} names retired vocabulary {retired!r}: {s[:160]!r}"


@pytest.mark.parametrize(
    "statement, leaks",
    [
        ("MATCH (s:JCallable)-[:J_CALLS]->(t:JCallable) WHERE s.id STARTS WITH $prefix RETURN s.id", ["t"]),
        ("MATCH (:JApplication {name: $app})-[:J_HAS_MODULE]->(m:JModule) MATCH (x:JType) RETURN x.id", ["x"]),
        ("MATCH (c:JCallable) RETURN c.id", ["c"]),
    ],
    ids=["one-endpoint-of-two", "a-second-unanchored-match", "no-scope-at-all"],
)
def test_the_audit_rejects_a_statement_that_scopes_only_part_of_its_pattern(statement, leaks):
    """The net's own net. A presence-based check ("does the text contain a prefix predicate?")
    passes the first of these, which is the regression this audit exists to catch."""
    assert _unscoped_variables(statement) == leaks
    assert _scope_kind(statement) is None


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (s:JCallable)-[:J_CALLS]->(t:JCallable) WHERE s.id STARTS WITH $prefix AND t.id STARTS WITH $prefix RETURN s.id",
        "MATCH (:JApplication {name: $app})-[:J_HAS_MODULE]->(m:JModule)-[r:J_IMPORTS]->() RETURN m.file_key",
        "CALL db.relationshipTypes()",
    ],
    ids=["both-endpoints-prefixed", "walked-from-the-anchor", "introspection"],
)
def test_the_audit_accepts_the_three_shapes_that_are_actually_scoped(statement):
    assert _unscoped_variables(statement) == []
    assert _scope_kind(statement) is not None


def test_no_statement_spells_the_scope_with_any():
    """``any(p IN $prefixes WHERE …)`` plans as a label scan; Java has one prefix, so the predicate
    is a bare ``STARTS WITH`` and there is nothing for ``any()`` to iterate."""
    assert [name for name, s in _every_statement().items() if "any(" in s] == []


@pytest.mark.parametrize("name", sorted(_every_statement()))
def test_every_statement_is_application_scoped_or_anchored(name):
    """Two ways a node stays inside one application, judged **per bound variable**: the pattern
    reaches it from an ``(:JApplication {name: $app})`` anchor, or it carries the
    ``id STARTS WITH $prefix`` predicate itself. A qualified name, a signature and a ``file_key``
    are all shared vocabulary, so neither can be skipped for one endpoint of two."""
    statement = _every_statement()[name]
    assert _unscoped_variables(statement) == [], f"{name} leaks through {_unscoped_variables(statement)}: {statement[:200]!r}"
    assert _scope_kind(statement) is not None, f"{name} carries no application scope: {statement[:160]!r}"


def test_no_statement_anchors_on_the_marker_label():
    """A grep, not a measurement: it freezes the *ruling* that no statement anchors on
    ``:JCanNode``, so a change of anchor has to change this test and re-run the numbers.

    The ruling, measured on ThingsBoard (7691, ``PROFILE``, median of 5 -- the table is in Task 3
    of the plan): the two prefix-scoped statements this backend issues both fan out over
    relationships, and the traversal dominates -- swapping the anchor moves the wall clock by under
    1% while ``:JCanNode`` adds a quarter again as many db hits (call sites 5.65M against 4.55M;
    call edges 1.89M against 0.73M). Every other statement walks out from the ``:JApplication``
    anchor and never scans. Where ``:JCanNode`` *is* the only seek available it still loses, its
    index being a non-constraint range index over 615,329 nodes: 118 ms against 24 on a
    whole-application prefix, 1.40 ms against 1.13 on a point lookup. It wins a *per-module*
    prefix (3.0 ms against 21.2), which no statement here issues. This test does not notice a
    switch to ``:JSymbol`` -- also measured, also not used (it loses a signature lookup at 31.6 ms
    against 22.4 and is a wash on the traversals) -- because that would be a different ruling with
    its own numbers, not a violation of this one."""
    for name, s in _every_statement().items():
        assert "JCanNode" not in s, f"{name} anchors on :JCanNode, which is never the faster anchor here: {s[:120]!r}"
