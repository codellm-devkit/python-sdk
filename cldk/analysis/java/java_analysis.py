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

"""Java analysis utilities.

Provides a high-level API to analyze Java projects or single-source inputs
using a Treesitter-based parser and the Code Analyzer backend.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Set, Union
import networkx as nx

from tree_sitter import Tree

from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.models.java import JCallable
from cldk.models.java import JApplication
from cldk.models.java.models import JCRUDOperation, JComment, JCompilationUnit, JMethodDetail, JType, JField
from cldk.analysis.java.codeanalyzer import JCodeanalyzer


class JavaAnalysis:
    """Analysis façade for Java code.

    This class exposes methods to query symbol tables, classes, methods,
    call graphs, comments, and CRUD operations for a Java project or a single
    source file, depending on the initialization parameters.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        source_code: str | None,
        analysis_backend_path: str | None,
        analysis_json_path: str | Path | None,
        analysis_level: str,
        target_files: List[str] | None,
        eager_analysis: bool,
    ) -> None:
        """Initialize the Java analysis backend.

        Args:
            project_dir (str | Path | None): Directory path of the project.
            source_code (str | None): Source text for single-file analysis.
            analysis_backend_path (str | None): Path to the analysis backend. For
                CodeQL, the CLI must be installed and on PATH. For CodeAnalyzer,
                the JAR is downloaded from the latest release when not provided.
            analysis_json_path (str | Path | None): Path to persist the analysis
                database (analysis.json). If None, results are not persisted.
            analysis_level (str): Analysis level. For example, "symbol-table" or
                "call-graph".
            target_files (list[str] | None): Optional list of target file paths to
                constrain analysis (primarily supported for symbol-table).
            eager_analysis (bool): If True, forces regeneration of analysis.json
                on each run even if it exists.

        Raises:
            NotImplementedError: If the requested analysis backend is unsupported.
        """

        self.project_dir = project_dir
        self.source_code = source_code
        self.analysis_level = analysis_level
        self.analysis_json_path = analysis_json_path
        self.analysis_backend_path = analysis_backend_path
        self.eager_analysis = eager_analysis
        self.target_files = target_files
        self.treesitter_java: TreesitterJava = TreesitterJava()
        # Initialize the analysis analysis_backend
        self.backend: JCodeanalyzer = JCodeanalyzer(
            project_dir=self.project_dir,
            source_code=self.source_code,
            eager_analysis=self.eager_analysis,
            analysis_level=self.analysis_level,
            analysis_json_path=self.analysis_json_path,
            analysis_backend_path=self.analysis_backend_path,
            target_files=self.target_files,
        )

    def get_imports(self) -> List[str]:
        """Return all import statements in the source code.

        Returns:
            list[str]: All import statements.

        Raises:
            NotImplementedError: If this functionality is not supported.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_variables(self, **kwargs):
        """Return all variables discovered in the source code.

        Returns:
            Any: Implementation-defined variable view.

        Raises:
            NotImplementedError: If this functionality is not supported.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_service_entry_point_classes(self, **kwargs):
        """Return all service entry-point classes.

        Returns:
            Any: Implementation-defined list or mapping of service classes.

        Raises:
            NotImplementedError: If this functionality is not supported.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_service_entry_point_methods(self, **kwargs):
        """Return all service entry-point methods.

        Returns:
            Any: Implementation-defined list or mapping of service methods.

        Raises:
            NotImplementedError: If this functionality is not supported.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_application_view(self) -> JApplication:
        """Return the application view of the Java code.

        Returns:
            JApplication: Application view of the Java code.

        Raises:
            NotImplementedError: If single-file mode is used (unsupported here).

        Examples:
            Get an application view using a project directory (backend required):

            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.symbol_table,
            ...                  target_files=None, eager_analysis=False)
            >>> isinstance(ja.source_code, type(None))
            True
        """
        if self.source_code:
            raise NotImplementedError("Support for this functionality has not been implemented yet.")
        return self.backend.get_application_view()

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        """Return the symbol table.

        Returns:
            dict[str, JCompilationUnit]: Symbol table keyed by file path.

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.symbol_table,
            ...                  target_files=None, eager_analysis=False)
            >>> isinstance(ja.get_symbol_table(), dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_symbol_table()

    def get_compilation_units(self) -> List[JCompilationUnit]:
        """Return all compilation units in the Java code.

        Returns:
            list[JCompilationUnit]: Compilation units of the Java code.

        Examples:
            List all compilation units for a project (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> isinstance(ja.get_compilation_units(), list)  # doctest: +SKIP
            True
        """
        return self.backend.get_compilation_units()

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Return the class hierarchy of the Java code.

        Returns:
            networkx.DiGraph: Class hierarchy.

        Raises:
            NotImplementedError: Always, as the feature is not implemented yet.
        """

        raise NotImplementedError("Class hierarchy is not implemented yet.")

    def is_parsable(self, source_code: str) -> bool:
        """Check if the source code is parsable.

        Args:
            source_code (str): Source code to parse.

        Returns:
            bool: True if parsable, False otherwise.

        Examples:
            >>> src = 'class A { void f(){} }'
            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(source_code=src)
            >>> ja.is_parsable(src)
            True
        """
        return self.treesitter_java.is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> Tree:
        """Parse and return the raw AST.

        Args:
            source_code (str): Source code to parse.

        Returns:
            Tree: Raw syntax tree.

        Examples:
            >>> src = 'class A { void f(){} }'
            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(source_code=src)
            >>> ast = ja.get_raw_ast(src)
            >>> ast.root_node is not None
            True
        """
        return self.treesitter_java.get_raw_ast(source_code)

    def get_call_graph(self) -> nx.DiGraph:
        """Return the call graph of the Java code.

        Returns:
            networkx.DiGraph: Call graph.

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.call_graph,
            ...                  target_files=None, eager_analysis=False)
            >>> isinstance(ja.get_call_graph(), nx.DiGraph)  # doctest: +SKIP
            True
        """
        return self.backend.get_call_graph()

    def get_call_graph_json(self) -> str:
        """Return the call graph serialized as JSON.

        Returns:
            str: Call graph encoded as JSON.

        Raises:
            NotImplementedError: If single-file mode is used (unsupported here).

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.call_graph,
            ...                  target_files=None, eager_analysis=False)
            >>> isinstance(ja.get_call_graph_json(), str)  # doctest: +SKIP
            True
        """
        if self.source_code:
            raise NotImplementedError("Producing a call graph over a single file is not implemented yet.")
        return self.backend.get_call_graph_json()

    def get_callers(self, target_class_name: str, target_method_declaration: str, using_symbol_table: bool = False) -> Dict:
        """Return all callers of a target method.

        Args:
            target_class_name (str): Qualified name of the target class.
            target_method_declaration (str): Target method signature.
            using_symbol_table (bool): Whether to use the symbol table. Defaults to False.

        Returns:
            dict: Mapping of callers to call sites/details.

        Raises:
            NotImplementedError: If single-file mode is used (unsupported here).

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.call_graph,
            ...                  target_files=None, eager_analysis=False)
            >>> callers = ja.get_callers('com.example.A', 'f()')  # doctest: +SKIP
            >>> isinstance(callers, dict)  # doctest: +SKIP
            True
        """

        if self.source_code:
            raise NotImplementedError("Generating all callers over a single file is not implemented yet.")
        return self.backend.get_all_callers(target_class_name, target_method_declaration, using_symbol_table)

    def get_callees(self, source_class_name: str, source_method_declaration: str, using_symbol_table: bool = False) -> Dict:
        """Return all callees of a given method.

        Args:
            source_class_name (str): Qualified class name containing the method.
            source_method_declaration (str): Method signature.
            using_symbol_table (bool): Whether to use the symbol table. Defaults to False.

        Returns:
            dict: Mapping of callees to call sites/details.

        Raises:
            NotImplementedError: If single-file mode is used (unsupported here).

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.call_graph,
            ...                  target_files=None, eager_analysis=False)
            >>> callees = ja.get_callees('com.example.A', 'f()')  # doctest: +SKIP
            >>> isinstance(callees, dict)  # doctest: +SKIP
            True
        """
        if self.source_code:
            raise NotImplementedError("Generating all callees over a single file is not implemented yet.")
        return self.backend.get_all_callees(source_class_name, source_method_declaration, using_symbol_table)

    def get_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Return all methods in the Java code.

        Returns:
            dict[str, dict[str, JCallable]]: Methods grouped by qualified class name.

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.symbol_table,
            ...                  target_files=None, eager_analysis=False)
            >>> methods = ja.get_methods()  # doctest: +SKIP
            >>> isinstance(methods, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_methods_in_application()

    def get_classes(self) -> Dict[str, JType]:
        """Return all classes in the Java code.

        Returns:
            dict[str, JType]: Classes keyed by qualified class name.

        Examples:
            >>> from cldk.analysis import AnalysisLevel
            >>> ja = JavaAnalysis(project_dir='path/to/project', source_code=None,
            ...                  analysis_backend_path=None, analysis_json_path=None,
            ...                  analysis_level=AnalysisLevel.symbol_table,
            ...                  target_files=None, eager_analysis=False)
            >>> classes = ja.get_classes()  # doctest: +SKIP
            >>> isinstance(classes, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_classes()

    def get_classes_by_criteria(self, inclusions=None, exclusions=None) -> Dict[str, JType]:
        """Return classes filtered by simple inclusion/exclusion criteria.

        Args:
            inclusions (list[str] | None): If provided, only classes whose name
                contains any of these substrings are included.
            exclusions (list[str] | None): If provided, classes whose name contains
                any of these substrings are excluded.

        Returns:
            dict[str, JType]: Matching classes keyed by qualified class name.

        Examples:
            Filter classes using simple contains checks (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> filtered = ja.get_classes_by_criteria(inclusions=['Service'], exclusions=['Test'])  # doctest: +SKIP
            >>> isinstance(filtered, dict)  # doctest: +SKIP
            True
        """
        if exclusions is None:
            exclusions = []
        if inclusions is None:
            inclusions = []
        class_dict: Dict[str, JType] = {}
        all_classes = self.backend.get_all_classes()
        for application_class in all_classes:
            is_selected = False
            for inclusion in inclusions:
                if inclusion in application_class:
                    is_selected = True

            for exclusion in exclusions:
                if exclusion in application_class:
                    is_selected = False
            if is_selected:
                class_dict[application_class] = all_classes[application_class]
        return class_dict

    def get_class(self, qualified_class_name: str) -> JType:
        """Return a class object.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            JType: Class object for the given class.

        Examples:
            Look up a class by its qualified name (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> c = ja.get_class('com.example.A')  # doctest: +SKIP
            >>> c is not None  # doctest: +SKIP
            True
        """

        return self.backend.get_class(qualified_class_name)

    def get_method(self, qualified_class_name: str, qualified_method_name: str) -> JCallable:
        """Return a method object.

        Args:
            qualified_class_name (str): Qualified class name.
            qualified_method_name (str): Qualified method name.

        Returns:
            JCallable: Method object.

        Examples:
            Look up a method by its qualified signature (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> m = ja.get_method('com.example.A', 'f()')  # doctest: +SKIP
            >>> m is not None  # doctest: +SKIP
            True
        """
        return self.backend.get_method(qualified_class_name, qualified_method_name)

    def get_method_parameters(self, qualified_class_name: str, qualified_method_name: str) -> List[str]:
        """Return the parameter types/names for a method.

        Args:
            qualified_class_name (str): Qualified class name.
            qualified_method_name (str): Qualified method name.

        Returns:
            list[str]: Method parameters.

        Examples:
            List parameters for a method (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> params = ja.get_method_parameters('com.example.A', 'g(int, java.lang.String)')  # doctest: +SKIP
            >>> isinstance(params, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_method_parameters(qualified_class_name, qualified_method_name)

    def get_java_file(self, qualified_class_name: str) -> str:
        """Return the Java file path containing a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            str: Path to the Java file.

        Examples:
            Get the source file path for a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> path = ja.get_java_file('com.example.A')  # doctest: +SKIP
            >>> isinstance(path, str)  # doctest: +SKIP
            True
        """
        return self.backend.get_java_file(qualified_class_name)

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """Return the compilation unit for a Java source file.

        Args:
            file_path (str): Absolute path to a Java source file.

        Returns:
            JCompilationUnit: Compilation unit object.

        Examples:
            Parse a Java file into a compilation unit (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> cu = ja.get_java_compilation_unit('/abs/path/to/A.java')  # doctest: +SKIP
            >>> cu is not None  # doctest: +SKIP
            True
        """
        return self.backend.get_java_compilation_unit(file_path)

    def get_methods_in_class(self, qualified_class_name) -> Dict[str, JCallable]:
        """Return all methods of a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            dict[str, JCallable]: Methods keyed by signature.

        Examples:
            List methods declared in a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> methods = ja.get_methods_in_class('com.example.A')  # doctest: +SKIP
            >>> isinstance(methods, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_methods_in_class(qualified_class_name)

    def get_constructors(self, qualified_class_name) -> Dict[str, JCallable]:
        """Return all constructors of a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            dict[str, JCallable]: Constructors keyed by signature.

        Examples:
            List constructors declared in a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> ctors = ja.get_constructors('com.example.A')  # doctest: +SKIP
            >>> isinstance(ctors, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_constructors(qualified_class_name)

    def get_fields(self, qualified_class_name) -> List[JField]:
        """Return all fields of a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            list[JField]: All fields of the class.

        Examples:
            List fields declared in a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> fields = ja.get_fields('com.example.A')  # doctest: +SKIP
            >>> isinstance(fields, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_fields(qualified_class_name)

    def get_nested_classes(self, qualified_class_name) -> List[JType]:
        """Return all nested classes of a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            list[JType]: Nested classes.

        Examples:
            List nested classes declared in a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> nested = ja.get_nested_classes('com.example.A')  # doctest: +SKIP
            >>> isinstance(nested, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_nested_classes(qualified_class_name)

    def get_sub_classes(self, qualified_class_name) -> Dict[str, JType]:
        """Return all subclasses of a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            dict[str, JType]: Subclasses keyed by qualified name.

        Examples:
            List subclasses of a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> subs = ja.get_sub_classes('com.example.A')  # doctest: +SKIP
            >>> isinstance(subs, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_sub_classes(qualified_class_name=qualified_class_name)

    def get_extended_classes(self, qualified_class_name) -> List[str]:
        """Return all extended superclasses for a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            list[str]: Extended classes.

        Examples:
            List extended classes for a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> supers = ja.get_extended_classes('com.example.A')  # doctest: +SKIP
            >>> isinstance(supers, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_extended_classes(qualified_class_name)

    def get_implemented_interfaces(self, qualified_class_name: str) -> List[str]:
        """Return all implemented interfaces for a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            list[str]: Implemented interfaces.

        Examples:
            List implemented interfaces for a class (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> ifaces = ja.get_implemented_interfaces('com.example.A')  # doctest: +SKIP
            >>> isinstance(ifaces, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_implemented_interfaces(qualified_class_name)

    def __get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> (List)[Tuple[JMethodDetail, JMethodDetail]]:
        """Return class-level call graph using the symbol table.

        Args:
            qualified_class_name (str): Qualified class name.
            method_signature (str | None): Optional method signature to scope the graph.

        Returns:
            list[tuple[JMethodDetail, JMethodDetail]]: Edge list of caller -> callee.
        """
        return self.backend.get_class_call_graph_using_symbol_table(qualified_class_name, method_signature)

    def get_class_call_graph(self, qualified_class_name: str, method_signature: str | None = None, using_symbol_table: bool = False) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Return a class-level call graph.

        Args:
            qualified_class_name (str): Qualified class name.
            method_signature (str | None): Optional method signature to scope the graph.
            using_symbol_table (bool): If True, use the symbol table for resolution.

        Returns:
            list[tuple[JMethodDetail, JMethodDetail]]: Edge list of caller -> callee.

        Examples:
            Build a class-level call graph (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project', analysis_level='call-graph')
            >>> edges = ja.get_class_call_graph('com.example.A')  # doctest: +SKIP
            >>> isinstance(edges, list)  # doctest: +SKIP
            True
        """
        if using_symbol_table:
            return self.__get_class_call_graph_using_symbol_table(qualified_class_name=qualified_class_name, method_signature=method_signature)
        return self.backend.get_class_call_graph(qualified_class_name, method_signature)

    def get_entry_point_classes(self) -> Dict[str, JType]:
        """Return all entry-point classes.

        Returns:
            dict[str, JType]: Entry-point classes keyed by qualified class name.

        Examples:
            List entry-point classes in an application (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> eps = ja.get_entry_point_classes()  # doctest: +SKIP
            >>> isinstance(eps, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_entry_point_classes()

    def get_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Return all entry-point methods grouped by class.

        Returns:
            dict[str, dict[str, JCallable]]: Entry-point methods keyed by class.

        Examples:
            List entry-point methods in an application (backend required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> epms = ja.get_entry_point_methods()  # doctest: +SKIP
            >>> isinstance(epms, dict)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_entry_point_methods()

    def remove_all_comments(self) -> str:
        """Remove all comments from the source code.

        Returns:
            str: Source code with comments removed.

        Examples:
            Remove comments from inline source code:

            >>> from cldk import CLDK
            >>> src = 'class A { /* c */ // d\n void f(){} }'.replace('\n', ' ')
            >>> ja = CLDK(language="java").analysis(source_code=src)
            >>> cleaned = ja.remove_all_comments()  # doctest: +SKIP
            >>> '/*' in cleaned or '//' in cleaned  # doctest: +SKIP
            False
        """
        return self.backend.remove_all_comments(self.source_code)

    def get_methods_with_annotations(self, annotations: List[str]) -> Dict[str, List[Dict]]:
        """Return methods grouped by the given annotations.

        Args:
            annotations (list[str]): Annotations to search for.

        Returns:
            dict[str, list[dict]]: Methods and bodies keyed by annotation.

        Raises:
            NotImplementedError: This functionality is not implemented yet.
        """
        # TODO: This call is missing some implementation. The logic currently resides in java_sitter but tree_sitter will no longer be option, rather it will be default and common. Need to implement this differently. Somthing like, self.commons.treesitter.get_methods_with_annotations(annotations)
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_test_methods(self) -> Dict[str, str]:
        """Return test methods discovered in the source code.

        Returns:
            dict[str, str]: Mapping of method signature to body.

        Examples:
            Extract test methods from inline source code:

            >>> from cldk import CLDK
            >>> src = 'import org.junit.Test; class A { @Test public void t(){} }'
            >>> ja = CLDK(language="java").analysis(source_code=src)
            >>> tests = ja.get_test_methods()  # doctest: +SKIP
            >>> isinstance(tests, dict)  # doctest: +SKIP
            True
        """

        return self.treesitter_java.get_test_methods(source_class_code=self.source_code)

    def get_calling_lines(self, target_method_name: str) -> List[int]:
        """Return line numbers where a target method is called within a method body.

        Args:
            target_method_name (str): Target method name.

        Returns:
            list[int]: Line numbers within the source method code block.

        Raises:
            NotImplementedError: This functionality is not implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_call_targets(self, declared_methods: dict) -> Set[str]:
        """Return call targets using simple name resolution over the AST.

        Args:
            declared_methods (dict): All methods declared in the class.

        Returns:
            set[str]: Discovered call targets.

        Raises:
            NotImplementedError: This functionality is not implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_all_crud_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all CRUD operations in the source code.

        Returns:
            list[dict[str, Union[JType, JCallable, list[JCRUDOperation]]]]: CRUD operations grouped by class/method.

        Examples:
            Get all CRUD operations discovered by the backend (project required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> ops = ja.get_all_crud_operations()  # doctest: +SKIP
            >>> isinstance(ops, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_crud_operations()

    def get_all_create_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all create operations in the source code.

        Returns:
            list[dict[str, Union[JType, JCallable, list[JCRUDOperation]]]]: Create operations.

        Examples:
            Get create operations (project required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> creates = ja.get_all_create_operations()  # doctest: +SKIP
            >>> isinstance(creates, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_create_operations()

    def get_all_read_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all read operations in the source code.

        Returns:
            list[dict[str, Union[JType, JCallable, list[JCRUDOperation]]]]: Read operations.

        Examples:
            Get read operations (project required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> reads = ja.get_all_read_operations()  # doctest: +SKIP
            >>> isinstance(reads, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_read_operations()

    def get_all_update_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all update operations in the source code.

        Returns:
            list[dict[str, Union[JType, JCallable, list[JCRUDOperation]]]]: Update operations.

        Examples:
            Get update operations (project required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> updates = ja.get_all_update_operations()  # doctest: +SKIP
            >>> isinstance(updates, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_update_operations()

    def get_all_delete_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Return all delete operations in the source code.

        Returns:
            list[dict[str, Union[JType, JCallable, list[JCRUDOperation]]]]: Delete operations.

        Examples:
            Get delete operations (project required):

            >>> from cldk import CLDK
            >>> ja = CLDK(language="java").analysis(project_path='path/to/project')
            >>> deletes = ja.get_all_delete_operations()  # doctest: +SKIP
            >>> isinstance(deletes, list)  # doctest: +SKIP
            True
        """
        return self.backend.get_all_delete_operations()

    # Some APIs to process comments
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """Return all comments in a method.

        Args:
            qualified_class_name (str): Qualified class name.
            method_signature (str): Method signature.

        Returns:
            list[JComment]: Comments in the method.
        """
        return self.backend.get_comments_in_a_method(qualified_class_name, method_signature)

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """Return all comments in a class.

        Args:
            qualified_class_name (str): Qualified class name.

        Returns:
            list[JComment]: Comments in the class.
        """
        return self.backend.get_comments_in_a_class(qualified_class_name)

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """Return all comments in a file.

        Args:
            file_path (str): Absolute file path.

        Returns:
            list[JComment]: Comments in the file.
        """
        return self.backend.get_comment_in_file(file_path)

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """Return all comments grouped by file.

        Returns:
            dict[str, list[JComment]]: Mapping of file path to comments.
        """
        return self.backend.get_all_comments()

    def get_all_docstrings(self) -> Dict[str, List[JComment]]:
        """Return all docstrings grouped by file.

        Returns:
            dict[str, list[JComment]]: Mapping of file path to docstrings.
        """
        return self.backend.get_all_docstrings()
