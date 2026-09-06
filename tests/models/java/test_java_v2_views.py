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

"""The v1 field names survive as computed views over the v2 models (J-8, J-13, J-15)."""

import pytest

from cldk.models.java import JAnalysis
from cldk.models.java.models import InitializationBlock, JCallable, JCallSite, JCompilationUnit, JField, JType

TRADE_DIRECT = "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java"
CANCEL_ORDER = "cancelOrder(java.lang.Integer, boolean)"


@pytest.fixture(scope="module")
def a4(analysis_json_a4) -> JAnalysis:
    return JAnalysis.model_validate_json(analysis_json_a4)


@pytest.fixture(scope="module")
def a1(analysis_json) -> JAnalysis:
    return JAnalysis.model_validate_json(analysis_json)


@pytest.fixture(scope="module")
def trade_direct(a4: JAnalysis) -> JType:
    return a4.application.symbol_table[TRADE_DIRECT].types["TradeDirect"]


@pytest.fixture(scope="module")
def cancel_order(trade_direct: JType) -> JCallable:
    return trade_direct.callable_declarations[CANCEL_ORDER]


# ---- JCompilationUnit ---------------------------------------------------------------------------


def test_unit_views(a4: JAnalysis):
    unit: JCompilationUnit = a4.application.symbol_table[TRADE_DIRECT]
    assert unit.file_path == TRADE_DIRECT  # the symbol-table key, not parsed from the id
    assert unit.package_name == "com.ibm.websphere.samples.daytrader.impl.direct"
    assert unit.type_declarations is unit.types
    assert unit.is_modified is False
    assert unit.imports[0] == "java.io.Serializable"  # v1 shape: List[str] of paths
    assert unit.import_declarations[0].path == "java.io.Serializable"
    assert unit.import_declarations[0].name == "Serializable"
    assert unit.import_declarations[0].is_static is False
    assert unit.comments and unit.comments[0].is_javadoc is True
    assert unit.comments[0].start_line == 1
    assert unit.start_line == 1 and unit.end_line == unit.span.end[0]


# ---- JType -----------------------------------------------------------------------------------------


def test_type_views(trade_direct: JType):
    t = trade_direct
    assert t.name == "TradeDirect"
    assert t.is_interface is False
    assert t.is_class_or_interface_declaration is True
    assert t.is_concrete_class is True
    assert t.is_nested_type is False and t.is_inner_class is False and t.is_local_class is False
    assert t.is_enum_declaration is False and t.is_annotation_declaration is False and t.is_record_declaration is False
    assert t.extends_list == t.base_types == []
    assert t.implements_list == t.interfaces == ["TradeServices", "java.io.Serializable"]
    assert t.annotations == ["@Dependent", "@TradeJDBC", '@RuntimeMode("Direct (JDBC)")', "@Trace"]  # v1 source spelling
    assert t.parent_type == ""
    assert t.callable_declarations is t.callables
    assert [f.name for f in t.field_declarations] == list(t.fields)
    assert t.nested_type_declarations == []
    assert t.enum_constants == [] and t.record_components == []
    assert t.initialization_blocks == []
    assert t.code.startswith("@Dependent") and t.code.rstrip().endswith("}")
    assert t.start_line == t.span.start[0]


def test_interface_and_annotation_kinds(a1: JAnalysis):
    st = a1.application.symbol_table
    tradedb = st["src/main/java/com/ibm/websphere/samples/daytrader/interfaces/TradeDB.java"].types["TradeDB"]
    assert tradedb.is_interface is True and tradedb.is_concrete_class is False and tradedb.is_class_or_interface_declaration is True
    msu = st["src/main/java/com/ibm/websphere/samples/daytrader/interfaces/MarketSummaryUpdate.java"].types["MarketSummaryUpdate"]
    assert msu.is_annotation_declaration is True and msu.is_class_or_interface_declaration is False
    # the v1 ``annotations`` spelling, pinned on a1
    assert st[TRADE_DIRECT].types["TradeDirect"].annotations[2] == '@RuntimeMode("Direct (JDBC)")'
    assert st[TRADE_DIRECT].types["TradeDirect"].callables[CANCEL_ORDER].annotations == ["@Override"]


def test_nested_type_views(a1: JAnalysis):
    outer = a1.application.symbol_table["src/main/java/com/ibm/websphere/samples/daytrader/impl/ejb3/TradeSLSBBean.java"].types["TradeSLSBBean"]
    assert outer.nested_type_declarations == ["com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean.quotePriceComparator"]  # v1: FQNs
    inner = outer.types["quotePriceComparator"]
    assert inner.name == "quotePriceComparator"
    assert inner.is_nested_type is True
    assert inner.is_inner_class is True  # non-static nested class
    assert inner.is_local_class is False
    assert inner.parent_type == "com.ibm.websphere.samples.daytrader.impl.ejb3.TradeSLSBBean"
    assert inner.code.startswith("class quotePriceComparator")
    # callables nested under a nested type still slice from the unit's source
    assert inner.callables["<init>()"].is_implicit is True


def test_local_anonymous_class_under_a_callable(a1: JAnalysis):
    unit = a1.application.symbol_table["src/main/java/com/ibm/websphere/samples/daytrader/web/prims/PingManagedExecutor.java"]
    doget = next(c for c in unit.types["PingManagedExecutor"].callables.values() if c.signature.startswith("doGet("))
    anon = doget.types["$anon$0"]
    assert anon.name == "$anon$0"
    assert anon.is_local_class is True and anon.is_nested_type is False
    assert anon.parent_type == "com.ibm.websphere.samples.daytrader.web.prims.PingManagedExecutor"
    run = anon.callables["run()"]
    assert run.code.startswith("{\n") and run.code.endswith("}")  # the body block


def test_initialization_blocks_are_initializer_callables(a1: JAnalysis):
    cfg = a1.application.symbol_table["src/main/java/com/ibm/websphere/samples/daytrader/util/TradeConfig.java"].types["TradeConfig"]
    blocks = cfg.initialization_blocks
    assert [b.signature for b in blocks] == ["<clinit>$0()", "<clinit>$1()"]
    assert all(isinstance(b, InitializationBlock) for b in blocks)
    assert blocks[0].is_static is True
    assert blocks[0].code.startswith("{\n") and blocks[0].code.endswith("}")  # the block, not ``static {``
    assert blocks[0].declaration is None  # 3.0.x emits no declaration for initializers


# ---- JCallable --------------------------------------------------------------------------------------


def test_callable_code_is_the_span_slice(cancel_order: JCallable):
    c = cancel_order
    # v1 ``code`` is the body block: ``body_span``, coherent with ``code_start_line``
    assert c.code.startswith("{\n\n    Connection conn = null;") and c.code.endswith("\n  }")
    assert c.code == c._unit.slice(c.body_span)
    assert c.start_line == 646 and c.end_line == 665
    assert c.code_start_line == 647  # body_span start, not span start
    assert c.declaration == "public void cancelOrder(Integer orderID, boolean twoPhase) throws Exception"


def test_callable_v1_views(cancel_order: JCallable):
    c = cancel_order
    assert c.thrown_exceptions == c.error_channel == ["java.lang.Exception"]
    assert c.cyclomatic_complexity == 2 == c.metrics.cyclomatic
    assert c.annotations == ["@Override"]
    assert c.referenced_types == c.refs.types == ["java.lang.Exception", "java.sql.Connection"]
    assert c.accessed_fields == c.refs.fields
    assert c.is_constructor is False
    assert c.is_implicit is False and c.is_entrypoint is False
    assert c.crud_operations == [] and c.crud_queries == []
    assert c.return_type == "void"
    assert [p.name for p in c.parameters] == ["orderID", "twoPhase"]


def test_variable_declarations_view(cancel_order: JCallable):
    v = cancel_order.variable_declarations[0]
    assert cancel_order.variable_declarations is cancel_order.local_variables
    assert v.name == "conn" and v.type == "java.sql.Connection" and v.initializer == "null"
    assert v.start_line == 649 and v.start_column == 16 and v.end_line == 649 and v.end_column == 26
    assert v.comment is None


def test_call_sites_are_views_over_call_body_nodes(cancel_order: JCallable):
    calls = {k: n for k, n in cancel_order.body.items() if n.kind == "call"}
    sites = cancel_order.call_sites
    assert len(sites) == len(calls) == 8
    assert all(isinstance(s, JCallSite) for s in sites)
    resolved = [s for s in sites if s.callee_signature]
    assert resolved, "at least one call site carries the callee's signature"
    # two ``Log.trace`` calls are unresolved on the wire: no callee_signature, no accessibility
    unresolved = [s for s in sites if not s.callee_signature]
    assert len(unresolved) == 2
    assert all(s.is_public is None and s.is_private is None for s in unresolved)
    # the unresolved pair are the qualified ``Log.trace``/``Log.error`` calls
    assert {s.receiver_expr for s in unresolved} == {"Log"} and {s.method_name for s in unresolved} == {"trace", "error"}
    # the six resolved ones are private implicit-``this`` calls: no receiver on the wire, ``is_private``
    assert len(resolved) == 6
    assert all(s.is_private is True and (s.is_public, s.is_protected, s.is_unspecified) == (False, False, False) for s in resolved)
    assert all(s.receiver_expr == "" and s.receiver_type == "" and s.is_static_call is False for s in resolved)
    s = next(x for x in resolved if x.method_name == "cancelOrder")
    assert s.callee_signature == "cancelOrder(java.sql.Connection, java.lang.Integer)"
    assert (s.start_line, s.start_column, s.end_line, s.end_column) == (656, 7, 656, 32)
    assert cancel_order.body["656:7"].code == "cancelOrder(conn, orderID)"  # body nodes slice too
    assert s.crud_operation is None and s.crud_query is None


def test_call_site_visibility_from_accessibility():
    def site(acc):
        from cldk.models.java.models import JBodyNode

        return JCallSite.from_body_node(JBodyNode(kind="call", method_name="m", accessibility=acc))

    assert (site("public").is_public, site("private").is_private, site("protected").is_protected, site("package_private").is_unspecified) == (True, True, True, True)
    assert site("public").is_private is False
    assert site(None).is_public is None


def test_constructor_and_implicit_callable(trade_direct: JType):
    init = trade_direct.callables["<init>()"]
    assert init.is_constructor is True
    assert init.is_implicit is True
    assert init.declaration is None
    assert init.code == ""
    assert init.start_line == -1 and init.end_line == -1 and init.code_start_line == -1
    assert init.call_sites == [] and init.variable_declarations == []
    assert init.cyclomatic_complexity is None
    assert init.referenced_types == [] and init.accessed_fields == []
    assert init.cfg is None and init.ddg is None


def test_two_overloads_are_distinct_callables(trade_direct: JType):
    a = trade_direct.callables[CANCEL_ORDER]
    b = trade_direct.callables["cancelOrder(java.sql.Connection, java.lang.Integer)"]
    assert a.id != b.id and hash(a) != hash(b)
    assert a.declaration != b.declaration


# ---- JField / JCallableParameter ----------------------------------------------------------------------


def test_field_views(trade_direct: JType):
    f: JField = trade_direct.fields["serialVersionUID"]
    assert f.variables == ["serialVersionUID"]
    assert f.start_line == 89 and f.end_line == 89
    assert f.annotations == []
    assert f.comment is not None and f.comment.is_javadoc is True
    assert f.initializer == "-8089049090952927985L"
    assert f.variable_initializers == {"serialVersionUID": "-8089049090952927985L"}
    assert f.modifiers == ["private", "static", "final"]


def test_parameter_views(cancel_order: JCallable):
    p = cancel_order.parameters[0]
    assert p.annotations == []
    assert (p.start_line, p.start_column, p.end_line, p.end_column) == (647, 27, 647, 41)
    assert p.is_variadic is False


# ---- J-15: byte offsets; J-13: identity and threading ---------------------------------------------


def _unit_payload(src: str) -> dict:
    b = src.encode("utf-8")
    t0, t1 = b.index(b"class A"), b.rindex(b"}") + 1
    m0, mb0 = b.index(b"void m"), b.index(b"{ }")
    return {
        "id": "can://java/x/A.java",
        "kind": "module",
        "span": {"start": [1, 1], "end": [4, 2], "bytes": [0, len(b)]},
        "package": "",
        "source": src,
        "types": {
            "A": {
                "id": "can://java/x/A.java/A",
                "kind": "class",
                "span": {"start": [2, 1], "end": [4, 2], "bytes": [t0, t1]},
                "callables": {
                    "m()": {
                        "id": "can://java/x/A.java/A/m()",
                        "kind": "method",
                        "signature": "m()",
                        "span": {"start": [3, 3], "end": [3, 15], "bytes": [m0, mb0 + 3]},
                        "body_span": {"start": [3, 12], "end": [3, 15], "bytes": [mb0, mb0 + 3]},
                    }
                },
            }
        },
    }


def test_code_slices_by_utf8_byte_offsets():
    src = "// \u00a9 IBM\nclass A {\n  void m() { }\n}\n"
    unit = JCompilationUnit.model_validate(_unit_payload(src))
    a = unit.types["A"]
    assert a.code == "class A {\n  void m() { }\n}"
    assert a.callables["m()"].code == "{ }"
    # the naive character slice is off by one after the two-byte ``\u00a9``
    b0, b1 = a.span.bytes
    assert src[b0:b1] == "lass A {\n  void m() { }\n}\n"
    # an ASCII unit takes the plain-index path and agrees with the byte slice
    ascii_unit = JCompilationUnit.model_validate(_unit_payload(src.replace("\u00a9", "(c)")))
    assert ascii_unit.types["A"].code == "class A {\n  void m() { }\n}"


def test_identity_is_the_id_across_independent_parses(a4: JAnalysis, analysis_json_a4: str):
    from cldk.models.java.models import JMethodDetail

    other = JAnalysis.model_validate_json(analysis_json_a4)
    u1, u2 = a4.application.symbol_table[TRADE_DIRECT], other.application.symbol_table[TRADE_DIRECT]
    t1, t2 = u1.types["TradeDirect"], u2.types["TradeDirect"]
    c1, c2 = t1.callables[CANCEL_ORDER], t2.callables[CANCEL_ORDER]
    f1, f2 = t1.fields["serialVersionUID"], t2.fields["serialVersionUID"]
    assert c1 == c2 and t1 == t2 and u1 == u2 and f1 == f2
    assert c1 is not c2 and c1 in [c2] and len({c1, c2}) == 1
    assert c1 != t2.callables["cancelOrder(java.sql.Connection, java.lang.Integer)"]
    assert JMethodDetail(method_declaration=c1.declaration, klass="TradeDirect", method=c1) == JMethodDetail(method_declaration=c2.declaration, klass="TradeDirect", method=c2)
    assert a4.application == other.application


def test_unthreaded_nodes_raise_instead_of_returning_empty(cancel_order: JCallable):
    lone = JCallable.model_validate(cancel_order.model_dump(by_alias=True))
    with pytest.raises(RuntimeError, match="not threaded"):
        lone.code
    unit = JCompilationUnit.model_validate(_unit_payload("class A {\n  void m() { }\n}\n"))
    with pytest.raises(RuntimeError, match="file_path"):
        unit.file_path
