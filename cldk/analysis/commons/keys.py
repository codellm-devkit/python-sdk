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

"""Module keys and scoping: how a caller's path names a module, in any language.

Lifted out of the Python backend and its Neo4j reconstruction (leg 2.5a, G4) unchanged except
that :func:`module_dotted` takes the language's source extensions as a parameter. Keys are the
repo-relative paths the analyzer saw; every ruling here is about matching a caller's spelling of
one to the key that exists, or refusing.
"""

from __future__ import annotations

import os
import posixpath
from typing import Collection, Iterable, List, Sequence

from cldk.analysis.commons.bounds import check_depth, check_selector, reject_bare_string


def resolve_module_key(path: str, keys: Iterable[str]) -> str:
    """The symbol-table / graph ``file_key`` naming ``path``, or ``path`` unchanged if none does.

    A caller of :meth:`PythonAnalysisBackend.locate` hands over whatever its scanner printed —
    ``./src/app.py``, ``src/../src/app.py``, or an absolute path from the machine the scan ran on —
    while both backends are keyed by the project-relative path the analyzer saw. Exact key first,
    then the normalised form, then the longest known key the normalised path *ends on a segment
    boundary* of (which is what an absolute path is). Returning ``path`` unchanged when nothing
    matches is deliberate: the caller then gets ``file_not_in_graph`` naming the path it asked
    about, not a silently substituted neighbour.
    """
    keys = list(keys)
    if path in keys:
        return path
    norm = posixpath.normpath(str(path).replace(os.sep, "/"))
    if norm in keys:
        return norm
    suffix_matches = [k for k in keys if norm.endswith("/" + k)]
    return max(suffix_matches, key=len) if suffix_matches else path


def scope_paths(paths: Sequence[str] | None, keys: Iterable[str], kind: str = "paths") -> List[str] | None:
    """Resolve requested module paths to symbol-table keys, or ``None`` for "the whole application".

    Both backends route their ``paths=`` / ``module=`` keywords through here, so the lenient
    resolution (:func:`resolve_module_key` — an absolute path or one with native separators finds
    its module) and the strictness (:func:`check_selector` — a path naming no module raises) cannot
    drift apart between them.

    Args:
        paths: What the caller named, or ``None`` for the unscoped call.
        keys: The symbol-table keys that exist — ``symbol_table.keys()`` locally, the
            application's module ``file_key``s over Neo4j.
        kind: The keyword's name for the error message; ``"module"`` for ``get_classes``, whose
            single-valued keyword routes through here as a one-element sequence.

    **Resolution is many-to-one, and the result is de-duplicated.** Leniency is the whole point of
    :func:`resolve_module_key` — ``"pkg/a.py"`` and ``"/abs/pkg/a.py"`` are two spellings a scanner
    may plausibly hand over for the *same* module — so two requested paths legitimately collapse to
    one key and the caller gets one entry back. Raising on the collapse would punish the very
    caller the leniency exists for; de-duplicating explicitly is what keeps the returned list from
    naming the same module twice and asking both backends to fetch it twice.

    Raises:
        TypeError: ``paths`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``paths`` is an empty sequence.
        SelectorNotInGraph: a path names no module in this application.
    """
    reject_bare_string(kind, paths)
    if paths is None:
        return None
    known = list(keys)
    resolved = [resolve_module_key(p, known) for p in paths]
    check_selector(kind, list(paths), [p for p, r in zip(paths, resolved) if r not in known])
    return list(dict.fromkeys(resolved))


def call_graph_scope(roots: Sequence[str] | None, depth: int | None) -> List[str] | None:
    """Normalise :meth:`PythonAnalysisBackend.get_call_graph`'s scoping keywords.

    Returns the roots as a list, or ``None`` for "the whole application" — the unscoped call,
    which must keep behaving exactly as it did before the keywords existed.

    Both backends route through this so the two cannot drift apart on what a keyword combination
    means (the failure mode Fix 1 of leg 1.5 had to go back and repair on the child-fetch paths).
    Whether each root *exists* is checked later, by whichever backend has the graph in hand, but
    through the same :func:`check_selector` — see :func:`bounded_subgraph`.

    Raises:
        TypeError: ``roots`` is a bare string (see :func:`reject_bare_string`).
        ValueError: ``depth`` that is not a positive ``int``, ``depth`` without ``roots``, or an
            empty ``roots``. A hop budget with no origin to count from has no meaning, and quietly
            returning all 364,752 edges would be the worst of the available answers — the caller
            asked for a bounded graph and would be handed an unbounded one with no signal.
            ``depth`` is type-checked rather than merely range-checked because the two ways of
            getting it wrong are silent otherwise: ``depth="2"`` raised ``TypeError`` from the
            comparison, and ``depth=2.5`` was accepted and truncated to 2 by the Cypher/ego-graph
            radius. ``bool`` is rejected for the same reason — ``depth=True`` is ``1`` by accident.
    """
    check_depth(depth)
    reject_bare_string("roots", roots)
    if roots is None:
        if depth is not None:
            raise ValueError("depth= requires roots=; a hop budget needs an origin to count from")
        return None
    check_selector("roots", list(roots), ())
    return list(roots)


def module_key_of(node_id: str, prefix: str, known: Collection[str]) -> str:
    """The repo-relative module key embedded in a ``can://`` id (F4).

    Ids are ``<prefix><file_key>/<rest>`` (or exactly ``<prefix><file_key>`` for a module), and a
    file key can itself contain ``.py/`` as a directory name, so the key is never recovered by
    splitting: every ``/``-boundary prefix of the id is tried longest first and the first that is
    a member of ``known`` -- the application's verified module keys -- wins. A miss raises: a key
    we cannot verify is a defect, not a guess. ``known`` should be a set; this runs once per row.
    """
    if not node_id.startswith(prefix):
        raise KeyError(node_id)
    parts = node_id[len(prefix) :].split("/")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n])
        if candidate in known:
            return candidate
    raise KeyError(node_id)


def module_dotted(path: str, *, extensions: Sequence[str] = (".py",)) -> str:
    """The dotted module name a repo-relative path spells: ``"odoo/tools/mail.py"`` →
    ``"odoo.tools.mail"``, ``"pkg/__init__.py"`` → ``"pkg"``. The same derivation the analyzer's
    signatures embody, so ``in_module=`` can be written the way a signature reads. ``extensions`` is
    the language's source suffixes; the Python default keeps every existing call site as it was."""
    stem = next((path[: -len(ext)] for ext in extensions if path.endswith(ext)), path)
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")
