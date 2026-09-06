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

"""Every statement ``TSNeo4jBackend`` issues stays inside one application (the leg-1.6 audit, for
TypeScript).

The SDK attaches to a graph someone else deployed, and a database holding several applications is
the expected deployment. A **signature** is not application-stamped -- two applications can declare
``shared.Widget`` -- so every statement that matches by signature must also carry the application
scope. On a 1.2.0 graph that scope is the ``can://`` id prefix and nothing else: there is no
``_module`` property (gone on ``main``, #166), and TypeScript's scope is **two** prefixes (TS-3),
``can://typescript/<app>/`` and ``can://javascript/<app>/``, spelled as
``x.id STARTS WITH $p1 OR x.id STARTS WITH $p2`` (measured: the ``any()`` form defeats the seek).

Two nets, as in the Python twin:

* a **fake two-application graph** in the 1.2.0 vocabulary, carrying no ``_module``, whose two
  applications declare the same class and method signatures -- every child is named for its own
  application, so a leak is visible by name, not by count; and
* an **audit** that harvests every Cypher statement on the class -- class-level constants and the
  ones written inline at each ``self._run(`` / ``self._fetch(`` site -- and judges each one.

Like every other Neo4j test here this suite never emits ``CREATE``/``MERGE``/``SET``/``DELETE``.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from cldk.analysis.typescript.neo4j import neo4j_backend
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend

from .conftest import FakeDriver

APP_A, APP_B = "app_a", "app_b"
CLASS_SIG = "shared.Widget"
METHOD_SIG = "shared.Widget.render"
SHARED_MODULE = "src/index.ts"


# =====================================================================================
# The fixture graph: two applications, colliding signatures, no ``_module`` anywhere.
# =====================================================================================
class _Graph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Tuple[set, Dict[str, Any]]] = {}
        self.edges: List[Tuple[str, str, str, Dict[str, Any]]] = []

    def node(self, node_id: str, labels: Sequence[str], **props: Any) -> str:
        self.nodes[node_id] = (
            set(labels) | {"CanNode"} if not {"Application", "TSDecorator", "Artifact", "Package", "ConfigKey"} & set(labels) else set(labels),
            {"id": node_id, **props},
        )
        return node_id

    def edge(self, src: str, rel: str, dst: str, **props: Any) -> None:
        self.edges.append((src, rel, dst, props))


def _build() -> _Graph:
    g = _Graph()
    for app, lang_mod, fn_name in ((APP_A, "a", "alpha"), (APP_B, "b", "beta")):
        app_id = g.node(f"can://typescript/{app}", ["Application", "TSApplication"], analyzer_version="1.2.0")
        mod = g.node(f"can://typescript/{app}/{lang_mod}/mod.ts", ["TSModule"], kind="module", name=f"{lang_mod}/mod.ts", start_line=1, end_line=20)
        g.edge(app_id, "TS_HAS_MODULE", mod)
        cls = g.node(
            f"{mod}/Widget", ["TSClass"], kind="class", signature=CLASS_SIG, name="Widget", base_classes=["shared.Base"], start_line=1, end_line=10, code="class Widget {}"
        )
        g.edge(mod, "TS_DECLARES", cls)
        g.edge(cls, "TS_DECORATED_BY", "Deco", positional_arguments=[f'"{fn_name}"'])
        method = g.node(f"{cls}/render", ["TSCallable"], kind="method", signature=METHOD_SIG, name=f"{fn_name}_method", start_line=2, end_line=6, code=f"{fn_name} code")
        g.edge(cls, "TS_HAS_METHOD", method)
        attr = g.node(f"{cls}/{fn_name}_attr", ["TSField"], kind="field", name=f"{fn_name}_attr", start_line=2, end_line=2)
        g.edge(cls, "TS_HAS_FIELD", attr)
        inner = g.node(f"{method}/inner_fn", ["TSCallable"], kind="function", signature=f"{fn_name}.inner_fn", name=f"{fn_name}_inner_fn", start_line=3, end_line=4, code="inner")
        g.edge(method, "TS_DECLARES", inner)
        call = g.node(f"{method}@5:3", ["TSBodyNode"], kind="call", callee=inner, start_line=5, end_line=5)
        g.edge(method, "TS_HAS_BODY_NODE", call)
        g.edge(call, "TS_RESOLVES_TO", inner)
        g.edge(method, "TS_CALLS", inner, weight=1, prov=["tsc"])
        ext = g.node(f"can://typescript/{app}/@external/os/path", ["TSExternal"], kind="external", name="path", module="os")
        g.edge(method, "TS_CALLS", ext, weight=1, prov=["import"])
        # The module-key collision: both applications declare src/index.ts.
        shared = g.node(f"can://typescript/{app}/{SHARED_MODULE}", ["TSModule"], kind="module", name=SHARED_MODULE, start_line=1, end_line=9)
        g.edge(app_id, "TS_HAS_MODULE", shared)
        fn = g.node(f"{shared}/{fn_name}_fn", ["TSCallable"], kind="function", signature=f"src/index.{fn_name}_fn", name=f"{fn_name}_fn", start_line=1, end_line=3, code="fn")
        g.edge(shared, "TS_DECLARES", fn)
        anon = g.node(
            f"{fn}/<anon@2:2>", ["TSCallable", "TSAnonymousCallable"], kind="arrow", signature="src/index.<anon@2:2>", name="(anonymous)", start_line=2, end_line=2, code="() => 1"
        )
        g.edge(fn, "TS_DECLARES", anon)
        var = g.node(f"{shared}/{fn_name}_var", ["TSField"], kind="field", name=f"{fn_name}_var", start_line=8, end_line=8)
        g.edge(shared, "TS_HAS_FIELD", var)
    # One JavaScript module in application A only: the second prefix must be honoured (TS-3).
    js = g.node(f"can://javascript/{APP_A}/a/legacy.js", ["TSModule"], kind="module", name="a/legacy.js", start_line=1, end_line=3)
    g.edge(f"can://typescript/{APP_A}", "TS_HAS_MODULE", js)
    legacy = g.node(f"{js}/legacy_fn", ["TSCallable"], kind="function", signature="a/legacy.legacy_fn", name="legacy_fn", start_line=1, end_line=2, code="legacy")
    g.edge(js, "TS_DECLARES", legacy)
    # TSDecorator is keyed by name and shared by every application in the database.
    g.node("Deco", ["TSDecorator"], name="Deco", qualified_name="Deco")
    return g


GRAPH = _build()


# =====================================================================================
# A small evaluator for the linear-chain statements the backend issues. It honours a scope
# predicate **only when the statement carries it** -- an unscoped statement sees both applications.
# =====================================================================================
_NODE = re.compile(r"\((\w*)(?::([\w|:]+))?(?: \{([^}]*)\})?\)")
_HOP = re.compile(r"(<)?-\[(\w*)(?::([\w|]+))?(\*0\.\.)?\]-(>)?")
_SCOPE = re.compile(r"\((\w+)\.id STARTS WITH \$p1 OR \1\.id STARTS WITH \$p2\)")
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
        for cm in re.finditer(r"(coalesce\([^)]*\)) = (\$\w+)", clause):
            if _expr(cm.group(1), b, params) != _value(cm.group(2), params):
                return False
        for sm in _SCOPE.finditer(clause):
            nid = b[sm.group(1)]
            if not (nid.startswith(params["p1"]) or nid.startswith(params["p2"])):
                return False
        for m in _COND.finditer(re.sub(r"coalesce\([^)]*\) = \$\w+", "", _SCOPE.sub("", clause))):
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
            return {k: v for k, v in bound.items() if k != "_type"}
        return dict(GRAPH.nodes[bound][1]) if bound else None
    if e.startswith("labels("):
        return sorted(GRAPH.nodes[b[e[7:-1]]][0]) if b.get(e[7:-1]) else None
    if e.startswith("type("):
        return b[e[5:-1]]["_type"]
    if e.startswith("coalesce("):
        return next((v for v in (_expr(a, b, params) for a in _split_top(e[9:-1], ", ")) if v is not None), None)
    if " + " in e:
        parts = [_expr(p, b, params) for p in e.split(" + ")]
        return None if any(p is None for p in parts) else "".join(parts)
    if e.startswith("'"):
        return e.strip("'")
    var, _, prop = e.partition(".")
    if b.get(var) is None:
        return None
    return b[var].get(prop) if isinstance(b[var], dict) else GRAPH.nodes[b[var]][1].get(prop)


def _return(clause: str, rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    clause = re.sub(r" ORDER BY .*?(?= LIMIT|$)", "", clause)
    limit = re.search(r" LIMIT (\d+)$", clause)
    clause = clause[: limit.start()] if limit else clause
    distinct = clause.startswith("DISTINCT ")
    items = [(i.rsplit(" AS ", 1) if " AS " in i else (i, i)) for i in _split_top(clause[9:] if distinct else clause, ", ")]
    plain = [(e, a) for e, a in items if not e.startswith("collect(")]
    aggs = [(e, a) for e, a in items if e.startswith("collect(")]
    if aggs:
        groups: Dict[tuple, Dict[str, Any]] = {}
        for b in rows:
            key = tuple(repr(_expr(e, b, params)) for e, _ in plain)
            g = groups.setdefault(key, {a: _expr(e, b, params) for e, a in plain} | {a: [] for _, a in aggs})
            for e, a in aggs:
                v = _expr(e[len("collect(DISTINCT ") : -1], b, params)
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
        elif kw == "WITH":
            keep = body.replace("DISTINCT ", "").split(", ")
            seen, projected = set(), []
            for b in rows:
                p = {k: b[k] for k in keep}
                if repr(p) not in seen:
                    seen.add(repr(p))
                    projected.append(p)
            rows = projected
        elif kw == "RETURN":
            return _return(body, rows, params)
    raise AssertionError(f"statement has no RETURN: {query!r}")


def _backend(record: List[str] | None = None) -> TSNeo4jBackend:
    """A backend scoped to application A over a fake driver holding A and B."""
    driver = FakeDriver(responder=fake_cypher)
    backend = TSNeo4jBackend._from_driver(driver, application_name=APP_A)
    if record is not None:
        record.extend(driver.statements)
        backend._run = lambda q, **p: (record.append(q), [r for r in fake_cypher(q, p)])[1]
    return backend


# =====================================================================================
# The fake graph is what a 1.2.0 graph is
# =====================================================================================
def test_the_fake_graph_carries_no_module_property():
    assert GRAPH.nodes and not any("_module" in props for _, props in GRAPH.nodes.values())
    prefixes = tuple(f"can://{lang}/{app}/" for lang in ("typescript", "javascript") for app in (APP_A, APP_B))
    assert all(nid.startswith(prefixes) for nid, (labels, _) in GRAPH.nodes.items() if "CanNode" in labels and "TSModule" not in labels or nid.count("/") > 3)


def test_the_scope_is_two_prefixes():
    backend = _backend()
    assert backend._scope_prefixes == [f"can://typescript/{APP_A}/", f"can://javascript/{APP_A}/"]
    assert set(backend._modules) == {"a/mod.ts", SHARED_MODULE, "a/legacy.js"}


# =====================================================================================
# Leak tests: application A's answers and nothing of B's
# =====================================================================================
def test_get_class_does_not_leak_another_applications_children():
    cls = _backend().get_class(CLASS_SIG)
    assert cls is not None, "the application's own class came back empty -- the statement scoped on something the graph does not carry"
    assert {m.name for m in cls.callables.values()} == {"alpha_method"}
    assert set(cls.callables) == {"render"}
    assert set(cls.fields) == {"alpha_attr"}
    assert [d.name for d in cls.decorators] == ["Deco"]
    assert {c.name for c in cls.callables["render"].callables.values()} == {"alpha_inner_fn"}
    assert cls.callables["render"].code == "alpha code"


def test_get_all_classes_agrees_with_get_class():
    backend = _backend()
    assert set(backend.get_all_classes()) == {CLASS_SIG}
    assert backend.get_all_classes()[CLASS_SIG] == backend.get_class(CLASS_SIG)


def test_symbol_table_honours_both_prefixes_and_a_shared_module_key():
    table = _backend().get_symbol_table()
    assert set(table) == {"a/mod.ts", SHARED_MODULE, "a/legacy.js"}
    assert set(table[SHARED_MODULE].functions) == {"alpha_fn"}
    assert set(table[SHARED_MODULE].functions["alpha_fn"].callables) == {"<anon@2:2>"}
    assert [v.name for v in table[SHARED_MODULE].variables] == ["alpha_var"]
    assert set(table["a/legacy.js"].functions) == {"legacy_fn"}


def test_get_typescript_module_on_a_shared_key_returns_this_applications():
    module = _backend().get_typescript_module(SHARED_MODULE)
    assert module is not None and set(module.functions) == {"alpha_fn"}
    assert _backend().get_typescript_module("b/mod.ts") is None


def test_get_method_resolves_inside_the_application_only():
    backend = _backend()
    assert backend.get_method(CLASS_SIG, "alpha_method").code == "alpha code"
    assert backend.get_method(CLASS_SIG, "beta_method") is None
    assert backend.get_method("src/index", "alpha_fn").signature == "src/index.alpha_fn"
    assert backend.get_method("src/index", "beta_fn") is None
    assert backend.get_method("whatever", "a/legacy.legacy_fn").name == "legacy_fn"


def test_get_all_functions_and_methods_in_application():
    backend = _backend()
    assert set(backend.get_all_functions()) == {"src/index.alpha_fn", "a/legacy.legacy_fn"}
    assert {k: set(v) for k, v in backend.get_all_methods_in_application().items()} == {CLASS_SIG: {"alpha_method"}}


def test_bulk_accessors_are_application_scoped():
    backend = _backend()
    assert {o.signature for o in backend.get_callables_overview()} == {METHOD_SIG, "alpha.inner_fn", "src/index.alpha_fn", "src/index.<anon@2:2>", "a/legacy.legacy_fn"}
    assert {o.path for o in backend.get_callables_overview()} == {"a/mod.ts", SHARED_MODULE, "a/legacy.js"}
    assert backend.get_method_bodies([METHOD_SIG, "nope"]) == {METHOD_SIG: "alpha code"}
    sites = backend.get_callsites_for([METHOD_SIG, "src/index.alpha_fn"])
    assert set(sites) == {METHOD_SIG, "src/index.alpha_fn"}
    assert [cs.callee_signature for cs in sites[METHOD_SIG]] == ["alpha.inner_fn"]
    assert sites["src/index.alpha_fn"] == []
    assert backend.get_classes_with_decorators(["Deco"]) == {"Deco": [CLASS_SIG]}


def test_call_graph_and_externals_are_application_scoped():
    backend = _backend()
    graph = backend.get_call_graph()
    assert set(graph.edges) == {(METHOD_SIG, "alpha.inner_fn"), (METHOD_SIG, "os.path")}
    assert graph.nodes["os.path"]["kind"] == "external"
    assert list(backend.get_external_symbols()) == ["os.path"]
    assert backend.get_external_symbols()["os.path"].id == f"can://typescript/{APP_A}/@external/os/path"
    assert backend.get_calling_lines("alpha.inner_fn") == [5]
    assert backend.get_calling_lines("beta.inner_fn") == []
    assert set(backend.get_synthesized_callables()) == {f"can://typescript/{APP_A}/{SHARED_MODULE}/alpha_fn/<anon@2:2>"}


def test_get_typescript_file_derives_the_module_key_from_the_id():
    backend = _backend()
    assert backend.get_typescript_file(CLASS_SIG) == "a/mod.ts"
    assert backend.get_typescript_file("a/legacy.legacy_fn") == "a/legacy.js"
    assert backend.get_typescript_file("nope") is None


# =====================================================================================
# The audit: every statement, class-level and inline, carries the application scope
# =====================================================================================
_MATCHES_BY_PREFIX = re.compile(r"\w+\.id STARTS WITH \$p1 OR \w+\.id STARTS WITH \$p2|\.id STARTS WITH \$prefix\b")
_MATCHES_BY_SIGNATURE = re.compile(r"signature\s*[:=]\s*\$|\.signature IN \$")
_MATCHES_BY_ID = re.compile(r"\bid\s*:\s*\$|\.id IN \$|\.id = \$")
_INTROSPECTION = re.compile(r"^\s*CALL (db|dbms)\.")
_ANCHORED_ON_THE_APPLICATION = re.compile(r"\(\w*:Application \{id: \$app_id\}\)")
#: Class-level strings that are Cypher but not a whole statement, judged at their use sites.
_FRAGMENTS = {"_OVERVIEW_PROJECTION", "_SUBTREE"}


def _is_scoped(statement: str) -> bool:
    return bool(_MATCHES_BY_PREFIX.search(statement))


def _scope_kind(statement: str) -> str | None:
    if _INTROSPECTION.match(statement):
        return "introspection"
    if _is_scoped(statement):
        return "prefix"
    if _ANCHORED_ON_THE_APPLICATION.search(statement):
        return "application"
    if _MATCHES_BY_ID.search(statement):
        return "id"
    return None


def _class_level_statements() -> Dict[str, str]:
    return {name: value for name, value in vars(TSNeo4jBackend).items() if isinstance(value, str) and re.match(r"(MATCH|OPTIONAL MATCH|UNWIND|CALL)\b", value.lstrip())}


def _inline_statements() -> Dict[str, str]:
    """The Cypher at every ``self._run(`` and ``self._fetch(`` site, keyed ``<method>@<line>``,
    reassembled from the class's own source (see the Python twin for the rules). Two additions:
    a call to the scope helper inside an f-string is replaced by the predicate it spells, and the
    first argument of ``self._fetch(`` -- the anchor pattern a subtree statement starts from -- is
    harvested as a statement in its own right."""
    class_strings = {name: value for name, value in vars(TSNeo4jBackend).items() if isinstance(value, str)}
    out: Dict[str, str] = {}
    for fn in ast.walk(ast.parse(inspect.getsource(TSNeo4jBackend))):
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
            if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "format":
                return text(e.func.value, depth + 1)
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
                and call.func.attr in ("_run", "_fetch")
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"
                and call.args
            ):
                out.setdefault(f"{fn.name}@{call.lineno}", text(call.args[0]))
    return out


def _every_statement() -> Dict[str, str]:
    inline = {name: s for name, s in _inline_statements().items() if "<anchor>" not in s}
    return {**{n: s for n, s in _class_level_statements().items() if n not in _FRAGMENTS}, **inline}


def test_the_audit_sees_every_inline_statement_too():
    """One harvested statement per ``self._run(`` / ``self._fetch(`` site; a site the harvester
    cannot fully reassemble is reported; the one allowed indirection is ``_fetch``'s own two
    ``_run`` calls (their ``<anchor>`` is judged at every ``self._fetch(`` call site). A helper
    parameterised on a label (``<label>``) is judged as written."""
    source = inspect.getsource(TSNeo4jBackend)
    inline = _inline_statements()
    assert len(inline) == source.count("self._run(") + source.count("self._fetch("), "a statement site the harvester did not see"
    indirect = sorted({name.split("@")[0] for name, s in inline.items() if "<anchor>" in s})
    assert indirect == ["_fetch"], f"unjudged statements passed through a variable: {indirect}"
    for expected in (
        "_probe_schema",
        "_load_module_keys",
        "_type_by_signature",
        "_types_by_signature",
        "get_symbol_table",
        "get_typescript_module",
        "get_method",
        "get_all_functions",
        "get_callables_overview",
        "get_method_bodies",
        "get_decorated_callables",
        "get_callsites_for",
        "_call_rows",
        "get_external_symbols",
        "get_synthesized_callables",
        "get_calling_lines",
        "get_artifacts",
        "get_dependencies",
        "get_config_keys",
        "get_config_uses",
    ):
        assert any(name.startswith(expected + "@") for name in inline), f"{expected}'s statement is not harvested"


def test_no_statement_names_retired_vocabulary():
    """The 0.4.3 labels/types the backend was migrated off, and the ``_module`` property 1.2.0's
    ``main`` retired (#166): a statement naming any of them matches nothing on the graph."""
    for name, s in _every_statement().items():
        for retired in (
            "._module",
            "_module IN",
            ":Symbol",
            ":Callable",
            ":CallSite",
            "HAS_CALLSITE",
            "[:CALLS]",
            ":Decorator)",
            "DECORATED_BY]" if "TS_DECORATED_BY" not in s else "\0",
            "{name: $app}",
            "TSCanNode",
            "JSCanNode",
        ):
            assert retired not in s, f"{name} names retired vocabulary {retired!r}: {s[:160]!r}"


def test_no_statement_spells_the_scope_with_any():
    """``any(p IN $prefixes WHERE …)`` plans as a label scan; the two-prefix ``OR`` seeks."""
    assert [name for name, s in _every_statement().items() if "any(" in s and "STARTS WITH" in s] == []


@pytest.mark.parametrize("name", sorted(_every_statement()))
def test_every_statement_is_application_scoped_or_keyed_by_an_application_stamped_id(name):
    """Three ways a statement stays inside one application. A **signature** is not
    application-stamped, so a statement matching by signature must carry the two-prefix scope. A
    **can:// id** embeds the application, so a statement keyed only by id (``{id: $id}``) is scoped
    by construction. A statement anchored on ``(:Application {id: $app_id})`` walks out from the
    application node and cannot leave it."""
    statement = _every_statement()[name]
    kind = _scope_kind(statement)
    assert kind is not None, f"{name} carries no application scope: {statement[:160]!r}"
    if _MATCHES_BY_SIGNATURE.search(statement):
        assert kind == "prefix", f"{name} matches by signature without the application prefix"


def test_the_overview_projection_is_only_ever_appended_to_a_scoped_match():
    source = inspect.getsource(neo4j_backend)
    uses = source.split("self._OVERVIEW_PROJECTION")[:-1]
    assert len(uses) >= 2, "the projection is used from fewer places than expected; did it move?"
    for before in uses:
        statement = re.sub(r"\{_scoped\('(\w+)'\)\}", lambda m: neo4j_backend._scoped(m.group(1)), before[before.rindex("self._run(") :])
        assert _is_scoped(statement), f"an unscoped MATCH feeds the overview projection: {statement[-200:]!r}"


def test_seek_labels_follow_the_measured_rule():
    """Measured on the superset graph (7690): a ``:TSCallable`` lookup by signature under the
    two-prefix scope plans best on the bare label (7.9 ms; ``:CanNode`` turns it into a 44 ms
    range-seek union), while an id-equality point lookup is a 1.5 ms unique-index seek only with
    ``:CanNode`` (a 9 ms label scan without). So: signature/prefix statements never name
    ``:CanNode``; ``{id: $…}`` point lookups always do."""
    for name, s in _every_statement().items():
        for m in re.finditer(r"\(\w*:([\w:|]+) \{id: \$\w+\}\)", s):
            assert m.group(1).startswith("CanNode:") or m.group(1) in ("Application",), f"{name}: id point lookup without :CanNode -- {m.group(0)}"
        if _MATCHES_BY_SIGNATURE.search(s):
            assert "CanNode" not in s, f"{name}: a signature lookup names :CanNode -- {s[:120]!r}"
