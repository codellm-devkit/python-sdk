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
