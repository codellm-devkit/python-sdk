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

"""
TreesitterPython module
"""

import os
from pathlib import Path
from typing import List

from tree_sitter import Language, Parser, Node, Tree
import tree_sitter_python as tspython
from cldk.models.python.models import PyMethod, PyClass, PyArg, PyImport, PyModule, PyCallSite
from cldk.analysis.commons.treesitter.models import Captures
from cldk.analysis.commons.treesitter.utils.treesitter_utils import TreeSitterUtils

LANGUAGE: Language = Language(tspython.language())
PARSER: Parser = Parser(LANGUAGE)


class TreesitterPython:
    """
    Tree sitter for Python use cases.
    """

    def __init__(self) -> None:
        self.utils: TreeSitterUtils = TreeSitterUtils()

    def is_parsable(self, code: str) -> bool:
        """
        Check if the code is parsable
        Args:
            code: source code

        Returns:
            True if the code is parsable, False otherwise
        """

        def syntax_error(node):
            if node.type == "ERROR":
                return True
            try:
                for child in node.children:
                    if syntax_error(child):
                        return True
            except RecursionError as err:
                print(err)
                return True

            return False

        tree = PARSER.parse(bytes(code, "utf-8"))
        if tree is not None:
            return not syntax_error(tree.root_node)
        return False

    def get_raw_ast(self, code: str) -> Tree:
        """
        Get the raw AST
        Args:
            code: source code

        Returns:
            Tree: the raw AST
        """
        return PARSER.parse(bytes(code, "utf-8"))

    def get_all_methods(self, module: str) -> List[PyMethod]:
        """
        Get all the methods in the specific module.
        Parameters
        ----------
        module: code body of the module

        Returns
        -------
            List[PyMethod]: returns all the method details within the module

        """
        methods: List[PyMethod] = []
        method_signatures: dict[str, List[int]] = {}
        # Get the methods declared under class
        all_class_details: List[PyClass] = self.get_all_classes(module=module)
        for class_details in all_class_details:
            for method in class_details.methods:
                method_signatures[method.full_signature] = [method.start_line, method.end_line]
            methods.extend(class_details.methods)
        return methods

    def get_all_functions(self, module: str) -> List[PyMethod]:
        """
        Get all the methods in the specific module.
        Parameters
        ----------
        module: code body of the module

        Returns
        -------
            List[PyMethod]: returns all the method details within the module

        """
        methods: List[PyMethod] = []
        functions: List[PyMethod] = []
        method_signatures: dict[str, List[int]] = {}
        # Get the methods declared under class
        all_class_details: List[PyClass] = self.get_all_classes(module=module)
        # Filter all method nodes
        all_method_nodes: Captures = self.__get_method_nodes(module=module)
        for class_details in all_class_details:
            for method in class_details.methods:
                method_signatures[method.full_signature] = [method.start_line, method.end_line]
            methods.extend(class_details.methods)
        for method_node in all_method_nodes:
            method_details = self.__get_function_details(node=method_node.node)
            if method_details.full_signature not in method_signatures:
                functions.append(method_details)
            elif (
                method_signatures[method_details.full_signature][0] != method_details.start_line and method_signatures[method_details.full_signature][1] != method_details.end_line
            ):
                functions.append(method_details)
        return functions

    def get_method_details(self, module: str, method_signature: str) -> PyMethod:
        """
        Given the code body and the method signature, returns the method details related to that method
        Parameters
        ----------
        module: code body
        method_signature: method signature

        Returns
        -------
            PyMethod: Returns the method details related to that method
        """
        all_method_details = self.get_all_methods(module=module)
        for method in all_method_details:
            if method.full_signature == method_signature:
                return method
        return None

    def get_all_imports(self, module: str) -> List[str]:
        """
        Given the code body, returns the imports in that module
        Parameters
        ----------
        module: code body

        Returns
        -------
            List[str]: List of imports
        """
        import_list = []
        captures_from_import: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((import_from_statement) @imports))", module)
        captures_import: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((import_statement) @imports))", module)
        for capture in captures_import:
            import_list.append(capture.node.text.decode())
        for capture in captures_from_import:
            import_list.append(capture.node.text.decode())
        return import_list

    def get_module_details(self, module: str) -> PyModule:
        return PyModule(
            functions=self.get_all_functions(module=module), classes=self.get_all_classes(module=module), imports=self.get_all_imports_details(module=module), qualified_name=""
        )

    def get_all_imports_details(self, module: str) -> List[PyImport]:
        """
        Given the code body, returns the imports in that module
        Parameters
        ----------
        module: code body

        Returns
        -------
            List[PyImport]: List of imports
        """
        import_list = []
        captures_from_import: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((import_from_statement) @imports))", module)
        captures_import: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((import_statement) @imports))", module)
        for capture in captures_import:
            imports = []
            for import_name in capture.node.children:
                if import_name.type == "dotted_name":
                    imports.append(import_name.text.decode())
                if import_name.type == "wildcard_import":
                    imports.append("ALL")
            import_list.append(PyImport(from_statement="", imports=imports))
        for capture in captures_from_import:
            imports = []
            for i in range(2, capture.node.child_count):
                if capture.node.children[i].type == "dotted_name":
                    imports.append(capture.node.children[i].text.decode())
                if capture.node.children[i].type == "wildcard_import":
                    imports.append("ALL")
            import_list.append(PyImport(from_statement=capture.node.children[1].text.decode(), imports=imports))
        return import_list

    def get_all_fields(self, module: str):
        pass

    def get_all_classes(self, module: str) -> List[PyClass]:
        """
        Given the code body of the module, returns details of all classes in it
        Parameters
        ----------
        module: code body

        Returns
        -------
            List[PyClass]: returns details of all classes in it
        """
        classes: List[PyClass] = []
        all_class_details: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((class_definition) @class_name))", module)
        for class_name in all_class_details:
            code_body = class_name.node.text.decode()
            class_full_signature = ""  # TODO: what to fill here
            klass_name = class_name.node.children[1].text.decode()
            methods: List[PyMethod] = []
            super_classes: List[str] = []
            is_test_class = False
            for child in class_name.node.children:
                if child.type == "argument_list":
                    for arg in child.children:
                        if "unittest" in arg.text.decode() or "TestCase" in arg.text.decode():
                            is_test_class = True
                        super_classes.append(arg.text.decode())
                if child.type == "block":
                    for block in child.children:
                        if block.type == "function_definition":
                            method = self.__get_function_details(node=block, klass_name=klass_name)
                            methods.append(method)
                        if block.type == "decorated_definition":
                            for decorated_def in block.children:
                                if decorated_def.type == "function_definition":
                                    method = self.__get_function_details(node=decorated_def, klass_name=klass_name)
                                    methods.append(method)
            classes.append(
                PyClass(code_body=code_body, full_signature=class_full_signature, methods=methods, super_classes=super_classes, class_name=klass_name, is_test_class=is_test_class)
            )
        return classes

    def get_all_modules(self, application_dir: Path) -> List[PyModule]:
        """
        Given an application directory, returns a list of modules
        Parameters
        ----------
        application_dir (Path): Location of the application directory

        Returns
        -------
            List[PyModule]: returns a list of modules
        """
        modules: List[PyModule] = []
        path_list = [os.path.join(dirpath, filename) for dirpath, _, filenames in os.walk(application_dir) for filename in filenames if filename.endswith(".py")]
        for p in path_list:
            modules.append(self.__get_module(path=p))
        return modules

    def __get_module(self, path: Path):
        module_qualified_path = os.path.join(path)
        module_qualified_name = str(module_qualified_path).replace(os.sep, ".")
        with open(module_qualified_path, "r") as file:
            py_module = self.get_module_details(module=file.read())
            qualified_name: str
            methods: List[PyMethod]
            functions: List[PyMethod]
            classes: List[PyClass]
            imports: List[PyImport]
            return PyModule(qualified_name=module_qualified_name, imports=py_module.imports, functions=py_module.functions, classes=py_module.classes)
        return None

    @staticmethod
    def __get_call_site_details(call_node: Node) -> PyCallSite:
        """
        Get details about the call site information given a call node
        Parameters
        ----------
        call_node

        Returns
        -------
            PyCallSite: Call site information
        """
        start_line = call_node.start_point[0]
        start_column = call_node.start_point[1]
        end_line = call_node.end_point[0]
        end_column = call_node.end_point[1]
        try:
            method_name = call_node.children[0].children[2].text.decode()
            declaring_object = call_node.children[0].children[0].text.decode()
            arguments: List[str] = []
            for arg in call_node.children[1].children:
                if arg.type not in ["(", ")", ","]:
                    arguments.append(arg.text.decode())
        except Exception:
            method_name = ""
            declaring_object = ""
            arguments = []
        return PyCallSite(
            method_name=method_name,
            declaring_object=declaring_object,
            arguments=arguments,
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
        )

    def __get_function_details(self, node: Node, klass_name: str = "") -> PyMethod:
        code_body: str = node.text.decode()
        start_line: int = node.start_point[0]
        end_line: int = node.end_point[0]
        method_name = ""
        modifier: str = ""
        formal_params: List[PyArg] = []
        return_type: str = ""
        class_signature: str = klass_name
        is_constructor = False
        is_static = False
        call_sites: List[PyCallSite] = []
        call_nodes: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((call) @call_name))", node.text.decode())
        for call_node in call_nodes:
            call_sites.append(self.__get_call_site_details(call_node.node))
        for function_detail in node.children:
            try:
                annotation_node = self.__safe_ascend(function_detail, 2)
            except Exception:
                annotation_node = None
            if annotation_node is not None:
                if annotation_node.type == "decorated_definition":
                    for child in annotation_node.children:
                        if child.type == "decorator":
                            if "staticmethod" in child.text.decode():
                                is_static = True
            if function_detail.type == "identifier":
                method_name = function_detail.text.decode()
                if "__init__" in method_name:
                    is_constructor = True
                if method_name.startswith("__"):
                    modifier = "private"
                elif method_name.startswith("_"):
                    modifier = "protected"
                else:
                    modifier = "public"
            if function_detail.type == "return_type":
                return_type = function_detail.text.decode()
            if function_detail.type == "parameters":
                parameters = function_detail.text.decode()

                for parameter in function_detail.children:
                    formal_param: PyArg = None
                    if parameter.type == "identifier":
                        formal_param = PyArg(arg_name=parameter.text.decode(), arg_type="")
                    elif parameter.type == "typed_parameter":
                        formal_param = PyArg(arg_name=parameter.children[0].text.decode(), arg_type=parameter.children[2].text.decode())
                    elif parameter.type == "dictionary_splat_pattern":
                        formal_param = PyArg(arg_name=parameter.text.decode(), arg_type="")
                    if formal_param is not None:
                        formal_params.append(formal_param)
        num_params = len(formal_params)
        full_signature = method_name + parameters
        function: PyMethod = PyMethod(
            method_name=method_name,
            code_body=code_body,
            full_signature=full_signature,
            num_params=num_params,
            modifier=modifier,
            formal_params=formal_params,
            return_type=return_type,
            class_signature=class_signature,
            start_line=start_line,
            end_line=end_line,
            is_static=is_static,
            is_constructor=is_constructor,
            call_sites=call_sites,
        )
        return function

    def __get_class_nodes(self, module: str) -> Captures:
        captures: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((class_definition) @class_name))", module)
        return captures

    def __get_method_nodes(self, module: str) -> Captures:
        captures: Captures = self.utils.frame_query_and_capture_output(PARSER, LANGUAGE, "(((function_definition) @function_name))", module)
        return captures
