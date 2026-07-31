"""Tests for runtime base_url override: --server flag, env vars, create_cli param."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from cliyard.runtime.runner import (
    _resolve_base_url_override,
    create_cli,
    extract_server_override,
    run_with_spec,
)

FIXTURES_DIR = "tests/fixtures/spec-dir"


@pytest.fixture(autouse=True)
def _no_saved_profile(monkeypatch):
    """Isolate tests from any ~/.cliyard/credentials.yaml on this host."""
    monkeypatch.delenv("CLIYARD_SERVER", raising=False)
    monkeypatch.delenv("TEST_SERVICE_SERVER", raising=False)
    monkeypatch.delenv("TEST_SERVICE_SERVER", raising=False)
    monkeypatch.setattr("cliyard.client.credentials.get_current_profile", lambda: None)
    yield


@pytest.fixture
def http_mock(monkeypatch):
    """Record every request URL sent by HttpClient."""
    calls: list[str] = []

    def _mock_request(self_obj, method, url, data=None, query_params=None, headers=None, timeout=None, files=None):
        calls.append(url)
        resp = MagicMock()
        resp.json.return_value = {}
        resp.status_code = 200
        resp.text = ""
        return resp

    monkeypatch.setattr("cliyard.client.http.HttpClient.request", _mock_request)
    return calls


def _invoke_list(cli):
    runner = click.testing.CliRunner()
    return runner.invoke(cli, ["repos", "list"])


# ---------------------------------------------------------------------------
# extract_server_override
# ---------------------------------------------------------------------------


class TestExtractServerOverride:
    def test_server_space_separated(self):
        argv, server = extract_server_override(["--server", "http://staging", "repos", "list"])
        assert server == "http://staging"
        assert argv == ["repos", "list"]

    def test_server_equals_form(self):
        argv, server = extract_server_override(["--server=http://staging", "repos"])
        assert server == "http://staging"
        assert argv == ["repos"]

    def test_short_flag(self):
        argv, server = extract_server_override(["-s", "http://staging", "repos"])
        assert server == "http://staging"
        assert argv == ["repos"]

    def test_short_flag_equals_form(self):
        argv, server = extract_server_override(["-s=http://staging", "repos"])
        assert server == "http://staging"
        assert argv == ["repos"]

    def test_no_override(self):
        argv, server = extract_server_override(["repos", "list", "--page=2"])
        assert server is None
        assert argv == ["repos", "list", "--page=2"]

    def test_unrelated_options_untouched(self):
        argv, server = extract_server_override(["--format", "yaml", "repos"])
        assert server is None
        assert argv == ["--format", "yaml", "repos"]


# ---------------------------------------------------------------------------
# _resolve_base_url_override
# ---------------------------------------------------------------------------


class TestResolveBaseUrlOverride:
    def test_explicit_param_wins(self, monkeypatch):
        monkeypatch.setenv("CLIYARD_SERVER", "http://env")
        assert _resolve_base_url_override("my-cli", "http://param") == "http://param"

    def test_service_name_env(self, monkeypatch):
        monkeypatch.setenv("MY_CLI_SERVER", "http://service-env")
        assert _resolve_base_url_override("my-cli", None) == "http://service-env"

    def test_cliyard_env_fallback(self, monkeypatch):
        monkeypatch.setenv("CLIYARD_SERVER", "http://generic")
        assert _resolve_base_url_override("my-cli", None) == "http://generic"

    def test_service_env_beats_generic(self, monkeypatch):
        monkeypatch.setenv("MY_CLI_SERVER", "http://service-env")
        monkeypatch.setenv("CLIYARD_SERVER", "http://generic")
        assert _resolve_base_url_override("my-cli", None) == "http://service-env"

    def test_none_when_nothing_set(self):
        assert _resolve_base_url_override("my-cli", None) is None


# ---------------------------------------------------------------------------
# create_cli base_url_override
# ---------------------------------------------------------------------------


class TestCreateCliOverride:
    def test_spec_default_url_used_without_override(self, http_mock):
        cli = create_cli(FIXTURES_DIR)
        result = _invoke_list(cli)
        assert result.exit_code == 0
        assert http_mock == ["https://httpbin.org/repos"]

    def test_env_cliyard_server_override(self, http_mock, monkeypatch):
        monkeypatch.setenv("CLIYARD_SERVER", "http://override.example.com")
        cli = create_cli(FIXTURES_DIR)
        result = _invoke_list(cli)
        assert result.exit_code == 0
        assert http_mock == ["http://override.example.com/repos"]

    def test_service_name_env_override(self, http_mock, monkeypatch):
        monkeypatch.setenv("TEST_SERVICE_SERVER", "http://svc.example.com")
        cli = create_cli(FIXTURES_DIR)
        result = _invoke_list(cli)
        assert result.exit_code == 0
        assert http_mock == ["http://svc.example.com/repos"]

    def test_explicit_param_beats_env(self, http_mock, monkeypatch):
        monkeypatch.setenv("CLIYARD_SERVER", "http://env.example.com")
        cli = create_cli(FIXTURES_DIR, base_url_override="http://param.example.com")
        result = _invoke_list(cli)
        assert result.exit_code == 0
        assert http_mock == ["http://param.example.com/repos"]


# ---------------------------------------------------------------------------
# run_with_spec --server flag
# ---------------------------------------------------------------------------


class TestRunWithSpecServerFlag:
    def test_server_flag_passed_to_create_cli(self, monkeypatch, http_mock):
        monkeypatch.setattr(sys, "argv", ["mycli", "--server", "http://flag.example.com", "repos", "list"])
        with patch("cliyard.runtime.runner.create_cli") as mock_create:
            mock_create.return_value = _build_group()
            with pytest.raises(SystemExit):
                run_with_spec(FIXTURES_DIR)
        _, kwargs = mock_create.call_args
        assert kwargs["base_url_override"] == "http://flag.example.com"
        assert sys.argv == ["mycli", "repos", "list"]

    def test_server_flag_end_to_end(self, monkeypatch, http_mock):
        monkeypatch.setattr(sys, "argv", ["mycli", "--server", "http://flag.example.com", "repos", "list"])
        with pytest.raises(SystemExit) as exc_info:
            run_with_spec(FIXTURES_DIR)
        assert exc_info.value.code in (0, 1, None)
        assert http_mock == ["http://flag.example.com/repos"]


def _build_group():
    from cliyard.engine.builder import build_resource_group
    from cliyard.engine.loader import load_resource

    resource = load_resource("tests/fixtures/repos_resource.yaml")
    return build_resource_group(resource["name"], resource, _ctx_for_test())


def _ctx_for_test():
    from cliyard.engine.builder import ServiceContext

    return ServiceContext(base_url="http://localhost", prefix="", servers={})
