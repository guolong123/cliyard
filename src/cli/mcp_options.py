"""``cliyard mcp`` 命令的共享 click options。

``cliyard mcp <spec-dir>``（:mod:`cliyard.cli.mcp`）与生成 CLI 的 ``mcp``
子命令（:mod:`cliyard.runtime.mcp_command`）复用同一组 6 个 option，集中定义
避免两处重复（87 行），保证选项面与语义一致。

用法（挂在 ``@click.command()`` 之下、命令函数之上）::

    @click.command()
    @click.argument("spec_dir", ...)
    @mcp_options
    def mcp(spec_dir, transport, host, port, server, token, allow_remote_no_auth):
        ...
"""

from __future__ import annotations

from typing import Any, Callable

import click

_OPT_TRANSPORT = click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default="stdio",
    show_default=True,
    help="MCP transport: stdio (default) or Streamable HTTP",
)
_OPT_HOST = click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind host address (http transport)",
)
_OPT_PORT = click.option(
    "--port",
    default=8081,
    type=int,
    show_default=True,
    help="Bind port (http transport)",
)
_OPT_SERVER = click.option(
    "--server",
    "-s",
    default=None,
    metavar="URL",
    help="Override server base URL (default: $<SERVICE>_SERVER / $CLIYARD_SERVER or spec base_url)",
)
_OPT_TOKEN = click.option(
    "--token",
    default=None,
    metavar="TOKEN",
    help="Bearer token for http transport (required when binding non-localhost)",
)
_OPT_ALLOW_REMOTE_NO_AUTH = click.option(
    "--allow-remote-no-auth",
    is_flag=True,
    default=False,
    help="Allow http transport on non-localhost without --token (not recommended)",
)


def _apply_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """依序应用 6 个共享 option（transport 最外层 → 帮助最先显示）。

    click 装饰器自外向内收集 ``__click_params__``：这里先应用 allow-remote、
    token、server、port、host，最后应用 transport，即 transport 在最外层。
    """
    f = _OPT_ALLOW_REMOTE_NO_AUTH(f)
    f = _OPT_TOKEN(f)
    f = _OPT_SERVER(f)
    f = _OPT_PORT(f)
    f = _OPT_HOST(f)
    f = _OPT_TRANSPORT(f)
    return f


def mcp_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """给 MCP 命令函数挂载共享 options（挂在命令函数上）。"""
    return _apply_options(f)
