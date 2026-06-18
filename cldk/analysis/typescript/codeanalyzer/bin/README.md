# codeanalyzer-typescript binary

This directory is the optional drop location for the compiled `codeanalyzer-typescript`
backend binary (built from the `codeanalyzer-ts` repo with `bun build --compile`).

The SDK wrapper (`cldk/analysis/typescript/codeanalyzer/codeanalyzer.py`) resolves the binary in
this order:

1. `analysis_backend_path=<dir>` passed to `CLDK("typescript").analysis(...)` (rglob'd here).
2. `$CODEANALYZER_TS_BIN` environment variable.
3. A binary named `codeanalyzer-typescript*` placed in **this** directory (bundled in the wheel).

The binary is platform-specific and ~70 MB, so it is **not** committed to the repo. Build it
with `bun build ./src/index.ts --compile --outfile dist/codeanalyzer-typescript` and copy it
here (or point `analysis_backend_path` at it). The pinned version is recorded under
`[tool.backend-versions] codeanalyzer-typescript` in `pyproject.toml`.
