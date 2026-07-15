"""Pin the accessors F7 (Task-7 provider) and F3 (views) depend on, against a real L4 sample."""
import json
from pathlib import Path
from cldk.models.cpg import AnalysisPayload

RES = Path(__file__).parent.parent.parent / "resources" / "cpg"


def _app(name):
    return AnalysisPayload(**json.loads((RES / name).read_text())).application


def test_symbol_table_module_source_and_containment():
    app = _app("py-a4.json")
    mod = app.symbol_table["pkg/mod.py"]
    assert isinstance(mod.source, str) and mod.source          # module.source (byte-slice base)
    assert isinstance(mod.types, dict) and isinstance(mod.functions, dict)   # both accessors pinned
    assert mod.types or mod.functions                          # at least one populated


def test_callable_body_and_dataflow_edges_present_at_l4():
    app = _app("py-a4.json")
    mod = app.symbol_table["pkg/mod.py"]
    cls = next(iter(mod.types.values()))
    call = next(iter(cls.callables.values()))
    assert call.signature                                      # callable.signature
    assert call.body                                           # body{} populated at L4
    assert call.cfg and call.ddg                               # cfg/ddg edge lists
    # cdg/summary are unpinned elsewhere and are the sole extra="allow" guard for these two
    # fields (both in F7's cfg/cdg/ddg/summary read set) — dereference an element attribute so a
    # deleted field (which would fall back to a raw dict under extra="allow") fails loudly.
    assert isinstance(call.cdg, list) and isinstance(call.summary, list)
    assert call.cdg[0].src and call.summary[0].src
    # span.bytes present for slicing a body node
    some = next(iter(call.body.values()))
    assert some.span is None or (some.span.bytes and len(some.span.bytes) == 2)


def test_envelope_k_limit_at_l4():
    payload = AnalysisPayload(**json.loads((RES / "py-a4.json").read_text()))
    assert payload.k_limit == 3
    # a plain value check alone would still pass via the extra="allow" passthrough even if
    # k_limit were deleted from the model — assert it's a declared field, not an extras leak.
    assert "k_limit" not in (payload.model_extra or {})


def test_application_interprocedural_edges_at_l4():
    app = _app("py-a4.json")
    assert app.call_graph                                      # L2 call graph
    assert app.param_in and app.param_out                     # L4 SDG param edges
    for e in app.call_graph:
        assert e.src.startswith("can://") and e.dst.startswith("can://")   # can:// identity


def test_typescript_sample_same_accessors():
    app = _app("ts-a4.json")
    mod = next(iter(app.symbol_table.values()))
    assert isinstance(mod.source, str) and mod.source
    # a TS type node with callables
    typ = next((t for t in mod.types.values() if t.callables), None)
    assert typ is not None and next(iter(typ.callables.values())).signature is not None
    # resetPassword under type Users: body/cfg must resolve to parsed Node/Edge, not raw dicts,
    # so the TS analyzer path isn't pinned on source/signature alone.
    call = typ.callables["resetPassword"]
    assert isinstance(call.body, dict) and next(iter(call.body.values())).kind
    assert isinstance(call.cfg, list) and call.cfg[0].kind
