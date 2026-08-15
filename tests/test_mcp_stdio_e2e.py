"""stdio transport 端到端测试（方案 V4）。

用 mcp SDK 官方 Client（``stdio_client`` + ``ClientSession``）真连子进程里的
``cliyard mcp`` 服务器，配合本地 mock 上游，走通完整链路：

- tools/list 工具发现（三态命名）
- tools/call 真实执行（mock 上游返回真实 JSON，走 execute_pipeline 全链路）
- 鉴权：env→inject 链注入 Authorization 头（上游记录断言）
- file 参数 base64 桥接（multipart 上传，上游收到文件字节）
- 错误路径：未知 tool / 缺 required 参数 / 上游失败
- ``--server`` 覆盖（R7）：spec 指向死地址，server 覆盖打到 mock
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

from tests.mcp_helpers import MockUpstream, write_auth_spec, write_spec

_MCP_SRV = (
    "import sys; "
    "from cliyard.server.mcp.server import run_mcp_server; "
    "run_mcp_server(sys.argv[1], transport='stdio', server_override=sys.argv[2] or None)"
)


def _spawn(spec_dir: str, server_override: str = "", env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", _MCP_SRV, str(spec_dir), server_override],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, **(env or {})},
    )


async def _connect(spec_dir, server_override=""):
    params = _spawn(spec_dir, server_override)
    ctx = stdio_client(params)
    streams = await ctx.__aenter__()
    session = ClientSession(*streams)
    await session.__aenter__()
    await session.initialize()
    return ctx, session


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 工具发现 / 调用 / server 覆盖
# ---------------------------------------------------------------------------


def test_stdio_list_tools_and_call(tmp_path):
    """tools/list 三态命名 + tools/call 真实命中 mock 上游。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"repos.list", "repos.create", "repos.upload"} <= names

            result = await session.call_tool("repos.list", {"page": 2})
            assert isinstance(result, CallToolResult)
            assert result.is_error is False
            text = result.content[0].text
            data = json.loads(text)
            assert data["items"][0]["name"] == "repo-a"
            assert data["total"] == 2
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        _run(main())
        assert upstream.records[0]["method"] == "GET"
        assert upstream.records[0]["path"].startswith("/repos")
    finally:
        upstream.close()


def test_stdio_server_override_reaches_upstream(tmp_path):
    """--server 覆盖：spec 指向死地址，server_override 命中 mock（R7）。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, "http://127.0.0.1:1")  # 不可达

    async def main():
        ctx, session = await _connect(str(spec), server_override=upstream.base_url)
        try:
            result = await session.call_tool("repos.list", {})
            assert result.is_error is False
            assert "repo-a" in result.content[0].text
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        _run(main())
        assert upstream.records, "上游应收到请求"
        assert upstream.records[0]["path"].startswith("/repos")
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def test_stdio_auth_env_chain_injects_header(tmp_path, monkeypatch):
    """env→inject 鉴权链：token 注入 Authorization 头并到达上游。"""
    monkeypatch.setenv("MOCK_TOKEN", "tok-secret-123")
    upstream = MockUpstream()
    spec = write_auth_spec(tmp_path, upstream.base_url, env_var="MOCK_TOKEN")

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            result = await session.call_tool("repos.list", {})
            assert result.is_error is False
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        _run(main())
        auth_header = upstream.records[0]["headers"].get("Authorization")
        assert auth_header == "Bearer tok-secret-123"
    finally:
        upstream.close()


def test_stdio_auth_missing_env_is_error(tmp_path, monkeypatch):
    """鉴权链 env 变量缺失 → tool 调用 is_error=True（不泄露/不崩溃）。"""
    monkeypatch.delenv("MOCK_TOKEN", raising=False)
    upstream = MockUpstream()
    spec = write_auth_spec(tmp_path, upstream.base_url, env_var="MOCK_TOKEN")

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            result = await session.call_tool("repos.list", {})
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        result = _run(main())
        assert result.is_error is True
        assert "MOCK_TOKEN" in result.content[0].text
        assert upstream.records == [], "鉴权失败不应发请求到上游"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# file 参数 base64 桥接
# ---------------------------------------------------------------------------


def test_stdio_file_param_multipart_upload(tmp_path):
    """file 参数 base64 → 临时文件 → multipart 上传，上游收到文件字节。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)
    payload = base64.b64encode(b"hello-mcp-file").decode()

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            result = await session.call_tool(
                "repos.upload", {"file": f"data:application/octet-stream;base64,{payload}"}
            )
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        result = _run(main())
        assert result.is_error is False
        assert upstream.records and upstream.records[0]["method"] == "POST"
        body = upstream.records[0]["body"]
        assert b"hello-mcp-file" in body, "上游 multipart 体应包含文件内容"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


def test_stdio_unknown_tool_protocol_error(tmp_path):
    """未知 tool → MCP 协议层错误。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            from mcp.shared.exceptions import MCPError

            with pytest.raises(MCPError):
                await session.call_tool("no.such.tool", {})
            return True
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        assert _run(main()) is True
    finally:
        upstream.close()


def test_stdio_missing_required_param_is_error(tmp_path):
    """缺 required 参数（repos.create.name）→ is_error=True + 校验错误消息。"""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            result = await session.call_tool("repos.create", {})
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        result = _run(main())
        assert result.is_error is True
        assert "name" in result.content[0].text
        assert upstream.records == [], "校验失败不应发请求"
    finally:
        upstream.close()


def test_stdio_upstream_5xx_is_error(tmp_path):
    """上游 5xx → is_error=True（响应解析错误进入错误结果）。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"server exploded"
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    spec = write_spec(tmp_path, f"http://127.0.0.1:{server.server_address[1]}")

    async def main():
        ctx, session = await _connect(str(spec))
        try:
            result = await session.call_tool("repos.list", {})
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        result = _run(main())
        assert result.is_error is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
