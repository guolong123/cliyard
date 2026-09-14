"""Tests for the ``server`` sub-command attached by ``create_cli``.

Covers registration on the returned click group, the option surface
(no spec-dir argument), delegation to the shared launcher with the
captured spec_dir, and error handling for a failing app build.
"""

from __future__ import annotations

from pathlib import Path

import click.testing

from cliyard.runtime import create_cli

_DEMO_SPEC = Path(__file__).resolve().parent.parent / "examples" / "demo"


def _runner() -> click.testing.CliRunner:
    return click.testing.CliRunner()


def test_group_contains_server_command():
    """``create_cli`` should attach a ``server`` sub-command."""
    group = create_cli(str(_DEMO_SPEC))
    assert "server" in group.commands


def test_server_help_shows_options_without_spec_dir():
    """``server --help`` shows --host/--port/--open and no SPEC_DIR arg."""
    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(group, ["server", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--open" in result.output
    assert "SPEC_DIR" not in result.output


def test_server_delegates_to_shared_launcher(monkeypatch):
    """``server`` calls ``run_server`` with the captured spec_dir + options."""
    launched: dict = {}

    def fake_run_server(spec_dir, **kwargs):
        launched["spec_dir"] = spec_dir
        launched["host"] = kwargs.get("host")
        launched["port"] = kwargs.get("port")
        launched["open_browser"] = kwargs.get("open_browser")

    monkeypatch.setattr("cliyard.runtime.server_command.run_server", fake_run_server)

    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(
        group,
        ["server", "--host", "0.0.0.0", "--port", "9000", "--open"],
    )
    assert result.exit_code == 0
    assert Path(launched["spec_dir"]).is_dir()
    assert launched["host"] == "0.0.0.0"
    assert launched["port"] == 9000
    assert launched["open_browser"] is True


def test_server_launches_uvicorn_with_captured_spec_dir(monkeypatch):
    """Real path: create_app receives the captured spec_dir, then uvicorn runs."""
    created_apps: list[str] = []
    launched: dict = {}

    def fake_create_app(spec_dir, **kwargs):
        created_apps.append(str(spec_dir))
        return object()

    def fake_run(app, **kwargs):
        launched["host"] = kwargs.get("host")
        launched["port"] = kwargs.get("port")

    monkeypatch.setattr("cliyard.server.launcher.create_app", fake_create_app)
    monkeypatch.setattr("cliyard.server.launcher.uvicorn.run", fake_run)

    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(group, ["server", "--host", "127.0.0.1", "--port", "8080"])
    assert result.exit_code == 0
    assert len(created_apps) == 1
    assert Path(created_apps[0]).is_dir()
    assert launched.get("host") == "127.0.0.1"
    assert launched.get("port") == 8080


def test_server_app_build_error_exits_nonzero(monkeypatch):
    """A failing app build surfaces as a clean click error (exit != 0)."""
    def boom(spec_dir, **kwargs):
        raise FileNotFoundError(f"Spec directory not found: {spec_dir}")

    monkeypatch.setattr("cliyard.server.launcher.create_app", boom)

    group = create_cli(str(_DEMO_SPEC))
    result = _runner().invoke(group, ["server"])
    assert result.exit_code != 0
    assert "Error" in result.output
