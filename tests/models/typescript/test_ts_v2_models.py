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

"""The v2 TypeScript models against real codeanalyzer-typescript 1.2.0 output, one fixture per
analysis level (``tests/resources/typescript/analysis_json/v2/a{1,2,3,4}``)."""

import json
from pathlib import Path
from typing import Iterator, Tuple

import pytest
from pydantic import ValidationError

from cldk.models.typescript import (
    TSAnalysis,
    TSApplication,
    TSCallable,
    TSCallGraphEdge,
    TSClass,
    TSModule,
)

FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "typescript" / "analysis_json" / "v2"


def _load(level: int) -> TSAnalysis:
    return TSAnalysis.model_validate_json((FIXTURES / f"a{level}" / "analysis.json").read_text(encoding="utf-8"))


def _callables(module: TSModule) -> Iterator[Tuple[TSCallable, TSModule]]:
    def walk_callable(c: TSCallable):
        yield c, module
        for n in c.callables.values():
            yield from walk_callable(n)
        for t in c.types.values():
            yield from walk_type(t)

    def walk_type(t):
        for m in getattr(t, "callables", {}).values():
            yield from walk_callable(m)
        for f in getattr(t, "functions", {}).values():
            yield from walk_callable(f)
        for nt in getattr(t, "types", {}).values():
            yield from walk_type(nt)

    for f in module.functions.values():
        yield from walk_callable(f)
    for t in module.types.values():
        yield from walk_type(t)


def _all_callables(app: TSApplication) -> Iterator[Tuple[TSCallable, TSModule]]:
    for m in app.symbol_table.values():
        yield from _callables(m)


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_every_level_validates(level: int):
    a = _load(level)
    assert a.schema_version == "2.0.0"
    assert a.language == "typescript"
    assert a.max_level == level
    assert a.analyzer.name == "codeanalyzer-typescript"
    assert a.analyzer.version == "1.2.0"  # T2: read from the pin in pyproject [tool.backend-versions]
    assert a.application.id == "can://typescript/slim"
    assert a.application.kind == "application"
    assert (a.k_limit is not None) == (level >= 3)


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_round_trip_is_lossless(level: int):
    raw = json.loads((FIXTURES / f"a{level}" / "analysis.json").read_text(encoding="utf-8"))
    assert TSAnalysis.model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw


def test_module_carries_source_and_content_hash():
    app = _load(1).application
    m = next(iter(app.symbol_table.values()))
    assert m.kind == "module"
    assert m.id.startswith("can://typescript/slim/")
    assert m.content_hash
    assert m.source and m.span.bytes[1] == len(m.source)


def test_types_is_a_kind_discriminated_union_and_the_1x_maps_are_filters():
    app = _load(1).application
    seen = set()
    for m in app.symbol_table.values():
        assert m.classes == {k: t for k, t in m.types.items() if t.kind == "class"}
        assert m.interfaces == {k: t for k, t in m.types.items() if t.kind == "interface"}
        assert m.enums == {k: t for k, t in m.types.items() if t.kind == "enum"}
        assert m.type_aliases == {k: t for k, t in m.types.items() if t.kind == "type_alias"}
        assert m.namespaces == {k: t for k, t in m.types.items() if t.kind == "namespace"}
        assert all(isinstance(c, TSClass) for c in m.classes.values())
        seen |= {t.kind for t in m.types.values()}
        for ns in m.namespaces.values():
            assert ns.classes == {k: t for k, t in ns.types.items() if t.kind == "class"}
    assert seen == {"class", "interface", "enum", "type_alias", "namespace"}


def test_callable_span_properties_and_code_slice():
    app = _load(1).application
    c, m = next((c, m) for c, m in _all_callables(app) if c.kind == "method")
    assert c.start_line == c.span.start[0]
    assert c.end_line == c.span.end[0]
    assert c.start_column == c.span.start[1]
    assert c.end_column == c.span.end[1]
    assert c.code == m.source[c.span.bytes[0] : c.span.bytes[1]]
    assert c.name in c.code
    # nested callables and types see the same source
    for inner, _ in _callables(m):
        assert inner.code == m.source[inner.span.bytes[0] : inner.span.bytes[1]]
    for t in m.types.values():
        assert t.code == m.source[t.span.bytes[0] : t.span.bytes[1]]
        assert t.start_line == t.span.start[0]


def test_class_1x_attribute_paths():
    app = _load(1).application
    cls = next(t for m in app.symbol_table.values() for t in m.classes.values() if t.callables)
    assert cls.methods is cls.callables
    assert cls.attributes is cls.fields
    for field in cls.fields.values():
        assert field.kind == "field" and field.id.startswith(cls.id + "/")
        # a constructor parameter property has no span; the 1.x sentinel is -1
        assert field.start_line == (field.span.start[0] if field.span else -1)
    assert any(f.span is None for f in cls.fields.values())
    en = next(t for m in app.symbol_table.values() for t in m.enums.values())
    assert en.members == list(en.fields.values())
    assert en.members[0].value is not None


def test_l1_call_body_node_has_null_callee_refined_at_l2():
    def calls(level):
        return [b for c, _ in _all_callables(_load(level).application) for b in c.body.values() if b.kind == "call"]

    l1, l2 = calls(1), calls(2)
    assert l1 and all(b.callee is None for b in l1)
    assert l2 and all(b.callee for b in l2)


def test_l3_bodies_have_entry_exit_and_reaching_defs_ddg():
    app = _load(3).application
    c = next(c for c, _ in _all_callables(app) if c.ddg)
    assert {"@entry", "@exit"} <= set(c.body)
    assert c.body["@entry"].kind == "entry"
    assert c.cfg and c.cdg
    assert all(e.prov == ["reaching-defs"] for e in c.ddg)
    assert {e.kind for c, _ in _all_callables(app) for e in (c.cfg or [])} >= {"fallthrough", "true", "false"}


def test_l4_formal_vertices_and_param_edges():
    app = _load(4).application
    c = next(c for c, _ in _all_callables(app) if "@formal_in:0" in c.body)
    assert c.body["@formal_in:0"].kind == "formal_in"
    assert c.body["@formal_out"].kind == "formal_out"
    assert app.param_in and app.param_out
    assert app.param_in[0].src.startswith("can://typescript/slim/")
    assert any(c.summary for c, _ in _all_callables(app))


def test_call_graph_edge_shape_and_externals():
    app = _load(2).application
    e = app.call_graph[0]
    assert isinstance(e, TSCallGraphEdge)
    assert set(TSCallGraphEdge.model_fields) == {"src", "dst", "prov", "weight"}
    assert e.src.startswith("can://") and e.dst.startswith("can://")
    assert all(k.startswith("can://typescript/slim/@external/") for k in app.external_symbols)
    ext = next(iter(app.external_symbols.values()))
    assert ext.kind == "external" and ext.id in app.external_symbols
    assert app.synthesized_callables
    assert all(v.kind == "callable" and v.id for v in app.synthesized_callables.values())


def test_artifact_layer():
    app = _load(1).application
    art = next(iter(app.artifacts.values()))
    assert art.kind == "artifact" and art.id.startswith("can://artifact/slim/")
    assert art.sha256 and art.source
    assert any(ck.value is not None for a in app.artifacts.values() for ck in a.config_keys)
    assert app.dependencies == [] and app.unresolved_imports == []


def test_1x_shaped_document_is_rejected():
    v1 = {
        "symbol_table": {"src/a.ts": {"file_path": "src/a.ts", "module_name": "a"}},
        "call_graph": [],
    }
    with pytest.raises(ValidationError):
        TSAnalysis.model_validate(v1)
    with pytest.raises(ValidationError):
        TSApplication.model_validate(v1)


def test_unknown_field_is_rejected():
    raw = json.loads((FIXTURES / "a1" / "analysis.json").read_text(encoding="utf-8"))
    raw["application"]["symbol_table"][next(iter(raw["application"]["symbol_table"]))]["file_path"] = "x"
    with pytest.raises(ValidationError):
        TSAnalysis.model_validate(raw)


def test_unknown_type_kind_is_rejected():
    raw = json.loads((FIXTURES / "a1" / "analysis.json").read_text(encoding="utf-8"))
    mod = next(m for m in raw["application"]["symbol_table"].values() if m["types"])
    next(iter(mod["types"].values()))["kind"] = "mixin"
    with pytest.raises(ValidationError):
        TSAnalysis.model_validate(raw)
