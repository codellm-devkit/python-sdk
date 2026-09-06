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

"""Task 10 review finding A: the ``PY_RESOLVES_TO`` capability probe.

``PyCallsite.callee_signature = None`` collapses "genuinely unresolved" and "this graph carries
no per-site resolution data at all" (see ``PythonAnalysisBackend.get_callsites_for``'s docstring).
``PyNeo4jBackend.has_resolution_edges`` is the mitigation: probed once at construction (same "one
round trip" pattern as the schema probe in ``test_schema_probe.py``), it tells a caller which of
those two situations they are in without raising -- a level-1 graph with zero such edges is a
legitimate, working graph, not an error.
"""

from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend


def test_has_resolution_edges_true_when_graph_carries_any(fake_driver):
    """At least one PY_RESOLVES_TO edge anywhere in this application's scope -> True."""
    fake_driver.responder = lambda query, params: [{"s": {"id": "x"}}] if "PY_RESOLVES_TO" in query else []
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert backend.has_resolution_edges is True


def test_has_resolution_edges_false_on_a_level_1_graph(fake_driver):
    """No responder set -> every non-schema query (including the probe) comes back empty, the
    default FakeDriver shape for a graph this backend can talk to but has no resolution data."""
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert backend.has_resolution_edges is False


def test_probe_is_scoped_to_this_applications_modules(fake_driver):
    """The probe query filters on ``s.id STARTS WITH $prefix`` -- verify the Cypher actually does,
    not just that the boolean comes back right."""
    seen_queries = []

    def responder(query, params):
        seen_queries.append((query, params))
        return []

    fake_driver.responder = responder
    PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    probe_calls = [(q, p) for q, p in seen_queries if "PY_RESOLVES_TO" in q]
    assert len(probe_calls) == 1
    query, params = probe_calls[0]
    assert "s.id STARTS WITH $prefix" in query
    assert params["prefix"] == "can://python/app/"


def test_probe_does_not_raise_on_a_legitimate_empty_result(fake_driver):
    """A level-1 graph is legitimate output, not an error -- construction must still succeed."""
    PyNeo4jBackend._from_driver(fake_driver, application_name="app")  # no raise
