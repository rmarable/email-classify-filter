"""`ecf upgrade` (SPEC §11.10; OD-310, OD-374 to OD-382; V1.5 step 11a: the checks; release source
per operator decision D7, 2026-10-06).

**Where the new version comes from:** `ecf upgrade` with no arguments takes the newest stable
GitHub release newer than this one, and `ecf upgrade --to vX.Y.Z[-rcN]` a named one (release
candidates only by name); both download it with `gh` and verify it (release_source.py) into
`<data>/releases/<tag>/`, where it stays as the installed wheel. `--to` with an *older* version
goes back to this install's snapshot from that upgrade (step 11c), never to a download.
`--wheel <file>` upgrades a test install from a local file (a `prod` install refuses it; OD-374),
optionally checked against a `SHA256SUMS` file (`--sha256sums`; an integrity check only, so prod
still refuses it). Before anything stops, `--check` (and every upgrade) checks: this ecf runs from
a `uv tool` environment whose wheel is still there (OD-380); the wheel's `release.json` against
this install (OD-375), and for a release, that it names the release's version; nothing
executing, no lease held, no `ecf claude` session and no `ecf watch` (OD-379); and lists model
pins that change and the addresses that will drop to `assist` (OD-381).
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from ecf import __version__, release_source, upgrade_check, upgrade_run, watch
from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf.service_unit import manager_for


def _gh() -> release_source.Gh:
    """The GitHub CLI; tests replace this."""
    return release_source.Gh()


def make_commands(app: typer.Typer, paths: Callable[[], Paths]) -> None:
    @app.command("upgrade")
    def upgrade_command(
        wheel: Annotated[
            str | None, typer.Option("--wheel", help="A wheel file (test installs).")
        ] = None,
        sums: Annotated[
            str | None,
            typer.Option("--sha256sums", help="Check --wheel against this SHA256SUMS file."),
        ] = None,
        to: Annotated[
            str | None,
            typer.Option(
                "--to", help="A release tag (vX.Y.Z), or an earlier version to go back to."
            ),
        ] = None,
        check: Annotated[bool, typer.Option("--check", help="Only run the checks.")] = False,
        cont: Annotated[bool, typer.Option("--continue", hidden=True)] = False,
    ) -> None:
        """Upgrade ecf to the newest release (or --to a named one), or go back with --to."""
        if cont:  # phase 2, run by the new version (upgrade_run.py)
            _continue(paths())
            return
        if sums is not None and wheel is None:
            raise InvalidInputError("--sha256sums checks a --wheel file")
        if wheel is not None and to is not None:
            raise InvalidInputError("use --wheel or --to, not both")
        tag: str | None = None
        if wheel is not None:
            new = Path(os.path.expanduser(wheel)).absolute()
            if sums is not None:
                release_source.check_sums_file(new, Path(os.path.expanduser(sums)))
                typer.echo(f"{new.name} matches {Path(sums).name}")
        else:
            back, tag = _target(to)
            if back is not None:
                _back(paths(), back)
                return
            got = _download(paths(), tag)
            if got is None:
                return
            new, tag = got
        rel, report, problems = preflight(paths(), new,
                                          source="wheel" if tag is None else "release")  # fmt: skip
        if tag is not None and rel.version != release_source.tag_version(tag):
            problems.append(f"the wheel's release.json says {rel.version}, not"
                            f" {release_source.tag_version(tag)}")  # fmt: skip
        _print(rel, report, problems)
        if problems:
            raise typer.Exit(1)
        if check:
            return
        if not typer.confirm(f"Upgrade to {rel.version}? The service stops for a minute or two.",
                             default=False):  # fmt: skip
            raise typer.Exit(1)
        p = paths()
        old = upgrade_check.this_install().wheel
        assert old is not None  # noqa: S101 - preflight refused otherwise
        upgrade_run.start(p, _tools(p), old_version=__version__, new_version=rel.version,
                          new_wheel=rel.wheel, old_wheel=old, pin_changes=report.pin_changes,
                          affected=report.affected)  # fmt: skip


def _target(to: str | None) -> tuple[str | None, str | None]:
    """(an older version to go back to, None) or (None, the release tag to fetch; None for the
    newest). Going back takes a tag or a version (snapshot folders are named by version); going
    forward takes only a release tag."""
    if to is None:
        return None, None
    version = release_source.tag_version(to) if to.startswith("v") else to
    order, now = upgrade_check.version_key(version), upgrade_check.version_key(__version__)
    if order < now:
        return version, None
    if order == now:
        raise InvalidInputError(f"ecf {__version__} is already installed")
    if not release_source.is_tag(to):
        want = release_source.version_tag(version)
        raise InvalidInputError(
            "--to takes a release tag to upgrade to"
            + (f" ({want})" if want else "; use --wheel for a build")
        )
    return None, to


def _download(p: Paths, tag: str | None) -> tuple[Path, str] | None:
    """Pick (when `tag` is None), download and verify a release; None when nothing is newer."""
    gh = _gh()
    if tag is None:
        found = release_source.newest(release_source.list_releases(gh), __version__)
        if found is None:
            typer.echo(f"ecf {__version__} is the newest release; nothing to upgrade to")
            return None
        tag = found.tag
    typer.echo(f"downloading {tag} from {gh.repo}")
    releases = p.data_dir / "releases"
    _prune(releases, keep=tag)
    wheel = release_source.fetch(gh, tag, releases / tag)
    typer.echo(f"{wheel.name}: sha256 matches the release's manifest and SHA256SUMS")
    return wheel, tag


def _prune(releases: Path, *, keep: str) -> None:
    """Earlier downloads, except the one this ecf was installed from (kept for a rollback)."""
    if not releases.is_dir():
        return
    installed = upgrade_check.this_install().wheel
    for d in releases.iterdir():
        if d.name == keep or (installed is not None and installed.parent == d):
            continue
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def preflight(
    paths: Paths,
    wheel: Path,
    *,
    install: upgrade_check.Install | None = None,
    source: str = "wheel",
) -> tuple[upgrade_check.Release, upgrade_check.Report, list[str]]:
    """Every check that comes before stopping anything; returns the problems (empty: go)."""
    problems: list[str] = []
    why = upgrade_check.install_problem(install or upgrade_check.this_install())
    if why:
        problems.append(why)
    rel = upgrade_check.read_wheel(wheel)
    with LocalClient(paths) as c:
        state: dict[str, Any] = c.get("/v1/upgrade/state")
    if source == "wheel" and state.get("install_role") == "prod":
        problems.append("a prod install upgrades only from published releases (ecf upgrade, or"
                        " ecf upgrade --to vX.Y.Z); --wheel is for test installs")  # fmt: skip
    report = upgrade_check.compare(state, rel, __version__)
    problems += report.problems + busy_problems(paths, state)
    return rel, report, problems


def _back(p: Paths, version: str) -> None:
    """`ecf upgrade --to <version>` (OD-331, OD-382)."""
    if upgrade_run.find_snapshot(p, version, __version__) is None:
        typer.echo(f"there's no copy from {version} here (only versions this install upgraded"
                   " from can be gone back to); otherwise use ecf export, then ecf import under"
                   " the older version", err=True)  # fmt: skip
        raise typer.Exit(1)
    with LocalClient(p) as c:
        state: dict[str, Any] = c.get("/v1/upgrade/state")
    problems = busy_problems(p, state)
    for why in problems:
        typer.echo(f"  can't go back: {why}", err=True)
    if problems:
        raise typer.Exit(1)
    rec: dict[str, Any] = state.get("upgrade") or {}
    settled = not (rec.get("to") == __version__ and rec.get("from") == version
                   and not rec.get("settled_at"))  # fmt: skip
    if settled:
        typer.echo(f"Going back to {version}: the database copy from the upgrade comes back,"
                   " keeping send history, sender records and gate history from now. Mail since"
                   " the upgrade is read again; approvals since then must be decided again;"
                   " anything that was running is marked for you to check; every address is"
                   " paused at assist at most.")  # fmt: skip
    else:
        typer.echo(f"Going back to {version}: the upgrade hasn't settled, so the database as it"
                   " was before the upgrade comes back whole.")  # fmt: skip
    if not typer.confirm("Go back?", default=False):
        raise typer.Exit(1)
    done = upgrade_run.downgrade(p, _tools(p), version=version, current=__version__,
                                 settled=settled)  # fmt: skip
    typer.echo(f"ecf {version} is installed (was {__version__})"
               + ("" if done["answered"] else "; the service didn't answer: see ecf service"
                  " status"))  # fmt: skip
    if settled:
        typer.echo("addresses are paused: check them, then ecf resume <address>")


def busy_problems(p: Paths, state: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    m = watch.marker(p)
    if m and m["alive"]:
        problems.append(f"`ecf watch` is running the service (pid {m['pid']}); stop it first")
    busy = state["busy"]
    if busy["executing"]:
        problems.append(f"{busy['executing']} action(s) are running; wait for them")
    if busy["leases"]:
        problems.append("a mail check is running; try again in a minute")
    if busy["claude_sessions"]:
        problems.append("an `ecf claude` session is open; close it first")
    return problems


def _tools(p: Paths) -> upgrade_run.Tools:
    return upgrade_run.Tools(manager=manager_for(p), echo=typer.echo)


def _continue(p: Paths) -> None:
    done = upgrade_run.resume(p, _tools(p), regrant=sys.platform == "darwin")
    if done["phase"] == "rolled_back":
        typer.echo(f"the upgrade to {done['to']} failed at the {done['why']} step; ecf"
                   f" {done['from']} is back, with its database as it was", err=True)  # fmt: skip
        raise typer.Exit(1)
    typer.echo(f"ecf {done['to']} is running (was {done['from']})")
    if done["affected"]:
        why = ("the classifier schema changed" if "schema" in done.get("pin_changes", [])
               else "model pins changed")  # fmt: skip
        typer.echo(f"{why}: {', '.join(done['affected'])} drop to assist until their gate passes"
                   " again; re-run ecf eval run")  # fmt: skip
    typer.echo("the database copy is kept in case you go back: ecf upgrade --to"
               f" {done['from']}")  # fmt: skip


def _print(rel: upgrade_check.Release, report: upgrade_check.Report, problems: list[str]) -> None:
    typer.echo(f"ecf {__version__} → {rel.version} ({rel.wheel.name})")
    if report.pin_changes:
        typer.echo(f"model pins that change: {', '.join(report.pin_changes)}")
        if report.affected:
            typer.echo(f"  these addresses drop to assist until their gate passes again:"
                       f" {', '.join(report.affected)}; re-run ecf eval run for them")  # fmt: skip
    for p in problems:
        typer.echo(f"  can't upgrade: {p}", err=True)
    if not problems:
        typer.echo("checks passed")
