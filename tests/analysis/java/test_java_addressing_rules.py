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

"""Java's addressing vocabulary: the two rules the shared helpers cannot express (J-2, J-3), and
the local-class spelling ``in_class=`` has to accept (the J-1 erratum).

Offline, over the committed v2 fixtures. Everything here is policy — no graph, no analyzer — which
is why it can be exhaustive: the same functions serve both backends in Task 1, so a rule proved
here cannot differ between them.
"""

from typing import List

import pytest

from cldk.analysis.commons.keys import module_dotted
from cldk.analysis.commons.resolve import CallableCandidate
from cldk.analysis.java.backend import java_callable_names, java_module_dotted, java_resolve_callable
from cldk.models.java import JAnalysis
from cldk.models.java.models import JApplication, JType
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

TRADE_DIRECT = "src/main/java/com/ibm/websphere/samples/daytrader/impl/direct/TradeDirect.java"
DIRECT_PKG = "com.ibm.websphere.samples.daytrader.impl.direct"
BEANS_PKG = "com.ibm.websphere.samples.daytrader.beans"

#: The two `cancelOrder` overloads of `TradeDirect`, spelled as J-1 keys.
CANCEL_INT_BOOL = f"{DIRECT_PKG}.TradeDirect.cancelOrder(java.lang.Integer, boolean)"
CANCEL_CONN_INT = f"{DIRECT_PKG}.TradeDirect.cancelOrder(java.sql.Connection, java.lang.Integer)"

#: A real anonymous class from the a1 fixture (four of them), and the `run()` it declares. The
#: qualified name carries the *declaring callable* (J-1 erratum), tail and all.
ANON_THREAD = "com.ibm.websphere.samples.daytrader.web.prims.PingManagedThread.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse).$anon$0"
ANON_THREAD_RUN = f"{ANON_THREAD}.run()"


def _candidates(app: JApplication) -> List[CallableCandidate]:
    """Every callable of the application, in the shape Task 1 will hand the resolver: keyed by
    ``<type fqn>.<signature>`` (J-1), carrying Java's match names and its module's dotted
    spellings."""
    out: List[CallableCandidate] = []

    def walk(t: JType, path: str, module_names) -> None:
        for signature, c in t.callables.items():
            full = f"{t.qualified_name}.{signature}"
            out.append(CallableCandidate(full, t.qualified_name, path, java_callable_names(full), module_names))
            for local in c.types.values():  # local and anonymous classes
                walk(local, path, module_names)
        for nested in t.types.values():
            walk(nested, path, module_names)

    for path, unit in app.symbol_table.items():
        module_names = java_module_dotted(unit.package, unit.types.keys())
        for t in unit.types.values():
            walk(t, path, module_names)
    return out


@pytest.fixture(scope="module")
def a4(analysis_json_a4) -> List[CallableCandidate]:
    return _candidates(JAnalysis.model_validate_json(analysis_json_a4).application)


@pytest.fixture(scope="module")
def a1(analysis_json) -> List[CallableCandidate]:
    return _candidates(JAnalysis.model_validate_json(analysis_json).application)


# ---- J-2: the dotted name is the declared package, never the path -------------------------------


def test_the_shared_path_derivation_is_wrong_for_java(analysis_json_a4):
    """Why Java has its own: `commons.keys.module_dotted` reads the path, and a Java path is a
    build layout, not a namespace."""
    unit = JAnalysis.model_validate_json(analysis_json_a4).application.symbol_table[TRADE_DIRECT]
    assert module_dotted(TRADE_DIRECT, extensions=(".java",)) == "src.main.java.com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect"
    assert unit.package == DIRECT_PKG
    assert java_module_dotted(unit.package, unit.types) == (DIRECT_PKG, f"{DIRECT_PKG}.TradeDirect")


def test_a_unit_in_the_default_package_dots_to_its_type_names_only():
    assert java_module_dotted("", ["Main"]) == ("Main",)
    assert java_module_dotted("p.q") == ("p.q",)


def test_in_module_takes_the_package_a_dotted_suffix_or_the_path(a4):
    for spelling in (DIRECT_PKG, "daytrader.impl.direct", f"{DIRECT_PKG}.TradeDirect", TRADE_DIRECT, "direct/TradeDirect.java"):
        assert java_resolve_callable("cancelOrder(java.lang.Integer, boolean)", a4, in_module=spelling) == CANCEL_INT_BOOL, spelling


def test_the_type_qualified_module_name_splits_two_files_of_one_package(a4):
    """`toString()` is declared in both beans, and they share a package — so the package alone
    cannot split them and `package.TypeName` (J-2) is what does."""
    with pytest.raises(AmbiguousName):
        java_resolve_callable("toString", a4, in_module=BEANS_PKG)
    assert java_resolve_callable("toString", a4, in_module=f"{BEANS_PKG}.RunStatsDataBean") == f"{BEANS_PKG}.RunStatsDataBean.toString()"


def test_a_module_naming_nothing_blames_in_module(a4):
    with pytest.raises(SelectorNotInGraph) as e:
        java_resolve_callable("cancelOrder", a4, in_module="com.ibm.nosuch.package")
    assert e.value.kind == "in_module"
    assert "com.ibm.nosuch.package" in str(e.value)


# ---- J-3: the simple name matches with the parameter tail stripped ------------------------------


def test_a_bare_name_matches_with_the_parameter_tail_stripped(a4):
    assert java_resolve_callable("buy", a4) == f"{DIRECT_PKG}.TradeDirect.buy(java.lang.String, java.lang.String, double, int)"


def test_two_overloads_raise_listing_the_full_signatures(a4):
    with pytest.raises(AmbiguousName) as e:
        java_resolve_callable("cancelOrder", a4)
    assert e.value.candidates == sorted([CANCEL_INT_BOOL, CANCEL_CONN_INT])
    assert "full signature" in e.value.message  # the way out that exists; in_class= cannot split an overload pair
    assert "can://" not in e.value.message


def test_the_full_signature_resolves_exactly(a4):
    assert java_resolve_callable("cancelOrder(java.lang.Integer, boolean)", a4) == CANCEL_INT_BOOL
    assert java_resolve_callable("cancelOrder(java.sql.Connection, java.lang.Integer)", a4) == CANCEL_CONN_INT
    assert java_resolve_callable(CANCEL_INT_BOOL, a4) == CANCEL_INT_BOOL


def test_in_class_cannot_split_an_overload_pair(a4):
    """The J-3 rationale, asserted: both overloads are declared by the same type, so the keyword
    that narrows by type leaves both standing."""
    with pytest.raises(AmbiguousName) as e:
        java_resolve_callable("cancelOrder", a4, in_class="TradeDirect")
    assert len(e.value.candidates) == 2


def test_a_name_no_callable_carries_blames_the_name(a4):
    with pytest.raises(SelectorNotInGraph) as e:
        java_resolve_callable("cancelOrders", a4)
    assert e.value.kind == "callable"
    assert e.value.missing == ["cancelOrders"]


def test_the_tail_stripped_is_the_callables_own(a4):
    assert java_callable_names(CANCEL_INT_BOOL) == (CANCEL_INT_BOOL, f"{DIRECT_PKG}.TradeDirect.cancelOrder")
    # The declaring callable's tail lives *inside* an anonymous class's qualified name, so the
    # stripping cuts at the last '(', not the first.
    assert java_callable_names(ANON_THREAD_RUN) == (ANON_THREAD_RUN, f"{ANON_THREAD}.run")


# ---- the J-1 erratum: a local class's qualified name carries its declaring callable -------------


def test_in_class_accepts_the_declaring_callable_spelling(a1):
    assert java_resolve_callable("run", a1, in_class=ANON_THREAD) == ANON_THREAD_RUN
    assert (
        java_resolve_callable("run", a1, in_class="PingManagedThread.doGet(javax.servlet.http.HttpServletRequest, javax.servlet.http.HttpServletResponse).$anon$0")
        == ANON_THREAD_RUN
    )


def test_the_enclosing_type_plus_simple_name_spelling_names_nothing(a1):
    """The erratum's point: dropping the callable segment is not a shorter spelling of the same
    class, it is a name no class has."""
    with pytest.raises(SelectorNotInGraph) as e:
        java_resolve_callable("run", a1, in_class="PingManagedThread.$anon$0")
    assert e.value.kind == "in_class"


def test_the_bare_anonymous_name_is_ambiguous_across_declaring_callables(a1):
    """`$anon$N` is numbered per declaring callable, so all four of the fixture's anonymous
    classes are `$anon$0` — and three of them declare a `run()`, which is what makes the bare
    name ambiguous three ways rather than four."""
    with pytest.raises(AmbiguousName) as e:
        java_resolve_callable("run", a1, in_class="$anon$0")
    assert len(e.value.candidates) == 3
    assert ANON_THREAD_RUN in e.value.candidates


def test_a_callable_inside_an_anonymous_class_resolves_by_its_own_name(a1):
    assert java_resolve_callable("onResult", a1) == (
        "com.ibm.websphere.samples.daytrader.web.prims.PingWebSocketTextAsync.ping(java.lang.String).$anon$0.onResult(javax.websocket.SendResult)"
    )


# ---- the three rules the review's should-fixes pin down -----------------------------------------


def test_supplying_no_module_names_is_not_the_same_as_supplying_none(a4):
    """``module_names=()`` means *this unit answers to no dotted module name* — a default-package
    unit declaring no type, which :func:`java_module_dotted` really does return ``()`` for. It must
    stay distinct from ``None``, which means "derive one from the path".

    Conflating them is what a truthiness test does, and the fallback it reaches is the exact thing
    J-2 forbids: :func:`module_dotted`'s default suffix list is ``(".py",)``, so a ``.java`` path
    is not even stripped and derives to ``src.main.java.….TradeDirect.java`` — a spelling
    ``in_module=`` would then *accept*, i.e. a path derivation presenting as a successful match.
    Unreachable from a symbol table (a unit with no types contributes no candidates), reachable
    from a Neo4j ``J_DECLARES`` collect that comes back missing or empty.
    """
    derived = "src.main.java.com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect.java"
    assert module_dotted(TRADE_DIRECT) == derived, "the .py default strips nothing from a .java path"
    assert java_module_dotted("", ()) == ()

    derive_it = CallableCandidate("p.C.m()", "p.C", TRADE_DIRECT, java_callable_names("p.C.m()"))
    assert derive_it.module_names is None, "the default must mean 'derive', not 'none'"
    assert java_resolve_callable("m", [derive_it], in_module=derived) == "p.C.m()"

    supplied_none = derive_it._replace(module_names=())
    with pytest.raises(SelectorNotInGraph) as e:
        java_resolve_callable("m", [supplied_none], in_module=derived)
    assert e.value.kind == "in_module"
    assert java_resolve_callable("m", [supplied_none], in_module=TRADE_DIRECT) == "p.C.m()", "the path still addresses it"


def test_the_ambiguity_advice_is_followable_because_it_names_the_listed_matches(a4):
    """The analyzer normalises no parameter tail, so *no description* of one is true: a4 spells
    ``setTopLosers(Collection<QuoteDataBean>)`` — generic argument kept, type name unqualified —
    where a1 spells the same method ``setTopLosers(java.util.Collection)``. Advice to name "the
    full signature, erased parameter types included" is a rule a caller can follow straight into a
    ``SelectorNotInGraph``, which is the confident wrong answer E8 keeps out of the error path.

    So the advice points at the candidates the message already carries, and this asserts the
    property that makes it true: every candidate, copied verbatim, resolves.
    """
    top_losers = f"{BEANS_PKG}.MarketSummaryDataBean.setTopLosers(Collection<QuoteDataBean>)"
    assert java_resolve_callable("setTopLosers", a4) == top_losers
    with pytest.raises(SelectorNotInGraph):
        java_resolve_callable("setTopLosers(java.util.Collection)", a4)  # what the old advice told a caller to write

    with pytest.raises(AmbiguousName) as e:
        java_resolve_callable("cancelOrder", a4)
    assert "erased" not in e.value.message, "no claim about a normalisation the analyzer does not perform"
    for candidate in e.value.candidates:
        assert java_resolve_callable(candidate, a4) == candidate, "the advice, followed literally"


def test_the_advice_drops_a_keyword_that_cannot_split_these_matches(a4):
    """``test_in_class_cannot_split_an_overload_pair`` proves ``in_class=`` is not a way out of an
    overload pair, so the message must not offer it; both overloads share one file, so
    ``in_module=`` goes too, and what is left is the clause that works. A name ambiguous across two
    types in two files keeps both keywords — the pruning is per ambiguity, not a blanket removal.
    """
    with pytest.raises(AmbiguousName) as overloads:
        java_resolve_callable("cancelOrder", a4)
    assert "in_class=" not in overloads.value.message and "in_module=" not in overloads.value.message
    assert "full signature" in overloads.value.message

    with pytest.raises(AmbiguousName) as two_beans:
        java_resolve_callable("toString", a4)
    assert "in_class=" in two_beans.value.message and "in_module=" in two_beans.value.message
