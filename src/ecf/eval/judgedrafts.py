"""`ecf eval judge-drafts`: one judge grades every draft of a complete draft run (M8 plan §4
"Judges (P5)"; Step 5c).

The service builds each draft's prompt and hands them out (`GET /v1/eval/drafts/<run>/items`), and
keeps the answers, one case at a time, so a stopped run resumes where it left off and a case is
never graded twice by the same judge. Mistral is asked through the service, which saves its answer
itself (`ask_draft`); a command-line judge (Sol, through codex) is given the service's prompt and
its answer posted back. A judge's spend is kept with the run, so its cap holds across resumes
(J5C-7). Claude is never a judge: it wrote the cards and the drafts.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ecf.errors import InvalidInputError
from ecf.eval.tools import Adapter, Breaker, CliAdapter, Reply

Request = Callable[[str, str, dict[str, Any] | None], dict[str, Any]]
# A command-line judge's call: its own timeout, and one retry when that call timed out, never on
# any other failure; the breaker sees only the final reply (OD-580, S10)
TIMEOUT_S = 120.0
TIMED_OUT = "timed out"


@runtime_checkable
class DraftJudge(Protocol):
    spent: float

    def ask_draft(self, run_id: str, case_id: str, schema: dict[str, Any]) -> Reply: ...


def run(request: Request, adapter: Adapter, run: str, workdir: Path,
        echo: Callable[[str], None] = lambda _s: None) -> tuple[dict[str, int], str]:  # fmt: skip
    """Judge the run's drafts not yet judged by this judge: counts by outcome, and why it stopped
    (empty when it finished)."""
    if adapter.provider == "claude":
        raise InvalidInputError("Claude can't judge the drafts: it wrote the cards and the drafts"
                                " (M8 plan P5)")  # fmt: skip
    judge = f"{adapter.provider}:{adapter.model}"
    if isinstance(adapter, CliAdapter):
        adapter.timeout_s = TIMEOUT_S
    got = request("GET", f"/v1/eval/drafts/{run}/items", None)
    run_id, digest = str(got["run_id"]), str(got["prompt_digest"])
    prior = request("GET", f"/v1/eval/drafts/{run_id}/judgements", None)
    if prior.get("prompt_digest") not in (None, digest):  # never skip as done, never mix (OD-580)
        raise InvalidInputError(f"run {run_id[:8]} was judged under another judge prompt: move its"
                                " judgements file aside (<run>.judgements-<old digest>.json)"
                                " before judging it again")  # fmt: skip
    done = prior["judges"].get(judge.lower(), {})
    api = isinstance(adapter, DraftJudge)
    if api:  # the cap counts what this judge already spent on this run (J5C-7)
        adapter.spent = float(prior.get("spent", {}).get(judge.lower(), 0.0))
    counts: Counter[str] = Counter(already=len(done))
    start = time.monotonic()
    todo = [i for i in got["items"] if i["case"] not in done]
    echo(f"{len(todo)} drafts to judge with {judge} ({len(done)} already judged)")
    try:
        detail = _judge_all(request, adapter, run_id, digest, judge, todo, counts, workdir)
    finally:  # the spend is kept even when a call or a route error stops the loop (delta 4)
        if api:
            try:
                request("POST", f"/v1/eval/drafts/{run_id}/judgements",
                        {"judge": judge, "answers": {}, "prompt_digest": digest,
                         "spent": adapter.spent})  # fmt: skip
            except Exception as exc:  # never hides the error that stopped the loop
                echo(f"the spend (US${adapter.spent:.2f}) wasn't saved: {exc}")
    spend = f", US${adapter.spent:.2f} spent on this run" if api else ""
    echo(f"{judge}: {round(time.monotonic() - start)} s{spend}")
    return dict(counts), detail


def _judge_all(request: Request, adapter: Adapter, run_id: str, digest: str, judge: str,
               todo: list[dict[str, Any]], counts: Counter[str], workdir: Path) -> str:  # fmt: skip
    breaker = Breaker()
    api = isinstance(adapter, DraftJudge)
    for it in todo:
        if api:
            reply = adapter.ask_draft(run_id, it["case"], it["schema"])
            if reply.status == "failed" and reply.detail == TIMED_OUT:  # one retry (OD-582)
                counts["retried"] += 1
                reply = adapter.ask_draft(run_id, it["case"], it["schema"])
            if reply.status == "ok" and reply.answer is not None:
                counts["refused" if reply.answer.get("refused") else "judged"] += 1
            else:
                counts[reply.status] += 1
        else:
            reply = adapter.ask(it["prompt"], it["schema"], workdir / "call")
            if isinstance(adapter, CliAdapter) and reply.status == "failed" \
                    and reply.detail == TIMED_OUT:  # fmt: skip
                counts["retried"] += 1
                reply = adapter.ask(it["prompt"], it["schema"], workdir / "call")
            if reply.status == "ok" and reply.answer is not None:
                request("POST", f"/v1/eval/drafts/{run_id}/judgements",
                        {"judge": judge, "answers": {it["case"]: reply.answer},
                         "prompt_digest": digest})  # fmt: skip
                counts["refused" if reply.answer.get("refused") else "judged"] += 1
            else:
                counts[reply.status] += 1
        if breaker.note(reply):
            why = f"stopped: {reply.status} ({reply.detail})"
            return f"{why}; fix the cause, then run the same command again: judged drafts are kept"
    return ""
