# python-sdk → canonical schema v2

**Date:** 2026-09-03
**Status:** design locked; implementation not started
**Parent epic:** [codellm-devkit/.github#35](https://github.com/codellm-devkit/.github/issues/35) — canonical schema v2 consistency across `codeanalyzer-*` and `python-sdk`
**Work items:** #309 (shared model layer), #308 (compatibility policy), #310 (Java leg), #311 (query layer), #315/#316/#317 (analyzer pin uptake), #307 (JavaScript support)

---

## 1. Contract-impact triage

**Does this change the schema v2 output?** No. Every analyzer already emits v2; the SDK is the
last consumer still parsing v1. This is the consumer leg of a schema major that shipped upstream.

| Producer | SDK pins today | Latest released | Emits canonical v2 |
| --- | --- | --- | --- |
| `codeanalyzer-python` | `0.3.1` | `1.4.0` | yes |
| `codeanalyzer-typescript` | `0.4.3` | `1.2.0` | yes |
| `codeanalyzer-java` | JAR `2.4.1` | `3.0.1` | yes |
| `codeanalyzer-clang` (libclang, in-process) | n/a | n/a | no — syntactic only, unassessed |

**Repos affected:** `python-sdk` (four model packages, four facades, three Neo4j backends);
`codellm-devkit/.github` (tracking record only). No analyzer changes are required.

**Change type:** schema v2 migration, SDK leg — plus one new facade surface (#311).

---

## 2. What changed since epic #35 was written

Epic #35 rests on a premise that has since expired. It states:

> the v2 work is unreleased on both analyzers … no consumer sees v2 today, and reconciling the
> two v2 lines before either ships is far cheaper than reconciling shipped contracts afterwards.

All three analyzers have since shipped their v2 majors — `codeanalyzer-python` 1.4.0,
`codeanalyzer-typescript` 1.2.0, `codeanalyzer-java` 3.0.1 (cut 2026-09-03) — while
[.github#36](https://github.com/codellm-devkit/.github/issues/36), which was to freeze the
canonical contract *before* any migration, is still open and `codellm-devkit/codeanalyzer-schema`
holds only a README. The gating inverted: the migrations landed, the contract never froze.

Two consequences shape this design.

**The contract cannot be frozen in the abstract any more.** #36's title — "extract and freeze the
canonical schema v2 contract *from `codeanalyzer-python`*" — assumed one reference implementation.
There are now three, and they disagree (§ 5). Extraction from one of them would ratify one
analyzer's choices by accident.

**#309's stated Java blocker is void.** #309 says the SDK "cannot move to identity-only edges
while its producer emits rich ones", and marks the Java leg blocked on
`codeanalyzer-java#179`. `codeanalyzer-java` 3.0.1 emits `JCallEdge{src, dst, prov, weight}`,
`JIdEdge{src, dst}`, and a canonical `body` map keyed by ordinal id. The rich-edge shape
(`JGraphEdges` wrapping `JMethodDetail`) and the module-global `_CALLABLES_LOOKUP_TABLE` survive
only in the SDK's own v1 models. Java remains the heaviest leg, but the weight is SDK-side legacy
retirement, not an upstream dependency.

---

## 3. Locked decisions

| | Decision | Rationale |
| --- | --- | --- |
| **D1** | The SDK models the **intersection the three shipped analyzers actually emit**, not an idealised contract. Divergences surface as views that will not write cleanly, and are recorded in § 5 as input to #36. | The contract is unfrozen and the analyzers disagree. Writing one model layer over all three *is* the extraction exercise #36 describes, performed against reality rather than against one analyzer. |
| **D2** | **Shared base classes with per-language subclasses.** `cldk/models/cpg/` defines the spine once; each language subclasses, narrows the container maps by redeclaring them, and adds its typed extras. No generics, no untyped `extra="allow"` dicts. | Extras stay typed and discoverable; the spine is defined once. Costs one redeclared line per container per language, which is cheaper than parameterising every signature or reaching into `__pydantic_extra__` by string key. |
| **D3** | **2.0.0, API-stable via views.** The v2 models replace the v1 ones outright. Every public accessor keeps its name, signature and return type behind a view layer. There is no parallel v1 model layer and no deprecation window. | Confirms what #310 already commits to, and closes #308. A parallel layer would mean carrying two model sets *and* two Neo4j reconstruction paths for a payload shape no released analyzer emits any more. |
| **D4** | **Python → TypeScript → Java → queries**, shipped as `2.0.0-rc.N` per leg, `2.0.0` when all four land. | Python is cheapest (models are re-exported from the analyzer, so the pin bump supplies them free) and validates the spine against a real payload first. Java is heaviest and goes late. The query layer needs the spine proven. Matches the rc staging already agreed for L3/L4. |
| **D5** | **Reuse epic #35.** No new epic. #309 is rewritten against the shape established here; #308 is filled from D3 and closed as decided; per-leg children are filed just-in-time as each is picked up. | The epic coordinating this work already exists. A second epic layer over work that already has one buys a rollup and costs a level of indirection. |

---

## 4. The design

### 4.1 The shared layer — `cldk/models/cpg/`

All three analyzers carry the canonical spine at every container level: `id` / `kind` / `span`,
`source` once on the module node, named-map containment (`types` / `functions` / `callables` /
`fields`), a `body{}` map keyed by ordinal id, split `cfg` / `cdg` / `ddg` / `summary` edge lists
on the callable, and identity-only edges at application scope. That agreement is the shared layer.

| Group | Types |
| --- | --- |
| Envelope | `Analysis` (`schema_version`, `language`, `max_level`, `k_limit?`, `analyzer`, `application`), `AnalyzerInfo` |
| Containers | `Application`, `Module`, `Type`, `Callable` |
| Leaves | `BodyNode`, `Field`, `Parameter`, `Decorator`, `Import`, `Comment`, `Span` |
| Edges | `CallEdge{src, dst, prov, weight}`, `CfgEdge{src, dst, kind}`, `CdgEdge{src, dst}`, `DdgEdge{src, dst, var, prov}`, `IdEdge{src, dst}` (carries `summary`, `param_in`, `param_out`) |
| Repository-artifact layer | `ExternalSymbol`, `Artifact`, `Dependency`, `ImportBinding`, `ConfigKey`, `ConfigUse`, `ConfigRead` |

The repository-artifact and config members sit on the shared `Application` with empty defaults.
Java does not populate the config members today (§ 5, divergences 6–7); absence is the "no fact"
encoding the canonical schema already mandates, so this needs no per-language branch.

The edge list name **is** the edge type. No `type` field, and no rich-edge variant: `src` and `dst`
are node ids, and detail is joined by id.

### 4.2 The view layer

Per-language subclasses live where the models live today — `cldk/models/{java,python,typescript}/`
— and are what the facades return. This promotes the existing `projections.py` pattern
(`PyCallableOverview`, from #180/#181) from an ad-hoc projection to the general device.

```python
# cldk/models/cpg/nodes.py
class Application(BaseModel):
    id: str
    kind: Literal["application"] = "application"
    symbol_table: Dict[str, Module]
    call_graph: List[CallEdge] = []
    param_in: List[IdEdge] = []
    param_out: List[IdEdge] = []

# cldk/models/python/models.py
class PyApplication(Application):
    symbol_table: Dict[str, PyModule]                       # narrowed
    entrypoint_report: PyEntrypointReport = PyEntrypointReport()
    repository: Optional[PyRepositoryInfo] = None
```

The view layer is also where API stability is bought (D3). Accessors whose v1 return type no longer
exists as stored data become computed views over the spine:

| Accessor | v1 source | v2 view |
| --- | --- | --- |
| `get_method_body(sig)` | per-callable `code` string | `module.source[callable.span.bytes]` |
| `get_call_sites(...)` | `callable.call_sites[]` | `body{}` entries with `kind == "call"` |
| `get_system_dependency_graph()` | `JGraphEdges` with `JMethodDetail` endpoints | the v2 SDG over identity-only edges |

Python is the special case: its models are re-exported from `codeanalyzer-python`
(`cldk/models/python/__init__.py`), so the pin bump supplies the v2 models and the SDK's own work
is the subclass narrowing, the views, the Neo4j backend and the facade.

### 4.3 What is *not* in scope

Collapsing the per-language facades (`PythonAnalysis` / `JavaAnalysis` / `TypeScriptAnalysis` /
`CAnalysis`) into one — out of scope for the parent epic and unchanged here. The entrypoint
vocabulary (§ 5, divergence 8), which epic #35 explicitly guards out into its own design session.
Any change to what an analyzer emits. `codeanalyzer-clang`, which is syntactic-only and has no
declared schema version.

---

## 5. Divergence register

Recorded against the three shipped analyzers. This is the evidence #36 needs, and it is a
deliverable of this spec rather than of the implementation.

| # | Divergence | Verdict |
| --- | --- | --- |
| 1 | `config_reads_unresolved` (python) vs `config_reads` (typescript) | shared field renamed — parity breach |
| 2 | python keeps `start_line` / `end_line` / `code_start_line` alongside `span` | v1 residue; canonical says `span` replaces them |
| 3 | python `base_classes` vs canonical `base_types` / `interfaces` | parity breach |
| 4 | python `attributes` vs `fields` (typescript, java, canonical) | parity breach |
| 5 | python `PyClass` (single kind) vs `TSType` / `JType` plus a kind ladder | naming only; python has one type kind |
| 6 | `k_limit` absent from java's envelope, present in python and typescript | gap |
| 7 | config layer and `unresolved_imports` absent from java | gap |
| 8 | entrypoints coined three ways: `PyEntrypoint`, `TSApplication.entrypoints`, `JCallable.is_entrypoint` | out of epic #35's scope by its own guard |
| 9 | python `entrypoint_report` / `repository`; typescript `synthesized_callables`; java `JRecordComponent` / `JTypeParameter` | legitimate language-additive under the parity clause |

Divergences 1–4 are parity-clause breaches on the python side; 6–7 are gaps on the java side. The
SDK absorbs all of them in the view layer for 2.0.0 rather than blocking on analyzer fixes, and
each absorption is a line item #36 can retire later.

---

## 6. Staging and release plan

| Order | Leg | Carries | Closes | Ships |
| --- | --- | --- | --- | --- |
| 1 | Shared layer + **Python** | `cldk/models/cpg/`; `codeanalyzer-python` → `1.4.0`; python subclasses, views, Neo4j backend, facade | #315, #316, #317, #301 | `2.0.0-rc.1` |
| 2 | **TypeScript** | `codeanalyzer-typescript` → `1.2.0`; typescript subclasses, views, Neo4j backend, facade; JavaScript as a supported language | #307; #300 falls out of the view layer | `2.0.0-rc.2` |
| 3 | **Java** | JAR → `3.0.1`; java subclasses and views; `JGraphEdges` and `_CALLABLES_LOOKUP_TABLE` retired; `AnalysisLevel` gains L3/L4 | #310 | `2.0.0-rc.3` |
| 4 | **Query layer** | slicing, taint, reachability over the v2 SDG; `…@line:col` addressing | #311 | `2.0.0` |

**Ordering constraints.**

- The shared layer lands *with* the Python leg, not before it. A spine designed with no consumer is
  a spine designed against the spec rather than against a real payload.
- Each leg's pin bump and model migration land in the **same** PR. The v1 models cannot parse v2
  output and the v2 models cannot parse v1 output, so splitting them leaves `main` unable to
  analyse that language.
- The Java leg is unblocked as of `codeanalyzer-java` 3.0.1 (2026-09-03). Its dependency is on the
  shared layer existing, not on any analyzer work.
- #311 needs L3/L4 populated, so it follows all three legs.

**Compatibility statement for the 2.0.0 CHANGELOG** (this is D3, stated for release):

- *Preserved* — every public accessor's name, signature and return type on all four facades; the
  public model class names (`PyCallable`, `JCallable`, `TSCallable`, …), now subclasses of the
  shared bases.
- *Not preserved* — consumers reaching past the public API: NetworkX node-key types, `JGraphEdges`
  rich endpoints, `_CALLABLES_LOOKUP_TABLE` and its synthetic `is_implicit=True` / `-1` sentinel
  callables, per-callable `code` as stored data, and raw `analysis.json` envelope keys.
- *Signal* — the major bump itself. There is no deprecation window: the analyzer pins move in the
  same release, so v1 payloads and v1 models retire together.

---

## 7. Decomposition

Per D5, epic #35 keeps coordinating this work.

| Action | Item |
| --- | --- |
| Rewrite | **#309** — from skeleton into the shared-layer work item, against § 4. Its Java-blocker caveat and its intersection premise are both withdrawn (§ 2). |
| Fill and close | **#308** — the policy is D3. Record it and close as decided; it is no longer an open design question. |
| Keep as filed | **#310** (java leg), **#311** (query layer) — both already carry maintainer-written bodies consistent with this spec. Note on #310 that its "starts after the analyzer's v2 release is cut" precondition is met. |
| Fold in | **#315**, **#316**, **#317** (pin uptake) → leg 1. **#307** (JavaScript) → leg 2. |
| File just-in-time | one work item per leg, at pickup — not now. The committed spec is what stops the plan being forgotten; issues filed ahead of the work are inventory. |

---

## 8. Definition of done

- `cldk/models/cpg/` defines the spine once, and no language model package redefines a spine field.
- All four facades load their analyzer's canonical v2 output, and every public accessor's name,
  signature and return type is unchanged — proven by the existing facade tests running unmodified.
- `pyproject.toml` pins `codeanalyzer-python` 1.4.0, `codeanalyzer-typescript` 1.2.0, and bundles
  the `codeanalyzer-java` 3.0.1 JAR.
- Both projections agree per language: `analysis.json` and the Neo4j backend return the same facts
  for the same run.
- The 2.0.0 CHANGELOG carries the compatibility statement in § 6 with each semantic shift named.
- `CLAUDE.md`'s supported-languages table reflects the v2 model layer.
- The divergence register (§ 5) is filed onto .github#36 as the extraction input.
- Epic #35's SDK children are closed and its SDK-side definition-of-done clauses are green.
