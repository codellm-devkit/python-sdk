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

"""Miss-path unit tests for JNeo4jBackend lookups (#248).

These do not need a live Neo4j server: :class:`JNeo4jBackend` only needs ``self.application``
populated to answer ``get_class``/``get_method``/``get_java_file``/``get_method_parameters`` (see
``get_symbol_table`` -> ``self.application.symbol_table``), so we bypass ``__init__`` (which opens a
driver connection) and seed ``.application`` directly from the same ``analysis.json`` fixture the
in-memory :class:`JCodeanalyzer` tests use — its shape is exactly the ``JApplication`` constructor's
kwargs (mirrors ``JCodeanalyzer._init_japplication``).
"""

import json

from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java.models import JApplication, JCallable, JType

_LOG_CLASS = "com.ibm.websphere.samples.daytrader.util.Log"
_LOG_TRACE_METHOD = "trace(java.lang.String)"


def _backend_from_analysis_json(analysis_json: str) -> JNeo4jBackend:
    backend = JNeo4jBackend.__new__(JNeo4jBackend)
    backend.application = JApplication(**json.loads(analysis_json))
    backend.analysis_level = "call_graph" if backend.application.call_graph else "symbol_table"
    backend.call_graph = None
    return backend


def test_get_class_miss_returns_none(analysis_json):
    backend = _backend_from_analysis_json(analysis_json)
    assert backend.get_class("com.example.NoSuchClass") is None


def test_get_method_miss_returns_none(analysis_json):
    backend = _backend_from_analysis_json(analysis_json)
    # Known class, typo'd signature.
    assert backend.get_method(_LOG_CLASS, "noSuchMethod()") is None
    # Unknown class altogether.
    assert backend.get_method("com.example.NoSuchClass", _LOG_TRACE_METHOD) is None


def test_get_java_file_miss_returns_none(analysis_json):
    backend = _backend_from_analysis_json(analysis_json)
    assert backend.get_java_file("com.example.NoSuchClass") is None


def test_get_method_parameters_miss_returns_empty_list(analysis_json):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'parameters'."""
    backend = _backend_from_analysis_json(analysis_json)
    assert backend.get_method_parameters(_LOG_CLASS, "noSuchMethod()") == []
    assert backend.get_method_parameters("com.example.NoSuchClass", _LOG_TRACE_METHOD) == []


def test_get_comments_in_a_method_miss_returns_empty_list(analysis_json):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'comments'."""
    backend = _backend_from_analysis_json(analysis_json)
    assert backend.get_comments_in_a_method(_LOG_CLASS, "noSuchMethod()") == []


def test_get_comments_in_a_class_miss_returns_empty_list(analysis_json):
    """Before the fix: AttributeError: 'NoneType' object has no attribute 'comments'."""
    backend = _backend_from_analysis_json(analysis_json)
    assert backend.get_comments_in_a_class("com.example.NoSuchClass") == []


def test_call_graph_target_method_miss_mid_construction_no_crash(analysis_json):
    """A get_method miss for the *target* method of a symbol-table call graph must not crash.

    Exercises JNeo4jBackend.__raw_call_graph_using_symbol_table_target_method, reached through the
    public get_all_callers(using_symbol_table=True) path. Mirrors the JCodeanalyzer fix (#248).
    """
    backend = _backend_from_analysis_json(analysis_json)

    result = backend.get_all_callers(
        target_class_name=_LOG_CLASS,
        target_method_signature="noSuchMethod()",
        using_symbol_table=True,
    )
    assert result == {}


def test_call_graph_source_method_miss_mid_construction_no_crash(analysis_json):
    """A get_method miss for a *candidate source* method mid-construction must be skipped, not crash.

    Makes a single (class, signature) pair momentarily miss while the target method is real,
    simulating a symbol table that disagrees with itself mid-construction.
    """
    backend = _backend_from_analysis_json(analysis_json)
    original_get_method = backend.get_method
    flaky_class, flaky_signature = _LOG_CLASS, "log(java.lang.String)"

    def flaky_get_method(qualified_class_name, method_signature):
        if qualified_class_name == flaky_class and method_signature == flaky_signature:
            return None
        return original_get_method(qualified_class_name, method_signature)

    backend.get_method = flaky_get_method
    try:
        result = backend.get_all_callers(
            target_class_name=_LOG_CLASS,
            target_method_signature=_LOG_TRACE_METHOD,
            using_symbol_table=True,
        )
    finally:
        backend.get_method = original_get_method

    assert isinstance(result, dict)


def test_get_class_and_method_hit_behavior_unchanged(analysis_json):
    """Sanity: the miss-path fix must not change hit behavior."""
    backend = _backend_from_analysis_json(analysis_json)

    the_class = backend.get_class(_LOG_CLASS)
    assert the_class is not None
    assert isinstance(the_class, JType)

    the_method = backend.get_method(_LOG_CLASS, _LOG_TRACE_METHOD)
    assert the_method is not None
    assert isinstance(the_method, JCallable)
    assert the_method.declaration == "public static void trace(String message)"

    the_method_parameters = backend.get_method_parameters(_LOG_CLASS, _LOG_TRACE_METHOD)
    assert len(the_method_parameters) == 1
