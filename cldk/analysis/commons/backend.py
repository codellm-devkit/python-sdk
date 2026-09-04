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

"""The generic analysis-backend contract shared across languages.

Every per-language backend ABC (:class:`~cldk.analysis.python.backend.PythonAnalysisBackend`
today; Java and TypeScript to follow) was the same interface written twice, differing only in the
concrete model types returned — ``get_all_classes()`` returns ``Dict[str, PyClass]`` in Python and
``Dict[str, JType]`` in Java, and so on. This module hoists that shared shape into one generic ABC,
parameterised per language by six type variables, so a query added to the shape has to land in
every language and in both the local and Neo4j backends, or the class stops instantiating.

This is a pure relocation: no abstract method here is new, and no behaviour changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Dict, Generic, List, Sequence, Tuple, TypeVar

import networkx as nx

from cldk.analysis.commons.results import LocateResult

AppT = TypeVar("AppT")
ModuleT = TypeVar("ModuleT")
TypeT = TypeVar("TypeT")
CallableT = TypeVar("CallableT")
FieldT = TypeVar("FieldT")
ParamT = TypeVar("ParamT")


class AnalysisBackend(ABC, Generic[AppT, ModuleT, TypeT, CallableT, FieldT, ParamT]):
    """Abstract base every language's analysis backend parameterises.

    ``P`` and ``N`` are the Neo4j relationship-type and node-label prefixes for the language's
    graph vocabulary (e.g. ``"PY"`` / ``"Py"`` for Python). They are declared here because the
    shape is shared, but only a Neo4j backend has a graph to prefix — a local, in-process backend
    has no vocabulary and leaves them unset.
    """

    P: ClassVar[str]
    N: ClassVar[str]

    # -----[ application / whole-program ]-----
    @abstractmethod
    def get_application_view(self) -> AppT:
        """The whole application view (symbol table + call graph)."""

    @abstractmethod
    def get_symbol_table(self) -> Dict[str, ModuleT]:
        """The per-file symbol table, keyed by module file path."""

    # -----[ call graph ]-----
    @abstractmethod
    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of the application's call edges."""

    # -----[ classes ]-----
    @abstractmethod
    def get_all_classes(self) -> Dict[str, TypeT]:
        """Every class, keyed by signature."""

    @abstractmethod
    def get_class(self, qualified_class_name: str) -> TypeT | None:
        """A single class by signature."""

    # -----[ methods / fields ]-----
    @abstractmethod
    def get_all_methods_in_class(self, qualified_class_name: str) -> Dict[str, CallableT]:
        """The methods of a class."""

    @abstractmethod
    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> CallableT | None:
        """A single method or module-level function."""

    @abstractmethod
    def get_all_fields(self, qualified_class_name: str) -> List[FieldT]:
        """The attributes/fields of a class."""

    @abstractmethod
    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[ParamT]:
        """The parameters of a method."""

    # -----[ graph queries ]-----
    # One template across languages (see the v2 query-facade spec, D3): a scanner alert arrives as
    # file:line and the caller needs the enclosing callable *and its source* in one round trip,
    # never an ambiguous empty. Declared here rather than per language because the shape doesn't
    # vary; only Python implements it so far (locate() ships with every language leg in turn).
    @abstractmethod
    def locate(self, path: str, line: int, col: int | None = None) -> LocateResult:
        """Resolve a source position to its enclosing callable, with the source in hand.

        Three outcomes, kept distinguishable rather than collapsed into an ambiguous empty: inside
        a callable (``callable`` set), at module scope (a real position with no enclosing
        callable — a ``module_scope`` diagnostic, never silently snapped to the nearest callable),
        or in a file the graph has no module for (``file_not_in_graph``).
        """

    @abstractmethod
    def locate_many(self, positions: Sequence[Tuple[str, int]]) -> List[LocateResult]:
        """Resolve many positions in one round trip, in input order.

        The bulk form, not an optimisation over :meth:`locate`: a scanner hands over a whole alert
        set at once, and round trips cost latency for a person and context for an agent.
        """
