# Leg 3b — the Java query surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Java the agent-facing query surface Python gained in legs 1.5/1.6 and TypeScript in 2.5b — addressing, per-callable graphs, bounded slices and flows, entrypoints, the artifact layer and the type-kind leaf accessors — on both backends, with every pre-existing accessor unmoved.

**Architecture:** Four moves. (T0) Java's addressing vocabulary: its own `module_dotted` reading the declared package, and the name-or-signature resolution rule, with the shared helpers taking Java's shapes. (T1) The addressing surface on both backends. (T2) The dataflow surface: per-callable `EdgePage`s, slices, reachability, paths, flow predicates. (T3) Entrypoints, the artifact layer and leaf accessors on the facade, the multi-application audit over every new statement, docs.

**Tech Stack:** Python 3.11+, pydantic v2, neo4j driver 5.x, codeanalyzer-java 3.0.2 (the PyPI wheel with its own JVM; `--emit neo4j` is always full depth).

**Spec:** `docs/design/specs/2026-09-06-leg-3-java.md` (J-1 … J-17, with errata on J-1, J-8 and J-9). Tracking: python-sdk#311 under codellm-devkit/.github#55.

## Global Constraints

- **Nothing pre-existing moves.** `tests/analysis/java/test_java_public_surface.py` freezes the current 47 accessors; this leg's frozen list grows by exactly what it adds and changes no existing entry.
- **Python's semantics are the contract**, not a starting point: signature, keyword-only-ness, defaults, and which bound is finite must match `cldk/analysis/python/python_analysis.py` exactly. Each task quotes the signatures. Where Java genuinely cannot answer, it raises naming the gap — never an empty that reads as a fact (D7).
- **Bounds are asymmetric on purpose (E5).** Slices and `backward_cone` default `depth` to a finite value and cap `max_nodes`; `reaches`, `paths_between`, `call_paths_between`, `flows_to_call`, `flows_to_argument` are **unbounded by default**, because a bounded predicate returns a *wrong* answer rather than a small one. Leg 1.5 shipped that bug by inheriting the slice default onto a predicate. Assert both halves.
- **One completeness protocol:** truncation is reported through `complete` on `EdgePage`/`Slice`/`FlowPaths`, never by returning less in silence.
- **Do not copy Python's per-callable edge query.** `PyNeo4jBackend._OWN_EDGES` binds the containment relationship twice, so Cypher's relationship-uniqueness rule silently drops every self-loop — python-sdk#349, 64,702 lost edges on the Python corpus. **The Java graph has 978 `J_DDG` self-loops**, so the same spelling would lose them here too. Anchor on the callable's id prefix instead, as TypeScript does.
- **Seek anchors are measured per statement family, never ported.** Leg 3a measured Java's whole-application and signature statements and found the bare label wins everywhere and `:JCanNode` nowhere. Leg 2.5b then found the deciding factor is **prefix narrowness**: a per-callable prefix made the marker-label seek 13× faster on TypeScript. Your per-callable body-node statements are the narrowest on this surface, so **re-measure them** (`PROFILE`, median of 5, first discarded) on `thingsboard` and record the numbers here. Expect a different answer from 3a's, and do not assume TypeScript's either.
- **Java's DDG has two provenance tiers**, `ssa` (133,608) and `points-to` (1,134) — Python has three and TypeScript one. `PROV_CERTAINTY` ranks `points-to` as least certain; keep the ranking, state the two-tier fact, invent nothing.
- **No `can://` and no ordinals** in any signature, in any return field other than `ref`/`node_id`/`next_cursor`, or in any error message (E6/E7). Errors name what missed; no suggestions, no fuzzy matching, anywhere (E8).
- **Read-only against every Neo4j instance.** Reference graph: `bolt://localhost:7691`, user `neo4j`, password `cldkleg3test`, holding **both** `daytrader8` (18,354 nodes) and `thingsboard` (598,413 nodes) so scoping is exercised rather than assumed. Python regression graph `bolt://localhost:7689` / `cldkleg16test`. **Port 7687 is an ssh tunnel; never a target.** Containers are in the `podman` docker context. Never set `CLDK_TEST_NEO4J_WRITE_*`.
- **The local analyzer comes from the `codeanalyzer-java` wheel** (`cldk[java]` extra), so no jar path and no `JAVA_HOME` are involved. Levels 3 and 4 need compiled classes; without them the analyzer degrades and the SDK reports it (J-17).
- **Dependency on leg 2.5b.** `LocateResult.body` is a language-neutral `BodyRef`, and `Span` lives in `cldk/analysis/commons/results.py`, only on the 2.5b branch. This branch is stacked on Java 3a, which predates that. **Rebase onto 2.5b before T1** if it has landed; if it has not, T1 must not invent a second `BodyRef` — stop and report instead, because two incompatible spellings of a shared type is the one merge conflict that cannot be resolved mechanically.
- **Reconciling the shared resolver with leg 2.5b, decided rather than left to the rebase.** Both legs
  parameterised `resolve_callable_signature` for the same reason and in different ways: 2.5b injects a
  `dotted=` function at the call site; this leg puts `match_names`/`module_names` on `CallableCandidate`.
  They are redundant, and the candidate fields are the more general of the two — a Java module answers to
  *several* dotted spellings (its declared package, and that package plus each declared type), which a
  function returning one string cannot express. **On the rebase: keep `match_names`/`module_names`, drop
  `dotted=`, and migrate TypeScript to supply `module_names`.** That also fixes a live defect this leg
  found: `resolve_callable_signature` calls `module_dotted(c.path)` with no `extensions=`, so it is
  hard-wired to `.py` and a TypeScript `in_module=` dotted spelling is derived with Python's suffix list.
  Do not resolve this conflict mechanically in either direction.
- **`a4`'s signature spellings are a pruning artifact; assert spellings against `a1`.** `a4` is a pruned copy,
  so most types are unresolvable and the analyzer falls back to the source spelling: `a1` has
  `setTopGainers(java.util.Collection)` where `a4` has `setTopGainers(Collection<QuoteDataBean>)`: across the
  four types the two fixtures share, **4 of `a4`'s 128 signatures carry a generic in the parameter tail and 0 of
  `a1`'s do**. A signature key is a function of what the analyzer could resolve, not of the analysis level.
  *(Erratum: this bullet first said "143 signatures contain `<` in `a1` against 8". That count was taken with a
  walk that skips nested, local and anonymous types — the same walk that yields the retracted 1,177 callables —
  and it counted `<init>`/`<clinit>` in the callable's **name**, not generics. Complete walk, whole of `a1`: 154
  signatures contain `<`, all 154 of them in the name and none in the parameter tail. See the fixture README.)* Use `a4` for the level-4 dataflow structure it exists to carry, and `a1`
  for anything that pins how a signature is spelled.
- Run suites **sequentially**; only one pytest session per checkout (the Java conftest extracts a fixture into the tree and removes it on teardown, so concurrent sessions race). Stage by name.
- **Baselines at this branch's base:** release gate **1069 passed / 224 skipped**, coverage 83.94%; Java offline **321 passed / 26 skipped**; live parity 19, scale 5, audit 34.
- **Never add Claude/AI attribution** to any commit, comment, doc or changelog entry. Changelog entries stay Keep-a-Changelog scale — one to three lines, detail in the spec (python-sdk#350).

## What the Java graph holds (measured on 7691, authority for every task)

`J_CFG_NEXT` 165,604 · `J_DDG` 134,742 (`ssa` 133,608, `points-to` 1,134; **978 self-loops**) · `J_RESOLVES_TO` 133,423 · `J_PARAM_IN` 76,810 · `J_CDG` 46,936 · `J_PARAM_OUT` 44,961 · `J_ANNOTATED_BY` 26,162 · `J_SUMMARY` 22,222 · `:JEntrypoint` 2,604 marker nodes.

**There is no entrypoint report.** The `:JApplication` anchor carries no entrypoint key of any kind — unlike codeanalyzer-python 1.4.1 and codeanalyzer-typescript 1.3.0, which both project one. Java has only the `is_entrypoint` boolean and the marker label, so `get_entrypoint_coverage` reports the report unavailable through the shared model's own vocabulary (J-4). Do not synthesise a report from the booleans: a count of syntactically-marked callables is not a coverage report, and presenting it as one is the ambiguous-empty defect wearing a hat.

---

## Task 0: Java's addressing vocabulary

**Files:**
- Modify: `cldk/analysis/java/backend.py` (Java's `module_dotted`, the resolution rule, the extension set), `cldk/analysis/commons/resolve.py` only if a parameter is genuinely missing
- Test: new `tests/analysis/java/test_java_addressing_rules.py` (offline, over the v2 fixtures)

**Interfaces:**
- Produces, in `cldk/analysis/java/backend.py`, for T1 to consume — **nothing else in the leg may compute a dotted name**:
  - `java_module_dotted(package: str, types: Iterable[str] = ()) -> Tuple[str, ...]` — the *several* dotted
    spellings a unit answers to (its declared package, and that package plus each declared type), not one
    string, and taking the package and type names rather than a `JCompilationUnit`, so the Neo4j backend can
    call it with a `J_DECLARES` collect. Both halves of the original line were wrong; T1 reads this one.
  - `java_callable_names(signature: str) -> Tuple[str, ...]` — the signature and the signature with the
    parameter tail cut at the **last** `(` (the J-1 erratum).
  - `java_resolve_callable(name, candidates, *, in_class=None, in_module=None) -> str` — the one entry point
    both backends resolve through.
  - On `CallableCandidate`: `match_names` (default `()`, meaning "the signature is the name") and
    `module_names` (default **`None`**, meaning "derive it from the path"; an empty tuple means the language
    supplied none, and T1's `J_DECLARES` collect must pass one deliberately, never by accident).

- [x] **Step 1: Failing tests for the two rules the spec fixes.** J-2: a Java module's dotted name is its **declared package** plus the type name, never derived from the path — `module_dotted("src/main/java/com/ibm/…/TradeDirect.java")` must not yield `src.main.java.com.ibm…`. The shared helper's path-derived behaviour is right for Python and TypeScript and wrong here, so Java passes its own. J-3: `resolve_callable("cancelOrder")` matches on the simple name with the parameter tail stripped; two overloads raise `AmbiguousName` listing the full signatures; spelling `cancelOrder(java.lang.Integer, boolean)` resolves exactly. daytrader8 has 15 overloaded names; use a real one. — **as done:** `tests/analysis/java/test_java_addressing_rules.py`, 15 tests over the committed a1/a4 fixtures. J-2 is asserted against the real miss (`module_dotted("src/main/java/…/TradeDirect.java", extensions=(".java",))` → `src.main.java.com.ibm…`) and the declared package `com.ibm.websphere.samples.daytrader.impl.direct`; J-3 against the spec's own pair, `cancelOrder(java.lang.Integer, boolean)` / `cancelOrder(java.sql.Connection, java.lang.Integer)` on `TradeDirect` (measured, not assumed: a4 has 11 same-class overloaded names, 9 of them on `TradeDirect`; a1 has 26 — the plan's "daytrader8 has 15" matches neither fixture).
- [x] **Step 2: Run them; watch them fail.** — **as done:** collection error, `cannot import name 'java_callable_names' from 'cldk.analysis.java.backend'`.
- [x] **Step 3: Implement**, reusing `commons/resolve.py` and `commons/keys.py` where they already take a parameter, and passing Java's shapes rather than forking. If a helper cannot express Java's rule, say so in the report and add the parameter rather than copying the function. — **as done:** `java_module_dotted` / `java_callable_names` / `java_resolve_callable` in `cldk/analysis/java/backend.py`. Two shared helpers could **not** express Java's rules and took parameters rather than being forked: `CallableCandidate` gained `match_names` and `module_names` (empty keeps every Python/TypeScript call site as it was), and `resolve_callable_signature` gained `by_full_name` for the last clause of an ambiguity's advice, because "by naming more of the dotted path" cannot split two overloads. Note `resolve_callable_signature` called `module_dotted(c.path)` with **no** `extensions=`, i.e. hard-wired to `.py` — the leg-2.5b parameterisation never reached the resolver, so TypeScript's `in_module=` dotted spelling is derived with Python's suffix list today (out of scope here; `module_names` is the seam that fixes it).
- [x] **Step 4: The local-class rule (J-1 erratum) applies here too.** A local or anonymous class's qualified name carries its declaring callable, so `in_class=` must accept that spelling. Test with a real `$anon$N` from the a4 fixture. — **as done:** four tests. `in_class=` accepts `…PingManagedThread.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse).$anon$0` whole or as a dotted suffix; the callable-less `PingManagedThread.$anon$0` raises `SelectorNotInGraph(kind="in_class")`; bare `$anon$0` is ambiguous across three declaring callables. **The plan is wrong about the fixture:** a4 has four units and *no* `$anon$N`; the anonymous classes are in **a1** (four of them, `PingManagedExecutor`, `PingManagedThread`, `PingWebSocketJson`, `PingWebSocketTextAsync`), so the tests use a1. The erratum also forced the tail cut in `java_callable_names` to be at the **last** `(`, not the first — the declaring callable's own tail sits inside the anonymous class's qualified name.
- [x] **Step 5: Run `tests/analysis/java tests/models/java`.** Commit — `feat(java): the addressing vocabulary — package-derived dotted names and signature-aware resolution`. — **as done:** 336 passed / 26 skipped (baseline 321/26, +15 new); release gate 1084 passed / 224 skipped, coverage 83.99% (baseline 1069/224, 83.94%).

---

## Task 1: the addressing surface

**Files:**
- Modify: `cldk/analysis/java/backend.py`, `cldk/analysis/java/codeanalyzer/codeanalyzer.py`, `cldk/analysis/java/neo4j/neo4j_backend.py`, `cldk/analysis/java/java_analysis.py`
- Test: new `tests/analysis/java/test_java_addressing.py` (offline), new `tests/analysis/java/test_java_addressing_live.py` (7691), `test_java_public_surface.py`

**Interfaces** — mirror exactly:
```
locate(self, path: str, line: int) -> LocateResult
locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]
resolve_callable(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> SliceNode
resolve_value(self, name: str, *, within: str) -> SliceNode
get_source(self, node_id: str) -> str
describe(self, nodes: Sequence[object]) -> List[SliceNode]
has_resolution_edges(self) -> bool
```

- [x] **Step 1: Failing tests.** Both backends, both corpora. An ambiguous name raises listing candidates and nothing else; a name in no module raises naming the selector. — **as done:** `tests/analysis/java/test_java_addressing.py`, 51 tests, **both backends over both fixtures** (a1 for spellings, a4 for the `formal_in` vertices). The graph backend is exercised for real, not stubbed: leg 3a made it rebuild the canonical `JApplication` and answer from it, so seeding `__dict__["_application"]` runs the shipped code; only the one statement Task 1 adds (the body-node fetch) goes through a fake responder built from the same fixture, and the live suite proves that half. Ambiguity, miss, and both scoping keywords are asserted, plus the ruling that both backends raise the **same exception type with the same message** on every miss path.
- [x] **Step 2: `get_source` is the one that differs by backend, and it must say so.** — **as done:** it is stated in three places a caller reads — `JavaAnalysis.get_source`'s and `JavaAnalysis.locate`'s docstrings, and the lossiness bullets of `docs/agent-api-reference.md` (a new bullet: callable text is the body block locally and the declaration over Neo4j, `neo.endswith(ref)`; a **body node** has text only locally, so `get_source` raises and `describe` gives `None` over the graph rather than the enclosing declaration; a module-scope `locate` over the graph is `""` plus `module_source_unavailable`; a `resolve_value` ref has text on neither). The relation is asserted, not tolerated: `neo.get_source(k).endswith(ref.get_source(k))` on all **1,117** body-bearing callables of daytrader8.
- [x] **Step 3: Implement on both backends**, measuring the seek anchor for the `(path, line)` and name-lookup families before choosing one. — **as done, and the plan was half wrong about what there was to measure.** The seven accessors are implemented **once**, on `JavaAnalysisBackend` (`cldk/analysis/java/backend.py`), because 3a's architecture leaves nothing for a second implementation to read; each backend supplies three facts (`_body_nodes`, `_body_source`, `has_resolution_edges`) plus the index it already builds. So the **name-lookup family issues no Cypher at all** — there is no anchor to choose, only a wall clock to report (ThingsBoard, median of 5, first discarded: `resolve_callable` 7.91 ms, `get_source` <0.01 ms, `describe` 7.64 ms, all zero statements, over an index built once in the 28.0 s reconstruction 3a already pays for every accessor). The **`(path, line)` family** adds exactly one statement, the per-callable body-node fetch, and it was measured — see the table on `JNeo4jBackend._BODY_NODES`: bare `:JBodyNode` wins (86.6 ms / 16,024 db hits for 8 callables and 4,004 nodes, against `:JCanNode`'s 86.8 ms / 20,028 and a `J_HAS_BODY_NODE` hop's 204.2 ms / 503,741 — the hop loses because `:JCallable` owns **no** id index). One more measurement changed a decision: adding `AND b.id STARTS WITH $prefix` so the scope audit's existing regex would match cost **10–15×** (145.8 ms against 14.1 for one callable, 1,349.2 against 86.6 for eight — the planner seeks the broad prefix and filters), so the audit learned the `UNWIND $prefixes AS p` spelling instead and `_responder` now judges the values of `$prefixes`.
- [x] **Step 4: Backend parity** — identical answers and identical diagnostics on every miss path. — **as done:** structural (one implementation) and asserted. Live on daytrader8 with ThingsBoard in the same database: `locate_many` over **1,354 positions** (one inside every span-bearing callable, plus line 1 of all 138 modules) identical on callable, type, module, dotted module name, diagnostics, `node_id`, body kind and every span **line**; `resolve_callable` identical on all **1,216** callables; `resolve_value` identical on all **1,166** named parameters; six miss paths raise the same type with the same message on both, with no `can://` in any of them.
- [x] **Step 5: Run offline, then live on 7691.** Commit — `feat(java): addressing — locate, resolve, source, describe`. — **as done:** Java offline **402 passed / 26 skipped** (baseline measured on this chain tip: 339/26); Java live on 7691 **12 passed** (new suite) plus the 3a suites unchanged; release gate and Python live on 7689 re-run. Two facts the plan did not have: `has_resolution_edges` costs **no extra round trip** (the schema probe already reads `db.relationshipTypes()`, so attach goes from three statements to four, not five), and `resolve_value`'s domain in Java is the **parameter list** — verified that all 225 `formal_in` vertices of the level-4 fixture line up name-for-name and index-for-index with the declared parameters, and live that every `<callable id>@formal_in:<n>` ref names a real `:JBodyNode {kind:'formal_in'}` in the graph.

---

## Task 2: per-callable graphs, slices, reachability, paths, predicates

**Files:**
- Modify: `cldk/analysis/java/backend.py`, both backends, `cldk/analysis/java/java_analysis.py`
- Test: new `tests/analysis/java/test_java_dataflow.py`, new `tests/analysis/java/test_java_dataflow_live.py`, `test_java_public_surface.py`

**Interfaces** — mirror exactly (Java's edge models, Python's signatures):
```
get_cfg(self, callable: str, *, in_class: str | None = None, page_size: int = DEFAULT_PAGE_SIZE, cursor: str | None = None) -> EdgePage[JCfgEdge]
get_cdg(...same...) -> EdgePage[JCdgEdge]
get_ddg(...same...) -> EdgePage[JDdgEdge]
slice_backward(self, src: str, *, within: str, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice
slice_forward(...same...) -> Slice
backward_cone(self, sinks: Sequence[str], *, depth: int | None = DEFAULT_DEPTH, max_nodes: int = DEFAULT_MAX_NODES) -> Slice
reaches(self, src: str, dst: str, *, depth: int | None = None) -> bool
callers_of(self, name: str, *, in_class: str | None = None, in_module: str | None = None) -> List[SliceNode]
callees_of(...same...) -> List[SliceNode]
paths_between(self, src: str, dst: str, *, src_within: str, dst_within: str, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths
call_paths_between(self, src: str, dst: str, *, depth: int | None = None, max_paths: int = DEFAULT_MAX_PATHS) -> FlowPaths
flows_to_call(self, src: str, callee: str, *, within: str, depth: int | None = None) -> bool
flows_to_argument(self, src: str, callee: str, arg: str, *, within: str, depth: int | None = None) -> bool
```
`paths_between` takes **two** scopes: one can never find a cross-callable path.

**The block above lists thirteen, not fourteen** — the heading's count is wrong, and inherited: leg
2.5b's Task 2 says "fourteen accessors" over the same thirteen names, and thirteen is what
`PythonAnalysis` carries. Thirteen is what shipped.

**The finding this task turned on, before any of the steps: codeanalyzer-java 3.0.1 emits the L4
port lattice disconnected from the statement dependence graph.** Not one of the reference database's
134,742 `J_DDG` or 46,936 `J_CDG` edges has a `formal_in` / `actual_in` / `formal_out` /
`actual_out` vertex at either end; the lattice's only edges are `J_PARAM_IN` (actual_in →
formal_in), `J_PARAM_OUT` (formal_out → actual_out) and `J_SUMMARY` (actual_in → actual_out). So a
`formal_in` — the only thing `resolve_value` ever returns — has **out-degree zero**, and the same
holds in `analysis.json` (measured on the committed a4 fixture and on the whole-application `-a 4`
run). codeanalyzer-python does *not* have this gap: 129,883 `PY_DDG` edges leave a `formal_in` on
the leg-1.6 reference graph. Consequently `slice_forward` would be the seed alone, `paths_between`
`[]`, and `flows_to_call` / `flows_to_argument` `False` — **for every input, whatever the program
does**, which is the ambiguous empty D7 forbids and the Global Constraints' "where Java genuinely
cannot answer, it raises naming the gap". Those four refuse, through one shared guard
(`JavaAnalysisBackend._require_connected_ports`) over one measured fact
(`_ports_carry_dependence`), so the day the analyzer connects the layers they answer with no code
change. `slice_backward` is deliberately **not** guarded: it follows `J_PARAM_IN` reversed and
reaches the argument vertex at every call site that passes one, which varies with the program.

- [x] **Step 1: Failing tests**, including a **self-loop test**: pick a callable whose `J_DDG` includes a self-loop (there are 978 in the graph) and assert the page contains it. This is the regression guard for python-sdk#349 in a language that has not shipped the bug. — **as done:** `tests/analysis/java/test_java_dataflow.py`, 66 tests (both backends over a4 for the call-graph half, the in-memory backend for the accessors that reach Cypher), and `test_java_dataflow_live.py`, 29 tests on 7691. The self-loop guard is asserted in **both**: offline, `MarketSummaryDataBean.toJSON()` has 4 self-loops of 48 DDG edges, and the fixture has 20 in all; live, `Log.printCollection(java.util.Collection)` has **20 edges, 11 of them self-loops**, and the same test runs the doubled-containment spelling and asserts it returns **9** — so the difference is a fact of the suite rather than of a comment. Every expected number was derived from the fixture and from the graph directly, never by running the implementation.
- [x] **Step 2: Implement locally** over the v2 models' `cfg`/`cdg`/`ddg`/`summary`, reusing `commons/{bounds,graphs}.py`. — **as done, and the split is not the one the plan assumed.** The **call-graph half** (`reaches`, `callers_of`, `callees_of`, `backward_cone`, `call_paths_between`) is implemented **once**, on `JavaAnalysisBackend`, over the `get_call_graph()` both backends already build — leg 3a makes `JNeo4jBackend` project `J_CALLS` into the same `nx.DiGraph` keyed by the same J-1 names, so a second implementation would have nothing to read and would only be a second place to drift. That is Task 1's precedent, and it means those five issue **no new Cypher at all**. So does `_callee_values`: `flows_to_call`'s targets are minted from the parameter list (Task 1 verified formal_in ⟺ parameters, 225 for 225), not queried. Six seams remain per backend: `get_cfg`/`get_cdg`/`get_ddg`, `_value_slice`, `_value_paths`, `_value_reaches`, plus `_ports_carry_dependence` and the level guard `_require_dataflow`. Every shared helper fitted and none was forked: `edge_sort_key`, `edge_page`, `keyset_where`/`encode_cursor`/`cursor_params`, `check_*`, `cone_sinks`, `slice_resolved`, `flow_path(..., via=via_table("J"))`, `shortest_walks`, `sdg_rels("J")`. **`bounded_subgraph` does not fit** — for TypeScript's reason, `backward_cone` needs an ancestor walk over a reversed view, not an induced descendant subgraph — and `body_node_kind` is language-specific, so Java has `java_body_node_kind`; its one Java-only rule is that the parameter's **name comes from the parameter list, not from the vertex**, because the Neo4j projection carries no `of` property on a `:JBodyNode` at all (measured: 0 of daytrader8's 11,436), so reading `of` would name a parameter locally and leave it `None` over the graph.
- [x] **Step 3: Implement on Neo4j**, anchored on the callable's id prefix, never on a doubled containment hop. Record the seek measurements. Quantified path patterns need server 5.9+. — **as done.** Five statements, all prefix-scoped: `_OWN_EDGES`, `_SLICE`, `_PATHS`, `_VALUE_REACHES`, `_PORTS_CARRY_DEPENDENCE`. Seek measured on ThingsBoard (`PROFILE`, median of 5, first discarded, over the driver), **not ported** — the bare `:JBodyNode` wins again, as in Task 1 and unlike TypeScript, because it owns its own id range index (`j_body_node_id`):

  | statement | wall clock | db hits |
  |---|---|---|
  | DDG page, 8 callables / 2,729 edges — `UNWIND $prefixes` + `(s:JBodyNode)` | **76.71 ms** | **19,407** |
  | …the same with `(s:JCanNode:JBodyNode)` | 79.02 ms | 22,136 |
  | DDG page, 1 callable / 482 edges — `$bp` + `(s:JBodyNode)` | **17.81 ms** | **3,195** |
  | …`(c:JCallable {id})-[:J_HAS_BODY_NODE]->(s)` | 19.31 ms | 21,435 |
  | …`(c:JSymbol {id})-[:J_HAS_BODY_NODE]->(s)` | 18.78 ms | 21,435 |
  | 1 callable, `J_CFG_NEXT` — bare / marker | 3.12 / 3.15 ms | 1,329 / 1,360 |
  | 1 callable, `J_CDG` — bare / marker | 2.59 / 2.95 ms | 1,267 / 1,267 |
  | 1 callable, `J_DDG` — bare / marker | 14.79 / 15.27 ms | 2,231 / 2,713 |
  | slice seed (point lookup, depth 5) — bare / marker / `:JCanNode` | 2.65 / 2.83 / 2.70 ms | 8 / 9 / 8 |
  | `UNWIND` of one prefix vs a bare `$bp` | 4.21 / 3.85 ms | 823 / 823 |

  The containment hop is within noise on the clock and reads **6.7×** the db hits (`:JCallable` owns no id index), and it is also *wrong*: it drops every self-loop. `UNWIND $prefixes` is kept over a bare `$bp` — 0.4 ms, same db hits — because it is the spelling the multi-application audit reads as the narrow scope and whose bound values `_responder` checks. **Quantified path patterns turned out not to be needed:** the accessors that need them in TypeScript (`reaches`, `backward_cone`) are answered in memory here, and `allShortestPaths` / `*0..` are ancient Cypher — so there is no version gate to add. (The reference server is 5.26.30 in any case.)
- [x] **Step 4: Scale.** — **as done.** ThingsBoard (598,413 nodes, 496,821 body nodes, 28,763 callables), through the shipped accessors, median of 5 with the first discarded: `get_ddg` **29.1 ms** on the largest callable (482 edges), `get_cfg` 12.2 ms, `get_cdg` 10.9 ms, `callers_of` 9.9 ms, `slice_backward` **22.2 ms** for a 604-node slice, and the port probe 231 ms (once per backend, and only if one of the four guarded accessors is called; 6.5 ms on daytrader8). The 28.3 s reconstruction leg 3a already pays is unchanged. `test_a_page_does_not_scan_the_scale_graph` carries the ceiling under the repo's `timed` marker, which `tests/analysis/java/conftest.py` gained (the hook the Python and TypeScript conftests already carry).
- [x] **Step 5: Backend parity** across all thirteen, both corpora, miss paths included. — **as done, with one measured exception that is the analyzer's.** Live on daytrader8 with ThingsBoard in the same database: `get_cfg`/`get_cdg`/`get_ddg` edge-for-edge over the 40 busiest callables; `callers_of`/`callees_of` over all **1,216** callables; `reaches`, `backward_cone` (bounded and unbounded) and `call_paths_between` path-for-path; `slice_backward` node-for-node over every named parameter of the 30 busiest; seven miss paths raising the same type with the same message on both, with no `can://` in any. **The exception:** over the whole application the in-memory backend reports 5,434 `ddg` edges and the graph 5,347. Every one of the 87 is an edge whose endpoint is a body key codeanalyzer-java **did not emit as a body node** — 38 distinct keys, all of the shape `<line>:0`, all on `points-to` edges, none on `cfg` (6,984 identical) or `cdg` (4,416 identical), and nothing in the other direction. The Neo4j emitter materialises nodes from `body{}`, so an edge with no node is not projected. Neither backend hides its own source's answer; the live suite measures the difference exactly and `get_ddg`'s docstring states it. It is invisible on the committed fixtures (a4 has 0 dangling endpoints), so it is a whole-application fact only. Worth an upstream issue.

  **Runs.** Java offline **494 passed / 67 skipped** (baseline 402/38: +66 dataflow, +13 frozen signatures, +13 audit statements, +29 live tests skipped offline). Java live on 7691, whole tree **559 passed / 2 skipped**. Release gate **1419 passed / 338 skipped**, coverage **84.47%** (baseline 1327/309, 84.64%: −0.17 pp over +287 statements, all of it the traversal behind the port guard — `_value_paths` / `_value_reaches` on both backends — which no input can reach on today's analyzer output). Python live on 7689 **589 passed / 6 skipped**, unchanged.

  Commit — `feat(java): per-callable graphs, slices, reachability and flow predicates`.

---

## Task 3: entrypoints, artifacts, leaf accessors, the audit, docs

**Files:**
- Modify: both backends, `cldk/analysis/java/java_analysis.py`, `CHANGELOG.md`, `docs/agent-api-reference.md`, `CLAUDE.md`, the spec
- Test: new `tests/analysis/java/test_java_entrypoints.py`, `test_java_neo4j_multi_application_scope.py`, `test_java_public_surface.py`

**Interfaces** — mirror exactly, plus the J-7 leaf accessors:
```
get_entrypoints(self) -> List[JCallableOverview]      get_entrypoint_classes(self) -> List[JClassOverview]
get_entrypoint_coverage(self) -> EntrypointCoverage   get_callables_overview(self) -> List[JCallableOverview]
get_method_bodies(self, signatures) -> Dict[str, str] get_decorated_callables(self, markers) -> List[JCallableOverview]
get_callsites_for(self, signatures) -> Dict[str, List[JCallSite]]
get_external_symbols(self) -> Dict[str, ...]          get_modules(self) -> List[JCompilationUnit]
get_artifacts(self) -> Dict[str, PyArtifact]          get_dependencies(self, *, direct_only=False, ecosystem=None, declared_in=None) -> List[PyDependency]
get_config_keys(self) -> Dict[str, PyConfigKey]       get_config_uses(self, key=None) -> List[PyConfigUseEdge]
get_unresolved_config_reads(self) -> List[PyConfigRead]   get_config_readers(self, key) -> List[JCallableOverview]
get_interfaces / get_enums / get_enum_members / get_records
```

- [x] **Step 1: Entrypoints, honestly.** `get_entrypoints` and `get_entrypoint_classes` read the analyzer's `is_entrypoint` / `is_entrypoint_class` and the `:JEntrypoint` marker. **`get_entrypoint_coverage` reports the report unavailable** (J-4): Java projects no entrypoint report, and a count of syntactically-marked callables is not coverage. The two legacy accessors `get_all_entry_point_methods` / `get_all_entry_point_classes` keep working and keep their return types. — **as done:** all three on `JavaAnalysisBackend`, so both backends run one implementation, for Tasks 1 and 2's reason. `get_entrypoint_coverage` returns `EntrypointCoverage(diagnostics=[entrypoint_report_unavailable])` and the premise was **verified against the server**, not assumed: `keys(:JApplication)` is exactly `analyzer_name, analyzer_version, name, schema_version` and `analysis.json` has no report key. Three spellings had to be reconciled, none of them in the plan: the wire says `is_entrypoint_class` on a type, the graph says **`is_entrypoint`** on a `:JType` (there is no `is_entrypoint_class` property in the graph at all — asking for one makes the server warn), and `:JEntrypoint` is stamped on both marked labels. 3a's `reconstruct.type_` already mapped the graph's spelling correctly, and the two sources agree **id for id**: 133 callables / 66 types on daytrader8, 1,501 / 904 on ThingsBoard, 2,604 marker nodes across both. The `:JEntrypoint` label is therefore never read — it is derived from the same boolean, and reading it would be a second source for one fact. The legacy pair is unmoved and `test_get_entrypoints_agrees_with_the_legacy_accessor` pins that the two never disagree.
- [x] **Step 2: `get_decorated_callables` matches annotations** by simple name, with a leading `@` ignored, or by exact fully-qualified name (J-5); on Neo4j it reads `J_ANNOTATED_BY` (26,162 edges). `get_method_bodies` omits a callable with no source text rather than returning `None`. — **as done:** J-5's three spellings collapse to **one** rule, because the Java wire carries an annotation's *simple* name only: both sides are compared on the segment after the last `.` with a leading `@` dropped, which subsumes the exact-FQN case (`Override`, `@Override` and `java.lang.Override` are one query, 328 callables). It reads `J_ANNOTATED_BY` on Neo4j **through 3a's containment walk** rather than through a statement of its own — that relationship is already one of the seven `_SUBTREE` collects, so the decorators are on the reconstructed callables; the plan implied a new statement and there is none. (26,162 is the whole database; 807 are daytrader8's and 25,355 ThingsBoard's.) It is the *callable-level* filter, which the plan does not say: daytrader8's 16 `@Trace` uses are all on **types**, so `["Trace"]` is empty and that is asserted. `get_method_bodies` omits the miss and the text-less callable alike, so every value is a real non-empty `str` — **1,117** of daytrader8's 1,216, the 99 implicit constructors omitted and nothing else. (An earlier note here said 1,115 and blamed the two `<clinit>$N()` initializers as well; they carry a body block and do come back. The filter is `code`, not `declaration` — 99 callables lack the first, 101 the second. Step 2 above already said 1,117.) One thing had to be decided: **what a "signature" is here.** A Java signature is unique only within its declaring type, so `get_method_bodies` / `get_callsites_for` key on the **J-1 key** `JCallableOverview.key` hands back, matched exactly, with no resolution and no fuzzy matching. Python's parameter *name* (`signatures`) is kept, because the constraint is to mirror the signature.
- [x] **Step 3: The J-7 leaf accessors** — `get_interfaces`, `get_enums`, `get_enum_members`, `get_records`, sharing TypeScript's names for the same concepts. ThingsBoard has 594 interfaces, 192 enums and 35 records; daytrader8 has none of the last two, so test against the scale corpus. — **as done:** three `kind` filters over the flattened type index plus one member reader, keyed by qualified name as `get_all_classes` is. The plan's counts are exact, re-measured through the shipped accessors on 7691: **594 / 192 / 35** on ThingsBoard, and daytrader8 really has none of the last two (136 classes, 10 annotations, 3 interfaces). `get_enum_members` reads a real 52-constant enum (`TbMsgType`) live. **One deliberate divergence from TypeScript**, which returns `[]` for an unknown enum: Java **raises `SelectorNotInGraph(kind="enum")`**, because "this enum declares no constant" and "no such enum" are different answers (D7) and this leg's rule is that the second says so. Names are shared; the miss policy is this leg's.
- [x] **Step 4: The artifact layer on the facade** — the five already exist on the backends from 3a; the facade delegates. **Do not change `get_config_keys`'s dict key here**: python-sdk#346 tracks aligning Python and TypeScript with Java's artifact-relative key, and doing it piecemeal is how the three languages diverged. — **as done:** the facade delegates all five and reimplements none; `get_config_keys`'s artifact-relative key is untouched and now asserted (`"@key/" in k and not k.startswith("can://")`). The sixth, `get_config_readers`, is new on the backends and returns `[]` for the reason 3a gave for `get_config_uses`: there is no `config_uses` on the Java wire, so there is no edge to resolve to a reading callable. **The plan's interface block also lists `get_modules` and `get_external_symbols`, and only one of them was a real gap.** `get_modules` was **not** added — Java's module accessor is `get_compilation_units`, on the frozen surface since 1.x, and J-2 keeps `JCompilationUnit`'s name; §4 of the spec never asked for it. `get_external_symbols` was, and it is the one accessor whose two sources were asked different questions: codeanalyzer-java emits `external_symbols` only under **`--external-calls`**, off by default and forced on by `--emit neo4j`. So the graph carries them and a plain `-a` payload carries `None`. Rather than `{}` (which reads as "this project calls nothing outside itself"), the accessor **raises naming the flag**, and `JNeo4jBackend` gained the leg's **one new statement** to project `:JExternal` into `JApplication.external_symbols` — 1,195 rows on daytrader8, 2,570 on ThingsBoard, disjoint, prefix-scoped because an `:JExternal` hangs off no containment edge.
- [x] **Step 5: The audit covers every statement this leg added** — class-level and inline, every variable not reachable from the `:JApplication` anchor carrying the prefix predicate, and the driver-surface allow-list. — **as done, and the audit needed no new machinery.** It parametrises over `_every_statement()`, so the one statement Task 3 adds was judged the moment it existed: **24 statements before (7 class-level + 17 inline), 25 after** (7 + 18), every one `prefix`- or `application`-scoped per bound variable, none naming retired vocabulary, none anchoring on `:JCanNode`, none spelling the scope with `any(`, and every `allShortestPaths` still carrying `all(n IN nodes(p) …)`. `_external_rows` was added to the harvester's expected-sites list so a later refactor that loses it fails rather than passes. Two behavioural leak tests were added beside them: one over the whole Task 3 surface (the fixture's two applications declare the same class, path and signature, so a leak reads as a wrong *name*) and one on `get_external_symbols` alone, whose two ghosts are named `printlnA`/`printlnB` for exactly this. The driver-surface allow-list is unchanged and still holds.
- [x] **Step 6: Docs.** CHANGELOG at Keep-a-Changelog scale; `docs/agent-api-reference.md` gains the Java query section replacing 3a's "arrives in 3b" line; `CLAUDE.md`'s Java row; the spec's J-numbers marked delivered with any measured facts that differed. — **as done:** the CHANGELOG gains two `Added` bullets and six `Known limitations` lines, all one to three lines (python-sdk#350); `docs/agent-api-reference.md` replaces "Still arriving in leg 3b" with the two landed sections and gains five lossiness bullets — the port lattice (**codeanalyzer-java#227**), the 87 dangling `ddg` endpoints (**#228**), the twelfth body-node kind `switch`, `get_external_symbols`' asymmetry and `get_callsites_for`'s intra-line order — beside the `get_source` divergence Task 1 recorded (#176); `CLAUDE.md` gains a "since leg 3b" paragraph; J-4, J-5 and J-7 are marked delivered and a **§4 erratum** records the three things §4 claims that no task of this plan carries. **One published number was wrong and is corrected:** Task 2's `java_body_node_kind` docstring said "1,498 `J_CDG` edges out of one `switch` on ThingsBoard"; re-measured, there are 3 such vertices in daytrader8 and 385 in ThingsBoard carrying 71 and 3,561 `J_CDG` edges, at most **154** out of any one. Every other number Task 2 published was re-measured and holds exactly (`J_DDG` 134,742, `J_CDG` 46,936, 978 self-loops, **0** DDG/CDG edges touching a port vertex, daytrader8 `ddg` 5,434 local against 5,347 on the graph, 87 dangling edges over 38 distinct keys all of shape `<line>:0` and all `points-to`, `cfg` 6,984 and `cdg` 4,416 identical).
- [x] **Step 7: The three runs, sequentially** — release gate, Java live on 7691, Python live on 7689. Commit — `feat(java): entrypoints, artifacts and leaf accessors; record the query surface`. — **as done:**

  | run | result | against Task 2's baseline |
  |---|---|---|
  | Release gate (`uv run pytest`) | **1481 passed / 350 skipped**, coverage **84.76%** | 1419/338, 84.47% — **+62 passed** (41 new offline tests, 18 frozen signatures, 3 audit tests) and **+12 skipped** (the new live suite, skipped without a server); **+0.29 pp** coverage, because the leg's own code is 100%-covered projection and filter logic and the one new statement is exercised offline through the fake responder |
  | Java live on 7691 (`tests/analysis/java tests/models/java`) | **633 passed / 2 skipped** | 559/2 — **+74**, which is the same 62 plus the 12 live tests now running |
  | Python live on 7689 | **601 passed / 6 skipped** | **identical at this branch's tip with the change stashed**, which is the point of the run. Task 2 recorded 589/6 for the same gate; the 12 are `tests/analysis/commons` + `tests/models/python`, i.e. a wider path selection, not a change |

  **One pre-existing test had to change, and it is the leg's one real behaviour change to a 3a
  answer.** `test_application_view_parity` asserted `neo.application.external_symbols ==
  ref.application.external_symbols` — true in 3a because both were `None`. Now the graph carries
  1,195 and the local payload still `None`, so the test pins the two views as they really are and
  says why. That was found by the live run, not by the offline suite, because the fixtures carry no
  `:JExternal`.

  A **process note worth recording**: `git stash -u` was run once while a live pytest session held
  this checkout, which yanked the extracted Java fixture out from under it. The tree came back
  intact and the run finished, but the Global Constraints' "one pytest session per checkout" covers
  more than concurrent pytest — anything that rewrites the working tree counts.

## Definition of done

- Every accessor above answers identically on `JCodeanalyzer` and `JNeo4jBackend` over daytrader8, with identical diagnostics on the miss paths, and works on ThingsBoard.
- The frozen public surface grows by exactly this leg's accessors; nothing pre-existing moves.
- A `J_DDG` self-loop appears in `get_ddg`'s page — the #349 shape cannot ship here.
- Bounds never silent; predicates unbounded by default; one completeness protocol.
- The multi-application audit enumerates every statement, with both applications in one database.
- One PR to `release/2.0`, `Closes #311`.

## Not in this plan

TypeScript (2.5b). The cross-language sweep. Analyzer work: codeanalyzer-java#187 (CRUD absent from v2, so those accessors keep raising), #176 (the graph's `code` is the declaration slice), #215. python-sdk#346 (the config-key alignment) and #349 (Python's self-loop bug).
