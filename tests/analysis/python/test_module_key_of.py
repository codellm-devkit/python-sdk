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

"""``module_key_of``: the repo-relative module key a ``can://`` id embeds, recovered by verified
longest match against the application's known keys -- never by splitting on ``.py/``."""

import pytest

from cldk.analysis.python.neo4j.reconstruct import module_key_of

APP = "can://python/app/"


def test_module_key_is_the_id_segment_up_to_the_first_py_boundary():
    known = {"addons/account/models/account_move.py"}
    node_id = "can://python/odoo-slim-19/addons/account/models/account_move.py/AccountMove/write(self,vals)"
    assert module_key_of(node_id, "can://python/odoo-slim-19/", known) == "addons/account/models/account_move.py"


def test_a_directory_named_like_a_module_cannot_mis_key():
    known = {"pkg/x.py/real.py"}
    assert module_key_of("can://python/app/pkg/x.py/real.py/f()", APP, known) == "pkg/x.py/real.py"


def test_a_key_outside_the_application_raises_rather_than_guesses():
    with pytest.raises(KeyError):
        module_key_of("can://python/app/gone.py/f()", APP, {"kept.py"})


def test_a_ghost_id_has_no_module_key():
    with pytest.raises(KeyError):
        module_key_of("can://python/app/@external/os/path", APP, {"a.py"})


def test_a_body_node_id_keys_to_its_callables_module():
    """A body node's id is its callable's id plus ``@<key>``, and the key itself may contain
    ``/`` (``@15:2/actual_in:0``); the module key is still the verified prefix, so a slice row can
    derive its file from the body node's own ``ref``."""
    known = {"pkg/a.py", "pkg"}
    assert module_key_of("can://python/app/pkg/a.py/f(x)@15:2/actual_in:0", APP, known) == "pkg/a.py"


def test_the_module_id_itself_keys_to_its_own_key():
    assert module_key_of("can://python/app/pkg/a.py", APP, {"pkg/a.py"}) == "pkg/a.py"


def test_an_id_under_another_application_raises():
    with pytest.raises(KeyError):
        module_key_of("can://python/app-b/pkg/a.py/f()", APP, {"pkg/a.py"})


# ----------------------------------------------------------------------------------------------
# The backend's use of it: the key set is read once at attach, from a graph the SDK does not own.
# ----------------------------------------------------------------------------------------------
from cldk.analysis.python.neo4j.neo4j_backend import PyNeo4jBackend  # noqa: E402
from cldk.utils.exceptions import CodeanalyzerExecutionException  # noqa: E402


def _overview_row(node_id: str) -> dict:
    return {"id": node_id, "signature": node_id.rsplit("/", 1)[1], "name": "f", "decorators": None, "start_line": 1, "end_line": 2, "class_signature": None}


def test_a_module_added_since_attach_is_found_after_one_reload_and_a_foreign_id_is_a_typed_error(fake_driver):
    """A re-emit can add a module after attach; its callables must not take down a whole-application
    answer. One miss reloads the key set once and retries. A second miss is a real defect, raised
    as an SDK exception that names the key count and never the ``can://`` id (E6)."""
    graph = {"modules": ["a.py"], "callables": ["can://python/app/a.py/f"]}

    def responder(query, params):
        if "RETURN m.file_key AS k" in query:
            return [{"k": k} for k in graph["modules"]]
        if "OPTIONAL MATCH (owner:PyClass)-[:PY_HAS_METHOD]->(c)" in query:
            return [_overview_row(i) for i in graph["callables"]]
        return []

    fake_driver.responder = responder
    backend = PyNeo4jBackend._from_driver(fake_driver, application_name="app")
    loads = lambda: sum("RETURN m.file_key AS k" in s for s in fake_driver.statements)  # noqa: E731
    assert loads() == 1

    graph["modules"].append("b.py")  # the graph moved under us
    graph["callables"].append("can://python/app/b.py/g")
    assert {o.path for o in backend.get_callables_overview()} == {"a.py", "b.py"}
    assert loads() == 2, "one reload, on the first miss"

    graph["callables"].append("can://python/app/zzz.py/h")  # no such module, before or after reload
    with pytest.raises(CodeanalyzerExecutionException) as e:
        backend.get_callables_overview()
    assert loads() == 3
    assert "changed since attach" in str(e.value) and "2 module keys" in str(e.value)
    assert "can://" not in str(e.value) and "zzz" not in str(e.value)

