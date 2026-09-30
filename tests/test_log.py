import json
import logging
import stat
from pathlib import Path

import pytest

from ecf import log


def test_no_content_redacts_truncates_and_drops() -> None:
    event = {
        "event": "classified",
        "subject": "Invoice 42",
        "Body": "Dear customer",
        "raw_bytes": b"\x00" * 10,
        "note": "x" * 500,
        "count": 3,
    }
    out = log.no_content(None, "info", event)
    assert out["subject"] == log.REDACTED
    assert out["Body"] == log.REDACTED
    assert out["raw_bytes"] == "[10 bytes dropped]"
    assert out["note"].startswith("x" * log.MAX_LOG_STRING)
    assert out["note"].endswith("[300 chars cut]")
    assert out["count"] == 3
    assert out["event"] == "classified"


def test_service_logging_writes_redacted_json_0600(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "ecf.log"
    log.configure_logging("service", log_file=path)
    log.get_logger("t").info("sent", subject="secret subject", address_id="billing")
    logging.getLogger().handlers[0].flush()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "sent"
    assert record["subject"] == log.REDACTED
    assert record["address_id"] == "billing"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_service_logging_needs_a_file() -> None:
    with pytest.raises(ValueError, match="log file"):
        log.configure_logging("service")


def test_cli_logging_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log.configure_logging("cli")
    log.get_logger("t").warning("hello", body="Dear customer")
    err = capsys.readouterr().err
    assert "hello" in err
    assert "Dear customer" not in err
    assert log.REDACTED in err


def test_tracebacks_carry_no_locals(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "ecf.log"
    log.configure_logging("service", log_file=path)

    def fails(password: str) -> None:
        secret_local = f"token-{password}"
        raise RuntimeError("boom " + str(len(secret_local)))

    try:
        fails("hunter2-very-secret")
    except RuntimeError:
        log.get_logger("t").exception("failed")
    logging.getLogger().handlers[0].flush()
    text = path.read_text(encoding="utf-8")
    assert "RuntimeError" in text and "boom" in text
    assert "hunter2-very-secret" not in text and '"locals"' not in text


# ---- V1.2: Slack material (security review of the V1.2 plan, 2026-09-29) -------------------

# built at runtime, so no token-shaped string sits in the repo for secret scanners to flag
BOT = "xox" + "b-" + "1234567890-0987654321-" + "A" * 24
APP = "xa" + "pp-1-A0TEST-1234567890-" + "f" * 32
CONFIG = "xox" + "e.xox" + "p-1-" + "Mi" * 30


def _service(tmp_path: Path, level: int = logging.INFO) -> Path:
    path = tmp_path / "logs" / "ecf.log"
    log.configure_logging("service", log_file=path, level=level)
    return path


def _flush_text(path: Path) -> str:
    logging.getLogger().handlers[0].flush()
    return path.read_text(encoding="utf-8")


def test_nested_slack_material_is_redacted(tmp_path: Path) -> None:
    """A real block_actions payload and a manifest response, logged as ecf fields."""
    path = _service(tmp_path)
    payload = {
        "type": "block_actions",
        "token": "legacy-verification-token",
        "user": {"id": "U123"},
        "message": {"text": "Invoice 42: new bank details"},
        "actions": [{"action_id": "approve", "value": "grant-ref-1"}],
        "view": {"state": {"values": {"answer": {"text": {"value": "pay acct 1234"}}}}},
    }
    manifest = {"ok": True, "app_id": "A0TEST", "credentials": {"client_secret": "s3cr3t-value"}}
    log.get_logger("t").info("slack.event", payload=payload, detail={"raw": payload})
    log.get_logger("t").info("slack.manifest", response=manifest, bot_token=BOT, auth={"x": APP})
    text = _flush_text(path)
    for leaked in ("new bank details", "grant-ref-1", "pay acct 1234", "legacy-verification",
                   "s3cr3t-value", BOT, APP):  # fmt: skip
        assert leaked not in text, leaked
    assert "A0TEST" in text  # an app ID isn't secret


def test_tokens_are_removed_from_free_text_library_messages_and_exceptions(
    tmp_path: Path,
) -> None:
    path = _service(tmp_path)
    log.get_logger("t").info(f"calling slack with {BOT}")
    logging.getLogger("some.library").warning("configured with %s and %s", APP, CONFIG)
    try:
        raise RuntimeError(f"auth failed for {BOT}")
    except RuntimeError:
        log.get_logger("t").exception("failed")
    text = _flush_text(path)
    assert BOT not in text and APP not in text and CONFIG not in text
    assert text.count(log.TOKEN_REDACTED) >= 4 and "auth failed for" in text


def test_slack_library_debug_output_never_reaches_the_log(tmp_path: Path) -> None:
    """At DEBUG slack_sdk writes request and event payloads; its logger stays at WARNING."""
    path = _service(tmp_path, level=logging.DEBUG)
    logging.getLogger("slack_sdk.web.base_client").debug("Sending a request - body: %s", BOT)
    logging.getLogger("slack_sdk.socket_mode.builtin.client").debug("on_message: invoice text")
    logging.getLogger("websocket").debug("frame: invoice text")
    logging.getLogger("ecf.other").debug("ecf's own debug line")
    text = _flush_text(path)
    assert "invoice text" not in text and "Sending a request" not in text
    assert "ecf's own debug line" in text


def test_deep_nesting_is_cut(tmp_path: Path) -> None:
    deep: dict[str, object] = {"x": 1}
    for _ in range(20):
        deep = {"inner": deep}
    out = log.no_content(None, "info", {"event": "e", "data": deep})
    assert "[nested value dropped]" in json.dumps(out)
