# CLAUDE.md

Guidance for AI assistants working in this repository. The goal is **strict adherence to the
existing structure and conventions** — match what is here rather than introducing new patterns.

## What this is

**Codellm-Devkit (CLDK)** is the official Python SDK for [codellm-devkit](https://codellm-devkit.info):
a unified, multilingual program-analysis SDK for Code LLMs. It turns raw source code into
structured, LLM-ready program facts (symbol tables, call graphs, type hierarchies) behind a single
Python API, normalizing the output of mature analysis engines (WALA, Tree-sitter, Jedi, CodeQL,
ts-morph) into typed [Pydantic](https://docs.pydantic.dev/) models.

- Package name: `cldk` (PyPI). Current version: see `pyproject.toml` (`[project] version`).
- Python: **>=3.11**. License: Apache-2.0. Developed at IBM Research.
- Tooling: **`uv`** for env/deps, **`pytest`** for tests, **`black`/`flake8`/`pylint`** for style,
  **`hatchling`** for builds.

## The one mental model to hold

CLDK selects analysis behavior along **two orthogonal axes**:

1. **Language** — chosen by *which factory method* you call: `CLDK.java()`, `CLDK.python()`,
   `CLDK.typescript()`, `CLDK.c()`.
2. **Backend** — chosen by the *type* of the config object passed as `backend=`:
   - `CodeAnalyzerConfig` (default) → in-process `codeanalyzer-*` binary.
   - `PyCodeAnalyzerConfig` (Python only) → adds `use_codeql`, `use_ray`.
   - `Neo4jConnectionConfig` → read-only Neo4j/Cypher backend over a graph populated **out of band**.

The user only ever touches the high-level facade (`get_symbol_table()`, `get_method_body()`,
`get_call_graph()`, …). All tool orchestration, parsing, and marshalling is hidden behind it. When
adding features, preserve this: **users configure via factory + typed config; everything else is
internal.**

## Repository layout

```
python-sdk/
├── cldk/                          # The SDK package (the only thing shipped in the wheel)
│   ├── __init__.py                # Public surface: exports `CLDK` only
│   ├── core.py                    # `CLDK` factory class — THE top-level entry point
│   ├── models/                    # Typed Pydantic schemas, one subpackage per language
│   │   ├── java/                  #   models.py (JType, JCallable, JField, …), enums.py
│   │   ├── python/                #   PyApplication, PyClass, PyCallable, PyModule, …
│   │   ├── typescript/            #   models.py
│   │   ├── c/                     #   models.py (CApplication, …)
│   │   └── treesitter/            #   tree-sitter node models
│   ├── analysis/                  # Analysis backends + facades, one subpackage per language
│   │   ├── __init__.py            #   `AnalysisLevel` enum (symbol_table / call_graph / …)
│   │   ├── commons/               #   Cross-language shared pieces:
│   │   │   ├── backend_config.py  #     Backend config dataclasses + `cache_subdir()` + unions
│   │   │   ├── treesitter/        #     TreesitterJava / TreesitterPython parsers + utils
│   │   │   └── lsp/               #     LSP helpers
│   │   ├── java/                  #   JavaAnalysis facade + backend.py (ABC) + codeanalyzer/ + neo4j/
│   │   ├── python/                #   PythonAnalysis facade + backend.py + codeanalyzer/ + neo4j/
│   │   ├── typescript/            #   TypeScriptAnalysis facade + backend.py + codeanalyzer/ + neo4j/
│   │   └── c/                     #   CAnalysis facade + clang/ (libclang backend)
│   └── utils/                     # exceptions/, logging.py, sanitization/ (java tree-sitter sanitizer)
├── tests/                         # Mirrors cldk/ layout: tests/analysis/<lang>/, tests/models/<lang>/
│   ├── conftest.py                # Session fixtures; reads sample-app paths from pyproject [tool.cldk.testing]
│   └── resources/                 # Sample apps (java/python/ts/c) + analysis_json fixtures
├── scripts/                       # Standalone dev scripts (e.g. smoke_test_python_analysis.py)
├── docs/                          # Images; full docs live at codellm-devkit.info
├── .github/workflows/             # release.yml (tag-triggered PyPI publish)
├── .devcontainer/                 # Dev container (uv + black-on-save, google docstrings)
├── pyproject.toml                 # Deps, tool config, [tool.backend-versions], [tool.cldk.testing]
├── Makefile                       # make venv / install / lint / test / build / refresh
├── uv.lock                        # Locked deps (committed; relock on dependency changes)
├── CHANGELOG.md                   # Keep a Changelog format; SemVer
└── README.md / CONTRIBUTING.md / LICENSE
```

### The per-language module pattern (follow it exactly)

Every language subpackage under `cldk/analysis/<lang>/` follows the same shape. When touching one
language, check the others for the established pattern before inventing anything:

- `<lang>_analysis.py` — the **facade** class (`JavaAnalysis`, `PythonAnalysis`,
  `TypeScriptAnalysis`, `CAnalysis`). Thin: it dispatches on backend config type and delegates
  analysis queries to `self.backend`.
- `backend.py` — an **ABC** (`JavaAnalysisBackend`, …) formalizing the query surface the facade
  depends on. Both the in-process and Neo4j backends subclass it, so they are drop-in interchangeable.
- `codeanalyzer/` — the in-process backend wrapping the packaged `codeanalyzer-*` engine.
- `neo4j/` — the read-only Cypher backend (`config.py`, `reconstruct.py`, `neo4j_backend.py`) that
  rebuilds the **same** models from a graph.

Each language has a matching `cldk/models/<lang>/` of Pydantic schemas the backend maps results onto.

## Backends and the codeanalyzer engines

| Language | Engine (pinned in `[tool.backend-versions]`) | Notes |
| --- | --- | --- |
| Java | `codeanalyzer-java` — **bundled JAR** under `cldk/analysis/java/codeanalyzer/jar/` | WALA + JavaParser. JAR is git-ignored except the tracked pinned version; injected at build/refresh time via `make build` / `make refresh`. A Temurin JDK is auto-downloaded+cached on first run. |
| Python | `codeanalyzer-python` (pip dep) | Jedi + optional CodeQL. Caching owned by the engine under `cache_dir` (default `<project>/.codeanalyzer`); first run is slow (provisions CodeQL DB). |
| TypeScript | `codeanalyzer-typescript` (pip dep) | ts-morph + Jelly call graphs. |
| C | `libclang` (system) | Basic parsing via `clang/clang_analyzer.py`. No Neo4j backend. |

Cache artifacts live under a **language-keyed subdirectory** (`<cache_dir>/java`,
`<cache_dir>/python`, …) via `cache_subdir()` so a polyglot repo doesn't overwrite a shared
`analysis.json`. The default cache root is `<project>/.codeanalyzer` — keep it `.gitignore`d.

When bumping an engine, update **both** the dependency (`[project] dependencies` or the bundled JAR)
**and** `[tool.backend-versions]` in `pyproject.toml`.

## Development workflow

Use the Makefile targets — don't reinvent the commands:

```bash
make venv      # uv venv
make install   # uv sync --all-groups
make lint      # flake8 (E9/F-codes + complexity) then pylint, max-line-length=180
make test      # uv run pytest --pspec --cov=cldk --cov-fail-under=33 --disable-warnings
make build     # injects latest codeanalyzer JAR, then uv build
make refresh   # refresh the bundled Java codeanalyzer JAR only
```

Run a single test: `uv run pytest tests/analysis/python/test_python_analysis.py -k name`.

- Activate the env via `.envrc` (`source .venv/bin/activate`) or `uv run`.
- `pytest` config is in `pyproject.toml` (`[tool.pytest.ini_options]`): `--pspec` spec-style output,
  coverage on `cldk`, `--cov-fail-under=50` (the Makefile relaxes this to 33). `testpaths = tests`.

## Conventions to adhere to

- **Style:** `black` and `flake8`/`pylint` with **`max-line-length = 180`** (set in `pyproject.toml`
  and `setup.cfg`). Run `make lint` before pushing. `__init__.py` files are exempt from F401/E402.
- **Docstrings:** Google style (the devcontainer enforces `autoDocstring.docstringFormat = google`).
  Modules, public classes, and methods carry rich docstrings with `Args`/`Returns`/`Raises`/`See Also`.
  Match that density; cross-reference with Sphinx `:class:` / `:meth:` roles as existing code does.
- **License header:** every source file starts with the Apache 2.0 IBM copyright header block. Copy
  it verbatim into new files.
- **Typing:** modern syntax (`str | Path | None`), `from __future__ import annotations` where used.
  Models are Pydantic v2.
- **Public API discipline:** `cldk/__init__.py` exports only `CLDK`. Factory methods in `core.py`
  expose **only options that apply to that language** (honest signatures). The legacy
  `CLDK(language).analysis(...)` is a deprecated compat shim emitting `DeprecationWarning` — keep it
  working but don't extend it; route new work through the factories + typed configs.
- **Backend contract:** if you add a method the facade delegates via `self.backend.X`, add it to the
  language's `backend.py` ABC. A contract test (`test_*_backend_contract.py`) asserts every delegated
  method exists on the ABC and that all backends fully implement it — it will fail otherwise.
- **Exceptions:** raise the typed exceptions in `cldk/utils/exceptions/` (e.g.
  `CldkInitializationException`) rather than bare built-ins.

## Programming design patterns (enforced)

These are the **design principles I enforce** on this codebase, adapted from a strong-typing,
fail-early discipline. The guiding philosophy: **make invalid states unrepresentable, push errors
to the earliest possible boundary, and be explicit about types and ownership.** Match these in every
change.

### Make invalid states unrepresentable

- **Model with types, not dicts.** Every program fact is a Pydantic v2 model in `cldk/models/<lang>/`
  — never pass raw `dict`s across the public surface. If a backend returns JSON, marshal it into a
  model at the boundary; downstream code consumes the typed object.
- **Honest signatures over flags.** Factory methods in `core.py` expose **only** the options that
  apply to that language (`CLDK.python()` has `use_codeql`, `CLDK.java()` does not). Don't add a
  parameter that's meaningful for only some inputs — split the method or the config type instead.
- **Discriminate on type, not strings.** Backend selection keys off the *type* of the `backend=`
  config (`CodeAnalyzerConfig` / `PyCodeAnalyzerConfig` / `Neo4jConnectionConfig`), via the
  `JavaBackend`/`PyBackend`/`TSBackend` unions — not a stringly-typed `backend="neo4j"` argument.
  New behavior gets a new typed config, not a new magic string.
- **Newtype-style domain types.** Use the `AnalysisLevel` enum and the Pydantic models rather than
  bare `str`/`int` for domain concepts. A function that takes an analysis level takes `AnalysisLevel`.

### Error handling — fail loud, fail typed (the `unwrap` rule)

- **Raise the typed exceptions in `cldk/utils/exceptions/`** (e.g. `CldkInitializationException`,
  `CodeanalyzerExecutionException`) — never a bare `Exception`/`ValueError` for domain failures.
- **No silent `unwrap`.** The Python analog of Rust's forbidden `.unwrap()` is an unchecked
  `dict[key]`, `list[0]`, `next(...)`, or an `Optional` used as if non-`None`. Validate at the
  boundary and raise a typed error with context (see `_normalize_project_path`, which resolves and
  validates the path *once* and raises `CldkInitializationException` if it isn't a directory).
- **Never swallow.** No bare `except:` and no `except Exception: pass`. Catch the narrowest type,
  add context, re-raise (or raise a typed wrapper). Let unexpected failures surface.
- **`None` is a deliberate value, not an error channel.** Return `None` only where it's a documented,
  expected outcome (e.g. `cache_subdir(...)` returns `None` when no cache root can be derived).
  Document it in the docstring's `Returns`.

### Ownership & boundaries — normalize once, keep the facade thin (the `&str` rule)

- **Normalize at the edge, trust within.** Accept flexible input (`str | Path | None`), convert and
  validate it at the entry point (`_normalize_project_path`), and pass the canonical typed value
  inward. Don't re-parse/re-validate the same value at every call site.
- **The facade delegates; it does not implement.** `JavaAnalysis`/`PythonAnalysis`/… stay thin: they
  dispatch on config type and forward analysis queries to `self.backend`. Real work lives in the
  backend. Adding logic to a facade is a smell — push it into the backend (or a `commons/` helper).
- **One source of truth for shared logic.** Cross-language helpers live in `analysis/commons/`
  (e.g. `cache_subdir`, the tree-sitter parsers). Don't copy a helper into each language package.

### API design — minimal public surface, internal by default (the `pub(crate)` rule)

- **Export only what users need.** `cldk/__init__.py` exports `CLDK` and nothing else. Re-export the
  intended public API from the package `__init__.py`; everything else is internal.
- **Mark internals with a leading underscore.** Module-private helpers and functions get `_name`
  (e.g. `_normalize_project_path`, `_CACHE_KEYS`, `JCodeanalyzer._locate_jar`). Treat unprefixed
  names as the supported contract.
- **Typed config objects are Parameter Objects (the builder analog).** Complex setup goes through a
  dataclass config passed to a factory, not a long positional argument list. Add a field to the
  config rather than another positional parameter.
- **The backend ABC is the contract.** Any method a facade calls via `self.backend.X` MUST be
  declared on the language's `backend.py` ABC; all backends must implement it. The
  `test_*_backend_contract.py` introspection tests fail otherwise — keep them green.

### Collections & data flow — prefer expressions, stay lazy

- **Comprehensions and generators over manual accumulation.** Build with comprehensions /
  `itertools` rather than `result = []; for ...: result.append(...)`. Reach for a generator when the
  caller iterates once and the set is large (symbol tables, ASTs, call-graph edges) — don't
  materialize a full list you immediately consume.
- **Call graphs are `networkx.DiGraph`.** Don't reinvent graph traversal; use `networkx`.

### Performance — measure before optimizing, let the engine do heavy lifting

- **Cache is owned, not reinvented.** Analysis artifacts are cached under the language-keyed
  `cache_dir`; reuse it rather than re-running an analyzer. Heavy parallelism (Ray, CodeQL) is a
  knob on the engine config (`use_ray`, `use_codeql`) — expose engine capabilities, don't hand-roll
  thread pools in the SDK.
- **Don't pay for analysis you won't use.** Default to the lowest sufficient `AnalysisLevel`
  (`symbol_table`); only raise to `call_graph` when the query needs it.
- **Profile before optimizing.** Don't micro-optimize speculatively; keep code readable and measure
  first.

### Before review

- Every change passes **`make lint`** (`black`, `flake8`, `pylint` at line-length 180) and
  **`make test`**. This is the Python analog of "passes `cargo fmt`, `cargo clippy`, and tests" —
  non-negotiable before pushing.

## Testing protocol & fidelity bar

**Java is the reference standard. Every language and every backend/framework added MUST reach the
same testing fidelity Java has.** Java carries ~120 tests across 8 files exercising every layer;
the other languages are not there yet (see the gap matrix). When you add a language, a backend, or a
framework integration, replicate the *full* Java layer set — do not ship a facade with one smoke test.

### Layout & fixtures (the mechanics)

- Tests **mirror** `cldk/`: `tests/analysis/<lang>/`, `tests/models/<lang>/`. Place new tests in the
  mirrored location; never invent a new tree.
- Sample apps and fixture paths are declared in `pyproject.toml` `[tool.cldk.testing]` and read via
  `tests/conftest.py`. **Reuse the fixtures; never hardcode resource paths.** Session-scoped,
  autouse fixtures unzip real sample apps (`daytrader8`, `plantsbywebsphere`, `binutils`) and load a
  committed slim `analysis.json`.
- **Two execution modes, both required:**
  1. **Mocked/offline** — patch `subprocess.run` (or the engine entry point) to write the committed
     fixture `analysis.json` into the `-o` cache dir, so facade/query tests run **fast,
     deterministic, and without invoking the real analyzer** (see `_write_java_output` in
     `test_java_analysis.py`). Most tests use this.
  2. **Real-analyzer** — a smaller set runs the actual engine against a real sample app for
     behavior that can't be faked (e.g. CRUD detection, entry-point discovery on `daytrader8`).

### The required test layers (replicate all that apply per language)

Each layer is a distinct file following the `test_*` naming below. A layer is *required* wherever
its component exists for that language (tree-sitter only where a `treesitter_<lang>.py` parser
exists; Neo4j only where a `neo4j/` backend exists).

| # | Layer | File pattern | What it must cover | Java reference |
| --- | --- | --- | --- | --- |
| 1 | **Facade** | `test_<lang>_analysis.py` | **Every public method** on the facade — both real query results (against the fixture) and `NotImplemented`/error paths. Project mode *and* source-code mode where supported. | 40 tests |
| 2 | **In-process backend** | `test_<engine>.py` (e.g. `test_jcodeanalyzer.py`) | The codeanalyzer backend directly: init, exec-command construction, cache write/reuse/validation, legacy-vs-structured schema handling, call-graph generation, and every query method. | 43 tests |
| 3 | **Tree-sitter** | `test_<lang>_sitter.py` / `test_treesitter_<lang>.py` | The syntactic parser/sanitizer: parsability, raw AST, imports, names, methods, comments, pruning. *(Only Java & Python have tree-sitter parsers today.)* | 21 tests |
| 4 | **Backend contract** | `test_<lang>_backend_contract.py` | Introspection-only (no analyzer run): every backend subclasses the ABC, fully implements it (`__abstractmethods__ == frozenset()`), and **every `self.backend.X` the facade calls exists on the ABC**. | 4 tests |
| 5 | **Neo4j backend** | `test_<lang>_neo4j_backend.py` | The read-only Cypher backend rebuilds the *same* models from a graph. | 4 tests |
| 6 | **Backend selection** | `test_<lang>_neo4j_selection.py` | Passing each typed config selects the right backend (assert the in-process class is *not* constructed and the Neo4j class *is*, via `patch`). | 3 tests |
| 7 | **Deep semantics** | `test_<feature>.py` (e.g. `test_inheritance_call_graph.py`) | Non-trivial cross-cutting behavior — call-graph correctness, inheritance, type hierarchy — beyond single-method smoke checks. | 8 tests |
| 8 | **Models** | `tests/models/<lang>/test_<lang>_models.py` | Pydantic schema behavior: construction, schema-evolution/back-compat (legacy vs structured), and **JSON round-trip** (`model_dump`/reload). | 6 tests |

### Current fidelity gaps (close these to reach the bar)

Treat this as the live backlog — bring every cell up to the Java standard.

| Layer | Java | Python | TypeScript | C |
| --- | :-: | :-: | :-: | :-: |
| 1 Facade (all methods) | ✅ 40 | ⚠️ 6 (dispatch only — does **not** exercise query methods) | ✅ 22 | ❌ 1 (smoke) |
| 2 In-process backend | ✅ 43 | ❌ none (no `test_pycodeanalyzer.py`) | ⚠️ folded into facade | ❌ none |
| 3 Tree-sitter | ✅ 21 | ⚠️ 3 (minimal) | n/a | n/a |
| 4 Backend contract | ✅ 4 | ✅ 4 | ✅ 4 | ❌ none |
| 5 Neo4j backend | ✅ 4 | ✅ 5 | ✅ 17 | n/a |
| 6 Backend selection | ✅ 3 | ✅ 5 | ✅ 3 | n/a |
| 7 Deep semantics | ✅ 8 | ❌ none | ❌ none | ❌ none |
| 8 Models | ✅ 6 | ❌ 0 (empty file) | ❌ none | ❌ none |

### Rules for new code

- **New facade method →** add a facade test (layer 1) *and*, if it delegates, a backend test
  (layer 2). If it's a new `self.backend.X`, it must be on the ABC or layer 4 fails.
- **New language →** stand up layers 1–4 and 8 at minimum on day one; add 5–6 when a Neo4j backend
  lands and 7 as semantics grow. A language is not "done" at one smoke test.
- **New backend/framework →** it must satisfy the same ABC (layer 4), get its own selection test
  (layer 6), and a behavior test that reconstructs the identical models the in-process backend
  returns (layers 2/5). Interchangeability is the contract — prove it.
- **Coverage floors:** `pyproject.toml` sets `--cov-fail-under=50`; the Makefile relaxes to 33 for
  local `make test`. New code should raise the real number, not ride the floor. Run `make test` and
  read the `show_missing` report before pushing.

## Git / contribution conventions

- **Branching:** work on the designated feature branch; never push to `main` directly. Push with
  `git push -u origin <branch>`. Do **not** open a PR unless explicitly asked.
- **Commit messages:** follow the repo's Conventional-Commits-ish style seen in history —
  `feat(java-neo4j): …`, `fix(ci): …`, `docs(changelog): …`, `chore(release): …`.
- **Changelog:** notable changes go in `CHANGELOG.md` under `[Unreleased]` (Keep a Changelog format,
  SemVer). Releases are cut by tagging `v*.*.*`, which triggers `.github/workflows/release.yml` to
  build (injecting the JAR) and publish to PyPI.
- Keep `README.md` and `CHANGELOG.md` current when you change public behavior.

## Quick reference — entry points

```python
from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

analysis = CLDK.java(project_path="/path/to/project")               # default in-process backend
analysis = CLDK.python(project_path="/p", analysis_level=AnalysisLevel.call_graph)
analysis = CLDK.python(backend=Neo4jConnectionConfig(uri="bolt://localhost:7687",
                                                     application_name="my-app"))  # read-only graph
```
