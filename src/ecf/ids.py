"""Identifier types.

Internally IDs are `NewType`s over `str` (no runtime cost). At the API boundary they are validated
with the `Annotated` types below (SPEC §17.2).
"""

from __future__ import annotations

import secrets
from typing import Annotated, NewType

from pydantic import StringConstraints

AddressId = NewType("AddressId", str)
InstallName = NewType("InstallName", str)
StableId = NewType("StableId", str)
GrantId = NewType("GrantId", str)
JobId = NewType("JobId", str)
NonceId = NewType("NonceId", str)

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$"
HEX64_PATTERN = r"^[0-9a-f]{64}$"
HEX32_PATTERN = r"^[0-9a-f]{32}$"
SHORT_ID_PATTERN = r"^[0-9a-f]{8,64}$"

AddressIdField = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
InstallNameField = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
StableIdField = Annotated[str, StringConstraints(pattern=HEX64_PATTERN)]
RandomIdField = Annotated[str, StringConstraints(pattern=HEX32_PATTERN)]
ShortIdField = Annotated[str, StringConstraints(pattern=SHORT_ID_PATTERN)]


def new_random_id() -> str:
    """A 32-hex random ID for grants, jobs and nonces."""
    return secrets.token_hex(16)


def new_grant_id() -> GrantId:
    return GrantId(new_random_id())


def new_job_id() -> JobId:
    return JobId(new_random_id())


def new_nonce_id() -> NonceId:
    return NonceId(new_random_id())
