Generated from `tests/resources/typescript/application` by the released **codeanalyzer-typescript 1.5.3** wheel:

    cants -i tests/resources/typescript/application --app-name slim -a <1|2|3|4> -o a<N> --cache-dir <scratch> -j 1 --no-build

The application's `package.json` and `tsconfig.json` are part of the input (the artifact layer inventories
them; `experimentalDecorators` comes from the tsconfig) and are tracked next to `src/` despite the
repo-wide `*.json` ignore. Regenerate all four when the pin moves; the models test asserts
`analyzer.version` against the pin, and `tests/analysis/typescript/test_typescript_e2e.py` runs the
pinned binary on the same application.

What 1.3.0 changed in this output, against the 1.2.0 generation these files held before
(`analyzer.version` aside): `application.entrypoint_report` appears (required on the application at
1.3.0; `rulesets: ["shipped"]`, no framework detected in this corpus, `unresolved` naming the two
decorators it could not resolve), `is_entrypoint` / `entrypoints` appear on classes and on every
callable, `parameters[].id` and body-node `id`s appear (cants#165), the `-a 4` DDG gains the port
lattice wired to the statement graph (`@formal_in:N` / `actual_out` endpoints, cants#169), and
decorators no longer carry `qualified_name` at all. No model needed widening for any of it.

What 1.5.2 changed against the 1.3.0 generation these files held before: the `can://` id grammar
puts the **application outermost** (`can://slim/typescript/src/index.ts`, was
`can://typescript/slim/src/index.ts`), the application root is bare (`can://slim`), `@external`
homes lost their language segment entirely (`can://slim/@external/(builtin)/log`), and artifacts
moved inside the application prefix (`can://slim/artifact/package.json`). 1.5.1 made the id change
but shipped with `ANALYZER_VERSION` left at `"1.5.0"`, so these were regenerated with 1.5.2 --
the first release whose stamp matches what it emits, and the SDK's floor for that reason.

What 1.5.3 changed against the 1.5.2 generation these files held before: `param_in` and `param_out`
edges name the bound formal in `var` (cants#197), on the argument leg and the return leg alike.
Measured on `a4`: `param_in` went from 5 of 31 edges carrying `var` to 31 of 31, and `param_out` from
0 of 26 to 26 of 26. Nothing else moved -- no top-level key appeared or vanished at any level, and no
model needed widening, because `TSParamEdge` had already declared `var` as optional. The sibling
analyzers shipped the same fix in lockstep (codeanalyzer-python 1.5.1 / #196, codeanalyzer-java
3.1.2 / #250), and Java's did need a model widening: `JParamEdge` had only `src`/`dst`, so every
param edge failed validation until the field was added — the mirrors were `extra="forbid"` then, and
are `extra="ignore"` since #386, so an addition like this one is now absorbed silently instead.

Why it mattered: the schemas had declared the property since the L4 layer landed and the projections
wrote nothing, so a consumer predicate on `var` was `null` on every edge crossing a call boundary.
Under Cypher's three-valued logic an `all()` over that `null` excludes the whole path, so an
interprocedural flow read as a proved absence of flow -- indistinguishable from a real negative.
