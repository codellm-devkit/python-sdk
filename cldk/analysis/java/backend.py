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

"""The Java analysis backend contract.

:class:`JavaAnalysis` is a (mostly) thin façade that delegates its static-analysis queries to a
*backend*. Today the only backend is :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`
(in-memory pydantic / NetworkX over the codeanalyzer JSON); this ABC formalizes the surface the
façade depends on so an alternative backend (e.g. a forthcoming Neo4j/Cypher backend, mirroring
the TypeScript :class:`~cldk.analysis.typescript.neo4j.TSNeo4jBackend`) can be dropped in and
selected without touching the façade.

The contract is enforced by the type system and at instantiation time rather than matching only by
convention. Note the façade also calls Tree-sitter directly for a few parsing/sanitization helpers
(e.g. ``is_parsable``, ``get_raw_ast``); those are not part of the backend contract — only the
analysis queries the façade routes through ``self.backend`` are.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Union

import networkx as nx

from cldk.models.java.models import (
    JApplication,
    JCallable,
    JCallableParameter,
    JComment,
    JCompilationUnit,
    JCRUDOperation,
    JField,
    JMethodDetail,
    JType,
)

# A CRUD query row: the owning type + callable and the operations found within it.
CRUDRow = Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]


class JavaAnalysisBackend(ABC):
    """Abstract base every Java analysis backend implements.

    A backend owns all indexing and query logic for a Java application (symbol table, call graph,
    class/method/field navigation, entry points, CRUD operations, comments/docstrings); the
    :class:`JavaAnalysis` façade delegates to it. Implementations must return the canonical
    ``cldk.models.java`` pydantic objects (or the documented NetworkX / dict / list shapes) so
    backends are behaviorally interchangeable.
    """

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_application_view(self) -> JApplication:
        """The whole application view."""

    @abstractmethod
    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        """The per-file symbol table, keyed by file path."""

    @abstractmethod
    def get_compilation_units(self) -> List[JCompilationUnit]:
        """All compilation units."""

    @abstractmethod
    def get_java_file(self, qualified_class_name: str) -> str | None:
        """The file path declaring a class. ``None`` if the class is not found."""

    @abstractmethod
    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """The compilation unit for a file path."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of the application's call edges."""

    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The call graph serialized as JSON."""

    @abstractmethod
    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        """Callers of a method."""

    @abstractmethod
    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        """Callees of a method."""

    @abstractmethod
    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Call-graph edges reachable from a class (or one of its methods)."""

    @abstractmethod
    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Call-graph edges reachable from a class, computed from the symbol table only."""

    # -----[ classes / methods / fields ]-----
    @abstractmethod
    def get_all_classes(self) -> Dict[str, JType]:
        """Every class, keyed by qualified name."""

    @abstractmethod
    def get_class(self, qualified_class_name: str) -> JType | None:
        """A single class by qualified name. ``None`` if not found."""

    @abstractmethod
    def get_all_sub_classes(self, qualified_class_name: str) -> Dict[str, JType]:
        """Classes that extend/implement the given class."""

    @abstractmethod
    def get_all_nested_classes(self, qualified_class_name: str) -> List[JType]:
        """The classes declared inside a class."""

    @abstractmethod
    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base classes a class extends."""

    @abstractmethod
    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """The interfaces a class implements."""

    @abstractmethod
    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        """All methods grouped by their owning class qualified name."""

    @abstractmethod
    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """The methods of a class."""

    @abstractmethod
    def get_method(self, qualified_class_name: str, method_signature: str) -> JCallable | None:
        """A single method of a class. ``None`` if not found."""

    @abstractmethod
    def get_method_parameters(self, qualified_class_name: str, method_signature: str) -> List[JCallableParameter]:
        """The parameters of a method. Empty list if the method is not found."""

    @abstractmethod
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """The constructors of a class."""

    @abstractmethod
    def get_all_fields(self, qualified_class_name: str) -> List[JField]:
        """The fields of a class."""

    # -----[ entry points ]-----
    @abstractmethod
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Methods identified as application entry points."""

    @abstractmethod
    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        """Classes identified as application entry points."""

    # -----[ CRUD operations ]-----
    @abstractmethod
    def get_all_crud_operations(self) -> List[CRUDRow]:
        """All CRUD operations across the application."""

    @abstractmethod
    def get_all_create_operations(self) -> List[CRUDRow]:
        """All create operations."""

    @abstractmethod
    def get_all_read_operations(self) -> List[CRUDRow]:
        """All read operations."""

    @abstractmethod
    def get_all_update_operations(self) -> List[CRUDRow]:
        """All update operations."""

    @abstractmethod
    def get_all_delete_operations(self) -> List[CRUDRow]:
        """All delete operations."""

    # -----[ comments / docstrings ]-----
    @abstractmethod
    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """All comments across the application, keyed by file."""

    @abstractmethod
    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """The comments in a file."""

    @abstractmethod
    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """The comments in a class."""

    @abstractmethod
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """The comments in a method."""

    @abstractmethod
    def get_all_docstrings(self) -> List[Tuple[str, JComment]]:
        """All docstring-style comments across the application."""

    @abstractmethod
    def remove_all_comments(self, src_code: str) -> str:
        """Strip all comments from the given source code."""
