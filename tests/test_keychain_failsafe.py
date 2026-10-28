"""If macOS stops letting ecf turn Keychain prompts off (the call is deprecated), the service starts
without secrets ("secret store needs you") instead of crashing or reading with prompts on (OD-569,
ADR 0001). The Security module is faked, so these run on every OS."""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog

from ecf.cli import secrets_line
from ecf.paths import Paths, paths_for
from ecf_server import db, destroy, regrant, service
from ecf_server.secretstore import (
    SecretStoreNeedsYouError,
    keyring_store,
    macos_interaction,
    select,
)
from ecf_server.secretstore.macos_interaction import (
    PROMPTS_GONE,
    KeychainPromptsError,
    set_interaction_allowed,
)
from ecf_server.secretstore.select import Probe
from ecf_server.service import Service


def _fake_security(monkeypatch: pytest.MonkeyPatch, security: object) -> None:
    monkeypatch.setattr(macos_interaction, "_security", lambda: security)


def _security(set_status: int, setting: tuple[int, bool] | None) -> SimpleNamespace:
    """A Security module whose set call answers `set_status` and whose get call answers `setting`
    (no get call when None)."""

    def set_call(_: bool) -> int:
        return set_status

    def get_call(_: None) -> tuple[int, bool]:
        assert setting is not None
        return setting

    calls: dict[str, object] = {"SecKeychainSetUserInteractionAllowed": set_call}
    if setting is not None:
        calls["SecKeychainGetUserInteractionAllowed"] = get_call
    return SimpleNamespace(**calls)


def _no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_store(*_: object, **__: object) -> None:
        raise AssertionError("a Keychain store was built after prompts couldn't be turned off")

    monkeypatch.setattr(keyring_store, "KeyringSecretStore", no_store)


def test_missing_call_refuses_to_turn_prompts_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_security(monkeypatch, SimpleNamespace())
    with pytest.raises(KeychainPromptsError) as err:
        set_interaction_allowed(False)
    assert err.value.detail == PROMPTS_GONE
    assert isinstance(err.value, SecretStoreNeedsYouError)  # every handler treats it as "needs you"


@pytest.mark.parametrize("error", [ImportError("No module named 'Security'"), OSError("dlopen")])
def test_missing_or_broken_pyobjc_refuses(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def broken() -> object:
        raise error

    monkeypatch.setattr(macos_interaction, "_import_security", broken)
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(KeychainPromptsError):
        set_interaction_allowed(False)


def test_missing_call_leaves_prompts_on_as_they_are(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_security(monkeypatch, SimpleNamespace())
    set_interaction_allowed(True)  # macOS's default; the re-grant needs nothing more


def test_failed_call_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_security(monkeypatch, _security(-1, (0, False)))
    with pytest.raises(KeychainPromptsError, match="-1"):
        set_interaction_allowed(False)


def test_a_call_that_raises_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def changed_bridge(*_: object) -> int:
        raise TypeError("wrong number of arguments")

    _fake_security(
        monkeypatch, SimpleNamespace(SecKeychainSetUserInteractionAllowed=changed_bridge)
    )
    with pytest.raises(KeychainPromptsError, match="TypeError"):
        set_interaction_allowed(False)
    security = _security(0, (0, False))
    security.SecKeychainGetUserInteractionAllowed = changed_bridge
    _fake_security(monkeypatch, security)
    with pytest.raises(KeychainPromptsError, match="TypeError"):
        set_interaction_allowed(False)


def test_a_stub_that_changes_nothing_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_security(monkeypatch, _security(0, (0, True)))  # "done", but prompts still on
    with pytest.raises(KeychainPromptsError) as err:
        set_interaction_allowed(False)
    assert err.value.detail == PROMPTS_GONE


@pytest.mark.parametrize("setting", [None, (-1, False)])
def test_a_setting_that_cant_be_read_back_refuses(
    monkeypatch: pytest.MonkeyPatch, setting: tuple[int, bool] | None
) -> None:
    _fake_security(monkeypatch, _security(0, setting))
    with pytest.raises(KeychainPromptsError):
        set_interaction_allowed(False)


def test_a_working_call_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_security(monkeypatch, _security(0, (0, False)))
    set_interaction_allowed(False)


def test_store_never_touches_the_keychain_without_prompt_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_security(monkeypatch, _security(0, (0, True)))
    _no_store(monkeypatch)
    with pytest.raises(KeychainPromptsError):
        keyring_store.macos_keychain("ecf-test-x", interactive=False)


def _darwin_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Service:
    _fake_security(monkeypatch, SimpleNamespace())
    _no_store(monkeypatch)
    darwin = Probe("darwin", False, lambda: False, lambda: None)
    monkeypatch.setattr(service, "host_probe", lambda: darwin)
    return Service(Paths("t", tmp_path))


def test_service_start_survives_missing_prompt_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    svc = _darwin_service(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    with structlog.testing.capture_logs() as logged:
        svc._keychain_prompts_off()  # pyright: ignore[reportPrivateUsage]  # no exception
    assert [e.get("detail") for e in logged if e["event"] == "secretstore.prompts_not_off"] == [
        PROMPTS_GONE
    ]


def test_service_runs_without_secrets_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    svc = _darwin_service(monkeypatch, tmp_path)
    tmp_path.joinpath("t").mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = db.connect(svc.paths.db)
    try:
        db.migrate(conn)
        svc._prepare_state(conn)  # pyright: ignore[reportPrivateUsage]
    finally:
        conn.close()
    assert svc.secrets is None
    assert svc.state.secret_store["backend"] == "keychain"
    assert svc.state.secret_store["detail"] == PROMPTS_GONE


def test_trip_report_tells_the_desktop_and_logs_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    svc = _darwin_service(monkeypatch, tmp_path)
    told: list[str] = []

    def notify(_title: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(service, "host_notifier", lambda: SimpleNamespace(notify=notify))
    with structlog.testing.capture_logs() as logged:
        svc._report_trip(3)  # pyright: ignore[reportPrivateUsage]
    assert len(told) == 1
    failed = [e for e in logged if e["event"] == "service.trip_report_failed"]
    assert [e.get("detail") for e in failed] == [PROMPTS_GONE]


def _foreground_keychain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A foreground command on a Mac whose prompt call fails (here: when turning prompts on)."""
    monkeypatch.setenv("ECF_HOME", str(tmp_path))

    def keychain(_probe: Probe) -> str:
        return "keychain"

    monkeypatch.setattr(select, "choose_backend", keychain)
    _fake_security(monkeypatch, _security(-1, (0, True)))
    _no_store(monkeypatch)
    paths = paths_for("t", for_service=True)
    paths.data_dir.mkdir(parents=True)
    conn = db.connect(paths.db)
    db.migrate(conn)
    conn.close()


def test_regrant_says_why_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _foreground_keychain(monkeypatch, tmp_path)
    assert regrant.main("t") == 1
    assert "turning Keychain prompts on failed: -1; nothing recorded" in capsys.readouterr().err


def test_foreground_destroy_says_why_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _foreground_keychain(monkeypatch, tmp_path)
    assert destroy.main("t", "t", ask_token=False) == 1
    err = capsys.readouterr().err
    assert "turning Keychain prompts on failed: -1; nothing was destroyed" in err


def test_status_names_why_the_store_is_unusable() -> None:
    assert secrets_line({"backend": "keychain", "detail": PROMPTS_GONE}) == (
        f"keychain, unusable: {PROMPTS_GONE}"
    )
    assert secrets_line({"backend": None, "detail": "no usable secret store"}) == (
        "none usable (no usable secret store)"
    )
    assert secrets_line({"backend": "keychain", "interpreter_changed": True}) == (
        "keychain (Python changed: run `ecf service regrant`)"
    )
    assert secrets_line({"backend": "keychain"}) == "keychain"
    assert secrets_line({}) == "none usable"
