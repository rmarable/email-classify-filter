"""The weekly model canary (SPEC §7.6, §19; V1.4 step 10; OD-053, OD-300). Run by
`.github/workflows/canary.yml` every Monday and on demand: `uv run python scripts/model_canary.py`.

It reads Anthropic's model deprecations page and models overview (their Markdown versions) and the
Ollama library's tags page for the pinned local model, and fails when:
- a page can't be read or parsed (a format change would otherwise hide everything below);
- a pinned Claude ID is missing from the deprecations page's status table, or its state, firm
  retirement date or "not sooner than" date differs from `models.lock`'s `lifecycle` (a release
  then records the change, and the service announces it; OD-299);
- the pinned Ollama tag isn't on its tags page.

A newer model in a family ecf pins (the overview's current lineup) and the deprecation's
recommended replacement are only reported in the job summary. Nothing here changes a pin.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

from ecf_server import claude_pins, model_watch, ollama

DEPRECATIONS = "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"
OVERVIEW = "https://platform.claude.com/docs/en/models/overview.md"
TIMEOUT_S = 30
_ROW = re.compile(r"^\|(.+)\|\s*$")
_ID = re.compile(r"`(claude-[a-z0-9-]+)`")


class CanaryError(Exception):
    """A page ecf can't parse."""


@dataclass(frozen=True)
class Status:
    state: str
    retires: date | None  # firm date
    not_sooner_than: date | None
    announced: bool  # "To be announced"


@dataclass
class Report:
    failures: list[str] = field(default_factory=list[str])
    notes: list[str] = field(default_factory=list[str])


def _cells(line: str) -> list[str] | None:
    m = _ROW.match(line.strip())
    return [c.strip() for c in m.group(1).split("|")] if m else None


def _day(text: str) -> date:
    return datetime.strptime(text.strip(), "%B %d, %Y").date()


def parse_status(md: str) -> dict[str, Status]:
    """The "Model status" table: API model name -> state and retirement."""
    try:
        section = md[md.index("## Model status") :]
    except ValueError as e:
        raise CanaryError("deprecations page: no 'Model status' section") from e
    rows = [c for c in map(_cells, section.splitlines()) if c is not None]
    if len(rows) < 3 or rows[0][:4] != ["API model name", "Current state", "Deprecated",
                                         "Tentative retirement date"]:  # fmt: skip
        raise CanaryError("deprecations page: the status table's columns changed")
    out: dict[str, Status] = {}
    for c in rows[2:]:
        if len(c) < 4 or not c[0].startswith("claude-"):
            break  # the table ended
        when = c[3]
        try:
            if when.lower().startswith("not sooner than"):
                st = Status(c[1], None, _day(when[len("not sooner than") :]), False)
            elif when.lower() in ("to be announced", "tba", "n/a"):
                st = Status(c[1], None, None, True)
            else:
                st = Status(c[1], _day(when), None, False)
        except ValueError as e:
            raise CanaryError(f"deprecations page: can't read the date {when!r}") from e
        out[c[0]] = st
    if not out:
        raise CanaryError("deprecations page: the status table is empty")
    return out


def parse_replacements(md: str) -> dict[str, str]:
    """Deprecated model -> recommended replacement, from the history tables."""
    out: dict[str, str] = {}
    for c in filter(None, map(_cells, md.splitlines())):
        if len(c) >= 3 and _ID.fullmatch(c[1]) and _ID.fullmatch(c[2]):
            old, new = _ID.fullmatch(c[1]), _ID.fullmatch(c[2])
            if old and new:
                out.setdefault(old.group(1), new.group(1))
    return out


def parse_lineup(md: str) -> list[str]:
    """The overview's current models (its "Claude API ID" row)."""
    for c in filter(None, map(_cells, md.splitlines())):
        if c and c[0] == "Claude API ID":
            ids = [m.group(1) for cell in c[1:] for m in [_ID.search(cell)] if m]
            if ids:
                return ids
    raise CanaryError("models overview: no 'Claude API ID' row")


def check(deprecations: str, overview: str, tags_html: str) -> Report:
    rep = Report()
    lock, life = claude_pins.load_lock(), claude_pins.lifecycle()
    pinned = sorted(set(lock.values()))
    status = parse_status(deprecations)
    repl = parse_replacements(deprecations)
    for mid in pinned:
        page, mine = status.get(mid), life[mid]
        if page is None:
            rep.failures.append(f"{mid}: not on the deprecations page's status table")
            continue
        if page.state != mine.state:
            rep.failures.append(f"{mid}: state {page.state} on the page, {mine.state} in"
                                " models.lock")  # fmt: skip
        if page.retires != mine.retires:
            rep.failures.append(f"{mid}: retirement {page.retires or 'not set'} on the page,"
                                f" {mine.retires or 'not set'} in models.lock")  # fmt: skip
        if page.not_sooner_than != mine.not_sooner_than:
            rep.failures.append(f"{mid}: 'not sooner than' {page.not_sooner_than or 'not set'}"
                                f" on the page, {mine.not_sooner_than or 'not set'} in"
                                " models.lock")  # fmt: skip
        if mid in repl and repl[mid] != mine.replacement:
            rep.notes.append(f"{mid}: Anthropic recommends {repl[mid]}")
    families = {claude_pins.family(i) for i in pinned}
    for mid in parse_lineup(overview):
        if mid not in pinned and claude_pins.family(mid) in families:
            rep.notes.append(f"newer in a pinned family: {mid} (the overview's lineup)")
    repo, tag = ollama.load_pin().tag.split(":", 1)
    tags = model_watch.parse_tags(tags_html, repo)
    if tag not in tags:
        rep.failures.append(f"Ollama library: {repo}:{tag} isn't on its tags page"
                            f" ({len(tags)} tags read)")  # fmt: skip
    else:
        rep.notes.append(f"Ollama library: {len(tags)} {repo} tags; {repo}:{tag} listed")
    return rep


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ecf-model-canary"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 - fixed https URLs
        return r.read().decode("utf-8")


def main() -> int:
    repo = ollama.load_pin().tag.split(":", 1)[0]
    try:
        rep = check(fetch(DEPRECATIONS), fetch(OVERVIEW),
                    fetch(model_watch.TAGS_URL.format(repo=repo)))  # fmt: skip
    except (CanaryError, OSError) as e:
        rep = Report(failures=[f"can't read a page: {e}"])
    lines = ["## Model canary", ""]
    lines += [f"- FAIL: {f}" for f in rep.failures] + [f"- {n}" for n in rep.notes]
    if rep.failures:
        lines += ["", "Record the change in `src/ecf_server/data/models.lock` (SPEC §7.6) in a"
                  " release, or fix the parser if a page changed format."]  # fmt: skip
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
