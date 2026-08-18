"""cliyard.auth — Manage authentication credentials.

Provides ``cliyard auth login``, ``cliyard auth status``, and
``cliyard auth logout`` subcommands.
"""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table


@click.group(name="auth")
def auth_group() -> None:
    """Manage authentication credentials.

    Login to a service, check saved credential status, or clear
    saved credentials.
    """


@auth_group.command(name="login")
@click.option("--spec-dir", required=True, help="Path to YAML spec directory")
def auth_login(spec_dir: str) -> None:
    """Initialize auth credentials by running the auth chain.

    Reads the auth spec from ``_auth.yaml``, executes all auth steps,
    and persists the resulting tokens according to the ``auth.persist``
    configuration.
    """
    from cliyard.client.auth import run_auth_chain
    from cliyard.client.credentials import save_service_credentials
    from cliyard.engine.loader import load_service

    console = Console()
    spec_path = Path(spec_dir).resolve()

    try:
        service = load_service(spec_path)
    except Exception as exc:
        console.print(f"[red]Error loading spec: {exc}[/red]")
        return

    auth_spec = service.get("auth")
    if not auth_spec:
        console.print("[red]No auth config found in _auth.yaml[/red]")
        return

    # Run the auth chain (no pre-filled — full re-login)
    try:
        from cliyard.client.http import HttpClient

        server = service.get("server", {})
        base_url = server.get("base_url", "http://localhost:8080")
        client = HttpClient(base_url)
        auth_state = run_auth_chain(auth_spec, http_client=client)
    except Exception as exc:
        console.print(f"[red]Auth failed: {exc}[/red]")
        return

    # Save based on persist config
    persist = auth_spec.get("persist", {})
    if persist.get("to") == "cliyard-config" or not persist.get("to"):
        service_id = auth_spec.get("id", service.get("name", "default"))
        fields: dict[str, str | int] = {}

        persist_fields = persist.get("fields", {})
        for field_name, field_config in persist_fields.items():
            ref: str = field_config.get("from", "")
            default = field_config.get("default")

            if "." in ref:
                step, key = ref.split(".", 1)
                step_value = auth_state.get(step)
                if isinstance(step_value, dict):
                    value = step_value.get(key)
                else:
                    value = None
                if value is not None:
                    fields[field_name] = value
                elif default is not None:
                    fields[field_name] = default
            else:
                # Direct reference: field_name = step_name
                value = auth_state.get(ref)
                if value is not None:
                    fields[field_name] = value
                elif default is not None:
                    fields[field_name] = default

        # Auto-compute expires_at from ttl if present
        # Look for a "ttl" key in the extracted auth state
        ttl_value: int | None = None
        for step_name, step_value in auth_state.items():
            if isinstance(step_value, dict) and "ttl" in step_value:
                ttl_value = step_value["ttl"]
                break
        if ttl_value:
            fields["expires_at"] = int(time.time()) + int(ttl_value)

        if fields:
            save_service_credentials(service_id, fields)
            console.print(f"[green]Credentials saved for '{service_id}'[/green]")
        else:
            console.print("[yellow]No fields configured for persistence.[/yellow]")
            console.print(f"[dim]auth_state: {auth_state}[/dim]")
    else:
        console.print(f"[yellow]Persistence target {persist.get('to')!r} not supported yet.[/yellow]")


@auth_group.command(name="status")
def auth_status() -> None:
    """Show saved authentication status."""
    from cliyard.client.credentials import list_services

    console = Console()
    services = list_services()
    if not services:
        console.print("[yellow]No credentials saved.[/yellow]")
        return

    table = Table()
    table.add_column("Service")
    table.add_column("Profile")
    table.add_column("Fields")
    table.add_column("Expires")

    for svc_id, block in services.items():
        profiles = block.get("profiles", {}) or {}
        current = block.get("current")
        if not profiles:
            table.add_row(svc_id, "-", "-", "-")
            continue
        for pname, fields in profiles.items():
            marker = "* " if pname == current else "  "
            field_names = [k for k in fields.keys() if k != "expires_at"]
            expires_at = fields.get("expires_at")
            if expires_at:
                remaining = int(expires_at) - int(time.time())
                if remaining > 0:
                    hours = remaining // 3600
                    minutes = (remaining % 3600) // 60
                    expiry = f"{hours}h {minutes}m remaining"
                else:
                    expiry = "[red]EXPIRED[/red]"
            else:
                expiry = "never"
            table.add_row(svc_id, f"{marker}{pname}", ", ".join(field_names), expiry)

    console.print(table)


@auth_group.command(name="logout")
@click.option("--service", "-s", help="Service ID to logout (default: all)")
def auth_logout(service: str | None) -> None:
    """Clear saved credentials."""
    console = Console()
    if service:
        from cliyard.client.credentials import clear_service_credentials

        clear_service_credentials(service)
        console.print(f"[green]Credentials cleared for '{service}'[/green]")
    else:
        from cliyard.client.credentials import clear_all_credentials

        clear_all_credentials()
        console.print("[green]All credentials cleared.[/green]")
