"""Tests for case execution support in the serve engine (ExecutionManager + API).

Covers:
- ``submit_case`` creates an Execution with kind="case" and returns an id
- ``_run_case`` matches a known case by name and drives ``run_case`` with
  ``step_cb`` threaded through (no real network — monkeypatched)
- unknown case name pushes an error event with status=error
- POST /api/execute accepts kind="case" and returns an execution_id
- POST /api/execute still rejects a bogus kind with 400

The case runner work is monkeypatched so no real flow/network traffic runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from cliyard.engine.flow import CaseSpec
from cliyard.server import executor as executor_mod
from cliyard.server.app import create_app

_CASES_SPEC = Path(__file__).resolve().parent / "fixtures" / "cases_demo"


def _fake_run_case(case, spec_dir, service_ctx, service_spec, **kwargs):
    """Stand-in for run_case that emits a step_start event via step_cb."""
    assert case.name == "smoke-basic"
    cb = kwargs["step_cb"]
    assert cb is not None
    cb("step_start", {"id": "create_introduce", "status": "ok"})
    cb("flow_end", {"outcome": "completed"})
    return {
        "case": case.name,
        "flow": case.flow,
        "rows": [{"case": "smoke-basic", "data_row": None, "outcome": "completed",
                  "all_pass": True, "elapsed": 0.01}],
        "pass_count": 1,
        "fail_count": 0,
        "all_pass": True,
    }


# ===========================================================================
# ExecutionManager — submit_case
# ===========================================================================


def test_submit_case_creates_execution(monkeypatch):
    """submit_case 返回 id 且 Execution.kind == 'case'。"""
    monkeypatch.setattr(executor_mod, "load_cases", lambda spec_dir: [
        CaseSpec(name="smoke-basic", flow="supplier-introduce", params={})
    ])
    monkeypatch.setattr(executor_mod, "run_case", _fake_run_case)
    # 避免真实 history DB 写入
    mgr = executor_mod.ExecutionManager()
    mgr.history_store = MagicMock()
    monkeypatch.setattr(executor_mod, "execution_manager", mgr, raising=False)

    execution_id = executor_mod.execution_manager.submit_case(
        str(_CASES_SPEC), "smoke-basic", {"extra": 1}
    )
    assert execution_id
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.kind == "case"
    assert execution.target == "smoke-basic"
    assert execution.params == {"extra": 1}
    assert execution.status == "running"

    assert execution.done_event.wait(5)
    assert execution.status == "done"


def test_create_execution_registers_kind_case():
    """_create_execution 接受 kind='case' 并注册（无需启动线程）。"""
    mgr = executor_mod.ExecutionManager()
    mgr.history_store = MagicMock()
    execution = mgr._create_execution(str(_CASES_SPEC), "case", "smoke-basic", {})
    assert execution.kind == "case"
    assert mgr.get(execution.id) is not None


def test_run_case_unknown_name_pushes_error(monkeypatch):
    """未知 case 名称 → error 事件 + status=error + done 收尾。"""
    mgr = executor_mod.ExecutionManager()
    mgr.history_store = MagicMock()
    monkeypatch.setattr(executor_mod, "execution_manager", mgr, raising=False)
    # load_cases 返回空 → 找不到匹配 case
    monkeypatch.setattr(executor_mod, "load_cases", lambda spec_dir: [])

    execution_id = executor_mod.execution_manager.submit_case(str(_CASES_SPEC), "ghost-case", {})
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "error"


def test_run_case_step_cb_threads_events(monkeypatch):
    """_run_case 的 step_cb 透传，事件进 steps/queue（SSE 可见）。"""
    mgr = executor_mod.ExecutionManager()
    mgr.history_store = MagicMock()
    monkeypatch.setattr(executor_mod, "execution_manager", mgr, raising=False)
    monkeypatch.setattr(executor_mod, "load_cases", lambda spec_dir: [
        CaseSpec(name="smoke-basic", flow="supplier-introduce", params={})
    ])
    monkeypatch.setattr(executor_mod, "run_case", _fake_run_case)

    execution_id = executor_mod.execution_manager.submit_case(
        str(_CASES_SPEC), "smoke-basic", {"supplier_name": "测试供应商A"}
    )
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "done"

    # 事件在返回值中被 _run_case 之外记录 —— 这里直接校验 steps 快照
    assert [s["type"] for s in list(execution.steps)] == [
        "step_start",
        "flow_end",
        "done",
    ]
    assert execution.steps[0]["id"] == "create_introduce"


# ===========================================================================
# HTTP API
# ===========================================================================


@pytest.fixture()
def client(monkeypatch):
    """TestClient over fixtures/cases_demo with run_case monkeypatched."""
    monkeypatch.setattr(executor_mod, "load_cases", lambda spec_dir: [
        CaseSpec(name="smoke-basic", flow="supplier-introduce", params={})
    ])
    monkeypatch.setattr(executor_mod, "run_case", _fake_run_case)
    return TestClient(create_app(str(_CASES_SPEC)))


def test_api_execute_accepts_kind_case(client):
    """POST /api/execute with kind=case returns execution_id + kind case."""
    resp = client.post(
        "/api/execute",
        json={"kind": "case", "target": "smoke-basic", "params": {"supplier_name": "x"}},
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]
    assert len(execution_id) == 32

    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.kind == "case"
    assert execution.done_event.wait(5)
    assert execution.status == "done"


def test_api_execute_case_unknown_target_returns_id(client, monkeypatch):
    """未知 case 名称也返回 execution_id（后台线程 push error 事件）。"""
    monkeypatch.setattr(executor_mod, "load_cases", lambda spec_dir: [])
    resp = client.post(
        "/api/execute", json={"kind": "case", "target": "no-case", "params": {}}
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "error"


def test_api_execute_rejects_bogus_kind_400(client):
    """bogus kind 依旧 400（不破坏原有校验）。"""
    resp = client.post(
        "/api/execute", json={"kind": "bogus", "target": "smoke-basic", "params": {}}
    )
    assert resp.status_code == 400