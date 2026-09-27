"""The `ecf-server` entry point: `local` (the service), `migrate`, `reset-breaker`."""

from __future__ import annotations

import argparse
import sys

from ecf import __version__
from ecf.errors import EcfError
from ecf.paths import paths_for


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ecf-server")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("local", "run the local service"),
        ("migrate", "apply database migrations and exit"),
        ("reset-breaker", "clear the crash-loop breaker (used by `ecf service start`)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--install", default="default")
        if name == "local":
            p.add_argument("--foreground", action="store_true")
            p.add_argument("--tick-seconds", type=float, default=None, help=argparse.SUPPRESS)
            p.add_argument("--watchdog-seconds", type=float, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    paths = paths_for(args.install)
    try:
        if args.command == "local":
            from ecf_server.service import Options, Service  # noqa: PLC0415

            opts = Options()
            if args.tick_seconds is not None:
                opts = Options(args.tick_seconds, args.watchdog_seconds or opts.watchdog_seconds)
            raise SystemExit(Service(paths, opts=opts).run())
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


if __name__ == "__main__":
    main()
