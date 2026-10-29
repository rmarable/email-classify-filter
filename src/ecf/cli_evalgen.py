"""`ecf eval generate`, `grade`, `expect` and `report`: rate ecf with other models
(planning-docs/EVAL-GENERATE-GRADE.md). The flow: generate, grade, label (blind), expect, run,
report. Generation and grading run in the client; `expect` asks the service's rules engine."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.paths import Paths

NOTICE = {
    "generator": ("The generator receives ecf's published category lists and definitions, the"
                  " fictitious recipient configuration, the safety rules and the output format,"
                  " under your own sign-in to {provider}. Nothing from your mail or your ecf"
                  " install is sent."),
    "grader": ("The grader receives each generated email and the field definitions, under your"
               " own sign-in to {provider}; never ecf's answers or your labels."),
    "draft_judge": ("The judge receives each synthetic test email of the draft set, its question"
                    " and the model's draft reply, under your own sign-in to {provider} (Mistral:"
                    " through ecf, with your key); never your mail."),
}  # fmt: skip
NEVER_REAL = "Never put real mail in a generated set."


if TYPE_CHECKING:
    from ecf.eval.generate import Summary


def closing_line(s: Summary) -> str:
    """A generation run's last line; refusals on the retry are within the drops (OD-554)."""
    on_retry = f" ({s.refused_on_retry} refused on the retry)" if s.refused_on_retry else ""
    return (f"{s.state}: kept {s.kept} of {s.target}; refused {sum(s.refused.values())};"
            f" dropped {sum(s.dropped.values())}{on_retry}")  # fmt: skip


def _terminals() -> bool:
    """Both standard input and output are a terminal (one seam, so tests can stand in for it)."""
    stdin, stdout = sys.stdin, sys.stdout
    return bool(stdin and stdin.isatty() and stdout and stdout.isatty())


def _may_show(dev: bool) -> None:
    """`--show-findings` needs a DEV run and a terminal. The terminal check guards against a
    mistake (a pipe, a file, a tool that captures output), not a determined user: the generated
    text it prints belongs in the operator's own Terminal only."""
    if not dev:
        raise InvalidInputError("--show-findings is for a DEV run only: add --dev")
    if not _terminals():
        raise InvalidInputError("--show-findings prints generated text: run it in Terminal, not"
                                " through a pipe or a tool that captures output")  # fmt: skip


def _checked(adapter: object) -> None:
    """A command-line provider's version and flags, then the tool-less canary (P3): one call,
    from a throwaway folder, before the set's folder is touched."""
    import tempfile  # noqa: PLC0415

    from ecf.eval import tools  # noqa: PLC0415

    if not isinstance(adapter, tools.CliAdapter):
        return
    with tempfile.TemporaryDirectory(prefix="ecf-canary-") as tmp:
        adapter.preflight(Path(tmp))
        ok, why = tools.canary(adapter, Path(tmp) / "canary")
    if not ok:
        raise InvalidInputError(f"{adapter.provider} failed the tool-less check: {why}; nothing"
                                " was generated or graded")  # fmt: skip
    typer.echo(f"{adapter.provider} {adapter.model}: tool-less check passed")


def _estimate(root: Path, adapter: object, yes: bool) -> None:
    """An API grader's cost before the run: at most about, with the cap (Cost guards)."""
    from ecf.eval import grade as grading  # noqa: PLC0415
    from ecf.eval import labels as label_file  # noqa: PLC0415
    from ecf.eval import mistral_api  # noqa: PLC0415

    assert isinstance(adapter, mistral_api.MistralAdapter)  # noqa: S101 - the caller checked
    rows = label_file.read(root)
    fixed = len(grading.grade_prompt(""))
    chars = sum(fixed + (root / str(r["file"])).stat().st_size for r in rows)
    cost = adapter.estimate(chars, len(rows))
    typer.echo(f"{len(rows)} calls to {adapter.model}: at most about ${cost:.2f}; this run stops"
               f" before passing ${adapter.max_cost:.2f} (--max-cost)")  # fmt: skip
    if not yes and not typer.confirm("Go ahead?", default=False):
        raise typer.Exit(1)


def _settle_terminal(root: Path, today: Any) -> None:
    """The disputes one by one: the email, each disputed field, then the judges' answers."""
    from ecf.eval import settle as st  # noqa: PLC0415
    from ecf.prompts import require_terminal  # noqa: PLC0415

    require_terminal()
    items = st.questions(root)
    if not items:
        typer.echo("nothing to settle: the judges agree on every open field")
        return
    typer.echo(f"Signs to look for (general, not about any one email): {st.SIGNS}.\n")
    done: dict[str, dict[str, Any]] = {}
    for n, it in enumerate(items, 1):
        typer.echo(f"\n== email {n} of {len(items)}\n{it['view']}\n")
        if "note" in it:
            typer.echo(f"({it['note']})")
        answer: dict[str, Any] = {}
        for f in it["fields"]:
            choices = ["yes", "no"] if f["kind"] == "boolean" else f["values"]
            typer.echo(f"{f['id']}: {f['help']}")
            while True:
                a = typer.prompt(f"  {f['id']} [{' / '.join(choices)}, q quits]").strip()
                if a == "q":
                    break
                if a in choices:
                    answer[f["id"]] = (a == "yes") if f["kind"] == "boolean" else a
                    break
            if a == "q":
                break
        if len(answer) < len(it["fields"]):
            break
        for f in it["fields"]:
            said = ", ".join(str(v) for v in it["judges"].get(f["id"], []))
            typer.echo(f"  judges said for {f['id']}: {said}")
        if typer.confirm("  Keep your answers?", default=True):
            done[it["case"]] = answer
    n_done = st.apply(root, done, today) if done else 0
    typer.echo(f"settled {n_done} emails; {len(items) - n_done} still open")
    if n_done:
        typer.echo(f"Next: ecf eval expect --root {root}")


def _write_private(path: Path, text: str) -> None:
    """A page's data, which holds email views and draft text: written 0600 in a 0700 folder (L6)."""
    import os  # noqa: PLC0415

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    path.chmod(0o600)  # an existing file keeps its old mode through O_CREAT


def _notice(role: str, provider: str, yes: bool) -> None:
    typer.echo(NOTICE[role].format(provider=provider) + " " + NEVER_REAL)
    if not yes and not typer.confirm("Go ahead?", default=False):
        raise typer.Exit(1)


def make_commands(eval_app: typer.Typer, paths: Callable[[], Paths]) -> None:  # noqa: PLR0915 - the four commands
    @eval_app.command("generate")
    def generate(  # noqa: PLR0913, PLR0917 - typer options
        root: Annotated[
            Path | None, typer.Option("--root", help="A new, empty folder for the set.")
        ] = None,
        mix_file: Annotated[
            Path | None,
            typer.Option("--mix", help="Your mix (ids and shares; default: the shipped one)."),
        ] = None,
        count: Annotated[
            int | None, typer.Option("--count", help="Emails to keep (default 300; 30 with --dev).")
        ] = None,
        generator: Annotated[
            str | None, typer.Option("--generator", help="<provider>[:<model>].")
        ] = None,
        dev: Annotated[
            bool, typer.Option("--dev", help="All Claude: a plumbing test, not a measurement.")
        ] = False,
        k: Annotated[int, typer.Option("--k", help="Seeds per email call.")] = 5,
        resume: Annotated[bool, typer.Option("--resume", help="Finish a stopped run.")] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
        write_mix: Annotated[
            Path | None,
            typer.Option("--write-mix", help="Write the shipped mix to edit, and stop."),
        ] = None,
        claude_generator: Annotated[
            bool,
            typer.Option(
                "--claude-generator",
                help="PROD: let Claude write the set (scored then"
                " on Claude-written mail; your own Claude plan sign-in, your own use).",
            ),
        ] = False,
        seeds_only: Annotated[
            bool, typer.Option("--seeds-only", help="A seed run: seeds for every group, no email.")
        ] = False,
        seeds_from: Annotated[
            Path | None,
            typer.Option(
                "--seeds-from", help="Write emails from a seed run's seeds (no seed call)."
            ),
        ] = None,
        show_findings: Annotated[
            bool,
            typer.Option(
                "--show-findings",
                help="DEV: print each hygiene finding's text as it happens, to find what the"
                " scan trips on; nothing is written. Run it yourself in Terminal (Claude never"
                " runs it); with --resume, pass it again.",
            ),
        ] = False,
    ) -> None:
        """Generate a test set with another model: seeds, then emails, each checked as it's made;
        refusals are recorded, never retried."""
        from ecf.eval import generate as gen  # noqa: PLC0415
        from ecf.eval import mix as mixmod  # noqa: PLC0415
        from ecf.eval import providers  # noqa: PLC0415

        if write_mix is not None:
            mixmod.write_default(write_mix)
            typer.echo(f"wrote {write_mix}: edit the shares, then pass it with --mix")
            return
        if root is None:
            raise InvalidInputError("--root: the folder for the new set")
        root = root.expanduser().resolve()  # a CLI tool's answer file is named from it
        mode = "dev" if dev else "prod"
        if show_findings:
            _may_show(dev)
        if seeds_only and seeds_from is not None:
            raise InvalidInputError("--seeds-only makes seeds; --seeds-from uses them: one or the"
                                    " other")  # fmt: skip
        shared: dict[str, Any] | None = None
        if resume:
            if seeds_only or seeds_from is not None:
                raise InvalidInputError("--resume carries on as the run began: leave out"
                                        " --seeds-only and --seeds-from")  # fmt: skip
            doc = gen.read_manifest(root)
            seeds_only, shared = bool(doc.get("seeds_only")), doc.get("seeds_from")
            claude_generator = bool(doc.get("generator_family_waiver"))
            if doc["mode"] != mode:
                raise InvalidInputError(f"{root} is a {doc['mode']} set: resume it the same way")
            m = mixmod.parse(doc["mix"], source="manifest")
            n = int(doc["count"])
            generator = generator or f"{doc['generator']['provider']}:{doc['generator']['model']}"
        else:
            m = mixmod.load(mix_file) if mix_file else mixmod.default()
            n = count if count is not None else (30 if dev else 300)
        adapter = providers.resolve(generator, "generator", mode, paths(),
                                    claude_waiver=claude_generator)  # fmt: skip
        _notice("generator", adapter.provider, yes)
        _checked(adapter)
        gen.start(root, resume=resume)
        if seeds_from is not None:
            shared = gen.share_seeds(root, seeds_from.expanduser().resolve(), mode,
                                     n, m, k)  # fmt: skip
        run = gen.Run(root, m, n, adapter, mode, k=k, seeds_only=seeds_only,
                      shared=shared, claude_waiver=claude_generator and adapter.provider ==
                      "claude", echo=typer.echo,
                      show=typer.echo if show_findings else None)  # fmt: skip
        s = run.run(resume=resume or shared is not None)
        typer.echo(closing_line(s))
        if s.detail:
            typer.echo(s.detail)
        if s.state == "seeded":
            typer.echo(f"Next: an email run per model with `--seeds-from {root}` (the same"
                       " --count, --mix and --k)")  # fmt: skip
        elif s.state != "stopped":
            typer.echo(f"Next: `ecf eval grade --root {root}`, then label blind with `ecf eval"
                       f" label --root {root} --review all --add`")  # fmt: skip

    @eval_app.command("grade")
    def grade(
        root: Annotated[Path, typer.Option("--root", help="A generated set.")],
        grader: Annotated[
            str | None, typer.Option("--grader", help="<provider>[:<model>].")
        ] = None,
        resume: Annotated[bool, typer.Option("--resume", help="Finish a stopped grading.")] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
        max_cost: Annotated[
            float, typer.Option("--max-cost", help="US$ this run may spend (API graders).")
        ] = 15.0,
        pace: Annotated[
            float, typer.Option("--pace", help="Seconds between API calls (a rate limit).")
        ] = 0.0,
    ) -> None:
        """Have a model from another family label the set blind; written as a result file."""
        from ecf.eval import generate as gen  # noqa: PLC0415
        from ecf.eval import grade as grading  # noqa: PLC0415
        from ecf.eval import mistral_api, models, providers  # noqa: PLC0415

        doc = gen.read_manifest(root)
        mode = str(doc.get("mode"))
        adapter = providers.resolve(grader, "grader", mode, paths())
        models.check_judge(doc, (adapter.provider, adapter.model))  # before the canary call
        _notice("grader", adapter.provider, yes)
        if isinstance(adapter, mistral_api.MistralAdapter):
            if max_cost <= 0:
                raise InvalidInputError("--max-cost must be more than 0")
            adapter.max_cost = max_cost
            adapter.pace = max(pace, 0.0)
            _estimate(root, adapter, yes)
        _checked(adapter)
        path, counts, detail = grading.Grading(root, adapter, echo=typer.echo).run(resume=resume)
        typer.echo(", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing graded")
        typer.echo(detail if path is None else f"wrote {path}")

    @eval_app.command("agree")
    def agree(root: Annotated[Path, typer.Option("--root", help="A generated set.")]) -> None:
        """Set the labels from the judges' grades: where they agree, the label is theirs; where
        they don't, the email waits for you (`ecf eval settle`). A disagreement on category or
        deadline_mentioned waits for `ecf eval expect`, which asks you only if it changes the
        expected rule or safety."""
        from datetime import UTC, datetime  # noqa: PLC0415

        from ecf.eval import agree as ag  # noqa: PLC0415

        o = ag.run(root.expanduser().resolve(), datetime.now(UTC).date())
        typer.echo(f"agreed: {o.agreed} emails confirmed by the judges; for you: {o.disputed}"
                   f" ({o.whole} whole, a judge refused or skipped them); already confirmed:"
                   f" {o.skipped - o.yours_edited - o.waiting}; still waiting for you:"
                   f" {o.waiting}")  # fmt: skip
        if o.yours_edited:
            typer.echo(f"{o.yours_edited} of your confirmed emails were edited: confirm them"
                       f" again with `ecf eval label --root {root}`, or the judges decide them"
                       " next run")  # fmt: skip
        if o.redecided:
            typer.echo(f"{o.redecided} emails edited by hand were decided again (an edit to"
                       " sender_type or priority doesn't stick: settle it)")  # fmt: skip
        if o.fields:
            typer.echo("disputed fields: " + ", ".join(f"{k} {v}" for k, v in
                                                       o.fields.most_common()))  # fmt: skip
        if o.deferred:
            typer.echo("left to `ecf eval expect` (asked only if they change the rule): "
                       + ", ".join(f"{k} {v}" for k, v in o.deferred.most_common())
                       + "; \"agreed\" counts emails the judges agree on otherwise")  # fmt: skip
        typer.echo(f"Next: ecf eval expect --root {root}")

    @eval_app.command("settle")
    def settle_cmd(
        root: Annotated[Path, typer.Option("--root", help="A generated set.")],
        export: Annotated[
            Path | None, typer.Option("--export", help="Write a phone page's data to this folder.")
        ] = None,
        answers: Annotated[
            Path | None, typer.Option("--from", help="A phone page's saved answers (JSON).")
        ] = None,
        tokens: Annotated[
            Path | None, typer.Option("--tokens", help="The page's token map (with --from).")
        ] = None,
    ) -> None:
        """Decide the fields the judges disagreed on, blind: in the terminal, or with a phone
        page (--export, then --from with --tokens)."""
        from datetime import UTC, datetime  # noqa: PLC0415

        from ecf.eval import settle as st  # noqa: PLC0415

        where = root.expanduser().resolve()
        today = datetime.now(UTC).date()
        if export is not None:
            data, key = st.export(where, export.expanduser())
            typer.echo(f"wrote {data} (no case ids) and {key} (keep it: --tokens)")
            return
        if answers is not None:
            if tokens is None:
                raise InvalidInputError("--from needs --tokens, the page's token map")
            done, left = st.apply_page(where, answers.expanduser(), tokens.expanduser(), today)
            typer.echo(f"settled {done} emails; {left} not fully answered yet")
            if done:
                typer.echo(f"Next: ecf eval expect --root {root}")
            return
        _settle_terminal(where, today)

    @eval_app.command("expect")
    def expect(root: Annotated[Path, typer.Option("--root", help="A generated set.")]) -> None:
        """Fill each labelled card's expected rule and safety from ecf's rules engine. A card the
        judges labelled is evaluated with each judge's sender_type and priority, and category or
        deadline_mentioned when they disagreed on it; when one decides the rule, the email goes to
        `ecf eval settle` for it."""
        from datetime import UTC, datetime  # noqa: PLC0415

        from ecf.eval import expect as ex  # noqa: PLC0415
        from ecf.eval import generate as gen  # noqa: PLC0415

        where = root.expanduser().resolve()
        gen.verify(where)
        p = ex.plan(where)
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/eval/expect", {"root": str(where), "variants": p.options},
                          timeout=300.0)  # fmt: skip
        counts = ex.apply(where, list(r["cases"]), datetime.now(UTC).date(), plan=p,
                          digest=r.get("rules_digest"))  # fmt: skip
        typer.echo(", ".join(f"{k} {v}" for k, v in counts.items()))
        if p.edited:
            typer.echo(f"{len(p.edited)} judged emails were edited by hand since their"
                       f" confirmation: run `ecf eval agree --root {root}` first")  # fmt: skip
        if failed := counts["undecided"] - counts["not_evaluated"]:
            typer.echo(f"{failed} emails stay undecided and unconfirmed (a rules error; see"
                       " work/expect.jsonl): no figure counts them yet")  # fmt: skip
        if counts["not_evaluated"]:
            typer.echo(f"{counts['not_evaluated']} emails weren't evaluated by the service (too"
                       " large to send): no expected rule, so out of the rule and safety figures;"
                       " their labels still count")  # fmt: skip
        if waiting := counts["to_settle"] + counts["waiting"]:
            typer.echo(f"Next: ecf eval settle --root {root} (or --export for the phone page):"
                       f" {waiting} emails wait for you")  # fmt: skip
        elif p.judged():
            typer.echo(f"Next: ecf eval run --root {root}")
        else:
            typer.echo(f"Next: confirm each with `ecf eval label --root {root}`.")

    @eval_app.command("report")
    def report(
        root: Annotated[Path, typer.Option("--root", help="A generated set.")],
        results: Annotated[
            list[Path] | None,
            typer.Argument(help="Result files: `ecf eval run` results and grades on this set."),
        ] = None,
    ) -> None:
        """The set's figures against your confirmed labels: the grader's accuracy first."""
        from ecf.eval import report as rep  # noqa: PLC0415
        from ecf.eval.results import load_result  # noqa: PLC0415

        loaded = [load_result(p) for p in results or []]
        for line in rep.report(root.expanduser(), loaded):
            typer.echo(line)

    @eval_app.command("judge-drafts")
    def judge_drafts(
        run: Annotated[str, typer.Argument(help="A finished draft run's ID (or its first 8).")],
        judge: Annotated[str, typer.Option("--judge", help="<provider>:<model>, never Claude.")],
        yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
        max_cost: Annotated[
            float, typer.Option("--max-cost", help="US$ this run may spend (API judges).")
        ] = 1.0,
    ) -> None:
        """Have a judge grade every draft of a finished draft run (M8 plan §4); run it again to
        finish a stopped one: judged drafts are kept."""
        import tempfile  # noqa: PLC0415

        from ecf.eval import judgedrafts, mistral_api, providers  # noqa: PLC0415

        adapter = providers.resolve(judge, "grader", "prod", paths())
        typer.echo(NOTICE["draft_judge"].format(provider=adapter.provider) + " " + NEVER_REAL)
        if not yes and not typer.confirm("Go ahead?", default=False):
            raise typer.Exit(1)
        if isinstance(adapter, mistral_api.MistralAdapter):
            if max_cost <= 0:
                raise InvalidInputError("--max-cost must be more than 0")
            adapter.max_cost = max_cost
        _checked(adapter)
        with LocalClient(paths()) as c, tempfile.TemporaryDirectory(prefix="ecf-judge-") as tmp:
            counts, detail = judgedrafts.run(c.request, adapter, run, Path(tmp),
                                             echo=typer.echo)  # fmt: skip
        typer.echo(", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing judged")
        if detail:
            typer.echo(detail)

    @eval_app.command("null-drafts")
    def null_drafts(root: Annotated[Path, typer.Option("--root", help="The draft set.")]) -> None:
        """The null floor (M8 plan §4): the fixed reply wherever a draft is allowed, as a run the
        judges grade like any other. A pipeline and judge check, never a gate."""
        where = str(root.expanduser().resolve())
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/eval/drafts/null", {"root": where})
        short = str(r["run_id"])[:8]
        typer.echo(f"null floor {short}: {r['drafts']} fixed drafts on {r['cases']} cards."
                   f" Next: ecf eval judge-drafts {short} --judge ...")  # fmt: skip

    @eval_app.command("drafts-score")
    def drafts_score(
        run: Annotated[str, typer.Argument(help="A finished draft run's ID (or its first 8).")],
    ) -> None:
        """A finished draft run's scores per group: OK with and without checker refusals, unsafe,
        still to settle. Provisional until calibration names the fields that count."""
        with LocalClient(paths()) as c:
            r = c.request("GET", f"/v1/eval/drafts/{run}/score")
        s = r["summary"]
        cal = r["calibration_digest"]
        typer.echo(f"run {r['run_id'][:8]} ({'forced' if r['forced'] else 'natural'}), judges:"
                   f" {', '.join(r['judges']) or 'none yet'}, calibration"
                   f" {cal[:12] if cal else 'none'}")  # fmt: skip
        for w in r["warnings"]:
            typer.echo(f"warning: {w}")
        for w in s["provisional_why"]:
            typer.echo(f"provisional: {w}")
        for g, n in sorted(s["groups"].items()):
            typer.echo(f"  {g}: {n['ok']}/{n['counted']} OK ({n['ok_without_refusals']} without"
                       f" checker refusals), unsafe {n['unsafe']} (+{n['unsafe_pending']} to"
                       f" settle), awaiting a judge {n['awaiting']}, to settle"
                       f" {n['to_settle']}")  # fmt: skip
        if s["disagreement"]:  # how often the two judges answered a field apart (K12)
            typer.echo("judges apart: " + ", ".join(
                f"{f} {d['apart']}/{d['of']}" + (" (over 30%)" if d["over_limit"] else "")
                for f, d in s["disagreement"].items()))  # fmt: skip
        if s["taps"]["hidden"]:
            typer.echo(f"settle page: {s['taps']['hidden']} name lines hidden,"
                       f" {s['taps']['tapped']} shown on a tap")  # fmt: skip

    @eval_app.command("drafts-record")
    def drafts_record(
        runs: Annotated[list[str], typer.Argument(help="Judged draft runs to record.")],
    ) -> None:
        """Fill each run's draft record with its scores and pin it to the calibration in force, if
        it isn't pinned already (a run with nothing to settle needs this; `settle-drafts --from`
        records the runs on its page)."""
        with LocalClient(paths()) as c:
            for run in runs:
                r = c.request("POST", f"/v1/eval/drafts/{run}/record", {})
                cal = r["calibration_digest"]
                typer.echo(f"run {r['run_id'][:8]}: recorded {r['recorded']} scored drafts,"
                           f" {r['to_settle']} still to settle, calibration"
                           f" {cal[:12] if cal else 'none yet'}")  # fmt: skip

    @eval_app.command("calibration-build")
    def calibration_build(
        pilot: Annotated[str, typer.Option("--pilot", help="A complete forced pilot run.")],
        seeds: Annotated[Path, typer.Option("--seeds", help="The seeds file (YAML).")],
    ) -> None:
        """Build the judges' calibration set: real drafts from the pilot and the seeded drafts,
        mixed unmarked (M8 plan §4). Then both judges grade it with `ecf eval judge-drafts`."""
        from ecf.yamlio import load_yaml  # noqa: PLC0415

        doc = load_yaml(seeds.expanduser().read_text(encoding="utf-8"), source=seeds.name)
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/eval/drafts/calibration", {"pilot": pilot, "seeds": doc})
        short = str(r["run_id"])[:8]
        left = r["left_out_changed"]
        typer.echo(f"calibration {short}: {r['real']} real drafts and {r['seeds']} seeds"
                   + (f" ({left} pilot drafts left out: their card was rewritten)" if left else "")
                   + f". Next: ecf eval judge-drafts {short} --judge ... (both judges), then"
                   f" ecf eval calibration-export {short} --export DIR")  # fmt: skip

    @eval_app.command("calibration-export")
    def calibration_export(
        run: Annotated[str, typer.Argument(help="The calibration record.")],
        export: Annotated[Path, typer.Option("--export", help="A folder for the page's data.")],
    ) -> None:
        """The blind rating page's data (no ids, no key); its token map stays with ecf."""
        import json  # noqa: PLC0415

        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/eval/drafts/{run}/rating-page", {})
        out = export.expanduser()
        _write_private(out / "draft-rating-page.json", json.dumps(r))
        typer.echo(f"wrote {out / 'draft-rating-page.json'} ({len(r['items'])} drafts), page"
                   f" {r['page']}. Next: ecf eval calibration-import {run} --from <answers>"
                   f" --page {r['page']}")  # fmt: skip

    @eval_app.command("calibration-import")
    def calibration_import(
        run: Annotated[str, typer.Argument(help="The calibration record.")],
        answers: Annotated[Path, typer.Option("--from", help="The page's saved answers.")],
        page: Annotated[str, typer.Option("--page", help="The rating page's id.")],
    ) -> None:
        """Your ratings from the page; an item not fully rated is left for later."""
        import json  # noqa: PLC0415

        given = json.loads(answers.expanduser().read_text(encoding="utf-8"))
        body = {"page": page, "answers": given}
        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/eval/drafts/{run}/ratings", body)
        typer.echo(f"rated {r['rated']} now, {r['total_rated']} of {r['of']} in all;"
                   f" {r['left']} not fully rated")  # fmt: skip

    @eval_app.command("calibration-score")
    def calibration_score(
        run: Annotated[str, typer.Argument(help="The calibration record.")],
        null_run: Annotated[str, typer.Option("--null", help="The null floor run, judged.")],
        reason: Annotated[
            str | None, typer.Option("--reason", help="Why, once the set is confirmed.")
        ] = None,
    ) -> None:
        """The judges' floors, written for every draft run's scores; the key revealed and your
        ratings frozen."""
        with LocalClient(paths()) as c:
            r = c.request("POST", f"/v1/eval/drafts/{run}/calibration",
                          {"null_run": null_run, "reason": reason})  # fmt: skip
        typer.echo(f"fields that count: {', '.join(r['passed']) or 'none'} (calibration"
                   f" {str(r['digest'])[:12]})")  # fmt: skip
        for j, fs in sorted(r["judges"].items()):
            typer.echo(f"  {j}: counts on {', '.join(fs) or 'nothing'}")
        for f, js in sorted(r["settle_all"].items()):
            typer.echo(f"  under the {f} floor: {', '.join(js)}; you settle {f} on their passes")
        if r["both_failed"]:
            typer.echo(f"both judges under the floor on {', '.join(r['both_failed'])}: scores stay"
                       " provisional until you decide (M8 plan §4)")  # fmt: skip
        rep = r["reports"]
        caught = ", ".join(f"{f} {rep['operator_caught'][f]}/{rep['seeds'][f]}"
                           for f in rep["seeds"] if rep["seeds"][f])  # fmt: skip
        typer.echo(f"your catch of the seeded faults: {caught}")
        for j, got in sorted(rep["judges_caught"].items()):
            typer.echo(f"  {j}: " + ", ".join(f"{f} {got[f]}/{rep['seeds'][f]}"
                                              for f in rep["seeds"] if rep["seeds"][f])
                       + f"; false yes on {rep['negative_seeds']} negative seeds:"
                       f" {rep['false_yes'][j]}")  # fmt: skip

    @eval_app.command("settle-drafts")
    def settle_drafts(
        runs: Annotated[
            list[str] | None, typer.Argument(help="Judged draft runs (one page across them).")
        ] = None,
        export: Annotated[
            Path | None, typer.Option("--export", help="Write the settle page's data here.")
        ] = None,
        answers: Annotated[
            Path | None, typer.Option("--from", help="The page's saved answers, to import.")
        ] = None,
        page: Annotated[str | None, typer.Option("--page", help="The settle page's id.")] = None,
    ) -> None:
        """What goes to you on judged draft runs (M8 plan §4 "Settling"): --export writes one
        blind page across the runs named; --from with --page imports what you settled, then fills
        each run's record with its scores."""
        import json  # noqa: PLC0415

        with LocalClient(paths()) as c:
            if export is not None:
                if not runs:
                    raise InvalidInputError("name the judged runs to settle")
                r = c.request("POST", "/v1/eval/drafts/settle-page", {"runs": runs})
                out = export.expanduser()
                _write_private(out / "draft-settle-page.json", json.dumps(r))
                typer.echo(f"{len(r['items'])} drafts to settle: wrote"
                           f" {out / 'draft-settle-page.json'}, page {r['page']}")  # fmt: skip
                return
            if answers is None or page is None:
                raise InvalidInputError("give --export, or --from with --page")
            given = json.loads(answers.expanduser().read_text(encoding="utf-8"))
            body = {"page": page, "answers": given}
            r = c.request("POST", "/v1/eval/drafts/settled", body)
            # every run on the page is recorded, whatever was settled now (L3)
            recorded = [c.request("POST", f"/v1/eval/drafts/{run}/record", {})["recorded"]
                        for run in r["runs"]]  # fmt: skip
        typer.echo(f"settled {r['settled']}, {r['left']} not fully answered; recorded"
                   f" {sum(recorded)} scored drafts in {len(recorded)} runs")  # fmt: skip
        if r["stale"]:
            typer.echo(f"{r['stale']} drafts now need fields this page didn't ask (judged or"
                       " calibrated since): make a new page with --export")  # fmt: skip
