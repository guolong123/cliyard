"""``server`` sub-command attached by ``create_cli`` to generated CLIs.

Unlike the top-level ``cliyard serve`` command (which requires a spec-dir
argument), this sub-command closes over the spec directory that was passed
to :func:`cliyard.runtime.create_cli`, so downstream CLIs such as ketacli
can start their web UI with a bare ``<cli> server``.
"""

from __future__ import annotations

import click

from cliyard.server.launcher import run_server


def build_server_command(spec_dir: str) -> click.Command:
    """Build the ``server`` sub-command bound to *spec_dir*.

    Args:
        spec_dir: Spec directory captured by ``create_cli``.

    Returns:
        A ``click.Command`` that starts the cliyard web UI for *spec_dir*.
    """

    @click.command(
        name="server",
        help="Start the cliyard web UI for this CLI's spec directory",
    )
    @click.option("--host", default="127.0.0.1", show_default=True, help="Bind host address")
    @click.option("--port", default=8080, type=int, show_default=True, help="Bind port")
    @click.option("--open", is_flag=True, default=False, help="Open browser after startup")
    def server_cmd(host: str, port: int, open: bool) -> None:
        """Start the web UI for the CLI's spec directory."""
        run_server(spec_dir, host=host, port=port, open_browser=open)

    return server_cmd
