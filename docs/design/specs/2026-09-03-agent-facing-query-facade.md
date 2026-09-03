# python-sdk 2.0 — a static-analysis front end for humans and agents

**Date:** 2026-09-03
**Status:** design locked except where marked; implementation not started
**Related:** [codellm-devkit/.github#35](https://github.com/codellm-devkit/.github/issues/35) (schema-v2 conformance — sibling concern, not parent)
**Supersedes:** the earlier draft of this spec, whose premise (one shared `Node`/`Edge`/`Application`) is withdrawn — see D2.
**Work items:** #310 (Java), #311 (query layer), #307 (JavaScript), #315/#316/#317/#301 (Python pin uptake), #309 (superseded)

---

## 1. Summary

CLDK 2.0 is a **complete static-analysis front end**: symbol table, source bodies, repository
artifacts, call graph, control and data flow, addressing, reachability, slicing and taint — one
surface, reached the same way whether the caller is a person at a REPL or an agent composing
queries at runtime.

There is one API, not two. A signature that is good for an agent — bulk by default, diagnostics
returned alongside results, no positional counting, no hand-rolled traversal — is the same
signature a person wants at 2am. Where the two pull apart the tie-breaker is that an agent cannot
ask a follow-up question and a person can, so the API answers the follow-up up front.

Two consequences run through everything below:

1. **Every call returns what the caller would otherwise have to infer.** A location comes back
   with its source text. An empty result comes back distinguishable from a broken query. A
   traversal comes back with a ledger of what it could not resolve.
2. **The front end does the graph work.** Enumeration, reachability and slicing belong in the SDK,
   not in caller-written loops — a caller writing its own traversal cannot tell a missing edge
   from a true negative, and neither can its reader.

---

## 2. What the front end is for — stage analysis

This design was derived by taking a production agent pipeline apart stage by stage and asking of
each: what is this stage doing that is really a static-analysis query, and what signature would
remove it? That pipeline is one of several possible consumers — an IDE assistant, a code-review
agent, a migration tool and a documentation generator all decompose into the same four moves,
**locate, read, relate, prove**. The stages below are the specific instance; the moves are the
general shape.

| Stage | What the caller does today | 2.0 furnishes | Effect |
| --- | --- | --- | --- |
| **map alert → sink** | resolves each `(file, line)` to its enclosing callable via `get_method` / `get_callers`, falling back to scanning the symbol table | `locate_many([(path, line), …])` → node, callable, type, module, **source slice**, diagnostics | resolution stops being a reasoning task; only grouping and ranking remain |
| **build callable index** | dumps its own index file from a full enumeration | `get_callables_overview()` with filters | the index build disappears |
| **catalog entrypoints** | shards the callable universe across dozens of parallel LLM workers to enumerate entrypoints and classify their inputs | `get_entrypoints()` — analyzers already emit `is_entrypoint` and entrypoint marker labels | the enumeration substage drops entirely; judgement narrows to attacker-controllability of a known set |
| **reachability cone** | hand-written multi-source backward traversal over the reversed call graph, re-run each fixpoint round | `backward_cone(sinks)` executed server-side, returning the cone **and** an unresolved-dispatch ledger | traversal leaves the prompt; soundness becomes data instead of discipline |
| **prove source → sink** | argues the dataflow narratively from bodies | `taint()` returning flows with per-edge provenance | the argument becomes evidence |

**The reachability stage is the sharpest case.** Its own instructions concede the failure mode:

> a live source that fell out of the cone because an edge was missing looks *identical* to a
> correct exclusion — both are "not in the cone". There is no signal in the output that tells them
> apart.

The mitigation today is an instruction not to modify the traversal. That is a database query
defended by prompt discipline. Moving it into the SDK and returning the ledger alongside the
result removes the error class for every caller, including human ones.

### 2.1 Signature principles these stages imply

- **Bulk by default.** `locate_many` before `locate`. Round trips cost latency for a person and
  context for an agent; a thousand alerts should be one call.
- **Return the next thing the caller will ask for.** A location without its source text guarantees
  a second call. `LocateResult` carries the slice.
- **Never return an ambiguous empty.** Absent, unanalysed, out-of-level and mis-versioned are four
  different answers and must not share a representation (D7).
- **Name what the caller can name.** Parameters by name, callables by signature, positions by
  `file:line`. Never by ordinal — an interface requiring the caller to know that `self` occupies
  slot 0 will be got wrong by a model and misread by a person.
- **Expose the soundness ledger.** Whatever the analysis could not resolve is part of the result,
  not a warning in a log.

### 2.2 Recorded failures become test cases

A production deployment's tool-gap log recorded 1,993 incidents against CLDK 1.5.0. The
frequencies are one sample and rank the work; the classes generalise and shape the API. Each class
becomes a named test in § 10 — the log is a conformance suite, not a bug list.

| Class | Incidents |
| --- | --- |
| silent empty result | 905 |
| unresolved callee (`callee_signature=None`) | 602 |
| alert quarantined for want of an enclosing callable | 513 |
| graph vocabulary mismatch, diagnosed by hand | 62 |

---

## 3. Locked decisions

| | Decision | Rationale |
| --- | --- | --- |
| **D1** | **One surface for humans and agents.** No agent-specific API, no human-specific API. | The properties agent authorship demands — bulk calls, returned diagnostics, named rather than positional addressing, no caller-written traversal — are ordinary good API design. Splitting the surface would double maintenance and let the human half rot. |
| **D2** | **No shared declaration models.** Each language owns its `type` / `callable` / `field` models; Python's remain a re-export from `codeanalyzer-python`. | Measured on the three released analyzers, `type` shares 8 fields of 23 and `callable` 10 of 26 — and every survivor is identity, containment or the graph. Zero shared *semantic* declaration fields. A shared base would carry five identity fields and two dicts while every question a caller asks lived in the subclass. |
| **D3** | **One generic ABC defines the query shape**, in three families: structural getters (per-language bodies), graph queries (one Cypher template plus a label-prefix pair), and `resolve()` (per-language). | The existing per-language ABCs are already the same interface written twice, differing only in returned types. The families divide exactly where the analyzers diverge. |
| **D4** | **2.0.0, with API signatures backwards compatible, enforced per leg.** Every public accessor keeps its name, parameters and return type. No parallel v1 model layer, no deprecation window. | Deployed consumers pin `cldk>=1.5.0`. Signature-level compatibility is what lets four legs land in sequence without any of them breaking a running caller. It does **not** extend to graph generation — see § 7.1. |
| **D5** | **Drop C entirely.** | libclang, syntactic-only, no v2 graph, no `schema.neo4j.json`, no analyzer. It cannot implement the graph family, and no deployment selects it. |
| **D6** | **Neo4j is the substrate for this cut.** Queries compile to Cypher. The graph is attached **read-only**, so nothing is materialised. | Deployments attach to a graph someone else built rather than maintaining a local cache, so the SDK may not assume write access. Read-only enforcement is #160. |
| **D7** | **An ambiguous empty is a defect.** Closed selector vocabulary, first-class `Diagnostic`, `strict=True` by default, and a `graph_schema_mismatch` probe on attach. | 905 recorded incidents where `[]` had to be told apart from a broken query by hand. An empty that reads as a negative is the one error class that costs a correctness-critical caller something. |
| **D8** | **Staging: Python → Java → TypeScript → query sweep.** `locate()` ships in every language leg rather than waiting for the sweep. | Three concrete legs validate the ABC before anything shared is built on it. `locate()` is pulled forward because it retires the most frequent failure and is small enough to carry three times. |
| **D9** | **A new epic**, sibling to `.github#35`. #309 closed as superseded. | #35 is schema conformance; this is a query facade. The register belongs to #35; the facade does not. |

---

## 4. Architecture

### 4.1 The ABC — three families

```python
# cldk/analysis/commons/backend.py
class AnalysisBackend(ABC, Generic[AppT, ModuleT, TypeT, CallableT, FieldT, ParamT]):
    """The query shape. Each language implements it against its own schema."""
    P: ClassVar[str]   # relationship prefix — "PY" | "TS" | "JS" | "J"
    N: ClassVar[str]   # node-label prefix  — "Py" | "TS" | "J"
```

| Family | Per-language? | Why |
| --- | --- | --- |
| **structural getters** — symbol table, types, callables, fields, parameters, **bodies**, **artifacts**, entrypoints | **yes, real bodies** | TypeScript splits type kinds across five node labels (`TSClass`, `TSInterface`, `TSEnum`, `TSTypeAlias`, `TSNamespace`); python and java use one each |
| **graph queries** — `locate`, `backward_cone`, `slice_backward`, `slice_forward`, `flows_to`, `reachable_by`, `taint` | **no — one template, plus `P`/`N`** | `PY_DDG` / `TS_DDG` / `J_DDG` are structurally identical: same endpoints (`XBodyNode → XBodyNode`), same properties (`var`, `prov`, `_k`) |
| **`resolve()`** — selector to node ids | **yes** | `Decorated` means decorators in python and typescript, annotations in java |

Language ABCs become parameterisations, adding only what they alone have:

```python
class PythonAnalysisBackend(
    AnalysisBackend[PyApplication, PyModule, PyClass, PyCallable, PyClassAttribute, str]
): ...

class JavaAnalysisBackend(
    AnalysisBackend[JApplication, JModule, JType, JCallable, JField, JCallableParameter]
):
    @abstractmethod
    def get_all_crud_operations(self) -> List[CRUDRow]: ...
```

`PyClassAttribute` and `JField` are the same slot under two names — which is where a naming
divergence becomes a type argument instead of an argument.

### 4.2 Source bodies are first-class

Body text is a structural getter, not an afterthought, and it is never truncated. v2 stores each
file's text once on its module node and every node carries byte offsets, so a body is a slice:

```python
def get_method_body(self, cls: str, method: str) -> str: ...            # module.source[span.bytes]
def get_method_bodies(self, sigs: Sequence[str]) -> dict[str, str]: ... # bulk
def get_source(self, node_id: str) -> str: ...                          # any node
```

`get_source` generalises to any node because every node has a span. A caller inspecting one
statement should not have to fetch a whole callable and re-slice it.

### 4.3 Repository artifacts are first-class

The repository-artifact layer — build manifests, configuration files, declared dependencies,
config keys, and the edges from code to config — is the one part of the graph all three analyzers
project **identically and unprefixed** (`Artifact`, `ConfigKey`, `Package`, `HAS_ARTIFACT`,
`DECLARES_DEPENDENCY`, `DEFINES_CONFIG`, `LOCKS`). It is uniform across languages and belongs in
the structural family:

```python
def get_artifacts(self) -> dict[str, Artifact]: ...
def get_dependencies(self) -> list[Dependency]: ...
def get_config_keys(self) -> dict[str, ConfigKey]: ...
def get_config_uses(self, key: str | None = None) -> list[ConfigUse]: ...
```

A front end that answers "which callable reads this config key" and "which manifest declared this
dependency" covers the questions that otherwise send a caller to grep.

### 4.4 `locate` — addressing, with the source in hand

```python
class LocateResult(BaseModel):
    node: Node | None            # innermost body node containing the position
    callable: CallableRef | None
    type: TypeRef | None
    module: ModuleRef
    source: str                  # the slice — enclosing callable's text, or the module's
    span: Span
    diagnostics: list[Diagnostic]

def locate(self, path: str, line: int, col: int | None = None) -> LocateResult: ...
def locate_many(self, positions: Sequence[tuple[str, int]]) -> list[LocateResult]: ...
```

`source` is why this is one call rather than two. Locating a position and reading its code is a
single question, and a front end answering half of it forces every caller to write the other half.

Three outcomes stay distinguishable, because the recorded failures conflate exactly these:

- **inside a callable** — `callable` set, `source` is the callable's text.
- **module top level** — `callable` is `None`, `module_scope` diagnostic, `source` is the module's
  text. A real position, not an absence.
- **not analysed** — `file_not_in_graph` diagnostic. The file exists on disk but no module node
  covers it; distinct from a file that does not exist.

### 4.5 Identity

Intra-callable edge lists use **local** ids (`@entry`, `19:8`, `22:8/actual_in:0`);
application-scope lists use fully-qualified `can://` ids. Every id crossing the public API is the
qualified form; qualification happens in the adapter.

---

## 5. The query surface

### 5.1 Closed selector vocabulary

Enumerable, so a caller picks rather than invents, and everything is named rather than positional:

```python
# cldk/analysis/commons/selectors.py
@dataclass(frozen=True)
class Callee(Selector):     target: str; arg: int | None = None
@dataclass(frozen=True)
class Param(Selector):      callable: str; name: str      # by NAME, never by index
@dataclass(frozen=True)
class Decorated(Selector):  decorator: str
@dataclass(frozen=True)
class Returns(Selector):    callable: str
@dataclass(frozen=True)
class Guard(Selector):      var: str
@dataclass(frozen=True)
class NodeId(Selector):     id: str                       # escape hatch
@dataclass(frozen=True)
class AnyOf(Selector):      of: tuple[Selector, ...]
```

`Param(callable="src.app.Store.key", name="name")` resolves to `…/key(self,name)@formal_in:1`. The
caller names the parameter; the SDK counts.

### 5.2 Diagnostics travel with results

```python
class Diagnostic(BaseModel):
    code: Literal["no_match", "ambiguous", "unknown_callable", "unknown_param",
                  "did_you_mean", "level_too_low", "module_scope",
                  "file_not_in_graph", "unresolved_dispatch", "graph_schema_mismatch"]
    message: str
    suggestions: list[str] = []
```

`strict=True` is the default: a selector matching nothing raises rather than returning an empty
result, and the error names the near-misses. Near-misses are a query, not a heuristic — every
candidate is already in the graph:

```cypher
MATCH (c:{N}Callable {signature:$sig})-[:{P}_HAS_BODY_NODE]->(f:{N}BodyNode)
WHERE f.kind = 'formal_in'
RETURN f.of AS name, f.id AS id
```

```
PolicyError: source selector matched nothing.
  Param(callable='src.app.Store.key', name='nmae')
  └─ unknown_param: 'src.app.Store.key' has parameters ['self', 'name']
     did_you_mean: name
```

Two codes carry more weight than the rest. **`level_too_low`** — below L4 there are no `param_in`
/ `param_out` edges, so an interprocedural question is *unanswerable, not negative*.
**`graph_schema_mismatch`** — § 7.1; the SDK probes the attached graph's vocabulary and says so
rather than returning zero rows.

### 5.3 Reachability returns its ledger

```python
class Cone(BaseModel):
    nodes: set[str]
    unresolved: list[Diagnostic]     # unresolved_dispatch, one per edge not followed

def backward_cone(self, sinks: Sequence[str]) -> Cone: ...
def reachable_by(self, node_id: str) -> Cone: ...
```

One multi-source reverse traversal, executed in the database. `unresolved` is the soundness ledger
that today lives in prompt instructions: every dispatch the resolved graph could not follow —
framework-mediated routes, dynamic dispatch, unresolved imports — comes back as data, so a caller
weakens its claim mechanically rather than by remembering to.

### 5.4 Taint

```python
def taint(self, sources: Sequence[Selector], sinks: Sequence[Selector],
          sanitizers: Sequence[Selector] = (), *, strict: bool = True) -> TaintResult: ...
```

```python
class FlowEdge(BaseModel):
    src: str; dst: str; var: str | None; prov: list[str]

class Flow(BaseModel):
    edges: list[FlowEdge]
    weakest: FlowEdge              # the least certain edge on the path

class TaintResult(BaseModel):
    flows: list[Flow]
    resolution: list[Resolution]   # what each selector matched
    unresolved: list[Diagnostic]
    max_level: int
```

`Guard` carries a variable rather than a node, and that distinction is load-bearing. In the worked
example the flow runs `@formal_in:1 → 19:8 → 22:8/actual_in:0` and never touches the guard
statement at `20:11`, so a node-granular sanitizer silently fails to cut it while a
variable-granular one succeeds. Under Neo4j that is one predicate —
`WHERE all(x IN r WHERE x.var <> $var)` — because `var` is a relationship property. Per-variable
precision is free here in a way it would not be for an in-process traversal.

A result carries the resolution that produced it, so a conclusion is auditable rather than
asserted, and `weakest` lets a caller cap a claim by its least certain edge without walking the
path.

---

## 6. Divergence register

Observed against `codeanalyzer-python` 1.4.0, `codeanalyzer-typescript` 1.2.0 and
`codeanalyzer-java` 3.0.1. These belong to `.github#35` and `#36`; the SDK absorbs them in its view
layer rather than blocking on them.

| # | Divergence | Verdict |
| --- | --- | --- |
| 1 | `config_reads_unresolved` (py) vs `config_reads` (ts) | shared field renamed |
| 2 | py keeps `start_line` / `end_line` / `code_start_line` alongside `span` | v1 residue |
| 3 | canonical says `base_types` / `interfaces`; **py and ts both emit `base_classes`**, and ts labels it `// spine:` | **canonical is the outlier**, not the analyzers |
| 4 | py `attributes` vs `fields` (ts, java, canonical) | parity breach on the py side |
| 5 | py `PyClass` vs `TSType` / `JType` plus a kind ladder | naming only |
| 6 | `k_limit` absent from java's envelope | gap |
| 7 | config layer and `unresolved_imports` absent from java | gap |
| 8 | entrypoints coined three ways | out of #35's scope by its own guard |
| 9 | py `entrypoint_report` / `repository`; ts `synthesized_callables`; java `JRecordComponent` | legitimate language-additive |
| 10 | py `analyzer` carries a third key, `config`, where canonical specifies `{name, version}` | undocumented |
| 11 | `callables` map keyed by simple name while the id tail is `key(self,name)` and `signature` is `src.app.Store.key` — three names for one callable; canonical says key by signature | collides on overloads in java and ts |
| 12 | **Neo4j projections are prefixed per language.** All three declare `schema_version: 2.0.0`, yet of their node labels (py 13, ts 16, java 15) only 3 are common, and of their relationship types (py 25, ts 23, java 24) only 4 — all 7 from the repository-artifact layer | the most serious entry: `#37`'s parity gate and `#38`'s conformance suite both presuppose a shared vocabulary that does not exist. Also the live cause of the recorded empty-result incidents |
| 13 | `prov: ["reaching-defs"]`, a third provenance value; canonical specifies only `ssa` and `points-to` | a caller filtering on `prov` per the spec silently drops these edges |

---

## 7. Staging and release plan

| Leg | Content | Retires | Ships |
| --- | --- | --- | --- |
| **1** | **Python** — v2 models (re-export), ABC conformance, `locate`/`locate_many`, bodies, artifacts, entrypoints, external symbols, `graph_schema_mismatch`. Drop C and four dead dependencies. | #315, #316, #317, #301 | `2.0.0-rc.1` |
| **2** | **Java** — v2 models, ABC conformance, `locate`, bodies, artifacts; `JGraphEdges` and `_CALLABLES_LOOKUP_TABLE` retired | #310 | `2.0.0-rc.2` |
| **3** | **TypeScript + JavaScript** — v2 models, prefixed (`TS_`/`JS_`) vocabulary, eager-init fix, `locate`, bodies, artifacts | #307; the 905 empty-result and 602 unresolved-callee incidents | `2.0.0-rc.3` |
| **4** | **Query sweep** — selectors, diagnostics, `backward_cone`, slicing, taint, across all three at once | #311; the 513 quarantines | `2.0.0` |

Three concrete legs validate the ABC before anything shared is written on it; a query layer built
against one backend would be an abstraction designed from one instance. The sweep is cheap
*because* it comes last — each graph query is one Cypher template and a language's contribution is
its `P`/`N` pair.

Python first because its models arrive free from the analyzer, so the leg exercises the ABC and
facade rather than the model layer. Java second because it is the hardest structural case, and
settling the ABC against it means TypeScript inherits a proven shape.

### 7.1 Graph-generation compatibility — what D4 does *not* cover

D4 freezes API signatures, so caller code keeps running across every leg. It does **not** make the
SDK compatible with every graph.

Each leg moves its language's Neo4j backend to the vocabulary the current analyzer emits. Against
a graph built by a matching analyzer generation the leg starts working; against one built by an
older generation it stops. Today that mismatch is silent — it is the 905 incidents, and the 62
where a caller diagnosed it by hand.

So the SDK **probes the attached graph's vocabulary on attach** and raises `graph_schema_mismatch`,
naming the labels it expected, the labels it found, and the analyzer generation each implies. That
check is part of leg 1 and every leg after it, and the compatibility matrix — SDK version to
analyzer generation to graph vocabulary — goes in each rc's release notes.

A caller upgrading across a leg therefore does two things, not one: take the new SDK, and confirm
the attached graph was built by a matching analyzer. The second was always true; until now nothing
said so.

### 7.2 Dropping C

Removes `cldk/analysis/c/` (1,038 LOC), `cldk/models/c/` (369 LOC), `tests/analysis/c/` and a
3.3 MB fixture, the `CLDK.c()` factory and its dispatch branch, and the `clang` and `libclang`
dependencies. `tree-sitter-c` and `tree-sitter-go` have no imports anywhere in `cldk/` or `tests/`
and go with them — four dependencies in total. Consumers validating a language against a closed
`Literal["python", "java", "typescript", "c"]` drop one token. The C row leaves `CLAUDE.md`'s
supported-languages table, and the CHANGELOG carries a *Removed* entry with no migration step,
because a removal has none.

---

## 8. Backwards compatibility

**Preserved — signature level.** Every public accessor on `PythonAnalysis`, `JavaAnalysis` and
`TypeScriptAnalysis` keeps its name, parameters and return type. Public model class names are kept,
now per-language rather than shared. Accessors whose v1 return type no longer exists as stored data
become computed views: `get_method_body` slices `module.source[callable.span.bytes]`,
`get_call_sites` reads `body{}` entries of kind `call`, `get_system_dependency_graph` returns the
v2 SDG over identity-only edges.

**Not preserved** — callers reaching past the public API: NetworkX node-key types (now `can://`
ids), `JGraphEdges` rich endpoints, `_CALLABLES_LOOKUP_TABLE` and its synthetic
`is_implicit=True` / `-1` sentinel callables, per-callable `code` as stored data, raw
`analysis.json` envelope keys, and the entire C surface.

**Not covered** — graph generation. See § 7.1.

**Signal:** the major bump. No deprecation window; analyzer pins move in the same release, so v1
payloads and v1 models retire together.

---

## 9. Tracking

A new epic in `codellm-devkit/.github`, sibling to #35: *"python-sdk 2.0: a static-analysis front
end"*. #35 remains the schema-conformance epic and receives register entries 1–13.

- **#309** — closed as superseded; its shared-model premise is withdrawn (D2).
- **#308** — stays closed; D4 is unchanged from its recorded policy.
- **#310**, **#311** — re-parented. #311's scope grows to selectors, diagnostics and reachability.
- **#307** — folds into leg 3; `JS_` labels are already present in deployed graphs.
- Per-leg children filed just-in-time, at pickup.

---

## 10. Definition of done

Each leg's facade passes its existing tests unmodified — the signature-stability gate. Beyond that,
the recorded failure classes become named tests, each reproducing an incident the log contains:

| Test | Asserts |
| --- | --- |
| `test_locate_module_scope` | a position in a module top-level statement returns the module with `module_scope`, not an empty result |
| `test_locate_unanalysed_file` | a file on disk with no module node returns `file_not_in_graph`, distinct from a file that does not exist |
| `test_locate_gap_between_callables` | a line falling between two callables' spans returns `module_scope`, never the nearest callable |
| `test_locate_carries_source` | `LocateResult.source` is the enclosing callable's text, byte-identical to the file |
| `test_body_not_truncated` | a callable longer than a thousand lines returns its whole body |
| `test_node_missing_optional_fields` | a node without `end_line` or `code` yields a result, not an exception |
| `test_external_callee_resolves` | a call to a builtin or library member resolves through `external_symbols` rather than leaving `callee` unset |
| `test_graph_schema_mismatch_raises` | attaching to a graph built by a mismatched analyzer generation raises `graph_schema_mismatch` naming expected and found labels — never returns zero rows |
| `test_callables_overview_non_empty` | against a graph built by a current analyzer, enumeration returns a non-zero count on all three languages |
| `test_selector_typo_raises` | a misspelled parameter raises under `strict=True` and names the near-misses |
| `test_cone_reports_unresolved` | `backward_cone` returns an `unresolved_dispatch` entry for every edge it could not follow |
| `test_level_too_low` | an interprocedural query against an L2 graph reports `level_too_low`, never an empty flow list |

Plus: `pyproject.toml` pins `codeanalyzer-python` 1.4.0 and `codeanalyzer-typescript` 1.2.0, bundles
the `codeanalyzer-java` 3.0.1 JAR, and carries none of `clang`, `libclang`, `tree-sitter-c`,
`tree-sitter-go`. `CLAUDE.md`'s supported-languages table drops C and describes the three families.
The 13-entry register is filed on `.github#36`.

---

## 11. Assumptions and scope

**Assumed.** Neo4j is the substrate (D6); the local codeanalyzer backends are out of scope for the
graph family in this cut. The graph is attached read-only, so nothing is materialised.

**Evidence caveat.** The 1,993 incidents come from a single production deployment, so the
frequencies are one sample: they rank the work, they do not prove generality. The failure classes
do generalise — an empty result indistinguishable from a negative one, positional addressing, and
caller-written traversal are properties of any static-analysis API with a non-expert caller, human
or otherwise.

**Out of scope.** Deriving edges no analyzer emitted. Analyzers emit both halves of a
shared-field dependency and not the join — one callable writes `self.salt`, another reads it, and
no call edge relates them — so a caller asking about shared-object state still infers it. CLDK
could compute that join, but an edge with no analyzer behind it is a fact the SDK would be
asserting on its own authority, and the join belongs upstream where every SDK would get it. To be
raised against the analyzers rather than built here.

Collapsing the per-language facades into one. The entrypoint vocabulary (register 8). Unprefixing the Neo4j labels (register 12) — the SDK ratifies the prefixes and
parameterises over them, so if `#36` later removes them both constants become `""`. Cross-request
persistence, which no static SDG covers. Any change to what an analyzer emits.
