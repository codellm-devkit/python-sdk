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
