from cldk.models.cpg.models import Node


def test_class_node_facet():
    n = Node(**{"id": "can://p/m.py/C", "kind": "class",
                "callables": {"C.f()": {"id": "can://p/m.py/C/f()", "kind": "method", "signature": "f"}}})
    assert n.kind == "class"
    assert n.callables["C.f()"].kind == "method" and n.callables["C.f()"].signature == "f"


def test_callable_node_carries_body_and_edges():
    n = Node(**{"id": "can://p/m.py/f()", "kind": "function", "signature": "f()",
                "body": {"f@1:0": {"id": "can://p/m.py/f()@1:0", "kind": "statement"}},
                "cfg": [{"src": "can://p/m.py/f()@1:0", "dst": "can://p/m.py/f()@2:0", "kind": "fallthrough"}],
                "ddg": [{"src": "can://p/m.py/f()@1:0", "dst": "can://p/m.py/f()@2:0", "var": "x", "prov": ["ssa"]}]})
    assert set(n.body) == {"f@1:0"}
    assert n.cfg[0].kind == "fallthrough" and n.ddg[0].var == "x" and n.ddg[0].prov == ["ssa"]


def test_call_body_node_callee_refines_from_null():
    n = Node(**{"id": "a@2:0", "kind": "call", "callee": None, "arguments": ["a@2:0/arg0"]})
    assert n.kind == "call" and n.callee is None and n.arguments == ["a@2:0/arg0"]


def test_language_extra_field_preserved():
    n = Node(**{"id": "x", "kind": "class", "is_abstract": True})   # a language-specific flag
    assert n.model_extra.get("is_abstract") is True
