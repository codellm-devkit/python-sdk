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


"""The SDK's :class:`~cldk.analysis.AnalysisLevel` names mapped to the analyzers' integer levels.

Every codeanalyzer takes ``-a 1..4`` (symbol table, call graph, intraprocedural dataflow,
interprocedural SDG); the SDK names those levels once, here, so no backend sends the enum's
display string or caps the level on the caller's behalf.
"""

from __future__ import annotations

from cldk.analysis import AnalysisLevel

#: The analyzer's ``-a`` integer for each SDK level.
ANALYZER_LEVELS = {
    AnalysisLevel.symbol_table: 1,
    AnalysisLevel.call_graph: 2,
    AnalysisLevel.program_dependency_graph: 3,
    AnalysisLevel.system_dependency_graph: 4,
}

#: The inverse, by the member name a caller writes (``"call_graph"``, not ``"call graph"``) — so
#: an error about the level in use names it the way it was asked for.
LEVEL_NAMES = {n: lvl.name for lvl, n in ANALYZER_LEVELS.items()}


def analyzer_level(level: "AnalysisLevel | str") -> int:
    """The analyzer's integer level for one of the SDK's :class:`~cldk.analysis.AnalysisLevel`
    names.

    Accepts the enum, its value (``"call graph"``) and its member name (``"call_graph"``): the
    facade's parameter is typed ``str``, and the underscore spelling is what a caller writing
    ``analysis_level="system_dependency_graph"`` produces. An unrecognised name raises rather than
    falling back to a default — a level that silently becomes 1 is the defect this function exists
    to close.
    """
    key = str(getattr(level, "value", level)).replace("_", " ")
    try:
        return ANALYZER_LEVELS[AnalysisLevel(key)]
    except ValueError:
        raise ValueError(f"unknown analysis_level {level!r}; expected one of {[lvl.name for lvl in AnalysisLevel]}") from None
