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

"""Reconstructing ``:TSExternal`` nodes from their graph properties (#231, on the 1.2.0 shape):
the node carries ``id``/``kind``/``module``/``name`` and reconstructs into the id-keyed
``TSExternalNode``; the backend keys the map ``"<module>.<name>"``, as the call graph does."""

from unittest.mock import patch

from cldk.analysis.typescript.neo4j import reconstruct as R
from cldk.analysis.typescript.neo4j.neo4j_backend import TSNeo4jBackend
from cldk.models.typescript import TSExternalNode, TSExternalSymbol

APP_ID = "can://typescript/app"


def test_external_reconstructs_from_full_graph_props():
    sym = R.external({"id": f"{APP_ID}/@external/commander/parse", "name": "parse", "module": "commander", "kind": "external"})
    assert isinstance(sym, TSExternalNode) and TSExternalSymbol is TSExternalNode
    assert sym.name == "parse"
    assert sym.module == "commander"
    assert sym.id == f"{APP_ID}/@external/commander/parse"


def test_external_reconstructs_with_empty_name_and_module():
    """The reported repro: an empty-named external must not raise."""
    sym = R.external({"id": f"{APP_ID}/@external//", "name": "", "module": "", "kind": "unknown"})
    assert isinstance(sym, TSExternalNode)


def test_get_external_symbols_keys_module_dot_name_and_scopes_by_both_namespace_prefixes():
    """The scope is the **two** application prefixes plus the ``@external`` segment, not the
    typescript prefix alone: an external a ``.js`` module owns is homed under
    ``can://javascript/<app>/`` and a single-prefix reading dropped it silently (TS-3; leg 2.5b
    review, finding 9). The third row is that external, and it must come back."""
    rows = [
        {"p": {"id": f"{APP_ID}/@external/commander/parse", "name": "parse", "module": "commander", "kind": "external"}},
        {"p": {"id": f"{APP_ID}/@external/fs/readFileSync", "name": "readFileSync", "module": "fs", "kind": "external"}},
        {"p": {"id": "can://javascript/app/@external/lodash/merge", "name": "merge", "module": "lodash", "kind": "external"}},
    ]
    captured = {}

    def _run(query, **params):
        captured["query"] = query
        captured.update(params)
        return rows

    backend = TSNeo4jBackend.__new__(TSNeo4jBackend)
    backend.application_name = "app"
    with patch.object(TSNeo4jBackend, "_run", side_effect=_run):
        out = backend.get_external_symbols()
    assert set(out) == {"commander.parse", "fs.readFileSync", "lodash.merge"}
    assert out["commander.parse"].module == "commander"
    assert out["commander.parse"].id == f"{APP_ID}/@external/commander/parse"
    assert "(e:TSExternal) WHERE (e.id STARTS WITH $p1 OR e.id STARTS WITH $p2) AND e.id CONTAINS '/@external/'" in captured["query"]
    assert (captured["p1"], captured["p2"]) == (f"{APP_ID}/", "can://javascript/app/")
