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

"""The frozen public surface of :class:`JavaAnalysis` (spec leg 3, the Iron Rule).

Every public accessor's name and signature is pinned here, derived from the 1.x facade at
``6f9c84d`` with exactly two deliberate differences: the constructor lost ``source_code`` (J-10)
and ``get_method_parameters`` is annotated with what it always returned
(``List[JCallableParameter]``, a latent 1.x annotation bug). A change to this list is a public-API
change and must be deliberate; the query surface (3b) extends it.

Leg 3b Task 2 adds the thirteen dataflow accessors; Task 1 added the six addressing **methods** below, plus the ``has_resolution_edges``
**property** — which is why :data:`SURFACE` is checked against the functions and the property is
pinned separately: ``inspect.isfunction`` does not see a property, and spelling it as a method to
make it visible here would be a different public API from Python's. Task 3 adds eighteen more: the
entrypoint trio, the four bulk projections, ``get_external_symbols``, the artifact six and the four
J-7 leaf accessors.

**Nothing pre-existing moved.** In particular the eight 1.x ``NotImplementedError`` raisers listed
in :data:`RAISING` are all still here and still raising: §4 of the spec proposes retiring them (and
deleting ``get_service_entry_point_*``), but no task of the 3b plan carries that work, and the
plan's own Global Constraints say this list "grows by exactly what it adds and changes no existing
entry". Retiring them is therefore a separate, deliberate change.
"""

import inspect

import pytest

from cldk.analysis.java.java_analysis import JavaAnalysis

SURFACE = {
    "get_all_comments": "(self) -> 'Dict[str, List[JComment]]'",
    "get_all_create_operations": "(self) -> 'List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]'",
    "get_all_crud_operations": "(self) -> 'List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]'",
    "get_all_delete_operations": "(self) -> 'List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]'",
    "get_all_docstrings": "(self) -> 'Dict[str, List[JComment]]'",
    "get_all_read_operations": "(self) -> 'List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]'",
    "get_all_update_operations": "(self) -> 'List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]'",
    "get_application_view": "(self) -> 'JApplication'",
    "get_call_graph": "(self) -> 'nx.DiGraph'",
    "get_call_graph_json": "(self) -> 'str'",
    "get_call_targets": "(self, declared_methods: 'dict') -> 'Set[str]'",
    "get_callees": "(self, source_class_name: 'str', source_method_declaration: 'str', using_symbol_table: 'bool' = False) -> 'Dict'",
    "get_callers": "(self, target_class_name: 'str', target_method_declaration: 'str', using_symbol_table: 'bool' = False) -> 'Dict'",
    "get_calling_lines": "(self, target_method_name: 'str') -> 'List[int]'",
    "get_class": "(self, qualified_class_name: 'str') -> 'JType | None'",
    "get_class_call_graph": "(self, qualified_class_name: 'str', method_signature: 'str | None' = None, using_symbol_table: 'bool' = False) -> 'List[Tuple[JMethodDetail, JMethodDetail]]'",
    "get_class_hierarchy": "(self) -> 'nx.DiGraph'",
    "get_classes": "(self) -> 'Dict[str, JType]'",
    "get_classes_by_criteria": "(self, inclusions: 'List[str] | None' = None, exclusions: 'List[str] | None' = None) -> 'Dict[str, JType]'",
    "get_comment_in_file": "(self, file_path: 'str') -> 'List[JComment]'",
    "get_comments_in_a_class": "(self, qualified_class_name: 'str') -> 'List[JComment]'",
    "get_comments_in_a_method": "(self, qualified_class_name: 'str', method_signature: 'str') -> 'List[JComment]'",
    # -- the dataflow surface (leg 3b, Task 2); Python's signatures, keyword-for-keyword, with
    # Java's edge models. ``slice_forward``, ``paths_between`` and the two ``flows_to_*`` predicates
    # refuse on today's analyzer output (the L4 port lattice carries no dependence edge); their
    # signatures are frozen here all the same, because the refusal is about the data.
    "get_cfg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[JCfgEdge]'",
    "get_cdg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[JCdgEdge]'",
    "get_ddg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[JDdgEdge]'",
    "slice_backward": "(self, src: 'str', *, within: 'str', depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "slice_forward": "(self, src: 'str', *, within: 'str', depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "backward_cone": "(self, sinks: 'Sequence[str]', *, depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "reaches": "(self, src: 'str', dst: 'str', *, depth: 'int | None' = None) -> 'bool'",
    "callers_of": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'List[SliceNode]'",
    "callees_of": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'List[SliceNode]'",
    "paths_between": "(self, src: 'str', dst: 'str', *, src_within: 'str', dst_within: 'str', depth: 'int | None' = None, max_paths: 'int' = 10) -> 'FlowPaths'",
    "call_paths_between": "(self, src: 'str', dst: 'str', *, depth: 'int | None' = None, max_paths: 'int' = 10) -> 'FlowPaths'",
    "flows_to_call": "(self, src: 'str', callee: 'str', *, within: 'str', depth: 'int | None' = None) -> 'bool'",
    "flows_to_argument": "(self, src: 'str', callee: 'str', arg: 'str', *, within: 'str', depth: 'int | None' = None) -> 'bool'",
    # -- entrypoints, the bulk projections, the artifact layer and the type-kind leaf accessors
    # (leg 3b, Task 3). Python's signatures, keyword-for-keyword, with Java's models -- except the
    # artifact layer, which is the one part of the graph every codeanalyzer projects identically and
    # so keeps the shared ``Py*`` models. ``get_entrypoint_coverage`` reports the report
    # *unavailable* on both backends (J-4): Java projects none.
    "get_callables_overview": "(self) -> 'List[JCallableOverview]'",
    "get_method_bodies": "(self, signatures: 'List[str]') -> 'Dict[str, str]'",
    "get_decorated_callables": "(self, markers: 'List[str]') -> 'List[JCallableOverview]'",
    "get_entrypoints": "(self) -> 'List[JCallableOverview]'",
    "get_entrypoint_classes": "(self) -> 'List[JClassOverview]'",
    "get_entrypoint_coverage": "(self) -> 'EntrypointCoverage'",
    "get_callsites_for": "(self, signatures: 'List[str]') -> 'Dict[str, List[JCallSite]]'",
    "get_external_symbols": "(self) -> 'Dict[str, JExternalSymbol]'",
    "get_artifacts": "(self) -> 'Dict[str, PyArtifact]'",
    "get_dependencies": "(self, *, direct_only: 'bool' = False, ecosystem: 'str | None' = None, declared_in: 'str | None' = None) -> 'List[PyDependency]'",
    "get_config_keys": "(self) -> 'Dict[str, PyConfigKey]'",
    "get_config_uses": "(self, key: 'str | None' = None) -> 'List[PyConfigUseEdge]'",
    "get_unresolved_config_reads": "(self) -> 'List[PyConfigRead]'",
    "get_config_readers": "(self, key: 'str') -> 'List[JCallableOverview]'",
    "get_interfaces": "(self) -> 'Dict[str, JType]'",
    "get_enums": "(self) -> 'Dict[str, JType]'",
    "get_enum_members": "(self, qualified_enum_name: 'str') -> 'List[JEnumConstant]'",
    "get_records": "(self) -> 'Dict[str, JType]'",
    # -- the addressing surface (leg 3b, Task 1); Python's signatures, keyword-for-keyword.
    "describe": "(self, nodes: 'Sequence[object]') -> 'List[SliceNode]'",
    "get_source": "(self, node_id: 'str') -> 'str'",
    "locate": "(self, path: 'str', line: 'int') -> 'LocateResult'",
    "locate_many": "(self, positions: 'Sequence[Tuple[str, int]]') -> 'List[LocateResult]'",
    "resolve_callable": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'SliceNode'",
    "resolve_value": "(self, name: 'str', *, within: 'str') -> 'SliceNode'",
    "get_compilation_units": "(self) -> 'List[JCompilationUnit]'",
    "get_constructors": "(self, qualified_class_name: 'str') -> 'Dict[str, JCallable]'",
    "get_entry_point_classes": "(self) -> 'Dict[str, JType]'",
    "get_entry_point_methods": "(self) -> 'Dict[str, Dict[str, JCallable]]'",
    "get_extended_classes": "(self, qualified_class_name: 'str') -> 'List[str]'",
    "get_fields": "(self, qualified_class_name: 'str') -> 'List[JField]'",
    "get_implemented_interfaces": "(self, qualified_class_name: 'str') -> 'List[str]'",
    "get_imports": "(self) -> 'List[str]'",
    "get_java_compilation_unit": "(self, file_path: 'str') -> 'JCompilationUnit'",
    "get_java_file": "(self, qualified_class_name: 'str') -> 'str | None'",
    "get_method": "(self, qualified_class_name: 'str', qualified_method_name: 'str') -> 'JCallable | None'",
    "get_method_parameters": "(self, qualified_class_name: 'str', qualified_method_name: 'str') -> 'List[JCallableParameter]'",
    "get_methods": "(self) -> 'Dict[str, Dict[str, JCallable]]'",
    "get_methods_in_class": "(self, qualified_class_name: 'str') -> 'Dict[str, JCallable]'",
    "get_methods_with_annotations": "(self, annotations: 'List[str]') -> 'Dict[str, List[Dict]]'",
    "get_nested_classes": "(self, qualified_class_name: 'str') -> 'List[JType]'",
    "get_raw_ast": "(self, source_code: 'str') -> 'Tree'",
    "get_service_entry_point_classes": "(self, **kwargs) -> 'Dict[str, JType]'",
    "get_service_entry_point_methods": "(self, **kwargs) -> 'Dict[str, Dict[str, JCallable]]'",
    "get_sub_classes": "(self, qualified_class_name: 'str') -> 'Dict[str, JType]'",
    "get_symbol_table": "(self) -> 'Dict[str, JCompilationUnit]'",
    "get_test_methods": "(self) -> 'Dict[str, str]'",
    "get_variables": "(self, **kwargs) -> 'Dict'",
    "is_parsable": "(self, source_code: 'str') -> 'bool'",
    "remove_all_comments": "(self) -> 'str'",
}

#: J-10: the 1.x constructor minus ``source_code``; everything else in place.
CONSTRUCTOR = "(self, project_dir: 'str | Path | None', analysis_level: 'str', target_files: 'List[str] | None', eager_analysis: 'bool', backend: 'JavaBackend | None' = None) -> 'None'"

#: The accessors that only raise ``NotImplementedError`` in 3a: the eight 1.x placeholders the
#: plan keeps until 3b (#311), plus ``remove_all_comments``, which only ever worked in the removed
#: single-file mode and now says so instead of silently changing.
RAISING = [
    "get_call_targets",
    "get_calling_lines",
    "get_class_hierarchy",
    "get_imports",
    "get_methods_with_annotations",
    "get_service_entry_point_classes",
    "get_service_entry_point_methods",
    "get_variables",
    "remove_all_comments",
]


def _public():
    return {n: f for n, f in inspect.getmembers(JavaAnalysis, inspect.isfunction) if not n.startswith("_")}


#: The one public **property**. Python and TypeScript both spell it this way; Java matches.
PROPERTIES = {"has_resolution_edges"}


def test_the_public_surface_is_exactly_the_frozen_list():
    assert set(_public()) == set(SURFACE)


def test_the_public_properties_are_exactly_the_frozen_list():
    assert {n for n, v in vars(JavaAnalysis).items() if isinstance(v, property) and not n.startswith("_")} == PROPERTIES


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_signature_is_frozen(name):
    assert str(inspect.signature(getattr(JavaAnalysis, name))) == SURFACE[name]


def test_constructor_lost_source_code_and_nothing_else():
    assert str(inspect.signature(JavaAnalysis.__init__)) == CONSTRUCTOR


@pytest.mark.parametrize("name", RAISING)
def test_placeholder_still_raises_not_implemented(name, test_fixture, analysis_json, tmp_path):
    """Pinned so that retiring one of them (3b) is a deliberate edit to this list."""
    from unittest.mock import MagicMock, patch

    from cldk.analysis import AnalysisLevel
    from cldk.analysis.commons.backend_config import CodeAnalyzerConfig

    with patch("cldk.analysis.java.codeanalyzer.codeanalyzer.subprocess.run") as run_mock:
        run_mock.return_value = MagicMock(stdout=analysis_json, returncode=0)
        (tmp_path / "java").mkdir()
        (tmp_path / "java" / "analysis.json").write_text(analysis_json, encoding="utf-8")
        analysis = JavaAnalysis(
            project_dir=test_fixture,
            analysis_level=AnalysisLevel.symbol_table,
            target_files=None,
            eager_analysis=False,
            backend=CodeAnalyzerConfig(cache_dir=str(tmp_path)),
        )
    params = inspect.signature(getattr(JavaAnalysis, name)).parameters
    args = [[] if p.annotation in ("List[str]", "dict") else "x" for n, p in params.items() if n != "self" and p.kind is p.POSITIONAL_OR_KEYWORD]
    with pytest.raises(NotImplementedError):
        getattr(analysis, name)(*args)
