"""End-to-end: run the real cango binary on a throwaway Go module. Skips cleanly when no binary is
resolvable (``$CODEANALYZER_GO_BIN`` or a ``codeanalyzer-go`` wheel), so CI without the analyzer is
unaffected."""

import os
import shlex
import shutil
from pathlib import Path

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig


def _binary() -> str | None:
    env = os.environ.get("CODEANALYZER_GO_BIN")
    if env:
        exe = shlex.split(env)[0]
        return env if Path(exe).exists() or shutil.which(exe) else None
    try:
        import codeanalyzer_go  # type: ignore[import-not-found]

        p = Path(codeanalyzer_go.bin_path())
        return str(p) if p.exists() else None
    except (ModuleNotFoundError, FileNotFoundError, AttributeError):
        return None


pytestmark = pytest.mark.skipif(_binary() is None, reason="no codeanalyzer-go (cango) binary resolvable")

_MAIN_GO = """package main

import "fmt"

type Greeter struct{ name string }

func (g Greeter) Hello() string { return fmt.Sprintf("hi %s", g.name) }

func main() { g := Greeter{"x"}; fmt.Println(g.Hello()) }
"""


@pytest.fixture(scope="module")
def go_module(tmp_path_factory):
    """A minimal real Go module (needs a go.mod — cango uses go/packages)."""
    d = tmp_path_factory.mktemp("go-e2e")
    (d / "main.go").write_text(_MAIN_GO, encoding="utf-8")
    (d / "go.mod").write_text("module goe2e\n\ngo 1.21\n", encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def analysis(go_module, tmp_path_factory):
    cache = tmp_path_factory.mktemp("go-e2e-cache")
    return CLDK.go(
        project_path=str(go_module),
        analysis_level=AnalysisLevel.call_graph,
        eager=True,
        backend=CodeAnalyzerConfig(cache_dir=str(cache)),
    )


def test_symbol_table_not_empty(analysis):
    assert analysis.get_symbol_table()


def test_types_and_functions(analysis):
    types = analysis.get_types()
    assert any(t.kind == "struct" for t in types.values()), "expected the Greeter struct"
    assert analysis.get_functions(), "expected the package-level main func"


def test_methods_and_source(analysis):
    tid = next(t_id for t_id, t in analysis.get_types().items() if t.kind == "struct")
    methods = analysis.get_methods_of(tid)
    assert methods, "the struct has a method"
    sig = next(iter(methods))
    assert "func" in analysis.get_source(analysis.get_method(tid, sig).id)


def test_call_graph_dangling_free(analysis):
    g = analysis.get_call_graph()
    for src, dst in g.edges():
        assert "id" in g.nodes[src] and "id" in g.nodes[dst]


# -----[ live run on the committed real application (litestream.zip) ]-----
import zipfile  # noqa: E402

_LITESTREAM_ZIP = Path(__file__).resolve().parents[2] / "resources" / "go" / "litestream.zip"


@pytest.fixture(scope="module")
def litestream_project(tmp_path_factory):
    """Unzip the committed litestream app; the archive roots at ``litestream/`` (carries go.mod)."""
    if not _LITESTREAM_ZIP.exists():
        pytest.skip("litestream.zip resource not present")
    dest = tmp_path_factory.mktemp("litestream-src")
    with zipfile.ZipFile(_LITESTREAM_ZIP) as zf:
        zf.extractall(dest)
    root = dest / "litestream"
    assert (root / "go.mod").exists(), "expected go.mod at the archive's litestream/ root"
    return root


@pytest.fixture(scope="module")
def litestream_analysis(litestream_project, tmp_path_factory):
    cache = tmp_path_factory.mktemp("litestream-cache")
    return CLDK.go(
        project_path=str(litestream_project),
        analysis_level=AnalysisLevel.call_graph,
        eager=True,
        backend=CodeAnalyzerConfig(cache_dir=str(cache)),
    )


def test_litestream_app_analyzes(litestream_analysis):
    """The real binary on the real app: a non-trivial symbol table and call graph, app id intact."""
    assert litestream_analysis.get_application_view().id == "can://litestream/litestream"
    assert len(litestream_analysis.get_symbol_table()) > 1
    g = litestream_analysis.get_call_graph()
    assert g.number_of_edges() > 0
    for src, dst in g.edges():
        assert "id" in g.nodes[src] and "id" in g.nodes[dst]
