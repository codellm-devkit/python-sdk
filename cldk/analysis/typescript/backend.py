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

"""The TypeScript analysis backend contract.

:class:`TypeScriptAnalysis` is a thin façade that delegates every query to a *backend*. Two
interchangeable backends exist:

* :class:`~cldk.analysis.typescript.codeanalyzer.TSCodeanalyzer` — walks the in-memory pydantic
  ``TSApplication`` / a NetworkX call graph built from ``analysis.json``;
* :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend` — answers the *same* queries with
  Cypher over the graph ``codeanalyzer-typescript`` emits with ``--emit neo4j``.

This ABC formalizes the surface those two share so the façade↔backend relationship is enforced by
the type system (and at instantiation time) instead of matching only by convention. Both backends
subclass it; the façade is typed against it. Backend-specific lifecycle (e.g. the Neo4j driver's
``close()`` / context-manager support) is intentionally *not* part of the contract.

The vocabulary mirrors :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer` /
:class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`, but the node kinds are TypeScript-native
(interfaces, type aliases, enums, namespaces, decorators, ...).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Set, Tuple

import networkx as nx

from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSTypeAlias,
    TSVariableDeclaration,
)


class TSAnalysisBackend(ABC):
    """Abstract base every TypeScript analysis backend implements.

    A backend owns *all* indexing and query logic for a TypeScript application; the
    :class:`TypeScriptAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.typescript`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so the two backends are behaviorally interchangeable.
    """

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_application(self) -> TSApplication:
        """The whole application view (symbol table + call graph + external symbols)."""

    @abstractmethod
    def get_symbol_table(self) -> Dict[str, TSModule]:
        """The per-file symbol table, keyed by module file path."""

    @abstractmethod
    def get_modules(self) -> List[TSModule]:
        """All modules (compilation units)."""

    @abstractmethod
    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """Phantom (external) call targets — imported/required library members."""

    @abstractmethod
    def get_typescript_file(self, qualified_name: str) -> str | None:
        """The file path declaring the symbol with the given signature."""

    @abstractmethod
    def get_typescript_module(self, file_path: str) -> TSModule | None:
        """The module for a file path."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of callable signatures (and phantom external symbols) + call edges."""

    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""

    @abstractmethod
    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base class."""

    # -----[ call sites ]-----
    @abstractmethod
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        """The rich, syntactic call sites inside a callable."""

    @abstractmethod
    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted source lines anywhere in the project where ``target_signature`` is invoked."""

    @abstractmethod
    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The call targets invoked from a callable, derived from its call sites."""

    # -----[ classes / interfaces / enums / type-aliases ]-----
    @abstractmethod
    def get_all_classes(self) -> Dict[str, TSClass]:
        """Every class, keyed by signature."""

    @abstractmethod
    def get_class(self, qualified_class_name: str) -> TSClass | None:
        """A single class by signature."""

    @abstractmethod
    def get_all_interfaces(self) -> Dict[str, TSInterface]:
        """Every interface, keyed by signature."""

    @abstractmethod
    def get_all_enums(self) -> Dict[str, TSEnum]:
        """Every enum, keyed by signature."""

    @abstractmethod
    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        """The members of an enum."""

    @abstractmethod
    def get_all_type_aliases(self) -> Dict[str, TSTypeAlias]:
        """Every type alias, keyed by signature."""

    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        """The classes declared inside a class."""

    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        """Classes that extend/implement the given class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends (base classes minus implemented interfaces)."""

    @abstractmethod
    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """The interfaces a class implements."""

    # -----[ methods / functions / fields ]-----
    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, TSCallable]]:
        """All methods grouped by their owning class/interface signature."""

    @abstractmethod
    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        """The methods of a class/interface, keyed by short name."""

    @abstractmethod
    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        """A single method of a class/interface."""

    @abstractmethod
    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """The parameter names of a method."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        """The constructors of a class."""

    @abstractmethod
    def get_all_functions(self) -> Dict[str, TSCallable]:
        """Top-level (module/namespace) functions, keyed by signature."""

    @abstractmethod
    def get_all_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        """The attributes/fields of a class."""

    @abstractmethod
    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        """The properties of an interface."""

    # -----[ imports / exports / variables ]-----
    @abstractmethod
    def get_imports(self) -> Dict[str, List[TSImport]]:
        """Per-file import bindings."""

    @abstractmethod
    def get_all_exports(self) -> Dict[str, List[TSExport]]:
        """Per-file export bindings."""

    @abstractmethod
    def get_all_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        """Per-file module-level variable declarations."""

    # -----[ decorators ]-----
    @abstractmethod
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        """Structured decorators applied to a callable."""

    @abstractmethod
    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        """Structured decorators applied to a class."""

    @abstractmethod
    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of callables carrying it."""

    @abstractmethod
    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of classes carrying it."""
