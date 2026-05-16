################################################################################
# Copyright IBM Corporation 2024
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

"""Smoke tests for the Python analysis façade.

End-to-end coverage requires running ``codeanalyzer-python`` against a real
project (creates a venv, installs dependencies). Those tests live elsewhere;
here we only verify the public API contract and the source-code-mode guard.
"""

import pytest

from cldk import CLDK
from cldk.utils.exceptions import CldkInitializationException


def test_python_analysis_rejects_source_code_mode():
    with pytest.raises(CldkInitializationException):
        CLDK(language="python").analysis(source_code="def f(): pass")


def test_python_analysis_requires_inputs():
    with pytest.raises(CldkInitializationException):
        CLDK(language="python").analysis()
