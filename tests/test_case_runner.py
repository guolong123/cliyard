"""Tests for cliyard.engine.case_runner.run_case."""
from __future__ import annotations

from cliyard.engine.case_runner import run_case
from cliyard.engine.flow import CaseAssertion, CaseSpec


class FakeFlowSpec:
    """Minimal stand-in for FlowSpec with the attributes run_case needs."""

    def __init__(self, command: str):
        self.command = command
        self.params = {}


class FakeFlowCtx:
    """Duck-typed stand-in for FlowContext with the attributes case_runner reads."""

    def __init__(self, outcome: str, step_state: dict | None = None, step_meta: dict | None = None):
        self.outcome = outcome
        self.step_state = step_state or {}
        self.step_meta = step_meta or {}


def make_assertion(step="step1", jsonpath="$.code", operator="eq", expected=0):
    return CaseAssertion(step=step, jsonpath=jsonpath, operator=operator, expected=expected)


def install_fake(monkeypatch, flow_outcomes=None, assert_results=None):
    """Monkeypatch case_runner.run_flow and load_flows to avoid real HTTP/Disk I/O.

    ``assert_results`` is a list whose items supply the ``code`` value for each
    consecutive flow run's step_state. When empty/None, every run uses 0.
    """
    flow_outcomes = list(flow_outcomes or ["completed"])
    assert_results = list(assert_results or [])

    def fake_run_flow(flow_spec, flow_params, service_ctx, service_spec, **kwargs):
        outcome = flow_outcomes.pop(0)
        if assert_results:
            code = assert_results.pop(0)
        else:
            code = 0
        step_state = {"step1": {"code": code}}
        return FakeFlowCtx(
            outcome=outcome,
            step_state=step_state,
            step_meta={
                "step1": {
                    "use": "demo.method",
                    "http_method": "POST",
                    "http_path": "/crm/v1/demo",
                }
            },
        )

    monkeypatch.setattr("cliyard.engine.case_runner.run_flow", fake_run_flow)
    monkeypatch.setattr(
        "cliyard.engine.case_runner.load_flows",
        lambda spec_dir: [FakeFlowSpec("demo-flow")],
    )


def run(case, **kwargs):
    return run_case(case, spec_dir="/tmp/specs", service_ctx=None, service_spec={"name": "demo"}, **kwargs)


def test_single_case_all_assertions_pass(monkeypatch):
    install_fake(monkeypatch, assert_results=[0])

    case = CaseSpec(name="tc_pass", flow="demo-flow", params={"a": 1}, assert_=[make_assertion()])
    result = run(case)

    assert result["case"] == "tc_pass"
    assert result["flow"] == "demo-flow"
    assert result["pass_count"] == 1
    assert result["fail_count"] == 0
    assert result["all_pass"] is True
    assert len(result["rows"]) == 1
    assert result["rows"][0]["all_pass"] is True
    assert result["rows"][0]["data_row"] is None
    assert result["rows"][0]["outcome"] == "completed"
    assert result["rows"][0]["name"] == "demo.method"
    assert result["rows"][0]["assertions_passed"][0]["passed"] is True


def test_assertion_failure_marks_case_failed(monkeypatch):
    install_fake(monkeypatch, assert_results=[0])

    case = CaseSpec(name="tc_assert_fail", flow="demo-flow", params={"a": 1}, assert_=[make_assertion(expected=999)])
    result = run(case)

    assert result["pass_count"] == 0
    assert result["fail_count"] == 1
    assert result["all_pass"] is False
    assert result["rows"][0]["all_pass"] is False
    assert result["rows"][0]["assertions_passed"][0]["passed"] is False


def test_flow_error_outcome_fails_even_with_passing_assertion(monkeypatch):
    install_fake(monkeypatch, flow_outcomes=["failed"], assert_results=[0])

    case = CaseSpec(name="tc_flow_fail", flow="demo-flow", params={"a": 1}, assert_=[make_assertion(expected=0)])
    result = run(case)

    assert result["pass_count"] == 0
    assert result["fail_count"] == 1
    assert result["all_pass"] is False
    assert result["rows"][0]["outcome"] == "failed"
    assert result["rows"][0]["all_pass"] is False


def test_multiple_data_rows_first_fails_then_all_report(monkeypatch):
    # Each data row runs the flow once. Make the FIRST row's "code" mismatch the
    # expected value so its assertion fails, while remaining rows pass.
    install_fake(monkeypatch, flow_outcomes=["completed"] * 3, assert_results=[999, 0, 0])

    case = CaseSpec(
        name="tc_multi",
        flow="demo-flow",
        params={"a": 1},
        data=[{"b": 2}, {"b": 3}, {"b": 4}],
        assert_=[make_assertion(expected=0)],
    )
    result = run(case)

    assert len(result["rows"]) == 3
    assert [r["data_row"] for r in result["rows"]] == [0, 1, 2]
    assert result["rows"][0]["all_pass"] is False
    assert result["rows"][0]["assertions_passed"][0]["passed"] is False
    assert result["rows"][1]["all_pass"] is True
    assert result["rows"][2]["all_pass"] is True
    assert result["pass_count"] == 2
    assert result["fail_count"] == 1
    assert result["all_pass"] is False