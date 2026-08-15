"""Tests for MCPExecutor (方案 V2 / V3 / V5).

Covers:
- execute_command 走 serve 执行内核路径（lookup → build_service_context →
  _bridge_file_params → execute_pipeline），结果脱敏
- file 参数 base64 桥接 + 临时文件清理
- flow 执行（step_cb 汇总）
- 错误路径：未知 tool → ValueError；执行异常 → is_error CallToolResult
- --server 覆盖（R7）：显式参数传给 build_service_context，不污染全局 env
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult

from cliyard.server.mcp.executor import MCPExecutor

from tests.mcp_helpers import write_spec

_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


def test_tool_specs_loaded_on_init():
    ex = MCPExecutor(_FIXTURES_SPEC)
    assert {"repos.list", "repos.create"} <= set(ex.tool_specs)


def test_grouped_resource_command_executes_via_lookup(tmp_path, monkeypatch):
    """分组资源工具（order.list）可经 _lookup_resource_method 真正执行。

    回归 CRITICAL#1：工具名必须是 resource.method（不带 group 前缀），
    否则 _lookup_resource_method 无法解析、调用必然 is_error。
    """
    spec = write_spec(tmp_path, "http://127.0.0.1:1")
    captured: dict = {}

    def fake_execute_pipeline(**kwargs):
        captured["target_resource"] = kwargs["resource_spec"]["name"]
        captured["target"] = kwargs["resource_name"]
        return {"items": [{"name": "order-a"}], "total": 1, "fields": [{"name": "name"}]}

    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_execute_pipeline)

    ex = MCPExecutor(spec)
    assert "order.list" in ex.tool_specs

    result = ex.execute_command("order.list", {"status": "placed"})
    assert captured["target_resource"] == "order"
    assert result["total"] == 1


def test_execute_command_reuses_execution_kernel(monkeypatch):
    """execute_command 复用 lookup/context/bridge/execute_pipeline。"""
    seen: dict = {}

    def fake_lookup(target, service):
        seen["target"] = target
        resource = {"name": "repos", "path": "repos", "methods": {"list": {}}}
        method = {"http": {"method": "GET", "path": "repos"}, "params": {}}
        return resource, method

    def fake_build_context(spec_dir, service, resource, base_url_override=None):
        seen["ctx_resource"] = resource["name"]
        return object()

    def fake_execute_pipeline(**kwargs):
        seen["kwargs"] = kwargs
        return {"items": [{"name": "x"}], "total": 1, "fields": [{"name": "name"}]}

    monkeypatch.setattr("cliyard.server.mcp.executor._lookup_resource_method", fake_lookup)
    monkeypatch.setattr("cliyard.server.mcp.executor.build_service_context", fake_build_context)
    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_execute_pipeline)

    ex = MCPExecutor(_FIXTURES_SPEC)
    result = ex.execute_command("repos.list", {"page": 2})

    assert seen["target"] == "repos.list"
    assert seen["ctx_resource"] == "repos"
    assert seen["kwargs"]["service_ctx"] is not None
    assert seen["kwargs"]["kwargs"] == {"page": 2}
    assert result == {"items": [{"name": "x"}], "total": 1, "fields": [{"name": "name"}]}


def test_execute_command_redacts_sensitive_result(monkeypatch):
    """结果经 redact_sensitive 脱敏（token/Authorization 等 → ***）。"""

    def fake_execute_pipeline(**kwargs):
        return {"ok": True, "token": "abc123", "headers": {"Authorization": "Bearer z"}}

    monkeypatch.setattr(
        "cliyard.server.mcp.executor._lookup_resource_method",
        lambda target, service: (
            {"name": "repos", "methods": {"list": {}}},
            {"http": {"method": "GET"}, "params": {}},
        ),
    )
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.build_service_context",
        lambda spec_dir, service, resource, base_url_override=None: object(),
    )
    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_execute_pipeline)

    ex = MCPExecutor(_FIXTURES_SPEC)
    result = ex.execute_command("repos.list", {})
    assert result["ok"] is True
    assert result["token"] == "***"
    assert result["headers"] == {"Authorization": "***"}
    assert "abc123" not in str(result)
    assert "Bearer z" not in str(result)


def test_execute_command_bridges_file_param_and_cleans_up(monkeypatch):
    """file 参数 base64 → 临时文件路径交给内核；执行后清理。"""
    captured: dict = {}

    def fake_execute_pipeline(**kwargs):
        captured["file"] = kwargs["kwargs"]["file"]
        assert os.path.exists(kwargs["kwargs"]["file"]), "执行期间临时文件应存在"
        return {"ok": True}

    monkeypatch.setattr(
        "cliyard.server.mcp.executor._lookup_resource_method",
        lambda target, service: (
            {"name": "upload", "methods": {"put": {}}},
            {
                "http": {"method": "POST", "path": "upload"},
                "params": {"body": [{"name": "file", "type": "file"}]},
            },
        ),
    )
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.build_service_context",
        lambda spec_dir, service, resource, base_url_override=None: object(),
    )
    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_execute_pipeline)

    payload = base64.b64encode(b"hello file").decode()
    ex = MCPExecutor(_FIXTURES_SPEC)
    result = ex.execute_command("upload.put", {"file": f"data:text/plain;base64,{payload}"})

    file_param = captured["file"]
    assert os.path.basename(file_param).startswith("cliyard-upload-")
    assert not os.path.exists(file_param), "执行结束后临时文件应被清理"
    assert result == {"ok": True}


def test_execute_flow_collects_step_summary(monkeypatch):
    """flow 执行经 step_cb 汇总 step 结果。"""

    def fake_run_flow(flow_spec, params, service_ctx, service, step_cb=None):
        step_cb("step_start", {"index": 1, "id": "s1"})
        step_cb("step_done", {"id": "s1", "label": "第一步", "status": "ok", "result_preview": "1"})
        step_cb("flow_end", {"outcome": "completed", "step_count": 1})

    monkeypatch.setattr(
        "cliyard.server.mcp.executor.build_service_context",
        lambda spec_dir, service, base_url_override=None: object(),
    )
    monkeypatch.setattr("cliyard.server.mcp.executor.run_flow", fake_run_flow)

    ex = MCPExecutor(_FIXTURES_SPEC)
    flow_spec = SimpleNamespace(command="add-user")
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.MCPExecutor._load_flows",
        lambda self: [flow_spec],
    )

    result = ex.execute_flow("add-user", {"name": "alice"})
    assert result["outcome"] == "completed"
    assert result["step_count"] == 1
    assert result["steps"][0]["id"] == "s1"
    assert result["steps"][0]["status"] == "ok"


def test_execute_flow_unknown_raises(monkeypatch):
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.MCPExecutor._load_flows", lambda self: []
    )
    ex = MCPExecutor(_FIXTURES_SPEC)
    with pytest.raises(ValueError, match="not found"):
        ex.execute_flow("ghost-flow", {})


def test_call_tool_unknown_tool_raises():
    """未知 tool 抛 ValueError（→ MCP 协议层错误，非 is_error 结果）。"""
    ex = MCPExecutor(_FIXTURES_SPEC)
    with pytest.raises(ValueError, match="Unknown tool"):
        import anyio

        async def _go():
            return await ex.call_tool(None, type("P", (), {"name": "no.such", "arguments": {}})())

        anyio.run(_go)


def test_call_tool_execution_error_returns_is_error(monkeypatch):
    """执行异常 → CallToolResult(is_error=True) + 错误消息。"""

    def boom(**kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", boom)

    import anyio

    ex = MCPExecutor(_FIXTURES_SPEC)
    params = type("P", (), {"name": "repos.list", "arguments": {}})()

    async def _go():
        return await ex.call_tool(None, params)

    result = anyio.run(_go)
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert "upstream exploded" in result.content[0].text


def test_call_tool_error_message_is_sanitized(monkeypatch):
    """错误消息中的 spec 绝对路径被脱敏（复用 _sanitize_error，防路径泄漏）。"""

    def boom(**kwargs):
        raise RuntimeError(f"failed reading {_FIXTURES_SPEC}/_auth.yaml")

    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", boom)

    import anyio

    ex = MCPExecutor(_FIXTURES_SPEC)
    params = type("P", (), {"name": "repos.list", "arguments": {}})()

    async def _go():
        return await ex.call_tool(None, params)

    result = anyio.run(_go)
    assert result.is_error is True
    text = result.content[0].text
    assert str(_FIXTURES_SPEC) not in text, "spec 绝对路径不应泄漏给 MCP 客户端"
    assert "<spec_dir>" in text


def test_call_tool_success_returns_structured_text(monkeypatch):
    """成功调用 → CallToolResult 含 JSON 文本，is_error=False。"""

    def fake_execute_pipeline(**kwargs):
        return {"items": [{"name": "repo-a"}], "total": 1, "fields": [{"name": "name"}]}

    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_execute_pipeline)

    import anyio

    ex = MCPExecutor(_FIXTURES_SPEC)
    params = type("P", (), {"name": "repos.list", "arguments": {"page": 1}})()

    async def _go():
        return await ex.call_tool(None, params)

    result = anyio.run(_go)
    assert result.is_error is False
    assert "repo-a" in result.content[0].text


def test_executor_server_override_passed_to_context(monkeypatch):
    """server_override 作为 base_url_override 传入 build_service_context（不污染 env）。"""
    monkeypatch.delenv("CLIYARD_SERVER", raising=False)
    seen: dict = {}

    def fake_build_context(spec_dir, service, resource=None, base_url_override=None):
        seen["override"] = base_url_override
        return object()

    monkeypatch.setattr(
        "cliyard.server.mcp.executor.build_service_context", fake_build_context
    )
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.execute_pipeline", lambda **k: {}
    )

    ex = MCPExecutor(_FIXTURES_SPEC, server_override="https://override.example.com")
    ex.execute_command("repos.list", {})
    assert seen["override"] == "https://override.example.com"
    # 不写全局环境变量
    assert "CLIYARD_SERVER" not in __import__("os").environ


def test_build_service_context_respects_server_env(monkeypatch):
    """env <SERVICE>_SERVER / CLIYARD_SERVER 由 build_service_context 解析（R7）。"""
    monkeypatch.setenv("CLIYARD_SERVER", "https://env-override.example.com")

    captured = {}

    def fake_pipeline(**kwargs):
        captured["base_url"] = kwargs["service_ctx"].base_url
        return {}

    monkeypatch.setattr("cliyard.server.mcp.executor.execute_pipeline", fake_pipeline)

    ex = MCPExecutor(_FIXTURES_SPEC)
    ex.execute_command("repos.list", {})
    assert captured["base_url"] == "https://env-override.example.com"
