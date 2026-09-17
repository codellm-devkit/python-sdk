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

"""Duplicate qualified names must not make the whole application unloadable (#420).

A monorepo whose services vendor a shared internal library puts several copies of one package
into one analysis, so two compilation units legitimately declare one qualified name. The analyzer
emits both, with distinct ``can://`` ids. The SDK used to raise while *flattening* that tree, which
meant no query at all was available — including the overwhelming majority naming no duplicated
type. These pin the ambiguity to the queries it actually affects.
"""

import json

import pytest

from cldk.analysis.java.codeanalyzer.codeanalyzer import JCodeanalyzer
from cldk.analysis.java.neo4j import JNeo4jBackend
from cldk.models.java.models import JAnalysis
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

VENDORED = "vendored/"


def _duplicate_one_module(analysis_json: str):
    """Copy one module to a second path, keeping every name it declares and rewriting only its
    ``can://`` ids — exactly what a vendored copy of a library looks like to the analyzer."""
    doc = json.loads(analysis_json)
    table = doc["application"]["symbol_table"]
    source_key = next(k for k, m in table.items() if m.get("types"))
    # Rewriting the key inside the serialized module rewrites the module id and every type and
    # callable id beneath it in one pass, so the copy is id-distinct and name-identical.
    copy = json.loads(json.dumps(table[source_key]).replace(source_key, VENDORED + source_key))
    table[VENDORED + source_key] = copy
    # `types` is keyed by simple name; the qualified name the index keys on is package-qualified.
    module = table[source_key]
    fqn = f"{module['package']}.{next(iter(module['types']))}"
    return json.dumps(doc), source_key, fqn


@pytest.fixture
def duplicated(analysis_json):
    payload, source_key, fqn = _duplicate_one_module(analysis_json)
    return payload, source_key, fqn


def _in_memory(payload: str) -> JCodeanalyzer:
    """A JCodeanalyzer over a seeded application, bypassing the analyzer subprocess that
    ``__init__`` would otherwise drive."""
    backend = JCodeanalyzer.__new__(JCodeanalyzer)
    backend.application = JAnalysis.model_validate_json(payload).application
    backend._call_graph = None
    backend._sdg_cache = None
    backend._index()
    return backend


def _neo4j(payload: str) -> JNeo4jBackend:
    backend = JNeo4jBackend.__new__(JNeo4jBackend)
    backend.application_name = "daytrader8"
    backend.__dict__["_application"] = JAnalysis.model_validate_json(payload).application
    return backend


def test_an_application_carrying_duplicate_qualified_names_still_loads(duplicated):
    payload, _, _ = duplicated
    # Flattening must not refuse: the duplication is in the source tree, and every other name in
    # the application is perfectly answerable.
    _in_memory(payload)


def test_a_name_declared_once_answers_as_it_always_did(duplicated):
    payload, source_key, duplicated_fqn = duplicated
    backend = _in_memory(payload)
    table = json.loads(payload)["application"]["symbol_table"]
    unambiguous = next(
        f"{module['package']}.{name}"
        for key, module in table.items()
        if not key.startswith(VENDORED) and key != source_key and module.get("package")
        for name in (module.get("types") or {})
    )
    assert backend.get_class(unambiguous) is not None


def test_the_contested_name_raises_at_the_query_and_names_the_competing_files(duplicated):
    payload, source_key, duplicated_fqn = duplicated
    backend = _in_memory(payload)
    with pytest.raises(CodeanalyzerExecutionException) as excinfo:
        backend.get_class(duplicated_fqn)
    message = str(excinfo.value)
    assert duplicated_fqn in message
    # The reader needs to know WHICH copies collided, or they cannot act on it.
    assert source_key in message and VENDORED + source_key in message


def test_every_copy_stays_addressable_by_its_own_can_id(duplicated):
    payload, source_key, duplicated_fqn = duplicated
    backend = _in_memory(payload)
    # Nothing is dropped: ids are distinct per copy, so both copies' callables remain indexed.
    ids = [cid for cid in backend._callables if VENDORED + source_key in cid]
    assert ids, "the vendored copy's callables must still be reachable by id"


def test_the_neo4j_backend_agrees_with_the_in_memory_one(duplicated):
    payload, source_key, duplicated_fqn = duplicated
    backend = _neo4j(payload)
    # Building the index must not raise here either, and the same query must fail the same way.
    backend._idx
    with pytest.raises(CodeanalyzerExecutionException):
        backend.get_class(duplicated_fqn)
