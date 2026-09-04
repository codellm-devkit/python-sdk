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


def test_locate_inside_callable(py):
    r = py.locate("src/app.py", 19)
    assert r.callable.signature == "src.app.Store.key"
    assert r.source.startswith("    def key(")  # the callable's text, not the file's
    assert r.diagnostics == []


def test_locate_module_scope(py):
    """A module top-level statement is a real position, not an absence."""
    r = py.locate("src/app.py", 1)
    assert r.callable is None
    assert r.module.path == "src/app.py"
    assert [d.code for d in r.diagnostics] == ["module_scope"]
    assert r.source  # the module's text


def test_locate_gap_between_callables(py):
    """A line between two callables must never snap to the nearest one."""
    r = py.locate("src/app.py", 17)  # blank line between wrap() and key()
    assert r.callable is None
    assert [d.code for d in r.diagnostics] == ["module_scope"]


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
