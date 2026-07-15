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

"""Regression tests for #247: ``get_method``/``get_method_parameters`` must resolve module- and
namespace-level functions, not just class methods, on both the local and Neo4j backends.

The shared repo fixture (``typescript_analysis_json`` / a live Neo4j) is not always available in
every sandbox (see ``tests/analysis/typescript/conftest.py`` and the Neo4j skip-gate in
``test_typescript_neo4j_backend.py``), so this module builds its own minimal, self-contained
``TSApplication`` fixture directly from the pydantic models. The same underlying data seeds both
backends (the local one via a mocked subprocess, the Neo4j one via a stubbed ``_run``), so the
parity test below is comparing apples to apples.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.analysis.typescript.neo4j import TSNeo4jBackend
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableParameter,
    TSCallEdge,
    TSClass,
    TSModule,
    TSNamespace,
)

# -----[ shared fixture data ]-----
#
# src/mod.ts:
#   class Foo { bar() {} }                          -> src/mod.Foo.bar
#   function baz(x) { Foo.prototype.bar(); }         -> src/mod.baz   (module-level function)
#   namespace NS { function qux() { baz(); } }       -> src/mod.NS.qux (namespace-nested function)
#
# call_graph: baz -> Foo.bar, NS.qux -> baz   (so both functions participate in a call edge)


def _bar() -> TSCallable:
    return TSCallable(name="bar", path="src/mod.ts", signature="src/mod.Foo.bar", kind="method")


def _baz() -> TSCallable:
    return TSCallable(
        name="baz",
        path="src/mod.ts",
        signature="src/mod.baz",
        kind="function",
        parameters=[TSCallableParameter(name="x")],
    )


def _qux() -> TSCallable:
    return TSCallable(name="qux", path="src/mod.ts", signature="src/mod.NS.qux", kind="function")


def _build_application() -> TSApplication:
    foo = TSClass(name="Foo", signature="src/mod.Foo", methods={"bar": _bar()})
    ns = TSNamespace(name="NS", signature="src/mod.NS", functions={"src/mod.NS.qux": _qux()})
    module = TSModule(
        file_path="src/mod.ts",
        module_name="mod",
        classes={"src/mod.Foo": foo},
        functions={"src/mod.baz": _baz()},
        namespaces={"src/mod.NS": ns},
    )
    return TSApplication(
        symbol_table={"src/mod.ts": module},
        call_graph=[
            TSCallEdge(source="src/mod.baz", target="src/mod.Foo.bar"),
            TSCallEdge(source="src/mod.NS.qux", target="src/mod.baz"),
        ],
    )


# -----[ local (in-memory) backend ]-----


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


@pytest.fixture
def ts_analysis(typescript_application, tmp_path, monkeypatch):
    """A local-backend facade over the minimal module-function fixture above."""
    payload = _build_application().model_dump_json()
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(payload),
    ):
        return CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )


def test_local_get_method_still_resolves_class_methods(ts_analysis):
    method = ts_analysis.get_method("src/mod.Foo", "bar")
    assert method is not None
    assert method.signature == "src/mod.Foo.bar"


def test_local_get_method_resolves_module_level_function_by_short_name(ts_analysis):
    method = ts_analysis.get_method("src/mod", "baz")
    assert method is not None
    assert method.signature == "src/mod.baz"


def test_local_get_method_resolves_by_exact_signature(ts_analysis):
    # scope is irrelevant/ignored when the method arg is already a full signature
    method = ts_analysis.get_method("whatever", "src/mod.baz")
    assert method is not None
    assert method.signature == "src/mod.baz"


def test_local_get_method_resolves_namespace_nested_function_by_short_name(ts_analysis):
    # "qux" isn't reachable via the naive "src/mod.qux" composed guess -- only via a short-name
    # search scoped under "src/mod".
    method = ts_analysis.get_method("src/mod", "qux")
    assert method is not None
    assert method.signature == "src/mod.NS.qux"


def test_local_get_method_parameters_module_level_function(ts_analysis):
    assert ts_analysis.get_method_parameters("src/mod", "baz") == ["x"]


def test_local_get_method_genuine_miss_returns_none(ts_analysis):
    assert ts_analysis.get_method("src/mod", "does_not_exist") is None
    assert ts_analysis.get_method_parameters("src/mod", "does_not_exist") == []


def test_local_module_function_participates_in_call_edge(ts_analysis):
    # sanity check on the fixture itself: baz is a real call-graph participant
    graph = ts_analysis.get_call_graph()
    assert graph.has_edge("src/mod.baz", "src/mod.Foo.bar")


# -----[ Neo4j backend ]-----
#
# No live Neo4j is assumed to be reachable in every environment (see the skip-gate in
# test_typescript_neo4j_backend.py). ``_run`` is the single seam every query method goes through,
# so it's stubbed here with canned rows equivalent to the same fixture data above -- this is a
# unit-level substitute for the (separately maintained) live-Neo4j integration coverage.


def _neo4j_backend_with_stubbed_run(rows_by_call: dict) -> TSNeo4jBackend:
    """A TSNeo4jBackend with __init__ (and its real driver connection) bypassed, ``_run`` stubbed
    to return canned rows keyed by (query, frozenset(params.items()))."""
    backend = object.__new__(TSNeo4jBackend)
    backend.application_name = "test-app"
    backend._database = None
    backend._modules = ["src/mod.ts"]

    def _normalize(value):
        return tuple(value) if isinstance(value, list) else value

    def _run(query: str, **params):
        normalized = {k: _normalize(v) for k, v in params.items()}
        for (q, p), result in rows_by_call.items():
            if q == query and dict(p) == normalized:
                return result
        return []

    backend._run = _run
    return backend


def _callable_props(c: TSCallable) -> dict:
    """The flattened Neo4j node property shape ``reconstruct.callable_`` expects (see
    ``codeanalyzer-ts/src/build/neo4j/project.ts``): JSON-encoded ``*_json`` scalars rather than
    nested lists, matching what a real projection would have written."""
    import json as _json

    return {
        "name": c.name,
        "path": c.path,
        "signature": c.signature,
        "parameters_json": _json.dumps([p.model_dump() for p in c.parameters]),
        "return_type": c.return_type,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "kind": c.kind,
    }


@pytest.fixture
def stub_neo4j_backend():
    bar_props = _callable_props(_bar())
    baz_props = _callable_props(_baz())
    qux_props = _callable_props(_qux())

    has_method_query = "MATCH (o:CanNode {signature: $sig})-[:TS_HAS_METHOD]->(m:TSCallable {name: $name}) RETURN properties(m) AS p LIMIT 1"
    exact_sig_query = (
        "MATCH (parent)-[:TS_DECLARES]->(c:TSCallable {signature: $sig}) "
        "WHERE (parent:TSModule OR parent:TSNamespace) AND c._module IN $mods "
        "RETURN properties(c) AS p LIMIT 1"
    )
    short_name_query = (
        "MATCH (parent)-[:TS_DECLARES]->(c:TSCallable {name: $name}) "
        "WHERE (parent:TSModule OR parent:TSNamespace) AND c._module IN $mods AND c.signature STARTS WITH $prefix "
        "RETURN properties(c) AS p LIMIT 1"
    )

    rows_by_call = {
        # class method lookup: hits
        (has_method_query, (("sig", "src/mod.Foo"), ("name", "bar"))): [{"p": bar_props}],
        # class method lookup: misses for module/namespace scopes
        (has_method_query, (("sig", "src/mod"), ("name", "baz"))): [],
        (has_method_query, (("sig", "whatever"), ("name", "src/mod.baz"))): [],
        (has_method_query, (("sig", "src/mod"), ("name", "qux"))): [],
        (has_method_query, (("sig", "src/mod"), ("name", "does_not_exist"))): [],
        # exact-signature DECLARES fallback
        (exact_sig_query, (("mods", ("src/mod.ts",)), ("sig", "src/mod.baz"))): [{"p": baz_props}],
        (exact_sig_query, (("mods", ("src/mod.ts",)), ("sig", "baz"))): [],
        (exact_sig_query, (("mods", ("src/mod.ts",)), ("sig", "does_not_exist"))): [],
        (exact_sig_query, (("mods", ("src/mod.ts",)), ("sig", "qux"))): [],
        # short-name DECLARES fallback, scoped under the given scope
        (short_name_query, (("mods", ("src/mod.ts",)), ("name", "baz"), ("prefix", "src/mod."))): [{"p": baz_props}],
        (short_name_query, (("mods", ("src/mod.ts",)), ("name", "qux"), ("prefix", "src/mod."))): [{"p": qux_props}],
        (short_name_query, (("mods", ("src/mod.ts",)), ("name", "does_not_exist"), ("prefix", "src/mod."))): [],
    }
    return _neo4j_backend_with_stubbed_run(rows_by_call)


def test_neo4j_get_method_still_resolves_class_methods(stub_neo4j_backend):
    method = stub_neo4j_backend.get_method("src/mod.Foo", "bar")
    assert method is not None
    assert method.signature == "src/mod.Foo.bar"


def test_neo4j_get_method_resolves_module_level_function_by_short_name(stub_neo4j_backend):
    method = stub_neo4j_backend.get_method("src/mod", "baz")
    assert method is not None
    assert method.signature == "src/mod.baz"


def test_neo4j_get_method_resolves_by_exact_signature(stub_neo4j_backend):
    method = stub_neo4j_backend.get_method("whatever", "src/mod.baz")
    assert method is not None
    assert method.signature == "src/mod.baz"


def test_neo4j_get_method_resolves_namespace_nested_function_by_short_name(stub_neo4j_backend):
    method = stub_neo4j_backend.get_method("src/mod", "qux")
    assert method is not None
    assert method.signature == "src/mod.NS.qux"


def test_neo4j_get_method_parameters_module_level_function(stub_neo4j_backend):
    assert stub_neo4j_backend.get_method_parameters("src/mod", "baz") == ["x"]


def test_neo4j_get_method_genuine_miss_returns_none(stub_neo4j_backend):
    assert stub_neo4j_backend.get_method("src/mod", "does_not_exist") is None
    assert stub_neo4j_backend.get_method_parameters("src/mod", "does_not_exist") == []


# -----[ backend parity ]-----


def test_backend_parity_module_level_function(ts_analysis, stub_neo4j_backend):
    local = ts_analysis.get_method("src/mod", "baz")
    remote = stub_neo4j_backend.get_method("src/mod", "baz")
    assert local.signature == remote.signature
    assert local.name == remote.name
    assert ts_analysis.get_method_parameters("src/mod", "baz") == stub_neo4j_backend.get_method_parameters("src/mod", "baz")


def test_backend_parity_namespace_nested_function(ts_analysis, stub_neo4j_backend):
    local = ts_analysis.get_method("src/mod", "qux")
    remote = stub_neo4j_backend.get_method("src/mod", "qux")
    assert local.signature == remote.signature


def test_backend_parity_genuine_miss(ts_analysis, stub_neo4j_backend):
    assert ts_analysis.get_method("src/mod", "does_not_exist") is None
    assert stub_neo4j_backend.get_method("src/mod", "does_not_exist") is None
