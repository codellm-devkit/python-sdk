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

"""Content-addressed cache locations for the Python analysis backend.

Two cache tiers, keyed so the *expensive* artifact (the virtualenv)
survives source edits:

- **backend tier** — ``<root>/venvs/<dep_hash>/``, keyed by the
  *dependency manifest* hash. ``codeanalyzer-python`` rebuilds the
  virtualenv only when dependencies change, so this directory makes the
  ~30s ``pip install`` reusable across every source edit.

  Caveat: this directory ALSO holds the CodeQL database
  (``<dir>/.codeanalyzer/.../codeql/<name>-db``). The CodeQL DB is a
  function of the *source*, not the dependencies — ``codeanalyzer-python``
  manages its own ``.checksum``-based invalidation and rebuilds the DB
  in place on any ``*.py`` change. The dep-hash key does NOT (and
  cannot) make the CodeQL DB survive source edits; it only keeps the
  venv stable. Splitting the DB onto a source-keyed root requires an
  upstream change, since ``codeanalyzer-python`` couples venv and DB
  under one ``cache_dir``.

- **analysis tier** — ``<root>/cache/<key[:2]>/<key>/analysis.json``,
  keyed by the full composite (backend version, analysis level, CodeQL
  flag, target files, full source-tree hash). This is what makes
  revisiting an exact prior source state cheap — the final
  ``analysis.json`` is returned without rebuilding the DB at all.

``<root>`` is ``$CLDK_CACHE_DIR`` when set, else ``~/.cldk``.
"""

from __future__ import annotations

import hashlib
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable, List

# Directories that never contribute to analysis input.
_EXCLUDED_DIR_PARTS = {
    ".git",
    ".venv",
    "venv",
    ".codeanalyzer",
    ".cldk-cache",
    "__pycache__",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
}

# Files whose content determines the virtualenv (dependency tier key).
_DEPENDENCY_MANIFESTS = (
    "requirements.txt",
    "requirements-dev.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
)


def backend_version() -> str:
    """Installed ``codeanalyzer-python`` version, or ``unknown``.

    Part of the analysis key: a backend upgrade can change the schema
    or extraction logic, so cached artifacts must not survive it.
    """
    try:
        return version("codeanalyzer-python")
    except PackageNotFoundError:
        return "unknown"


def _iter_source_files(project_dir: Path) -> Iterable[Path]:
    """Yield analysis-relevant ``*.py`` files in deterministic order."""
    for path in sorted(project_dir.rglob("*.py")):
        if _EXCLUDED_DIR_PARTS.intersection(path.parts):
            continue
        yield path


def tree_hash(project_dir: Path) -> str:
    """SHA256 over every analysis-relevant ``*.py`` file's bytes.

    Mirrors ``codeanalyzer-python``'s own checksum semantics so the two
    layers agree on "did the source change?". Path-relative names are
    folded in so a rename alone busts the cache.
    """
    digest = hashlib.sha256()
    for path in _iter_source_files(project_dir):
        digest.update(str(path.relative_to(project_dir)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def dependency_hash(project_dir: Path) -> str:
    """SHA256 over the dependency manifests that exist in the project.

    Keys the virtualenv tier. Source edits do not change this, so the
    (slow) ``pip install`` is reused until dependencies actually move.
    """
    digest = hashlib.sha256()
    for name in _DEPENDENCY_MANIFESTS:
        manifest = project_dir / name
        if manifest.is_file():
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(manifest.read_bytes())
    # Empty digest (no manifests) still yields a stable value.
    return digest.hexdigest()


def compute_cache_key(
    project_dir: Path,
    analysis_level: str,
    use_codeql: bool,
    target_files: List[str] | None,
) -> str:
    """Composite key for the analysis tier.

    Any input that changes the produced ``analysis.json`` is folded in:
    backend version, analysis level, CodeQL flag, the (sorted) target
    file list, and the full source-tree hash.
    """
    parts = [
        backend_version(),
        str(analysis_level),
        "codeql=1" if use_codeql else "codeql=0",
        ",".join(sorted(t.strip() for t in target_files)) if target_files else "",
        tree_hash(project_dir),
    ]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def cache_root() -> Path:
    """Cache root: ``$CLDK_CACHE_DIR`` if set, else ``~/.cldk``."""
    override = os.environ.get("CLDK_CACHE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".cldk"


def default_backend_cache_dir(project_dir: Path) -> Path:
    """Backend tier root — keyed by dependency manifest hash.

    Keeps the virtualenv stable across source edits. The CodeQL DB also
    lives here but is invalidated by ``codeanalyzer-python`` itself on
    any source change (see module docstring) — the dep-hash key does not
    extend the DB's lifetime.
    """
    return cache_root() / "venvs" / dependency_hash(project_dir)


def default_analysis_dir(
    project_dir: Path,
    analysis_level: str,
    use_codeql: bool,
    target_files: List[str] | None,
) -> Path:
    """Analysis-JSON tier — keyed by the full composite, prefix-sharded."""
    key = compute_cache_key(project_dir, analysis_level, use_codeql, target_files)
    return cache_root() / "cache" / key[:2] / key
