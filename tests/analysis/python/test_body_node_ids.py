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

"""``body_node_id`` against the analyzer's own ``BodyNode.id`` (codeanalyzer-python 1.4.1, #180).

Until 1.4.1 ``BodyNode`` carried no id in ``analysis.json``, so the local backend composed one from
``callable.id`` and the body key by copying the emitter's ``_global_ordinal`` rule, and leg 1.5
could only claim the two agreed by construction. 1.4.1 stamps ``BodyNode.id`` (and
``PyParameter.id``) from the same ``codeanalyzer.schema.ids.global_ordinal``, so agreement is now a
per-run assertion over a real level-4 analysis: every id the analyzer wrote must be the one the SDK
composes. A mismatch here is a finding about one of the two rules, never something to paper over
on the SDK side.

The fixture is small enough to analyse at level 4 in a test and shaped to produce every vertex
kind leg 1.5 addresses: parameters (``formal_in``), a return (``formal_out``), a call between two
callables (``actual_in`` / ``actual_out``), plus a nested function and a nested class so the walk
reaches callables at every depth ``_iter_callables`` enumerates.
"""

import textwrap

import pytest

from cldk.analysis import AnalysisLevel
from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer, body_node_id


@pytest.fixture(scope="module")
def l4(tmp_path_factory) -> PyCodeanalyzer:
    root = tmp_path_factory.mktemp("body-ids")
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text(
        textwrap.dedent(
            """
            LIMIT = 100


            def helper(x):
                return x + 1


            class Portal:
                class Meta:
                    def tag(self, n):
                        return n

                def charge(self, invoice_id):
                    def bump(v):
                        return v + 1

                    total = invoice_id * 2
                    amount = helper(total)
                    if amount > LIMIT:
                        amount = bump(LIMIT)
                    return amount
            """
        ).lstrip()
    )
    return PyCodeanalyzer(
        project_dir=root,
        analysis_level=AnalysisLevel.system_dependency_graph,
        analysis_json_path=None,
        eager_analysis=False,
        cache_dir=tmp_path_factory.mktemp("cache-body-ids"),
    )


def _body_nodes(backend):
    for c, *_ in backend._iter_callables():
        for key, node in (c.body or {}).items():
            yield c, key, node


def test_the_fixture_produces_the_vertex_kinds_the_assertion_is_about(l4):
    kinds = {node.kind for *_, node in _body_nodes(l4)}
    assert {"entry", "exit", "formal_in", "formal_out", "actual_in", "actual_out"} <= kinds, kinds


def test_every_analyzer_stamped_id_is_the_id_the_sdk_composes(l4):
    stamped = [(c, key, node) for c, key, node in _body_nodes(l4) if node.id]
    assert stamped
    mismatches = [(node.kind, key, node.id, body_node_id(c.id, key)) for c, key, node in stamped if body_node_id(c.id, key) != node.id]
    assert mismatches == []


def test_no_body_node_is_left_without_an_id(l4):
    """Which kinds, if any, the analyzer leaves unstamped -- ``formal_in`` / ``formal_out`` are the
    ones leg 1.5 addresses by id, so an unstamped one there is a finding, not a skip."""
    unstamped = sorted({(node.kind, key) for _, key, node in _body_nodes(l4) if not node.id})
    assert unstamped == []


def test_every_parameter_carries_its_formal_in_id(l4):
    seen = 0
    for c, *_ in l4._iter_callables():
        for i, p in enumerate(c.parameters or []):
            seen += 1
            assert p.id == body_node_id(c.id, f"@formal_in:{i}"), (c.signature, p.name, p.id)
    assert seen
