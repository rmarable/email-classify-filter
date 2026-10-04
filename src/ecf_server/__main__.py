"""The `ecf-server` entry point: `local` (the service), `migrate`, `reset-breaker`, `regrant`,
`snapshot` (an upgrade's database copy), `downgrade-prepare` (going back after one), `destroy`
(its service part, when the service can't run)."""

from __future__ import annotations

import argparse
import sys

from ecf import __version__
from ecf.errors import EcfError
from ecf.paths import Paths, paths_for


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ecf-server")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("local", "run the local service"),
        ("dev", "run a throwaway development service (fake clock, fake chat, memory secrets)"),
        ("migrate", "apply database migrations and exit"),
        ("reset-breaker", "clear the crash-loop breaker (used by `ecf service start`)"),
        (
            "regrant",
            "let this Python read ecf's Keychain items again (used by `ecf service regrant`)",
        ),
        ("snapshot", "copy the database before an upgrade (used by `ecf upgrade`)"),
        ("downgrade-prepare", "build the database for going back (used by `ecf upgrade --to`)"),
        ("destroy", "the service's part of a destroy, in the foreground (used by `ecf destroy`)"),
    ):
        _add_arguments(sub.add_parser(name, help=help_text), name)
    args = parser.parse_args(argv)
    import re  # noqa: PLC0415

    from ecf.ids import SLUG_PATTERN  # noqa: PLC0415

    if not re.fullmatch(SLUG_PATTERN, args.install):
        parser.error("--install: lowercase letters, digits and hyphens, at most 40")
    paths = paths_for(args.install, for_service=True)
    try:
        if args.command == "dev":
            raise SystemExit(_run_dev(args))
        if args.command == "local":
            from ecf_server.service import Options, Service  # noqa: PLC0415

            opts = Options()
            if args.tick_seconds is not None:
                opts = Options(args.tick_seconds, args.watchdog_seconds or opts.watchdog_seconds)
            raise SystemExit(Service(paths, opts=opts).run())
        if args.command in ("snapshot", "downgrade-prepare"):
            sys.stdout.write(f"{_upgrade_files(args.command, paths, args.label)}\n")
            return
        if args.command in ("regrant", "destroy"):
            raise SystemExit(_foreground(args))
        if args.command == "migrate":
            from ecf_server import db  # noqa: PLC0415

            conn = db.connect(paths.db)
            applied = db.migrate(conn)
            conn.close()
            sys.stdout.write(f"applied: {', '.join(applied) or 'none'}\n")
            return
        from ecf_server import breaker  # noqa: PLC0415

        paths.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        breaker.reset(paths.crash_state)
    except EcfError as exc:
        sys.stderr.write(f"ecf-server: {exc.detail}\n")
        raise SystemExit(int(exc.exit_code)) from exc


def _add_arguments(p: argparse.ArgumentParser, name: str) -> None:
    p.add_argument("--install", default="dev" if name == "dev" else "default")
    if name == "dev":
        p.add_argument("--home", help="data root to use (default: a new /tmp folder)")
        p.add_argument("--keep", action="store_true", help="keep the data folder on exit")
    if name in ("snapshot", "downgrade-prepare"):
        p.add_argument("--label", required=True, help="e.g. 0.1.0-to-0.1.1")
    if name == "destroy":
        p.add_argument("--confirm", required=True, help="the install name, typed again")
        p.add_argument(
            "--config-token",
            action="store_true",
            help="ask for a Slack configuration token to delete the app",
        )
    if name in ("local", "dev"):
        p.add_argument("--foreground", action="store_true")
        p.add_argument("--tick-seconds", type=float, default=None, help=argparse.SUPPRESS)
        p.add_argument("--watchdog-seconds", type=float, default=None, help=argparse.SUPPRESS)


def _foreground(args: argparse.Namespace) -> int:
    """`regrant` and `destroy`: run with a person at the terminal, while the service is stopped."""
    if args.command == "regrant":
        from ecf_server import regrant  # noqa: PLC0415

        return regrant.main(args.install)
    from ecf_server import destroy  # noqa: PLC0415

    return destroy.main(args.install, args.confirm, ask_token=args.config_token)


def _upgrade_files(command: str, paths: Paths, label: str) -> object:
    """`snapshot` (before an upgrade) and `downgrade-prepare` (going back after one)."""
    if command == "snapshot":
        from ecf_server import upgrade_snapshot  # noqa: PLC0415

        return upgrade_snapshot.take(paths, label)
    from ecf_server import downgrade  # noqa: PLC0415

    return downgrade.prepare(paths, label)


def _run_dev(args: argparse.Namespace) -> int:
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from ecf.paths import Paths  # noqa: PLC0415
    from ecf_server.clock import FakeClock  # noqa: PLC0415
    from ecf_server.service import Options, Service  # noqa: PLC0415

    root = Path(args.home) if args.home else Path(tempfile.mkdtemp(prefix="ecf-dev", dir="/tmp"))
    paths = Paths(args.install, root, honor_ecf_socket=False)
    sys.stderr.write(
        f"ecf-server dev: data in {paths.data_dir}\n"
        f"  point the CLI at it:  export ECF_SOCKET={paths.socket}\n"
        f"  (or ECF_HOME={root} with --install {args.install})\n"
    )
    opts = Options()
    if args.tick_seconds is not None:
        opts = Options(args.tick_seconds, args.watchdog_seconds or opts.watchdog_seconds)
    try:
        return Service(paths, clock=FakeClock(), opts=opts, dev=True).run()
    finally:
        if not args.keep and not args.home:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
