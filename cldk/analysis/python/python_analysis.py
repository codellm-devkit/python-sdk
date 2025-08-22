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

"""Python analysis utilities.

Provides a high-level API to query modules, classes, functions, and methods
from Python projects or single-source inputs using Treesitter.
"""

from pathlib import Path
from typing import List

from cldk.analysis.commons.treesitter import TreesitterPython
from cldk.models.python.models import PyMethod, PyImport, PyModule, PyClass


class PythonAnalysis:
    """Analysis façade for Python code.

    Args:
        project_dir (str | Path | None): Directory path of the project.
        source_code (str | None): Source text for single-file analysis.
    """

    def __init__(
        self,
        project_dir: str | Path | None,
        source_code: str | None,
    ) -> None:
        self.project_dir = project_dir
        self.source_code = source_code
        self.analysis_backend: TreesitterPython = TreesitterPython()

    def get_methods(self) -> List[PyMethod]:
        """Return all methods.

        Returns:
            list[PyMethod]: Methods discovered in the source code.

        Examples:
            >>> src = 'class C: def f(self): pass def g(self): pass'
            >>> pa = PythonAnalysis(project_dir=None, source_code=src)  # doctest: +SKIP
            >>> len(pa.get_methods())  # doctest: +SKIP
            2
        """
        return self.analysis_backend.get_all_methods(self.source_code)

    def get_functions(self) -> List[PyMethod]:
        """Return all functions.

        Returns:
            list[PyMethod]: Functions discovered in the source code.

        Examples:
            >>> src = 'def f(): return 1'
            >>> pa = PythonAnalysis(project_dir=None, source_code=src)
            >>> [m.full_signature for m in pa.get_functions()]
            ['f()']
        """
        return self.analysis_backend.get_all_functions(self.source_code)

    def get_modules(self) -> List[PyModule]:
        """Return all modules in the project directory.

        Returns:
            list[PyModule]: Modules discovered under project_dir.

        Examples:
            Create a temporary project and discover modules:

            >>> import os, tempfile
            >>> d = tempfile.mkdtemp()
            >>> _ = open(os.path.join(d, 'a.py'), 'w').write('print(1)')
            >>> _ = open(os.path.join(d, 'b.py'), 'w').write('print(2)')
            >>> pa = PythonAnalysis(project_dir=d, source_code=None)
            >>> len(pa.get_modules()) >= 2
            True
        """
        return self.analysis_backend.get_all_modules(self.project_dir)

    def get_method_details(self, method_signature: str) -> PyMethod:
        """Return details for a given method signature.

        Args:
            method_signature (str): Method signature to look up.

        Returns:
            PyMethod: Method details.

        Examples:
            >>> src = 'class C: def add(self, a, b): return a+b'
            >>> pa = PythonAnalysis(project_dir=None, source_code=src)  # doctest: +SKIP
            >>> pa.get_method_details('add(self, a, b)').full_signature  # doctest: +SKIP
            'add(self, a, b)'
        """
        return self.analysis_backend.get_method_details(self.source_code, method_signature)

    def is_parsable(self, source_code: str) -> bool:
        """Check if the source code is parsable.

        Args:
            source_code (str): Source code to parse.

        Returns:
            bool: True if parsable, False otherwise.

        Examples:
            >>> PythonAnalysis(None, None).is_parsable('def f(): pass')
            True
            >>> PythonAnalysis(None, None).is_parsable('def f(): pass if')
            False
        """
        return TreesitterPython().is_parsable(source_code)

    def get_raw_ast(self, source_code: str) -> str:
        """Parse and return the raw AST.

        Args:
            source_code (str): Source code to parse.

        Returns:
            str: Raw AST representation.

        Examples:
            >>> ast = PythonAnalysis(None, None).get_raw_ast('def f(): pass')
            >>> isinstance(ast, str)
            True
        """
        return TreesitterPython().get_raw_ast(source_code)

    def get_imports(self) -> List[PyImport]:
        """Return all import statements.

        Returns:
            list[PyImport]: Imports discovered in the source code.

        Examples:
            >>> src = 'import os; from math import sqrt; from x import *'
            >>> pa = PythonAnalysis(project_dir=None, source_code=src)
            >>> len(pa.get_imports())
            3
        """
        return self.analysis_backend.get_all_imports_details(self.source_code)

    def get_variables(self, **kwargs):
        """Return all variables discovered in the source code.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='x=1')
            >>> pa.get_variables()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_classes(self) -> List[PyClass]:
        """Return all classes.

        Returns:
            list[PyClass]: Classes discovered in the source code.

        Examples:
            >>> src = 'class A: pass'
            >>> pa = PythonAnalysis(project_dir=None, source_code=src)
            >>> [c.class_name for c in pa.get_classes()]
            ['A']
        """
        return self.analysis_backend.get_all_classes(self.source_code)

    def get_classes_by_criteria(self, **kwargs):
        """Return classes filtered by inclusion/exclusion criteria.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: pass')
            >>> pa.get_classes_by_criteria()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_sub_classes(self, **kwargs):
        """Return all subclasses.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: pass')
            >>> pa.get_sub_classes()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_nested_classes(self, **kwargs):
        """Return all nested classes.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: class B: pass')
            >>> pa.get_nested_classes()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_constructors(self, **kwargs):
        """Return all constructors.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: def __init__(self): pass')
            >>> pa.get_constructors()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_methods_in_class(self, **kwargs):
        """Return all methods within a given class.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: def f(self): pass')
            >>> pa.get_methods_in_class()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")

    def get_fields(self, **kwargs):
        """Return all fields.

        Raises:
            NotImplementedError: This functionality is not implemented yet.

        Examples:
            >>> pa = PythonAnalysis(project_dir=None, source_code='class A: x=1')
            >>> pa.get_fields()  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            NotImplementedError: Support for this functionality has not been implemented yet.
        """
        raise NotImplementedError("Support for this functionality has not been implemented yet.")
