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

"""Tests for :mod:`cldk.models.typescript.projections`, against a real 1.2.0 ``a4`` fixture."""

from pathlib import Path

import pytest

from cldk.models.typescript import TSAnalysis, TSCallable
from cldk.models.typescript.projections import TSCallableOverview

FIXTURE = Path(__file__).resolve().parents[2] / "resources" / "typescript" / "analysis_json" / "v2" / "a4" / "analysis.json"


@pytest.fixture(scope="module")
def app():
    return TSAnalysis.model_validate_json(FIXTURE.read_text(encoding="utf-8")).application


def _first(app, pred):
    for path, m in app.symbol_table.items():
        for t in m.types.values():
            for c in getattr(t, "callables", {}).values():
                if pred(c, t):
                    return path, t, c
        for c in m.functions.values():
            if pred(c, None):
                return path, None, c
    raise AssertionError("no callable matched")


def test_from_callable_method_case_projects_owner_pair_and_decorators(app):
    path, owner, c = _first(app, lambda c, t: t is not None and t.kind == "class" and c.decorators)

    overview = TSCallableOverview.from_callable(c, owner_signature=owner.signature, owner_kind=owner.kind, path=path)

    assert overview.signature == c.signature
    assert overview.name == c.name
    assert overview.owner_signature == owner.signature
    assert overview.owner_kind == "class"
    assert overview.kind == c.kind
    assert overview.path == path
    assert overview.start_line == c.span.start[0]
    assert overview.end_line == c.span.end[0]
    assert overview.decorators == [d.name for d in c.decorators]
    assert overview.is_exported is c.is_exported
    assert overview.is_async is c.is_async
    assert overview.is_static is c.is_static
    assert overview.accessibility == c.accessibility


def test_from_callable_module_function_has_none_owner_pair(app):
    path, _, c = _first(app, lambda c, t: t is None)

    overview = TSCallableOverview.from_callable(c, owner_signature=None, owner_kind=None, path=path)

    assert overview.owner_signature is None
    assert overview.owner_kind is None
    assert overview.path == path
    assert overview.start_line == c.start_line


def test_from_callable_namespace_owned_function_has_none_owner_pair(app):
    """Namespace-owned functions (TSNamespace.functions) are ownerless: TS namespaces are
    module-like scoping, not a class/interface owner, and the dotted signature already encodes
    the namespace path — so the owner pair stays None/None, same as module-level and nested
    callables. The caller (the backend's callable walk) is what keeps this invariant; the
    projection passes the pair through."""
    path, ns, c = next((path, t, c) for path, m in app.symbol_table.items() for t in m.namespaces.values() for c in t.functions.values())
    assert isinstance(c, TSCallable)

    overview = TSCallableOverview.from_callable(c, owner_signature=None, owner_kind=None, path=path)

    assert overview.owner_signature is None
    assert overview.owner_kind is None
    assert overview.signature.startswith(ns.signature + ".")


def test_no_code_field():
    assert "code" not in TSCallableOverview.model_fields
