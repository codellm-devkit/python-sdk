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
    r = py.locate("src/app.py", 17)  # blank line between __init__ and key
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
