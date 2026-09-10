# tests/analysis/commons/test_lifted_helpers.py
import hashlib
import importlib, pytest

LIFTED = {
    "cldk.analysis.commons.bounds": ["DEFAULT_PAGE_SIZE", "DEFAULT_DEPTH", "DEFAULT_MAX_NODES", "DEFAULT_MAX_PATHS",
        "check_depth", "check_max_nodes", "check_max_paths", "check_page_size", "check_distinct_endpoints",
        "reject_bare_string", "check_selector", "encode_cursor", "decode_cursor", "keyset_where", "cursor_params",
        "edge_page", "EdgeOrder"],
    "cldk.analysis.commons.graphs": ["bounded_subgraph", "hop_sort_key", "slice_resolved", "cone_sinks",
        "as_slice_node", "flow_path", "edge_sort_key", "sdg_rels", "sdg_rel_pattern", "via_table", "via_case", "path_order"],
    "cldk.analysis.commons.keys": ["resolve_module_key", "scope_paths", "call_graph_scope", "module_key_of", "module_dotted"],
}

@pytest.mark.parametrize("module,names", LIFTED.items())
def test_each_helper_lives_in_commons_and_python_reexports_it(module, names):
    home = importlib.import_module(module)
    py = importlib.import_module("cldk.analysis.python.backend")
    for n in names:
        assert hasattr(home, n), f"{n} missing from {module}"
        if hasattr(py, n):
            assert getattr(py, n) is getattr(home, n), f"{n} re-exported from python.backend is a copy, not the same object"

def test_python_tables_are_the_P_prefixed_instances():
    from cldk.analysis.commons.graphs import sdg_rels, via_table
    from cldk.analysis.python import backend
    assert backend.SDG_RELS == sdg_rels("PY")
    assert backend.VIA == via_table("PY")
    assert sdg_rels("TS")[0].startswith("TS_")

def test_module_dotted_defaults_to_python_and_takes_other_extensions():
    from cldk.analysis.commons.keys import module_dotted
    assert module_dotted("odoo/tools/mail.py") == "odoo.tools.mail"
    assert module_dotted("src/pages/Home.tsx", extensions=(".ts", ".tsx")) == "src.pages.Home"


def test_semver_and_the_artifact_reconstructors_are_lifted_and_python_reexports_them():
    """Task 4 lift: the version-floor parser and the shared artifact layer's reconstructors live in
    commons; the Python Neo4j backend and its reconstruct module bind the same objects."""
    from cldk.analysis.commons import artifacts, backend
    from cldk.analysis.python.neo4j import neo4j_backend, reconstruct

    assert neo4j_backend._semver is backend.semver
    for n in ("artifact", "config_key", "dependency"):
        assert getattr(reconstruct, n) is getattr(artifacts, n), f"{n} re-exported from python reconstruct is a copy"
    assert backend.semver("1.4.1.post0") == (1, 4, 1) and backend.semver("garbage") is None


# ----------------------------------------------------------------------------------------------
# TS-1 (leg 2.5b, Task 0): commons/ speaks no language's declaration schema.
# ----------------------------------------------------------------------------------------------

#: The one sanctioned exception, documented in ``cldk/analysis/commons/backend.py``'s module
#: docstring: the repository-artifact layer is the part of the graph every analyzer projects
#: identically and unprefixed (``:Artifact``, ``:ConfigKey``, ``:Package``, ``HAS_ARTIFACT``,
#: ``DECLARES_DEPENDENCY``, ``DEFINES_CONFIG``, ``LOCKS``), and codeanalyzer-python's own schema
#: module documents the ``Py`` on these five as "naming precedent, not a Python-specific claim".
#: Every other name from a language package is the defect this test exists to catch.
SHARED_ARTIFACT_LAYER = frozenset({"PyArtifact", "PyConfigKey", "PyConfigRead", "PyConfigUseEdge", "PyDependency"})

LANGUAGE_PACKAGES = ("cldk.models.python", "cldk.models.typescript", "cldk.models.java", "codeanalyzer")


def _language_imports():
    """Every ``(module, source_package, name)`` a module under ``cldk/analysis/commons/`` imports
    from a language package, read off the AST.

    Parsed, not imported: ``import cldk`` pulls the whole SDK in, so a runtime check on
    ``sys.modules`` would see every language package no matter which module asked for it.
    """
    import ast
    from pathlib import Path

    import cldk.analysis.commons as commons

    root = Path(commons.__file__).parent
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == p or node.module.startswith(p + ".") for p in LANGUAGE_PACKAGES):
                    for alias in node.names:
                        yield rel, node.module, alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == p or alias.name.startswith(p + ".") for p in LANGUAGE_PACKAGES):
                        yield rel, alias.name, alias.name


def test_commons_imports_no_language_package_beyond_the_shared_artifact_layer():
    """TS-1: ``LocateResult`` was typed on ``codeanalyzer-python``'s ``BodyNode``/``Span``, which
    made ``commons/results.py`` import one language's schema to declare a language-neutral result.
    ``BodyRef`` and the commons ``Span`` replace it."""
    offenders = [(mod, src, name) for mod, src, name in _language_imports() if name not in SHARED_ARTIFACT_LAYER]
    assert offenders == [], f"commons/ imports language-package names beyond the artifact layer: {offenders}"


def test_locate_result_carries_a_language_neutral_body_ref():
    from cldk.analysis.commons.results import BodyRef, LocateResult, Span

    assert LocateResult.model_fields["body"].annotation == (BodyRef | None)
    assert LocateResult.model_fields["span"].annotation is Span
    assert set(BodyRef.model_fields) == {"id", "kind", "span", "callee"}
    assert BodyRef.model_fields["span"].annotation == (Span | None)
    assert "node" not in LocateResult.model_fields


def test_the_commons_span_accepts_either_analyzers_span_unchanged():
    """One span type on the surface, populated from either analyzer: ``codeanalyzer-python``'s
    ``Span`` and ``cldk.models.typescript``'s ``TSSpan`` both carry ``start``/``end``/``bytes``
    and validate into it by attribute, so neither backend converts by hand."""
    from cldk.analysis.commons.results import BodyRef, Span
    from cldk.models.python import Span as PySpan
    from cldk.models.typescript import TSSpan

    for foreign in (PySpan(start=(3, 4), end=(5, 6), bytes=(7, 8)), TSSpan(start=(3, 4), end=(5, 6), bytes=(7, 8))):
        ref = BodyRef(id="x", kind="statement", span=foreign, callee=None)
        assert isinstance(ref.span, Span)
        assert (ref.span.start, ref.span.end, ref.span.bytes) == ((3, 4), (5, 6), (7, 8))


def test_python_still_re_exports_its_own_analyzers_span_object():
    """``cldk.models.python`` is codeanalyzer-python's schema, re-exported — so ``Span`` there stays
    the class every ``Py*`` model's ``span`` field is annotated on. A commons class put in its place
    would make ``BodyNode(span=Span(...))`` a ValidationError, which is why ``Span`` is *added* to
    commons rather than moved out of the Python schema."""
    import codeanalyzer.schema.py_schema as py_schema

    from cldk.models.python import BodyNode, Span

    assert Span is py_schema.Span
    assert BodyNode.model_fields["span"].annotation is not None
    assert BodyNode(kind="statement", span=Span(start=(1, 0), end=(1, 0), bytes=(0, 0))).span.start == (1, 0)


# ---- reaches(x, x) is the cycle question, in every language -------------------------------------
def _cycle_graph():
    import networkx as nx

    g = nx.DiGraph()
    g.add_edge("selfcaller", "selfcaller")  # direct recursion
    g.add_edge("ping", "pong")
    g.add_edge("pong", "ping")  # mutual recursion, two hops
    g.add_edge("top", "leaf")  # no cycle at all
    return g


def test_call_reaches_answers_the_self_question_as_the_cycle_question():
    """``reaches(x, x)`` is documented in three facades as "``True`` only through a real cycle",
    and both Neo4j backends answer it that way -- their pattern is ``{1,depth}``, which lands back
    on the source like any other node. The in-memory backends asked ``b in nx.descendants(graph, a)``
    instead, and *descendants excludes the source*, self-loop or not: the answer was ``False`` for
    every input, including a directly recursive callable.

    That made the advice on the self-path refusal wrong too --
    :func:`~cldk.analysis.commons.bounds.check_distinct_endpoints` says "ask ``reaches(X, X)``
    whether a cycle exists", and it could not answer yes.
    """
    from cldk.analysis.commons.graphs import call_reaches

    g = _cycle_graph()
    assert call_reaches(g, "selfcaller", "selfcaller", None) is True
    assert call_reaches(g, "selfcaller", "selfcaller", 1) is True
    assert call_reaches(g, "ping", "ping", None) is True
    assert call_reaches(g, "ping", "ping", 2) is True
    assert call_reaches(g, "ping", "ping", 1) is False, "the cycle is two hops; a one-hop budget must say no"
    assert call_reaches(g, "top", "top", None) is False, "no path is still no path -- never vacuously true"
    assert call_reaches(g, "leaf", "leaf", None) is False


def test_call_reaches_is_unchanged_for_two_different_endpoints():
    from cldk.analysis.commons.graphs import call_reaches

    g = _cycle_graph()
    assert call_reaches(g, "top", "leaf", None) is True and call_reaches(g, "leaf", "top", None) is False
    assert call_reaches(g, "top", "leaf", 1) is True and call_reaches(g, "top", "leaf", 0) is False
    assert call_reaches(g, "top", "missing", None) is False and call_reaches(g, "missing", "top", None) is False


@pytest.mark.parametrize(
    "module, owner",
    [
        ("cldk.analysis.java.backend", "JavaAnalysisBackend"),
        ("cldk.analysis.python.codeanalyzer.codeanalyzer", "PyCodeanalyzer"),
        ("cldk.analysis.typescript.codeanalyzer.codeanalyzer", "TSCodeanalyzer"),
    ],
)
def test_every_in_memory_reaches_routes_through_the_shared_rule(module, owner):
    """One rule, not three copies of it -- the three had the identical wrong line."""
    import inspect

    source = inspect.getsource(getattr(importlib.import_module(module), owner).reaches)
    assert "call_reaches(" in source, f"{owner}.reaches does not use the shared rule"
    assert "nx.descendants" not in source, f"{owner}.reaches still asks descendants, which excludes the source"


# ----------------------------------------------------------------------------------------------
# Leg 4a, Task 1: the SDG path Cypher's two shared fragments.
#
# The three Neo4j backends each carried an identical *expression* computing a per-language *value*:
# ``_VIA_CASE`` and ``_PATH_ORDER`` are built from that backend's ``VIA`` table, so the strings
# differ (``J_DDG`` against ``PY_DDG``) while the code producing them did not. That is what makes
# them liftable as functions of ``P`` rather than as constants.
# ----------------------------------------------------------------------------------------------


def _path_backends():
    """The three backends and their relationship-type prefixes, imported lazily like every other
    backend reference in this file so a missing install extra cannot fail collection."""
    from cldk.analysis.java.neo4j.neo4j_backend import JNeo4jBackend
    from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend
    from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend

    return [("PY", PyNeo4jBackend), ("J", JNeo4jBackend), ("TS", TSNeo4jBackend)]


def test_the_three_constants_differ_only_in_the_relationship_prefix():
    """What is and is not shared, stated exactly.

    The values are **not** interchangeable -- three distinct strings, because each names its own
    language's relationship types. What was duplicated is the expression, and the only difference
    between the results is the prefix, which is why one function of ``P`` replaces three constants.
    A future divergence beyond the prefix would fail here rather than being absorbed silently.
    """
    from cldk.analysis.commons.graphs import path_order, via_case

    assert len({via_case(P) for P in ("PY", "J", "TS")}) == 3
    assert len({path_order(P) for P in ("PY", "J", "TS")}) == 3
    for P in ("J", "TS"):
        assert via_case(P).replace(f"{P}_", "PY_") == via_case("PY")
        assert path_order(P).replace(f"{P}_", "PY_") == path_order("PY")


# ----------------------------------------------------------------------------------------------
# Leg 4a, Task 2: the whole path statement.
#
# Five things differ between the three backends' ``_PATHS``, and all five are parameters: the node
# label, the endpoint scope, the interior scope, the node projection, and -- found while writing
# this -- the relationship variable, which Java spells ``e`` where the other two spell ``r``. That
# last one is semantically inert; it is a parameter so this lift can be byte-identical rather than a
# judgement call. Normalising it is a separate, arguable change.
#
# The arguments themselves live at each backend's own ``sdg_path_query(...)`` call site, not here --
# these tests judge ``backend._PATHS``, the statement that ships, rather than a reconstruction of it
# from a second, hand-kept copy of its arguments.
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("P", ["PY", "J", "TS"])
def test_the_generated_statement_still_formats(P):
    """The result is a ``.format()`` template, not a finished statement -- the runners supply ``rels``
    and ``depth``.

    What formatting must leave behind is *single* braces: Cypher map literals need them, so
    ``{{id:$src}}`` becoming ``{id:$src}`` is the point. What it must **not** leave is a doubled
    brace, which would mean an escape the template never resolved and a statement the server would
    reject.
    """
    from cldk.analysis.commons.graphs import sdg_rel_pattern

    backend = dict(_path_backends())[P]
    out = backend._PATHS.format(rels=sdg_rel_pattern(P), depth="")
    assert "{{" not in out and "}}" not in out, "an escape survived formatting"
    assert "{id:$src}" in out and "{id:$dst}" in out
    assert out.count("allShortestPaths") == 1


def test_the_scope_predicates_are_written_where_the_backend_writes_them():
    """Not a restatement of the digest below: it pins *which* backend scopes what, so a future
    edit that moved Python onto the prefix predicate would fail here with a reason rather than
    silently changing a statement whose omission was measured and is sanctioned (see
    tests/analysis/python/test_neo4j_multi_application_scope.py -- id-keying is a scope kind).
    """
    backends = dict(_path_backends())
    py, j, ts = (backends[P]._PATHS for P in ("PY", "J", "TS"))
    assert "STARTS WITH" not in py, "Python's path statement is scoped by id, deliberately"
    assert j.count("STARTS WITH $prefix") == 3, "Java scopes both endpoints and the interior"
    assert ts.count("STARTS WITH $p") == 1, "TypeScript scopes the interior only"


# ----------------------------------------------------------------------------------------------
# Leg 4a, Task 3: the byte-identity guard expires the moment ``_PATHS`` becomes the call it used to
# be compared against.
#
# Once each backend's ``_PATHS`` became a call to ``sdg_path_query(...)`` with its own arguments,
# comparing that call's result against itself proved nothing: a change to ``sdg_path_query``,
# ``path_order`` or ``via_case`` moves both sides together and passes silently. The digest below is
# what still fails when the statement itself changes.
# ----------------------------------------------------------------------------------------------

#: A digest of each backend's `_PATHS`, pinned so that a change to the shared generator, to
#: `path_order`/`via_case`, or to a backend's own arguments cannot pass unnoticed.
#:
#: This replaces the byte-identity comparison leg 4a retired. While `_PATHS` was a hand-written
#: literal, comparing it against `sdg_path_query()` proved the generator reproduced it -- the two
#: sides were independent. Once `_PATHS` became that call itself, both sides of that equality moved
#: together, so only a digest still fails when the statement itself changes.
#:
#: **When this fails:** the statement changed. Print `backend._PATHS` and diff it against the
#: previous value to see how, decide whether the change was intended, and if it was, update the
#: digest **in the same commit that changed the statement** -- never in a separate one, or the two
#: stop being reviewable together.
PATHS_DIGESTS = {"PY": "c1ea290360d42460", "J": "236a302937bcd98a", "TS": "a710986b595dc4df"}


@pytest.mark.parametrize("P", ["PY", "J", "TS"])
def test_the_generated_statement_has_not_drifted(P):
    backend = dict(_path_backends())[P]
    assert hashlib.sha256(backend._PATHS.encode()).hexdigest()[:16] == PATHS_DIGESTS[P], backend._PATHS


# ----------------------------------------------------------------------------------------------
# Leg 4b, Task 3: sdg_taint_query() -- the multi-source, multi-sink statement behind taint().
# ----------------------------------------------------------------------------------------------


def test_the_taint_query_is_null_safe_and_caps_per_pair():
    """Three properties, each of which has a specific failure mode if absent."""
    from cldk.analysis.commons.graphs import sdg_taint_query

    q = sdg_taint_query("PY", node_label="PyBodyNode", projection="ref: n.id")
    assert "coalesce(r.var, '')" in q, "a bare r.var <> $v refutes every interprocedural flow"
    assert "collect(p)[0..$cap]" in q, "a flat LIMIT lets one prolific pair starve the rest"
    assert "a.id IN $srcs" in q and "b.id IN $dsts" in q, "taint is m x n in one statement"
    assert "allShortestPaths" in q, "a variable-length pattern enumerates trails and will not finish"
    assert "b <> a" in q, "Neo4j aborts the whole batch when one requested pair is source == sink"


def test_the_taint_query_groups_by_pair():
    """The result must say which source reached which sink; a caller cannot recover it otherwise."""
    from cldk.analysis.commons.graphs import sdg_taint_query

    q = sdg_taint_query("PY", node_label="PyBodyNode", projection="ref: n.id")
    assert "a.id AS src" in q and "b.id AS dst" in q


def test_the_taint_query_uses_callables_not_string_replace():
    """The scope injection points are callables, mirroring ``sdg_path_query`` -- a textual
    ``.replace('n.', 'a.')`` would corrupt any fragment containing ``fn.`` (-> ``fa.``)."""
    from cldk.analysis.commons.graphs import sdg_taint_query

    q = sdg_taint_query(
        "J",
        node_label="JBodyNode",
        endpoint_scope=lambda v: f"{v}.id STARTS WITH $prefix",
        interior_scope=lambda v: f"{v}.id STARTS WITH $prefix",
        projection="ref: n.id",
        rel_var="e",
    )
    assert q.count("STARTS WITH $prefix") == 3
    assert "AND a.id STARTS WITH $prefix" in q
    assert "AND b.id STARTS WITH $prefix" in q


def test_the_taint_query_delimits_the_callable_cut_rather_than_bare_prefixing_it():
    """The Cypher half of :func:`~cldk.analysis.commons.graphs.under_callable`, asserted here so the
    two halves cannot drift: a TypeScript callable id has no closing delimiter, so a bare
    ``n.id STARTS WITH q`` cuts ``createGuest`` when the caller named ``create`` -- 13 real ids in
    the committed level-4 TypeScript fixture. Over-cutting adds pairs to ``exhausted``, which
    certifies that no flow exists, so it is a false refutation and not a conservative default."""
    from cldk.analysis.commons.graphs import sdg_taint_query

    q = sdg_taint_query("PY", node_label="PyBodyNode", projection="ref: n.id")
    assert "n.id STARTS WITH q)" not in q, "a bare prefix test over-cuts every sibling callable"
    for disjunct in ("n.id = q", "n.id STARTS WITH q + '@'", "n.id STARTS WITH q + '/'"):
        assert disjunct in q, f"the callable cut lost its {disjunct!r} disjunct"
    for disjunct in ("startNode(r).id = c.prefix", "startNode(r).id STARTS WITH c.prefix + '@'", "startNode(r).id STARTS WITH c.prefix + '/'"):
        assert disjunct in q, f"the variable cut's scope lost its {disjunct!r} disjunct"
    assert "STARTS WITH c.prefix)" not in q, "the variable cut's scope is still a bare prefix test"
    # The same asymmetry, reached through three-valued logic rather than through a prefix. ``var`` is
    # null on every ``CDG``/``SUMMARY`` edge and on every param crossing emitted before the analyzers'
    # ``var``-on-param-edge fix, and ``NULL = c.var`` is NULL, so ``NOT any(...)`` is NULL and the
    # whole ``all()`` drops the path -- an over-cut, in the false-refutation direction, on exactly the
    # edges an interprocedural flow has to use. Asserted by name because deleting the ``coalesce``
    # leaves the offline semantics suite green: only a live graph carries the nulls.
    assert "coalesce(r.var, '')" in q, "the variable cut lost its coalesce; a null var would over-cut"


def test_the_taint_query_still_formats():
    from cldk.analysis.commons.graphs import sdg_rel_pattern, sdg_taint_query

    q = sdg_taint_query("PY", node_label="PyBodyNode", projection="ref: n.id")
    out = q.format(rels=sdg_rel_pattern("PY"), depth="")
    assert "{{" not in out and "}}" not in out, "an escape survived formatting"
    assert out.count("allShortestPaths") == 1


# ----------------------------------------------------------------------------------------------
# Leg 4b, Task 7: the three ``_TAINT`` statements, now that all three exist.
# ----------------------------------------------------------------------------------------------

#: A digest of each graph backend's `_TAINT`, for `PATHS_DIGESTS`' reason: each is a call to
#: `sdg_taint_query(...)` with its own arguments, so a change to the generator, to `path_order`, or
#: to one backend's node label or scope callables moves every side that could be compared against it.
#:
#: **When this fails:** the statement changed. Print `backend._TAINT` and diff it against the
#: previous value, decide whether the change was intended, and if it was, update the digest **in the
#: same commit that changed the statement** -- never in a separate one.
TAINT_DIGESTS = {"PY": "7187b2a862485643", "J": "f21a4e01ddc3e8a9", "TS": "eb9ac5abbd76fd14"}


@pytest.mark.parametrize("P", ["PY", "J", "TS"])
def test_the_generated_taint_statement_has_not_drifted(P):
    backend = dict(_path_backends())[P]
    assert hashlib.sha256(backend._TAINT.encode()).hexdigest()[:16] == TAINT_DIGESTS[P], backend._TAINT
