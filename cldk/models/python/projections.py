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

"""CLDK-defined projection models for the Python facade.

Unlike the rest of :mod:`cldk.models.python`, these are **not** part of the ``codeanalyzer-python``
schema — they are lightweight, field-projected views CLDK exposes so callers can enumerate the
application set-at-a-time without paying for the full per-callable reconstruction. They map cleanly
to a single Cypher ``RETURN`` on the Neo4j backend and to one symbol-table walk in-process.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class PyCallableOverview(BaseModel):
    """A lightweight projection of one callable — enough to enumerate and filter without the full
    :class:`~cldk.models.python.PyCallable` reconstruction (call-sites, inner callables, locals).

    Returned set-at-a-time by :meth:`PythonAnalysis.get_callables_overview` /
    :meth:`PythonAnalysis.get_decorated_callables`. Body-inspect only the few you need afterwards
    via :meth:`PythonAnalysis.get_method`/:meth:`PythonAnalysis.get_method_bodies`.

    Attributes:
        signature: The callable's unique signature (the key the call graph references).
        name: The callable's short name.
        class_signature: Signature of the class that declares this callable as a method, or ``None``
            for a module-level or nested function.
        kind: ``"method"`` when ``class_signature`` is set, else ``"function"``.
        path: Project-relative path of the declaring module.
        start_line / end_line: The callable's line span.
        decorators: The decorator names applied to the callable.
    """

    signature: str
    name: str
    class_signature: Optional[str] = None
    kind: str
    path: str
    start_line: int
    end_line: int
    decorators: List[str] = []
