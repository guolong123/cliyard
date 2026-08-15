"""Tests for command-level plugin (``@register_command``) MCP support.

Covers:
- ``build_plugin_tool_specs`` registers ``cmd.<command>`` and
  ``cmd.<group>.<sub>`` tools with click-derived JSON schemas
- ``build_tool_specs`` includes plugin tools alongside resource/flow tools
- ``MCPExecutor.execute_plugin_command`` captures rich/click output, passes
  arguments (options/flags/positional/multiple), handles ``sys.exit(0)``
- tool naming stays isolated from resource tools (``resource.method``)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.types import CallToolResult, Tool

from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.tools import build_plugin_tool_specs, build_tool_specs

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spec-plugins"


def test_plugin_tool_names_and_kind():
    specs = build_plugin_tool_specs(_FIXTURES)
    names = set(specs)
    assert "cmd.hello" in names
    assert "cmd.pkg.info" in names
    assert "cmd.pkg.search" in names
    for spec in specs.values():
        assert spec.kind == "plugin"
        assert spec.target == spec.name


def test_plugin_tool_schema_from_click_params():
    specs = build_plugin_tool_specs(_FIXTURES)
    hello = specs["cmd.hello"]
    schema = hello.input_schema
    props = schema["properties"]

    assert schema["required"] == ["name"]  # positional argument
    assert props["name"] == {"type": "string"}  # click.Argument → string
    assert props["greeting"] == {"type": "string", "default": "Hello", "description": "Greeting word"}
    # is_flag → boolean（带 default False + help 透传）
    assert props["uppercase"]["type"] == "boolean"
    assert props["uppercase"]["default"] is False

    search = specs["cmd.pkg.search"]
    s_props = search.input_schema["properties"]
    assert s_props["limit"] == {"type": "integer", "default": 10, "description": "Max results"}
    assert s_props["keywords"]["type"] == "array"  # nargs=-1 → array


def test_plugin_tools_included_in_build_tool_specs():
    specs = build_tool_specs(_FIXTURES)
    assert "cmd.hello" in specs
    assert "cmd.pkg.info" in specs
    kinds = {s.kind for s in specs.values()}
    assert "plugin" in kinds


def test_tool_as_tool_metadata():
    specs = build_plugin_tool_specs(_FIXTURES)
    tool: Tool = specs["cmd.hello"].as_tool()
    assert tool.name == "cmd.hello"
    assert "Greet" in (tool.description or "")
    assert tool.input_schema["type"] == "object"


def test_execute_plugin_command_captures_rich_output():
    ex = MCPExecutor(_FIXTURES)
    result = ex.execute_spec(
        ex.tool_specs["cmd.hello"], {"name": "world", "greeting": "Hi"}
    )
    assert result == "Hi, world!"


def test_execute_plugin_command_flag_and_uppercase():
    ex = MCPExecutor(_FIXTURES)
    result = ex.execute_spec(
        ex.tool_specs["cmd.hello"],
        {"name": "world", "greeting": "hi", "uppercase": True},
    )
    assert result == "HI, WORLD!"


def test_execute_plugin_group_subcommand():
    ex = MCPExecutor(_FIXTURES)
    result = ex.execute_spec(
        ex.tool_specs["cmd.pkg.info"], {"package_name": "app", "verbose": True}
    )
    assert result == "pkg app verbose=True"


def test_execute_plugin_multiple_argument_list():
    ex = MCPExecutor(_FIXTURES)
    result = ex.execute_spec(
        ex.tool_specs["cmd.pkg.search"], {"keywords": ["a", "b"], "limit": 5}
    )
    assert result == "search ['a', 'b'] limit=5"


def test_execute_plugin_missing_optional_args_uses_defaults():
    ex = MCPExecutor(_FIXTURES)
    result = ex.execute_spec(ex.tool_specs["cmd.pkg.search"], {})
    assert result == "search ['*'] limit=10"


def test_execute_plugin_missing_required_argument_errors():
    ex = MCPExecutor(_FIXTURES)
    result: CallToolResult = asyncio.run(
        ex.call_tool(None, type("P", (), {"name": "cmd.hello", "arguments": {}}))
    )
    assert result.is_error is True
    text = getattr(result.content[0], "text", "")
    assert "Missing parameter" in text or "UsageError" in text or "Error" in text


def test_call_tool_plugin_success():
    ex = MCPExecutor(_FIXTURES)
    result: CallToolResult = asyncio.run(
        ex.call_tool(None, type("P", (), {"name": "cmd.hello", "arguments": {"name": "keta"}}))
    )
    assert result.is_error is False
    text = getattr(result.content[0], "text", "")
    assert "Hello, keta!" in text


def test_call_tool_unknown_plugin_raises():
    ex = MCPExecutor(_FIXTURES)
    with pytest.raises(ValueError):
        asyncio.run(ex.call_tool(None, type("P", (), {"name": "cmd.nonexistent", "arguments": {}})))
