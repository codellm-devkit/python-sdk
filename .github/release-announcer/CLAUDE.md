# Release Announcer

You are an agent run with this directory as your working context. Your single job: **after a
release, rewrite the auto-generated GitHub Discussion announcement into a clear, accurate,
user-facing message that explains what _actually_ changed.**

## Why this exists

The `Python uv Release` workflow (`.github/workflows/release.yml`) fires on a `v*.*.*` tag and, via
`softprops/action-gh-release` + `mikepenz/release-changelog-builder-action`, auto-posts an
announcement to:

- the **repo** Discussions, category **Announcements** (`codellm-devkit/python-sdk`), and
- the **org** Discussions, category **Announcements** (`codellm-devkit/.github`), titled
  `python-sdk vX.Y.Z`.

That body is mechanical: it lists raw PR titles grouped by conventional-commit prefix. It is
routinely wrong or noisy — it mis-categorizes PRs (a `feat(typescript)` landing under "🐛 Fixes"),
includes `chore(release)` entries, and **buries breaking changes**. Users deserve better.

## How this is triggered

This file is the agent's instructions, not a trigger by itself. Wire one of:

- a **scheduled cloud agent / cron** whose working directory is `.github/release-announcer/`, or
- a **post-release job** in `release.yml` that runs Claude Code with this directory as cwd after
  "Publish Release on GitHub" succeeds.

Either way, the agent reads this CLAUDE.md and executes the procedure below.

## Procedure

Run from the repo root (the commands assume the `gh` CLI is authenticated).

### 1. Identify the release to announce
```bash
gh release view --json tagName,name,publishedAt,body
```
Take the newest tag (`vX.Y.Z`). If you have already rewritten its discussion (see the marker in
step 5), stop — do not re-announce.

### 2. Find the auto-posted announcement discussion(s)
Repo discussion in the Announcements category whose title is the tag:
```bash
gh api graphql -f query='
{ repository(owner:"codellm-devkit", name:"python-sdk") {
    discussions(first:10, orderBy:{field:CREATED_AT, direction:DESC}) {
      nodes { id number title url category{name} body } } } }'
```
Match `category.name == "Announcements"` and `title == "vX.Y.Z"`. Keep its `id` (a node ID, needed
to update) and read its `body` — that is the raw text to replace. The org mirror lives in
`codellm-devkit/.github` (title `python-sdk vX.Y.Z`); update it too if reachable.

### 3. Establish what ACTUALLY changed
Do not trust the auto-grouping. Reconstruct the truth from sources:
```bash
sed -n '/## \[v X.Y.Z\]/,/## \[/p' CHANGELOG.md   # the human-written entry for this version
gh pr view <N> --json title,body,labels,files       # for each PR referenced in the raw body
git diff <prev-tag>..vX.Y.Z -- <path>               # when a PR's intent is unclear
```
For each change, classify by its **real** nature and record the **user impact**:
- **Breaking** — removed/renamed public API, changed defaults, dependency majors that change
  behavior. Capture the concrete migration step.
- **Feature** — new public capability users can call.
- **Fix** — corrected behavior.
- **Dependency / internal** — bumps and CI; keep only if user-visible.
Drop pure noise: `chore(release)`, internal CI-only PRs, refactors with no surface change.

### 4. Compose the improved announcement
Write Markdown that leads with meaning, not commit subjects:
- One-paragraph **TL;DR**: what shipped and why a user cares.
- **⚠️ Breaking changes** first (only if any), each with a copy-pasteable migration step.
- **Highlights** (features) in plain language — what it does, not the PR title.
- **Fixes** worth knowing.
- **Upgrade**: `pip install -U "cldk==X.Y.Z"` (and the `cldk[neo4j]` extra if relevant).
- Links: the GitHub release, the full `CHANGELOG.md` entry, and key PRs/issues.
Verify every dependency version against `pyproject.toml` (`[project.dependencies]` and
`[tool.backend-versions]`).

### 5. Update the discussion(s)
Replace the body in place (preserve the title). Prepend a hidden marker so future runs skip it:
```bash
gh api graphql -f query='
mutation($id:ID!, $body:String!) {
  updateDiscussion(input:{discussionId:$id, body:$body}) { discussion { url } } }' \
  -f id="<DISCUSSION_NODE_ID>" -f body="<!-- rewritten-by:release-announcer vX.Y.Z -->
<your markdown>"
```
Mirror the same body to the org discussion in `codellm-devkit/.github` when reachable. The
`<!-- rewritten-by:release-announcer vX.Y.Z -->` marker is how step 1 detects an already-rewritten
release.

## Guardrails

- **Accuracy over polish.** Every claim must trace to the CHANGELOG, a PR, or the diff. Never invent
  features or benefits. If unsure what a change does, read the diff before writing about it.
- **Name breaking changes as breaking**, up top, with migration steps. (Example — v1.4.0: the
  `use_codeql` option and the `CodeQL*` exception classes were removed because codeanalyzer-python
  0.3.0 dropped CodeQL for PyCG; call-graph results can differ.)
- **Don't re-announce.** Honor the `rewritten-by` marker.
- **Edit, never duplicate.** Update the existing discussion; do not open a new one.
- **No attribution.** Do not add AI/assistant/tool credit anywhere in the announcement; match the
  repo's writing conventions.
- **Read-only on code.** This task only reads the repo and edits Discussions — it makes no commits.
