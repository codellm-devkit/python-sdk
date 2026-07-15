"""The models must parse REAL, conformant analysis.json from BOTH analyzers at L1 and L4, and the
L1 tree must be a subset of the L4 tree (additive-levels invariant)."""
import json
from pathlib import Path
import pytest
from cldk.models.cpg import AnalysisPayload, Span

RES = Path(__file__).parent.parent.parent / "resources" / "cpg"


def _load(name):
    return AnalysisPayload(**json.loads((RES / name).read_text()))


@pytest.mark.parametrize("name,lang,level", [
    ("py-a1.json", "python", 1), ("py-a4.json", "python", 4),
    ("ts-a1.json", "typescript", 1), ("ts-a4.json", "typescript", 4),
])
def test_real_sample_parses(name, lang, level):
    p = _load(name)
    assert p.schema_version == "2.0.0" and p.language == lang and p.max_level == level
    assert p.application.symbol_table                      # non-empty tree
    # every call-graph edge is identity-only src/dst (no dangling shape)
    for e in p.application.call_graph:
        assert e.src and e.dst


def _keys(obj, prefix=""):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(prefix + str(k)); out |= _keys(v, prefix + str(k) + "/")
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys(v, prefix + "[]/")
    return out


@pytest.mark.parametrize("lo,hi", [("py-a1.json", "py-a4.json"), ("ts-a1.json", "ts-a4.json")])
def test_l1_subset_of_l4(lo, hi):
    lo_t = json.loads((RES / lo).read_text())["application"]["symbol_table"]
    hi_t = json.loads((RES / hi).read_text())["application"]["symbol_table"]
    assert not (_keys(lo_t) - _keys(hi_t)), "L1 tree keys must be a subset of L4"


def test_module_span_parses_on_typescript_sample():
    # span is a common field per the keystone (Part II), module included; ts-a4 emits it on the
    # module node — it must parse into Span, not fall through to model_extra as a raw dict.
    p = _load("ts-a4.json")
    mod = next(iter(p.application.symbol_table.values()))
    assert isinstance(mod.span, Span)
    assert mod.span.bytes == (0, 254)
    assert len(mod.span.bytes) == 2 and all(isinstance(b, int) for b in mod.span.bytes)
