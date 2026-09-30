"""`ecf stage`, `ecf sensitivity`, `ecf settings` (SPEC §9.1, §9.4, §14; V1.2 step 10a), `ecf config
apply` and `ecf rules test` (SPEC §8.6, §9.7; step 10b)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf.client import LocalClient
from ecf.paths import Paths
from ecf.stepup import with_step_up


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    stage_app = typer.Typer(no_args_is_help=True, help="Watching only, labels only, or full.")
    app.add_typer(stage_app, name="stage")
    sens_app = typer.Typer(no_args_is_help=True, help="standard or high (extra checks).")
    app.add_typer(sens_app, name="sensitivity")
    settings_app = typer.Typer(no_args_is_help=True, help="Install and per-address settings.")
    app.add_typer(settings_app, name="settings")
    config_app = typer.Typer(no_args_is_help=True, help="Security-relevant configuration.")
    app.add_typer(config_app, name="config")
    rules_app = typer.Typer(no_args_is_help=True, help="Try rules before applying them.")
    app.add_typer(rules_app, name="rules")
    _config_commands(config_app, rules_app, paths)

    @stage_app.command("status")
    def stage_status() -> None:
        """Each address's stage, how long it has been there, and what's held."""
        with LocalClient(paths()) as c:
            rows: list[dict[str, Any]] = c.get("/v1/stages")["addresses"]
        for r in rows:
            paused = ", PAUSED" if r["paused"] else ""
            typer.echo(f"{r['address_id']:<16} {r['stage']:<7} ({r['label']}) for {r['days']} "
                       f"day(s), {r['sensitivity']}{paused}; held: {r['held']}")  # fmt: skip
        if rows:
            typer.echo(f"live: {rows[0]['gate']}")
        else:
            typer.echo("no addresses yet")

    @stage_app.command("set")
    def stage_set(
        address: Annotated[str, typer.Argument(help="Address id or email.")],
        stage: Annotated[str, typer.Argument(help="shadow or assist (live arrives in V1.3).")],
        reason: Annotated[str, typer.Option("--reason", help="Why (kept in the audit log).")] = "",
    ) -> None:
        """Change an address's stage. (step-up to move forward; going back is instant)"""
        body = {"value": stage, "reason": reason}
        with LocalClient(paths()) as c:
            r = with_step_up(c, lambda n: c.request("POST", f"/v1/addresses/{address}/stage",
                                                    body | {"nonce_id": n}),
                             echo=typer.echo)  # fmt: skip
        typer.echo(f"{r['address_id']}: {r['stage']}" + ("" if r["changed"] else " (unchanged)"))

    @sens_app.command("set")
    def sensitivity_set(
        address: Annotated[str, typer.Argument(help="Address id or email.")],
        level: Annotated[str, typer.Argument(help="standard or high.")],
        reason: Annotated[
            str, typer.Option("--reason", help="Why; required to lower it (audited).")
        ] = "",
    ) -> None:
        """Raise sensitivity at once, or lower it. (step-up and a reason to lower)"""
        body = {"value": level, "reason": reason}
        with LocalClient(paths()) as c:
            r = with_step_up(c, lambda n: c.request("POST", f"/v1/addresses/{address}/sensitivity",
                                                    body | {"nonce_id": n}),
                             echo=typer.echo)  # fmt: skip
        typer.echo(f"{r['address_id']}: {r['sensitivity']}"
                   + ("" if r["changed"] else " (unchanged)"))  # fmt: skip

    @settings_app.command("show")
    def settings_show(
        address: Annotated[str | None, typer.Option("--address", help="One address.")] = None,
    ) -> None:
        """Settings you can change with `ecf settings set`, with their values."""
        with LocalClient(paths()) as c:
            rows = c.get("/v1/settings" + (f"?address_id={address}" if address else ""))
        for r in rows["settings"]:
            note = " (after a service restart)" if r["restart"] else ""
            typer.echo(f"{r['key']:<30} {r['value']}{note}")

    @settings_app.command("set")
    def settings_set(
        key: Annotated[str, typer.Argument(help="A key from `ecf settings show`.")],
        value: Annotated[str, typer.Argument(help="The new value.")],
        address: Annotated[str | None, typer.Option("--address", help="One address.")] = None,
    ) -> None:
        """Change a setting (for one address with --address)."""
        with LocalClient(paths()) as c:
            if key == "slack_member_id":  # the same step-up and confirmation as set-member
                with_step_up(c, lambda n: c.request("POST", "/v1/slack/member",
                                                    {"member": value, "stepup_nonce": n}),
                             echo=typer.echo)  # fmt: skip
                typer.echo(f"ecf sent {value} a DM; clicks stay with the old ID until it's"
                           " confirmed.")  # fmt: skip
                return
            r = c.request("POST", "/v1/settings",
                          {"key": key, "value": value, "address_id": address})  # fmt: skip
        where = f" for {r['address_id']}" if r["address_id"] else ""
        after = " (takes effect when the service restarts: ecf service restart)" if r["restart"] \
            else ""  # fmt: skip
        typer.echo(f"{r['key']} = {r['value']}{where}{after}")


def _config_commands(
    config_app: typer.Typer, rules_app: typer.Typer, paths: Callable[[], Paths]
) -> None:
    @config_app.command("apply")
    def config_apply(
        file: Annotated[Path, typer.Argument(help="A YAML file with `version: 1` and sections.")],
        yes: Annotated[bool, typer.Option("--yes", help="Don't ask before applying.")] = False,
    ) -> None:
        """Apply org domains, allow-lists, action policy, rules or templates. (step-up)"""
        text = file.read_text(encoding="utf-8")
        with LocalClient(paths()) as c:
            plan = c.request("POST", "/v1/config/apply", {"document": text, "dry_run": True})
            if not plan["changed"]:
                typer.echo("nothing changes")
                return
            for ch in plan["changes"]:
                typer.echo(f"{ch['section']}: {ch['change']}")
            if not yes and not typer.confirm("Apply these changes?", default=False):
                raise typer.Exit(1)
            with_step_up(c, lambda n: c.request("POST", "/v1/config/apply",
                                                {"document": text, "nonce_id": n}),
                         echo=typer.echo)  # fmt: skip
        typer.echo("applied (a Security Notice was sent)")

    @rules_app.command("test")
    def rules_test(
        file: Annotated[Path, typer.Argument(help="A rules file (SPEC §8.6).")],
        cases: Annotated[
            Path, typer.Option("--cases", help="The synthetic set folder (with labels.jsonl).")
        ] = Path("tests/eval/synthetic"),
        all_cases: Annotated[bool, typer.Option("--all", help="List unchanged cases too.")] = False,
    ) -> None:
        """Run proposed rules on the synthetic set; show which outcomes change."""
        body = {"rules": file.read_text(encoding="utf-8"), "cases_dir": str(cases.resolve())}
        with LocalClient(paths()) as c:
            r = c.request("POST", "/v1/rules/test", body, timeout=300.0)
        for case in r["cases"]:
            if not (case["changed"] or all_cases):
                continue
            mark = "CHANGED" if case["changed"] else "same"
            typer.echo(f"{mark:<8}{case['id']} (expected rule: {case['expected_rule'] or '-'})")
            for which in ("current", "proposed"):
                o = case[which]
                what = o.get("error") or f"{o['rule']}: {', '.join(o['actions']) or 'no actions'}"
                typer.echo(f"        {which:<9}{what}" + (" -> actor" if o["actor"] else ""))
        m = r["expected_matched"]
        typer.echo(f"{r['changed']} of {len(r['cases'])} case(s) change; expected rule matched:"
                   f" current {m['current']}, proposed {m['proposed']} of"
                   f" {r['with_expected_rule']}")  # fmt: skip
        if r["skipped"]:
            typer.echo(f"skipped (not built; run `ecf eval build`): {', '.join(r['skipped'])}")
