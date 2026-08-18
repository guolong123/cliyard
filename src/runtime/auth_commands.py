"""Auth commands for generated CLIs (add/status/switch/logout)."""

import os
import time
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from cliyard.client.auth import run_auth_chain
from cliyard.client.credentials import (
    clear_service_credentials,
    delete_profile,
    get_current_profile,
    list_profiles,
    save_profile,
    switch_profile,
)
from cliyard.client.http import HttpClient

_console = Console()


def add_auth_commands(
    cli: click.Group,
    service: dict,
    base_url: str = "",
    service_id: str | None = None,
) -> None:
    """Register auth command group on a CLI group.

    Credentials are namespaced by *service_id* so each cliyard-based CLI
    only reads/writes its own profiles.
    """
    auth_spec = service.get("auth") or {}
    svc: str = service_id or auth_spec.get("id") or service.get("name", "default")

    @cli.group()
    def auth():
        "Manage authentication credentials."

    # Build dynamic server options from service.servers
    servers: dict[str, Any] = service.get("servers", {})
    _server_options: dict[str, click.Option] = {}
    for sname in servers:
        opt = click.Option(
            [f"--server-{sname}"],
            default=None,
            help=f"Endpoint URL for {sname}",
            metavar="URL",
        )
        _server_options[sname] = opt

    # Dynamically create auth_add command with server options
    _auth_params = [
        click.Option(["-n", "--name"], default=None, help="Environment name (default: prod)"),
        click.Option(["-u", "--username"], help="Login username"),
        click.Option(["-p", "--password"], help="Login password"),
        click.Option(["-t", "--token"], help="API token (skip login, save directly)"),
        click.Option(["-e", "--endpoint"], help="Default endpoint URL (fallback)"),
        click.Option(["--default"], "set_default", is_flag=True, help="Set as default environment"),
        click.Option(["--set"], "set_extra", type=str, multiple=True, help="Set env vars, e.g. --set API_KEY=abc"),
    ] + list(_server_options.values())

    def auth_add_callback(**kwargs: Any) -> None:
        profile_name = kwargs.pop("name") or "prod"
        username = kwargs.pop("username", None)
        password = kwargs.pop("password", None)
        token = kwargs.pop("token", None)
        endpoint = kwargs.pop("endpoint", None)
        set_default = kwargs.pop("set_default", False)
        set_vars = kwargs.pop("set_extra", ())

        # Collect per-server endpoints from dynamic options
        server_endpoints: dict[str, str] = {}
        for sname in servers:
            key = f"server_{sname}"
            val = kwargs.pop(key, None)
            if val:
                server_endpoints[sname] = val

        auth_spec = service.get("auth")
        if not auth_spec:
            _console.print("[red]No auth config found[/red]")
            return

        # Determine which server to authenticate against
        auth_server_name = None
        for step in auth_spec.get("steps", []):
            auth_server_name = step.get("server")
            if auth_server_name:
                break
        auth_url = server_endpoints.get(auth_server_name or "") or endpoint or base_url or "http://localhost:8080"

        client = HttpClient(auth_url)
        if token:
            fields: dict[str, Any] = {"token": token, "endpoint": endpoint or base_url or "http://localhost:8080"}
            if server_endpoints:
                fields["endpoints"] = server_endpoints
            save_profile(profile_name, fields,
                         set_current=set_default or not get_current_profile(service=svc),
                         service=svc)
            _console.print(f"[green]Token saved for '{profile_name}'[/green]")
            return

        # Set env vars
        auth_params = auth_spec.get("params", {})
        if username:
            env_user = auth_params.get("username", "KETA_USER")
            os.environ[env_user] = username
        if password:
            env_pass = auth_params.get("password", "KETA_PASS")
            os.environ[env_pass] = password
        for kv in (set_vars or ()):
            if "=" in kv:
                k, v = kv.split("=", 1)
                os.environ[k.strip()] = v.strip()
        try:
            auth_state = run_auth_chain(auth_spec, http_client=client)
        except Exception as e:
            _console.print(f"[red]Auth failed: {e}[/red]")
            return

        persist = auth_spec.get("persist", {})
        if persist.get("to") == "cliyard-config":
            fields = {"endpoint": endpoint or base_url or "http://localhost:8080"}
            if server_endpoints:
                fields["endpoints"] = server_endpoints
            for fn, fc in persist.get("fields", {}).items():
                ref = fc.get("from", "")
                dft = fc.get("default")
                if "." in ref:
                    step, key = ref.split(".", 1)
                    sv = auth_state.get(step)
                    val = sv.get(key) if isinstance(sv, dict) else None
                else:
                    val = auth_state.get(ref)
                if val is not None:
                    fields[fn] = val
                elif dft is not None:
                    fields[fn] = dft
            if fields:
                save_profile(profile_name, fields,
                             set_current=set_default or not get_current_profile(service=svc),
                             service=svc)
                _console.print(f"[green]Credentials saved for '{profile_name}'[/green]")
            else:
                _console.print("[yellow]No credentials to save.[/yellow]")

    auth_add_cmd = click.Command(
        name="add",
        params=_auth_params,
        callback=auth_add_callback,
        short_help="Add/authenticate an environment",
    )
    auth.add_command(auth_add_cmd)

    @auth.command("status")
    def auth_status():
        profiles = list_profiles(svc)
        current = get_current_profile(service=svc)
        current_name = current.get("_name") if current else None
        if not profiles:
            _console.print("[yellow]No environments configured.[/yellow]")
            return
        table = Table()
        for col in ("Environment", "Endpoint", "Token", "Expires"):
            table.add_column(col)
        for nm, flds in profiles.items():
            m = "* " if nm == current_name else "  "
            ep = flds.get("endpoint", "-")
            tk = (flds.get("token", "")[:20] + "...") if flds.get("token") else "-"
            exp = flds.get("expires_at")
            exs = f"{int(exp) - int(time.time()) // 3600}h" if exp else "never"
            table.add_row(f"{m}{nm}", ep, tk, exs)
        _console.print(table)

    @auth.command("switch")
    @click.argument("env_name", required=False)
    def auth_switch(env_name):
        if not env_name:
            cur = get_current_profile(service=svc)
            if cur:
                _console.print(f"[bold]{cur.get('_name', '?')}[/bold]")
            else:
                _console.print("[yellow]No default set.[/yellow]")
            return
        if switch_profile(env_name, service=svc):
            _console.print(f"[green]Switched to '{env_name}'[/green]")
        else:
            _console.print(f"[red]Not found: {env_name}[/red]")

    @auth.command("rm")
    @click.argument("env_name", required=False)
    @click.option("--all", "clear_all", is_flag=True)
    def auth_rm(env_name, clear_all):
        if env_name:
            delete_profile(env_name, service=svc)
            _console.print(f"[green]Removed: {env_name}[/green]")
        elif clear_all:
            clear_service_credentials(svc)
            _console.print("[green]All cleared.[/green]")
        else:
            cur = get_current_profile(service=svc)
            if cur:
                _console.print(f"Current: {cur.get('_name', '?')}")
