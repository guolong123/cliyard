"""``mcp`` sub-command attached by ``create_cli`` to generated CLIs.

镜像 :mod:`cliyard.runtime.server_command` 的模式：闭包捕获 ``create_cli`` 传入
的 spec 目录，下游 CLI（如 ketacli）可裸调 ``<cli> mcp`` 把自身作为 MCP Server
暴露（stdio 默认，``--transport http`` 可选）。
"""

from __future__ import annotations

import click

from cliyard.cli.mcp_options import mcp_options
from cliyard.server.mcp.server import run_mcp_server


def build_mcp_command(spec_dir: str) -> click.Command:
    """Build the ``mcp`` sub-command bound to *spec_dir*.

    Args:
        spec_dir: Spec directory captured by ``create_cli``.

    Returns:
        A ``click.Command`` that starts this CLI as an MCP server.
    """

    @click.command(
        name="mcp",
        help="Start this CLI as an MCP server (stdio or Streamable HTTP)",
    )
    @mcp_options
    def mcp_cmd(
        transport: str,
        host: str,
        port: int,
        server: str | None,
        token: str | None,
        allow_remote_no_auth: bool,
    ) -> None:
        """Start this CLI's spec as an MCP server."""
        run_mcp_server(
            spec_dir,
            transport=transport,
            host=host,
            port=port,
            server_override=server,
            token=token,
            allow_remote_no_auth=allow_remote_no_auth,
        )

    return mcp_cmd
