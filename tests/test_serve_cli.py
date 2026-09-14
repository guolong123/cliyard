"""Tests for the ``cliyard serve`` command skeleton.

Covers command registration in ``cliyard --help``, argument/option
surface in ``serve --help``, and error handling for a missing or
nonexistent ``<spec-dir>``.
"""

from __future__ import annotations

from pathlib import Path

import click.testing

from cliyard.cli.__main__ import cli

_DEMO_SPEC = Path(__file__).resolve().parent.parent / "examples" / "demo"


def _runner() -> click.testing.CliRunner:
    return click.testing.CliRunner()


def test_cli_help_lists_serve_command():
    """``cliyard --help`` should list the serve command."""
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_serve_help_shows_all_options():
    """``serve --help`` should show spec-dir argument and all options."""
    result = _runner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "SPEC_DIR" in result.output
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--open" in result.output
    assert "--reload" in result.output


def test_serve_missing_spec_dir_fails():
    """``cliyard serve`` without spec-dir should report MissingParameter."""
    result = _runner().invoke(cli, ["serve"])
    assert result.exit_code != 0
    assert "SPEC_DIR" in result.output


def test_serve_nonexistent_spec_dir_fails():
    """``cliyard serve /nonexistent`` should exit non-zero."""
    result = _runner().invoke(cli, ["serve", "/nonexistent/spec-dir"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_serve_existing_spec_dir_prints_startup_params_and_launches(monkeypatch):
    """``serve <spec-dir>`` prints the startup line and calls uvicorn.run.

    T3 wires the real startup into the command body, so uvicorn.run is
    monkeypatched to keep the test non-blocking.
    """
    launched: dict = {}

    def fake_run(app, **kwargs):
        launched["host"] = kwargs.get("host")
        launched["port"] = kwargs.get("port")

    monkeypatch.setattr("cliyard.cli.serve.uvicorn.run", fake_run)

    result = _runner().invoke(
        cli,
        [
            "serve",
            str(_DEMO_SPEC),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--token",
            "test-token",
        ],
    )
    assert result.exit_code == 0
    assert "Serve spec" in result.output
    # browser-facing URL is remapped from 0.0.0.0 to 127.0.0.1
    assert "127.0.0.1:9000" in result.output
    # uvicorn still binds the real 0.0.0.0 host
    assert launched.get("host") == "0.0.0.0"
    assert launched.get("port") == 9000


def test_serve_remote_host_without_token_fails_fast():
    """非本地 host 无 --token → 启动拒绝（mirror MCP --token 语义）。"""
    result = _runner().invoke(
        cli,
        ["serve", str(_DEMO_SPEC), "--host", "0.0.0.0"],
    )
    assert result.exit_code != 0


def test_serve_help_shows_upload_options():
    """``serve --help`` 与 ``mcp --help`` 列出相同的上传三选项 + --token。"""
    result = _runner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--upload-dir" in result.output
    assert "--upload-base-url" in result.output
    assert "--file-allow-dirs" in result.output
    assert "--token" in result.output


def test_serve_dir_without_auth_yaml_exits_nonzero(tmp_path):
    """``serve <dir-without-_auth.yaml>`` exits 1 with a clean error."""
    result = _runner().invoke(cli, ["serve", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error" in result.output
