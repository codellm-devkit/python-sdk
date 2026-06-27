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

"""Integration parity tests: the read-only Neo4j backend vs the in-memory backend.

These assert that :class:`PyNeo4jBackend` answers every query **identically** to the canonical
:class:`PyCodeanalyzer` (analysis.json) backend on the same project — the definition of the
"1-to-1 map". The harness loads the graph out of band, in-process, via the analyzer's own
``emit_neo4j`` (the same projection a cloud deployment would run), then queries it read-only.

The whole module is skipped unless a Neo4j server is reachable. Point the tests at one with:

    CLDK_TEST_NEO4J_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=test \
    pytest tests/analysis/python/test_python_neo4j_backend.py

(e.g. `docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5`).

Parity is asserted modulo the projection's documented-lossy fields (see
``cldk.analysis.python.neo4j.reconstruct``): comments collapse to a docstring, and
``PyVariableDeclaration`` loses ``value`` and its column span. The ``norm`` helper strips exactly
those before comparing; everything else must match byte-for-byte.
"""

import logging
import os

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
APP_NAME = "cldk_py_parity"

MODELS_PY = '''\
"""Module docstring for models."""

GLOBAL_LIMIT = 100


class Entity:
    """Base entity."""

    registry = "default"

    def __init__(self, name: str, tag: str = "x"):
        self.name = name

    def describe(self) -> str:
        return self.name

    class Meta:
        ordering = "name"


class User(Entity):
    def describe(self) -> str:
        return greet(self.name)


def greet(who: str) -> str:
    def _decorate(s):
        return s.upper()

    return _decorate(f"hi {who}")
'''

SERVICE_PY = '''\
from .models import User


def make_user(n: str) -> User:
    u = User(n)
    return u
'''


def _neo4j_reachable() -> bool:
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        return True
    except Exception:  # noqa: BLE001 - any connection failure ⇒ skip
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason=f"no Neo4j reachable at {NEO4J_URI} (set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD)",
)


def _norm(o):
    """``model_dump`` minus the projection's documented-lossy fields.

    Drops ``comments`` everywhere; for a ``PyVariableDeclaration`` (identified by its
    ``initializer``/``scope`` keys) also drops the un-projected ``value`` and column span.
    """
    if hasattr(o, "model_dump"):
        o = o.model_dump()
    if isinstance(o, dict):
        is_var = "initializer" in o and "scope" in o
        drop = {"comments"} | ({"value", "start_column", "end_column"} if is_var else set())
        return {k: _norm(v) for k, v in o.items() if k not in drop}
    if isinstance(o, list):
        return [_norm(x) for x in o]
    return o


@pytest.fixture(scope="module")
def backends(tmp_path_factory):
    """(ref, neo): the in-memory backend and a Neo4j backend over the same project's graph."""
    from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
    from cldk.analysis.python.neo4j import PyNeo4jBackend
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.neo4j.emit import emit_neo4j
    from codeanalyzer.options import AnalysisOptions, EmitTarget

    proj = tmp_path_factory.mktemp("parity_proj")
    pkg = proj / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text(MODELS_PY)
    (pkg / "service.py").write_text(SERVICE_PY)

    ref = PyCodeanalyzer(project_dir=proj, analysis_level="call_graph", analysis_json_path=None, eager_analysis=True)

    # Load the graph out of band (in-process), exactly as a populator job would.
    opts = AnalysisOptions(
        input=proj,
        emit=EmitTarget.NEO4J,
        app_name=APP_NAME,
        rebuild_analysis=True,
        neo4j_uri=NEO4J_URI,
        neo4j_user=NEO4J_USER,
        neo4j_password=NEO4J_PASSWORD,
    )
    with Codeanalyzer(opts) as az:
        emit_neo4j(az.analyze(), opts)

    neo = PyNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=APP_NAME)
    yield ref, neo
    neo.close()


def test_symbol_table_parity(backends):
    ref, neo = backends
    st_ref, st_neo = ref.get_symbol_table(), neo.get_symbol_table()
    assert set(st_ref) == set(st_neo)
    for fp in st_ref:
        a, b = _norm(st_ref[fp]), _norm(st_neo[fp])
        a.pop("imports", None)  # imports are reconstructed best-effort from aggregated edges
        b.pop("imports", None)
        assert a == b, f"module {fp} differs"


def test_modules_and_file_lookup_parity(backends):
    ref, neo = backends
    assert len(ref.get_modules()) == len(neo.get_modules())
    assert ref.get_python_file("pkg.models.User") == neo.get_python_file("pkg.models.User")
    # inner classes are not in the top-level map on either backend
    assert ref.get_python_file("pkg.models.Entity.Meta") == neo.get_python_file("pkg.models.Entity.Meta")


def test_classes_parity(backends):
    ref, neo = backends
    ac_ref, ac_neo = ref.get_all_classes(), neo.get_all_classes()
    assert set(ac_ref) == set(ac_neo)
    for sig in ac_ref:
        assert _norm(ac_ref[sig]) == _norm(ac_neo[sig]), f"class {sig} differs"
        assert _norm(ref.get_all_nested_classes(sig)) == _norm(neo.get_all_nested_classes(sig))
        assert set(ref.get_all_sub_classes(sig)) == set(neo.get_all_sub_classes(sig))
        assert ref.get_extended_classes(sig) == neo.get_extended_classes(sig)


def test_methods_and_fields_parity(backends):
    ref, neo = backends
    assert set(ref.get_all_methods_in_application()) == set(neo.get_all_methods_in_application())
    for sig in ref.get_all_classes():
        mc = ref.get_all_methods_in_class(sig)
        assert set(mc) == set(neo.get_all_methods_in_class(sig))
        for mname in mc:
            assert _norm(ref.get_method(sig, mname)) == _norm(neo.get_method(sig, mname)), f"{sig}.{mname} differs"
            assert ref.get_method_parameters(sig, mname) == neo.get_method_parameters(sig, mname)
        assert set(ref.get_all_constructors(sig)) == set(neo.get_all_constructors(sig))
        assert _norm(ref.get_all_fields(sig)) == _norm(neo.get_all_fields(sig))


def test_bulk_accessors_parity(backends):
    ref, neo = backends

    # get_callables_overview: same set of callables, identical projection per signature.
    ov_ref = {o.signature: o.model_dump() for o in ref.get_callables_overview()}
    ov_neo = {o.signature: o.model_dump() for o in neo.get_callables_overview()}
    assert set(ov_ref) == set(ov_neo)
    for sig in ov_ref:
        assert ov_ref[sig] == ov_neo[sig], f"overview for {sig} differs"

    # get_method_bodies: identical bodies for the whole frontier, and missing sigs omitted on both.
    sigs = list(ov_ref)
    assert ref.get_method_bodies(sigs) == neo.get_method_bodies(sigs)
    assert ref.get_method_bodies(["nope.not.here"]) == neo.get_method_bodies(["nope.not.here"]) == {}

    # get_decorated_callables: parity for whatever decorators the project actually uses.
    markers = sorted({d for o in ov_ref.values() for d in o["decorators"]})
    if markers:
        dec_ref = {o.signature: o.model_dump() for o in ref.get_decorated_callables(markers)}
        dec_neo = {o.signature: o.model_dump() for o in neo.get_decorated_callables(markers)}
        assert dec_ref == dec_neo
    assert ref.get_decorated_callables(["__no_such_decorator__"]) == neo.get_decorated_callables(["__no_such_decorator__"]) == []

    # get_callsites_for: same keys (every existing signature) and identical, identically-ordered sites.
    cs_ref = ref.get_callsites_for(sigs)
    cs_neo = neo.get_callsites_for(sigs)
    assert set(cs_ref) == set(cs_neo)
    for sig in cs_ref:
        assert [_norm(s) for s in cs_ref[sig]] == [_norm(s) for s in cs_neo[sig]], f"call sites for {sig} differ"
    assert ref.get_callsites_for(["nope.not.here"]) == neo.get_callsites_for(["nope.not.here"]) == {}


def test_call_graph_parity(backends):
    ref, neo = backends
    g_ref, g_neo = ref.get_call_graph(), neo.get_call_graph()

    def edgeset(g):
        return {(u, v, g[u][v]["type"], g[u][v]["weight"], tuple(g[u][v]["provenance"])) for u, v in g.edges}

    assert edgeset(g_ref) == edgeset(g_neo)
    assert ref.get_all_callers("pkg.models.User", "describe") == neo.get_all_callers("pkg.models.User", "describe")
    assert ref.get_all_callees("pkg.models.User", "describe") == neo.get_all_callees("pkg.models.User", "describe")
    assert set(map(tuple, ref.get_class_call_graph("pkg.models.User"))) == set(map(tuple, neo.get_class_call_graph("pkg.models.User")))
