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

"""Validated raw application identity and canonical URI ownership."""

import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from cldk.utils.exceptions import CodeanalyzerExecutionException


@dataclass(frozen=True)
class ApplicationIdentity:
    namespace: str
    application_name: str
    application_id: str


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value in {".", ".."} or any(c in "/\\" or unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise ValueError("identity names must be nonempty raw names without separators or control characters")
    return quote(value, safe="-._~", encoding="utf-8", errors="strict")


def resolve_identity(application_name: str | None, namespace: str | None, *, project_root: Path | None = None) -> ApplicationIdentity:
    if application_name is None:
        if project_root is None:
            raise CodeanalyzerExecutionException("application_name is required to scope queries to an application.")
        application_name = Path(project_root).expanduser().resolve().name
    namespace = application_name if namespace is None else namespace
    return ApplicationIdentity(namespace, application_name, f"can://{_segment(namespace)}/{_segment(application_name)}")


def validate_identity(payload, identity: ApplicationIdentity, *, envelope: bool = True):
    """Reject legacy or foreign persisted data without synthesizing metadata."""
    if envelope and hasattr(payload, "application"):
        app_model = payload.application
        if hasattr(app_model, "identity_version") and "identity_version" not in app_model.model_fields_set:
            raise CodeanalyzerExecutionException("Analysis identity marker is missing from the application.")
    raw = (
        payload.model_dump(include={"identity_version": True, "language": True, "application": {"identity_version", "id", "application_name", "identity_namespace"}})
        if envelope and hasattr(payload, "model_dump")
        else payload
    )
    if not isinstance(raw, dict):
        raise CodeanalyzerExecutionException("Analysis identity metadata must be an object.")
    app = raw.get("application", {}) if envelope else raw
    if not isinstance(app, dict):
        raise CodeanalyzerExecutionException("Application identity metadata must be an object.")
    if envelope and raw.get("language") != "java" and (type(app.get("identity_version")) is not int or app.get("identity_version") != 2):
        raise CodeanalyzerExecutionException("Analysis identity marker is missing from the application.")
    if (
        type(raw.get("identity_version")) is not int
        or raw.get("identity_version") != 2
        or app.get("application_name") != identity.application_name
        or app.get("identity_namespace") != identity.namespace
        or app.get("id") != identity.application_id
    ):
        raise CodeanalyzerExecutionException("Analysis identity metadata is missing, incompatible, or outside the requested application scope.")
    return payload


def cache_matches(path: Path, identity: ApplicationIdentity) -> bool:
    try:
        validate_identity(json.loads(path.read_text(encoding="utf-8")), identity)
        return True
    except (OSError, ValueError, CodeanalyzerExecutionException):
        return False


def index_module_rows(rows: Iterable[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    """Collapse repeated module projections by canonical identity, never by filename."""
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        module_id, file_key = row["module_id"], row["file_key"]
        if module_id in index and index[module_id]["file_key"] != file_key:
            raise ValueError(f"Conflicting file keys for canonical module {module_id}: {index[module_id]['file_key']!r} and {file_key!r}")
        index[module_id] = {"module_id": module_id, "file_key": file_key}
    return index


def scoped_module_keys(index: Mapping[str, Mapping[str, str]], application_id: str, languages: tuple[str, ...]) -> dict[str, str]:
    """Derive the relative-key API only after validating owner and language.

    Candidate IDs remain in the primary index; an ambiguous relative key cannot silently
    select whichever module happened to be projected last.
    """
    candidates: dict[str, set[str]] = {}
    prefixes = tuple(f"{application_id}/{language}/" for language in languages)
    for module_id, row in index.items():
        if not module_id.startswith(prefixes):
            raise ValueError(f"Module URI is outside the bound application/language scope: {module_id}")
        candidates.setdefault(row["file_key"], set()).add(module_id)
    for key, ids in candidates.items():
        if len(ids) != 1:
            raise ValueError(f"Ambiguous module file key {key!r}: {sorted(ids)}")
    return {key: next(iter(ids)) for key, ids in candidates.items()}
