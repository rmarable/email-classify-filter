"""The `mistral` grader (eval generate plan, Adapters and Cost guards; build step 3).

The service holds the Mistral key and makes the call (`POST /v1/eval/mistral`, operator decision
2026-10-20): this side sends only the set and the case id; the service checks the set, reads the
email and builds the grading prompt. Here: the answer checked against the schema (Mistral's
strict mode isn't trusted alone), the cost of each call from its token counts and the shipped
price, a stop when the next call could pass `--max-cost`, and pacing after a 429 (Mistral's wait
or a doubling one) for up to 5 minutes, then a stop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.eval import models, tools
from ecf.paths import Paths

MAX_COST = 15.0  # US$ per run (`--max-cost`)
MAX_WAIT_S = 300.0  # 429s are paced for this long in all, then the run stops
FIRST_WAIT_S = 5.0
CALL_TIMEOUT_S = 210.0  # above the service's 180 s call (OD-582)
# before any call has been priced: a large email and a full answer
FIRST_CALL_TOKENS = (20_000, 2_048)


class MistralAdapter:
    provider = "mistral"

    def __init__(self, model: str, paths: Paths, *, max_cost: float = MAX_COST,
                 sleep: Callable[[float], None] = time.sleep,
                 pace: float = 0.0) -> None:  # fmt: skip
        entry = next((m for m in models.listed() if m.provider == "mistral" and m.id == model),
                     None)  # fmt: skip
        if entry is None or entry.price is None:
            raise InvalidInputError(f"mistral:{model} has no price on ecf's model list")
        self.model, self.paths, self.max_cost, self._sleep = model, paths, max_cost, sleep
        self.price: dict[str, Any] = entry.price
        self.spent = 0.0
        self.pace = pace  # seconds between calls, to stay under a known rate limit (J19)
        self._last = 0.0
        self._largest = self.cost({"prompt_tokens": FIRST_CALL_TOKENS[0],
                                   "completion_tokens": FIRST_CALL_TOKENS[1]})  # fmt: skip

    def cost(self, usage: dict[str, int]) -> float:
        return (
            usage.get("prompt_tokens", 0) * float(self.price["input"])
            + usage.get("completion_tokens", 0) * float(self.price["output"])
        ) / 1e6

    def estimate(self, chars: int, calls: int) -> float:
        """At most about this much (US$) for `calls` calls over `chars` characters of prompt:
        three characters a token, and every answer at its token cap."""
        return self.cost({"prompt_tokens": chars // 3,
                          "completion_tokens": FIRST_CALL_TOKENS[1] * calls})  # fmt: skip

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "version": None,
                "via": "ecf service", "price": self.price, "max_cost": self.max_cost,
                "spent": round(self.spent, 4)}  # fmt: skip

    def ask(self, prompt: str, schema: dict[str, Any], workdir: Path) -> tools.Reply:
        raise InvalidInputError("the Mistral grader takes a case, not a prompt (ask_case)")

    def ask_case(self, root: Path, case_id: str, schema: dict[str, Any]) -> tools.Reply:
        return self._call("/v1/eval/mistral", {"root": str(root.resolve()), "case": case_id,
                                               "model": self.model}, schema)  # fmt: skip

    def ask_draft(self, run_id: str, case_id: str, schema: dict[str, Any]) -> tools.Reply:
        """One draft of a draft run judged (M8 plan P5): the service builds the judge prompt from
        the set's card and the run's draft, as it does for grading."""
        body = {"case": case_id, "model": self.model}
        return self._call(f"/v1/eval/drafts/{run_id}/mistral", body, schema)

    def _call(self, path: str, body: dict[str, Any],  # noqa: PLR0911 - one return an outcome
              schema: dict[str, Any]) -> tools.Reply:  # fmt: skip
        if self.spent + self._largest > self.max_cost:
            return tools.Reply("limit", detail=f"--max-cost ${self.max_cost:.2f} reached")
        if self.pace and (gap := self.pace - (time.monotonic() - self._last)) > 0:
            self._sleep(gap)
        self._last = time.monotonic()
        waited, wait = 0.0, FIRST_WAIT_S
        start = time.monotonic()
        while True:
            with LocalClient(self.paths) as c:
                r: dict[str, Any] = c.request("POST", path, body, timeout=CALL_TIMEOUT_S)
            if r.get("status") != "rate_limit":
                break
            pause = float(r.get("retry_after") or wait)
            if waited + pause > MAX_WAIT_S:
                why = r.get("detail") or "HTTP 429"
                return tools.Reply("limit", detail=f"rate limited for 5 minutes ({why})",
                                   seconds=time.monotonic() - start)  # fmt: skip
            self._sleep(pause)
            waited, wait = waited + pause, wait * 2
        usage = {k: int(v) for k, v in dict(r.get("usage") or {}).items()}
        cost = self.cost(usage)
        self.spent += cost
        self._largest = max(self._largest, cost)
        seconds = time.monotonic() - start
        status, detail = str(r.get("status")), str(r.get("detail") or "")
        if status == "auth":
            return tools.Reply("limit", detail=f"Mistral {detail}: ecf models api-key set"
                               " --provider mistral", usage=usage, seconds=seconds)  # fmt: skip
        if status == "too large":
            return tools.Reply("failed", detail="too large", seconds=seconds)
        if status != "ok":
            return tools.Reply("failed", detail=detail or status, usage=usage, seconds=seconds)
        answer: dict[str, Any] | None = r.get("answer")
        if (bad := tools.checked(answer, schema)) is not None:
            return tools.Reply("failed", detail=bad.detail, usage=usage, seconds=seconds)
        return tools.Reply("ok", answer, usage=usage, seconds=seconds)


def adapter(model: str, paths: Paths) -> MistralAdapter:
    return MistralAdapter(model, paths)
