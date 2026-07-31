"""cliyard.engine.builder — Dynamic Click command builder.
 
Generates Click groups and commands from loaded YAML resource specs.
Follows a pipeline pattern where each command callback runs through stages:

    1. bind_and_validate() — validate params against spec
    2. run_auth_chain()  — authenticate via env vars / login
    3. assemble_request() — build HTTP request from validated params
    4. http_request()  — execute HTTP call
    5. parse_response() + format — parse and display output

This avoids ketacli's giant closure pattern by splitting each stage into
a standalone function that can be tested and evolved independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import click

from cliyard.engine.flow import FlowSpec


# ---------------------------------------------------------------------------
# Service context
# ---------------------------------------------------------------------------


@dataclass
class ServiceContext:
    """Runtime context carrying service-level configuration.

    Passed through the builder chain to the callback so that the
    generated Click commands can access base_url, prefix, and auth
    without relying on globals or import-time coupling.
    """

    base_url: str
    prefix: str = ""
    auth_spec: dict | None = None
    pre_filled_auth: dict | None = None
    servers: dict | None = None
    timeout: int = 30  # HTTP request timeout in seconds  # All named servers: {name: {base_url, prefix, ...}}


# ---------------------------------------------------------------------------
# Type mapping: YAML param type → Click-compatible type
# ---------------------------------------------------------------------------


def _map_param_type(type_str: str) -> type:
    """Convert a YAML param type string to a Python type.

    Handles both verbose ('string') and shorthand ('str') forms.
    Falls back to ``str`` for custom registered field types.

    Returns:
        A Python type usable as ``click.Option(type=...)``.
    """
    mapping: dict[str, type] = {
        "int": int,
        "string": str,
        "str": str,
        "float": float,
    }
    try:
        return mapping[type_str]
    except KeyError:
        pass
    from cliyard.plugin import PluginRegistry as _Reg
    from cliyard.plugin.discovery import discover_plugins as _Disc
    _Disc()
    if _Reg.get_field_type(type_str):
        return str
    raise ValueError(f"Unknown param type: {type_str!r}") from None


# ---------------------------------------------------------------------------
# Param → Click argument/option converters
# ---------------------------------------------------------------------------


def _param_to_option(param: dict[str, Any]) -> click.Option:
    """Convert a ParamSpec dict into a ``click.Option``.

    Used for *query*, *body*, and *header* params (not path params).

    Mapping rules
    -------------
    * ``type: int``        → ``type=int``
    * ``type: string``     → ``type=str``
    * ``type: float``      → ``type=float``
    * ``type: bool``       → ``is_flag=True``
    * ``type: enum``       → ``type=click.Choice(choices)``
    * ``required: true``   → ``required=True``
    * ``default: X``       → ``default=X``
    * ``description: ...`` → ``help=...``

    Returns:
        A ``click.Option`` configured for this parameter.
    """
    import re
    name: str = param["name"]
    converted = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', name).replace('_', '-').lower()
    option_flag = f"--{converted}"

    kwargs: dict[str, Any] = {}
    kwargs["help"] = param.get("description", "")

    type_str: str = param.get("type", "string")

    if type_str == "bool":
        kwargs["is_flag"] = True
        kwargs["default"] = param.get("default", False)
    elif type_str == "file":
        kwargs["type"] = click.Path(exists=True, readable=True)
        if "default" in param:
            kwargs["default"] = param["default"]
        elif param.get("required", False):
            kwargs["required"] = True
            kwargs.pop("default", None)
        else:
            kwargs["default"] = None
    elif type_str == "enum":
        kwargs["type"] = click.Choice(param["choices"])
        if "default" in param:
            kwargs["default"] = param["default"]
    else:
        # int, string, float
        kwargs["type"] = _map_param_type(type_str)
        if "default" in param:
            kwargs["default"] = param["default"]
        elif param.get("required", False):
            kwargs["required"] = True
            kwargs.pop("default", None)
        else:
            kwargs["default"] = None

    # Support multiple values (e.g. --names a --names b)
    if param.get("multiple"):
        kwargs["multiple"] = True
        kwargs.pop("required", None)

    # Show default value in --help when present
    if "default" in kwargs and kwargs["default"] is not None:
        if not param.get("type") == "bool" or not kwargs.get("is_flag"):
            kwargs["show_default"] = True

    # Only pass to click if not None
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    return click.Option([option_flag], **kwargs)


def _param_to_argument(param: dict[str, Any]) -> click.Argument:
    """Convert a ParamSpec dict into a ``click.Argument``.

    Used for *path* params and params with ``argument: true``.

    Returns:
        A ``click.Argument`` configured for this parameter.
    """
    raw_name: str = param["name"]
    name = raw_name
    type_str: str = param.get("type", "string")

    kwargs: dict[str, Any] = {}

    if type_str in ("int", "float"):
        kwargs["type"] = _map_param_type(type_str)
    else:
        kwargs["type"] = str

    if param.get("required", False):
        kwargs["required"] = True

    if "default" in param:
        kwargs["default"] = param["default"]

    return click.Argument([name], **kwargs)


# ---------------------------------------------------------------------------
# Pipeline: bind & validate  (Stage 1)
# ---------------------------------------------------------------------------


def bind_and_validate(kwargs: dict[str, Any], method_spec: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: Bind Click keyword-arguments to named params and validate.

    Currently a pass-through — full validation will be added in a future
    task.  The function signature is stable so that later stages can be
    stacked without changing the callback.

    Args:
        kwargs: Raw keyword arguments from Click.
        method_spec: MethodSpec dict describing expected params.

    Returns:
        Dict of ``param_name → validated_value``.
    """
    return kwargs


# ---------------------------------------------------------------------------
# Callback factory
# ---------------------------------------------------------------------------


def execute_pipeline(
    kwargs: dict[str, Any],
    method_spec: dict[str, Any],
    resource_spec: dict[str, Any],
    service_ctx: ServiceContext,
    resource_name: str = "",
    http_client: Any = None,
    raw_response: bool = False,
) -> dict[str, Any] | str:
    """Execute the full request pipeline and return response data.

    Args:
        http_client: Pre-configured :class:`HttpClient` (e.g. with auth headers
            already set).  If omitted, a fresh client is created and the auth
            chain from *service_ctx* is executed.
        raw_response: If ``True``, return the raw JSON response dict instead
            of the parsed ``{items, total, fields}`` format.  Used by the
            flow orchestrator for JSONPath extraction.

    Returns:
        Parsed response data dict (``{items, total, fields}``) by default,
        or the raw JSON response dict when *raw_response* is ``True``.

    Raises:
        CliyError: On pipeline failure.
    """
    from cliyard.client.auth import run_auth_chain
    from cliyard.client.http import HttpClient
    from cliyard.engine.assembler import assemble_request
    from cliyard.engine.binder import bind_and_validate
    from cliyard.engine.errors import CliyError
    from cliyard.engine.hooks import run_post_response_hooks, run_pre_request_hooks
    from cliyard.output.handler import parse_response

    if not method_spec.get("http", {}).get("path"):
        method_spec.setdefault("http", {})["path"] = resource_spec.get("path", resource_name)

    validated = bind_and_validate(kwargs, method_spec)

    # Read file-type params (skip for multipart — files stay as paths)
    _is_multipart = method_spec.get("body_type") == "multipart"
    if not _is_multipart:
        for _location in ("path", "query", "header", "body"):
            for _param in method_spec.get("params", {}).get(_location, []):
                if _param.get("type") == "file" and _param["name"] in kwargs:
                    _file_path = kwargs[_param["name"]]
                    if isinstance(_file_path, (tuple, list)):
                        _file_path = _file_path[0]
                    if _file_path:
                        try:
                            with open(_file_path) as _f:
                                kwargs[_param["name"]] = _f.read()
                        except Exception as _e:
                            raise CliyError(f"Failed to read config file {_file_path}: {_e}")
                    validated = bind_and_validate(kwargs, method_spec)

    from cliyard.plugin import PluginRegistry as _Reg
    from cliyard.plugin.discovery import discover_plugins as _disc

    _disc()
    for _loc in ("body", "query", "header", "path", "argument"):
        for _param in method_spec.get("params", {}).get(_loc, []):
            _resolver_name = _param.get("resolver", "")
            if _resolver_name.startswith("plugin:"):
                _fn_name = _resolver_name[7:]
                _fn = _Reg.get_field_resolver(_fn_name)
                if _fn:
                    _cli = HttpClient(service_ctx.base_url)
                    if service_ctx.auth_spec:
                        run_auth_chain(
                            service_ctx.auth_spec,
                            http_client=_cli,
                            pre_filled=service_ctx.pre_filled_auth,
                        )
                    kwargs[_param["name"]] = _fn(
                        params=kwargs,
                        http_client=_cli,
                        config=_param.get("resolver_config", {}),
                    )
                    validated = bind_and_validate(kwargs, method_spec)

    merged_params: dict[str, Any] = {}
    for loc in ("query", "body", "header"):
        merged_params[loc] = getattr(validated, loc)
    merged_params["path"] = getattr(validated, "path")
    merged_params.update(getattr(validated, "path"))
    merged_params.update(getattr(validated, "body"))
    merged_params.update(getattr(validated, "argument"))

    # Auth chain (skip if a pre-configured client was provided)
    if http_client is None:
        if service_ctx.auth_spec:
            client = HttpClient(service_ctx.base_url, timeout=service_ctx.timeout)
            run_auth_chain(
                service_ctx.auth_spec,
                http_client=client,
                pre_filled=service_ctx.pre_filled_auth,
            )
            if client.default_headers:
                merged_params.setdefault("header", {})
                if isinstance(merged_params.get("header"), dict):
                    merged_params["header"].update(client.default_headers)
            http_client = client
        else:
            http_client = HttpClient(service_ctx.base_url, timeout=service_ctx.timeout)

    req = assemble_request(
        method_spec,
        merged_params,
        base_url=service_ctx.base_url,
        prefix=service_ctx.prefix,
    )

    # Stage 4: run pre-request hooks
    hooks_config = method_spec.get("hooks", {})
    _pre_hooks = hooks_config.get("pre-request", [])
    if _pre_hooks:
        req = run_pre_request_hooks(_pre_hooks, req)

    _timeout = method_spec.get("http", {}).get("timeout", service_ctx.timeout)
    response = http_client.request(
        method=req.method,
        url=req.url,
        data=req.body,
        query_params=req.query_params,
        headers=req.headers,
        files=req.files,
        timeout=_timeout,
    )

    if method_spec.get("response_type") == "file":
        import re as _re

        cd = response.headers.get("Content-Disposition", "")
        fname = "download"
        if "filename=" in cd:
            fname = cd.split("filename=")[1].strip('"\'')
        elif req.url.rstrip("/").split("/"):
            fname = req.url.rstrip("/").split("/")[-1]
        ct = response.headers.get("Content-Type", "")
        if "." not in fname:
            ext = _re.sub(r".*/(\w+).*", r"\1", ct)
            fname = f"{fname}.{ext}" if ext and ext != ct else f"{fname}.bin"
        with open(fname, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return {"_downloaded": fname}

    try:
        resp_data = response.json()
    except ValueError:
        text = response.text
        resp_data = text if text.strip() else {}

    # Flow orchestrator needs raw response for JSONPath extraction
    if raw_response:
        return resp_data

    output_spec: dict[str, Any] = method_spec.get("output", {})
    if isinstance(resp_data, (dict, list)) and output_spec.get("items_path"):
        _raw_hooks = hooks_config.get("before-extract", [])
        if _raw_hooks:
            resp_data = run_post_response_hooks(_raw_hooks, resp_data)

        data = parse_response(resp_data, output_spec)

        _fmt_hooks = hooks_config.get("before-format", [])
        if _fmt_hooks:
            data = run_post_response_hooks(_fmt_hooks, data)

        return data

    return resp_data


def _make_callback(
    method_spec: dict[str, Any],
    service_ctx: ServiceContext,
    resource_name: str,
    resource_spec: dict[str, Any],
) -> Callable[..., None]:
    """Create a Click callback that runs the full pipeline stages.

    Pipeline:
        1. :func:`execute_pipeline` — shared pipeline (bind → auth → assemble → http → parse)
        2. Format output for display (table / json / csv)
    """

    def callback(**kwargs: Any) -> None:
        from cliyard.engine.errors import CliyError
        from cliyard.output.formatter import format_as_json, format_as_table, format_as_csv
        from rich.console import Console

        console = Console()

        try:
            # Extract built-in options (--format) before validation
            output_format: str = kwargs.pop("format", "json")

            # Run shared pipeline
            data = execute_pipeline(kwargs, method_spec, resource_spec, service_ctx, resource_name)

            # Skip output formatting for file downloads
            if isinstance(data, dict) and data.get("_downloaded"):
                console.print(f"[green]Downloaded: {data['_downloaded']}[/green]")
                return

            # Raw text response (XML, HTML, empty 204) — print as-is
            if isinstance(data, str):
                console.print(data)
                return

            # Format output for CLI display
            output_spec: dict[str, Any] = method_spec.get("output", {})
            items = data.get("items")

            if items is not None and len(items) == 0:
                console.print("[yellow]No results found.[/yellow]")
            elif items:
                fields = output_spec.get("fields", [])
                if output_format == "json":
                    console.print(format_as_json(data))
                elif output_format == "csv":
                    console.print(format_as_csv(data, fields))
                else:
                    console.print(format_as_table(data, fields))
            elif output_spec.get("items_path"):
                console.print(format_as_json(data))
            else:
                if output_format == "json":
                    console.print(format_as_json(data))
                elif output_format == "csv" and isinstance(data, list) and data:
                    fields = [{"name": k, "alias": k} for k in data[0]]
                    console.print(format_as_csv({"items": data, "total": len(data), "fields": fields}, fields))
                else:
                    fields = [{"name": k, "alias": k} for k in data[0]] if isinstance(data, list) and data else []
                    console.print(format_as_table({"items": data if isinstance(data, list) else [data], "total": 0, "fields": fields}, fields))

        except CliyError as e:
            console.print(f"[red]Error:[/red] {str(e).replace('[', '[[]').replace(']', '[]]')}")
        except Exception as e:
            _msg = str(e).replace('[', '[[]').replace(']', '[]]')
            console.print(f"[red]错误:[/red] {_msg}")

    http_method = method_spec.get("http", {}).get("method", "?")
    callback.__doc__ = f"Execute {http_method} operation."

    return callback


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_list_command(resource_spec: dict[str, Any], ctx: ServiceContext) -> click.Command:
    """Build a Click ``list`` subcommand from a resource spec.

    Reads the ``list`` method from ``resource_spec['methods']['list']``.
    *Query* params become Click **options**; *path* params become
    Click **arguments**.

    Args:
        resource_spec: Dict matching :class:`cliyard.schema.types.ResourceSpec`.
        ctx: ServiceContext carrying base_url, prefix, and auth config.

    Returns:
        ``click.Command`` for the list operation.
    """
    method_spec = resource_spec["methods"]["list"]
    params_spec: dict[str, Any] = method_spec.get("params", {})

    click_params: list[click.Parameter] = []

    # Body / Query / Header params → options (with argument support)
    for _loc in ("body", "query", "header"):
        for param in params_spec.get(_loc, []):
            if param.get("argument"):
                click_params.append(_param_to_argument(param))
            else:
                click_params.append(_param_to_option(param))

    # Path params → positional arguments
    for param in params_spec.get("path", []):
        click_params.append(_param_to_argument(param))

    # Built-in --format option for list command
    click_params.append(
        click.Option(
            ["--format"],
            type=click.Choice(["table", "json", "csv"]),
            default="json",
            help="Output format",
            show_default=True,
        )
    )

    return click.Command(
        name="list",
        callback=_make_callback(method_spec, ctx, resource_spec["name"], resource_spec),
        params=click_params,
        short_help=method_spec.get("description") or "List resources",
    )


def build_operation_command(
    method_name: str,
    method_spec: dict[str, Any],
    resource_spec: dict[str, Any],
    ctx: ServiceContext,
) -> click.Command:
    """Build a Click command for a non-list operation (get/create/update/delete).

    Args:
        method_name: Method name (e.g. ``'create'``, ``'get'``, ``'delete'``).
        method_spec: Dict matching :class:`~cliyard.schema.types.MethodSpec`.
        resource_spec: Dict matching :class:`~cliyard.schema.types.ResourceSpec`.
        ctx: ServiceContext carrying base_url, prefix, and auth config.

    Returns:
        ``click.Command`` for the operation.
    """
    params_spec: dict[str, Any] = method_spec.get("params", {})

    click_params: list[click.Parameter] = []

    # Collect all params that should be Click arguments
    for _loc in ("body", "query", "header"):
        for param in params_spec.get(_loc, []):
            if param.get("argument"):
                click_params.append(_param_to_argument(param))
            else:
                click_params.append(_param_to_option(param))

    # Path params → positional arguments (always)
    for param in params_spec.get("path", []):
        click_params.append(_param_to_argument(param))

    http_method = method_spec.get("http", {}).get("method", "?")
    method_type = method_spec.get("type", "")

    if method_type.startswith("plugin:"):
        plugin_name = method_type[7:]
        callback = _make_plugin_callback(plugin_name, method_spec, ctx)
    else:
        callback = _make_callback(method_spec, ctx, resource_spec["name"], resource_spec)

    return click.Command(
        name=method_name,
        callback=callback,
        params=click_params,
        short_help=method_spec.get("description") or f"{http_method} operation",
    )


def _make_plugin_callback(
    plugin_name: str,
    method_spec: dict[str, Any],
    ctx: ServiceContext,
) -> Callable[..., None]:
    """Create a callback that runs a registered plugin method."""
    from rich.console import Console
    from cliyard.engine.binder import bind_and_validate
    from cliyard.client.http import HttpClient
    from cliyard.client.auth import run_auth_chain
    from cliyard.engine.errors import CliyError
    from cliyard.plugin import PluginRegistry
    from cliyard.plugin.discovery import discover_plugins
    import json

    console = Console()

    def callback(**kwargs: Any) -> None:
        discover_plugins()
        plugin_fn = PluginRegistry.get_method(plugin_name)
        if not plugin_fn:
            console.print(f"[red]Plugin method '{plugin_name}' not found[/red]")
            return

        try:
            validated = bind_and_validate(kwargs, method_spec)
            merged = {"query": {}, "body": {}, "header": {}, "path": {}}
            for loc in ("argument", "query", "body", "header"):
                merged[loc] = getattr(validated, loc)
            merged["path"] = getattr(validated, "path")
            merged.update(getattr(validated, "path"))
            merged.update(getattr(validated, "body"))
            merged.update(getattr(validated, "argument"))

            client = HttpClient(ctx.base_url, timeout=ctx.timeout)
            if ctx.auth_spec:
                run_auth_chain(ctx.auth_spec, http_client=client,
                               pre_filled=ctx.pre_filled_auth)

            config = method_spec.get("config", {})
            result = plugin_fn(params=merged, http_client=client, config=config)
            if isinstance(result, dict) and result.get("_formatted"):
                return  # Plugin already handled output
            console.print(json.dumps(result, indent=2, ensure_ascii=False))

        except CliyError as e:
            console.print(f"[red]Error:[/red] {str(e).replace('[', '[[]').replace(']', '[]]')}")
        except Exception as e:
            _msg = str(e).replace('[', '[[]').replace(']', '[]]')
            console.print(f"[red]错误:[/red] {_msg}")

    return callback


def build_resource_group(
    name: str,
    resource_spec: dict[str, Any],
    ctx: ServiceContext,
) -> click.Group:
    """Build a ``click.Group`` for a resource, with methods as subcommands.

    The ``list`` method (if present) is always added first so that it
    appears at the top of ``--help`` output.  Other methods are added
    in definition order.

    Args:
        name: Resource name used as the group name (e.g. ``'repos'``).
        resource_spec: Dict matching :class:`~cliyard.schema.types.ResourceSpec`.
        ctx: ServiceContext carrying base_url, prefix, and auth config.

    Returns:
        ``click.Group`` containing subcommands for each method.
    """
    methods: dict[str, Any] = resource_spec.get("methods", {})
    description = resource_spec.get("description", name)

    group = click.Group(
        name=name,
        short_help=description,
    )

    # Add 'list' first for consistent --help ordering
    if "list" in methods:
        group.add_command(build_list_command(resource_spec, ctx))

    # Add remaining methods
    for method_name, method_spec in methods.items():
        if method_name == "list":
            continue
        group.add_command(build_operation_command(method_name, method_spec, resource_spec, ctx))

    return group


# ---------------------------------------------------------------------------
# Flow command builder
# ---------------------------------------------------------------------------


def build_flow_command(
    flow_spec: FlowSpec,
    ctx: ServiceContext,
    service_spec: dict | None = None,
) -> click.Command:
    """Build a Click command from a FlowSpec for command orchestration.

    Flow params are converted to Click options using the same
    ``_param_to_option()`` converter used for resource methods.

    Args:
        flow_spec: FlowSpec dataclass from :mod:`cliyard.engine.flow`.
        ctx: ServiceContext carrying base_url, prefix, and auth config.
        service_spec: Full loaded service spec (for resource/method
            lookup in ``use:`` steps). Defaults to an empty dict.

    Returns:
        ``click.Command`` for the flow orchestration.
    """
    params_spec: dict[str, Any] = flow_spec.params

    click_params: list[click.Parameter] = []

    for _loc in ("query", "body", "header"):
        for param in params_spec.get(_loc, []):
            click_params.append(_param_to_option(param))

    click_params.append(
        click.Option(
            ["--format"],
            type=click.Choice(["table", "json", "csv"]),
            default="json",
            help="Output format",
            show_default=True,
        )
    )

    def callback(**kwargs: Any) -> None:
        from cliyard.engine.errors import CliyError
        from rich.console import Console

        console = Console()

        try:
            from cliyard.engine.orchestrator import run_flow

            run_flow(flow_spec, kwargs, ctx, service_spec or {})
        except CliyError as e:
            console.print(f"[red]Error:[/red] {str(e).replace('[', '[[]').replace(']', '[]]')}")
        except Exception as e:
            _msg = str(e).replace('[', '[[]').replace(']', '[]]')
            console.print(f"[red]错误:[/red] {_msg}")

    return click.Command(
        name=flow_spec.command,
        callback=callback,
        params=click_params,
        short_help=flow_spec.description or "Flow orchestration",
    )
