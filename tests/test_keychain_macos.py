"""Real Keychain tests (macOS only; run before merging to main). Items use an `ecf-test-*` install
name and are deleted afterwards; Keychain prompts are turned back on at the end."""

import secrets
import sys
from collections.abc import Iterator

import pytest

pytestmark = [
    pytest.mark.macos,
    pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain"),
]


@pytest.fixture
def install() -> Iterator[str]:
    from ecf_server.secretstore.keyring_store import macos_keychain  # noqa: PLC0415
    from ecf_server.secretstore.macos_interaction import set_interaction_allowed  # noqa: PLC0415

    name = f"ecf-test-{secrets.token_hex(4)}"
    yield name
    store = macos_keychain(name, interactive=False)
    for item in ("slack/bot", "mailbox/billing"):
        store.delete(item)
    set_interaction_allowed(True)


def test_keychain_round_trip_without_prompts(install: str) -> None:
    from ecf_server.secretstore.keyring_store import macos_keychain  # noqa: PLC0415
    from ecf_server.secretstore.macos_interaction import interaction_allowed  # noqa: PLC0415

    store = macos_keychain(install, interactive=False)
    assert not interaction_allowed()
    assert store.get("slack/bot") is None
    store.set("slack/bot", "dummy-" + secrets.token_hex(4))
    store.set("mailbox/billing", "dummy")
    assert (store.get("mailbox/billing") or "") == "dummy"
    assert (store.get("slack/bot") or "").startswith("dummy-")
    store.delete("slack/bot")
    assert store.get("slack/bot") is None


def test_interaction_can_be_restored(install: str) -> None:
    from ecf_server.secretstore.macos_interaction import (  # noqa: PLC0415
        interaction_allowed,
        set_interaction_allowed,
    )

    set_interaction_allowed(False)
    assert not interaction_allowed()
    set_interaction_allowed(True)
    assert interaction_allowed()
