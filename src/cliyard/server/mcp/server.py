"""MCP Server 构建 / 启动 —— stdio 与 Streamable HTTP。

两个入口共用 :func:`create_mcp_server` 构建低层 MCP Server：

* ``cliyard mcp <spec-dir>``（默认 stdio，``--transport http`` 可选）
  → :func:`run_mcp_server`；
* 生成 CLI 的 ``mcp`` 子命令（:mod:`cliyard.runtime.mcp_command`）同样调用
  :func:`run_mcp_server`。

Streamable HTTP 复用 serve 的 launcher 模式（uvicorn 启动 / fail-fast）：
* :func:`build_mcp_http_app` —— 构建可独立启动的 Starlette app；
* :func:`mount_mcp_http` —— 挂载进现有 FastAPI serve（同一端口复用 uvicorn）。
默认绑定 ``127.0.0.1``；``--host`` 非本地（非 localhost）时**强制**要求
``--token`` bearer 鉴权，否则启动失败（除非显式 ``--allow-remote-no-auth``）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
from pathlib import Path
from typing import Any

import click
import uvicorn
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from cliyard.server.mcp.executor import MCPExecutor

# 视为本地环回的绑定地址（不强制鉴权）。仅真正的 loopback 视为本地——
# ``0.0.0.0`` / ``::`` 是全接口通配（可被外部访问），同样强制鉴权。
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def is_local_host(host: str) -> bool:
    """host 是否为本地环回（本地绑定不强制 bearer 鉴权）。"""
    return host in _LOCAL_HOSTS


class _StaticTokenVerifier:
    """静态 bearer token 校验器（Streamable HTTP 鉴权开关用）。

    命中配置 token 返回 :class:`AccessToken`，否则 ``None``（→ 401）。
    用恒定时间比较避免时序侧信道。
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(token, self._token):
            return AccessToken(token=token, client_id="cliyard", scopes=[])
        return None


def display_host(host: str) -> str:
    """Map wildcard binds to loopback for display/template URLs."""
    return "127.0.0.1" if host in ("0.0.0.0", "::") else host


def _auth_settings_for(token: str, host: str, port: int) -> AuthSettings:
    """构造 Streamable HTTP 鉴权配置（issuer/resource URL 仅为元数据）。"""
    base = f"http://{display_host(host)}:{port}"
    return AuthSettings(
        issuer_url=f"{base}/.well-known/oauth-authorization-server",
        resource_server_url=base,
    )


def _transport_security_for(host: str) -> TransportSecuritySettings | None:
    """为 Streamable HTTP 提供显式的 ``transport_security``（mcp 2.1+ 兼容）。

    * localhost：返回 ``None`` → mcp SDK 自动启用 DNS rebinding 保护
      （loopback Host/Origin 白名单），无需显式传入；
    * 非 localhost（已强制 bearer token 鉴权）：显式传入并关闭 DNS rebinding
      保护——bearer token 鉴权已是安全边界；通配绑定（``0.0.0.0`` / ``::``）
      下 Host 头校验不可行，且避免 mcp 2.1+（PR #861）对未显式配置的
      非 localhost 请求返回 421（Misdirected Request）。
    """
    if is_local_host(host):
        return None
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def create_mcp_server(
    spec_dir: str | Path,
    *,
    server_override: str | None = None,
    name: str | None = None,
    version: str = "0.12.1",
    upload_base: str | None = None,
    transport: str = "http",
) -> MCPServer:
    """构建 spec 对应的低层 MCP Server（工具动态注册自命令树）。

    Args:
        spec_dir: cliyard spec 目录。
        server_override: 可选 base_url 运行时覆盖（同 CLI ``--server``）。
        name: serverInfo 名称覆盖（缺省取 spec name）。
        version: serverInfo 版本。
        upload_base: 对外 ``POST /upload`` 基地址（透传给 file 参数描述模板）。
        transport: ``"http"`` 或 ``"stdio"``（透传给 file 参数描述模板；
            ``run_mcp_server`` 按其已知 transport 传入）。

    Returns:
        已注册 ``tools/list`` / ``tools/call`` 的低层 :class:`MCPServer`。
    """
    executor = MCPExecutor(
        spec_dir,
        server_override=server_override,
        upload_base=upload_base,
        transport=transport,
    )
    return executor.to_mcp_server(name=name, version=version)


def build_mcp_http_app(
    spec_dir: str | Path,
    *,
    server_override: str | None = None,
    token: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8081,
    path: str = "/mcp",
    upload_base: str | None = None,
    upload_dir: str | None = None,
):
    """构建 Streamable HTTP 的 Starlette app（独立 uvicorn 启动用）。

    Args:
        token: 提供后启用 bearer 鉴权（``Authorization: Bearer <token>``）。
        upload_base: 对外 ``POST /upload`` 基地址（描述模板透传用，todo 4；
            本 todo 仅写入 ``app.state``，缺省 ``None``）。
        upload_dir: 上传存储目录（写入 ``app.state.upload_dir`` 供
            ``mcp_upload_endpoint`` 消费；``None`` → handler 回退默认目录）。
    """
    server = create_mcp_server(
        spec_dir,
        server_override=server_override,
        upload_base=upload_base,
        transport="http",
    )
    kwargs: dict[str, Any] = {"streamable_http_path": path, "host": host}
    transport_security = _transport_security_for(host)
    if transport_security is not None:
        kwargs["transport_security"] = transport_security
    if token:
        kwargs["auth"] = _auth_settings_for(token, host, port)
        kwargs["token_verifier"] = _StaticTokenVerifier(token)
    app = server.streamable_http_app(**kwargs)
    _mount_upload_route(app, token=token, upload_base=upload_base, upload_dir=upload_dir)
    return app


def _mount_upload_route(
    app, *, token: str | None, upload_base: str | None, upload_dir: str | None
) -> None:
    """在 Streamable HTTP app 上挂载 ``POST /upload``（MCP 文件交接点）。

    路由追加在 ``/mcp`` 之后、互不吞没（不同 path）；鉴权由
    ``mcp_upload_endpoint`` 内部以 ``_StaticTokenVerifier`` 语义执行，
    未配置 token（本地回环）时免鉴，与 :func:`is_local_host` 一致。
    """
    from cliyard.server.api.upload import mcp_upload_endpoint

    app.state.upload_token = token
    app.state.upload_base = upload_base
    app.state.upload_dir = upload_dir
    app.routes.append(Route(path="/upload", endpoint=mcp_upload_endpoint, methods=["POST"]))


def mount_mcp_http(
    app,
    spec_dir: str | Path,
    *,
    path: str = "/mcp",
    server_override: str | None = None,
    token: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8081,
    upload_base: str | None = None,
    upload_dir: str | None = None,
) -> MCPServer:
    """把 MCP Streamable HTTP 挂载进现有 FastAPI serve（同一端口/uvicorn）。

    Starlette 的 ``Mount`` 不会运行子 app 的 lifespan，因此这里显式把 MCP
    session manager 的 lifespan 合并进父 app 的 ``lifespan_context``，保证
    Streamable HTTP 会话管理随父服务启停。

    用 ``Route``（而非 ``Mount``）直接挂 ``endpoint=mcp_app``：
    * ``Mount("/mcp")`` 要求带尾斜杠的 ``/mcp/``，而 MCP 客户端请求的是
      ``POST /mcp``（无尾斜杠）——Mount 匹配不到会被 ``serve`` 的
      ``StaticFiles`` 兜底挂载 ``/`` 吞掉返回 405；
    * ``Route`` 直接命中 ``/mcp``，且 ``endpoint`` 是完整 Starlette 子应用，
      middleware（token 鉴权）与路由都在，无前缀剥离问题。
    路由插入**最前**：`serve` 的 ``create_app`` 会用 ``StaticFiles`` 兜底挂载
    ``/``（吞掉非 GET 方法返回 405），若不插到前面会把 ``/mcp`` 的 POST 也拦掉。
    """
    server = create_mcp_server(spec_dir, server_override=server_override)
    kwargs: dict[str, Any] = {"streamable_http_path": path, "host": host}
    transport_security = _transport_security_for(host)
    if transport_security is not None:
        kwargs["transport_security"] = transport_security
    if token:
        kwargs["auth"] = _auth_settings_for(token, host, port)
        kwargs["token_verifier"] = _StaticTokenVerifier(token)
    mcp_app = server.streamable_http_app(**kwargs)
    route = Route(path=path, endpoint=mcp_app)
    app.router.routes.insert(0, route)
    # ``POST /upload`` 挂父级：子 app 以 endpoint 形式被调用时其内部路由表
    # 不参与父级 path 匹配，故父级需自有 /upload 路由（与 /mcp 同理插最前，
    # 避开 serve ``/`` 静态兜底）。endpoint 复用同一实现，鉴权读父 app
    # 的 ``state.upload_token``（同部署下与 serve /api/upload 同 token）。
    from cliyard.server.api.upload import mcp_upload_endpoint

    app.router.routes.insert(0, Route(path="/upload", endpoint=mcp_upload_endpoint, methods=["POST"]))
    if upload_base is not None:
        app.state.upload_base = upload_base
    if upload_dir is not None:
        app.state.upload_dir = upload_dir
    _combine_lifespan(app, server.session_manager)
    return server


def _combine_lifespan(app, session_manager) -> None:
    """把 MCP session manager 的 lifespan 并入父 app（保留原 lifespan）。"""
    original = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def combined(application):
        async with session_manager.run():
            async with original(application) as state:
                yield state

    app.router.lifespan_context = combined


async def _serve_stdio(server: MCPServer) -> None:
    """stdio 服务循环：stdin/stdout 走 JSON-RPC，直到客户端关闭。"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run_mcp_stdio(
    spec_dir: str | Path,
    *,
    server_override: str | None = None,
    upload_base: str | None = None,
) -> None:
    """以 stdio transport 启动 MCP Server（阻塞直到客户端断开）。

    所有日志/提示输出到 stderr，保持 stdout 仅供 JSON-RPC。
    """
    server = create_mcp_server(
        spec_dir,
        server_override=server_override,
        upload_base=upload_base,
        transport="stdio",
    )
    click.echo(f"MCP (stdio) serving spec {Path(spec_dir).resolve()}", err=True)
    asyncio.run(_serve_stdio(server))


def _check_http_auth(
    host: str, token: str | None, allow_remote_no_auth: bool
) -> None:
    """非本地 host 强制鉴权：未带 token 且未显式放行则拒绝启动。"""
    if is_local_host(host) or token or allow_remote_no_auth:
        return
    raise click.ClickException(
        "Refusing to bind an unauthenticated MCP HTTP server to a "
        f"non-localhost host ({host!r}). Pass --token <TOKEN> to enable "
        "bearer auth, or --allow-remote-no-auth to explicitly disable auth."
    )


def run_mcp_server(
    spec_dir: str | Path,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8081,
    server_override: str | None = None,
    token: str | None = None,
    allow_remote_no_auth: bool = False,
    upload_dir: str | None = None,
    upload_base_url: str | None = None,
    file_allow_dirs: tuple[str, ...] | list[str] | None = None,
    version: str = "0.12.1",
) -> None:
    """启动 MCP Server。

    Args:
        transport: ``"stdio"``（默认，独立进程）或 ``"http"``（Streamable HTTP）。
        host/port: http transport 的 uvicorn 绑定参数。
        server_override: base_url 运行时覆盖（同 CLI ``--server``）。
        token: http transport 的 bearer token（非本地 host 强制要求）。
        allow_remote_no_auth: 显式允许非本地 host 无鉴权启动（不推荐）。
        upload_dir: 上传存储目录（经 todo 1 ``validate_dir`` 启动
            fail-fast 校验，resolve 后透传 ``build_mcp_http_app`` 写入
            ``app.state.upload_dir``）。
        upload_base_url: 对外基址（透传 ``build_mcp_http_app(upload_base=...)``
            写入 ``app.state.upload_base``，供 todo 4 模板链）。
        file_allow_dirs: 额外可读目录（todo 5 的 jail 消费，见 learnings）。
    """
    from cliyard.server.launcher import check_upload_dir

    if transport not in ("stdio", "http"):
        raise click.ClickException(
            f"Unknown transport {transport!r}; expected 'stdio' or 'http'"
        )
    resolved_upload_dir = check_upload_dir(upload_dir, spec_dir)
    if transport == "stdio":
        run_mcp_stdio(
            spec_dir, server_override=server_override, upload_base=upload_base_url
        )
        return

    _check_http_auth(host, token, allow_remote_no_auth)
    app = build_mcp_http_app(
        spec_dir,
        server_override=server_override,
        token=token,
        host=host,
        port=port,
        path="/mcp",
        upload_base=upload_base_url,
        upload_dir=resolved_upload_dir,
    )
    url = f"http://{display_host(host)}:{port}/mcp"
    click.echo(f"MCP (Streamable HTTP) serving spec {Path(spec_dir).resolve()} at {url}")
    if token:
        click.echo("Bearer token auth enabled (Authorization: Bearer <token>)")
    uvicorn.run(app, host=host, port=port)
