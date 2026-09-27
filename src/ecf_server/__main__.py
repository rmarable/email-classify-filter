"""The `ecf-server` entry point (the local service arrives in V1.0 step 8)."""

import sys

from ecf_server import __version__


def main() -> None:
    args = sys.argv[1:]
    if args == ["--version"]:
        sys.stdout.write(f"{__version__}\n")
        return
    sys.stderr.write("ecf-server: the local service is not built yet (V1.0 step 8)\n")
    raise SystemExit(3)


if __name__ == "__main__":
    main()
