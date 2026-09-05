# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Python leg (leg 1) of the CLDK 2.0 agent-facing query facade (see
`docs/design/specs/2026-09-03-agent-facing-query-facade.md`). Targets `2.0.0-rc.1`.

### Changed
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
  `get_config_readers()` now read `:PyCallable._module` rather than `:PyCallable.path`, and the
  local backend projects the symbol table key rather than `PyCallable.path`.
  **Migration:** a caller that stored or persisted these paths will see different strings for the
  same callable, and one that stripped a project-root prefix off them must stop.

#### Compatibility matrix (spec §7.1)

| SDK version | Requires `codeanalyzer-python` | Graph vocabulary |
| --- | --- | --- |
| <= 1.5.0 | 0.3.x | `PyCallSite` / `PY_HAS_CALLSITE` / `PySymbol` |
| 2.0.0-rc.1 (this) | >= 1.4.0 | `PyBodyNode` / `PY_HAS_BODY_NODE` |

### Added
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
  argument omitted.
- **`GraphSchemaMismatch`** (`cldk.utils.exceptions`) — raised by the Neo4j backend's one-time
  schema probe at attach time; see the breaking-change note above.

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
