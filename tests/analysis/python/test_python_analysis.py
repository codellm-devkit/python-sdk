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


def test_use_codeql_forwarded_through_facade(monkeypatch, tmp_path):
    """Regression: CLDK.analysis() must forward use_codeql to the backend.

    Previously the façade dropped the flag, making CodeQL-augmented edges
    unreachable through the public API (only Jedi-resolved edges returned).
    """
    captured = {}

    class FakeBackend:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "cldk.analysis.python.python_analysis.PyCodeanalyzer", FakeBackend
    )

    CLDK(language="python").analysis(project_path=tmp_path, use_codeql=False)
    assert captured["use_codeql"] is False

    # CodeQL is the default; the façade must not silently drop it.
    captured.clear()
    CLDK(language="python").analysis(project_path=tmp_path)
    assert captured["use_codeql"] is True
