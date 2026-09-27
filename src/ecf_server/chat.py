"""The ChatSurface port (SPEC §3.3). V1.0 has only the recording fake used by `ecf-server dev`
and tests; the Slack Socket Mode adapter arrives in V1.2 and extends this interface."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol

from ecf.ids import new_random_id


class ChatSurface(Protocol):
    def post(self, route: str, text: str) -> str: ...


@dataclass
class FakeChat:
    """Records posts instead of sending them."""

    posts: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def post(self, route: str, text: str) -> str:
        ref = new_random_id()
        with self._lock:
            self.posts.append({"ref": ref, "route": route, "text": text})
        return ref

    def clear(self) -> None:
        with self._lock:
            self.posts.clear()
