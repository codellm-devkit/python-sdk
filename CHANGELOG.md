# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
