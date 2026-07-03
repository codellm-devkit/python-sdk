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

"""TypeScript test fixtures.

Also overrides the heavy, network/zip-dependent session autouse fixtures from the top-level
``tests/conftest.py`` with no-ops, so the (fully mocked) TypeScript tests run in isolation
without downloading daytrader or extracting the Java/C sample zips.
"""

import json
from pathlib import Path

import pytest
import toml


def _testing_cfg() -> dict:
    root = Path(__file__).resolve().parents[3]
    return toml.load(root / "pyproject.toml")["tool"]["cldk"]["testing"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# --- neutralize the heavy autouse fixtures from the parent conftest for this subtree ---
@pytest.fixture(scope="session", autouse=True)
def test_fixture():  # noqa: D401 - override
    yield None


@pytest.fixture(scope="session", autouse=True)
def test_fixture_pbw():  # noqa: D401 - override
    yield None


@pytest.fixture(scope="session", autouse=True)
def test_fixture_binutils():  # noqa: D401 - override
    yield None


# --- TypeScript-specific fixtures ---
@pytest.fixture(scope="session")
def typescript_application() -> Path:
    """Path to the sample TypeScript application fixture."""
    return (_repo_root() / _testing_cfg()["sample-typescript-application"]).resolve()


@pytest.fixture(scope="session")
def typescript_analysis_json() -> str:
    """The pre-computed analysis.json contents (as a JSON string) for the sample TS app."""
    path = _repo_root() / _testing_cfg()["sample-typescript-analysis-json"] / "slim" / "analysis.json"
    with open(path, encoding="utf-8") as f:
        return json.dumps(json.load(f))
