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

"""``analysis_level=`` has to reach the analyzer, or it is a parameter that lies.

The SDK accepted the parameter, stored it, and built ``AnalysisOptions`` without it — so every
local analysis ran at the analyzer's default (level 1) whatever the caller asked for. Nothing
caught it because ``cfg``/``cdg``/``ddg`` being empty and there being no ``formal_in`` vertex is
*also* what a correct level-1 run looks like: the only way to tell the two apart is to ask for a
higher level and check the higher level's artifacts appear.

These tests run the real analyzer over a three-callable project. That costs a few seconds; the
alternative is asserting the option plumbing against a mock, which is exactly the class of
evidence that let this defect ship in the first place.
"""

from __future__ import annotations

import textwrap

import pytest

from cldk.analysis import AnalysisLevel
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer, analyzer_level


@pytest.fixture(scope="module")
def tiny_project(tmp_path_factory):
    """A project small enough to analyse at level 4 in a test, with the shapes the dataflow
    levels are about: a parameter, a branch, and a module global read from inside a method."""
    root = tmp_path_factory.mktemp("levels")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            LIMIT = 100


            class Portal:
                def charge(self, invoice_id):
                    amount = invoice_id * 2
                    if amount > LIMIT:
                        amount = LIMIT
                    return amount
            """
        ).lstrip()
    )
    return root


@pytest.fixture(scope="module")
def analyse(tiny_project, tmp_path_factory):
    """One analyzer run per level, shared across the tests below — each is a few seconds."""
    cache = tmp_path_factory.mktemp("level-caches")
    done: dict = {}

    def run(level) -> PyCodeanalyzer:
        if level not in done:
            done[level] = PyCodeanalyzer(
                project_dir=tiny_project,
                analysis_level=level,
                analysis_json_path=None,
                eager_analysis=False,
                cache_dir=cache / str(level).replace(" ", "_"),
            )
        return done[level]

    return run


def _charge(backend):
    return backend.application.symbol_table["src/pay.py"].types["src.pay.Portal"].callables["charge"]


# ----------------------------------------------------------------------------------------------
# The mapping, pinned against the analyzer's own documented vocabulary.
# ----------------------------------------------------------------------------------------------
def test_every_sdk_level_maps_to_an_analyzer_level():
    """``codeanalyzer --analysis-level``: 1 = symbol table + Jedi call graph, 2 = + defuse-linker
    call graph, 3 = + intraprocedural dataflow, 4 = + interprocedural SDG. The SDK's four names
    line up with those four integers in order."""
    assert [analyzer_level(lvl) for lvl in AnalysisLevel] == [1, 2, 3, 4]


def test_the_member_name_spelling_is_accepted_too():
    """``AnalysisLevel.call_graph.value`` is ``"call graph"``, but callers (and this repo's own
    tests) write ``"call_graph"``. Both name the same level."""
    assert analyzer_level("call_graph") == analyzer_level(AnalysisLevel.call_graph) == 2
    assert analyzer_level("system_dependency_graph") == 4


def test_an_unknown_level_is_rejected_rather_than_silently_defaulted():
    with pytest.raises(ValueError, match="unknown analysis_level"):
        analyzer_level("deepest")


# ----------------------------------------------------------------------------------------------
# The plumbing, against a real analyzer run.
# ----------------------------------------------------------------------------------------------
def test_call_graph_level_produces_no_dataflow(analyse):
    """The negative half of the check: dataflow is absent below level 3, so the positive half
    below is evidence of the level arriving and not of the analyzer emitting it unconditionally."""
    c = _charge(analyse(AnalysisLevel.call_graph))
    assert (c.cfg, c.cdg, c.ddg) == ([], [], [])
    assert not [k for k, n in (c.body or {}).items() if n.kind == "formal_in"]


def test_system_dependency_graph_level_produces_dataflow(analyse):
    """The defect, stated as a test: at level 4 the callable carries a CFG, a DDG and one
    ``formal_in`` vertex per parameter *and* per transitively-read global."""
    c = _charge(analyse(AnalysisLevel.system_dependency_graph))
    assert c.cfg and c.ddg
    formals = {k: n.of for k, n in c.body.items() if n.kind == "formal_in"}
    assert sorted(formals.values()) == ["<global>:pay::LIMIT", "invoice_id", "self"]


def test_real_body_keys_carry_a_leading_at_on_the_synthetic_vertices(analyse):
    """The grammar every hand-written fixture in this suite has to match: ``line:col`` statement
    keys are bare, every synthetic vertex key starts with ``@``."""
    c = _charge(analyse(AnalysisLevel.system_dependency_graph))
    for key, node in c.body.items():
        assert key.startswith("@") is (node.kind not in ("statement", "call", "branch", "loop", "return")), key


def test_the_call_graph_is_built_at_every_level_that_has_one(analyse):
    """``call_graph`` was gated on ``== AnalysisLevel.call_graph``, so asking for a *deeper*
    level than the one that builds a call graph used to hand back ``None``."""
    assert analyse(AnalysisLevel.symbol_table).call_graph is None
    assert analyse(AnalysisLevel.system_dependency_graph).call_graph is not None
