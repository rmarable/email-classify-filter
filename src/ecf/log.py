"""Logging (SPEC §17.2): structlog everywhere, rendered through stdlib `logging`, with one
no-content processor so email content never reaches a log.

The processor redacts known content fields, drops raw bytes, and truncates any other string longer
than `MAX_LOG_STRING`, since a long string is most likely message text. The service logs JSON to a
rotating file (V1.0 step 8); the CLI logs to stderr.
"""

from __future__ import annotations

import logging
import logging.handlers
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
        "password",
        "app_password",
        "token",
        "secret",
        "passphrase",
    }
)
LOG_ROTATE_BYTES = 50 * 1024 * 1024  # OD-160
LOG_ROTATE_COUNT = 10


def no_content(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        value = event_dict[key]
        if key.lower() in CONTENT_KEYS:
            event_dict[key] = REDACTED
        elif isinstance(value, bytes | bytearray):
            event_dict[key] = f"[{len(value)} bytes dropped]"
        elif isinstance(value, memoryview):
            event_dict[key] = f"[{value.nbytes} bytes dropped]"
        elif isinstance(value, str) and len(value) > MAX_LOG_STRING and key != "exception":
            event_dict[key] = value[:MAX_LOG_STRING] + f"…[{len(value) - MAX_LOG_STRING} chars cut]"
    return event_dict


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
        exc = structlog.processors.dict_tracebacks
    else:
        handler = logging.StreamHandler(sys.stderr)
        renderer = structlog.dev.ConsoleRenderer(colors=False)
        exc = structlog.processors.format_exc_info
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, exc, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
