from cldk.models.cpg import AnalysisPayload, Application, Module, Node, Edge, Span, Import, Analyzer


def test_envelope_reads_authoritative_level():
    p = AnalysisPayload(**{
        "schema_version": "2.0.0", "language": "python", "max_level": 4, "k_limit": 3,
        "analyzer": {"name": "codeanalyzer-python", "version": "0.4.0"},
        "application": {"id": "can://python/app", "kind": "application", "symbol_table": {}},
    })
    assert p.schema_version == "2.0.0" and p.max_level == 4 and p.k_limit == 3
    assert p.analyzer.name == "codeanalyzer-python"
    assert p.application.id == "can://python/app"


def test_module_holds_source_and_containment():
    m = Module(**{"id": "can://python/app/m.py", "kind": "module", "source": "x = 1\n",
                  "types": {"C": {"id": "can://python/app/m.py/C", "kind": "class"}},
                  "functions": {"f()": {"id": "can://python/app/m.py/f()", "kind": "function"}}})
    assert m.source == "x = 1\n"
    assert m.types["C"].kind == "class" and m.functions["f()"].kind == "function"


def test_application_edge_lists():
    a = Application(**{"id": "can://python/app", "kind": "application", "symbol_table": {},
                       "call_graph": [{"src": "a", "dst": "b", "prov": ["jedi"], "weight": 1}],
                       "param_in": [{"src": "c@in", "dst": "d@in"}]})
    assert a.call_graph[0].dst == "b" and a.param_in[0].src == "c@in" and a.param_out == []
