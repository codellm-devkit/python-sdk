# Facade decisions — python-sdk

Decision log for SDK-surface design (per designing-cldk-changes, sdk-facade-design-loop).
One line per locked decision; newest section first.

## 2026-07-21 — Rust query engine (fluent API core)

- **M1 scope:** Epic B success criteria — the two Odoo PoE audit queries (#155), single-language,
  in-memory + Neo4j backends, `.explain()` reproduces the manual audit evidence. Cross-service
  (services/gRPC/proto, the RFC's boutique examples) is Epic E, out of scope; requires an
  analyzer-side schema design that has not happened.
- **Identity scheme:** `can://` (what analyzers emit today), extended as needed. The 2026-07-09
  fluent-query spec's `cldk://` is amended to `can://`; no parallel `service://`/`proto://`
  schemes — Epic E extends the `can://` grammar instead.
- **Plan algebra:** redesigned fresh, taking the 2026-07-09 spec's six primitives
  (Descend/Ascend/Relate/Filter/PathQuery/Project) and the RFC's LogicalOp sketch as inputs.
  Deliverable: an algebra ADR locked before the Rust core builds.
- **Data plane:** `cldk-query-core` consumes schema-2.0.0 `analysis.json` natively (serde CPG
  models) AND speaks Bolt directly (neo4rs) for the Neo4j backend. Core tests are cargo-only on
  fixture JSONs; no Python in the core.
- **Opaque(fn):** plan-split semantics — Rust executes the prefix, returns URIs, Python applies
  the lambda, execution re-enters Rust for remaining steps; `explain()` marks the split point.
- **Packaging:** fat wheel — `cldk` itself becomes a maturin/PyO3 platform wheel (abi3).
  Consequence accepted: `cldk` is no longer pure-Python; release workflow becomes a per-platform
  build matrix; platforms without a prebuilt wheel need a Rust toolchain for the sdist.
- **L3/L4 slicer:** the Rust core REPLACES the `cldk.graph` slice engine (#270/#271); the Python
  engine is deprecated once the Rust slicer passes the same exact-set gates. Single dataflow
  semantics owner; replacement staged post-M1.
- **Extraction boundary:** no PyO3 types/exceptions/callbacks in `cldk-query-core`; versioned
  `PlanEnvelope` wire format (semver string, house convention, not u32); language-neutral result
  structs; extraction only when independently consumed/released (per RFC criteria).
- **Repo layout (amends the RFC's `rust/crates/` sketch):** root-level `crates/` with the
  workspace `Cargo.toml` at the repo root (polars/ruff idiom; canonical Cargo layout, zero-config
  rust-analyzer, maturin driven from the root pyproject via
  `tool.maturin.manifest-path = "crates/cldk-python/Cargo.toml"`). The extension module compiles
  to the private submodule `cldk._native` — users import `cldk.query`; the public namespace never
  admits Rust exists. Crate names unchanged: `cldk-query-core` (survives extraction),
  `cldk-python` (bindings).
