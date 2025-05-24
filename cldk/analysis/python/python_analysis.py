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
Python module
"""

from pathlib import Path
from typing import List

from cldk.analysis.commons.treesitter import TreesitterPython
from cldk.models.python.models import PyMethod, PyImport, PyModule, PyClass


class PythonAnalysis:
    """Python Analysis Class"""

    def __init__(
        self,
        project_dir: str | Path | None,
        source_code: str | None,
    ) -> None:
        self.project_dir = project_dir
        self.source_code = source_code
        self.analysis_backend: TreesitterPython = TreesitterPython()

    def get_methods(self) -> List[PyMethod]:
        """
        Given an application or a source code, get all the methods
        """
        return self.analysis_backend.get_all_methods(self.source_code)

    def get_functions(self) -> List[PyMethod]:
        """
        Given an application or a source code, get all the methods
        """
        return self.analysis_backend.get_all_functions(self.source_code)

    def get_modules(self) -> List[PyModule]:
        """
        Given the project directory, get all the modules
        """
        return self.analysis_backend.get_all_modules(self.project_dir)

    def get_method_details(self, method_signature: str) -> PyMethod:
        """
        Given the code body and the method signature, returns the method details related to that method
        Parameters
        ----------
        method_signature: method signature

        Returns
        -------
            PyMethod: Returns the method details related to that method
        """
        return self.analysis_backend.get_method_details(self.source_code, method_signature)

    def is_parsable(self, source_code: str) -> bool:
        """
        Check if the code is parsable
        Args:
            source_code: source code

        Returns:
            True if the code is parsable, False otherwise
        """
        return TreesitterPython().is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> str:
        """
        Get the raw AST
        Args:
            code: source code

        Returns:
            Tree: the raw AST
        """
        return TreesitterPython().get_raw_ast(source_code)

    def get_imports(self) -> List[PyImport]:
        """
        Given an application or a source code, get all the imports
        """
        return self.analysis_backend.get_all_imports_details(self.source_code)

    def get_variables(self, **kwargs):
        """
        Given an application or a source code, get all the variables
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_classes(self) -> List[PyClass]:
        """
        Given an application or a source code, get all the classes
        """
        return self.analysis_backend.get_all_classes(self.source_code)

    def get_classes_by_criteria(self, **kwargs):
        """
        Given an application or a source code, get all the classes given the inclusion and exclution criteria
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_sub_classes(self, **kwargs):
        """
        Given an application or a source code, get all the sub-classes
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_nested_classes(self, **kwargs):
        """
        Given an application or a source code, get all the nested classes
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_constructors(self, **kwargs):
        """
        Given an application or a source code, get all the constructors
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_methods_in_class(self, **kwargs):
        """
        Given an application or a source code, get all the methods within the given class
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_fields(self, **kwargs):
        """
        Given an application or a source code, get all the fields
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")
