"""An in-memory SecretStore for tests and `ecf-server dev`."""

from __future__ import annotations

from ecf_server.secretstore import SecretStoreNeedsYouError, check_name


class MemorySecretStore:
    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[str, str] = {}
        self.locked = False

    def get(self, name: str) -> str | None:
        self._gate()
        return self._items.get(check_name(name))

    def set(self, name: str, value: str) -> None:
        self._gate()
        self._items[check_name(name)] = value

    def delete(self, name: str) -> None:
        self._gate()
        self._items.pop(check_name(name), None)

    def _gate(self) -> None:
        if self.locked:
            raise SecretStoreNeedsYouError("secret store needs you (locked)")
