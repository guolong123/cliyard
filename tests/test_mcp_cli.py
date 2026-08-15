"""Tests for the ``cliyard mcp`` command + generated-CLI ``mcp`` sub-command.

Covers:
- ``cliyard --help`` 列出 mcp；``mcp --help`` 选项面（transport/host/port/server/token）
- ``cliyard mcp <spec-dir>`` 委托 run_mcp_server（stdio 默认；--transport http 透传）
- 生成 CLI（create_cli）注册 ``mcp`` 子命令并闭包捕获 spec_dir（镜像 server 子命令）
"""

from __future__ import annotations

from pathlib import Path

import click.testing

from cliyard.cli.__main__ import cli
from cliyard.runtime import create_cli

_DEMO_SPEC = Path(__file__).resolve().parent.parent / "examples" / "demo"


def _runner() -> click.testing.CliRunner:
    return click.testing.CliRunner()


# ---------------------------------------------------------------------------
# 顶层 cliyard mcp
# ---------------------------------------------------------------------------


def test_cli_help_lists_mcp_command():
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "mcp" in result.output


def test_mcp_help_shows_options():
    result = _runner().invoke(cli, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "SPEC_DIR" in result.output
    assert "--transport" in result.output
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--server" in result.output
    assert "--token" in result.output
    assert "--allow-remote-no-auth" in result.output


def test_mcp_missing_spec_dir_fails():
    result = _runner().invoke(cli, ["mcp"])
    assert result.exit_code != 0
    assert "SPEC_DIR" in result.output


def test_mcp_nonexistent_spec_dir_fails():
    result = _runner().invoke(cli, ["mcp", "/nonexistent/spec-dir"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_mcp_delegates_to_run_mcp_server_stdio_default(monkeypatch):
    """默认 stdio：run_mcp_server 收到 spec_dir + transport=stdio。"""
    seen: dict = {}

    def fake_run(spec_dir, **kwargs):
        seen["spec_dir"] = spec_dir
        seen.update(kwargs)

    monkeypatch.setattr("cliyard.cli.mcp.run_mcp_server", fake_run)
    result = _runner().invoke(cli, ["mcp", str(_DEMO_SPEC)])
    assert result.exit_code == 0
    assert seen["transport"] == "stdio"
    assert seen["server_override"] is None
    assert Path(seen["spec_dir"]).is_dir()


def test_mcp_transport_http_and_overrides(monkeypatch):
    """--transport http + --server + --token 透传。"""
    seen: dict = {}

    def fake_run(spec_dir, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("cliyard.cli.mcp.run_mcp_server", fake_run)
    result = _runner().invoke(
        cli,
        [
            "mcp",
            str(_DEMO_SPEC),
            "--transport",
            "http",
            "--host",
            "0.0.0.0",
            "--port",
            "9090",
            "--server",
            "https://override.example.com",
            "--token",
            "t0ken",
        ],
    )
    assert result.exit_code == 0
    assert seen["transport"] == "http"
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9090
    assert seen["server_override"] == "https://override.example.com"
    assert seen["token"] == "t0ken"


def test_mcp_stdio_bad_transport_rejected():
    """非法 transport → Click 拒绝（exit != 0）。"""
    result = _runner().invoke(cli, ["mcp", str(_DEMO_SPEC), "--transport", "bogus"])
    assert result.exit_code != 0
    assert "stdio" in result.output or "http" in result.output


# ---------------------------------------------------------------------------
# 生成 CLI 的 mcp 子命令
# ---------------------------------------------------------------------------


def test_generated_cli_contains_mcp_command():
    group = create_cli(str(_DEMO_SPEC))
    assert "mcp" in group.commands


def test_generated_mcp_help_shows_options_without_spec_dir():
    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(group, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.output
    assert "--server" in result.output
    assert "SPEC_DIR" not in result.output


def test_generated_mcp_delegates_with_captured_spec_dir(monkeypatch):
    """mcp 子命令用闭包捕获的 spec_dir 调用 run_mcp_server。"""
    seen: dict = {}

    def fake_run(spec_dir, **kwargs):
        seen["spec_dir"] = spec_dir
        seen["transport"] = kwargs.get("transport")
        seen["server_override"] = kwargs.get("server_override")

    monkeypatch.setattr("cliyard.runtime.mcp_command.run_mcp_server", fake_run)
    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(
        group,
        ["mcp", "--transport", "http", "--port", "9191", "--server", "https://x.example"],
    )
    assert result.exit_code == 0
    assert Path(seen["spec_dir"]).is_dir()
    assert seen["transport"] == "http"
    assert seen["server_override"] == "https://x.example"
