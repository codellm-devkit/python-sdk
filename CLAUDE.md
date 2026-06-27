# CLAUDE.md

Agent guidance for `codellm-devkit/python-sdk`.

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
