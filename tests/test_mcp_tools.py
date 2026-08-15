"""Tests for MCP tool discovery / registration (方案 V1).

Covers:
- 工具命名与 /api/execute target 对齐：无 group（user.list）/ 有 group
  （order.list，不带 group 前缀）/ flow（flow.add_user → target add-user）
- inputSchema 与 build_command_tree（=/api/spec）一致性：required / enum /
  multiple→array / file→format:binary / json|object→object
- Tool 元数据（name/description/input_schema）可通过 ``as_tool()`` 转换
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cliyard.server.mcp.tools import build_tool_specs
from cliyard.server.schema_bridge import build_command_tree

_DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "demo"
_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


def test_flat_resource_tool_names_align_with_execute_target():
    """无 group 资源：tool name == <resource>.<method>（= /api/execute target）。"""
    specs = build_tool_specs(_DEMO_DIR)
    assert "user.list" in specs
    assert "user.create" in specs
    assert "pet.list" in specs
    for name, spec in specs.items():
        if spec.kind == "command":
            assert spec.target == name, f"command tool target must equal name: {name}"


def test_grouped_resource_tool_names_use_resource_method():
    """有 group 资源：tool name == <resource>.<method>（= /api/execute target）。

    执行内核 ``_lookup_resource_method`` 只解析 ``resource.method``，因此工具名
    不带 group 前缀（group 仅供描述分组，不入命名）。
    """
    specs = build_tool_specs(_DEMO_DIR)
    assert "order.list" in specs
    assert "order.place" in specs
    assert specs["order.list"].kind == "command"
    assert specs["order.list"].target == "order.list"
    # 描述里仍可带组名（订单管理）以帮助 LLM 理解归属
    assert "订单管理" in (specs["order.list"].description or "")


def test_grouped_tool_target_resolvable_by_lookup():
    """分组资源工具 target 可被 _lookup_resource_method 解析（不抛 ValueError）。"""
    from cliyard.engine.loader import load_service
    from cliyard.engine.orchestrator import _lookup_resource_method

    service = load_service(_DEMO_DIR)
    resource, method_spec = _lookup_resource_method("order.list", service)
    assert resource["name"] == "order"
    assert method_spec.get("http", {}).get("method", "GET").upper() == "GET"
    assert resource.get("group") == "store"


def test_flow_tool_names_and_targets():
    """flow：tool name flow.<command-with-underscores>，target 保持原始 command。"""
    specs = build_tool_specs(_DEMO_DIR)
    assert "flow.add_user" in specs
    spec = specs["flow.add_user"]
    assert spec.kind == "flow"
    assert spec.target == "add-user"  # 与 /api/execute kind=flow 对齐


def test_tool_input_schema_matches_command_tree():
    """inputSchema 与 build_command_tree（/api/spec 同源）完全一致。"""
    specs = build_tool_specs(_DEMO_DIR)
    tree = build_command_tree(_DEMO_DIR)
    groups = {g["group"]: g for g in tree["groups"]}

    store = groups["store"]
    order = next(r for r in store["resources"] if r["name"] == "order")
    order_cmds = {c["name"]: c for c in order["commands"]}

    mcp_schema = specs["order.place"].input_schema
    api_schema = order_cmds["place"]["schema"]
    assert mcp_schema == api_schema
    # required / enum / default 透传
    assert mcp_schema["required"] == ["pet_id"]
    assert mcp_schema["properties"]["quantity"]["default"] == 1
    assert mcp_schema["properties"]["ship_date"]["type"] == "string"


def test_schema_type_mapping_in_enum_and_file():
    """enum→string+enum；file→string format:binary；multiple→array；json→object。"""
    from cliyard.server.schema_bridge import params_to_json_schema

    schema = params_to_json_schema(
        {
            "query": [{"name": "status", "type": "enum", "choices": ["a", "b"]}],
            "body": [
                {"name": "file", "type": "file"},
                {"name": "tags", "type": "string", "multiple": True},
                {"name": "meta", "type": "object"},
            ],
        },
        title="t",
    )
    props = schema["properties"]
    assert props["status"] == {
        "type": "string",
        "enum": ["a", "b"],
        "x-location": "query",
    }
    assert props["file"] == {"type": "string", "format": "binary", "x-location": "body"}
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "x-location": "body",
    }
    assert props["meta"] == {"type": "object", "x-location": "body"}


def test_tool_as_tool_metadata():
    """as_tool() 产出 MCP Tool（name/description/input_schema）。"""
    specs = build_tool_specs(_DEMO_DIR)
    tool = specs["user.list"].as_tool()
    assert tool.name == "user.list"
    assert "HTTP GET users" in (tool.description or "")
    assert tool.input_schema["type"] == "object"


def test_fixtures_spec_has_repos_tools():
    """fixtures/spec-dir：扁平 repos 资源映射为 repos.list / repos.create。"""
    specs = build_tool_specs(_FIXTURES_SPEC)
    assert set(specs) == {"repos.list", "repos.create"}
    create = specs["repos.create"]
    assert create.input_schema["required"] == ["name"]


_DUP_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dup"


def test_duplicate_resource_names_get_group_prefix():
    """跨组同名资源：tool name/target 用 group.resource.method 消歧。"""
    specs = build_tool_specs(_DUP_SPEC)
    assert "admin.templates.list" in specs
    assert "alert.templates.list" in specs
    assert "dc.token.list" in specs
    assert "dc.token.create" in specs
    assert "setting.token.list" in specs
    assert "setting.token.create" in specs
    assert "setting.token.delete" in specs
    # 消歧工具 target 保持三段（执行内核支持 group.resource.method）
    assert specs["admin.templates.list"].target == "admin.templates.list"
    assert specs["alert.templates.list"].target == "alert.templates.list"
    assert specs["setting.token.delete"].target == "setting.token.delete"


def test_duplicate_resource_tools_resolvable_by_lookup():
    """消歧后的三段 target 可被 _lookup_resource_method 解析到正确资源。"""
    from cliyard.engine.loader import load_service
    from cliyard.engine.orchestrator import _lookup_resource_method

    service = load_service(_DUP_SPEC)
    admin_tpl, _ = _lookup_resource_method("admin.templates.list", service)
    assert admin_tpl["name"] == "templates"
    assert admin_tpl["group"] == "admin"
    alert_tpl, _ = _lookup_resource_method("alert.templates.list", service)
    assert alert_tpl["group"] == "alert"
    setting_tok, _ = _lookup_resource_method("setting.token.create", service)
    assert setting_tok["group"] == "setting"
    dc_tok, _ = _lookup_resource_method("dc.token.create", service)
    assert dc_tok["group"] == "dc"
    setting_del, _ = _lookup_resource_method("setting.token.delete", service)
    assert setting_del["group"] == "setting"


def test_ambiguous_resource_method_raises():
    """同名资源用无 group 的 resource.method 应报歧义错误。"""
    from cliyard.engine.loader import load_service
    from cliyard.engine.orchestrator import _lookup_resource_method

    service = load_service(_DUP_SPEC)
    with pytest.raises(ValueError, match="ambiguous"):
        _lookup_resource_method("templates.list", service)
    with pytest.raises(ValueError, match="ambiguous"):
        _lookup_resource_method("token.list", service)


def test_unique_resource_unaffected_by_duplicates():
    """唯一资源名仍用 resource.method，不受同名消歧影响。"""
    specs = build_tool_specs(_DUP_SPEC)
    assert "user.list" in specs
    assert specs["user.list"].target == "user.list"
