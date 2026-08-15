"""Streamable HTTP transport 端到端测试（方案 V4 / V7）。

真起 uvicorn（线程内）+ mcp SDK 官方 Client（``streamable_http_client``）真连：

- 独立启动（build_mcp_http_app）list/call 全链路
- 挂载进现有 FastAPI serve（同一端口复用 launcher）
- 非本地 host 强制 bearer 鉴权：无 token / 错 token → 失败；正确 token → 成功
- 默认绑定 127.0.0.1；--host 非本地未带 token → 拒绝启动
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from cliyard.server.app import create_app
from cliyard.server.mcp.server import (
    _check_http_auth,
    build_mcp_http_app,
    is_local_host,
    mount_mcp_http,
)

from tests.mcp_helpers import MockUpstream, write_spec

_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_uvicorn(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn 未在预期时间内就绪"
    return server, thread


def _stop_uvicorn(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


async def _connect(url: str, token: str | None = None):
    kwargs = {}
    client = None
    if token:
        import httpx2

        client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
        kwargs["http_client"] = client
    ctx = streamable_http_client(url, **kwargs)
    streams = await ctx.__aenter__()
    session = ClientSession(*streams)
    await session.__aenter__()
    await session.initialize()
    return ctx, session, client


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 独立启动（build_mcp_http_app）
# ---------------------------------------------------------------------------


def test_http_standalone_list_and_call(tmp_path):
    """独立 uvicorn + Streamable HTTP 全链路 list/call。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    port = _free_port()
    app = build_mcp_http_app(spec, host="127.0.0.1", port=port, path="/mcp")
    server, thread = _start_uvicorn(app, port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:

        async def main():
            ctx, session, client = await _connect(url)
            try:
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert "repos.list" in names

                result = await session.call_tool("repos.list", {"page": 1})
                assert isinstance(result, CallToolResult)
                assert result.is_error is False
                data = json.loads(result.content[0].text)
                assert data["total"] == 2
                return result
            finally:
                await session.__aexit__(None, None, None)
                await ctx.__aexit__(None, None, None)
                if client:
                    await client.aclose()

        assert _run(main()).is_error is False
    finally:
        _stop_uvicorn(server, thread)
        upstream.close()


# ---------------------------------------------------------------------------
# 挂载进现有 FastAPI serve（同端口）
# ---------------------------------------------------------------------------


def test_http_mounted_into_serve_same_port(tmp_path):
    """MCP 挂载进 serve FastAPI：/mcp 可用且 /health 同端口正常。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    port = _free_port()

    serve_app = create_app(_FIXTURES_SPEC)  # 现有 serve（fixtures/spec-dir）
    mount_mcp_http(serve_app, spec, path="/mcp", host="127.0.0.1", port=port)
    server, thread = _start_uvicorn(serve_app, port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:

        async def main():
            # serve 自身 /health 同端口可达
            import httpx2

            async with httpx2.AsyncClient() as hc:
                health = await hc.get(f"http://127.0.0.1:{port}/health")
                assert health.status_code == 200

            ctx, session, client = await _connect(url)
            try:
                tools = await session.list_tools()
                assert "repos.list" in {t.name for t in tools.tools}
                result = await session.call_tool("repos.list", {})
                assert result.is_error is False
                return True
            finally:
                await session.__aexit__(None, None, None)
                await ctx.__aexit__(None, None, None)
                if client:
                    await client.aclose()

        assert _run(main()) is True
    finally:
        _stop_uvicorn(server, thread)
        upstream.close()


# ---------------------------------------------------------------------------
# bearer 鉴权（V7）
# ---------------------------------------------------------------------------


def test_http_auth_required_and_token_works(tmp_path):
    """无 token → 失败；正确 token → 成功；错 token → 失败。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    port = _free_port()
    app = build_mcp_http_app(
        spec, host="127.0.0.1", port=port, path="/mcp", token="sekret"
    )
    server, thread = _start_uvicorn(app, port)
    url = f"http://127.0.0.1:{port}/mcp"
    try:

        async def with_token(token):
            ctx, session, client = await _connect(url, token=token)
            try:
                tools = await session.list_tools()
                return [t.name for t in tools.tools]
            finally:
                await session.__aexit__(None, None, None)
                await ctx.__aexit__(None, None, None)
                if client:
                    await client.aclose()

        # 无 token → 连接/初始化失败
        with pytest.raises(Exception):
            _run(with_token(None))

        # 错误 token → 失败
        with pytest.raises(Exception):
            _run(with_token("wrong"))

        # 正确 token → 成功
        names = _run(with_token("sekret"))
        assert "repos.list" in names
    finally:
        _stop_uvicorn(server, thread)
        upstream.close()


# ---------------------------------------------------------------------------
# 非本地 host 强制鉴权开关（V7）
# ---------------------------------------------------------------------------


def test_is_local_host():
    assert is_local_host("127.0.0.1") is True
    assert is_local_host("localhost") is True
    assert is_local_host("::1") is True
    assert is_local_host("0.0.0.0") is False
    assert is_local_host("::") is False
    assert is_local_host("10.0.0.5") is False


def test_non_local_host_requires_token_or_override():
    """非本地 host 未带 token → 拒绝启动；带 token 或显式放行 → 通过。"""
    with pytest.raises(Exception):
        _check_http_auth("10.0.0.5", None, False)
    _check_http_auth("10.0.0.5", "tok", False)  # 带 token
    _check_http_auth("10.0.0.5", None, True)  # 显式放行
    _check_http_auth("127.0.0.1", None, False)  # 本地免鉴权


def test_run_mcp_server_http_passes_host_port(monkeypatch, tmp_path):
    """run_mcp_server(http) 复用 uvicorn：正确 host/port。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    launched: dict = {}
    fake_app = object()

    monkeypatch.setattr(
        "cliyard.server.mcp.server.build_mcp_http_app", lambda *a, **k: fake_app
    )
    monkeypatch.setattr("cliyard.server.mcp.server.uvicorn.run", lambda app, **kw: launched.update(app=app, **kw))

    from cliyard.server.mcp.server import run_mcp_server

    run_mcp_server(spec, transport="http", host="127.0.0.1", port=9191)
    assert launched["app"] is fake_app
    assert launched["host"] == "127.0.0.1"
    assert launched["port"] == 9191
    upstream.close()


def test_run_mcp_server_rejects_remote_without_token(monkeypatch, tmp_path):
    """run_mcp_server http 非本地无 token → ClickException（不启动）。"""
    import click

    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    launched: dict = {}
    monkeypatch.setattr("cliyard.server.mcp.server.uvicorn.run", lambda app, **kw: launched.update(**kw))

    from cliyard.server.mcp.server import run_mcp_server

    with pytest.raises(click.ClickException, match="non-localhost"):
        run_mcp_server(spec, transport="http", host="0.0.0.0", port=9191)
    assert launched == {}, "不应启动 uvicorn"
    upstream.close()
