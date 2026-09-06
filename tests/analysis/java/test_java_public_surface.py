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
``1375b55`` with exactly two deliberate differences: the constructor lost ``source_code`` (J-10)
and ``get_method_parameters`` is annotated with what it always returned
(``List[JCallableParameter]``, a latent 1.x annotation bug). A change to this list is a public-API
change and must be deliberate; the query surface (3b) extends it.
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


def test_the_public_surface_is_exactly_the_frozen_list():
    assert set(_public()) == set(SURFACE)


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
