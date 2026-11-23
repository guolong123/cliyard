"""auth 管理命令 → MCP 工具注册 / 执行测试。

覆盖：
* flat 模式注册 auth.add/status/switch/rm，schema 从真实 click 命令派生
* grouped 模式合并为单条 auth 工具，operation 选子命令
* discover 模式不直接暴露 auth（catalog 内可见）
* 真 call_tool 跑通 add/status/switch/rm（凭据写临时目录，隔离宿主）
* 命名与 cmd.* / resource.method 命名空间隔离
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, Tool

from cliyard.client import credentials as cred
from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.tools import build_auth_tool_specs, build_tool_specs

# 独立最小 spec（无 plugins），避免 discover_plugins 污染其他测试全局状态。
_DEMO = Path(__file__).resolve().parent / "fixtures" / "spec-auth"


@pytest.fixture()
def isolated_credentials(tmp_path, monkeypatch):
    """凭据写临时目录，避免污染 ~/.cliyard/credentials.yaml。"""
    monkeypatch.setattr(cred, "CLIYARD_DIR", str(tmp_path))
    monkeypatch.setattr(cred, "CREDENTIALS_PATH", str(tmp_path / "credentials.yaml"))
    # rich 模块级 Console 持有构造时的 stdout；测试间重置其 file 引用。
    from cliyard.runtime import auth_commands as auth_mod

    original_file = auth_mod._console.file
    yield tmp_path
    auth_mod._console.file = original_file


def _call(ex: MCPExecutor, name: str, arguments: dict):
    result: CallToolResult = asyncio.run(
        ex.call_tool(None, SimpleNamespace(name=name, arguments=arguments))
    )
    return result


def _text(result: CallToolResult) -> str:
    return getattr(result.content[0], "text", "")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


def test_flat_mode_registers_auth_tools():
    specs = build_tool_specs(_DEMO, mode="flat")
    for name in ("auth.add", "auth.status", "auth.switch", "auth.rm"):
        assert name in specs, f"missing MCP tool {name}"
        assert specs[name].kind == "auth"
        assert specs[name].target == name


def test_auth_tool_schema_from_click_params():
    specs = build_tool_specs(_DEMO, mode="flat")
    props = specs["auth.add"].input_schema["properties"]
    assert "name" in props
    assert props["token"]["type"] == "string"
    assert props["default"]["type"] == "boolean"
    assert props["set"]["type"] == "array"
    assert "server_main" in props  # 动态 server 选项（来自 spec.servers）

    switch_props = specs["auth.switch"].input_schema["properties"]
    assert "env_name" in switch_props
    assert specs["auth.status"].input_schema["properties"] == {}


def test_auth_tool_as_tool_metadata():
    specs = build_tool_specs(_DEMO, mode="flat")
    tool: Tool = specs["auth.add"].as_tool()
    assert tool.name == "auth.add"
    assert tool.input_schema["type"] == "object"


def test_grouped_mode_single_auth_tool():
    specs = build_tool_specs(_DEMO, mode="grouped")
    assert "auth" in specs
    assert specs["auth"].kind == "grouped"
    assert set(specs["auth"].operations or {}) == {"add", "status", "switch", "rm"}
    assert "operation" in specs["auth"].input_schema["properties"]


def test_discover_mode_no_direct_auth_tools():
    specs = build_tool_specs(_DEMO, mode="discover")
    assert not any(n.startswith("auth") for n in specs)
    # catalog（flat）包含 auth 工具
    from cliyard.server.mcp.tools import build_discover_catalog

    catalog = build_discover_catalog(_DEMO)
    assert "auth.add" in catalog


def test_auth_tools_isolated_from_other_namespaces():
    specs = build_tool_specs(_DEMO, mode="flat")
    auth_names = {n for n in specs if n.startswith("auth.")}
    assert auth_names == {"auth.add", "auth.status", "auth.switch", "auth.rm"}
    # resource.method 命名空间未被 auth 覆盖
    assert any(n.startswith("pets.") for n in specs)


def test_auth_specs_with_no_auth_config_still_register(tmp_path):
    """即使 spec 无 auth 配置，add_auth_commands 仍建空壳 auth 组，工具照常注册。"""
    spec = tmp_path / "no-auth"
    spec.mkdir()
    (spec / "pets.yaml").write_text(
        "path: pets\nmethods:\n  list:\n    http: {method: GET}\n", encoding="utf-8"
    )
    # _auth.yaml 只含 service 信息，不含 auth: 段
    (spec / "_auth.yaml").write_text(
        "name: no-auth\nserver:\n  main:\n    base_url: http://127.0.0.1:1\n",
        encoding="utf-8",
    )
    specs = build_auth_tool_specs(spec)
    assert "auth.add" in specs


# ---------------------------------------------------------------------------
# 执行（真 call_tool）
# ---------------------------------------------------------------------------


def test_call_tool_auth_add_and_status(isolated_credentials):
    ex = MCPExecutor(str(_DEMO))
    add = _call(ex, "auth.add", {
        "name": "demo",
        "token": "my-token",
        "endpoint": "https://x.example",
        "default": True,
    })
    assert add.is_error is False
    assert "Token saved" in _text(add)

    status = _call(ex, "auth.status", {})
    assert status.is_error is False
    assert "demo" in _text(status)
    assert "my-token" in _text(status)


def test_call_tool_auth_switch_and_rm(isolated_credentials):
    ex = MCPExecutor(str(_DEMO))
    _call(ex, "auth.add", {"name": "a", "token": "t1"})
    _call(ex, "auth.add", {"name": "b", "token": "t2"})

    switched = _call(ex, "auth.switch", {"env_name": "b"})
    assert switched.is_error is False
    assert "Switched" in _text(switched)

    removed = _call(ex, "auth.rm", {"env_name": "a"})
    assert removed.is_error is False
    assert "Removed" in _text(removed)


def test_call_tool_grouped_auth(isolated_credentials):
    ex = MCPExecutor(str(_DEMO), tool_mode="grouped")
    added = _call(ex, "auth", {
        "operation": "add", "name": "g", "token": "t", "endpoint": "http://e"
    })
    assert added.is_error is False
    assert "Token saved" in _text(added)

    status = _call(ex, "auth", {"operation": "status"})
    assert status.is_error is False
    assert "g" in _text(status)


def test_call_tool_auth_add_set_extra(isolated_credentials, monkeypatch):
    ex = MCPExecutor(str(_DEMO))
    added = _call(ex, "auth.add", {
        "name": "env", "token": "t", "set": ["FOO=bar", "BAZ=qux"]
    })
    assert added.is_error is False
    # --set 变量的解析由 auth.add 回调处理，这里只确认工具参数被接收
    assert "Token saved" in _text(added)


def test_call_tool_auth_unknown_tool_raises():
    ex = MCPExecutor(str(_DEMO))
    with pytest.raises(ValueError):
        asyncio.run(ex.call_tool(None, SimpleNamespace(name="auth.nope", arguments={})))
