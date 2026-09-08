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

"""The v2 TypeScript models against real codeanalyzer-typescript output, one fixture per analysis
level (``tests/resources/typescript/analysis_json/v2/a{1,2,3,4}``), at whatever the pin says.

Leg 2.5a wrote these models against 1.2.0 and pre-declared 1.3.0's additive fields as optional so
one model parses both generations; leg 2.5b moved the pin and regenerated the fixtures.
:func:`test_the_pinned_generation_parses_with_nothing_widened` is what holds that claim honest.
"""

import json
from pathlib import Path
from typing import Iterator, Tuple

import pytest
import toml
from pydantic import ValidationError

from cldk.models.typescript.models import _iter_spanned
from cldk.models.typescript import (
    TSAnalysis,
    TSApplication,
    TSCallable,
    TSCallGraphEdge,
    TSClass,
    TSEnum,
    TSInterface,
    TSModule,
    TSNamespace,
    TSTypeAlias,
)

FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "typescript" / "analysis_json" / "v2"


def _pinned_version() -> str:
    """The pinned analyzer, read rather than written down: the fixtures are regenerated with the
    pinned wheel, so a hand-copied version string here is one more place a pin bump has to be
    remembered (and 2.5b found it stale)."""
    root = Path(__file__).resolve().parents[2].parent
    return toml.load(root / "pyproject.toml")["tool"]["backend-versions"]["codeanalyzer-typescript"]


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
    assert a.analyzer.version == _pinned_version()
    assert a.application.id == "can://slim"
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
    assert m.id.startswith("can://slim/typescript/")
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
    assert app.param_in[0].src.startswith("can://slim/typescript/")
    assert any(c.summary for c, _ in _all_callables(app))


def test_call_graph_edge_shape_and_externals():
    app = _load(2).application
    e = app.call_graph[0]
    assert isinstance(e, TSCallGraphEdge)
    assert set(TSCallGraphEdge.model_fields) == {"src", "dst", "prov", "weight"}
    assert e.src.startswith("can://") and e.dst.startswith("can://")
    assert all(k.startswith("can://slim/@external/") for k in app.external_symbols)
    ext = next(iter(app.external_symbols.values()))
    assert ext.kind == "external" and ext.id in app.external_symbols
    assert app.synthesized_callables
    assert all(v.kind == "callable" and v.id for v in app.synthesized_callables.values())


def test_artifact_layer():
    app = _load(1).application
    art = next(iter(app.artifacts.values()))
    assert art.kind == "artifact" and art.id.startswith("can://slim/artifact/")
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


def test_every_type_kind_accepts_the_1_3_0_entrypoint_fields():
    # TS-8: both fields are declared on the shared ``_Type`` base, so a 1.3.0 payload that stamps
    # them on any of the five kinds validates under ``extra="forbid"`` before the pin moves --
    # declaring them on ``TSClass`` alone would fail validation on the other four.
    span = {"start": (1, 1), "end": (2, 1), "bytes": (0, 4)}
    entrypoint = {"framework": "express", "route": "/x", "http_methods": ["GET"]}
    for cls, kind in ((TSClass, "class"), (TSInterface, "interface"), (TSEnum, "enum"), (TSTypeAlias, "type_alias"), (TSNamespace, "namespace")):
        raw = {"id": "can://app/typescript/src/a.ts/X", "kind": kind, "name": "X", "signature": "src/a.X", "span": span, "is_entrypoint": True, "entrypoints": [entrypoint]}
        node = cls.model_validate(raw)
        assert node.is_entrypoint is True, kind
        assert node.entrypoints and node.entrypoints[0].framework == "express", kind
        assert cls.model_validate({k: v for k, v in raw.items() if k not in ("is_entrypoint", "entrypoints")}).is_entrypoint is None, kind


def test_the_pinned_generation_parses_with_nothing_widened():
    """The 1.3.0 bump moved no model (leg 2.5b, Task 0).

    Every fixture is 1.3.0 output and every model is ``extra="forbid"``, so a field 1.3.0 added
    that 2.5a had not pre-declared would be a ``ValidationError`` here, not a silent pass. What
    1.3.0 added over 1.2.0 in this corpus: ``application.entrypoint_report`` (**required** on the
    application in 1.3.0's ``schema.ts``, kept optional here because the graph-backed application
    view carries the report as a JSON string on the anchor instead), ``is_entrypoint`` /
    ``entrypoints`` on classes and callables, ``parameters[].id`` and body-node ``id``s, and the
    L4 port lattice wired into the statement DDG.
    """
    for level in (1, 2, 3, 4):
        a = _load(level)  # extra="forbid": this line is the assertion
        report = a.application.entrypoint_report
        assert report is not None, f"a{level} carries no entrypoint_report"
        assert report.rulesets == ["shipped"]
        assert report.unresolved == {"Get": 2, "Controller": 1}
        assert report.frameworks_detected == [] and report.errors == []


def test_is_entrypoint_is_declared_on_every_type_kind_and_emitted_where_the_corpus_has_one():
    """``entrypoints`` / ``is_entrypoint`` live on ``TSType``, the base every kind extends, and on
    ``TSCallable`` -- so all five kinds *parse* them.

    Only classes and callables carry them in this corpus, which is a property of the corpus, not of
    1.3.0: this application declares no entrypoint at all (``is_entrypoint`` is ``False`` on the
    class the two unresolved decorators sit on). The other four kinds fall back to the model's
    ``None`` default, and "untested by corpus" is the honest word for them.
    """
    for kind in (TSClass, TSInterface, TSEnum, TSTypeAlias, TSNamespace, TSCallable):
        assert {"is_entrypoint", "entrypoints"} <= set(kind.model_fields), kind.__name__
    a = _load(4)
    seen = {t.kind: (t.is_entrypoint, t.entrypoints) for m in a.application.symbol_table.values() for t in m.types.values()}
    assert seen["class"] == (False, [])
    assert {k: v for k, v in seen.items() if k != "class"} == {k: (None, None) for k in seen if k != "class"}
    for c, _ in _all_callables(a.application):
        assert c.is_entrypoint is False and c.entrypoints == []


def test_1_3_0_stopped_emitting_a_decorator_qualified_name():
    """An analyzer behaviour change the pin bump brought with it, recorded rather than papered over.

    1.2.0 emitted ``qualified_name`` on every ``TSDecorator`` (equal to ``name`` in this corpus);
    1.3.0 emits none. The field stays ``Optional[str] = None`` -- which is why nothing had to be
    widened -- so it now reads ``None`` on the local backend. No TypeScript accessor reads it; the
    Neo4j reconstructor still maps the property if a graph carries one.
    """
    a = _load(1)
    decorated = [t for m in a.application.symbol_table.values() for t in m.types.values() if getattr(t, "decorators", None)]
    assert decorated, "the fixture application must still have a decorated type"
    for t in decorated:
        assert [d.name for d in t.decorators] == ["Controller"]
        assert all(d.qualified_name is None for d in t.decorators)


def test_span_bytes_are_utf8_offsets_and_code_decodes_them():
    """codeanalyzer-typescript 1.5.0's one breaking change (cants#179): ``span.bytes`` are UTF-8
    byte offsets on every node at every level, so ``code`` must slice the encoded module, not the
    string.

    The fixture app carries em-dashes in comments, which is what makes this a real test rather
    than a tautology: the assertion below counts how many nodes a *character* slice would have
    got wrong, and fails if that count is zero — a corpus that went ASCII would make the whole
    test vacuous without saying so.
    """
    a = _load(4)
    checked = drifted = 0
    for module in a.application.symbol_table.values():
        if module.source.isascii():
            continue
        encoded = module.source.encode("utf-8")
        for node in _iter_spanned(module):
            start, end = node.span.bytes
            expected = encoded[start:end].decode("utf-8")
            assert node.code == expected, f"{node.span.bytes} in {module.id}"
            checked += 1
            drifted += module.source[start:end] != expected
    assert checked, "no non-ASCII module in the fixture; this test proves nothing as written"
    assert drifted, "every span in the non-ASCII modules starts before the first multi-byte character; the test cannot fail"
