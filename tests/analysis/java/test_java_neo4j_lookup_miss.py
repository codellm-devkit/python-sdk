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

"""Miss-path unit tests for JNeo4jBackend lookups (#248), on the schema-v2 seed.

These need no live Neo4j: every lookup here is answered from the reconstructed
:class:`JApplication` and its index, so the test seeds ``.application`` directly from the same
codeanalyzer-java 3.0.1 fixture the in-memory :class:`JCodeanalyzer` tests use, bypassing the
attach (which opens a driver and probes the graph). ``application`` is a ``cached_property``, so
assigning it is the documented seam -- what a reconstruction would have produced, without one.
"""

import pytest

from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java.models import JAnalysis, JCallable, JType
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

_LOG_CLASS = "com.ibm.websphere.samples.daytrader.util.Log"
_LOG_TRACE_METHOD = "trace(java.lang.String)"


@pytest.fixture
def backend(analysis_json) -> JNeo4jBackend:
    backend = JNeo4jBackend.__new__(JNeo4jBackend)
    backend.application_name = "daytrader8"
    backend.application = JAnalysis.model_validate_json(analysis_json).application
    return backend


def test_get_class_miss_returns_none(backend):
    assert backend.get_class("com.example.NoSuchClass") is None


def test_get_method_miss_returns_none(backend):
    # Known class, typo'd signature.
    assert backend.get_method(_LOG_CLASS, "noSuchMethod()") is None
    # Unknown class altogether.
    assert backend.get_method("com.example.NoSuchClass", _LOG_TRACE_METHOD) is None


def test_get_java_file_miss_returns_none(backend):
    assert backend.get_java_file("com.example.NoSuchClass") is None


def test_get_method_parameters_miss_returns_empty_list(backend):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'parameters'."""
    assert backend.get_method_parameters(_LOG_CLASS, "noSuchMethod()") == []
    assert backend.get_method_parameters("com.example.NoSuchClass", _LOG_TRACE_METHOD) == []


def test_get_comments_in_a_method_miss_returns_empty_list(backend):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'comments'."""
    assert backend.get_comments_in_a_method(_LOG_CLASS, "noSuchMethod()") == []


def test_get_comments_in_a_class_miss_returns_empty_list(backend):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'comments'."""
    assert backend.get_comments_in_a_class("com.example.NoSuchClass") == []


def test_call_graph_target_method_miss_mid_construction_no_crash(backend):
    """A get_method miss for the *target* method of a symbol-table call graph must not crash.

    Exercises ``_st_edges_into`` through the public ``get_all_callers(using_symbol_table=True)``
    path. Mirrors the JCodeanalyzer fix (#248).
    """
    assert backend.get_all_callers(target_class_name=_LOG_CLASS, target_method_signature="noSuchMethod()", using_symbol_table=True) == {}


def test_call_graph_source_method_miss_mid_construction_no_crash(backend):
    """A get_method miss for a *candidate source* method mid-construction must be skipped, not
    crash: one (class, signature) pair momentarily misses while the target method is real."""
    original_get_method = backend.get_method
    flaky = (_LOG_CLASS, "log(java.lang.String)")

    def flaky_get_method(qualified_class_name, qualified_method_name):
        if (qualified_class_name, qualified_method_name) == flaky:
            return None
        return original_get_method(qualified_class_name, qualified_method_name)

    backend.get_method = flaky_get_method
    try:
        result = backend.get_all_callers(target_class_name=_LOG_CLASS, target_method_signature=_LOG_TRACE_METHOD, using_symbol_table=True)
    finally:
        del backend.get_method
    assert isinstance(result, dict)


def test_get_class_and_method_hit_behavior_unchanged(backend):
    """Sanity: the miss-path handling must not change hit behaviour."""
    the_class = backend.get_class(_LOG_CLASS)
    assert isinstance(the_class, JType)

    the_method = backend.get_method(_LOG_CLASS, _LOG_TRACE_METHOD)
    assert isinstance(the_method, JCallable)
    assert the_method.declaration == "public static void trace(String message)"
    assert len(backend.get_method_parameters(_LOG_CLASS, _LOG_TRACE_METHOD)) == 1


def test_comment_accessors_the_projection_cannot_serve_raise_naming_the_gap(backend):
    """D7: file-level comments are not projected at all, so an empty list would read as "this file
    has no comments". Both file-keyed accessors raise instead, naming what is missing and what to
    read instead -- and never an id (E6)."""
    for call in (lambda: backend.get_all_comments(), lambda: backend.get_comment_in_file("does/not/matter.java")):
        with pytest.raises(CodeanalyzerExecutionException, match="carries no comment nodes for application 'daytrader8'") as e:
            call()
        assert "get_all_docstrings()" in str(e.value) and "can://" not in str(e.value)


def test_crud_accessors_raise_naming_the_upstream_issue(backend):
    """J-4: the same refusal on both backends, from the one shared message."""
    for name in ("get_all_crud_operations", "get_all_create_operations", "get_all_read_operations", "get_all_update_operations", "get_all_delete_operations"):
        with pytest.raises(CodeanalyzerExecutionException, match="codeanalyzer-java#187"):
            getattr(backend, name)()
