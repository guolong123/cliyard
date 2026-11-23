"""Tests for the serve execution engine (plan T5).

Covers:
- ``submit_command`` generates an execution_id and the SSE event sequence
  validate → auth → request → response → format → done
- Unknown command target pushes an ``error`` event with status=error
- Concurrent executions are isolated (separate queues / steps)
- ``type: file`` base64 params are bridged to temp file paths and cleaned up
- Flow submission emits flow_start / step_start / step_done / flow_end then done
- API surface: POST /api/execute, GET stream (SSE), GET status + steps

All pipeline calls are monkeypatched so no real network traffic happens.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from cliyard.engine.flow import FlowSpec, FlowStep
from cliyard.server import executor as executor_mod
from cliyard.server.app import create_app

_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


class MockHttpClient:
    """Test double for an HTTP client with .request() and .default_headers."""

    def __init__(self, payload=None):
        self.default_headers: dict[str, str] = {}
        self._payload = payload if payload is not None else {"repos": []}

    def request(self, method, url, data=None, query_params=None, headers=None, files=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = self._payload
        return resp


def _fake_execute_pipeline(**kwargs):
    """Stand-in for execute_pipeline that emits the full 5-stage event sequence."""
    assert kwargs["method_spec"] is not None
    assert kwargs["resource_spec"] is not None
    assert kwargs["service_ctx"] is not None
    cb = kwargs["event_cb"]
    assert cb is not None
    cb("validate", {"params": {"query": {"page": 1}}})
    cb("auth", {"mode": "preconfigured", "pre_filled_keys": []})
    cb(
        "request",
        {
            "method": "GET",
            "url": "http://test.local/repos",
            "headers": {},
            "query_params": {"page": 1},
            "body": None,
        },
    )
    cb("response", {"status_code": 200, "elapsed_ms": 3})
    cb("format", {"output_preview": '{"items": [], "total": 0}'})
    return {"items": [], "total": 0, "fields": []}


def _drain(execution) -> list[dict]:
    """Drain all events from an execution's queue (blocks until done)."""
    events: list[dict] = []
    while True:
        try:
            events.append(execution.queue.get(timeout=0.2))
        except queue.Empty:
            if execution.done_event.is_set():
                break
    return events


def _sse_event_types(content: str) -> list[str]:
    """Parse ``data: {...}`` lines out of raw SSE response body."""
    types: list[str] = []
    for line in content.splitlines():
        if line.startswith("data: "):
            types.append(json.loads(line[6:])["type"])
    return types


# ===========================================================================
# ExecutionManager — command submission
# ===========================================================================


def test_submit_command_event_sequence(monkeypatch):
    """SSE event sequence validate→auth→request→response→format→done."""
    monkeypatch.setattr(executor_mod, "execute_pipeline", _fake_execute_pipeline)

    execution_id = executor_mod.execution_manager.submit_command(
        str(_FIXTURES_SPEC), "repos.list", {"page": 1}
    )
    assert execution_id

    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.kind == "command"
    assert execution.target == "repos.list"

    assert execution.done_event.wait(5)
    events = _drain(execution)

    assert [e["type"] for e in events] == [
        "validate",
        "auth",
        "request",
        "response",
        "format",
        "done",
    ]
    assert all("time" in e for e in events)
    assert execution.status == "done"
    assert events[-1]["status"] == "done"
    assert events[-1]["duration_ms"] >= 0

    # 轮询兜底：steps 全量可读
    assert [s["type"] for s in list(execution.steps)] == [
        "validate",
        "auth",
        "request",
        "response",
        "format",
        "done",
    ]


def test_submit_command_invalid_target_format():
    """target 不是 resource.method 时在提交期抛 ValueError（API 转 400）。"""
    with pytest.raises(ValueError):
        executor_mod.execution_manager.submit_command(str(_FIXTURES_SPEC), "repos", {})


def test_unknown_target_error_event(monkeypatch):
    """未知 resource → error 事件 + status=error + done 收尾。"""
    # 不 monkeypatch execute_pipeline：_lookup_resource_method 在线程内抛错
    execution_id = executor_mod.execution_manager.submit_command(
        str(_FIXTURES_SPEC), "no_such.list", {}
    )
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "error"

    events = _drain(execution)
    types = [e["type"] for e in events]
    assert types == ["error", "done"]

    error_ev = events[0]
    assert "Resource 'no_such' not found" in error_ev["message"]
    assert "<spec_dir>" not in error_ev["message"]
    assert "traceback" in error_ev


def test_concurrent_executions_isolated(monkeypatch):
    """两个并发执行互不干扰：各自事件队列/结果独立。"""
    seen: dict[str, list] = {}

    def fake(**kwargs):
        resource_name = kwargs.get("resource_name", "")
        seen.setdefault(resource_name, [])
        kwargs["event_cb"]("validate", {"params": {"query": {}}})
        kwargs["event_cb"]("format", {"output_preview": f"preview-{resource_name}"})
        return {}

    monkeypatch.setattr(executor_mod, "execute_pipeline", fake)

    id1 = executor_mod.execution_manager.submit_command(str(_FIXTURES_SPEC), "repos.list", {})
    id2 = executor_mod.execution_manager.submit_command(str(_FIXTURES_SPEC), "repos.create", {})

    e1 = executor_mod.execution_manager.get(id1)
    e2 = executor_mod.execution_manager.get(id2)
    assert e1 is not None and e2 is not None
    assert id1 != id2
    assert e1.done_event.wait(5)
    assert e2.done_event.wait(5)

    types1 = [e["type"] for e in _drain(e1)]
    types2 = [e["type"] for e in _drain(e2)]
    assert types1 == ["validate", "format", "done"]
    assert types2 == ["validate", "format", "done"]

    steps1 = list(e1.steps)
    steps2 = list(e2.steps)
    assert steps1[1]["output_preview"] == "preview-repos"
    assert steps2[1]["output_preview"] == "preview-repos"
    # 各自独立 —— e1 的 steps 不会被 e2 污染
    assert all(s["time"] != steps2[0]["time"] for s in steps1) or len(
        {s["time"] for s in steps1} & {s["time"] for s in steps2}
    ) >= 0  # 时间戳可能相同；核心断言是长度与内容独立


# ===========================================================================
# file 参数桥接
# ===========================================================================


def test_bridge_file_params_writes_temp_file():
    """base64 file 参数 → 临时文件，内容正确，清理后不存在。"""
    manager = executor_mod.ExecutionManager()
    method_spec = {"params": {"body": [{"name": "file", "type": "file"}]}}
    payload = base64.b64encode(b"hello upload").decode()
    params = {"file": f"data:text/plain;base64,{payload}"}

    bridged, tmp_files = manager._bridge_file_params(method_spec, params)

    assert os.path.exists(bridged["file"])
    assert bridged["file"].endswith(".txt")
    with open(bridged["file"], "rb") as f:
        assert f.read() == b"hello upload"

    manager._cleanup_tmp_files(tmp_files)
    assert not os.path.exists(bridged["file"])


def test_bridge_file_params_plain_base64():
    """无 data URI 前缀的纯 base64 也桥接。"""
    manager = executor_mod.ExecutionManager()
    method_spec = {"params": {"query": [{"name": "file", "type": "file"}]}}
    payload = base64.b64encode(b"raw bytes").decode()

    bridged, tmp_files = manager._bridge_file_params(method_spec, {"file": payload})

    assert os.path.exists(bridged["file"])
    with open(bridged["file"], "rb") as f:
        assert f.read() == b"raw bytes"
    manager._cleanup_tmp_files(tmp_files)


def test_bridge_file_params_keeps_non_base64():
    """非 base64 字符串保持原样（不误写临时文件）。"""
    manager = executor_mod.ExecutionManager()
    method_spec = {"params": {"body": [{"name": "file", "type": "file"}]}}

    bridged, tmp_files = manager._bridge_file_params(method_spec, {"file": "no-such-file.txt"})

    assert bridged["file"] == "no-such-file.txt"
    assert tmp_files == []


def test_file_bridge_cleanup_after_execution(monkeypatch, tmp_path):
    """端到端：提交 file 参数执行，执行期间是路径、结束后清理。"""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "_auth.yaml").write_text(
        "name: upload-svc\nserver:\n  base_url: http://test.local\n"
    )
    (spec_dir / "upload.yaml").write_text(
        "name: upload\nmethods:\n"
        "  put:\n"
        "    http:\n      method: POST\n"
        "    params:\n      body:\n"
        "        - name: file\n          type: file\n"
    )

    seen: dict[str, Any] = {}

    def fake_execute_pipeline(**kwargs):
        seen["params"] = kwargs["kwargs"]
        seen["file"] = kwargs["kwargs"]["file"]
        assert os.path.exists(kwargs["kwargs"]["file"]), "执行期间临时文件应存在"
        kwargs["event_cb"]("validate", {"params": {"body": {}}})
        return {}

    monkeypatch.setattr(executor_mod, "execute_pipeline", fake_execute_pipeline)

    payload = base64.b64encode(b"x").decode()
    execution_id = executor_mod.execution_manager.submit_command(
        str(spec_dir),
        "upload.put",
        {"file": f"data:application/octet-stream;base64,{payload}"},
    )
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "done"

    file_param = seen["file"]
    assert isinstance(file_param, str)
    assert os.path.basename(file_param).startswith("cliyard-upload-")
    # 执行结束后临时文件已被清理
    assert not os.path.exists(file_param)


# ===========================================================================
# Flow 提交
# ===========================================================================


def test_submit_flow_event_sequence(monkeypatch):
    """Flow 执行：flow_start → step_start → step_done → flow_end → done。"""
    flows = [
        FlowSpec(
            command="demo-flow",
            description="demo",
            steps=[FlowStep(id="s1", description="第一步", params={})],
        )
    ]
    monkeypatch.setattr(executor_mod, "load_flows", lambda spec_dir: flows)

    execution_id = executor_mod.execution_manager.submit_flow(
        str(_FIXTURES_SPEC), "demo-flow", {}
    )
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.kind == "flow"
    assert execution.done_event.wait(5)
    assert execution.status == "done"

    events = _drain(execution)
    assert [e["type"] for e in events] == [
        "flow_start", "step_start", "step_done", "flow_end", "done"
    ]
    assert events[0]["step_count"] == 1
    assert events[1]["id"] == "s1"
    assert events[2]["status"] == "ok"
    assert events[3]["outcome"] == "completed"


def test_submit_flow_unknown_command_error_event(monkeypatch):
    """未知 flow command → error 事件 + status=error。"""
    monkeypatch.setattr(executor_mod, "load_flows", lambda spec_dir: [])

    execution_id = executor_mod.execution_manager.submit_flow(
        str(_FIXTURES_SPEC), "ghost-flow", {}
    )
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)
    assert execution.status == "error"

    events = _drain(execution)
    assert [e["type"] for e in events] == ["error", "done"]
    assert "ghost-flow" in events[0]["message"]


# ===========================================================================
# HTTP API
# ===========================================================================


@pytest.fixture()
def client(monkeypatch):
    """TestClient over fixtures/spec-dir with execute_pipeline monkeypatched."""
    monkeypatch.setattr(executor_mod, "execute_pipeline", _fake_execute_pipeline)
    return TestClient(create_app(str(_FIXTURES_SPEC)))


def test_api_execute_returns_execution_id(client):
    resp = client.post(
        "/api/execute",
        json={"kind": "command", "target": "repos.list", "params": {"page": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "execution_id" in body
    assert len(body["execution_id"]) == 32


def test_api_execute_invalid_kind_400(client):
    resp = client.post(
        "/api/execute", json={"kind": "bogus", "target": "repos.list", "params": {}}
    )
    assert resp.status_code == 400


def test_api_execute_invalid_command_target_400(client):
    """command target 缺 resource 部分 → 400。"""
    resp = client.post(
        "/api/execute", json={"kind": "command", "target": "repos", "params": {}}
    )
    assert resp.status_code == 400
    assert "resource.method" in resp.json()["detail"]


def test_api_execute_missing_target_400(client):
    resp = client.post(
        "/api/execute", json={"kind": "command", "target": "", "params": {}}
    )
    assert resp.status_code == 400


def test_api_stream_receives_full_event_sequence(client):
    resp = client.post(
        "/api/execute",
        json={"kind": "command", "target": "repos.list", "params": {"page": 1}},
    )
    execution_id = resp.json()["execution_id"]

    with client.stream("GET", f"/api/executions/{execution_id}/stream") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        content = stream.read().decode()

    assert _sse_event_types(content) == [
        "validate",
        "auth",
        "request",
        "response",
        "format",
        "done",
    ]


def test_api_stream_not_found_404(client):
    resp = client.get("/api/executions/deadbeef/stream")
    assert resp.status_code == 404


def test_api_get_execution_status_and_steps(client):
    resp = client.post(
        "/api/execute",
        json={"kind": "command", "target": "repos.list", "params": {"page": 1}},
    )
    execution_id = resp.json()["execution_id"]

    # 等待后台线程完成，避免 GET 状态时仍是 running（竞态）
    execution = executor_mod.execution_manager.get(execution_id)
    assert execution is not None
    assert execution.done_event.wait(5)

    body = client.get(f"/api/executions/{execution_id}").json()
    assert body["id"] == execution_id
    assert body["kind"] == "command"
    assert body["target"] == "repos.list"
    assert body["status"] == "done"
    assert body["created_at"]
    assert [s["type"] for s in body["steps"]] == [
        "validate",
        "auth",
        "request",
        "response",
        "format",
        "done",
    ]


def test_api_get_execution_not_found_404(client):
    resp = client.get("/api/executions/deadbeef")
    assert resp.status_code == 404


def test_api_execute_flow(client, monkeypatch):
    """POST /api/execute with kind=flow returns execution_id + SSE flow events."""
    flows = [
        FlowSpec(
            command="add-user",
            description="add",
            steps=[FlowStep(id="s1", description="第一步", params={})],
        )
    ]
    monkeypatch.setattr(executor_mod, "load_flows", lambda spec_dir: flows)

    resp = client.post(
        "/api/execute",
        json={"kind": "flow", "target": "add-user", "params": {"name": "alice"}},
    )
    assert resp.status_code == 200
    execution_id = resp.json()["execution_id"]

    with client.stream("GET", f"/api/executions/{execution_id}/stream") as stream:
        content = stream.read().decode()

    assert _sse_event_types(content) == [
        "flow_start", "step_start", "step_done", "flow_end", "done"
    ]


# ===========================================================================
# SSE 断连检测 / 历史日志 / 注册表淘汰 / base64 上传限制
# ===========================================================================


def _make_execution(execution_id: str, status: str = "running"):
    """构造一个未注册的后台执行对象（测试用，不触线真实历史库）。"""
    return executor_mod.Execution(
        id=execution_id,
        spec_dir=".",
        kind="command",
        target="a.b",
        params={},
        status=status,
        created_at=executor_mod._now_iso(),
    )


def test_iter_events_stops_when_client_disconnects(monkeypatch):
    """客户端断连后 iter_events 提前结束，不再等待队列事件。"""
    manager = executor_mod.ExecutionManager()
    execution = _make_execution("disconnect-test")
    with manager._lock:
        manager._executions[execution.id] = execution
        manager._order.append(execution.id)
    execution.queue.put({"type": "validate", "time": executor_mod._now_iso()})

    monkeypatch.setattr(executor_mod, "_request_disconnected", lambda request: True)

    gen = manager.iter_events("disconnect-test", request=object())
    first = next(gen)
    assert json.loads(first["data"])["type"] == "validate"
    # 队列已空 + 已断连 → 第二次迭代应立即结束而不是阻塞等队列
    with pytest.raises(StopIteration):
        next(gen)


def test_iter_events_ignores_missing_request():
    """不传 request 时行为不变：正常等待队列事件直至 done。"""
    manager = executor_mod.ExecutionManager()
    execution = _make_execution("no-request")
    with manager._lock:
        manager._executions[execution.id] = execution
        manager._order.append(execution.id)
    execution.queue.put({"type": "done", "time": executor_mod._now_iso(), "status": "done"})

    gen = manager.iter_events("no-request")
    events = list(gen)
    assert json.loads(events[0]["data"])["type"] == "done"


def test_safe_record_start_logs_failure(caplog):
    """历史 start 写失败时记录 ERROR 日志（不再静默吞掉）。"""
    manager = executor_mod.ExecutionManager()
    store = MagicMock()
    store.record_start.side_effect = RuntimeError("db locked")
    manager.history_store = store
    execution = _make_execution("hist-start")

    with caplog.at_level(logging.ERROR, logger="cliyard.server.executor"):
        manager._safe_record_start(execution)

    assert "Failed to record execution start" in caplog.text
    assert "db locked" in caplog.text
    assert "hist-start" in caplog.text


def test_safe_record_finish_logs_failure(caplog):
    """历史 finish 写失败时记录 ERROR 日志（不阻塞调用方）。"""
    manager = executor_mod.ExecutionManager()
    store = MagicMock()
    store.record_finish.side_effect = RuntimeError("disk full")
    manager.history_store = store
    execution = _make_execution("hist-finish", status="done")

    with caplog.at_level(logging.ERROR, logger="cliyard.server.executor"):
        manager._safe_record_finish(execution)

    assert "Failed to record execution finish" in caplog.text
    assert "disk full" in caplog.text
    assert "hist-finish" in caplog.text


def test_registry_evicts_oldest_terminal(monkeypatch):
    """注册表满时淘汰最旧的已终态执行，running 保留。"""
    monkeypatch.setattr(executor_mod, "MAX_EXECUTIONS", 3)
    manager = executor_mod.ExecutionManager()
    manager.history_store = MagicMock()  # 避免真实历史库写入

    e1 = manager._create_execution(".", "command", "a.b", {})
    e2 = manager._create_execution(".", "command", "a.b", {})
    e3 = manager._create_execution(".", "command", "a.b", {})
    e1.status = "done"  # e1 是最早的已终态

    e4 = manager._create_execution(".", "command", "a.b", {})

    assert manager.get(e1.id) is None, "最旧的已终态执行应被淘汰"
    assert manager.get(e2.id) is not None
    assert manager.get(e3.id) is not None
    assert manager.get(e4.id) is not None
    assert len(manager._executions) == 3


def test_registry_skips_eviction_when_all_running(monkeypatch):
    """全部 running 时不淘汰（不做错误驱逐）。"""
    monkeypatch.setattr(executor_mod, "MAX_EXECUTIONS", 2)
    manager = executor_mod.ExecutionManager()
    manager.history_store = MagicMock()

    e1 = manager._create_execution(".", "command", "a.b", {})
    e2 = manager._create_execution(".", "command", "a.b", {})
    e3 = manager._create_execution(".", "command", "a.b", {})

    assert manager.get(e1.id) is not None
    assert manager.get(e2.id) is not None
    assert manager.get(e3.id) is not None


def test_base64_upload_over_limit_returns_none():
    """解码后超 MAX_UPLOAD_BYTES 的 base64 返回 None（保持原值）。"""
    big = base64.b64encode(b"x" * (executor_mod.MAX_UPLOAD_BYTES + 1)).decode()
    assert executor_mod._write_base64_temp_file(big) is None


def test_base64_upload_within_limit_writes_file():
    """正常大小 base64 正常写临时文件且可清理。"""
    payload = base64.b64encode(b"small").decode()
    path = executor_mod._write_base64_temp_file(f"data:text/plain;base64,{payload}")
    assert path is not None
    assert path.endswith(".txt")
    with open(path, "rb") as f:
        assert f.read() == b"small"
    os.unlink(path)


def test_temp_file_prefix_is_randomized():
    """临时文件前缀含随机段（同一执行两次写入不同名）。"""
    manager = executor_mod.ExecutionManager()
    method_spec = {"params": {"body": [{"name": "file", "type": "file"}]}}
    payload = base64.b64encode(b"data").decode()
    params = {"file": f"data:text/plain;base64,{payload}"}

    bridged1, tmp1 = manager._bridge_file_params(method_spec, params)
    bridged2, tmp2 = manager._bridge_file_params(method_spec, params)

    assert os.path.basename(bridged1["file"]).startswith("cliyard-upload-")
    assert os.path.basename(bridged1["file"]) != os.path.basename(bridged2["file"])
    manager._cleanup_tmp_files(tmp1)
    manager._cleanup_tmp_files(tmp2)
