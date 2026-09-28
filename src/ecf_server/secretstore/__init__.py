"""The SecretStore port (SPEC §3.3, §11.6). The service is the only writer of secrets.

Backends: macOS Keychain; Linux Secret Service or `systemd-creds --user` (unverified until V1.6).
Names are fixed (SPEC §11.6); items live under the service name `email-classify-filter/<install>`.
"""

from __future__ import annotations

import re
from typing import Protocol

from ecf.errors import InvalidInputError, ServiceUnavailableError

SERVICE_PREFIX = "email-classify-filter"
_NAME = re.compile(
    r"^(mailbox/[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?|slack/bot|slack/app|models-api-key"
    r"|export-signing-seed)$"
)


def service_name(install: str) -> str:
    return f"{SERVICE_PREFIX}/{install}"


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise InvalidInputError(f"not a secret name ecf uses: {name!r}")
    return name


class SecretStoreNeedsYouError(ServiceUnavailableError):
    """The store is locked or asks for permission; the service waits instead of hanging."""


class SecretStore(Protocol):
    backend: str

    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
