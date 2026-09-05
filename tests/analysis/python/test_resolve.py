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

import pytest

from cldk.analysis.commons import resolve as resolve_module

from cldk.analysis.commons.resolve import (
    CallableCandidate,
    resolve_callable_signature,
    resolve_name,
    resolve_value_name,
    segment_match,
)
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.models.python import BodyNode, PyApplication, PyCallable, PyClass, PyModule
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


@pytest.fixture
def py_params(tmp_path) -> PyCodeanalyzer:
    """A local backend over one method with real ``formal_in`` vertices — the shape ``resolve_value``
    addresses. Built here rather than bolted onto the shared ``locate`` fixture, whose body specs
    exist to pin span containment and carry no ``of``."""
    fn = PyCallable(
        name="charge",
        path="src/pay.py",
        signature="src.pay.Portal.charge",
        id="can://python/app/src/pay.py/Portal/charge(self,invoice_id)",
        start_line=7,
        end_line=9,
        body={
            "formal_in:0": BodyNode(kind="formal_in", of="self"),
            "formal_in:1": BodyNode(kind="formal_in", of="invoice_id"),
            "formal_out:0": BodyNode(kind="formal_out", of="<return>"),
            # The same name again on the exit vertex: proof the domain is formal_in only, or this
            # parameter would be ambiguous with its own outgoing value.
            "formal_out:1": BodyNode(kind="formal_out", of="invoice_id"),
            "8:8": BodyNode(kind="statement"),
        },
    )
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(
        symbol_table={"src/pay.py": PyModule(file_path="src/pay.py", module_name="src.pay", types={"src.pay.Portal": PyClass(name="Portal", signature="src.pay.Portal", callables={"charge": fn})})}
    )
    backend.project_dir = tmp_path
    return backend


def test_local_value_resolution_addresses_a_parameter_by_name(py_params):
    n = py_params.resolve_value("invoice_id", within="Portal.charge")
    assert (n.kind, n.name) == ("parameter", "invoice_id")
    assert (n.file, n.line, n.callable) == ("src/pay.py", 7, "src.pay.Portal.charge")
    assert "formal_in" not in n.kind and "formal_in" not in n.name


def test_local_value_ref_is_the_emitters_own_id(py_params):
    """#320: the id joins only if it is ``<callable can:// id>@<body key>``. Composed from
    ``callable.id``, never from ``callable.signature``, and never invented here."""
    n = py_params.resolve_value("invoice_id", within="Portal.charge")
    assert n.ref == "can://python/app/src/pay.py/Portal/charge(self,invoice_id)@formal_in:1"


def test_local_value_resolution_misses_and_ambiguity(py_params):
    with pytest.raises(SelectorNotInGraph):
        py_params.resolve_value("no_such_value", within="Portal.charge")
    with pytest.raises(SelectorNotInGraph):
        py_params.resolve_value("invoice_id", within="NoSuchCallable")
