from unittest.mock import MagicMock

import networkx as nx

from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.graph import SliceResult, FlowResult


def _facade_with_backend(backend):
    pa = PythonAnalysis.__new__(PythonAnalysis)
    pa.backend = backend
    return pa


def _mock_backend():
    b = MagicMock()
    b.max_level.return_value = 3
    b.callable_of.side_effect = lambda u: "c"
    g = nx.MultiDiGraph()
    # Edge direction: def→use; the definition at c@1:0 flows into the use at c@2:0.
    # A backward slice from the use (c@2:0) walks incoming edges to reach the def (c@1:0).
    g.add_edge("c@1:0", "c@2:0", family="ddg", prov=["ssa"], var="x", kind=None)
    b.program_graph.return_value = g
    b.sdg_edges.return_value = []
    b.resolve_location.return_value = ["c@2:0"]
    b.source_slice.side_effect = lambda u: (u, u)
    return b


def test_slice_backward_delegates():
    r = _facade_with_backend(_mock_backend()).slice_backward("m.py:2", edges=("ddg",))
    assert isinstance(r, SliceResult)
    assert set(r.uris()) == {"c@2:0", "c@1:0"}


def test_flows_to_delegates():
    r = _facade_with_backend(_mock_backend()).flows_to("c@1:0", "c@2:0")
    assert isinstance(r, FlowResult)


def test_all_five_verbs_exist():
    for verb in ("slice_backward", "slice_forward", "flows_to", "def_use", "control_deps"):
        assert callable(getattr(PythonAnalysis, verb, None)), verb


def test_public_exports():
    import cldk.graph as gr
    for name in ("Engine", "SliceResult", "FlowResult", "FlowPath", "CapabilityError"):
        assert hasattr(gr, name), name
