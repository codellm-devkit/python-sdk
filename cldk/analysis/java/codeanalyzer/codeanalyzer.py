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
import json
import logging
import re
import shlex
import subprocess
from importlib import resources
from itertools import chain, groupby
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict, List, Tuple
from typing import Union

import networkx as nx

from cldk.analysis import AnalysisLevel
from cldk.analysis.commons.treesitter import TreesitterJava
from cldk.models.java import JGraphEdges
from cldk.models.java.enums import CRUDOperationType
from cldk.models.java.models import JApplication, JCRUDOperation, JCallable, JCallableParameter, JComment, JField, JMethodDetail, JType, JCompilationUnit, JGraphEdgesST
from cldk.utils.exceptions.exceptions import CodeanalyzerExecutionException

logger = logging.getLogger(__name__)


class JCodeanalyzer:
    """A class for building the application view of a Java application using Codeanalyzer.

    Args:
        project_dir (str or Path): The path to the root of the Java project.
        source_code (str, optional): The source code of a single Java file to analyze. Defaults to None.
        analysis_backend_path (str or Path, optional): The path to the analysis backend. Defaults to None.
        analysis_json_path (str or Path, optional): The path to save the intermediate code analysis outputs.
            If None, the analysis will be read from the pipe.
        analysis_level (str): The level of analysis ('symbol_table' or 'call_graph').
        eager_analysis (bool): If True, the analysis will be performed every time the object is created.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        source_code: str | None,
        analysis_backend_path: Union[str, Path, None],
        analysis_json_path: Union[str, Path, None],
        analysis_level: str,
        eager_analysis: bool,
        target_files: List[str] | None,
    ) -> None:
        self.project_dir = project_dir
        self.source_code = source_code
        self.analysis_backend_path = analysis_backend_path
        self.analysis_json_path = analysis_json_path
        self.eager_analysis = eager_analysis
        self.analysis_level = analysis_level
        self.target_files = target_files
        self.application = self._init_codeanalyzer(analysis_level=1 if analysis_level == AnalysisLevel.symbol_table else 2)
        # Attributes related the Java code analysis...
        if analysis_level == AnalysisLevel.call_graph:
            self.call_graph: nx.DiGraph = self._generate_call_graph(using_symbol_table=False)
        else:
            self.call_graph: nx.DiGraph | None = None

    def _get_application(self) -> JApplication:
        """Should return  the application view of the Java code.

        Returns:
            JApplication: The application view of the Java code.
        """
        if self.application is None:
            self.application = self._init_codeanalyzer()
        return self.application

    def _get_codeanalyzer_exec(self) -> List[str]:
        """Should return  the executable command for codeanalyzer.

        Returns:
            List[str]: The executable command for codeanalyzer.

        Notes:
            - If the analysis_backend_path is provided, the codeanalyzer jar from that path will be used.
            - If not provided, the latest codeanalyzer jar from GitHub will be downloaded.
        """

        if self.analysis_backend_path:
            analysis_backend_path = Path(self.analysis_backend_path)
            logger.info(f"Using codeanalyzer jar from {analysis_backend_path}")
            codeanalyzer_jar_file = next(analysis_backend_path.rglob("codeanalyzer-*.jar"), None)
            if codeanalyzer_jar_file is None:
                raise CodeanalyzerExecutionException("Codeanalyzer jar not found in the provided path.")
            codeanalyzer_exec = shlex.split(f"java -jar {codeanalyzer_jar_file}")
        else:
            # Since the path to codeanalyzer.jar we will use the default jar from the cldk/analysis/java/codeanalyzer/jar folder
            with resources.as_file(resources.files("cldk.analysis.java.codeanalyzer.jar")) as codeanalyzer_jar_path:
                codeanalyzer_jar_file = next(codeanalyzer_jar_path.rglob("codeanalyzer-*.jar"), None)
                codeanalyzer_exec = shlex.split(f"java -jar {codeanalyzer_jar_file}")
        return codeanalyzer_exec

    @staticmethod
    def _init_japplication(data: str) -> JApplication:
        """Should return JApplication giving the stringified JSON as input.
        Returns
        -------
        JApplication
            The application view of the Java code with the analysis results.
        """
        # from ipdb import set_trace

        # set_trace()
        return JApplication(**json.loads(data))

    def _init_codeanalyzer(self, analysis_level=1) -> JApplication:
        """Should initialize the Codeanalyzer.

        Args:
            analysis_level (int): The level of analysis to be performed (1 for symbol table, 2 for call graph).

        Returns:
            JApplication: The application view of the Java code with the analysis results.

        Raises:
            CodeanalyzerExecutionException: If there is an error running Codeanalyzer.
        """
        codeanalyzer_exec = self._get_codeanalyzer_exec()
        codeanalyzer_args = ""
        if self.analysis_json_path is None:
            logger.info("Reading analysis from the pipe.")
            # If target file is provided, the input is merged into a single string and passed to codeanalyzer
            if self.target_files:
                target_file_options = " -t ".join([s.strip() for s in self.target_files])
                codeanalyzer_args = codeanalyzer_exec + shlex.split(f"-i {Path(self.project_dir)} --analysis-level={analysis_level} -t {target_file_options}")
            else:
                codeanalyzer_args = codeanalyzer_exec + shlex.split(f"-i {Path(self.project_dir)} --analysis-level={analysis_level}")
            try:
                logger.info(f"Running codeanalyzer: {' '.join(codeanalyzer_args)}")
                console_out: CompletedProcess[str] = subprocess.run(
                    codeanalyzer_args,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return self._init_japplication(console_out.stdout)
            except Exception as e:
                raise CodeanalyzerExecutionException(str(e)) from e
        else:
            # Check if the code analyzer needs to be run
            is_run_code_analyzer = False
            analysis_json_path_file = Path(self.analysis_json_path).joinpath("analysis.json")
            # If target file is provided, the input is merged into a single string and passed to codeanalyzer
            if self.target_files:
                target_file_options = " -t ".join([s.strip() for s in self.target_files])
                codeanalyzer_args = codeanalyzer_exec + shlex.split(
                    f"-i {Path(self.project_dir)} --analysis-level={analysis_level}" f" -o {self.analysis_json_path} -t {target_file_options}"
                )
                is_run_code_analyzer = True
            else:
                if not analysis_json_path_file.exists() or self.eager_analysis:
                    # If the analysis file does not exist, we'll run the analysis. Alternately, if the eager_analysis
                    # flag is set, we'll run the analysis every time the object is created. This will happen regradless
                    # of the existence of the analysis file.
                    # Create the executable command for codeanalyzer.
                    codeanalyzer_args = codeanalyzer_exec + shlex.split(f"-i {Path(self.project_dir)} --analysis-level={analysis_level} -o {self.analysis_json_path} -v")
                    is_run_code_analyzer = True

            if is_run_code_analyzer:
                try:
                    logger.info(f"Running codeanalyzer subprocess with args {codeanalyzer_args}")
                    subprocess.run(
                        codeanalyzer_args,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    if not analysis_json_path_file.exists():
                        raise CodeanalyzerExecutionException("Codeanalyzer did not generate the analysis file.")

                except Exception as e:
                    raise CodeanalyzerExecutionException(str(e)) from e
            with open(analysis_json_path_file) as f:
                data = json.load(f)
                return self._init_japplication(json.dumps(data))

    def _codeanalyzer_single_file(self) -> JApplication:
        """Invokes codeanalyzer in a single file mode.

        Returns:
            JApplication: The application view of the Java code with the analysis results.
        """
        codeanalyzer_exec = self._get_codeanalyzer_exec()
        codeanalyzer_args = ["--source-analysis", self.source_code]
        codeanalyzer_cmd = codeanalyzer_exec + codeanalyzer_args
        try:
            logger.info(f"Running {' '.join(codeanalyzer_cmd)}")
            console_out: CompletedProcess[str] = subprocess.run(codeanalyzer_cmd, capture_output=True, text=True, check=True)
            if console_out.returncode != 0:
                raise CodeanalyzerExecutionException(console_out.stderr)
            return self._init_japplication(console_out.stdout)
        except Exception as e:
            raise CodeanalyzerExecutionException(str(e)) from e

    def get_symbol_table(self) -> Dict[str, JCompilationUnit]:
        """Should return  the symbol table of the Java code.

        Returns:
            Dict[str, JCompilationUnit]: The symbol table of the Java code.
        """
        if self.application is None:
            self.application = self._init_codeanalyzer()
        return self.application.symbol_table

    def get_application_view(self) -> JApplication:
        """Should return  the application view of the Java code.

        Returns:
            JApplication: The application view of the Java code.
        """
        if self.source_code:
            # This branch is triggered when a single file is being analyzed.
            self.application = self._codeanalyzer_single_file()
            return self.application
        else:
            if self.application is None:
                self.application = self._init_codeanalyzer()
            return self.application

    def get_system_dependency_graph(self) -> list[JGraphEdges]:
        """Runs the codeanalyzer to get the system dependency graph.

        Returns:
            list[JGraphEdges]: The system dependency graph.
        """
        if self.application.system_dependency_graph is None or self.application.call_graph is None:
            self.application = self._init_codeanalyzer(analysis_level=2)

        logger.warning("System dependency graph is not yet implemented. Returning the call graph instead.")
        return self.application.call_graph

    def _generate_call_graph(self, using_symbol_table) -> nx.DiGraph:
        """Generates the call graph of the Java code.

        Args:
            using_symbol_table (bool): Whether to use the symbol table for generating the call graph.

        Returns:
            nx.DiGraph: The call graph of the Java code.
        """
        cg = nx.DiGraph()
        if using_symbol_table:
            NotImplementedError("Call graph generation using symbol table is not implemented yet.")
        else:
            sdg = self.get_system_dependency_graph()
            tsu = TreesitterJava()
            edge_list = [
                (
                    (jge.source.method.signature, jge.source.klass),
                    (jge.target.method.signature, jge.target.klass),
                    {
                        "type": jge.type,
                        "weight": jge.weight,
                        "calling_lines": (
                            tsu.get_calling_lines(jge.source.method.code, jge.target.method.signature)
                            if not jge.source.method.is_implicit or not jge.target.method.is_implicit
                            else []
                        ),
                    },
                )
                for jge in sdg
                if jge.type == "CALL_DEP"  # or jge.type == "CONTROL_DEP"
            ]
            for jge in sdg:
                cg.add_node(
                    (jge.source.method.signature, jge.source.klass),
                    method_detail=jge.source,
                )
                cg.add_node(
                    (jge.target.method.signature, jge.target.klass),
                    method_detail=jge.target,
                )
            cg.add_edges_from(edge_list)
        return cg

    def get_class_hierarchy(self) -> nx.DiGraph:
        """Should return  the class hierarchy of the Java code.

        Returns:
            nx.DiGraph: The class hierarchy of the Java code.
        """

    def get_call_graph(self) -> nx.DiGraph:
        """Should return  the call graph of the Java code.

        Returns:
            nx.DiGraph: The call graph of the Java code.
        """
        if self.analysis_level == "symbol_table":
            self.call_graph = self._generate_call_graph(using_symbol_table=True)
        if self.call_graph is None:
            self.call_graph = self._generate_call_graph(using_symbol_table=False)
        return self.call_graph

    def get_call_graph_json(self) -> str:
        """Get call graph in serialized json format.

        Returns:
            str: Call graph in json.
        """
        callgraph_list = []
        edges = list(self.call_graph.edges.data("calling_lines"))
        for edge in edges:
            callgraph_dict = {}
            callgraph_dict["source_method_signature"] = edge[0][0]
            callgraph_dict["source_method_body"] = self.call_graph.nodes[edge[0]]["method_detail"].method.code
            callgraph_dict["source_class"] = edge[0][1]
            callgraph_dict["target_method_signature"] = edge[1][0]
            callgraph_dict["target_method_body"] = self.call_graph.nodes[edge[1]]["method_detail"].method.code
            callgraph_dict["target_class"] = edge[1][1]
            callgraph_dict["calling_lines"] = edge[2]
            callgraph_list.append(callgraph_dict)
        return json.dumps(callgraph_list)

    def get_all_callers(self, target_class_name: str, target_method_signature: str, using_symbol_table: bool) -> Dict:
        """Get all the caller details for a given Java method.

        Args:
            target_class_name (str): The qualified class name of the target method.
            target_method_signature (str): The signature of the target method.
            using_symbol_table (bool): Whether to use the symbol table to generate the call graph.

        Returns:
            Dict: A dictionary containing caller details.
        """

        caller_detail_dict = {}
        call_graph = None
        if using_symbol_table:
            call_graph = self.__call_graph_using_symbol_table(qualified_class_name=target_class_name, method_signature=target_method_signature, is_target_method=True)
        else:
            call_graph = self.call_graph
        if (target_method_signature, target_class_name) not in call_graph.nodes():
            return caller_detail_dict

        in_edge_view = call_graph.in_edges(
            nbunch=(
                target_method_signature,
                target_class_name,
            ),
            data=True,
        )
        caller_detail_dict["caller_details"] = []
        caller_detail_dict["target_method"] = call_graph.nodes[(target_method_signature, target_class_name)]["method_detail"]

        for source, target, data in in_edge_view:
            cm = {"caller_method": call_graph.nodes[source]["method_detail"], "calling_lines": data["calling_lines"]}
            caller_detail_dict["caller_details"].append(cm)
        return caller_detail_dict

    def get_all_callees(self, source_class_name: str, source_method_signature: str, using_symbol_table: bool) -> Dict:
        """Get all the callee details for a given Java method.

        Args:
            source_class_name (str): The qualified class name of the source method.
            source_method_signature (str): The signature of the source method.
            using_symbol_table (bool): Whether to use the symbol table to generate the call graph.

        Returns:
            Dict: A dictionary containing callee details.
        """
        callee_detail_dict = {}
        call_graph = None
        if using_symbol_table:
            call_graph = self.__call_graph_using_symbol_table(qualified_class_name=source_class_name, method_signature=source_method_signature)
        else:
            call_graph = self.call_graph
        if (source_method_signature, source_class_name) not in call_graph.nodes():
            return callee_detail_dict

        out_edge_view = call_graph.out_edges(nbunch=(source_method_signature, source_class_name), data=True)

        callee_detail_dict["callee_details"] = []
        callee_detail_dict["source_method"] = call_graph.nodes[(source_method_signature, source_class_name)]["method_detail"]
        for source, target, data in out_edge_view:
            cm = {"callee_method": call_graph.nodes[target]["method_detail"]}
            cm["calling_lines"] = data["calling_lines"]
            callee_detail_dict["callee_details"].append(cm)
        return callee_detail_dict

    def get_all_methods_in_application(self) -> Dict[str, Dict[str, JCallable]]:
        """Should return  a dictionary of all methods in the Java code with qualified class name as the key
            and a dictionary of methods in that class as the value.

        Returns:
            Dict[str, Dict[str, JCallable]]: A dictionary of dictionaries of all methods in the Java code.
        """

        class_method_dict = {}
        class_dict = self.get_all_classes()
        for k, v in class_dict.items():
            class_method_dict[k] = v.callable_declarations
        return class_method_dict

    def get_all_classes(self) -> Dict[str, JType]:
        """Should return  a dictionary of all classes in the Java code.

        Returns:
            Dict[str, JType]: A dictionary of all classes in the Java code, with qualified class names as keys.
        """

        class_dict = {}
        symtab = self.get_symbol_table()
        for v in symtab.values():
            class_dict.update(v.type_declarations)
        return class_dict

    def get_class(self, qualified_class_name) -> JType:
        """Should return  a class given the qualified class name.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            JType: A class for the given qualified class name.
        """
        symtab = self.get_symbol_table()
        for _, v in symtab.items():
            if qualified_class_name in v.type_declarations.keys():
                return v.type_declarations.get(qualified_class_name)

    def get_method(self, qualified_class_name, method_signature) -> JCallable:
        """Should return  a method given the qualified method name.

        Args:
            qualified_class_name (str): The qualified name of the class.
            method_signature (str): The signature of the method.

        Returns:
            JCallable: A method for the given qualified method name.
        """
        symtab = self.get_symbol_table()
        for v in symtab.values():
            if qualified_class_name in v.type_declarations.keys():
                ci = v.type_declarations[qualified_class_name]
                for cd in ci.callable_declarations.keys():
                    if cd == method_signature:
                        return ci.callable_declarations[cd]

    def get_method_parameters(self, qualified_class_name, method_signature) -> List[JCallableParameter]:
        """Should return  a dictionary of method parameters given the qualified class name and method signature.

        Args:
            qualified_class_name (str): The qualified name of the class.
            method_signature (str): The signature of the method.

        Returns:
            Dict[str, str]: A dictionary of method parameters for the given qualified class name and method signature.
        """
        return self.get_method(qualified_class_name, method_signature).parameters

    def get_parameters_from_callable(self, callable: JCallable) -> List[JCallableParameter]:
        """Should return  a dictionary of method parameters given the callable.

        Args:
            callable (JCallable): The callable object.

        Returns:
            Dict[str, str]: A dictionary of method parameters for the given callable.
        """
        return callable.parameters

    def get_java_file(self, qualified_class_name) -> str:
        """Should return  java file name given the qualified class name.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            str: Java file name containing the given qualified class.
        """
        symtab = self.get_symbol_table()
        for k, v in symtab.items():
            if (qualified_class_name) in v.type_declarations.keys():
                return k

    def get_compilation_units(self) -> List[JCompilationUnit]:
        """Get all the compilation units in the symbol table.

        Returns:
            List[JCompilationUnit]: A list of compilation units.
        """
        if self.application is None:
            self.application = self._init_codeanalyzer()
        return self.get_symbol_table().values()

    def get_java_compilation_unit(self, file_path: str) -> JCompilationUnit:
        """Given the path of a Java source file, returns the compilation unit object from the symbol table.

        Args:
            file_path (str): Absolute path to the Java source file.

        Returns:
            JCompilationUnit: Compilation unit object for the Java source file.
        """

        if self.application is None:
            self.application = self._init_codeanalyzer()
        return self.application.symbol_table[file_path]

    def get_all_methods_in_class(self, qualified_class_name) -> Dict[str, JCallable]:
        """Should return  a dictionary of all methods in the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            Dict[str, JCallable]: A dictionary of all methods in the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            return {}
        methods = {k: v for (k, v) in ci.callable_declarations.items() if v.is_constructor is False}
        return methods

    def get_all_constructors(self, qualified_class_name) -> Dict[str, JCallable]:
        """Should return  a dictionary of all constructors of the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            Dict[str, JCallable]: A dictionary of all constructors of the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            return {}
        constructors = {k: v for (k, v) in ci.callable_declarations.items() if v.is_constructor is True}
        return constructors

    def get_all_sub_classes(self, qualified_class_name) -> Dict[str, JType]:
        """Should return  a dictionary of all sub-classes of the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            Dict[str, JType]: A dictionary of all sub-classes of the given class, and class details.
        """

        all_classes = self.get_all_classes()
        sub_classes = {}
        for cls in all_classes:
            if qualified_class_name in all_classes[cls].implements_list or qualified_class_name in all_classes[cls].extends_list:
                sub_classes[cls] = all_classes[cls]
        return sub_classes

    def get_all_fields(self, qualified_class_name) -> List[JField]:
        """Should return  a list of all fields of the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            List[JField]: A list of all fields of the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.field_declarations

    def get_all_nested_classes(self, qualified_class_name) -> List[JType]:
        """Should return  a list of all nested classes for the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            List[JType]: A list of nested classes for the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        nested_classes = ci.nested_type_declarations
        return [self.get_class(c) for c in nested_classes]  # Assuming qualified nested class names

    def get_extended_classes(self, qualified_class_name) -> List[str]:
        """Should return  a list of all extended classes for the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            List[str]: A list of extended classes for the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.extends_list

    def get_implemented_interfaces(self, qualified_class_name) -> List[str]:
        """Should return  a list of all implemented interfaces for the given class.

        Args:
            qualified_class_name (str): The qualified name of the class.

        Returns:
            List[str]: A list of implemented interfaces for the given class.
        """
        ci = self.get_class(qualified_class_name)
        if ci is None:
            logging.warning(f"Class {qualified_class_name} not found in the application view.")
            return list()
        return ci.implements_list

    def get_class_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str | None = None) -> (List)[Tuple[JMethodDetail, JMethodDetail]]:
        """Should return  call graph using symbol table. The analysis will not be
        complete as symbol table has known limitation of resolving types
        Args:
            qualified_class_name: qualified name of the class
            method_signature: method signature of the starting point of the call graph

        Returns: List[Tuple[JMethodDetail, JMethodDetail]]
            List of edges
        """
        call_graph = self.__call_graph_using_symbol_table(qualified_class_name, method_signature)
        if method_signature is None:
            filter_criteria = {node for node in call_graph.nodes if node[1] == qualified_class_name}
        else:
            filter_criteria = {node for node in call_graph.nodes if tuple(node) == (method_signature, qualified_class_name)}

        graph_edges: List[Tuple[JMethodDetail, JMethodDetail]] = list()
        for edge in call_graph.edges(nbunch=filter_criteria):
            source: JMethodDetail = call_graph.nodes[edge[0]]["method_detail"]
            target: JMethodDetail = call_graph.nodes[edge[1]]["method_detail"]
            graph_edges.append((source, target))
        return graph_edges

    def __call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str, is_target_method: bool = False) -> nx.DiGraph:
        """Should generate call graph using symbol table
        Args:
            qualified_class_name: qualified class name
            method_signature: method signature
            is_target_method: is the input method is a target method. By default, it is the source method

        Returns:
            nx.DiGraph: call graph
        """
        cg = nx.DiGraph()
        sdg = None
        if is_target_method:
            sdg = self.__raw_call_graph_using_symbol_table_target_method(target_class_name=qualified_class_name, target_method_signature=method_signature)
        else:
            sdg = self.__raw_call_graph_using_symbol_table(qualified_class_name=qualified_class_name, method_signature=method_signature)
        tsu = TreesitterJava()
        edge_list = [
            (
                (jge.source.method.signature, jge.source.klass),
                (jge.target.method.signature, jge.target.klass),
                {
                    "type": jge.type,
                    "weight": jge.weight,
                    "calling_lines": tsu.get_calling_lines(jge.source.method.code, jge.target.method.signature),
                },
            )
            for jge in sdg
        ]
        for jge in sdg:
            cg.add_node(
                (jge.source.method.signature, jge.source.klass),
                method_detail=jge.source,
            )
            cg.add_node(
                (jge.target.method.signature, jge.target.klass),
                method_detail=jge.target,
            )
        cg.add_edges_from(edge_list)
        return cg

    def __raw_call_graph_using_symbol_table_target_method(self, target_class_name: str, target_method_signature: str, cg=None) -> list[JGraphEdgesST]:
        """Generates call graph using symbol table information given the target method and target class
        Args:
            qualified_class_name: qualified class name
            method_signature: source method signature
            cg: call graph

        Returns:
            list[JGraphEdgesST]: list of call edges
        """
        if cg is None:
            cg = []
        target_method_details = self.get_method(qualified_class_name=target_class_name, method_signature=target_method_signature)
        for class_name in self.get_all_classes():
            for method in self.get_all_methods_in_class(qualified_class_name=class_name):
                method_details = self.get_method(qualified_class_name=class_name, method_signature=method)
                for call_site in method_details.call_sites:
                    source_method_details = None
                    source_class = ""
                    callee_signature = ""
                    if call_site.callee_signature != "":
                        pattern = r"\b(?:[a-zA-Z_][\w\.]*\.)+([a-zA-Z_][\w]*)\b|<[^>]*>"

                        # Find the part within the parentheses
                        start = call_site.callee_signature.find("(") + 1
                        end = call_site.callee_signature.rfind(")")

                        # Extract the elements inside the parentheses
                        elements = call_site.callee_signature[start:end].split(",")

                        # Apply the regex to each element
                        simplified_elements = [re.sub(pattern, r"\1", element.strip()) for element in elements]

                        # Reconstruct the string with simplified elements
                        callee_signature = f"{call_site.callee_signature[:start]}{', '.join(simplified_elements)}{call_site.callee_signature[end:]}"

                    if call_site.receiver_type != "":
                        # call to any class
                        if self.get_class(qualified_class_name=call_site.receiver_type):
                            if callee_signature == target_method_signature and call_site.receiver_type == target_class_name:
                                source_method_details = self.get_method(method_signature=method, qualified_class_name=class_name)
                                source_class = class_name
                    else:
                        # check if any method exists with the signature in the class even if the receiver type is blank
                        if callee_signature == target_method_signature and class_name == target_class_name:
                            source_method_details = self.get_method(method_signature=method, qualified_class_name=class_name)
                            source_class = class_name

                    if source_class != "" and source_method_details is not None:
                        source: JMethodDetail
                        target: JMethodDetail
                        type: str
                        weight: str
                        call_edge = JGraphEdgesST(
                            source=JMethodDetail(method_declaration=source_method_details.declaration, klass=source_class, method=source_method_details),
                            target=JMethodDetail(method_declaration=target_method_details.declaration, klass=target_class_name, method=target_method_details),
                            type="CALL_DEP",
                            weight="1",
                        )
                        if call_edge not in cg:
                            cg.append(call_edge)
        return cg

    def __raw_call_graph_using_symbol_table(self, qualified_class_name: str, method_signature: str, cg=None) -> list[JGraphEdgesST]:
        """Generates a call graph using symbol table information.

        Args:
            qualified_class_name (str): The qualified class name.
            method_signature (str): The source method signature.
            cg (list[JGraphEdgesST], optional): Existing call graph edges. Defaults to None.

        Returns:
            list[JGraphEdgesST]: A list of call edges.
        """
        if cg is None:
            cg = []
        source_method_details = self.get_method(qualified_class_name=qualified_class_name, method_signature=method_signature)
        # If the provided classname and method signature combination do not exist
        if source_method_details is None:
            return cg
        for call_site in source_method_details.call_sites:
            target_method_details = None
            target_class = ""
            callee_signature = ""
            if call_site.callee_signature != "":
                # Currently the callee signature returns the fully qualified type, whereas
                # the key for JCallable does not. The below logic converts the fully qualified signature
                # to the desider format. Only limitation is the nested generic type.
                pattern = r"\b(?:[a-zA-Z_][\w\.]*\.)+([a-zA-Z_][\w]*)\b|<[^>]*>"

                # Find the part within the parentheses
                start = call_site.callee_signature.find("(") + 1
                end = call_site.callee_signature.rfind(")")

                # Extract the elements inside the parentheses
                elements = call_site.callee_signature[start:end].split(",")

                # Apply the regex to each element
                simplified_elements = [re.sub(pattern, r"\1", element.strip()) for element in elements]

                # Reconstruct the string with simplified elements
                callee_signature = f"{call_site.callee_signature[:start]}{', '.join(simplified_elements)}{call_site.callee_signature[end:]}"

            if call_site.receiver_type != "":
                # call to any class
                if self.get_class(qualified_class_name=call_site.receiver_type):
                    tmd = self.get_method(method_signature=callee_signature, qualified_class_name=call_site.receiver_type)
                    if tmd is not None:
                        target_method_details = tmd
                        target_class = call_site.receiver_type
            else:
                # check if any method exists with the signature in the class even if the receiver type is blank
                tmd = self.get_method(method_signature=callee_signature, qualified_class_name=qualified_class_name)
                if tmd is not None:
                    target_method_details = tmd
                    target_class = qualified_class_name

            if target_class != "" and target_method_details is not None:
                source: JMethodDetail
                target: JMethodDetail
                type: str
                weight: str
                call_edge = JGraphEdgesST(
                    source=JMethodDetail(method_declaration=source_method_details.declaration, klass=qualified_class_name, method=source_method_details),
                    target=JMethodDetail(method_declaration=target_method_details.declaration, klass=target_class, method=target_method_details),
                    type="CALL_DEP",
                    weight="1",
                )
                if call_edge not in cg:
                    cg.append(call_edge)
                # cg = self.__raw_call_graph_using_symbol_table(qualified_class_name=target_class, method_signature=target_method_details.signature, cg=cg)
        return cg

    def get_class_call_graph(self, qualified_class_name: str, method_name: str | None = None) -> List[Tuple[JMethodDetail, JMethodDetail]]:
        """Generates a call graph for a given class and (optionally) filters by a given method.

        Args:
            qualified_class_name (str): The qualified name of the class.
            method_name (str, optional): The name of the method in the class.

        Returns:
            List[Tuple[JMethodDetail, JMethodDetail]]: An edge list of the call graph
            for the given class and method.

        Notes:
            The class name must be fully qualified, e.g., "org.example.MyClass" and not "MyClass".
        """
        # If the method name is not provided, we'll get the call graph for the entire class.

        if method_name is None:
            filter_criteria = {node for node in self.call_graph.nodes if node[1] == qualified_class_name}
        else:
            filter_criteria = {node for node in self.call_graph.nodes if tuple(node) == (method_name, qualified_class_name)}

        graph_edges: List[Tuple[JMethodDetail, JMethodDetail]] = list()
        for edge in self.call_graph.edges(nbunch=filter_criteria):
            source: JMethodDetail = self.call_graph.nodes[edge[0]]["method_detail"]
            target: JMethodDetail = self.call_graph.nodes[edge[1]]["method_detail"]
            graph_edges.append((source, target))

        return graph_edges

    def remove_all_comments(self, src_code: str) -> str:
        """Remove all comments in the source code.

        Args:
            src_code (str): Original source code.

        Returns:
            str: The same source code without comments.
        """
        raise NotImplementedError("This function is not implemented yet.")

    def get_all_entry_point_methods(self) -> Dict[str, Dict[str, JCallable]]:
        """Should return  a dictionary of all entry point methods in the Java code.

        Returns:
            Dict[str, Dict[str, JCallable]]: A dictionary of all entry point methods in the Java code.
        """
        methods = chain.from_iterable(
            ((typename, method, callable) for method, callable in methods.items() if callable.is_entrypoint) for typename, methods in self.get_all_methods_in_application().items()
        )
        return {typename: {method: callable for _, method, callable in group} for typename, group in groupby(methods, key=lambda x: x[0])}

    def get_all_entry_point_classes(self) -> Dict[str, JType]:
        """Should return  a dictionary of all entry point classes in the Java code.

        Returns:
            Dict[str, JType]: A dictionary of all entry point classes in the Java code,
            with qualified class names as keys.
        """

        return {typename: klass for typename, klass in self.get_all_classes().items() if klass.is_entrypoint_class}

    def get_all_crud_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Should return  a dictionary of all CRUD operations in the source code.

        Raises:
            NotImplementedError: Raised when we do not support this function.

        Returns:
            Dict[str, List[str]]: A dictionary of all CRUD operations in the source code.
        """

        crud_operations = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if len(method_details.crud_operations) > 0:
                    crud_operations.append({class_name: class_details, method_name: method_details, "crud_operations": method_details.crud_operations})
        return crud_operations

    def get_all_read_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Should return  a list of all read operations in the source code.

        Raises:
            NotImplementedError: Raised when we do not support this function.

        Returns:
            List[Dict[str, Union[str, JCallable, List[CRUDOperation]]]]:: A list of all read operations in the source code.
        """
        crud_read_operations = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if len(method_details.crud_operations) > 0:
                    crud_read_operations.append(
                        {
                            class_name: class_details,
                            method_name: method_details,
                            "crud_operations": [crud_op for crud_op in method_details.crud_operations if crud_op.operation_type == CRUDOperationType.READ],
                        }
                    )
        return crud_read_operations

    def get_all_create_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Should return  a list of all create operations in the source code.

        Raises:
            NotImplementedError: Raised when we do not support this function.

        Returns:
            List[Dict[str, Union[str, JCallable, List[CRUDOperation]]]]: A list of all create operations in the source code.
        """
        crud_create_operations = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if len(method_details.crud_operations) > 0:
                    crud_create_operations.append(
                        {
                            class_name: class_details,
                            method_name: method_details,
                            "crud_operations": [crud_op for crud_op in method_details.crud_operations if crud_op.operation_type == CRUDOperationType.CREATE],
                        }
                    )
        return crud_create_operations

    def get_all_update_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Should return  a list of all update operations in the source code.

        Raises:
            NotImplementedError: Raised when we do not support this function.

        Returns:
            List[Dict[str, Union[str, JCallable, List[CRUDOperation]]]]: A list of all update operations in the source code.
        """
        crud_update_operations = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if len(method_details.crud_operations) > 0:
                    crud_update_operations.append(
                        {
                            class_name: class_details,
                            method_name: method_details,
                            "crud_operations": [crud_op for crud_op in method_details.crud_operations if crud_op.operation_type == CRUDOperationType.UPDATE],
                        }
                    )

        return crud_update_operations

    def get_all_delete_operations(self) -> List[Dict[str, Union[JType, JCallable, List[JCRUDOperation]]]]:
        """Should return  a list of all delete operations in the source code.

        Raises:
            NotImplementedError: Raised when we do not support this function.

        Returns:
            List[Dict[str, Union[str, JCallable, List[CRUDOperation]]]]: A list of all delete operations in the source code.
        """
        crud_delete_operations = []
        for class_name, class_details in self.get_all_classes().items():
            for method_name, method_details in class_details.callable_declarations.items():
                if len(method_details.crud_operations) > 0:
                    crud_delete_operations.append(
                        {
                            class_name: class_details,
                            method_name: method_details,
                            "crud_operations": [crud_op for crud_op in method_details.crud_operations if crud_op.operation_type == CRUDOperationType.DELETE],
                        }
                    )
        return crud_delete_operations

    # Some APIs to process comments
    def get_comments_in_a_method(self, qualified_class_name: str, method_signature: str) -> List[JComment]:
        """Get all comments in a method.

        Args:
            qualified_class_name (str): Qualified name of the class.
            method_signature (str): Signature of the method.

        Returns:
            List[str]: List of comments in the method.
        """
        callable = self.get_method(qualified_class_name, method_signature)
        return callable.comments

    def get_comments_in_a_class(self, qualified_class_name: str) -> List[JComment]:
        """Get all comments in a class.

        Args:
            qualified_class_name (str): Qualified name of the class.

        Returns:
            List[str]: List of comments in the class.
        """
        klass = self.get_class(qualified_class_name)
        return klass.comments

    def get_comment_in_file(self, file_path: str) -> List[JComment]:
        """Get all comments in a file.

        Args:
            file_path (str): Path to the file.

        Returns:
            List[str]: List of comments in the file.
        """
        compilation_unit = self.get_symbol_table().get(file_path, None)
        if compilation_unit is None:
            raise CodeanalyzerExecutionException(f"File {file_path} not found in the symbol table.")
        return compilation_unit.comments

    def get_all_comments(self) -> Dict[str, List[JComment]]:
        """Get all comments in the Java application.

        Returns:
            Dict[str, List[str]]: Dictionary of file paths and their corresponding comments.
        """
        comments = {}
        for file_path, _ in self.get_symbol_table().items():
            comments[file_path] = self.get_comment_in_file(file_path)
        return comments

    def get_all_docstrings(self) -> List[Tuple[str, JComment]]:
        """Get all docstrings in the Java application.

        Returns:
            Dict[str, List[str]]: Dictionary of file paths and their corresponding docstrings.
        """
        docstrings = {}
        for file_path, list_of_comments in self.get_all_comments().items():
            javadoc_comments = [docstring for docstring in list_of_comments if docstring.is_javadoc]
            if javadoc_comments:
                docstrings[file_path] = javadoc_comments

        return docstrings
