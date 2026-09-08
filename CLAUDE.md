# CLAUDE.md

Agent guidance for `codellm-devkit/python-sdk`.

## Supported languages

The SDK exposes one static factory method per language on `CLDK`. Each returns a facade backed by
a per-language `<Lang>AnalysisBackend` ABC, with a local codeanalyzer backend and (where available)
an optional read-only Neo4j backend — selected by the *type* of the `backend=` config object.

| Language | Entry point | Local backend | Neo4j backend | Models |
|----------|-------------|---------------|---------------|--------|
| Java | `CLDK.java(...)` (needs the `cldk[java]` extra) | `JCodeanalyzer` (the `codeanalyzer-java` 3.1.1 wheel's jar on its bundled JVM, subprocess, `-a 1..4` — no jar in this repo, no JDK download) | `JNeo4jBackend` (graphs emitted by ≥ 3.1.1, probed at attach) | `cldk/models/java/` (schema v2 mirror) + `projections.py` |
| Python | `CLDK.python(...)` | `PyCodeanalyzer` (in-process `codeanalyzer-python`) | `PyNeo4jBackend` | re-exported from `codeanalyzer-python` |
| TypeScript (+ JavaScript modules) | `CLDK.typescript(...)` | `TSCodeanalyzer` (`codeanalyzer-typescript` 1.5.2 binary from the wheel, subprocess; `-a 1..4`, but `--emit neo4j` takes no `-a` and is always full depth) | `TSNeo4jBackend` (graphs emitted by ≥ 1.5.2; older refused at attach) | `cldk/models/typescript/` (schema v2 mirror) |

**Java, since leg 3a (#310):** the models are an `extra="forbid"` mirror of canonical schema v2, so
a 1.x `analysis.json` (and a pre-3.0.1 Neo4j graph) is refused, not parsed; `get_call_graph()` keys
nodes by the string `"<type fqn>.<signature>"`; the `source_code` single-file mode is gone; the CRUD
accessors raise (codeanalyzer-java#187).

**Java, since leg 3b (#311):** the leg-1.5/1.6 query surface, 38 accessors with `PythonAnalysis`'s
signatures — addressing, per-callable graphs and dataflow, entrypoints and the bulk projections, the
artifact six, and the J-7 leaf accessors (`get_interfaces`/`get_enums`/`get_enum_members`/
`get_records`, names shared with TypeScript). Both backends answer identically, including the miss
paths; the policy lives once on `JavaAnalysisBackend` because `JNeo4jBackend` rebuilds the canonical
`JApplication` and answers from it. Four things Java says rather than answering, each measured:
`slice_forward` / `paths_between` / `flows_to_call` / `flows_to_argument` raise (the analyzer's L4
port lattice carries no dependence edge, codeanalyzer-java#227); `get_entrypoint_coverage` reports
`entrypoint_report_unavailable` and the three config-read accessors raise on an analysis older than
codeanalyzer-java 3.1.0, which is the release that added both overlays (the probe is the entrypoint
report's presence, measured from the data, never a version string); `get_external_symbols` raises
off a local run (`--external-calls` is opt-in and `--emit neo4j` forces it); the CRUD accessors
still raise. `docs/agent-api-reference.md` has the full lossiness list.

The legacy `CLDK(language="<lang>").analysis(...)` entry still works as a compat shim. Adding a
language means a new factory method + facade + backend ABC/impl(s) + models + tests — **update this
table in the same change**.

## I implement features myself — you assist

For feature work, **I write the implementation myself** to stay fluent in my own SDK. Act as a helper, not the author:

- **Don't write the feature code** or apply edits to implement it unless I explicitly ask
  ("write this", "implement X", "apply it"). Default to guiding, not doing.
- **Do** help me move fast: explain the relevant patterns and where things live, point me at
  prior art (e.g. the `PyCallableOverview` accessors from #180/#181 as the template), sketch
  signatures/types, outline an approach, and answer questions about the codebase.
- **Review on request:** when I share a diff or push, critique it — correctness, parity across
  backends, missing tests, edge cases — and suggest concrete improvements.
- Scaffolding like tests or boilerplate is fine **when I ask**; otherwise leave the keyboard to me.
- If you think I'm about to go wrong, say so briefly and let me decide — don't pre-empt by
  implementing the fix.

## Rule 1 — Think Before Coding
State assumptions explicitly. Ask rather than guess.
Push back when a simpler approach exists. Stop when confused.

## Rule 2 — Simplicity First
Guide me towards minimum idomatic code that solves the problem. 
Nothing speculative. No abstractions for single-use code.

## Rule 3 — Issue -> Branch -> Work -> PR
Everything I do will be a new issue, a new branch titled `fix/issue-XXX`, or `feat/issue-XXX`,
or `chore/issue-XXX`, etc. The the work will go into that branch and a PR will be submitted.

## Rule 4 — Goal-Driven Execution, as a teaching loop
Success is measured by the sole fact that **I understand it**. 
The success criterion: I can point to the exact line of code where any feature lives, 
however remote or obscure, and explain why it's there and how it behaves.

To that end, you will be my teacher and a Socratic one at that, not an answer key:
- Lead with questions that make me derive the answer; don't hand me the solution.
- Verify understanding, not just behavior — have me locate and explain the relevant LOC, walk
  edge cases, and predict what a change would do before running it.
- Teach, help improve, and strengthen the weak spots you surface; circle back to them.
- The loop closes when I can **teach it back** and place every feature on a line, not merely when
  the tests pass.
- Over the session, frequently, but not so much that I am stymied, ask spaced repetition questions
so concepts are internalized. 

# Auxilliary support tasks

## Tidy up the release announcement

Every `vX.Y.Z` tag makes the release workflow (`.github/workflows/release.yml`) auto-post an
announcement to the **Announcements** discussion (this repo, and the org mirror in
`codellm-devkit/.github`). That body is machine-generated from PR titles — it mis-categorizes
changes, includes `chore(release)` noise, and buries breaking changes.

When you're working in this repo, check whether the latest release's announcement still needs
cleanup, and if so, fix it:

1. **Find it.** `gh release view --json tagName,publishedAt`; then list recent discussions via
   `gh api graphql` (repository → discussions), match category `Announcements` and title `vX.Y.Z`.
   Keep the discussion node `id` and read its `body`.
2. **Skip if already done.** If the body starts with `<!-- cleaned-up -->` (or already reads as a
   clear, human-written announcement), do nothing.
3. **Otherwise rewrite it** into a clear, user-facing announcement, grounded in `CHANGELOG.md` and
   the referenced PRs/diff (not the auto-grouping — verify each change; never invent anything):
   - **breaking changes first**, each with a one-line migration step;
   - plain-language highlights (what it does, not the PR title);
   - upgrade line: `pip install -U "cldk==X.Y.Z"`;
   - links to the GitHub release and `CHANGELOG.md`.
4. **Update in place.** Edit the discussion body with the GraphQL `updateDiscussion` mutation
   (don't open a new one), prepend `<!-- cleaned-up -->`, and mirror the same body to the org
   discussion. This task only reads code and edits Discussions — it makes no commits.
