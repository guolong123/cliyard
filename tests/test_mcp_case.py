"""MCP case tool coverage: registration, dispatch, and execution.

Regression guard for the P0 finding where ``execute_case`` / ``execute_case_list``
called ``load_cases`` without importing it (tools registered, but every call
raised ``NameError``). No real flow/network traffic runs — ``run_case`` is
monkeypatched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.tools import ToolSpec, build_tool_specs
from cliyard.server.schema_bridge import case_params_schema

_CASES_SPEC = Path(__file__).resolve().parent / "fixtures" / "cases_demo"


@pytest.fixture()
def executor() -> MCPExecutor:
    return MCPExecutor(str(_CASES_SPEC))


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_build_tool_specs_registers_case_tools():
    specs = build_tool_specs(str(_CASES_SPEC))
    assert specs["case.smoke-basic"].kind == "case"
    assert specs["case.smoke-basic"].target == "smoke-basic"
    assert specs["case.csp-check"].kind == "case"
    assert specs["case.list"].kind == "case_list"
    # params schema is typed from case.params (shared with the Web UI)
    props = specs["case.smoke-basic"].input_schema["properties"]
    assert props["supplier_name"]["type"] == "string"


def test_build_tool_specs_no_cases_omits_case_tools(tmp_path):
    (tmp_path / "_auth.yaml").write_text(
        "name: empty\nauth:\n  steps: []\n", encoding="utf-8"
    )
    specs = build_tool_specs(str(tmp_path))
    assert not [n for n in specs if n.startswith("case")]


def test_case_params_schema_preserves_types():
    """Falsy defaults (0 / False) keep their type instead of becoming ""."""
    schema = case_params_schema({"n": 0, "flag": False, "name": "x"})
    assert schema["properties"]["n"] == {"type": "integer", "default": 0}
    assert schema["properties"]["flag"] == {"type": "boolean", "default": False}
    assert schema["properties"]["name"] == {"type": "string", "default": "x"}


# ---------------------------------------------------------------------------
# execute_case_list / execute_case
# ---------------------------------------------------------------------------


def test_execute_case_list_returns_metadata(executor):
    items = executor.execute_case_list()
    by_name = {c["name"]: c for c in items}
    assert set(by_name) == {"smoke-basic", "csp-check"}
    smoke = by_name["smoke-basic"]
    assert smoke["flow"] == "supplier-introduce"
    assert smoke["labels"] == ["smoke", "daily"]
    assert smoke["assert_count"] == 1
    assert smoke["has_data"] is False
    assert by_name["csp-check"]["has_data"] is True


def test_execute_case_unknown_name_raises(executor):
    with pytest.raises(ValueError, match="ghost-case"):
        executor.execute_case("ghost-case", {})


def test_execute_case_runs_and_forwards_params(executor, monkeypatch):
    seen = {}

    def fake_run_case(case, spec_dir, service_ctx, service_spec, **kwargs):
        seen["case"] = case.name
        seen["spec_dir"] = spec_dir
        seen["params_override"] = kwargs.get("params_override")
        return {
            "case": case.name,
            "flow": case.flow,
            "rows": [],
            "pass_count": 0,
            "fail_count": 0,
            "all_pass": True,
        }

    monkeypatch.setattr("cliyard.engine.case_runner.run_case", fake_run_case)

    result = executor.execute_case("smoke-basic", {"supplier_name": "示例"})
    assert result["case"] == "smoke-basic"
    assert seen["case"] == "smoke-basic"
    assert Path(seen["spec_dir"]) == _CASES_SPEC
    assert seen["params_override"] == {"supplier_name": "示例"}


# ---------------------------------------------------------------------------
# execute_spec dispatch (the reviewer's exact repro: case.list used to NameError)
# ---------------------------------------------------------------------------


def test_execute_spec_dispatches_case_list_end_to_end(executor):
    spec = ToolSpec(name="case.list", kind="case_list", target="", description="")
    out = executor.execute_spec(spec, {})
    assert {c["name"] for c in out} == {"smoke-basic", "csp-check"}


def test_execute_spec_dispatches_case(executor, monkeypatch):
    calls = {}

    def fake_execute_case(self, target, args):
        calls["case"] = (target, args)
        return {"ok": True}

    monkeypatch.setattr(MCPExecutor, "execute_case", fake_execute_case)

    spec = ToolSpec(name="case.smoke-basic", kind="case", target="smoke-basic", description="")
    assert executor.execute_spec(spec, {"a": 1}) == {"ok": True}
    assert calls["case"] == ("smoke-basic", {"a": 1})
