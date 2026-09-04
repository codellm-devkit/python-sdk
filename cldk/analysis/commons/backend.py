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

Originally a pure relocation (no abstract method new, no behaviour changed); the repository-artifact
getters below are the first genuinely new addition, for the reasons given after the ``locate``
discussion.

What does *not* belong here is a query whose return type is one language's models. ``locate`` /
``locate_many`` (the v2 query-facade spec's D3) is declared on
:class:`~cldk.analysis.python.backend.PythonAnalysisBackend` instead, because
:class:`~cldk.analysis.commons.results.LocateResult` carries ``codeanalyzer-python``'s ``BodyNode``
and ``Span``: hoisted here it would be a shared contract Java and TypeScript cannot satisfy as
typed. It is hoisted when a second language implements it and the language-neutral shape of a body
node and a span is known from two examples rather than guessed from one.

The repository-artifact getters below (``get_artifacts`` / ``get_dependencies`` /
``get_config_keys`` / ``get_config_uses`` / ``get_unresolved_config_reads``) are the opposite case,
even though they are typed on ``cldk.models.python``'s ``Py*`` classes today. Unlike
``PyModule``/``PyClass``/``BodyNode``, those models are not Python-specific shapes wearing a ``Py``
prefix out of habit:
``codeanalyzer-python``'s own schema module documents the ``Py`` on ``PyArtifact`` / ``PyConfigKey``
as "naming precedent, not a Python-specific claim," and the Neo4j labels/relationship-types they
mirror (``Artifact``, ``ConfigKey``, ``Package``, ``HAS_ARTIFACT``, ``DECLARES_DEPENDENCY``,
``DEFINES_CONFIG``, ``LOCKS``) are the one part of the graph every analyzer projects identically,
with no ``P``/``N`` prefix at all. So this hoist is not "guessed from one example" the way a second
``locate`` implementation would be — it is one example of a layer already built, contractually,
to be reused verbatim. Java/TypeScript are expected to return these same four classes unchanged
when they implement this layer, not language-specific equivalents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Dict, Generic, List, TypeVar

import networkx as nx

from cldk.models.python import PyArtifact, PyConfigKey, PyConfigRead, PyConfigUseEdge, PyDependency

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

    # -----[ repository artifacts ]-----
    # See the module docstring for why these are typed on cldk.models.python's Py* classes despite
    # living on the generic ABC.
    @abstractmethod
    def get_artifacts(self) -> Dict[str, PyArtifact]:
        """Every non-code project artifact (manifest, config file, lockfile, ...), keyed by its
        repo-relative path."""

    @abstractmethod
    def get_dependencies(
        self, *, direct_only: bool = False, ecosystem: str | None = None, declared_in: str | None = None
    ) -> List[PyDependency]:
        """Every declared third-party dependency, one entry per declaring manifest.

        All three filters default to "don't filter" -- a pure widening of the original
        zero-argument signature, so every existing call keeps working unchanged.

        Args:
            direct_only: When ``True``, excludes lockfile-only transitive pins
                (``PyDependency.direct is False``).
            ecosystem: When given, only dependencies from this package ecosystem (e.g.
                ``"pypi"``) -- matched against the real ``Package.ecosystem`` graph property /
                ``PyDependency.ecosystem`` field, never a hardcoded assumption.
            declared_in: When given, only dependencies declared by this artifact id
                (``PyDependency.declared_in``, e.g. from :meth:`get_artifacts`).
        """

    @abstractmethod
    def get_config_keys(self) -> Dict[str, PyConfigKey]:
        """Every configuration key flattened out of a config-bearing artifact, keyed by its id
        (``<artifact-id>@key/<dotted.key>``) — a bare ``key`` (e.g. ``"DB_URL"``) is not unique
        across artifacts/namespaces, so the id is the dict key."""

    @abstractmethod
    def get_config_uses(self, key: str | None = None) -> List[PyConfigUseEdge]:
        """Resolved code-to-config edges: which body node reads which config key.

        Args:
            key: When given, only edges whose target :class:`PyConfigKey` has this bare ``key``
                (e.g. ``"DB_URL"``) — matched against :meth:`get_config_keys`, since
                :class:`PyConfigUseEdge` itself carries only ``src``/``dst``/``prov``, not the key
                text. ``None`` (default) returns every edge.
        """

    @abstractmethod
    def get_unresolved_config_reads(self) -> List[PyConfigRead]:
        """Detector-matched config reads that never closed on exactly one declared key -- the
        failure case :meth:`get_config_uses` cannot show, so that method's empty result stays
        ambiguous between "nothing reads this key" and "a read exists but the analyzer couldn't
        resolve it" without this sibling.

        Distinct from a missing declaration: this is a call the detector matched (e.g.
        ``os.getenv(...)``) whose key argument either never closed on a literal at all
        (``reason="non-literal"``) or closed on one that matches no declared
        :class:`PyConfigKey` (``reason="undefined-key"``, with ``key`` populated).
        """
