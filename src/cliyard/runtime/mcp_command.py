"""``mcp`` sub-command attached by ``create_cli`` to generated CLIs.

镜像 :mod:`cliyard.runtime.server_command` 的模式：闭包捕获 ``create_cli`` 传入
的 spec 目录，下游 CLI（如 ketacli）可裸调 ``<cli> mcp`` 把自身作为 MCP Server
暴露（stdio 默认，``--transport http`` 可选）。
"""

from __future__ import annotations

import click

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
