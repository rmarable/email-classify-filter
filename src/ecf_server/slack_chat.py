"""The Slack adapter for the ChatSurface port (SPEC §10.1; V1.2 step 3). It renders cards with
`slack_render` and calls the Web API through anything with `call(method, **params)` (the
`_slack.Web` facade, or a fake in tests).

Behavior from the real-service test (2026-09-29, §21.1): private channels are created with
`groups:write`; `chat:write.customize` names and icons show on top posts and in threads (never on
DMs); pins need `pins:write`; ephemeral replies are visible only to the user named.
"""

from __future__ import annotations

from typing import Any, Protocol

from ecf_server._slack import SlackError
from ecf_server.chat import (
    Capabilities,
    Card,
    Identity,
    RouteGoneError,
    RouteNameTakenError,
    RouteNotAllowedError,
    RouteRef,
    ThreadRef,
)
from ecf_server.slack_render import blocks, clean, fallback

GONE = frozenset({"channel_not_found", "is_archived", "not_in_channel"})
HARMLESS = {
    "conversations.invite": {"already_in_channel"},
    "conversations.archive": {"already_archived"},
    "pins.add": {"already_pinned"},
}


class WebLike(Protocol):
    def call(self, method: str, **params: Any) -> dict[str, Any]: ...


class SlackChat:
    capabilities = Capabilities(
        private_routes=True,
        create_route=True,
        pin=True,
        per_route_identity=True,
        ephemeral=True,
        sync_interaction_response=False,  # Socket Mode: cards are edited with chat.update
    )

    def __init__(self, web: WebLike) -> None:
        self._web = web

    def post(
        self,
        route: RouteRef,
        card: Card,
        *,
        thread: ThreadRef | None = None,
        identity: Identity | None = None,
    ) -> ThreadRef:
        params: dict[str, Any] = {
            "channel": route.channel,
            "text": fallback(card.title),
            "blocks": blocks(card),
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread is not None:
            params["thread_ts"] = thread.ts
        if identity is not None and not route.channel.startswith("D"):  # never on DMs (§10.1)
            params["username"] = clean(identity.name, 80)
            params["icon_emoji"] = identity.icon_emoji
        r = self._web.call("chat.postMessage", **params)
        return ThreadRef(RouteRef(str(r.get("channel", route.channel))), str(r["ts"]))

    def update(self, ref: ThreadRef, card: Card) -> None:
        self._web.call(
            "chat.update",
            channel=ref.route.channel,
            ts=ref.ts,
            text=fallback(card.title),
            blocks=blocks(card),
        )

    def pin(self, ref: ThreadRef) -> None:
        self._harmless("pins.add", channel=ref.route.channel, timestamp=ref.ts)

    def ephemeral(self, route: RouteRef, user: str, text: str) -> None:
        self._web.call("chat.postEphemeral", channel=route.channel, user=user,
                       text=fallback(text))  # fmt: skip

    def create_route(self, name: str) -> RouteRef:
        """A private channel; if the name is taken by one ecf can see, that channel is reused."""
        try:
            r = self._web.call("conversations.create", name=name, is_private=True)
            return RouteRef(str(r["channel"]["id"]))
        except SlackError as exc:
            if exc.code == "restricted_action":
                raise RouteNotAllowedError(name) from None
            if exc.code != "name_taken":
                raise
        found = self._find_private(name)
        if found is None:  # archived, or a channel ecf isn't in
            raise RouteNameTakenError(name)
        return found

    def find_route(self, name: str) -> RouteRef | None:
        return self._find_private(name)

    def invite(self, route: RouteRef, user: str) -> None:
        try:
            self._harmless("conversations.invite", channel=route.channel, users=user)
        except SlackError as exc:
            if exc.code in GONE:
                raise RouteGoneError(route.channel) from None
            raise

    def archive(self, route: RouteRef) -> None:
        self._harmless("conversations.archive", channel=route.channel)

    def members(self, route: RouteRef) -> list[str]:
        r = self._web.call("conversations.members", channel=route.channel, limit=200)
        return [str(m) for m in r.get("members", [])]

    def _find_private(self, name: str) -> RouteRef | None:
        cursor = ""
        for _ in range(20):  # at most 20 pages of 200
            r = self._web.call("conversations.list", types="private_channel",
                               exclude_archived=True, limit=200, cursor=cursor)  # fmt: skip
            for ch in r.get("channels", []):
                if ch.get("name") == name:
                    return RouteRef(str(ch["id"]))
            meta: dict[str, Any] = r.get("response_metadata") or {}
            cursor = str(meta.get("next_cursor") or "")
            if not cursor:
                return None
        return None

    def _harmless(self, method: str, **params: Any) -> None:
        try:
            self._web.call(method, **params)
        except SlackError as exc:
            if exc.code not in HARMLESS.get(method, set()):
                raise
