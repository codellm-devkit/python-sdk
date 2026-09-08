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

**THIS MODULE WRITES TO THE DATABASE.** Its fixture runs the analyzer's own ``emit_neo4j`` to
project a throwaway application (``APP_NAME``) into the target server before querying it. The
*queries* are read-only; getting to them is not. Point it only at a database you are willing to
have written to — never at a populated one you care about. Teardown deletes what the fixture
emitted (see :func:`_purge_application`), so a deliberate run leaves the server as it found it,
but a run interrupted before teardown leaves ``APP_NAME``'s subgraph behind.

The whole module skips unless ``CLDK_TEST_NEO4J_WRITE_URI`` is set. That variable is deliberately
distinct from the ``CLDK_TEST_NEO4J_URI`` the read-only suites use, and there is deliberately **no
default URI and no default credentials**: an unset environment must mean "do not run", not "run
against whatever is listening on the usual port". Point the tests at a scratch server with:

    CLDK_TEST_NEO4J_WRITE_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_WRITE_USER=neo4j \
    CLDK_TEST_NEO4J_WRITE_PASSWORD=test \
    pytest tests/analysis/python/test_python_neo4j_backend.py

(e.g. `docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5`).

These assert that :class:`PyNeo4jBackend` answers every query **identically** to the canonical
:class:`PyCodeanalyzer` (analysis.json) backend on the same project — the definition of the
"1-to-1 map".

**Two things the fixture makes equal before comparing**, because otherwise the two sides are not
answering about the same analysis and every difference below would be noise:

* The project is created in a directory *named* ``APP_NAME``. The in-memory backend derives the
  application segment of every ``can://<app>/python/…`` id from the project directory's name,
  while the emitter is told ``app_name=APP_NAME`` — give them different names and every id in the
  graph disagrees with every id in memory for a reason that has nothing to do with the projection.
* The graph is emitted at ``analysis_level=2``, matching the reference's ``"call_graph"``.
  ``AnalysisOptions`` defaults to level 1, which emits **no** ``PY_RESOLVES_TO`` edges (so every
  call site's ``callee_signature`` is ``None``) and none of the ``defuse``-provenance call edges
  (so ``pkg.models.greet -> pkg.models.greet._decorate`` is missing from the call graph). Neither
  is a projection loss; both are just a shallower analysis.

**The tolerances that remain, each with its cause** (see
``cldk.analysis.python.neo4j.reconstruct`` and ``PyNeo4jBackend._callable_full`` for the
projection's side of each). Nothing here is tolerated because it was inconvenient, and where a
relation is exact it is asserted rather than skipped:

* ``PyModule.file_path`` is **absolute** in memory and the graph's own module key — repo-relative
  — on Neo4j (``reconstruct.module`` sets ``file_path=file_key``). Exact and total, so it is
  asserted both ways: the absolute path ends with the key, and the key is the symbol-table key.
* ``PyModule.source`` is ``""``: ``:PyModule`` carries no source property at all (its keys are
  ``id``, ``module_name``, ``last_modified``, ``content_hash``, ``file_key``, ``file_size``) — a
  module's text is not projected.
* ``PyModule.imports`` is ``[]``: ``PY_IMPORTS`` aggregates per distribution package, and this
  project's only import is the relative ``from .models import User``, which is not one. The graph
  declares no ``PY_IMPORTS`` relationship at all here, so the empty list is asserted, not skipped.
* ``id`` is ``""`` on every reconstructed module, class and callable. **This one is recoverable
  and simply not read**: the nodes do carry ``id`` (the teardown below matches on it), but no
  ``reconstruct`` function copies it onto the model. Worth closing; not closed here, because this
  module changes no SDK behaviour.
* ``span`` is ``None``: a node carries ``start_line``/``end_line`` and nothing finer, so there are
  no columns and no byte offsets to build a :class:`Span` from. The lines themselves survive and
  are compared — they are inside the dicts these tests assert equal, not tolerated.
* ``PyClassAttribute.initializer`` is ``None``, and a call site's ``arguments`` is ``[]``, although
  ``:PyAttribute.initializer`` and ``:PyBodyNode.arguments_json`` are both **present in the
  graph** — ``reconstruct.attribute`` / ``reconstruct.callsite`` read neither. Same shape as the
  ``id`` gap above.
* A call site's ``argument_types`` and its ``start_column``/``end_column`` are genuinely not
  projected (``reconstruct.callsite``), so they come back ``[]`` / ``-1``.
* ``callee_signature`` is ``None`` on every call site reached through ``_callable_full`` —
  ``get_method``, ``get_class``, ``get_symbol_table`` and their siblings — because that path never
  follows ``PY_RESOLVES_TO``. It is **not** tolerated on :meth:`get_callsites_for`, the one
  accessor that does follow it: there the resolved callee is compared exactly.
* ``PyCallable.body`` is ``{}``: ``_callable_full`` fetches a callable's ``call`` body nodes as
  call sites and does not also assemble them into the ``body`` map. Asserted empty rather than
  dropped.
* Comments collapse to a single docstring, and ``PyVariableDeclaration`` loses ``value`` and its
  column span (both documented in ``reconstruct``).
"""

import logging
import os

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

# No defaults, by design: a default URI plus default credentials is how an unset environment turns
# an emitting test into a write against whatever happens to be listening (#324).
NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_WRITE_URI")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_WRITE_USER")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_WRITE_PASSWORD")
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


def helper(x: str) -> str:
    return x.upper()


def entry(x: str) -> str:
    return helper(x)
'''

SERVICE_PY = '''\
from .models import User


def make_user(n: str) -> User:
    u = User(n)
    return u
'''


def _neo4j_reachable() -> bool:
    if not NEO4J_URI:
        return False
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
    reason=(
        "this module WRITES to the database (its fixture emits an application into it); set "
        "CLDK_TEST_NEO4J_WRITE_URI / _WRITE_USER / _WRITE_PASSWORD to a scratch server to run it"
    ),
)


# The emitter's own project wipe (``codeanalyzer/neo4j/cypher.py::_wipe``), reproduced verbatim so
# the two cannot drift, and scoped to one ``:PyApplication`` by name. It can reach nothing outside
# APP_NAME's subgraph: the anchor is a parameterised name match, and everything else is reached
# through that node's own PY_HAS_MODULE / declaration edges.
_PURGE = (
    "MATCH (a:PyApplication {name: $app}) "
    "OPTIONAL MATCH (a)-[:PY_HAS_MODULE]->(m:PyModule) "
    "OPTIONAL MATCH (m)-[:PY_DECLARES|PY_HAS_METHOD|PY_HAS_ATTRIBUTE|PY_DECLARES_VAR|PY_HAS_CALLSITE*1..]->(x) "
    "DETACH DELETE x, m, a"
)

# The emitter's wipe is a *re-emission* guard, not a teardown: everything it leaves standing is
# something the following MERGE statements immediately overwrite. A teardown has no MERGE after it,
# so the leftovers are litter that accumulates across runs. Measured on a real 1,626-module graph,
# the wipe above reaches 67,535 nodes and leaves these behind, every one of them created by the
# emitter for this application alone:
#
#     :PyBodyNode   885,218   hangs off :PyCallable by PY_HAS_BODY_NODE, which the wipe's
#                             relationship list does not include, so DETACH DELETE of the callable
#                             orphans rather than removes it
#     :Artifact      10,196   anchored on the application by HAS_ARTIFACT, not PY_HAS_MODULE
#     :PyExternal     5,715   ghost callees, reached only through PY_CALLS
#     :ConfigKey         93   hangs off :Artifact by DEFINES_CONFIG
#
# All four are addressable by one rule rather than a second traversal to keep in sync: the emitter
# mints their ids under ``can://<app>/python/`` or ``can://<app>/artifact/``, so the application
# name is *in the key*. That is also why this cannot reach a neighbour: another application's nodes
# carry its own name in the same position, and the trailing slash stops ``odoo-slim-19`` matching
# ``odoo-slim-19-b``.
_PURGE_UNANCHORED = "MATCH (n) WHERE n.id STARTS WITH $py OR n.id STARTS WITH $artifact DETACH DELETE n"


def _purge_application() -> None:
    """Delete the application this module emitted, so a deliberate run leaves nothing behind.

    The one place in this suite where destructive Cypher is correct. Two statements: the emitter's
    own wipe (:data:`_PURGE`), then the four node kinds that wipe deliberately leaves for a
    re-emission's MERGE to overwrite (:data:`_PURGE_UNANCHORED`). Neither can touch another
    application — one anchors on ``$app`` by name, the other on ids that embed it.

    Three kinds are left behind **on purpose**, exactly as the emitter leaves them:
    ``:PyPackage`` (keyed by distribution name), ``:Package`` (keyed by purl, e.g.
    ``pkg:pypi/babel``) and ``:PyDecorator`` (keyed by qualified name). None carries an application
    in its key, because none belongs to one: they are shared vocabulary that a second application
    on the same server MERGEs onto rather than duplicates, so deleting them here would corrupt a
    neighbour's graph. They are also bounded — 664 nodes for a 2,364-file application — so they do
    not accumulate the way the four above would.
    """
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            session.run(_PURGE, app=APP_NAME).consume()
            session.run(
                _PURGE_UNANCHORED,
                py=f"can://{APP_NAME}/python/",
                artifact=f"can://{APP_NAME}/artifact/",
            ).consume()
    finally:
        driver.close()


def _norm(o, *, resolved_callees: bool = False):
    """``model_dump`` minus the projection's lossy fields — the module docstring names each cause.

    Node shapes are told apart by their own keys, since what arrives here is already a dict:
    a ``PyVariableDeclaration`` has ``initializer`` *and* ``scope``, a ``PyClassAttribute`` has
    ``initializer`` without one, a ``PyCallsite`` has ``callee_signature``, a ``PyCallable`` has
    ``call_sites``.

    ``resolved_callees=True`` keeps ``callee_signature``, for the one accessor that populates it
    (:meth:`PyNeo4jBackend.get_callsites_for` follows ``PY_RESOLVES_TO``; ``_callable_full`` does
    not). Tolerating it everywhere would let that accessor's resolution regress unnoticed.
    """
    if hasattr(o, "model_dump"):
        o = o.model_dump()
    if isinstance(o, dict):
        drop = {"comments", "id", "span"}
        if "initializer" in o:
            drop |= {"value", "start_column", "end_column"} if "scope" in o else {"initializer"}
        if "callee_signature" in o:
            drop |= {"argument_types", "arguments", "start_column", "end_column"}
            if not resolved_callees:
                drop |= {"callee_signature"}
        if "call_sites" in o:
            drop |= {"body"}
        return {k: _norm(v, resolved_callees=resolved_callees) for k, v in o.items() if k not in drop}
    if isinstance(o, list):
        return [_norm(x, resolved_callees=resolved_callees) for x in o]
    return o


@pytest.fixture(scope="module")
def backends(tmp_path_factory):
    """(ref, neo): the in-memory backend and a Neo4j backend over the same project's graph."""
    from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
    from cldk.analysis.python.neo4j import PyNeo4jBackend
    from codeanalyzer.core import Codeanalyzer
    from codeanalyzer.neo4j.emit import emit_neo4j
    from codeanalyzer.options import AnalysisOptions, EmitTarget

    # Named APP_NAME on purpose: the in-memory backend takes the can:// application segment from
    # the project directory's name and the emitter takes it from --app-name, so this is what makes
    # the two sides mint the same ids (see the module docstring).
    proj = tmp_path_factory.mktemp("parity_root") / APP_NAME
    proj.mkdir()
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
        # Level 2 == the reference's "call_graph". AnalysisOptions defaults to 1, which emits no
        # PY_RESOLVES_TO edges and no defuse-provenance call edges -- comparing that against a
        # level-2 reference measures the level difference, not the projection.
        analysis_level=2,
    )
    with Codeanalyzer(opts) as az:
        emit_neo4j(az.analyze(), opts)

    neo = PyNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=APP_NAME)
    try:
        yield ref, neo
    finally:
        neo.close()
        _purge_application()


def test_symbol_table_parity(backends):
    ref, neo = backends
    st_ref, st_neo = ref.get_symbol_table(), neo.get_symbol_table()
    assert set(st_ref) == set(st_neo)
    assert any(st_ref[fp].source for fp in st_ref), "the in-memory side does carry module text"
    for fp in st_ref:
        a, b = _norm(st_ref[fp]), _norm(st_neo[fp])
        # file_path: absolute in memory, the graph's own module key on Neo4j. Exact and total, so
        # asserted in both directions rather than skipped.
        assert b["file_path"] == fp, "the Neo4j file_path is the symbol-table key itself"
        assert a.pop("file_path").endswith(b.pop("file_path"))
        # source: :PyModule carries no source property, so a module's text is not projected.
        assert b.pop("source") == ""
        a.pop("source")
        # imports: PY_IMPORTS aggregates per distribution package; this project's only import is
        # relative, so the graph declares no PY_IMPORTS at all. Assert the empty list, don't drop it.
        assert b.pop("imports") == []
        a.pop("imports")
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
            m_ref, m_neo = ref.get_method(sig, mname), neo.get_method(sig, mname)
            assert _norm(m_ref) == _norm(m_neo), f"{sig}.{mname} differs"
            # _callable_full turns a callable's `call` body nodes into call sites and does not also
            # assemble them into `body`, so this path's body map is always empty. Asserted, so the
            # day it is populated this test says so instead of quietly comparing nothing.
            assert m_neo.body == {}
            assert ref.get_method_parameters(sig, mname) == neo.get_method_parameters(sig, mname)
        assert set(ref.get_all_constructors(sig)) == set(neo.get_all_constructors(sig))
        f_ref, f_neo = ref.get_all_fields(sig), neo.get_all_fields(sig)
        assert _norm(f_ref) == _norm(f_neo)
        # `initializer` is on the :PyAttribute node but reconstruct.attribute does not read it --
        # a recoverable gap, not a projection loss. Both halves asserted so closing it breaks here.
        assert all(f.initializer is None for f in f_neo)
        if sig == "pkg.models.Entity":
            assert {f.name: f.initializer for f in f_ref} == {"registry": "'default'"}


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
    # resolved_callees=True: this is the only accessor that follows PY_RESOLVES_TO, so its
    # callee_signature is compared exactly -- including the @external can-id an external target
    # resolves to. Everywhere else it is None and tolerated (see the module docstring).
    for sig in cs_ref:
        assert [_norm(s, resolved_callees=True) for s in cs_ref[sig]] == [_norm(s, resolved_callees=True) for s in cs_neo[sig]], f"call sites for {sig} differ"
    assert any(s.callee_signature for sites in cs_neo.values() for s in sites), "PY_RESOLVES_TO is followed here"
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

    # Regression (#246): get_method / get_all_callers / get_all_callees must resolve module-level
    # functions too, scoped by module name rather than class name — "pkg.models.entry" calls
    # "pkg.models.helper". The scope key is the module's own `module_name`, which for pkg/models.py
    # is the short "models" and not the dotted "pkg.models"; both backends agree on that, and on
    # answering a scope key that resolves to nothing with an empty result rather than raising.
    assert {m.module_name for m in ref.get_modules()} == {m.module_name for m in neo.get_modules()} >= {"models"}
    assert ref.get_method("models", "helper").signature == neo.get_method("models", "helper").signature == "pkg.models.helper"
    callers_ref = ref.get_all_callers("models", "helper")
    callers_neo = neo.get_all_callers("models", "helper")
    assert callers_ref == callers_neo
    assert callers_ref["target_method"] == "pkg.models.helper"
    assert [c["caller_signature"] for c in callers_ref["caller_details"]] == ["pkg.models.entry"]
    assert ref.get_all_callees("models", "entry") == neo.get_all_callees("models", "entry")
    assert ref.get_all_callers("pkg.models", "helper") == neo.get_all_callers("pkg.models", "helper") == {"caller_details": []}
