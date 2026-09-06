# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

**Leg 2.5a — TypeScript on codeanalyzer-typescript 1.2.0's schema v2**, on both backends, with the
public API frozen. Ships in `2.0.0-rc.3`. Design record:
`docs/design/specs/2026-09-06-leg-2.5-typescript.md`.

### Breaking

- **A graph emitted by codeanalyzer-typescript below 1.2.0, or holding no matching application, is
  refused at attach** with `GraphSchemaMismatch` instead of answering every query with zero rows.
  Re-ingest with `codeanalyzer-typescript>=1.2.0 --emit neo4j`; there is no in-place upgrade.
- **`TSNeo4jBackend` speaks the 1.2.0 graph vocabulary**, which shares nothing with 0.4.3's.
- **Values that changed shape:** `TSCallEdge` is `{src, dst, prov, weight}`; `TSCallable` no longer
  carries `path`, `call_sites`, `accessed_symbols`, `local_variables` or `code_start_line`, and
  `TSModule` no longer carries `file_path` or `module_name`, since v2 keys modules by path and stores
  source once per module; `TSCallableOverview.from_callable` takes a required keyword-only `path`.
- **Removed:** `get_entry_point_methods` and `get_service_entry_point_methods`, which only ever raised
  `NotImplementedError`. Working entrypoint accessors arrive with the query surface (leg 2.5b).

Every other accessor keeps its name, signature and return type; a test freezes all 46.

### Added

- **JavaScript modules are in scope** (`.js/.jsx/.mjs/.cjs`), under their own `can://javascript/` id
  prefix, in the same application as the TypeScript ones.
- **Both backends answer the shared artifact, dependency and configuration accessors**, now that
  `TSAnalysisBackend` inherits the generic `AnalysisBackend`.

### Changed

- **Pin: `codeanalyzer-typescript` 0.4.3 → 1.2.0.** The backend drives the 1.2.0 CLI and every
  analysis level now reaches the analyzer; `TSCodeAnalyzerConfig.tsc_only` is a deprecated no-op.
- **`get_call_graph()` keys nodes as every other accessor does** and tags each with a `kind`
  (`module | class | interface | enum | type_alias | namespace | callable | external`). TypeScript
  keeps module callers and class callees, unlike Python — filter on `kind == "callable"` for that shape.
- **Where the graph cannot answer, the backend says so instead of returning an empty value:**
  `get_imports`, `get_all_exports`, `get_unresolved_config_reads`, `get_method_parameters` for a found
  method, and `get_extended_classes`/`get_implemented_interfaces` when the relationship type is absent.
  The remaining documented gaps, and where the two backends legitimately differ, are in
  `docs/agent-api-reference.md`.
- Internal: the language-neutral query helpers moved from `cldk/analysis/python/` to
  `cldk/analysis/commons/`; Python re-imports every name unchanged.

### Known limitations

- codeanalyzer-typescript mints one id for a value and a type of the same name under declaration
  merging (codeanalyzer-typescript#177); such a node resolves to the facet its `kind` names, or not at
  all, and never comes back described as something it is not.
- A graph emitted from an unreleased `main` build stamps `1.2.0` and cannot be told from a release
  graph.

## [v2.0.0-rc.2] - 2026-09-06
Python legs 1, 1.5 and 1.6 of the CLDK 2.0 agent-facing query facade (see
`docs/design/specs/2026-09-03-agent-facing-query-facade.md`,
`docs/design/specs/2026-09-05-leg-1.5-bounded-queries-and-dataflow.md` and
`docs/design/specs/2026-09-06-leg-1.6-id-prefix-scoping.md`). Targets `2.0.0-rc.2`.

### Changed
- **Pinned `codeanalyzer-python` 1.4.0 → 1.4.1** (`pyproject.toml` `dependencies` and
  `[tool.backend-versions]`). 1.4.1 removes the `_module` node property from every graph it emits
  (upstream #183), so the Neo4j backend no longer scopes on it: **every statement is scoped to the
  application by its `can://` id prefix** (`n.id STARTS WITH 'can://python/<app>/'`), the rule the
  analyzer's own destructive statements use, and a callable's repo-relative `path` is derived from
  its id and verified against the application's module keys rather than projected from a property.
  No accessor changes name, signature, return type or value; the `_module` scoping is simply gone.
  Ghosts (`:PyExternal`) fall inside the prefix, so every call-graph walk pins its traversal source
  to `:PyCallable` by label -- an `@external` node is still reached and never traversed through.
- **The schema probe reads the graph's analyzer generation.** Attaching to a graph whose
  `:PyApplication.analyzer_version` is below **1.4.0** (or missing) raises `GraphSchemaMismatch`
  naming the version found and the floor -- before this it was served with silent empties. A
  **1.4.0** graph and a **1.4.1** graph are served identically and silently: both carry the unique
  `:PySymbol(id)` range index, so `locate` / `locate_many` and `resolve_callable` anchor on
  `(c:PyCallable:PySymbol)` and the prefix *seeks* on either generation (40 positions: 381 → 46 ms
  on 1.4.1, 427 → 53 ms on 1.4.0; `resolve_callable` 19.2 → 15.3 ms on 1.4.1, a wash on 1.4.0).
  1.4.1's `:PyCanNode(id)` index was measured and rejected -- it spans all 955,961 application
  nodes, so seeking it made `resolve_callable` 10× slower -- and no statement names it. Statements
  whose prefix is the whole application stay on the `:PyCallable` label scan, which is faster there.
- **BREAKING: Neo4j graph vocabulary migration.** The Python Neo4j backend
  (`cldk.analysis.python.neo4j.PyNeo4jBackend`) now queries `PyBodyNode` / `PY_HAS_BODY_NODE`
  instead of the pre-1.4.0 `PyCallSite` / `PY_HAS_CALLSITE` / `PySymbol` vocabulary, matching what
  `codeanalyzer-python` >=1.4.0 actually emits.
  **Signature-level backwards compatibility does not extend to graph generation.** A graph built
  by `codeanalyzer-python` 0.3.x that used to answer every query with zero rows (silently,
  indistinguishable from "this codebase has no callables") now raises `GraphSchemaMismatch` at
  attach time instead, naming the expected/found/missing relationship types and the analyzer
  generation implied. **Migration:** re-ingest the project with `codeanalyzer-python>=1.4.0`
  (`codeanalyzer-python --emit neo4j`) before attaching this SDK version to it; there is no
  in-place graph upgrade.
- **Pinned `codeanalyzer-python` 0.3.1 → 1.4.0** (`pyproject.toml`), the source of the vocabulary
  migration above. The in-process (local) backend picks this up automatically; only the Neo4j
  backend needs a re-ingested graph.

- **Neo4j enumeration is no longer an N+1 fan-out.** `get_symbol_table()`, `get_classes()` and
  everything built on them (`get_modules()`, `get_application_view()`, `get_call_graph_json()`)
  used to rebuild each declaration with one query per child collection *per parent node* — 73,669
  round trips to describe a 1,626-module application, at ~5 ms each. Each collection is now fetched
  once for the scope being read and served from a by-parent index, so a bulk accessor pays at most
  eleven round trips however many modules, classes and callables it walks. Measured on a 970k-node
  graph (Odoo, 2,364 files): `get_classes()` 348s → 10.4s, `get_symbol_table()` 382s → 11.9s,
  `get_call_graph_json()` 422s → 26.1s. No signature, return type or result changes.

- **BREAKING (value, not signature): `PyCallableOverview.path` is now repo-relative.** It used to
  be the absolute path on whichever machine ran the analysis
  (`/Users/someone/checkout/addons/account/models/onboarding.py`); it is now the repo-relative
  module key (`addons/account/models/onboarding.py`) — the same string as `locate().module.path`,
  `PyClassOverview.path` and the keys of `get_symbol_table()`, which it previously could not be
  joined against. Both backends changed together: the Neo4j projections behind
  `get_callables_overview()`, `get_decorated_callables()`, `get_entrypoints()` and
  `get_config_readers()` now project the module key rather than `:PyCallable.path` (from the
  `_module` property on a 1.4.0 graph in leg 1; derived from the node's `can://` id since leg 1.6),
  and the local backend projects the symbol table key rather than `PyCallable.path`.
  **Migration:** a caller that stored or persisted these paths will see different strings for the
  same callable, and one that stripped a project-root prefix off them must stop.

- **BREAKING (value, not signature): `LocateResult.node_id` is now the analyzer's own body-node
  id.** It used to be composed by the SDK as `<signature>@<body key>`
  (`addons.onboarding.models.onboarding_onboarding_step.OnboardingOnboardingStep._compute_current_progress@51:34`),
  a string that named nothing in the graph; it is now the node's own id
  (`can://python/<app>/…/OnboardingOnboardingStep/_compute_current_progress(self)@51:34`) — read
  off `:PyBodyNode.id` over Neo4j, and composed from `PyCallable.id` by the emitter's own rule
  locally, so both backends produce the same string and it joins to the graph (verified over all
  885,218 body nodes). It is the same vocabulary `SliceNode.ref` uses. **Migration:** treat it as
  an opaque handle to pass back to `get_source()` / `describe()`, never as a string to parse or
  build; `get_source()` still accepts the old callable-half spelling, so a stored id keeps
  resolving. (#320)

- **One completeness protocol on every bounded result.** `EdgePage`, `Slice` and `FlowPaths` all
  expose **`complete: bool`**, and it is serialised — `model_dump()` carries it, so a result
  written out and read back by another process still says whether it was whole. It replaces two
  names for one fact: `EdgePage.has_more` and `Slice.truncated` (both unreleased) are gone, not
  aliased. All three also behave as the list they carry: `for edge in page`, `len(slice)`,
  `paths[0]`, and `if paths:` is `False` when empty — pydantic's defaults had `for x in page`
  yielding `(field, value)` tuples and `bool(page)` always `True`. `FlowPaths` is now a model
  (`paths`, `complete`) rather than a `list` subclass whose `truncated` attribute `json.dumps`
  silently dropped. `FlowPath.weakest` stays an unserialised property; `prov_rank` over the dumped
  hops recovers it. A single probe table (`tests/analysis/python/test_completeness_protocol.py`)
  runs over all three so the protocol cannot drift again.

- **The predicates and path queries are unbounded by default; only the slices bound themselves.**
  `paths_between`, `call_paths_between`, `flows_to_call` and `flows_to_argument` had inherited the
  slices' `depth=5`, and on a boolean or a path list a hop budget is not a smaller answer but a
  wrong one: `flows_to_call("kwargs", "Website.create", within="Website.configurator_apply")`
  answered `False` for a flow that exists, and the matching `paths_between` answered `[]` with
  nothing to say a bound had fired. All four now default to `depth=None`, as `reaches` already
  did, and the rule is stated once on `DEFAULT_DEPTH` and cited by all five; `depth=` remains an
  explicit narrowing. The three slices keep `depth=5`, because a bounded slice is a *complete*
  answer to a narrower question and `total` says so.

- **`paths_between` takes `src_within=` and `dst_within=`, both required.** `within=` is renamed
  and `dst_within` no longer defaults to it: two values of one callable are joined only through
  recursion, so the old default made the default call the degenerate case. `flows_to_call` /
  `flows_to_argument` keep a single `within=` deliberately — it scopes `src`, and their second
  endpoint is a callable addressed by name, with `arg` scoped by `callee` itself.

- **`in_module=` also takes the dotted module name** (`"odoo.tools.mail"`, or a dotted suffix such
  as `"tools.mail"`), alongside the repo-relative path. That is the vocabulary every signature
  reads in, and `in_class=` was already a dotted suffix. A resolution that fails because a keyword
  excluded every match now raises `SelectorNotInGraph` **naming that keyword**
  (`kind="in_module"`) instead of claiming the callable does not exist.

- **`reaches` and `backward_cone` need Neo4j 5.9+ on the Neo4j backend**, as
  `get_call_graph(roots=)` already did: their walks are now quantified path patterns constrained
  at every hop (see the parity fix below). Every other accessor still runs on any 5.x; the version
  is checked per accessor, so an older server keeps serving the rest.

#### Compatibility matrix (spec §7.1)

| SDK version | Requires `codeanalyzer-python` | Graph vocabulary |
| --- | --- | --- |
| <= 1.5.0 | 0.3.x | `PyCallSite` / `PY_HAS_CALLSITE` / `PySymbol` |
| 2.0.0-rc.2 (this) | 1.4.1 pinned; graphs emitted by >= 1.4.0 served identically | `PyBodyNode` / `PY_HAS_BODY_NODE`, scope by `can://` id prefix |

### Added
- **Slices and reachability: `slice_backward(src, within=)` / `slice_forward(src, within=)` /
  `backward_cone(sinks)` / `reaches(src, dst)` / `callers_of(name)` / `callees_of(name)`**, on both
  backends. Names in, names out — a value is addressed as `"invoice_id"` scoped by its callable,
  never as `"…@formal_in:1"`, and no `can://` URI appears in an argument or a result. The
  traversal follows data dependence, control dependence, argument passing, returns and call
  summaries at once (`PY_DDG` / `PY_CDG` / `PY_PARAM_IN` / `PY_PARAM_OUT` / `PY_SUMMARY` —
  6,089,420 edges on the measured application) and **runs in the database**: on the Neo4j backend
  each slice is one variable-length Cypher match, planned as a pruning breadth-first search, so
  the largest cone measured — 195,784 nodes — is counted and its first 10,000 nodes described in
  about 1.5 seconds without the SDK ever holding an edge. The local backend answers the same
  questions interprocedurally at `analysis_level="system_dependency_graph"`, building its index
  from `PyApplication.param_in`/`param_out` and `PyCallable.summary`, which are the same edges the
  graph is projected from. Below `program_dependency_graph` the slices raise through the guard
  `get_ddg` already uses; the call-graph accessors (`reaches`, `callers_of`, `callees_of`,
  `backward_cone`) work from `call_graph` and are not guarded, because the call graph exists there.

  **A slice is capped, not paged — and the cap reports what it dropped** (`Slice.total`,
  `Slice.truncated`, `Slice.nodes`, `Slice.roots`, `Slice.resolved`). This is deliberately the
  opposite ruling to `EdgePage` on the sibling accessors above, and the measurement is why. On the
  same application a backward slice is either about **1** node or about **195,786** (the median
  over seeds that have callers; p95 195,790, max 196,117 over 200 random seeds), and a forward
  slice reaches **440,270** at the 95th percentile — of 885,218 body nodes in the whole program.
  The distribution has no middle, so a page is not a useful unit; and a slice *is* the traversal,
  so unlike a keyset cursor over an order the database already maintains, every page would re-run
  the whole closure. `total` carries E5's "a bound is never silent" instead: the whole slice's size
  arrives with the first and only call, and `truncated` is derived from it rather than stored, so
  the two cannot disagree. When a cap fires the way forward is `depth=`, which bounds the
  *traversal* and so returns a complete answer to a narrower question. Nodes come back ordered by
  `ref` and the cap takes a prefix of that order, so the same call twice returns the same subset.
  `Slice.root` is the singular seed for the single-seed accessors and raises on a multi-sink cone
  rather than silently answering with the first.

  **`depth` defaults to 5 on `slice_backward`, `slice_forward` and `backward_cone`** — a finite
  bound, not `None`. Because the distribution has no middle, an unbounded default gave a connected
  seed 10,000 arbitrary nodes of a 195,819-node closure: honestly flagged `truncated`, and still an
  unprincipled 5% of a cone. A hop bound answers a narrower question *completely* instead, and the
  number is measured, not chosen: over 120 random connected `formal_in` seeds, the backward slice
  at depth 5 has median 33 / p75 188 / max 1,539 and the forward slice median 24 / p75 63 / max
  1,053, with **not one seed in either direction reaching `max_nodes`**; at depth 6 a forward slice
  first exceeds it (14,260), and at depth 8 three do. So a slice is now normally one where
  `truncated` is `False` and `total` is the size of `nodes`. **`depth=None` still means the whole
  closure** and is how a caller asks for the fifth of the program — every capability Task 6 shipped
  remains reachable, one keyword away. `reaches` keeps its unbounded default deliberately: a hop
  budget on a *boolean* turns "no path" and "no path within 5 hops" into the same `False`, which is
  a wrong answer rather than a small one, and the unbounded call measures 20ms mean / 112ms worst
  over 200 random pairs. The same change made a latent local-backend defect load-bearing and it is
  fixed here: `backward_cone(sinks, depth=n)` walked *forward* over the call graph
  (`nx.ego_graph` follows successors, and was not given the reversed view), so a bounded cone
  returned the sink's descendants; only the unbounded `nx.ancestors` path was correct.

  **`callees_of` includes calls out of the project** — 38,585 of the application's 370,110 call
  edges, and usually the ones a caller tracing a sink is after. An external comes back
  `kind="external"` with a readable dotted name built from the node's own `module`/`name`
  (`"odoo.exceptions.ValidationError.__init__"`, never its `can://` id) and with `file=""` /
  `line=0`, because it was never analysed and there is no position to point at. `callers_of` is
  declared callables only. Neither touches the frozen `get_all_callers` / `get_all_callees`.
- **Paths, mixed flow queries and hydration: `paths_between(src, dst, src_within=, dst_within=)` /
  `call_paths_between(src, dst)` / `flows_to_call(src, callee, within=)` /
  `flows_to_argument(src, callee, arg, within=)` / `describe(nodes)`**, on both backends. A path
  is a *sequence* where a slice is a set (E2): `paths_between` returns the **shortest** flows from
  one value to another as `FlowPath`s, each an ordered list of `PathHop`s that say what justified
  the step — `via` in the caller's words (`data` / `control` / `argument` / `return` / `summary`,
  never `PY_DDG` / `PY_PARAM_IN`), the variable it flows on, and the provenance it was established
  with — so a caller can argue a flow rather than assert one. `FlowPath.weakest` names the hop
  that caps the claim, ranked by the new `PROV_CERTAINTY` / `prov_rank`: `ssa` (exact def-use) >
  `reaching-defs` (a may-analysis over the CFG) > `points-to` (alias analysis), least certain
  first; an unlabelled structural hop claims no approximation and ranks strongest; an unrecognised
  label ranks weakest. Measured: `prov` is a singleton on every one of the 5,134,655 `PY_DDG`
  edges, so "the weakest hop" is well defined. Only shortest paths are returned — enumerating
  every walk does not terminate on a real dependence graph — and at most `max_paths` of them
  (`DEFAULT_MAX_PATHS = 10`, witnesses rather than extent; the extent question is `slice_forward`),
  in a stated order (`hop_sort_key`: length, then `(via, var, to.ref)` hop by hop) identical on
  both backends, so a cap takes a prefix rather than whichever paths arrived first. Both are
  `FlowPaths`, which says whether the cap fired. Over Neo4j the query is `allShortestPaths` — a
  bidirectional BFS that answers the pathological seeds in under 0.1s where a variable-length
  match never finished; locally it is the same shortest-walk enumeration over the SDG index the
  slices already build. A path from a node to itself is refused (`ValueError`, in the caller's
  vocabulary, advising the `reaches` call that answers the cycle question) rather than answered
  `[]`, which a node genuinely on a cycle could not be told apart from.
  `call_paths_between` is the same shape over the call graph — every hop `via="call"` with no
  variable and no provenance, because a call edge carries neither — and the evidence-carrying form
  of `reaches`. `flows_to_call` asks whether a value reaches *any* value entering a call to the
  callee; `flows_to_argument` asks whether it reaches the callee's parameter **named** `arg` (E7,
  no ordinals). They are different questions with different answers — measured, `invoice_id` of
  `PaymentPortal.invoice_transaction` reaches six of `_process_transaction`'s seven entering values
  and not `kwargs` — and `flows_to_argument ⟹ flows_to_call` holds by construction because both
  run one reachability predicate over a subset and its superset. An `arg` naming no value of the
  callee raises rather than answering `False`. `describe(nodes)` fills in `SliceNode.source` for
  the positions a caller chose to read, in **one round trip** whatever the count, and accepts
  anything carrying a ref — slice nodes, path-hop endpoints, `locate()` results (E4: references by
  default, payloads on request). Afterwards `source=None` means exactly one thing: the position
  exists and this backend has no text for it — a value vertex on either backend, a statement over
  Neo4j (the graph carries no text below callable granularity; the local backend slices it out of
  the module), or an external (never analysed, so no text anywhere). A ref that names nothing
  raises `KeyError`, reported by callable and `file:line`, never by ref.
- **Per-callable control and data flow: `get_cfg(callable)` / `get_cdg(callable)` /
  `get_ddg(callable)`**, on both backends. `DdgEdge` carries the variable that flows (`var`) and
  the evidence for it (`prov` — `ssa`, `reaching-defs` or `points-to`), so syntactic and
  alias-aware dependence are told apart without a second call. Endpoints are body-node ids in the
  same vocabulary `LocateResult.node_id` uses and `get_source()` accepts. Requires
  `analysis_level="program_dependency_graph"` or deeper: a local backend built shallower raises
  `CodeanalyzerUsageException` naming the level required and the level in use, rather than
  returning an empty result that could not be told apart from a callable with no dependence.

  **All three return an `EdgePage`, not a list** (`cldk.analysis.commons.results.EdgePage`).
  Per-callable is a *scoping* bound, not a *size* one: measured on a 970k-node graph, one callable
  (`Website.configurator_apply`) has 1,386,918 DDG edges — 27% of the whole application's
  5,134,655 — which as a list is around half a gigabyte of models in one call. The page bounds the
  response and **discards nothing**: `page.total` is the size of the whole answer, `page.edges` is
  at most `page_size` of them (default 10,000, chosen because 15,520 of the application's 15,549
  callables fall under it and so need no loop at all), and `page.next_cursor` — opaque, passed
  back as `cursor=` — reaches the rest. `page.has_more` distinguishes "this is everything" from
  "there is more" without a second call. Edges come in one canonical order, identical on both
  backends (source, target, then `kind` for CFG and `var`/`prov` for DDG), so page *n* is the same
  page whichever backend answered. Paging is by cursor rather than offset because offsets get
  slower the deeper you read: for the same query on that callable, `SKIP` costs 2.6s / 9.0s / 4.3s
  for the first / middle / last page while the cursor filter is flat at 3.1s / 2.9s / 2.4s (end to
  end through the SDK, including name resolution and the count, the cursor form measures 3.5s /
  3.6s / 3.0s, median of three runs). CFG and CDG page identically despite topping out at 402 and 314 edges — three
  sibling accessors answering in two shapes is a defect generator on a surface composed at
  runtime.
- **`locate(path, line)` / `locate_many(positions)`** — resolve a source position to its enclosing
  callable (or `module_scope` / `file_not_in_graph` when there isn't one), with the callable's
  source text attached. Backed by the new `LocateResult` model
  (`cldk.analysis.commons.results`).
- **`get_source(node_id)`** — the source text named by a callable's signature or by
  `LocateResult.node_id` (a body-node id), byte-accurate against non-ASCII source. Below-callable
  granularity is local-backend-only; the Neo4j backend raises `NotImplementedError` rather than
  substituting the enclosing callable's text.
- **Entrypoint detection surface**: `get_entrypoints()` (callable-level), `get_entrypoint_classes()`
  (class-level, new `PyClassOverview` model), and `get_entrypoint_coverage()` (the detection pass's
  own coverage/failure record via the new `EntrypointCoverage` result, so an empty result can be
  told apart from a pass that had gaps).
- **Repository-artifact / dependency / config layer**: `get_artifacts()`, `get_dependencies()`,
  `get_config_keys()`, `get_config_uses()`, `get_unresolved_config_reads()`, and
  `get_config_readers(key)`.
- **External call resolution**: `get_external_symbols()` returns every call-graph endpoint outside
  the analyzed project, keyed by its `can://…/@external/…` id. `get_callsites_for()` and the call
  graph accessors (`get_call_graph()`/`get_callers()`/`get_callees()`/`get_class_call_graph()`) now
  resolve a call landing on such a target to that id instead of leaving `callee_signature`/the
  graph node as `None` (previously 602 recorded incidents for the former, 38,585 of 364,752 in-scope
  `PY_CALLS` edges on a live graph for the latter).
- **`has_resolution_edges`** (property) — whether the current backend can resolve call-site callees
  at all right now. Always `True` on the local backend (it always attempts Jedi resolution);
  on the Neo4j backend it is probed once at connection time and is `False` only when the attached
  graph carries no `PY_RESOLVES_TO` edge anywhere, which disambiguates a genuinely unresolved call
  site from a graph with no resolution data.
- **Scoping keywords on the enumerating accessors** — `get_symbol_table(paths=…)`,
  `get_classes(module=…)` and `get_call_graph(roots=…, depth=…)`, on both backends. All are
  keyword-only and default to the whole-application behaviour they had before, so no existing call
  site changes. `paths=` / `module=` name modules by symbol-table key (absolute paths and native
  separators resolve too); `roots=` names callables by signature, or by `@external` id for an
  out-of-project target. `get_call_graph(roots=…)` returns the **induced** sub-graph over the
  callables reached — every edge among them, not only those on a path out from a root — so
  `predecessors()` cannot lie about a node in the graph it just returned; a root that calls nothing
  comes back as a graph of one node, **including one that appears in no call edge at all** — a root
  is validated against the callables the application declares (plus the graph's own nodes, which is
  what makes an `@external` id a valid root), never against edge participation, so the two backends
  judge the same domain. `depth=` bounds the walk in call hops, requires `roots=`, and must be an
  `int` >= 1; `paths=`/`roots=` take sequences and reject a bare string with `TypeError` rather than
  iterating its characters; `paths=` resolution is many-to-one and de-duplicated, so two spellings
  of one module are one entry. The Neo4j backend pushes all of it into Cypher, including the
  prefetch scope, so one module or one root costs well under a second instead of a full fetch
  filtered in Python; the bounded call graph is a quantified path pattern scoped to the application
  at every hop and therefore **requires Neo4j 5.9+** — the server version is read when the backend
  attaches and enforced by that one accessor, so an older server keeps serving all the others.
- **`SelectorNotInGraph`** (`cldk.utils.exceptions`, a `ValueError`) — a value passed to `paths=`,
  `module=` or `roots=` that names nothing in the analysed application now raises, naming the
  values that matched nothing and how many were asked for (`1 of 2 paths not in graph: 'gone.py'`),
  including when only some of them missed. Previously each contributed nothing, so a mistyped path
  and a module that genuinely declares no classes were the same `{}`, and an unknown root and a
  callable that calls nothing were the same empty graph. The message lists what missed and stops —
  no near-miss suggestions, per the leg's E8. An **empty** sequence (`paths=[]`, `roots=[]`) raises
  plain `ValueError` instead: nothing missed, but the argument that means "everything" is the
  argument omitted. A root that misses says what `roots=` takes — full signatures or `@external`
  ids, not bare names — and where name-based addressing lives, since `roots=` is an exact filter
  while every name-taking accessor suffix-matches, so a correct short name and a typo miss
  identically.
- **`AmbiguousName`** (`cldk.utils.exceptions`, a `ValueError`) — raised when a name the caller
  wrote matches more than one callable or value. Carries every match as data (`candidates`,
  sorted), shows the first few plus a total in the message, and names the keyword that would
  narrow it — only one the caller has not already used. Nothing is picked and nothing is
  suggested that did not genuinely match.
- **Name-based addressing: `resolve_callable(name, in_class=, in_module=)` and
  `resolve_value(name, within=)`** (both Python backends, and on the `PythonAnalysis` facade like
  every other accessor) — a caller names a callable or one of the
  values entering it the way it already thinks of it, and gets back a `SliceNode`; nothing in the
  surface takes, returns, or requires assembling a `can://` URI, and nothing takes an ordinal.
  Names match whole or as a dotted suffix on segment boundaries, with an exact match winning
  outright; more than one match raises `AmbiguousName` carrying every candidate rather than
  picking one, and there is no typo-tolerant matching anywhere — not in the resolver, not in the
  error path. Both backends route through one policy module (`cldk.analysis.commons.resolve`), so
  they cannot drift on what "ambiguous" means.
  A `resolve_callable` `ref` round-trips through `get_source()` on either backend. A
  `resolve_value` `ref` does **not**, on either: `parameter` / `global` / `capture` are dataflow
  vertices with no source span, so there is no text to return (the local backend raises `KeyError:
  … (no span)`, the Neo4j backend `NotImplementedError`). Local variables have no address through
  `resolve_value` at all — only values that *enter* a callable do; `locate(path, line)` addresses
  positions inside a body.
  `SliceNode` gains **`defined_in`**, the defining module of a captured global.

- **`GraphSchemaMismatch`** (`cldk.utils.exceptions`) — raised by the Neo4j backend's one-time
  schema probe at attach time; see the breaking-change note above.

### Fixed
- **`get_entrypoint_coverage()` over Neo4j reads the projected report.** It answered with an
  `entrypoint_report_unavailable` diagnostic unconditionally; codeanalyzer-python 1.4.1 (#182)
  projects `entrypoint_frameworks` / `entrypoint_report_json` onto `:PyApplication`, so on such a
  graph the answer is now the pass's own `PyEntrypointReport`, field for field what the local
  backend returns. The diagnostic survives only for a graph that genuinely lacks the property (one
  emitted by 1.4.0).
- **Resolved through the 1.4.1 pin** -- python-sdk's upstream reports #176 / #177 / #178, fixed
  in codeanalyzer-python as #180 (body nodes and parameters carry their `id` in `analysis.json`;
  the SDK's composed body-node id is now pinned equal to the analyzer's own per run), #182 / #185
  (entrypoint report projected, Odoo `@http.route` / `http.Controller` detected: 534 callables and
  94 classes on the same checkout that 1.4.0 flagged 0 / 0), #181 (`PY_EXTENDS` is actually emitted:
  1,573 edges where every 2.0.0 graph had none) and #183 (`_module` retired, `:PyCanNode` range
  index on `id`).
- **The N+1 timing assertion measured the coverage tracer, not the query.** The two timed live
  tests (`get_symbol_table` / `get_classes` under 15 s) ran under `sys.settrace`, which adds ~5 s
  to a ~10 s Python-side reconstruction; they passed the ceiling by luck. They now run with coverage
  paused (`pytest-cov`'s `no_cover`), so the ceiling measures the round trips it exists to bound.
- **Java: a missing JDK cache root is refused, not crashed on.** `JCodeanalyzer._get_codeanalyzer_exec`
  now raises `CodeanalyzerExecutionException` ("no cache directory and no project directory")
  when neither is available, instead of letting `ensure_jdk` fail with `TypeError: ... not
  'NoneType'`. Only single-file source mode reaches this path; that mode is won't-fix (#256) and
  its ten witness tests are skip-gated under #255, so the release test gate (live since #306) is
  green again (#328).
- **Java: the cached Temurin JDK is actually reused.** `ensure_jdk` looked for `<cache>/jdk/<release>/bin/java`,
  but the archive extracts nested (`<release>/bin`, or `<release>/Contents/Home/bin` on macOS), so
  the cache never hit; without a `$JAVA_HOME` carrying `jmods` every process re-downloaded the JDK
  and then failed extracting over the read-only `lib/server/*.jsa` files already there. It now
  finds the extracted JDK the same way the download does. Mocked-analyzer Java tests no longer
  resolve a JDK at all, and `JAVA_HOME` no longer leaks between tests.
- **Neo4j statements no longer leak across applications in a shared database.** The per-parent
  child fetches (`get_class()` and everything reconstructing one declaration) matched by a bare
  `{signature: $sig}` / `{file_key: $fk}` while their bulk twins were application-scoped, so in a
  database holding two applications `get_class()` merged another application's members and
  `get_all_classes()` did not. All twelve carry the same application-scope predicate now (`_module
  IN $mods` when this landed, the `can://` id prefix since leg 1.6), and so
  do the leg-1.5 call-graph statements behind `reaches`, `backward_cone`, `call_paths_between` and
  `flows_to_call`, which had matched by signature unscoped too. Statements keyed only by a
  body-node or ghost id (`slice_*`, `paths_between`, the value-reachability predicate) are scoped
  by construction — the id embeds the application — and stay as they are. A fake two-application
  driver pins the child fetches, and an audit over every class-level statement pins the rule:
  matched by signature ⇒ carries the scope; otherwise keyed by id.
- **The call-graph walks no longer route through an external ghost on the Neo4j backend.**
  `reaches`, `backward_cone` and `call_paths_between` labelled only the *endpoints* of their
  variable-length match, so an intermediate could be a `:PyExternal` — and ghosts do have outgoing
  `PY_CALLS` edges (5,307 on the measured application, 198 landing on a declared callable). For the
  two in-application `callable → ghost → callable` chains there, `reaches` answered `True` with no
  all-callable route and `call_paths_between` returned a path with an `external` interior node,
  while `get_call_graph` — built from declared-origin edges on both backends — has no such route.
  The walks are now constrained at every hop (a quantified path pattern for `reaches` /
  `backward_cone`, an inlined `all(n IN nodes(p) WHERE n:PyCallable)` for the shortest-path
  query); measured cost 0.25s against 0.03s, 0.27s against 0.08s, and unchanged respectively.
- **The local call graph has the graph backend's shape.** It kept edges originating at an external
  ghost and edges landing on a class node, both of which the Neo4j projection filters by label, so
  `reaches` / `callers_of` / `call_paths_between` / `backward_cone` — all of which read from it
  locally — could differ by backend on the same project. `backward_cone` also now orders by `ref`
  locally, as `Slice.nodes` documents and as the graph's `ORDER BY m.id` does, so a capped cone
  takes the same prefix on both backends.
- **`LocateResult.module.path` is repo-relative on the local backend.** It was the absolute path
  on the analysing machine (`/Users/…/src/pay.py`) while the Neo4j backend answered
  `addons/…/payment.py`; the `module_scope` diagnostic leaked the same string. Both now use the
  symbol-table key, the vocabulary `SliceNode.file` and `PyCallableOverview.path` already share.
- **`describe()` composes with `callees_of()`.** `callees_of` deliberately returns externals and
  `describe` accepts anything with a ref, but the five externals of a typical callee list raised
  `KeyError: 5 of 7 refs name nothing` and printed their `can://` ids. An external is found and has
  no source by definition, so it comes back `source=None`; the `KeyError` remains for a ref that
  genuinely names nothing and now reports positions by callable and `file:line`.
- **No `can://` id or ordinal in an error message.** The self-question refusal on `paths_between`
  rendered both endpoints as `…@formal_in:0` and advised a `reaches(...)` call with those ids as
  arguments, which does not run; it now names the value within its callable and advises the
  `reaches` call on the enclosing callable, which does.
- **Argument validation precedes name resolution on both backends.** `page_size=0` plus a bad name
  raised `ValueError` over Neo4j and `SelectorNotInGraph` locally; the cheap argument check now
  comes first everywhere, before any round trip.
- **`analysis_level=` now reaches the analyzer.** `PythonAnalysis` / `PyCodeanalyzer` accepted the
  parameter, stored it, and then built `codeanalyzer-python`'s `AnalysisOptions` without it, so
  every in-process analysis ran at the analyzer's own default (level 1) whatever the caller asked
  for: `cfg` / `cdg` / `ddg` were always empty and no `formal_in` vertex ever existed, which is
  also exactly what a correct level-1 run looks like. The four `AnalysisLevel` names now map to the
  analyzer's levels 1–4, so `analysis_level="program_dependency_graph"` produces intraprocedural
  dataflow and `"system_dependency_graph"` produces the interprocedural vertices `resolve_value`
  addresses. `AnalysisLevel` member-name spellings (`"call_graph"`) are accepted alongside the
  enum's values (`"call graph"`); an unrecognised name now raises instead of silently becoming
  level 1. `call_graph` is also built at every level at or above `"call_graph"`, not only exactly
  at it. **Deeper levels cost more analysis time** — callers who relied on the parameter being
  inert will now pay for the level they asked for.
- **Body-node ids no longer double the `@`.** The local backend composed a body node's id as
  `f"{callable.id}@{body_key}"`, but the analyzer's synthetic body keys already carry a leading
  `@` (`"@formal_in:1"`, `"@entry"`), so `resolve_value` produced
  `…charge(self,invoice_id)@@formal_in:1` — a string naming nothing in the graph and nothing in
  `PyCallable.body`. Composition now follows the emitter's own rule (`_global_ordinal`), and
  `get_source()` accepts both spellings of a body key when splitting one back apart.
- **A captured global is no longer reported as a parameter.** 84% of the `formal_in` vertices on a
  real application are module globals the callable reads, and both backends labelled them
  `kind="parameter"` with the analyzer's internal `"<global>:payment::AccessError"` in
  `SliceNode.name`. They now come back `kind="global"` (or `"capture"` for a closure capture) with
  the readable identifier in `name` and the defining module in `defined_in`, and are addressable
  as `"AccessError"` or, where several modules supply that name to one callable, as
  `"payment.AccessError"`.
- **Ambiguity messages name only keywords the caller can actually use.** `resolve_callable` no
  longer advises narrowing with `in_class=` to someone who already passed `in_class=`, and an
  ambiguous `within=` inside `resolve_value` — which accepts neither `in_class=` nor `in_module=` —
  now advises naming more of the dotted path in `within=`, which resolves.
- **A signature carried by two analysed callables raises instead of resolving arbitrarily.** Both
  backends keyed their candidate map by signature, silently collapsing a collision. Not reachable
  on a real application (15,549 distinct signatures), and a duplicate elsewhere does not break
  unrelated resolutions — the check is on the answer.
- **`resolve_callable` no longer hands back a sentinel as an address.** `PyCallable.id` defaults to
  `""` and `start_line` to `-1`; the local backend copied both into `SliceNode`, yielding `ref=""`
  and `line=-1`. It now raises.

### Removed
- **C analysis support** (`CLDK.c()`, `cldk.analysis.c`, `cldk.models.c`) — libclang-backed and
  syntactic-only, it emitted none of the 2.0 code-property graph the query surface is built on and
  had no analyzer to grow one. `clang`, `libclang`, `tree-sitter-c`, and `tree-sitter-go` are
  dropped as dependencies along with it. `CLDK.java()`, `CLDK.python()`, and `CLDK.typescript()`
  are unaffected.
- **BREAKING: fifteen `PythonAnalysis` accessors that only raised `NotImplementedError`** —
  `get_class_hierarchy`, `get_service_entry_point_classes`, `get_service_entry_point_methods`,
  `get_entry_point_classes`, `get_entry_point_methods`, `get_implemented_interfaces`,
  `get_methods_with_decorators`, `get_test_methods`, `get_calling_lines`, `get_call_targets`,
  `get_all_crud_operations`, `get_all_create_operations`, `get_all_read_operations`,
  `get_all_update_operations`, `get_all_delete_operations`. Every one raised unconditionally, so no
  caller could have depended on behaviour — only on the name existing. This also removes the
  `get_entrypoint_classes` (works) / `get_entry_point_classes` (raised) name collision one space
  apart; `See Also` cross-links pointing at the deleted half are removed too. `CLDK.java()` and
  `CLDK.typescript()` keep their same-named accessors, which do work.

## [v1.5.0] - 2026-07-27

### Added
- **TypeScript bulk/projected accessors** — parity with the Python surface from #180/#181, on
  both backends (in-process and Neo4j), verified by a live dual-backend parity suite (#298, #302):
  - `get_callables_overview() -> List[TSCallableOverview]` — a lightweight projection of every
    callable (methods, functions, arrows, accessors, namespace and nested functions) with
    TS-native facets: the analyzer's 7-value `kind`, `is_exported` / `is_async` / `is_static`,
    `accessibility`, decorator names, and an `owner_signature`/`owner_kind` pair
    (`"class" | "interface"`; namespace-owned, nested, and module-level callables are ownerless —
    the dotted signature carries the namespace path). The overview deliberately excludes source
    text; drill in via `get_method_bodies`.
  - `get_method_bodies(signatures) -> Dict[str, str]` — source bodies keyed by signature;
    unknown signatures and code-less callables (implicit constructors) are omitted, so every
    returned value is a real `str`.
  - `get_decorated_callables(markers) -> List[TSCallableOverview]` — overviews of callables
    decorated with any of the given marker names.
  - `get_callsites_for(signatures) -> Dict[str, List[TSCallsite]]` — call sites per callable;
    every existing signature gets an entry (empty list when it has no call sites).
- The blessed TypeScript test fixture (`tests/resources/typescript/analysis_json/slim/analysis.json`)
  is now tracked, so the test suite runs from a clean clone.

### Known limitations
- Getter/setter pairs share one signature in the analyzer's output; the two backends currently
  collapse such pairs differently (#300 — documented on `get_callables_overview`).

## [v1.4.4] - 2026-07-22

### Fixed
- **Published wheels bundle the `codeanalyzer-java` JAR again.** Every hatchling-built wheel/sdist
  since v1.2.0 shipped without the JAR, so `pip install cldk` + `CLDK.java(...)` raised
  `CodeanalyzerExecutionException: codeanalyzer jar not found`. The root `.gitignore` `*.jar` rule
  was applied at build time while the nested `!codeanalyzer-*.jar` negation (which keeps the JAR in
  git) was not honored, silently dropping the JAR from any build run inside a git repo — i.e. CI. A
  `[tool.hatch.build] artifacts` rule force-includes it, and the release workflow now fails if a
  built artifact is missing the JAR. (#284)

## [v1.4.3] - 2026-07-14

### Fixed
- **`pip install cldk` no longer fails on Python 3.14.** Removed the unused `pyarrow==20.0.0`
  dependency (the orphaned companion of the previously removed pandas): nothing in the SDK
  imports it, and its cp39–cp313-only wheels forced an Arrow C++ source build — and usually a
  failure — on Python 3.14 installs. (#145)
- **TypeScript Neo4j backend: `get_external_symbols()` works again.** Reconstructing External
  (phantom) nodes raised a Pydantic `extra_forbidden` error for every node, because the
  reconstructor forwarded graph properties (`signature`, and a fabricated `kind`) that the slim
  `TSExternalSymbol` model deliberately omits. The reconstructor now conforms to the model and
  the analyzer's published Neo4j schema; the node's `signature` remains the map key. (#231)

## [v1.4.2] - 2026-07-14

### Fixed
- **Intra-class method calls resolve to the method again.** Upgraded `codeanalyzer-python`
  0.3.0 → 0.3.1, which fixes callee resolution for `self._method(...)` calls: the call graph
  previously pointed such edges at the *class* node (truncating call chains at their deepest
  hop) and could omit receiver-method edges entirely. Call-site `callee_signature` now names
  the method, and `PyCallsite` gains an additive `arguments` field.
  (codellm-devkit/codeanalyzer-python#94, #260)

## [v1.4.1] - 2026-07-14

### Fixed
- **Module-level functions are now resolvable through `get_method` (Python and TypeScript, both
  backends).** Previously `get_method` searched class scope only, so module-level functions were
  unreachable and `get_all_callers`/`get_all_callees` returned a silent empty result for them even
  when the call graph contained the edges. The scope argument now accepts a module name as well as
  a qualified class name; callers/callees, `get_method_parameters`, and comment lookups inherit the
  fix. (#250, #251)
- **Java lookups no longer crash on a miss.** `get_method`/`get_class`/`get_java_file` are honestly
  annotated `... | None` (ABC, both backends, and the public facade), `get_method_parameters`
  returns an empty list instead of raising `AttributeError` for an unknown class or signature, and
  all internal call-graph/comment lookups are guarded. No behavior change on successful lookups.
  (#252)

## [v1.4.0] - 2026-06-27

### Changed
- **Upgraded `codeanalyzer-python` 0.2.0 → 0.3.0**, which drops CodeQL and uses **PyCG** for
  call-graph construction.

### Removed
- **`use_codeql` (BREAKING).** Because codeanalyzer-python 0.3.0 removed CodeQL, the `use_codeql`
  knob no longer maps to anything and is removed from CLDK's public surface: the
  `PyCodeAnalyzerConfig.use_codeql` field, the deprecated `CLDK(language).analysis(use_codeql=...)`
  parameter, and the `PyCodeanalyzer(use_codeql=...)` argument. The `CodeQLDatabaseBuildException`
  and `CodeQLQueryExecutionException` exception classes are removed as well. Call-graph results may
  differ (PyCG vs CodeQL-augmented Jedi). See #185.

## [v1.3.0] - 2026-06-27

### Added
- **Bulk, field-projected accessors on the Python facade** (`PythonAnalysis`) for enumerating an
  application set-at-a-time, instead of paying the per-entity reconstruction `get_methods()` does
  (tens of thousands of round-trips on the Neo4j backend for a large app): `get_callables_overview()`
  (returns the new `PyCallableOverview` projection), `get_method_bodies(signatures)`,
  `get_decorated_callables(markers)`, and `get_callsites_for(signatures)`. On the read-only Neo4j
  backend each is a single projected Cypher query; in-process each is one symbol-table walk. New
  `PyCallableOverview` model in `cldk.models.python`.
- **`tsc_only` toggle for the TypeScript backend.** New `TSCodeAnalyzerConfig` backend config exposes
  `tsc_only`, which passes `--tsc-only` (codeanalyzer-typescript >= 0.4.2) to pin the call graph to
  the resolver path, replacing reliance on the obsolete `--call-graph-provider both`.
- **Synthesized anonymous callables for TypeScript.** New `TSSynthesizedCallable` model and
  `get_synthesized_callables()` on the TypeScript analysis surface expose the Jelly-resolved
  anonymous-callback endpoints the symbol table never names, so anonymous call-graph edges no longer
  dangle. Empty under the `tsc`-only resolver.
- README **Cited By** section highlighting papers that cite CLDK (SAINT, ASTER, RECON, PRAXIS,
  Phaedrus, and others), compiled from Semantic Scholar / OpenAlex citation data.

### Changed
- The read-only Neo4j Python backend now reuses a single read session across queries instead of
  opening one per Cypher statement, cutting per-call overhead on the reconstruction path.
- Bumped `codeanalyzer-typescript` 0.4.0 → 0.4.3.

## [v1.2.0] - 2026-06-22

### Added
- **Per-language factory methods on `CLDK`** — `CLDK.java()`, `CLDK.python()`, `CLDK.typescript()`,
  and `CLDK.c()` — each with an honest signature exposing only the options that apply to that
  language. These are the preferred entry points, replacing the stringly-typed
  `CLDK(language).analysis(...)`.
- **Typed backend-configuration objects** in `cldk.analysis.commons.backend_config`. The backend is
  now selected by the *type* of the `backend=` config passed to a factory: `CodeAnalyzerConfig`
  (default; in-process analyzer) / `PyCodeAnalyzerConfig` (adds `use_codeql`, `use_ray`), or
  `Neo4jConnectionConfig` (read-only Neo4j). `Neo4jConnectionConfig` is hoisted here and re-exported
  from `cldk.analysis.{python,typescript}.neo4j` for backward compatibility.
- **Unified, language-keyed cache directory.** All backends now share a single `cache_dir`
  (default `<project>/.codeanalyzer`) and write their artifacts under a per-language subdirectory
  (`<cache_dir>/java`, `<cache_dir>/python`, `<cache_dir>/typescript`), so a polyglot project
  analyzed under more than one language no longer overwrites a shared `analysis.json`.

### Changed
- **Caching is on by default for Java/TypeScript.** The in-process backend now caches `analysis.json`
  to disk (under the language-keyed `cache_dir`) instead of streaming over a stdout pipe.
- `CLDK(language).analysis(...)` is **deprecated** and retained as a thin compatibility shim that
  forwards to the new factory methods (emits a `DeprecationWarning`).

### Deprecated
- Java `source_code` (single-file) input — pass `project_path` instead.

### Removed
- `analysis_backend_path` from the public interface. The backend binary ships with the packaged
  `codeanalyzer-*` dependency; for TypeScript, `$CODEANALYZER_TS_BIN` remains as the only
  out-of-band override.
- `analysis_json_path` from the public interface — folded into the unified `cache_dir`.

### Migration
- The language-keyed cache relocates `analysis.json` from `<cache_dir>/analysis.json` to
  `<cache_dir>/<language>/analysis.json`; existing caches are not found at the new path, so the
  first run after upgrading recomputes the analysis.

### Added (Neo4j)
- Read-only Neo4j-backed TypeScript analysis backend (`cldk.analysis.typescript.neo4j.TSNeo4jBackend`).
  It is a drop-in alternative to the in-memory `TSCodeanalyzer`: it answers the **same** `get_*`
  query surface (call graph, callers/callees, class hierarchy, call sites, decorators, symbol
  lookups, ...) by running **Cypher over a live Neo4j graph** instead of walking the pydantic /
  NetworkX structures. The graph is the one `codeanalyzer-typescript` emits with `--emit neo4j`
  (schema `schema.neo4j.json`); it is always populated out of band, and the SDK only polls it
  (read-only — never writes, needs no binary or project sources).
- `TypeScriptAnalysis` / `CLDK.analysis(language="typescript")` now accept an optional
  `neo4j_config` (`Neo4jConnectionConfig`) to select the Neo4j backend; without it the in-memory
  backend is used, unchanged.
- Read-only Neo4j-backed **Python** analysis backend (`cldk.analysis.python.neo4j.PyNeo4jBackend`),
  the analog of the TypeScript one. It answers all 21 `PythonAnalysisBackend` queries via Cypher
  over the graph `codeanalyzer-python` (>= 0.2.0) emits with `--emit neo4j`. Verified against a real
  57-module project: every node/edge **present in the graph** reconstructs identically to the
  in-memory `PyCodeanalyzer` (3169/3200 checks; zero weight/provenance mismatches on shared call
  edges). Known gaps are not in the query layer: projection-lossy fields (comments → docstring,
  `PyVariableDeclaration.value`/columns, per-binding import detail), and an **upstream emitter bug**
  where calls to a bare module name that is also imported (e.g. `os`/`re`/`json`) are dropped from
  the emitted call graph. `PythonAnalysis` / `CLDK.analysis(language="python")` accept the same
  optional `neo4j_config`.
- Read-only Neo4j-backed **Java** analysis backend (`cldk.analysis.java.neo4j.JNeo4jBackend`),
  completing Neo4j parity across all three languages. It reconstructs the canonical `JApplication`
  from the graph `codeanalyzer-java` (>= 2.4.0) emits with `--emit neo4j` and answers all 36
  `JavaAnalysisBackend` queries with the in-memory backend's logic. Verified against the daytrader8
  sample (145 classes): everything the graph actually contains reconstructs identically to
  `JCodeanalyzer` (97% of checks). Three projection gaps in the `codeanalyzer-java` 2.4.0 emitter
  (fields collapsing to one node, imports reduced to packages, a truncated call graph) are **fixed
  in 2.4.1** (codeanalyzer-java#156/#157/#158, verified on daytrader — `J_CALLS` went 287 → 1702),
  the version the SDK release now bundles. `JavaAnalysis` / `CLDK.java(...)` accept a
  `Neo4jConnectionConfig` as the `backend=` config to select it.
- Bumped `codeanalyzer-python` to `0.2.0` (adds the Neo4j graph emitter); the bundled
  `codeanalyzer-java` jar is now `2.4.1` (adds the Neo4j graph emitter + the field/import/call-graph
  projection fixes). The Java analyzer jar is no longer a pip dependency — the SDK release workflow
  downloads the latest `codeanalyzer-java` jar into the bundled `jar/` directory.
- Optional `neo4j` extra (`pip install cldk[neo4j]`) for the Neo4j Python driver.

### Fixed
- **Bundled JDK download for the Java backend.** `ensure_jdk` resolved the Temurin JVM via the
  Adoptium `/assets/version/{release}` endpoint, which now returns 404 for pinned releases (e.g.
  `jdk-21.0.5+11`) — so the first Java analysis on a clean machine failed before it started. It now
  resolves via the `/binary/version/...` endpoint (following the redirect to the GitHub asset) and
  reads the checksum from the asset's `.sha256.txt`.

## [v1.0.7] - 2026-02-14

### Added
- Doctest-style Examples across the public API surface of JavaAnalysis, PythonAnalysis, CAnalysis, and core CLDK helpers. Coverage includes Java CRUD operations and comment/docstring query APIs, plus concise inline examples for Python and C where applicable.
- Examples documenting expected NotImplementedError behavior for placeholder APIs (PythonAnalysis and CAnalysis) using doctest flags.

### Changed
- Converted and standardized docstrings to strict Google style (Args, Returns, Raises, Examples) across edited modules.
- Standardized Examples to use the CLDK facade (e.g., `CLDK(language="java").analysis(...)`) instead of raw constructor calls.
- Normalized all doctest Example inputs to single-line strings to ensure reliable mkdocstrings rendering.
- Clarified `CLDK.analysis` return type with a precise union: `JavaAnalysis | PythonAnalysis | CAnalysis`.
- Updated codeanalyzer version to v2.3.6.

### Fixed
- Fixed README.md logo display on PyPI by updating image URLs to use raw GitHub URLs and maintaining theme-based auto-switching with proper fallback
- mkdocstrings rendering issues caused by multi-line doctest strings and formatting inconsistencies.
- Replaced confusing examples like `JavaAnalysis(None, None, ...)` with clear CLDK-based initialization patterns.
- Packaging: ensured the built wheel includes the `cldk` package by adding `packages = [{ include = "cldk" }]` to Poetry configuration.
- Fixed #141

### Removed
- Multi-line doctest strings in Examples that broke mkdocstrings rendering; all examples are now single-line.
- Removed pandas dependency (#145)

## [v1.0.6] - 2025-07-23

### Added
- Added `argument_expr` field to JCallSite model for capturing actual parameter expressions in method calls
- Added Star History section to README.md for tracking project popularity

### Changed
- Updated codeanalyzer jar to version 2.3.5 with support for call argument expressions and fully qualified parameter types
- Modified codeanalyzer.py to preserve fully qualified parameter types in method signatures instead of simplifying them
- Updated method signature format to use fully qualified type names (e.g., `java.lang.String` instead of `String`)
- Updated test fixtures with new analysis.json data reflecting the signature format changes

### Fixed
- Fixed method signature handling to maintain fully qualified parameter types for better type resolution
- Updated test cases to use fully qualified method signatures for improved accuracy

## [v1.0.5] - 2025-06-24

### Fixed
- Fixed issue #135
- Analysis level compatibility checking for analysis.json with passed analysis level

### Changed
- Updated treesitter analysis to use global declarations of parser and language

## [v1.0.4] - 2025-06-11

### Added
- Added missing callable fields field validator

### Changed
- Updated test fixture setup to use codeanalyzer jar from cldk/analysis/java/codeanalyzer/jar instead of test resources directory
- Updated analysis.json fixtures (daytrader8 and plantsbywebsphere)

### Removed
- Removed dangling codeanalyzer jars from test resources
- Removed obsolete analysis.json fixture

## [v1.0.3] - 2025-06-01

### Added
- Added code start line attribute to JCallable (corresponding to added attribute in the java code analyzer model)

## [v1.0.2] - 2025-05-24

### Added
- Added test case and fixture for source analysis
- Added missing attributes in compilation unit model

### Fixed
- Fixed handling of `source_code` option in Java codeanalyzer
- Updated core.py to match python analysis signature

## [v1.0.1] - 2025-05-07

### Changed
- Updated treesitter analysis to use global declarations of parser and language

## [v1.0.0] - 2025-04-29

### Added
- First stable release
- Updated contributing guidelines

### Changed
- Updated README.md
- Updated codeanalyzer jar
- Updated java version in release automation

## [v0.5.1] - 2025-03-13

### Changed
- Updated Java model to comply with codeanalyzer v2.3.1
- Updated codeanalyzer jar to the latest from codeanalyzer-java
- Updated get_all_docstrings to return dict

## [v0.5.0] - 2025-02-21

### Added
- Added release automation github actions
- Added Java 11 support in github actions
- Added release_config.json
- Added Comment parsing APIs at file, class, method, and docstring level
- Added support for parsing callable parameters and their location information
- Added Dev container instructions with Python, Java, C, and Rust support
- Added C/C++ analysis support
- Added CRUD operations support for Java JPA applications

### Changed
- Consolidated analysis_level enums in __init__.py
- Updated codeanalyzer jar to the latest version
- Changed coverage minimum to 70%
- Updated documentation with mkdocs
- Updated badges and logos in README
- Added Discord community support

### Removed
- Removed CodeQL dependency and refactored treesitter
- Removed ABCs from analysis
- Removed logic to find LLVM in linux OSes (only appears in Darwin)
- Removed redundant is_entry_point fields from JCallable and JType
- Removed unused parameters and code cleanup

### Fixed
- Fixed various test cases and compatibility issues
- Fixed treesitter superclass identification issues
- Fixed entry point detection code
- Fixed recursive error issues

## [v0.4.0] - 2024-11-13

### Fixed
- Fixed issue 67 - symbol table is none

### Changed
- Updated poetry build rules to include codeanalyzer-*.jar
- Added test case to verify jar file exists

## [v0.3.0] - 2024-11-12

### Added
- Support for reading slim JSON from codeanalyzer v1.1.0
- Added more test tools (pylint, flake8, black, pspec, coverage)
- Added test coverage reporting

### Changed
- Updated README.md to include the arXiv paper
- Removed obsolete test cases for unsupported languages

## [v0.2.0] - 2024-10-11

### Added
- Added GitHub Action to publish manual releases
- Added PyPi badge to README.md

## [v0.1.4] - 2024-10-21

### Fixed
- Fixed codeanalyzer.jar not being a PosixPath

## [v0.1.3] - 2024-10-21

### Fixed
- Fixed calling the correct codeanalyzer jar on version 0.1.3
- Removed auto-download of codeanalyzer jar

## [v0.1.2] - 2024-10-17

### Fixed
- Fixed tree-sitter bug
- Defined self.captures explicitly

## [0.1.0-dev] - 2024-10-07

### Added
- Initial development version
- Set version to über json support
- Support for slim JSONs from codeanalyzer
- IBM Copyright added to all source files
- Added code parsing support
- Added support for symbol table call graph
- Added notebook examples for code summarization and test generation
- Basic CLDK framework implementation

### Changed
- Updated dependencies in pyproject.toml
- Added metadata for PyPi distribution
- Updated README with installation instructions

### Fixed
- Fixed caller method implementation
- Fixed incremental analysis support
- Fixed download jar issues

---

## Release Links

- [v1.0.5]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.5
- [v1.0.4]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.4
- [v1.0.3]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.3
- [v1.0.2]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.2
- [v1.0.1]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.1
- [v1.0.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v1.0.0
- [v0.5.1]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.5.1
- [v0.5.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.5.0
- [v0.4.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.4.0
- [v0.3.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.3.0
- [v0.2.0]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.2.0
- [v0.1.4]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.4
- [v0.1.3]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.3
- [v0.1.2]: https://github.com/codellm-devkit/python-sdk/releases/tag/v0.1.2
- [0.1.0-dev]: https://github.com/codellm-devkit/python-sdk/releases/tag/0.1.0-dev
