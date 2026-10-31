"""Strict wire marker shared by SDK-owned analysis models."""

from typing import Annotated, Literal

from pydantic import BeforeValidator


def _require_integer_version(value):
    if type(value) is not int or value != 2:
        raise ValueError("identity_version must be the integer 2")
    return value


IdentityVersion = Annotated[Literal[2], BeforeValidator(_require_integer_version)]
