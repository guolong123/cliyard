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

from cliyard.engine.errors import CliyError
from cliyard.server.app import create_app
from cliyard.server.mcp.server import is_local_host
from cliyard.server.uploads import DEFAULT_UPLOAD_DIR, validate_dir


def browser_url(host: str, port: int) -> str:
    """Build a browser-addressable URL (0.0.0.0/:: -> 127.0.0.1)."""
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{display_host}:{port}"


def build_app_or_exit(spec_dir: str, **kwargs):
    """Build the app, converting invalid specs into a clean exit(1)."""
    try:
        return create_app(spec_dir, **kwargs)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def check_upload_dir(
    upload_dir: str | None, spec_dir: str | None = None
) -> str:
    """Resolve *upload_dir* (``None`` → uploads ``DEFAULT_UPLOAD_DIR``).

    Delegates to todo 1's :func:`validate_dir` (full dangerous-dir list +
    makedirs); converts :class:`CliyError` to :class:`ClickException` so both
    ``serve`` and ``mcp`` fail fast with a non-zero exit at startup.
    """
    try:
        return validate_dir(upload_dir or DEFAULT_UPLOAD_DIR, spec_dir=spec_dir)
    except CliyError as exc:
        raise click.ClickException(str(exc)) from exc


def _check_serve_auth(host: str, token: str | None) -> None:
    """Mirror MCP ``--token`` semantics for serve: non-localhost requires it."""
    if is_local_host(host) or token:
        return
    raise click.ClickException(
        "Refusing to bind an unauthenticated web server to a "
        f"non-localhost host ({host!r}). Pass --token <TOKEN> to enable "
        "bearer auth for /api/upload."
    )


def run_server(
    spec_dir: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    reload: bool = False,
    token: str | None = None,
    upload_dir: str | None = None,
    upload_base_url: str | None = None,
    file_allow_dirs: tuple[str, ...] | list[str] | None = None,
) -> None:
    """Start the FastAPI web server for *spec_dir*.

    Args:
        spec_dir: Path to the cliyard spec directory.
        host: Bind address passed through to uvicorn.
        port: Bind port passed through to uvicorn.
        open_browser: Open the browser-addressable URL after startup.
        reload: Enable uvicorn auto-reload (import-string factory mode;
            requires the app factory to read ``CLIYARD_SPEC_DIR``).
        token: Bearer token guarding ``POST /api/upload`` (required on
            non-localhost, optional on loopback; consumed by todo 2's
            ``verify_upload_token`` Depends via ``app.state.upload_token``).
        upload_dir: Upload storage directory (validated fail-fast here,
            consumed by todo 1's store).
        upload_base_url: Public base URL for upload instructions (consumed
            by todo 4's description template chain).
        file_allow_dirs: Extra server-readable directories (consumed by
            todo 5's path jail).
    """
    _check_serve_auth(host, token)
    resolved_upload_dir = check_upload_dir(upload_dir, spec_dir)
    upload_kwargs = {
        "token": token,
        "upload_dir": resolved_upload_dir,
        "upload_base_url": upload_base_url,
        "file_allow_dirs": file_allow_dirs,
    }
    url = browser_url(host, port)

    if reload:
        # Validate the spec up front (fail fast, clean error).
        build_app_or_exit(spec_dir, **upload_kwargs)
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

    app = build_app_or_exit(spec_dir, **upload_kwargs)
    if open_browser:
        webbrowser.open(url)
    click.echo(f"Serve spec {spec_dir} at {url}")
    uvicorn.run(app, host=host, port=port)
