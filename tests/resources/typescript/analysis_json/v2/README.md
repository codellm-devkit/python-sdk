Generated from `tests/resources/typescript/application` by the released **codeanalyzer-typescript 1.2.0** wheel:

    cants -i tests/resources/typescript/application --app-name slim -a <1|2|3|4> -o a<N> --cache-dir <scratch> -j 1

The application's `package.json` and `tsconfig.json` are part of the input (the artifact layer inventories
them; `experimentalDecorators` comes from the tsconfig) and are tracked next to `src/` despite the
repo-wide `*.json` ignore. Regenerate all four when the pin moves; the models test asserts
`analyzer.version` against the pin, and `tests/analysis/typescript/test_typescript_e2e.py` runs the
pinned binary on the same application.
