"""``cliyard mcp`` — 把 spec 作为 MCP Server 启动（stdio / Streamable HTTP）。

Usage::

    cliyard mcp ./examples/demo                 # stdio（Claude Desktop 等）
    cliyard mcp ./examples/demo --transport http --port 8081
    cliyard mcp ./examples/demo --server https://prod.example.com

需要 ``cliyard[mcp]`` 可选依赖（官方 mcp Python SDK）。
"""

from __future__ import annotations

import click

from cliyard.server.mcp.server import run_mcp_server


@click.command()
@click.argument(
    "spec_dir",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    show_default=True,
    help="MCP transport: stdio (default) or Streamable HTTP",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind host address (http transport)",
)
@click.option(
    "--port",
    default=8081,
    type=int,
    show_default=True,
    help="Bind port (http transport)",
)
@click.option(
    "--server",
    "-s",
    default=None,
    metavar="URL",
    help="Override server base URL (default: $<SERVICE>_SERVER / $CLIYARD_SERVER or spec base_url)",
)
@click.option(
    "--token",
    default=None,
    metavar="TOKEN",
    help="Bearer token for http transport (required when binding non-localhost)",
)
@click.option(
    "--allow-remote-no-auth",
    is_flag=True,
    default=False,
    help="Allow http transport on non-localhost without --token (not recommended)",
)
def mcp(
    spec_dir: str,
    transport: str,
    host: str,
    port: int,
    server: str | None,
    token: str | None,
    allow_remote_no_auth: bool,
) -> None:
    """Expose the YAML specs in SPEC_DIR as an MCP server.

    Tools are registered from the spec command/flow tree and reuse the same
    execution kernel as the CLI and web UI. stdio is the default transport
    (zero network, spawned by MCP clients like Claude Desktop); Streamable
    HTTP is available via --transport http.
    """
    run_mcp_server(
        spec_dir,
        transport=transport,
        host=host,
        port=port,
        server_override=server,
        token=token,
        allow_remote_no_auth=allow_remote_no_auth,
    )
