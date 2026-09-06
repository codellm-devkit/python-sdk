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

"""The frozen public surface of :class:`TypeScriptAnalysis` (spec leg 2.5, G3).

Every public accessor's name and signature is pinned here. A change to this list is a public-API
change and must be deliberate: the only removals so far are the two accessors that only ever
raised ``NotImplementedError``; additions land with the query surface (2.5b) and extend the list.
"""

import ast
import inspect
import textwrap

import pytest

from cldk.analysis.typescript.typescript_analysis import TypeScriptAnalysis

SURFACE = {
    "get_application_view": "(self) -> 'TSApplication'",
    "get_call_graph": "(self) -> 'nx.DiGraph'",
    "get_call_graph_json": "(self) -> 'str'",
    "get_call_sites": "(self, qualified_callable_name: 'str') -> 'List[TSCallsite]'",
    "get_call_targets": "(self, source_signature: 'str') -> 'Set[str]'",
    "get_callables_overview": "(self) -> 'List[TSCallableOverview]'",
    "get_callees": "(self, source_class_name: 'str', source_method_declaration: 'str | None' = None) -> 'Dict'",
    "get_callers": "(self, target_class_name: 'str', target_method_declaration: 'str | None' = None) -> 'Dict'",
    "get_calling_lines": "(self, target_signature: 'str') -> 'List[int]'",
    "get_callsites_for": "(self, signatures: 'List[str]') -> 'Dict[str, List[TSCallsite]]'",
    "get_class": "(self, qualified_class_name: 'str') -> 'TSClass | None'",
    "get_class_call_graph": "(self, qualified_class_name: 'str', method_signature: 'str | None' = None) -> 'List[Tuple[str, str]]'",
    "get_class_decorators": "(self, qualified_class_name: 'str') -> 'List[TSDecorator]'",
    "get_class_hierarchy": "(self) -> 'nx.DiGraph'",
    "get_classes": "(self) -> 'Dict[str, TSClass]'",
    "get_classes_by_criteria": "(self, inclusions: 'List[str] | None' = None, exclusions: 'List[str] | None' = None) -> 'Dict[str, TSClass]'",
    "get_classes_with_decorators": "(self, decorators: 'List[str]') -> 'Dict[str, List[str]]'",
    "get_constructors": "(self, qualified_class_name: 'str') -> 'Dict[str, TSCallable]'",
    "get_decorated_callables": "(self, markers: 'List[str]') -> 'List[TSCallableOverview]'",
    "get_decorators": "(self, qualified_callable_name: 'str') -> 'List[TSDecorator]'",
    "get_enum_members": "(self, qualified_enum_name: 'str') -> 'List[TSEnumMember]'",
    "get_enums": "(self) -> 'Dict[str, TSEnum]'",
    "get_exports": "(self) -> 'Dict[str, List[TSExport]]'",
    "get_extended_classes": "(self, qualified_class_name: 'str') -> 'List[str]'",
    "get_external_symbols": "(self) -> 'Dict[str, TSExternalSymbol]'",
    "get_fields": "(self, qualified_class_name: 'str') -> 'List[TSClassAttribute]'",
    "get_functions": "(self) -> 'Dict[str, TSCallable]'",
    "get_implemented_interfaces": "(self, qualified_class_name: 'str') -> 'List[str]'",
    "get_imports": "(self) -> 'Dict[str, List[TSImport]]'",
    "get_interface_properties": "(self, qualified_interface_name: 'str') -> 'List[TSClassAttribute]'",
    "get_interfaces": "(self) -> 'Dict[str, TSInterface]'",
    "get_method": "(self, qualified_class_name: 'str', qualified_method_name: 'str') -> 'TSCallable | None'",
    "get_method_bodies": "(self, signatures: 'List[str]') -> 'Dict[str, str]'",
    "get_method_parameters": "(self, qualified_class_name: 'str', qualified_method_name: 'str') -> 'List[str]'",
    "get_methods": "(self) -> 'Dict[str, Dict[str, TSCallable]]'",
    "get_methods_in_class": "(self, qualified_class_name: 'str') -> 'Dict[str, TSCallable]'",
    "get_methods_with_decorators": "(self, decorators: 'List[str]') -> 'Dict[str, List[str]]'",
    "get_modules": "(self) -> 'List[TSModule]'",
    "get_nested_classes": "(self, qualified_class_name: 'str') -> 'List[TSClass]'",
    "get_sub_classes": "(self, qualified_class_name: 'str') -> 'Dict[str, TSClass]'",
    "get_symbol_table": "(self) -> 'Dict[str, TSModule]'",
    "get_synthesized_callables": "(self) -> 'Dict[str, TSSynthesizedCallable]'",
    "get_type_aliases": "(self) -> 'Dict[str, TSTypeAlias]'",
    "get_typescript_file": "(self, qualified_name: 'str') -> 'str | None'",
    "get_typescript_module": "(self, file_path: 'str') -> 'TSModule | None'",
    "get_variables": "(self) -> 'Dict[str, List[TSVariableDeclaration]]'",
}

REMOVED = ["get_entry_point_methods", "get_service_entry_point_methods"]


def _public():
    return {n: f for n, f in inspect.getmembers(TypeScriptAnalysis, inspect.isfunction) if not n.startswith("_")}


def test_the_public_surface_is_exactly_the_frozen_list():
    assert set(_public()) == set(SURFACE)


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_signature_is_frozen(name):
    assert str(inspect.signature(getattr(TypeScriptAnalysis, name))) == SURFACE[name]


@pytest.mark.parametrize("name", REMOVED)
def test_raising_accessor_is_gone(name):
    assert not hasattr(TypeScriptAnalysis, name), f"{name} raised unconditionally; it should not exist"


def test_no_public_accessor_only_raises():
    """Every remaining public accessor must do something (the statements of its body, docstring
    excluded, never ``raise NotImplementedError``)."""
    for name, fn in _public().items():
        node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        statements = node.body[1:] if ast.get_docstring(node) else node.body
        body = "\n".join(ast.unparse(s) for s in statements)
        assert "raise NotImplementedError" not in body, f"{name} only raises"
