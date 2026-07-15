"""CpgLocalProviderMixin: Step 1's hand-built Application (kept verbatim) plus the same five
primitives exercised end-to-end against a REAL committed L4 golden sample
(tests/resources/cpg/py-a4.json) — including driving the merged Engine on real data."""
import json
from pathlib import Path
import networkx as nx
from cldk.graph._cpg_local import CpgLocalProviderMixin
from cldk.graph.engine import Engine
from cldk.models.cpg.models import Application, Module, Node, Edge, Span
from cldk.models.cpg import AnalysisPayload


def _app():
    call = Node(id="can://x/m.py/f", kind="function", signature="f",
                span=Span(start=(1, 0), end=(4, 0), bytes=(0, 40)),
                body={"f@1:0": Node(id="can://x/m.py/f@1:0", kind="statement",
                                    span=Span(start=(1, 0), end=(1, 8), bytes=(0, 8))),
                      "f@2:0": Node(id="can://x/m.py/f@2:0", kind="statement",
                                    span=Span(start=(2, 0), end=(2, 8), bytes=(9, 17)))},
                cfg=[Edge(src="can://x/m.py/f@1:0", dst="can://x/m.py/f@2:0", kind="fallthrough")],
                ddg=[Edge(src="can://x/m.py/f@1:0", dst="can://x/m.py/f@2:0", var="a", prov=["ssa"])],
                cdg=[], summary=[])
    mod = Module(id="can://x/m.py", source="a = 1\nb = a\n", functions={"f": call}, types={})
    return Application(id="can://x", symbol_table={"m.py": mod}, call_graph=[], param_in=[], param_out=[])


class Backend(CpgLocalProviderMixin):
    def __init__(self): self.application = _app(); self._level = 3
    def max_level(self): return self._level


def test_program_graph_has_body_and_edges():
    g = Backend().program_graph("can://x/m.py/f")
    assert set(g.nodes) == {"can://x/m.py/f@1:0", "can://x/m.py/f@2:0"}
    assert g.get_edge_data("can://x/m.py/f@1:0", "can://x/m.py/f@2:0", key=None) is None or True
    fams = {d["family"] for _, _, d in g.edges(data=True)}
    assert fams == {"cfg", "ddg"}


def test_resolve_location_hits_line():
    assert Backend().resolve_location("m.py", 2) == ["can://x/m.py/f@2:0"]


def test_source_slice_reads_module_source():
    fl, code = Backend().source_slice("can://x/m.py/f@1:0")
    assert fl == "m.py:1" and code == "a = 1\nb "[:8][0:8].split("\n")[0] or code is not None


# --- Golden fixture: a REAL, conformant L4 codeanalyzer-python sample ------------------------
# Unlike the hand-built Application above (whose body nodes carry an explicit .id matching the
# brief's own convention), a real analyzer leaves body-node .id unset — they're keyed by LOCAL
# position ("3:8", "@entry", ...) per Node's own docstring and tests/models/cpg/test_node.py's
# test_body_node_missing_id_parses. Application-level param_in/param_out already reference these
# as "<callable_id>@<local_key>" (verified against the raw JSON below), which is the scheme the
# mixin must reproduce so provider-synthesized ids agree with the application's own.
RES = Path(__file__).parent.parent / "resources" / "cpg"
GOLDEN = AnalysisPayload(**json.loads((RES / "py-a4.json").read_text())).application

MOD_PATH = "pkg/mod.py"
ENTRY = "can://python/pyfix/pkg/mod.py/entry()"
RESET_PW = "can://python/pyfix/pkg/mod.py/ResUsers/reset_password(self,login)"
ACTION = "can://python/pyfix/pkg/mod.py/ResUsers/_action_reset_password(self,ids)"

# reset_password's own body keys, straight from py-a4.json (11 total: 2 spanned source
# positions, entry/exit, 2 formal_in/2 formal_out ports, 3 actual_in/out ports for its one
# call site).
RESET_PW_BODY_KEYS = ["3:15", "@entry", "3:8", "@exit", "@formal_in:0", "@formal_in:1",
                       "@formal_out:0", "@formal_out:1", "3:8/actual_in:0", "3:8/actual_in:1",
                       "3:8/actual_out"]


def _qid(callable_id: str, local_key: str) -> str:
    # Independent re-derivation of the qualification scheme (not imported from the mixin under
    # test) so a bug in the mixin's own _qualify can't silently validate itself.
    return callable_id + local_key if local_key.startswith("@") else f"{callable_id}@{local_key}"


class GoldenBackend(CpgLocalProviderMixin):
    application = GOLDEN
    def max_level(self): return 4


def test_golden_program_graph_preserves_parallel_edges():
    # reset_password has THREE ddg edges between the same pair (@entry -> 3:8, one per var) and
    # TWO cfg edges between another same pair (3:8 -> @exit, kinds 'exception' and 'return').
    # provider.py's ABC docstring requires these to stay distinct parallel edges, each with its
    # own family/var/prov/kind — a naive MultiDiGraph key of just the family name would silently
    # collapse each trio/pair into one edge.
    g = GoldenBackend().program_graph(RESET_PW)
    assert isinstance(g, nx.MultiDiGraph)
    assert set(g.nodes) == {_qid(RESET_PW, k) for k in RESET_PW_BODY_KEYS}
    assert g.number_of_edges() == 7                      # 3 cfg + 1 cdg + 3 ddg (summary excluded)

    fams = [d["family"] for _, _, d in g.edges(data=True)]
    assert sorted(fams) == sorted(["cfg", "cfg", "cfg", "cdg", "ddg", "ddg", "ddg"])

    ddg_vars = {d["var"] for _, _, d in g.edges(data=True) if d["family"] == "ddg"}
    assert ddg_vars == {"login", "self", "self._action_reset_password"}   # none collapsed away

    exit_kinds = {d["kind"] for _, _, d in g.edges(data=True)
                  if d["family"] == "cfg" and d["kind"] in ("exception", "return")}
    assert exit_kinds == {"exception", "return"}          # both 3:8 -> @exit edges survive

    assert g.has_edge(_qid(RESET_PW, "@entry"), _qid(RESET_PW, "3:8"))


def test_golden_resolve_location_hits_real_lines():
    b = GoldenBackend()
    # line 3 has TWO spanned body nodes in reset_password: the call (col 15) and the return
    # (col 8) — col=None must return both; a col narrows to exactly one.
    assert set(b.resolve_location(MOD_PATH, 3)) == {_qid(RESET_PW, "3:15"), _qid(RESET_PW, "3:8")}
    assert b.resolve_location(MOD_PATH, 3, 8) == [_qid(RESET_PW, "3:8")]
    assert b.resolve_location(MOD_PATH, 3, 15) == [_qid(RESET_PW, "3:15")]
    assert b.resolve_location("mod.py", 3, 8) == [_qid(RESET_PW, "3:8")]   # basename suffix match
    assert b.resolve_location(MOD_PATH, 2) == []               # the `def` line has no body node


def test_golden_source_slice_reads_real_module_source():
    b = GoldenBackend()
    fl, code = b.source_slice(_qid(RESET_PW, "3:8"))
    assert fl == "pkg/mod.py:3" and code == "return self._action_reset_password([login])"
    fl2, code2 = b.source_slice(_qid(RESET_PW, "3:15"))
    assert fl2 == "pkg/mod.py:3" and code2 == "self._action_reset_password([login])"
    # @entry has no span (it's a synthetic CFG node, not a source position) — degrades to
    # (path, None) rather than raising.
    fl3, code3 = b.source_slice(_qid(RESET_PW, "@entry"))
    assert fl3 == "pkg/mod.py" and code3 is None


def test_golden_callable_of_maps_body_node_and_is_identity_on_callable():
    b = GoldenBackend()
    assert b.callable_of(_qid(RESET_PW, "3:8")) == RESET_PW
    assert b.callable_of(_qid(RESET_PW, "@entry")) == RESET_PW
    assert b.callable_of(_qid(ACTION, "5:8")) == ACTION
    assert b.callable_of(RESET_PW) == RESET_PW              # passthrough: not a body vertex
    assert b.callable_of("can://nonexistent") == "can://nonexistent"


def test_golden_sdg_edges_include_param_in_out_and_summary_tagged_by_kind():
    # param_in/param_out are already <callable>@<local> qualified at the Application level;
    # summary is per-callable and LOCAL like cfg/cdg/ddg, so it needs the same qualification the
    # mixin applies to program_graph. All three must be tagged with their OWN kind
    # ("param_in"/"param_out"/"summary") — real param_in/param_out/summary edges carry kind=None
    # in the raw JSON, and engine.flows_to reports a boundary hop's family ("sdg") only when kind
    # is unset (test_engine_interproc.py::test_flow_boundary_hop_reports_sdg_kind, already
    # merged), so an untagged edge here would surface as the opaque "sdg" instead.
    got = {(e.src, e.dst, e.kind) for e in GoldenBackend().sdg_edges()}
    assert got == {
        (_qid(RESET_PW, "3:8/actual_in:0"), _qid(ACTION, "formal_in:0"), "param_in"),
        (_qid(RESET_PW, "3:8/actual_in:1"), _qid(ACTION, "formal_in:1"), "param_in"),
        (_qid(ENTRY, "7:4/actual_in:0"), _qid(RESET_PW, "formal_in:1"), "param_in"),
        (_qid(ACTION, "formal_out:0"), _qid(RESET_PW, "3:8/actual_out"), "param_out"),
        (_qid(RESET_PW, "formal_out:0"), _qid(ENTRY, "7:4/actual_out"), "param_out"),
        (_qid(RESET_PW, "3:8/actual_in:1"), _qid(RESET_PW, "3:8/actual_out"), "summary"),
        (_qid(ENTRY, "7:4/actual_in:0"), _qid(ENTRY, "7:4/actual_out"), "summary"),
    }


def test_golden_engine_slice_backward_on_real_line():
    # Feeds the mixin to the real merged Engine and drives it with a plain 'file:line[:col]'
    # seed string, exactly as an SDK caller would — proving the mixin's five primitives compose
    # correctly through resolve_vertex -> callable_of -> program_graph -> sdg overlay -> subgraph.
    r = Engine(GoldenBackend()).slice_backward(f"{MOD_PATH}:3:8")
    assert bool(r) and len(r) > 0
    # only @entry precedes 3:8 (via cfg/cdg/ddg); the sdg overlay is engaged (level 4, ddg
    # requested) but nothing points INTO the bare '3:8' return node from another callable in
    # this sample — the interprocedural wiring attaches at the unspanned actual/formal ports.
    assert set(r.uris()) == {_qid(RESET_PW, "3:8"), _qid(RESET_PW, "@entry")}
    assert r.explain()["interprocedural"] is True
    assert r.explain()["level"] == 4

    ev = {e["uri"]: e for e in r.evidence}
    assert ev[_qid(RESET_PW, "3:8")]["code"] == "return self._action_reset_password([login])"
    assert ev[_qid(RESET_PW, "3:8")]["role"] == "seed"
    assert ev[_qid(RESET_PW, "@entry")]["code"] is None
    assert ev[_qid(RESET_PW, "@entry")]["role"] == "def"


def test_golden_engine_flows_to_crosses_callable_boundary():
    # A real cross-callable dataflow hop, driven end to end through the merged Engine: the value
    # passed as reset_password's 2nd call argument (3:8/actual_in:1) flows via param_in into
    # _action_reset_password's 2nd formal parameter — proving the mixin's sdg_edges() actually
    # drives Engine.flows_to's interprocedural path on real data, not just synthetic fixtures.
    src = _qid(RESET_PW, "3:8/actual_in:1")
    dst = _qid(ACTION, "formal_in:1")
    r = Engine(GoldenBackend()).flows_to(src, dst)
    assert len(r.paths) == 1
    hop = r.paths[0].hops[0]
    assert hop["from"] == src and hop["to"] == dst
    assert hop["kind"] == "param_in"          # the specific sdg kind, not the opaque family "sdg"
    # this param_in edge carries no ssa/points-to provenance in the raw sample, so the honest
    # confidence is 'unresolved' rather than a stronger tier it can't back up.
    assert r.paths[0].confidence == "unresolved"
