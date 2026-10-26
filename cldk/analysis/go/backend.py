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

"""The abstract base every Go analysis backend implements.

:class:`GoAnalysisBackend` parameterises the shared, cross-language
:class:`~cldk.analysis.commons.backend.AnalysisBackend` with Go's pydantic models and sets the
Go graph vocabulary prefixes (``N="Go"`` / ``P="GO_"``). The local
:class:`~cldk.analysis.go.codeanalyzer.codeanalyzer.GoCodeanalyzer` and the read-only
``GoNeo4jBackend`` both subclass it, which is what guarantees the two backends answer the facade
identically — a ``test_go_backend_contract`` asserts every abstract method is implemented on both.

**Go is the SDK's first class-less, type-centric facade.** The *facade* vocabulary the user sees
is ``get_types`` / ``get_functions`` / ``get_methods_of`` — never ``class``. The class-named slots
on this backend (``get_all_classes`` / ``get_class`` / ``get_all_methods_in_class`` / ``get_method``)
are **inherited from the generic base ABC** and filled only so the backend can instantiate (Python
refuses an abstract class with an unimplemented ``@abstractmethod``); they map ``TypeT=GoType``
(structs + interfaces) and are never user-facing. The base has no package-level-function slot, so
:meth:`get_functions` is the one type-centric accessor added here.

**The artifact/dependency/config layer** (the inherited ``get_artifacts`` / ``get_dependencies`` /
``get_config_keys`` / ``get_config_uses`` / ``get_unresolved_config_reads``) is not emitted by a
local L1/L2 ``cango`` run: those app-scope keys are absent from ``analysis.json``. Following the
Java precedent — ``[]`` is reserved for "the pass ran and found nothing" — the local backend
**raises** :data:`ARTIFACT_LAYER_UNAVAILABLE` rather than returning an ambiguous empty; the Neo4j
backend answers them for real from its ``Artifact`` / ``Package`` / ``ConfigKey`` nodes.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, Dict, List

from cldk.analysis.commons.backend import AnalysisBackend
from cldk.models.go import (
    GoApplication,
    GoCallable,
    GoCallGraphEdge,
    GoField,
    GoModule,
    GoParam,
    GoType,
)

#: Raised by the local backend for the artifact/dependency/config accessors: a local L1/L2 cango
#: analysis does not emit that layer, so an empty result would be indistinguishable from a proved
#: absence. Both the message and the raise mirror codeanalyzer-java's unavailable-overlay pattern.
#: ``{app}`` is the application anchor name, never a can:// id.
ARTIFACT_LAYER_UNAVAILABLE = (
    "the artifact/dependency/config layer is not emitted by a local codeanalyzer-go L1/L2 analysis "
    "of {app!r}; query the Neo4j backend, which carries it"
)


class GoAnalysisBackend(AnalysisBackend[GoApplication, GoModule, GoType, GoCallable, GoField, GoParam]):
    """Abstract base every Go analysis backend implements (see the module docstring).

    Inherited abstract (from :class:`~cldk.analysis.commons.backend.AnalysisBackend`):
    ``get_application_view``, ``get_symbol_table``, ``get_call_graph``, ``get_all_classes``,
    ``get_class``, ``get_all_methods_in_class``, ``get_method``, ``get_all_fields``,
    ``get_method_parameters``, ``get_artifacts``, ``get_dependencies``, ``get_config_keys``,
    ``get_config_uses``, ``get_unresolved_config_reads``.
    """

    P: ClassVar[str] = "GO_"
    N: ClassVar[str] = "Go"

    # -----[ type-centric leaf accessors the base ABC does not cover ]-----
    @abstractmethod
    def get_functions(self) -> Dict[str, GoCallable]:
        """Every package-level function, keyed by signature.

        Go keeps package-level functions distinct from receiver methods (no module-as-class hack),
        and the generic base ABC has no slot for them — ``get_all_methods_in_class`` covers only a
        type's methods. This is the facade's ``get_functions``.
        """

    # -----[ call graph (keyed on the callable id, SDK2) ]-----
    @abstractmethod
    def get_callers(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``dst`` is ``callable_id`` — the callables that call it.

        Returns the full :class:`~cldk.models.go.GoCallGraphEdge` (``src``/``dst``/``prov``/
        ``weight``), losslessly: the caller ids are ``[e.src for e in result]``, and ``prov``/
        ``weight`` stay available. Both backends supply the same shape (the Neo4j ``GO_CALLS``
        relationship carries ``weight``/``prov`` as properties).
        """

    @abstractmethod
    def get_callees(self, callable_id: str) -> List[GoCallGraphEdge]:
        """Every call-graph edge whose ``src`` is ``callable_id`` — the callables it calls.

        Full edges, as :meth:`get_callers`; the callee ids are ``[e.dst for e in result]``.
        """

    # -----[ source + whole-program ]-----
    @abstractmethod
    def get_source(self, node_id: str) -> str:
        """The source text of the node ``node_id`` — ``module.source[node.span.bytes]``."""

    @abstractmethod
    def get_call_graph_json(self) -> str:
        """The application's call graph as a JSON string (Tier A)."""
