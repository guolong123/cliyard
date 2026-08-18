"""``cliyard mcp`` — 把 spec 作为 MCP Server 启动（stdio / Streamable HTTP）。

Usage::

    cliyard mcp ./examples/demo                 # stdio（Claude Desktop 等）
    cliyard mcp ./examples/demo --transport http --port 8081
    cliyard mcp ./examples/demo --server https://prod.example.com

需要 ``cliyard[mcp]`` 可选依赖（官方 mcp Python SDK）。
"""

from __future__ import annotations

import click

from cliyard.cli.mcp_options import mcp_options
from cliyard.server.mcp.server import run_mcp_server


@click.command()
@click.argument(
    "spec_dir",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@mcp_options
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
