from __future__ import annotations
from pydantic import BaseModel, ConfigDict, model_validator


class _NullSafeBase(BaseModel):
    """Shared base for every canonical (cpg) model. `extra="allow"` so language-specific fields
    (TS is_tsx/exports, Python package, …) are tolerated and preserved rather than rejected — the
    device that lets ONE model set parse every analyzer. The before-validator drops None-valued
    keys so a collection serialized as `null` (Go/Rust/C) falls back to its field default; the one
    sanctioned null (a body-node `callee`) simply resolves to its `None` default."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data
