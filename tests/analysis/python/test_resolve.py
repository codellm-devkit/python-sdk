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

"""The addressing layer: a caller names things, the SDK resolves them (leg 1.5, E6-E8).

Two halves, deliberately separated by what they need to run:

* The **policy** (``cldk.analysis.commons.resolve``) is pure — a query name and a list of
  candidates in, one survivor or an exception out. No Cypher, no driver, no symbol table. That is
  what lets the exhaustive cases below run with no graph attached, and it is the same code both
  backends route through, so they cannot drift on what "ambiguous" means.
* The **wiring** needs a real application, and runs against the live graph under the same
  environment variables as ``test_e2e_neo4j_live.py`` (see ``conftest.py``'s ``live_analysis``).
  Strictly read-only::

      CLDK_TEST_NEO4J_URI=bolt://localhost:7688 \
      CLDK_TEST_NEO4J_USER=neo4j \
      CLDK_TEST_NEO4J_PASSWORD=cldkleg1test \
      CLDK_TEST_NEO4J_APP=odoo-slim-19 \
      uv run pytest tests/analysis/python/test_resolve.py
"""

from __future__ import annotations

import os
import pathlib
import re
import textwrap

import pytest

from codeanalyzer.neo4j.project import _global_ordinal

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons import resolve as resolve_module

from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.analysis.commons.resolve import (
    CallableCandidate,
    module_dotted,
    resolve_callable_signature,
    resolve_name,
    resolve_value_name,
    resolve_within,
    segment_match,
    value_candidate,
)
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer, body_node_id
from cldk.models.python import PyApplication, PyCallable, PyClass, PyModule
from cldk.utils.exceptions import AmbiguousName, SelectorNotInGraph

live_only = pytest.mark.skipif(
    not os.environ.get("CLDK_TEST_NEO4J_URI"),
    reason="no live Neo4j (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)",
)


# ----------------------------------------------------------------------------------------------
# The five the plan names — resolution end to end, over a real application.
# ----------------------------------------------------------------------------------------------
@live_only
def test_unique_name_resolves_without_a_scope(live_analysis):
    n = live_analysis.backend.resolve_callable("action_validate_step")
    assert n.kind == "call" or n.callable.endswith("action_validate_step")
    assert "can://" not in n.callable, "callers never see URIs"


@live_only
def test_ambiguous_name_raises_with_candidates(live_analysis):
    with pytest.raises(AmbiguousName) as e:
        live_analysis.backend.resolve_callable("write")
    assert len(e.value.candidates) > 1
    assert e.value.name == "write"


@live_only
def test_suffix_match_narrows(live_analysis):
    """Segment matching on the dotted name is deterministic, not fuzzy."""
    n = live_analysis.backend.resolve_callable("OnboardingOnboardingStep.action_validate_step")
    assert n.callable.endswith("action_validate_step")


@live_only
def test_value_resolves_within_a_callable(live_analysis):
    n = live_analysis.backend.resolve_value("invoice_id", within="PaymentPortal.invoice_transaction")
    assert n.kind == "parameter"
    assert n.name == "invoice_id"
    assert n.line > 0 and n.file.endswith(".py")


@live_only
def test_resolution_never_leaks_ordinals(live_analysis):
    n = live_analysis.backend.resolve_value("invoice_id", within="PaymentPortal.invoice_transaction")
    assert "formal_in" not in repr(n.kind)
    assert "formal_in" not in (n.name or "")


@live_only
def test_narrowing_by_class_resolves_what_the_bare_name_could_not(live_analysis):
    """The way out of an ``AmbiguousName`` has to actually work, or the message is a dead end."""
    n = live_analysis.backend.resolve_callable("write", in_class="AccountMove")
    assert n.callable == "addons.account.models.account_move.AccountMove.write"
    assert n.file.endswith("account_move.py") and n.line > 0


@live_only
def test_an_unresolvable_name_raises_without_suggesting_anything(live_analysis):
    """E8 in the error path: ``write`` is one character away and must not be mentioned."""
    with pytest.raises(SelectorNotInGraph) as e:
        live_analysis.backend.resolve_callable("writ")
    assert "callable not in graph: 'writ'" in str(e.value)
    assert "AccountMove" not in str(e.value)


@live_only
def test_ref_round_trips_through_get_source(live_analysis):
    """``ref`` is opaque, but opaque means "do not parse it", not "cannot use it": the one
    sanctioned use is handing it back, and that must work on the backend that produced it."""
    n = live_analysis.backend.resolve_callable("OnboardingOnboardingStep.action_validate_step")
    assert "def action_validate_step" in live_analysis.get_source(n.ref)


@live_only
def test_a_captured_global_is_not_labelled_a_parameter(live_analysis):
    """84% of the ``formal_in`` vertices on this application are captured module globals. They were
    all coming back ``kind="parameter"`` with the analyzer's ``"<global>:payment::AccessError"`` in
    ``name`` — internal vocabulary in a field E6 reserves for the caller's."""
    n = live_analysis.backend.resolve_value("AccessError", within="PaymentPortal.invoice_transaction")
    assert n.kind == "global"
    assert n.name == "AccessError" and n.defined_in
    assert "<global>" not in n.name and "::" not in n.name


@live_only
def test_an_ambiguous_within_is_narrowed_by_following_the_advice_it_gives(live_analysis):
    """Step 6's point: an error a caller can act on. ``within="write"`` matches 220 callables, and
    ``resolve_value`` accepts neither ``in_class=`` nor ``in_module=`` — so the message names the
    one way out that exists, and following it has to work."""
    with pytest.raises(AmbiguousName) as e:
        live_analysis.backend.resolve_value("vals", within="write")
    msg = str(e.value)
    assert "in_class=" not in msg and "in_module=" not in msg
    assert "within=" in msg

    n = live_analysis.backend.resolve_value("vals", within="AccountMove.write")
    assert n.callable == "addons.account.models.account_move.AccountMove.write"
    assert n.name == "vals"


@live_only
def test_only_a_callable_ref_round_trips_through_get_source(live_analysis):
    """The narrowed guarantee. A ``resolve_callable`` ref returns text; a ``resolve_value`` ref
    names a dataflow vertex with no span, so there is no text to return — on *either* backend, the
    local one included (it raises ``KeyError: … (no span)``, not a slice)."""
    callable_ref = live_analysis.backend.resolve_callable("OnboardingOnboardingStep.action_validate_step").ref
    assert "def action_validate_step" in live_analysis.get_source(callable_ref)

    value_ref = live_analysis.backend.resolve_value("invoice_id", within="PaymentPortal.invoice_transaction").ref
    with pytest.raises(NotImplementedError):
        live_analysis.get_source(value_ref)


@live_only
def test_the_candidate_domain_is_every_callable_in_the_application(live_analysis):
    """The domain, checked rather than asserted in prose.

    The Neo4j resolver pushes the match into Cypher; the local resolver filters a list it already
    holds. Two earlier defects in this leg came from backends agreeing on a *predicate* while
    running it against different *sets*, so pin the set: what ``write`` matches must be exactly the
    callables in ``get_callables_overview()`` — the domain both backends document — whose signature
    the policy would keep. Nothing extra from the graph, nothing missing from it.
    """
    expected = sorted(o.signature for o in live_analysis.get_callables_overview() if o.signature == "write" or o.signature.endswith(".write"))
    with pytest.raises(AmbiguousName) as e:
        live_analysis.backend.resolve_callable("write")
    assert e.value.candidates == expected
    assert len(expected) > 1


# ----------------------------------------------------------------------------------------------
# The policy, offline. No graph, no driver, no symbol table — see the module docstring for why
# that is a property of the design and not a convenience.
# ----------------------------------------------------------------------------------------------
def test_segment_match_is_on_boundaries_not_substrings():
    assert segment_match("execute", "db.cursor.execute")
    assert segment_match("cursor.execute", "db.cursor.execute")
    assert segment_match("db.cursor.execute", "db.cursor.execute")
    assert not segment_match("ute", "db.cursor.execute"), "a substring is not a segment"
    assert not segment_match("xecute", "db.cursor.execute")
    assert not segment_match("cursor", "db.cursor.execute"), "a suffix, not any segment"
    assert not segment_match("db.cursor", "db.cursor.execute")


def test_segment_match_takes_the_separator_of_the_vocabulary():
    assert segment_match("models/bar.py", "addons/foo/models/bar.py", sep="/")
    assert not segment_match("s/bar.py", "addons/foo/models/bar.py", sep="/")


def test_exact_match_wins_over_a_suffix_match():
    """``"a.write"`` is a name, not a choice between the thing it names and the things ending
    with it — otherwise a fully spelled name would be *more* ambiguous than a partial one."""
    assert resolve_name("a.write", ["a.write", "pkg.a.write"], kind="callable", narrow_with="x") == "a.write"


def test_one_survivor_resolves():
    assert resolve_name("execute", ["db.cursor.execute", "db.cursor.close"], kind="callable", narrow_with="x") == "db.cursor.execute"


def test_more_than_one_survivor_raises_carrying_all_of_them():
    with pytest.raises(AmbiguousName) as e:
        resolve_name("write", [f"m{i}.C.write" for i in range(9)], kind="callable", narrow_with="in_class=")
    assert e.value.name == "write"
    assert len(e.value.candidates) == 9
    assert e.value.candidates == sorted(e.value.candidates), "deterministic order, not row order"


def test_the_ambiguity_message_is_actionable_rather_than_a_wall_of_strings():
    """220 raw strings is not an error message. The message states the total, shows a few so the
    caller can see the *shape* of a full name, and names the keyword that narrows it; the complete
    list stays available as data on ``candidates``."""
    with pytest.raises(AmbiguousName) as e:
        resolve_name("write", [f"m{i}.C.write" for i in range(220)], kind="callable", narrow_with="in_class= or in_module=")
    msg = str(e.value)
    assert "220 callables match" in msg
    assert "in_class= or in_module=" in msg
    assert msg.count("';") + 1 <= AmbiguousName.SHOWN + 1
    assert "and 215 more" in msg
    assert len(e.value.candidates) == 220


def test_the_hint_does_not_repeat_a_keyword_the_caller_already_supplied():
    """"Narrow it with ``in_class=``" is not advice for someone who wrote ``in_class=``. The
    message offers only the ways out that are still open."""
    candidates = [CallableCandidate(f"m{i}.ResPartner.write", "m.ResPartner", "pkg/m.py") for i in range(3)]
    with pytest.raises(AmbiguousName) as e:
        resolve_callable_signature("write", candidates, in_class="ResPartner")
    msg = str(e.value)
    assert "in_class=" not in msg
    assert "in_module=" in msg and "naming more of the dotted path" in msg

    with pytest.raises(AmbiguousName) as both:
        resolve_callable_signature("write", candidates, in_class="ResPartner", in_module="pkg/m.py")
    assert "in_class=" not in str(both.value) and "in_module=" not in str(both.value)
    assert "naming more of the dotted path" in str(both.value)


def test_an_ambiguous_within_advises_a_keyword_resolve_value_actually_accepts():
    """``resolve_value(name, *, within)`` takes no ``in_class=`` / ``in_module=``, so an ambiguity
    raised while resolving ``within`` must not tell the caller to pass them."""

    def ambiguous(_name):
        raise AmbiguousName("write", ["a.C.write", "b.C.write"], kind="callable", narrow_with="in_class= or in_module=")

    with pytest.raises(AmbiguousName) as e:
        resolve_within(ambiguous, "write")
    msg = str(e.value)
    assert "in_class=" not in msg and "in_module=" not in msg
    assert "within=" in msg
    assert e.value.candidates == ["a.C.write", "b.C.write"], "the matches survive the re-raise"


def test_the_value_vocabulary_translates_the_analyzers_markers():
    """The three shapes a ``formal_in`` vertex's ``var`` takes, and what a caller sees of each."""
    assert value_candidate("invoice_id") == ("invoice_id", "parameter", "invoice_id", None)
    assert value_candidate("<global>:payment::AccessError") == ("payment.AccessError", "global", "AccessError", "payment")
    assert value_candidate("<capture>:result") == ("result", "capture", "result", None)


def test_two_globals_of_the_same_leaf_name_are_a_real_ambiguity_the_caller_can_resolve():
    """The 14,432-pair case: one callable captures the same leaf name from several modules. Nothing
    matches exactly, both match as suffixes, so it raises carrying both — and the qualified
    spelling the message points at resolves."""
    values = ["odoo.http._logger", "odoo.modules.loading._logger"]
    with pytest.raises(AmbiguousName) as e:
        resolve_value_name("_logger", values, within="m.C.f")
    assert sorted(e.value.candidates) == sorted(values)
    assert "within=" in str(e.value)
    assert resolve_value_name("odoo.http._logger", values, within="m.C.f") == "odoo.http._logger"


def test_a_parameter_wins_outright_over_a_global_that_ends_with_its_name():
    """Deliberate, and the same rule callables already follow: an exact match wins over a segment
    suffix, so a parameter literally named ``AccessError`` is not made ambiguous by a captured
    ``payment.AccessError``. Giving values a second, subtly different rule is precisely what the
    shared policy exists to prevent; the global stays addressable by its qualified name."""
    values = ["AccessError", "payment.AccessError"]
    assert resolve_value_name("AccessError", values, within="m.C.f") == "AccessError"
    assert resolve_value_name("payment.AccessError", values, within="m.C.f") == "payment.AccessError"


def test_no_match_raises_the_not_found_error_with_no_suggestions():
    with pytest.raises(SelectorNotInGraph) as e:
        resolve_name("writ", ["m.C.write"], kind="callable", narrow_with="x")
    assert "1 of 1 callable not in graph: 'writ'" in str(e.value)
    assert "write" not in str(e.value).replace("'writ'", "")


def test_the_resolver_imports_no_similarity_scoring():
    """E8 bans typo-tolerant matching outright — "not in the resolver, not in the error path". The
    cheapest way for it to creep back in is an import, so pin the module's *bindings* (prose about
    the ban is fine; a name you could call is not) and the import statements themselves."""
    src = pathlib.Path(resolve_module.__file__).read_text()
    assert not re.search(r"^\s*(import|from)\s+difflib", src, re.M), "E8 puts typo-tolerant matching out of scope"
    for banned in ("difflib", "get_close_matches", "SequenceMatcher"):
        assert not hasattr(resolve_module, banned), f"{banned} is bound in the resolver; E8 puts it out of scope"


def test_in_class_narrows_and_excludes_module_level_functions():
    candidates = [
        CallableCandidate("m.A.write", "m.A", "m.py"),
        CallableCandidate("m.B.write", "m.B", "m.py"),
        CallableCandidate("m.write", None, "m.py"),
    ]
    assert resolve_callable_signature("write", candidates, in_class="A") == "m.A.write"
    assert resolve_callable_signature("write", candidates, in_class="m.B") == "m.B.write"
    with pytest.raises(AmbiguousName):
        resolve_callable_signature("write", candidates)


def test_in_module_narrows_on_path_segments():
    candidates = [
        CallableCandidate("a.C.write", "a.C", "pkg/a.py"),
        CallableCandidate("b.C.write", "b.C", "pkg/sub/b.py"),
    ]
    assert resolve_callable_signature("write", candidates, in_module="sub/b.py") == "b.C.write"
    assert resolve_callable_signature("write", candidates, in_module="pkg/a.py") == "a.C.write"


def test_a_filter_that_removes_the_only_match_is_a_miss_not_a_silent_empty():
    candidates = [CallableCandidate("a.C.write", "a.C", "pkg/a.py")]
    with pytest.raises(SelectorNotInGraph):
        resolve_callable_signature("write", candidates, in_class="D")


def test_value_resolution_shares_the_policy():
    assert resolve_value_name("invoice_id", ["self", "invoice_id", "kwargs"], within="m.C.f") == "invoice_id"
    with pytest.raises(SelectorNotInGraph):
        resolve_value_name("nope", ["self"], within="m.C.f")


def test_an_ambiguous_value_names_within_as_the_way_to_narrow():
    with pytest.raises(AmbiguousName) as e:
        resolve_value_name("x", ["x", "x"], within="m.C.f")
    assert "within=" in str(e.value) and "'m.C.f'" in str(e.value)


# ----------------------------------------------------------------------------------------------
# The local backend's wiring. The shared policy is what stops the two backends disagreeing about
# *ambiguity*; what each still has to get right on its own is the candidate set it hands over and
# the ``ref`` it composes. Both are checked here against a real in-memory application, and above
# against the live graph, rather than against a stubbed driver.
# ----------------------------------------------------------------------------------------------
def test_local_resolution_domain_includes_nested_callables(py_local):
    """The domain is every callable ``get_callables_overview`` reports — closures included, which
    is the case a walk that stopped at class methods would silently drop."""
    assert py_local.resolve_callable("inner").callable == "src.app.Store.wrap.<locals>.inner"
    assert py_local.resolve_callable("Meta.tag").callable == "src.app.Store.Meta.tag"
    assert {o.signature for o in py_local.get_callables_overview()} >= {"src.app.Store.wrap.<locals>.inner", "src.app.Store.Meta.tag"}


def test_local_resolution_narrows_by_class_and_module(py_local):
    assert py_local.resolve_callable("key", in_class="Store").callable == "src.app.Store.key"
    assert py_local.resolve_callable("key", in_module="app.py").callable == "src.app.Store.key"
    with pytest.raises(SelectorNotInGraph):
        py_local.resolve_callable("key", in_class="Meta")


def test_local_resolution_reports_the_file_and_line(py_local):
    n = py_local.resolve_callable("src.app.Store.key")
    assert (n.file, n.line, n.kind, n.name) == ("src/app.py", 19, "callable", "key")
    assert "can://" not in n.callable


@pytest.fixture(scope="module")
def py_params(tmp_path_factory) -> PyCodeanalyzer:
    """A local backend over a real level-4 analyzer run — the shape ``resolve_value`` addresses.

    This fixture used to be hand-written, and it invented the input that made its own assertion
    pass: it spelled the body keys ``"formal_in:0"``, with no leading ``@``, which is a grammar the
    analyzer never emits. The ``ref`` composition it "proved" correct was in fact producing a
    double ``@`` against real output. So the vertices come from the analyzer now. The project is
    three callables and analysing it costs about a second warm; the alternative is a fixture that
    can agree with a defect.

    The source is chosen for the four shapes ``resolve_value`` has to tell apart: a parameter, a
    parameter that is *also* mutated (so it appears on ``formal_out`` under the same name), a
    captured module global, and a closure capture.
    """
    root = tmp_path_factory.mktemp("params")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            from decimal import Decimal

            LIMIT = 100


            class Portal:
                def charge(self, invoice_id, items):
                    items.append(invoice_id)
                    total = Decimal(len(items))
                    if total > LIMIT:
                        total = LIMIT
                    return total
            """
        ).lstrip()
    )
    return PyCodeanalyzer(
        project_dir=root,
        analysis_level=AnalysisLevel.system_dependency_graph,
        analysis_json_path=None,
        eager_analysis=False,
        cache_dir=root / ".cache",
    )


def test_the_fixture_carries_the_grammar_the_analyzer_really_emits(py_params):
    """Guard on the fixture itself, because the last one did not have one: synthetic body keys
    carry a leading ``@``, and the values entering ``charge`` include a global and a mutated
    parameter, or the tests below are testing a shape that does not occur."""
    c = py_params.application.symbol_table["src/pay.py"].types["src.pay.Portal"].callables["charge"]
    formal_in = {k: n.of for k, n in c.body.items() if n.kind == "formal_in"}
    assert all(k.startswith("@formal_in:") for k in formal_in)
    assert sorted(formal_in.values()) == ["<global>:pay::Decimal", "<global>:pay::LIMIT", "invoice_id", "items", "self"]
    assert "invoice_id" in {n.of for n in c.body.values() if n.kind == "formal_out"}


def test_local_value_resolution_addresses_a_parameter_by_name(py_params):
    n = py_params.resolve_value("invoice_id", within="Portal.charge")
    assert (n.kind, n.name, n.defined_in) == ("parameter", "invoice_id", None)
    assert (n.file, n.line, n.callable) == ("src/pay.py", 7, "src.pay.Portal.charge")
    assert "formal_in" not in n.kind and "formal_in" not in n.name


def test_the_domain_is_formal_in_only(py_params):
    """``invoice_id`` is on the ``formal_out`` vertex too (it is mutated). If the domain were every
    body node carrying an ``of``, every such parameter would be ambiguous with its own exit
    value."""
    assert py_params.resolve_value("invoice_id", within="Portal.charge").kind == "parameter"


def test_local_value_ref_is_the_emitters_own_id(py_params):
    """#320: the id joins only if it is the emitter's ``_global_ordinal`` — the callable's
    ``can://`` id and the body key, with **one** ``@`` between them and none added when the key
    already carries its own. Checked against the analyzer's real key, not an invented one."""
    c = py_params.application.symbol_table["src/pay.py"].types["src.pay.Portal"].callables["charge"]
    key = next(k for k, n in c.body.items() if n.kind == "formal_in" and n.of == "invoice_id")
    n = py_params.resolve_value("invoice_id", within="Portal.charge")
    assert n.ref == f"{c.id}{key}" == _global_ordinal(c.id, key)
    assert "@@" not in n.ref


def test_the_composition_matches_the_emitters_own_rule():
    """``body_node_id`` exists to track ``codeanalyzer/neo4j/project.py``'s ``_global_ordinal``.
    Pin it against that function directly, so an upstream change to the grammar fails here rather
    than producing ids that silently name nothing."""
    for key in ("@entry", "@exit", "@formal_in:1", "@formal_out:0", "9:8", "12:16"):
        assert body_node_id("can://python/app/m.py/C/f(self)", key) == _global_ordinal("can://python/app/m.py/C/f(self)", key)


def test_a_captured_global_is_labelled_and_named_honestly(py_params):
    """84% of the values entering a callable on a real application are captured globals. They stay
    in the domain — dropping them would make a legitimately-named global unresolvable with no
    signal — but they are not parameters, and ``<global>:pay::LIMIT`` is not a name a caller wrote."""
    n = py_params.resolve_value("LIMIT", within="Portal.charge")
    assert (n.kind, n.name, n.defined_in) == ("global", "LIMIT", "pay")
    assert "<global>" not in n.name and "::" not in n.name
    assert "@formal_in:" in n.ref and "@@" not in n.ref


def test_a_global_is_addressable_by_its_qualified_name_too(py_params):
    """The qualified spelling is what makes an ambiguity between two modules' globals resolvable,
    so it has to actually resolve."""
    assert py_params.resolve_value("pay.LIMIT", within="Portal.charge").name == "LIMIT"


def test_a_callable_with_no_address_raises_rather_than_handing_back_a_sentinel():
    """``PyCallable.id`` defaults to ``""`` and ``start_line`` to ``-1``. Copied into a
    ``SliceNode`` those are ``ref=""`` and ``line=-1`` — an address that fails somewhere else,
    later, which is worse than failing here."""
    fn = PyCallable(name="f", path="m.py", signature="m.C.f")
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"m.py": PyModule(file_path="m.py", module_name="m", types={"m.C": PyClass(name="C", signature="m.C", callables={"f": fn})})})
    with pytest.raises(KeyError, match="no usable address"):
        backend.resolve_callable("f")


def test_two_callables_sharing_a_signature_raise_rather_than_resolving_arbitrarily():
    """Latent: 15,549 signatures on a real application, all distinct. But a dict keyed by signature
    silently keeps the last, and "resolve arbitrarily" is what this layer exists not to do."""
    twins = {n: PyCallable(name="f", path="m.py", signature="m.C.f", id=f"can://x/{n}", start_line=1) for n in ("f", "f2")}
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"m.py": PyModule(file_path="m.py", module_name="m", types={"m.C": PyClass(name="C", signature="m.C", callables=twins)})})
    with pytest.raises(ValueError, match="more than one analysed callable"):
        backend.resolve_callable("f")


def test_local_value_resolution_misses_and_ambiguity(py_params):
    with pytest.raises(SelectorNotInGraph):
        py_params.resolve_value("no_such_value", within="Portal.charge")
    with pytest.raises(SelectorNotInGraph):
        py_params.resolve_value("invoice_id", within="NoSuchCallable")


# ----------------------------------------------------------------------------------------------
# Fix round: ``in_module`` in the vocabulary a caller reads, and errors that blame the right
# argument.
# ----------------------------------------------------------------------------------------------
def test_in_module_takes_the_dotted_module_name_too():
    """A signature reads ``odoo.tools.mail.email_domain_extract`` and ``in_class=`` is a dotted
    suffix already, so the module named the way it appears in every signature must work. A
    dotted ``"mail"`` names only a module whose dotted name *ends* in ``.mail`` -- not everything
    under a ``mail/`` package -- so the widening adds no ambiguity."""
    candidates = [
        CallableCandidate("odoo.tools.mail.extract", None, "odoo/tools/mail.py"),
        CallableCandidate("odoo.addons.mail.models.thread.extract", "odoo.addons.mail.models.thread.Thread", "odoo/addons/mail/models/thread.py"),
    ]
    for spelling in ("odoo.tools.mail", "tools.mail", "mail", "odoo/tools/mail.py", "tools/mail.py", "mail.py"):
        assert resolve_callable_signature("extract", candidates, in_module=spelling) == "odoo.tools.mail.extract", spelling
    assert resolve_callable_signature("extract", candidates, in_module="models.thread") == "odoo.addons.mail.models.thread.extract"
    assert module_dotted("odoo/tools/mail.py") == "odoo.tools.mail"
    assert module_dotted("pkg/__init__.py") == "pkg"


def test_a_keyword_that_excludes_every_match_is_blamed_not_the_name():
    """``callers_of("x", in_module=...)`` failed with ``callable not in graph: 'x'`` when ``x``
    existed and the module was what missed. The raise names the argument that actually missed."""
    candidates = [CallableCandidate("odoo.tools.mail.x", None, "odoo/tools/mail.py")]
    with pytest.raises(SelectorNotInGraph) as by_module:
        resolve_callable_signature("x", candidates, in_module="odoo/tools/other.py")
    assert by_module.value.kind == "in_module" and by_module.value.missing == ["odoo/tools/other.py"]
    assert "'x' matches 1 callable" in str(by_module.value)
    with pytest.raises(SelectorNotInGraph) as by_class:
        resolve_callable_signature("x", candidates, in_class="Nope")
    assert by_class.value.kind == "in_class"
    with pytest.raises(SelectorNotInGraph) as by_name:
        resolve_callable_signature("nope", candidates, in_module="odoo/tools/mail.py")
    assert by_name.value.kind == "callable" and by_name.value.missing == ["nope"]


@live_only
def test_in_module_resolves_the_dotted_spelling_on_the_graph(live_analysis):
    dotted = live_analysis.callers_of("email_domain_extract", in_module="odoo.tools.mail")
    by_path = live_analysis.callers_of("email_domain_extract", in_module="odoo/tools/mail.py")
    assert [c.ref for c in dotted] == [c.ref for c in by_path]
    with pytest.raises(SelectorNotInGraph) as e:
        live_analysis.callers_of("email_domain_extract", in_module="odoo/tools/other.py")
    assert e.value.kind == "in_module"


def test_the_facade_exposes_resolution(py_local):
    """Every other accessor is on the facade; ``py.resolve_callable`` was an ``AttributeError``."""
    facade = object.__new__(PythonAnalysis)
    facade.backend = py_local
    assert facade.resolve_callable("key", in_module="app.py").callable == "src.app.Store.key"
    assert facade.resolve_callable("key", in_module="src.app").callable == "src.app.Store.key"
    with pytest.raises(SelectorNotInGraph):
        facade.resolve_value("nope", within="key")
