"""The `ecf-mcp` stdio MCP server (SPEC §10.4; V1.4 step 4), run by `ecf claude`'s session.

Logging goes to stderr before anything else is imported, so stdout carries only JSON-RPC (the
SDK's stdio transport also points fd 1 at stderr while it serves). The socket is `ECF_SOCKET`
(written into the session's MCP config by `ecf claude`), `--socket`, or the named install's; the
profile token is `ECF_PROFILE_TOKEN` (none: OBSERVE). With `--agent <name>` it is that agent's
server (OD-307): the agent token `ECF_AGENT_TOKEN`, inherited from the session's environment
(Claude Code doesn't expand variables in an agent's server definition, tested 2026-10-02).
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
    parser.add_argument("--socket", help="the service's socket (default: the install's)")
    parser.add_argument("--agent", help="serve this ecf agent's tools only")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    from ecf import mcp_server  # noqa: PLC0415

    if args.agent is not None and not mcp_server.AGENT_NAME.match(args.agent):
        parser.error(f"not an ecf agent: {args.agent!r}")

    import anyio  # noqa: PLC0415

    anyio.run(_serve, args.install, args.socket, args.agent)


async def _serve(install: str, socket: str | None, agent: str | None) -> None:
    import os  # noqa: PLC0415

    import httpx  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    from ecf import mcp_server  # noqa: PLC0415
    from ecf.paths import paths_for  # noqa: PLC0415

    socket = socket or str(paths_for(install).socket)
    token = os.environ.get("ECF_AGENT_TOKEN" if agent else "ECF_PROFILE_TOKEN", "")
    transport = httpx.AsyncHTTPTransport(uds=socket)
    async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as http:
        server = mcp_server.build(mcp_server.Service(http, token, agent=agent))
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
