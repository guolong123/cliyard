"""Tests for the --format option: operation commands, yaml, plugin formats."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import click.testing
import pytest

from cliyard.engine.builder import (
    ServiceContext,
    _build_format_option,
    _render_output,
    build_list_command,
    build_operation_command,
    build_resource_group,
)
from cliyard.output.formatter import format_as_yaml
from cliyard.plugin import PluginRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _cleanup_plugins():
    PluginRegistry.clear()
    yield
    PluginRegistry.clear()


def _ctx(base_url: str = "http://localhost", default_format: str = "json") -> ServiceContext:
    return ServiceContext(base_url=base_url, default_format=default_format)


# ---------------------------------------------------------------------------
# format_as_yaml
# ---------------------------------------------------------------------------


class TestFormatAsYaml:
    def test_dict_output(self):
        result = format_as_yaml({"name": "myrepo", "type": "EVENTS"})
        assert "name: myrepo" in result
        assert "type: EVENTS" in result

    def test_cjk_unicode_not_escaped(self):
        result = format_as_yaml({"desc": "仓库"})
        assert "仓库" in result

    def test_list_output(self):
        result = format_as_yaml([{"a": 1}])
        assert "- a: 1" in result


# ---------------------------------------------------------------------------
# _render_output dispatch
# ---------------------------------------------------------------------------


class TestRenderOutput:
    def test_json(self):
        assert _render_output("json", {"a": 1}) == '{\n  "a": 1\n}'

    def test_yaml(self):
        assert _render_output("yaml", {"a": 1}) == "a: 1\n"

    def test_table(self):
        result = _render_output("table", {"items": [{"name": "x"}], "fields": [{"name": "name", "alias": "Name"}]})
        assert "Name" in result and "x" in result

    def test_csv(self):
        fields = [{"name": "name", "alias": "Name"}]
        result = _render_output("csv", {"items": [{"name": "x"}], "fields": fields}, fields)
        assert result.splitlines() == ["Name", "x"]

    def test_plugin_registered_format(self):
        calls = []

        def format_xml(data, fields=None):
            calls.append((data, fields))
            return "<root/>"

        PluginRegistry.register_output_format("xml", format_xml)
        assert _render_output("xml", {"a": 1}) == "<root/>"
        assert calls == [({"a": 1}, None)]

    def test_plugin_registered_format_without_fields_param(self):
        def format_csv_like(data):
            return "custom"

        PluginRegistry.register_output_format("custom", format_csv_like)
        assert _render_output("custom", {"a": 1}) == "custom"

    def test_unknown_format_falls_back_to_json(self):
        assert _render_output("nope", {"a": 1}) == '{\n  "a": 1\n}'


# ---------------------------------------------------------------------------
# --format option on list + operation commands
# ---------------------------------------------------------------------------


class TestFormatOption:
    def test_list_command_has_format_option(self):
        resource = {
            "name": "repos",
            "methods": {
                "list": {"http": {"method": "GET"}, "output": {"items_path": "$.repos"}},
                "get": {"http": {"method": "GET", "path": "repos/{{ id }}"}, "params": {"path": [{"name": "id", "type": "string"}]}},
            },
        }
        cmd = build_list_command(resource, _ctx())
        names = [p.name for p in cmd.params]
        assert "format" in names

    def test_operation_command_has_format_option(self):
        resource = {
            "name": "repos",
            "methods": {
                "create": {"http": {"method": "POST", "path": "repos"}},
            },
        }
        cmd = build_operation_command("create", resource["methods"]["create"], resource, _ctx())
        names = [p.name for p in cmd.params]
        assert "format" in names

    def test_format_choices_include_yaml(self):
        resource = {
            "name": "repos",
            "methods": {"list": {"http": {"method": "GET"}}},
        }
        cmd = build_list_command(resource, _ctx())
        fmt = next(p for p in cmd.params if p.name == "format")
        assert "yaml" in fmt.type.choices
        assert "json" in fmt.type.choices
        assert "table" in fmt.type.choices
        assert "csv" in fmt.type.choices

    def test_plugin_format_added_to_choices(self):
        PluginRegistry.register_output_format("xml", lambda data, fields=None: "<xml/>")
        resource = {
            "name": "repos",
            "methods": {"list": {"http": {"method": "GET"}}},
        }
        cmd = build_list_command(resource, _ctx())
        fmt = next(p for p in cmd.params if p.name == "format")
        assert "xml" in fmt.type.choices

    def test_plugin_format_does_not_override_builtin(self):
        PluginRegistry.register_output_format("json", lambda data, fields=None: "hacked")
        resource = {
            "name": "repos",
            "methods": {"list": {"http": {"method": "GET"}}},
        }
        cmd = build_list_command(resource, _ctx())
        fmt = next(p for p in cmd.params if p.name == "format")
        # Built-in json still renders real JSON, not the plugin function
        assert _render_output("json", {"a": 1}) == '{\n  "a": 1\n}'
        assert fmt.type.choices.count("json") == 1


class TestDefaultFormat:
    def test_defaults_to_json(self):
        opt = _build_format_option({"http": {"method": "GET"}}, _ctx())
        assert opt.default == "json"

    def test_output_default_takes_precedence(self):
        opt = _build_format_option({"output": {"default": "table"}}, _ctx())
        assert opt.default == "table"

    def test_service_default_used_when_method_has_none(self):
        opt = _build_format_option({"http": {"method": "GET"}}, _ctx(default_format="yaml"))
        assert opt.default == "yaml"

    def test_method_default_beats_service_default(self):
        opt = _build_format_option({"output": {"default": "csv"}}, _ctx(default_format="yaml"))
        assert opt.default == "csv"


# ---------------------------------------------------------------------------
# End-to-end: invoke with --format yaml (mocked HTTP)
# ---------------------------------------------------------------------------


class TestFormatEndToEnd:
    def _invoke(self, http_mock, argv):
        resource = {
            "name": "repos",
            "methods": {
                "list": {
                    "http": {"method": "GET"},
                    "output": {"items_path": "$.repos"},
                    "params": {"query": [{"name": "page", "type": "int", "default": 1}]},
                },
                "create": {
                    "http": {"method": "POST", "path": "repos"},
                    "params": {"body": [{"name": "name", "type": "string", "required": True}]},
                    "request_body": {"name": "{{ name }}"},
                },
            },
        }
        group = build_resource_group(resource["name"], resource, _ctx())
        runner = click.testing.CliRunner()
        return runner.invoke(group, argv)

    @pytest.fixture
    def http_mock(self, monkeypatch):
        calls: list[dict] = []

        def _mock_request(self_obj, method, url, data=None, query_params=None, headers=None, timeout=None, files=None):
            calls.append({"method": method, "url": url, "data": data})
            resp = MagicMock()
            if method == "POST":
                resp.json.return_value = {"name": "myrepo", "ok": True}
            else:
                resp.json.return_value = {"repos": [{"name": "a"}]}
            resp.status_code = 200
            resp.text = ""
            return resp

        monkeypatch.setattr("cliyard.client.http.HttpClient.request", _mock_request)
        return calls

    def test_operation_accepts_format_option(self, http_mock):
        result = self._invoke(http_mock, ["create", "--name=myrepo", "--format=yaml"])
        assert result.exit_code == 0
        assert http_mock[-1]["method"] == "POST"
        assert http_mock[-1]["data"] == {"name": "myrepo"}
        assert "name: myrepo" in result.output

    def test_operation_defaults_to_json(self, http_mock):
        result = self._invoke(http_mock, ["create", "--name=myrepo"])
        assert result.exit_code == 0
        assert '"name": "myrepo"' in result.output

    def test_list_yaml_output(self, http_mock):
        result = self._invoke(http_mock, ["list", "--format=yaml"])
        assert result.exit_code == 0
        assert "name: a" in result.output

    def test_list_json_output_has_no_fields(self, http_mock):
        result = self._invoke(http_mock, ["list", "--format=json"])
        assert result.exit_code == 0
        assert '"fields"' not in result.output


# ---------------------------------------------------------------------------
# parse_response must not leak display metadata into data payload
# ---------------------------------------------------------------------------


class TestParseResponseExcludesFields:
    def test_parse_response_omits_fields_key(self):
        from cliyard.output.handler import parse_response

        resp = MagicMock()
        resp.json.return_value = {"repos": [{"name": "a", "bytes": "1b"}], "total": 1}
        parsed = parse_response(resp, {"items_path": "$.repos", "total_path": "$.total",
                                       "fields": [{"name": "name", "alias": "仓库名称"}]})
        assert parsed == {"items": [{"name": "a", "bytes": "1b"}], "total": 1}
        assert "fields" not in parsed
