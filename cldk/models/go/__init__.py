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

"""Go model package — a pydantic mirror of the ``codeanalyzer-go`` (``cango``) ``analysis.json``
(schema v2, L1/L2).

Go is the SDK's first class-less, type-centric facade: :class:`GoType` is one model discriminated
by a ``kind`` field (``struct`` | ``interface``), never a class; package-level ``functions`` are
kept distinct from receiver ``methods``. No ``projections.py`` yet — the overview models the
Java/TS siblings ship are out of this train's SDK3 scope and add purely additively later.
"""

from .models import (
    GoAnalysis,
    GoAnalyzer,
    GoApplication,
    GoBodyNode,
    GoCallable,
    GoCallGraphEdge,
    GoField,
    GoImport,
    GoMetrics,
    GoModule,
    GoParam,
    GoSpan,
    GoType,
)

__all__ = [
    "GoAnalysis",
    "GoAnalyzer",
    "GoApplication",
    "GoBodyNode",
    "GoCallable",
    "GoCallGraphEdge",
    "GoField",
    "GoImport",
    "GoMetrics",
    "GoModule",
    "GoParam",
    "GoSpan",
    "GoType",
]
