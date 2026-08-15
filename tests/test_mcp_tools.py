"""Tests for MCP tool discovery / registration (方案 V1).

Covers:
- 三态命名对齐 /api/execute target：无 group（user.list）/ 有 group
  （store.order.*）/ flow（flow.add_user → target add-user）
- inputSchema 与 build_command_tree（=/api/spec）一致性：required / enum /
  multiple→array / file→format:binary / json|object→object
- Tool 元数据（name/description/input_schema）可通过 ``as_tool()`` 转换
"""

from __future__ import annotations

from pathlib import Path

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


def test_grouped_resource_three_part_tool_names():
    """有 group 资源：tool name == <group>.<resource>.<method>。"""
    specs = build_tool_specs(_DEMO_DIR)
    assert "store.order.list" in specs
    assert "store.order.place" in specs
    assert specs["store.order.list"].kind == "command"
    assert specs["store.order.list"].target == "store.order.list"


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

    mcp_schema = specs["store.order.place"].input_schema
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
