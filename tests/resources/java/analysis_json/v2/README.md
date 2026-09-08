Generated from `tests/resources/java/application/daytrader8-1.2.zip` (unzipped to `<daytrader8>` =
`sample.daytrader8-1.2/`) by **codeanalyzer-java 3.1.0** — the pinned `codeanalyzer-java` wheel (the
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

Measured on the committed files: `a1` has `schema_version 2.0.0`, `analyzer.version 3.1.0`,
`max_level 1`, 138 symbol-table keys, no `call_graph`/`param_in`/`param_out` keys at all, 235
artifacts, 149 types and 1,216 callables (133 of them `is_entrypoint`, in 66 `is_entrypoint_class`
types). `a4` has `max_level 4`, 4 symbol-table keys, 128 callables, `call_graph` 247 edges,
`param_in` 258, `param_out` 97, 2,358 `ddg` edges (2,038 `ssa`, 320 with `prov == ["points-to"]`,
20 self-loops), 1,570 `cfg`, 1,258 `cdg`, 76 `summary` edges, 235 artifacts, and both `cancelOrder`
overloads on `TradeDirect` (`cancelOrder(java.lang.Integer, boolean)`,
`cancelOrder(java.sql.Connection, java.lang.Integer)`). Artifact text (default `--artifact-text`,
256 KiB cap) is included in both; the four `jmeter_files/*.jmx` are the largest entries.

**The 3.1.0 overlays, and what each fixture carries of them.** Both carry `entrypoint_report`
(`a1`: `frameworks_detected ["jakarta", "jaxrs", "spring"]`; `a4`: `["jakarta"]`; both with the
same five `rulesets`, nothing unresolved, no errors) and `entrypoint_frameworks` on every callable
and type — non-empty on exactly the marked ones, 133 callables and 66 types in `a1`, 13 callables
and no type in `a4`. `a1` carries **13** `config_uses` (every one `prov == ["literal"]`, all in
`TradeWebContextListener.contextInitialized`, over 11 distinct `daytrader.properties` keys) and
**16** `config_reads_unresolved` (all `reason "undefined-key"`, the `System.getenv` reads, `prov
["literal"]` — level 1 has no DDG for the dataflow tier to run over; the same reads carry
`["literal", "dataflow"]` at level 4). `a4` carries **neither key at all**: its pruned tree reads no
configuration, and the analyzer writes those two only when non-empty. That is the measurement the
SDK's overlay probe rests on — an absent `config_uses` cannot mean "this analyzer had no detector",
so the probe is the entrypoint report's presence instead (`cldk/analysis/java/backend.py`,
`CONFIG_OVERLAY_UNAVAILABLE`).

## What the 3.1.0 regeneration moved

Both files were regenerated with the commands above when the pin moved from 3.0.3 to 3.1.0
(python-sdk#369, analyzer-side codeanalyzer-java#233 / #235 / #237). Measured by loading the old and
new copies and comparing them structurally: **both are identical to their 3.0.3 copies apart from
the `analyzer.version` stamp and the additive fields** — `entrypoint_report`, `config_uses` and
`config_reads_unresolved` on the application, `entrypoint_frameworks` on every type and callable.
Every figure published above and below this section was re-measured on the new files and none of
them moved: 138 units / 149 types / 1,216 callables / 235 artifacts in `a1`, and `a4`'s whole
dataflow structure (2,358 `ddg`, 2,038 `ssa`, 320 `points-to`, 20 self-loops, 1,570 `cfg`, 1,258
`cdg`, 76 `summary`, 247 `call_graph`, 258 `param_in`, 97 `param_out`), as well as the signature
table further down (154 / 0 / 45 of 45 for `a1`, 8 / 4 / 4 of 5 for `a4`).

Note that the new fields make a 3.1.0 payload **unparsable by the pre-#369 models**, which are
`extra="forbid"`: the graph contract stays at 2.0.0 and the wire is additive, but the SDK's mirror
had to grow the five fields before it could read one.

## What the 3.0.3 regeneration moved

Both files were regenerated with the commands above when the pin moved from 3.0.2 to 3.0.3
(python-sdk#354, analyzer-side codeanalyzer-java#227 / #228). Measured by loading the old and new
copies and comparing them structurally:

- `a1` is **identical apart from the `analyzer.version` stamp** — 138 units, 149 types, 1,216
  callables, 235 artifacts, no `cfg`/`cdg`/`ddg` at L1.
- `a4` differs in **`ddg` and nothing else**. Every pre-existing edge is still there; 867 `ssa`
  edges were added (1,491 → 2,358), and all 867 join the port lattice to the statement graph:
  `formal_in → call` 272, `statement → actual_in` 129, `formal_in → statement` 125,
  `call → actual_in` 98, `return → formal_out` 77, `actual_out → statement` 76, and seven smaller
  directions. `points-to` stays at 320, the 20 `ddg` self-loops stay, and `cfg` (1,570), `cdg`
  (1,258), `summary` (76), `call_graph`, `param_in`, `param_out`, the artifacts, the callables and
  the units are byte-identical.
- 217 of `a4`'s 225 `formal_in` vertices now have an outgoing `ddg` edge (0 before). The eight
  that have no outgoing SDG edge at all — `investmentReturn`'s `rnd1`/`rnd2`, `orderCompleted`'s
  `userID`/`orderID`, `pingTwoPhase`'s `symbol`, `removeHolding`'s `orderID`,
  `updateHoldingStatus`'s `symbol`, `requestDispatch`'s `userID` — are parameters nothing in the
  callable depends on, so "every port is attached" would be the wrong assertion.
- **Dangling `ddg` endpoints are 0 in both releases on this fixture**, so codeanalyzer-java#228 is
  not observable here; it was only ever a whole-project fact.


The committed files are gzip-compressed (`analysis.json.gz`, `gzip -9`) because the analyzer pretty-prints and the raw pair is 18.7 MB; `tests/conftest.py` reads them with `gzip.open`. Regenerate, then `gzip -9 -k` — never hand-edit.

## A caveat about `a4`'s signature spellings

`a4` is a **pruned copy** of the project (schema v2 rejects `-t`, so a small level-4 fixture can only be made by
deleting sources). Pruning makes most types unresolvable, and the analyzer then falls back to the spelling written
in the source. Measured across the four types both fixtures share — 128 callables on each side, walked over the
same **flattened** type index the SDK addresses on (`JCodeanalyzer._types`: nested, local and anonymous types
included):

| | `a1` (whole project) | `a4` (pruned) |
|---|---|---|
| parameter types | `com.ibm…AccountDataBean` | `AccountDataBean` |
| generic arguments | erased — `java.util.Collection` | kept — `Collection<QuoteDataBean>` |
| qualified interface names | 5 of 5 | 4 of 5 |
| signatures with a generic in the **parameter tail** (`"<" in sig.partition("(")[2]`) | 0 of 128 | 4 of 128 |
| signatures containing `<` anywhere, tail or name | 6 | 8 |

So a callable's signature key is a function of **what the analyzer could resolve**, not of the analysis level.

*Erratum (2026-09-07).* The first version of this table, and the message of commit `228a4c4`, published "143"
signatures containing `<` and "34 of 34" qualified interface names for `a1`. Both came from a walk that visits only
each compilation unit's top-level `type_declarations` — it skips nested, local and anonymous types, and the same
walk yields 1,177 callables for `a1`, the figure the leg-3a J-8 erratum already retracted in favour of **1,216**.
Over the whole of `a1`, walked completely, the numbers are **154** signatures containing `<` and **45 of 45**
qualified interface names (149 types, 1,216 callables). The row was also mislabelled: all 154 of those `<`
characters are in the callable's *name* (`<init>` / `<clinit>`) and **none** is a generic in the parameter tail, so
"generic arguments" was never what that row counted. The conclusion is unchanged — `a4` keeps generics where `a1`
erases them — only the evidence is. Reproduce with `JCodeanalyzer._types` (not `unit.type_declarations`) and
`"<" in signature.partition("(")[2]`.

**Use `a1` for anything that asserts a signature's spelling**, and `a4` only for what it exists to carry: the
level-4 dataflow structure (`param_in`, `param_out`, `summary`, `points-to` provenance). A test that pins an `a4`
signature is pinning an artifact of the pruning, and it will not match the same method in a complete project.
