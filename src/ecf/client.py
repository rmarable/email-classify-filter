"""The local API client: HTTP over the service's 0600 Unix socket (SPEC §11.4)."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, cast

import httpx

from ecf.errors import EcfError, InternalError, ServiceUnavailableError, UnauthorizedError
from ecf.paths import Paths

TIMEOUT_S = 10.0  # SPEC §15.4
NOT_RUNNING = "service not running: `ecf service start` or `ecf watch`"


class LocalClient:
    def __init__(self, paths: Paths, *, timeout: float = TIMEOUT_S) -> None:
        self.paths = paths
        self._http = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(paths.socket)),
            base_url="http://ecf",
            timeout=timeout,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: type[BaseException] | BaseException | TracebackType | None) -> None:
        self._http.close()

    def _token(self) -> str:
        try:
            return self.paths.token.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise ServiceUnavailableError(NOT_RUNNING) from exc
        except PermissionError as exc:
            raise UnauthorizedError("can't read the CLI token file") from exc

    def request(self, method: str, path: str, json: Any = None, *, auth: bool = True) -> Any:
        if not self.paths.socket.exists():
            raise ServiceUnavailableError(NOT_RUNNING)
        headers = {"Authorization": f"Bearer {self._token()}"} if auth else {}
        try:
            r = self._http.request(method, path, json=json, headers=headers)
        except httpx.TransportError as exc:
            raise ServiceUnavailableError(NOT_RUNNING) from exc
        if r.is_success:
            return r.json()
        try:
            body: Any = r.json()
        except ValueError as exc:
            raise InternalError(f"unexpected reply ({r.status_code})") from exc
        if isinstance(body, dict):
            raise EcfError.from_problem(cast(dict[str, Any], body))
        raise InternalError(f"unexpected reply ({r.status_code})")

    def get(self, path: str, *, auth: bool = True) -> Any:
        return self.request("GET", path, auth=auth)
