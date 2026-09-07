Generated from `tests/resources/typescript/application` by the released **codeanalyzer-typescript 1.3.0** wheel:

    cants -i tests/resources/typescript/application --app-name slim -a <1|2|3|4> -o a<N> --cache-dir <scratch> -j 1

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
