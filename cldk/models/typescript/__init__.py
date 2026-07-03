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

"""TypeScript model package — identity-only schema mirror of codeanalyzer-ts/src/schema.ts."""

from .models import (
    TSApplication,
    TSCallEdge,
    TSCallable,
    TSCallableParameter,
    TSCallsite,
    TSClass,
    TSClassAttribute,
    TSComment,
    TSDecorator,
    TSEntrypoint,
    TSEnum,
    TSEnumMember,
    TSExport,
    TSExternalSymbol,
    TSImport,
    TSInterface,
    TSModule,
    TSNamespace,
    TSOverloadSignature,
    TSSymbol,
    TSSynthesizedCallable,
    TSTypeAlias,
    TSTypeParameter,
    TSVariableDeclaration,
)

__all__ = [
    "TSApplication",
    "TSCallEdge",
    "TSCallable",
    "TSCallableParameter",
    "TSCallsite",
    "TSClass",
    "TSClassAttribute",
    "TSComment",
    "TSDecorator",
    "TSEntrypoint",
    "TSEnum",
    "TSEnumMember",
    "TSExport",
    "TSExternalSymbol",
    "TSImport",
    "TSInterface",
    "TSModule",
    "TSNamespace",
    "TSOverloadSignature",
    "TSSymbol",
    "TSSynthesizedCallable",
    "TSTypeAlias",
    "TSTypeParameter",
    "TSVariableDeclaration",
]
