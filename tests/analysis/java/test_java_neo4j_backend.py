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

r"""Live parity: the read-only Java Neo4j backend against the ``analysis.json`` backend.

Every accessor that exists after leg-3a Task 2 is compared on the **same application**, and the
graph is expected to hold **more than one** — the leg-3 reference database holds daytrader8 and
ThingsBoard side by side, so each count matching the daytrader8-only ``analysis.json`` *is* the
live half of the multi-application scope audit (its offline half is
``test_java_neo4j_multi_application_scope.py``).

Skipped unless pointed at an already-populated graph and a matching reference cache::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7691 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=... \
    CLDK_TEST_NEO4J_JAVA_APP=daytrader8 \      # the --app-name the graph was emitted with
    CLDK_TEST_JAVA_PROJECT=/path/to/project \  # the reference project dir
    CLDK_TEST_JAVA_CACHE=/path/to/dir \        # dir holding a level-4 reference analysis.json
    uv run pytest tests/analysis/java/test_java_neo4j_backend.py

Populate the graph with ``codeanalyzer-java -i <project> --emit neo4j --neo4j-uri … --app-name
<app>`` and the reference with ``codeanalyzer-java -i <project> -a 4 --app-name <app> -o <cache>``.
``--emit neo4j`` always forces level 4, so the reference is read at level 4 too; a cache computed
lower is skipped rather than silently re-analysed.

**The tolerances, each with its cause** (nothing here is a tolerance because it was inconvenient;
see :mod:`cldk.analysis.java.neo4j.reconstruct` for the projection's side of each):

* ``JCompilationUnit.source`` is ``""`` — a module's text is not projected at all.
* ``JCallable.code`` is the whole **declaration**, the local backend's is the **body block**: the
  projection carries one line range per callable, the declaration's, and no ``body_span``. The
  relation is exact and total, so it is asserted rather than skipped:
  ``neo.code.endswith(ref.code)``. ``code_start_line`` therefore agrees except where the opening
  brace sits below the declaration's first line, and ``calling_lines`` — line offsets *into*
  ``code`` — shift by the same prefix.
* Order within one source line is not recoverable: a field, a local variable and a call site carry
  a line but no column in the graph, so a declaration's ``field_declarations`` and
  ``local_variables`` are compared as multisets. Annotation order is not recoverable at all
  (``J_ANNOTATED_BY`` carries only ``arguments``), so ``annotations`` is compared as a multiset.
* ``imports`` are aggregated per import target, so a file's import order is not recoverable; the
  set is.
* ``get_all_docstrings`` reports the javadoc of each file's *declarations*; the local backend
  reports the compilation unit's own comment list, which holds every file-level javadoc instead.
  The two are compared against each other's actual contents, not assumed equal.
* ``get_all_comments`` / ``get_comment_in_file`` raise on the graph (no comment nodes at all).
* ``param_in`` / ``param_out``, ``cfg`` / ``cdg`` / ``ddg`` / ``summary``, ``type_parameters`` and
  the non-``call`` body nodes are not rebuilt in 3a.
"""

import json
import logging
import os
from pathlib import Path

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
JAVA_APP = os.environ.get("CLDK_TEST_NEO4J_JAVA_APP")
JAVA_PROJECT = os.environ.get("CLDK_TEST_JAVA_PROJECT")
JAVA_CACHE = os.environ.get("CLDK_TEST_JAVA_CACHE")

#: The reference is read at the level ``--emit neo4j`` forces, so the two see the same overlays.
REFERENCE_LEVEL = "system_dependency_graph"
REFERENCE_MAX_LEVEL = 4


def _reference_cache_is_level_4() -> bool:
    if not JAVA_CACHE:
        return False
    path = Path(JAVA_CACHE) / "analysis.json"
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("max_level", 0)) >= REFERENCE_MAX_LEVEL
    except (OSError, ValueError, AttributeError):
        return False


def _neo4j_reachable() -> bool:
    if not (JAVA_APP and JAVA_PROJECT and _reference_cache_is_level_4()):
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
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_reachable(),
    reason="needs a pre-populated Neo4j Java graph + a level-4 reference cache (set CLDK_TEST_NEO4J_* / CLDK_TEST_JAVA_*)",
)


@pytest.fixture(scope="module")
def backends():
    from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
    from cldk.analysis.java.neo4j import JNeo4jBackend

    ref = JCodeanalyzer(project_dir=JAVA_PROJECT, analysis_json_path=JAVA_CACHE, analysis_level=REFERENCE_LEVEL, eager_analysis=False, target_files=None)
    neo = JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=JAVA_APP)
    yield ref, neo
    neo.close()


def test_the_graph_holds_more_than_one_application(backends):
    """The premise of every count below: a leak would show up as a *larger* answer, so a
    single-application database would make this suite prove nothing about scoping."""
    _, neo = backends
    others = neo._run("MATCH (a:JApplication) WHERE a.name <> $app RETURN a.name AS name", app=JAVA_APP)
    assert others, "the reference graph holds only one application; the scope audit's live half needs at least two"
    assert neo._analyzer_version >= (3, 0, 1)


def test_attached_to_the_3_0_1_vocabulary(backends):
    """The v1 labels still exist as index definitions on this database; none of them holds a node,
    which is why reading them would have been a silent empty rather than an error."""
    _, neo = backends
    rows = neo._run("MATCH (n) WHERE n:JCompilationUnit OR n:JCallSite OR n:JParameter OR n:JComment OR n:JCrudOperation OR n:JCrudQuery RETURN count(n) AS n")
    assert rows[0]["n"] == 0


def test_symbol_table_parity(backends):
    ref, neo = backends
    st_r, st_n = ref.get_symbol_table(), neo.get_symbol_table()
    assert sorted(st_r) == sorted(st_n)
    for key, unit in st_r.items():
        other = st_n[key]
        assert (other.file_path, other.package, other.content_hash) == (unit.file_path, unit.package, unit.content_hash)
        assert sorted(other.imports) == sorted(unit.imports), f"{key}: import set differs"
        assert other.source == "" and other.comments == []  # documented: not projected


def test_application_view_parity(backends):
    ref, neo = backends
    assert neo.application.id == ref.application.id
    assert neo.application.external_symbols == ref.application.external_symbols
    wire = lambda app: sorted((e.src, e.dst, tuple(e.prov), e.weight) for e in app.call_graph)
    assert wire(neo.application) == wire(ref.application), "the wire call graph is not byte-equal"
    assert len(neo.application.artifacts) == len(ref.application.artifacts)
    assert (neo.application.param_in, neo.application.param_out) == ([], [])  # documented: not rebuilt in 3a


def _annotations(node):
    return sorted(node.annotations)


def test_type_parity(backends):
    """Every ``JType``, field by field, after the documented normalisation."""
    ref, neo = backends
    cr, cn = ref.get_all_classes(), neo.get_all_classes()
    assert sorted(cr) == sorted(cn), "the class key set differs (nested and local classes included)"
    for name, t in cr.items():
        o = cn[name]
        assert (o.id, o.kind, o.modifiers, o.base_types, o.interfaces) == (t.id, t.kind, t.modifiers, t.base_types, t.interfaces), name
        assert (o.is_entrypoint_class, o.qualified_name, o.parent_type, o.name) == (t.is_entrypoint_class, t.qualified_name, t.parent_type, t.name), name
        assert (o.start_line, o.end_line) == (t.start_line, t.end_line), name
        assert _annotations(o) == _annotations(t), name
        assert sorted(o.fields) == sorted(t.fields) and sorted(o.callables) == sorted(t.callables) and sorted(o.types) == sorted(t.types), name
        assert sorted(o.nested_type_declarations) == sorted(t.nested_type_declarations), name
        assert [c.content for c in o.comments] == [c.content for c in t.comments if c.is_javadoc], name


def test_field_parity(backends):
    ref, neo = backends
    for name in ref.get_all_classes():
        # Fields on the same source line have no recoverable order (no column in the graph).
        fr = sorted(ref.get_all_fields(name), key=lambda f: f.name)
        fn = sorted(neo.get_all_fields(name), key=lambda f: f.name)
        assert [f.name for f in fr] == [f.name for f in fn], name
        for a, b in zip(fr, fn):
            assert (b.id, b.type, b.modifiers, b.initializer, b.start_line, b.end_line) == (a.id, a.type, a.modifiers, a.initializer, a.start_line, a.end_line), f"{name}.{a.name}"
            assert _annotations(b) == _annotations(a), f"{name}.{a.name}"
            assert b.variables == a.variables and b.variable_initializers == a.variable_initializers


def test_callable_parity(backends):
    """Every ``JCallable`` of every class: the scalars exactly, the two order-lossy lists as
    multisets, and ``code`` through the declaration-prefix relation."""
    ref, neo = backends
    cr, cn = ref.get_all_classes(), neo.get_all_classes()
    for name, t in cr.items():
        assert sorted(t.callables) == sorted(cn[name].callables), name
        for sig, m in t.callables.items():
            o, where = cn[name].callables[sig], f"{name}::{sig}"
            assert (o.id, o.kind, o.signature, o.declaration, o.return_type, o.modifiers) == (m.id, m.kind, m.signature, m.declaration, m.return_type, m.modifiers), where
            assert (o.is_implicit, o.is_entrypoint, o.is_constructor, o.is_static) == (m.is_implicit, m.is_entrypoint, m.is_constructor, m.is_static), where
            assert (o.start_line, o.end_line) == (m.start_line, m.end_line), where
            assert o.thrown_exceptions == m.thrown_exceptions and o.cyclomatic_complexity == m.cyclomatic_complexity, where
            assert sorted(o.referenced_types) == sorted(m.referenced_types) and sorted(o.accessed_fields) == sorted(m.accessed_fields), where
            assert (o.refs is None) == (m.refs is None), where
            assert _annotations(o) == _annotations(m), where
            assert [c.content for c in o.comments] == [c.content for c in m.comments if c.is_javadoc], where
            assert o.crud_operations == [] and o.crud_queries == []
            # Local variables: same multiset; within-line order is not recoverable.
            key = lambda v: (v.name, v.type, v.initializer, v.start_line)
            assert sorted(map(key, o.variable_declarations)) == sorted(map(key, m.variable_declarations)), where
            # ``code``: identical without a body, otherwise the body block behind its declaration.
            if m.code:
                assert o.code.endswith(m.code), where
            else:
                assert o.code == "", where


def test_parameter_parity(backends):
    """``parameters_json`` is the analyzer's own serialisation, so parameters round-trip exactly —
    names, types, modifiers, annotations, variadic flag and spans."""
    ref, neo = backends
    shape = lambda p: (p.name, p.type, p.is_variadic, p.modifiers, p.annotations, p.start_line, p.start_column, p.end_line, p.end_column)
    seen = 0
    for name, t in ref.get_all_classes().items():
        for sig in t.callables:
            a = [shape(p) for p in ref.get_method_parameters(name, sig)]
            assert a == [shape(p) for p in neo.get_method_parameters(name, sig)], f"{name}::{sig}"
            seen += len(a)
    assert seen > 0


def test_call_site_parity(backends):
    """The ``call`` body nodes, resolved over ``J_RESOLVES_TO``, reproduce the whole 1.x call-site
    view except what the projection drops: ``arguments`` (body-key references), the comment, and
    **both columns** -- a body node carries only its lines, and the ``L:C`` its id ends with is a
    key spelling a different position (see :func:`reconstruct.body_node`), so the columns come back
    as the model's own ``-1`` rather than as a plausible wrong number."""
    ref, neo = backends
    shape = lambda s: (
        s.method_name,
        s.callee_signature,
        s.receiver_type,
        s.receiver_expr,
        s.return_type,
        s.start_line,
        s.is_static_call,
        s.is_constructor_call,
        (s.is_public, s.is_private, s.is_protected, s.is_unspecified),
        tuple(s.argument_types),
        tuple(s.argument_expr),
    )
    cr, cn = ref.get_all_classes(), neo.get_all_classes()
    total = 0
    for name, t in cr.items():
        for sig, m in t.callables.items():
            a = sorted(shape(s) for s in m.call_sites)
            sites = cn[name].callables[sig].call_sites
            assert a == sorted(shape(s) for s in sites), f"{name}::{sig}"
            assert all((s.start_column, s.end_column) == (-1, -1) for s in sites), f"{name}::{sig}"
            total += len(a)
    assert total > 0


def test_methods_constructors_hierarchy_and_lookups_parity(backends):
    ref, neo = backends
    for name in ref.get_all_classes():
        assert sorted(ref.get_all_methods_in_class(name)) == sorted(neo.get_all_methods_in_class(name)), name
        assert sorted(ref.get_all_constructors(name)) == sorted(neo.get_all_constructors(name)), name
        assert ref.get_extended_classes(name) == neo.get_extended_classes(name), name
        assert ref.get_implemented_interfaces(name) == neo.get_implemented_interfaces(name), name
        assert sorted(ref.get_all_sub_classes(name)) == sorted(neo.get_all_sub_classes(name)), name
        assert sorted(t.qualified_name for t in ref.get_all_nested_classes(name)) == sorted(t.qualified_name for t in neo.get_all_nested_classes(name)), name
        assert ref.get_java_file(name) == neo.get_java_file(name), name
    mr, mn = ref.get_all_methods_in_application(), neo.get_all_methods_in_application()
    assert sorted(mr) == sorted(mn) and all(sorted(mr[k]) == sorted(mn[k]) for k in mr)
    assert sorted(u.file_path for u in ref.get_compilation_units()) == sorted(u.file_path for u in neo.get_compilation_units())


def test_call_graph_parity(backends):
    """The node set, the edge set and every edge's ``type``/``weight``. ``calling_lines`` are line
    offsets into ``JCallable.code``, which differs by the declaration prefix, so they are compared
    only where the two ``code`` values agree."""
    ref, neo = backends
    gr, gn = ref.get_call_graph(), neo.get_call_graph()
    assert set(gr.nodes) == set(gn.nodes), "call-graph node set differs"
    assert set(gr.edges) == set(gn.edges), "call-graph edge set differs"
    assert all("can://" not in n for n in gn.nodes) and all(gn.nodes[n]["kind"] == "callable" for n in gn.nodes)
    for u, v in gr.edges:
        assert (gn[u][v]["type"], gn[u][v]["weight"]) == (gr[u][v]["type"], gr[u][v]["weight"]), f"{u} -> {v}"
        assert gn.nodes[u]["method_detail"].klass == gr.nodes[u]["method_detail"].klass
        if gn.nodes[u]["method_detail"].method.code == gr.nodes[u]["method_detail"].method.code:
            assert gn[u][v]["calling_lines"] == gr[u][v]["calling_lines"], f"{u} -> {v}"
    # Only :JCallable -> :JCallable edges are in the graph: the projection forces external calls,
    # and 3a keeps the 1.x callable-only shape.
    _, neo_only = ref, neo
    forced = neo_only._run(
        "MATCH (s:JCallable)-[r:J_CALLS]->(t) WHERE s.id STARTS WITH $prefix RETURN count(r) AS all, count(CASE WHEN t:JExternal THEN 1 END) AS external",
        prefix=neo_only._scope_prefix,
    )
    assert forced[0]["external"] > 0, "the graph carries no external call edges; nothing was being dropped"
    assert forced[0]["all"] - forced[0]["external"] == gn.number_of_edges()


def test_call_graph_json_and_sdg_parity(backends):
    ref, neo = backends
    key = lambda rows: sorted((r["source_class"], r["source_method_signature"], r["target_class"], r["target_method_signature"]) for r in rows)
    assert key(json.loads(ref.get_call_graph_json())) == key(json.loads(neo.get_call_graph_json()))
    assert len(neo.get_system_dependency_graph()) == len(ref.get_system_dependency_graph())


def test_callers_and_callees_parity(backends):
    """Both directions, both modes, on every callable of one heavily-connected class."""
    ref, neo = backends
    klass = max(ref.get_all_classes(), key=lambda n: len(ref.get_all_classes()[n].callables))
    for sig in ref.get_all_classes()[klass].callables:
        for using_symbol_table in (False, True):
            a = ref.get_all_callers(klass, sig, using_symbol_table)
            b = neo.get_all_callers(klass, sig, using_symbol_table)
            assert sorted(d["caller_method"].klass + "." + d["caller_method"].method.signature for d in a.get("caller_details", [])) == sorted(
                d["caller_method"].klass + "." + d["caller_method"].method.signature for d in b.get("caller_details", [])
            ), f"callers of {klass}::{sig} (symbol table: {using_symbol_table})"
            a = ref.get_all_callees(klass, sig, using_symbol_table)
            b = neo.get_all_callees(klass, sig, using_symbol_table)
            assert sorted(d["callee_method"].klass + "." + d["callee_method"].method.signature for d in a.get("callee_details", [])) == sorted(
                d["callee_method"].klass + "." + d["callee_method"].method.signature for d in b.get("callee_details", [])
            ), f"callees of {klass}::{sig} (symbol table: {using_symbol_table})"


def test_class_call_graph_parity(backends):
    ref, neo = backends
    edges = lambda pairs: sorted((s.klass, s.method.signature, t.klass, t.method.signature) for s, t in pairs)
    for name in sorted(ref.get_all_classes())[:40]:
        assert edges(ref.get_class_call_graph(name)) == edges(neo.get_class_call_graph(name)), name
        assert edges(ref.get_class_call_graph_using_symbol_table(name)) == edges(neo.get_class_call_graph_using_symbol_table(name)), name


def test_entrypoint_parity(backends):
    ref, neo = backends
    assert sorted(ref.get_all_entry_point_classes()) == sorted(neo.get_all_entry_point_classes())
    er, en = ref.get_all_entry_point_methods(), neo.get_all_entry_point_methods()
    assert sorted(er) == sorted(en) and all(sorted(er[k]) == sorted(en[k]) for k in er)


def test_artifact_layer_parity(backends):
    ref, neo = backends
    ar, an = ref.get_artifacts(), neo.get_artifacts()
    assert sorted(ar) == sorted(an)
    for path, artifact in ar.items():
        assert an[path].model_dump() == artifact.model_dump(), path
    assert [d.model_dump() for d in neo.get_dependencies()] == [d.model_dump() for d in ref.get_dependencies()]
    assert [d.name for d in neo.get_dependencies(direct_only=True)] == [d.name for d in ref.get_dependencies(direct_only=True)]
    assert [d.name for d in neo.get_dependencies(ecosystem="maven")] == [d.name for d in ref.get_dependencies(ecosystem="maven")]
    ck_r, ck_n = ref.get_config_keys(), neo.get_config_keys()
    assert sorted(ck_r) == sorted(ck_n) and all(ck_n[k].model_dump() == ck_r[k].model_dump() for k in ck_r)
    assert neo.get_config_uses() == [] == ref.get_config_uses()
    assert neo.get_unresolved_config_reads() == [] == ref.get_unresolved_config_reads()
    # The Java wire's own artifact models carry two fields the shared Py* ones have no home for.
    assert sorted(neo.application.artifacts) == sorted(ref.application.artifacts)
    assert {p: (a.text_truncated, a.sha256) for p, a in neo.application.artifacts.items()} == {p: (a.text_truncated, a.sha256) for p, a in ref.application.artifacts.items()}
    assert [d.group for d in neo.application.dependencies] == [d.group for d in ref.application.dependencies]


def test_docstring_parity_within_the_documented_lossiness(backends):
    """The graph's docstrings are exactly the reference's *declaration-level* javadoc, file by
    file — which is a different set from the reference's own ``get_all_docstrings`` (the
    compilation unit's comment list, holding the file-level javadoc instead)."""
    ref, neo = backends
    expected: dict[str, list[str]] = {}
    for name, t in ref.get_all_classes().items():
        path = ref.get_java_file(name)
        javadoc = [c.content for c in t.comments if c.is_javadoc]
        javadoc += [c.content for m in t.callables.values() for c in m.comments if c.is_javadoc]
        javadoc += [c.content for f in t.fields.values() for c in f.comments if c.is_javadoc]
        if javadoc:
            expected.setdefault(path, []).extend(javadoc)
    actual = {path: [c.content for c in comments] for path, comments in neo.get_all_docstrings().items()}
    assert {k: sorted(v) for k, v in actual.items()} == {k: sorted(v) for k, v in expected.items()}
    for name in ref.get_all_classes():
        assert [c.content for c in neo.get_comments_in_a_class(name)] == [c.content for c in ref.get_comments_in_a_class(name) if c.is_javadoc], name
        for sig in ref.get_all_classes()[name].callables:
            assert [c.content for c in neo.get_comments_in_a_method(name, sig)] == [c.content for c in ref.get_comments_in_a_method(name, sig) if c.is_javadoc], f"{name}::{sig}"


def test_the_accessors_the_projection_cannot_serve_raise(backends):
    from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

    ref, neo = backends
    for call in (lambda: neo.get_all_comments(), lambda: neo.get_comment_in_file(next(iter(ref.get_symbol_table())))):
        with pytest.raises(CodeanalyzerExecutionException, match="carries no comment nodes"):
            call()
    for name in ("get_all_crud_operations", "get_all_create_operations", "get_all_read_operations", "get_all_update_operations", "get_all_delete_operations"):
        with pytest.raises(CodeanalyzerExecutionException, match="codeanalyzer-java#187"):
            getattr(neo, name)()
    with pytest.raises(NotImplementedError):
        neo.remove_all_comments("class A {}")


def test_an_absent_application_is_refused_not_served_empty(backends):
    """The same database, a name it does not hold: refused at attach (J-9), because every statement
    would otherwise come back empty and read as "this application has no code"."""
    from cldk.analysis.java.neo4j import JNeo4jBackend
    from cldk.utils.exceptions import GraphSchemaMismatch

    with pytest.raises(GraphSchemaMismatch, match="has no :JApplication node"):
        JNeo4jBackend(neo4j_uri=NEO4J_URI, neo4j_username=NEO4J_USER, neo4j_password=NEO4J_PASSWORD, application_name=f"{JAVA_APP}-does-not-exist")
