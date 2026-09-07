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
import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Set, Tuple

import pytest

from cldk.analysis.typescript.backend import SDG_REL_PATTERN
from cldk.analysis.typescript.neo4j import neo4j_backend
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.utils.exceptions import SelectorNotInGraph

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
        if not any(e[:3] == (src, rel, dst) for e in self.edges):  # MERGE: one edge per (src, type, dst)
            self.edges.append((src, rel, dst, props))


def _build() -> _Graph:
    g = _Graph()
    for app, lang_mod, fn_name in ((APP_A, "a", "alpha"), (APP_B, "b", "beta")):
        app_id = g.node(
            f"can://typescript/{app}",
            ["Application", "TSApplication"],
            analyzer_version="1.2.0",
            entrypoint_frameworks=[f"{fn_name}-framework"],
            entrypoint_report_json=json.dumps({"frameworks_detected": [f"{fn_name}-framework"], "rulesets": ["shipped"], "unresolved": {f"{fn_name}.miss": 2}, "errors": []}),
        )
        mod = g.node(f"can://typescript/{app}/{lang_mod}/mod.ts", ["TSModule"], kind="module", name=f"{lang_mod}/mod.ts", start_line=1, end_line=20)
        g.edge(app_id, "TS_HAS_MODULE", mod)
        cls = g.node(
            f"{mod}/Widget",
            ["TSClass"],
            kind="class",
            signature=CLASS_SIG,
            name="Widget",
            base_classes=["shared.Base"],
            start_line=1,
            end_line=10,
            code="class Widget {}",
            is_entrypoint=True,
        )
        g.edge(mod, "TS_DECLARES", cls)
        g.edge(cls, "TS_DECORATED_BY", "Deco", positional_arguments=[f'"{fn_name}"'])
        method = g.node(
            f"{cls}/render", ["TSCallable"], kind="method", signature=METHOD_SIG, name=f"{fn_name}_method", start_line=2, end_line=6, code=f"{fn_name} code", is_entrypoint=True
        )
        g.edge(cls, "TS_HAS_METHOD", method)
        attr = g.node(f"{cls}/{fn_name}_attr", ["TSField"], kind="field", name=f"{fn_name}_attr", start_line=2, end_line=2)
        g.edge(cls, "TS_HAS_FIELD", attr)
        inner = g.node(f"{method}/inner_fn", ["TSCallable"], kind="function", signature=f"{fn_name}.inner_fn", name=f"{fn_name}_inner_fn", start_line=3, end_line=4, code="inner")
        g.edge(method, "TS_DECLARES", inner)
        call = g.node(f"{method}@5:3", ["TSBodyNode"], kind="call", callee=inner, start_line=5, end_line=5)
        g.edge(method, "TS_HAS_BODY_NODE", call)
        g.edge(call, "TS_RESOLVES_TO", inner)
        g.edge(method, "TS_CALLS", inner, weight=1, prov=["tsc"])
        ext = g.node(
            f"can://typescript/{app}/@external/os/{'path' if app == APP_A else 'join'}", ["TSExternal"], kind="external", name="path" if app == APP_A else "join", module="os"
        )
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
    # Declaration-merged nodes (one id for two declarations, both labels, the last writer's kind):
    # a `const Option = () => …` + `interface Option {…}` whose interface fields hang off an arrow,
    # and a `type Gran = …` + a field `Gran` -- only in application A.
    mod_a = f"can://typescript/{APP_A}/a/mod.ts"
    merged = g.node(f"{mod_a}/Option", ["TSCallable", "TSInterface"], kind="arrow", signature="a/mod.Option", name="Option", start_line=11, end_line=12, code="() => 1")
    g.edge(mod_a, "TS_DECLARES", merged)
    g.edge(mod_a, "TS_DECLARES", merged)  # the emitter writes one edge per facet; MERGE keeps one
    g.edge(merged, "TS_HAS_FIELD", g.node(f"{merged}/label", ["TSField"], kind="field", name="label", start_line=11, end_line=11))
    gran = g.node(f"{mod_a}/Gran", ["TSTypeAlias", "TSField"], kind="field", signature="a/mod.Gran", name="Gran", aliased_type="string", start_line=13, end_line=13)
    g.edge(mod_a, "TS_DECLARES", gran)
    g.edge(mod_a, "TS_HAS_FIELD", gran)
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
_COND = re.compile(r"(\w+)\.(\w+) (STARTS WITH|ENDS WITH|IN|=|<>) (\$\w+|'[^']*'|true|false|\w+\.\w+)|(\$\w+) IN (\w+)\.(\w+)|(\w+)\.(\w+) IS NOT NULL")
#: ``a.x <= b.y`` / ``b.y <= a.x`` -- the line-containment comparisons ``_LOCATE_QUERY`` is built on.
_ORDER = re.compile(r"(\w+\.\w+) (<=|>=|<|>) (\w+\.\w+)")
#: A node **or** a hop, in one alternation, so the audit can walk a pattern token by token and see
#: what separates two tokens. Groups 1-3 are the node's (var, labels, props); group 6 is the hop's
#: relationship types.
_TOKEN = re.compile(r"\((\w*)(?::([\w|:<>]+))?(?: ?\{([^}]*)\})?\)|(<)?-\[(\w*)(?::([\w|<>]+))?(\*[\d.]*[\w]*)?\]-(>)?")


def _value(token: str, params: Dict[str, Any], row: Dict[str, Any] | None = None) -> Any:
    """A Cypher scalar: a ``$parameter``, a ``'literal'``, or -- inside an ``UNWIND``ed statement --
    a property of a bound row variable (``pos.path``), which is what ``_LOCATE_QUERY`` compares
    against."""
    if token.startswith("$"):
        return params[token[1:]]
    if token in ("true", "false"):  # a Cypher boolean literal, e.g. ``c.is_entrypoint = true``
        return token == "true"
    if "." in token and row is not None:
        var, _, prop = token.partition(".")
        if isinstance(row.get(var), dict):
            return row[var][prop]
    return token.strip("'")


def _node_ok(node_id: str, labels: str | None, props: str | None, params: Dict[str, Any], row: Dict[str, Any] | None = None) -> bool:
    node_labels, node_props = GRAPH.nodes[node_id]
    if labels and not any(set(alt.split(":")) <= node_labels for alt in labels.split("|")):
        return False
    for item in filter(None, (props or "").split(", ")):
        key, token = item.split(": ")
        if node_props.get(key) != _value(token, params, row):
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
                        if b[var] is not None and landed == b[var] and _node_ok(b[var], labels, props, params, b):
                            nxt.append(b)
                    elif "_next" in b:
                        n = b.pop("_next")
                        if _node_ok(n, labels, props, params, b):
                            nxt.append({**b, var: n})
                    else:
                        nxt += [{**b, var: n} for n in GRAPH.nodes if _node_ok(n, labels, props, params, b)]
                cur, prev_var = nxt, var
            else:
                rvar, rels, back, var_len = t
                cur = [{**b, "_next": n, **({rvar: e} if rvar else {})} for b in cur if b.get(prev_var) is not None for n, e in _walk(b[prev_var], rels, back, var_len)]
        if cur:
            out += cur
        elif optional:
            out.append({**row, **{v: None for v in new_vars if v not in row}})
    return out


def _conjunct_ok(clause: str, b: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """One ``AND``-conjunct of a ``WHERE``, with no top-level disjunction left in it."""
    for cm in re.finditer(r"(coalesce\([^)]*\)) = (\$\w+)", clause):
        if _expr(cm.group(1), b, params) != _value(cm.group(2), params):
            return False
    for m in _COND.finditer(re.sub(r"coalesce\([^)]*\) = \$\w+", "", clause)):
        if m.group(1):
            if isinstance(b.get(m.group(1)), dict):
                continue  # the left-hand side is an UNWIND row, not a node -- judged by _ORDER
            if b.get(m.group(1)) is None:
                return False  # a predicate on a null variable is null, i.e. not true
            actual = GRAPH.nodes[b[m.group(1)]][1].get(m.group(2))
            op, expected = m.group(3), _value(m.group(4), params, b)
            if not {
                "STARTS WITH": lambda: str(actual).startswith(expected),
                "ENDS WITH": lambda: str(actual).endswith(expected),
                "IN": lambda: actual in expected,
                "=": lambda: actual == expected,
                "<>": lambda: actual != expected,
            }[op]():
                return False
        elif m.group(5):
            if b.get(m.group(6)) is None or _value(m.group(5), params) not in (GRAPH.nodes[b[m.group(6)]][1].get(m.group(7)) or []):
                return False
        elif b.get(m.group(8)) is None or GRAPH.nodes[b[m.group(8)]][1].get(m.group(9)) is None:
            return False
    for om in _ORDER.finditer(clause):
        left, right = _expr(om.group(1), b, params), _expr(om.group(3), b, params)
        if left is None or right is None:
            return False
        if not {"<=": lambda: left <= right, ">=": lambda: left >= right, "<": lambda: left < right, ">": lambda: left > right}[om.group(2)]():
            return False
    return True


def _where(clause: str, rows: List[Dict[str, Any]], params: Dict[str, Any], optional_vars: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """``AND`` of conjuncts, each of which may be a parenthesised ``OR`` of conditions.

    ``optional_vars`` are the variables the *immediately preceding* ``OPTIONAL MATCH`` introduced.
    Cypher reads such a ``WHERE`` as part of that optional match, so a row for which the predicate
    holds of nothing survives with those variables null rather than being dropped -- which is
    exactly the "no callable contains this line" case ``locate`` reports as module scope.

    Disjunction is evaluated generically rather than pattern-matched one shape at a time: the
    backend spells three of them -- the two-prefix application scope ``(x.id STARTS WITH $p1 OR
    x.id STARTS WITH $p2)``, the resolver's ``(c.signature = $name OR c.signature ENDS WITH
    $dotted)``, and ``describe``'s ``(c.id IN $refs OR c.signature IN $refs)`` -- and a special case
    per shape is a harness that silently answers "no rows" the day a fourth appears.
    """

    def ok(b: Dict[str, Any]) -> bool:
        for conjunct in _split_top(clause, " AND "):
            conjunct = conjunct.strip()
            inner = conjunct[1:-1] if conjunct.startswith("(") and conjunct.endswith(")") else conjunct
            disjuncts = _split_top(inner, " OR ")
            if not any(_conjunct_ok(d, b, params) for d in disjuncts):
                return False
        return True

    kept = [b for b in rows if ok(b)]
    if not optional_vars:
        return kept
    base = lambda b: {k: v for k, v in b.items() if k not in optional_vars}
    survived = {repr(base(b)) for b in kept}
    for b in rows:
        if repr(base(b)) not in survived:
            survived.add(repr(base(b)))
            kept.append({**base(b), **{v: None for v in optional_vars}})
    return kept


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
    if " UNION " in query:
        return [row for arm in query.split(" UNION ") for row in fake_cypher(arm, params)]
    rows: List[Dict[str, Any]] = [{}]
    #: The variables the immediately preceding OPTIONAL MATCH introduced -- see :func:`_where`.
    optional_vars: List[str] = []
    clauses = re.split(r"(?<!STARTS)(?<!ENDS)(?<!OPTIONAL) (?=MATCH |OPTIONAL MATCH |WHERE |WITH |RETURN |UNWIND )", query.strip())
    for clause in clauses:
        kw, _, body = clause.partition(" ")
        if kw == "UNWIND":
            source, _, var = body.partition(" AS ")
            rows = [{**b, var: item} for b in rows for item in _value(source.strip(), params)]
        elif kw == "OPTIONAL":
            before = set().union(*(set(b) for b in rows)) if rows else set()
            rows = _match(body[len("MATCH ") :], rows, params, optional=True)
            optional_vars = sorted((set().union(*(set(b) for b in rows)) if rows else set()) - before)
            continue
        elif kw == "MATCH":
            rows = _match(body, rows, params, optional=False)
        elif kw == "WHERE":
            rows = _where(body, rows, params, optional_vars)
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
        optional_vars = []
    raise AssertionError(f"statement has no RETURN: {query!r}")


_A_PREFIXES = (f"can://typescript/{APP_A}/", f"can://javascript/{APP_A}/")


def _responder(query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """:func:`fake_cypher`, after asserting every prefix parameter bound at run time is one of
    application A's -- the audit below judges spellings; this judges the values."""
    for name in ("prefix", "p1", "p2"):
        if name in params:
            assert params[name].startswith(_A_PREFIXES), f"${name} bound to {params[name]!r}, outside application A's scope"
    return fake_cypher(query, params)


def _backend() -> TSNeo4jBackend:
    """A backend scoped to application A over a fake driver holding A and B."""
    return TSNeo4jBackend._from_driver(FakeDriver(responder=_responder), application_name=APP_A)


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
    assert set(table["a/mod.ts"].functions) == {"Option"} and table["a/mod.ts"].functions["Option"].kind == "arrow"
    assert set(table["a/mod.ts"].types) == {"Widget", "Gran"} and table["a/mod.ts"].types["Gran"].kind == "type_alias"


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
    assert set(backend.get_all_functions()) == {"src/index.alpha_fn", "a/legacy.legacy_fn", "a/mod.Option"}
    assert {k: set(v) for k, v in backend.get_all_methods_in_application().items()} == {CLASS_SIG: {"alpha_method"}}


def test_bulk_accessors_are_application_scoped():
    backend = _backend()
    assert {o.signature for o in backend.get_callables_overview()} == {
        METHOD_SIG,
        "alpha.inner_fn",
        "src/index.alpha_fn",
        "src/index.<anon@2:2>",
        "a/legacy.legacy_fn",
        "a/mod.Option",
    }
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
    assert list(backend.get_external_symbols()) == ["os.path"], "application B's external (os.join) leaked"
    assert backend.get_external_symbols()["os.path"].id == f"can://typescript/{APP_A}/@external/os/path"
    assert backend.get_calling_lines("alpha.inner_fn") == [5]
    assert backend.get_calling_lines("beta.inner_fn") == []
    assert set(backend.get_synthesized_callables()) == {f"can://typescript/{APP_A}/{SHARED_MODULE}/alpha_fn/<anon@2:2>"}


def test_a_declaration_merged_node_is_the_facet_its_edge_and_labels_name_and_nothing_else():
    """``Option`` is one node carrying ``TSCallable`` and ``TSInterface`` with ``kind: arrow``: it
    is a function to every accessor, an interface to none, and never a namespace."""
    backend = _backend()
    assert "a/mod.Option" not in backend.get_all_interfaces()
    assert backend.get_interface_properties("a/mod.Option") == []
    assert backend.get_all_functions()["a/mod.Option"].kind == "arrow"
    assert "a/mod.Gran" not in backend.get_all_functions()
    assert backend.get_all_type_aliases() == {}, "Gran's kind is 'field': it is not served as a type alias by signature"
    assert backend.get_symbol_table()["a/mod.ts"].types["Gran"].kind == "type_alias"
    assert [f.name for f in backend.get_symbol_table()["a/mod.ts"].fields.values()] == ["Gran"]


def test_a_merged_node_whose_labels_name_no_single_facet_is_raised_as_a_defect():
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    backend = _backend()
    parent = "can://typescript/app_a/x/y.ts"
    undecidable = {"id": f"{parent}/Z", "kind": "field", "name": "Z", "_labels": ["CanNode", "TSClass", "TSInterface"]}
    with pytest.raises(CodeanalyzerExecutionException, match=r"'Z' \(labels \['CanNode', 'TSClass', 'TSInterface'\], kind 'field'\).*one id for two declarations") as e:
        backend._declared(parent, {parent: [("TS_DECLARES", undecidable, {})]})
    assert "can://" not in str(e.value)
    with pytest.raises(CodeanalyzerExecutionException, match="none of the five type kinds"):
        backend._type({"id": f"{parent}/W", "kind": "arrow", "name": "W", "signature": "x/y.W", "_labels": ["TSCallable"]}, {})


def test_get_typescript_file_derives_the_module_key_from_the_id():
    backend = _backend()
    assert backend.get_typescript_file(CLASS_SIG) == "a/mod.ts"
    assert backend.get_typescript_file("a/legacy.legacy_fn") == "a/legacy.js"
    assert backend.get_typescript_file("nope") is None


# =====================================================================================
# The addressing surface (leg 2.5b) answers out of one application, and out of the right facet
# =====================================================================================
def test_locate_resolves_a_shared_module_key_inside_its_own_application():
    """``src/index.ts`` exists in both applications with a different function on the same line.
    The statement seeks the *module's own id* prefix, not the module key, so B's function cannot
    win -- the failure this fixture exists to catch."""
    r = _backend().locate(SHARED_MODULE, 1)
    assert r.callable is not None and r.callable.name == "alpha_fn"
    assert r.callable.signature == "src/index.alpha_fn"
    assert r.module.path == SHARED_MODULE and r.diagnostics == []
    assert r.source == "fn"


def test_locate_finds_the_innermost_callable_and_its_body_node():
    r = _backend().locate("a/mod.ts", 5)
    assert r.callable.signature == METHOD_SIG and r.type is not None and r.type.signature == CLASS_SIG
    assert r.body is not None and r.body.kind == "call"
    assert r.body.id == f"can://typescript/{APP_A}/a/mod.ts/Widget/render@5:3"
    assert r.node_id == r.body.id
    # ``callee`` is a property on a TypeScript body node, so this backend fills it in -- unlike the
    # Python Neo4j backend, where callee resolution is a separate edge and the field stays None.
    assert r.body.callee == f"can://typescript/{APP_A}/a/mod.ts/Widget/render@5:3".replace("@5:3", "/inner_fn")
    # Line-only span: the projection carries no columns and no offsets.
    assert r.body.span.start == (5, 0) and r.body.span.bytes == (0, 0)


def test_locate_at_module_scope_says_the_graph_has_no_module_text():
    r = _backend().locate("a/mod.ts", 20)
    assert r.callable is None and r.body is None and r.source == ""
    assert [d.code for d in r.diagnostics] == ["module_scope", "module_source_unavailable"]


def test_locate_in_another_applications_module_is_file_not_in_graph():
    r = _backend().locate("b/mod.ts", 2)
    assert r.callable is None and [d.code for d in r.diagnostics] == ["file_not_in_graph"]


def test_locate_many_answers_in_input_order_including_the_misses():
    out = _backend().locate_many([("a/legacy.js", 1), ("b/mod.ts", 2), ("a/mod.ts", 5)])
    assert [r.callable.signature if r.callable else None for r in out] == ["a/legacy.legacy_fn", None, METHOD_SIG]


def test_resolve_callable_honours_the_javascript_prefix():
    n = _backend().resolve_callable("legacy_fn")
    assert n.callable == "a/legacy.legacy_fn" and n.file == "a/legacy.js"
    assert n.ref == f"can://javascript/{APP_A}/a/legacy.js/legacy_fn"


def test_resolve_callable_does_not_see_another_applications_colliding_signature():
    n = _backend().resolve_callable("Widget.render")
    assert n.name == "alpha_method" and n.ref.startswith(f"can://typescript/{APP_A}/")


def test_resolve_callable_takes_the_dotted_module_form():
    assert _backend().resolve_callable("alpha_fn", in_module="src.index").callable == "src/index.alpha_fn"
    assert _backend().resolve_callable("alpha_fn", in_module="src/index.ts").callable == "src/index.alpha_fn"


def test_an_anonymous_callable_is_addressed_by_its_signature():
    n = _backend().resolve_callable("<anon@2:2>")
    assert n.callable == "src/index.<anon@2:2>" and n.name == "(anonymous)"
    with pytest.raises(SelectorNotInGraph):
        _backend().resolve_callable("(anonymous)")


def test_a_declaration_merged_node_resolves_only_to_the_facet_its_kind_names():
    """``a/mod.Option`` is one node carrying ``TSCallable`` **and** ``TSInterface`` (the emitter
    minted one id for ``const Option = () => …`` and ``interface Option``). Its ``kind`` is the
    arrow's, so it resolves as the callable it is. ``a/mod.Gran`` is the other shape -- labels
    ``TSTypeAlias``/``TSField``, kind ``field`` -- and must not be reachable through a *callable*
    accessor at all, which is what the ``kind IN`` guard buys: a miss, never a wrong facet."""
    assert _backend().resolve_callable("Option").callable == "a/mod.Option"
    with pytest.raises(SelectorNotInGraph):
        _backend().resolve_callable("Gran")


def test_get_source_answers_for_a_callable_and_refuses_below_it():
    backend = _backend()
    assert backend.get_source(METHOD_SIG) == "alpha code"
    assert backend.get_source(f"can://typescript/{APP_A}/a/mod.ts/Widget/render") == "alpha code"
    with pytest.raises(NotImplementedError):
        backend.get_source(f"can://typescript/{APP_A}/a/mod.ts/Widget/render@5:3")
    with pytest.raises(KeyError):
        backend.get_source("no.such.thing")


def test_get_source_does_not_answer_with_another_applications_text():
    with pytest.raises(KeyError):
        _backend().get_source(f"can://typescript/{APP_B}/b/mod.ts/Widget/render")


def test_describe_hydrates_a_callable_and_leaves_a_body_node_textless():
    backend = _backend()
    located = backend.locate("a/mod.ts", 5)
    resolved = backend.resolve_callable("Widget.render")
    out = backend.describe([resolved, located])
    assert out[0].source == "alpha code"
    assert out[1].source is None  # the graph carries no text below callable granularity


def test_get_entrypoints_sees_only_this_applications_marked_callable():
    """Both applications mark ``shared.Widget.render``; the names differ, so a leak shows up as a
    name rather than as a count."""
    (entrypoint,) = _backend().get_entrypoints()
    assert entrypoint.name == "alpha_method" and entrypoint.signature == METHOD_SIG
    assert entrypoint.owner_signature == CLASS_SIG and entrypoint.owner_kind == "class"
    assert entrypoint.path == "a/mod.ts"


def test_get_entrypoint_classes_sees_only_this_applications_marked_class():
    (klass,) = _backend().get_entrypoint_classes()
    assert klass.signature == CLASS_SIG and klass.name == "Widget" and klass.path == "a/mod.ts"
    assert klass.decorators == ["Deco"] and (klass.start_line, klass.end_line) == (1, 10)


def test_get_entrypoint_coverage_reads_this_applications_anchor():
    coverage = _backend().get_entrypoint_coverage()
    assert coverage.frameworks_detected == ["alpha-framework"], "the other application's report was read"
    assert coverage.unresolved == {"alpha.miss": 2}
    assert coverage.rulesets == ["shipped"] and coverage.errors == [] and coverage.diagnostics == []


def test_get_config_readers_of_a_key_no_body_node_names_is_empty():
    """The fixture graph declares no ``TS_USES_CONFIG`` edge, so this is the empty that means
    "nothing reads it" -- the same answer the superset reference graph gives."""
    assert _backend().get_config_readers("some.key") == []


def test_has_resolution_edges_is_probed_against_this_applications_edges():
    assert _backend().has_resolution_edges is True


# =====================================================================================
# The audit: every statement, class-level and inline, carries the application scope
# =====================================================================================
_MATCHES_BY_PREFIX = re.compile(r"\w+\.id STARTS WITH \$p1 OR \w+\.id STARTS WITH \$p2")
_MATCHES_BY_SIGNATURE = re.compile(r"signature\s*[:=]\s*\$|\.signature IN \$")
#: A ``can://`` id, or a **prefix of one**. ``$bp`` (leg 2.5b) is a resolved callable's own ``ref``
#: plus ``@`` -- minted by ``resolve_callable``, which is itself two-prefix scoped -- so a body node
#: whose id starts with it is this application's by construction, exactly as one matched by a whole
#: id is. It is the narrowest scope on this surface, not a missing one.
_MATCHES_BY_ID = re.compile(r"\bid\s*:\s*\$|\.id IN \$|\.id = \$|\.id STARTS WITH \$bp\b")
_INTROSPECTION = re.compile(r"^\s*CALL (db|dbms)\.")
_ANCHORED_ON_THE_APPLICATION = re.compile(r"\(\w*:Application \{id: \$app_id\}\)")
#: Class-level strings that are Cypher but not a whole statement, judged at their use sites.
_FRAGMENTS = {"_OVERVIEW_PROJECTION", "_SUBTREE"}


#: The ``.format()`` placeholders the templated statements carry, resolved to one representative
#: rendering so the audit reads real Cypher rather than a template: ``{{`` collapses to ``{``, a
#: quantifier's ``{{0,{depth}}}`` to ``{0,5}``, ``{left}``/``{right}`` to the forward direction.
#: Without this a template's ``{{id:$src}}`` does not read as an id lookup and its quantified
#: pattern does not tokenise at all -- the audit would judge a statement nobody issues.
_TEMPLATE_ARGS = {"{rel}": "TS_DDG", "{rels}": SDG_REL_PATTERN, "{depth}": "5", "{left}": "-", "{right}": "->"}

#: Every parameter a ``STARTS WITH`` may bind that is an **application-stamped** id prefix, so the
#: variable carrying it cannot match another application's node. ``$p1``/``$p2`` are the two-prefix
#: application scope (TS-3); ``$bp`` is a resolved callable's own ``ref`` plus ``@``, minted by
#: ``resolve_callable``, which is itself two-prefix scoped; ``pos.module_prefix`` is one module's
#: own id plus ``/``, bound only for a key this application declares. Each is narrower than the
#: application scope, never wider. A **single**-namespace prefix is deliberately not on this list:
#: ``can://typescript/<app>/@external/`` is inside the application but drops every external a
#: ``.js`` module owns, so it is a bug rather than a scope (leg 2.5b review, finding 9).
_SCOPED_VAR = re.compile(r"\b(\w+)\.id STARTS WITH (?:\$(?:p1|p2|bp)|pos\.module_prefix)\b")

#: A variable pinned to an id -- in the node pattern (``{id: $x}``) or in a ``WHERE``
#: (``x.id = $y`` / ``x.id IN $ys``). A ``can://`` id embeds the application that minted it, so a
#: node matched by one is inside that application by construction.
#:
#: The value must be a **parameter**. Accepting any expression (``{id: nid}``, a variable the
#: statement itself projected) made the pin as trustworthy as whatever produced that variable, which
#: the audit does not follow -- so a leak upstream of the pin read as a scope (leg 2.5b review,
#: finding 8). A statement that hydrates by a projected id now scopes the node it walks in from.
_PINNED_BY_ID = re.compile(r"\((\w+):[\w:|]+ ?\{id ?: ?\$\w+\}\)|\b(\w+)\.id (?:=|IN) \$")

#: Relationship types a variable's scope survives, in **either** direction. Two reasons, both
#: about how the emitter mints ids rather than about what this corpus happens to hold:
#:
#: * *containment* -- the child's id **is** the parent's id extended, so a containment neighbour of
#:   an in-scope node carries the same application prefix by construction. Walking one backwards is
#:   as safe as walking it forwards, which is why this is bidirectional where Java's twin is not:
#:   ``(o:TSClass)-[:TS_HAS_METHOD]->(c)`` scopes ``c`` and reads ``o`` off it.
#: * *shared vocabulary* -- the far endpoint is a node no application owns: a ``:TSDecorator`` keyed
#:   by name, a ``:Package`` coordinate, a ``:ConfigKey``, or a ``TS_RESOLVES_TO`` callee that may
#:   be an external. There is no per-application node to reach, so there is nothing to scope.
#:
#: **``TS_CALLS`` and the SDG edges are deliberately absent.** Both endpoints are
#: application-owned, so a graph someone else deployed may well carry an edge between two of them
#: -- and this SDK attaches to graphs it did not emit. Both ends must carry the predicate.
_KEEPS_SCOPE = frozenset(
    {
        "TS_HAS_MODULE",
        "TS_DECLARES",
        "TS_HAS_METHOD",
        "TS_HAS_FIELD",
        "TS_HAS_BODY_NODE",
        "HAS_ARTIFACT",
        "DEFINES_CONFIG",
        "TS_DECORATED_BY",
        "DECLARES_DEPENDENCY",
        "TS_RESOLVES_TO",
        "TS_USES_CONFIG",
    }
)

_CLAUSE_KEYWORDS = ("OPTIONAL MATCH ", "MATCH ", "WHERE ", "WITH ", "RETURN ", "UNWIND ", "UNION ")


def _render(statement: str) -> str:
    """One representative rendering of a ``.format()``-ed statement (see :data:`_TEMPLATE_ARGS`)."""
    for placeholder, value in _TEMPLATE_ARGS.items():
        statement = statement.replace(placeholder, value)
    return statement.replace("{{", "{").replace("}}", "}")


def _clauses(statement: str) -> List[str]:
    """Split into clauses at **top-level** keywords only.

    A naive ``re.split`` breaks a quantified path apart at the ``WHERE`` *inside* its parentheses
    (``((x)-[:TS_CALLS]->(y) WHERE …){1,5}``), which silently drops the pattern's own trailing node
    from the audit -- the variable most likely to be the leak. Depth-tracking keeps it whole.
    """
    parts, depth, start, i = [], 0, 0, 0
    while i < len(statement):
        char = statement[i]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and (i == 0 or statement[i - 1] == " "):
            for keyword in _CLAUSE_KEYWORDS:
                if statement.startswith(keyword, i) and i > start and not statement.endswith(("OPTIONAL ", "STARTS ", "ENDS "), 0, i):
                    parts.append(statement[start:i].strip())
                    start = i
                    break
        i += 1
    parts.append(statement[start:].strip())
    return [c for c in parts if c]


#: A pattern comprehension's pattern -- the ``[`` and the node pattern that follows it, up to the
#: ``|`` before its projection or the ``WHERE`` that filters it. Anchored on the ``(`` so a plain
#: list comprehension (``[n IN nodes(p) | …]``) does not match.
_PATTERN_COMPREHENSION = re.compile(r"\[\s*(\((?:[^\[\]|]|\[[^\[\]]*\])*?)\s*(?:WHERE\b|\|)")

#: A relationship pattern that walks a **distance** -- ``[:R*1..5]`` -- and the shortest-path
#: functions, which do the same without a ``*`` on the visible hop.
_VARLENGTH_HOP = re.compile(r"-\[(\w*)(?::([\w|<>]+))?\*[\d.]*\]-")
_SHORTEST_PATH = re.compile(r"\b(?:all)?[Ss]hortest[Pp]aths?\(")

#: ``MATCH p = …``: the path variable an interior predicate needs to name (``all(n IN nodes(p) …)``).
_PATH_VARIABLE = re.compile(r"^(\w+)\s*=\s*")


def _match_clauses(statement: str) -> List[str]:
    """Every clause that binds node variables: the ``MATCH`` clauses, plus the pattern
    comprehensions anywhere else in the statement.

    A pattern comprehension binds nodes as surely as a ``MATCH`` does -- ``_PATHS`` has two
    (``head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.signature])``) -- and reading only the
    ``MATCH`` clauses made them invisible to the audit (leg 2.5b review, finding 8). They are
    returned as clauses of their own so the chain scanner sees each pattern whole.
    """
    out = [re.sub(r"^(?:OPTIONAL )?MATCH ", "", c) for c in _clauses(statement) if re.match(r"(?:OPTIONAL )?MATCH ", c)]
    return out + [m.group(1) for m in _PATTERN_COMPREHENSION.finditer(statement)]



def _paths_with_scoped_interiors(rendered: str) -> Set[str]:
    """Path variables every one of whose nodes the statement pins, via
    ``all(<v> IN nodes(<p>) WHERE … <v> is scoped …)``.

    The ``all()`` body is read to its balanced close rather than with a regex, so a scope predicate
    that is itself parenthesised -- which :func:`_scoped` always is -- is inside the text searched.
    """
    found: Set[str] = set()
    for m in re.finditer(r"all\((\w+) IN nodes\((\w+)\)\s+WHERE ", rendered):
        var, path, depth, i = m.group(1), m.group(2), 1, m.end()
        while i < len(rendered) and depth:
            depth += (rendered[i] == "(") - (rendered[i] == ")")
            i += 1
        if var in _SCOPED_VAR.findall(rendered[m.end() : i - 1]):
            found.add(path)
    return found


def _chain(clause: str) -> Tuple[List[Tuple[str, str | None, str | None]], List[Tuple[str, str, Set[str]]]]:
    """One clause's bound node variables and the relationship edges between adjacent ones.

    Tokens are scanned in source order; anything the scanner cannot read as a node or a hop breaks
    the chain, so an unparsed construct can only ever *remove* an excuse, never invent one.
    """
    nodes: List[Tuple[str, str | None, str | None]] = []
    links: List[Tuple[str, str, Set[str]]] = []
    previous, rels, pos = None, None, 0
    for m in _TOKEN.finditer(clause):
        if clause[pos : m.start()].strip():  # a gap the scanner could not read: the chain breaks
            previous, rels = None, None
        pos = m.end()
        if m.group(0).startswith("("):
            var = m.group(1) or f"_{m.start()}"
            nodes.append((var, m.group(2), m.group(3)))
            if previous is not None and rels is not None:
                links.append((previous, var, rels))
            previous, rels = var, None
        else:
            rels = set((m.group(6) or "").split("|"))
    return nodes, links


def _unscoped_variables(statement: str) -> List[str]:
    """The node variables the statement binds that are **not** provably inside one application.

    Judging per variable is the point, and it is the hardening Java's leg-3a review forced. A
    presence check ("does the text contain a prefix predicate?") passes
    ``MATCH (s:TSCallable)-[:TS_CALLS]->(t:TSCallable {signature: $sig}) WHERE s.id STARTS WITH $p1
    OR s.id STARTS WITH $p2``, which matches ``t`` by a signature two applications can both declare
    and returns whichever the graph holds.

    A variable is inside when it carries a scoped ``STARTS WITH`` (:data:`_SCOPED_VAR`), when it is
    pinned to an id (:data:`_PINNED_BY_ID`), when it *is* the ``(:Application {id: $app_id})``
    anchor, or when the pattern connects it to one of those over :data:`_KEEPS_SCOPE`. Anonymous
    pattern nodes bind nothing and are skipped.

    A **quantified** path (``((x)-[:R]->(y) WHERE …){1,5}``) binds its interior, so its hops are
    judged like any other variable. A **variable-length** hop (``[:R*1..5]``) and a shortest-path
    function do not: their interior nodes have no name, and every relationship type this surface
    walks a distance over is deliberately outside :data:`_KEEPS_SCOPE`, so an interior node is not
    provably this application's on a graph the SDK did not emit. Such a walk is therefore judged by
    the one predicate that can reach its interior -- ``all(<v> IN nodes(<p>) WHERE <v> is scoped)``
    over a *named* path -- and reported as ``nodes(<p>)`` when it carries none. A walk whose types
    are all in :data:`_KEEPS_SCOPE` needs no such predicate, for the reason that set exists. An
    unnamed distance walk can carry no interior predicate at all, so it is always reported.
    """
    rendered = _render(statement)
    seeds = set(_SCOPED_VAR.findall(rendered)) | {v for m in _PINNED_BY_ID.finditer(rendered) for v in m.groups() if v}
    scoped_paths = _paths_with_scoped_interiors(rendered)
    inside: Dict[str, bool] = {}
    links: List[Tuple[str, str, Set[str]]] = []
    bound: List[str] = []
    for clause in _match_clauses(rendered):
        nodes, clause_links = _chain(clause)
        links += clause_links
        hops = _VARLENGTH_HOP.findall(clause)
        if hops or _SHORTEST_PATH.search(clause):
            rels = {r for _, types in hops for r in types.split("|") if r}
            if not rels <= _KEEPS_SCOPE or not rels:
                path = _PATH_VARIABLE.match(clause)
                name = f"nodes({path.group(1)})" if path else "nodes(<unnamed path>)"
                inside[name] = inside.get(name, False) or bool(path and path.group(1) in scoped_paths)
                bound.append(name)
        for var, labels, props in nodes:
            anchor = labels == "Application" and (props or "").startswith("id: $app_id")
            inside[var] = inside.get(var, False) or var in seeds or anchor
            if not var.startswith("_"):
                bound.append(var)
    changed = True
    while changed:  # containment and shared-vocabulary edges carry scope in either direction
        changed = False
        for src, dst, rels in links:
            if rels <= _KEEPS_SCOPE and inside.get(src, False) != inside.get(dst, False):
                inside[src] = inside[dst] = True
                changed = True
    return sorted({v for v in bound if not inside.get(v, False)})


def _is_scoped(statement: str) -> bool:
    return bool(_MATCHES_BY_PREFIX.search(statement))


def _scope_kind(statement: str) -> str | None:
    if _INTROSPECTION.match(statement):
        return "introspection"
    if _unscoped_variables(statement):
        return None
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
    harvested as a statement in its own right.

    **A statement assembled across branches is read as the union of its branches, in source
    order.** ``get_config_uses`` appends ``AND ck.key = $key`` only when a key was given; the
    harvester concatenates every ``+=`` regardless. Sorting by line number is what makes that union
    the statement the ``key is not None`` branch issues rather than the garbled text it used to be
    (``… RETURN … AND ck.key = $key``), and the other branch is a prefix of it. **The limit that
    remains, stated:** a union can only ever *add* text, so a scope predicate present in one branch
    and missing from another would read as scoped. Nothing on this surface builds a scope
    conditionally -- every ``self._scope_params`` goes in unconditionally -- and this test is where
    that would have to be re-checked if one ever did (leg 2.5b review, finding 8)."""
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
                return "".join(text(v, depth + 1) for v in sorted(assigned[e.id], key=lambda v: v.lineno))
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
    inline = {name: s for name, s in _inline_statements().items() if "<anchor>" not in s and "<query>" not in s}
    return {**{n: s for n, s in _class_level_statements().items() if n not in _FRAGMENTS}, **inline}


#: What the backend is allowed to touch on the driver and on the session it opens. The harvester
#: only follows Cypher passed to ``self._run(`` / ``self._fetch(``, so anything reaching the server
#: another way is invisible to it: ``self._driver.execute_query(...)`` would satisfy a ``.run(``
#: count of one and be harvested zero times. Judging the *surface* instead makes a new driver API a
#: deliberate act -- adding it here, with the harvester taught to follow it. (Java's leg-3a review
#: forced this; it is ported, not re-derived.)
_DRIVER_SURFACE = frozenset({"session", "close"})
_SESSION_SURFACE = frozenset({"run", "close"})


def _attribute_uses(target: str) -> Dict[str, List[str]]:
    """``<method>@<line> -> [attribute, ...]`` for every ``self.<target>.<attr>`` in the class."""
    out: Dict[str, List[str]] = defaultdict(list)
    for fn in ast.walk(ast.parse(inspect.getsource(TSNeo4jBackend))):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == target and getattr(node.value.value, "id", None) == "self":
                out[f"{fn.name}@{node.lineno}"].append(node.attr)
    return out


@pytest.mark.parametrize("target, allowed", [("_driver", _DRIVER_SURFACE), ("_session_obj", _SESSION_SURFACE)])
def test_the_backend_reaches_the_server_only_through_the_harvested_surface(target, allowed):
    """Every attribute the backend touches on the driver and on its session is in a small
    allow-list, so no statement can reach Neo4j by a route the audit does not read."""
    for where, attributes in _attribute_uses(target).items():
        assert set(attributes) <= allowed, f"{where} uses self.{target}.{sorted(set(attributes) - allowed)}, outside the audited surface"


@pytest.mark.parametrize(
    "statement, leaks",
    [
        ("MATCH (s:TSCallable)-[:TS_CALLS]->(t:TSCallable {signature: $sig}) WHERE (s.id STARTS WITH $p1 OR s.id STARTS WITH $p2) RETURN t.id", ["t"]),
        ("MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule) MATCH (x:TSClass) RETURN x.id", ["x"]),
        ("MATCH (c:TSCallable) RETURN c.id", ["c"]),
        ("MATCH (a) ((x:TSCallable)-[:TS_CALLS]->(y:TSCallable) WHERE (x.id STARTS WITH $p1 OR x.id STARTS WITH $p2)){1,5} (m:TSCallable) RETURN m.id", ["a", "m", "y"]),
        # Leg 2.5b review, finding 1: the shape `_PATHS` shipped. Both ends are pinned by a
        # `can://` id, the audit reported nothing -- and every node between them was another
        # application's for the asking, over edge types `_KEEPS_SCOPE` deliberately excludes.
        (
            "MATCH (a:CanNode:TSBodyNode {id:$src}) MATCH (b:CanNode:TSBodyNode {id:$dst}) "
            "MATCH p = allShortestPaths((a)-[:TS_DDG|TS_CFG_NEXT*1..5]->(b)) RETURN nodes(p) AS ns",
            ["nodes(p)"],
        ),
        # The shape `_CALL_PATHS` shipped: the interior predicate was a *label* filter with no
        # prefix conjunct, which is the leak the label hides.
        (
            "MATCH (a:TSCallable {signature:$src}) WHERE (a.id STARTS WITH $p1 OR a.id STARTS WITH $p2) "
            "MATCH p = allShortestPaths((a)-[:TS_CALLS*1..5]->(b:TSCallable)) WHERE all(n IN nodes(p) WHERE n:TSCallable) RETURN nodes(p) AS ns",
            ["b", "nodes(p)"],
        ),
        # An unnamed variable-length walk can carry no interior predicate at all, so naming the
        # path is part of the fix, not a style preference.
        ("MATCH (a:CanNode:TSBodyNode {id:$src})-[:TS_DDG*1..5]->(m:TSBodyNode) WHERE m.id IN $dsts RETURN m.id", ["nodes(<unnamed path>)"]),
        # Finding 8: node variables bound by a pattern comprehension in `RETURN` -- invisible while
        # the harvester read only the MATCH clauses.
        ("MATCH (b:CanNode:TSBodyNode {id:$id}) RETURN head([(c:TSCallable)-[:TS_CALLS]->(x:TSCallable) | c.signature]) AS s", ["c", "x"]),
        # Finding 8: `{id: <a variable the statement projected>}` is only as good as whatever
        # produced the variable, which the audit does not follow.
        ("UNWIND $ids AS nid MATCH (c:TSCallable)-[:TS_HAS_BODY_NODE]->(b:CanNode:TSBodyNode {id:nid}) RETURN b.id", ["b", "c"]),
    ],
    ids=[
        "one-endpoint-of-two",
        "a-second-unanchored-match",
        "no-scope-at-all",
        "a-quantified-paths-far-end",
        "a-shortest-paths-unscoped-interior",
        "a-shortest-paths-interior-filtered-by-label-only",
        "an-unnamed-variable-length-interior",
        "a-pattern-comprehension-in-the-return",
        "pinned-by-a-projected-id-rather-than-a-parameter",
    ],
)
def test_the_audit_rejects_a_statement_that_scopes_only_part_of_its_pattern(statement, leaks):
    """The net's own net. A presence check ("does the text contain a prefix predicate?") passes the
    first and the fourth of these -- the first matches ``t`` by a signature two applications can
    both declare, the fourth walks out of the application on every hop but the first. The last five
    are leg 2.5b's review findings 1 and 8, each written as the statement that actually shipped."""
    assert _unscoped_variables(statement) == leaks
    assert _scope_kind(statement) is None


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (s:TSCallable)-[:TS_CALLS]->(t:TSCallable) WHERE (s.id STARTS WITH $p1 OR s.id STARTS WITH $p2) AND (t.id STARTS WITH $p1 OR t.id STARTS WITH $p2) RETURN s.id",
        "MATCH (:Application {id: $app_id})-[:TS_HAS_MODULE]->(m:TSModule)-[:TS_HAS_FIELD]->(f:TSField) RETURN m.name",
        "MATCH (o:TSClass)-[:TS_HAS_METHOD]->(c:TSCallable) WHERE (c.id STARTS WITH $p1 OR c.id STARTS WITH $p2) RETURN o.signature",
        "MATCH (c:CanNode:TSCallable {id: $id})-[:TS_DECORATED_BY]->(d:TSDecorator) RETURN d.name",
        "CALL db.relationshipTypes()",
        # The fix for finding 1: the interior predicate reaches every node the walk touches.
        "MATCH (a:CanNode:TSBodyNode {id:$src}) MATCH p = allShortestPaths((a)-[:TS_DDG*1..5]->(b:CanNode:TSBodyNode {id:$dst})) "
        "WHERE all(n IN nodes(p) WHERE (n.id STARTS WITH $p1 OR n.id STARTS WITH $p2)) RETURN nodes(p) AS ns",
        # A distance walked over containment needs no interior predicate: the child's id *is* the
        # parent's id extended, which is the whole reason `_KEEPS_SCOPE` exists.
        "MATCH (root:TSClass) WHERE (root.id STARTS WITH $p1 OR root.id STARTS WITH $p2) MATCH (root)-[:TS_DECLARES|TS_HAS_METHOD*0..]->(n) RETURN n.id",
    ],
    ids=[
        "both-endpoints-prefixed",
        "walked-from-the-anchor",
        "containment-read-backwards",
        "pinned-by-id-into-shared-vocabulary",
        "introspection",
        "a-shortest-paths-scoped-interior",
        "a-variable-length-walk-over-containment",
    ],
)
def test_the_audit_accepts_the_shapes_that_are_actually_scoped(statement):
    assert _unscoped_variables(statement) == []
    assert _scope_kind(statement) is not None


def test_the_audit_reads_a_template_as_the_statement_it_becomes():
    """``_render`` is load-bearing, not cosmetic: without it a template's ``{{id:$src}}`` is not an
    id lookup and its quantified pattern does not tokenise, so the audit would judge a statement
    nobody issues -- and would pass it for the wrong reason."""
    assert _render("MATCH (a:CanNode:TSBodyNode {{id:$src}})-[:{rels}*1..{depth}]->(m) RETURN m").startswith("MATCH (a:CanNode:TSBodyNode {id:$src})-[:TS_")
    assert "{0,5}" in _render("(x){{0,{depth}}} (m)")
    template = "MATCH p = (a:CanNode:TSBodyNode {{id:$src}})-[:{rels}*1..{depth}]->(m:TSBodyNode) WHERE " + neo4j_backend._scoped("m")
    assert _unscoped_variables(template + " AND all(n IN nodes(p) WHERE " + neo4j_backend._scoped("n") + ") RETURN m.id") == []
    # ... and the same template without the interior predicate is a leak, not a pass.
    assert _unscoped_variables(template + " RETURN m.id") == ["nodes(p)"]


def test_the_audit_reads_a_statement_assembled_across_branches_in_source_order():
    """``get_config_uses`` appends its key filter conditionally. The harvester concatenates every
    ``+=``, so the ordering is what decides whether it judges Cypher or noise: unsorted it read
    ``… RETURN bn.id AS src, ck.id AS dst, u.prov AS prov AND ck.key = $key`` (leg 2.5b review,
    finding 8). The union of the branches is an over-approximation, stated in
    :func:`_inline_statements`; being in source order is what makes it a *statement*."""
    (harvested,) = [s for name, s in _inline_statements().items() if name.startswith("get_config_uses@")]
    assert harvested.endswith("RETURN bn.id AS src, ck.id AS dst, u.prov AS prov"), harvested
    assert "AND ck.key = $key RETURN" in harvested, harvested


def test_the_audit_sees_every_inline_statement_too():
    """One harvested statement per ``self._run(`` / ``self._fetch(`` site; a site the harvester
    cannot fully reassemble is reported; the one allowed indirection is ``_fetch``'s own two
    ``_run`` calls (their ``<anchor>`` is judged at every ``self._fetch(`` call site). A helper
    parameterised on a label (``<label>``) is judged as written."""
    source = inspect.getsource(TSNeo4jBackend)
    assert source.count(".run(") == 1, "only _run may touch the session"
    inline = _inline_statements()
    assert len(inline) == source.count("self._run(") + source.count("self._fetch("), "a statement site the harvester did not see"
    indirect = sorted({name.split("@")[0] for name, s in inline.items() if "<anchor>" in s or "<query>" in s})
    # ``_fetch``'s ``<anchor>`` is judged at every ``self._fetch(`` call site; ``_paths``' ``<query>``
    # is one of the two class-level path statements, both of which are harvested and judged in their
    # own right (``_PATHS`` by an id point lookup, ``_CALL_PATHS`` by the two-prefix scope).
    assert indirect == ["_fetch", "_paths"], f"unjudged statements passed through a variable: {indirect}"
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
        # leg 2.5b: addressing (Task 1), dataflow (Task 2), entrypoints and config readers (Task 3)
        "locate_many",
        "resolve_callable",
        "resolve_value",
        "_sources_for",
        "get_source",
        "_probe_resolution_edges",
        "_own_edges",
        "_slice",
        "reaches",
        "backward_cone",
        "callers_of",
        "callees_of",
        "_paths",
        "_value_reaches",
        "flows_to_call",
        "get_entrypoints",
        "get_entrypoint_classes",
        "get_entrypoint_coverage",
        "get_config_readers",
    ):
        assert any(name.startswith(expected + "@") for name in inline), f"{expected}'s statement is not harvested"


#: The 0.4.3 labels and relationship types the backend was migrated off, plus the ``_module``
#: property 1.2.0's ``main`` retired (#166). A statement naming any of these matches nothing on a
#: graph this backend accepts.
_RETIRED = (
    "._module",
    "_module IN",
    ":Symbol",
    ":Callable",
    ":CallSite",
    "HAS_CALLSITE",
    "[:CALLS]",
    ":Decorator)",
    "{name: $app}",
)

#: Labels this backend deliberately does not target **yet**. ``TSCanNode``/``JSCanNode`` are the
#: *newer* per-language marker labels the live graph already carries -- not retired vocabulary --
#: and cants#95 is expected to land them as the prefixed replacement for the bare ``CanNode``
#: merge label. Until it does, every statement here anchors on ``CanNode`` or the specific label
#: (the measured seek rule), so naming a marker label would be an untested seek, not a fix.
_NOT_TARGETED_YET = ("TSCanNode", "JSCanNode")


def test_no_statement_names_retired_or_untargeted_vocabulary():
    for name, s in _every_statement().items():
        for retired in _RETIRED:
            assert retired not in s, f"{name} names retired vocabulary {retired!r}: {s[:160]!r}"
        if "TS_DECORATED_BY" not in s:
            assert "DECORATED_BY]" not in s, f"{name} names retired vocabulary 'DECORATED_BY]': {s[:160]!r}"
        for untargeted in _NOT_TARGETED_YET:
            assert untargeted not in s, f"{name} names {untargeted!r}, a label this backend deliberately does not target yet (cants#95): {s[:160]!r}"


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
    leaks = _unscoped_variables(statement)
    assert leaks == [], f"{name} leaks through {leaks}: {statement[:200]!r}"
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
    ``:CanNode`` (a 9 ms label scan without). So: every prefix-scoped statement without an id
    equality never names ``:CanNode``; ``{id: $…}`` point lookups always do."""
    for name, statement in _every_statement().items():
        s = _render(statement)  # a template's `{{id:$x}}` is an id point lookup; judge what runs
        for m in re.finditer(r"\(\w*:([\w:|]+) ?\{id ?: ?\$\w+\}\)", s):
            assert m.group(1).startswith("CanNode:") or m.group(1) in ("Application",), f"{name}: id point lookup without :CanNode -- {m.group(0)}"
        if _is_scoped(s) and not re.search(r"\{id ?: ?\$\w+\}", s):
            assert "CanNode" not in s, f"{name}: a prefix-scoped statement names :CanNode (measured: bare 1.62 ms vs 32.35 ms) -- {s[:120]!r}"
