# tests/analysis/commons/test_lifted_helpers.py
import importlib, pytest

LIFTED = {
    "cldk.analysis.commons.bounds": ["DEFAULT_PAGE_SIZE", "DEFAULT_DEPTH", "DEFAULT_MAX_NODES", "DEFAULT_MAX_PATHS",
        "check_depth", "check_max_nodes", "check_max_paths", "check_page_size", "check_distinct_endpoints",
        "reject_bare_string", "check_selector", "encode_cursor", "decode_cursor", "keyset_where", "cursor_params",
        "edge_page", "EdgeOrder"],
    "cldk.analysis.commons.graphs": ["bounded_subgraph", "hop_sort_key", "slice_resolved", "cone_sinks",
        "as_slice_node", "flow_path", "edge_sort_key", "sdg_rels", "sdg_rel_pattern", "via_table"],
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
