# Leg 4a — the shared SDG path skeleton

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the triplicated SDG path Cypher into one generator in `commons/graphs.py`, with **no
behaviour change** — every generated statement byte-identical to the constant it replaces.

**Architecture:** Three Neo4j backends each carry `_VIA_CASE`, `_PATH_ORDER` and `_PATHS`.
`_VIA_CASE` and `_PATH_ORDER` are byte-identical across all three, so they become
`via_case(P)` / `path_order(P)`. `_PATHS` differs in five places, all of which become parameters of
one `sdg_path_query()`. Nothing else moves: the runner methods, the projections consumed downstream,
and the statements' semantics are untouched.

**Tech Stack:** Python 3.11+, pytest, no new dependency. Neo4j is not required — every test here
compares strings.

**Spec:** `docs/design/specs/2026-09-09-leg-4-taint.md` (T8, §2's fourth fact, §8 step 1)

## Global Constraints

- **No behaviour change.** This plan changes no query's semantics. The completion criterion for
  every task is string equality against the current constant, not a live query.
- **No public accessor changes name, signature or return type.** Nothing in this plan touches a
  facade.
- The three backends are **shipped code on `release/2.0` mid-rc** (`v2.0.0-rc.4`). Byte-identity is
  what keeps the refactor from being the thing that reddens a live suite.
- Java's relationship variable in `_PATHS` is `e`; Python's and TypeScript's is `r`. It is
  semantically inert and **must not be normalised in this plan** — pass it as a parameter so
  byte-identity holds. Normalising is a separate, arguable change.
- Python's path statements are scoped **by id**, which
  `tests/analysis/python/test_neo4j_multi_application_scope.py` sanctions as one of four scope kinds
  for a measured reason. **Do not "fix" Python to carry `STARTS WITH`** — that is a behaviour change,
  it was measured and rejected, and #381 was closed as not a defect for exactly this.
- Do not delete `_paths()`'s unused `prefix=` keyword argument here. It is dead, it is cosmetic, and
  it is not this plan's business.

## File Structure

| File | Responsibility after this plan |
| --- | --- |
| `cldk/analysis/commons/graphs.py` | gains `via_case(P)`, `path_order(P)`, `sdg_path_query(...)` alongside the existing `sdg_rels` / `sdg_rel_pattern` / `via_table` / `hop_sort_key` / `flow_path` family |
| `cldk/analysis/python/neo4j/neo4j_backend.py` | `_VIA_CASE` / `_PATH_ORDER` deleted; `_PATHS` becomes one `sdg_path_query(...)` call |
| `cldk/analysis/java/neo4j/neo4j_backend.py` | same |
| `cldk/analysis/typescript/neo4j/neo4j_backend.py` | same |
| `tests/analysis/commons/test_lifted_helpers.py` | gains the byte-identity assertions (the file already exists and is the right home) |

`graphs.py` is 370 lines and already owns this family, so no new module. It grows by roughly 60 lines.

---

### Task 1: `via_case()` and `path_order()`

The two pure duplicates. Both are currently built at class-definition time from the module-level
`VIA` (`via_table("PY")` / `("J")` / `("TS")`), so the lifted forms take `P` and rebuild the same
table internally.

**Files:**
- Modify: `cldk/analysis/commons/graphs.py` (append after `via_table`, around line 158)
- Test: `tests/analysis/commons/test_lifted_helpers.py`

**Interfaces:**
- Consumes: `via_table(P)` — already in this module.
- Produces: `via_case(P: str) -> str`, `path_order(P: str) -> str`. Task 3 replaces three class
  attributes with calls to these.

- [ ] **Step 1: Write the failing test**

`tests/analysis/commons/test_lifted_helpers.py`:

```python
import pytest

from cldk.analysis.commons.graphs import path_order, via_case
from cldk.analysis.java.neo4j.neo4j_backend import JNeo4jBackend
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend

#: Each backend's relationship-type prefix, and the class whose constants it must reproduce.
BACKENDS = [("PY", PyNeo4jBackend), ("J", JNeo4jBackend), ("TS", TSNeo4jBackend)]


@pytest.mark.parametrize("P, backend", BACKENDS)
def test_via_case_reproduces_the_backends_constant(P, backend):
    """The lift is only safe if it is byte-identical: a changed CASE arm would change which word a
    hop is reported under, and a changed ORDER BY would change which paths max_paths keeps."""
    assert via_case(P) == backend._VIA_CASE


@pytest.mark.parametrize("P, backend", BACKENDS)
def test_path_order_reproduces_the_backends_constant(P, backend):
    assert path_order(P) == backend._PATH_ORDER


def test_the_three_constants_differ_only_in_the_relationship_prefix():
    """What is and is not shared, stated exactly.

    **Corrected while implementing.** An earlier draft of this plan asserted the three constants were
    byte-identical. They are not: three distinct values each, because every CASE arm names its own
    language's relationship types (``J_DDG`` at 244 characters against ``PY_DDG`` at 250). What was
    triplicated is the *expression*; the result was always per-language, which is exactly why the
    lifted forms take ``P`` and are functions rather than constants. A divergence beyond the prefix
    fails here rather than being absorbed into a parameter silently.
    """
    assert len({via_case(P) for P, _ in BACKENDS}) == 3
    assert len({path_order(P) for P, _ in BACKENDS}) == 3
    for P in ("J", "TS"):
        assert via_case(P).replace(f"{P}_", "PY_") == via_case("PY")
        assert path_order(P).replace(f"{P}_", "PY_") == path_order("PY")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run --all-groups pytest tests/analysis/commons/test_lifted_helpers.py -v`
Expected: FAIL with `ImportError: cannot import name 'via_case'`.

- [ ] **Step 3: Implement the minimal code**

In `cldk/analysis/commons/graphs.py`, after `via_table`:

```python
def via_case(P: str) -> str:
    """The Cypher ``CASE`` that maps a hop's relationship type to the caller's word for it.

    Computed in Cypher rather than in Python because the ``ORDER BY`` in :func:`path_order` sorts by
    the same vocabulary :func:`hop_sort_key` sorts by. Ordering by the raw ``type(r)`` instead would
    be just as deterministic and a *different* order (``PY_CDG`` before ``PY_DDG`` before
    ``PY_PARAM_IN``, against ``argument`` before ``control`` before ``data``), so the two backends
    would truncate ``max_paths`` to different witnesses.
    """
    return "CASE type(relationships(p)[i]) " + " ".join(f"WHEN '{rel}' THEN '{word}'" for rel, word in via_table(P).items()) + " ELSE type(relationships(p)[i]) END"


def path_order(P: str) -> str:
    """One sort key per path, ordered exactly as Python would order the tuple :func:`hop_sort_key`
    builds.

    ``\\u0001`` is the separator rather than ``|`` for one reason: string comparison agrees with
    field-by-field comparison **only** when the separator sorts below every character a field can
    hold, and ``|`` (0x7C) sorts *above* every lowercase letter, which would order a variable ``x``
    after ``xy``. ``elementId`` is the last field of each hop and breaks the tie between parallel
    relationships a caller cannot tell apart; it is stable for repeated calls against one database
    and means nothing outside it.
    """
    return "reduce(k = '', i IN range(0, length(p) - 1) | k + " + via_case(P) + " + '\\u0001' + coalesce(relationships(p)[i].var, '') " "+ '\\u0001' + nodes(p)[i + 1].id + '\\u0001' + elementId(relationships(p)[i]) + '\\u0001')"
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --all-groups pytest tests/analysis/commons/test_lifted_helpers.py -q --no-cov`
Expected: 22 passed.

**Pass `--no-cov`.** One test file exercises ~42% of the package against a 50% `--cov-fail-under`
floor, so the run reports `FAIL Required test coverage of 50% not reached` on top of a green suite.
That is the floor, not a failure.

Also register both names in this file's existing `LIFTED` table (the
`cldk.analysis.commons.graphs` list), so the "lives in commons, and `python.backend` re-exports the
same object" audit covers them alongside their siblings.

If `test_path_order_reproduces_each_backends_constant` fails, diff the two strings character by
character — the likely cause is the implicit string concatenation in the original spanning two
source lines with a space between `") "` and `"+ '\\u0001'"`. Reproduce it exactly; do not
"tidy" the spacing.

- [ ] **Step 5: Commit**

```bash
git add cldk/analysis/commons/graphs.py tests/analysis/commons/test_lifted_helpers.py
git commit -m "refactor(graphs): lift via_case and path_order out of the three backends"
```

---

### Task 2: `sdg_path_query()`

**Files:**
- Modify: `cldk/analysis/commons/graphs.py`
- Test: `tests/analysis/commons/test_lifted_helpers.py`

**Interfaces:**
- Consumes: `path_order(P)` from Task 1.
- Produces:

```python
def sdg_path_query(P: str, *, node_label: str, endpoint_scope: str = "",
                   interior_scope: str = "", projection: str, rel_var: str = "r") -> str: ...
```

`endpoint_scope` and `interior_scope` are Cypher fragments already spelled by the caller (a
backend's `_scoped()` output, or `"n.id STARTS WITH $prefix"`), empty when that backend does not
carry one. `projection` is the per-node map body **without** the surrounding braces. The returned
string is still a `.format()` template carrying `{rels}` and `{depth}`, exactly as today, so the
runner methods are unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/analysis/commons/test_lifted_helpers.py`:

```python
from cldk.analysis.commons.graphs import sdg_path_query

#: The five differences between the three `_PATHS` constants, as the arguments that reproduce each.
#: Written out rather than derived, because a derivation that produced the wrong string would also
#: produce the wrong expectation.
PATHS_ARGS = {
    "PY": dict(
        node_label="PyBodyNode",
        projection="ref: n.id, kind: n.kind, var: n.var, line: n.start_line, "
        "callable: head([(c:PyCallable)-[:PY_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_line: head([(c:PyCallable)-[:PY_HAS_BODY_NODE]->(n) | c.start_line])",
    ),
    "J": dict(
        node_label="JBodyNode",
        endpoint_scope="n.id STARTS WITH $prefix",
        interior_scope="n.id STARTS WITH $prefix",
        projection="ref: n.id, kind: n.kind, line: n.start_line",
        rel_var="e",
    ),
    "TS": dict(
        node_label="CanNode:TSBodyNode",
        interior_scope="(n.id STARTS WITH $p)",
        projection="ref: n.id, kind: n.kind, of: n.of, line: n.start_line, "
        "callable: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_line: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.start_line])",
    ),
}


@pytest.mark.parametrize("P, backend", BACKENDS)
def test_sdg_path_query_reproduces_the_backends_paths(P, backend):
    """Byte-identical, including Java's `e` relationship variable and TypeScript's parenthesised
    scope fragment. A difference here is a behaviour change, which this plan forbids."""
    assert sdg_path_query(P, **PATHS_ARGS[P]) == backend._PATHS


@pytest.mark.parametrize("P, backend", BACKENDS)
def test_the_generated_template_still_formats(P, backend):
    """The result is a `.format()` template, not a finished statement: the runners pass `rels` and
    `depth`. If the lift ever emitted a literal brace it would raise here rather than at runtime."""
    from cldk.analysis.commons.graphs import sdg_rel_pattern

    out = sdg_path_query(P, **PATHS_ARGS[P]).format(rels=sdg_rel_pattern(P), depth="")
    assert "{" not in out and "}" not in out.replace("{{", "").replace("}}", "")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run --all-groups pytest tests/analysis/commons/test_lifted_helpers.py -k sdg_path_query -v`
Expected: FAIL with `ImportError: cannot import name 'sdg_path_query'`.

- [ ] **Step 3: Implement the minimal code**

In `cldk/analysis/commons/graphs.py`:

```python
def sdg_path_query(P: str, *, node_label: str, endpoint_scope: str = "", interior_scope: str = "", projection: str, rel_var: str = "r") -> str:
    """The shortest-path statement all three Neo4j backends issue for ``paths_between``.

    ``allShortestPaths`` and not a plain variable-length match. A variable-length pattern enumerates
    *trails*, the shape that does not terminate on a real dependence graph; ``allShortestPaths`` is a
    bidirectional BFS and answers the pathological cases in milliseconds. ``$cap`` is
    ``max_paths + 1`` at the call site, so one extra row reports the truncation rather than a second
    ``count(p)`` traversal for a number the caller cannot act on.

    The five per-language differences are the five parameters. ``endpoint_scope`` and
    ``interior_scope`` are Cypher fragments over the node variable ``n``, empty for a backend whose
    statement is scoped by id instead (see the scope-kind audit in
    ``tests/analysis/python/test_neo4j_multi_application_scope.py`` — id-keying is sanctioned, and
    adding a predicate to those statements was measured and rejected). ``rel_var`` exists only
    because Java spells its relationship ``e`` and the other two spell it ``r``; the name is inert
    and is a parameter so this lift can be byte-identical rather than a judgement call.

    Returns a ``.format()`` template still carrying ``{rels}`` and ``{depth}``.
    """
    a_scope = f" WHERE {endpoint_scope.replace('n.', 'a.')}" if endpoint_scope else ""
    b_scope = f" WHERE {endpoint_scope.replace('n.', 'b.')}" if endpoint_scope else ""
    interior = f" WHERE all(n IN nodes(p) WHERE {interior_scope})" if interior_scope else ""
    return (
        f"MATCH (a:{node_label} {{{{id:$src}}}}){a_scope} "
        f"MATCH (b:{node_label} {{{{id:$dst}}}}){b_scope} "
        "MATCH p = allShortestPaths((a)-[:{rels}*1..{depth}]->(b))" + interior + " "
        "WITH p, " + path_order(P) + " AS key ORDER BY length(p), key LIMIT $cap "
        f"RETURN [n IN nodes(p) | {{{{{projection}}}}}] AS ns, "
        f"[{rel_var} IN relationships(p) | {{{{via: type({rel_var}), var: {rel_var}.var, prov: {rel_var}.prov}}}}] AS rs"
    )
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run --all-groups pytest tests/analysis/commons/test_lifted_helpers.py -v`
Expected: 13 passed.

The `.replace('n.', 'a.')` on `endpoint_scope` is the one fragile line: it assumes the fragment
names the node variable as `n.`. Only Java supplies one and it does. If a future backend supplies a
fragment shaped differently, the parameter should become a callable — but do not pre-build that
here.

- [ ] **Step 5: Commit**

```bash
git add cldk/analysis/commons/graphs.py tests/analysis/commons/test_lifted_helpers.py
git commit -m "refactor(graphs): add sdg_path_query, reproducing all three _PATHS byte-identically"
```

---

### Task 3: Replace the constants in the three backends

The tests from Tasks 1 and 2 compare against `backend._PATHS`, so they keep passing **only** if the
replacement is exact — they become the regression test for this task rather than needing new ones.

**Files:**
- Modify: `cldk/analysis/python/neo4j/neo4j_backend.py` (`_VIA_CASE` ~1590, `_PATH_ORDER` ~1600, `_PATHS` ~1615)
- Modify: `cldk/analysis/java/neo4j/neo4j_backend.py` (~898, ~918, ~933)
- Modify: `cldk/analysis/typescript/neo4j/neo4j_backend.py` (~1847, ~1854, ~1867)

**Interfaces:**
- Consumes: `via_case`, `path_order`, `sdg_path_query` from Tasks 1-2.
- Produces: nothing new. `_PATHS` remains a class attribute of the same name and type, because
  `tests/analysis/python/test_neo4j_multi_application_scope.py` enumerates class attributes that are
  `str` and starting with a Cypher clause — a `_PATHS` that stopped being a plain class-level string
  would silently drop out of that audit, and
  `test_the_audit_sees_the_dataflow_statements_too` names it explicitly so it would fail loudly.

- [ ] **Step 1: Run the audit tests first, to record the baseline**

Run: `uv run --all-groups pytest tests/analysis/python/test_neo4j_multi_application_scope.py -v`
Expected: all pass. Note the count; it must not change.

- [ ] **Step 2: Replace in the Python backend**

Delete `_VIA_CASE` and `_PATH_ORDER` (keeping their comments above `_PATHS` where they explain the
statement), and replace `_PATHS` with:

```python
    _PATHS = sdg_path_query(
        "PY",
        node_label="PyBodyNode",
        projection="ref: n.id, kind: n.kind, var: n.var, line: n.start_line, "
        "callable: head([(c:PyCallable)-[:PY_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_line: head([(c:PyCallable)-[:PY_HAS_BODY_NODE]->(n) | c.start_line])",
    )
```

Add `sdg_path_query` (and `via_case`/`path_order` if `_CALL_PATHS` still references the deleted
`_PATH_ORDER` — it does; point it at `path_order("PY")`) to the `cldk.analysis.commons.graphs`
import.

- [ ] **Step 3: Run the tests**

Run: `uv run --all-groups pytest tests/analysis/commons/test_lifted_helpers.py tests/analysis/python -v`
Expected: pass, with the same count as Step 1 for the audit file.

- [ ] **Step 4: Repeat for Java and TypeScript**

Java (note `rel_var="e"` and both scope fragments):

```python
    _PATHS = sdg_path_query(
        "J",
        node_label="JBodyNode",
        endpoint_scope="n.id STARTS WITH $prefix",
        interior_scope="n.id STARTS WITH $prefix",
        projection="ref: n.id, kind: n.kind, line: n.start_line",
        rel_var="e",
    )
```

TypeScript (interior only, and the fragment comes from the module's own `_scoped`):

```python
    _PATHS = sdg_path_query(
        "TS",
        node_label="CanNode:TSBodyNode",
        interior_scope=_scoped("n"),
        projection="ref: n.id, kind: n.kind, of: n.of, line: n.start_line, "
        "callable: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.signature]), "
        "c_line: head([(c:TSCallable)-[:TS_HAS_BODY_NODE]->(n) | c.start_line])",
    )
```

- [ ] **Step 5: Run the full offline suite**

Run: `uv run --all-groups pytest tests -x -q`
Expected: the same pass/skip counts as before this plan. **Use `--all-groups`, not
`--all-extras`** — `--all-extras` builds a 58-package environment against `--all-groups`' 148 and
produces phantom failures.

- [ ] **Step 6: Confirm no duplicate remains**

Run:
```bash
grep -rn "_VIA_CASE\|_PATH_ORDER = " cldk/analysis/*/neo4j/neo4j_backend.py
```
Expected: no output. Any hit is a copy the lift missed.

- [ ] **Step 7: Commit**

```bash
git add cldk/analysis/*/neo4j/neo4j_backend.py
git commit -m "refactor(neo4j): the three backends build _PATHS from the shared generator"
```

---

### Task 4: Live confirmation

String equality proves the statements did not change. It does not prove the backends still work,
because a mis-wired import or a stale attribute would pass every test above.

**Files:** none. This task runs tests only.

- [ ] **Step 1: Run each language's live dataflow suite against a real graph**

```bash
uv run --all-groups pytest tests/analysis/python/test_dataflow.py -q
uv run --all-groups pytest tests/analysis/java/test_java_dataflow_live.py -q
uv run --all-groups pytest tests/analysis/typescript/test_typescript_dataflow_live.py -q
```

Expected: the same counts as before the plan. These need the `CLDK_TEST_NEO4J_*` variables pointing
at graphs emitted by the pinned analyzer generations; they skip cleanly when absent, and **a skip is
not a pass** — if all three skip, this task is not done.

- [ ] **Step 2: Confirm `paths_between` returns identical witnesses**

For one known reachable pair per language, capture `[h.via for h in path.hops]` before and after
(use `git stash` to compare). Expected: identical lists in identical order. `max_paths` truncates a
*total order*, so a changed order would silently change which witnesses a caller sees — the one
regression byte-identity cannot rule out if the lift accidentally reordered the `ORDER BY` terms.

- [ ] **Step 3: Commit nothing; report the counts**

This task produces evidence, not a diff. Record the three counts in the PR body.
