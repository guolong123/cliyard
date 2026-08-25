"""Tests for Issue #24 — Flow engine fixes.

Four independent fixes:

1. ``execute_use_step`` must honor saved credentials endpoints
   (``~/.cliyard/credentials.yaml`` ``endpoints.<server>``) so that flow
   ``use:`` steps target the user-configured server, not the spec default.
2. Jinja2 sandbox needs ``split`` and ``re_extract`` filters (string method
   calls like ``msg.split(...)`` are blocked by SandboxedEnvironment).
3. ``on_result`` sub-steps must honor ``show_response: true`` without
   requiring ``--verbose``.
4. ``for_each`` must support ``type: echo`` sub-steps.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from cliyard.engine.builder import ServiceContext, build_flow_command
from cliyard.engine.errors import CliyError
from cliyard.engine.flow import FlowSpec, FlowStep, ForEachConfig
from cliyard.engine.orchestrator import (
    FlowContext,
    execute_use_step,
    handle_on_result,
    _execute_for_each,
)
from cliyard.engine.template import Template
from cliyard.plugin import PluginRegistry, register_step_type

from tests.test_flow import MockConsole, MockHttpClient


class Issue24MockHttpClient(MockHttpClient):
    """MockHttpClient accepting the ``files`` kwarg used by the pipeline."""

    def request(
        self,
        method,
        url,
        data=None,
        query_params=None,
        headers=None,
        timeout=None,
        files=None,
    ):
        self._last_request = {
            "method": method,
            "url": url,
            "data": data,
            "query_params": query_params,
            "files": files,
            "headers": headers,
        }
        return self._response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_spec(resources: list[dict]) -> dict:
    """Build a service spec with named servers (new list format)."""
    return {
        "name": "test",
        "servers": {
            "default": {"base_url": "http://localhost", "prefix": "/api/v1"},
            "java": {
                "base_url": "http://localhost:8080/",
                "prefix": "/api/v1",
            },
        },
        "resources": resources,
    }


def _java_resource():
    """Resource that declares ``server: java``."""
    return {
        "name": "partner",
        "path": "partners",
        "server": "java",
        "methods": {
            "list": {
                "http": {"method": "GET", "path": "partners"},
                "params": {"query": []},
            },
        },
    }


def _make_flow_context(
    service_spec: dict,
    saved_endpoints: dict | None = None,
    step_state: dict | None = None,
) -> FlowContext:
    """Build a FlowContext with mocked console and HTTP client."""
    return FlowContext(
        flow_params={},
        step_state=step_state or {},
        http_client=Issue24MockHttpClient(),
        console=MockConsole(),
        service_spec=service_spec,
        base_url="http://localhost",
        prefix="/api/v1",
        saved_endpoints=saved_endpoints or {},
    )


# ---------------------------------------------------------------------------
# 1. execute_use_step honors saved credentials endpoints
# ---------------------------------------------------------------------------


class TestSavedEndpoints:
    """Flow ``use:`` steps must target user-configured credentials endpoints."""

    def test_use_step_uses_saved_endpoint_over_spec_default(self):
        """saved endpoints.<server> beats the spec server base_url."""
        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(
            spec,
            saved_endpoints={"java": "https://crm-api-java-test6.ehsy.com/"},
        )

        step = FlowStep(id="list_partners", use="partner.list", params={})
        execute_use_step(step, {}, ctx)

        last_url = ctx.http_client._last_request["url"]
        assert last_url.startswith(
            "https://crm-api-java-test6.ehsy.com/api/v1/partners"
        ), f"Expected saved endpoint URL, got {last_url}"

    def test_use_step_falls_back_to_spec_server(self):
        """Without saved endpoint, spec server base_url is used."""
        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(spec)

        step = FlowStep(id="list_partners", use="partner.list", params={})
        execute_use_step(step, {}, ctx)

        last_url = ctx.http_client._last_request["url"]
        assert last_url.startswith(
            "http://localhost:8080/api/v1/partners"
        ), f"Expected spec server URL, got {last_url}"

    def test_runtime_server_override_wins(self):
        """--server override still takes precedence over saved endpoints."""
        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(
            spec,
            saved_endpoints={"java": "https://crm-api-java-test6.ehsy.com/"},
        )
        ctx.server_override = "https://override.example.com/"

        step = FlowStep(id="list_partners", use="partner.list", params={})
        execute_use_step(step, {}, ctx)

        last_url = ctx.http_client._last_request["url"]
        assert last_url.startswith(
            "https://override.example.com/api/v1/partners"
        ), f"Expected override URL, got {last_url}"


# ---------------------------------------------------------------------------
# 2. Jinja2 sandbox filters: split / re_extract
# ---------------------------------------------------------------------------


class TestSandboxFilters:
    """Jinja2 sandbox must provide split / re_extract filters."""

    def test_split_filter(self):
        result = Template("{{ msg | split('<br/>') }}").render(
            msg="line1<br/>line2<br/>line3"
        )
        assert result == '["line1", "line2", "line3"]'

    def test_split_filter_default_separator(self):
        result = Template("{{ msg | split() }}").render(msg="a b c")
        assert result == '["a", "b", "c"]'

    def test_re_extract_filter(self):
        result = Template("{{ msg | re_extract('\\\\d{4}-\\\\d{2}-\\\\d{2}') }}").render(
            msg="created 2026-08-11 ok"
        )
        assert result == "2026-08-11"

    def test_re_extract_group(self):
        result = Template("{{ msg | re_extract('code=(\\\\w+)', 1) }}").render(
            msg="code=HELLO"
        )
        assert result == "HELLO"

    def test_split_filter_usable_in_condition(self):
        """split output can feed a length comparison in conditions."""
        result = Template("{{ msg | split('<br/>') | length }}").render(
            msg="a<br/>b"
        )
        assert result == "2"


# ---------------------------------------------------------------------------
# 3. on_result sub-steps honor show_response
# ---------------------------------------------------------------------------


class TestOnResultShowResponse:
    """Sub-steps inside on_result must print details when show_response: true."""

    def test_sub_step_show_response_prints_details(self):
        from cliyard.engine.flow import FlowStep as FS

        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(spec)
        ctx.step_state = {"check": {"found": False}}

        on_result = [
            {
                "if": "{{ step.check.found }}",
                "then": [],
                "else": {
                    "steps": [
                        {
                            "id": "create_partner",
                            "use": "partner.list",
                            "params": {"query": {"env": "test"}},
                            "show_response": True,
                        },
                    ],
                },
            }
        ]

        ctx.http_client.set_json_response({"partners": []})

        handle_on_result(on_result, ctx, "check")

        # Even without --verbose, show_response sub-steps print params+response
        assert "params:" in "\n".join(ctx.console.output)
        assert "response:" in "\n".join(ctx.console.output)

    def test_sub_step_without_show_response_stays_quiet(self):
        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(spec)
        ctx.step_state = {"check": {"found": False}}

        on_result = [
            {
                "if": "{{ step.check.found }}",
                "then": [],
                "else": {
                    "steps": [
                        {
                            "id": "create_partner",
                            "use": "partner.list",
                            "params": {"query": {"env": "test"}},
                        },
                    ],
                },
            }
        ]

        ctx.http_client.set_json_response({"partners": []})

        handle_on_result(on_result, ctx, "check")

        assert "params:" not in "\n".join(ctx.console.output)


# ---------------------------------------------------------------------------
# 4. for_each supports type: echo sub-steps
# ---------------------------------------------------------------------------


class TestForEachEcho:
    """for_each loops must support type: echo sub-steps."""

    def test_for_each_echo_step(self):
        spec = _make_service_spec([_java_resource()])
        ctx = _make_flow_context(
            spec,
            step_state={"item_list": [{"name": "a"}, {"name": "b"}]},
        )

        sub_steps = [
            FlowStep(
                id="print_item",
                type="echo",
                params={"message": "Processing {{ row.name }}"},
            ),
            FlowStep(
                id="do_thing",
                use="partner.list",
                params={"query": {"name": "{{ row.name }}"}},
            ),
        ]

        step = FlowStep(
            id="foreach_step",
            for_each=ForEachConfig(
                items="{{ step.item_list }}",
                as_name="row",
                steps=sub_steps,
            ),
        )

        ctx.http_client.set_json_response({"partners": []})

        results = _execute_for_each(step, ctx)

        output = "\n".join(ctx.console.output)
        assert "Processing a" in output
        assert "Processing b" in output
        assert len(results) == 2


# ---------------------------------------------------------------------------
# 5. on_result sub-steps with type:plugin: are executed (Issue #57)
# ---------------------------------------------------------------------------


class TestOnResultPluginSubSteps:
    """Plugin sub-steps inside on_result then/else blocks must be executed."""

    def test_plugin_sub_step_in_then_block(self):
        """A type: plugin:xxx sub-step in a then block is executed."""
        call_log: list[dict] = []

        @register_step_type("test_plugin")
        def test_plugin(params: dict, context: object) -> dict:
            call_log.append(params)
            return {"plugin_called": True}

        spec = _make_service_spec([])
        ctx = _make_flow_context(spec)
        ctx.step_state = {"check": {"count": 5}}

        on_result = [
            {
                "if": "{{ step.check.count > 0 }}",
                "then": [
                    {
                        "id": "plugin_step",
                        "type": "plugin:test_plugin",
                        "params": {"input": "hello"},
                    }
                ],
            }
        ]

        handle_on_result(on_result, ctx, "check")

        assert len(call_log) == 1, f"Expected 1 plugin call, got {len(call_log)}"
        assert call_log[0] == {"input": "hello"}
        assert "plugin_step" in ctx.step_state
        assert ctx.step_state["plugin_step"] == {"plugin_called": True}

    def test_plugin_sub_step_in_else_block(self):
        """A type: plugin:xxx sub-step in an else block is executed."""
        call_log: list[dict] = []

        @register_step_type("test_plugin_else")
        def test_plugin_else(params: dict, context: object) -> dict:
            call_log.append(params)
            return {"else_plugin_called": True}

        spec = _make_service_spec([])
        ctx = _make_flow_context(spec)
        ctx.step_state = {"check": {"count": 0}}

        on_result = [
            {
                "if": "{{ step.check.count > 0 }}",
                "then": [],
                "else": [
                    {
                        "id": "else_plugin_step",
                        "type": "plugin:test_plugin_else",
                        "params": {"input": "fallback"},
                    }
                ],
            }
        ]

        handle_on_result(on_result, ctx, "check")

        assert len(call_log) == 1, f"Expected 1 plugin call, got {len(call_log)}"
        assert call_log[0] == {"input": "fallback"}
        assert "else_plugin_step" in ctx.step_state
        assert ctx.step_state["else_plugin_step"] == {"else_plugin_called": True}

    def test_plugin_sub_step_with_show_response(self):
        """Plugin sub-step with show_response: true prints details."""
        call_log: list[dict] = []

        @register_step_type("test_plugin_show")
        def test_plugin_show(params: dict, context: object) -> dict:
            call_log.append(params)
            return {"show_called": True}

        spec = _make_service_spec([])
        ctx = _make_flow_context(spec)
        ctx.step_state = {"check": {"count": 1}}

        on_result = [
            {
                "if": "{{ step.check.count > 0 }}",
                "then": [
                    {
                        "id": "plugin_show_step",
                        "type": "plugin:test_plugin_show",
                        "params": {"input": "show_me"},
                        "show_response": True,
                    }
                ],
            }
        ]

        handle_on_result(on_result, ctx, "check")

        assert len(call_log) == 1
        assert call_log[0] == {"input": "show_me"}
        # show_response should trigger verbose output even without --verbose
        output = "\n".join(ctx.console.output)
        assert "params:" in output
        assert "response:" in output