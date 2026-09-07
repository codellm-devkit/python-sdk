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
  `setTopGainers(java.util.Collection)` where `a4` has `setTopGainers(Collection<QuoteDataBean>)`, and 143
  signatures contain `<` in `a1` against 8 in `a4`. A signature key is a function of what the analyzer could
  resolve, not of the analysis level. Use `a4` for the level-4 dataflow structure it exists to carry, and `a1`
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

- [ ] **Step 1: Failing tests.** Both backends, both corpora. An ambiguous name raises listing candidates and nothing else; a name in no module raises naming the selector.
- [ ] **Step 2: `get_source` is the one that differs by backend, and it must say so.** The graph carries no module `source`, and `:JCallable.code` is the **declaration** slice where the JSON's is the **body block** (upstream codeanalyzer-java#176). So `get_source` off Neo4j returns the declaration text; that is a documented divergence, not a bug to paper over, and it belongs in the lossiness table and the docstring, not only in a module comment.
- [ ] **Step 3: Implement on both backends**, measuring the seek anchor for the `(path, line)` and name-lookup families before choosing one.
- [ ] **Step 4: Backend parity** — identical answers and identical diagnostics on every miss path.
- [ ] **Step 5: Run offline, then live on 7691.** Commit — `feat(java): addressing — locate, resolve, source, describe`.

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

- [ ] **Step 1: Failing tests**, including a **self-loop test**: pick a callable whose `J_DDG` includes a self-loop (there are 978 in the graph) and assert the page contains it. This is the regression guard for python-sdk#349 in a language that has not shipped the bug.
- [ ] **Step 2: Implement locally** over the v2 models' `cfg`/`cdg`/`ddg`/`summary`, reusing `commons/{bounds,graphs}.py` — `edge_sort_key`, `flow_path(..., via=via_table("J"))`, `as_slice_node`, `cone_sinks`, `slice_resolved`, the keyset cursor codec, `sdg_rels("J")`. Where a helper does not fit, say why rather than forking it.
- [ ] **Step 3: Implement on Neo4j**, anchored on the callable's id prefix, never on a doubled containment hop. Record the seek measurements. Quantified path patterns need server 5.9+: read the version at probe and fail at the call that needs it, naming the requirement.
- [ ] **Step 4: Scale.** `thingsboard` has 496,821 body nodes; a page must not scan the graph. Measure a page's wall clock and record it, using the repo's `timed` marker so the assertion does not run under coverage instrumentation.
- [ ] **Step 5: Backend parity** across all fourteen, both corpora, miss paths included. Commit — `feat(java): per-callable graphs, slices, reachability and flow predicates`.

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

- [ ] **Step 1: Entrypoints, honestly.** `get_entrypoints` and `get_entrypoint_classes` read the analyzer's `is_entrypoint` / `is_entrypoint_class` and the `:JEntrypoint` marker. **`get_entrypoint_coverage` reports the report unavailable** (J-4): Java projects no entrypoint report, and a count of syntactically-marked callables is not coverage. The two legacy accessors `get_all_entry_point_methods` / `get_all_entry_point_classes` keep working and keep their return types.
- [ ] **Step 2: `get_decorated_callables` matches annotations** by simple name, with a leading `@` ignored, or by exact fully-qualified name (J-5); on Neo4j it reads `J_ANNOTATED_BY` (26,162 edges). `get_method_bodies` omits a callable with no source text rather than returning `None`.
- [ ] **Step 3: The J-7 leaf accessors** — `get_interfaces`, `get_enums`, `get_enum_members`, `get_records`, sharing TypeScript's names for the same concepts. ThingsBoard has 594 interfaces, 192 enums and 35 records; daytrader8 has none of the last two, so test against the scale corpus.
- [ ] **Step 4: The artifact layer on the facade** — the five already exist on the backends from 3a; the facade delegates. **Do not change `get_config_keys`'s dict key here**: python-sdk#346 tracks aligning Python and TypeScript with Java's artifact-relative key, and doing it piecemeal is how the three languages diverged.
- [ ] **Step 5: The audit covers every statement this leg added** — class-level and inline, every variable not reachable from the `:JApplication` anchor carrying the prefix predicate, and the driver-surface allow-list.
- [ ] **Step 6: Docs.** CHANGELOG at Keep-a-Changelog scale; `docs/agent-api-reference.md` gains the Java query section replacing 3a's "arrives in 3b" line; `CLAUDE.md`'s Java row; the spec's J-numbers marked delivered with any measured facts that differed.
- [ ] **Step 7: The three runs, sequentially** — release gate, Java live on 7691, Python live on 7689. Commit — `feat(java): entrypoints, artifacts and leaf accessors; record the query surface`.

## Definition of done

- Every accessor above answers identically on `JCodeanalyzer` and `JNeo4jBackend` over daytrader8, with identical diagnostics on the miss paths, and works on ThingsBoard.
- The frozen public surface grows by exactly this leg's accessors; nothing pre-existing moves.
- A `J_DDG` self-loop appears in `get_ddg`'s page — the #349 shape cannot ship here.
- Bounds never silent; predicates unbounded by default; one completeness protocol.
- The multi-application audit enumerates every statement, with both applications in one database.
- One PR to `release/2.0`, `Closes #311`.

## Not in this plan

TypeScript (2.5b). The cross-language sweep. Analyzer work: codeanalyzer-java#187 (CRUD absent from v2, so those accessors keep raising), #176 (the graph's `code` is the declaration slice), #215. python-sdk#346 (the config-key alignment) and #349 (Python's self-loop bug).
