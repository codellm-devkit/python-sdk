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

"""Task 10: external symbol resolution.

The brief's own suggested tests assumed the ``py``/``conftest.py`` fixture already carried a call
to a builtin and a queryable call-site responder; it doesn't (that fixture only answers
``locate``/``get_source``/``get_method_bodies``-shaped queries -- confirmed by reading
``_locate_responder`` directly). Following the precedent ``test_entrypoints.py`` set for the same
situation: hand-built fixtures here, ``object.__new__`` + a stubbed ``_run`` for the Neo4j backend
(no live server, no FakeDriver machinery needed for two projection-shaped queries).

Checked against the installed analyzer (``codeanalyzer`` 1.4.0 in ``.venv``) rather than trusted
from the brief:

* ``:PyExternal`` carries exactly ``id``/``name``/``module`` (``neo4j/schema.py``'s ``NodeLabel``)
  -- no ``_module`` property, so it cannot be scoped the way every other node here is; its id
  embeds the owning application's own can-id by construction instead
  (``<app-id>/@external/<module>/<name>``, confirmed by running the analyzer against a throwaway
  project and inspecting ``PyApplication.external_symbols`` directly).
* ``:PyBodyNode`` (a ``kind: 'call'`` node) carries no ``callee_signature`` property at all --
  callee resolution is the separate ``PY_RESOLVES_TO`` edge to a declared ``:PyCallable`` (has
  ``signature``) or a ``:PyExternal`` ghost (no ``signature``, only ``id``/``name``/``module``).
* Empirically (a real analyzer run, default options, analysis level 1 -- the SDK's own default,
  confirmed by reading ``AnalysisOptions.analysis_level: int = 1`` and that
  ``PyCodeanalyzer._run_analyzer`` never overrides it): Jedi's own ``PyCallsite.callee_signature``
  is *already* populated for a resolved builtin call (e.g. ``"builtins.ValueError.__init__"`` for
  a bare ``ValueError(...)``) -- it is not ``None`` on the local backend today. The actual gap is
  that this raw dotted guess is not addressable through ``get_external_symbols()`` (whose keys are
  ``can://…/@external/…`` ids, not dotted names) -- ``PyExternalSymbol.module``/``.name`` reverse
  cleanly into the dotted spelling that named it (the analyzer's own id-minting is
  ``module, name = sig.rsplit(".", 1)``, so ``f"{module}.{name}" == sig`` by construction), which
  is what the local fix rewrites through, without reimplementing the analyzer's private id scheme.
  ``PyBodyNode.callee`` (the finer, per-body-node resolution) is only ever set by the
  defuse-linker backfill, gated to analysis level >= 2 -- absent at the SDK's own default level,
  confirmed the same way. The local fix therefore prefers the body node when present (finer,
  covers defuse-only resolutions Jedi missed) and falls back to the Jedi-signature rewrite
  otherwise -- both paths exercised below.
"""

from unittest.mock import patch

from codeanalyzer.schema.py_schema import BodyNode, PyApplication, PyCallable, PyCallsite, PyClass, PyExternalSymbol, PyModule

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

VALUEERROR_ID = "can://python/proj/@external/builtins.ValueError/__init__"
KEY_ID = "can://python/proj/app.py/Store/key(self)"


# =====================================================================================
# Local backend
# =====================================================================================
def _local_backend() -> PyCodeanalyzer:
    """One class, four call sites -- one per resolution branch in ``_resolve_callee``:

    * ``key``'s ``ValueError(...)`` -- Jedi resolved a builtin, no body-node callee (level 1):
      rewritten to the external's can-id via the module/name reversal.
    * ``helper``'s ``self.key()`` -- Jedi resolved a declared in-project target: already the
      right dotted signature, left unchanged.
    * ``dyn``'s ``obj.whatever()`` -- Jedi failed outright, no body-node callee: genuinely
      unresolved, stays ``None``.
    * ``via_defuse``'s call -- Jedi failed but the (simulated level-2) body node's ``callee``
      carries the declared target's can-id: resolved to its dotted signature via ``id_to_sig``.
    """
    key_fn = PyCallable(
        name="key",
        path="src/app.py",
        signature="src.app.Store.key",
        id=KEY_ID,
        call_sites=[PyCallsite(method_name="ValueError", callee_signature="builtins.ValueError.__init__", start_line=7, start_column=18)],
        body={"7:18": BodyNode(kind="call", callee=None, method_name="ValueError")},
    )
    helper_fn = PyCallable(
        name="helper",
        path="src/app.py",
        signature="src.app.Store.helper",
        call_sites=[PyCallsite(method_name="key", callee_signature="src.app.Store.key", start_line=11, start_column=15)],
        body={"11:15": BodyNode(kind="call", callee=None, method_name="key")},
    )
    dyn_fn = PyCallable(
        name="dyn",
        path="src/app.py",
        signature="src.app.Store.dyn",
        call_sites=[PyCallsite(method_name="whatever", callee_signature=None, start_line=15, start_column=10)],
        body={"15:10": BodyNode(kind="call", callee=None, method_name="whatever")},
    )
    via_defuse_fn = PyCallable(
        name="via_defuse",
        path="src/app.py",
        signature="src.app.Store.via_defuse",
        call_sites=[PyCallsite(method_name="key", callee_signature=None, start_line=19, start_column=8)],
        body={"19:8": BodyNode(kind="call", callee=KEY_ID, method_name="key")},
    )
    store = PyClass(
        name="Store",
        signature="src.app.Store",
        callables={"key": key_fn, "helper": helper_fn, "dyn": dyn_fn, "via_defuse": via_defuse_fn},
    )
    module = PyModule(file_path="src/app.py", module_name="src.app", types={"src.app.Store": store})
    ext = PyExternalSymbol(id=VALUEERROR_ID, name="__init__", module="builtins.ValueError")
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"src/app.py": module}, external_symbols={ext.id: ext})
    return backend


def test_builtin_callee_resolves_to_the_external_can_id():
    """A call to a builtin is a resolved edge to an @external node, not an unresolved one."""
    sites = _local_backend().get_callsites_for(["src.app.Store.key"])
    (site,) = sites["src.app.Store.key"]
    assert site.method_name == "ValueError"
    assert site.callee_signature == VALUEERROR_ID


def test_external_callee_is_addressable_via_get_external_symbols():
    backend = _local_backend()
    site = backend.get_callsites_for(["src.app.Store.key"])["src.app.Store.key"][0]
    ext = backend.get_external_symbols()
    assert site.callee_signature in ext
    assert ext[site.callee_signature].name == "__init__"
    assert ext[site.callee_signature].module == "builtins.ValueError"


def test_declared_callee_keeps_its_dotted_signature():
    site = _local_backend().get_callsites_for(["src.app.Store.helper"])["src.app.Store.helper"][0]
    assert site.callee_signature == "src.app.Store.key"


def test_genuinely_unresolved_callee_stays_none():
    """Jedi failed and no body-node resolution exists -- a real absence, not a gap to close."""
    site = _local_backend().get_callsites_for(["src.app.Store.dyn"])["src.app.Store.dyn"][0]
    assert site.callee_signature is None


def test_body_node_resolution_is_preferred_when_jedi_missed_it():
    """The defuse-linker's body-node-level backfill (level >= 2) resolves a call Jedi didn't."""
    site = _local_backend().get_callsites_for(["src.app.Store.via_defuse"])["src.app.Store.via_defuse"][0]
    assert site.callee_signature == "src.app.Store.key"


def test_get_external_symbols_are_addressable_can_ids():
    ext = _local_backend().get_external_symbols()
    assert any(k.startswith("can://python/") and "@external" in k for k in ext)


def test_get_external_symbols_empty_is_a_real_empty():
    module = PyModule(file_path="src/app.py", module_name="src.app")
    backend = object.__new__(PyCodeanalyzer)
    backend.application = PyApplication(symbol_table={"src/app.py": module})
    assert backend.get_external_symbols() == {}


# =====================================================================================
# Neo4j backend
# =====================================================================================
def _neo4j_backend(modules=("src/app.py",)) -> PyNeo4jBackend:
    """A ``PyNeo4jBackend`` with ``__init__`` (and its real driver connection) bypassed -- same
    construction ``test_entrypoints.py`` uses."""
    backend = object.__new__(PyNeo4jBackend)
    backend.application_name = "app"
    backend._database = None
    backend._modules = list(modules)
    return backend


def _run_keyed(rows_by_fragment: dict):
    def _run(query: str, **params):
        for fragment, result in rows_by_fragment.items():
            if fragment in query:
                return result
        raise AssertionError(f"no canned rows for query: {query!r} (params={params})")

    return _run


def test_neo4j_builtin_callee_resolves_via_py_resolves_to():
    """The brief's own suggested assertion, made to pass against a graph that actually carries
    the PY_RESOLVES_TO edge (the shared ``py`` conftest fixture does not -- see module docstring)."""
    row = {
        "owner": "src.app.Store.key",
        "p": {"method_name": "ValueError", "start_line": 7, "start_column": 18},
        "callee": VALUEERROR_ID,  # t is a :PyExternal (no `signature`), so coalesce picked t.id
    }
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"PY_RESOLVES_TO": [row]})) as run:
        sites = backend.get_callsites_for(["src.app.Store.key"])
    (site,) = sites["src.app.Store.key"]
    assert site.method_name == "ValueError"
    assert site.callee_signature == VALUEERROR_ID
    query = run.call_args.args[0]
    assert "SET" not in query and "CREATE" not in query and "MERGE" not in query and "DELETE" not in query


def test_neo4j_declared_callee_resolves_to_its_signature():
    row = {
        "owner": "src.app.Store.helper",
        "p": {"method_name": "key", "start_line": 11, "start_column": 15},
        "callee": "src.app.Store.key",  # t is a :PyCallable, so coalesce picked t.signature
    }
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"PY_RESOLVES_TO": [row]})):
        sites = backend.get_callsites_for(["src.app.Store.helper"])
    assert sites["src.app.Store.helper"][0].callee_signature == "src.app.Store.key"


def test_neo4j_unresolved_callee_stays_none():
    row = {"owner": "src.app.Store.dyn", "p": {"method_name": "whatever", "start_line": 15, "start_column": 10}, "callee": None}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"PY_RESOLVES_TO": [row]})):
        sites = backend.get_callsites_for(["src.app.Store.dyn"])
    assert sites["src.app.Store.dyn"][0].callee_signature is None


def test_neo4j_get_external_symbols_filters_by_id_prefix():
    row = {"p": {"id": VALUEERROR_ID, "name": "__init__", "module": "builtins.ValueError"}}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"STARTS WITH": [row]})) as run:
        ext = backend.get_external_symbols()
    assert set(ext) == {VALUEERROR_ID}
    assert ext[VALUEERROR_ID].name == "__init__"
    assert ext[VALUEERROR_ID].module == "builtins.ValueError"
    query, params = run.call_args.args[0], run.call_args.kwargs
    assert "e.id STARTS WITH $prefix" in query
    assert params["prefix"] == "can://python/app/@external/"
    assert "SET" not in query and "CREATE" not in query and "MERGE" not in query and "DELETE" not in query


def test_neo4j_get_external_symbols_empty_is_a_real_empty():
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"STARTS WITH": []})):
        assert backend.get_external_symbols() == {}


# =====================================================================================
# Parity
# =====================================================================================
def test_callsites_for_parity_between_backends():
    """Same underlying resolution (builtin / declared / unresolved), same result on both backends."""
    local = _local_backend().get_callsites_for(["src.app.Store.key", "src.app.Store.helper", "src.app.Store.dyn"])
    local_sigs = {sig: [s.callee_signature for s in sites] for sig, sites in local.items()}

    rows = [
        {"owner": "src.app.Store.key", "p": {"method_name": "ValueError", "start_line": 7, "start_column": 18}, "callee": VALUEERROR_ID},
        {"owner": "src.app.Store.helper", "p": {"method_name": "key", "start_line": 11, "start_column": 15}, "callee": "src.app.Store.key"},
        {"owner": "src.app.Store.dyn", "p": {"method_name": "whatever", "start_line": 15, "start_column": 10}, "callee": None},
    ]
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"PY_RESOLVES_TO": rows})):
        neo4j = backend.get_callsites_for(["src.app.Store.key", "src.app.Store.helper", "src.app.Store.dyn"])
    neo4j_sigs = {sig: [s.callee_signature for s in sites] for sig, sites in neo4j.items()}

    assert local_sigs == neo4j_sigs == {
        "src.app.Store.key": [VALUEERROR_ID],
        "src.app.Store.helper": ["src.app.Store.key"],
        "src.app.Store.dyn": [None],
    }


def test_get_external_symbols_parity_between_backends():
    local_ext = _local_backend().get_external_symbols()

    row = {"p": {"id": VALUEERROR_ID, "name": "__init__", "module": "builtins.ValueError"}}
    backend = _neo4j_backend()
    with patch.object(PyNeo4jBackend, "_run", side_effect=_run_keyed({"STARTS WITH": [row]})):
        neo4j_ext = backend.get_external_symbols()

    assert set(local_ext) == set(neo4j_ext) == {VALUEERROR_ID}
    assert local_ext[VALUEERROR_ID].model_dump() == neo4j_ext[VALUEERROR_ID].model_dump()
