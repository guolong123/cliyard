"""Case runner — single-case execution with assertion evaluation.

A *case* (see :class:`~cliyard.engine.case.CaseSpec`) binds a command or
flow target with fixed params and assertions.  :func:`run_case` executes the
target through the existing engine (``execute_pipeline`` for commands,
``run_flow`` for flows), evaluates the case's assertions against the result,
and returns a :class:`CaseResult` — no printing, no summary: progress and
summary rendering belong to the CLI layer.

Assertion contexts (decisive contract):

* command case → ``{"result": <raw response dict>}``
* flow case    → ``{"result": <run_flow return dict>, "step": <step_state>,
  "flow": <merged params>}`` — hence structured jsonpath assertions target
  ``$.outcome`` / ``$.step_state.<step_id>.<field>``, expression assertions
  use ``{{ step.<step_id>... }}`` / ``{{ result.outcome == "completed" }}``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from cliyard.engine.assertions import AssertionFailure, evaluate_assertions
from cliyard.engine.case import CaseSpec
from cliyard.engine.errors import CliyError


@dataclass
class CaseResult:
    """Outcome of a single case execution.

    Attributes:
        name: Case name (mirrors ``CaseSpec.name``).
        kind: Case kind — ``"command"`` or ``"flow"``.
        target: Command/flow path the case invoked.
        status: ``"passed"``, ``"failed"`` (assertion/pipeline failure) or
            ``"error"`` (target not found / unexpected exception).
        assertion_failures: Failed assertions collected during evaluation.
        error: Error message when ``status != "passed"`` and no assertion
            failed (pipeline/target errors); ``None`` otherwise.
        duration_ms: Whole-run wall time in milliseconds.
        steps: Collected ``(event_name, payload)`` events as dicts
            (``{"event": ..., **payload}``) for serve passthrough.
    """

    name: str
    kind: str
    target: str
    status: str  # "passed" | "failed" | "error"
    assertion_failures: list[AssertionFailure] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    steps: list[dict] = field(default_factory=list)


def filter_cases(cases: list[CaseSpec], labels_expr: str | None) -> list[CaseSpec]:
    """Filter *cases* by a label expression.

    Grammar: comma-separated groups OR'd together; ``+`` within a group is
    AND — ``"a+b,c"`` matches cases having (a AND b) OR c.  Tokens are
    stripped; empty tokens are ignored; duplicate labels are harmless (set
    semantics).  An expression that parses to zero valid labels raises
    :class:`ValueError`.
    """
    if labels_expr is None:
        return list(cases)

    groups: list[set[str]] = []
    for raw_group in labels_expr.split(","):
        labels = {token.strip() for token in raw_group.split("+") if token.strip()}
        if labels:
            groups.append(labels)

    if not groups:
        raise ValueError(f"invalid labels expression: {labels_expr!r}")

    return [c for c in cases if any(group <= set(c.labels) for group in groups)]


def run_case(
    case_spec: CaseSpec,
    service_ctx,
    service_spec: dict,
    params_override: dict | None = None,
    server_override: str | None = None,
    verbose: bool = False,
    console: Any = None,
    step_cb: Callable[[str, dict], None] | None = None,
    spec_dir: str | None = None,
) -> CaseResult:
    """Execute one case and evaluate its assertions.

    Args:
        case_spec: Case to run.
        service_ctx: :class:`~cliyard.engine.builder.ServiceContext` with
            ``base_url``, ``timeout``, ``auth_spec``, ``pre_filled_auth``.
        service_spec: Full loaded service dict (for resource/method lookup).
        params_override: Merged over ``case_spec.params`` (override wins).
        server_override: Named server or base URL override.
        verbose: Passed through to ``run_flow`` for flow cases.
        console: Unused for output (case layer never prints); kept for
            signature symmetry with the CLI/serve layers.
        step_cb: Optional ``(event_name, payload)`` callback; every event is
            also recorded into ``CaseResult.steps`` before forwarding.
        spec_dir: Spec directory — required for ``kind="flow"`` cases so the
            flow definition can be located via ``load_flows``.

    Returns:
        A :class:`CaseResult`; never raises for expected failure modes
        (missing target, pipeline errors, failed assertions).
    """
    started = time.perf_counter()
    result = CaseResult(
        name=case_spec.name, kind=case_spec.kind, target=case_spec.target, status="error"
    )

    def _cb(event_name: str, payload: dict) -> None:
        # Record first, forward second — serve passthrough keeps full history
        # even if the consumer callback misbehaves.
        result.steps.append({"event": event_name, **payload})
        if step_cb is not None:
            step_cb(event_name, payload)

    try:
        merged: dict[str, Any] = {**case_spec.params, **(params_override or {})}

        if case_spec.kind == "command":
            context, status, error = _run_command_case(
                case_spec, merged, service_ctx, service_spec, server_override, _cb
            )
        elif case_spec.kind == "flow":
            context, status, error = _run_flow_case(
                case_spec, merged, service_ctx, service_spec,
                server_override, verbose, spec_dir, _cb,
            )
        else:
            context, status, error = None, "error", f"unknown case kind {case_spec.kind!r}"

        result.status = status
        result.error = error

        if context is not None:
            failures: list[AssertionFailure] = []
            for index, assertion in enumerate(case_spec.asserts):
                item_failures = evaluate_assertions(assertion, context)
                failure = item_failures[0] if item_failures else None
                _cb(
                    "assert_result",
                    {
                        "index": index,
                        "passed": failure is None,
                        "assertion": assertion,
                        "message": failure.message if failure else "",
                    },
                )
                failures.extend(item_failures)
            result.assertion_failures = failures
            if failures:
                result.status = "failed"
    finally:
        result.duration_ms = int((time.perf_counter() - started) * 1000)

    return result


# ---------------------------------------------------------------------------
# Kind runners — return (assertion_context, status, error)
# ---------------------------------------------------------------------------


def _run_command_case(
    case_spec: CaseSpec,
    merged: dict[str, Any],
    service_ctx,
    service_spec: dict,
    server_override: str | None,
    event_cb: Callable[[str, dict], None],
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Run a ``kind="command"`` case through execute_pipeline (raw response)."""
    from cliyard.client.auth import run_auth_chain
    from cliyard.client.http import HttpClient
    from cliyard.engine.builder import execute_pipeline
    from cliyard.engine.orchestrator import _lookup_resource_method

    try:
        resource_spec, method_spec = _lookup_resource_method(case_spec.target, service_spec)
    except Exception as exc:  # noqa: BLE001 — 目标不存在/歧义/格式非法统一归为 error
        return None, "error", str(exc)

    # Shared client + auth chain（镜像 run_flow 的 client+auth 模式）
    _base = server_override or service_ctx.base_url
    client = HttpClient(_base, timeout=service_ctx.timeout)
    if service_ctx.auth_spec:
        run_auth_chain(
            service_ctx.auth_spec,
            http_client=client,
            pre_filled=service_ctx.pre_filled_auth,
        )

    try:
        resp = execute_pipeline(
            kwargs=merged,
            method_spec=method_spec,
            resource_spec=resource_spec,
            service_ctx=service_ctx,
            resource_name=resource_spec.get("name", ""),
            http_client=client,
            raw_response=True,
            event_cb=event_cb,
        )
    except CliyError as exc:
        return None, "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 — 非预期异常归为 error（文档化边界之一）
        return None, "error", str(exc)

    return {"result": resp}, "passed", None


def _run_flow_case(
    case_spec: CaseSpec,
    merged: dict[str, Any],
    service_ctx,
    service_spec: dict,
    server_override: str | None,
    verbose: bool,
    spec_dir: str | None,
    step_cb: Callable[[str, dict], None],
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Run a ``kind="flow"`` case via run_flow, suppressing its summary print."""
    import io

    from rich.console import Console

    from cliyard.engine.loader import load_flows
    from cliyard.engine.orchestrator import run_flow

    if spec_dir is None:
        return None, "error", f"flow case {case_spec.name!r} requires spec_dir"

    flow_spec = next(
        (f for f in load_flows(spec_dir) if f.command == case_spec.target), None
    )
    if flow_spec is None:
        return None, "error", (
            f"flow {case_spec.target!r} not found in spec dir {spec_dir!r}"
        )

    # Always route flow output into a throwaway StringIO console — the case
    # layer owns progress/summary printing (same pattern as executor.py).
    flow_console = Console(
        file=io.StringIO(), soft_wrap=True, force_terminal=False, no_color=True
    )

    try:
        flow_result = run_flow(
            flow_spec,
            merged,
            service_ctx,
            service_spec,
            server_override=server_override,
            verbose=verbose,
            step_cb=step_cb,
            console=flow_console,
        )
    except CliyError as exc:
        return None, "failed", str(exc)

    context = {
        "result": flow_result,
        "step": flow_result["step_state"],
        "flow": merged,
    }
    passed = flow_result.get("outcome") != "failed"
    return context, "passed" if passed else "failed", None if passed else (
        f"flow outcome: {flow_result.get('outcome')}"
    )
