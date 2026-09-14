"""E2E proof for ``type: plugin:*`` methods over serve + MCP (plan todo 5).

Fixture mirrors a ketacli ``dashboard_create`` shape: a ``type: plugin:``
method with a ``chart_file: type: file`` param whose plugin self-reads the
file, asserts marker bytes, forwards them to the MockUpstream, and echoes
the marker in its result.

Real surfaces only — TestClient over ``create_app`` / ``build_mcp_http_app``
and real ``MCPExecutor.call_tool``; no mocked handlers, no mocked call_tool:

* unknown-plugin → isError / error status carrying the plugin name (FIRST,
  failure-first);
* serve: real ``POST /api/upload`` → ``POST /api/execute`` → done, marker in
  the response-step preview, marker bytes recorded upstream;
* MCP: same chain via real ``call_tool``;
* deleted server file → isError with re-upload guidance, upstream untouched;
* stdio: same-shape local file succeeds with no jail.

Isolation: unique ``tmp_path`` spec/upload dirs per test, ephemeral
MockUpstream ports, uuid-suffixed plugin module filenames, and an autouse
fixture restoring ``PluginRegistry`` + ``_scanned_dirs`` — no fixed ports,
no shared static temp dirs.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import cliyard.server.executor as executor_mod
from cliyard.server.app import create_app
from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.server import build_mcp_http_app
from cliyard.plugin import PluginRegistry
from cliyard.plugin.discovery import _scanned_dirs

from tests.mcp_helpers import MockUpstream

_MARKER = "DASH-E2E-MARKER"
_PLUGIN_NAME = "e2e_dash_create"
_GHOST_TARGET = "dashboards.ghost"
_CREATE_TARGET = "dashboards.create"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """Neutralize intercepting proxies (MockUpstream lives on 127.0.0.1)."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture(autouse=True)
def _isolate_plugins():
    """Clear registry + restore discovery cache around each test."""
    scanned_snapshot = set(_scanned_dirs)
    PluginRegistry.clear()
    yield
    PluginRegistry.clear()
    _scanned_dirs.clear()
    _scanned_dirs.update(scanned_snapshot)


def _write_plugin_spec(tmp_path: Path, base_url: str) -> Path:
    """Dashboard-shaped spec dir: plugin method + ghost + spec-local plugin."""
    tag = uuid.uuid4().hex[:8]
    spec = tmp_path / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "_auth.yaml").write_text(
        f"name: dash-svc\nversion: '1.0'\nserver:\n  base_url: {base_url}\n",
        encoding="utf-8",
    )
    (spec / "dashboards.yaml").write_text(
        "description: dashboard mgmt\n"
        "path: dashboards\n"
        "methods:\n"
        f"  create:\n"
        f"    type: plugin:{_PLUGIN_NAME}\n"
        "    description: create dashboard\n"
        "    params:\n"
        "      body:\n"
        "        - name: title\n"
        "          type: string\n"
        "          required: true\n"
        "        - name: chart_file\n"
        "          type: file\n"
        "          required: true\n"
        "  ghost:\n"
        "    type: plugin:e2e_no_such_plugin\n"
        "    params: {}\n",
        encoding="utf-8",
    )
    plug_dir = spec / "plugins"
    plug_dir.mkdir(exist_ok=True)
    (plug_dir / f"e2e_dash_plug_{tag}.py").write_text(
        "from cliyard.plugin import register_method\n"
        f"@register_method({_PLUGIN_NAME!r})\n"
        "def _fn(params, http_client, config):\n"
        "    with open(params['chart_file'], 'rb') as f:\n"
        "        raw = f.read()\n"
        f"    assert b'{_MARKER}' in raw, 'chart file missing marker'\n"
        "    resp = http_client.request('POST', '/dashboards',\n"
        "        data={'title': params.get('title'), "
        f"'marker': '{_MARKER}'}})\n"
        "    return {'created': True, "
        f"'marker': '{_MARKER}', 'upstream': resp.json()}}\n",
        encoding="utf-8",
    )
    return spec


def _upload_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _call(executor: MCPExecutor, tool: str, args: dict):
    """REAL ``call_tool`` (never mocked) run to completion."""
    params = SimpleNamespace(name=tool, arguments=args)
    return asyncio.run(executor.call_tool(None, params))


def _serve_execute(client: TestClient, target: str, params: dict) -> dict:
    """REAL ``POST /api/execute`` → wait → ``GET`` final execution body."""
    resp = client.post(
        "/api/execute", json={"kind": "command", "target": target, "params": params}
    )
    assert resp.status_code == 200, resp.text
    execution_id = resp.json()["execution_id"]
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(10), "background run timed out"
    return client.get(f"/api/executions/{execution_id}").json()


# ---------------------------------------------------------------------------
# Failure-first: unknown plugin end-to-end
# ---------------------------------------------------------------------------


def test_mcp_unknown_plugin_is_error(tmp_path):
    """未知插件方法经 MCP 真 call_tool → isError 并携带插件名。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-ghost-mcp")
        executor = MCPExecutor(str(spec), transport="http", upload_dir=str(upload_dir))
        result = _call(executor, _GHOST_TARGET, {})
        assert result.is_error is True, result.content[0].text
        assert "e2e_no_such_plugin" in result.content[0].text
        assert upstream.records == [], "unknown plugin must never reach upstream"
    finally:
        upstream.close()


def test_serve_unknown_plugin_is_error(tmp_path):
    """未知插件方法经 serve 真链路 → error 状态并携带插件名。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-ghost-serve")
        client = TestClient(create_app(str(spec), upload_dir=str(upload_dir)))
        body = _serve_execute(client, _GHOST_TARGET, {})
        assert body["status"] == "error", body
        err_steps = [s for s in body["steps"] if s.get("type") == "error"]
        assert err_steps, body["steps"]
        assert "e2e_no_such_plugin" in err_steps[0].get("message", ""), body
        assert upstream.records == [], "unknown plugin must never reach upstream"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# Happy paths: upload → plugin call → upstream marker bytes
# ---------------------------------------------------------------------------


def test_serve_upload_to_plugin_call(tmp_path):
    """serve 真链 upload→execute→done；marker 落 preview 与上游。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-serve")
        client = TestClient(create_app(str(spec), upload_dir=str(upload_dir)))

        up = client.post(
            "/api/upload",
            files={"file": ("chart.yaml", b"blob: " + _MARKER.encode(), "text/yaml")},
        )
        assert up.status_code == 200, up.text
        server_path = up.json()["path"]
        assert Path(server_path).is_file()

        body = _serve_execute(
            client, _CREATE_TARGET, {"title": "ops", "chart_file": server_path}
        )
        assert body["status"] == "done", body
        response_steps = [s for s in body["steps"] if s.get("type") == "response"]
        assert response_steps, body["steps"]
        assert response_steps[0]["result_preview"]["marker"] == _MARKER

        assert len(upstream.records) == 1
        rec = upstream.records[0]
        assert rec["method"] == "POST" and rec["path"] == "/dashboards"
        assert _MARKER.encode() in rec["body"], "upstream must receive marker bytes"
    finally:
        upstream.close()


def test_mcp_upload_to_plugin_call(tmp_path):
    """MCP 真链 upload→call_tool；marker 落结果文本与上游。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-mcp")
        app = build_mcp_http_app(str(spec), upload_dir=str(upload_dir))
        up = TestClient(app).post(
            "/upload",
            files={"file": ("chart.yaml", b"blob: " + _MARKER.encode(), "text/yaml")},
        )
        assert up.status_code == 200, up.text
        server_path = up.json()["path"]

        executor = MCPExecutor(str(spec), transport="http", upload_dir=str(upload_dir))
        result = _call(
            executor, _CREATE_TARGET, {"title": "ops", "chart_file": server_path}
        )
        assert result.is_error is False, result.content[0].text
        assert _MARKER in result.content[0].text

        assert len(upstream.records) == 1
        assert _MARKER.encode() in upstream.records[0]["body"]
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# Negatives: deleted file guidance + stdio no-jail contrast
# ---------------------------------------------------------------------------


def test_mcp_deleted_file_is_error_guidance(tmp_path):
    """删除服务端文件后重调 → isError 缺失指引，上游零调用。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-del")
        app = build_mcp_http_app(str(spec), upload_dir=str(upload_dir))
        up = TestClient(app).post(
            "/upload",
            files={"file": ("chart.yaml", b"blob: " + _MARKER.encode(), "text/yaml")},
        )
        server_path = up.json()["path"]
        Path(server_path).unlink()  # 过期/清理服务端文件

        executor = MCPExecutor(str(spec), transport="http", upload_dir=str(upload_dir))
        result = _call(
            executor, _CREATE_TARGET, {"title": "ops", "chart_file": server_path}
        )
        assert result.is_error is True, result.content[0].text
        text = result.content[0].text
        assert "POST /upload" in text, text
        assert ("已过期" in text) or ("不存在" in text), text
        assert upstream.records == [], "missing file must never reach upstream"
    finally:
        upstream.close()


def test_stdio_same_shape_no_jail(tmp_path):
    """stdio 同形成功：jail 外本地文件直传，无需上传。"""
    upstream = MockUpstream()
    try:
        spec = _write_plugin_spec(tmp_path, upstream.base_url)
        upload_dir = _upload_dir(tmp_path, "up-stdio")
        outside = tmp_path / "local-chart.yaml"
        outside.write_bytes(b"blob: " + _MARKER.encode())

        executor = MCPExecutor(str(spec), transport="stdio", upload_dir=str(upload_dir))
        result = _call(
            executor, _CREATE_TARGET, {"title": "ops", "chart_file": str(outside)}
        )
        assert result.is_error is False, result.content[0].text
        assert _MARKER in result.content[0].text
        assert len(upstream.records) == 1
        assert _MARKER.encode() in upstream.records[0]["body"]
    finally:
        upstream.close()
