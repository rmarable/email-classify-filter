"""`ecf eval generate`, `grade`, `expect` and `report`: rate ecf with other models
(planning-docs/EVAL-GENERATE-GRADE.md). The flow: generate, grade, label (blind), expect, run,
report. Generation and grading run in the client; `expect` asks the service's rules engine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

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
}  # fmt: skip
NEVER_REAL = "Never put real mail in a generated set."


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
        seeds_only: Annotated[
            bool, typer.Option("--seeds-only", help="A seed run: seeds for every group, no email.")
        ] = False,
        seeds_from: Annotated[
            Path | None,
            typer.Option(
                "--seeds-from", help="Write emails from a seed run's seeds (no seed call)."
            ),
        ] = None,
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
        mode = "dev" if dev else "prod"
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
            if doc["mode"] != mode:
                raise InvalidInputError(f"{root} is a {doc['mode']} set: resume it the same way")
            m = mixmod.parse(doc["mix"], source="manifest")
            n = int(doc["count"])
            generator = generator or f"{doc['generator']['provider']}:{doc['generator']['model']}"
        else:
            m = mixmod.load(mix_file) if mix_file else mixmod.default()
            n = count if count is not None else (30 if dev else 300)
        adapter = providers.resolve(generator, "generator", mode, paths())
        _notice("generator", adapter.provider, yes)
        _checked(adapter)
        gen.start(root.expanduser(), resume=resume)
        if seeds_from is not None:
            shared = gen.share_seeds(root.expanduser(), seeds_from.expanduser().resolve(), mode,
                                     n, m, k)  # fmt: skip
        run = gen.Run(root.expanduser(), m, n, adapter, mode, k=k, seeds_only=seeds_only,
                      shared=shared, echo=typer.echo)  # fmt: skip
        s = run.run(resume=resume or shared is not None)
        typer.echo(f"{s.state}: kept {s.kept} of {s.target}; refused {sum(s.refused.values())};"
                   f" dropped {sum(s.dropped.values())}")  # fmt: skip
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
    ) -> None:
        """Have a model from another family label the set blind; written as a result file."""
        from ecf.eval import generate as gen  # noqa: PLC0415
        from ecf.eval import grade as grading  # noqa: PLC0415
        from ecf.eval import mistral_api, models, providers  # noqa: PLC0415

        doc = gen.read_manifest(root)
        mode = str(doc.get("mode"))
        adapter = providers.resolve(grader, "grader", mode, paths())
        g = doc["generator"]
        models.check_roles((g["provider"], g["model"]), (adapter.provider, adapter.model), mode)
        _notice("grader", adapter.provider, yes)
        if isinstance(adapter, mistral_api.MistralAdapter):
            if max_cost <= 0:
                raise InvalidInputError("--max-cost must be more than 0")
            adapter.max_cost = max_cost
            _estimate(root, adapter, yes)
        _checked(adapter)
        path, counts, detail = grading.Grading(root, adapter, echo=typer.echo).run(resume=resume)
        typer.echo(", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing graded")
        typer.echo(detail if path is None else f"wrote {path}")

    @eval_app.command("agree")
    def agree(root: Annotated[Path, typer.Option("--root", help="A generated set.")]) -> None:
        """Set the labels from the judges' grades: where they agree, the label is theirs; where
        they don't, the email waits for you (`ecf eval settle`)."""
        from datetime import UTC, datetime  # noqa: PLC0415

        from ecf.eval import agree as ag  # noqa: PLC0415

        o = ag.run(root.expanduser().resolve(), datetime.now(UTC).date())
        typer.echo(f"agreed: {o.agreed} emails confirmed by the judges; for you: {o.disputed}"
                   f" ({o.whole} whole, a judge refused or skipped them); already confirmed:"
                   f" {o.skipped}")  # fmt: skip
        if o.fields:
            typer.echo("disputed fields: " + ", ".join(f"{k} {v}" for k, v in
                                                       o.fields.most_common()))  # fmt: skip

    @eval_app.command("expect")
    def expect(root: Annotated[Path, typer.Option("--root", help="A generated set.")]) -> None:
        """Fill each labelled card's expected rule and safety from ecf's rules engine; confirm
        them with `ecf eval label`."""
        from ecf.eval import expect as ex  # noqa: PLC0415
        from ecf.eval import generate as gen  # noqa: PLC0415

        where = root.expanduser().resolve()
        gen.verify(where)
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/eval/expect", {"root": str(where)})
        counts = ex.apply(where, list(r["cases"]))
        typer.echo(", ".join(f"{k} {v}" for k, v in counts.items()))
        typer.echo(f"Confirm each with `ecf eval label --root {root}`.")

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
