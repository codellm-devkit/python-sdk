---
name: using-cldk
description: Use when querying a codebase through the CLDK Python SDK — addressing a callable, reading control/data flow, slicing, tracing a value across calls, or listing entrypoints, artifacts and configuration — in Python, TypeScript, JavaScript or Java, over an analysis.json or an attached Neo4j graph.
---

# Using CLDK (schema v2 query surface)

One facade per language, two interchangeable backends behind each. Every accessor answers the
same question whichever backend serves it, **including when it has to refuse** — a backend that
cannot answer says so and says why, and never returns an empty value that reads like a real answer.

```python
from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

py = CLDK.python(project_path="/path/to/repo")                      # runs the analyzer in process
ts = CLDK.typescript(project_path="/path/to/repo")                  # ditto, subprocess
jv = CLDK.java(project_path="/path/to/repo",
               analysis_level="system_dependency_graph")            # levels matter, see below

graph = CLDK.java(project_path=None, backend=Neo4jConnectionConfig(  # read-only, no analyzer runs
    uri="bolt://localhost:7687", username="neo4j", password="…",
    application_name="daytrader8"))
```

`CLDK(language="…").analysis(…)` still works as a compatibility shim.

## Identity

Ids are `can://<app>/<lang>/<file>/<type>/<callable-signature>`, application outermost. **One
`can://<app>/` prefix scopes a whole application across every language it is written in** — that
is the property multi-language repositories depend on.

- **Ids are opaque.** Match on properties, never split on delimiters. An application named
  `python` yields `can://python/python/…`; anything reading segment 1 as the language is wrong.
- **Externals are language-neutral**: `can://<app>/@external/<module>/<name>`, no language
  segment, in every analyzer.
- **Artifacts are language-neutral too**: `can://<app>/artifact/<path>`. So are `Package` ids,
  which are purls (`pkg:pypi/flask`). These are the cross-language merge keys.

You rarely construct an id. Address things by name and let the SDK resolve:

```python
jv.resolve_callable("TradeDirect.cancelOrder(java.lang.Integer, boolean)")
py.locate("addons/account/models/account_move.py", line=812)
```

Ambiguity **raises with the candidates listed** rather than guessing. There is no fuzzy matching
anywhere, including in error messages — advice you are given is always followable verbatim.

## Analysis levels

Accessors need the level that produces their data, and asking below it refuses rather than
returning nothing.

| level | what appears |
| --- | --- |
| `symbol table` | modules, types, callables, call sites |
| `call graph` | the call graph; resolution edges |
| `program dependency graph` | per-callable `cfg` / `cdg` / `ddg` |
| `system dependency graph` | the interprocedural overlay: `param_in`, `param_out`, `summary` |

`AnalysisLevel`'s own values are spelled with spaces, as above. The factories accept the
underscored spelling too (`analysis_level="system_dependency_graph"`), so either works — but
`AnalysisLevel("system_dependency_graph")` does not.

`--emit neo4j` always runs at full depth, so a graph backend has no shallow mode.

## The query surface

**Addressing** — `locate`, `locate_many`, `resolve_callable`, `resolve_value`, `get_source`,
`describe`.

**Per-callable graphs** — `get_cfg`, `get_cdg`, `get_ddg`. Paged, and the page tells you the truth
about itself: `complete` is computed from a source that can disagree with the rows, never from the
same match that produced them.

**Slices and cones** — `slice_backward`, `slice_forward`, `backward_cone`.

**Call graph** — `reaches`, `callers_of`, `callees_of`, `call_paths_between`.

**Value flow** — `paths_between`, `flows_to_call`, `flows_to_argument`. Each hop is labelled
`data`, `argument`, `return` or `control`, so a path is evidence rather than an assertion.

**Inventory** — `get_entrypoints`, `get_entrypoint_classes`, `get_entrypoint_coverage`,
`get_external_symbols`, the artifact and dependency getters, `get_config_keys` and the config-read
accessors.

## Bounds are never silent

Anything that can truncate reports that it did. `depth` and `max_paths`/`max_nodes` have documented
defaults, and the result carries `complete` — a computed field on `Slice` and `EdgePage`, a plain one on
`FlowPaths` — derived independently of the returned rows. A paged result also carries
`next_cursor`; feed it back to continue.

```python
s = jv.slice_backward("orderID", within=CANCEL, max_nodes=3)
s.total      # 35 — the size of the whole slice
s.complete   # False — so you know 3 is a window, not the answer
```

Do not paper over a bound by raising it blindly on a large graph; page instead.

## Refusals mean something

Three kinds, and they are not interchangeable:

- **Below the analyzer floor.** A graph emitted by too old an analyzer is refused *at attach* with
  `GraphSchemaMismatch` naming the version found and the floor. Re-emit; there is no in-place
  upgrade.
- **The data cannot support the question.** Measured from the graph in front of you, never from a
  version string, so a re-emitted graph starts answering on its own with no SDK change.
- **The analyzer does not emit it.** Named, with the upstream issue where one exists.

An empty list is a real answer meaning "none"; a refusal means "not knowable here". Treat them
differently.

## Traps

1. **A signature is not unique.** Two types can declare the same one, and in TypeScript a
   declaration-merged name is several nodes. Address by the full key, or pass a scoping keyword,
   and let ambiguity raise.
2. **`flows_to_argument` is coarse on Java.** Every actual of a call site is fed by the statement
   containing it, so it answers `True` for any argument of a reached call. Paths are complete;
   per-argument precision is not there yet.
3. **DDG provenance differs by language** — three tiers in Python, two in Java, one in TypeScript.
   `prov` says which; do not compare tiers across languages.
4. **`get_source` over Neo4j is the declaration, in process it is the body block.** Both are
   documented, and the relation between them is exact — but they are not the same string.
5. **A polyglot application is pushed in all languages, or none.** A `--emit neo4j` push is
   destructive and the application prefix is shared, so pushing one language sweeps derived rows
   (notably `@external` ghosts) belonging to its siblings until they push again.

## Both backends, one answer

Where a backend is genuinely lossy, it is documented and asserted rather than hidden — the Neo4j
projection carries no column or byte offsets (they are `-1`, never `0`), a module's text is not
projected, and comment accessors have no comment nodes to read on some languages. Everything else
is asserted equal between the two, per accessor, including on the miss paths.
