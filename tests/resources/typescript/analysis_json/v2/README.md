Generated from `tests/resources/typescript/application` by the released **codeanalyzer-typescript 1.5.2** wheel:

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
