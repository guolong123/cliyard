"""``cliyard serve`` — Serve a web UI for YAML specs.

Usage::

    cliyard serve ./examples/demo --host 127.0.0.1 --port 8080
    cliyard serve ./examples/demo --open --reload

Starts a FastAPI server that turns the spec directory into a browsable web
interface (command tree, auto-generated forms, live execution steps, history).

The startup logic lives in :mod:`cliyard.server.launcher` and is shared with
the ``server`` sub-command that ``create_cli`` attaches to generated CLIs.
"""

from __future__ import annotations

import click
import uvicorn  # noqa: F401  (kept as monkeypatch target for existing tests)

from cliyard.server.launcher import run_server


@click.command()
@click.argument(
    "spec_dir",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host address")
@click.option("--port", default=8080, type=int, show_default=True, help="Bind port")
@click.option("--open", is_flag=True, default=False, help="Open browser after startup")
@click.option("--reload", is_flag=True, default=False, help="Enable uvicorn auto-reload")
def serve(spec_dir: str, host: str, port: int, open: bool, reload: bool) -> None:
    """Serve a web UI for the YAML specs in SPEC_DIR.

    Turns the spec directory into a FastAPI-powered web interface with
    auto-generated forms, live execution steps, and execution history.
    """
    run_server(spec_dir, host=host, port=port, open_browser=open, reload=reload)
