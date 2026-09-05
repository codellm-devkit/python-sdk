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


class GraphSchemaMismatch(RuntimeError):
    """Raised when a Neo4j backend's attached graph doesn't speak its expected vocabulary.

    A Neo4j-backed analysis facade queries a graph some other process built with a
    ``codeanalyzer-*`` analyzer. If that analyzer's generation predates (or postdates) the
    vocabulary the querying backend was written against, every query silently returns zero
    rows — indistinguishable from "this codebase genuinely has no callables". This exception
    makes that mismatch loud: it names what the backend expected, what it actually found on
    the graph, and which relationship types are missing.

    Raised by a one-time schema probe run at connection time (e.g.
    :meth:`~cldk.analysis.python.neo4j.neo4j_backend.PyNeo4jBackend._probe_schema`), never by
    an individual query.

    Attributes:
        expected (set[str]): The relationship types the backend requires.
        found (set[str]): The relationship types actually present on the connected graph.
        missing (set[str]): ``expected - found`` — what the graph is missing.
    """

    def __init__(self, expected: set[str], found: set[str], missing: set[str], message: str | None = None) -> None:
        """Initialize the exception with the expected/found/missing vocabulary sets.

        Args:
            expected: The relationship types the backend requires to be present.
            found: The relationship types actually present on the connected graph.
            missing: The subset of ``expected`` absent from ``found``.
            message: A precomputed, human-readable message. If omitted, one is built from
                ``expected``/``found``/``missing`` naming the likely analyzer generation.
        """
        self.expected = expected
        self.found = found
        self.missing = missing
        self.message = message or self._describe(expected, found, missing)
        super().__init__(self.message)

    @staticmethod
    def _describe(expected: set[str], found: set[str], missing: set[str]) -> str:
        if "PY_HAS_CALLSITE" in found:
            generation = (
                "This looks like a graph built by codeanalyzer-python 0.3.x (it has "
                "PY_HAS_CALLSITE / :PyCallSite instead of PY_HAS_BODY_NODE / :PyBodyNode). "
                "Re-ingest the project with codeanalyzer-python>=1.4.0."
            )
        elif not found:
            generation = "The graph has no relationship types at all — an empty or asset-only database, not a codeanalyzer-python application graph."
        else:
            generation = "This does not match any known codeanalyzer-python generation this backend supports."
        return f"Graph schema mismatch: expected relationship types {sorted(expected)}, missing {sorted(missing)}. Found on the graph: {sorted(found)}. {generation}"


class SelectorNotInGraph(ValueError):
    """Raised when a scoping keyword names something the analysed application does not hold.

    ``get_symbol_table(paths=...)``, ``get_classes(module=...)`` and ``get_call_graph(roots=...)``
    all narrow a whole-application enumeration to a caller-supplied selection. A value in that
    selection which matches nothing used to contribute nothing, so a typo'd path and a module that
    genuinely declares no classes were the *same* empty dict — the ambiguous-empty defect the
    query facade exists to remove (D7). Raising makes them different answers.

    The message names the values that matched nothing and stops there. It deliberately offers no
    near-miss candidates: leg 1.5's E8 puts typo-tolerant matching out of scope "not in the
    resolver, not in the error path", because a suggestion is a guess, and a guess presented as a
    correction is the confident-wrong-answer failure this design exists to prevent.

    Subclasses :class:`ValueError` because that is what these accessors already document raising
    for a malformed scoping keyword (an empty selection, a ``depth`` below 1) — an unmatched value
    is the same class of caller error, not a new one, and a caller catching ``ValueError`` around a
    scoped call keeps working.

    Attributes:
        kind (str): Which keyword named them — ``"paths"``, ``"module"`` or ``"roots"``.
        missing (list[str]): The values, as the caller wrote them, that matched nothing.
        requested (int): How many values the keyword carried in total, so a partial miss is
            visible as a partial miss.
    """

    def __init__(self, kind: str, missing: list[str], requested: int) -> None:
        """Initialize with the keyword, the values that missed, and how many were asked for.

        Args:
            kind: The scoping keyword's name (``"paths"`` / ``"module"`` / ``"roots"``).
            missing: The unmatched values, in the caller's own spelling.
            requested: The total number of values the keyword carried.
        """
        self.kind = kind
        self.missing = list(missing)
        self.requested = requested
        self.message = f"{len(self.missing)} of {requested} {kind} not in graph: " + ", ".join(repr(m) for m in self.missing)
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
