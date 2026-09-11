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

Leg 2.5b Task 1 adds the seven addressing accessors, six of them methods and one
(``has_resolution_edges``) a property — which :func:`inspect.isfunction` cannot see, so it is
frozen separately in :data:`PROPERTIES`. Task 2 adds the fourteen dataflow accessors, every one of
them signature-for-signature ``PythonAnalysis``'s — the defaults included, which is where the
asymmetry lives: the three slices carry a finite ``depth`` and the five predicate/path accessors
carry ``depth: int | None = None``. Task 3 adds the last nine: the three entrypoint accessors,
``get_config_readers``, and the five repository-artifact getters, which have been on both backends
since leg 2.5a and reach the caller here. All nine are ``PythonAnalysis``'s signatures with only
the projection types renamed (``TSCallableOverview``/``TSClassOverview`` for the ``Py*`` pair);
the artifact five keep the shared ``Py*`` models, which is the contract, not an oversight.
"""

import ast
import inspect
import textwrap

import pytest

from cldk.analysis.python.python_analysis import PythonAnalysis
from cldk.analysis.typescript.typescript_analysis import TypeScriptAnalysis

SURFACE = {
    "describe": "(self, nodes: 'Sequence[object]') -> 'List[SliceNode]'",
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
    "get_source": "(self, node_id: 'str') -> 'str'",
    "get_sub_classes": "(self, qualified_class_name: 'str') -> 'Dict[str, TSClass]'",
    "get_symbol_table": "(self) -> 'Dict[str, TSModule]'",
    "get_synthesized_callables": "(self) -> 'Dict[str, TSSynthesizedCallable]'",
    "get_type_aliases": "(self) -> 'Dict[str, TSTypeAlias]'",
    "get_typescript_file": "(self, qualified_name: 'str') -> 'str | None'",
    "get_typescript_module": "(self, file_path: 'str') -> 'TSModule | None'",
    "get_variables": "(self) -> 'Dict[str, List[TSVariableDeclaration]]'",
    "locate": "(self, path: 'str', line: 'int') -> 'LocateResult'",
    "locate_many": "(self, positions: 'Sequence[Tuple[str, int]]') -> 'List[LocateResult]'",
    "resolve_callable": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'SliceNode'",
    "resolve_value": "(self, name: 'str', *, within: 'str') -> 'SliceNode'",
    "backward_cone": "(self, sinks: 'Sequence[str]', *, depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "call_paths_between": "(self, src: 'str', dst: 'str', *, depth: 'int | None' = None, max_paths: 'int' = 10) -> 'FlowPaths'",
    "callees_of": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'List[SliceNode]'",
    "callers_of": "(self, name: 'str', *, in_class: 'str | None' = None, in_module: 'str | None' = None) -> 'List[SliceNode]'",
    "flows_to_argument": "(self, src: 'str', callee: 'str', arg: 'str', *, within: 'str', depth: 'int | None' = None) -> 'bool'",
    "taint": "(self, sources: 'Sequence[Tuple[str, str]]', sinks: 'Sequence[Tuple[str, str]]', sanitizers: 'Sequence[Tuple[str, str] | str]' = (), *, depth: 'int | None' = None, max_paths: 'int' = 10) -> 'TaintResult'",
    "flows_to_call": "(self, src: 'str', callee: 'str', *, within: 'str', depth: 'int | None' = None) -> 'bool'",
    "get_cdg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[TSCdgEdge]'",
    "get_cfg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[TSCfgEdge]'",
    "get_ddg": "(self, callable: 'str', *, in_class: 'str | None' = None, page_size: 'int' = 10000, cursor: 'str | None' = None) -> 'EdgePage[TSDdgEdge]'",
    "paths_between": "(self, src: 'str', dst: 'str', *, src_within: 'str', dst_within: 'str', depth: 'int | None' = None, max_paths: 'int' = 10) -> 'FlowPaths'",
    "reaches": "(self, src: 'str', dst: 'str', *, depth: 'int | None' = None) -> 'bool'",
    "slice_backward": "(self, src: 'str', *, within: 'str', depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "slice_forward": "(self, src: 'str', *, within: 'str', depth: 'int | None' = 5, max_nodes: 'int' = 10000) -> 'Slice'",
    "get_entrypoints": "(self) -> 'List[TSCallableOverview]'",
    "get_entrypoint_classes": "(self) -> 'List[TSClassOverview]'",
    "get_entrypoint_coverage": "(self) -> 'EntrypointCoverage'",
    "get_artifacts": "(self) -> 'Dict[str, PyArtifact]'",
    "get_dependencies": "(self, *, direct_only: 'bool' = False, ecosystem: 'str | None' = None, declared_in: 'str | None' = None) -> 'List[PyDependency]'",
    "get_config_keys": "(self) -> 'Dict[str, PyConfigKey]'",
    "get_config_uses": "(self, key: 'str | None' = None) -> 'List[PyConfigUseEdge]'",
    "get_unresolved_config_reads": "(self) -> 'List[PyConfigRead]'",
    "get_config_readers": "(self, key: 'str') -> 'List[TSCallableOverview]'",
}

#: Public *properties* — frozen the same way, since ``inspect.isfunction`` does not see them.
PROPERTIES = {"has_resolution_edges": "bool"}

REMOVED = ["get_entry_point_methods", "get_service_entry_point_methods"]


def _public():
    return {n: f for n, f in inspect.getmembers(TypeScriptAnalysis, inspect.isfunction) if not n.startswith("_")}


def _public_properties():
    return {n for n, v in vars(TypeScriptAnalysis).items() if isinstance(v, property) and not n.startswith("_")}


def test_the_public_surface_is_exactly_the_frozen_list():
    assert set(_public()) == set(SURFACE)


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_signature_is_frozen(name):
    assert str(inspect.signature(getattr(TypeScriptAnalysis, name))) == SURFACE[name]


def test_the_public_properties_are_exactly_the_frozen_list():
    assert _public_properties() == set(PROPERTIES)


@pytest.mark.parametrize("name", sorted(PROPERTIES))
def test_property_return_annotation_is_frozen(name):
    assert inspect.signature(vars(TypeScriptAnalysis)[name].fget).return_annotation == PROPERTIES[name]


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


#: Accessors both facades carry whose *parameters* differ. Two kinds, and the difference matters:
#:
#: * **Open scope, tracked as python-sdk#352** -- ``get_call_graph`` (no ``roots=``/``depth=``),
#:   ``get_classes`` (no ``module=``) and ``get_symbol_table`` (no ``paths=``). 1.x-era in
#:   *origin*, but they are open items against leg 2.5b's own definition of done: the shared
#:   surface is meant to take the same arguments on both facades, and adding a keyword-only
#:   optional with a default would not have moved an existing signature. On a large application the
#:   unscoped call is the only call available and it is the expensive one, so this is a gap a
#:   caller feels, not a cosmetic one.
#: * **Benign** -- ``get_callers``/``get_callees`` keep TypeScript's *optional* method argument
#:   where Python's is required. Nothing a Python caller writes stops working on TypeScript; only
#:   the reverse, and TypeScript is the looser of the two.
#:
#: Listed so each is a recorded decision that has to be deleted from here to be closed, not a
#: silence -- ``test_a_recorded_divergence_is_still_a_divergence`` below is what enforces that.
KNOWN_ARGUMENT_DIVERGENCES = {"get_call_graph", "get_callees", "get_callers", "get_classes", "get_symbol_table"}


def _parameters(cls, name):
    return str(inspect.signature(getattr(cls, name)).replace(return_annotation=inspect.Signature.empty))


def test_every_accessor_both_facades_carry_takes_the_same_arguments():
    """Leg 2.5b's contract in one line: where ``TypeScriptAnalysis`` and ``PythonAnalysis`` both
    declare an accessor, its *parameters* are identical — names, order, keyword-only-ness and
    defaults. Return types are excluded because they are where the two legitimately differ (a
    ``TSCallableOverview`` for a ``PyCallableOverview``); everything a caller passes is not.
    """
    shared = set(_public()) & {n for n, _ in inspect.getmembers(PythonAnalysis, inspect.isfunction) if not n.startswith("_")}
    assert len(shared) >= 50, f"only {len(shared)} accessors are shared; did a facade lose its mirror?"
    mismatched = {
        n: (_parameters(TypeScriptAnalysis, n), _parameters(PythonAnalysis, n)) for n in sorted(shared) if _parameters(TypeScriptAnalysis, n) != _parameters(PythonAnalysis, n)
    }
    assert set(mismatched) == KNOWN_ARGUMENT_DIVERGENCES, f"the mirror moved: {mismatched}"


@pytest.mark.parametrize("name", sorted(KNOWN_ARGUMENT_DIVERGENCES))
def test_a_recorded_divergence_is_still_a_divergence(name):
    """The list above is a ledger, not a mute button: an entry that has been closed must be deleted
    from it rather than left behind."""
    assert _parameters(TypeScriptAnalysis, name) != _parameters(PythonAnalysis, name), f"{name} now matches PythonAnalysis; drop it from KNOWN_ARGUMENT_DIVERGENCES"
