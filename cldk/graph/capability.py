from __future__ import annotations
from typing import Optional, Dict


class CapabilityError(Exception):
    pass


def require(level_needed: int, provider, *, strict: bool, what: str) -> Optional[Dict]:
    available = provider.max_level()
    if available >= level_needed:
        return None
    if strict:
        raise CapabilityError(
            f"{what} requires analysis level {level_needed}; backend is at level {available}. "
            f"Re-analyze at -a {level_needed} or drop strict=True to degrade.")
    return {
        "requested": level_needed,
        "available": available,
        "gap": f"{what} requires level {level_needed}; backend at level {available} — "
               f"reduced result returned; absence of a result here is UNKNOWN, not safety.",
    }
