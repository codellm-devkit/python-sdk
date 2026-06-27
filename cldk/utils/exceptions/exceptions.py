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

"""Custom exceptions module for CLDK.

This module defines custom exception classes used throughout the CLDK library
to provide clear, specific error information for different failure scenarios.

The exceptions are organized by category:
    - **Initialization Errors**: :class:`CldkInitializationException`
    - **Analysis Backend Errors**: :class:`CodeanalyzerExecutionException`,
      :class:`CodeanalyzerUsageException`

All exceptions inherit from Python's built-in :class:`Exception` class and
include a descriptive message attribute.
"""


class CldkInitializationException(Exception):
    """Exception raised for errors during CLDK initialization.

    This exception is raised when the CLDK core or its components fail to
    initialize properly. Common causes include:
        - Invalid language specification
        - Missing required parameters
        - Conflicting configuration options

    Attributes:
        message (str): A descriptive error message explaining the
            initialization failure.

    See Also:
        :class:`~cldk.CLDK`: The main entry point that may raise this exception.
    """

    def __init__(self, message: str) -> None:
        """Initialize the exception with a descriptive message.

        Args:
            message: A descriptive error message explaining what went wrong
                during initialization and how to resolve it.
        """
        self.message = message
        super().__init__(self.message)


class CodeanalyzerExecutionException(Exception):
    """Exception raised for errors during CodeAnalyzer execution.

    This exception is raised when the CodeAnalyzer backend (JAR or Python)
    fails during analysis. Common causes include:
        - Backend process crash or timeout
        - Invalid project structure
        - Memory exhaustion during analysis
        - Missing dependencies

    Attributes:
        message (str): A descriptive error message explaining the
            execution failure.

    See Also:
        :class:`~cldk.analysis.java.codeanalyzer.JCodeanalyzer`: Java backend.
        :class:`~cldk.analysis.python.codeanalyzer.PyCodeanalyzer`: Python backend.
    """

    def __init__(self, message: str) -> None:
        """Initialize the exception with a descriptive message.

        Args:
            message: A descriptive error message explaining what went wrong
                during CodeAnalyzer execution.
        """
        self.message = message
        super().__init__(self.message)


class CodeanalyzerUsageException(Exception):
    """Exception raised for incorrect CodeAnalyzer usage.

    This exception is raised when the CodeAnalyzer is used incorrectly,
    such as providing invalid argument combinations or unsupported
    configurations. Common causes include:
        - Unsupported analysis level for the language
        - Invalid file paths
        - Incompatible option combinations

    Attributes:
        message (str): A descriptive error message explaining the
            usage error and how to correct it.

    See Also:
        :class:`CldkInitializationException`: Related exception for
            initialization-time errors.
    """

    def __init__(self, message: str) -> None:
        """Initialize the exception with a descriptive message.

        Args:
            message: A descriptive error message explaining the usage error
                and how to correct it.
        """
        self.message = message
        super().__init__(self.message)
