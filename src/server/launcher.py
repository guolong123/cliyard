"""Shared server launcher for the cliyard web UI.

Used by two entry points:

* the top-level ``cliyard serve <spec-dir>`` command
  (:mod:`cliyard.cli.serve`);
* the ``server`` sub-command attached by
  :func:`cliyard.runtime.create_cli` to generated CLIs
  (:mod:`cliyard.runtime.server_command`).

Keeping the startup sequence in one place guarantees both entry points
behave identically (browser-URL remapping, fail-fast error handling,
uvicorn launch).
"""

from __future__ import annotations

import os
import webbrowser

import click
import uvicorn

from cliyard.server.app import create_app


def browser_url(host: str, port: int) -> str:
    """Build a browser-addressable URL (0.0.0.0/:: -> 127.0.0.1)."""
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{display_host}:{port}"


def build_app_or_exit(spec_dir: str):
    """Build the app, converting invalid specs into a clean exit(1)."""
    try:
        return create_app(spec_dir)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def run_server(
    spec_dir: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    reload: bool = False,
) -> None:
    """Start the FastAPI web server for *spec_dir*.

    Args:
        spec_dir: Path to the cliyard spec directory.
        host: Bind address passed through to uvicorn.
        port: Bind port passed through to uvicorn.
        open_browser: Open the browser-addressable URL after startup.
        reload: Enable uvicorn auto-reload (import-string factory mode;
            requires the app factory to read ``CLIYARD_SPEC_DIR``).
    """
    url = browser_url(host, port)

    if reload:
        # Validate the spec up front (fail fast, clean error).
        build_app_or_exit(spec_dir)
        # uvicorn --reload needs an import string, not an app instance.
        os.environ["CLIYARD_SPEC_DIR"] = spec_dir
        if open_browser:
            webbrowser.open(url)
        click.echo(f"Serve spec {spec_dir} at {url} (reload on)")
        uvicorn.run(
            "cliyard.server.app:create_app_from_env",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
        return

    app = build_app_or_exit(spec_dir)
    if open_browser:
        webbrowser.open(url)
    click.echo(f"Serve spec {spec_dir} at {url}")
    uvicorn.run(app, host=host, port=port)
