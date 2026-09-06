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

import pytest
from cldk import CLDK


def test_c_factory_is_gone():
    assert not hasattr(CLDK, "c")


def test_c_modules_are_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("cldk.analysis.c")
    with pytest.raises(ModuleNotFoundError):
        __import__("cldk.models.c")


def test_legacy_shim_rejects_c():
    with pytest.raises(NotImplementedError):
        CLDK(language="c").analysis(project_path=".")
