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

"""TypeScript model package — pydantic mirror of codeanalyzer-typescript ``src/schema/schema.ts`` (schema v2).

``TSCallEdge``, ``TSExternalSymbol``, ``TSSynthesizedCallable``, ``TSClassAttribute``, ``TSEnumMember`` and
``TSVariableDeclaration`` are 1.x names kept as aliases of their v2 classes.
"""

from .models import (
    TSAnalysis,
    TSAnalyzer,
    TSApplication,
    TSArtifact,
    TSBodyNode,
    TSCallEdge,
    TSCallGraphEdge,
    TSCallable,
    TSCallableParameter,
    TSCallsite,
    TSCdgEdge,
    TSCfgEdge,
    TSClass,
    TSClassAttribute,
    TSComment,
    TSConfigKey,
    TSConfigRead,
    TSConfigUse,
    TSDdgEdge,
    TSDecorator,
    TSDependency,
    TSEntrypoint,
    TSEntrypointReport,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalNode,
    TSExternalSymbol,
    TSField,
    TSImport,
    TSImportBinding,
    TSInterface,
    TSModule,
    TSNamespace,
    TSOverloadSignature,
    TSParamEdge,
    TSSpan,
    TSSummaryEdge,
    TSSymbol,
    TSSynthesizedCallable,
    TSSynthesizedNode,
    TSType,
    TSTypeAlias,
    TSTypeParameter,
    TSVariableDeclaration,
)
from .projections import TSCallableOverview

__all__ = [
    "TSAnalysis",
    "TSAnalyzer",
    "TSApplication",
    "TSArtifact",
    "TSBodyNode",
    "TSCallEdge",
    "TSCallGraphEdge",
    "TSCallable",
    "TSCallableOverview",
    "TSCallableParameter",
    "TSCallsite",
    "TSCdgEdge",
    "TSCfgEdge",
    "TSClass",
    "TSClassAttribute",
    "TSComment",
    "TSConfigKey",
    "TSConfigRead",
    "TSConfigUse",
    "TSDdgEdge",
    "TSDecorator",
    "TSDependency",
    "TSEntrypoint",
    "TSEntrypointReport",
    "TSEnum",
    "TSEnumMember",
    "TSExport",
    "TSExternalNode",
    "TSExternalSymbol",
    "TSField",
    "TSImport",
    "TSImportBinding",
    "TSInterface",
    "TSModule",
    "TSNamespace",
    "TSOverloadSignature",
    "TSParamEdge",
    "TSSpan",
    "TSSummaryEdge",
    "TSSymbol",
    "TSSynthesizedCallable",
    "TSSynthesizedNode",
    "TSType",
    "TSTypeAlias",
    "TSTypeParameter",
    "TSVariableDeclaration",
]
