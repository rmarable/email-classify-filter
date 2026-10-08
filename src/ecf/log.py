"""Logging (SPEC §17.2): structlog everywhere, rendered through stdlib `logging`, with one
no-content processor so email content never reaches a log.

The processor redacts known content fields, drops raw bytes, and truncates any other string longer
than `MAX_LOG_STRING`, since a long string is most likely message text. Since V1.2 (security review
of the V1.2 plan, 2026-09-29) it also works inside nested dicts and lists, redacts any key naming a
token, secret, password or credential and Slack's payload keys, and removes anything shaped like a
Slack token from every string, including messages from libraries and rendered exceptions. The
Slack and websocket libraries' own loggers are held at WARNING whatever the root level is, since
at DEBUG they write request and event payloads. The service logs JSON to a rotating file (V1.0
step 8); the CLI logs to stderr.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Literal

import structlog

MAX_LOG_STRING = 200
REDACTED = "[redacted]"
CONTENT_KEYS = frozenset(
    {
        "body",
        "text",
        "excerpts",  # corpus manifest and case fields (OD-466, R199)
        "unredacted",
        "display",
        "html",
        "raw",
        "content",
        "subject",
        "from",
        "from_",
        "sender",
        "to",
        "cc",
        "reply_to",
        "recipients",
        "excerpt",
        "snippet",
        "headers",
        "attachment_name",
        "filename",
        "answer",
        "reason",
        "question",
        "prompt",
        "output",
        "completion",
        "messages",
        "password",
        "app_password",
        "token",
        "secret",
        "passphrase",
        # Slack interactivity and Web API shapes: they carry subjects, answers and button values
        "payload",
        "blocks",
        "view",
        "state",
        "value",
        "values",
        "message",
        "attachments",
        "credentials",
        # the backup key text, shown once by `ecf export keys rotate` (V1.5 step 8a)
        "key_text",
        "backup_key",
    }
)
# Any key containing one of these is redacted too ("bot_token", "client_secret", ...).
SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "passphrase", "authorization")
# Slack tokens: xoxb-/xoxp-/xoxa-/xoxe.xoxp- (configuration) and xapp- (app-level).
_SLACK_TOKEN = re.compile(r"\b(?:xox[a-z]?(?:\.xox[a-z])?|xapp)-[A-Za-z0-9-]{6,}")
TOKEN_REDACTED = "[token redacted]"  # noqa: S105 - the placeholder, not a secret
MAX_DEPTH = 6
# Libraries whose DEBUG output includes payloads or request bodies: never below WARNING.
QUIET_LIBRARIES = ("slack_sdk", "slack_bolt", "websocket", "urllib3", "aiohttp", "imapclient",
                   "httpx", "httpcore")  # fmt: skip
LOG_ROTATE_BYTES = 50 * 1024 * 1024  # OD-160
LOG_ROTATE_COUNT = 10


def no_content(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key == "exception":  # rendered later; redact_tokens runs on it then
            continue
        event_dict[key] = _scrub(key, event_dict[key], 0)
    return event_dict


def redact_tokens(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """After exceptions are rendered: tokens out of every string, tracebacks included."""
    for key in list(event_dict):
        event_dict[key] = _tokens_out(event_dict[key], 0)
    return event_dict


def _secret_key(key: str) -> bool:
    k = key.lower()
    return k in CONTENT_KEYS or any(part in k for part in SECRET_KEY_PARTS)


def _scrub(key: str, value: Any, depth: int) -> Any:
    if _secret_key(key):
        return REDACTED
    if isinstance(value, str):
        return _short(_SLACK_TOKEN.sub(TOKEN_REDACTED, value))
    if isinstance(value, bytes | bytearray | memoryview):
        size = value.nbytes if isinstance(value, memoryview) else len(value)
        return f"[{size} bytes dropped]"
    if isinstance(value, dict | list | tuple):
        return _nested(key, value, depth)  # pyright: ignore[reportUnknownArgumentType]
    return value


def _short(value: str) -> str:
    if len(value) > MAX_LOG_STRING:
        return value[:MAX_LOG_STRING] + f"…[{len(value) - MAX_LOG_STRING} chars cut]"
    return value


def _nested(key: str, value: dict[Any, Any] | list[Any] | tuple[Any, ...], depth: int) -> Any:
    if depth >= MAX_DEPTH:
        return "[nested value dropped]"
    if isinstance(value, dict):
        return {k: _scrub(str(k), v, depth + 1) for k, v in value.items()}
    return [_scrub(key, v, depth + 1) for v in value]


def _tokens_out(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return _SLACK_TOKEN.sub(TOKEN_REDACTED, value)
    if depth >= MAX_DEPTH:
        return value
    if isinstance(value, dict):
        d: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {k: _tokens_out(v, depth + 1) for k, v in d.items()}
    if isinstance(value, list | tuple):
        items: list[Any] | tuple[Any, ...] = value  # pyright: ignore[reportUnknownVariableType]
        return [_tokens_out(v, depth + 1) for v in items]
    return value


def configure_logging(
    mode: Literal["cli", "service"] = "cli",
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """CLI: human-readable lines on stderr. Service: JSON lines to a rotating file (0600)."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        no_content,
    ]
    renderer: Any
    handler: logging.Handler
    if mode == "service":
        if log_file is None:
            raise ValueError("service logging needs a log file")
        log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_file.touch(mode=0o600, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_ROTATE_BYTES, backupCount=LOG_ROTATE_COUNT, encoding="utf-8"
        )
        renderer = structlog.processors.JSONRenderer()
        # never show_locals: frame locals can hold tokens, passwords and message text
        exc = structlog.processors.ExceptionRenderer(
            structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
        )
    else:
        handler = logging.StreamHandler(sys.stderr)
        renderer = structlog.dev.ConsoleRenderer(colors=False)
        exc = structlog.processors.format_exc_info
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                exc,
                redact_tokens,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
