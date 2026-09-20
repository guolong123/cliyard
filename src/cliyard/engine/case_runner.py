"""Case runner: orchestrate running flows as test cases and evaluating assertions."""
from __future__ import annotations

import time
from typing import Any, Callable

from cliyard.engine.case_assertion import evaluate_all
from cliyard.engine.flow import CaseSpec
from cliyard.engine.loader import load_flows
from cliyard.engine.orchestrator import run_flow

#: 业务成功码（``msg`` 只有在 ``code`` 非成功码时才被视为错误文案）。
_SUCCESS_CODES = {"", "0", "200", "success", "ok"}


def _report_row(case: CaseSpec, data_row: int | None, flow_ctx) -> dict:
    """Build one report row for a single flow run (one data row or single)."""
    # Determine the display step: first asserted step, else last executed step
    display_step_id = None
    if case.assert_:
        display_step_id = case.assert_[0].step
    elif flow_ctx.step_meta:
        display_step_id = list(flow_ctx.step_meta)[-1]

    meta = flow_ctx.step_meta.get(display_step_id, {}) if display_step_id else {}
    return {
        "case": case.name,
        "data_row": data_row,
        "outcome": flow_ctx.outcome,
        "name": meta.get("use") or display_step_id or "",
        "http_method": meta.get("http_method", ""),
        "http_path": meta.get("http_path", ""),
        "all_pass": flow_ctx.outcome == "completed",
        "elapsed": 0.0,
    }


def _collect_flow_errors(flow_ctx) -> list[str]:
    """Collect error messages from step results when the flow did not complete.

    Traverses step_state looking for explicit failure signals only: an
    ``error`` field, a business ``msg`` **paired with a failing ``code``**
    (so a success body whose msg is e.g. "查询成功" is not misreported), and
    ``issues`` (from custom verify steps).
    """
    errors: list[str] = []
    if flow_ctx.outcome == "completed":
        return errors
    for step_id, result in (flow_ctx.step_state or {}).items():
        if not isinstance(result, dict):
            continue
        # Plugin error return (e.g. {"error": "DB not found"})
        err = result.get("error")
        if err and isinstance(err, str) and err.strip():
            errors.append(f"[{step_id}] {err.strip()}")
        # Business error message: only surface ``msg`` when ``code`` is a
        # failure code, otherwise plain success text would be misreported.
        code = result.get("code")
        code_is_failure = (
            code is not None and str(code).strip().lower() not in _SUCCESS_CODES
        )
        msg = result.get("msg")
        if (
            code_is_failure
            and isinstance(msg, str)
            and msg.strip()
            and msg.strip().lower() not in ("success", "ok")
        ):
            errors.append(f"[{step_id}] {msg.strip()}")
        # Verify step issues (e.g. {"issues": ["创建时间缺失", ...]})
        issues = result.get("issues")
        if issues and isinstance(issues, list):
            for iss in issues:
                if iss and isinstance(iss, str):
                    errors.append(f"[{step_id}] {iss.strip()}")
    return errors


def run_case(
    case: CaseSpec,
    spec_dir: str,
    service_ctx,
    service_spec: dict,
    params_override: dict | None = None,
    step_cb: Callable[[str, dict], None] | None = None,
    console=None,
) -> dict:
    """Run one CaseSpec.

    For each data row (or a single run when no data), execute the referenced
    flow via run_flow, evaluate assertions, and aggregate a report.

    Args:
        case: The :class:`CaseSpec` to execute.
        spec_dir: Path to the service spec directory (used to load the flow).
        service_ctx: Service context passed through to ``run_flow``.
        service_spec: Full loaded service dict passed to ``run_flow``.
        params_override: Optional params merged on top of ``case.params`` /
            each data row before execution.
        step_cb: Optional ``(event_name, payload)`` callback forwarded to
            ``run_flow``.
        console: Optional Rich console forwarded to ``run_flow``.

    Returns:
        {
          "case": name,
          "flow": case.flow,
          "rows": [ {case, data_row, outcome, name, http_method, http_path,
                      assertions_passed, all_pass, elapsed}, ... ],
          "pass_count": int,
          "fail_count": int,
          "all_pass": bool,
        }
    """
    flows = load_flows(spec_dir)
    flow_spec = next((f for f in flows if f.command == case.flow), None)
    if flow_spec is None:
        raise ValueError(
            f"Flow '{case.flow}' referenced by case '{case.name}' not found"
        )

    # Collect flow-level param defaults (mirrors Click's default application in
    # build_flow_command). Without these, `{{ flow.xxx }}` renders empty for
    # params not present in case.params, breaking int()-typed flow params.
    flow_defaults: dict[str, Any] = {}
    for _loc in ("query", "body", "header"):
        for _param in (flow_spec.params or {}).get(_loc, []):
            if isinstance(_param, dict) and "default" in _param:
                flow_defaults[_param.get("name") or _param.get("field")] = _param["default"]

    # Build the list of param rows. Each data row merges on top of case.params;
    # when there is no data, run the flow once with case.params alone.
    if case.data:
        rows = [dict(flow_defaults, **case.params, **row) for row in case.data]
    else:
        rows = [dict(flow_defaults, **case.params)]

    report_rows: list[dict] = []
    pass_count = 0
    fail_count = 0

    for i, row in enumerate(rows):
        # ``row`` already merges flow_defaults + case.params (+ the data row).
        merged = dict(row)
        if params_override:
            merged.update(params_override)

        data_row = i if case.data else None
        start = time.monotonic()

        try:
            flow_ctx = run_flow(
                flow_spec,
                merged,
                service_ctx,
                service_spec,
                step_cb=step_cb,
                console=console,
                spec_dir=spec_dir,
            )
        except Exception as exc:  # row failure does NOT stop subsequent rows
            elapsed = time.monotonic() - start
            row_repr = {
                "case": case.name,
                "data_row": data_row,
                "outcome": "error",
                "name": "",
                "http_method": "",
                "http_path": "",
                "assertions_passed": None,
                "all_pass": False,
                "elapsed": round(elapsed, 4),
                "error": str(exc),
            }
            report_rows.append(row_repr)
            fail_count += 1
            continue

        elapsed = time.monotonic() - start

        assert_results = evaluate_all(case.assert_, flow_ctx.step_state) if case.assert_ else []
        # PASS 判定：
        # - 默认：outcome == completed 且所有断言通过
        # - 若 case 声明 expected_return: true：允许 flow 提前 return（outcome=returned）
        #   只要所有显式断言通过即判 PASS（用于"无权限等预期早退"场景）
        assertions_all_ok = all(p for p, _, _ in assert_results)
        if getattr(case, "expected_return", False):
            all_pass = (flow_ctx.outcome in ("completed", "returned")) and assertions_all_ok
        else:
            all_pass = (flow_ctx.outcome == "completed") and assertions_all_ok
        assertions_passed = [
            {
                "step": a.step,
                "jsonpath": a.jsonpath,
                "operator": a.operator,
                "expected": a.expected,
                "actual": actual,
                "passed": p,
            }
            for (p, a, actual) in assert_results
        ]

        row_repr = _report_row(case, data_row, flow_ctx)
        row_repr["assertions_passed"] = assertions_passed
        row_repr["all_pass"] = all_pass
        row_repr["elapsed"] = round(elapsed, 4)
        row_repr["flow_errors"] = _collect_flow_errors(flow_ctx)
        report_rows.append(row_repr)

        if all_pass:
            pass_count += 1
        else:
            fail_count += 1

    report = {
        "case": case.name,
        "flow": case.flow,
        "rows": report_rows,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "all_pass": pass_count > 0 and fail_count == 0,
    }
    if step_cb is not None:
        try:
            step_cb("case_report", {"report": report})
        except Exception:
            pass
    return report