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

## Tests

- Layout **mirrors** `cldk/`: `tests/analysis/<lang>/`, `tests/models/<lang>/`. Place new tests in
  the mirrored location.
- Sample applications and fixture paths are declared in `pyproject.toml` `[tool.cldk.testing]` and
  read via `tests/conftest.py`. Reuse these fixtures; don't hardcode resource paths.
- Backend-contract tests (`test_*_backend_contract.py`) are introspection-only and must pass without
  running an analyzer. Per-language `test_*_analysis.py`, `test_*_neo4j_backend.py`, and
  tree-sitter tests cover the real behavior.

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
