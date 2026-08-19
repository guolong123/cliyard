"""cliyard.engine.orchestrator — Sequential flow execution engine.

Executes flow definitions (:class:`~cliyard.engine.flow.FlowSpec`) by iterating
through steps sequentially, resolving Jinja2 templates at each step, delegating
to resource methods via the standard pipeline, and accumulating results.

Pipeline per step (matching :func:`~cliyard.engine.builder._make_callback`):

    1. **Resolve templates** — render ``{{ flow.xxx }}`` / ``{{ step.xxx }}``
    2. **Bind & validate** — via :func:`~cliyard.engine.binder.bind_and_validate`
    3. **Merge params** — group by HTTP location for the assembler
    4. **Assemble request** — via :func:`~cliyard.engine.assembler.assemble_request`
    5. **Execute HTTP** — via the shared :class:`~cliyard.client.http.HttpClient`
    6. **Parse response** — JSONPath extraction (if configured in the step)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

from jinja2 import ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment

from cliyard.engine.errors import CliyError
from cliyard.engine.template import Template
from cliyard.plugin import PluginRegistry
from cliyard.server.redact import redact_sensitive

# ---------------------------------------------------------------------------
# Flow context
# ---------------------------------------------------------------------------


@dataclass
class FlowContext:
    """Runtime context carried through all steps of a flow execution.

    Attributes:
        flow_params: Raw CLI argument values from Click (``**kwargs``).
        step_state: Accumulated step results keyed by ``step.id``.
        http_client: Shared, authenticated HTTP client for all step requests.
        console: ``rich.console.Console`` for user-facing output.
        service_spec: Full loaded service spec (for resource/method lookup).
        base_url: Base URL for the default server.
        prefix: URL prefix for the default server.
    """

    flow_params: dict = field(default_factory=dict)
    step_state: dict = field(default_factory=dict)
    http_client: Any = None
    console: Any = None
    service_spec: dict = field(default_factory=dict)
    base_url: str = ""
    prefix: str = ""
    server_override: str = ""
    saved_endpoints: dict = field(default_factory=dict)
    pre_filled_auth: dict | None = None
    step_cb: Callable[[str, dict], None] | None = None
    _flow_aborted: bool = False
    _flow_skipped: bool = False
    _current_flow: Any = None
    verbose: bool = False


# ---------------------------------------------------------------------------
# Template resolver
# ---------------------------------------------------------------------------


def resolve_template(obj: Any, context: dict) -> Any:
    """Recursively resolve Jinja2 templates in a nested object.

    Strings containing ``{{`` are rendered via the sandboxed
    :class:`~cliyard.engine.template.Template` engine.  Dicts and lists
    are recursed. Non-string values pass through unchanged.

    Graceful degradation: if rendering fails (e.g. missing variable),
    the original string is returned unchanged.

    Args:
        obj: The object to resolve (str, dict, list, or scalar).
        context: Template variables (``{"flow": ..., "step": ...}``).

    Returns:
        The resolved object with all templates rendered.
    """
    if isinstance(obj, str):
        if "{{" not in obj:
            return obj
        try:
            return Template(obj).render(**context)
        except Exception:
            return obj
    elif isinstance(obj, dict):
        return {k: resolve_template(v, context) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_template(item, context) for item in obj]
    return obj


def _evaluate_expression(expr: str, context: dict) -> Any:
    """Evaluate a Jinja2 expression and return the actual Python value.

    Unlike :func:`resolve_template` which renders templates to strings,
    this function evaluates an expression and returns native Python types
    (lists, dicts, scalars, etc.).

    The expression may be wrapped in ``{{ }}`` markers or bare::

        _evaluate_expression("step.users", ctx)       → [{"name": "alice"}, ...]
        _evaluate_expression("{{ step.users }}", ctx)  → same result

    Args:
        expr: Jinja2 expression string.
        context: Template variables dict.

    Returns:
        The evaluated Python value.
    """
    expr = expr.strip()
    if expr.startswith("{{") and expr.endswith("}}"):
        expr = expr[2:-2].strip()

    env = SandboxedEnvironment(undefined=ChainableUndefined)
    compiled = env.compile_expression(expr)
    return compiled(**context)


# ---------------------------------------------------------------------------
# Resource / method lookup
# ---------------------------------------------------------------------------


def _lookup_resource_method(
    use: str,
    service_spec: dict,
) -> tuple[dict, dict]:
    """Parse ``resource.method`` (or ``group.resource.method``) and return
    (resource_spec, method_spec).

    Two target formats are supported:

    * ``resource.method`` — unambiguous form; used when resource names are
      globally unique (the common case).
    * ``group.resource.method`` — disambiguation form; required when the same
      resource name appears under multiple groups (e.g. ``admin.templates.list``
      vs ``alert.templates.list``). The group prefix selects the exact resource
      the same way ``build_command_tree`` renders the nested command tree.

    Args:
        use: Dot-separated ``"resource_name.method_name"`` or
            ``"group_name.resource_name.method_name"``.
        service_spec: Full loaded service with a ``resources`` key.

    Returns:
        Tuple of ``(resource_spec, method_spec)``.

    Raises:
        ValueError: If the resource/method is not found, the target is
            malformed, or an unambiguous ``resource.method`` targets a
            resource name shared by multiple groups.
    """
    resources = service_spec.get("resources", [])
    parts = use.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid 'use' format {use!r}: expected 'resource.method'"
        )
    head, method_name = parts
    head_parts = head.rsplit(".", 1)

    def _method_of(resource: dict) -> tuple[dict, dict]:
        methods = resource.get("methods", {})
        if method_name not in methods:
            raise ValueError(
                f"Method {method_name!r} not found in "
                f"resource {resource.get('name')!r}"
            )
        return resource, methods[method_name]

    if len(head_parts) == 2:
        # group.resource.method — 精确匹配 group + resource
        group_name, resource_name = head_parts
        for resource in resources:
            if (
                resource.get("name") == resource_name
                and (resource.get("group") or resource_name) == group_name
            ):
                return _method_of(resource)
        raise ValueError(
            f"Resource {resource_name!r} in group {group_name!r} "
            f"not found in service spec"
        )

    # resource.method — 需资源名全局唯一，否则必须用 group 前缀消歧
    resource_name = head
    matches = [
        r for r in resources if r.get("name") == resource_name
    ]
    if not matches:
        raise ValueError(
            f"Resource {resource_name!r} not found in service spec"
        )
    if len(matches) > 1:
        groups = sorted(
            (m.get("group") or resource_name) for m in matches
        )
        raise ValueError(
            f"Resource {resource_name!r} is ambiguous: it exists in groups "
            f"{groups}. Use 'group.{resource_name}.{method_name}' to "
            f"disambiguate."
        )
    return _method_of(matches[0])


# ---------------------------------------------------------------------------
# Template context builder
# ---------------------------------------------------------------------------


def _build_template_context(context: FlowContext) -> dict:
    """Build the Jinja2 template variable dict from flow context.

    Exposes:
    - ``flow`` — full flow_params dict
    - ``step`` — full step_state dict (step_id → result)
    - Individual step results as top-level keys (when scalar)

    Args:
        context: Current flow execution context.

    Returns:
        Dict of template variables.
    """
    ctx: dict = {
        "flow": context.flow_params,
        "step": context.step_state,
    }
    # Expose individual step results at top level for convenience
    for step_id, result in context.step_state.items():
        if isinstance(result, (str, int, float, bool)):
            ctx[step_id] = result
    return ctx


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def execute_use_step(
    step,
    resolved_params: dict,
    context: FlowContext,
) -> dict:
    """Execute a ``use: resource.method`` step.

    Delegates to :func:`cliyard.engine.builder.execute_pipeline`, which is the
    same shared pipeline used by direct CLI invocations.  This ensures every
    command runs through identical logic regardless of how it was triggered.

    Returns:
        Parsed response data (typically a ``dict``).
    """
    from cliyard.engine.builder import ServiceContext, execute_pipeline

    resource_spec, method_spec = _lookup_resource_method(
        step.use, context.service_spec
    )

    base_url = context.base_url
    prefix = context.prefix
    # Server resolution precedence (mirrors runner.py resource wiring):
    # runtime --server > saved credentials endpoints.<server> > spec server > default
    resource_server_name = resource_spec.get("server", "")
    if resource_server_name:
        servers = context.service_spec.get("servers", {})
        srv = servers.get(resource_server_name, {})
        saved_ep = (context.saved_endpoints or {}).get(resource_server_name)
        if saved_ep:
            base_url = saved_ep
        elif srv:
            base_url = srv.get("base_url", base_url)
            prefix = srv.get("prefix", prefix)
    # Runtime --server override takes precedence over everything
    if context.server_override:
        base_url = context.server_override

    step_ctx = ServiceContext(
        base_url=base_url,
        prefix=prefix,
        auth_spec=context.service_spec.get("auth"),
        pre_filled_auth=context.pre_filled_auth,
        servers=context.service_spec.get("servers"),
        timeout=30,
    )

    data = execute_pipeline(
        kwargs=resolved_params,
        method_spec=method_spec,
        resource_spec=resource_spec,
        service_ctx=step_ctx,
        resource_name=resource_spec.get("name", ""),
        http_client=context.http_client,
        raw_response=True,
        event_cb=context.step_cb,
    )

    # Emit a format event for the web UI when the method has output.items_path
    # (same logic as execute_pipeline's non-raw path, but run here because
    #  raw_response=True short-circuits before the format event emission).
    _emit_format_event(context.step_cb, method_spec, data)

    # Extract specific fields if configured in step.extract
    if step.extract and isinstance(data, dict):
        import jsonpath_ng as _jp

        extracted: dict[str, Any] = {}
        for field_name, json_path in step.extract.items():
            try:
                expr = _jp.parse(json_path)
                matches = expr.find(data)
                extracted[field_name] = (
                    matches[0].value if matches else None
                )
            except Exception:
                extracted[field_name] = None
        return extracted

    return data


def _emit_format_event(
    step_cb: Callable[[str, dict], None] | None,
    method_spec: dict[str, Any],
    data: Any,
) -> None:
    """Emit a ``format`` event (with optional ``table``) for the web UI.

    Mirrors the format-event logic in ``execute_pipeline`` (builder.py
    lines 506-512) so that flow ``use:`` steps also deliver structured
    table data to the frontend.  No-op when *step_cb* is ``None``,
    *method_spec* has no ``output.items_path``, or the data doesn't
    contain table-shaped items + fields.
    """
    if step_cb is None:
        return
    output_spec = method_spec.get("output", {})
    items_path = output_spec.get("items_path")
    if not items_path or not isinstance(data, (dict, list)):
        return

    from cliyard.output.handler import parse_response
    from cliyard.engine.hooks import run_post_response_hooks
    from cliyard.engine.builder import _build_table_payload, _json_preview

    hooks_config = method_spec.get("hooks", {})
    resp_data = data
    _raw_hooks = hooks_config.get("before-extract", [])
    if _raw_hooks:
        resp_data = run_post_response_hooks(_raw_hooks, resp_data)

    try:
        parsed = parse_response(resp_data, output_spec)
    except Exception:
        return

    _fmt_hooks = hooks_config.get("before-format", [])
    if _fmt_hooks:
        parsed = run_post_response_hooks(_fmt_hooks, parsed)

    format_payload: dict[str, Any] = {
        "output_preview": _json_preview(redact_sensitive(parsed)),
    }
    table_payload = _build_table_payload(parsed)
    if table_payload is not None:
        format_payload["table"] = table_payload
    _emit_step(step_cb, "format", format_payload)


# ---------------------------------------------------------------------------
# Conditional branching — on_result (if/else)
# ---------------------------------------------------------------------------


def evaluate_condition(condition_str: str, context: dict) -> bool:
    """Evaluate a Jinja2 condition string to a boolean.

    Handles conditions like ``{{ step.X.count > 0 }}``, ``{{ step.X }}``,
    ``{{ step.X is not none }}``, and ``{{ step.X | length }}``.

    The condition string may or may not include ``{{ }}`` delimiters. If
    the rendered result is "True"/"true"/"1" the function returns ``True``.
    "False"/"false"/"0"/""/``None`` all return ``False``. On any Jinja2
    error (missing variable, syntax) it returns ``False`` (safe default).

    Args:
        condition_str: Jinja2 expression (optionally wrapped in ``{{ }}``).
        context: Template variable dict (``{"flow": ..., "step": ...}``).

    Returns:
        ``True`` if the condition evaluates truthy, ``False`` otherwise.
    """
    import re

    cond = condition_str.strip()

    # Strip surrounding {{ }} if present
    # Handle: {{ expr }}, {{- expr -}}, etc.
    cond = re.sub(r"^\{\{[\s\-]*", "", cond)
    cond = re.sub(r"[\s\-]*\}\}$", "", cond)
    cond = cond.strip()

    if not cond:
        return False

    # Re-wrap as a full Jinja2 expression and render
    try:
        rendered = Template("{{ " + cond + " }}").render(**context)
    except Exception:
        return False

    rendered = rendered.strip()

    # Boolean conversion
    if rendered in ("True", "true", "1"):
        return True
    if rendered in ("False", "false", "0", "", "None", "none", "null"):
        return False

    # Truthy: non-empty string (e.g. a non-empty list/object repr)
    return bool(rendered)


def execute_echo_action(message: str, context: FlowContext, color: str = "green") -> None:
    """Print a formatted message via the flow console.

    Supports ``{{ flow.xxx }}`` and ``{{ step.xxx }}`` template resolution
    in the message string.

    Args:
        message: Message to print (with optional Jinja2 templates).
        context: Current flow execution context.
        color: Rich markup color name (default: "green").
    """
    template_ctx = _build_template_context(context)
    rendered = resolve_template(message, template_ctx)
    context.console.print(f"[{color}]{rendered}[/{color}]")
    _emit_step(context.step_cb, "step_echo", {"message": rendered, "color": color})


def execute_action(
    action_type: str,
    action_config: dict,
    context: FlowContext,
) -> None:
    """Execute a built-in control action.

    Supported actions:

    * ``return`` — sets ``_flow_aborted`` flag so ``run_flow()`` stops
      cleanly (no error).
    * ``abort`` — raises :class:`~cliyard.engine.errors.CliyError` with
      the configured message.
    * ``warn`` — prints a yellow warning and continues execution.
    * ``skip`` — sets ``_flow_skipped`` flag so ``run_flow()`` skips
      remaining steps.

    Args:
        action_type: One of ``"return"``, ``"abort"``, ``"warn"``, ``"skip"``.
        action_config: Dict with optional ``message`` key.
        context: Current flow execution context.

    Raises:
        CliyError: When action is ``abort``.
        ValueError: When an unknown action type is encountered.
    """
    action_map: dict[str, str] = {
        "return": "return",
        "abort": "abort",
        "warn": "warn",
        "skip": "skip",
    }

    normalized = action_map.get(action_type)
    if normalized is None:
        raise ValueError(f"Unknown action type: {action_type!r}")

    if normalized == "return":
        context._flow_aborted = True

    elif normalized == "abort":
        message = action_config.get("message", "Flow aborted by action")
        # Resolve templates in the abort message
        template_ctx = _build_template_context(context)
        rendered_msg = resolve_template(message, template_ctx)
        raise CliyError(rendered_msg)

    elif normalized == "warn":
        message = action_config.get("message", "")
        if message:
            template_ctx = _build_template_context(context)
            rendered_msg = resolve_template(message, template_ctx)
            context.console.print(f"[yellow]⚠ {rendered_msg}[/yellow]")

    elif normalized == "skip":
        context._flow_skipped = True


def _normalize_on_result_block(
    block: list | dict | Any,
) -> list[dict]:
    """Normalize a ``then`` / ``else`` block to a list of action dicts.

    Handles two YAML structures:

    * **List form** (``then``)::

          - type: echo
            message: "hello"
          - action: return

    * **Dict with steps** (``else``)::

          steps:
            - id: create_user
              use: user.create
              ...

    Args:
        block: Raw YAML block value.

    Returns:
        List of action dicts.
    """
    if isinstance(block, list):
        return block
    if isinstance(block, dict):
        steps = block.get("steps", [])
        if isinstance(steps, list):
            return steps
    return []


def _emit_step(
    step_cb: Callable[[str, dict], None] | None,
    name: str,
    payload: dict[str, Any],
) -> None:
    """Invoke *step_cb* with ``(name, payload)``; log callback errors."""
    if step_cb is None:
        return
    try:
        step_cb(name, payload)
    except Exception:
        logger.warning("step_cb callback failed for event %s", name, exc_info=True)


def _execute_action_item(
    item: dict,
    context: FlowContext,
) -> None:
    """Execute a single action item from a ``then`` / ``else`` block.

    Three item types:

    * ``type: echo`` + ``message`` — prints a message.
    * ``action: return/abort/warn/skip`` + optional ``message`` — control.
    * ``id`` + ``use`` + ``params`` — sub-step (only basic execution).

    Args:
        item: Action item dict.
        context: Current flow execution context.
    """
    # Echo action
    if item.get("type") == "echo":
        message = item.get("message", "")
        color = item.get("color", "green")
        execute_echo_action(message, context, color)
        return

    # Control action
    action_val = item.get("action")
    if action_val:
        execute_action(action_val, item, context)
        return

    # Sub-step (has an "id" + "use" + "params")
    step_id = item.get("id")
    if step_id and item.get("use"):
        from cliyard.engine.flow import FlowStep

        template_ctx = _build_template_context(context)
        resolved = resolve_template(item.get("params", {}), template_ctx)
        sub_step = FlowStep(
            id=step_id,
            description=item.get("description", ""),
            use=item.get("use", ""),
            params=resolved,
            retry=item.get("retry"),
            until=item.get("until"),
            for_each=item.get("for_each"),
            extract=item.get("extract"),
            on_result=item.get("on_result"),
            on_failure=item.get("on_failure"),
            show_response=bool(item.get("show_response", False)),
        )

        try:
            result, _ = _execute_step(sub_step, context)
            context.step_state[step_id] = result
            _emit_step(context.step_cb, "step_done", {
                "index": 0,
                "step_id": step_id,
                "label": item.get("description", step_id),
                "status": "ok",
                "use": sub_step.use or "",
                "elapsed_ms": 0,
                "result_preview": _step_result_preview(result),
                "params_preview": _step_result_preview(resolved) if resolved else "",
            })
            if getattr(context, "verbose", False) or sub_step.show_response:
                _show_sub_step_details(
                    sub_step,
                    resolved,
                    result,
                    context,
                )
        except CliyError as e:
            _msg = str(e).replace("[", "[[]").replace("]", "[]]")
            context.console.print(f"[red]✗ Sub-step {step_id!r} failed:[/red] {_msg}")
        except Exception as e:
            _msg = str(e).replace("[", "[[]").replace("]", "[]]")
            context.console.print(f"[red]✗ Sub-step {step_id!r} failed:[/red] {_msg}")


def handle_on_result(
    on_result_list: list[dict],
    context: FlowContext,
    step_id: str,
) -> None:
    """Evaluate conditional branching after a step completes.

    Iterates over the ``on_result`` list. Each entry is either:

    * An **if/then** (with optional ``else``)::

          if: '{{ step.X.count > 0 }}'
          then: [...]
          else: [...]        # optional

    * An **else-only** fallback (no ``if``)::

          else:
            steps: [...]

    If an ``if`` condition evaluates to ``True``, its ``then`` block is
    executed and iteration stops (no further items in the list are
    processed — the first matching branch wins).  This gives ``if/elif/else``
    semantics.

    Args:
        on_result_list: List of on_result condition dicts.
        context: Current flow execution context.
        step_id: ID of the step that just completed.
    """
    matched = False

    for item in on_result_list:
        # Item with "if" + "then" (condition node)
        if "if" in item:
            condition = item["if"]
            template_ctx = _build_template_context(context)
            if evaluate_condition(condition, template_ctx):
                matched = True
                then_block = _normalize_on_result_block(item.get("then", []))
                for action_item in then_block:
                    _execute_action_item(action_item, context)
                    # Stop processing actions if a control action fired
                    if context._flow_aborted:
                        return
                    if context._flow_skipped:
                        return
                break  # First matching branch wins
            else:
                # Condition false — check for "else" on this same item
                if "else" in item:
                    matched = True
                    else_block = _normalize_on_result_block(item["else"])
                    for action_item in else_block:
                        _execute_action_item(action_item, context)
                        if context._flow_aborted:
                            return
                        if context._flow_skipped:
                            return
                    break

        # Item with "else" but no "if" — catch-all fallback
        elif "else" in item:
            matched = True
            else_block = _normalize_on_result_block(item["else"])
            for action_item in else_block:
                _execute_action_item(action_item, context)
                if context._flow_aborted:
                    return
                if context._flow_skipped:
                    return
            break

        # Item with "then" but no "if" — unconditional action
        elif "then" in item:
            matched = True
            then_block = _normalize_on_result_block(item["then"])
            for action_item in then_block:
                _execute_action_item(action_item, context)
                if context._flow_aborted:
                    return
                if context._flow_skipped:
                    return
            break

    if not matched:
        # No branch matched — do nothing, flow continues
        pass


# ---------------------------------------------------------------------------
# Loop mechanisms
# ---------------------------------------------------------------------------


def _execute_for_each(step, context: FlowContext) -> list:
    """Execute a for_each loop step.

    Iterates over items resolved from the template context, executing
    sub-steps for each iteration.  The loop variable is accessible as
    ``{{ <as_name> }}`` (top-level) and ``{{ step.<as_name> }}`` in
    sub-step templates.
    """
    template_ctx = _build_template_context(context)

    items = _evaluate_expression(step.for_each.items, template_ctx)

    if not isinstance(items, (list, tuple)):
        raise CliyError(
            f"Step {step.id!r}: for_each.items resolved to "
            f"{type(items).__name__}, expected a list"
        )

    row_name = step.for_each.as_name or "row"
    results: list[dict] = []

    for idx, item in enumerate(items):
        context.console.print(
            f"  [cyan]{step.id!r}: iteration {idx + 1}/{len(items)} "
            f"({row_name}={item!r})[/cyan]"
        )

        iter_state = dict(context.step_state)
        iter_state[row_name] = item

        iter_ctx = FlowContext(
            flow_params=context.flow_params,
            step_state=iter_state,
            http_client=context.http_client,
            console=context.console,
            service_spec=context.service_spec,
            base_url=context.base_url,
            prefix=context.prefix,
            server_override=context.server_override,
            saved_endpoints=context.saved_endpoints,
            pre_filled_auth=context.pre_filled_auth,
            _current_flow=context._current_flow,
            verbose=context.verbose,
            step_cb=context.step_cb,
        )

        iter_results: dict[str, Any] = {}
        for sub_step in step.for_each.steps:
            sub_ctx = dict(_build_template_context(iter_ctx))
            sub_ctx[row_name] = item

            sub_resolved = resolve_template(sub_step.params, sub_ctx)

            if sub_step.use:
                sub_result = execute_use_step(sub_step, sub_resolved, iter_ctx)
            elif sub_step.type == "echo":
                message = sub_resolved.get("message", "") if isinstance(
                    sub_resolved, dict
                ) else ""
                color = sub_resolved.get("color", "green") if isinstance(
                    sub_resolved, dict
                ) else "green"
                execute_echo_action(message, iter_ctx, color)
                sub_result = message
            else:
                sub_result = sub_resolved

            iter_results[sub_step.id] = sub_result
            iter_ctx.step_state[sub_step.id] = sub_result

        results.append(iter_results)

    return results


def _execute_with_retry(
    step,
    context: FlowContext,
    resolved_params: dict,
) -> dict:
    """Execute a step with retry logic.

    Re-attempts on failure with configurable delay and optional exponential
    backoff.  If ``on_exhausted`` is configured and all attempts fail, the
    fallback action is executed instead of raising immediately.
    """
    max_attempts = step.retry.max_attempts
    delay = step.retry.delay
    backoff = step.retry.backoff
    last_exception: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if step.use:
                result = execute_use_step(step, resolved_params, context)
            else:
                result = resolved_params
            return result
        except Exception as e:
            last_exception = e
            if attempt < max_attempts:
                context.console.print(
                    f"  [yellow]⚠ {step.id!r}: attempt {attempt}/{max_attempts} "
                    f"failed ({e}). Retrying in {delay}s...[/yellow]"
                )
                time.sleep(delay)
                if backoff:
                    delay *= backoff
            else:
                if step.retry.on_exhausted:
                    exc = step.retry.on_exhausted
                    if isinstance(exc, dict) and "use" in exc:
                        from cliyard.engine.flow import FlowStep

                        fallback_step = FlowStep(
                            id=f"{step.id}_fallback",
                            use=exc["use"],
                            params=exc.get("params", {}),
                        )
                        fb_ctx = _build_template_context(context)
                        fb_resolved = resolve_template(
                            fallback_step.params, fb_ctx
                        )
                        return execute_use_step(
                            fallback_step, fb_resolved, context
                        )

                raise CliyError(
                    f"Step {step.id!r}: all {max_attempts} retries exhausted. "
                    f"Last error: {last_exception}"
                ) from last_exception


def _execute_until(
    step,
    context: FlowContext,
    resolved_params: dict,
) -> dict:
    """Execute a step with until (polling) logic.

    Repeats execution until the condition expression evaluates to a truthy
    value, or until ``max_iterations`` is reached.  On timeout, the
    ``timeout_action`` (abort/continue) determines the behaviour.
    """
    max_iterations = step.until.max_iterations
    interval = step.until.interval
    condition = step.until.condition
    timeout_action = step.until.timeout_action or "abort"
    timeout_message = step.until.timeout_message or ""

    last_result: dict = {}

    for iteration in range(1, max_iterations + 1):
        if step.use:
            result = execute_use_step(step, resolved_params, context)
        else:
            result = resolved_params

        last_result = result

        context.step_state[step.id] = result
        template_ctx = _build_template_context(context)
        condition_met = _evaluate_expression(condition, template_ctx)

        if condition_met:
            context.console.print(
                f"  [green]✓ {step.id!r}: condition met "
                f"after {iteration} iteration(s)[/green]"
            )
            return result

        context.console.print(
            f"  [dim]{step.id!r}: iteration {iteration}/{max_iterations} "
            f"-- condition not yet met, waiting {interval}s...[/dim]"
        )

        if iteration < max_iterations:
            time.sleep(interval)

    if timeout_action == "abort":
        msg = (
            timeout_message
            or f"Step {step.id!r}: timeout after {max_iterations} iterations"
        )
        raise CliyError(msg)

    msg = (
        timeout_message
        or f"Step {step.id!r}: timeout after {max_iterations} iterations "
        f"(action=continue)"
    )
    context.console.print(f"  [yellow]⚠ {msg}[/yellow]")
    return last_result


# ---------------------------------------------------------------------------
# Plugin step execution
# ---------------------------------------------------------------------------


def _make_plugin_console(console, step_cb):
    """Wrap rich Console so .print() also emits step_echo events.

    Creates a NEW Console with the same settings — does NOT mutate
    the original, so it never produces duplicate step_echo events
    from other code paths that already emit them explicitly.
    """
    import types as _types
    from rich.console import Console as _RichConsole

    # Build a fresh Console mirroring the original's key settings
    _plugin_console = _RichConsole(
        soft_wrap=getattr(console, "soft_wrap", True),
        force_terminal=getattr(console, "force_terminal", None),
        color_system=getattr(console, "color_system", None),
    )
    _original_print = _plugin_console.print

    def _wrapped_print(self, *args, **kwargs):
        _original_print(*args, **kwargs)
        if step_cb is not None:
            text = " ".join(str(a) for a in args if not isinstance(a, (list, dict)))
            if text.strip():
                _emit_step(step_cb, "step_echo", {"message": text.strip(), "color": kwargs.get("style", "default")})

    _plugin_console.print = _types.MethodType(_wrapped_print, _plugin_console)
    return _plugin_console


def _execute_plugin_step(step, context: FlowContext) -> dict:
    """Execute a plugin step registered via ``@register_step_type``.

    Used when ``step.type`` starts with ``"plugin:"``.  Extracts the
    plugin name (everything after ``"plugin:"``), looks it up in the
    :class:`~cliyard.plugin.PluginRegistry`, builds a scoped context
    dict, resolves step params, and calls the plugin function.

    Args:
        step: :class:`~cliyard.engine.flow.FlowStep` with ``type: plugin:xxx``.
        context: Current flow execution context.

    Returns:
        Dict returned by the plugin function.

    Raises:
        CliyError: If the plugin name is unknown or execution fails.
    """
    plugin_name = step.type[len("plugin:"):]
    if not plugin_name:
        raise CliyError(
            f"Step {step.id!r}: plugin type {step.type!r} has no plugin name"
        )

    plugin_fn = PluginRegistry.get_step_type(plugin_name)
    if plugin_fn is None:
        raise CliyError(
            f"Step {step.id!r}: unknown plugin {plugin_name!r} — "
            f"no step type registered with that name"
        )

    plugin_ctx: dict[str, Any] = {
        "flow_params": context.flow_params,
        "step_state": context.step_state,
        "http_client": context.http_client,
        "console": _make_plugin_console(context.console, context.step_cb),
        "step_cb": context.step_cb,
    }

    template_ctx = _build_template_context(context)
    resolved_params = resolve_template(step.params, template_ctx)

    try:
        result = plugin_fn(resolved_params, plugin_ctx)
    except Exception as e:
        raise CliyError(
            f"Step {step.id!r}: plugin {plugin_name!r} failed: {e}"
        ) from e

    return result


# ---------------------------------------------------------------------------
# Step dispatch
# ---------------------------------------------------------------------------


def _execute_step(step, context: FlowContext) -> tuple[Any, dict]:
    """Execute a single flow step, dispatching to the right handler.

    Handles for_each, retry, until, use, and plain param steps.

    Returns:
        Tuple of ``(result, resolved_params)`` — the resolved params are
        returned so callers can display them without re-rendering templates.
    """
    template_ctx = _build_template_context(context)
    resolved_params = resolve_template(step.params, template_ctx)

    if step.for_each:
        result = _execute_for_each(step, context)
    elif step.retry:
        result = _execute_with_retry(step, context, resolved_params)
    elif step.until:
        result = _execute_until(step, context, resolved_params)
    elif step.type and step.type.startswith("plugin:"):
        result = _execute_plugin_step(step, context)
    elif step.use:
        result = execute_use_step(step, resolved_params, context)
    else:
        result = resolved_params
    return result, resolved_params


# ---------------------------------------------------------------------------
# Verbose / debug output
# ---------------------------------------------------------------------------

_STEP_NUMERALS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _step_numeral(index: int) -> str:
    """Return the circled numeral for a 1-based step index."""
    if 1 <= index <= len(_STEP_NUMERALS):
        return _STEP_NUMERALS[index - 1]
    return f"[{index}]"


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``1.23s`` or ``1m 30s``."""
    if seconds >= 60:
        minutes = int(seconds // 60)
        secs = seconds - minutes * 60
        return f"{minutes}m {secs:.1f}s"
    return f"{seconds:.2f}s"


def _format_value(value: Any) -> str:
    """Pretty-format a value for verbose output.

    JSON-serializable values are rendered as compact JSON (Chinese preserved);
    everything else falls back to ``repr()``.
    """
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(
                value, ensure_ascii=False, indent=2, default=str
            )
        except Exception:
            pass
    return repr(value)


def _show_step_progress(
    step_index: int,
    label: str,
    elapsed: float,
    context: FlowContext,
) -> None:
    """Print a compact one-line step result (default flow output).

    Args:
        step_index: 1-based step position.
        label: Step description or id.
        elapsed: Step execution time in seconds.
        context: Current flow execution context (for console output).
    """
    context.console.print(
        f"{_step_numeral(step_index)} {label} "
        f"[green]✓[/green] [dim]({_format_elapsed(elapsed)})[/dim]"
    )


def _show_step_panel(
    step,
    step_index: int,
    label: str,
    resolved_params: dict,
    result: Any,
    elapsed: float,
    context: FlowContext,
) -> None:
    """Print a step's request/response details in a rich Panel.

    Triggered when the flow runs with ``--verbose`` or the step sets
    ``show_response: true`` in its YAML definition.  Displays:

    * The delegated target (``use: resource.method`` / ``type: ...``)
    * The resolved request params (what was actually sent)
    * The step result (what came back / was extracted)
    * Execution time

    Args:
        step: The :class:`~cliyard.engine.flow.FlowStep` that executed.
        step_index: 1-based step position.
        label: Step description or id.
        resolved_params: Step params after Jinja2 template resolution.
        result: Step execution result.
        elapsed: Step execution time in seconds.
        context: Current flow execution context (for console output).
    """
    from rich.console import Group
    from rich.json import JSON
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    parts: list = []
    if step.use:
        parts.append(Text(f"  use: {step.use}", style="dim"))
    elif step.type:
        parts.append(Text(f"  type: {step.type}", style="dim"))

    if resolved_params:
        parts.append(Text("  params:"))
        parts.append(Padding(JSON.from_data(resolved_params, indent=2), (0, 0, 0, 2)))

    parts.append(Text("  response:"))
    if isinstance(result, (dict, list)):
        try:
            parts.append(Padding(JSON.from_data(result, indent=2), (0, 0, 0, 2)))
        except Exception:
            parts.append(Text(f"  {result!r}"))
    else:
        parts.append(Text(f"  {result!r}"))

    parts.append(
        Text(f"  ✓ 完成 ({_format_elapsed(elapsed)})", style="green")
    )

    panel = Panel(
        Group(*parts),
        title=f"{_step_numeral(step_index)} {label}",
        title_align="left",
        border_style="blue",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    context.console.print(panel)


def _show_sub_step_details(
    sub_step,
    resolved_params: dict,
    result: Any,
    context: FlowContext,
) -> None:
    """Print indented request/response details for an on_result sub-step.

    Used in verbose mode so sub-steps (e.g. the ``else`` branch of a
    decision step) are as observable as top-level steps.

    Args:
        sub_step: The :class:`~cliyard.engine.flow.FlowStep` that executed.
        resolved_params: Step params after template resolution.
        result: Step execution result.
        context: Current flow execution context (for console output).
    """
    console = context.console
    target = sub_step.use or sub_step.type or ""
    console.print(f"    [dim]└─ {target}[/dim]")
    if resolved_params:
        console.print(f"        [dim]params:[/dim] {_format_value(resolved_params)}")
    console.print(f"        [dim]response:[/dim] {_format_value(result)}")


def _show_failed_step(
    step,
    step_index: int,
    label: str,
    error_msg: str,
    context: FlowContext,
) -> None:
    """Print a failed step's request details in a red Panel (verbose mode).

    Triggered when a step raises while ``--verbose`` or ``show_response:
    true`` is active — the params that were about to be sent are exactly
    what a debugger needs to see when a step fails.

    Args:
        step: The :class:`~cliyard.engine.flow.FlowStep` that failed.
        step_index: 1-based step position.
        label: Step description or id.
        error_msg: Sanitized error message (already escaped for rich).
        context: Current flow execution context (for console output).
    """
    from rich.console import Group
    from rich.json import JSON
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    resolved_params = resolve_template(
        step.params, _build_template_context(context)
    )

    parts: list = []
    if step.use:
        parts.append(Text(f"  use: {step.use}", style="dim"))
    elif step.type:
        parts.append(Text(f"  type: {step.type}", style="dim"))

    if resolved_params:
        parts.append(Text("  params:"))
        parts.append(
            Padding(JSON.from_data(resolved_params, indent=2), (0, 0, 0, 2))
        )

    parts.append(Text(f"  ✗ {error_msg}", style="red"))

    panel = Panel(
        Group(*parts),
        title=f"{_step_numeral(step_index)} {label}",
        title_align="left",
        border_style="red",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    context.console.print(panel)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


def _trigger_flow_hooks(hook_type: str, context: FlowContext) -> None:
    """Execute flow-level lifecycle hooks.

    Looks up hook names from ``context._current_flow.hooks[hook_type]``,
    resolves them via :class:`~cliyard.plugin.PluginRegistry`, and calls
    each hook function with the flow context.

    Hook failures are caught and logged as warnings — they never block the
    flow execution.

    Args:
        hook_type: One of ``"on_start"``, ``"on_end"``, ``"on_failure"``.
        context: Current flow execution context.
    """
    if not context._current_flow or not context._current_flow.hooks:
        return

    hook_names = context._current_flow.hooks.get(hook_type, [])
    if isinstance(hook_names, str):
        hook_names = [hook_names]
    if not isinstance(hook_names, (list, tuple)):
        return

    from cliyard.plugin import PluginRegistry

    for name in hook_names:
        try:
            hook_fn = PluginRegistry.get_hook(name)
            if hook_fn:
                hook_fn(context)
        except Exception as e:
            context.console.print(
                f"  [yellow]⚠ Flow hook {name!r} failed: {e}[/yellow]"
            )


def _trigger_step_hooks(hook_type: str, step, context: FlowContext) -> None:
    """Execute step-level lifecycle hooks.

    Same pattern as :func:`_trigger_flow_hooks` but reads hook names from
    the individual step's ``hooks`` dict.

    Args:
        hook_type: One of ``"on_step_start"``, ``"on_step_end"``.
        step: The :class:`~cliyard.engine.flow.FlowStep` being executed.
        context: Current flow execution context.
    """
    if not step or not getattr(step, "hooks", None):
        return

    hook_names = step.hooks.get(hook_type, [])
    if isinstance(hook_names, str):
        hook_names = [hook_names]
    if not isinstance(hook_names, (list, tuple)):
        return

    from cliyard.plugin import PluginRegistry

    for name in hook_names:
        try:
            hook_fn = PluginRegistry.get_hook(name)
            if hook_fn:
                hook_fn(context)
        except Exception as e:
            context.console.print(
                f"  [yellow]⚠ Step hook {name!r} failed: {e}[/yellow]"
            )


# ---------------------------------------------------------------------------
# Flow runner
# ---------------------------------------------------------------------------


def run_flow(
    flow_spec,
    flow_params: dict,
    service_ctx,
    service_spec: dict,
    server_override: str | None = None,
    verbose: bool = False,
    step_cb: Callable[[str, dict], None] | None = None,
) -> None:
    """Execute a flow definition sequentially.

    Creates a shared :class:`~cliyard.client.http.HttpClient`, runs the
    auth chain if the service has one, then iterates through each step:

    * Resolves step params via ``{{ flow.* }}`` / ``{{ step.* }}``
    * Delegates ``use:`` steps to :func:`execute_use_step`
    * Stores results in ``step_state[step.id]``
    * Prints progress and error messages via ``rich.console.Console``

    Args:
        flow_spec: :class:`~cliyard.engine.flow.FlowSpec` with ``steps``.
        flow_params: CLI argument dict from Click (``**kwargs``).
        service_ctx: :class:`~cliyard.engine.builder.ServiceContext` with
            ``base_url``, ``prefix``, ``auth_spec``, ``pre_filled_auth``.
        service_spec: Full loaded service dict (for resource/method lookup).
        verbose: If ``True``, print each step's resolved params and response
            details (equivalent to ``show_response: true`` on every step).
        step_cb: Optional ``(event_name, payload)`` callback invoked per
            step (``"step_start"`` / ``"step_done"``) and once at flow end
            (``"flow_end"``).  Emitted on every termination path, including
            early aborts and failures.  Exceptions raised by the callback are
            swallowed and never affect flow execution.  Default ``None``.

    Raises:
        CliyError: If any step fails (flow is aborted).
    """
    from rich.console import Console
    from rich.table import Table
    from rich import box

    from cliyard.client.auth import run_auth_chain
    from cliyard.client.http import HttpClient

    console = Console(soft_wrap=True, force_terminal=False, no_color=True)

    # Create shared HTTP client
    _base = server_override or service_ctx.base_url
    client = HttpClient(_base, timeout=service_ctx.timeout)

    # Load saved credentials endpoints (mirrors runner.py resource wiring)
    saved_endpoints: dict = {}
    try:
        from cliyard.client.credentials import get_current_profile

        service_name = service_spec.get("name", "cliyard")
        auth_spec = service_ctx.auth_spec or {}
        service_id = auth_spec.get("id", service_name)
        _profile = get_current_profile(service=service_id)
        if _profile:
            saved_endpoints = _profile.get("endpoints", {}) or {}
    except Exception:
        saved_endpoints = {}

    # Run auth chain if configured
    if service_ctx.auth_spec:
        console.print("[dim]Authenticating...[/dim]")
        run_auth_chain(
            service_ctx.auth_spec,
            http_client=client,
            pre_filled=service_ctx.pre_filled_auth,
        )

    # Build flow context
    context = FlowContext(
        flow_params=flow_params,
        http_client=client,
        console=console,
        service_spec=service_spec,
        base_url=_base,
        prefix=service_ctx.prefix,
        server_override=server_override or "",
        saved_endpoints=saved_endpoints,
        pre_filled_auth=service_ctx.pre_filled_auth,
        step_cb=step_cb,
        _current_flow=flow_spec,
        verbose=verbose,
    )

    # --- on_start hooks ---
    _trigger_flow_hooks("on_start", context)

    # Execute steps sequentially
    if not flow_spec.steps:
        console.print("[yellow]Flow completed (no steps)[/yellow]")
        _emit_step(step_cb, "flow_end", {"outcome": "completed", "step_count": 0})
        return

    step_results: list[dict] = []
    for step_index, step in enumerate(flow_spec.steps, 1):
        label = step.description or step.id
        _start = time.perf_counter()

        # --- on_step_start hooks ---
        _trigger_step_hooks("on_step_start", step, context)

        try:
            result, resolved_params = _execute_step(step, context)

            # Emit step_start AFTER _execute_step so that pipeline events
            # (validate/auth/request/response/format from execute_use_step)
            # appear BEFORE the merged step card in the frontend timeline.
            _emit_step(
                step_cb,
                "step_start",
                {"index": step_index, "id": step.id, "label": label, "use": step.use},
            )

            # Store result in step_state for subsequent steps
            context.step_state[step.id] = result
            step_results.append({"id": step.id, "label": label, "status": "ok"})
            _elapsed = time.perf_counter() - _start

            # Verbose / show_response: print request & response details
            if verbose or getattr(step, "show_response", False):
                _show_step_panel(
                    step,
                    step_index,
                    label,
                    resolved_params,
                    result,
                    _elapsed,
                    context,
                )
            else:
                _show_step_progress(step_index, label, _elapsed, context)

            # --- on_step_end hooks ---
            _trigger_step_hooks("on_step_end", step, context)

            _emit_step(
                step_cb,
                "step_done",
                {
                    "index": step_index,
                    "id": step.id,
                    "label": label,
                    "status": "ok",
                    "use": step.use or "",
                    "elapsed_ms": int(_elapsed * 1000),
                    "result_preview": _step_result_preview(result),
                    "params_preview": _step_result_preview(resolved_params) if resolved_params else "",
                },
            )

            # Conditional branching — evaluate on_result if configured
            if step.on_result:
                handle_on_result(step.on_result, context, step.id)
                # Check if a control action was triggered
                if context._flow_aborted:
                    _show_flow_summary(console, step_results, "returned")
                    _emit_step(step_cb, "flow_end", {"outcome": "returned", "step_count": len(step_results)})
                    return
                if context._flow_skipped:
                    _show_flow_summary(console, step_results, "skipped")
                    _emit_step(step_cb, "flow_end", {"outcome": "skipped", "step_count": len(step_results)})
                    return

        except CliyError as e:
            _msg = str(e).replace("[", "[[]").replace("]", "[]]")
            if verbose or getattr(step, "show_response", False):
                _show_failed_step(step, step_index, label, _msg, context)
            else:
                console.print(
                    f"{_step_numeral(step_index)} {label} "
                    f"[red]✗[/red] {_msg}"
                )
            step_results.append({"id": step.id, "label": label, "status": "fail"})
            _trigger_flow_hooks("on_failure", context)
            _show_flow_summary(console, step_results, "failed")
            _emit_step(
                step_cb,
                "step_start",
                {"index": step_index, "id": step.id, "label": label, "use": step.use},
            )
            _emit_step(
                step_cb,
                "step_done",
                {
                    "index": step_index,
                    "id": step.id,
                    "label": label,
                    "status": "fail",
                    "elapsed_ms": int((time.perf_counter() - _start) * 1000),
                    "result_preview": "",
                },
            )
            _emit_step(step_cb, "flow_end", {"outcome": "failed", "step_count": len(step_results)})
            return
        except Exception as e:
            _msg = str(e).replace("[", "[[]").replace("]", "[]]")
            if verbose or getattr(step, "show_response", False):
                _show_failed_step(step, step_index, label, _msg, context)
            else:
                console.print(
                    f"{_step_numeral(step_index)} {label} "
                    f"[red]✗[/red] {_msg}"
                )
            step_results.append({"id": step.id, "label": label, "status": "fail"})
            _trigger_flow_hooks("on_failure", context)
            _show_flow_summary(console, step_results, "failed")
            _emit_step(
                step_cb,
                "step_start",
                {"index": step_index, "id": step.id, "label": label, "use": step.use},
            )
            _emit_step(
                step_cb,
                "step_done",
                {
                    "index": step_index,
                    "id": step.id,
                    "label": label,
                    "status": "fail",
                    "elapsed_ms": int((time.perf_counter() - _start) * 1000),
                    "result_preview": "",
                },
            )
            _emit_step(step_cb, "flow_end", {"outcome": "failed", "step_count": len(step_results)})
            return

    _show_flow_summary(console, step_results, "completed")
    _trigger_flow_hooks("on_end", context)
    _emit_step(step_cb, "flow_end", {"outcome": "completed", "step_count": len(step_results)})


def _show_flow_summary(
    console: Any,
    step_results: list[dict],
    outcome: str,
) -> None:
    """Display a summary table of step results after flow execution.

    Args:
        console: Rich console instance.
        step_results: List of dicts with ``id``, ``label``, ``status``.
        outcome: One of ``"completed"``, ``"returned"``, ``"skipped"``, ``"failed"``.
    """
    from rich.table import Table
    from rich import box

    if not step_results:
        return

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    table.add_column(style="bold", width=3)
    table.add_column(style="bold")
    table.add_column()

    for sr in step_results:
        if sr["status"] == "ok":
            table.add_row("[green]✓[/green]", sr["label"], "[dim]ok[/dim]")
        elif sr["status"] == "fail":
            table.add_row("[red]✗[/red]", sr["label"], "[red]failed[/red]")
        else:
            table.add_row("[yellow]…[/yellow]", sr["label"], "[dim]skipped[/dim]")

    console.print()
    console.print(table)

    if outcome == "completed":
        console.print("[bold green] ✓ Flow completed[/bold green]")
    elif outcome == "returned":
        console.print("[bold yellow] ⚑ Flow returned (early exit)[/bold yellow]")
    elif outcome == "skipped":
        console.print("[bold yellow] ⚑ Flow skipped[/bold yellow]")
    elif outcome == "failed":
        console.print("[bold red] ✗ Flow failed[/bold red]")


def _step_result_preview(result: Any, limit: int | None = None) -> str:
    """Render a step result as a redacted string for events (no truncation)."""
    text = _format_value(redact_sensitive(result))
    return text if limit is None else text[:limit]



