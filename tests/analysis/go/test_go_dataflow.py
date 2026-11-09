"""The Go L3/L4 dataflow surface, local backend, cango subprocess mocked — no binary, no Neo4j.

Two committed payloads (``tests/resources/go/dataflow/``, see its README for how each was produced
and why): ``httpsql_l4.json`` is the analyzer's own HTTP→SQL fixture at ``-a 4``, whose flow is
gated analyzer-side by ``TestGateL4_HTTPToSQLPath``; ``four_type_kinds.json`` is one declaration of
each ``GoType.kind`` at ``-a 1``.

**What is asserted and what is not.** These tests pin *behaviour a caller depends on* — that the
gated flow is found, that the four Go id/name traps are handled, that a refutation is only made
where one is warranted. They deliberately do not pin edge counts: those move with every analyzer
release and a test that fails on an improvement teaches nothing. Where a count appears it is
because the shape matters (a path's hop sequence, a cap's effect), not the magnitude.

Backend *parity* is measured in ``test_go_neo4j_live.py``, which needs a live graph; that is where
the Cypher is checked, because four of its bugs were invisible from this side.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.go.ids import body_node_id, callable_match_names, external_signature, go_value_names, split_body_node_id
from cldk.models.go import GoAnalysis
from cldk.utils.exceptions.exceptions import AmbiguousName, CodeanalyzerUsageException, SelectorNotInGraph

FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "go" / "dataflow"
HTTPSQL_L4 = FIXTURES / "httpsql_l4.json"
FOUR_KINDS = FIXTURES / "four_type_kinds.json"

#: The sink the whole fixture exists to reach, and the two spellings of its one tainted argument.
SINK = "DB.Query"
SINK_ARG = "query"


def _fake_run_writing_output(payload: str):
    """``subprocess.run`` that writes ``payload`` where cango's ``-o`` would put it."""

    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


def _facade(tmp_path, monkeypatch, payload_path, *, app, level=AnalysisLevel.system_dependency_graph):
    monkeypatch.setenv("CODEANALYZER_GO_BIN", "cango")
    monkeypatch.setattr("subprocess.run", _fake_run_writing_output(payload_path.read_text(encoding="utf-8")))
    return CLDK.go(
        project_path=str(tmp_path),
        analysis_level=level,
        eager=True,
        backend=CodeAnalyzerConfig(application_name=app, namespace=app, cache_dir=str(tmp_path)),
    )


@pytest.fixture
def httpsql(tmp_path, monkeypatch):
    """The level-4 HTTP→SQL fixture, through the facade."""
    return _facade(tmp_path, monkeypatch, HTTPSQL_L4, app="httpsql")


@pytest.fixture
def backend(httpsql):
    return httpsql.backend


# ======================================================================================
# The L3/L4 overlays survive validation. The *parse blocker* that shipped alongside them
# (GoType.kind omitting "alias") is a model-level fact about -a 1 output with nothing to
# do with dataflow, so it is pinned in test_go_analysis.py beside the other model tests.
# ======================================================================================


def test_the_level_four_fixture_round_trips_losslessly():
    """Every overlay the dataflow surface reads must survive validation — the regression that
    matters is the *silent* one, where a level-4 document parses and the graph is simply gone."""
    analysis = GoAnalysis.model_validate_json(HTTPSQL_L4.read_text(encoding="utf-8"))
    app = analysis.application
    assert analysis.max_level == 4 and analysis.k_limit is not None
    assert app.param_in and app.param_out and app.external_symbols
    callables = [c for m in app.symbol_table.values() for c in m.functions.values()]
    assert any(c.cfg and c.cdg and c.ddg for c in callables), "no callable kept its L3 graphs"
    assert any(c.summary for c in callables), "no callable kept its L4 summary edges"
    vertices = [n for c in callables for n in (c.body or {}).values() if n.kind == "formal_in"]
    assert vertices and all(v.id and v.of for v in vertices), "L4 vertices lost their id or `of`"


def test_absent_overlays_stay_none_not_empty():
    """``None`` and ``[]`` mean different things and the distinction is load-bearing: ``None`` is
    "the analyzer did not compute this", ``[]`` is "it did, and there are no edges". Collapsing
    them is what makes an empty answer indistinguishable from a proved absence (D7)."""
    analysis = GoAnalysis.model_validate_json(FOUR_KINDS.read_text(encoding="utf-8"))
    callables = [c for m in analysis.application.symbol_table.values() for c in m.functions.values()]
    assert callables and all(c.cfg is None and c.ddg is None and c.summary is None for c in callables)


# ======================================================================================
# The four Go-specific id/name traps (spec §7). Each is a rule that holds for Python or
# TypeScript and does NOT hold for Go, so a shared helper would be confidently wrong.
# ======================================================================================


def test_trap1_a_standin_vertex_id_is_not_split_on_its_first_at(backend):
    """A stand-in's *callable* id contains ``/@external/`` before the vertex ``@``, so
    ``partition("@")`` yields a callable half that names nothing — and hands it onward as if it
    did. Every vertex must recover its true owner, by prefix-stripping instead."""
    owners = list(backend._dataflow_owners)
    standins = [e for e in backend.get_external_symbols().values() if e.body]
    assert standins, "fixture carries no stand-in with a body"
    naive_failures = 0
    for sym in standins:
        for key, node in sym.body.items():
            assert split_body_node_id(node.id, owners) == (sym.id, key if key.startswith("@") else f"@{key}")
            naive_failures += node.id.partition("@")[0] not in backend._dataflow_owners
    assert naive_failures > 0, "the naive split would have worked here, so this proves nothing"


def test_trap1_a_nested_closure_does_not_lose_vertices_to_its_parent(backend):
    """A callable id can be a proper *prefix* of a nested closure's (``…Handler`` and
    ``…Handler.closure_1``), so the longest match is the right one — the shorter would claim the
    longer's vertices."""
    owners = list(backend._dataflow_owners)
    nested = [cid for cid in owners if ".closure_" in cid]
    assert nested, "fixture carries no closures"
    for cid in nested:
        c = backend._dataflow_owners[cid][0]
        for key in c.body or {}:
            assert split_body_node_id(body_node_id(cid, key), owners)[0] == cid


def test_trap2_a_methods_receiver_resolves_by_of_not_by_parameter_ordinal(backend):
    """``formal_in:0`` on a method is the *receiver*, which never appears in ``parameters``. A
    backend pairing ``formal_in:N`` with ``parameters[N]`` would name every formal one position off,
    silently — so the receiver is addressable and the next formal is still the first parameter."""
    method = backend._dataflow_owners[backend.resolve_callable("sqlRepo.Find").ref][0]
    assert [p.name for p in method.parameters] == ["id"], "fixture changed shape"
    receiver = (method.body or {})["@formal_in:0"]
    assert receiver.of == "s" and receiver.of not in [p.name for p in method.parameters]
    assert backend.resolve_value("s", within="sqlRepo.Find").ref.endswith("@formal_in:0")
    assert backend.resolve_value("id", within="sqlRepo.Find").ref.endswith("@formal_in:1")


def test_trap3_a_global_is_matchable_by_the_spelling_a_go_programmer_writes():
    """``value_candidate`` renders a global's module half as the import path, whose separators are
    ``/`` — and ``segment_match`` narrows on ``.``. So ``libflow.Last`` matched *nothing* until Go
    added the package-qualified spelling as a second name for the same value."""
    from cldk.analysis.commons.resolve import segment_match

    names = go_value_names("<global>:example.com/dataflow/libflow::Last")
    assert names == ["example.com/dataflow/libflow.Last", "libflow.Last"]
    # The canonical name stays first, so an error message and a SliceNode read in one vocabulary.
    assert not segment_match("libflow.Last", names[0]), "the trap this exists for has gone away"
    assert segment_match("libflow.Last", names[1])
    # A parameter and a capture have exactly one name, so this is the identity there.
    assert go_value_names("id") == ["id"]
    assert go_value_names("<capture>:buf") == ["buf"]


def test_trap4_a_degraded_analysis_cannot_certify_an_absence(backend, monkeypatch):
    """``degraded[]`` is the honesty signal. A returned path over an approximated graph is still a
    real may-flow, so this must **not** refuse — it must void the *absence* claims, which is what
    ``exhausted`` carries. The clean fixture refutes an unreachable pair; the same call over a
    degraded analysis refutes nothing."""
    from cldk.models.go import GoDegraded

    unreachable = dict(sources=[("id", "sqlRepo.Find")], sinks=[("user", "sqlRepo.Count")])
    clean = backend.taint(**unreachable)
    assert clean.exhausted == [("id", "user")] and not clean.paths

    monkeypatch.setattr(type(backend), "get_degraded", lambda self: [GoDegraded(code="range_func", packages=["x"])])
    degraded = backend.taint(**unreachable)
    assert not degraded.paths, "the flow did not change"
    assert degraded.exhausted == [], "an approximated graph certified an absence"
    assert degraded.complete is False
    assert any("degrad" in d.message for d in degraded.unresolved), "no diagnostic says why"
    # And a flow that IS found is still reported -- refusing would discard the findings that matter.
    found = backend.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])
    assert found.paths and found.exhausted == []


def test_the_degradation_notice_is_stated_once_per_batch_not_once_per_pair(backend, monkeypatch):
    """The condition is a property of the whole analysis — the approximation is somewhere in the
    SDG and nothing can say which pairs ran through it — so it is one diagnostic, filed under a key
    no pair claims, which is ``taint_verdict``'s own mechanism for a diagnostic it cannot attribute.

    The verdict is identical either way; filing per pair would say the same thing m×n times (400
    copies on a 40×10 batch), which buries the ledger a caller is meant to read.
    """
    from cldk.models.go import GoDegraded

    monkeypatch.setattr(type(backend), "get_degraded", lambda self: [GoDegraded(code="no_vta")])
    batch = backend.taint(
        sources=[("id", "sqlRepo.Find"), ("id", "sqlRepo.SanitizedFind"), ("user", "sqlRepo.Count")],
        sinks=[(SINK_ARG, SINK), ("db", SINK)],
    )
    assert len(batch.unresolved) == 1, "one batch-wide condition produced more than one diagnostic"
    assert batch.unresolved[0].code == "analysis_degraded"
    # The verdict is what matters, and it is the strict one: nothing certified, batch not complete.
    assert batch.exhausted == [] and batch.complete is False
    assert batch.paths, "the witnesses are still reported -- a degraded graph is not a refusal"


# ======================================================================================
# Addressing: the spellings spec §7 requires, and the ambiguities that must raise.
# ======================================================================================


@pytest.mark.parametrize(
    "spelling",
    ["sqlRepo.Find", "(*sqlRepo).Find", "httpsql.sqlRepo.Find", "example.com/httpsql.sqlRepo.Find"],
)
def test_the_go_receiver_spellings_all_resolve(backend, spelling):
    assert backend.resolve_callable(spelling).callable.endswith("httpsql.sqlRepo.Find")


def test_a_bare_method_name_shared_with_its_interface_is_ambiguous(backend):
    """``Find`` names both the ``Repo`` interface's declaration and ``sqlRepo``'s implementation,
    and they are different callables with different dataflow — the interface method has formal
    vertices but no body. Picking either would be a guess, so the receiver is how a caller says
    which, and ``in_class=`` narrows it the same way."""
    with pytest.raises(AmbiguousName) as e:
        backend.resolve_callable("Find")
    assert {c.rpartition(".")[0].rpartition(".")[2] for c in e.value.candidates} == {"Repo", "sqlRepo"}
    assert backend.resolve_callable("sqlRepo.Find").callable.endswith("sqlRepo.Find")
    assert backend.resolve_callable("Repo.Find").callable.endswith("Repo.Find")


@pytest.mark.parametrize("spelling", ["DB.Query", "sql.DB.Query", "database/sql.DB.Query"])
def test_a_library_standin_is_addressable_as_a_sink(backend, spelling):
    """Spec §7's own worked example. A stand-in is not a project callable, but its ``formal_in``
    ports are real L4 vertices a flow passes through — so it has to be in the resolution domain or
    ``flows_to_call(…, "DB.Query")`` cannot be asked at all."""
    node = backend.resolve_callable(spelling)
    assert node.callable == "database/sql.DB.Query"
    assert node.kind == "external" and node.file == "", "a stand-in was given a fabricated position"
    assert "/@external/" in node.ref


def test_an_ambiguous_name_raises_listing_candidates_rather_than_guessing(backend):
    """``Query`` names both ``sql.DB.Query`` and ``url.URL.Query`` in this fixture. Picking one
    would be the confident-wrong-answer failure the addressing layer exists to prevent."""
    with pytest.raises(AmbiguousName) as e:
        backend.resolve_callable("Query")
    assert len(e.value.candidates) >= 2
    assert all("Query" in c for c in e.value.candidates)


def test_a_name_that_matches_nothing_raises(backend):
    with pytest.raises(SelectorNotInGraph):
        backend.resolve_callable("NoSuchCallable")
    with pytest.raises(SelectorNotInGraph):
        backend.resolve_value("no_such_value", within="sqlRepo.Find")


def test_in_module_narrows_by_the_go_package_clause(backend):
    """A Go module's readable name is its ``package`` clause. The shared derivation strips a ``.py``
    suffix and would leave ``httpsql.go`` riding into the dotted name, matching nothing."""
    assert backend.resolve_callable("sqlRepo.Find", in_module="httpsql").callable.endswith("sqlRepo.Find")
    with pytest.raises(SelectorNotInGraph, match="in_module"):
        backend.resolve_callable("sqlRepo.Find", in_module="not_a_package")


def test_two_packages_exporting_one_leaf_name_are_split_by_the_qualified_spelling():
    """Two globals sharing a leaf name are a real ambiguity, and the package-qualified spelling is
    what resolves it — the capability trap 3 buys.

    Asserted on :func:`~cldk.analysis.go.ids.go_value_names` directly rather than through a facade:
    the ``httpsql`` fixture reads no package-level globals, and manufacturing one would be a fixture
    written to suit the test. The condition *was* measured end to end on the analyzer's full
    ``testdata/dataflow`` corpus, where ``libstore`` and ``wrap`` both export ``lastQuery`` and one
    callable reads both — a bare ``lastQuery`` raises there and each qualified form resolves.
    """
    a = go_value_names("<global>:example.com/dataflow/libstore::lastQuery")
    b = go_value_names("<global>:example.com/dataflow/wrap::lastQuery")
    # The leaf collides, so a bare name cannot pick -- and the qualified names do not collide, so
    # naming the package always can.
    assert a[0].rpartition(".")[2] == b[0].rpartition(".")[2] == "lastQuery"
    assert a[-1] == "libstore.lastQuery" and b[-1] == "wrap.lastQuery"
    assert set(a).isdisjoint(b)


# ======================================================================================
# The per-callable graphs, and the level gate.
# ======================================================================================


@pytest.mark.parametrize("getter", ["get_cfg", "get_cdg", "get_ddg"])
def test_the_three_graphs_answer_with_absolute_endpoint_ids(httpsql, getter):
    """Endpoints are the body nodes' own ``can://`` ids — the spelling the graph merges on and the
    one ``get_source`` accepts — not the callable-local keys the payload carries."""
    page = getattr(httpsql, getter)("sqlRepo.SanitizedFind")
    assert page.edges and page.total == len(page.edges)
    for e in page.edges:
        assert e.src.startswith("can://") and e.dst.startswith("can://")
        assert "SanitizedFind" in e.src


def test_the_ddg_carries_gos_three_provenance_tiers(httpsql):
    """Unlike TypeScript's single tier, Go emits ``reaching-defs`` at L3 and adds ``ssa`` /
    ``points-to`` at L4 — so ``prov_rank``'s certainty ordering is live here."""
    seen = {p for sig in ("sqlRepo.Find", "sqlRepo.SanitizedFind", "httpsql.Handler") for e in httpsql.get_ddg(sig).edges for p in e.prov}
    assert "reaching-defs" in seen
    assert seen - {"reaching-defs"}, "no L4 provenance tier present; is this payload really -a 4?"


def test_a_cfg_edge_keeps_its_kind_so_two_successors_stay_two_edges(httpsql):
    page = httpsql.get_cfg("sqlRepo.SanitizedFind")
    assert {e.kind for e in page.edges} - {"fallthrough"}, "no conditional edge kinds survived"


def test_paging_is_a_prefix_of_one_total_order(httpsql):
    """A cursor is a position in the canonical order, so consecutive pages partition the edge set
    without overlap or gap — and ``total`` is the whole size from the first page, not the page's."""
    whole = httpsql.get_ddg("sqlRepo.SanitizedFind")
    assert whole.total > 2, "fixture too small to page"
    first = httpsql.get_ddg("sqlRepo.SanitizedFind", page_size=2)
    assert len(first.edges) == 2 and first.total == whole.total and first.next_cursor
    second = httpsql.get_ddg("sqlRepo.SanitizedFind", page_size=2, cursor=first.next_cursor)
    assert [(e.src, e.dst) for e in first.edges + second.edges] == [(e.src, e.dst) for e in whole.edges[:4]]


def test_a_shallow_analysis_refuses_rather_than_answering_empty(tmp_path, monkeypatch):
    """At a level below the dataflow pass the three lists are absent, and an empty page there would
    mean "not analysed" while looking exactly like "no dependence" (D7). So it raises, naming the
    level to rebuild at."""
    shallow = _facade(tmp_path, monkeypatch, FOUR_KINDS, app="kinds", level=AnalysisLevel.symbol_table)
    with pytest.raises(CodeanalyzerUsageException, match="level 3"):
        shallow.get_cfg("kinds.Use")


def test_bad_bounds_are_refused_before_any_name_is_resolved(httpsql):
    """The order of the checks is part of the contract: a caller with a malformed ``page_size``
    hears about ``page_size``, not about a name, and pays for no resolution to learn it."""
    with pytest.raises(ValueError):
        httpsql.get_cfg("NoSuchCallableAtAll", page_size=0)


# ======================================================================================
# The flow surface: the gated HTTP -> SQL question, and the refutations.
# ======================================================================================


def test_the_gated_http_to_sql_flow_is_found(httpsql):
    """``TestGateL4_HTTPToSQLPath``'s question, asked through the SDK. Interface dispatch,
    interprocedural binding and the library stand-in all have to be right; any one missing yields
    ``False``, and ``False`` is the answer that closes a live alert."""
    assert httpsql.flows_to_call("id", SINK, within="sqlRepo.Find") is True
    assert httpsql.flows_to_argument("id", SINK, SINK_ARG, within="sqlRepo.Find") is True


def test_flows_to_argument_implies_flows_to_call(httpsql):
    """A fact about their *targets* — one is the other narrowed to a single port — so the two can
    never disagree."""
    for within in ("sqlRepo.Find", "sqlRepo.SanitizedFind", "sqlRepo.Count"):
        arg_name = "id" if "Find" in within else "user"
        if httpsql.flows_to_argument(arg_name, SINK, SINK_ARG, within=within):
            assert httpsql.flows_to_call(arg_name, SINK, within=within)


def test_a_path_reads_as_a_sequence_of_justified_hops(httpsql):
    """A path is a sequence, and each hop says what justified it in the *caller's* vocabulary —
    ``data`` / ``argument`` / ``return`` / ``summary`` / ``control``, never ``GO_DDG``."""
    paths = httpsql.paths_between("id", SINK_ARG, src_within="sqlRepo.Find", dst_within=SINK, depth=None)
    assert paths.paths and paths.complete
    hops = paths.paths[0].hops
    assert {h.via for h in hops} <= {"data", "control", "argument", "return", "summary"}
    # It crosses into the callee, which is the hop an intraprocedural answer could not produce.
    assert hops[-1].via == "argument" and "/@external/" in hops[-1].to.ref
    assert hops[-1].to.kind == "parameter" and hops[-1].to.name == SINK_ARG


def test_max_paths_caps_witnesses_and_says_so(httpsql):
    capped = httpsql.paths_between("id", SINK_ARG, src_within="sqlRepo.Find", dst_within=SINK, max_paths=1)
    assert len(capped.paths) <= 1


def test_the_two_slices_are_complementary(httpsql):
    """Forward is what a value affects, backward what affects it. The sink's port must be in the
    source's forward slice, and symmetrically."""
    fwd = httpsql.slice_forward("id", within="sqlRepo.Find", depth=None)
    sink_ref = httpsql.resolve_value(SINK_ARG, within=SINK).ref
    assert sink_ref in [n.ref for n in fwd.nodes]
    back = httpsql.slice_backward(SINK_ARG, within=SINK, depth=None)
    src_ref = httpsql.resolve_value("id", within="sqlRepo.Find").ref
    assert src_ref in [n.ref for n in back.nodes]


def test_a_capped_slice_reports_the_whole_size(httpsql):
    """A cap that fires is reported rather than silently returning less — ``total`` is the size of
    the whole slice, which is what lets a caller see what it is walking into."""
    whole = httpsql.slice_forward("id", within="sqlRepo.Find", depth=None)
    assert whole.total > 1, "fixture too small"
    capped = httpsql.slice_forward("id", within="sqlRepo.Find", depth=None, max_nodes=1)
    assert len(capped.nodes) == 1 and capped.total == whole.total and capped.complete is False


def test_a_slice_describes_a_standin_vertex_without_inventing_a_file(httpsql):
    """A stand-in has no source file. ``kind="external"``/``file=""`` says so, rather than leaving
    a fabricated path to be discovered as a sentinel later."""
    fwd = httpsql.slice_forward("id", within="sqlRepo.Find", depth=None)
    external = [n for n in fwd.nodes if "/@external/" in n.ref]
    assert external, "the flow never left the project, so this proves nothing"
    assert all(n.file == "" for n in external)


def test_describe_hydrates_real_text_and_leaves_vertices_none(httpsql):
    """Source is a second call because a slice answers *where* and source answers *what*. A
    parameter-passing vertex is a dataflow position, not a region of the file, so it has no text —
    ``None``, not ``""``.

    **A vertex is identified by its ``ref``, not by its ``kind``**, and that is the point worth
    pinning: ``kind="return"`` is reported both for a ``return`` *statement* (which has a span, and
    text) and for a ``formal_out`` *vertex* (which has neither), so a caller deciding from ``kind``
    alone would expect text where there is none. The id is what distinguishes them.
    """
    nodes = httpsql.slice_forward("id", within="sqlRepo.Find", depth=2).nodes
    hydrated = httpsql.describe(nodes)
    assert any(n.source and "Query" in n.source for n in hydrated), "nothing hydrated"
    is_vertex = lambda n: any(tag in n.ref for tag in ("formal_in", "formal_out", "actual_in", "actual_out", "@entry", "@exit"))  # noqa: E731
    vertices = [n for n in hydrated if is_vertex(n)]
    assert vertices, "the slice reached no parameter-passing vertex"
    assert all(n.source is None for n in vertices), "a dataflow position was given source text"
    statements = [n for n in hydrated if not is_vertex(n)]
    assert statements and all(n.source for n in statements), "a real statement came back with no text"
    # The same ``kind`` spans both groups, which is why the id and not the kind is the test.
    assert {n.kind for n in vertices} & {n.kind for n in statements}


# ======================================================================================
# taint: the verb triage needs. The rules are shared with the siblings (leg 4b, #382).
# ======================================================================================


def test_taint_finds_the_flow_and_certifies_nothing_it_should_not(httpsql):
    result = httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])
    assert result.paths and result.complete
    assert result.exhausted == [], "a pair with a witness was also certified absent"
    assert "sqlRepo.Find" in result.resolved and "DB.Query" in result.resolved


def test_taint_refutes_a_pair_only_when_it_searched_to_exhaustion(httpsql):
    """The distinction ``paths_between`` cannot report: an unreachable pair is *refuted*, and the
    refutation is named by the strings the caller passed."""
    result = httpsql.taint(sources=[("user", "sqlRepo.Count")], sinks=[("id", "sqlRepo.Find")])
    assert not result.paths and result.exhausted == [("user", "id")]


def test_an_explicit_depth_empties_exhausted_by_rule(httpsql):
    """A pair with no path inside N hops is not refuted, it is *unmeasured*. Conflating the two is
    the bounded-boolean error, so a bounded search certifies nothing."""
    bounded = httpsql.taint(sources=[("user", "sqlRepo.Count")], sinks=[("id", "sqlRepo.Find")], depth=2)
    assert bounded.exhausted == []
    unbounded = httpsql.taint(sources=[("user", "sqlRepo.Count")], sinks=[("id", "sqlRepo.Find")])
    assert unbounded.exhausted == [("user", "id")], "the pair is genuinely unreachable, so this is the control"


def test_max_paths_caps_per_pair_never_per_call(httpsql):
    """With one sink and several sources a flat cap would let one prolific pair starve the rest, and
    in triage the per-source witness *is* the answer."""
    sources = [("id", "sqlRepo.Find"), ("id", "sqlRepo.SanitizedFind"), ("user", "sqlRepo.Count")]
    result = httpsql.taint(sources=sources, sinks=[(SINK_ARG, SINK)], max_paths=1)
    witnesses = {(p.hops[0].frm.callable if p.hops else None) for p in result.paths}
    assert len(result.paths) >= 2, "a flat cap would have returned one path for the whole batch"
    assert len(witnesses) >= 2


def test_a_variable_sanitizer_cuts_inside_the_search(httpsql):
    """Both cuts are applied *inside* the traversal, not to the rows it returns: filtering afterwards
    would report nothing for a source whose ten shortest paths are sanitized and whose eleventh is
    not — a false refutation, the one output this accessor must never produce."""
    unsanitized = httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])
    assert unsanitized.paths, "nothing to cut"
    cut = httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)], sanitizers=[("id", "sqlRepo.Find")])
    assert not cut.paths and cut.exhausted == [("id", SINK_ARG)]


def test_a_sanitizer_naming_a_nonexistent_variable_is_refused(httpsql):
    """The domain is the variables on SDG edges *scoped to that callable* — not the parameters, since
    most real edge variables are locals. A sanitizer the caller believes is in force and is not would
    be worse than an error."""
    with pytest.raises((SelectorNotInGraph, ValueError)):
        httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)], sanitizers=[("no_such_var", "sqlRepo.Find")])


def test_taint_refuses_an_empty_batch_rather_than_answering_nothing(httpsql):
    with pytest.raises(ValueError, match="sources="):
        httpsql.taint(sources=[], sinks=[(SINK_ARG, SINK)])
    with pytest.raises(ValueError, match="sinks="):
        httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[])


def test_a_bare_string_batch_is_a_typeerror_not_a_silent_unpack(httpsql):
    """``sources="id"`` would unpack into pairs of characters. Refusing it by type is the only way a
    caller hears about the mistake rather than about a name that does not exist."""
    with pytest.raises(TypeError):
        httpsql.taint(sources="id", sinks=[(SINK_ARG, SINK)])


def test_two_selectors_naming_one_position_are_one_pair(httpsql):
    """A pair is a pair of *resolved positions*, so a duplicated selector neither doubles the
    witnesses nor the cap."""
    once = httpsql.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])
    twice = httpsql.taint(sources=[("id", "sqlRepo.Find"), ("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])
    assert len(twice.paths) == len(once.paths)


# ======================================================================================
# The honesty signals, and the gates.
# ======================================================================================


def test_the_standins_are_reported_with_readable_names(httpsql):
    """Where a path's external hops are explained. The reported signature is assembled from the
    stand-in's own ``module``/``signature``, not parsed back out of its ``can://`` id."""
    symbols = httpsql.get_external_symbols()
    assert symbols
    sigs = {external_signature(s.module, s.signature, s.name) for s in symbols.values()}
    assert "database/sql.DB.Query" in sigs
    query = next(s for s in symbols.values() if s.signature == "DB.Query")
    assert query.body, "a stand-in with no body cannot be flowed through"
    assert any(n.kind == "formal_in" for n in query.body.values())


def test_a_clean_analysis_reports_no_degradation(httpsql):
    assert httpsql.get_degraded() == []


def test_the_port_gate_refuses_when_no_port_carries_dependence(tmp_path, monkeypatch):
    """The interprocedural layer can be *present* and have nothing crossing into it, in which case
    every slice is empty for want of edges rather than for want of a flow. The probe reads the data,
    never a version — so a payload whose ports connect answers instead."""
    doc = json.loads(HTTPSQL_L4.read_text(encoding="utf-8"))
    doc["application"]["param_in"] = []
    doc["application"]["param_out"] = []

    def strip(owner):
        """Empty every dependence list, closures included.

        Recursive because a Go closure is a callable in its own right with its own dataflow — and a
        non-recursive strip left ``Handler.closure_1``'s edges in place, so a port still had an
        outgoing edge and the probe correctly answered ``True``. The bug was in this setup, not in
        the probe, which is the distinction a shallow strip hides.
        """
        owner["ddg"], owner["cdg"], owner["summary"] = [], [], []
        for inner in (owner.get("callables") or {}).values():
            strip(inner)

    for module in doc["application"]["symbol_table"].values():
        for owner in list(module.get("functions", {}).values()) + [c for t in module.get("types", {}).values() for c in t.get("callables", {}).values()]:
            strip(owner)
    payload = tmp_path / "portless.json"
    payload.write_text(json.dumps(doc), encoding="utf-8")
    project = tmp_path / "p"
    project.mkdir()
    portless = _facade(project, monkeypatch, payload, app="httpsql")
    assert portless.backend._ports_carry_dependence is False
    with pytest.raises(Exception, match="dependence"):
        portless.slice_forward("id", within="sqlRepo.Find")
    with pytest.raises(Exception, match="dependence"):
        portless.taint(sources=[("id", "sqlRepo.Find")], sinks=[(SINK_ARG, SINK)])


# ======================================================================================
# A Go signature is not always a unique address. Several files in one package may each
# declare their own init(), so one signature names several DIFFERENT callables with
# different bodies. Both bugs below were found by review against a live graph.
# ======================================================================================


def test_a_signature_declared_in_several_files_is_an_ambiguity_not_a_pick():
    """Go permits a per-file ``init()``, so one signature can name two callables.

    The local backend keyed its candidate list by *signature*, which collapsed the two into one
    entry and hid the ambiguity from the shared policy — so it raised a bare ``ValueError`` where
    the Neo4j backend (whose rows are per declaration) raised ``AmbiguousName``, and ``in_module=``
    could not narrow it on either. One candidate per declaration is what makes both backends hand
    the policy the same domain.

    Asserted on :func:`~cldk.analysis.go.ids.pick_declaration` directly: the committed fixtures are
    single-file, and the condition was measured end to end on the analyzer's ``testdata/dataflow``
    corpus (3 colliding signatures there, `initclosure.init` / `initclosure.init.closure_1` /
    `inits.init`), where both backends now answer identically.
    """
    from cldk.analysis.go.ids import pick_declaration

    decls = [("initclosure/a.go", None), ("initclosure/b.go", None)]
    # One declaration needs no narrowing.
    assert pick_declaration("x.init", [("initclosure/a.go", None)], in_class=None, in_module=None) == "initclosure/a.go"
    # Two do, and the error names the paths -- passing one is the way out, so "it is ambiguous"
    # alone would be advice a caller cannot act on.
    with pytest.raises(ValueError, match="declared in 2 files") as e:
        pick_declaration("x.init", decls, in_class=None, in_module=None)
    assert "initclosure/a.go" in str(e.value) and "initclosure/b.go" in str(e.value)
    # The keyword that narrowed the candidates narrows the declaration too -- by bare file name or
    # by the fuller path, since `in_module=` matches segment-wise on `/`.
    assert pick_declaration("x.init", decls, in_class=None, in_module="a.go") == "initclosure/a.go"
    assert pick_declaration("x.init", decls, in_class=None, in_module="initclosure/b.go") == "initclosure/b.go"
    # A keyword that cannot split them leaves the ambiguity rather than picking.
    with pytest.raises(ValueError, match="declared in 2 files"):
        pick_declaration("x.init", decls, in_class=None, in_module="initclosure")


def test_the_three_graphs_answer_empty_for_a_library_standin(httpsql):
    """A stand-in resolves (it is spec §7's own example) and has no statements — the analyzer
    synthesizes only its ``formal_in``/``formal_out`` ports — so all three graphs are legitimately
    empty there.

    The local backend read ``.cfg`` off the model blindly and raised ``AttributeError``, since
    :class:`~cldk.models.go.GoExternalSymbol` has no such field, while the Neo4j backend returned a
    clean empty page. An empty page is the honest answer, and it is **not** the D7 case the level
    gate guards: there, empty would be indistinguishable from "no dependence" because the pass never
    ran; here the pass ran and the absence is a fact about what a stand-in is.
    """
    standin = httpsql.resolve_callable("DB.Query")
    assert standin.kind == "external"
    for getter in ("get_cfg", "get_cdg", "get_ddg"):
        page = getattr(httpsql, getter)("DB.Query")
        assert page.edges == [] and page.total == 0 and page.complete is True, getter
        assert page.next_cursor is None, getter
    # Its ports are still real vertices, so the flow surface DOES cross it -- which is the whole
    # reason an empty page here must not read as "nothing to see".
    assert httpsql.flows_to_argument("id", "DB.Query", "query", within="sqlRepo.Find") is True


def test_match_names_widen_resolution_without_multiplying_ambiguity(backend):
    """``callable_match_names`` adds the package-qualified and pointer-receiver spellings. A
    candidate matched by *any* of its names counts once, so these widen what resolves rather than
    making more things ambiguous."""
    names = callable_match_names("example.com/httpsql.sqlRepo.Find")
    assert names == ("example.com/httpsql.sqlRepo.Find", "httpsql.sqlRepo.Find", "(*sqlRepo).Find")
    assert len(set(names)) == len(names), "a duplicate spelling would double-count a candidate"
    # A signature with no slash has no package-qualified form to add.
    assert callable_match_names("pkg.Fn") == ("pkg.Fn", "(*pkg).Fn")
