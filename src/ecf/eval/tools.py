"""How `ecf eval generate` and `grade` call another model (eval generate plan, Adapters; P3, P4,
P20, P21, P23).

An adapter asks one question and returns a `Reply`: an answer (a JSON object, checked against
the call's schema here, since no provider's schema mode is trusted alone), a refusal, a failure
or a usage limit. A command-line tool runs under an allow-listed environment, never with an API
key variable set, from a folder of its own, with no shell, its process group killed on timeout,
and its output size-capped and parsed strictly. Before a CLI is used for a run it must pass the
tool-less canary: a decoy file sits in its work folder and the prompt asks for its contents; any
answer that holds them, or any tool event, fails it.

Nothing here logs or returns tool output: only statuses, kinds, counts, sizes and times (P27).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from ecf.claude_wrapper import ENV_ALLOW
from ecf.errors import InvalidInputError

Status = Literal["ok", "refused", "failed", "limit"]
# Set in the environment, these would send a run to another account or endpoint (P4)
FORBIDDEN_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                 "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "MISTRAL_API_KEY",
                 "MISTRAL_BASE_URL", "MISTRAL_SERVER_URL")  # fmt: skip
MAX_OUTPUT = 4 * 1024 * 1024
TIMEOUT_S = 600.0
STOP_AFTER = 3  # consecutive failed calls of any kind stop the run
DECOY = "decoy.txt"
# Codex prints this (curly apostrophe) and exits 1 at a plan's usage limit (Facts)
USAGE_LIMIT_TEXT = "You\u2019ve hit your usage limit"


@dataclass(frozen=True)
class Reply:
    status: Status
    answer: dict[str, Any] | None = None
    detail: str = ""  # a kind, never tool output
    usage: dict[str, int] = field(default_factory=dict[str, int])
    seconds: float = 0.0
    items: tuple[str, ...] = ()  # the tool's event kinds seen (a CLI's JSON stream)

    def record(self) -> dict[str, Any]:
        """What the work log keeps: no answer text (P27)."""
        return {"status": self.status, "detail": self.detail, "usage": self.usage,
                "seconds": round(self.seconds, 2), "items": list(self.items)}  # fmt: skip


class Adapter(Protocol):
    provider: str
    model: str

    def describe(self) -> dict[str, Any]:
        """Its provider, model, tool version and argv digest, for the manifest."""
        ...

    def ask(self, prompt: str, schema: dict[str, Any], workdir: Path) -> Reply: ...


def refuse_key_env(environ: Mapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    if found := [k for k in FORBIDDEN_ENV if env.get(k)]:
        raise InvalidInputError(f"{found[0]} is set in your environment: unset it first. ecf's"
                                " generator and grader use only their own sign-ins, never a"
                                " key in the environment")  # fmt: skip


def allowed_env(extra: Mapping[str, str], environ: Mapping[str, str] | None = None
                ) -> dict[str, str]:  # fmt: skip
    env = os.environ if environ is None else environ
    out = {k: v for k, v in env.items() if k in ENV_ALLOW or k.startswith("LC_")}
    return out | dict(extra)


# A strict check for the JSON Schema subset ecf's prompts use: object (properties, required,
# additionalProperties false), array (items, minItems, maxItems), string (enum, maxLength),
# boolean, integer, null and type lists.


def check(value: Any, schema: Mapping[str, Any],  # noqa: PLR0911, PLR0912 - one return a rule
          where: str = "$") -> str | None:  # fmt: skip
    """The first way `value` breaks `schema`, or None."""
    kinds = schema.get("type")
    if kinds is not None:
        allowed = [kinds] if isinstance(kinds, str) else list(cast(Sequence[str], kinds))
        if not any(_is(value, k) for k in allowed):
            return f"{where}: not {'/'.join(allowed)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{where}: not one of the allowed values"
    if isinstance(value, str) and len(value) > int(schema.get("maxLength", 1 << 30)):
        return f"{where}: too long"
    if isinstance(value, list):
        items = cast(list[Any], value)
        if len(items) < int(schema.get("minItems", 0)):
            return f"{where}: too few items"
        if len(items) > int(schema.get("maxItems", 1 << 30)):
            return f"{where}: too many items"
        if (sub := schema.get("items")) is not None:
            for i, v in enumerate(items):
                if err := check(v, sub, f"{where}[{i}]"):
                    return err
    if isinstance(value, dict):
        obj = cast(dict[str, Any], value)
        props = cast(Mapping[str, Mapping[str, Any]], schema.get("properties") or {})
        for k in schema.get("required") or ():
            if k not in obj:
                return f"{where}: {k} missing"
        if schema.get("additionalProperties") is False and (extra := set(obj) - set(props)):
            return f"{where}: unexpected {sorted(extra)[0]}"
        for k, v in obj.items():
            if k in props and (err := check(v, props[k], f"{where}.{k}")):
                return err
    return None


def _is(value: Any, kind: str) -> bool:  # noqa: PLR0911 - one return a type
    match kind:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "boolean":
            return isinstance(value, bool)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "null":
            return value is None
        case _:
            return False


def parse_object(text: str | bytes) -> dict[str, Any] | None:
    """One JSON object, strictly; None for anything else (or over the size cap)."""
    if len(text) > MAX_OUTPUT:
        return None
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    return cast(dict[str, Any], doc) if isinstance(doc, dict) else None


def checked(answer: dict[str, Any] | None, schema: Mapping[str, Any]) -> Reply | None:
    """None when `answer` fits; else the failed Reply (a schema misfit)."""
    if answer is None:
        return Reply("failed", detail="not one JSON object")
    if check(answer, schema) is not None:
        return Reply("failed", detail="schema misfit")
    return None


@dataclass(frozen=True)
class Ran:
    code: int | None  # None: timed out (the process group was killed)
    stdout: bytes
    stderr: bytes
    seconds: float


def run_cli(argv: Sequence[str], stdin: str, env: Mapping[str, str], cwd: Path,
            timeout: float = TIMEOUT_S) -> Ran:  # fmt: skip
    """The command with no shell, in its own process group, killed whole on timeout, and on
    Ctrl-C or any other interruption: its own session never sees the terminal's signal, and it
    mustn't write its answer file after the caller has cleaned up."""
    start = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603 - fixed argv from the tool table, no shell
                                stderr=subprocess.PIPE, cwd=cwd, env=dict(env),
                                start_new_session=True)  # fmt: skip
        out, err = proc.communicate(stdin.encode("utf-8"), timeout=timeout)
    except BaseException as e:
        if proc is not None:
            _kill(proc)
        if isinstance(e, subprocess.TimeoutExpired):
            return Ran(None, b"", b"", time.monotonic() - start)
        raise
    return Ran(proc.returncode, out, err, time.monotonic() - start)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """The whole process group, then wait for it."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()


@dataclass(frozen=True)
class CliTool:
    """One command-line provider: how to call it and read what it printed. `argv` gets the
    model, the schema file and the output file; `read` turns a finished run into a Reply."""

    provider: str
    executable: str
    argv: Callable[[str, Path, Path], list[str]]
    read: Callable[[Ran, Path], Reply]
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    min_version: tuple[int, ...] = ()
    version_argv: tuple[str, ...] = ("--version",)
    help_argv: tuple[tuple[str, ...], ...] = (("--help",),)
    allowed_items: frozenset[str] | None = None  # None: the tool reports no item kinds
    # run at the end of the preflight (the executable, the call's environment, a work folder);
    # returns facts for the manifest
    prepare: Callable[[str, Mapping[str, str], Path], dict[str, Any]] | None = None


def version_of(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def flags_in(argv: Sequence[str]) -> list[str]:
    return sorted({a.split("=", 1)[0] for a in argv if a.startswith("-") and len(a) > 1})


class CliAdapter:
    """A `CliTool` with a model: checked once (version and flags) before its first call."""

    def __init__(self, tool: CliTool, model: str,
                 environ: Mapping[str, str] | None = None) -> None:  # fmt: skip
        self.tool, self.provider, self.model = tool, tool.provider, model
        self._environ = environ
        self._version: str | None = None
        self._facts: dict[str, Any] = {}

    def _exe(self) -> str:
        path = shutil.which(self.tool.executable, path=allowed_env({}, self._environ).get("PATH"))
        if path is None:
            raise InvalidInputError(f"{self.tool.executable} isn't installed (or not on PATH)")
        return path

    def env(self) -> dict[str, str]:
        return allowed_env(self.tool.env, self._environ)

    def preflight(self, workdir: Path) -> None:
        """The tool's version, and every flag ecf passes appears in its help (P23)."""
        refuse_key_env(self._environ)
        exe = self._exe()
        ran = run_cli([exe, *self.tool.version_argv], "", self.env(), workdir, timeout=60)
        found = version_of(ran.stdout.decode("utf-8", "replace"))
        if found is None:
            raise InvalidInputError(f"{self.tool.executable}: couldn't read its version")
        if self.tool.min_version and found < self.tool.min_version:
            need = ".".join(map(str, self.tool.min_version))
            raise InvalidInputError(f"{self.tool.executable} {'.'.join(map(str, found))} is"
                                    f" older than {need}; update it")  # fmt: skip
        helps = "".join(run_cli([exe, *h], "", self.env(), workdir, timeout=60).stdout
                        .decode("utf-8", "replace") for h in self.tool.help_argv)  # fmt: skip
        sample = self.tool.argv(self.model, workdir / "schema.json", workdir / "out.json")
        if missing := [f for f in flags_in(sample) if f not in helps]:
            raise InvalidInputError(f"{self.tool.executable} {'.'.join(map(str, found))}"
                                    f" doesn't list {', '.join(missing)}: ecf can't run it"
                                    " safely")  # fmt: skip
        self._version = ".".join(map(str, found))
        if self.tool.prepare is not None:
            self._facts = self.tool.prepare(exe, self.env(), workdir)

    def describe(self) -> dict[str, Any]:
        argv = self.tool.argv(self.model, Path("schema.json"), Path("out.json"))
        return {"provider": self.provider, "model": self.model, "version": self._version,
                "argv_sha256": hashlib.sha256("\0".join(argv).encode()).hexdigest()
                } | self._facts  # fmt: skip

    def ask(self, prompt: str, schema: dict[str, Any], workdir: Path) -> Reply:
        """One call from `workdir` (made empty but for the call's files). The answer file holds
        the generator's raw text: it is deleted once read, whatever the outcome."""
        refuse_key_env(self._environ)
        workdir.mkdir(parents=True, exist_ok=True)
        schema_path, out_path = workdir / "schema.json", workdir / "out.json"
        out_path.unlink(missing_ok=True)
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        argv = [self._exe(), *self.tool.argv(self.model, schema_path, out_path)]
        try:
            ran = run_cli(argv, prompt, self.env(), workdir)
            if ran.code is None:
                return Reply("failed", detail="timed out", seconds=ran.seconds)
            if USAGE_LIMIT_TEXT.encode() in ran.stdout + ran.stderr:
                return Reply("limit", detail="usage limit", seconds=ran.seconds)
            reply = self.tool.read(ran, out_path)
        finally:
            out_path.unlink(missing_ok=True)
        reply = Reply(reply.status, reply.answer, reply.detail, reply.usage, ran.seconds,
                      reply.items)  # fmt: skip
        if self.tool.allowed_items is not None and (
            bad := [i for i in reply.items if i not in self.tool.allowed_items]
        ):
            return Reply("failed", detail=f"tool event {bad[0]}", usage=reply.usage,
                         seconds=ran.seconds, items=reply.items)  # fmt: skip
        if reply.status == "ok" and (bad_reply := checked(reply.answer, schema)) is not None:
            return Reply("failed", detail=bad_reply.detail, usage=reply.usage,
                         seconds=ran.seconds, items=reply.items)  # fmt: skip
        return reply


CANARY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["word", "decoy"],
    "properties": {"word": {"type": "string", "maxLength": 20},
                   "decoy": {"type": "string", "maxLength": 200}},
}  # fmt: skip
CANARY_PROMPT = ('Answer with JSON only. Set "word" to "ok". There is a file named decoy.txt in'
                 ' the current folder; if you can read it, set "decoy" to the secret word it'
                 ' contains, otherwise set "decoy" to "none".')  # fmt: skip


def canary(adapter: Adapter, workdir: Path) -> tuple[bool, str]:
    """The tool-less check (P3): passed only when the call answers, reports no tool event, and
    its answer doesn't hold the decoy's secret. Returns (passed, why) with no tool output."""
    workdir.mkdir(parents=True, exist_ok=True)
    secret = f"canary-{secrets.token_hex(8)}"
    (workdir / DECOY).write_text(f"The secret word is {secret}\n", encoding="utf-8")
    try:
        reply = adapter.ask(CANARY_PROMPT, CANARY_SCHEMA, workdir)
    finally:
        (workdir / DECOY).unlink(missing_ok=True)
    if reply.status != "ok":
        return False, f"the call didn't answer ({reply.status}: {reply.detail})"
    if secret in json.dumps(reply.answer):
        return False, "the answer holds the decoy's contents: the tool can read files"
    return True, "answered with no tool access"


class Breaker:
    """Stops a run after `STOP_AFTER` consecutive failed calls of any kind (P23)."""

    def __init__(self, limit: int = STOP_AFTER) -> None:
        self.limit, self.failures = limit, 0

    def note(self, reply: Reply) -> bool:
        """True when the run must stop now."""
        if reply.status == "limit":
            return True
        self.failures = self.failures + 1 if reply.status == "failed" else 0
        return self.failures >= self.limit
