"""The single error hierarchy (SPEC §15.3).

One table maps each stable `code` to its HTTP status, CLI exit code, and Slack text. Exit codes
follow one rule: 1 user error, 2 refused, 3 unavailable, 4 conflict, 5 internal. Every error is
`isError` in MCP. Errors travel over the socket as RFC 9457 problem+json.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, ClassVar

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "urn:ecf:error:"


class ExitCode(IntEnum):
    OK = 0
    USER_ERROR = 1
    REFUSED = 2
    UNAVAILABLE = 3
    CONFLICT = 4
    INTERNAL = 5


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN_PROFILE = "forbidden_profile"
    STEPUP_REQUIRED = "stepup_required"
    STEPUP_FAILED = "stepup_failed"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    GRANT_INVALID = "grant_invalid"
    POLICY_DENIED = "policy_denied"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    MAIL_UNAVAILABLE = "mail_unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorSpec:
    code: ErrorCode
    http_status: int
    exit_code: ExitCode
    title: str
    slack_text: str


ERROR_TABLE: dict[ErrorCode, ErrorSpec] = {
    s.code: s
    for s in (
        ErrorSpec(
            ErrorCode.INVALID_INPUT,
            400,
            ExitCode.USER_ERROR,
            "Request failed validation",
            "That input isn't valid.",
        ),
        ErrorSpec(
            ErrorCode.UNAUTHORIZED, 401, ExitCode.REFUSED, "Missing or bad token", "Not authorized."
        ),
        ErrorSpec(
            ErrorCode.FORBIDDEN_PROFILE,
            403,
            ExitCode.REFUSED,
            "Caller or profile not allowed",
            "That isn't allowed from here.",
        ),
        ErrorSpec(
            ErrorCode.STEPUP_REQUIRED,
            403,
            ExitCode.REFUSED,
            "Step-up required",
            "Needs confirmation at your computer.",
        ),
        ErrorSpec(
            ErrorCode.STEPUP_FAILED,
            403,
            ExitCode.REFUSED,
            "Step-up failed or unavailable",
            "Confirmation at your computer failed.",
        ),
        ErrorSpec(ErrorCode.NOT_FOUND, 404, ExitCode.USER_ERROR, "Unknown id", "Not found."),
        ErrorSpec(
            ErrorCode.CONFLICT,
            409,
            ExitCode.CONFLICT,
            "State changed or stale lease",
            "Already handled or changed; refresh.",
        ),
        ErrorSpec(
            ErrorCode.GRANT_INVALID,
            409,
            ExitCode.CONFLICT,
            "Grant expired, consumed or mismatched",
            "This approval is no longer valid.",
        ),
        ErrorSpec(
            ErrorCode.POLICY_DENIED,
            422,
            ExitCode.REFUSED,
            "Refused by stage, sensitivity, outbound or ceiling",
            "Not allowed by this address's settings.",
        ),
        ErrorSpec(
            ErrorCode.RATE_LIMITED,
            429,
            ExitCode.UNAVAILABLE,
            "Circuit breaker or rate limit",
            "Limit reached; try later.",
        ),
        ErrorSpec(
            ErrorCode.SERVICE_UNAVAILABLE,
            503,
            ExitCode.UNAVAILABLE,
            "Service dependency unavailable",
            "ecf can't do that right now.",
        ),
        ErrorSpec(
            ErrorCode.MAIL_UNAVAILABLE,
            503,
            ExitCode.UNAVAILABLE,
            "Mail provider unavailable",
            "The mail provider isn't reachable.",
        ),
        ErrorSpec(
            ErrorCode.INTERNAL,
            500,
            ExitCode.INTERNAL,
            "Internal error",
            "Something went wrong in ecf.",
        ),
    )
}


class EcfError(Exception):
    """Base class. Subclasses fix `code`; `detail` is a short message, never email content."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.spec.title)
        self.detail = detail or self.spec.title
        self.extra = extra

    @property
    def spec(self) -> ErrorSpec:
        return ERROR_TABLE[self.code]

    @property
    def exit_code(self) -> ExitCode:
        return self.spec.exit_code

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": PROBLEM_TYPE_BASE + self.code.value,
            "title": self.spec.title,
            "status": self.spec.http_status,
            "detail": self.detail,
            "code": self.code.value,
        }
        if instance:
            body["instance"] = instance
        body.update(self.extra)
        return body

    def to_mcp(self) -> dict[str, Any]:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"{self.code}: {self.detail}"}],
        }

    @staticmethod
    def from_problem(body: dict[str, Any]) -> EcfError:
        code = body.get("code")
        cls = _BY_CODE.get(ErrorCode(code)) if code in ErrorCode._value2member_map_ else None
        extra = {
            k: v
            for k, v in body.items()
            if k not in {"type", "title", "status", "detail", "code", "instance"}
        }
        return (cls or InternalError)(str(body.get("detail", "")), **extra)


class InvalidInputError(EcfError):
    code = ErrorCode.INVALID_INPUT


class UnauthorizedError(EcfError):
    code = ErrorCode.UNAUTHORIZED


class ForbiddenProfileError(EcfError):
    code = ErrorCode.FORBIDDEN_PROFILE


class StepupRequiredError(EcfError):
    code = ErrorCode.STEPUP_REQUIRED


class StepupFailedError(EcfError):
    code = ErrorCode.STEPUP_FAILED


class NotFoundError(EcfError):
    code = ErrorCode.NOT_FOUND


class ConflictError(EcfError):
    code = ErrorCode.CONFLICT


class GrantInvalidError(EcfError):
    code = ErrorCode.GRANT_INVALID


class PolicyDeniedError(EcfError):
    code = ErrorCode.POLICY_DENIED


class RateLimitedError(EcfError):
    code = ErrorCode.RATE_LIMITED


class ServiceUnavailableError(EcfError):
    code = ErrorCode.SERVICE_UNAVAILABLE


class MailUnavailableError(EcfError):
    code = ErrorCode.MAIL_UNAVAILABLE


class InternalError(EcfError):
    code = ErrorCode.INTERNAL


_BY_CODE: dict[ErrorCode, type[EcfError]] = {c.code: c for c in EcfError.__subclasses__()}


def error_table_markdown() -> str:
    """The generated table (SPEC §15.3 is written from this)."""
    rows = ["| Code | HTTP | Exit | Slack text |", "|---|---|---|---|"]
    rows += [
        f"| `{s.code}` | {s.http_status} | {int(s.exit_code)} | {s.slack_text} |"
        for s in ERROR_TABLE.values()
    ]
    return "\n".join(rows)
