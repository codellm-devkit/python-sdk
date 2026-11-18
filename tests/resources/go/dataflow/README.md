# Go L3/L4 dataflow fixtures (python-sdk#436)

Two committed `cango` payloads, small enough to read, that between them exercise the whole
dataflow surface. The `.go.txt` files are the exact sources each was produced from — carried as
`.txt` so neither becomes part of this repo's own Go build, and so a reader can see what the
dataflow is *of* without cloning the analyzer.

| File | Produced by | What it is for |
|------|-------------|----------------|
| `httpsql_l4.json` | `cango -i . -a 4 --app-name httpsql --namespace httpsql` | The flow surface. The analyzer's own `testdata/dataflow/httpsql` fixture, whose HTTP→SQL path is gated analyzer-side by `TestGateL4_HTTPToSQLPath`. |
| `four_type_kinds.json` | `cango -i . -a 1 --app-name kinds --namespace kinds` | The parse blocker. One declaration of each of the four `GoType.kind` values. |

Regenerating either (after an analyzer release, say) is the command in the table, run from a
directory holding the matching `.go` file and a `go.mod`, with the output copied back here.
Produced against `cango 0.3.0`.

## Why `httpsql`

It is the smallest program that needs the *whole* L4 pipeline to answer one question. A handler
reads `r.URL.Query().Get("id")` and passes it to a `Repo` **interface** method; only VTA resolves
that call to `sqlRepo.Find`, which concatenates the value into `database/sql.DB.Query`'s `query`
argument. So a backend answering `flows_to_argument("id", "DB.Query", "query", …)` has to get
interface dispatch, interprocedural parameter binding *and* the library stand-in right — any one
of them missing yields `False`, and `False` is the answer that closes a live alert.

It also carries, without being contrived to:

- **a library stand-in as a sink** (`database/sql.DB.Query`), with a synthesized body a flow
  passes *through* rather than stopping at;
- **an ambiguity** — `Query` names both `sql.DB.Query` and `url.URL.Query`, so a bare name must
  raise rather than pick;
- **a sanitized sibling path** (`SanitizedHandler` → `SanitizedFind`, which parses the value with
  `strconv.Atoi` first), so a refutation can be measured against a real alternative route and not
  only against an absent one;
- **an unrelated pair** (`Count`/`user`, called only by `Audit`), so "no flow" can be checked
  where no flow exists.

## Why `four_type_kinds`

`GoType.kind` is a `Literal`, which `extra="ignore"` does not cover: a kind the SDK does not list
is a hard parse failure of the whole document, not a dropped field. The analyzer emits four
(`internal/syntactic_analysis/symbol_table.go`), and the SDK listed three — so real `-a 1` output
from any project containing `type Alias = int` did not load at all. One declaration of each kind
is the cheapest guard against that recurring, and at `-a 1` the payload is ~3 KB.
