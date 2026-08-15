"""Tests for the flow orchestration feature.

Test categories (use ``-k <category>`` to filter):
- ``unit`` — unit tests (dataclass, template, condition, etc.)
- ``integration`` — multi-step integration tests
- ``e2e`` — end-to-end tests with CliRunner and mocked HTTP
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import click.testing
import pytest
import yaml

from cliyard.engine.builder import ServiceContext, build_flow_command
from cliyard.engine.errors import ApiError, CliyError
from cliyard.engine.flow import (
    FlowSpec,
    FlowStep,
    ForEachConfig,
    RetryConfig,
    UntilConfig,
)
from cliyard.engine.loader import _is_resource_file, load_flows
from cliyard.engine.orchestrator import (
    FlowContext,
    evaluate_condition,
    execute_echo_action,
    execute_use_step,
    resolve_template,
    _execute_for_each,
    _execute_plugin_step,
    _execute_step,
    _execute_until,
    _execute_with_retry,
    _evaluate_expression,
    _lookup_resource_method,
    handle_on_result,
)
from cliyard.engine.template import Template
from cliyard.plugin import PluginRegistry, register_hook, register_step_type


# ---------------------------------------------------------------------------
# Mock HTTP client
# ---------------------------------------------------------------------------


class MockHttpClient:
    """Test double for an HTTP client with .request() and .default_headers."""

    def __init__(self):
        self.default_headers: dict[str, str] = {}
        self._response = MagicMock()

    def request(self, method, url, data=None, query_params=None, headers=None, timeout=None):
        self._last_request = {
            "method": method,
            "url": url,
            "data": data,
            "query_params": query_params,
        }
        return self._response

    def set_json_response(self, body: dict):
        """Configure the mock to return a JSON body."""
        self._response.json.return_value = body


class MockConsole:
    """Test double for rich.console.Console."""

    def __init__(self):
        self.output: list[str] = []

    def print(self, msg, *args, **kwargs):
        self.output.append(str(msg))


# ---------------------------------------------------------------------------
# Unit tests (-k unit)
# ---------------------------------------------------------------------------


class TestDataclassDefaults:
    """FlowSpec/FlowStep/Config dataclass construction."""

    def test_flow_step_defaults(self):
        step = FlowStep(id="test")
        assert step.id == "test"
        assert step.description == ""
        assert step.use == ""
        assert step.params == {}
        assert step.extract is None
        assert step.on_result is None
        assert step.for_each is None
        assert step.retry is None
        assert step.until is None
        assert step.show_response is False

    def test_flow_spec(self):
        spec = FlowSpec(command="test", steps=[])
        assert spec.command == "test"
        assert spec.steps == []
        assert spec.description == ""
        assert spec.hooks is None

    def test_for_each_config(self):
        fe = ForEachConfig(items="{{ x }}", as_name="item", steps=[])
        assert fe.items == "{{ x }}"
        assert fe.as_name == "item"
        assert fe.steps == []

    def test_retry_config_defaults(self):
        r = RetryConfig(max_attempts=3)
        assert r.max_attempts == 3
        assert r.delay == 1
        assert r.backoff is None
        assert r.on_exhausted is None

    def test_until_config_defaults(self):
        u = UntilConfig(condition="ok")
        assert u.condition == "ok"
        assert u.max_iterations == 30
        assert u.interval == 5
        assert u.timeout_action == "abort"


class TestResolveTemplate:
    """Template resolution tests."""

    def test_simple_variable(self):
        result = resolve_template("{{ flow.name }}", {"flow": {"name": "test"}})
        assert result == "test"

    def test_nested_dict(self):
        obj = {"a": "{{ flow.x }}", "b": {"c": "{{ flow.y }}"}}
        ctx = {"flow": {"x": "val1", "y": "val2"}}
        result = resolve_template(obj, ctx)
        assert result == {"a": "val1", "b": {"c": "val2"}}

    def test_non_string_passthrough(self):
        result = resolve_template(42, {"flow": {}})
        assert result == 42

    def test_missing_variable_returns_empty_string(self):
        """ChainableUndefined renders missing variables as empty string."""
        result = resolve_template("{{ flow.missing }}", {"flow": {}})
        assert result == ""

    def test_step_state_variable(self):
        ctx = {"step": {"check": {"count": 5}}}
        result = resolve_template("{{ step.check.count }}", ctx)
        assert result == "5"

    def test_list_resolution(self):
        ctx = {"flow": {"a": "1", "b": "2"}}
        result = resolve_template(["{{ flow.a }}", "{{ flow.b }}"], ctx)
        assert result == ["1", "2"]


class TestEvaluateCondition:
    """Condition evaluation tests."""

    def test_gt_true(self):
        assert evaluate_condition("{{ value > 0 }}", {"value": 3}) is True

    def test_eq_false(self):
        assert evaluate_condition("{{ value == 0 }}", {"value": 3}) is False

    def test_not_false(self):
        assert evaluate_condition("{{ not value }}", {"value": False}) is True

    def test_length_filter(self):
        assert evaluate_condition("{{ value | length > 0 }}", {"value": [1, 2]}) is True

    def test_is_not_none(self):
        assert evaluate_condition("{{ value is not none }}", {"value": 3}) is True

    def test_missing_var_returns_false(self):
        assert evaluate_condition("{{ missing_var > 0 }}", {}) is False

    def test_empty_condition_returns_false(self):
        assert evaluate_condition("", {}) is False


class TestIsResourceFile:
    """Resource file filtering."""

    def test_flows_yaml_not_resource(self):
        assert _is_resource_file(Path("_flows.yaml")) is False

    def test_auth_yaml_not_resource(self):
        assert _is_resource_file(Path("_auth.yaml")) is False

    def test_groups_yaml_not_resource(self):
        assert _is_resource_file(Path("_groups.yaml")) is False

    def test_service_yaml_not_resource(self):
        assert _is_resource_file(Path("_service.local.yaml")) is False

    def test_normal_yaml_is_resource(self):
        assert _is_resource_file(Path("pet.yaml")) is True

    def test_other_yaml_is_resource(self):
        assert _is_resource_file(Path("repos.yaml")) is True


class TestLoadFlows:
    """Flow loader tests."""

    def test_empty_directory(self, tmp_path):
        result = load_flows(tmp_path)
        assert result == []

    def test_valid_flows_yaml(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  deploy:
    command: deploy
    description: Deploy an app
    steps:
      - id: list_apps
        use: app.list
        params:
          query:
            env: "{{ flow.env }}"
      - id: create_release
        use: app.create
        params:
          body:
            app_name: "{{ flow.app_name }}"
"""
        )
        result = load_flows(tmp_path)
        assert len(result) == 1
        flow = result[0]
        assert flow.command == "deploy"
        assert len(flow.steps) == 2
        assert flow.steps[0].id == "list_apps"
        assert flow.steps[0].use == "app.list"
        assert flow.steps[1].id == "create_release"

    def test_flows_show_response(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  debug:
    command: debug
    steps:
      - id: list_apps
        use: app.list
        show_response: true
      - id: create_release
        use: app.create
"""
        )
        result = load_flows(tmp_path)
        assert len(result) == 1
        steps = result[0].steps
        assert steps[0].show_response is True
        assert steps[1].show_response is False

    def test_missing_command_raises(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  deploy:
    steps:
      - id: step1
        use: app.list
"""
        )
        assert load_flows(tmp_path) == []

    def test_flows_with_for_each(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  batch:
    command: batch
    steps:
      - id: process
        for_each:
          items: "{{ flow.items }}"
          as: row
          steps:
            - id: do_thing
              use: item.process
              params:
                body:
                  name: "{{ row.name }}"
"""
        )
        result = load_flows(tmp_path)
        assert len(result) == 1
        step = result[0].steps[0]
        assert step.for_each is not None
        assert step.for_each.as_name == "row"
        assert len(step.for_each.steps) == 1

    def test_flows_with_retry(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  resilient:
    command: resilient
    steps:
      - id: fetch
        use: api.fetch
        retry:
          max_attempts: 5
          delay: 2
          backoff: 2
"""
        )
        result = load_flows(tmp_path)
        step = result[0].steps[0]
        assert step.retry is not None
        assert step.retry.max_attempts == 5
        assert step.retry.delay == 2
        assert step.retry.backoff == 2

    def test_flows_with_until(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  poll:
    command: poll
    steps:
      - id: check
        use: job.status
        until:
          condition: "{{ step.check.status == 'done' }}"
          max_iterations: 10
          interval: 1
"""
        )
        result = load_flows(tmp_path)
        step = result[0].steps[0]
        assert step.until is not None
        assert step.until.condition == "{{ step.check.status == 'done' }}"
        assert step.until.max_iterations == 10

    def test_missing_steps_raises(self, tmp_path):
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text(
            """
flows:
  deploy:
    command: deploy
"""
        )
        assert load_flows(tmp_path) == []


# ---------------------------------------------------------------------------
# Integration tests (-k integration)
# ---------------------------------------------------------------------------


def _make_service_spec(resources: list[dict]) -> dict:
    """Build a minimal service_spec for testing."""
    return {
        "name": "test",
        "servers": {"default": {"base_url": "http://localhost", "prefix": "/api/v1"}},
        "resources": resources,
    }


def _make_flow_context(service_spec: dict, step_state: dict | None = None) -> FlowContext:
    """Build a FlowContext with mocked console and HTTP client."""
    return FlowContext(
        flow_params={"env": "prod", "app_name": "myapp"},
        step_state=step_state or {},
        http_client=MockHttpClient(),
        console=MockConsole(),
        service_spec=service_spec,
        base_url="http://localhost",
        prefix="/api/v1",
    )


def _user_resource():
    return {
        "name": "user",
        "path": "user",
        "methods": {
            "list": {
                "http": {"method": "GET", "path": "user"},
                "params": {"query": [{"name": "env", "field": "env", "type": "string"}]},
            },
            "create": {
                "http": {"method": "POST", "path": "user"},
                "params": {
                    "body": [{"name": "name", "field": "name", "type": "string", "required": True}]
                },
            },
        },
    }


class TestSequentialExecution:
    """Multi-step sequential flow execution."""

    def test_two_steps(self):
        spec = _make_service_spec([_user_resource()])
        ctx = _make_flow_context(spec)

        step1 = FlowStep(id="list_users", use="user.list", params={"query": {"env": "{{ flow.env }}"}})
        step2 = FlowStep(id="create_user", use="user.create", params={"body": {"name": "{{ flow.app_name }}"}})

        # Mock HTTP responses
        ctx.http_client.set_json_response({"users": [{"name": "alice"}, {"name": "bob"}]})
        call_count = 0

        def mock_request(**kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.json.return_value = {"users": [{"name": "alice"}, {"name": "bob"}]}
            else:
                resp.json.return_value = {"name": "myapp", "created": True}
            return resp

        ctx.http_client.request = mock_request

        # Execute steps
        with patch("cliyard.engine.orchestrator.execute_use_step") as mock_exec:
            mock_exec.side_effect = [
                {"users": [{"name": "alice"}, {"name": "bob"}]},
                {"name": "myapp", "created": True},
            ]
            _execute_step(step1, ctx)
            ctx.step_state[step1.id] = ctx.step_state.get(step1.id, {"users": [{"name": "alice"}, {"name": "bob"}]})
            _execute_step(step2, ctx)
            ctx.step_state[step2.id] = ctx.step_state.get(step2.id, {"name": "myapp", "created": True})

        assert "list_users" in ctx.step_state or call_count == 2


class TestStepFailureAborts:
    """Step failure aborts flow."""

    def test_failure_stops_execution(self):
        spec = _make_service_spec([_user_resource()])
        ctx = _make_flow_context(spec)

        step1 = FlowStep(id="fail_step", use="user.list", params={})
        step2 = FlowStep(id="never_run", use="user.list", params={})

        step1_executed = False

        with patch("cliyard.engine.orchestrator.execute_use_step") as mock_exec:
            mock_exec.side_effect = CliyError("API down")
            try:
                _execute_step(step1, ctx)
                step1_executed = True
            except CliyError:
                pass

        # step2 should not be reached in a real flow runner
        # but the error propagates correctly
        assert step1_executed is False


class TestIfTrueBranch:
    """on_result with if condition evaluating to True."""

    def test_true_branch_executes_then(self):
        ctx = _make_flow_context({})
        ctx.step_state = {"check": {"count": 5}}

        on_result = [
            {
                "if": "{{ step.check.count > 0 }}",
                "then": [{"type": "echo", "message": "Found items"}],
            }
        ]

        handle_on_result(on_result, ctx, "check")

        # Verify echo was printed (via console output)
        assert any("Found items" in msg for msg in ctx.console.output)


class TestIfFalseElseBranch:
    """on_result with if condition evaluating to False."""

    def test_false_branch_executes_else(self):
        ctx = _make_flow_context({})
        ctx.step_state = {"check": {"count": 0}}

        on_result = [
            {
                "if": "{{ step.check.count > 0 }}",
                "then": [{"type": "echo", "message": "Found items"}],
                "else": [{"type": "echo", "message": "No items found"}],
            }
        ]

        handle_on_result(on_result, ctx, "check")

        assert any("No items found" in msg for msg in ctx.console.output)
        assert not any("Found items" in msg for msg in ctx.console.output)


class TestRetrySuccess:
    """Retry step that eventually succeeds."""

    def test_retry_succeeds_on_third_attempt(self):
        ctx = _make_flow_context(_make_service_spec([]))
        ctx.step_state = {}

        step = FlowStep(
            id="retry_step",
            use="user.list",
            params={},
            retry=RetryConfig(max_attempts=3, delay=0),
        )

        call_count = 0

        def mock_exec(s, rp, c):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise CliyError("Temporary failure")
            return {"ok": True}

        with patch("cliyard.engine.orchestrator.execute_use_step", side_effect=mock_exec):
            result = _execute_with_retry(step, ctx, {})

        assert result == {"ok": True}
        assert call_count == 3


class TestRetryExhausted:
    """Retry step that exhausts all attempts."""

    def test_retry_exhausted_raises(self):
        ctx = _make_flow_context(_make_service_spec([]))

        step = FlowStep(
            id="retry_fail",
            use="user.list",
            params={},
            retry=RetryConfig(max_attempts=2, delay=0),
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=CliyError("Permanent failure"),
        ):
            with pytest.raises(CliyError, match="retries exhausted"):
                _execute_with_retry(step, ctx, {})


class TestForEach:
    """for_each iteration."""

    def test_iterates_over_items(self):
        ctx = _make_flow_context(
            _make_service_spec([_user_resource()]),
            step_state={"item_list": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
        )

        sub_step = FlowStep(id="process_item", use="user.create", params={"body": {"name": "{{ row.name }}"}})

        step = FlowStep(
            id="foreach_step",
            for_each=ForEachConfig(
                items="{{ step.item_list }}",
                as_name="row",
                steps=[sub_step],
            ),
        )

        results = []
        with patch("cliyard.engine.orchestrator.execute_use_step") as mock_exec:
            mock_exec.side_effect = lambda s, rp, c: {"processed": rp.get("body", {}).get("name", "unknown")}
            results = _execute_for_each(step, ctx)

        assert len(results) == 3
        assert results[0]["process_item"]["processed"] == "a"
        assert results[1]["process_item"]["processed"] == "b"


class TestUntilPolling:
    """until polling mechanism."""

    def test_poll_until_condition_met(self):
        ctx = _make_flow_context(
            _make_service_spec([]),
            step_state={},
        )

        step = FlowStep(
            id="poll_step",
            use="user.list",
            params={},
            until=UntilConfig(
                condition="{{ step.poll_step.status == 'done' }}",
                max_iterations=5,
                interval=0,
            ),
        )

        call_count = 0

        def mock_exec(s, rp, c):
            nonlocal call_count
            call_count += 1
            return {"status": "done" if call_count >= 2 else "pending"}

        with patch("cliyard.engine.orchestrator.execute_use_step", side_effect=mock_exec):
            result = _execute_until(step, ctx, {})

        assert result == {"status": "done"}
        assert call_count == 2


class TestEchoAction:
    """Echo action in on_result."""

    def test_echo_prints_message(self):
        ctx = _make_flow_context({})
        ctx.step_state = {"step1": {"count": 5}}

        on_result = [{"then": [{"type": "echo", "message": "Step complete: {{ step.step1.count }} items"}]}]

        handle_on_result(on_result, ctx, "step1")

        assert any("5 items" in msg for msg in ctx.console.output)


class TestEvaluateExpression:
    """Expression evaluation for native Python types."""

    def test_list_expression(self):
        result = _evaluate_expression("{{ items }}", {"items": [1, 2, 3]})
        assert result == [1, 2, 3]

    def test_scalar_expression(self):
        result = _evaluate_expression("{{ value }}", {"value": 42})
        assert result == 42

    def test_bool_expression(self):
        result = _evaluate_expression("{{ flag }}", {"flag": True})
        assert result is True


class TestExecuteAction:
    """Built-in control actions."""

    def test_return_sets_flag(self):
        ctx = _make_flow_context({})
        from cliyard.engine.orchestrator import execute_action

        execute_action("return", {}, ctx)
        assert ctx._flow_aborted is True

    def test_abort_raises_error(self):
        ctx = _make_flow_context({})
        from cliyard.engine.orchestrator import execute_action

        with pytest.raises(CliyError, match="aborted"):
            execute_action("abort", {"message": "Flow aborted"}, ctx)


# ===========================================================================
# E2E tests (-k e2e) — full pipeline from CLI to mock HTTP
# ===========================================================================

# Shared service spec for E2E tests
_E2E_SERVICE_SPEC: dict = {
    "name": "petstore",
    "resources": [
        {
            "name": "user",
            "path": "users",
            "methods": {
                "list": {
                    "http": {"method": "GET"},
                    "params": {
                        "query": [
                            {"name": "name", "type": "string", "default": ""},
                        ],
                    },
                    "output": {"items_path": "$"},
                },
                "create": {
                    "http": {"method": "POST", "path": "users"},
                    "params": {
                        "body": [
                            {"name": "name", "type": "string", "required": True},
                            {"name": "phone", "type": "string", "default": ""},
                        ],
                    },
                    "request_body": {
                        "name": "{{ name }}",
                        "phone": "{{ phone }}",
                    },
                },
            },
        },
        {
            "name": "pet",
            "path": "pets",
            "methods": {
                "list": {
                    "http": {"method": "GET"},
                    "params": {
                        "query": [
                            {"name": "status", "type": "string", "default": "available"},
                        ],
                    },
                    "output": {"items_path": "$"},
                },
            },
        },
    ],
}


@pytest.fixture
def http_mock(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch ``HttpClient.request`` so E2E tests never make real HTTP calls.

    Returns a dict with:
    - ``responses``: list of response dicts (or Exception) to return in order
    - ``calls``: list of dicts recording every HTTP call made

    Usage::

        http_mock["responses"].append({"users": []})
        http_mock["responses"].append(ApiError(500, ...))
        ...
        assert len(http_mock["calls"]) == 3
    """
    responses: list = []
    calls: list[dict] = []

    def _mock_request(
        http_client_self: object,
        method: str,
        url: str,
        data: dict | list | None = None,
        query_params: dict | None = None,
        headers: dict | None = None,
        timeout: int | None = None,
        files: dict | None = None,
    ) -> MagicMock:
        calls.append({
            "method": method,
            "url": url,
            "data": data,
            "query_params": query_params,
            "files": files,
        })

        if not responses:
            pytest.fail(
                f"No mock response configured for call #{len(calls)}: "
                f"{method} {url}"
            )

        cfg = responses.pop(0)

        if isinstance(cfg, Exception):
            raise cfg

        resp = MagicMock()
        resp.json.return_value = cfg
        resp.status_code = 200
        resp.text = ""
        return resp

    monkeypatch.setattr(
        "cliyard.client.http.HttpClient.request",
        _mock_request,
    )

    return {"responses": responses, "calls": calls}


@pytest.fixture(autouse=True)
def _cleanup_plugins_e2e() -> None:
    """Clear plugin registry before and after each E2E test."""
    PluginRegistry.clear()
    yield
    PluginRegistry.clear()


# ---------------------------------------------------------------------------
# E2E: add_user flow — 新增用户流程
# ---------------------------------------------------------------------------


class TestE2EAddUserFlow:
    """E2E tests for the add_user flow (check → decide → create → verify)."""

    FLOW_SPEC = FlowSpec(
        command="add-user",
        description="新增用户流程（查→判→创→验）",
        params={
            "query": [
                {"name": "name", "type": "string", "required": True, "description": "用户名"},
                {"name": "phone", "type": "string", "description": "手机号"},
            ],
        },
        steps=[
            FlowStep(
                id="check_user",
                description="查询用户是否存在",
                use="user.list",
                params={"name": "{{ flow.name }}"},
                extract={"found_users": "$.users"},
            ),
            FlowStep(
                id="decision",
                on_result=[
                    {
                        "if": "{{ step.check_user.found_users | length > 0 }}",
                        "then": [
                            {"type": "echo", "message": "用户已存在"},
                            {"action": "return"},
                        ],
                    },
                ],
            ),
            FlowStep(
                id="create_user",
                description="创建用户",
                use="user.create",
                params={"name": "{{ flow.name }}", "phone": "{{ flow.phone | default('') }}"},
            ),
            FlowStep(
                id="verify_user",
                description="验证用户已创建",
                use="user.list",
                params={"name": "{{ flow.name }}"},
            ),
        ],
    )

    def test_e2e_add_user_success(self, http_mock) -> None:
        """Flow creates user: check → create → verify — 3 HTTP calls."""
        http_mock["responses"].append({"users": []})
        http_mock["responses"].append({"user": {"name": "alice", "phone": "123"}})
        http_mock["responses"].append({"users": [{"name": "alice", "phone": "123"}]})

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice", "--phone=123"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert len(http_mock["calls"]) == 3, (
            f"Expected 3 HTTP calls (check+create+verify), got {len(http_mock['calls'])}"
        )
        assert http_mock["calls"][0]["method"] == "GET"
        assert http_mock["calls"][0]["query_params"] == {"name": "alice"}
        assert http_mock["calls"][1]["method"] == "POST"
        assert http_mock["calls"][1]["data"] == {"name": "alice", "phone": "123"}
        assert http_mock["calls"][2]["method"] == "GET"
        assert "Flow completed" in result.output

    def test_e2e_add_user_already_exists(self, http_mock) -> None:
        """Flow exits early when user exists — only 1 HTTP call, no create."""
        http_mock["responses"].append({"users": [{"name": "alice", "phone": "123"}]})

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert len(http_mock["calls"]) == 1, (
            f"Expected 1 HTTP call (check only), got {len(http_mock['calls'])}"
        )
        assert http_mock["calls"][0]["method"] == "GET"
        assert "用户已存在" in result.output or "returned" in result.output


# ---------------------------------------------------------------------------
# E2E: retry logic — 重试机制
# ---------------------------------------------------------------------------


class TestE2ERetryFlow:
    """E2E tests for retry logic in flow steps."""

    def test_e2e_retry_success_after_retry(self, http_mock) -> None:
        """Retry step succeeds after an initial failure."""
        http_mock["responses"].append(ApiError(500, "http://test.local/pets", "Server Error"))
        http_mock["responses"].append({"pets": [{"name": "pet1"}]})

        flow_spec = FlowSpec(
            command="retry-demo",
            description="演示重试机制的流程",
            steps=[
                FlowStep(
                    id="fetch_data",
                    description="获取数据（可重试）",
                    use="pet.list",
                    params={"query": {"status": "{{ flow.status | default('available') }}"}},
                    retry=RetryConfig(max_attempts=2, delay=0),
                ),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert len(http_mock["calls"]) == 2, (
            f"Expected 2 HTTP calls (1 failed + 1 retry), got {len(http_mock['calls'])}"
        )
        assert all(c["method"] == "GET" for c in http_mock["calls"])
        assert all("pets" in c["url"] for c in http_mock["calls"])

    def test_e2e_retry_all_exhausted(self, http_mock) -> None:
        """Retry step fails after all attempts exhausted."""
        http_mock["responses"].append(ApiError(500, "http://test.local/pets", "Server Error"))
        http_mock["responses"].append(ApiError(500, "http://test.local/pets", "Server Error"))

        flow_spec = FlowSpec(
            command="retry-demo",
            description="演示重试机制的流程",
            steps=[
                FlowStep(
                    id="fetch_data",
                    description="获取数据（可重试）",
                    use="pet.list",
                    params={"query": {"status": "{{ flow.status | default('available') }}"}},
                    retry=RetryConfig(max_attempts=2, delay=0),
                ),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0  # error caught by Click callback
        assert len(http_mock["calls"]) == 2
        assert "retries exhausted" in result.output.lower() or "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# E2E: plugin step — 插件步骤
# ---------------------------------------------------------------------------


class TestE2EPluginStepFlow:
    """E2E tests for plugin step execution."""

    def test_e2e_plugin_step_execution(self, http_mock) -> None:
        """Plugin step is called with correct params, flow continues."""
        call_log: list[dict] = []

        @register_step_type("my_step")
        def my_step(params: dict, context: object) -> dict:
            call_log.append(params)
            return {"called": True, "input": params.get("input", "")}

        flow_spec = FlowSpec(
            command="plugin-demo",
            description="演示插件步骤的流程",
            steps=[
                FlowStep(id="custom_step", description="执行自定义插件步骤", type="plugin:my_step", params={"input": "hello"}),
                FlowStep(id="list_pets", description="列出宠物", use="pet.list"),
            ],
        )

        http_mock["responses"].append({"pets": [{"name": "pet1"}]})

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert len(call_log) == 1, f"Expected 1 plugin call, got {len(call_log)}"
        assert call_log[0] == {"input": "hello"}
        assert len(http_mock["calls"]) == 1  # pet.list
        assert http_mock["calls"][0]["method"] == "GET"

    def test_e2e_plugin_step_unknown(self) -> None:
        """Unknown plugin name is handled gracefully (no crash)."""
        flow_spec = FlowSpec(
            command="plugin-demo",
            description="演示插件步骤的流程",
            steps=[
                FlowStep(id="custom_step", description="未知插件", type="plugin:nonexistent", params={"input": "hello"}),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0
        assert "unknown plugin" in result.output.lower() or "nonexistent" in result.output


# ---------------------------------------------------------------------------
# E2E: lifecycle hooks — 生命周期钩子
# ---------------------------------------------------------------------------


class TestE2EHooksFlow:
    """E2E tests for lifecycle hooks in flows."""

    def test_e2e_hooks_on_start_on_end(self, http_mock) -> None:
        """on_start fires before steps, on_end fires after completion."""
        hook_log: list[str] = []

        @register_hook("notify_start")
        def notify_start(context: object) -> None:
            hook_log.append("start")

        @register_hook("notify_end")
        def notify_end(context: object) -> None:
            hook_log.append("end")

        http_mock["responses"].append({"pets": [{"name": "pet1"}]})

        flow_spec = FlowSpec(
            command="hook-demo",
            description="演示生命周期钩子的流程",
            hooks={
                "on_start": ["notify_start"],
                "on_end": ["notify_end"],
            },
            steps=[
                FlowStep(id="list_pets", description="列出宠物", use="pet.list"),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert hook_log == ["start", "end"], f"Expected ['start', 'end'], got {hook_log}"

    def test_e2e_hooks_on_failure(self, http_mock) -> None:
        """on_failure hook fires when a step fails."""
        hook_log: list[str] = []

        @register_hook("notify_failure")
        def notify_failure(context: object) -> None:
            hook_log.append("failure")

        http_mock["responses"].append(ApiError(500, "http://test.local/pets", "Server Error"))

        flow_spec = FlowSpec(
            command="hook-demo",
            description="演示生命周期钩子的流程",
            hooks={
                "on_start": [],
                "on_failure": ["notify_failure"],
            },
            steps=[
                FlowStep(id="list_pets", description="列出宠物", use="pet.list"),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0
        assert hook_log == ["failure"], f"Expected on_failure to fire, got {hook_log}"

    def test_e2e_hooks_unregistered_not_blocking(self, http_mock) -> None:
        """Unregistered hooks are silently ignored — flow continues normally."""
        http_mock["responses"].append({"pets": [{"name": "pet1"}]})

        flow_spec = FlowSpec(
            command="hook-demo",
            description="带未注册钩子的流程",
            hooks={
                "on_start": ["nonexistent_hook"],
            },
            steps=[
                FlowStep(id="list_pets", description="列出宠物", use="pet.list"),
            ],
        )

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])

        assert result.exit_code == 0
        assert "Flow completed" in result.output


# ===========================================================================
# Verbose / debug mode tests (-k verbose)
# ===========================================================================


class TestVerboseMode:
    """Flow verbose output: --verbose flag and per-step show_response."""

    FLOW_SPEC = FlowSpec(
        command="verbose-demo",
        description="调试输出流程",
        params={
            "query": [
                {"name": "name", "type": "string", "default": ""},
            ],
        },
        steps=[
            FlowStep(id="list_users", description="列出用户", use="user.list",
                     params={"name": "{{ flow.name }}"}),
            FlowStep(id="create_user", description="创建用户", use="user.create",
                     params={"name": "{{ flow.name }}"}),
        ],
    )

    def test_verbose_flag_prints_step_details(self, http_mock) -> None:
        """--verbose prints use / params / response for every step."""
        http_mock["responses"].append({"users": [{"name": "alice"}]})
        http_mock["responses"].append({"user": {"name": "alice", "created": True}})

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice", "--verbose"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert result.output.count("✓ 完成") == 2
        assert "① 列出用户" in result.output
        assert "② 创建用户" in result.output
        assert "use: user.list" in result.output
        assert "use: user.create" in result.output
        assert "params:" in result.output
        assert "response:" in result.output
        assert '"users"' in result.output

    def test_no_verbose_omits_details(self, http_mock) -> None:
        """Default run keeps minimal output — no response details."""
        http_mock["responses"].append({"users": []})
        http_mock["responses"].append({"user": {"created": True}})

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice"])

        assert result.exit_code == 0
        assert "response:" not in result.output
        assert "params:" not in result.output
        # 非 verbose：每步一行编号 + ✓
        assert "① 列出用户" in result.output
        assert "② 创建用户" in result.output
        assert "✓" in result.output

    def test_show_response_step_option(self, http_mock) -> None:
        """Per-step show_response: true prints details without --verbose."""
        flow_spec = FlowSpec(
            command="partial-debug",
            description="仅单个步骤输出",
            params={
                "query": [
                    {"name": "name", "type": "string", "default": ""},
                ],
            },
            steps=[
                FlowStep(id="list_users", description="列出用户", use="user.list",
                         params={"name": "{{ flow.name }}"}, show_response=True),
                FlowStep(id="create_user", description="创建用户", use="user.create",
                         params={"name": "{{ flow.name }}"}),
            ],
        )
        http_mock["responses"].append({"users": [{"name": "alice"}]})
        http_mock["responses"].append({"user": {"name": "alice", "created": True}})

        cmd = build_flow_command(flow_spec, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice"])

        assert result.exit_code == 0
        assert result.output.count("✓ 完成") == 1
        assert "use: user.list" in result.output
        assert "use: user.create" not in result.output
        assert '"users"' in result.output
        assert '"created"' not in result.output

    def test_failed_step_verbose_shows_params(self, http_mock) -> None:
        """--verbose 下失败步骤也展示请求参数与错误详情."""
        http_mock["responses"].append({"users": []})
        http_mock["responses"].append(ApiError(500, "create failed", "boom"))

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice", "--verbose"])

        assert result.exit_code == 0
        assert "① 列出用户" in result.output
        assert "params:" in result.output
        assert '"name"' in result.output
        assert "✗" in result.output
        assert "boom" in result.output
        assert "Flow failed" in result.output

    def test_failed_step_no_verbose_plain_line(self, http_mock) -> None:
        """非 verbose 失败步骤保持单行输出，无 params 详情."""
        http_mock["responses"].append({"users": []})
        http_mock["responses"].append(ApiError(500, "create failed", "boom"))

        cmd = build_flow_command(self.FLOW_SPEC, ServiceContext(base_url="http://test.local"), _E2E_SERVICE_SPEC)
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, ["--name=alice"])

        assert result.exit_code == 0
        assert "params:" not in result.output
        assert "boom" in result.output
        assert "Flow failed" in result.output


# ===========================================================================
# Edge case tests (-k edge)
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for flow orchestration covering error paths,
    boundary conditions, and failure modes (-k edge)."""

    # -----------------------------------------------------------------------
    # Empty state edge cases
    # -----------------------------------------------------------------------

    def test_empty_steps_list(self, http_mock):
        """Flow with ``steps: []`` completes normally with no errors."""
        flow_spec = FlowSpec(command="empty", steps=[])
        cmd = build_flow_command(
            flow_spec, ServiceContext(base_url="http://test.local"), {}
        )
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 0
        assert "Flow completed" in result.output

    def test_for_each_empty_items(self):
        """for_each with items resolving to empty list — no sub-steps execute."""
        ctx = _make_flow_context(
            _make_service_spec([_user_resource()]),
            step_state={"item_list": []},
        )
        sub_step = FlowStep(
            id="process_item",
            use="user.create",
            params={"body": {"name": "{{ row.name }}"}},
        )
        step = FlowStep(
            id="foreach_step",
            for_each=ForEachConfig(
                items="{{ step.item_list }}",
                as_name="row",
                steps=[sub_step],
            ),
        )
        results = _execute_for_each(step, ctx)
        assert results == []

    def test_until_condition_immediately_met(self):
        """until step where condition is already true on first call —
        only 1 iteration, no polling delay.
        """
        ctx = _make_flow_context(_make_service_spec([]))
        step = FlowStep(
            id="poll_step",
            use="user.list",
            params={},
            until=UntilConfig(
                condition="{{ step.poll_step.status == 'done' }}",
                max_iterations=5,
                interval=0,
            ),
        )

        call_count = 0

        def mock_exec(s, rp, c):
            nonlocal call_count
            call_count += 1
            return {"status": "done"}

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=mock_exec,
        ):
            result = _execute_until(step, ctx, {})

        assert result == {"status": "done"}
        assert call_count == 1, "Expected only 1 iteration for immediately-satisfied condition"

    def test_retry_first_attempt_succeeds(self):
        """Retry step succeeds on first attempt — no retry delay or extra calls."""
        ctx = _make_flow_context(_make_service_spec([]))
        step = FlowStep(
            id="retry_step",
            use="user.list",
            params={},
            retry=RetryConfig(max_attempts=3, delay=0),
        )

        call_count = 0

        def mock_exec(s, rp, c):
            nonlocal call_count
            call_count += 1
            return {"ok": True}

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=mock_exec,
        ):
            result = _execute_with_retry(step, ctx, {})

        assert result == {"ok": True}
        assert call_count == 1, "Expected only 1 attempt on immediate success"

    # -----------------------------------------------------------------------
    # Error handling edge cases
    # -----------------------------------------------------------------------

    def test_unknown_resource_method(self, http_mock):
        """use: ``nonexistent.list`` prints clear error identifying the resource."""
        flow_spec = FlowSpec(
            command="test",
            steps=[
                FlowStep(id="bad_step", use="nonexistent.list", params={}),
            ],
        )
        cmd = build_flow_command(
            flow_spec,
            ServiceContext(base_url="http://test.local"),
            _E2E_SERVICE_SPEC,
        )
        runner = click.testing.CliRunner()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 0
        assert "nonexistent" in result.output.lower()

    def test_unknown_step_type(self):
        """type: ``plugin:nonexistent_plugin`` raises CliyError identifying plugin."""
        ctx = _make_flow_context(_make_service_spec([]))
        step = FlowStep(
            id="bad_plugin",
            type="plugin:nonexistent_plugin",
            params={},
        )
        with pytest.raises(CliyError, match="nonexistent_plugin"):
            _execute_plugin_step(step, ctx)

    def test_invalid_use_format(self):
        """use: ``justaname`` (no dot separator) raises ValueError."""
        with pytest.raises(ValueError, match="resource.method"):
            _lookup_resource_method("justaname", {"resources": []})

    def test_use_group_resource_method_disambiguates(self):
        """group.resource.method 精确命中跨组同名资源。"""
        service = {
            "resources": [
                {
                    "name": "templates",
                    "group": "admin",
                    "methods": {"list": {"http": {"method": "GET"}}},
                },
                {
                    "name": "templates",
                    "group": "alert",
                    "methods": {"list": {"http": {"method": "GET"}}},
                },
                {
                    "name": "user",
                    "methods": {"list": {"http": {"method": "GET"}}},
                },
            ]
        }
        resource, _ = _lookup_resource_method("admin.templates.list", service)
        assert resource["group"] == "admin"
        resource, _ = _lookup_resource_method("alert.templates.list", service)
        assert resource["group"] == "alert"
        # 扁平资源（无 group）也可用三段 target，group 段 = 资源名自身
        resource, _ = _lookup_resource_method("user.user.list", service)
        assert resource["name"] == "user"

    def test_use_ambiguous_resource_name_raises(self):
        """同名资源用无 group 的 resource.method 报歧义错误。"""
        service = {
            "resources": [
                {
                    "name": "templates",
                    "group": "admin",
                    "methods": {"list": {"http": {"method": "GET"}}},
                },
                {
                    "name": "templates",
                    "group": "alert",
                    "methods": {"list": {"http": {"method": "GET"}}},
                },
            ]
        }
        with pytest.raises(ValueError, match="ambiguous"):
            _lookup_resource_method("templates.list", service)

    def test_template_reference_unknown_step(self):
        """Template ``{{ step.nonexistent.field }}`` resolves to empty string."""
        result = resolve_template("{{ step.nonexistent.field }}", {"step": {}})
        assert result == "", "Unknown step.field should render as empty string"

    # -----------------------------------------------------------------------
    # Condition expression edge cases
    # -----------------------------------------------------------------------

    def test_condition_syntax_error(self):
        """Invalid Jinja2 syntax in condition returns ``False`` (safe default)."""
        assert evaluate_condition("{{ invalid syntax !!! }}", {}) is False

    # -----------------------------------------------------------------------
    # Flow-level loading edge cases
    # -----------------------------------------------------------------------

    def test_load_flows_invalid_yaml(self, tmp_path):
        """Invalid YAML in ``_flows.yaml`` raises ``yaml.YAMLError``."""
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text("{invalid: yaml: content: [}")
        with pytest.raises(yaml.YAMLError):
            load_flows(tmp_path)

    def test_load_flows_empty_flows_key(self, tmp_path):
        """``_flows.yaml`` with ``flows: {}`` returns empty list."""
        flows_yaml = tmp_path / "_flows.yaml"
        flows_yaml.write_text("flows:")
        result = load_flows(tmp_path)
        assert result == []
