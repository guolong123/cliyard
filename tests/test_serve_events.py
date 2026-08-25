"""Tests for the serve event-callback extension (plan T4).

Covers:
- ``execute_pipeline`` event sequence: validate → auth → request → response → format
- Sensitive values (token / X-Api-Key) redacted to ``***`` in event payloads
- ``run_flow`` step_cb receives step_start / step_done / flow_end
- An event_cb that raises never breaks pipeline execution
"""

from unittest.mock import MagicMock

from cliyard.engine.builder import ServiceContext, execute_pipeline
from cliyard.engine.flow import FlowSpec, FlowStep
from cliyard.engine.orchestrator import run_flow
from cliyard.server.redact import is_sensitive_key, redact_sensitive


class MockHttpClient:
    """Test double for an HTTP client with .request() and .default_headers."""

    def __init__(self, payload=None):
        self.default_headers: dict[str, str] = {}
        self._payload = payload if payload is not None else {"data": []}

    def request(self, method, url, data=None, query_params=None, headers=None, files=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = self._payload
        return resp


def _make_context():
    method_spec = {
        "http": {"method": "GET", "path": "/users"},
        "params": {
            "query": [{"name": "token", "type": "string"}],
            "header": [{"name": "X-Api-Key", "type": "string"}],
        },
        "output": {"items_path": "$.data", "fields": []},
    }
    resource_spec = {"path": "users"}
    service_ctx = ServiceContext(
        base_url="http://test.local",
        prefix="/api",
        auth_spec=None,
        pre_filled_auth=None,
    )
    return method_spec, resource_spec, service_ctx


def _run_pipeline(events: list, event_cb=None):
    method_spec, resource_spec, service_ctx = _make_context()
    client = MockHttpClient()
    result = execute_pipeline(
        {"token": "secret-token", "X-Api-Key": "supersecret"},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=event_cb or (lambda name, payload: events.append((name, payload))),
    )
    return result, events


def test_execute_pipeline_event_sequence():
    """validate → auth → request → response → format in strict order."""
    events: list = []
    result, events = _run_pipeline(events)

    assert [name for name, _ in events] == ["validate", "auth", "request", "response", "format"]

    validate = events[0][1]
    assert set(validate["params"]) == {"argument", "path", "query", "header", "body"}
    assert validate["params"]["query"] == {"token": "***"}

    auth = events[1][1]
    assert auth["mode"] == "preconfigured"
    assert auth["pre_filled_keys"] == []

    request = events[2][1]
    assert request["method"] == "GET"
    assert request["url"].startswith("http://test.local")
    assert "query_params" in request and "headers" in request and "body" in request

    response = events[3][1]
    assert response["status_code"] == 200
    assert isinstance(response["elapsed_ms"], int) and response["elapsed_ms"] >= 0

    format_event = events[4][1]
    assert "output_preview" in format_event

    assert result == {"items": [], "total": 0}


def test_sensitive_values_redacted_in_events():
    """token / X-Api-Key appear as *** in validate and request payloads."""
    events: list = []
    _run_pipeline(events)

    payload_by_name = {name: payload for name, payload in events}

    validate_params = payload_by_name["validate"]["params"]
    assert validate_params["query"]["token"] == "***"
    assert validate_params["header"]["X-Api-Key"] == "***"

    request = payload_by_name["request"]
    assert request["query_params"]["token"] == "***"
    assert request["headers"]["X-Api-Key"] == "***"


def test_format_output_preview_redacted_without_items_path():
    """format event output_preview redacts token even when no items_path (raw response)."""
    method_spec, resource_spec, service_ctx = _make_context()
    # Drop items_path so the pipeline takes the raw-response format branch (line 508).
    method_spec["output"] = {"fields": []}
    client = MockHttpClient(payload={"token": "SECRET123", "name": "ok"})

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    format_event = [payload for name, payload in events if name == "format"][0]
    preview = format_event["output_preview"]
    assert '"token": "***"' in preview
    assert "SECRET123" not in preview
    assert '"name": "ok"' in preview
    # 无 items_path 分支：format 事件不携带 table 字段
    assert "table" not in format_event


def test_format_output_preview_not_truncated_for_large_response():
    """format 事件 output_preview 不截断：超过 2000 字符的原始响应完整保留。"""
    method_spec, resource_spec, service_ctx = _make_context()
    method_spec["output"] = {"fields": []}
    big_payload = {"items": [{"name": f"item-{i}"} for i in range(500)]}
    client = MockHttpClient(payload=big_payload)

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    format_event = [payload for name, payload in events if name == "format"][0]
    preview = format_event["output_preview"]
    assert len(preview) > 2000  # 超过旧 2000 截断上限
    assert preview.endswith("}")  # JSON 完整闭合
    assert '"item-499"' in preview  # 尾部内容保留


def test_format_event_carries_structured_table():
    """items_path 分支：format 事件携带 table（columns=alias 列头 / rows / total）。"""
    method_spec, resource_spec, service_ctx = _make_context()
    method_spec["output"] = {
        "items_path": "$.repos",
        "fields": [
            {"name": "name", "alias": "仓库名称"},
            {"name": "type", "alias": "类型"},
        ],
    }
    client = MockHttpClient(
        payload={
            "repos": [
                {"name": "a", "type": "EVENTS"},
                {"name": "b", "type": "LOGS"},
            ]
        }
    )

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    format_event = [payload for name, payload in events if name == "format"][0]
    assert "output_preview" in format_event
    table = format_event["table"]
    assert table["columns"] == [
        {"name": "name", "alias": "仓库名称"},
        {"name": "type", "alias": "类型"},
    ]
    assert table["rows"] == [["a", "EVENTS"], ["b", "LOGS"]]
    assert table["total"] == 2


def test_format_event_table_no_table_when_fields_empty():
    """items_path 分支但 fields 为空：format 事件不携带 table 字段。"""
    method_spec, resource_spec, service_ctx = _make_context()
    # _make_context 的 output 已是 items_path + fields: []
    client = MockHttpClient(payload={"data": [{"name": "a"}]})

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    format_event = [payload for name, payload in events if name == "format"][0]
    assert "output_preview" in format_event
    assert "table" not in format_event


def test_format_event_table_applies_field_format():
    """table 行值复用 _format_field_value：format: datetime 转换生效。"""
    from datetime import datetime

    method_spec, resource_spec, service_ctx = _make_context()
    method_spec["output"] = {
        "items_path": "$.items",
        "fields": [
            {"name": "name", "alias": "名称"},
            {"name": "ts", "alias": "时间", "format": "datetime"},
        ],
    }
    client = MockHttpClient(payload={"items": [{"name": "x", "ts": 1700000000000}]})

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    table = [payload for name, payload in events if name == "format"][0]["table"]
    expected = datetime.fromtimestamp(1700000000).strftime("%Y-%m-%d %H:%M:%S")
    assert table["rows"] == [["x", expected]]


def test_format_event_table_redacts_sensitive_values():
    """table 行值/列名含敏感键时脱敏为 ***（token 值不流出）。"""
    import json

    method_spec, resource_spec, service_ctx = _make_context()
    method_spec["output"] = {
        "items_path": "$.items",
        "fields": [
            {"name": "name", "alias": "名称"},
            {"name": "token", "alias": "令牌"},
        ],
    }
    client = MockHttpClient(payload={"items": [{"name": "a", "token": "SECRET123"}]})

    events: list = []
    execute_pipeline(
        {},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=client,
        event_cb=lambda name, payload: events.append((name, payload)),
    )

    table = [payload for name, payload in events if name == "format"][0]["table"]
    # 行值：token 字段被 redact_sensitive 替换
    assert table["rows"] == [["a", "***"]]
    assert "SECRET123" not in json.dumps(table)


def test_run_flow_step_callback():
    """run_flow emits step_start / step_done per step and one flow_end."""
    flow_spec = FlowSpec(
        command="demo",
        description="demo flow",
        steps=[
            FlowStep(id="s1", description="第一步", params={"a": 1}),
            FlowStep(id="s2", description="第二步", params={"b": 2}),
        ],
    )
    service_ctx = ServiceContext(base_url="http://test.local", auth_spec=None, pre_filled_auth=None)
    service_spec = {"name": "demo"}

    events: list = []
    run_flow(
        flow_spec,
        {},
        service_ctx,
        service_spec,
        step_cb=lambda name, payload: events.append((name, payload)),
    )

    assert [name for name, _ in events] == [
        "step_start",
        "step_done",
        "step_start",
        "step_done",
        "flow_end",
    ]

    assert events[0][1] == {"index": 1, "id": "s1", "label": "第一步", "use": ""}
    assert events[1][1]["status"] == "ok"
    assert events[1][1]["elapsed_ms"] >= 0
    assert "result_preview" in events[1][1]
    assert events[3][1]["index"] == 2
    assert events[4][1] == {"outcome": "completed", "step_count": 2}


def test_run_flow_step_callback_no_steps():
    """flow_end is emitted even when the flow has no steps."""
    flow_spec = FlowSpec(command="empty", steps=[])
    service_ctx = ServiceContext(base_url="http://test.local", auth_spec=None, pre_filled_auth=None)

    events: list = []
    run_flow(
        flow_spec,
        {},
        service_ctx,
        {"name": "demo"},
        step_cb=lambda name, payload: events.append((name, payload)),
    )

    assert events == [("flow_end", {"outcome": "completed", "step_count": 0})]


def test_event_cb_raising_does_not_break_pipeline():
    """A raising event_cb is swallowed; execute_pipeline returns normally."""
    method_spec, resource_spec, service_ctx = _make_context()

    def boom(name, payload):
        raise RuntimeError("callback exploded")

    result = execute_pipeline(
        {"token": "t", "X-Api-Key": "k"},
        method_spec,
        resource_spec,
        service_ctx,
        http_client=MockHttpClient(),
        event_cb=boom,
    )

    assert result == {"items": [], "total": 0}


def test_redact_sensitive_unit():
    """redact_sensitive replaces sensitive values and preserves others."""
    assert is_sensitive_key("Authorization")
    assert is_sensitive_key("apiToken")
    assert is_sensitive_key("X-Api-Key")
    assert is_sensitive_key("credentials")
    assert is_sensitive_key("passphrase")
    assert is_sensitive_key("pwd_expire_time")
    assert is_sensitive_key("jwt")
    assert is_sensitive_key("Bearer")
    assert not is_sensitive_key("name")

    redacted = redact_sensitive({"header": {"Authorization": "Bearer abc", "X-Tenant": "t1"}})
    assert redacted == {"header": {"Authorization": "***", "X-Tenant": "t1"}}

    assert redact_sensitive([{"token": "x"}, {"id": 1}]) == [{"token": "***"}, {"id": 1}]
    assert redact_sensitive("plain") == "plain"
    assert redact_sensitive(None) is None

    redacted2 = redact_sensitive(
        {
            "credentials": {"pwd": "s3cret"},
            "jwt": "eyJhbGci",
            "passphrase": "hunter2",
            "bearer": "abc.def",
            "page": 1,
        }
    )
    # credentials 键命中 credential 关键词 → 整个值替换为 ***（不递归内部）
    assert redacted2 == {
        "credentials": "***",
        "jwt": "***",
        "passphrase": "***",
        "bearer": "***",
        "page": 1,
    }


def test_step_result_preview_not_truncated_for_large_result():
    """_step_result_preview 不再截断：超过 20000 字符的大 JSON 结果完整保留。"""
    from cliyard.engine.orchestrator import _step_result_preview

    large = {"repos": [{"name": f"repo-{i}", "id": i} for i in range(2000)]}
    preview = _step_result_preview(large)

    assert len(preview) > 20000  # 超过旧 20000 上限
    assert preview.endswith("}")  # JSON 完整闭合，未被截断
    assert '"id": 1999' in preview  # 尾部内容保留
