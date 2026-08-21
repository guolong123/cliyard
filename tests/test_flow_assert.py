"""Tests for ``run_flow`` execution-result dict and ``FlowStep.assert_`` enforcement.

Two behaviors:

1. ``run_flow`` returns ``{"outcome", "step_state", "steps"}`` from every
   termination path (completed / returned / skipped / failed).
2. A step's ``assert_`` expression is enforced after execution: failure marks
   the step failed and skips its ``on_result`` branching; success leaves the
   positive path untouched.
"""

from unittest.mock import patch

from cliyard.engine.builder import ServiceContext
from cliyard.engine.errors import CliyError
from cliyard.engine.flow import FlowSpec, FlowStep
from cliyard.engine.orchestrator import run_flow

from tests.test_flow import MockConsole

_SERVICE_SPEC = {"name": "test", "resources": []}

RESULT_KEYS = {"outcome", "step_state", "steps"}


class FlowConsole(MockConsole):
    """MockConsole tolerating the zero-arg ``print()`` in _show_flow_summary."""

    def print(self, msg="", *args, **kwargs):
        self.output.append(str(msg))


def _run_flow(flow_spec, step_cb=None):
    """Run *flow_spec* in-process with a mocked console; return (result, console)."""
    console = FlowConsole()
    result = run_flow(
        flow_spec,
        {},
        ServiceContext(base_url="http://test.local"),
        _SERVICE_SPEC,
        console=console,
        step_cb=step_cb,
    )
    return result, console


# ---------------------------------------------------------------------------
# 1. Return-dict key shape on every exit path
# ---------------------------------------------------------------------------


class TestReturnDictShape:
    """run_flow must return the execution-result dict from every exit."""

    def test_no_steps_returns_completed_dict(self):
        result, _ = _run_flow(FlowSpec(command="t", steps=[]))

        assert isinstance(result, dict)
        assert set(result.keys()) == RESULT_KEYS
        assert result["outcome"] == "completed"
        assert result["steps"] == []

    def test_completed_path_returns_dict(self):
        spec = FlowSpec(
            command="t",
            steps=[FlowStep(id="s1", use="user.list", params={})],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": True},
        ):
            result, _ = _run_flow(spec)

        assert isinstance(result, dict)
        assert set(result.keys()) == RESULT_KEYS
        assert result["outcome"] == "completed"

    def test_failed_path_returns_dict(self):
        spec = FlowSpec(
            command="t",
            steps=[FlowStep(id="s1", use="user.list", params={})],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=CliyError("boom"),
        ):
            result, _ = _run_flow(spec)

        assert isinstance(result, dict)
        assert set(result.keys()) == RESULT_KEYS
        assert result["outcome"] == "failed"

    def test_returned_path_returns_dict(self):
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    on_result=[
                        {"if": "{{ step.s1.ok }}", "then": [{"action": "return"}], "else": []}
                    ],
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": True},
        ):
            result, _ = _run_flow(spec)

        assert isinstance(result, dict)
        assert set(result.keys()) == RESULT_KEYS
        assert result["outcome"] == "returned"

    def test_skipped_path_returns_dict(self):
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    on_result=[
                        {"if": "{{ step.s1.ok }}", "then": [{"action": "skip"}], "else": []}
                    ],
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": True},
        ):
            result, _ = _run_flow(spec)

        assert isinstance(result, dict)
        assert set(result.keys()) == RESULT_KEYS
        assert result["outcome"] == "skipped"


# ---------------------------------------------------------------------------
# 2. Result carries step_state and ordered step records
# ---------------------------------------------------------------------------


class TestResultContents:
    """The returned dict maps step ids to results and lists steps in order."""

    def test_step_state_maps_ids_to_results(self):
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(id="s1", use="user.list", params={}),
                FlowStep(id="s2", use="user.get", params={}),
            ],
        )

        def mock_exec(step, resolved_params, ctx):
            return {"id": step.id, "value": 42}

        with patch("cliyard.engine.orchestrator.execute_use_step", side_effect=mock_exec):
            result, _ = _run_flow(spec)

        assert result["step_state"] == {
            "s1": {"id": "s1", "value": 42},
            "s2": {"id": "s2", "value": 42},
        }
        assert [s["id"] for s in result["steps"]] == ["s1", "s2"]
        assert all(s["status"] == "ok" for s in result["steps"])


# ---------------------------------------------------------------------------
# 3. FlowStep.assert_ enforcement
# ---------------------------------------------------------------------------


class TestAssertEnforcement:
    """A failing assert_ fails the step; a passing one keeps the happy path."""

    def test_assert_pass_completes_with_all_steps_ok(self):
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    assert_="{{ step.s1.ok }}",
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": True},
        ):
            result, _ = _run_flow(spec)

        assert result["outcome"] == "completed"
        assert all(s["status"] == "ok" for s in result["steps"])

    def test_assert_fail_fails_flow_and_records_fail_step(self):
        events = []
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    assert_="{{ step.s1.ok }}",
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": False},
        ):
            result, console = _run_flow(
                spec, step_cb=lambda n, p: events.append((n, p))
            )

        assert result["outcome"] == "failed"
        fail_records = [s for s in result["steps"] if s["status"] == "fail"]
        assert len(fail_records) == 1
        assert fail_records[0]["id"] == "s1"

        flow_end = [p for n, p in events if n == "flow_end"]
        assert flow_end and flow_end[-1]["outcome"] == "failed"

        # Console ✗ line carries both the assertion text and the reason
        output = "\n".join(console.output)
        assert "✗" in output
        assert "{{ step.s1.ok }}" in output
        assert "evaluated to falsy" in output

    def test_assert_fail_skips_on_result_actions(self):
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    assert_="{{ step.s1.ok }}",
                    on_result=[
                        {"type": "echo", "message": "ON_RESULT_MUST_NOT_RUN"}
                    ],
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": False},
        ):
            result, console = _run_flow(spec)

        assert result["outcome"] == "failed"
        assert "ON_RESULT_MUST_NOT_RUN" not in "\n".join(console.output)

    def test_assert_pass_still_evaluates_on_result_branch(self):
        """Positive-path regression: passing assert leaves on_result intact."""
        spec = FlowSpec(
            command="t",
            steps=[
                FlowStep(
                    id="s1",
                    use="user.list",
                    params={},
                    assert_="{{ step.s1.ok }}",
                    on_result=[
                        {
                            "if": "{{ step.s1.ok }}",
                            "then": [
                                {"type": "echo", "message": "BRANCH_FIRED"}
                            ],
                            "else": [],
                        }
                    ],
                ),
            ],
        )

        with patch(
            "cliyard.engine.orchestrator.execute_use_step",
            side_effect=lambda s, rp, c: {"ok": True},
        ):
            result, console = _run_flow(spec)

        assert result["outcome"] == "completed"
        assert "BRANCH_FIRED" in "\n".join(console.output)
