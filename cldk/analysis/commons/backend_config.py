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

"""Backend configuration objects for the CLDK analysis facades.

The CLDK front end selects analysis behavior along two orthogonal axes: the *language* (chosen by
which :class:`~cldk.core.CLDK` factory method is called) and the *backend* (chosen by the **type**
of the configuration object passed as ``backend=``). The dataclasses here are those configuration
objects -- Parameter Objects that the facades ingest and dispatch on.

Two backend families exist:

* :class:`CodeAnalyzerConfig` (and its language-specific subclasses) selects the in-process
  codeanalyzer backend, which runs the packaged ``codeanalyzer-*`` binary and caches its
  ``analysis.json`` under a language-keyed cache directory.
* :class:`Neo4jConnectionConfig` selects the read-only Neo4j/Cypher backend, which answers the
  same queries over a graph populated out of band.

The per-language ``*Backend`` unions below are the discriminated unions the facades match on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

# The canonical sub-directory name each language's artifacts live under inside the shared cache
# root. Keyed so that a polyglot repository analyzed under more than one language does not have its
# backends overwrite a single shared ``analysis.json``.
_CACHE_KEYS = {"java": "java", "python": "python", "typescript": "typescript", "c": "c"}


@dataclass
class CodeAnalyzerConfig:
    """Select the in-process codeanalyzer backend.

    The backend binary is sourced from the packaged ``codeanalyzer-*`` dependency, so the only
    knob is where analysis artifacts are cached.

    Attributes:
        cache_dir: Root directory for analysis artifacts. When ``None`` the facade defaults it to
            ``<project>/.codeanalyzer``. Each backend writes under a language-keyed subdirectory of
            this root (see :func:`cache_subdir`), so the same root can be shared across languages.
    """

    cache_dir: Union[str, Path, None] = None


@dataclass
class PyCodeAnalyzerConfig(CodeAnalyzerConfig):
    """Select the in-process codeanalyzer backend for Python.

    Adds the Python-only call-graph knobs on top of :class:`CodeAnalyzerConfig`.

    Attributes:
        use_codeql: If ``True`` (default), augment Jedi-based call-graph resolution with CodeQL.
        use_ray: If ``True``, enable Ray-based parallel processing for large projects.
    """

    use_codeql: bool = True
    use_ray: bool = False


@dataclass
class TSCodeAnalyzerConfig(CodeAnalyzerConfig):
    """Select the in-process codeanalyzer backend for TypeScript.

    Adds the TypeScript-only call-graph knob on top of :class:`CodeAnalyzerConfig`.

    Attributes:
        tsc_only: If ``True``, restrict the analyzer to the tsc resolver call graph by passing
            ``--tsc-only`` (codeanalyzer-typescript >= 0.4.2). Defaults to ``False`` (let the
            binary choose its default). This is the supported replacement for the obsolete
            ``--call-graph-provider both``.
    """

    tsc_only: bool = False


@dataclass
class Neo4jConnectionConfig:
    """Select the read-only Neo4j-backed analysis backend.

    The graph is always populated out of band (e.g. a job that runs ``codeanalyzer-* --emit
    neo4j``); the SDK only polls it. This config carries the connection details and which
    application to scope queries to.

    Attributes:
        uri: Bolt URI of the Neo4j server (e.g. ``bolt://localhost:7687``).
        username: Neo4j username (read-only credentials are sufficient).
        password: Neo4j password.
        database: Database name (``None`` => server default).
        application_name: The application anchor name to scope queries to. Matches the
            ``--app-name`` the graph was loaded with (defaults to the project directory name).
    """

    uri: str
    username: str = "neo4j"
    password: str = "neo4j"
    database: str | None = None
    application_name: str | None = None


# Per-language discriminated unions the facades match on.
JavaBackend = Union[CodeAnalyzerConfig, Neo4jConnectionConfig]
PyBackend = Union[PyCodeAnalyzerConfig, Neo4jConnectionConfig]
TSBackend = Union[TSCodeAnalyzerConfig, CodeAnalyzerConfig, Neo4jConnectionConfig]


def cache_subdir(cache_dir: Union[str, Path, None], project_dir: Union[str, Path, None], language: str) -> Path | None:
    """Resolve the language-keyed cache directory for a backend.

    Args:
        cache_dir: The cache root from the backend config. When ``None``, defaults to
            ``<project_dir>/.codeanalyzer``.
        project_dir: The project directory, used to derive the default root.
        language: The canonical language key (``"java"``, ``"python"``, ``"typescript"``, ``"c"``).

    Returns:
        ``<root>/<language>`` as an absolute path, or ``None`` if no root can be determined
        (no ``cache_dir`` and no ``project_dir``).
    """
    key = _CACHE_KEYS.get(language, language)
    if cache_dir is not None:
        root = Path(cache_dir).expanduser().resolve()
    elif project_dir is not None:
        root = Path(project_dir).expanduser().resolve() / ".codeanalyzer"
    else:
        return None
    return root / key
