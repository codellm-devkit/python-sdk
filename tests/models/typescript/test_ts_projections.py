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

"""Tests for :mod:`cldk.models.typescript.projections`."""

from cldk.models.typescript import TSCallable, TSDecorator
from cldk.models.typescript.projections import TSCallableOverview


def test_from_callable_method_case_projects_owner_pair_and_decorators():
    c = TSCallable(
        name="getUser",
        path="src/user.ts",
        signature="src/user.UserService.getUser",
        decorators=[TSDecorator(name="Get"), TSDecorator(name="Deprecated")],
        start_line=10,
        end_line=15,
        kind="method",
        accessibility="public",
        is_static=False,
        is_async=True,
        is_exported=False,
    )

    overview = TSCallableOverview.from_callable(
        c, owner_signature="src/user.UserService", owner_kind="class"
    )

    assert overview.signature == "src/user.UserService.getUser"
    assert overview.name == "getUser"
    assert overview.owner_signature == "src/user.UserService"
    assert overview.owner_kind == "class"
    assert overview.kind == "method"
    assert overview.path == "src/user.ts"
    assert overview.start_line == 10
    assert overview.end_line == 15
    assert overview.decorators == ["Get", "Deprecated"]
    assert overview.is_exported is False
    assert overview.is_async is True
    assert overview.is_static is False
    assert overview.accessibility == "public"


def test_from_callable_arrow_case_has_none_owner_pair():
    c = TSCallable(
        name="handler",
        path="src/handlers.ts",
        signature="src/handlers.handler",
        start_line=1,
        end_line=3,
        kind="arrow",
        is_exported=True,
    )

    overview = TSCallableOverview.from_callable(c, owner_signature=None, owner_kind=None)

    assert overview.owner_signature is None
    assert overview.owner_kind is None
    assert overview.kind == "arrow"
    assert overview.decorators == []
    assert overview.is_exported is True
    assert overview.is_async is False
    assert overview.is_static is False
    assert overview.accessibility is None


def test_no_code_field():
    assert "code" not in TSCallableOverview.model_fields
