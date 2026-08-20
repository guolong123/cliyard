"""cliyard.runtime.runner — Entry-point pipeline for spec-driven CLIs.

Provides :func:`run_with_spec`, the single function that a generated CLI
calls to load a YAML service spec, build Click commands, and execute the CLI.

Usage (from a generated CLI's ``__main__.py``)::

    import sys
    from cliyard.runtime import run_with_spec

    sys.exit(run_with_spec("path/to/spec-dir"))
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from collections import defaultdict
from typing import Any, NoReturn

import click


def _resolve_base_url_override(service_name: str, base_url_override: str | None) -> str | None:
    """Resolve a runtime base_url override: explicit arg > env > None.

    Env vars checked: ``<SERVICE_NAME>_SERVER`` (e.g. ``MYCLI_SERVER`` for a
    service named ``my-cli``) and the generic ``CLIYARD_SERVER``.
    """
    if base_url_override:
        return base_url_override
    env_key = re.sub(r"[^A-Za-z0-9]+", "_", service_name.upper()).strip("_")
    for var in (f"{env_key}_SERVER", "CLIYARD_SERVER"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def extract_server_override(argv: list[str]) -> tuple[list[str], str | None]:
    """Extract a ``--server``/``-s`` override from *argv*.

    Both ``--server URL`` and ``--server=URL`` (and ``-s URL``) forms are
    supported.  The matched arguments are removed from the returned argv so
    Click never sees them.

    Returns:
        ``(cleaned_argv, server_url)`` — ``server_url`` is ``None`` when no
        override was present.
    """
    out: list[str] = []
    server: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--server", "-s") and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            server = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--server="):
            server = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("-s=") and len(arg) > 3:
            server = arg[3:]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, server


def create_cli(
    spec_dir: str,
    version: str | None = None,
    base_url_override: str | None = None,
) -> click.Group:
    """Load a cliyard service spec and build a Click CLI group.

    Returns a ``click.Group`` with all resource commands registered.
    Call ``cli()`` to execute, or attach to another Click app::

        from cliyard.runtime import create_cli
        app = click.Group()
        app.add_command(create_cli("path/to/spec"))

    Args:
        spec_dir: Path to the service spec directory.
        base_url_override: Optional base_url that takes precedence over the
            saved profile and the spec's default server (also honored by the
            ``<SERVICE>_SERVER`` / ``CLIYARD_SERVER`` env vars).

    Returns:
        ``click.Group`` with resource commands ready to run.
    """
    from cliyard.engine.loader import load_service
    from cliyard.engine.builder import build_resource_group, LabeledGroup, ServiceContext

    spec_path = Path(spec_dir).resolve()
    if not spec_path.is_dir():
        raise FileNotFoundError(f"Spec directory not found: {spec_path}")

    service = load_service(spec_path)

    service_name: str = service.get("name", "cliyard")
    description: str = service.get("description", service_name)
    servers: dict[str, Any] = service.get("servers", {})
    default_server_name: str = service.get("_default_server", "")
    auth_spec: dict[str, Any] | None = service.get("auth")
    service_id: str = auth_spec.get("id", service_name) if auth_spec else service_name

    # Get the default server config (base_url may come from saved credentials)
    default_server: dict[str, Any] = {}
    if servers:
        default_server = servers.get(default_server_name) or next(iter(servers.values()), {})

    # Resolve base_url: runtime override > saved profile > default (no hard YAML base_url required)
    runtime_override = _resolve_base_url_override(service_name, base_url_override)
    from cliyard.client.credentials import get_current_profile
    saved_profile = get_current_profile(service=service_id)
    saved_endpoints: dict[str, str] = saved_profile.get("endpoints", {}) if saved_profile else {}
    saved_endpoint = saved_profile.get("endpoint") if saved_profile else None
    base_url = runtime_override or saved_endpoint or default_server.get("base_url", "http://localhost:8080")
    prefix = default_server.get("prefix", "")

    # Service-level default output format (--format)
    default_format: str = (service.get("output") or {}).get("default") or "json"

    # Auto-read saved credentials if persist is configured
    pre_filled: dict[str, Any] | None = None
    if auth_spec and auth_spec.get("persist"):
        from cliyard.client.credentials import get_service_credentials

        saved = get_service_credentials(service_id)
        if saved:
            persist = auth_spec.get("persist", {})
            persist_fields = persist.get("fields", {})
            pre_filled = {}
            for storage_key, field_config in persist_fields.items():
                ref: str = field_config.get("from", "")
                if "." in ref:
                    step_name, field_name = ref.split(".", 1)
                    value = saved.get(storage_key)
                    if value is not None:
                        if step_name not in pre_filled:
                            pre_filled[step_name] = {}
                        pre_filled[step_name][field_name] = value
                else:
                    value = saved.get(storage_key)
                    if value is not None:
                        pre_filled[ref] = value
            if not persist_fields:
                pre_filled = saved

    base_ctx = ServiceContext(
        base_url=base_url,
        prefix=prefix,
        auth_spec=auth_spec,
        pre_filled_auth=pre_filled,
        servers=servers,
        timeout=default_server.get("timeout", 30),
        default_format=default_format,
    )

    cli = LabeledGroup(name=service_name, help=description)

    # Runtime --server/-s override: parsed natively by Click so it shows in
    # --help and works without any entry-point wiring in downstream CLIs.
    cli.params.append(
        click.Option(
            ["--server", "-s"],
            default=None,
            metavar="URL",
            help="Override server base URL (default: $CLIYARD_SERVER or spec base_url)",
        )
    )

    @click.pass_context
    def _root_callback(ctx: click.Context, server: str | None) -> None:
        if server:
            ctx.ensure_object(dict)["server"] = server

    cli.callback = _root_callback

    # Add --version if version is provided
    if version:
        from click.decorators import version_option
        version_option(version=version, prog_name=service_name)(cli)

    from cliyard.runtime.auth_commands import add_auth_commands
    add_auth_commands(cli, service, service_id=service_id)

    # Group resources by their "group" field for nesting
    # Supports dot-separated paths: "asset.logcluster" → asset → logcluster
    groups_data: dict[str, dict[str, Any]] = {}
    ungrouped: list[click.Group] = []

    # Load group definitions from _groups.yaml (optional)
    _groups_def: dict[str, Any] = {}
    _groups_file = spec_path / "_groups.yaml"
    if _groups_file.is_file():
        import yaml as _yaml
        try:
            _groups_def = _yaml.safe_load(_groups_file.read_text()) or {}
        except Exception:
            pass

    def _ensure_group(path: str) -> str:
        """Ensure a group path exists and return the leaf group name."""
        parts = path.split(".")
        for i, part in enumerate(parts):
            prefix = ".".join(parts[:i+1])
            if prefix not in groups_data:
                _gdef = _groups_def.get(part, {})
                _gdesc = _gdef.get("description") or f"{part} 管理"
                groups_data[prefix] = {"description": _gdesc, "children": [], "parent": ".".join(parts[:i]) if i > 0 else ""}
        return path

    for resource in service.get("resources", []):
        group_name = resource.get("group", "")
        # Resolve per-resource server: runtime override > saved endpoints > saved default > YAML config
        resource_server_name = resource.get("server", "")
        if resource_server_name and servers and resource_server_name in servers:
            srv = servers[resource_server_name]
            res_base = runtime_override or saved_endpoints.get(resource_server_name) or saved_endpoint or srv.get("base_url", base_url)
            res_prefix = srv.get("prefix", prefix)
            res_timeout = srv.get("timeout", 30)
        elif resource_server_name and saved_endpoints.get(resource_server_name):
            res_base = runtime_override or saved_endpoints[resource_server_name]
            res_prefix = prefix
            res_timeout = 30
        else:
            res_base = base_url
            res_prefix = prefix
            res_timeout = 30

        res_ctx = ServiceContext(
            base_url=res_base,
            prefix=res_prefix,
            auth_spec=auth_spec,
            pre_filled_auth=pre_filled,
            servers=servers,
            timeout=res_timeout,
            default_format=default_format,
        )
        grp = build_resource_group(resource["name"], resource, res_ctx)
        if group_name:
            leaf = _ensure_group(group_name)
            groups_data[leaf]["children"].append(grp)
        else:
            ungrouped.append(grp)

    # Build parent groups with descriptions that include child names
    built: dict[str, click.Group] = {}
    # Sort by depth (shallowest first) so parents are built before children
    for gpath in sorted(groups_data.keys(), key=lambda p: p.count(".")):
        gdata = groups_data[gpath]
        children_names = ", ".join(sorted(c.name for c in gdata["children"]))
        gdesc = f"{gdata['description']}（{children_names}）"
        parent_grp = LabeledGroup(name=gpath.split(".")[-1], short_help=gdesc)
        for child in gdata["children"]:
            parent_grp.add_command(child)
        built[gpath] = parent_grp

    # Wire up parent-child relationships
    for gpath, parent_grp in built.items():
        parent_path = groups_data[gpath].get("parent", "")
        if parent_path and parent_path in built:
            built[parent_path].add_command(parent_grp)
        else:
            cli.add_command(parent_grp)

    # Add ungrouped resources directly to top-level
    for grp in ungrouped:
        cli.add_command(grp)

    # Add top-level command plugins (e.g. search)
    from cliyard.plugin import PluginRegistry
    from cliyard.plugin.discovery import discover_plugins

    discover_plugins()
    for _cmd_name, _cmd_fn in PluginRegistry.get_all_commands().items():
        _cmd_fn(cli, base_ctx)

    from cliyard.engine.loader import load_flows

    flows = load_flows(spec_path)
    if flows:
        from cliyard.engine.builder import build_flow_command

        flow_group = LabeledGroup(name="flow", help="List and run orchestrated workflow pipelines")

        @flow_group.command(name="list")
        def _list_flows():
            """List available flow orchestrations."""
            from rich.console import Console
            from rich.table import Table

            console = Console()

            # 按 category 分组
            grouped = defaultdict(list)
            for f in flows:
                grouped[f.category or "其他"].append(f)

            for cat, cat_flows in grouped.items():
                # 取组内第一个 flow 的 category_label 作为组名
                label = cat_flows[0].category_label or cat
                table = Table(title=f"[{label}]", box=None, show_header=False, padding=(0, 2))
                for f in cat_flows:
                    desc = f.description or ""
                    table.add_row(f.command, desc)
                console.print(table)
                console.print()

        run_group = LabeledGroup(name="run", help="Run a flow orchestration")
        for flow_spec in flows:
            flow_cmd = build_flow_command(flow_spec, base_ctx, service)
            run_group.add_command(flow_cmd)

        flow_group.add_command(run_group)
        cli.add_command(flow_group)

    # ``server`` sub-command: starts the web UI for this CLI's spec dir
    # (captured via closure — no spec-dir argument needed).
    from cliyard.runtime.server_command import build_server_command

    cli.add_command(build_server_command(str(spec_path)))

    # ``mcp`` sub-command: starts this CLI as an MCP server (stdio default).
    from cliyard.runtime.mcp_command import build_mcp_command

    cli.add_command(build_mcp_command(str(spec_path)))

    return cli


def run_with_spec(spec_dir: str) -> NoReturn:
    """Load a cliyard service spec and run the generated CLI.

    This is the primary entry point for generated CLIs.  It reads the
    YAML service spec from *spec_dir*, dynamically builds a Click CLI
    tree, and executes it.

    Args:
        spec_dir: Path to the service spec directory (must contain
            ``_auth.yaml`` and ``*.yaml`` resource files).

    Returns:
        This function never returns; it calls ``sys.exit()`` with the
        appropriate exit code from Click.

    Example:
        >>> import sys
        >>> from cliyard.runtime import run_with_spec
        >>> sys.exit(run_with_spec("tests/fixtures/spec-dir"))
    """
    try:
        cli = create_cli(spec_dir)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    try:
        code: int = cli(standalone_mode=False)
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 0
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        code = 1

    sys.exit(code)
