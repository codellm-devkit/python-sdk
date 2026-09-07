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

"""A per-callable graph must contain its self-loops (#349).

A data-dependence self-loop is a statement that reads a variable it also redefines. Written as two
containment patterns, ``(c)-[:PY_HAS_BODY_NODE]->(s)-[r]->(d)<-[:PY_HAS_BODY_NODE]-(c)`` binds the
same ``PY_HAS_BODY_NODE`` relationship twice when ``s`` and ``d`` are one node, and Cypher's
relationship-uniqueness rule drops the row. The second hop is therefore a *predicate*.

The defect was invisible from inside: ``total`` is counted from the same match, so the short page
agreed with its own count and reported ``complete``. Only a comparison against the graph shows it.
"""

import os

import pytest

_URI = os.environ.get("CLDK_TEST_NEO4J_URI")

pytestmark = pytest.mark.skipif(
    not _URI,
    reason="needs a live Python graph: set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD",
)


def test_the_query_does_not_bind_the_containment_relationship_twice():
    """The shape check, which needs no graph and is the regression guard proper."""
    from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

    match = PyNeo4jBackend._OWN_EDGES.format(rel="PY_DDG")
    assert match.count("PY_HAS_BODY_NODE") == 2, "both hops are still there"
    assert "<-[:PY_HAS_BODY_NODE]-(c)" not in match, (
        "the second containment hop is a pattern again, so Cypher's relationship-uniqueness rule "
        "will silently drop every self-loop edge (#349)"
    )
    assert "WHERE (c)-[:PY_HAS_BODY_NODE]->(d)" in match


def test_a_callables_ddg_contains_its_self_loops(live_analysis):
    """Against the live graph: the page must hold every self-loop the graph holds."""
    backend = live_analysis.backend
    rows = backend._run(
        "MATCH (c:PyCallable) WHERE c.id STARTS WITH $prefix "
        "MATCH (c)-[:PY_HAS_BODY_NODE]->(s:PyBodyNode)-[r:PY_DDG]->(s) "
        "RETURN c.signature AS sig, count(r) AS loops ORDER BY loops DESC LIMIT 1",
        prefix=backend._scope_prefix,
    )
    if not rows or not rows[0]["loops"]:
        pytest.skip("this graph has no data-dependence self-loop to check")
    sig, expected = rows[0]["sig"], rows[0]["loops"]

    page = backend.get_ddg(sig, page_size=1_000_000)
    returned = sum(1 for e in page.edges if e.src == e.dst)
    assert returned == expected, f"{sig}: {returned} self-loops returned, {expected} in the graph"
    assert page.total == len(page.edges)
    assert page.complete
