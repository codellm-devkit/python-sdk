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
