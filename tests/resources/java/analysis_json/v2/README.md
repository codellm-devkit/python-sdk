Generated from `tests/resources/java/application/daytrader8-1.2.zip` (unzipped to `<daytrader8>` =
`sample.daytrader8-1.2/`) by **codeanalyzer-java 3.0.2** — the pinned `codeanalyzer-java` wheel (the
`cldk[java]` extra), run as `codeanalyzer_java.command()`: the wheel's own jar on the JVM it bundles
(`jdk4py`, Temurin 21.0.8), with `JAVA_HOME` unset. **Never hand-edit these files** — regenerate
them when the pin moves.

`a1/analysis.json` — the whole application at L1 (138 compilation units; 13.3 MB, pretty-printed by
the analyzer):

    java -jar codeanalyzer.jar -i <daytrader8> -a 1 --app-name daytrader8 -o a1 -c <scratch>/fx-cache-a1

`a4/analysis.json` — L4 for four units (5.4 MB). `--schema v2` (the default) rejects `-t` /
`--target-files` ("supports whole-project analysis only"), so the restriction is done on the input
tree instead: a copy of `<daytrader8>` whose `src/main/java` is reduced to

    src/main/java/com/ibm/websphere/samples/daytrader/beans/MarketSummaryDataBean.java
    src/main/java/com/ibm/websphere/samples/daytrader/beans/RunStatsDataBean.java
    src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java
    src/main/java/com/ibm/websphere/samples/daytrader/web/servlet/TradeServletAction.java

(everything else under `src/main/java` deleted; `pom.xml`, resources, webapp, `jmeter_files` and
`target/` kept), analyzed with `--no-build` so the L4 WALA pass reads the `target/classes` compiled
earlier by a whole-project `-a 4` run of the analyzer on the unpruned tree (the zip ships no classes):

    java -jar codeanalyzer.jar -i <daytrader8-pruned> -a 4 --no-build --app-name daytrader8 -o a4 -c <scratch>/fx-cache-a4

Measured on the committed files: `a1` has `schema_version 2.0.0`, `analyzer.version 3.0.2`,
`max_level 1`, 138 symbol-table keys, no `call_graph`/`param_in`/`param_out` keys at all, 235
artifacts. `a4` has `max_level 4`, 4 symbol-table keys, `call_graph` 247 edges, `param_in` 258,
`param_out` 97, 320 `ddg` edges with `prov == ["points-to"]`, 76 `summary` edges, 235 artifacts, and
both `cancelOrder` overloads on `TradeDirect` (`cancelOrder(java.lang.Integer, boolean)`,
`cancelOrder(java.sql.Connection, java.lang.Integer)`). Artifact text (default `--artifact-text`,
256 KiB cap) is included in both; the four `jmeter_files/*.jmx` are the largest entries.


The committed files are gzip-compressed (`analysis.json.gz`, `gzip -9`) because the analyzer pretty-prints and the raw pair is 18.7 MB; `tests/conftest.py` reads them with `gzip.open`. Regenerate, then `gzip -9 -k` — never hand-edit.
