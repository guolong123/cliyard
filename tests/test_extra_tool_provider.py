"""Generic extension-point tests (no product semantics)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from cliyard.server.mcp.executor import MCPExecutor, register_tool_executor
from cliyard.server.mcp.tools import ToolSpec, build_tool_specs, register_tool_provider

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spec-dir"
_NAME = "demo_extra_tool"


def test_extra_tool_provider_extends_tools_list():
    register_tool_provider(lambda: {
        _NAME: ToolSpec(name=_NAME, kind="custom", target="", description="demo", input_schema={"type": "object"})
    })
    specs = build_tool_specs(_FIXTURES)
    assert _NAME in specs
    assert specs[_NAME].kind == "custom"


def test_custom_tool_executor_dispatch():
    register_tool_provider(lambda: {
        _NAME: ToolSpec(name=_NAME, kind="custom", target="", description="demo", input_schema={"type": "object"})
    })
    register_tool_executor(_NAME, lambda arguments: {"echo": arguments})
    ex = MCPExecutor(str(_FIXTURES))
    params = type("P", (), {"name": _NAME, "arguments": {"x": 1}})
    result = asyncio.run(ex.call_tool(None, params))
    assert result.is_error is False
    assert '"x": 1' in result.content[0].text
