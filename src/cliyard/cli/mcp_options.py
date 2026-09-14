"""``cliyard mcp`` 命令的共享 click options。

``cliyard mcp <spec-dir>``（:mod:`cliyard.cli.mcp`）与生成 CLI 的 ``mcp``
子命令（:mod:`cliyard.runtime.mcp_command`）复用同一组 10 个 option，集中定义
避免两处重复，保证选项面与语义一致。

用法（挂在 ``@click.command()`` 之下、命令函数之上）::

    @click.command()
    @click.argument("spec_dir", ...)
    @mcp_options
    def mcp(spec_dir, transport, host, port, server, token, allow_remote_no_auth,
            upload_dir, upload_base_url, file_allow_dirs, mcp_tool_mode):
        ...
"""

from __future__ import annotations

from typing import Any, Callable

import click

from cliyard.server.uploads import DEFAULT_UPLOAD_DIR

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
_OPT_UPLOAD_DIR = click.option(
    "--upload-dir",
    default=DEFAULT_UPLOAD_DIR,
    show_default=True,
    metavar="DIR",
    help="Directory for MCP file uploads (server-side handoff storage)",
)
_OPT_UPLOAD_BASE_URL = click.option(
    "--upload-base-url",
    default=None,
    metavar="URL",
    help="Public base URL for upload instructions (default: http://<host>:<port>)",
)
_OPT_FILE_ALLOW_DIRS = click.option(
    "--file-allow-dirs",
    multiple=True,
    default=(),
    metavar="DIR",
    help="Extra server-readable directories (repeatable)",
)
_OPT_MCP_TOOL_MODE = click.option(
    "--mcp-tool-mode",
    type=click.Choice(["flat", "grouped"]),
    default="flat",
    show_default=True,
    help="MCP tool layout: flat (one tool per method) or grouped (one tool per resource)",
)


def _apply_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """依序应用 10 个共享 option（transport 最外层 → 帮助最先显示）。

    click 装饰器自外向内收集 ``__click_params__``：新 option 最先应用
    （最内层 → 帮助末尾），随后是上传三选项，再是既有 6 个，最后应用
    transport，即 transport 在最外层。
    """
    f = _OPT_MCP_TOOL_MODE(f)
    f = _OPT_FILE_ALLOW_DIRS(f)
    f = _OPT_UPLOAD_BASE_URL(f)
    f = _OPT_UPLOAD_DIR(f)
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
