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

"""Tests for the four bulk/projected accessors (#298):
``get_callables_overview`` / ``get_method_bodies`` / ``get_decorated_callables`` /
``get_callsites_for`` — on the in-memory backend and the facade delegates.

Built against the real sample-app fixture (``tests/resources/typescript/analysis_json/slim``)
already used elsewhere in this package. Expected sets below were derived by reading that fixture's
JSON directly (see the exploration notes in the task brief), never by running the implementation
and copying its output.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.backend_config import CodeAnalyzerConfig
from cldk.models.typescript import TSCallableOverview


def _fake_run_writing_output(payload: str):
    def _run(cmd, *args, **kwargs):
        if "-o" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "analysis.json").write_text(payload, encoding="utf-8")
        return MagicMock(stdout=payload, returncode=0)

    return _run


@pytest.fixture
def ts_analysis(typescript_application, typescript_analysis_json, tmp_path, monkeypatch):
    """A local-backend facade over the real sample-app fixture."""
    monkeypatch.setenv("CODEANALYZER_TS_BIN", "codeanalyzer-typescript")
    with patch(
        "cldk.analysis.typescript.codeanalyzer.codeanalyzer.subprocess.run",
        side_effect=_fake_run_writing_output(typescript_analysis_json),
    ):
        return CLDK.typescript(
            project_path=typescript_application,
            eager=True,
            analysis_level=AnalysisLevel.call_graph,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )


# The fixture app has exactly 33 callables total (module functions, namespace functions, class and
# interface methods -- including nested classes' methods -- and inner/nested callables), 13 of
# which are owner-less: module-level functions, the one inner function
# (``src/util.classify.keyOf``), and the two namespace-owned functions on ``StringUtil``
# (``repeat``/``slug``) per the ruling that namespace-owned functions carry no owner pair.
TOTAL_CALLABLES = 33
OWNERLESS_SIGNATURES = {
    "src/controllers.Controller",
    "src/controllers.Get",
    "src/controllers.Param",
    "src/external.extensionOf",
    "src/external.fingerprint",
    "src/index.main",
    "src/services.announce",
    "src/services.makeGuestName",
    "src/services.nextId",
    "src/util.StringUtil.repeat",
    "src/util.StringUtil.slug",
    "src/util.classify",
    "src/util.classify.keyOf",
}


def by_signature(overviews):
    return {o.signature: o for o in overviews}


# -----[ get_callables_overview ]-----


def test_overview_enumerates_every_callable_including_inner(ts_analysis):
    overview = ts_analysis.get_callables_overview()
    assert all(isinstance(o, TSCallableOverview) for o in overview)
    signatures = {o.signature for o in overview}
    assert len(overview) == TOTAL_CALLABLES
    assert len(signatures) == TOTAL_CALLABLES  # no duplicates
    assert "src/util.classify.keyOf" in signatures  # inner callable is enumerated


def test_overview_owner_pair_is_none_for_owned_less_callables(ts_analysis):
    rows = by_signature(ts_analysis.get_callables_overview())
    for sig in OWNERLESS_SIGNATURES:
        assert rows[sig].owner_signature is None, sig
        assert rows[sig].owner_kind is None, sig


def test_overview_known_method_row_full_field_tuple(ts_analysis):
    rows = by_signature(ts_analysis.get_callables_overview())
    row = rows["src/models.User.recordLogin"]
    assert row.signature == "src/models.User.recordLogin"
    assert row.name == "recordLogin"
    assert row.owner_signature == "src/models.User"
    assert row.owner_kind == "class"
    assert row.kind == "method"
    assert row.start_line == 52
    assert row.end_line == 55
    assert row.decorators == []
    assert row.is_exported is False
    assert row.is_async is True
    assert row.is_static is False
    assert row.accessibility is None


def test_overview_interface_method_owner_kind_is_interface(ts_analysis):
    rows = by_signature(ts_analysis.get_callables_overview())
    row = rows["src/models.Named.describe"]
    assert row.owner_signature == "src/models.Named"
    assert row.owner_kind == "interface"


def test_overview_arrow_row_has_no_owner_and_native_kind(ts_analysis):
    rows = by_signature(ts_analysis.get_callables_overview())
    row = rows["src/services.nextId"]
    assert row.owner_signature is None
    assert row.owner_kind is None
    assert row.kind == "arrow"


def test_overview_namespace_owned_function_is_ownerless_with_dotted_signature(ts_analysis):
    """RULING: namespace-owned functions (``TSNamespace.functions``) are enumerated but carry no
    owner pair -- unlike a namespace-owned *class*'s methods, which do get an owner (the class)."""
    rows = by_signature(ts_analysis.get_callables_overview())
    row = rows["src/util.StringUtil.slug"]
    assert row.signature == "src/util.StringUtil.slug"
    assert row.owner_signature is None
    assert row.owner_kind is None
    # A namespace-owned *class*'s method, by contrast, does have an owner.
    builder_add = rows["src/util.StringUtil.Builder.add"]
    assert builder_add.owner_signature == "src/util.StringUtil.Builder"
    assert builder_add.owner_kind == "class"


# -----[ get_method_bodies ]-----


def test_method_bodies_mixes_real_interface_stub_and_unknown(ts_analysis):
    bodies = ts_analysis.get_method_bodies(
        [
            "src/services.UserService.create",
            "src/models.Named.describe",
            "src/does/not.exist",
        ]
    )
    assert set(bodies) == {"src/services.UserService.create", "src/models.Named.describe"}
    assert bodies["src/models.Named.describe"] == "describe(): string;"
    assert "this.parts.push" not in bodies["src/models.Named.describe"]


def test_method_bodies_empty_for_no_matches(ts_analysis):
    assert ts_analysis.get_method_bodies(["nope"]) == {}


# -----[ get_decorated_callables ]-----


def test_decorated_callables_exact_set(ts_analysis):
    decorated = ts_analysis.get_decorated_callables(["Get"])
    signatures = {o.signature for o in decorated}
    assert signatures == {"src/controllers.UserController.show", "src/controllers.UserController.list"}
    assert all(isinstance(o, TSCallableOverview) for o in decorated)


def test_decorated_callables_no_match_is_empty(ts_analysis):
    assert ts_analysis.get_decorated_callables(["NoSuchDecorator"]) == []


# -----[ get_callsites_for ]-----


def test_callsites_for_exact_per_signature_lists_and_empty_entry(ts_analysis):
    result = ts_analysis.get_callsites_for(
        [
            "src/services.UserService.create",
            "src/models.Entity.constructor",
            "src/does/not.exist",
        ]
    )
    assert set(result) == {"src/services.UserService.create", "src/models.Entity.constructor"}
    # existing-but-callsite-less callable gets an empty list, not omitted
    assert result["src/models.Entity.constructor"] == []
    create_targets = {cs.callee_signature or cs.method_name for cs in result["src/services.UserService.create"]}
    assert create_targets == {"src/services.nextId", "src/models.User.constructor", "push"}


# -----[ facade delegates to the same backend objects ]-----


def test_facade_delegates_return_backend_objects(ts_analysis):
    assert ts_analysis.get_callables_overview() == ts_analysis.backend.get_callables_overview()
    assert ts_analysis.get_method_bodies(["src/services.UserService.create"]) == ts_analysis.backend.get_method_bodies(
        ["src/services.UserService.create"]
    )
    assert ts_analysis.get_decorated_callables(["Get"]) == ts_analysis.backend.get_decorated_callables(["Get"])
    assert ts_analysis.get_callsites_for(["src/services.UserService.create"]) == ts_analysis.backend.get_callsites_for(
        ["src/services.UserService.create"]
    )
