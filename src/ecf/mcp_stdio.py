"""The `ecf-mcp` stdio MCP server (SPEC §10.4; V1.4 step 4), run by `ecf claude`'s session.

Logging goes to stderr before anything else is imported, so stdout carries only JSON-RPC (the
SDK's stdio transport also points fd 1 at stderr while it serves). The socket is `ECF_SOCKET`
(written into the session's MCP config by `ecf claude`) or the named install's; the profile
token is `ECF_PROFILE_TOKEN` (none: OBSERVE).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    import logging  # noqa: PLC0415

    from ecf.log import configure_logging  # noqa: PLC0415

    configure_logging("cli", level=logging.INFO)

    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="ecf-mcp", description="ecf's stdio MCP server")
    parser.add_argument("--stdio", action="store_true", required=True, help="serve over stdio")
    parser.add_argument("--install", default="default", help="the ecf install (default: default)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    import anyio  # noqa: PLC0415

    anyio.run(_serve, args.install)


async def _serve(install: str) -> None:
    import os  # noqa: PLC0415

    import httpx  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    from ecf import mcp_server  # noqa: PLC0415
    from ecf.paths import paths_for  # noqa: PLC0415

    socket = str(paths_for(install).socket)
    token = os.environ.get("ECF_PROFILE_TOKEN", "")
    transport = httpx.AsyncHTTPTransport(uds=socket)
    async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as http:
        server = mcp_server.build(mcp_server.Service(http, token))
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
