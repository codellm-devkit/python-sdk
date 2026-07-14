################################################################################
# Copyright IBM Corporation 2024, 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Regression tests for #231: reconstructing External (phantom) nodes from Neo4j
properties must conform to the slim TSExternalSymbol model (name + module only) —
the graph node legitimately carries signature/kind properties, and the
reconstructor must not forward them into an extra='forbid' model."""

from unittest.mock import patch

from cldk.analysis.typescript.neo4j import reconstruct as R
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.models.typescript import TSExternalSymbol


def test_external_reconstructs_from_full_graph_props():
    """A real External node's property bag (signature, kind, _module included) reconstructs."""
    sym = R.external(
        {
            "signature": "commander.parse",
            "name": "parse",
            "module": "commander",
            "kind": "external",
            "_module": "app",
        }
    )
    assert isinstance(sym, TSExternalSymbol)
    assert sym.name == "parse"
    assert sym.module == "commander"


def test_external_reconstructs_with_empty_signature():
    """The reported repro: an empty-signature External node must not raise."""
    sym = R.external({"signature": "", "name": "", "module": "", "kind": "unknown"})
    assert isinstance(sym, TSExternalSymbol)


def test_get_external_symbols_end_to_end_via_stubbed_run():
    """Backend-level: get_external_symbols reconstructs every returned row, keyed by signature."""
    rows = [
        {"p": {"signature": "commander.parse", "name": "parse", "module": "commander", "kind": "external"}},
        {"p": {"signature": "fs.readFileSync", "name": "readFileSync", "module": "fs", "kind": "external"}},
    ]
    backend = TSNeo4jBackend.__new__(TSNeo4jBackend)
    backend._modules = ["app"]
    with patch.object(TSNeo4jBackend, "_run", return_value=rows):
        out = backend.get_external_symbols()
    assert set(out) == {"commander.parse", "fs.readFileSync"}
    assert out["commander.parse"].module == "commander"
