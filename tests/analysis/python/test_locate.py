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

"""Task 6: ``locate()`` / ``locate_many()`` — file:line to enclosing callable.

The single most-needed query: a scanner alert arrives as ``file:line`` and the caller needs the
enclosing callable *and its source*. The three outcomes below must stay distinguishable — an
ambiguous empty is a defect (see ``cldk/analysis/commons/results.py``).
"""

from cldk.analysis.python.neo4j import PyNeo4jBackend


def test_locate_inside_callable(py):
    r = py.locate("src/app.py", 19)
    assert r.callable.signature == "src.app.Store.key"
    assert r.source.startswith("    def key(")  # the callable's text, not the file's
    assert r.diagnostics == []


def test_locate_module_scope(py):
    """A module top-level statement is a real position, not an absence.

    The original ``assert r.source`` here was wrong and only passed because the fixture fabricated a
    ``source`` property on the module node. ``:PyModule`` has no such property (see
    ``test_locate_parity_documented_module_source_divergence``), so over Neo4j the honest answer is
    an empty source that says *why* it is empty — never the module text invented from somewhere.
    """
    r = py.locate("src/app.py", 1)
    assert r.callable is None
    assert r.module.path == "src/app.py"
    assert "module_scope" in [d.code for d in r.diagnostics]
    assert r.source == ""
    assert "module_source_unavailable" in [d.code for d in r.diagnostics]


def test_locate_gap_between_callables(py):
    """A line between two callables must never snap to the nearest one.

    Membership rather than list equality: this backend's module-scope result now also carries
    ``module_source_unavailable`` (it cannot supply the module text). What this test is about is
    that the position does not snap to a neighbouring callable, so it asserts that directly.
    """
    r = py.locate("src/app.py", 17)  # blank line between wrap() and key()
    assert r.callable is None
    assert r.type is None
    assert "module_scope" in [d.code for d in r.diagnostics]
    assert "file_not_in_graph" not in [d.code for d in r.diagnostics]


def test_locate_unanalysed_file(py):
    r = py.locate("test/conftest.py", 3)
    assert [d.code for d in r.diagnostics] == ["file_not_in_graph"]
    assert r.callable is None


def test_locate_many_is_one_round_trip(py, query_counter):
    before = query_counter.count
    rs = py.locate_many([("src/app.py", 19), ("src/app.py", 22), ("src/app.py", 1)])
    assert len(rs) == 3
    assert query_counter.count - before == 1


# ================================================================================================
# The same four outcomes, on the LOCAL backend.
#
# The five tests above take ``py``, which is a PyNeo4jBackend — so PyCodeanalyzer's ``_locate_one``
# and the recursive ``_find_innermost``/``_find_body_node`` walk had no coverage at all. These run
# the identical positions in-process, and ``test_locate_parity_*`` below pins the two together.
# ================================================================================================
def test_locate_inside_callable_local(py_local):
    r = py_local.locate("src/app.py", 19)
    assert r.callable.signature == "src.app.Store.key"
    assert r.source.startswith("    def key(")
    assert r.diagnostics == []


def test_locate_module_scope_local(py_local):
    """The local backend has the module text, so module scope carries no second diagnostic."""
    r = py_local.locate("src/app.py", 1)
    assert r.callable is None
    assert r.module.path == "src/app.py"
    assert [d.code for d in r.diagnostics] == ["module_scope"]
    assert r.source.startswith('"""Store module."""')


def test_locate_gap_between_callables_local(py_local):
    r = py_local.locate("src/app.py", 17)
    assert r.callable is None
    assert [d.code for d in r.diagnostics] == ["module_scope"]


def test_locate_unanalysed_file_local(py_local):
    r = py_local.locate("test/conftest.py", 3)
    assert [d.code for d in r.diagnostics] == ["file_not_in_graph"]
    assert r.callable is None


def test_locate_many_local_is_input_order(py_local):
    rs = py_local.locate_many([("src/app.py", 19), ("src/app.py", 1), ("src/app.py", 11)])
    assert [r.callable.signature if r.callable else None for r in rs] == ["src.app.Store.key", None, "src.app.Store.Meta.tag"]


# ================================================================================================
# What ``_find_innermost`` has to get right.
# ================================================================================================
def test_locate_closure_inside_method_wins(py_either):
    """A closure nested inside a method is the innermost callable — and has no owning class."""
    r = py_either.locate("src/app.py", 15)
    assert r.callable.signature == "src.app.Store.wrap.<locals>.inner"
    assert r.callable.class_signature is None
    assert r.type is None


def test_locate_enclosing_method_when_outside_the_closure(py_either):
    """One line further out, the method wins — the closure must not swallow its owner's lines."""
    r = py_either.locate("src/app.py", 16)
    assert r.callable.signature == "src.app.Store.wrap"
    assert r.type.signature == "src.app.Store"


def test_locate_class_nested_in_class(py_either):
    """The owning type is the innermost class, not the outer one."""
    r = py_either.locate("src/app.py", 11)
    assert r.callable.signature == "src.app.Store.Meta.tag"
    assert r.type.signature == "src.app.Store.Meta"
    assert r.callable.class_signature == "src.app.Store.Meta"


def test_locate_callable_with_absent_span_does_not_raise(py_either):
    """An abstract method / protocol stub has no span: the callable still resolves, source is ""."""
    r = py_either.locate("src/app.py", 24)
    assert r.callable.signature == "src.app.Store.stub"
    assert r.source == ""
    assert r.diagnostics == []


# ================================================================================================
# LocateResult.node — the innermost body node.
# ================================================================================================
def test_locate_innermost_body_node(py_either):
    """Line 21 sits in both the ``if`` (20-21) and its ``return`` (21): the return must win."""
    r = py_either.locate("src/app.py", 21)
    assert r.callable.signature == "src.app.Store.key"
    assert r.node is not None
    assert r.node.kind == "return"
    assert (r.node.span.start[0], r.node.span.end[0]) == (21, 21)


def test_locate_body_node_when_only_the_outer_statement_contains_the_line(py_either):
    r = py_either.locate("src/app.py", 20)
    assert r.node.kind == "if"
    assert (r.node.span.start[0], r.node.span.end[0]) == (20, 21)


def test_locate_node_is_none_on_the_def_line(py_either):
    """No body node contains the ``def`` line. That is a real outcome, not an error: the callable
    is still resolved and there are no diagnostics."""
    r = py_either.locate("src/app.py", 19)
    assert r.node is None
    assert r.callable.signature == "src.app.Store.key"
    assert r.diagnostics == []


def test_locate_spanless_body_node_never_matches(py_either):
    """``@entry``/``@exit`` carry no span, so they can never contain a position — a missing span
    must not read as "contains everything". ``key`` has both, and every position inside it either
    resolves to a real statement node or to None."""
    kinds = {py_either.locate("src/app.py", line).node.kind if py_either.locate("src/app.py", line).node else None for line in (19, 20, 21, 22)}
    assert kinds == {None, "if", "return"}


def test_locate_body_node_survives_a_callable_with_no_body(py_either):
    """``stub`` has an empty ``body``/no PY_HAS_BODY_NODE edges: node is None, not an exception."""
    assert py_either.locate("src/app.py", 24).node is None


# ================================================================================================
# Parity — the two backends on the same position, including where they legitimately differ.
# ================================================================================================
def test_locate_parity_inside_callable(py, py_local):
    a, b = py.locate("src/app.py", 21), py_local.locate("src/app.py", 21)
    assert a.callable == b.callable
    assert a.type == b.type
    assert a.module == b.module
    assert a.source == b.source  # the callable's text: `code` property vs `span.bytes` slice
    assert [d.code for d in a.diagnostics] == [d.code for d in b.diagnostics] == []
    # ...and the same innermost body node, compared on the fields the graph actually carries.
    assert (a.node.kind, a.node.span.start[0], a.node.span.end[0]) == (b.node.kind, b.node.span.start[0], b.node.span.end[0])


def test_locate_parity_innermost_node_agrees_at_every_position(py, py_local):
    def probe(backend, line):
        r = backend.locate("src/app.py", line)
        node = r.node
        return (
            r.callable.signature if r.callable else None,
            r.type.signature if r.type else None,
            (node.kind, node.span.start[0], node.span.end[0]) if node else None,
        )

    lines = [1, 11, 15, 16, 17, 19, 20, 21, 22, 24]
    assert [probe(py, line) for line in lines] == [probe(py_local, line) for line in lines]


def test_locate_parity_documented_span_divergence(py, py_local):
    """``span``'s meaningful fields differ *by design*, and the docstring now says which.

    The local backend returns the analyzer's real Span (columns + UTF-8 byte offsets into the
    module source); the graph projects only ``start_line``/``end_line`` on ``:PyCallable``, so the
    Neo4j span's columns and ``bytes`` are 0 placeholders. Lines agree; nothing else claims to.
    """
    a, b = py.locate("src/app.py", 21), py_local.locate("src/app.py", 21)
    assert (a.span.start[0], a.span.end[0]) == (b.span.start[0], b.span.end[0]) == (19, 22)
    assert a.span.bytes == (0, 0) and a.span.start[1] == 0 and a.span.end[1] == 0
    assert b.span.bytes != (0, 0)
    assert b.span.end[1] > 0
    # The byte offsets are real: slicing the module source by them reproduces `source`.
    assert py_local.application.symbol_table["src/app.py"].source.encode("utf-8")[slice(*b.span.bytes)].decode("utf-8") == b.source


def test_locate_parity_documented_module_source_divergence(py, py_local):
    """The one outcome the two backends cannot agree on, stated rather than papered over.

    :PyModule nodes carry no ``source`` property (``codeanalyzer/neo4j/project.py::_module_props``
    writes id/file_key/module_name/content_hash/last_modified/file_size/_module, and
    ``neo4j/schema.py`` declares the same set), and this backend has no project checkout to read
    the file from — so it returns "" plus ``module_source_unavailable`` instead of inventing text.
    """
    a, b = py.locate("src/app.py", 1), py_local.locate("src/app.py", 1)
    assert a.callable is None and b.callable is None
    assert a.module == b.module
    assert [d.code for d in a.diagnostics] == ["module_scope", "module_source_unavailable"]
    assert [d.code for d in b.diagnostics] == ["module_scope"]
    assert a.source == ""
    assert b.source == py_local.application.symbol_table["src/app.py"].source
    # The empty source says *why* it is empty (D7) rather than looking like a negative result.
    assert "does not carry module text" in next(d.message for d in a.diagnostics if d.code == "module_source_unavailable")


# ================================================================================================
# Application scope, path normalisation, and the file_not_in_graph distinction.
# ================================================================================================
def test_locate_query_is_scoped_to_the_application(py, fake_driver):
    """Every other query in neo4j_backend.py constrains ``.id STARTS WITH $prefix``; this one
    narrows further, to the position's own module: ``module_id(app, key) + "/"`` per position, so a
    same-valued file_key from another application in the same database cannot win, and neither can
    a module whose key merely extends this one's spelling."""
    py.locate("src/app.py", 21)
    statement = next(s for s in fake_driver.statements if "UNWIND $positions AS pos" in s)
    assert "c.id STARTS WITH pos.module_prefix" in statement
    assert "_module" not in statement, "the graph stores no _module property to match on"


def test_locate_names_pycannode_only_on_a_graph_that_has_it(py, fake_driver):
    """The per-module prefix seeks the ``:PyCanNode(id)`` range index a 1.4.1 graph carries
    (measured on odoo: 40 positions 399 -> 89 ms); a 1.4.0 graph has no such label and naming it
    would match nothing. Both spellings are pinned because the swap is a string edit on the
    statement -- a renamed anchor would silently lose the seek, never the answer."""
    py.locate("src/app.py", 21)
    assert "OPTIONAL MATCH (c:PyCallable:PyCanNode) " in next(s for s in fake_driver.statements if "UNWIND $positions AS pos" in s)

    fake_driver.statements.clear()
    fake_driver.analyzer_version = "1.4.0"
    old = PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    assert old.locate("src/app.py", 21).callable is not None, "the 1.4.0 spelling must still answer"
    statement = next(s for s in fake_driver.statements if "UNWIND $positions AS pos" in s)
    assert "OPTIONAL MATCH (c:PyCallable) " in statement and "PyCanNode" not in statement


def test_locate_scope_is_actually_honoured(py):
    """Not just present in the text: attach as another application and no callable matches."""
    py.application_name = "some_other_application"
    r = py.locate("src/app.py", 21)
    assert r.callable is None
    assert "module_scope" in [d.code for d in r.diagnostics]


def test_locate_normalises_the_path(py_either):
    """A scanner prints ``./src/app.py`` or an absolute path; neither is the symbol-table key."""
    for path in ("./src/app.py", "src/./app.py", "/home/ci/checkout/src/app.py"):
        r = py_either.locate(path, 21)
        assert r.callable is not None, path
        assert r.callable.signature == "src.app.Store.key"
        assert r.module.path == "src/app.py"


def test_locate_not_in_graph_message_says_what_the_backend_knows(py, py_local, tmp_path):
    """``file_not_in_graph`` can't tell "not analysed" from "not on disk" — but the local backend
    can look, and the Neo4j backend says plainly that it cannot."""
    (tmp_path / "present.py").write_text("x = 1\n")
    present = py_local.locate("present.py", 1).diagnostics[0].message
    absent = py_local.locate("gone.py", 1).diagnostics[0].message
    assert "exists but no analysed module covers it" in present
    assert "no such file" in absent
    assert "no access to the project sources" in py.locate("present.py", 1).diagnostics[0].message


# ================================================================================================
# Equal-width ties. Both backends rank by line span, and lines are all the graph carries — so when
# two callables (or two body nodes) span exactly the same lines, width cannot decide and something
# else must, identically on both sides. Locally an unbroken tie keeps whichever the walk met first
# (the *owner*, i.e. the wrong one); over Neo4j it keeps whichever row Cypher returned first, which
# is not even deterministic. That is a parity divergence of the same class as the fabricated module
# source, so it is pinned here rather than left to the first `def f(): return lambda: 1` in the wild.
# ================================================================================================
def test_locate_innermost_callable_wins_an_equal_width_tie(py_either):
    """``def one(self): return lambda: 2`` — the lambda and its owner span the same single line."""
    r = py_either.locate("src/app.py", 26)
    assert r.callable.signature == "src.app.Store.one.<locals>.<lambda>"
    assert r.callable.class_signature is None  # a lambda has no owning class
    assert r.type is None


def test_locate_innermost_body_node_wins_an_equal_width_tie(py_either):
    """``if x: return x`` — the ``if`` and the ``return`` both span line 29 alone, so the tie is
    broken on the key's start column: the ``return`` at col 14 is nested inside the ``if`` at col 8."""
    r = py_either.locate("src/app.py", 29)
    assert r.callable.signature == "src.app.Store.two"
    assert r.node.kind == "return"
    assert (r.node.span.start[0], r.node.span.end[0]) == (29, 29)


def test_locate_parity_equal_width_ties_agree(py, py_local):
    """The two backends must resolve both ties to the same node, not merely to *a* node."""
    for line in (26, 29):
        a, b = py.locate("src/app.py", line), py_local.locate("src/app.py", line)
        assert a.callable == b.callable, line
        assert (a.node is None) == (b.node is None), line
        if a.node is not None:
            assert (a.node.kind, a.node.span.start[0]) == (b.node.kind, b.node.span.start[0]), line


def test_locate_body_key_column_is_parsed_not_string_compared():
    """The keys are ``<line>:<col>``, sometimes suffixed. Comparing them as strings would order
    ``"29:10"`` before ``"29:4"`` and pick the *outer* statement; the column is parsed as an int."""
    from cldk.analysis.python.backend import body_key_column

    assert body_key_column("29:4") == 4
    assert body_key_column("29:10") == 10
    assert body_key_column("22:8/actual_in:0") == 8
    assert body_key_column("@entry") == -1  # synthetic vertices carry no column
    assert "29:10" < "29:4"  # ...which is why the string comparison had to go
    assert body_key_column("29:10") > body_key_column("29:4")
