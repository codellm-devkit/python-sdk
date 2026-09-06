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

The shape shared with every other language — application view, symbol table, call graph, the
class/method/field lookups and the repository-artifact layer — is inherited from the generic
:class:`~cldk.analysis.commons.backend.AnalysisBackend`; what is declared here is the
TypeScript-native remainder (interfaces, type aliases, enums, namespaces, decorators, the
1.x call-site accessors and the bulk projections). Both backends subclass it; the façade is typed
against it. Backend-specific lifecycle (e.g. the Neo4j driver's ``close()`` / context-manager
support) is intentionally *not* part of the contract.

The call graph both backends return keeps TypeScript's own endpoints (decision TS-11): cants emits
a module as the caller of its top-level code and a class as the callee of ``new X()``, and both
are kept, tagged with a ``kind`` node attribute (:data:`CALL_GRAPH_NODE_KINDS`) so a
caller wanting Python's callable-only shape filters in one line rather than the SDK erasing every
top-level call.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, Dict, List, Set, Tuple

import networkx as nx

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.models.typescript import (
    TSApplication,
    TSCallable,
    TSCallableOverview,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSDecorator,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSField,
    TSImport,
    TSInterface,
    TSModule,
    TSSynthesizedCallable,
    TSType,
    TSTypeAlias,
    TSVariableDeclaration,
)


#: The ``kind`` vocabulary of a call-graph node: what the id index holds — a module, any of the
#: five type kinds (a class is the callee of ``new X()``; the others are indexed and would be kept
#: if the analyzer ever emitted an edge to one), a callable, or an external.
CALL_GRAPH_NODE_KINDS = frozenset({"module", "class", "interface", "enum", "type_alias", "namespace", "callable", "external"})


class TSAnalysisBackend(AnalysisBackend[TSApplication, TSModule, TSType, TSCallable, TSField, str]):
    """Abstract base every TypeScript analysis backend implements.

    A backend owns *all* indexing and query logic for a TypeScript application; the
    :class:`TypeScriptAnalysis` façade is a one-line-delegation shim over it. Implementations must
    return the canonical ``cldk.models.typescript`` pydantic objects (or the documented
    NetworkX / dict / list shapes) so the two backends are behaviorally interchangeable.

    Inherited abstract (see :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "TS"
    N: ClassVar[str] = "TS"

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_modules(self) -> List[TSModule]:
        """All modules (compilation units)."""

    @abstractmethod
    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """Phantom (external) call targets — imported/required library members and builtins —
        keyed ``"<module>.<name>"``, the key the call graph uses for them; the wire's ``can://``
        id is on the value."""

    @abstractmethod
    def get_synthesized_callables(self) -> Dict[str, TSSynthesizedCallable]:
        """The application's anonymous callables, each value carrying the ``can://`` tree id of
        the callable it stands for. Empty below level 2.

        **The key is backend-dependent**, and each backend's own docstring says which it uses: a
        backend reading ``analysis.json`` passes the analyzer's compatibility index through as
        emitted, so the key is the *older* anonymous id and the value's ``id`` is the tree id that
        replaced it (key != ``id``); a backend reading the Neo4j projection has the tree nodes and
        not the index, so it keys by the node's own id (key == ``id``). Do not key a cross-backend
        lookup on this map -- ask for the value's ``id``."""

    @abstractmethod
    def get_typescript_file(self, qualified_name: str) -> str | None:
        """The file path declaring the symbol with the given signature."""

    @abstractmethod
    def get_typescript_module(self, file_path: str) -> TSModule | None:
        """The module for a file path."""

    # -----[ call graph ]-----
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
        """The syntactic call sites inside a callable — its ``body`` nodes of ``kind == "call"``,
        with the resolved callee mapped to its signature."""

    @abstractmethod
    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted source lines anywhere in the project where ``target_signature`` is invoked."""

    @abstractmethod
    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The call targets invoked from a callable, derived from its call sites."""

    # -----[ interfaces / enums / type-aliases ]-----
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
        """The classes declared inside a class -- on schema v2 always ``[]``, on every backend: a
        class holds only ``callables`` and ``fields``, so no class nests a type. A class declared
        inside a *callable* survives as ``TSCallable.inner_classes``. Kept for the 1.x surface."""

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
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        """The constructors of a class."""

    @abstractmethod
    def get_all_functions(self) -> Dict[str, TSCallable]:
        """Top-level (module/namespace) functions, keyed by signature."""

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

    # -----[ bulk / projected accessors ]-----
    # Set-at-a-time, field-projected reads — one round-trip on the Neo4j backend, one symbol-table
    # walk in-process — for callers that enumerate the whole application and would otherwise pay the
    # per-entity reconstruction of get_all_methods_in_application.
    @abstractmethod
    def get_callables_overview(self) -> List[TSCallableOverview]:
        """A lightweight projection of every callable in the application (methods, module-level,
        namespace-level, and nested/inner functions), without the full :class:`TSCallable`
        reconstruction.

        Known limitation: a ``get x()``/``set x()`` accessor pair shares one ``signature``, so
        this (and the other bulk accessors) can diverge between backends on a paired accessor —
        see `#300 <https://github.com/codellm-devkit/python-sdk/issues/300>`_."""

    @abstractmethod
    def get_method_bodies(self, signatures: List[str]) -> Dict[str, str]:
        """Source bodies for the given callable signatures, keyed by signature. Signatures with no
        matching callable are omitted, as are callables with no source text (an implicit
        constructor the analyzer synthesizes has an empty span, so its ``code`` is ``""``; 1.x
        carried ``None``) — every returned value is a real, non-empty ``str``."""

    @abstractmethod
    def get_decorated_callables(self, markers: List[str]) -> List[TSCallableOverview]:
        """Overviews of callables decorated with any of ``markers`` (matched against the decorator
        names)."""

    @abstractmethod
    def get_callsites_for(self, signatures: List[str]) -> Dict[str, List[TSCallsite]]:
        """Call sites of the given callable signatures, keyed by owning signature. Each existing
        signature gets an entry (an empty list if it has no call sites); signatures with no matching
        callable are omitted."""
