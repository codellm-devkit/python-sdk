################################################################################
# Copyright IBM Corporation 2024
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

"""Python analysis façade.

Mirrors the API surface of :class:`cldk.analysis.java.java_analysis.JavaAnalysis`
on top of the ``codeanalyzer-python`` backend. Single-file ``source_code``
mode is intentionally not supported — analysis always runs against a project
directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple

import networkx as nx
from tree_sitter import Tree

from cldk.analysis.commons.treesitter import TreesitterPython
from cldk.analysis.python.codeanalyzer import PyCodeanalyzer
from cldk.models.python import (
    PyApplication,
    PyCallable,
    PyClass,
    PyClassAttribute,
    PyComment,
    PyModule,
)


class PythonAnalysis:
    """Analysis façade for Python projects.

    Args:
        project_dir: Directory path of the project (required).
        cache_dir: Cache home for ``codeanalyzer-python`` — its virtualenv,
            CodeQL database, and ``analysis_cache.json`` (forwarded as the
            backend's ``cache_dir``). The backend owns all caching. If None,
            it defaults to ``<project_dir>/.codeanalyzer``.
        analysis_json_path: Forwarded to the backend's ``output``. CLDK keeps
            no cache of its own.
        analysis_level: Analysis level (symbol-table or call-graph).
        target_files: Optional list of target files to constrain analysis.
        eager_analysis: If True, regenerate analysis.json on each run.
    """

    def __init__(
        self,
        project_dir: str | Path,
        cache_dir: str | Path | None,
        analysis_json_path: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
        use_codeql: bool = True,
    ) -> None:
        if project_dir is None:
            raise ValueError(
                "project_dir is required; source_code mode is not supported for Python."
            )
        self.project_dir = project_dir
        self.analysis_level = analysis_level
        self.analysis_json_path = analysis_json_path
        self.cache_dir = cache_dir
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.treesitter_python: TreesitterPython = TreesitterPython()
        self.backend: PyCodeanalyzer = PyCodeanalyzer(
            project_dir=project_dir,
            analysis_level=analysis_level,
            analysis_json_path=analysis_json_path,
            eager_analysis=eager_analysis,
            cache_dir=cache_dir,
            target_files=target_files,
            use_codeql=use_codeql,
        )

    # -----[ treesitter passthrough ]-----
    def is_parsable(self, source_code: str) -> bool:
        """Return True when ``source_code`` parses as Python."""
        return self.treesitter_python.is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> Tree:
        """Return the raw tree-sitter AST for ``source_code``."""
        return self.treesitter_python.get_raw_ast(source_code)

    # -----[ application view ]-----
    def get_application_view(self) -> PyApplication:
        """Return the analyzed :class:`PyApplication`."""
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, PyModule]:
        """Return the symbol table keyed by file path."""
        return self.backend.get_symbol_table()

    def get_modules(self) -> List[PyModule]:
        """Return all modules."""
        return self.backend.get_modules()

    def get_python_file(self, qualified_class_name: str) -> str | None:
        """Return the file path containing the given class signature."""
        return self.backend.get_python_file(qualified_class_name)

    def get_python_module(self, file_path: str) -> PyModule | None:
        """Return the :class:`PyModule` for the given file path."""
        return self.backend.get_python_module(file_path)

    # -----[ imports ]-----
    def get_imports(self) -> Dict[str, List]:
        """Return imports for each module in the application."""
        return {
            fp: list(m.imports) for fp, m in self.backend.get_symbol_table().items()
        }

    # -----[ call graph ]-----
    def get_call_graph(self) -> nx.DiGraph:
        """Return the call graph as a directed NetworkX graph."""
        return self.backend.get_call_graph()

    def get_call_graph_json(self) -> str:
        """Return the analysis serialized to JSON."""
        return self.backend.get_call_graph_json()

    def get_callers(
        self, target_class_name: str, target_method_declaration: str
    ) -> Dict:
        """Return callers of the target method."""
        return self.backend.get_all_callers(
            target_class_name, target_method_declaration
        )

    def get_callees(
        self, source_class_name: str, source_method_declaration: str
    ) -> Dict:
        """Return callees of the source method."""
        return self.backend.get_all_callees(
            source_class_name, source_method_declaration
        )

    def get_class_call_graph(
        self, qualified_class_name: str, method_signature: str | None = None
    ) -> List[Tuple[str, str]]:
        """Return an edge list reachable from a class (and optionally a method)."""
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    # -----[ methods ]-----
    def get_methods(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return all methods grouped by class signature."""
        return self.backend.get_all_methods_in_application()

    def get_methods_in_class(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return methods of the given class."""
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_method(
        self, qualified_class_name: str, qualified_method_name: str
    ) -> PyCallable | None:
        """Return the :class:`PyCallable` for the given class+method signatures."""
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(
        self, qualified_class_name: str, qualified_method_name: str
    ) -> List[str]:
        """Return parameter names for the given method."""
        return self.backend.get_method_parameters(
            qualified_class_name, qualified_method_name
        )

    def get_constructors(self, qualified_class_name: str) -> Dict[str, PyCallable]:
        """Return ``__init__`` methods of the given class."""
        return self.backend.get_all_constructors(qualified_class_name)

    # -----[ classes ]-----
    def get_classes(self) -> Dict[str, PyClass]:
        """Return all classes keyed by qualified signature."""
        return self.backend.get_all_classes()

    def get_class(self, qualified_class_name: str) -> PyClass | None:
        """Return the :class:`PyClass` for the given qualified signature."""
        return self.backend.get_class(qualified_class_name)

    def get_classes_by_criteria(
        self, inclusions=None, exclusions=None
    ) -> Dict[str, PyClass]:
        """Return classes whose qualified name matches inclusion/exclusion filters."""
        inclusions = inclusions or []
        exclusions = exclusions or []
        result: Dict[str, PyClass] = {}
        for sig, cls in self.backend.get_all_classes().items():
            selected = any(inc in sig for inc in inclusions)
            if any(exc in sig for exc in exclusions):
                selected = False
            if selected:
                result[sig] = cls
        return result

    def get_fields(self, qualified_class_name: str) -> List[PyClassAttribute]:
        """Return class attributes for the given class."""
        return self.backend.get_all_fields(qualified_class_name)

    def get_nested_classes(self, qualified_class_name: str) -> List[PyClass]:
        """Return inner classes of the given class."""
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name: str) -> Dict[str, PyClass]:
        """Return classes that subclass the given class."""
        return self.backend.get_all_sub_classes(qualified_class_name)

    def get_extended_classes(self, qualified_class_name: str) -> List[str]:
        """Return base class names of the given class."""
        return self.backend.get_extended_classes(qualified_class_name)

    # -----[ unsupported ]-----
    def get_class_hierarchy(self) -> nx.DiGraph:
        """Return the class hierarchy. Not implemented."""
        raise NotImplementedError("Class hierarchy is not implemented yet.")

    def get_service_entry_point_classes(self, **kwargs):
        """Return service entry-point classes. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_service_entry_point_methods(self, **kwargs):
        """Return service entry-point methods. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_entry_point_classes(self) -> Dict[str, PyClass]:
        """Return entry-point classes. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_entry_point_methods(self) -> Dict[str, Dict[str, PyCallable]]:
        """Return entry-point methods. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """Java parity stub — Python has no separate interface concept."""
        raise NotImplementedError(
            "Python does not distinguish interfaces from base classes; use get_extended_classes."
        )

    def get_methods_with_decorators(
        self, decorators: List[str]
    ) -> Dict[str, List[Dict]]:
        """Return methods carrying the given decorators. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_test_methods(self) -> Dict[str, str]:
        """Return test methods. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """Return line numbers calling a method. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """Return call targets via simple name resolution. Not implemented."""
        raise NotImplementedError(
            "Support for this functionality has not been implemented yet."
        )

    def get_all_crud_operations(self):
        """Return CRUD operations. Not implemented."""
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_create_operations(self):
        """Return create operations. Not implemented."""
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_read_operations(self):
        """Return read operations. Not implemented."""
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_update_operations(self):
        """Return update operations. Not implemented."""
        raise NotImplementedError("CRUD analysis is not supported for Python.")

    def get_all_delete_operations(self):
        """Return delete operations. Not implemented."""
        raise NotImplementedError("CRUD analysis is not supported for Python.")
