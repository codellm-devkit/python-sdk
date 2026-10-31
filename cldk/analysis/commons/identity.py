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
