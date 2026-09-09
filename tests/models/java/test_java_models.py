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

"""Schema-level tests for the Java v2 models: the codeanalyzer-java 3.1.0 envelope parses at
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
        assert a.analyzer.version == "3.1.0"
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


def test_an_unknown_top_level_key_is_ignored(analysis_json_a4: str):
    """#386: the mirrors are ``extra="ignore"``, so an undeclared key is dropped, not rejected.

    ``repository`` is a real field on codeanalyzer-python's application and absent from Java's, so it
    stands in for the shape this policy exists to absorb: a sibling analyzer's field arriving in a
    later Java release. It used to fail the whole payload; now it parses and the value is gone.
    """
    raw = json.loads(analysis_json_a4)
    raw["repository"] = "x"

    a = JAnalysis.model_validate(raw)
    assert not hasattr(a, "repository")
    assert "repository" not in a.model_dump()
    assert a.model_extra in (None, {}), "extra=ignore must not retain it; extra=allow would"


def test_an_unknown_nested_key_is_ignored(analysis_json_a4: str):
    """The same policy one level down, where the old behaviour was most expensive.

    ``file_path`` is a **retired 1.x** key on the compilation unit. Rejecting it meant a stale or
    misspelled wire key failed the entire analysis; ignoring it means the key is unreachable and
    nothing says so. That is the trade #386 accepted, and the assertion here is what keeps it
    explicit rather than folklore.

    ``JCompilationUnit`` is worth naming: it overrides ``model_config`` wholesale for its alias
    settings, so ``_Base``'s policy does not reach it and it carries its own ``ignore``. If this test
    ever fails while the top-level one passes, that override has drifted back to ``forbid``.
    """
    raw = json.loads(analysis_json_a4)
    key = next(iter(raw["application"]["symbol_table"]))
    raw["application"]["symbol_table"][key]["file_path"] = "x"

    a = JAnalysis.model_validate(raw)
    unit = a.application.symbol_table[key]

    # `file_path` is a property over a PrivateAttr that `JApplication` stamps from the symbol-table
    # key (models.py:691,714) -- not a wire field -- so the test is not that the attribute vanishes
    # but that the injected wire value never reaches it.
    assert "file_path" not in type(unit).model_fields, "file_path is not a wire field"
    assert unit.file_path == key, "the property still derives from the symbol-table key"
    assert unit.file_path != "x", "the ignored wire value must not have taken effect"
    assert unit.model_extra in (None, {}), "extra=ignore must not retain it; extra=allow would"


def test_v1_shaped_payload_is_rejected():
    v1 = {"symbol_table": {"a/B.java": {"file_path": "a/B.java", "package_name": "a", "comments": [], "imports": [], "type_declarations": {}}}}
    with pytest.raises(ValidationError):
        JAnalysis.model_validate(v1)
    with pytest.raises(ValidationError):
        JApplication.model_validate(v1)


def test_jgraphedges_is_the_call_graph_edge():
    assert JGraphEdges is JCallGraphEdge
