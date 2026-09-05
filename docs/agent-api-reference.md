# CLDK Python API — agent reference

What you can ask a CLDK Python analysis, what comes back, and what will mislead you if you don't
know it. Written for an agent composing queries at runtime.

Status: leg 1 (`codeanalyzer-python` 1.4.0). Accessors marked **1.5** are specified but not yet
implemented — see `docs/design/specs/2026-09-05-leg-1.5-bounded-queries-and-dataflow.md`.

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

---

## The four moves

Most questions decompose into these. Start here, then use the tables below.

| You have | You want | Call |
| --- | --- | --- |
| a scanner alert at `file:line` | the enclosing callable and its code | `locate(path, line)` |
| a callable name | its source | `get_method_bodies([sig])` or `get_source(node_id)` |
| a callable | who calls it / what it calls | `get_callers(...)` / `get_callees(...)` |
| a value and a sink | does one reach the other | `flows_to_call(...)` **1.5** |
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
| `get_symbol_table()` | `Dict[str, PyModule]` | **382s** ⚠ | `py.get_symbol_table()` |
| `get_classes()` | `Dict[str, PyClass]` | **348s** ⚠ | `py.get_classes()` |
| `get_modules()` | `List[PyModule]` | ⚠ | `py.get_modules()` |
| `get_application_view()` | `PyApplication` | ⚠ | `py.get_application_view()` |
| `get_imports()` | `Dict[str, List]` | | `py.get_imports()` |

**Prefer the projection.** `PyCallableOverview` carries what you usually need and costs a single
query; the full reconstruction costs minutes on a real application (measured on Odoo: 1,626
modules, 73,669 round trips). Leg 1.5 collapses this — until then, reach for overviews.

```python
class PyCallableOverview:
    signature: str          # "addons.onboarding.models.step.OnboardingStep.action_validate"
    name: str               # "action_validate"
    class_signature: str | None
    kind: str               # "method" | "function"
    path: str               # ⚠ currently ABSOLUTE; repo-relative in 1.5
    start_line: int
    end_line: int
    decorators: list[str]
```

**Gotcha:** `PyCallableOverview.path` is an absolute path carrying the *analysis machine's*
filesystem layout, while `locate().module.path` and `PyModule` keys are repo-relative. They do
not join today. Fixed in 1.5 (issue #320 covers the sibling id problem).

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
| `has_resolution_edges()` | `bool` | | can this graph resolve callees at all |
| `get_call_graph()` | `nx.DiGraph` | 12s, **364,752 edges** | rarely what you want whole |
| `get_call_graph_json()` | `str` | **422s** ⚠ | |

**Node keys are signatures**, except out-of-project targets, which keep their `@external` id:

```
addons.account_payment.controllers.payment.PaymentPortal.invoice_transaction
can://python/odoo-slim-19/@external/logging.Logger/info
```

**Gotcha:** call the whole call graph only if you truly need it. 364,752 edges is not an answer to
a question about one function. Leg 1.5 adds `get_call_graph(roots=…, depth=…)`.

**Gotcha:** `PyCallsite.callee_signature` may be `None`. Check `has_resolution_edges()` first — if
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
checkout it detects **zero** entrypoints across 15,549 callables, in a framework built entirely
from HTTP routes.

So an empty `get_entrypoints()` means either "no entrypoints" or "the pass found nothing", and you
cannot tell from the list. `get_entrypoint_coverage()` is how you ask. Over Neo4j it reports
`entrypoint_report_unavailable` — the graph does not carry the report — which is itself the answer:
*you cannot trust the zero*.

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

## Dataflow — **leg 1.5, not yet implemented**

Names in, names out. No `can://` URIs, no ordinals — you say `"invoice_id"`, not `"…@formal_in:1"`.

| API | Answers | Example |
| --- | --- | --- |
| `slice_backward(src, within=, depth=, max_nodes=)` | what affects this value | `py.slice_backward("invoice_id", within="PaymentPortal.invoice_transaction")` |
| `slice_forward(src, within=)` | what this value affects | `py.slice_forward("access_token", within="PaymentPortal.invoice_transaction")` |
| `paths_between(src, dst, within=, max_paths=)` | how one reaches the other | `py.paths_between("invoice_id", "kwargs", within="…invoice_transaction")` |
| `flows_to_call(src, callee, within=)` | reaches **any call to** X | `py.flows_to_call("invoice_id", "execute", within="…invoice_transaction")` |
| `flows_to_argument(src, callee, arg, within=)` | reaches X's **named argument** | `py.flows_to_argument("invoice_id", "execute", arg="query", within="…")` |
| `reaches(src, dst)` | is there a call path | `py.reaches("invoice_transaction", "execute")` |
| `call_paths_between(src, dst, max_paths=)` | show the call chains | |
| `backward_cone(sinks=[…])` | everything reaching these | `py.backward_cone(sinks=["execute"])` |
| `callers_of(name, in_class=, in_module=)` | who calls this, by name | `py.callers_of("action_validate_step")` |
| `callees_of(name, in_class=, in_module=)` | what this calls, by name | |
| `get_cfg(callable, in_class=)` | control flow, one callable | `py.get_cfg("invoice_transaction", in_class="PaymentPortal")` |
| `get_cdg(callable, in_class=)` | control dependence | |
| `get_ddg(callable, in_class=)` | data dependence | |
| `describe(nodes)` | fill in `source` for chosen nodes | `py.describe([n for n in sl.nodes if n.kind == "call"])` |

**`flows_to_call` and `flows_to_argument` are different questions.** A tainted value can reach a
function without reaching the parameter that matters. Ask the one you mean.

**A slice is a set; a path is a sequence.** `slice_backward` answers "what is in scope"; a 10k-node
cone can contain millions of paths, so it never returns them. `paths_between` answers "how does A
reach B", with each hop carrying the edge that justified it.

**Naming rules:**
- A value name is scoped by its callable: `within="PaymentPortal.invoice_transaction"`. Parameter
  names are always unique inside a callable, so once scoped there is no ambiguity.
- A callable name is *disambiguated*, not scoped: `in_class=`, `in_module=`.
- Suffix matching works and is deterministic: `"execute"` matches anything ending `.execute`;
  `"cursor.execute"` narrows.
- **Ambiguity raises with candidates. Nothing is guessed.** 86% of names in a real application are
  unique; the rest are framework methods (`__init__`, `write`, `create` — 200+ each) where you must
  pass `in_class=`.

**Reading code from a slice is a second call:**

```python
sl = py.slice_backward("invoice_id", within="PaymentPortal.invoice_transaction")
len(sl.nodes)     # 47 — decide what you can afford
sl.truncated      # False — a cap did not fire
sl.resolved       # what "invoice_id" actually matched

for n in py.describe(sl.nodes):
    print(f"{n.file}:{n.line} {n.kind:<9} {n.name or ''}\n    {n.source}")
```

`SliceNode.kind` is `parameter | argument | statement | call | return` — your vocabulary, not the
schema's `formal_in`/`actual_out`. `ref` is an opaque handle; do not read it.

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
| `no_match`, `ambiguous`, `unknown_callable`, `unknown_param`, `did_you_mean` | resolution failures |
| `unresolved_dispatch` | an edge the traversal could not follow |

**The rule behind all of them:** an empty result that could mean two things is a defect. When you
get nothing back, check the diagnostics before concluding the answer is "no".

---

## Cost summary

| Fast (< 1s) | Slow (minutes) ⚠ |
| --- | --- |
| `get_callables_overview`, `get_artifacts`, `get_dependencies`, `get_config_keys`, `locate`, `locate_many`, `get_source`, `get_entrypoints`, `get_method_bodies` | `get_symbol_table` (382s), `get_classes` (348s), `get_call_graph_json` (422s) |

Measured on a 970k-node graph (Odoo, 2,364 files). The slow three are N+1 fan-outs that leg 1.5
collapses; until then, prefer projections and scoped queries.
