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
