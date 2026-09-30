"""The ChatSurface port (SPEC §3.3, §10.1). V1.2 step 1: the neutral card, route and thread
references, capability flags, and the recording fake used by `ecf-server dev` and tests; the Slack
Socket Mode adapter arrives in V1.2 step 3.

A card holds plain text only. Adapters render every field as plain text (never markup), so text
taken from an email can't mention anyone, format or link (tested against Slack 2026-09-29, §21.1).
A button carries an opaque reference, never the action itself: the service loads the action from
the grant the reference names (§9.5).
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from ecf.ids import new_random_id

Style = Literal["primary", "danger"]


@dataclass(frozen=True)
class RouteRef:
    """Where posts go: a Slack channel ID (private per address, or the summary channel)."""

    channel: str


@dataclass(frozen=True)
class ThreadRef:
    """A posted message; a reply to it opens or continues its thread."""

    route: RouteRef
    ts: str


@dataclass(frozen=True)
class Button:
    action: str  # e.g. "approve", "show_excerpt"; the handler dispatches on it
    label: str
    ref: str  # opaque: a grant or item reference the service resolves itself
    style: Style | None = None


@dataclass(frozen=True)
class Card:
    title: str
    fields: tuple[tuple[str, str], ...] = ()
    text: str = ""
    buttons: tuple[Button, ...] = ()
    note: str = ""  # a line under the buttons, e.g. "This acts on the email only."
    mention: str = ""  # a Slack member ID to mention (escalations); never text from an email


@dataclass(frozen=True)
class Identity:
    """A per-address display name and icon (Slack `chat:write.customize`; never on DMs)."""

    name: str
    icon_emoji: str


class RouteNameTakenError(Exception):
    """The name belongs to a route ecf can't use (archived, or one it isn't in)."""


class RouteGoneError(Exception):
    """The route was deleted or archived outside ecf, or ecf was removed from it."""


class RouteNotAllowedError(Exception):
    """The workspace doesn't let ecf create routes; a person must create it and add ecf."""


@dataclass(frozen=True)
class Capabilities:
    private_routes: bool
    create_route: bool
    pin: bool
    per_route_identity: bool
    ephemeral: bool
    sync_interaction_response: bool


class ChatSurface(Protocol):
    capabilities: Capabilities

    def post(
        self,
        route: RouteRef,
        card: Card,
        *,
        thread: ThreadRef | None = None,
        identity: Identity | None = None,
    ) -> ThreadRef: ...

    def update(self, ref: ThreadRef, card: Card) -> None: ...

    def pin(self, ref: ThreadRef) -> None: ...

    def ephemeral(self, route: RouteRef, user: str, text: str) -> None: ...

    def create_route(self, name: str) -> RouteRef:
        """A private route; if the name is taken by one ecf is in, that one. Raises
        RouteNameTakenError or RouteNotAllowedError."""
        ...

    def find_route(self, name: str) -> RouteRef | None:
        """An unarchived private route with this name that ecf is already in, or None."""
        ...

    def invite(self, route: RouteRef, user: str) -> None:
        """Raises RouteGoneError when the route no longer exists for ecf."""
        ...

    def archive(self, route: RouteRef) -> None: ...


@dataclass
class FakeChat:
    """Records everything instead of sending it (tests and `ecf-server dev`)."""

    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(
            private_routes=True,
            create_route=True,
            pin=True,
            per_route_identity=True,
            ephemeral=True,
            sync_interaction_response=True,
        )
    )
    posts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    routes: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    creation_refused: bool = False  # like a workspace that restricts channel creation
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def post(
        self,
        route: RouteRef,
        card: Card,
        *,
        thread: ThreadRef | None = None,
        identity: Identity | None = None,
    ) -> ThreadRef:
        ref = ThreadRef(route, new_random_id())
        with self._lock:
            self.posts.append(
                {
                    "ts": ref.ts,
                    "route": route.channel,
                    "thread": thread.ts if thread else None,
                    "identity": asdict(identity) if identity else None,
                    "card": asdict(card),
                    "pinned": False,
                }
            )
        return ref

    def update(self, ref: ThreadRef, card: Card) -> None:
        with self._lock:
            post = self._find(ref)
            post["card"] = asdict(card)

    def pin(self, ref: ThreadRef) -> None:
        with self._lock:
            self._find(ref)["pinned"] = True

    def ephemeral(self, route: RouteRef, user: str, text: str) -> None:
        with self._lock:
            self.posts.append({"route": route.channel, "ephemeral_to": user, "text": text})

    def create_route(self, name: str) -> RouteRef:
        route = RouteRef("C" + new_random_id()[:10].upper())
        with self._lock:
            for channel, r in self.routes.items():  # Slack's name_taken, as SlackChat handles it
                if r["name"] == name:
                    if r["archived"]:
                        raise RouteNameTakenError(name)
                    return RouteRef(channel)
            if self.creation_refused:
                raise RouteNotAllowedError(name)
            self.routes[route.channel] = {"name": name, "members": [], "archived": False}
        return route

    def find_route(self, name: str) -> RouteRef | None:
        with self._lock:
            for channel, r in self.routes.items():
                if r["name"] == name and not r["archived"]:
                    return RouteRef(channel)
        return None

    def invite(self, route: RouteRef, user: str) -> None:
        with self._lock:
            r = self.routes.get(route.channel)
            if r is None or r["archived"]:
                raise RouteGoneError(route.channel)
            members = r["members"]
            if user not in members:  # like Slack's already_in_channel, which ecf ignores
                members.append(user)

    def archive(self, route: RouteRef) -> None:
        with self._lock:
            self.routes[route.channel]["archived"] = True

    def clear(self) -> None:
        with self._lock:
            self.posts.clear()
            self.routes.clear()

    def _find(self, ref: ThreadRef) -> dict[str, Any]:
        for p in self.posts:
            if p.get("ts") == ref.ts and p.get("route") == ref.route.channel:
                return p
        raise KeyError(f"no post {ref.ts} in {ref.route.channel}")
