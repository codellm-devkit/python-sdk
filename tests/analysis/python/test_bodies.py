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

"""Task 7: ``get_source()`` and the #301 omission rule for ``get_method_bodies``.

``get_source(node_id)`` generalises body access below callable granularity — the id is either a
callable's signature or ``"<signature>@<body key>"`` for one of its body nodes, exactly the string
:attr:`~cldk.analysis.commons.results.LocateResult.node_id` hands back alongside ``node``. The two
backends diverge in what they can answer: the local backend holds the module's real text and byte
offsets for every node, while the graph only precomputes ``:PyCallable.code`` — nothing below that
granularity has a text property to slice, so the Neo4j backend raises rather than substituting the
enclosing callable's (too much) text.

The #301 rule (Python's twin of TypeScript's #298): a callable the analyzer emitted with no
recoverable source contributes no entry to ``get_method_bodies`` — never an empty string standing
in for "absent".
"""

import pytest

from cldk.analysis.python.codeanalyzer.codeanalyzer import PyCodeanalyzer
from cldk.models.python import PyApplication, PyCallable, PyModule, Span
from tests.analysis.python.conftest import _locate_code


# ================================================================================================
# get_source() — callable granularity, both backends.
# ================================================================================================
def test_get_source_of_a_callable(py_either):
    assert py_either.get_source("src.app.Store.key") == _locate_code(19, 22)


def test_get_source_of_an_unknown_callable_raises(py_either):
    with pytest.raises(KeyError):
        py_either.get_source("src.app.NoSuchCallable")


def test_body_not_truncated():
    """Spec §10 DoD: a callable long enough to matter (well over a thousand lines) returns its
    whole body, not a truncated prefix. Local backend only -- ``_slice``'s UTF-8 byte-offset
    slicing (see ``codeanalyzer.py``) has no length cap, but this pins that down as a standing
    guarantee rather than an accident of the implementation.
    """
    n_lines = 1500
    body = "".join(f"    x{i} = {i}\n" for i in range(n_lines))
    source = "def big():\n" + body
    span = Span(start=(1, 0), end=(n_lines + 1, 0), bytes=(0, len(source.encode("utf-8"))))

    big = PyCallable(name="big", path="pkg/mod.py", signature="pkg.mod.big", span=span)
    module = PyModule(file_path="pkg/mod.py", module_name="pkg.mod", source=source, functions={"big": big})
    app = PyApplication(symbol_table={"pkg/mod.py": module})

    backend = object.__new__(PyCodeanalyzer)
    backend.application = app
    backend.call_graph = None

    got = backend.get_source("pkg.mod.big")
    assert got == source
    assert got.count("\n") > 1000
    assert backend.get_method_bodies(["pkg.mod.big"])["pkg.mod.big"] == source


# ================================================================================================
# get_source() — body-node granularity. Only the local backend can answer; the graph structurally
# cannot (see PyNeo4jBackend.get_source's docstring), and must say so rather than fake it.
# ================================================================================================
def test_get_source_of_a_body_node_local(py_local):
    """Line 11 (``Store.Meta.tag``'s body) is the fixture's non-ASCII line — byte-offset slicing is
    exactly what would break silently on a naive character slice.

    The id is ``<callable.id>@<body key>``, not ``<callable.signature>@<body key>``: #320 — the
    emitter mints ``:PyBodyNode.id`` from the callable's ``can://`` id, so composing from
    ``signature`` produced a string that named nothing in the graph.
    """
    r = py_local.locate("src/app.py", 11)
    assert r.node_id == "can://python/app/src/app.py/src.app.Store.Meta.tag@11:12"
    assert py_local.get_source(r.node_id) == _locate_code(11, 11)
    assert "café" in py_local.get_source(r.node_id)


def test_get_source_of_a_body_node_over_neo4j_raises_not_implemented(py):
    with pytest.raises(NotImplementedError):
        py.get_source("src.app.Store.Meta.tag@11:12")


def test_get_source_of_an_unknown_body_key_raises_locally(py_local):
    with pytest.raises(KeyError):
        py_local.get_source("src.app.Store.key@no-such-key")


# ================================================================================================
# #301 — a bodyless callable contributes no entry to get_method_bodies, on both backends.
# ================================================================================================
def test_bodyless_callables_are_omitted_from_get_method_bodies(py_either):
    """``Store.stub`` has no span (an abstract/protocol stub); ``Store.key`` has a real body.
    Asking for both must come back with ``key`` only — never an empty string standing in for
    ``stub``'s absent source."""
    bodies = py_either.get_method_bodies(["src.app.Store.stub", "src.app.Store.key"])
    assert "src.app.Store.stub" not in bodies
    assert bodies["src.app.Store.key"] == _locate_code(19, 22)
