# CLDK Python API — agent reference

What you can ask a CLDK Python analysis, what comes back, and what will mislead you if you don't
know it. Written for an agent composing queries at runtime.

Status: legs 1, 1.5 and 1.6 (`codeanalyzer-python` 1.4.1 pinned; graphs emitted by 1.4.0 or newer
served) — every accessor below is implemented on both backends and exercised against live graphs of
both generations. Design records: `docs/design/specs/2026-09-03-agent-facing-query-facade.md`,
`docs/design/specs/2026-09-05-leg-1.5-bounded-queries-and-dataflow.md` and
`docs/design/specs/2026-09-06-leg-1.6-id-prefix-scoping.md`.

---

## Attach

```python
from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig, PyCodeAnalyzerConfig

# Against a deployed graph (read-only; the SDK never builds it)
py = CLDK.python(project_path=".", backend=Neo4jConnectionConfig(
    uri="bolt://localhost:7687", username="neo4j", password="…",
    application_name="my-app"))

# Or analyse a checkout in-process
py = CLDK.python(project_path="/path/to/project", backend=PyCodeAnalyzerConfig())
```

Attaching raises `GraphSchemaMismatch` if the graph was built by a different analyzer generation.
That is deliberate: the alternative is every query silently returning zero rows. If you see it,
the graph needs re-ingesting — it is not a bug in your query.

**Graph version floor: codeanalyzer-python 1.4.0.** The probe reads `:PyApplication.analyzer_version`
and the message names what it found and the floor. What attaching to each generation does:

| graph emitted by | attach | behaviour |
| --- | --- | --- |
| < 1.4.0, or no `analyzer_version` | refused (`GraphSchemaMismatch`) | the `can://` id grammar every query scopes on does not exist there |
| 1.4.0 | served, silent | identical results and plans (`locate` / `resolve_callable` seek the `:PySymbol(id)` index both generations carry); `get_entrypoint_coverage()` reports `entrypoint_report_unavailable` (the report was not projected yet) |
| 1.4.1 and newer | served, silent | as 1.4.0, and the entrypoint report is read off the graph |

---

## TypeScript

Status: leg 2.5a (`codeanalyzer-typescript` 1.2.0 pinned; graphs emitted by 1.2.0 or newer
served). What attaches today is the **1.x accessor surface** — symbol table, classes / interfaces /
enums / type aliases / namespaces, methods, fields, call graph, call sites, decorators, externals,
synthesized callables, the four bulk accessors and the repository-artifact layer — on both backends.
Every accessor is **called** against the live superset-frontend graph (1,841 modules: 1,557
TypeScript, 284 JavaScript) and asserted against raw Cypher counts, but that corpus does not
exercise all of them on data: it holds **no `TSDecorator` nodes and no `TS_DECORATED_BY`,
`TS_IMPLEMENTS` or `TS_USES_CONFIG` relationship types at all**, so `get_decorators`,
`get_class_decorators`, `get_methods_with_decorators`, `get_classes_with_decorators`,
`get_decorated_callables` and `get_config_uses` are verified only to return their empty there, and
`get_implemented_interfaces` only to raise. Their query shapes are pinned offline against a fake
two-application graph that does carry those edges; their behaviour on a decorator-heavy or
`implements`-heavy corpus (an Angular or NestJS project) is **untested by corpus**.
**The query surface documented in the rest of this reference** (`locate`, the scoping
keywords, `get_cfg`/`get_cdg`/`get_ddg`, slices, reachability, paths, `describe`, entrypoints,
`SelectorNotInGraph` / `AmbiguousName`) **is not on `TypeScriptAnalysis` yet — it arrives in leg
2.5b**, on codeanalyzer-typescript 1.3.0. Design record:
`docs/design/specs/2026-09-06-leg-2.5-typescript.md`.

```python
ts = CLDK.typescript(project_path=None, backend=Neo4jConnectionConfig(
    uri="bolt://localhost:7690", username="neo4j", password="…",
    application_name="my-app"))          # the --app-name the graph was emitted with

ts = CLDK.typescript(project_path="/path/to/project", backend=TSCodeAnalyzerConfig())
```

**Graph version floor: codeanalyzer-typescript 1.2.0.** The probe requires `TS_HAS_MODULE` /
`TS_HAS_METHOD` / `TS_HAS_BODY_NODE` / `TS_CALLS`, then reads `analyzer_version` off
`:Application {id: can://typescript/<app>}`.

| graph | attach |
| --- | --- |
| emitted by 0.4.x (`:Symbol` / `CALLS` / `HAS_CALLSITE`), a Python graph, an empty database | refused (`GraphSchemaMismatch`), naming the relationship types found and missing |
| `analyzer_version` below 1.2.0, unparsable, or no `:Application` with that id (an absent application) | refused, naming what was found and the floor |
| 1.2.0 and newer | served, silent — a graph emitted by an unreleased `main` build also stamps `1.2.0` and cannot be told apart (filed upstream) |

**JavaScript is in scope.** The analyzer ids `.js/.jsx/.mjs/.cjs` modules `can://javascript/<app>/…`
beside `can://typescript/<app>/…`; every accessor reads both, and every Neo4j statement is scoped
by the two prefixes. Path values are repo-relative module keys with their real extension
(`src/pages/Home.tsx`).

**The call graph keeps TypeScript's endpoints.** A module (top-level code) can be a caller and a
class (`new X()`) a callee; nodes carry `kind` ∈ `module | class | interface | enum | type_alias |
namespace | callable | external`. Filter on `kind == "callable"` for Python's shape.

**What the 1.2.0 Neo4j projection does not carry.** The in-memory backend reads `analysis.json`
and has everything below; the graph does not. Where an empty value would read as a fact, the Neo4j
backend **raises** (`CodeanalyzerExecutionException`, naming the gap); otherwise the model's
documented empty comes back.

| accessor / field | on `TSNeo4jBackend` |
| --- | --- |
| `get_imports()`, `get_exports()` | **raises** — no import/export vocabulary in the graph |
| `get_unresolved_config_reads()` | **raises** — `config_reads` not projected; `[]` would read as "every read resolved" |
| `get_method_parameters(cls, m)` | **raises** for a found method (no parameters on `:TSCallable`); `[]` for a missing one |
| `get_extended_classes(sig)`, `get_implemented_interfaces(sig)` | read off `TS_EXTENDS` / `TS_IMPLEMENTS`, so **resolved in-repo bases only** (a library base is in `base_classes` with no node to point at); each **raises** when its relationship type is absent from the database — as `TS_IMPLEMENTS` is on superset-frontend. The `implements_types` property the in-memory split uses is written by no node in the projection, so subtracting it would return the interfaces as extended classes |
| `get_config_uses(key=None)` | `[]` also when the database declares no `TS_USES_CONFIG` at all (superset-frontend): indistinguishable from a corpus with no config reads. Not raised — a project that reads no configuration is a valid project, and refusing it at attach would be worse |
| `get_nested_classes(sig)` | permanently `[]`, on **both** backends and not a projection gap: a schema-v2 class holds only `callables` and `fields`, so no class nests a class. `TSCallable.inner_classes` is the surviving case |
| `get_application_view().param_in` / `.param_out` | empty — leg 2.5a reads **no** dataflow overlay (2.5b does). `.config_reads` and `.unresolved_imports` are empty for their own reasons: not projected, and no accessor reads `TS_UNRESOLVED_IMPORT`. `.artifacts` / `.dependencies` / `.config_uses` **are** populated, from the same rows the dedicated accessors return |
| `TSCallable.parameters`, `comments`, `type_parameters`, `overload_signatures`, `body`, `cfg`/`cdg`/`ddg`/`summary` | empty |
| `TSEnumMember.value`; `TSModule.source` / `imports` / `exports` / `comments`; decorator positions; a call site's `method_name`, receiver and argument facets | empty / `None` |
| `code` on any node | the text the graph projected for that node, on a line-only span (columns `0`) |
| `get_call_targets(sig)` | an unresolved call site contributes `""` (in-memory: the call's `method_name`) |
| `get_synthesized_callables()` | keyed by the anonymous node's own id (the analyzer's older compatibility key is JSON-only) |

One more is the emitter's: a value and a type of the same name (`const X = …` + `interface X`)
share one id, so the graph holds one node with both labels. The backend rebuilds it as the facet
its containment edge and labels name and raises when they name none or several — three such nodes
on superset-frontend.

---

## The four moves

Most questions decompose into these. Start here, then use the tables below.

| You have | You want | Call |
| --- | --- | --- |
| a scanner alert at `file:line` | the enclosing callable and its code | `locate(path, line)` |
| a callable name | its source | `get_method_bodies([sig])` or `get_source(node_id)` |
| a callable | who calls it / what it calls | `get_callers(...)` / `get_callees(...)` |
| a value and a sink | does one reach the other | `flows_to_call(...)` / `flows_to_argument(...)` |
| a config key | which code reads it | `get_config_readers(key)` |
| nothing, want the surface | entrypoints | `get_entrypoints()` + `get_entrypoint_coverage()` |

---

## Addressing — turning a position into a node

| API | Returns | Example |
| --- | --- | --- |
| `locate(path, line)` | `LocateResult` | `py.locate("addons/account_payment/controllers/payment.py", 36)` |
| `locate_many(positions)` | `List[LocateResult]` | `py.locate_many([("a.py", 12), ("b.py", 44)])` — **one round trip** |

```python
class LocateResult:
    node: BodyNode | None      # innermost body node at that position
    node_id: str | None        # handle for get_source()
    callable: CallableRef | None
    type: TypeRef | None
    module: ModuleRef
    source: str                # the enclosing callable's text, or the module's
    span: Span
    diagnostics: list[Diagnostic]
```

**Four outcomes, and you must tell them apart:**

| Outcome | How you know |
| --- | --- |
| inside a callable | `callable` is set, no diagnostic |
| module top level | `callable is None`, diagnostic `module_scope` — a **real position**, not an absence |
| between two callables | same as module scope; it never snaps to the nearest callable |
| file not analysed | diagnostic `file_not_in_graph` — distinct from a file that doesn't exist |

**Gotcha:** over Neo4j, module-scope `source` is empty and carries `module_source_unavailable`.
`:PyModule` nodes genuinely do not store source text. The local backend returns it. Do not read
an empty `source` as "no code there".

---

## Reading source

| API | Returns | Example |
| --- | --- | --- |
| `get_method_bodies(signatures)` | `Dict[str, str]` | `py.get_method_bodies(["addons.foo.Bar.baz"])` |
| `get_source(node_id)` | `str` | `py.get_source(loc.node_id)` |

**Gotcha:** `get_method_bodies` **omits** callables with no body rather than returning `""` for
them. A missing key means "no body" (an abstract method, a protocol stub), not "no such callable".
Check `key in result`, not `result[key] == ""`.

**Gotcha:** `get_source` raises `NotImplementedError` over Neo4j for a body-node id (one containing
`@`). The graph has no per-statement source. The message names the gap. Callable-granularity ids
work on both backends.

---

## Enumerating the application

| API | Returns | Cost | Example |
| --- | --- | --- | --- |
| `get_callables_overview()` | `List[PyCallableOverview]` | **0.6s** ✓ | `py.get_callables_overview()` |
| `get_symbol_table(paths=None)` | `Dict[str, PyModule]` | **12s** | `py.get_symbol_table(paths=["addons/account/models/account_move.py"])` |
| `get_classes(module=None)` | `Dict[str, PyClass]` | **10s** | `py.get_classes(module="addons/account/models/account_move.py")` |
| `get_modules()` | `List[PyModule]` | **14s** | `py.get_modules()` |
| `get_application_view()` | `PyApplication` | **28s** | `py.get_application_view()` |
| `get_imports()` | `Dict[str, List]` | | `py.get_imports()` |

**Scope it.** `paths=` (a sequence of symbol-table keys) and `module=` (one key) narrow the query
in the database, not afterwards — one module comes back in well under a second. Absolute paths and
native separators resolve too. Omit the keyword to enumerate the whole application.

```python
py.get_symbol_table(paths=["addons/account/models/account_move.py"])   # one module
py.get_classes(module="addons/account/models/account_move.py")         # that module's classes
```

**A selector that matches nothing raises** `SelectorNotInGraph` (`cldk.utils.exceptions`, a
`ValueError`), naming the values that missed — `1 of 2 paths not in graph: 'gone.py'`. A mistyped
path used to return `{}`, indistinguishable from a module that genuinely declares no classes. The
message lists what missed and stops: no "did you mean", by design. An **empty** sequence
(`paths=[]`) is a different error — plain `ValueError`, because the argument that means
"everything" is the argument omitted.

**Prefer the projection anyway.** `PyCallableOverview` carries what you usually need and costs a
single query; a whole-application reconstruction is ten seconds and a large object graph even now
that the per-node fan-out is gone (it was 73,669 round trips and ~6 minutes).

```python
class PyCallableOverview:
    signature: str          # "addons.onboarding.models.step.OnboardingStep.action_validate"
    name: str               # "action_validate"
    class_signature: str | None
    kind: str               # "method" | "function"
    path: str               # repo-relative, e.g. "addons/onboarding/models/step.py"
    start_line: int
    end_line: int
    decorators: list[str]
```

**Changed in 1.5:** `PyCallableOverview.path` is now the repo-relative module key — the same
string as `locate().module.path`, `PyClassOverview.path` and the keys of `get_symbol_table()`, so
the four join directly. Before 1.5 it was the absolute path carrying the *analysis machine's*
filesystem layout (`/Users/…/checkout/addons/…`), which joined to none of them and did not exist
on any other host. Code that stored these paths, or that stripped a prefix off them, sees
different strings and should drop the stripping. (Issue #320 covers the sibling id problem.)

| API | Returns | Example |
| --- | --- | --- |
| `get_class(sig)` | `PyClass \| None` | `py.get_class("addons.onboarding.models.step.OnboardingStep")` |
| `get_classes_by_criteria(inclusions=, exclusions=)` | `Dict[str, PyClass]` | `py.get_classes_by_criteria(inclusions=["*Controller"])` |
| `get_methods_in_class(sig)` | `Dict[str, PyCallable]` | `py.get_methods_in_class("…OnboardingStep")` |
| `get_method(class_sig, method)` | `PyCallable \| None` | `py.get_method("…OnboardingStep", "action_validate")` |
| `get_method_parameters(class_sig, method)` | `List[str]` | `["self", "invoice_id", "access_token"]` |
| `get_constructors(sig)` | `Dict[str, PyCallable]` | `py.get_constructors("…OnboardingStep")` |
| `get_fields(sig)` | `List[PyClassAttribute]` | `py.get_fields("…OnboardingStep")` |
| `get_nested_classes(sig)` | `List[PyClass]` | |
| `get_sub_classes(sig)` | `Dict[str, PyClass]` | who extends this |
| `get_extended_classes(sig)` | `List[str]` | what this extends |
| `get_decorated_callables(markers)` | `List[PyCallableOverview]` | `py.get_decorated_callables(["http.route"])` |

---

## Call graph

| API | Returns | Cost | Example |
| --- | --- | --- | --- |
| `get_callers(class_sig, method)` | `Dict` | | `py.get_callers("…PaymentPortal", "invoice_transaction")` |
| `get_callees(class_sig, method)` | `Dict` | | |
| `get_class_call_graph(class_sig, method_signature=)` | `List[Tuple[str, str]]` | | |
| `get_callsites_for(signatures)` | `Dict[str, List[PyCallsite]]` | | `py.get_callsites_for(["…invoice_transaction"])` |
| `get_external_symbols()` | `Dict[str, PyExternalSymbol]` | | out-of-project call targets |
| `has_resolution_edges` | `bool` (a **property**, not a call) | | can this graph resolve callees at all |
| `get_call_graph(roots=None, depth=None)` | `nx.DiGraph` | 12s whole (**364,752 edges**); < 1s scoped | `py.get_call_graph(roots=["…invoice_transaction"], depth=2)` |
| `get_call_graph_json()` | `str` | **26s**, ~144 MB of JSON | the whole application, serialised |

**Node keys are signatures**, except out-of-project targets, which keep their `@external` id:

```
addons.account_payment.controllers.payment.PaymentPortal.invoice_transaction
can://python/odoo-slim-19/@external/logging.Logger/info
```

**Scope it.** 364,752 edges is not an answer to a question about one function.

```python
py.get_call_graph(roots=["…PaymentPortal.invoice_transaction"], depth=2)
```

`roots=` names callables the same way the graph does (a signature, or an `@external` id), and the
result is the **induced** sub-graph over everything reached: every edge *among* the reached
callables, not only the ones lying on a path out from a root — so `graph.predecessors(n)` never
lies about a node you can see. `depth=` bounds it in call hops and requires `roots=`; it must be an
`int` >= 1. Both backends return the identical graph, including a root that calls nothing, which
comes back as a graph of one node — and that holds for a callable with **no call edge at all**,
2.9% of a real application (444 of odoo's 15,549), which is a node of no edge-built graph.

- A root is valid if the application **declares** it, or if it is a node of the call graph (an
  `@external` id is the second and not the first). Anything else raises `SelectorNotInGraph` — not
  the same answer as "this callable calls nothing". Both backends check that same domain.
- **`roots=` is an exact filter, not a name.** Every other accessor here suffix-matches, so
  `roots=["AccountMove.write"]` — correct as a name — misses exactly like a typo would. The error
  says so. Resolve first: `py.get_call_graph(roots=[py.resolve_callable("write", in_class="AccountMove").callable])`,
  or ask the name-based question directly (`backward_cone`, `callers_of`, `call_paths_between`).
- `paths=` and `roots=` take *sequences*; a bare string raises `TypeError` rather than being
  iterated character by character. (`module=` is the single-valued one.)
- `paths=` resolution is many-to-one: `"pkg/a.py"` and `"/abs/pkg/a.py"` are the same module, so
  two requested paths can legitimately come back as one entry.
- `depth=` without `roots=`, `roots=[]`, and a non-integer `depth` all raise `ValueError`.
- Over Neo4j this compiles to one query, scoped to the application at every hop, and needs
  **Neo4j 5.9+** — checked against the version read when the backend attached, and raised only by
  the hop-scoped walks (this call, `reaches` and `backward_cone`), so an older server still serves
  every other accessor. A mistyped root is only visible
  *after* that query runs, so a partial miss costs the surviving roots' traversal first (5.11s for
  an unbounded walk out of odoo's busiest callable) rather than failing instantly (0.02s) the way
  an all-miss request does.

**Gotcha:** `PyCallsite.callee_signature` may be `None`. Check `has_resolution_edges` (a property) first — if
it is `False`, the graph carries no resolution data at all and *every* callee is `None` for that
reason, not because those particular call sites are unresolvable.

**Gotcha:** `get_method` and `get_constructors` return call sites with `callee_signature` unset
even when the data exists. `get_callsites_for` resolves the same call sites. Use it when you care
about callees.

**Gotcha:** `prov` on a call edge can mislabel its source. Edges resolved by CodeQL are reported as
`jedi` (codeanalyzer-python#28, open). Treat `prov` as "something resolved this", not as a reliable
attribution of *which* resolver.

**Stability note — `PyCallsite` may converge away.** Python emits call-site facts **twice**: as
`call_sites[]` on the callable *and* as `call` nodes in `body{}`. TypeScript emits only the body
nodes. codeanalyzer-python#120 records the plan to converge them and is parked pending a
cross-repo spec. The lists survive today because they carry detail the body nodes lack —
structured `arguments` (`ast_kind`, `inferred_type`), `receiver_expr`, `receiver_type`,
`is_constructor_call`.

If you only need *which callee*, prefer reading `body{}` `call` nodes (via `locate()` or the
per-callable graphs), which is the representation both languages agree on and the one that
survives convergence. Reach for `PyCallsite` when you specifically need the argument or receiver
detail — and know that shape is the one under review.

---

## Entrypoints

| API | Returns | Example |
| --- | --- | --- |
| `get_entrypoints()` | `List[PyCallableOverview]` | `py.get_entrypoints()` |
| `get_entrypoint_classes()` | `List[PyClassOverview]` | class-level entrypoints (CBVs) |
| `get_entrypoint_coverage()` | `EntrypointCoverage` | **call this before trusting a zero** |

```python
class EntrypointCoverage:
    frameworks_detected: list[str]
    rulesets: list[str]
    unresolved: list[str]
    errors: list[str]
    diagnostics: list[Diagnostic]
```

**This is the most important gotcha in the API.** The analyzer's entrypoint pass
*under-approximates by design* — its own docs say "silence is its failure mode". On a real Odoo
checkout, codeanalyzer-python 1.4.0 detected **zero** entrypoints across 15,549 callables, in a
framework built entirely from HTTP routes; 1.4.1 ships Odoo rules and flags 534 callables and 94
classes on the same checkout. The number is a fact about the analyzer generation and its rules,
never about the application alone.

So an empty `get_entrypoints()` means either "no entrypoints" or "the pass found nothing", and you
cannot tell from the list. `get_entrypoint_coverage()` is how you ask. Over a Neo4j graph emitted
by codeanalyzer-python 1.4.0 it reports `entrypoint_report_unavailable` — that graph does not carry
the report — which is itself the answer: *you cannot trust the zero*. From 1.4.1 the graph carries
it and the answer is the pass's own report, same as the local backend.

Concluding "this application has no attack surface" from an empty list is the single worst mistake
available in this API.

---

## Repository artifacts and configuration

| API | Returns | Example |
| --- | --- | --- |
| `get_artifacts()` | `Dict[str, PyArtifact]` | `py.get_artifacts()["pyproject.toml"]` |
| `get_dependencies(direct_only=, ecosystem=, declared_in=)` | `List[PyDependency]` | `py.get_dependencies(direct_only=True)` |
| `get_config_keys()` | `Dict[str, PyConfigKey]` | `py.get_config_keys()["DB_URL"]` |
| `get_config_uses(key=None)` | `List[PyConfigUseEdge]` | raw code→config edges |
| `get_config_readers(key)` | `List[PyCallableOverview]` | **`py.get_config_readers("DB_URL")`** |
| `get_unresolved_config_reads()` | `List[PyConfigRead]` | reads that could not be resolved |

Prefer `get_config_readers(key)` over `get_config_uses` — it answers "which callable reads this"
directly, where `PyConfigUseEdge` gives you `{src, dst, prov}` ids you would have to unpack.

`PyConfigRead` carries `reason`, so an unresolved read tells you *why* it could not be resolved
rather than just vanishing.

This layer is identical across Python, Java and TypeScript — the queries port unchanged.

---

## Dataflow

All implemented, on both backends: the three per-callable graphs (`get_cfg` / `get_cdg` /
`get_ddg`), the slices (`slice_backward` / `slice_forward` / `backward_cone`), the call-graph
questions (`reaches` / `callers_of` / `callees_of` / `call_paths_between`), the value-flow
questions (`paths_between` / `flows_to_call` / `flows_to_argument`), the addressing step behind
them (`resolve_callable` / `resolve_value`) and `describe`.

Names in, names out. No `can://` URIs, no ordinals — you say `"invoice_id"`, not `"…@formal_in:1"`.

| API | Answers | Example |
| --- | --- | --- |
| `slice_backward(src, within=, depth=5, max_nodes=)` | what affects this value | `py.slice_backward("invoice_id", within="PaymentPortal.invoice_transaction")` |
| `slice_forward(src, within=, depth=5, max_nodes=)` | what this value affects | `py.slice_forward("access_token", within="PaymentPortal.invoice_transaction")` |
| `paths_between(src, dst, src_within=, dst_within=, depth=None, max_paths=10)` | how one reaches the other | `py.paths_between("invoice_id", "invoice_ids", src_within="PaymentPortal.invoice_transaction", dst_within="PaymentPortal._process_transaction")` |
| `flows_to_call(src, callee, within=, depth=None)` | reaches **any call to** X | `py.flows_to_call("invoice_id", "_process_transaction", within="PaymentPortal.invoice_transaction")` |
| `flows_to_argument(src, callee, arg, within=, depth=None)` | reaches X's **named argument** | `py.flows_to_argument("invoice_id", "_process_transaction", arg="invoice_ids", within="…invoice_transaction")` |
| `reaches(src, dst, depth=None)` | is there a call path | `py.reaches("invoice_transaction", "AccountMove.write")` |
| `call_paths_between(src, dst, depth=None, max_paths=10)` | show the call chains | `py.call_paths_between("PaymentPortal.invoice_transaction", "AccountMove.write")` |
| `resolve_callable(name, in_class=, in_module=)` | what a name means, before asking | `py.resolve_callable("write", in_class="AccountMove").callable` |
| `resolve_value(name, within=)` | what a value name means | `py.resolve_value("AccessError", within="…invoice_transaction").defined_in` |
| `backward_cone(sinks, depth=5, max_nodes=)` | everything reaching these | `py.backward_cone(["AccountMove.write"])` |
| `callers_of(name, in_class=, in_module=)` | who calls this, by name | `py.callers_of("action_validate_step")` |
| `callees_of(name, in_class=, in_module=)` | what this calls, by name | |
| `get_cfg(callable, in_class=, page_size=, cursor=)` | control flow, one callable | `py.get_cfg("invoice_transaction", in_class="PaymentPortal")` |
| `get_cdg(callable, in_class=, page_size=, cursor=)` | control dependence | |
| `get_ddg(callable, in_class=, page_size=, cursor=)` | data dependence | |
| `describe(nodes)` | fill in `source` for chosen nodes | `py.describe([n for n in sl.nodes if n.kind == "call"])` |

**`flows_to_call` and `flows_to_argument` are different questions.** A tainted value can reach a
function without reaching the parameter that matters. Ask the one you mean.

**A slice is a set; a path is a sequence.** `slice_backward` answers "what is in scope"; a 10k-node
cone can contain millions of paths, so it never returns them. `paths_between` answers "how does A
reach B", with each hop carrying the edge that justified it — `hop.via` in your words (`data` /
`control` / `argument` / `return` / `summary`; `call` on a call path), `hop.var`, `hop.prov` — and
`path.weakest` is the hop that caps the claim: `ssa` > `reaching-defs` > `points-to`, the
most approximate one. Only **shortest** paths come back, at most `max_paths` (10), in one stated
order on both backends. `paths_between` takes **two** callables, `src_within=` and `dst_within=`,
both required: two values of one callable are joined only through recursion, so there is no
sensible default for the second. A path from a node to itself is refused with a `ValueError` that
tells you the `reaches` call to ask instead.

**Naming rules:**
- A value name is scoped by its callable: `within="PaymentPortal.invoice_transaction"`. Parameter
  names are always unique inside a callable, so once scoped there is no ambiguity.
- A callable name is *disambiguated*, not scoped: `in_class=` (a dotted suffix of the owning
  class), `in_module=` (a path — `"controllers/payment.py"` — **or** the dotted module name as it
  appears in every signature — `"controllers.payment"`, `"payment"`).
- When a keyword excludes every match, the error blames the keyword (`1 of 1 in_module not in
  graph: …`), not the name — `callers_of("x", in_module=…)` no longer claims `x` does not exist.
- Suffix matching works and is deterministic: `"execute"` matches anything ending `.execute`;
  `"cursor.execute"` narrows.
- **Ambiguity raises with candidates. Nothing is guessed.** 86% of names in a real application are
  unique; the rest are framework methods (`__init__`, `write`, `create` — 200+ each) where you must
  pass `in_class=`.

**The per-callable graphs return a page, not a list.** Naming a callable says *which* edges you
want, not *how many*: on a real application one callable's DDG is 1,386,918 edges — 27% of the whole
application's 5,134,655 (1.4.0 graph; 5,129,295 on 1.4.1) — while 15,520 of its 15,549 callables
have fewer than 10,000. So all three
return an `EdgePage`, and nothing is discarded to make it fit:

```python
page = py.get_ddg("configurator_apply", in_class="Website")
page.total          # 1386918 — the size of the whole answer, on the first page
len(page)           # 10000  — the default page_size; iterate the page to get the edges
page.complete       # False

while not page.complete:                                # the rest is reachable, not thrown away
    page = py.get_ddg("configurator_apply", in_class="Website", cursor=page.next_cursor)
```

**One completeness protocol, three shapes.** Every bounded result — `EdgePage`, `Slice`,
`FlowPaths` — carries **`complete: bool`**, serialised (`model_dump()` and JSON keep it), and
behaves as the list it holds: `for edge in page`, `len(slice)`, `paths[0]`, and `if paths:` is
`False` when empty. Each keeps the extra fields its bound needs (`total` / `next_cursor` on a page,
`total` / `roots` / `resolved` on a slice). There is no `has_more` and no `truncated`; one name for
one fact. `total` and `complete` are what make the bound non-silent: "this is everything" and
"there is more" are distinguishable from one page, and an empty page whose `total` is 0 still
means "this callable has no data dependence". `next_cursor` is opaque — pass it back, do not read
it. Edges come in one canonical order (source, target, then `kind` for CFG and `var`/`prov`
for DDG), identical on both backends, so page *n* is the same page whichever backend answered.
CFG and CDG page the same way even though they are small (402 and 314 edges at their largest) —
three sibling accessors with two different shapes is a trap for anything composing them.

**A slice is capped, not paged — and it says how much it left behind.** The sibling per-callable
graphs paginate; slices do not, and the difference is measured rather than stylistic. On a real
application a backward slice is either about **1** node or about **195,786** (the median over
seeds that have callers), and a forward slice reaches **440,270** at the 95th percentile — of
885,218 body nodes in the whole program. There is almost nothing in between, so "page 3 of 20"
answers no question anyone asked; and a slice *is* the traversal, so unlike an `EdgePage` cursor
(a keyset position in an order the database already keeps) every page would re-run the whole
closure. `Slice.total` is what keeps the bound from being silent:

```python
sl = py.slice_forward("kwargs", within="Website.configurator_apply", depth=None, max_nodes=10)
sl.total        # 440270 — the whole answer's size, in the same call
sl.complete     # False — derived from total and len(nodes), so the two cannot disagree
len(sl)         # 10
```

**Which is why `depth` defaults to 5, not to `None`.** That `depth=None` above is deliberate: with
no bound, a connected seed hands you 10,000 arbitrary nodes of a 195,819-node closure — honestly
flagged, and still an unprincipled 5% of a cone. A hop bound answers a *narrower* question
*completely* instead, and the measurement picked the number. Node counts over 120 random connected
`formal_in` seeds on a real application:

| depth | backward median / p75 / max | forward median / p75 / max | seeds over `max_nodes` |
| --- | --- | --- | --- |
| 3 | 14 / 70 / 846 | 12 / 34 / 440 | 0 |
| **5** | **33 / 188 / 1,539** | **24 / 63 / 1,053** | **0** |
| 6 | 56 / 324 / 2,818 | 35 / 166 / 14,260 | 1 |
| 8 | 464 / 2,044 / 16,028 | 48 / 402 / 37,326 | 3 |
| `None` | 195,786 / 195,787 / 198,306 | 71 / 440,269 / 440,645 | 131 |

5 is the last depth at which nothing measured needs `max_nodes` at all. So `complete` is normally
`True` and `total` is normally the size of what you got; when you want the whole closure, say
`depth=None` and read `total` before `nodes`. Anything in between is a legitimate question too —
`depth=` bounds the traversal, so what comes back is the *complete* slice of a smaller one.

Nodes come back ordered by `ref`, and the cap takes a prefix of that order, so the same call twice
gives the same subset. The traversal runs in the database over data dependence, control dependence,
argument passing, returns and call summaries at once — unbounded, 195,784 nodes reached, counted
and the first 10,000 described, in about 1.5s.

`backward_cone` is the same shape over the call graph, and takes the same five-hop default for
consistency rather than for necessity: cones are small — the largest measured is 9,346 callables,
under `max_nodes` — so `depth=None` on one is a cheap request. Its nodes are callables, its sinks
are in the result, and a sink nothing calls comes back as its own one-node cone rather than as an
empty answer you could not tell from a name that matched nothing. `sl.root` is the single seed for the
slices; a multi-sink cone has `sl.roots` and raises if you ask it for one.

**Three bound themselves by default; five do not, and the split is the whole point.** The
*slices* (`slice_backward`, `slice_forward`, `backward_cone`) default to `depth=5`: a bounded slice
is a **complete** answer to a narrower question, and `total` tells you so. The *predicates*
(`reaches`, `flows_to_call`, `flows_to_argument`) and the *path queries* (`paths_between`,
`call_paths_between`) default to `depth=None`, unbounded: a hop budget on a boolean or a path list
is not a smaller answer but a wrong one — "no flow" and "no flow within five hops" collapse into
the same `False` / `[]` with nothing in the result to tell them apart. Measured:
`flows_to_call("kwargs", "Website.create", within="Website.configurator_apply")` is `False` at five
hops and `True` unbounded; the matching `paths_between` is `[]` at five hops and ten paths at
eight. Unbounded is not a cost problem either — `reaches` over 200 random pairs: 20ms mean, 112ms
worst. `depth=` is still there on all five as a narrowing *you* chose and can see in your own call.

**`callees_of` includes calls out of the project.** They are 10% of the call edges on a real
application (38,585 of 370,110) and usually the ones you are looking for. An external comes back
`kind="external"` with a readable dotted name (`"odoo.exceptions.ValidationError.__init__"`) and
no position — `file` is `""` and `line` is `0`, because it was never analysed. `describe()` accepts
them and hands them back with `source=None` (no text exists), so `py.describe(py.callees_of(x))`
composes. `callers_of` is declared callables only: an unanalysed symbol has no body to have called
from — and neither `reaches`, `backward_cone` nor `call_paths_between` will route *through* one,
on either backend, even though the raw graph carries call edges out of ghosts.

**Reading code from a slice is a second call:**

```python
sl = py.slice_backward("invoice_id", within="PaymentPortal.invoice_transaction")
len(sl.nodes)     # 47 — decide what you can afford
sl.complete       # True — a cap did not fire
sl.resolved       # what "invoice_id" actually matched

for n in py.describe(sl.nodes):
    print(f"{n.file}:{n.line} {n.kind:<9} {n.name or ''}\n    {n.source}")
```

`SliceNode.kind` is `parameter | global | capture | argument | statement | call | return | branch |
loop | raise | handler | entry | exit | callable | external` — your vocabulary, not the schema's
`formal_in`/`actual_out`. An `argument` is named for the *callee's* parameter it binds, not for the
expression written at the call site; a `return` has no name at all. A `global` is a module global the callable
reads (84% of the values entering a callable on a real application are these); its `name` is the
identifier as written (`AccessError`) and `defined_in` is the module it comes from, so you can also
address it as `payment.AccessError` when one callable captures that name from several modules.
`ref` is an opaque handle; do not read it — the one thing you may do with it is hand it back to
`get_source()`, and that returns text only for a `kind="callable"` ref. `parameter` / `global` /
`capture` are dataflow vertices with no source span, so neither backend has text for one.

---

## Diagnostics

Any accessor may attach these. They exist so an empty result is never ambiguous.

| Code | Means |
| --- | --- |
| `module_scope` | the position is real, but outside any callable |
| `file_not_in_graph` | the file was not analysed — not "does not exist" |
| `module_source_unavailable` | this backend cannot supply module text (Neo4j) |
| `entrypoint_report_unavailable` | **you cannot tell whether an empty entrypoint list is real** |
| `level_too_low` | the graph lacks the analysis level this question needs — *unanswerable, not negative* |
| `graph_schema_mismatch` | analyzer/graph generation mismatch (raised, not attached) |
| — | a scoping keyword naming nothing raises `SelectorNotInGraph`; it is an error, not a diagnostic |
| `no_match`, `ambiguous`, `unknown_callable`, `unknown_param`, `did_you_mean` | declared in the `Diagnostic` code vocabulary, but **nothing emits them**: resolution failures are raised (`AmbiguousName` / `SelectorNotInGraph`), not attached, and `did_you_mean` in particular can never fire — E8 puts typo-tolerant matching out of scope in the error path as much as in the resolver |
| `unresolved_dispatch` | an edge the traversal could not follow |

**The rule behind all of them:** an empty result that could mean two things is a defect. When you
get nothing back, check the diagnostics before concluding the answer is "no".

---

## Cost summary

| Fast (< 1s) | Seconds |
| --- | --- |
| `get_callables_overview`, `get_artifacts`, `get_dependencies`, `get_config_keys`, `locate`, `locate_many`, `get_source`, `get_entrypoints`, `get_method_bodies`, **any scoped enumeration** (`paths=` / `module=` / `roots=`) | `get_classes` (10s), `get_symbol_table` (12s), `get_call_graph` (12s), `get_call_graph_json` (26s) |

Measured on a 970k-node graph (Odoo, 2,364 files). The whole-application four were minutes each
until the per-node fan-out was collapsed to one query per child collection; there is no
minutes-long accessor left. Scope with `paths=` / `module=` / `roots=` and they cost under a
second — a whole-application enumeration should be something you asked for, not the default shape.

`get_cfg` / `get_cdg` / `get_ddg` cost under a second on a normal callable. One page of 10,000 DDG
edges from the largest callable on that graph (1,386,918 edges) is ~3s, and stays ~3s at any depth:
paging resumes from a cursor rather than an offset, so reading the last page is not more expensive
than reading the first (measured end to end, median of three runs: 3.5s / 3.6s / 3.0s for the
first, middle and last page; the offset form would have cost 2.6s / 9.0s / 4.3s for the same
three).
