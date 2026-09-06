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

"""Python analysis test fixtures.

``FakeDriver``/``FakeSession`` stand in for the real ``neo4j`` driver — no container, no
network — so :class:`~cldk.analysis.python.neo4j.neo4j_backend.PyNeo4jBackend` can be
constructed and probed in-process. Build a backend from one via
``PyNeo4jBackend._from_driver(fake_driver, application_name=...)`` — the private constructor
seam that exists precisely so the public ``__init__`` (a real ``bolt://`` URI + credentials)
never has to accept anything but a URI string.
"""

import os
from typing import Any, Callable

import pytest

from cldk import CLDK
from cldk.analysis.commons.backend_config import Neo4jConnectionConfig

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.analysis.python.neo4j import PyNeo4jBackend
from cldk.models.python import BodyNode, PyApplication, PyCallable, PyClass, PyModule, Span

# The full vocabulary codeanalyzer-python 1.4.0 emits (see cldk/analysis/python/neo4j/neo4j_backend
# .py's module docstring / the leg-1 brief) — a reasonable "healthy v2 graph" default so fixtures
# that don't care about the schema probe (e.g. a future round-trip-counting test) don't have to set
# rel_types themselves.
#
# PY_EXTENDS is the class-inheritance edge type. codeanalyzer-python 1.4.0 never landed one on a
# real graph (the live Odoo application had 0 PY_EXTENDS edges across 1,656 classes: its emitter
# looked bases up by signature while `base_classes` held the written spelling, so every row was
# dropped as dangling); 1.4.1 (#181) resolves bases per module and emits them. A 1.4.0-emitted graph
# still has none, so a fixture meant to match one drops PY_EXTENDS.
_V2_RELATIONSHIP_TYPES = frozenset(
    {
        "PY_CALLS",
        "PY_CDG",
        "PY_CFG_NEXT",
        "PY_DDG",
        "PY_DECLARES",
        "PY_DECLARES_VAR",
        "PY_DECORATED_BY",
        "PY_EXTENDS",
        "PY_HAS_ATTRIBUTE",
        "PY_HAS_BODY_NODE",
        "PY_HAS_METHOD",
        "PY_HAS_MODULE",
        "PY_IMPORTS",
        "PY_PARAM_IN",
        "PY_PARAM_OUT",
        "PY_PROVIDES",
        "PY_READS_CONFIG_UNRESOLVED",
        "PY_RESOLVES_TO",
        "PY_SUMMARY",
        "PY_UNRESOLVED_IMPORT",
        "PY_USES_CONFIG",
        "DECLARES_DEPENDENCY",
        "DEFINES_CONFIG",
        "HAS_ARTIFACT",
        "LOCKS",
    }
)


class _FakeRecord:
    """Stands in for ``neo4j.Record``: ``PyNeo4jBackend._run`` calls ``.data()`` on every row."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def data(self) -> dict:
        return self._data


class FakeSession:
    """Stub Neo4j session: answers ``CALL db.relationshipTypes()`` from the driver's settable
    ``rel_types``; every other statement gets an empty result, which is enough for the schema
    probe and for construction to complete without a real graph behind it.
    """

    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def run(self, query: str, **params: Any) -> list[_FakeRecord]:
        self._driver.statements.append(query)
        if "db.relationshipTypes" in query:
            return [_FakeRecord({"relationshipType": rt}) for rt in self._driver.rel_types]
        if "RETURN a.analyzer_version AS v" in query:
            return [] if self._driver.analyzer_version is None else [_FakeRecord({"v": self._driver.analyzer_version})]
        if self._driver.responder is not None:
            return [_FakeRecord(d) for d in self._driver.responder(query, params)]
        return []

    def close(self) -> None:
        pass


class FakeDriver:
    """Stub Neo4j driver with a settable ``rel_types`` set and ``analyzer_version`` (``None``
    means "no :PyApplication of that name"), standing in for a real ``neo4j.GraphDatabase``
    driver in unit tests.

    ``responder``, when set, answers every statement that isn't the schema probe — a tiny
    in-memory Cypher stub (query text, params) -> rows, the same shape as the ad hoc
    ``_fake_cypher`` helper in ``test_python_method_lookup.py``, but wired through the real
    ``FakeSession.run`` so ``QueryCounter`` still counts the round trips genuinely fired.
    """

    def __init__(
        self,
        rel_types: set[str] | frozenset[str] = _V2_RELATIONSHIP_TYPES,
        responder: Callable[[str, dict], list[dict]] | None = None,
        analyzer_version: str | None = "1.4.1",
    ) -> None:
        self.rel_types: set[str] = set(rel_types)
        self.analyzer_version = analyzer_version
        self.statements: list[str] = []
        self.responder = responder

    def session(self, database: str | None = None) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_driver() -> FakeDriver:
    """A fresh :class:`FakeDriver`, defaulting to a healthy v2 vocabulary.

    Set ``fake_driver.rel_types`` before constructing a backend to simulate a different
    analyzer generation (or an empty graph).
    """
    return FakeDriver()


class QueryCounter:
    """Counts every Cypher statement actually executed against a driver.

    Not a stub: it wraps the driver's real ``session().run`` (via ``FakeDriver.statements``,
    which every ``FakeSession.run`` call appends to), so ``.count`` reflects genuine round
    trips. A counter that can't go wrong when the code under test fires N queries instead of
    one would defeat the point of asserting round-trip counts (see Task 6's ``locate_many``).
    """

    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    @property
    def count(self) -> int:
        return len(self.driver.statements)

    def reset(self) -> None:
        self.driver.statements.clear()


@pytest.fixture
def query_counter(fake_driver: FakeDriver) -> QueryCounter:
    """A :class:`QueryCounter` over a fresh, schema-healthy :class:`FakeDriver`."""
    return QueryCounter(fake_driver)


# --------------------------------------------------------------------------------------------
# Task 6 locate()/locate_many() fixture — ONE module description, BOTH backends.
#
# The module below, its callables and its body nodes are described once; the local
# ``PyCodeanalyzer`` application and the Neo4j responder rows are both derived from that single
# description. That is deliberate: a parity test comparing two backends is only worth running if
# they are answering about the same code, and two hand-maintained copies of the same fixture drift.
#
# What each position in the module is for:
#   line  1  module docstring          -> module scope (a real position, not an absence)
#   line 11  Meta.tag's body           -> a class nested inside a class (innermost owner wins)
#   line 15  inner()'s body            -> a closure nested inside a method (no owning class)
#   line 17  blank, inside the class   -> the gap between two callables; must NOT snap to either
#   line 19  ``def key(self):``        -> inside a callable, but inside no body node
#   line 20  ``if self._key:``         -> the ``if`` body node
#   line 21  the ``if``'s return       -> two nested body nodes contain it; innermost must win
#   line 24  ``def stub(self): ...``   -> a callable with NO span (abstract/protocol stub)
#   line 26  ``def one(self): return lambda: 2``
#                                     -> a lambda inside a one-line def: two callables of EQUAL line
#                                        width contain the position, so the innermost cannot be
#                                        decided by width and both backends must break the tie the
#                                        same way (longer signature = deeper)
#   line 29  ``if x: return x``        -> two body nodes of equal line width contain the position;
#                                        the tie breaks on the key's start column, deeper first
# "test/conftest.py" is deliberately absent (a file on disk that was never analysed).
# --------------------------------------------------------------------------------------------
_LOCATE_MODULE_PATH = "src/app.py"
_LOCATE_SOURCE_LINES = [
    '"""Store module."""',  # 1
    "",  # 2
    "",  # 3
    "class Store:",  # 4
    '    """A store, with a nested class and a closure."""',  # 5
    "",  # 6
    "    class Meta:",  # 7
    '        label = "meta"',  # 8
    "",  # 9
    "        def tag(self):",  # 10
    "            return self.label  # café",  # 11 — non-ASCII: byte offsets != char offsets
    "",  # 12
    "    def wrap(self):",  # 13
    "        def inner():",  # 14
    "            return 1",  # 15
    "        return inner",  # 16
    "",  # 17
    "",  # 18
    "    def key(self):",  # 19
    "        if self._key:",  # 20
    "            return self._key",  # 21
    "        return None",  # 22
    "",  # 23
    "    def stub(self): ...",  # 24
    "",  # 25
    "    def one(self): return lambda: 2",  # 26 — two callables tie on line width
    "",  # 27
    "    def two(self, x):",  # 28
    "        if x: return x",  # 29 — two body nodes tie on line width
]
_LOCATE_MODULE_SOURCE = "".join(line + "\n" for line in _LOCATE_SOURCE_LINES)


def _locate_span(start_line: int, end_line: int) -> Span:
    """The analyzer-shaped :class:`Span` for lines ``start_line``..``end_line`` (1-based, inclusive).

    ``bytes`` are real UTF-8 offsets into ``_LOCATE_MODULE_SOURCE``, computed rather than written by
    hand, so ``_code_of``'s encode/slice/decode is exercised on a module that actually contains a
    non-ASCII character (line 11) — a char-offset bug would slice every later callable short.
    """
    lines = _LOCATE_MODULE_SOURCE.splitlines(keepends=True)
    before = "".join(lines[: start_line - 1]).encode("utf-8")
    body = "".join(lines[start_line - 1 : end_line]).encode("utf-8")
    first, last = lines[start_line - 1], lines[end_line - 1].rstrip("\n")
    return Span(
        start=(start_line, len(first) - len(first.lstrip())),
        end=(end_line, len(last)),
        bytes=(len(before), len(before) + len(body)),
    )


def _locate_code(start_line: int, end_line: int) -> str:
    """The module text of lines ``start_line``..``end_line`` — what the graph's flat ``code``
    property holds (the emitter computes it the same way, via ``_span_code``)."""
    start, end = _locate_span(start_line, end_line).bytes
    return _LOCATE_MODULE_SOURCE.encode("utf-8")[start:end].decode("utf-8")


_LOCATE_CLASSES = {
    "src.app.Store": {"signature": "src.app.Store", "name": "Store"},
    "src.app.Store.Meta": {"signature": "src.app.Store.Meta", "name": "Meta"},
}

# ``body`` maps a local body key -> (kind, start_line, end_line); ``None`` lines mark the synthetic
# analysis vertices (@entry/@exit/@formal_in:N) that carry no span and can never contain a position.
# ``has_span`` False is a callable the analyzer emitted with no span at all — an abstract method or a
# protocol stub — whose source is unrecoverable, and which must degrade rather than raise.
_LOCATE_CALLABLE_SPECS = [
    {
        "signature": "src.app.Store.Meta.tag",
        "name": "tag",
        "start_line": 10,
        "end_line": 11,
        "class_signature": "src.app.Store.Meta",
        "has_span": True,
        "body": {"@entry": ("entry", None, None), "11:12": ("return", 11, 11)},
    },
    {
        "signature": "src.app.Store.wrap",
        "name": "wrap",
        "start_line": 13,
        "end_line": 16,
        "class_signature": "src.app.Store",
        "has_span": True,
        "body": {"16:8": ("return", 16, 16)},
    },
    {
        "signature": "src.app.Store.wrap.<locals>.inner",
        "name": "inner",
        "start_line": 14,
        "end_line": 15,
        "class_signature": None,  # a closure has no owning class
        "has_span": True,
        "body": {"15:12": ("return", 15, 15)},
    },
    {
        "signature": "src.app.Store.key",
        "name": "key",
        "start_line": 19,
        "end_line": 22,
        "class_signature": "src.app.Store",
        "has_span": True,
        "body": {
            "@entry": ("entry", None, None),
            "20:8": ("if", 20, 21),
            "21:12": ("return", 21, 21),
            "22:8": ("return", 22, 22),
            "@exit": ("exit", None, None),
        },
    },
    {
        "signature": "src.app.Store.one",
        "name": "one",
        "start_line": 26,
        "end_line": 26,
        "class_signature": "src.app.Store",
        "has_span": True,
        "body": {"26:19": ("return", 26, 26)},
    },
    {
        "signature": "src.app.Store.one.<locals>.<lambda>",
        "name": "<lambda>",
        "start_line": 26,  # same span as its owner: the width tie-break case
        "end_line": 26,
        "class_signature": None,
        "has_span": True,
        "body": {"26:34": ("return", 26, 26)},
    },
    {
        "signature": "src.app.Store.two",
        "name": "two",
        "start_line": 28,
        "end_line": 29,
        "class_signature": "src.app.Store",
        "has_span": True,
        # Both span line 29 alone: equal width, so the start column decides.
        "body": {"29:8": ("if", 29, 29), "29:14": ("return", 29, 29)},
    },
    {
        "signature": "src.app.Store.stub",
        "name": "stub",
        "start_line": 24,
        "end_line": 24,
        "class_signature": "src.app.Store",
        "has_span": False,
        "body": {},
    },
]
_LOCATE_SPEC = {c["signature"]: c for c in _LOCATE_CALLABLE_SPECS}


def _locate_callable_id(signature: str) -> str:
    """A stand-in for the analyzer's ``can://`` id. Only its shape matters here: a body node's graph
    ``id`` is ``<callable id>@<body key>``, which is what the Neo4j backend's innermost-node tie
    break splits on."""
    return f"can://python/app/{_LOCATE_MODULE_PATH}/{signature}"


# -----[ the local backend's view: a real in-memory PyApplication ]-----
def _locate_pycallable(spec: dict, **children: Any) -> PyCallable:
    return PyCallable(
        name=spec["name"],
        path=_LOCATE_MODULE_PATH,
        signature=spec["signature"],
        id=_locate_callable_id(spec["signature"]),
        span=_locate_span(spec["start_line"], spec["end_line"]) if spec["has_span"] else None,
        start_line=spec["start_line"],
        end_line=spec["end_line"],
        body={key: BodyNode(kind=kind, span=_locate_span(s, e) if s is not None else None) for key, (kind, s, e) in spec["body"].items()},
        **children,
    )


def _locate_application() -> PyApplication:
    """The fixture module as the in-process analyzer would hand it over."""
    inner = _locate_pycallable(_LOCATE_SPEC["src.app.Store.wrap.<locals>.inner"])
    lam = _locate_pycallable(_LOCATE_SPEC["src.app.Store.one.<locals>.<lambda>"])
    meta = PyClass(
        name="Meta",
        signature="src.app.Store.Meta",
        callables={"tag": _locate_pycallable(_LOCATE_SPEC["src.app.Store.Meta.tag"])},
    )
    store = PyClass(
        name="Store",
        signature="src.app.Store",
        callables={
            "wrap": _locate_pycallable(_LOCATE_SPEC["src.app.Store.wrap"], callables={"inner": inner}),
            "key": _locate_pycallable(_LOCATE_SPEC["src.app.Store.key"]),
            "one": _locate_pycallable(_LOCATE_SPEC["src.app.Store.one"], callables={"<lambda>": lam}),
            "two": _locate_pycallable(_LOCATE_SPEC["src.app.Store.two"]),
            "stub": _locate_pycallable(_LOCATE_SPEC["src.app.Store.stub"]),
        },
        types={"src.app.Store.Meta": meta},
    )
    module = PyModule(
        file_path=_LOCATE_MODULE_PATH,
        module_name="src.app",
        types={"src.app.Store": store},
        source=_LOCATE_MODULE_SOURCE,
    )
    return PyApplication(symbol_table={_LOCATE_MODULE_PATH: module})


# -----[ the Neo4j backend's view: the same module, as property maps ]-----
def _prune(props: dict) -> dict:
    """What ``codeanalyzer/neo4j/project.py``'s ``prune`` does: a ``None`` property is not written at
    all, so ``properties(n)`` simply has no such key. Body nodes with no span therefore reach the
    query with no ``start_line``, which is why the Cypher guards on ``IS NOT NULL``."""
    return {k: v for k, v in props.items() if v is not None}


# NB: no "source" key. :PyModule nodes carry file_key/module_name/content_hash/last_modified/
# file_size/_module/id and nothing else (codeanalyzer/neo4j/project.py::_module_props, and
# neo4j/schema.py declares the same set) — fabricating one here is what let the module-scope test
# pass against a property the graph never emits.
_LOCATE_MODULE_PROPS = {
    "id": f"can://{_LOCATE_MODULE_PATH}",
    "file_key": _LOCATE_MODULE_PATH,
    "module_name": "src.app",
    "content_hash": "deadbeef",
    "file_size": len(_LOCATE_MODULE_SOURCE.encode("utf-8")),
    "_module": _LOCATE_MODULE_PATH,
}


def _locate_callable_props(spec: dict) -> dict:
    return _prune(
        {
            "id": _locate_callable_id(spec["signature"]),
            "signature": spec["signature"],
            "name": spec["name"],
            "path": _LOCATE_MODULE_PATH,
            "code": _locate_code(spec["start_line"], spec["end_line"]) if spec["has_span"] else None,
            "start_line": spec["start_line"],
            "end_line": spec["end_line"],
            "_module": _LOCATE_MODULE_PATH,
        }
    )


def _locate_body_props(spec: dict) -> list[dict]:
    return [
        _prune(
            {
                "id": f"{_locate_callable_id(spec['signature'])}@{key}",
                "kind": kind,
                "start_line": s,
                "end_line": e,
                "_module": _LOCATE_MODULE_PATH,
            }
        )
        for key, (kind, s, e) in spec["body"].items()
    ]


def _locate_row(idx, module_props=None, callable_props=None, class_props=None, body_props=None) -> dict:
    return {"idx": idx, "module_props": module_props, "callable_props": callable_props, "class_props": class_props, "body_props": body_props}


def _locate_responder(query: str, params: dict) -> list[dict]:
    """Answers ``_load_module_keys``, ``PyNeo4jBackend._LOCATE_QUERY``, ``get_method_bodies`` and
    ``get_source`` for the ``py`` fixture.

    This evaluates the query's WHERE clauses rather than short-circuiting them, so the assertions it
    backs are about the query the backend actually sends: ``$prefix`` really gates the callable
    match (attach as another application and the callables disappear), a callable row repeats once
    per matching body node, and every containment decision is made on lines the way Cypher would.
    """
    in_scope = "prefix" in params and _locate_callable_id("").startswith(params["prefix"])
    if "RETURN m.file_key AS k" in query:
        return [{"k": _LOCATE_MODULE_PATH}]
    if "c.code IS NOT NULL" in query:
        # get_method_bodies (bulk, "c.signature IN $sigs") and get_source (single, "c.signature =
        # $sig") both gate on $prefix and on a real code property -- a bodyless callable (has_span
        # False, e.g. Store.stub) has none, so it is simply absent from the rows, never "".
        if not in_scope:
            return []
        if "IN $sigs" in query:
            return [
                {"signature": sig, "code": _locate_code(spec["start_line"], spec["end_line"])}
                for sig in params.get("sigs", [])
                if (spec := _LOCATE_SPEC.get(sig)) is not None and spec["has_span"]
            ]
        spec = _LOCATE_SPEC.get(params.get("sig"))
        return [{"code": _locate_code(spec["start_line"], spec["end_line"])}] if spec is not None and spec["has_span"] else []
    if "UNWIND $positions AS pos" not in query:
        return []
    rows: list[dict] = []
    for pos in params["positions"]:
        if pos["path"] != _LOCATE_MODULE_PATH:
            rows.append(_locate_row(pos["idx"]))  # no :PyModule for this file_key
            continue
        # ``OPTIONAL MATCH (c:PyCallable {_module: pos.path}) WHERE c.id STARTS WITH $prefix AND ...``
        matches = [c for c in _LOCATE_CALLABLE_SPECS if in_scope and c["start_line"] <= pos["line"] <= c["end_line"]]
        if not matches:
            rows.append(_locate_row(pos["idx"], _LOCATE_MODULE_PROPS))
            continue
        for spec in matches:
            cprops = _locate_callable_props(spec)
            clsprops = _LOCATE_CLASSES.get(spec["class_signature"]) if spec["class_signature"] else None
            bodies = [b for b in _locate_body_props(spec) if "start_line" in b and b["start_line"] <= pos["line"] <= b["end_line"]]
            for b in bodies or [None]:
                rows.append(_locate_row(pos["idx"], _LOCATE_MODULE_PROPS, cprops, clsprops, b))
    return rows


@pytest.fixture
def py(fake_driver: FakeDriver) -> PyNeo4jBackend:
    """A :class:`PyNeo4jBackend` over the fixture module above, driven by ``fake_driver`` — the same
    instance :func:`query_counter` wraps, so a test taking both fixtures counts genuine round trips
    against the backend ``py`` actually queries.
    """
    fake_driver.responder = _locate_responder
    return PyNeo4jBackend._from_driver(fake_driver, application_name="app")


@pytest.fixture
def py_local(tmp_path) -> PyCodeanalyzer:
    """A :class:`PyCodeanalyzer` over the *same* fixture module, with no analyzer run and no Neo4j.

    Built with ``object.__new__`` and a hand-assembled application (the pattern
    ``test_python_bulk_accessors.py`` uses), plus a ``project_dir`` — ``locate`` consults it to tell
    "the file exists but was not analysed" from "no such file", which is the one thing the local
    backend knows and the Neo4j backend cannot.
    """
    backend = object.__new__(PyCodeanalyzer)
    backend.application = _locate_application()
    backend.project_dir = tmp_path
    return backend


@pytest.fixture(params=["neo4j", "local"])
def py_either(request, py, py_local):
    """Both backends, for the outcome tests that must hold identically on each."""
    return py if request.param == "neo4j" else py_local


# --------------------------------------------------------------------------------------------
# Live-graph fixtures — a facade attached to a Neo4j graph loaded OUT OF BAND, plus the
# round-trip counter used to assert on what an accessor costs.
#
# The environment variable names are ``test_e2e_neo4j_live.py``'s, deliberately reused rather
# than re-invented, so one export sets up every live-graph suite in this directory. Everything
# here is strictly read-only: no test using these writes a node, relationship, or property.
# --------------------------------------------------------------------------------------------
LIVE_NEO4J_URI = os.environ.get("CLDK_TEST_NEO4J_URI", "")
LIVE_NEO4J_USER = os.environ.get("CLDK_TEST_NEO4J_USER", "neo4j")
LIVE_NEO4J_PASSWORD = os.environ.get("CLDK_TEST_NEO4J_PASSWORD", "neo4j")
LIVE_NEO4J_APP = os.environ.get("CLDK_TEST_NEO4J_APP", "odoo-slim-19")


@pytest.fixture(scope="session")
def live_analysis():
    """A ``PythonAnalysis`` facade over the live graph. Session-scoped: constructing one runs the
    schema probe, the resolution probe and a full module-key load, and repeating that per test is
    pure waste."""
    facade = CLDK.python(
        backend=Neo4jConnectionConfig(
            uri=LIVE_NEO4J_URI,
            username=LIVE_NEO4J_USER,
            password=LIVE_NEO4J_PASSWORD,
            application_name=LIVE_NEO4J_APP,
        )
    )
    yield facade
    facade.backend.close()


@pytest.fixture
def busy_callable() -> str:
    """A live-graph callable with a non-trivial data dependence, named the way a caller would.

    ``addons.account_payment.controllers.payment.PaymentPortal.invoice_transaction`` — the same
    callable ``test_resolve.py`` resolves values inside, so the addressing suite and the dataflow
    suite are talking about one piece of code. Measured on odoo-slim-19: 169 DDG edges, carrying
    all three provenances (109 ``reaching-defs``, 32 ``ssa``, 28 ``points-to``), 32 CFG edges and
    22 CDG edges — busy enough that an empty answer is a defect, small enough to assert on.
    """
    return "PaymentPortal.invoice_transaction"


@pytest.fixture
def count_round_trips():
    """Yields a helper: ``n = count_round_trips(analysis)`` wraps the backend's ``_run`` and
    returns a live ``{"c": <round trips since the wrap>}``.

    It counts genuinely executed statements — it wraps the real ``_run`` and delegates to it — so
    it cannot go green on an accessor that quietly fires thousands of queries. Every wrap is undone
    at teardown, which matters because ``live_analysis`` is session-scoped and would otherwise
    accumulate one counting layer per test.
    """
    patched: list[tuple[Any, Any]] = []

    def counted(analysis) -> dict:
        backend = analysis.backend
        original, n = backend._run, {"c": 0}

        def counting(query, **params):
            n["c"] += 1
            return original(query, **params)

        backend._run = counting
        patched.append((backend, original))
        return n

    yield counted
    for backend, original in patched:
        backend._run = original
