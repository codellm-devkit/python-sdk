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

"""Schema-level tests for the Java v2 models: the codeanalyzer-java 3.0.2 envelope parses at
L1 and L4, round-trips losslessly, and rejects anything the wire does not carry."""

import json

import pytest
from pydantic import ValidationError

from cldk.models.java import JAnalysis, JApplication, JCallGraphEdge, JGraphEdges


def _sorted(obj) -> str:
    return json.dumps(obj, sort_keys=True)


@pytest.fixture(scope="module")
def a1(analysis_json) -> JAnalysis:
    return JAnalysis.model_validate_json(analysis_json)


@pytest.fixture(scope="module")
def a4(analysis_json_a4) -> JAnalysis:
    return JAnalysis.model_validate_json(analysis_json_a4)


def test_envelope_at_both_levels(a1: JAnalysis, a4: JAnalysis):
    for a, level in ((a1, 1), (a4, 4)):
        assert a.schema_version == "2.0.0"
        assert a.language == "java"
        assert a.max_level == level
        assert a.k_limit is None  # never emitted by 3.0.x
        assert a.analyzer.name == "codeanalyzer-java"
        assert a.analyzer.version == "3.0.2"
        assert a.application.id == "can://java/daytrader8"
        assert a.application.kind == "application"


def test_l1_has_138_units_and_no_app_scope_overlays(a1: JAnalysis):
    app = a1.application
    assert len(app.symbol_table) == 138
    # L1 emits no ``call_graph``/``param_in``/``param_out`` keys at all — the defaults are empty, not None.
    assert app.call_graph == []
    assert app.param_in == []
    assert app.param_out == []
    assert "call_graph" not in app.model_fields_set
    assert "param_in" not in app.model_fields_set


def test_l4_carries_param_edges_and_points_to_ddg(a4: JAnalysis):
    app = a4.application
    assert len(app.param_in) == 258
    assert len(app.param_out) == 97
    assert len(app.call_graph) == 247
    assert all(isinstance(e, JCallGraphEdge) for e in app.call_graph)
    provs = {tuple(e.prov) for u in app.symbol_table.values() for t in u.types.values() for c in t.callables.values() for e in (c.ddg or [])}
    assert ("points-to",) in provs
    assert ("ssa",) in provs


@pytest.mark.parametrize("fixture_name", ["analysis_json", "analysis_json_a4"])
def test_round_trip_is_byte_equal(fixture_name: str, request):
    raw = request.getfixturevalue(fixture_name)
    dumped = JAnalysis.model_validate_json(raw).model_dump(mode="json", exclude_unset=True, by_alias=True)
    assert _sorted(dumped) == _sorted(json.loads(raw))


def test_unknown_top_level_key_is_rejected(analysis_json_a4: str):
    raw = json.loads(analysis_json_a4)
    raw["repository"] = "x"
    with pytest.raises(ValidationError):
        JAnalysis.model_validate(raw)


def test_unknown_nested_key_is_rejected(analysis_json_a4: str):
    raw = json.loads(analysis_json_a4)
    unit = next(iter(raw["application"]["symbol_table"].values()))
    unit["file_path"] = "x"
    with pytest.raises(ValidationError):
        JAnalysis.model_validate(raw)


def test_v1_shaped_payload_is_rejected():
    v1 = {"symbol_table": {"a/B.java": {"file_path": "a/B.java", "package_name": "a", "comments": [], "imports": [], "type_declarations": {}}}}
    with pytest.raises(ValidationError):
        JAnalysis.model_validate(v1)
    with pytest.raises(ValidationError):
        JApplication.model_validate(v1)


def test_jgraphedges_is_the_call_graph_edge():
    assert JGraphEdges is JCallGraphEdge
