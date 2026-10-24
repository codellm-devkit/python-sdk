"""Go model tests — the ``extra="forbid"`` pydantic mirror of canonical schema v2.

Like the sibling ``tests/models/<lang>`` suites, the file corpus comes from ``tests/resources/go``
(no local toy JSON): the committed real ``can://go/litestream`` project (55 modules, 959 edges)
proves the mirror parses genuine cango output at scale. The remaining tests construct models inline
to pin the Go-specific shape — the single ``GoType`` with a ``kind`` field (no discriminated union),
the null-safe collection coercion for Go's nil-slice idiom, and the native first-class fields.
"""

from pathlib import Path

import pytest

from cldk.models.go import GoAnalysis, GoModule, GoType

#: the committed real corpus, via the parents[2]/"resources" convention the TS/Java tests use
GO_ANALYSIS = Path(__file__).resolve().parents[2] / "resources" / "go" / "analysis.json"

# real, stable ids from the litestream corpus (abs/replica_client.go)
LS_MODULE = "abs/replica_client.go"
LS_TYPE = "ReplicaClient"
LS_METHOD_SIG = "github.com/benbjohnson/litestream/abs.ReplicaClient.DeleteAll"
LS_FUNC_SIG = "github.com/benbjohnson/litestream/abs.NewReplicaClient"


# -----[ real corpus: the mirror parses genuine cango output at scale ]-----
@pytest.fixture(scope="module")
def litestream() -> GoAnalysis:
    return GoAnalysis.model_validate_json(GO_ANALYSIS.read_text(encoding="utf-8"))


def test_real_corpus_envelope(litestream):
    assert litestream.schema_version == "2.0.0"
    assert litestream.language == "go"
    assert litestream.max_level == 2
    assert litestream.application.id == "can://go/litestream"
    assert len(litestream.application.symbol_table) == 55
    assert len(litestream.application.call_graph) == 959


def test_real_corpus_struct_round_trips(litestream):
    """A real struct with its method and field survives the round trip with its native shape."""
    mod = litestream.application.symbol_table[LS_MODULE]
    assert mod.package == "abs" and mod.source  # the module carries its source text
    ty = mod.types[LS_TYPE]
    assert ty.kind == "struct"
    assert LS_METHOD_SIG in ty.callables
    assert ty.callables[LS_METHOD_SIG].kind == "method"
    assert ty.fields["AccountKey"].type == "string"
    assert LS_FUNC_SIG in mod.functions  # a package-level constructor function


def test_real_corpus_call_edges_are_well_formed(litestream):
    """Every edge names a src and dst id under the application; prov/weight parse."""
    pre = "can://go/litestream/"
    for e in litestream.application.call_graph:
        assert e.src.startswith(pre) and e.dst.startswith(pre)
        assert isinstance(e.prov, list) and isinstance(e.weight, int)


# -----[ the Go-specific model shape, pinned inline ]-----
def test_gotype_is_a_single_model_with_a_kind_field():
    t = GoType(id="can://go/x/f.go/T", kind="struct")
    assert t.kind == "struct"
    assert t.base_types == [] and t.callables == {} and t.fields == {}


def test_null_safe_base_coerces_null_collections():
    """A JSON ``null`` list/map (Go marshals a nil slice this way) becomes the empty default rather
    than raising, honoring the ``= []`` field style via the broadened validator."""
    t = GoType(id="x", kind="interface", base_types=None, callables=None, fields=None)
    assert t.base_types == [] and t.callables == {} and t.fields == {}
    m = GoModule(id="m", package="p", source="", imports=None, types=None, functions=None)
    assert m.imports == [] and m.types == {} and m.functions == {}


def test_go_native_fields_present():
    """The Go-specific first-class fields exist with their defaults."""
    from cldk.models.go import GoBodyNode, GoCallable, GoParam

    assert GoParam(name="x").is_variadic is False
    assert GoBodyNode(kind="call").is_goroutine is False
    c = GoCallable(id="c", signature="s")
    assert c.error_channel == [] and c.source_file is None
