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

"""End-to-end integration tests for CLDK 2.0 leg 1 against a **live, pre-loaded** Neo4j graph.

Why this module exists
----------------------
Every other Neo4j assertion in leg 1 runs against ``conftest.FakeDriver`` — a hand-written fake
whose responses the tests themselves supply. That harness cannot fail when the SDK reads a
property the emitter never writes: the fake happily returns whatever the test put there. The leg
shipped a test asserting on a ``:PyModule.source`` property that does not exist, and two more
places where the SDK read absent properties, all of which passed review. This module is the
counterweight: no fakes, no fixtures we author — a real ``codeanalyzer-python`` 1.4.0 graph of a
real 2,364-file application, queried over Bolt through the public facade.

Running it
----------
The graph is loaded **out of band** (this suite is strictly read-only and never writes a node,
relationship, or property — not even in setup). Point it at a server with::

    CLDK_TEST_NEO4J_URI=bolt://localhost:7688 \
    CLDK_TEST_NEO4J_USER=neo4j \
    CLDK_TEST_NEO4J_PASSWORD=cldkleg1test \
    CLDK_TEST_NEO4J_APP=odoo-slim-19 \
    uv run pytest tests/analysis/python/test_e2e_neo4j_live.py

``CLDK_TEST_NEO4J_APP`` is new here (the older ``test_python_neo4j_backend.py`` hardcodes the
application name it loads itself; this suite attaches to a graph it did not build, so the name has
to be an input). The whole module skips — cleanly, never fails — when no server answers or when the
named application is absent from it, so CI without Neo4j stays green.

The three heaviest accessors run by default
-------------------------------------------
``get_symbol_table()``, ``get_classes()`` and ``get_call_graph_json()`` are the three heaviest calls
in the facade. They **used to** take about six to seven minutes each (recorded here: 391 s, 352 s,
422 s), because the first two rebuilt every module / class by fanning out one Cypher query per child
collection — an N+1 over 1,626 modules and 1,642 classes, 73,669 round trips for the symbol table
alone — and the third calls ``get_symbol_table()`` internally and inherited the whole bill. That
fan-out is gone (leg 1.5: the child collections are now fetched once for the application and served
from a by-parent index), and the same three now measure **10.5 s, 11.1 s and 28.3 s** on the same
graph.

They were gated behind ``CLDK_TEST_NEO4J_SLOW=1`` while twenty minutes was the known state. That
gate is gone: it now guards about fifty seconds, and it was buying that back at the cost of the
only end-to-end proof that the collapse holds — a regression to the fan-out would have gone unseen
in every default run. Fifty seconds against a live graph you had to stand up on purpose is a price
worth paying for the assertion that the collapse is still in force. ``CLDK_TEST_NEO4J_SLOW`` is no
longer read anywhere.

The whole module now runs in about seventy seconds.

Fixture selection
-----------------
Nothing below hardcodes a signature, path, line number, or count read off a file by hand. Every
fixture is *derived from the graph at run time* by :func:`cypher`, deterministically
(``ORDER BY ... LIMIT 1``), and assertions prefer structural invariants over magic numbers so a
rebuilt graph does not turn this suite red for the wrong reason.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig
from cldk.utils.exceptions import GraphSchemaMismatch

logging.getLogger("neo4j").setLevel(logging.ERROR)

NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
APP_NAME = os.environ.get("CLDK_TEST_NEO4J_APP", "odoo-slim-19")

# The four relationship types PyNeo4jBackend._probe_schema insists on. Duplicated here on purpose:
# a test that imports the constant it is checking cannot catch the constant changing.
REQUIRED_RELATIONSHIP_TYPES = {"PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_BODY_NODE", "PY_CALLS"}


def _live_application_present() -> bool:
    """True iff a Neo4j server answers at ``NEO4J_URI`` *and* holds ``APP_NAME``.

    Connectivity alone is not enough: a developer with the leg-1 parity suite's own container on the
    default port would otherwise get this module's assertions run against the wrong graph and see
    failures that mean nothing. Requiring the named ``:PyApplication`` makes "wrong server" skip the
    same way "no server" does.
    """
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError:
        return False
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        try:
            driver.verify_connectivity()
            with driver.session() as session:
                found = session.run("MATCH (a:PyApplication {name: $n}) RETURN count(a) AS c", n=APP_NAME).single()
                return bool(found and found["c"])
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 - any connection/auth failure ⇒ skip, never fail
        return False


pytestmark = pytest.mark.skipif(
    not _live_application_present(),
    reason=(
        f"no Neo4j at {NEO4J_URI} holding application {APP_NAME!r} "
        "(set CLDK_TEST_NEO4J_URI / _USER / _PASSWORD / _APP)"
    ),
)


# =====================================================================================
# Fixtures — one attached facade, plus a raw read-only session used ONLY to derive
# fixtures. Assertions go through the public facade; cypher() only chooses what to
# assert *about*, so the suite never proves the SDK right by asking the SDK.
# =====================================================================================
@pytest.fixture(scope="module")
def raw_session():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    session = driver.session()
    yield session
    session.close()
    driver.close()


@pytest.fixture(scope="module")
def cypher(raw_session):
    """Run a read-only Cypher statement, returning plain dicts. Fixture derivation only."""

    def run(query: str, **params: Any) -> List[Dict[str, Any]]:
        return [record.data() for record in raw_session.run(query, **params)]

    return run


@pytest.fixture(scope="module")
def analysis():
    """The facade under test, attached once for the module (construction costs ~0.5-1.6 s)."""
    facade = CLDK.python(
        backend=Neo4jConnectionConfig(
            uri=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD,
            application_name=APP_NAME,
        )
    )
    yield facade
    facade.backend.close()


@pytest.fixture(scope="module")
def sample(cypher) -> Dict[str, Any]:
    """A real, deterministically chosen callable to locate/read source for, plus its module.

    Every constraint below is load-bearing, and three of them were added *because* a looser query
    picked a fixture that made a correct SDK look broken:

    * ``(:PyModule)-[:PY_DECLARES]->(c)`` — a genuine **top-level** module function. A merely
      "not a class method" filter also admits functions nested inside a method, and those behave
      differently in two ways that are the analyzer's business, not the SDK's: ``get_method`` does
      not resolve them by module name (correctly — they are not module functions), and their
      ``:PyCallable.code`` is not verbatim disk text (see
      :func:`test_nested_callable_code_is_not_verbatim_disk_text`).
    * a **unique** ``module_name`` — Odoo has hundreds of ``__init__.py``, and ``get_method`` looks a
      module function up by module *name*, so a non-unique name would make that test ambiguous.
    * ``first_def > 2`` — guarantees a real module-scope line exists above every definition.
    * a ``call``-kind body node with a line span — gives a position that must resolve past the
      callable to a ``node_id``.

    ``ORDER BY ... LIMIT 1`` keeps the pick stable across runs and across graph rebuilds.
    """
    rows = cypher(
        """
        MATCH (m:PyModule) WITH m.module_name AS mn, collect(m.file_key) AS ks WHERE size(ks) = 1
        WITH mn, ks[0] AS path
        MATCH (any:PyCallable {_module: path}) WITH mn, path, min(any.start_line) AS first_def
        WHERE first_def > 2
        MATCH (:PyModule {file_key: path})-[:PY_DECLARES]->(c:PyCallable)-[:PY_HAS_BODY_NODE]->(b:PyBodyNode)
        WHERE c.code IS NOT NULL AND c.end_line - c.start_line >= 3
          AND b.kind = 'call' AND b.start_line IS NOT NULL
        WITH mn, path, first_def, c, min(b.start_line) AS call_line
        RETURN c.signature AS signature, c.name AS name, c.code AS code, c.path AS disk_path,
               mn AS module_name, path AS module_path, c.start_line AS start_line,
               c.end_line AS end_line, call_line, first_def
        ORDER BY c.signature LIMIT 1
        """
    )
    assert rows, "no top-level module function in the graph satisfies the fixture constraints"
    picked = dict(rows[0])
    # A line that is inside the callable AND inside a body node — the position that must resolve all
    # the way down to a node_id, not just to the enclosing callable.
    picked["inner_line"] = picked["call_line"]
    # A line above every definition in this module: module scope by construction, not by guesswork.
    picked["module_scope_line"] = picked["first_def"] - 1
    return picked


# =====================================================================================
# The schema probe
# =====================================================================================
def test_schema_probe_passes_against_a_genuine_1_4_0_graph(analysis, cypher):
    """Attaching succeeded (the ``analysis`` fixture would have raised), and the vocabulary is real.

    ``_probe_schema`` runs inside ``__init__``, so a green fixture already proves it did not raise.
    This additionally pins *why*: the four required types are genuinely present in this graph, so
    the probe passed on merit rather than because the check is vacuous.
    """
    found = {r["relationshipType"] for r in cypher("CALL db.relationshipTypes()")}
    assert REQUIRED_RELATIONSHIP_TYPES <= found, f"missing {REQUIRED_RELATIONSHIP_TYPES - found}"
    # And the v1 vocabulary this backend was migrated off is genuinely gone.
    assert "PY_HAS_CALLSITE" not in found


def test_schema_probe_still_raises_when_the_vocabulary_is_missing(cypher):
    """``GraphSchemaMismatch`` on a graph missing the expected vocabulary — negative case, one server.

    Simulated without a second container by wrapping the **real** driver in a shim that intercepts
    only ``CALL db.relationshipTypes()`` and answers with the 0.3.x vocabulary (``PY_HAS_CALLSITE``
    instead of ``PY_HAS_BODY_NODE``). Every other query still hits the live database, so what is
    under test is the probe rather than a fake's bookkeeping — and nothing is written.
    """
    from neo4j import GraphDatabase

    class _V1Record:
        def __init__(self, value: str) -> None:
            self._value = value

        def data(self) -> Dict[str, str]:
            return {"relationshipType": self._value}

    class _ShimSession:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def run(self, query: str, **params: Any) -> Any:
            if "db.relationshipTypes" in query:
                return [_V1Record(t) for t in ("PY_HAS_MODULE", "PY_HAS_METHOD", "PY_HAS_CALLSITE", "PY_CALLS")]
            return self._inner.run(query, **params)

        def close(self) -> None:
            self._inner.close()

    class _ShimDriver:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def session(self, **kwargs: Any) -> _ShimSession:
            return _ShimSession(self._inner.session(**kwargs))

        def close(self) -> None:
            self._inner.close()

    from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with pytest.raises(GraphSchemaMismatch) as excinfo:
            PyNeo4jBackend._from_driver(_ShimDriver(driver), application_name=APP_NAME)
        assert "PY_HAS_BODY_NODE" in excinfo.value.missing
        assert "PY_HAS_CALLSITE" in str(excinfo.value)  # names what it found, not only what it wanted
    finally:
        driver.close()


# =====================================================================================
# The silent-empty guard — the single most important assertion here
# =====================================================================================
def test_callables_overview_is_not_silently_empty(analysis, cypher):
    """The direct check against the 905 recorded silent-empty incidents.

    Asserted as an invariant against the graph's own count rather than against the literal 15,549,
    so a rebuilt or extended graph keeps this honest instead of brittle.
    """
    overview = analysis.get_callables_overview()
    assert overview, "get_callables_overview() came back empty against a graph with callables in it"

    expected = cypher("MATCH (c:PyCallable) RETURN count(c) AS c")[0]["c"]
    assert expected > 0
    assert len(overview) == expected

    # And the rows carry real content, not empty shells that would read as "populated".
    assert all(o.signature for o in overview)
    assert any(o.path for o in overview)


def test_callables_overview_is_scoped_to_the_application(analysis, cypher):
    """Every overview row belongs to one of the application's own modules (the ``_module IN $mods``
    scoping that keeps a multi-application database from bleeding across).

    Checked by *equality* against ``m.file_key``: the projection reads ``c._module``, which is that
    same key — see :func:`test_overview_path_joins_locate_and_class_overview`.
    """
    module_keys = {r["k"] for r in cypher("MATCH (:PyApplication {name: $n})-[:PY_HAS_MODULE]->(m:PyModule) RETURN m.file_key AS k", n=APP_NAME)}
    paths = {o.path for o in analysis.get_callables_overview() if o.path}
    assert paths, "no overview row carried a path"
    unowned = [p for p in paths if p not in module_keys]
    assert not unowned, f"overview rows outside the application's modules: {unowned[:3]}"


def test_overview_path_joins_locate_and_class_overview(analysis, sample, cypher):
    """The facade hands back **one** path vocabulary, and this is the test that says so.

    Until 1.5 it handed back two. ``PyCallableOverview.path`` was ``:PyCallable.path`` — the
    *absolute* path on the machine that ran the analysis, so all 15,549 rows started with ``/`` —
    while ``LocateResult.module.path`` and ``PyClassOverview.path`` were the project-relative
    ``file_key`` / ``_module``, the latter because ``:PyClass`` carries no ``path`` property at all
    (still verified below). A caller feeding ``get_callables_overview()[i].path`` back into
    ``locate()`` was relying on ``resolve_module_key``'s suffix fallback to save it, and one
    comparing the two fields for equality got silently empty results.

    The callable overview now projects ``c._module``, so the three agree exactly. This test is the
    old divergence witness turned the right way up: it fails if either vocabulary drifts back.
    """
    assert cypher("MATCH (c:PyClass) WHERE c.path IS NOT NULL RETURN count(c) AS c")[0]["c"] == 0

    overview = {o.signature: o for o in analysis.get_callables_overview()}
    row = overview[sample["signature"]]
    assert row.path == sample["module_path"], "the overview path is the repo-relative module key"
    assert not row.path.startswith("/"), "an absolute path names the analysis machine, not the repo"

    located = analysis.locate(sample["module_path"], sample["inner_line"])
    assert located.module.path == row.path

    # ...and the third vocabulary. ``PyClassOverview.path`` is projected from ``cl._module``; this
    # graph has no entrypoint classes to read one back through, so the check goes to that property
    # directly. Both projections now name a module by its ``file_key`` -- the same dictionary the
    # callable overview draws from. (Not a subset check against the callable paths: a module can
    # declare a class and no callable, and 66 of this application's 1,157 class-bearing modules do.)
    module_keys = {r["k"] for r in cypher("MATCH (:PyApplication {name: $n})-[:PY_HAS_MODULE]->(m:PyModule) RETURN m.file_key AS k", n=APP_NAME)}
    class_paths = {r["p"] for r in cypher("MATCH (cl:PyClass) WHERE cl._module IN $m RETURN DISTINCT cl._module AS p", m=list(module_keys))}
    assert class_paths, "no classes in the graph"
    assert row.path in module_keys and class_paths <= module_keys, "class and callable overviews disagree on path spelling"


# =====================================================================================
# v2 vocabulary migration — PyBodyNode{kind:'call'} reached over PY_HAS_BODY_NODE
# =====================================================================================
def test_call_sites_come_from_body_nodes_over_py_has_body_node(analysis, sample, cypher):
    """The v2 shape: call sites are ``:PyBodyNode {kind:'call'}`` hung off ``PY_HAS_BODY_NODE``.

    Asserted through the facade (``get_callsites_for``) and cross-checked against the graph's own
    count of that exact pattern, so the SDK is shown to be reading the v2 vocabulary rather than
    coincidentally returning the right number of somethings.
    """
    sig = sample["signature"]
    sites = analysis.get_callsites_for([sig])
    assert sig in sites, "an existing callable must be keyed even when it has no call sites"

    expected = cypher(
        "MATCH (c:PyCallable {signature: $s})-[:PY_HAS_BODY_NODE]->(b:PyBodyNode {kind: 'call'}) RETURN count(b) AS c",
        s=sig,
    )[0]["c"]
    assert expected > 0, "fixture selection guarantees at least one call body node"
    assert len(sites[sig]) == expected


def test_get_callsites_for_keys_every_requested_signature(analysis, sample):
    """A requested signature with no call sites still gets an (empty) entry — not a missing key."""
    sig = sample["signature"]
    result = analysis.get_callsites_for([sig, "no.such.module.no_such_callable"])
    assert sig in result
    assert "no.such.module.no_such_callable" not in result


# =====================================================================================
# locate / locate_many
# =====================================================================================
def test_locate_resolves_to_the_enclosing_callable(analysis, sample):
    """A real position in a real Odoo file resolves to the callable that encloses it."""
    result = analysis.locate(sample["module_path"], sample["inner_line"])

    assert result.callable is not None, f"line {sample['inner_line']} of {sample['module_path']} resolved to nothing"
    assert result.callable.signature == sample["signature"]
    assert result.callable.name == sample["name"]
    assert result.module.path == sample["module_path"]
    assert not result.diagnostics, f"unexpected diagnostics: {result.diagnostics}"

    # The span really contains the position asked about.
    assert result.span.start[0] <= sample["inner_line"] <= result.span.end[0]
    assert result.span.start[0] == sample["start_line"]
    assert result.span.end[0] == sample["end_line"]

    # The position was chosen to sit on a call body node, so it must resolve past the callable.
    assert result.node is not None
    assert result.node_id is not None
    # #320: the id is the graph's own ``:PyBodyNode.id``, read off the node — not composed from
    # the dotted signature, which is a different namespace and joined to nothing.
    assert result.node_id.startswith("can://")


def test_locate_source_is_real_text_matching_the_file_on_disk(analysis, sample):
    """``source`` is the callable's actual text — verified against the bytes on disk, not just
    against the graph that produced it.

    ``:PyCallable.path`` carries the absolute path the analyzer read. When the sources are not
    checked out on this machine the disk half is skipped (attaching to a graph built elsewhere is
    the Neo4j backend's whole point), but the graph-side assertions above still run.
    """
    result = analysis.locate(sample["module_path"], sample["inner_line"])
    assert result.source, "locate() returned empty source for a callable that has code"
    assert result.source.lstrip().startswith(("def ", "async def ", "@")), result.source[:80]
    assert sample["name"] in result.source

    disk = Path(sample["disk_path"])
    if not disk.is_file():
        pytest.skip(f"project sources not on this machine ({disk})")

    lines = disk.read_text(encoding="utf-8", errors="replace").splitlines()
    on_disk = "\n".join(lines[sample["start_line"] - 1 : sample["end_line"]])
    assert result.source.rstrip() == on_disk.rstrip()


def test_nested_callable_code_is_not_verbatim_disk_text(analysis, cypher):
    """Recorded quirk found while choosing fixtures: for a **nested** callable, ``code`` is only
    half-dedented, so it matches no contiguous slice of the file.

    The stored text drops the indentation of the ``def`` line but keeps it on every body line::

        def rename_duplicates(docs):
                    seen = {}

    Top-level functions are unaffected (the test above compares one byte for byte). This is
    ``codeanalyzer-python``'s text extraction, not an SDK defect — the SDK returns the property
    faithfully — but it means a caller cannot splice ``get_source``/``locate().source`` for a nested
    callable back into the file, and cannot re-derive its column offsets from the text. Pinned so
    the limitation is known rather than discovered at a call site.
    """
    rows = cypher(
        """
        MATCH (c:PyCallable)
        WHERE c.code IS NOT NULL AND c.start_line IS NOT NULL AND c.path IS NOT NULL
          AND NOT ( (:PyModule)-[:PY_DECLARES]->(c) ) AND NOT ( (:PyClass)-[:PY_HAS_METHOD]->(c) )
          AND c.end_line > c.start_line
        RETURN c.signature AS signature, c.path AS disk_path, c.start_line AS start_line, c.end_line AS end_line
        ORDER BY c.signature LIMIT 1
        """
    )
    if not rows:
        pytest.skip("no nested callables in this graph")
    row = rows[0]
    disk = Path(row["disk_path"])
    if not disk.is_file():
        pytest.skip(f"project sources not on this machine ({disk})")

    source = analysis.get_source(row["signature"])
    lines = disk.read_text(encoding="utf-8", errors="replace").splitlines()
    on_disk = "\n".join(lines[row["start_line"] - 1 : row["end_line"]])

    assert source.rstrip() != on_disk.rstrip(), "nested callable code now matches disk; this quirk is fixed"
    # It is the leading indentation of the first line, and only that, which differs.
    assert not source.startswith((" ", "\t"))
    assert on_disk.startswith((" ", "\t"))
    assert source.rstrip() == on_disk.lstrip().rstrip()


def test_locate_accepts_an_absolute_path(analysis, sample):
    """``resolve_module_key`` normalises whatever a scanner printed; an absolute path must land on
    the same result as the project-relative ``file_key``."""
    if not Path(sample["disk_path"]).is_file():
        pytest.skip("project sources not on this machine")
    by_key = analysis.locate(sample["module_path"], sample["inner_line"])
    by_abs = analysis.locate(sample["disk_path"], sample["inner_line"])
    assert by_abs.callable is not None
    assert by_abs.callable.signature == by_key.callable.signature
    assert by_abs.module.path == by_key.module.path


def test_locate_at_module_scope_reports_source_unavailable(analysis, sample):
    """The documented divergence: over Neo4j a module-scope position has **empty** source.

    ``:PyModule`` genuinely carries no ``source`` property — its projected properties are
    ``file_key``/``module_name``/``content_hash``/``last_modified``/``file_size`` and nothing else
    (asserted below against the live node, because a leg-1 test previously asserted on a
    ``:PyModule.source`` that never existed). So the backend must say it cannot answer rather than
    invent text, and the caller must be able to tell that apart from "this module is empty".
    """
    result = analysis.locate(sample["module_path"], sample["module_scope_line"])

    assert result.callable is None
    assert result.node is None
    assert result.type is None
    assert result.module.path == sample["module_path"]
    assert result.source == ""

    codes = {d.code for d in result.diagnostics}
    assert "module_scope" in codes
    assert "module_source_unavailable" in codes


def test_py_module_really_has_no_source_property(cypher):
    """The ground truth behind the previous test — and the exact fact a leg-1 test got wrong.

    If a future emitter starts projecting module text, this fails and the divergence above should be
    revisited rather than left documented-but-stale.
    """
    rows = cypher("MATCH (m:PyModule) WHERE m.source IS NOT NULL RETURN count(m) AS c")
    assert rows[0]["c"] == 0, ":PyModule now carries a source property; the module_source_unavailable divergence is stale"


def test_locate_on_a_file_outside_the_graph_reports_file_not_in_graph(analysis):
    """A path the graph never saw is reported as such, naming the path asked about — never silently
    snapped to a neighbouring module."""
    result = analysis.locate("definitely/not/a/real/module_xyzzy.py", 1)
    assert result.callable is None
    assert result.source == ""
    codes = {d.code for d in result.diagnostics}
    assert codes == {"file_not_in_graph"}
    assert "module_xyzzy.py" in result.diagnostics[0].message


def test_locate_many_agrees_with_locate_position_by_position(analysis, sample, cypher):
    """``locate_many`` is not allowed to drift from ``locate``, nor to reorder its results."""
    others = cypher(
        """
        MATCH (c:PyCallable) WHERE c.code IS NOT NULL AND c.start_line IS NOT NULL AND c.end_line > c.start_line
        RETURN c._module AS path, c.start_line + 1 AS line, c.signature AS signature
        ORDER BY c.signature LIMIT 12
        """
    )
    positions = [(sample["module_path"], sample["inner_line"])]
    positions += [(r["path"], r["line"]) for r in others]
    positions += [
        (sample["module_path"], sample["module_scope_line"]),  # module scope
        ("definitely/not/a/real/module_xyzzy.py", 3),  # not in graph
    ]

    batch = analysis.locate_many(positions)
    assert len(batch) == len(positions)

    for (path, line), got in zip(positions, batch):
        one = analysis.locate(path, line)
        assert got.model_dump() == one.model_dump(), f"locate_many disagreed with locate at {path}:{line}"


def test_locate_many_is_a_single_round_trip(analysis, cypher):
    """Many positions must cost **one** Cypher statement, not one per position.

    Counted by wrapping the backend's ``_run`` seam — the only way to observe round trips at all.
    """
    positions = [
        (r["path"], r["line"])
        for r in cypher(
            """
            MATCH (c:PyCallable) WHERE c.start_line IS NOT NULL AND c.end_line > c.start_line
            RETURN c._module AS path, c.start_line + 1 AS line ORDER BY c.signature LIMIT 40
            """
        )
    ]
    assert len(positions) == 40

    backend = analysis.backend
    original = backend._run
    calls: List[str] = []

    def counting(query: str, **params: Any):
        calls.append(query)
        return original(query, **params)

    backend._run = counting  # type: ignore[method-assign]
    try:
        results = backend.locate_many(positions)
    finally:
        backend._run = original  # type: ignore[method-assign]

    assert len(results) == 40
    assert len(calls) == 1, f"locate_many issued {len(calls)} statements for 40 positions"
    assert "UNWIND" in calls[0]


# =====================================================================================
# get_source
# =====================================================================================
def test_get_source_returns_real_code_for_a_callable_id(analysis, sample):
    """A callable-granularity ``node_id`` is answerable: ``:PyCallable.code`` is a real property."""
    source = analysis.get_source(sample["signature"])
    assert source
    assert source == sample["code"]
    assert source == analysis.locate(sample["module_path"], sample["inner_line"]).source


def test_get_source_raises_for_a_body_node_id_naming_the_schema_gap(analysis, sample):
    """A body-node id names something the graph structurally cannot supply text for.

    The id used here is the one ``locate`` itself minted, so this is the exact round trip an agent
    would attempt: locate a position, then ask for the statement's source. It must raise and *say
    why* rather than silently substituting the enclosing callable's (much larger) text.
    """
    node_id = analysis.locate(sample["module_path"], sample["inner_line"]).node_id
    assert node_id is not None and "@" in node_id

    with pytest.raises(NotImplementedError) as excinfo:
        analysis.get_source(node_id)
    message = str(excinfo.value)
    assert node_id in message
    assert "PyBodyNode" in message and "PyModule" in message  # names the schema gap, not just "unsupported"


def test_get_source_raises_key_error_for_an_unknown_callable(analysis):
    with pytest.raises(KeyError):
        analysis.get_source("no.such.module.no_such_callable")


# =====================================================================================
# Repository-artifact layer
# =====================================================================================
def test_get_artifacts_returns_real_repository_manifests(analysis, cypher):
    artifacts = analysis.get_artifacts()
    assert artifacts, "get_artifacts() came back empty"

    expected = cypher("MATCH (:PyApplication {name: $n})-[:HAS_ARTIFACT]->(a:Artifact) RETURN count(a) AS c", n=APP_NAME)[0]["c"]
    assert len(artifacts) == expected

    # Keyed by path, and the paths are real repository paths.
    assert all(path == art.path for path, art in artifacts.items())
    assert any(path.endswith(("requirements.txt", "setup.py", "pyproject.toml")) for path in artifacts)


def test_get_dependencies_returns_real_declared_packages(analysis, cypher):
    deps = analysis.get_dependencies()
    assert deps, "get_dependencies() came back empty"

    expected = cypher(
        "MATCH (:PyApplication {name: $n})-[:HAS_ARTIFACT]->(:Artifact)-[r:DECLARES_DEPENDENCY]->(:Package) RETURN count(r) AS c",
        n=APP_NAME,
    )[0]["c"]
    assert len(deps) == expected
    assert all(d.name and d.ecosystem and d.declared_in for d in deps)


def test_get_dependencies_filters_actually_narrow(analysis, cypher):
    """Each filter is exercised against a value the graph really holds, and against one it does not.

    ``declared_in`` is the filter that genuinely partitions this application (several manifests each
    declaring a different slice); ``ecosystem`` and ``direct_only`` are checked as subsets plus a
    negative case, because on this graph every dependency happens to be a direct pypi one — a
    filter that cannot narrow here must still not *widen*, and must still return nothing for a value
    that matches nothing.
    """
    all_deps = analysis.get_dependencies()

    manifests = cypher(
        """
        MATCH (:PyApplication {name: $n})-[:HAS_ARTIFACT]->(a:Artifact)-[r:DECLARES_DEPENDENCY]->(:Package)
        RETURN a.id AS id, count(r) AS c ORDER BY c DESC
        """,
        n=APP_NAME,
    )
    assert len(manifests) >= 2, "need at least two declaring manifests to prove declared_in narrows"

    narrowed = analysis.get_dependencies(declared_in=manifests[0]["id"])
    assert narrowed, "declared_in returned nothing for a manifest the graph says declares dependencies"
    assert len(narrowed) == manifests[0]["c"]
    assert len(narrowed) < len(all_deps), "declared_in did not narrow"
    assert all(d.declared_in == manifests[0]["id"] for d in narrowed)

    assert analysis.get_dependencies(declared_in="can://artifact/nope/nope.txt") == []

    ecosystems = {d.ecosystem for d in all_deps}
    one_eco = sorted(ecosystems)[0]
    by_eco = analysis.get_dependencies(ecosystem=one_eco)
    assert by_eco and len(by_eco) <= len(all_deps)
    assert all(d.ecosystem == one_eco for d in by_eco)
    assert analysis.get_dependencies(ecosystem="no-such-ecosystem") == []

    direct = analysis.get_dependencies(direct_only=True)
    assert len(direct) <= len(all_deps)
    assert {d.name for d in direct} <= {d.name for d in all_deps}


def test_get_config_keys_and_config_uses_line_up(analysis, cypher):
    keys = analysis.get_config_keys()
    assert keys, "get_config_keys() came back empty"

    expected = cypher(
        "MATCH (:PyApplication {name: $n})-[:HAS_ARTIFACT]->(:Artifact)-[:DEFINES_CONFIG]->(ck:ConfigKey) RETURN count(ck) AS c",
        n=APP_NAME,
    )[0]["c"]
    assert len(keys) == expected
    assert all(key_id == ck.id for key_id, ck in keys.items())
    assert all(ck.key for ck in keys.values())

    uses = analysis.get_config_uses()
    assert uses, "get_config_uses() came back empty on a graph with PY_USES_CONFIG edges"
    # Every use points at a key this application actually defines.
    assert {u.dst for u in uses} <= set(keys)


def test_get_config_readers_finds_the_callable_that_reads_the_key(analysis, cypher):
    """Derived end to end: pick a key the graph says is read, ask who reads it, check the answer
    against the body node the use edge actually hangs off."""
    read_keys = cypher(
        "MATCH (bn:PyBodyNode)-[:PY_USES_CONFIG]->(ck:ConfigKey) RETURN DISTINCT ck.key AS key ORDER BY key LIMIT 1"
    )
    if not read_keys:
        pytest.skip("this graph has no PY_USES_CONFIG edges")
    key = read_keys[0]["key"]

    readers = analysis.get_config_readers(key)
    assert readers, f"nobody reads {key!r} but the graph has a PY_USES_CONFIG edge for it"

    expected = {
        r["sig"]
        for r in cypher(
            """
            MATCH (bn:PyBodyNode)-[:PY_USES_CONFIG]->(ck:ConfigKey {key: $k})
            MATCH (reader:PyCallable)-[:PY_HAS_BODY_NODE]->(bn)
            RETURN DISTINCT reader.signature AS sig
            """,
            k=key,
        )
    }
    assert {r.signature for r in readers} == expected

    # And filtering the uses by that key really narrows to that key.
    scoped = analysis.get_config_uses(key=key)
    assert scoped
    assert len(scoped) <= len(analysis.get_config_uses())
    assert all(u.dst.endswith(f"/{key}") or key in u.dst for u in scoped)


def test_get_unresolved_config_reads_returns_real_edges(analysis, cypher):
    reads = analysis.get_unresolved_config_reads()
    expected = cypher(
        "MATCH (:PyApplication {name: $n})-[u:PY_READS_CONFIG_UNRESOLVED]->(:PyExternal) RETURN count(u) AS c",
        n=APP_NAME,
    )[0]["c"]
    assert len(reads) == expected
    if not reads:
        pytest.skip("this graph records no unresolved config reads")
    assert all(r.callee for r in reads)
    # Documented lossiness: `site` is not an edge property, so it comes back empty over Neo4j.
    assert all(r.site == "" for r in reads)

    # `key` is empty exactly when the read's key is not a literal — the analyzer has no key to
    # record, and `reason` is what carries the explanation. (On this graph: 1 of 30 such edges.)
    # Asserting `all(r.key)` would be wrong, so the invariant is "key or a reason for its absence".
    assert all(r.key or r.reason for r in reads)
    keyless = [r for r in reads if not r.key]
    assert all(r.reason == "non-literal" for r in keyless), [r.reason for r in keyless]
    assert any(r.key for r in reads), "no unresolved read carried a key at all"


# =====================================================================================
# Entrypoints — the SDK must report what the graph says, including zero
# =====================================================================================
def test_entrypoints_faithfully_report_what_the_graph_says(analysis, cypher):
    """**Not** an assertion that Odoo has entrypoints.

    ``is_entrypoint`` is ``FALSE`` on all 15,549 callables and all 1,656 classes of this graph: the
    analyzer's detection pass found nothing on a framework built entirely from HTTP routes. That is
    upstream under-detection, not an SDK defect, and this suite must not paper over it by demanding
    a non-zero count. What the SDK owes the caller is fidelity — exactly as many entrypoints as the
    graph flags, no more and no fewer.
    """
    flagged_callables = cypher("MATCH (c:PyCallable) WHERE c.is_entrypoint = true RETURN count(c) AS c")[0]["c"]
    flagged_classes = cypher("MATCH (c:PyClass) WHERE c.is_entrypoint = true RETURN count(c) AS c")[0]["c"]

    assert len(analysis.get_entrypoints()) == flagged_callables
    assert len(analysis.get_entrypoint_classes()) == flagged_classes


def test_entrypoint_coverage_reports_that_it_cannot_tell(analysis):
    """The honest "I cannot tell you whether that zero is real".

    This diagnostic is the only thing standing between a caller and concluding, from
    ``get_entrypoints() == []``, that this application has no attack surface. The Neo4j projection
    never carries ``PyApplication.entrypoint_report``, so empty coverage fields here mean *unknown*,
    not *none* — and the caller has to be able to see the difference.
    """
    coverage = analysis.get_entrypoint_coverage()
    codes = {d.code for d in coverage.diagnostics}
    assert "entrypoint_report_unavailable" in codes

    message = next(d.message for d in coverage.diagnostics if d.code == "entrypoint_report_unavailable")
    assert "entrypoint_report" in message
    # The clean-looking empties are exactly what the diagnostic is there to qualify.
    assert coverage.frameworks_detected == []
    assert coverage.rulesets == []


def test_entrypoint_report_is_genuinely_absent_from_the_graph(cypher):
    """Ground truth for the diagnostic above: no node anywhere carries an entrypoint report."""
    rows = cypher("MATCH (a:PyApplication {name: $n}) RETURN properties(a) AS p", n=APP_NAME)
    assert rows, f"application {APP_NAME!r} vanished"
    props = set(rows[0]["p"])
    assert not any("entrypoint" in p for p in props), f"the projection now carries {props}; the diagnostic is stale"


# =====================================================================================
# External symbols and resolution
# =====================================================================================
def test_get_external_symbols_is_non_empty_and_application_scoped(analysis, cypher):
    externals = analysis.get_external_symbols()
    assert externals, "get_external_symbols() came back empty on a graph with :PyExternal nodes"

    expected = cypher("MATCH (e:PyExternal) RETURN count(e) AS c")[0]["c"]
    assert len(externals) == expected

    # :PyExternal has no `_module`, so scoping rides on the id prefix instead — check it holds.
    prefix = f"can://python/{APP_NAME}/@external/"
    assert all(key.startswith(prefix) for key in externals)
    assert all(key == sym.id for key, sym in externals.items())
    assert all(sym.name for sym in externals.values())


def test_has_resolution_edges_is_true_on_this_graph(analysis, cypher):
    """This graph is **not** the level-1 case: it carries ``PY_RESOLVES_TO``, so the probe says True.

    The probe exists to tell "genuinely unresolved call site" apart from "graph built below the
    analysis level where resolution runs". Getting the *positive* answer right matters as much as
    the negative one, and only a live graph can prove it.
    """
    edges = cypher("MATCH (:PyBodyNode)-[r:PY_RESOLVES_TO]->() RETURN count(r) AS c")[0]["c"]
    assert edges > 0, "fixture graph is expected to carry resolution edges"
    assert analysis.has_resolution_edges is True


def test_callsites_resolve_through_to_external_symbol_ids(analysis, cypher):
    """A call resolving to an external ghost must come back as that ghost's addressable ``@external``
    id — the ``coalesce(t.signature, t.id)`` path, which only a real graph exercises because
    ``:PyExternal`` carries no ``signature`` at all.
    """
    rows = cypher(
        """
        MATCH (c:PyCallable)-[:PY_HAS_BODY_NODE]->(b:PyBodyNode {kind: 'call'})-[:PY_RESOLVES_TO]->(t:PyExternal)
        RETURN c.signature AS sig, t.id AS target ORDER BY c.signature, t.id LIMIT 1
        """
    )
    if not rows:
        pytest.skip("no call site in this graph resolves to an external symbol")
    sig, target = rows[0]["sig"], rows[0]["target"]

    sites = analysis.get_callsites_for([sig])
    resolved = {s.callee_signature for s in sites[sig] if s.callee_signature}
    assert target in resolved, f"{sig} should resolve a call site to {target}"

    # The id is addressable: it is a key of get_external_symbols().
    assert target in analysis.get_external_symbols()


# =====================================================================================
# Pre-existing accessors
# =====================================================================================
def test_get_method_returns_a_real_callable(analysis, sample):
    """``get_method`` resolves a **module-level** function by module name, as documented.

    "Module-level" is exact: a function nested inside a method is not one, and ``get_method`` returns
    ``None`` for it (the ``sample`` fixture pins this down with ``PY_DECLARES``). The module name
    used here is unique in the graph, so the lookup is unambiguous.
    """
    module_name = sample["module_name"]
    method = analysis.get_method(module_name, sample["name"])
    assert method is not None, f"get_method({module_name!r}, {sample['name']!r}) found nothing"
    assert method.signature == sample["signature"]
    assert method.name == sample["name"]

    assert analysis.get_method(module_name, "no_such_function_xyzzy") is None


def test_get_all_classes_returns_top_level_classes_only(analysis, cypher):
    """``get_classes()`` is top-level-only by construction (``(:PyModule)-[:PY_DECLARES]->``).

    Recorded here as an invariant so the gap between ``count(:PyClass)`` and ``len(get_classes())``
    reads as *intended* rather than as loss: on this graph 1,656 classes = 1,642 top-level + 14
    nested. The expensive full call is gated below; this proves the partition cheaply.
    """
    total = cypher("MATCH (c:PyClass) RETURN count(c) AS c")[0]["c"]
    top_level = cypher("MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass) RETURN count(DISTINCT c) AS c")[0]["c"]
    nested = cypher("MATCH (c:PyClass) WHERE NOT ( (:PyModule)-[:PY_DECLARES]->(c) ) RETURN count(c) AS c")[0]["c"]
    assert total == top_level + nested
    assert nested > 0, "no nested classes here; this invariant is untested on this graph"


# =====================================================================================
# FIXED — the call-graph accessors used to be unusable on any graph with external calls
# (#see PyNeo4jBackend._call_rows: coalesce(t.signature, t.id))
# =====================================================================================
def test_call_graph_projection_reads_a_property_py_external_does_not_have(cypher):
    """Pins the graph *fact* the fix is grounded on (cheap, always runs).

    ``:PyExternal`` carries no ``signature`` property at all — only ``id``/``name``/``module`` — and
    ``PY_CALLS`` targets it for every call to a builtin or a library member. Measured on this graph:
    of the 364,752 in-scope ``PY_CALLS`` rows, **38,585 (10.6%) target a signature-less node**.

    ``PyNeo4jBackend._call_rows`` used to project bare ``t.signature`` for the edge target, so those
    38,585 rows came back with ``tgt = None`` and broke two ways downstream: ``_build_call_graph``
    calling ``nx.DiGraph.add_edge(src, None)`` (``ValueError: None cannot be a node`` — took down
    ``get_call_graph``, ``get_all_callers``, ``get_all_callees`` and ``get_class_call_graph``), and
    ``_call_edges`` building ``PyCallEdge(dst=None)`` (pydantic ``ValidationError`` — took down
    ``get_application_view`` and ``get_call_graph_json``). The fix is
    ``coalesce(t.signature, t.id)`` — the identical idiom ``get_callsites_for`` already used one
    screen away — so an external target resolves to its addressable ``@external`` can-id instead.

    Kept as a standing regression guard for the *cause*: if this ever reads 0, the coalesce is dead
    code and the "external targets are addressable nodes" tests below are not exercising anything.
    """
    externals_with_signature = cypher("MATCH (e:PyExternal) WHERE e.signature IS NOT NULL RETURN count(e) AS c")[0]["c"]
    assert externals_with_signature == 0, ":PyExternal now carries a signature; re-check the call-graph projection"

    null_targets = cypher(
        """
        MATCH (s:PyCallable|PyExternal)-[r:PY_CALLS]->(t:PyCallable|PyExternal)
        WHERE t.signature IS NULL
        RETURN count(r) AS c
        """
    )[0]["c"]
    assert null_targets > 0, (
        "no PY_CALLS edge targets a signature-less node here, so the coalesce fix is not exercised "
        "on this graph"
    )


@pytest.fixture(scope="module")
def module_keys(cypher) -> set:
    return {r["k"] for r in cypher("MATCH (:PyApplication {name: $n})-[:PY_HAS_MODULE]->(m:PyModule) RETURN m.file_key AS k", n=APP_NAME)}


@pytest.fixture(scope="module")
def busy_callable(cypher, module_keys) -> Dict[str, Any]:
    """A real, top-level module function that both has a real caller *and* calls an external —
    one fixture doubling for ``get_all_callers`` and ``get_all_callees`` ("external target is a
    node, not a dropped edge") without a second full-graph traversal.
    """
    rows = cypher(
        """
        MATCH (m:PyModule)-[:PY_DECLARES]->(c:PyCallable) WHERE c._module IN $mods
        MATCH (caller:PyCallable)-[:PY_CALLS]->(c) WHERE caller._module IN $mods
        WITH m, c, count(caller) AS n_callers
        WHERE n_callers > 0
        MATCH (c)-[:PY_CALLS]->(ext:PyExternal)
        WITH m, c, n_callers, collect(DISTINCT ext.id)[0] AS external_callee_id
        RETURN m.module_name AS module_name, c.name AS name, c.signature AS signature, external_callee_id
        ORDER BY c.signature LIMIT 1
        """,
        mods=list(module_keys),
    )
    assert rows, "no top-level module function here has both a real caller and an external callee"
    return dict(rows[0])


def test_get_call_graph_builds_with_external_targets_resolved(analysis, cypher, module_keys):
    """The fix, end to end through the facade — the networkx half.

    Not gated: fetching all 364,752 in-scope ``PY_CALLS`` rows measures at ~11 s. Node/edge counts
    are checked exactly (not just "some"), and a real ``@external`` can-id is confirmed present as a
    graph node rather than silently dropped — dropping the edge instead of crashing would be a worse
    bug than the crash (a missing call edge makes a reachable sink look unreachable).
    """
    graph = analysis.get_call_graph()

    expected_edges = cypher(
        "MATCH (s:PyCallable|PyExternal)-[r:PY_CALLS]->(t:PyCallable|PyExternal) WHERE s._module IN $mods RETURN count(r) AS c",
        mods=list(module_keys),
    )[0]["c"]
    assert graph.number_of_edges() == expected_edges
    assert graph.number_of_nodes() > 0

    external_ids = {r["id"] for r in cypher("MATCH (e:PyExternal) RETURN e.id AS id")}
    assert external_ids & set(graph.nodes), "no @external can-id landed as a call-graph node"


def test_get_all_callers_and_callees_resolve_for_a_real_callable(analysis, busy_callable):
    """``get_callers``/``get_callees`` (the facade names for backend ``get_all_callers``/
    ``get_all_callees``) route through the same ``get_call_graph()`` builder as the test above, so
    this is the facade-shaped proof rather than a re-derivation: a real caller comes back for a
    callable known to have one, and the external callee is keyed by its ``@external`` can-id rather
    than missing or ``None``.
    """
    callers = analysis.get_callers(busy_callable["module_name"], busy_callable["name"])
    assert callers["target_method"] == busy_callable["signature"]
    assert callers["caller_details"], "expected at least one caller (fixture guarantees n_callers > 0)"

    callees = analysis.get_callees(busy_callable["module_name"], busy_callable["name"])
    assert callees["source_method"] == busy_callable["signature"]
    callee_signatures = {c["callee_signature"] for c in callees["callee_details"]}
    assert busy_callable["external_callee_id"] in callee_signatures, (
        "external callee missing or dropped instead of appearing as its @external can-id"
    )


def test_get_call_graph_json_builds_with_external_targets_resolved(analysis):
    """The pydantic half of the same fix — and the **third** and heaviest of these accessors.

    ``get_call_graph_json`` → ``get_application_view()`` → ``PyApplication(symbol_table=
    self.get_symbol_table(), call_graph=self._call_edges())``. Before the fix this paid the full
    ~390 s symbol-table reconstruction and only then hit ``PyCallEdge(dst=None)`` and threw all of
    that work away. Now ``_call_edges`` never sees a ``None`` ``dst``, so this asserts the JSON
    actually contains resolved external call-edge targets rather than merely "did not raise" — and
    since leg 1.5 collapsed the symbol table's N+1 the whole call costs ~28 s, not ~420 s.
    """
    payload = analysis.get_call_graph_json()

    import json

    data = json.loads(payload)
    call_graph = data["call_graph"]
    assert call_graph, "call_graph is empty on a 364,752-edge application"
    assert all(edge["dst"] is not None for edge in call_graph)
    assert any(edge["dst"].startswith("can://") and "/@external/" in edge["dst"] for edge in call_graph), (
        "no external @external can-id target found in the serialized call graph"
    )


# =====================================================================================
# The two heaviest accessors — no longer opt-in
# =====================================================================================
# Measured on this graph (2,364 files, 1,626 modules, 1,656 classes):
#
#                          before      after     round trips
#     get_symbol_table()   ~440 s     10.5 s     73,669 -> 12   -> 1,626 modules
#     get_classes()        ~410 s     11.1 s     62,435 ->  8   -> 1,642 top-level classes
#
# "Before" is the N+1 fan-out these used to pay: one Cypher query per child collection per parent
# node, all the way down the nesting. "After" is leg 1.5's collapse — each child collection fetched
# once for the whole application and served from a by-parent index — verified to rebuild all 1,626
# modules and 1,642 classes byte-identically to what the fan-out produced.
#
# The ceiling was 900 s while six minutes was the known state. Thirty is now the generous figure:
# nearly 3x the measured 11 s, so a colder page cache or a busier machine does not turn it red, but
# a regression to the fan-out (or to anything else scaling with the application) cannot hide under
# it the way it could under fifteen minutes.
_SLOW_CEILING_SECONDS = 30


def test_get_symbol_table_returns_every_module(analysis, cypher):
    import time

    started = time.monotonic()
    table = analysis.get_symbol_table()
    elapsed = time.monotonic() - started

    expected = cypher("MATCH (:PyApplication {name: $n})-[:PY_HAS_MODULE]->(m:PyModule) RETURN count(m) AS c", n=APP_NAME)[0]["c"]
    assert len(table) == expected
    assert all(m is not None for m in table.values())
    assert elapsed < _SLOW_CEILING_SECONDS, f"get_symbol_table took {elapsed:.0f}s (10.5s when recorded, from ~440s before the N+1 collapse)"


def test_get_classes_returns_every_top_level_class(analysis, cypher):
    import time

    started = time.monotonic()
    classes = analysis.get_classes()
    elapsed = time.monotonic() - started

    expected = cypher("MATCH (:PyModule)-[:PY_DECLARES]->(c:PyClass) RETURN count(DISTINCT c) AS c")[0]["c"]
    assert len(classes) == expected
    assert all(name == cls.signature for name, cls in classes.items())
    assert elapsed < _SLOW_CEILING_SECONDS, f"get_classes took {elapsed:.0f}s (11.1s when recorded, from ~410s before the N+1 collapse)"


# =====================================================================================
# Leg 1.5, tasks 4-7: addressing, per-callable graphs, traversals, paths and hydration.
#
# ``test_dataflow.py`` pins these against one hand-picked callable with measured constants; this
# section is the other half of the evidence and asks a different question: do they hold for
# whatever the graph happens to contain? Every fixture below is derived at run time, in the file's
# own discipline -- no signature, value name, line number or count is written by hand -- so a
# rebuilt graph exercises different code and the assertions still mean something.
#
# The lesson being paid for is leg 1's: six accessors shipped broken on every real graph because
# hand-written fixtures encoded the SDK's own assumptions. This leg has already paid it twice more
# -- a fixture with a fabricated body-key grammar, and a bounded ``backward_cone`` that walked the
# wrong direction because nothing exercised it.
# =====================================================================================
_CROSSING_FLOW = """
MATCH (a:PyBodyNode {kind:'formal_in', _module:$mod})-[:PY_DDG]->(:PyBodyNode)-[:PY_DDG]->(:PyBodyNode {kind:'actual_in'})-[:PY_PARAM_IN]->(v:PyBodyNode {kind:'formal_in'})
WHERE a.var IS NOT NULL AND NOT a.var STARTS WITH '<' AND v.var IS NOT NULL AND NOT v.var STARTS WITH '<'
WITH a, v ORDER BY a.id, v.id LIMIT 1
MATCH (c1:PyCallable)-[:PY_HAS_BODY_NODE]->(a) MATCH (c2:PyCallable)-[:PY_HAS_BODY_NODE]->(v)
RETURN c1.signature AS src_callable, a.var AS src_value, a.id AS src_ref,
       c2.signature AS dst_callable, v.var AS dst_value, v.id AS dst_ref
"""


@pytest.fixture(scope="module")
def crossing_flow(analysis, cypher, module_keys) -> Dict[str, Any]:
    """A real value-to-value flow that crosses a call boundary, found by asking the graph.

    Scoped to one module at a time and taken from the first module (in ``file_key`` order) that has
    one: the unscoped form is the same query without ``_module``, and it takes 66 seconds because
    ordering the whole crossing set is the cost. Module-scoped it is 0.08 s, and the first hit on
    this graph is the fifth module.

    Both ends are then run back through ``resolve_value``, and a module whose names do not resolve
    uniquely is skipped rather than worked around: the fixture has to be addressable *the way a
    caller would address it*, or the accessors under test are not being exercised through their
    real front door.
    """
    for path in sorted(module_keys)[:40]:
        rows = cypher(_CROSSING_FLOW, mod=path)
        if not rows:
            continue
        found = dict(rows[0])
        try:
            if (
                analysis.backend.resolve_value(found["src_value"], within=found["src_callable"]).ref == found["src_ref"]
                and analysis.backend.resolve_value(found["dst_value"], within=found["dst_callable"]).ref == found["dst_ref"]
            ):
                return found
        except Exception:  # noqa: BLE001 - an ambiguous name here means "try the next module"
            continue
    pytest.skip("no module in the first 40 carries a uniquely addressable value flow across a call")


# -----[ Task 4: addressing ]-----
def test_resolved_addresses_name_real_nodes_in_the_graph(analysis, cypher, crossing_flow):
    """#320's invariant, on both kinds of address: the opaque ``ref`` a caller is handed back joins
    to a node that exists. A ref that named nothing would fail later, somewhere else, as a
    ``KeyError`` from ``describe`` or ``get_source``."""
    callable_node = analysis.backend.resolve_callable(crossing_flow["src_callable"])
    value_node = analysis.backend.resolve_value(crossing_flow["src_value"], within=crossing_flow["src_callable"])
    assert cypher("MATCH (c:PyCallable {id:$id}) RETURN count(c) AS c", id=callable_node.ref)[0]["c"] == 1
    assert cypher("MATCH (b:PyBodyNode {id:$id}) RETURN count(b) AS c", id=value_node.ref)[0]["c"] == 1
    assert callable_node.kind == "callable" and value_node.kind in {"parameter", "global", "capture"}


def test_no_analyzer_vocabulary_reaches_the_caller(analysis, crossing_flow):
    """E6/E7 on the addressing layer: nothing a caller reads is a ``can://`` id or an ordinal."""
    node = analysis.backend.resolve_value(crossing_flow["src_value"], within=crossing_flow["src_callable"])
    for field in (node.file, node.callable, node.kind, node.name or ""):
        assert "can://" not in field and "formal_in:" not in field
    assert node.name == crossing_flow["src_value"]


def test_locate_node_id_names_a_real_node(analysis, sample, cypher):
    """The same invariant from the other addressing entry point (#320)."""
    found = analysis.locate(sample["module_path"], sample["inner_line"])
    assert found.node_id
    assert cypher("MATCH (b:PyBodyNode {id:$id}) RETURN count(b) AS c", id=found.node_id)[0]["c"] == 1


# -----[ Task 5: the per-callable graphs ]-----
def test_the_per_callable_graphs_are_bounded_to_their_callable(analysis, cypher, crossing_flow):
    """The scoping is structural, not a cap: every endpoint of every edge is a body node the named
    callable owns, checked against the graph's own ``PY_HAS_BODY_NODE`` rather than by parsing ids.
    """
    sig = crossing_flow["src_callable"]
    owned = {r["id"] for r in cypher("MATCH (:PyCallable {signature:$s})-[:PY_HAS_BODY_NODE]->(b) RETURN b.id AS id", s=sig)}
    assert owned
    for page in (analysis.get_cfg(sig), analysis.get_cdg(sig), analysis.get_ddg(sig)):
        assert {e.src for e in page.edges} | {e.dst for e in page.edges} <= owned
        assert page.total >= len(page.edges)
    assert {p for e in analysis.get_ddg(sig).edges for p in e.prov} <= {"ssa", "reaching-defs", "points-to"}


def test_a_page_total_is_the_graphs_own_count(analysis, cypher, crossing_flow):
    """``total`` is E5's "a bound is never silent", and it has to be the size of the *whole* answer
    -- so it is compared to a count the database computes independently of the pager."""
    sig = crossing_flow["src_callable"]
    for accessor, rel in ((analysis.get_ddg, "PY_DDG"), (analysis.get_cdg, "PY_CDG"), (analysis.get_cfg, "PY_CFG_NEXT")):
        expected = cypher(
            f"MATCH (c:PyCallable {{signature:$s}})-[:PY_HAS_BODY_NODE]->(x)-[r:{rel}]->(y)<-[:PY_HAS_BODY_NODE]-(c) RETURN count(r) AS c",
            s=sig,
        )[0]["c"]
        assert accessor(sig).total == expected


# -----[ Task 6: slices and the call graph ]-----
def test_a_forward_slice_reaches_the_callee_and_stays_addressable(analysis, cypher, crossing_flow):
    """A slice contains its seed, reaches the value the graph says it reaches, and every node it
    hands back is addressed in the caller's vocabulary and joins to a real graph node."""
    sl = analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None)
    refs = [n.ref for n in sl.nodes]
    assert crossing_flow["src_ref"] in refs and crossing_flow["dst_ref"] in refs
    assert refs == sorted(set(refs)), "a slice is a set, in the one order both backends can compute"
    assert all("can://" not in n.callable and "can://" not in (n.name or "") for n in sl.nodes)
    assert all(n.source is None for n in sl.nodes), "a slice answers where, not what"
    present = cypher("MATCH (b:PyBodyNode) WHERE b.id IN $ids RETURN count(b) AS c", ids=refs)[0]["c"]
    assert present == len(refs), "every node in a slice is a node in the graph"


def test_a_capped_slice_reports_the_size_it_could_not_return(analysis, crossing_flow):
    sl = analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None)
    capped = analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None, max_nodes=1)
    assert capped.total == sl.total
    assert capped.complete is (sl.total <= 1)
    assert [n.ref for n in capped.nodes] == [n.ref for n in sl.nodes][:1]


def test_reaches_backward_cone_and_the_neighbour_accessors_agree_with_the_call_graph(analysis, cypher, crossing_flow):
    """Four accessors over one relationship, checked against ``PY_CALLS`` itself rather than
    against each other."""
    sig = crossing_flow["src_callable"]
    callees = [c for c in analysis.callees_of(sig) if c.kind == "callable"]
    declared = cypher(
        "MATCH (:PyCallable {signature:$s})-[:PY_CALLS]->(t:PyCallable) RETURN collect(DISTINCT t.signature) AS sigs", s=sig
    )[0]["sigs"]
    assert {c.callable for c in callees} == set(declared)
    for callee in callees:
        assert analysis.reaches(sig, callee.callable, depth=1)
        assert sig in {c.callable for c in analysis.callers_of(callee.callable)}
        assert sig in {n.callable for n in analysis.backward_cone([callee.callable], depth=1).nodes}


# -----[ Task 7: paths, mixed queries, hydration ]-----
def test_paths_between_explains_the_flow_the_slice_only_asserts(analysis, crossing_flow):
    """Every path is a joined sequence, every hop names the edge that justified it, and every node
    a path visits is in the forward slice of the same seed -- the set and the sequences describing
    one traversal, not two."""
    reached = {n.ref for n in analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None).nodes}
    paths = analysis.paths_between(
        crossing_flow["src_value"], crossing_flow["dst_value"],
        src_within=crossing_flow["src_callable"], dst_within=crossing_flow["dst_callable"],
    )
    assert paths, "the graph says this flow exists, so the paths accessor must find it"
    for p in paths:
        assert p.hops and all(h.via in {"data", "control", "argument", "return", "summary"} for h in p.hops)
        assert all(a.to.ref == b.frm.ref for a, b in zip(p.hops, p.hops[1:]))
        assert p.hops[0].frm.ref == crossing_flow["src_ref"] and p.hops[-1].to.ref == crossing_flow["dst_ref"]
        assert {h.frm.ref for h in p.hops} | {h.to.ref for h in p.hops} <= reached
        assert not [h for h in p.hops if h.to.file.startswith("/")], "a path is repo-relative, never the analysing machine's paths"
        assert p.weakest in p.hops
        assert all(set(h.prov) <= {"ssa", "reaching-defs", "points-to"} for h in p.hops)
    assert len({tuple((h.via, h.var, h.to.ref) for h in p.hops) for p in paths}) == len(paths), "no path is returned twice"


def test_call_paths_between_agrees_with_reaches(analysis, crossing_flow):
    """``reaches`` says whether; ``call_paths_between`` says how, and the two cannot disagree."""
    sig = crossing_flow["src_callable"]
    callees = [c.callable for c in analysis.callees_of(sig) if c.kind == "callable"]
    if not callees:
        pytest.skip("the derived source callable calls nothing declared")
    target = sorted(callees)[0]
    paths = analysis.call_paths_between(sig, target)
    assert paths and analysis.reaches(sig, target)
    assert all({h.via for h in p.hops} == {"call"} for p in paths)
    assert all(p.hops[-1].to.callable == target for p in paths)


def test_flows_to_argument_implies_flows_to_call(analysis, crossing_flow):
    """The implication holds by construction -- the argument's vertex is one of the callee's own
    entry vertices, which is the set ``flows_to_call`` tests -- so it is checked here on real data
    rather than assumed from the code."""
    reaches_arg = analysis.flows_to_argument(
        crossing_flow["src_value"], crossing_flow["dst_callable"], arg=crossing_flow["dst_value"],
        within=crossing_flow["src_callable"], depth=None,
    )
    assert reaches_arg is True, "the graph shows the flow, so the narrow question must answer yes"
    assert analysis.flows_to_call(
        crossing_flow["src_value"], crossing_flow["dst_callable"], within=crossing_flow["src_callable"], depth=None
    ) is True


def test_describe_hydrates_callables_and_is_honest_about_the_rest(analysis, crossing_flow):
    """What this graph can and cannot fill in. A callable has a real ``code`` property; a value
    vertex has no span in the analyzer's model and a statement has no text in the graph, so both
    come back ``None`` -- and ``None`` means only that, because a ref naming nothing raises."""
    cone = analysis.backward_cone([crossing_flow["dst_callable"]], depth=1)
    hydrated = analysis.describe(cone.nodes)
    assert [n.ref for n in hydrated] == [n.ref for n in cone.nodes]
    assert any(n.source for n in hydrated), "a callable's source is in the graph"

    values = analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None).nodes
    assert all(n.source is None for n in analysis.describe(values)), "no text below callable granularity"
    with pytest.raises(KeyError):
        analysis.describe([cone.nodes[0].model_copy(update={"ref": "can://python/absent/nothing.py/nothing"})])


def test_describe_is_one_round_trip_whatever_it_is_handed(analysis, crossing_flow, count_round_trips):
    """The promise that makes ``describe`` usable on a slice at all. A mixed batch, because that is
    the normal case: a path's endpoints are callables and its interior body nodes."""
    nodes = analysis.slice_forward(crossing_flow["src_value"], within=crossing_flow["src_callable"], depth=None).nodes
    nodes = list(nodes) + list(analysis.backward_cone([crossing_flow["dst_callable"]], depth=1).nodes)
    assert len(nodes) > 2
    n = count_round_trips(analysis)
    hydrated = analysis.describe(nodes)
    assert n["c"] == 1, f"describe took {n['c']} round trips for {len(nodes)} nodes"
    assert len(hydrated) == len(nodes)
