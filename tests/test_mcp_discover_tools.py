from __future__ import annotations

from pathlib import Path

from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.tools import (
    build_discover_catalog,
    build_tool_specs,
    search_catalog,
)

from tests.mcp_helpers import write_spec

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


def test_discover_meta_tools_only():
    specs = build_tool_specs(_FIXTURES, mode="discover")
    assert set(specs) == {"search", "describe", "call", "catalog"}
    assert specs["search"].kind == "discover_search"
    assert specs["describe"].kind == "discover_describe"
    assert specs["call"].kind == "discover_call"
    assert specs["catalog"].kind == "discover_catalog"


def test_catalog_covers_all_flat_tools():
    flat = build_tool_specs(_FIXTURES, mode="flat")
    catalog = build_discover_catalog(_FIXTURES)
    assert set(catalog) == set(flat)


def test_flat_and_grouped_unchanged_by_discover():
    flat = build_tool_specs(_FIXTURES, mode="flat")
    grouped = build_tool_specs(_FIXTURES, mode="grouped")
    discover = build_tool_specs(_FIXTURES, mode="discover")
    assert not set(discover) & set(flat)
    assert not set(discover) & set(grouped)


def test_search_hits_keyword():
    catalog = build_discover_catalog(_FIXTURES)
    hits = search_catalog(catalog, "repos", 5)
    assert any(item["name"].startswith("repos.") for item in hits)
    assert all({"name", "description"} <= set(item) for item in hits)


def test_search_limit_applied():
    catalog = build_discover_catalog(_FIXTURES)
    assert len(search_catalog(catalog, "list", 1)) <= 1


def test_executor_discover_exposes_meta_but_full_catalog():
    ex = MCPExecutor(_FIXTURES, tool_mode="discover")
    assert set(ex.tool_specs) == {"search", "describe", "call", "catalog"}
    assert set(ex._catalog) == set(build_tool_specs(_FIXTURES, mode="flat"))


def test_describe_matches_flat_registration():
    ex = MCPExecutor(_FIXTURES, tool_mode="discover")
    flat = build_tool_specs(_FIXTURES, mode="flat")
    for name in flat:
        described = ex.execute_spec(ex.tool_specs["describe"], {"name": name})
        assert described["input_schema"] == flat[name].input_schema
        assert described["description"] == flat[name].description


def test_call_routes_to_catalog_tool(monkeypatch, tmp_path):
    spec = write_spec(tmp_path, "http://127.0.0.1:1")
    monkeypatch.setattr(
        "cliyard.server.mcp.executor.execute_pipeline",
        lambda **kwargs: {
            "items": [{"name": "r1"}],
            "total": 1,
            "fields": [{"name": "name"}],
        },
    )
    ex = MCPExecutor(spec, tool_mode="discover")
    direct = ex.execute_spec(ex._catalog["repos.list"], {})
    via = ex.execute_spec(
        ex.tool_specs["call"], {"name": "repos.list", "arguments": {}}
    )
    assert via == direct


def test_describe_unknown_tool_errors():
    ex = MCPExecutor(_FIXTURES, tool_mode="discover")
    try:
        ex.execute_spec(ex.tool_specs["describe"], {"name": "does.not.exist"})
    except ValueError as exc:
        assert "does.not.exist" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown tool")


def test_catalog_filter():
    ex = MCPExecutor(_FIXTURES, tool_mode="discover")
    out = ex.execute_spec(ex.tool_specs["catalog"], {"query": "repos."})
    assert out["count"] >= 1
    assert all(item["name"].startswith("repos.") for item in out["tools"])
