"""Live Neo4j parity: GoNeo4jBackend must answer identically to GoCodeanalyzer on the same
application. Skips unless a Go graph is reachable AND its matching analysis.json is given.

Set, pointing at a read-only graph a ``cango --emit neo4j`` run populated:

    CLDK_TEST_GO_NEO4J_URI   (e.g. bolt://localhost:7687)
    CLDK_TEST_GO_NEO4J_USER  (default: neo4j)
    CLDK_TEST_GO_NEO4J_PASSWORD
    CLDK_TEST_GO_NEO4J_APP   (the --app-name the graph was loaded with, e.g. go/cobra)
    CLDK_TEST_GO_NEO4J_NAMESPACE  (the --namespace; defaults to the app name)
    CLDK_TEST_GO_ANALYSIS_JSON  (path to the SAME application's analysis.json, for the diff)

With the first four set but no analysis.json, only the standalone (non-parity) checks run.

**The L3/L4 parity checks (python-sdk#436) need a level-4 graph and a level-4 payload**, which
``--emit neo4j`` always is and ``analysis.json`` is only at ``-a 4``; they skip themselves on a
shallower pair rather than failing, since a level-2 payload legitimately has no dataflow to compare.
To reproduce the pair this suite was developed against::

    cango -i <repo>/testdata/dataflow -a 4 -o /tmp/go-l4 --app-name dataflow --namespace dataflow
    NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=... \\
        cango -i <repo>/testdata/dataflow --emit neo4j --app-name dataflow --namespace dataflow
"""

import os
import shutil
from pathlib import Path

import pytest

from cldk.analysis.commons.backend_config import cache_subdir
from cldk.analysis.commons.identity import resolve_identity
from cldk.analysis.go.codeanalyzer import GoCodeanalyzer
from cldk.analysis.go.neo4j import GoNeo4jBackend

URI = os.environ.get("CLDK_TEST_GO_NEO4J_URI")
USER = os.environ.get("CLDK_TEST_GO_NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("CLDK_TEST_GO_NEO4J_PASSWORD")
APP = os.environ.get("CLDK_TEST_GO_NEO4J_APP")
NAMESPACE = os.environ.get("CLDK_TEST_GO_NEO4J_NAMESPACE") or APP
ANALYSIS_JSON = os.environ.get("CLDK_TEST_GO_ANALYSIS_JSON")

pytestmark = pytest.mark.skipif(
    not (URI and PASSWORD and APP),
    reason="set CLDK_TEST_GO_NEO4J_URI / _PASSWORD / _APP to a read-only codeanalyzer-go graph",
)


@pytest.fixture(scope="module")
def neo4j_backend():
    b = GoNeo4jBackend(neo4j_uri=URI, neo4j_username=USER, neo4j_password=PASSWORD, application_name=APP, namespace=NAMESPACE)
    yield b
    b.close()


@pytest.fixture(scope="module")
def local_backend(tmp_path_factory):
    """The local backend over the *same* application, fed from the cached ``analysis.json``.

    ``application_name``/``namespace`` are passed explicitly and must match the graph's: identity is
    validated against the payload's own ``identity_namespace`` (#434), so letting them default from
    the ``project_dir`` basename makes the cache look like a foreign application and the backend
    re-runs ``cango`` against a path that does not exist. ``analysis_level`` is
    ``system_dependency_graph`` so a level-4 payload is used at its full depth — the level only
    selects what is *read*, since the cache is pre-seeded and no run happens.
    """
    if not (ANALYSIS_JSON and Path(ANALYSIS_JSON).exists()):
        pytest.skip("set CLDK_TEST_GO_ANALYSIS_JSON to the same application's analysis.json for the parity diff")
    cache = tmp_path_factory.mktemp("go-parity")
    identity = resolve_identity(APP, NAMESPACE)
    # ``cache_subdir`` keys the cache by the *identity digest* (#434), not by the bare language —
    # ``<root>/go/<sha256(application_id)>``. Seeding ``<root>/go/analysis.json`` instead wrote
    # where nothing reads, so the backend found no cache and tried to re-run cango.
    seeded = cache_subdir(cache, None, "go", identity=identity)
    seeded.mkdir(parents=True, exist_ok=True)
    shutil.copy(ANALYSIS_JSON, seeded / "analysis.json")
    return GoCodeanalyzer(
        project_dir=None,
        cache_dir=str(cache),
        analysis_level="system_dependency_graph",
        eager_analysis=False,
        target_files=None,
        application_name=APP,
        namespace=NAMESPACE,
    )


@pytest.fixture(scope="module")
def dataflow_pair(local_backend, neo4j_backend):
    """Both backends, once it is known they describe a level-4 application.

    A level-2 payload has no ``cfg``/``ddg`` and no ports, so a dataflow comparison over it would
    be comparing two empty answers and would pass without measuring anything. Skipping is the
    honest outcome; the gate is the *data* (do the ports carry dependence), never a version.
    """
    if not local_backend._ports_carry_dependence:
        pytest.skip("the analysis.json is below -a 4 (no interprocedural ports); the L3/L4 parity checks need one")
    return local_backend, neo4j_backend


def _flowing_pair(local):
    """A ``(caller, caller's plain formal_in, stand-in signature, its plain formal_in)`` quadruple
    that a flow actually connects, or ``None``s.

    Searched rather than taken from the first external call edge: that edge's caller may read only a
    package-level global (so it has no plain parameter to seed from), and its callee may be a
    one-argument library function whose formal is a receiver. The search prefers a pair the local
    backend reports a *path* for, so the comparison exercises witnesses and not just two empty
    answers — and falls back to any well-formed pair, so a refutation is still compared where no
    application flow exists.
    """
    externals = {e.dst for e in local.get_application_view().call_graph if "/@external/" in e.dst}
    fallback = None
    for edge in local.get_application_view().call_graph:
        if edge.dst not in externals or edge.src not in local._dataflow_owners:
            continue
        caller = local._dataflow_owners[edge.src][0]
        callee = local._dataflow_owners.get(edge.dst, (None,))[0]
        if callee is None:
            continue
        plain = lambda c: [n.of for n in (c.body or {}).values() if n.kind == "formal_in" and n.of and not n.of.startswith("<")]  # noqa: E731
        src_names, dst_names = plain(caller), plain(callee)
        if not src_names or not dst_names:
            continue
        callee_sig = local._callable_node(edge.dst).callable
        for s in src_names:
            for d in dst_names:
                quad = (caller.signature, s, callee_sig, d)
                fallback = fallback or quad
                try:
                    if local.paths_between(s, d, src_within=caller.signature, dst_within=callee_sig, depth=None).paths:
                        return quad
                except Exception:
                    continue
    return fallback or (None, None, None, None)


def _one_method_and_its_param(local):
    """A ``(callable signature, formal_in name)`` pair this application actually carries.

    Picked from the data rather than hardcoded, so the parity checks run on whatever application the
    environment points at instead of only on the fixture they were written against. A callable with
    both a ``ddg`` and a named ``formal_in`` is what every accessor below needs.
    """
    for c, _tid, _path in local._iter_callables():
        if not c.ddg:
            continue
        for node in (c.body or {}).values():
            if node.kind == "formal_in" and node.of and not node.of.startswith(("<global>:", "<capture>:")):
                return c.signature, node.of
    return None, None


# -----[ standalone: the graph answers at all ]-----
def test_neo4j_backend_populated(neo4j_backend):
    assert neo4j_backend.get_all_classes() or neo4j_backend.get_functions()
    assert neo4j_backend.get_call_graph().number_of_edges() >= 0


def test_artifact_six_raise_on_neo4j_too(neo4j_backend):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    for name in ("get_artifacts", "get_dependencies", "get_config_keys", "get_config_uses", "get_unresolved_config_reads"):
        with pytest.raises(CodeanalyzerExecutionException):
            getattr(neo4j_backend, name)()


# -----[ parity: the two backends agree on the same application ]-----
def test_type_id_sets_match(local_backend, neo4j_backend):
    assert set(local_backend.get_all_classes()) == set(neo4j_backend.get_all_classes())


def test_function_signature_sets_match(local_backend, neo4j_backend):
    assert set(local_backend.get_functions()) == set(neo4j_backend.get_functions())


def test_call_graph_edge_counts_match(local_backend, neo4j_backend):
    assert local_backend.get_call_graph().number_of_edges() == neo4j_backend.get_call_graph().number_of_edges()


def test_symbol_table_module_counts_match(local_backend, neo4j_backend):
    assert len(local_backend.get_symbol_table()) == len(neo4j_backend.get_symbol_table())


def test_callees_match_on_a_real_callable(local_backend, neo4j_backend):
    edges = local_backend.get_application_view().call_graph
    if not edges:
        pytest.skip("no call edges in this application")
    cid = edges[0].src
    lo = sorted((e.src, e.dst, tuple(e.prov), e.weight) for e in local_backend.get_callees(cid))
    ne = sorted((e.src, e.dst, tuple(e.prov), e.weight) for e in neo4j_backend.get_callees(cid))
    assert lo == ne


# -----[ L3/L4 parity (python-sdk#436): the dataflow surface answers identically ]-----
#
# Every check below compares the two backends rather than asserting a literal, which is the point:
# a literal would pin one application's numbers, while the contract is that two *different*
# implementations — an in-memory index over analysis.json and Cypher over the projected graph —
# cannot disagree. Four of these failed when first run against a live graph, and each failure was a
# real bug in the Cypher (the scope guard refusing stand-in ids, ``:GoExternal`` conflating callable
# stand-ins with import ghosts, and ``_REACHES`` pinning its endpoint to ``:GoCallable``); none was
# visible from the local backend alone, which is why they live here and not in a mocked suite.


def test_degradation_markers_match(dataflow_pair):
    local, graph = dataflow_pair
    assert [d.model_dump() for d in local.get_degraded()] == [d.model_dump() for d in graph.get_degraded()]


def test_external_standins_match(dataflow_pair):
    """The *callable* stand-ins, which on the graph share the ``:GoExternal`` label with the import
    ghosts an unresolved import mints — a package, not a callable, and absent from
    ``analysis.json``'s ``external_symbols``. Conflating the two over-reported 51 against 35."""
    local, graph = dataflow_pair
    assert set(local.get_external_symbols()) == set(graph.get_external_symbols())


def test_ports_carry_dependence_agrees(dataflow_pair):
    local, graph = dataflow_pair
    assert local._ports_carry_dependence == graph._ports_carry_dependence


def test_per_callable_graphs_match(dataflow_pair):
    local, graph = dataflow_pair
    sig, _param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no callable with both a ddg and a named formal_in")
    for getter in ("get_cfg", "get_cdg", "get_ddg"):
        lo, ne = getattr(local, getter)(sig), getattr(graph, getter)(sig)
        assert lo.total == ne.total, getter
        shape = lambda p: [(e.src, e.dst, getattr(e, "kind", None), getattr(e, "var", None), tuple(getattr(e, "prov", []) or [])) for e in p.edges]  # noqa: E731
        assert shape(lo) == shape(ne), getter


def test_edge_page_cursor_is_interchangeable(dataflow_pair):
    """A cursor minted by one backend names the same position on the other — the property that
    makes the two interchangeable mid-pagination, not merely equivalent per call."""
    local, graph = dataflow_pair
    sig, _param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no callable with a ddg")
    first = local.get_ddg(sig, page_size=1)
    if first.next_cursor is None:
        pytest.skip("that callable's ddg fits in one page")
    lo = local.get_ddg(sig, page_size=1, cursor=first.next_cursor)
    ne = graph.get_ddg(sig, page_size=1, cursor=first.next_cursor)
    assert [(e.src, e.dst, e.var) for e in lo.edges] == [(e.src, e.dst, e.var) for e in ne.edges]


def test_value_resolution_matches(dataflow_pair):
    local, graph = dataflow_pair
    sig, param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no callable with a named formal_in")
    shape = lambda v: (v.kind, v.name, v.defined_in, v.ref)  # noqa: E731
    assert shape(local.resolve_value(param, within=sig)) == shape(graph.resolve_value(param, within=sig))


def test_slices_match_in_both_directions(dataflow_pair):
    local, graph = dataflow_pair
    sig, param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no callable with a named formal_in")
    for direction in ("slice_forward", "slice_backward"):
        for depth in (None, 2):
            lo = getattr(local, direction)(param, within=sig, depth=depth)
            ne = getattr(graph, direction)(param, within=sig, depth=depth)
            assert lo.total == ne.total, (direction, depth)
            assert [n.ref for n in lo.nodes] == [n.ref for n in ne.nodes], (direction, depth)
            assert [(n.kind, n.name, n.callable) for n in lo.nodes] == [(n.kind, n.name, n.callable) for n in ne.nodes], (direction, depth)


def test_a_standin_is_reachable_as_a_call_target(dataflow_pair):
    """``reaches`` must admit a library stand-in as the walk's *end*.

    This is the coarse form of the HTTP→SQL question, and the Cypher answered ``False`` for it while
    the local backend answered ``True``: the quantified pattern pinned ``(m)`` to ``:GoCallable``,
    which a stand-in is not. Admitting the endpoint cannot widen the interior — no stand-in has an
    outgoing ``GO_CALLS`` edge, asserted below so the premise is measured and not assumed.
    """
    local, graph = dataflow_pair
    edges = local.get_application_view().call_graph
    assert not [e for e in edges if "/@external/" in e.src], "a stand-in gained an outgoing call edge; _REACHES' premise no longer holds"
    into_standin = next((e for e in edges if "/@external/" in e.dst), None)
    if into_standin is None:
        pytest.skip("this application calls nothing outside itself")
    caller = local._dataflow_owners[into_standin.src][0].signature
    callee = local._callable_node(into_standin.dst).callable
    assert local.reaches(caller, callee) is True
    assert graph.reaches(caller, callee) is True


def test_flow_predicates_match_into_a_standin(dataflow_pair):
    local, graph = dataflow_pair
    caller_sig, src, callee_sig, arg = _flowing_pair(local)
    if caller_sig is None:
        pytest.skip("no caller/stand-in pair with a plain formal_in on both ends")
    assert local.flows_to_call(src, callee_sig, within=caller_sig) == graph.flows_to_call(src, callee_sig, within=caller_sig)
    assert local.flows_to_argument(src, callee_sig, arg, within=caller_sig) == graph.flows_to_argument(src, callee_sig, arg, within=caller_sig)


def test_paths_and_taint_match(dataflow_pair):
    """The witnesses *and* the verdict: a path's hop sequence, and ``taint``'s per-pair
    ``exhausted`` / ``complete`` ledger, must be byte-identical across the two."""
    local, graph = dataflow_pair
    caller_sig, src, callee_sig, dst = _flowing_pair(local)
    if caller_sig is None:
        pytest.skip("no caller/stand-in pair with a plain formal_in on both ends")

    def path_shape(p):
        return (len(p.paths), p.complete, [[(h.via, h.var, tuple(h.prov or []), h.to.ref) for h in path.hops] for path in p.paths])

    lo = local.paths_between(src, dst, src_within=caller_sig, dst_within=callee_sig, depth=None)
    ne = graph.paths_between(src, dst, src_within=caller_sig, dst_within=callee_sig, depth=None)
    assert path_shape(lo) == path_shape(ne)
    # The pair this suite selects is one the local backend finds a route for (see _flowing_pair), so
    # a witness really was compared rather than two empty lists agreeing with each other.
    assert lo.paths, "selected a pair with no path; the comparison above proved nothing"

    def taint_shape(t):
        return (len(t.paths), sorted(t.exhausted), t.complete, t.resolved, len(t.unresolved))

    sources, sinks = [(src, caller_sig)], [(dst, callee_sig)]
    assert taint_shape(local.taint(sources=sources, sinks=sinks)) == taint_shape(graph.taint(sources=sources, sinks=sinks))
    # An explicit depth empties `exhausted` by rule, on both.
    assert taint_shape(local.taint(sources=sources, sinks=sinks, depth=3)) == taint_shape(graph.taint(sources=sources, sinks=sinks, depth=3))
    # A variable cut is applied inside the search, not to the rows it returns, on both -- and it
    # must actually bite, or this asserts that two unsanitized searches agree.
    cut = [(src, caller_sig)]
    lo_cut, ne_cut = local.taint(sources=sources, sinks=sinks, sanitizers=cut), graph.taint(sources=sources, sinks=sinks, sanitizers=cut)
    assert taint_shape(lo_cut) == taint_shape(ne_cut)
    assert not lo_cut.paths, "the variable cut severed nothing, so the sanitizer parity check is vacuous"
    assert sorted(lo_cut.exhausted) == [(src, dst)]


def test_edge_vars_domain_matches(dataflow_pair):
    """The domain a variable sanitizer is validated against. It must be the same set on both, or a
    sanitizer accepted by one backend is refused by the other."""
    local, graph = dataflow_pair
    sig, _param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no callable with a ddg")
    ref = local.resolve_callable(sig).ref
    assert sorted(local._edge_vars_in(ref)) == sorted(graph._edge_vars_in(ref))


def test_a_colliding_signature_fails_and_narrows_identically(dataflow_pair):
    """Go permits a per-file ``init()``, so one signature can name two callables with different
    bodies. Both backends must refuse it the same way *and* accept the same narrowing.

    This diverged: the local backend collapsed the declarations into one candidate and raised a bare
    ``ValueError`` where the graph raised ``AmbiguousName``, and ``in_module=`` naming a file worked
    on neither. Found by review against this graph; invisible from either backend alone.
    """
    local, graph = dataflow_pair
    by_sig = {}
    for c, _tid, path in local._iter_callables():
        by_sig.setdefault(c.signature, []).append(path)
    collisions = {sig: paths for sig, paths in by_sig.items() if len(paths) > 1}
    if not collisions:
        pytest.skip("this application declares no signature in more than one file")
    sig, paths = sorted(collisions.items())[0]

    # Refused the same way, by type -- a bare name cannot pick between two real callables.
    with pytest.raises(Exception) as lo:
        local.resolve_callable(sig)
    with pytest.raises(Exception) as ne:
        graph.resolve_callable(sig)
    assert type(lo.value) is type(ne.value), (type(lo.value), type(ne.value))

    # And narrowed the same way, to the same node, by the file the caller names.
    target = sorted(paths)[0]
    lo_node = local.resolve_callable(sig, in_module=target)
    ne_node = graph.resolve_callable(sig, in_module=target)
    assert (lo_node.ref, lo_node.file, lo_node.callable) == (ne_node.ref, ne_node.file, ne_node.callable)
    assert lo_node.file == target


def test_the_three_graphs_agree_on_a_standin(dataflow_pair):
    """A stand-in has no statements, so all three graphs are empty — and both backends must *say*
    so rather than one crashing. The local backend read a field ``GoExternalSymbol`` does not have.
    """
    local, graph = dataflow_pair
    standins = [s for s in local.get_external_symbols().values() if s.signature]
    if not standins:
        pytest.skip("this application calls nothing outside itself")
    sig = f"{standins[0].module}.{standins[0].signature}"
    for getter in ("get_cfg", "get_cdg", "get_ddg"):
        lo, ne = getattr(local, getter)(sig), getattr(graph, getter)(sig)
        assert (lo.total, list(lo.edges), lo.complete) == (ne.total, list(ne.edges), ne.complete), getter
        assert lo.total == 0, f"{getter} on a stand-in returned edges; is it still body-less?"


def test_miss_paths_match(dataflow_pair):
    """Both backends must *fail* the same way, not merely succeed the same way: an ambiguous name
    and an absent one are part of the contract, and a backend that raised a different type there
    would be a drop-in replacement only on the happy path."""
    local, graph = dataflow_pair
    sig, _param = _one_method_and_its_param(local)
    if sig is None:
        pytest.skip("no resolvable callable")
    with pytest.raises(Exception) as lo:
        local.resolve_callable("definitely_not_a_callable_in_this_app")
    with pytest.raises(Exception) as ne:
        graph.resolve_callable("definitely_not_a_callable_in_this_app")
    assert type(lo.value) is type(ne.value)
    with pytest.raises(Exception) as lo:
        local.resolve_value("definitely_not_a_value", within=sig)
    with pytest.raises(Exception) as ne:
        graph.resolve_value("definitely_not_a_value", within=sig)
    assert type(lo.value) is type(ne.value)
