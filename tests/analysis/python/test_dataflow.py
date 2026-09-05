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

"""Per-callable control and data flow: ``get_cfg`` / ``get_cdg`` / ``get_ddg`` (leg 1.5, Task 5).

Three groups, separated by what evidence each is worth:

* **Live graph.** The Neo4j backend answered by a real application (odoo-slim-19), under the same
  environment variables as ``test_e2e_neo4j_live.py`` — see ``conftest.py``'s ``live_analysis``.
  Strictly read-only::

      CLDK_TEST_NEO4J_URI=bolt://localhost:7688 \
      CLDK_TEST_NEO4J_USER=neo4j \
      CLDK_TEST_NEO4J_PASSWORD=cldkleg1test \
      CLDK_TEST_NEO4J_APP=odoo-slim-19 \
      uv run pytest tests/analysis/python/test_dataflow.py

* **Real analyzer run.** The local backend over a three-callable project analysed at level 4. The
  local path only became capable of dataflow at all when ``analysis_level`` started reaching the
  analyzer (see ``test_analysis_level.py``), so a mock here would assert the plumbing that was
  already wrong once.

* **The level contract.** Dataflow exists only at ``program_dependency_graph`` and deeper. A
  backend built below that must say so, because ``[]`` there would be indistinguishable from a
  callable that genuinely has no data dependence — the ambiguous empty D7 forbids.
"""

from __future__ import annotations

import os
import re
import textwrap

import pytest

from codeanalyzer.neo4j.project import _project_program_graphs
from codeanalyzer.neo4j.rows import RowBuilder

from cldk.analysis import AnalysisLevel
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.utils.exceptions import AmbiguousName, CodeanalyzerUsageException

live_only = pytest.mark.skipif(
    not os.environ.get("CLDK_TEST_NEO4J_URI"),
    reason="no live Neo4j (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)",
)


# ----------------------------------------------------------------------------------------------
# The three the plan names, over the live graph.
# ----------------------------------------------------------------------------------------------
@live_only
def test_ddg_for_a_real_callable(live_analysis, busy_callable):
    edges = live_analysis.get_ddg(busy_callable)
    assert edges, "a busy callable has data dependence"
    assert all(e.src and e.dst for e in edges)
    assert any(e.var for e in edges), "DDG edges carry the variable"


@live_only
def test_ddg_prov_distinguishes_evidence(live_analysis, busy_callable):
    """``prov`` separates syntactic from alias-aware evidence."""
    provs = {p for e in live_analysis.get_ddg(busy_callable) for p in (e.prov or [])}
    assert provs, "every edge carries provenance"
    assert provs <= {"ssa", "reaching-defs", "points-to"}, f"unexpected prov: {provs}"


@live_only
def test_cfg_is_bounded_to_one_callable(live_analysis, busy_callable):
    assert len(live_analysis.get_cfg(busy_callable)) < 10_000


# ----------------------------------------------------------------------------------------------
# Live: the domain, stated and then checked. One callable's own edges, and nothing else's.
# ----------------------------------------------------------------------------------------------
@live_only
def test_every_endpoint_belongs_to_the_named_callable(live_analysis, busy_callable):
    """The bound is structural, not a cap: every endpoint id is prefixed by the resolved
    callable's own id, so no edge can reach a body node another callable owns."""
    node = live_analysis.backend.resolve_callable(busy_callable)
    for edges in (live_analysis.get_cfg(busy_callable), live_analysis.get_cdg(busy_callable), live_analysis.get_ddg(busy_callable)):
        assert edges
        assert all(e.src.startswith(node.ref + "@") and e.dst.startswith(node.ref + "@") for e in edges)


@live_only
def test_cfg_edges_carry_their_kind(live_analysis, busy_callable):
    kinds = {e.kind for e in live_analysis.get_cfg(busy_callable)}
    assert kinds and all(kinds), "a CFG edge without a kind cannot be read"


@live_only
def test_an_ambiguous_callable_name_raises_rather_than_guessing(live_analysis):
    """Resolution is Task 4's, not a second path: ``write`` is ambiguous here exactly as it is
    for ``resolve_callable``, and ``in_class=`` is the documented way out."""
    with pytest.raises(AmbiguousName):
        live_analysis.get_ddg("write")
    assert live_analysis.get_ddg("write", in_class="AccountMove") is not None


# ----------------------------------------------------------------------------------------------
# The local backend, over a real level-4 analyzer run.
# ----------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tiny_project(tmp_path_factory):
    """Small enough to analyse at level 4 in a test, with the shapes dataflow is about: a
    parameter, a branch, a loop and a module global read from inside a method."""
    root = tmp_path_factory.mktemp("dataflow")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            LIMIT = 100


            class Portal:
                def charge(self, invoice_id):
                    amount = invoice_id * 2
                    for _ in range(3):
                        amount = amount + 1
                    if amount > LIMIT:
                        amount = LIMIT
                    return amount
            """
        ).lstrip()
    )
    return root


def _backend(project, cache, level) -> PyCodeanalyzer:
    return PyCodeanalyzer(
        project_dir=project,
        analysis_level=level,
        analysis_json_path=None,
        eager_analysis=False,
        cache_dir=cache,
    )


@pytest.fixture(scope="module")
def local_l4(tiny_project, tmp_path_factory) -> PyCodeanalyzer:
    return _backend(tiny_project, tmp_path_factory.mktemp("cache-l4"), AnalysisLevel.system_dependency_graph)


@pytest.fixture(scope="module")
def local_l2(tiny_project, tmp_path_factory) -> PyCodeanalyzer:
    return _backend(tiny_project, tmp_path_factory.mktemp("cache-l2"), AnalysisLevel.call_graph)


def test_local_backend_answers_all_three_graphs(local_l4):
    assert local_l4.get_cfg("Portal.charge")
    assert local_l4.get_cdg("Portal.charge")
    ddg = local_l4.get_ddg("Portal.charge")
    assert ddg and any(e.var == "amount" for e in ddg)
    assert {p for e in ddg for p in e.prov} <= {"ssa", "reaching-defs", "points-to"}


def test_the_local_answer_is_edge_for_edge_what_the_graph_would_hold(local_l4):
    """The parity anchor, and the reason it is shaped this way.

    Twice in this leg two backends agreed on a *predicate* while disagreeing about the *set* it
    ran against. Comparing the two backends directly would need a second populated database, and
    this repository's live graph is read-only. So compare against the thing that stands between
    them instead: ``codeanalyzer.neo4j.project`` is the emitter that produces the very
    ``PY_CFG_NEXT`` / ``PY_CDG`` / ``PY_DDG`` rows the Neo4j backend's Cypher reads back. If the
    local backend's answer equals the emitter's rows edge for edge — endpoints, ``kind``, ``var``
    and ``prov`` included — then the two backends are reporting one set in one vocabulary.

    What this does *not* establish is that the Cypher matches every row it should on a real
    database; the live tests above are what covers that.
    """
    rows = RowBuilder()
    _project_program_graphs(rows, local_l4.application, {}, {})
    edges = rows.finish().edges

    def emitted(rel, *props):
        return sorted((e.from_ref.value, e.to_ref.value, *(e.props.get(k) for k in props)) for e in edges if e.type == rel)

    assert sorted((e.src, e.dst, e.kind) for e in local_l4.get_cfg("Portal.charge")) == emitted("PY_CFG_NEXT", "kind")
    assert sorted((e.src, e.dst) for e in local_l4.get_cdg("Portal.charge")) == emitted("PY_CDG")
    assert sorted((e.src, e.dst, e.var, e.prov) for e in local_l4.get_ddg("Portal.charge")) == emitted("PY_DDG", "var", "prov")


def test_local_endpoints_round_trip_through_get_source(local_l4):
    """A statement endpoint is an address, not an opaque token: it is the same ``node_id``
    ``get_source`` takes.

    Only the real statements — the synthetic bookends (``…@entry``, ``…@exit``) are body nodes
    with no span, and ``get_source`` raises on those by design rather than inventing text. The two
    are told apart by the shape of the key after the last ``@``: ``line:col`` for a statement, a
    word for a synthetic vertex (``codeanalyzer.neo4j.project._global_ordinal``).
    """
    stmts = [e.src for e in local_l4.get_cfg("Portal.charge") if re.fullmatch(r"\d+:\d+", e.src.rsplit("@", 1)[-1])]
    assert stmts
    assert all(local_l4.get_source(s).strip() for s in stmts)


def test_a_callable_with_no_dependence_is_an_honest_empty(local_l4):
    """``[]`` at level 4 means "no data dependence", and must stay available to mean that."""
    assert local_l4.get_cfg("Portal.charge")  # the callable is analysed
    assert isinstance(local_l4.get_ddg("Portal.charge"), list)


# ----------------------------------------------------------------------------------------------
# The level contract (D7): below level 3 there is no dataflow, and an empty list would lie.
# ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("accessor", ["get_cfg", "get_cdg", "get_ddg"])
def test_below_level_three_the_backend_refuses_rather_than_returning_empty(local_l2, accessor):
    with pytest.raises(CodeanalyzerUsageException) as e:
        getattr(local_l2, accessor)("Portal.charge")
    assert "program_dependency_graph" in str(e.value), "the error names the level required"
    assert "call_graph" in str(e.value), "the error names the level in use"
