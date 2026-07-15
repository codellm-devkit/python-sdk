from cldk.models.cpg.models import Edge, Import


def test_call_edge_shape():
    e = Edge(**{"src": "can://p/a#f", "dst": "can://p/a#g", "prov": ["jedi", "pycg"], "weight": 2})
    assert e.src.endswith("#f") and e.dst.endswith("#g")
    assert e.prov == ["jedi", "pycg"] and e.weight == 2 and e.kind is None and e.var is None


def test_ddg_edge_carries_var_and_prov():
    e = Edge(**{"src": "a@1:0", "dst": "a@2:0", "var": "x", "prov": ["ssa"]})
    assert e.var == "x" and e.prov == ["ssa"] and e.weight == 1


def test_import_optional_fields():
    i = Import(**{"name": "os"})
    assert i.name == "os" and i.path is None and i.alias is None
