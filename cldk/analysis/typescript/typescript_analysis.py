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

"""TypeScript analysis facade.

Thin, read-only query layer over the canonical ``TSApplication`` produced by the
codeanalyzer-typescript backend. Mirrors the method vocabulary of ``JavaAnalysis`` /
``PythonAnalysis`` (there is no shared base class — the facades match by convention) and, like
those, delegates all indexing and query work to its backend (:class:`TSCodeanalyzer`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple

import networkx as nx

from cldk.analysis.typescript.backend import TSAnalysisBackend
from cldk.analysis.typescript.codeanalyzer import TSCodeanalyzer
from cldk.analysis.typescript.neo4j import Neo4jConnectionConfig, TSNeo4jBackend, TSNeo4jIngestor
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


class TypeScriptAnalysis:
    """Analysis facade for TypeScript projects.

    Delegates every query to a backend. Two interchangeable backends exist, both exposing the
    same method surface:

    * :class:`TSCodeanalyzer` (default) — walks the in-memory pydantic ``TSApplication`` / a
      NetworkX call graph built from ``analysis.json``;
    * :class:`TSNeo4jBackend` — answers the *same* ``get_*`` queries with Cypher over the graph
      ``codeanalyzer-typescript`` emits with ``--emit neo4j``. Selected by passing
      ``neo4j_config``.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        analysis_level: str,
        analysis_backend_path: str | None,
        analysis_json_path: str | Path | None,
        target_files: List[str] | None,
        eager_analysis: bool,
        neo4j_config: Neo4jConnectionConfig | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.analysis_backend_path = analysis_backend_path
        self.analysis_json_path = analysis_json_path
        self.target_files = target_files
        self.eager_analysis = eager_analysis
        self.neo4j_config = neo4j_config
        self.backend: TSAnalysisBackend
        if neo4j_config is not None:
            application_name = neo4j_config.application_name or (Path(project_dir).name if project_dir else None)
            # Local/dev convenience: populate the graph from sources before querying it. In a cloud
            # deployment the graph is loaded out of band, so pass build_db=False and we only read.
            if neo4j_config.build_db:
                TSNeo4jIngestor(
                    project_dir=project_dir,
                    analysis_backend_path=analysis_backend_path,
                    analysis_level=analysis_level,
                    neo4j_uri=neo4j_config.uri,
                    neo4j_username=neo4j_config.username,
                    neo4j_password=neo4j_config.password,
                    neo4j_database=neo4j_config.database,
                    application_name=application_name,
                    eager_analysis=eager_analysis,
                    target_files=target_files,
                ).build()
            self.backend = TSNeo4jBackend(
                neo4j_uri=neo4j_config.uri,
                neo4j_username=neo4j_config.username,
                neo4j_password=neo4j_config.password,
                neo4j_database=neo4j_config.database,
                application_name=application_name,
            )
        else:
            self.backend = TSCodeanalyzer(
                project_dir=project_dir,
                analysis_backend_path=analysis_backend_path,
                analysis_json_path=analysis_json_path,
                analysis_level=analysis_level,
                eager_analysis=eager_analysis,
                target_files=target_files,
            )
        self.application: TSApplication = self.backend.get_application()

    # -----[ Tier A: lifecycle / whole-program ]-----
    def get_application_view(self) -> TSApplication:
        return self.backend.get_application()

    def get_symbol_table(self) -> Dict[str, TSModule]:
        return self.backend.get_symbol_table()

    def get_modules(self) -> List[TSModule]:
        return self.backend.get_modules()

    def get_call_graph(self) -> nx.DiGraph:
        """NetworkX DiGraph of callable signatures (and phantom external symbols) connected by the
        identity-only call edges."""
        return self.backend.get_call_graph()

    def get_external_symbols(self) -> Dict[str, TSExternalSymbol]:
        """The phantom (external) call targets — imported/required library members the call graph
        points at (e.g. ``node:fs.readFileSync``, ``js-yaml.load``). Useful for source→sink
        reachability."""
        return self.backend.get_external_symbols()

    def get_call_graph_json(self) -> str:
        return self.backend.get_call_graph_json()

    def get_callers(self, target_class_name: str, target_method_declaration: str | None = None) -> Dict:
        """Callers of a method, with the connecting call-graph edge metadata (``provenance`` /
        ``tags``). Pass a bare signature as the first argument for module-level functions or
        external (phantom) targets."""
        return self.backend.get_all_callers(target_class_name, target_method_declaration)

    def get_callees(self, source_class_name: str, source_method_declaration: str | None = None) -> Dict:
        """Callees of a method, with the connecting call-graph edge metadata."""
        return self.backend.get_all_callees(source_class_name, source_method_declaration)

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None) -> List[Tuple[str, str]]:
        """Call-graph edges reachable from a class (or one of its methods)."""
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Inheritance/implementation graph: an edge child → base for every base_class."""
        return self.backend.get_class_hierarchy()

    # -----[ call sites ]-----
    def get_call_sites(self, qualified_callable_name: str) -> List[TSCallsite]:
        """The rich, syntactic call sites *inside* a callable (receiver/argument types, resolved
        ``callee_signature``, source position)."""
        return self.backend.get_call_sites(qualified_callable_name)

    def get_calling_lines(self, target_signature: str) -> List[int]:
        """Sorted source lines anywhere in the project where ``target_signature`` is invoked."""
        return self.backend.get_calling_lines(target_signature)

    def get_call_targets(self, source_signature: str) -> Set[str]:
        """The call targets invoked from a callable, derived from its call sites."""
        return self.backend.get_call_targets(source_signature)

    # -----[ entrypoints (not yet supported) ]-----
    def get_entry_point_methods(self) -> Dict[str, Dict[str, TSCallable]]:
        """Return methods identified as application entry points.

        Not yet supported: the codeanalyzer-typescript backend's entrypoint detection is a stub
        placeholder — the ``entrypoints`` list on each ``TSCallable``/``TSClass`` is always empty
        (level-2 finders are not implemented) — so this method exists for API parity with
        :class:`PythonAnalysis` / :class:`JavaAnalysis` but raises.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Entrypoint detection is not implemented in the codeanalyzer-typescript backend yet.")

    def get_service_entry_point_methods(self, **kwargs) -> Dict[str, Dict[str, TSCallable]]:
        """Return methods that serve as service entry points (e.g. Express/NestJS routes).

        Not yet supported; see :meth:`get_entry_point_methods`.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError("Entrypoint detection is not implemented in the codeanalyzer-typescript backend yet.")

    # -----[ Tier B: navigation ]-----
    def get_classes(self) -> Dict[str, TSClass]:
        return self.backend.get_all_classes()

    def get_class(self, qualified_class_name: str) -> TSClass | None:
        return self.backend.get_class(qualified_class_name)

    def get_classes_by_criteria(self, inclusions: List[str] | None = None, exclusions: List[str] | None = None) -> Dict[str, TSClass]:
        inclusions = inclusions or []
        exclusions = exclusions or []
        result: Dict[str, TSClass] = {}
        for sig, cls in self.backend.get_all_classes().items():
            selected = any(inc in sig for inc in inclusions)
            if any(exc in sig for exc in exclusions):
                selected = False
            if selected:
                result[sig] = cls
        return result

    def get_interfaces(self) -> Dict[str, TSInterface]:
        return self.backend.get_all_interfaces()

    def get_enums(self) -> Dict[str, TSEnum]:
        return self.backend.get_all_enums()

    def get_enum_members(self, qualified_enum_name: str) -> List[TSEnumMember]:
        return self.backend.get_enum_members(qualified_enum_name)

    def get_type_aliases(self) -> Dict[str, TSTypeAlias]:
        return self.backend.get_all_type_aliases()

    def get_functions(self) -> Dict[str, TSCallable]:
        """Top-level (module/namespace) functions."""
        return self.backend.get_all_functions()

    def get_methods(self) -> Dict[str, Dict[str, TSCallable]]:
        """All methods grouped by class/interface signature."""
        return self.backend.get_all_methods_in_application()

    def get_methods_in_class(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> TSCallable | None:
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        return self.backend.get_method_parameters(qualified_class_name, qualified_method_name)

    def get_constructors(self, qualified_class_name: str) -> Dict[str, TSCallable]:
        return self.backend.get_all_constructors(qualified_class_name)

    def get_fields(self, qualified_class_name: str) -> List[TSClassAttribute]:
        return self.backend.get_all_fields(qualified_class_name)

    def get_interface_properties(self, qualified_interface_name: str) -> List[TSClassAttribute]:
        return self.backend.get_interface_properties(qualified_interface_name)

    def get_imports(self) -> Dict[str, List[TSImport]]:
        return self.backend.get_imports()

    def get_exports(self) -> Dict[str, List[TSExport]]:
        return self.backend.get_all_exports()

    def get_variables(self) -> Dict[str, List[TSVariableDeclaration]]:
        """Module-level variable declarations per file."""
        return self.backend.get_all_variables()

    def get_typescript_file(self, qualified_name: str) -> str | None:
        """File path declaring the class/interface/enum/callable with the given signature."""
        return self.backend.get_typescript_file(qualified_name)

    def get_typescript_module(self, file_path: str) -> TSModule | None:
        return self.backend.get_typescript_module(file_path)

    def get_nested_classes(self, qualified_class_name: str) -> List[TSClass]:
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name: str) -> Dict[str, TSClass]:
        return self.backend.get_all_sub_classes(qualified_class_name)

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """The base types a class extends (base_classes minus the implemented interfaces)."""
        return self.backend.get_extended_classes(qualified_class_name)

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        return self.backend.get_implemented_interfaces(qualified_class_name)

    # -----[ decorators ]-----
    def get_decorators(self, qualified_callable_name: str) -> List[TSDecorator]:
        """Structured decorators (with arguments) applied to a callable."""
        return self.backend.get_decorators(qualified_callable_name)

    def get_class_decorators(self, qualified_class_name: str) -> List[TSDecorator]:
        """Structured decorators (with arguments) applied to a class."""
        return self.backend.get_class_decorators(qualified_class_name)

    def get_methods_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of callables carrying it. TS
        decorators are captured structurally, so this is populatable at level 1."""
        return self.backend.get_methods_with_decorators(decorators)

    def get_classes_with_decorators(self, decorators: List[str]) -> Dict[str, List[str]]:
        """Map each requested decorator name to the signatures of classes carrying it."""
        return self.backend.get_classes_with_decorators(decorators)
