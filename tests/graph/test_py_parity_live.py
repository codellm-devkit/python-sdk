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

"""Live dual-backend parity for the five verbs' data seam (env-gated, mirrors
tests/analysis/python/test_python_neo4j_backend.py). Writes the pyfix sources from the
py-a4.json fixture to a temp project, runs the real analyzer at level 4, emits to Neo4j
in-process from that SAME analysis, then asserts each provider primitive agrees modulo
documented lossiness (Neo4j source_slice code is None).

The whole module is skipped unless CLDK_TEST_NEO4J_URI is set. Point it at a server with:

    CLDK_TEST_NEO4J_URI=bolt://localhost:7687 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=testpassword \
    uv run pytest tests/graph/test_py_parity_live.py -v

(e.g. `podman run -d -p 7687:7687 -e NEO4J_AUTH=neo4j/testpassword neo4j:5` — a DEDICATED
container is still advised, even though the analyzer's bolt writer scopes its orphan-module
prune to THIS application's own app_name (codeanalyzer/neo4j/bolt.py's full-run prune reads
`WHERE ... app=app_name`, never touching other applications' data) — a fresh container just
keeps this suite free of any stale state left over from a previous run.)
"""

import json
import os
from pathlib import Path

import pytest

RES = Path(__file__).parent.parent / "resources" / "cpg"
NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(not NEO4J_URI, reason="set CLDK_TEST_NEO4J_URI to run")


@pytest.fixture(scope="module")
def both_backends(tmp_path_factory):
    """(local, remote): the real in-process PyCodeanalyzer and a PyNeo4jBackend over the
    SAME analysis, projected to Neo4j out of band."""
    payload = json.loads((RES / "py-a4.json").read_text())
    proj = tmp_path_factory.mktemp("pyfix")
    for path, mod in payload["application"]["symbol_table"].items():
        f = proj / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(mod["source"] or "")

    from cldk.analysis import AnalysisLevel
    from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer

    local = PyCodeanalyzer(
        project_dir=proj,
        analysis_level=AnalysisLevel.system_dependency_graph,
        analysis_json_path=None,
        eager_analysis=True,
    )

    # Populate Neo4j from the SAME analysis `local` already ran — never a second analyze():
    # jedi call-resolution is nondeterministic across runs and a re-analyze would masquerade
    # as a backend diff. codeanalyzer-python 1.0.2's emit_neo4j takes the v2 `Analysis`
    # envelope (schema_version/max_level/analyzer/application), not a bare PyApplication —
    # confirmed against its real signature and tests/analysis/python/test_python_neo4j_backend.py
    # (the emit-API sketch in the task brief predates that envelope). `analyzer` lives only on
    # the envelope Codeanalyzer.analyze() built (not on PyApplication itself), and `local`
    # doesn't keep that envelope around, so it defaults here — pure metadata, not read by
    # emit_neo4j's projection logic. Wrapping `local`'s own already-analyzed application in a
    # fresh envelope reuses the identical analysis result (zero re-analysis).
    from codeanalyzer.neo4j.emit import emit_neo4j
    from codeanalyzer.options import AnalysisOptions
    from codeanalyzer.schema.py_schema import Analysis

    neo4j_user = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")

    # app_name MUST equal proj.name (the same value PyCodeanalyzer's own AnalysisOptions
    # defaulted to at analysis time — cldk never sets app_name, so codeanalyzer-python
    # falls back to `self.project_dir.name`). emit_neo4j's assign_ids() is only idempotent
    # when re-run with the SAME app_name: it re-stamps every module/class/callable `.id` in
    # place on `local.application`, but does NOT touch the already-baked-in cfg/ddg/param_in/
    # param_out edge src/dst strings from the original analysis. A different app_name here
    # would rename callable ids out from under those edges, corrupting `local`'s own identity
    # consistency as a side effect of populating Neo4j (a real footgun in this two-actor
    # in-place-mutation harness, hand-verified against codeanalyzer-python 1.0.2's
    # `Codeanalyzer.analyze()` / `codeanalyzer.schema.assign_ids.assign_ids`).
    app_name = proj.name
    analysis = Analysis(
        max_level=local.max_level(),
        application=local.get_application_view(),
    )
    emit_neo4j(
        analysis,
        AnalysisOptions(
            input=proj,
            app_name=app_name,
            neo4j_uri=NEO4J_URI,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
        ),
    )

    from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

    remote = PyNeo4jBackend(
        neo4j_uri=NEO4J_URI,
        neo4j_username=neo4j_user,
        neo4j_password=neo4j_password,
        neo4j_database=None,
        application_name=app_name,
    )
    yield local, remote
    remote.close()


def test_max_level_parity(both_backends):
    local, remote = both_backends
    assert local.max_level() == remote.max_level() == 4


def test_program_graph_parity_per_callable(both_backends):
    local, remote = both_backends
    for cid in local._index()["callables"]:
        lg, rg = local.program_graph(cid), remote.program_graph(cid)
        assert set(lg.nodes) == set(rg.nodes), cid
        # prov included (as a tuple, so it hashes into the set key) — #270 final review
        # Finding 4(a): a backend that dropped/mismatched ddg provenance would previously pass
        # this parity check silently since prov wasn't compared at all.
        lset = {(u, v, d["family"], d.get("kind"), d.get("var"), tuple(d.get("prov") or []))
                for u, v, d in lg.edges(data=True)}
        rset = {(u, v, d["family"], d.get("kind"), d.get("var"), tuple(d.get("prov") or []))
                for u, v, d in rg.edges(data=True)}
        assert lset == rset, cid


def test_sdg_parity(both_backends):
    local, remote = both_backends
    key = lambda es: {(e.src, e.dst, e.kind) for e in es}  # noqa: E731
    assert key(local.sdg_edges()) == key(remote.sdg_edges())


def test_source_slice_and_callable_of_parity_per_vertex(both_backends):
    # #270 final review Finding 4(b): every vertex of every callable must agree on callable_of
    # (both backends), and on source_slice's location half (Neo4j's `code` is documented-lossy —
    # always None there — so only the (module[:line] | None) half is compared).
    local, remote = both_backends
    checked = 0
    for cid in local._index()["callables"]:
        for v in local.program_graph(cid).nodes():
            assert local.callable_of(v) == remote.callable_of(v), v
            l_fl, _ = local.source_slice(v)
            r_fl, r_code = remote.source_slice(v)
            assert l_fl == r_fl, v
            assert r_code is None
            checked += 1
    assert checked > 0  # sanity: the fixture actually has vertices to compare


def test_resolve_location_parity_for_every_spanned_vertex(both_backends):
    # #270 final review Finding 4(b): every (file, line) that owns at least one real vertex must
    # return the SAME full hit-list on both backends, not just agree on a single hand-picked
    # location (the existing verb-parity tests only ever probe one seed per verb).
    local, remote = both_backends
    locations = set()
    for cid in local._index()["callables"]:
        for v in local.program_graph(cid).nodes():
            fl, _ = local.source_slice(v)
            if not fl or ":" not in fl:
                continue  # synthetic vertex (@entry/@exit/formal_*/actual_*) — no line to probe
            file, _, line = fl.rpartition(":")
            locations.add((file, int(line)))
    assert locations  # sanity: the fixture actually has spanned (real source line) vertices
    for file, line in locations:
        assert set(local.resolve_location(file, line)) == set(remote.resolve_location(file, line)), (file, line)


def test_verb_parity(both_backends):
    from cldk.graph import Engine

    local, remote = both_backends
    # A real param_in pair, discovered from the live application rather than hardcoded ids: the
    # fixture's app_name is a random tmp-dir name (see both_backends), so can:// ids aren't
    # stable across runs and can't be pinned as string literals here.
    param_in_edge = next(e for e in local.sdg_edges() if e.kind == "param_in")
    for verb, args in (
        ("slice_backward", ("pkg/mod.py:5",)),
        ("slice_forward", ("pkg/mod.py:3",)),
        ("def_use", ("pkg/mod.py:3",)),
        ("control_deps", ("pkg/mod.py:3",)),
        ("flows_to", (param_in_edge.src, param_in_edge.dst)),
    ):
        l = getattr(Engine(local), verb)(*args)  # noqa: E741
        r = getattr(Engine(remote), verb)(*args)
        assert set(l.uris()) == set(r.uris()), verb
