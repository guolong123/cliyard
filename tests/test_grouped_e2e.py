"""Grouped-mode end-to-end proof over real transports (plan todo 8).

Proves, with NO mocked handler and NO mocked ``call_tool``:

* (failure-first) unknown ``operation`` end-to-end → ``is_error=True``
  with the ``available operations`` guidance;
* stdio: real ``ClientSession`` over a real ``cliyard mcp`` subprocess
  (``tool_mode='grouped'``) — grouped ``repos`` list/call succeeds against
  a ``MockUpstream`` on an ephemeral port;
* http: real uvicorn (ephemeral port) + real Streamable-HTTP session —
  grouped ``repos`` list/call succeeds;
* file: real ``POST /upload`` → take ``path`` → grouped call
  (``operation='upload'``) → upstream mock records the file bytes;
  the grouped schema still carries the curl three-element handoff copy;
* negative: delete the server-side file post-upload, then grouped call →
  ``is_error=True`` with short-form re-upload guidance.

Isolation: every test uses its own ``tmp_path`` upload dir and ephemeral
ports — no fixed ports, no shared static temp dirs.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.server import build_mcp_http_app

from tests.mcp_helpers import MockUpstream, write_spec

_PAYLOAD = b"grouped-e2e-file-proof-bytes"

_MCP_SRV_GROUPED = (
    "import sys; "
    "from cliyard.server.mcp.server import run_mcp_server; "
    "run_mcp_server(sys.argv[1], transport='stdio', "
    "server_override=sys.argv[2] or None, tool_mode='grouped')"
)


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """Neutralize intercepting proxies: MockUpstream lives on 127.0.0.1."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# stdio helpers (grouped subprocess)
# ---------------------------------------------------------------------------


def _spawn_grouped(spec_dir: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", _MCP_SRV_GROUPED, str(spec_dir), ""],
        cwd=Path(__file__).resolve().parent.parent,
        env={k: v for k, v in os.environ.items()},
    )


async def _connect_stdio(spec_dir):
    ctx = stdio_client(_spawn_grouped(str(spec_dir)))
    streams = await ctx.__aenter__()
    session = ClientSession(*streams)
    await session.__aenter__()
    await session.initialize()
    return ctx, session


# ---------------------------------------------------------------------------
# http helpers (grouped uvicorn, ephemeral port)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_grouped_http(spec: Path, upload_dir: Path, port: int):
    app = build_mcp_http_app(
        str(spec),
        host="127.0.0.1",
        port=port,
        path="/mcp",
        tool_mode="grouped",
        upload_dir=str(upload_dir),
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn 未在预期时间内就绪"
    return app, server, thread


def _stop_uvicorn(server, thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


async def _connect_http(url: str):
    ctx = streamable_http_client(url)
    streams = await ctx.__aenter__()
    session = ClientSession(*streams)
    await session.__aenter__()
    await session.initialize()
    return ctx, session


def _call(executor: MCPExecutor, tool: str, args: dict):
    """REAL ``call_tool`` (never mocked) run to completion."""
    params = SimpleNamespace(name=tool, arguments=args)
    return asyncio.run(executor.call_tool(None, params))


# ---------------------------------------------------------------------------
# (0) FAILURE-FIRST: unknown operation end-to-end → isError
# ---------------------------------------------------------------------------


def test_grouped_unknown_operation_is_error_stdio(tmp_path):
    """Red-signal probe FIRST: unknown op over a real stdio session → isError.

    If this ever goes green-when-it-should-be-red (e.g. dispatch silently
    swallows bad ops), the suite proves it can catch that; green here proves
    the ``Unknown operation ... available operations`` guidance path works.
    """
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)

    async def main():
        ctx, session = await _connect_stdio(str(spec))
        try:
            result = await session.call_tool("repos", {"operation": "nope"})
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        result = _run(main())
        assert isinstance(result, CallToolResult)
        assert result.is_error is True, result.content[0].text
        text = result.content[0].text
        assert "Unknown operation 'nope'" in text, text
        assert "available operations" in text, text
        assert upstream.records == [], "bad operation must never reach upstream"
    finally:
        upstream.close()


def test_grouped_unknown_operation_is_error_http(tmp_path):
    """Same unknown-op proof over real Streamable HTTP (ephemeral port)."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-unk-http"
        upload_dir.mkdir()
        port = _free_port()
        _, server, thread = _start_grouped_http(spec, upload_dir, port)
        url = f"http://127.0.0.1:{port}/mcp"
        try:

            async def main():
                ctx, session = await _connect_http(url)
                try:
                    return await session.call_tool("repos", {"operation": "nope"})
                finally:
                    await session.__aexit__(None, None, None)
                    await ctx.__aexit__(None, None, None)

            result = _run(main())
            assert result.is_error is True, result.content[0].text
            assert "Unknown operation 'nope'" in result.content[0].text
            assert upstream.records == [], "bad operation must never reach upstream"
        finally:
            _stop_uvicorn(server, thread)
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (a) grouped success: stdio AND http over real servers
# ---------------------------------------------------------------------------


def test_grouped_stdio_list_and_call(tmp_path):
    """Grouped stdio: tools/list shows bare ``repos``, call succeeds."""
    upstream = MockUpstream()
    spec = write_spec(tmp_path, upstream.base_url)

    async def main():
        ctx, session = await _connect_stdio(str(spec))
        try:
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "repos" in names, f"grouped tool missing: {sorted(names)}"
            assert "repos.list" not in names, "flat names must not leak into grouped table"

            result = await session.call_tool("repos", {"operation": "list", "page": 2})
            assert isinstance(result, CallToolResult)
            assert result.is_error is False, result.content[0].text
            data = json.loads(result.content[0].text)
            assert data["items"][0]["name"] == "repo-a"
            assert data["total"] == 2

            created = await session.call_tool(
                "repos", {"operation": "create", "name": "n1"}
            )
            assert created.is_error is False, created.content[0].text
            return result
        finally:
            await session.__aexit__(None, None, None)
            await ctx.__aexit__(None, None, None)

    try:
        _run(main())
        assert len(upstream.records) == 2, "list + create must each reach upstream"
        assert upstream.records[0]["method"] == "GET"
        assert upstream.records[1]["method"] == "POST"
    finally:
        upstream.close()


def test_grouped_http_list_and_call(tmp_path):
    """Grouped http: real uvicorn + real session, list/call succeeds."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-http"
        upload_dir.mkdir()
        port = _free_port()
        _, server, thread = _start_grouped_http(spec, upload_dir, port)
        url = f"http://127.0.0.1:{port}/mcp"
        try:

            async def main():
                ctx, session = await _connect_http(url)
                try:
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert "repos" in names, f"grouped tool missing: {sorted(names)}"
                    assert "repos.list" not in names

                    result = await session.call_tool(
                        "repos", {"operation": "list", "page": 1}
                    )
                    assert isinstance(result, CallToolResult)
                    assert result.is_error is False, result.content[0].text
                    data = json.loads(result.content[0].text)
                    assert data["total"] == 2
                    return result
                finally:
                    await session.__aexit__(None, None, None)
                    await ctx.__aexit__(None, None, None)

            assert _run(main()).is_error is False
            assert upstream.records and upstream.records[0]["method"] == "GET"
        finally:
            _stop_uvicorn(server, thread)
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (b) file-param grouped tool: POST /upload → grouped call → upstream bytes
# ---------------------------------------------------------------------------


def test_grouped_file_upload_chain_and_schema_curl(tmp_path):
    """Real ``POST /upload`` → grouped ``upload`` call → upstream gets bytes.

    Also proves the grouped ``repos`` schema still carries the curl
    three-element handoff copy for the file param.
    """
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-file"
        upload_dir.mkdir()
        app = build_mcp_http_app(
            str(spec), tool_mode="grouped", upload_dir=str(upload_dir)
        )

        client = TestClient(app)
        resp = client.post(
            "/upload",
            files={"file": ("proof.bin", _PAYLOAD, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Path(body["path"]).is_file(), "uploaded file must land on disk"
        assert body["bytes"] == len(_PAYLOAD)

        executor = MCPExecutor(str(spec), transport="http", tool_mode="grouped",
                               upload_dir=str(upload_dir))
        result = _call(executor, "repos", {"operation": "upload", "file": body["path"]})
        assert result.is_error is False, result.content[0].text
        assert upstream.records and upstream.records[0]["method"] == "POST"
        assert _PAYLOAD in upstream.records[0]["body"], (
            "upstream multipart body must carry file bytes"
        )

        # Grouped schema: curl three-element copy readable for the file param.
        listed = asyncio.run(executor.list_tools(None, None))
        grouped_repos = next(t for t in listed.tools if t.name == "repos")
        blob = json.dumps(grouped_repos.model_dump(), ensure_ascii=False, default=str)
        assert "POST" in blob and "/upload" in blob, (
            "grouped file schema must carry the curl handoff copy"
        )
        assert "curl" in blob.lower(), "curl copy line must be present in grouped schema"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (c) negative: deleted server file → isError short-form guidance
# ---------------------------------------------------------------------------


def test_grouped_deleted_server_file_is_error_guidance(tmp_path):
    """Upload, delete the server file, grouped call → isError + guidance."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-neg"
        upload_dir.mkdir()
        app = build_mcp_http_app(
            str(spec), tool_mode="grouped", upload_dir=str(upload_dir)
        )

        client = TestClient(app)
        resp = client.post(
            "/upload",
            files={"file": ("gone.bin", _PAYLOAD, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        server_path = resp.json()["path"]
        Path(server_path).unlink()  # expire the server-side file post-upload

        executor = MCPExecutor(str(spec), transport="http", tool_mode="grouped",
                               upload_dir=str(upload_dir))
        result = _call(executor, "repos", {"operation": "upload", "file": server_path})
        assert result.is_error is True, result.content[0].text
        text = result.content[0].text
        assert "POST /upload" in text, text  # short-form re-upload guidance
        assert ("已过期" in text) or ("不存在" in text), text
        assert upstream.records == [], "missing file must never reach upstream"
    finally:
        upstream.close()
