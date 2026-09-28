"""Keychain (macOS) and Secret Service (Linux) through `keyring` (SPEC §11.6).

On macOS the service runs with Keychain prompts off (OD-163), so a read the process isn't trusted
for fails at once; that and a locked store both raise SecretStoreNeedsYouError.
"""

from __future__ import annotations

from typing import Any

import keyring.errors
from keyring.backend import KeyringBackend

from ecf_server.secretstore import SecretStoreNeedsYouError, check_name, service_name

_NEEDS_YOU = (keyring.errors.KeyringLocked, keyring.errors.InitError)


class KeyringSecretStore:
    def __init__(self, backend: KeyringBackend, install: str, *, backend_name: str) -> None:
        self._kr = backend
        self._service = service_name(install)
        self.backend = backend_name

    def get(self, name: str) -> str | None:
        try:
            value: Any = self._kr.get_password(self._service, check_name(name))
        except _NEEDS_YOU as exc:
            raise SecretStoreNeedsYouError(f"secret store needs you: {exc}") from exc
        except keyring.errors.KeyringError as exc:
            raise SecretStoreNeedsYouError(f"secret store refused access: {exc}") from exc
        return None if value is None else str(value)

    def set(self, name: str, value: str) -> None:
        try:
            self._kr.set_password(self._service, check_name(name), value)  # pyright: ignore[reportUnknownMemberType]
        except keyring.errors.KeyringError as exc:
            raise SecretStoreNeedsYouError(f"secret store refused the write: {exc}") from exc

    def delete(self, name: str) -> None:
        try:
            self._kr.delete_password(self._service, check_name(name))
        except keyring.errors.PasswordDeleteError:
            return  # already absent
        except keyring.errors.KeyringError as exc:
            raise SecretStoreNeedsYouError(f"secret store refused the delete: {exc}") from exc


def macos_keychain(install: str, *, interactive: bool) -> KeyringSecretStore:
    """The Keychain store. `interactive=False` (the service) turns prompts off for this process."""
    from keyring.backends import macOS  # noqa: PLC0415

    from ecf_server.secretstore.macos_interaction import set_interaction_allowed  # noqa: PLC0415

    set_interaction_allowed(interactive)
    return KeyringSecretStore(macOS.Keyring(), install, backend_name="keychain")


def secret_service(install: str) -> KeyringSecretStore:
    """Secret Service (GNOME Keyring, KWallet, KeePassXC). Unverified until V1.6."""
    from keyring.backends import SecretService  # noqa: PLC0415

    return KeyringSecretStore(SecretService.Keyring(), install, backend_name="secret-service")
