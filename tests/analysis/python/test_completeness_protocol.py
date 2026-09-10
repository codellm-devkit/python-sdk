################################################################################
# Copyright IBM Corporation 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""One probe table over every bounded result type, so the completeness protocol cannot drift.

``EdgePage``, ``Slice`` and ``FlowPaths`` are three shapes for one fact -- "was that everything?"
-- and before this they spelled it three ways (``has_more``, ``truncated``, a plain attribute
``json.dumps`` dropped), iterated as pydantic field tuples, and were truthy when empty. Every row
below runs against all three, empty and non-empty, so adding a fourth shape or changing one of
these means adding a row here rather than discovering the divergence in a caller.
"""

from __future__ import annotations

import json

import pytest

from cldk.analysis.commons.results import BoundedResult, EdgePage, FlowPath, FlowPaths, PathHop, Slice, SliceNode
from cldk.models.python import CfgEdge

_NODE = SliceNode(file="m.py", line=1, callable="m.f", kind="statement", name=None, ref="can://x/m.py/f()@1:0")
_HOP = PathHop(frm=_NODE, to=_NODE.model_copy(update={"ref": "can://x/m.py/f()@2:0"}), via="data", var="x", prov=["ssa"])
_EDGE = CfgEdge(src="a", dst="b", kind="fallthrough")


def _cases():
    """``(label, result, its items, whether it claims to be complete)`` for every shape, both ways."""
    return [
        ("page/empty", EdgePage[CfgEdge](edges=[], total=0), [], True),
        ("page/whole", EdgePage[CfgEdge](edges=[_EDGE], total=1), [_EDGE], True),
        ("page/partial", EdgePage[CfgEdge](edges=[_EDGE], total=2, next_cursor="c"), [_EDGE], False),
        ("slice/whole", Slice(nodes=[_NODE], roots=[_NODE], resolved="m.f", total=1), [_NODE], True),
        ("slice/capped", Slice(nodes=[_NODE], roots=[_NODE], resolved="m.f", total=9, ), [_NODE], False),
        ("paths/empty", FlowPaths(paths=[], complete=True), [], True),
        ("paths/whole", FlowPaths(paths=[FlowPath(hops=[_HOP])], complete=True), [FlowPath(hops=[_HOP])], True),
        ("paths/capped", FlowPaths(paths=[FlowPath(hops=[_HOP])], complete=False), [FlowPath(hops=[_HOP])], False),
    ]


@pytest.mark.parametrize("label,result,items,complete", _cases(), ids=[c[0] for c in _cases()])
def test_every_bounded_result_speaks_one_protocol(label, result, items, complete):
    assert isinstance(result, BoundedResult)
    # the fact, as an attribute and as data -- serialised, so it survives a JSON round trip
    assert result.complete is complete
    dumped = result.model_dump()
    assert dumped["complete"] is complete
    assert json.loads(json.dumps(dumped))["complete"] is complete
    # list behaviour over the payload, not over pydantic's field tuples
    assert list(result) == items
    assert len(result) == len(items)
    assert bool(result) is bool(items), "an empty result is falsy"
    if items:
        assert result[0] == items[0]


def test_an_empty_slice_cannot_be_constructed_incomplete():
    """``Slice.complete`` is derived: nothing reached and ``total`` 0 is whole by construction, so
    there is no way to hand a caller an empty slice that claims there was more."""
    assert Slice(nodes=[], roots=[], resolved="", total=0).complete is True


def test_complete_is_in_the_serialisation_schema_of_every_shape():
    for model in (EdgePage[CfgEdge], Slice, FlowPaths):
        assert "complete" in model.model_json_schema(mode="serialization")["properties"], model


def test_the_old_spellings_are_gone():
    """One name for one fact -- ``has_more`` and ``truncated`` are not kept as aliases."""
    for model in (EdgePage[CfgEdge], Slice, FlowPaths):
        assert not hasattr(model, "has_more") and not hasattr(model, "truncated"), model


def test_weakest_is_recoverable_from_a_dumped_path():
    """``FlowPath.weakest`` stays a property and is not serialised; ``model_dump`` carries every
    hop's ``prov``, from which it is recomputed."""
    from cldk.analysis.commons.results import prov_rank

    path = FlowPath(hops=[_HOP, _HOP.model_copy(update={"prov": ["points-to"]})])
    dumped = path.model_dump()
    assert "weakest" not in dumped
    assert min(dumped["hops"], key=lambda h: prov_rank(h["prov"])) == path.weakest.model_dump()
