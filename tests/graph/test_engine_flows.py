# tests/graph/test_engine_flows.py
from cldk.graph.engine import Engine
from tests.graph.test_engine_slice import OneCallableProvider


def test_flows_to_finds_witness_with_min_confidence():
    e = Engine(OneCallableProvider())
    r = e.flows_to("m:1", "m:3")  # s1 -> s2 (ssa) -> s3 (points-to); min = structural
    assert bool(r) is True
    assert len(r.paths) == 1
    assert [h["from"] for h in r.paths[0].hops] == ["c@1:0", "c@2:0"]
    assert r.paths[0].confidence == "structural"


def test_flows_to_no_path_is_falsy():
    e = Engine(OneCallableProvider())
    r = e.flows_to("m:3", "m:1")  # no forward ddg path s3 -> s1
    assert not r.paths


def test_def_use_returns_downstream_uses():
    e = Engine(OneCallableProvider())
    r = e.def_use("m:1")  # def at s1 flows to s2, s3
    assert set(r.uris()) == {"c@1:0", "c@2:0", "c@3:0"}
