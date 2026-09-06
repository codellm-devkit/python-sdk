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

:class:`JavaAnalysis` is a thin façade that delegates its static-analysis queries to a *backend*.
Two interchangeable backends exist:

* :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer` — walks the in-memory pydantic
  ``JApplication`` / a NetworkX call graph built from the ``analysis.json`` codeanalyzer-java emits;
* :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` — answers the *same* queries with Cypher over
  the graph codeanalyzer-java emits with ``--emit neo4j``.

The shape shared with every other language — application view, symbol table, call graph, the
class/method/field lookups and the repository-artifact layer — is inherited from the generic
:class:`~cldk.analysis.commons.backend.AnalysisBackend`; what is declared here is the Java-native
remainder (compilation units, the 1.x caller/callee and class-call-graph accessors, constructors,
sub/nested classes, entry points, CRUD, comments). Both backends subclass it; the façade is typed
against it. Note the façade also calls Tree-sitter directly for a few parsing helpers
(``is_parsable``, ``get_raw_ast``); those are not part of the backend contract.

``get_call_graph()`` on both backends is keyed by ``"<type fqn>.<signature>"`` strings (spec J-1),
with a ``method_detail`` (:class:`~cldk.models.java.models.JMethodDetail`) and ``kind`` node
attribute; edges carry ``type``, ``weight`` and ``calling_lines``. A local or anonymous class's
segment of that key carries the signature of the callable that declares it
(``p.Outer.m(int).$anon$0``): ``$anon$N`` is numbered per declaring callable (J-1 erratum).

**Two fields of a returned** :class:`~cldk.models.java.models.JCallable` **depend on the backend.**
Off ``analysis.json``, ``code`` is the body block and ``body`` is every body node. Off the Neo4j
projection, ``code`` is the whole *declaration* (which ends with the body block) and ``body`` holds
the ``call`` nodes only — about 30% of the graph's body nodes, enough for ``call_sites`` and
nothing else.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, Dict, List, Tuple, Union

from cldk.analysis.commons.backend import AnalysisBackend
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

#: J-4: the CRUD accessors keep their names and raise this on schema v2, on both backends.
CRUD_UNAVAILABLE = "CRUD operations are not emitted by codeanalyzer-java 3.0.1 (schema v2); tracked upstream as codeanalyzer-java#187"


def duplicate_type_name(qualified_name: str) -> str:
    """The defect message for two declarations that spell one qualified name — which would make a
    ``get_call_graph()`` node key and a ``get_class()`` key ambiguous, so it is surfaced rather than
    letting the second silently shadow the first. Both backends raise this text, identically, and
    it names only the qualified name: a ``can://`` id must not appear in a message (E6)."""
    return f"type qualified name {qualified_name!r} is declared twice: codeanalyzer-java emitted two declarations that spell one name"


def unhomed_endpoint(node_id: str) -> str:
    """The defect message for a call-graph endpoint that is not one of the application's callables.
    Both backends raise this text, identically, and it names the endpoint by the signature and
    module key its id *spells* rather than by the id itself (E6)."""
    module, sep, rest = node_id.partition(".java/")
    signature = (rest or node_id).rpartition("/")[2]
    where = f"{module.split('/', 4)[4]}.java" if sep and module.count("/") >= 4 else "no module of this application"
    return f"call-graph endpoint {signature!r} in {where!r} is not one of its callables: codeanalyzer-java emitted an unhomed endpoint"


class JavaAnalysisBackend(AnalysisBackend[JApplication, JCompilationUnit, JType, JCallable, JField, JCallableParameter]):
    """Abstract base every Java analysis backend implements.

    A backend owns all indexing and query logic for a Java application; the :class:`JavaAnalysis`
    façade delegates to it. Implementations must return the canonical ``cldk.models.java`` pydantic
    objects (or the documented NetworkX / dict / list shapes) so backends are behaviorally
    interchangeable.

    Inherited abstract (see :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "J"
    N: ClassVar[str] = "J"

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_compilation_units(self) -> List[JCompilationUnit]:
        """All compilation units."""

    @abstractmethod
    def get_java_file(self, qualified_class_name: str) -> str | None:
        """The (repo-relative) file path declaring a class. ``None`` if the class is not found."""

    @abstractmethod
    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """The compilation unit for a file path."""

    # -----[ call graph ]-----
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
        """Call-graph edges out of a class (or one of its methods)."""

    @abstractmethod
    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Call-graph edges out of a class, computed from the symbol table's call sites only."""

    # -----[ classes / methods / fields ]-----
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
    def get_all_constructors(self, qualified_class_name: str) -> Dict[str, JCallable]:
        """The constructors of a class."""

    # -----[ entry points ]-----
    @abstractmethod
    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Methods identified as application entry points."""

    @abstractmethod
    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        """Classes identified as application entry points."""

    # -----[ CRUD operations — J-4: raise CRUD_UNAVAILABLE on schema v2 ]-----
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
    # J-16, the one rule, split by whether a *smaller* answer is still an answer under the name.
    # A backend that keeps only per-declaration javadoc (the Neo4j projection) can answer the three
    # **declaration-keyed** accessors with a javadoc-only subset — narrower than "every comment in
    # this class", but a real answer about a real declaration. It cannot answer the two
    # **file-keyed** ones at all: it holds nothing file-level, so every answer would be an empty
    # list reading as "this file has no comments" (D7). Those two therefore raise.
    @abstractmethod
    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """All comments across the application, keyed by file.

        Raises:
            CodeanalyzerExecutionException: If the backend's source carries no file-level comments
                at all (the Neo4j projection does not), naming what is missing and what to read
                instead. Returning the per-declaration javadoc under this name would be a silent
                partial (J-16).
        """

    @abstractmethod
    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """The comments in a file.

        Raises:
            CodeanalyzerExecutionException: As :meth:`get_all_comments` does, and for the same
                reason (J-16).
        """

    @abstractmethod
    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """The comments in a class. Returns an empty list if the class is not found.

        A backend whose source keeps only per-declaration javadoc returns **just the javadoc** — a
        strictly smaller set than every comment in the class body, and a real answer rather than a
        refusal (J-16). :class:`~cldk.analysis.java.neo4j.JNeo4jBackend` is such a backend.
        """

    @abstractmethod
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """The comments in a method. Returns an empty list if the method is not found.

        Narrows to javadoc only on a javadoc-only backend, exactly as
        :meth:`get_comments_in_a_class` does (J-16).
        """

    @abstractmethod
    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        """All Javadoc comments across the application, keyed by file.

        Which javadoc depends on what the backend's source keeps: the in-memory backend reports
        each compilation unit's own comment list (holding the *file-level* javadoc), the Neo4j
        backend the javadoc of each *declaration* in the file. Both are javadoc keyed by file, and
        they are different sets for the same file (J-16).
        """

    @abstractmethod
    def remove_all_comments(self, src_code: str) -> str:
        """Strip all comments from the given source code."""
