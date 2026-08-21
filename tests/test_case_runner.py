"""Tests for the case runner: CaseResult / run_case / filter_cases.

No real network: ``HttpClient.request`` is patched class-wide (same pattern
as ``tests/test_flow.py``'s ``http_mock`` fixture), covering both command
cases (run_case creates the client) and flow cases (run_flow creates its own).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from cliyard.engine.builder import ServiceContext
from cliyard.engine.case import CaseSpec
from cliyard.engine.errors import ApiError


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SERVICE_SPEC = {
    "name": "test",
    "resources": [
        {
            "name": "pets",
            "path": "pets",
            "methods": {
                "list": {
                    "http": {"method": "GET", "path": "pets"},
                    "params": {
                        "query": [{"name": "status", "field": "status", "type": "string"}]
                    },
                },
            },
        },
    ],
}

SERVICE_CTX = ServiceContext(base_url="http://fake")


@pytest.fixture
def fake_http(monkeypatch):
    """Patch ``HttpClient.request`` so run_case/run_flow never hit network.

    Returns a dict with:
    - ``responses``: response dicts (or Exceptions) returned in order
    - ``calls``: recorded HTTP calls
    """
    responses: list = []
    calls: list[dict] = []

    def _mock_request(
        self,
        method,
        url,
        data=None,
        query_params=None,
        headers=None,
        timeout=None,
        files=None,
    ):
        calls.append(
            {"method": method, "url": url, "data": data, "query_params": query_params}
        )
        cfg = responses.pop(0)
        if isinstance(cfg, Exception):
            raise cfg
        resp = MagicMock()
        resp.json.return_value = cfg
        resp.status_code = 200
        resp.text = ""
        return resp

    monkeypatch.setattr("cliyard.client.http.HttpClient.request", _mock_request)
    return {"responses": responses, "calls": calls}


def _write_flow_spec(tmp_path: Path) -> str:
    """Create a minimal spec dir with one flow ``check-pets``."""
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    (flows_dir / "_flows.yaml").write_text(
        yaml.safe_dump(
            {
                "flows": {
                    "check-pets": {
                        "command": "check-pets",
                        "description": "查询宠物流程",
                        "steps": [
                            {
                                "id": "fetch",
                                "use": "pets.list",
                                "params": {"query": {"status": "{{ flow.status }}"}},
                                "extract": {"pets": "$.data"},
                            },
                        ],
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return str(tmp_path)


# ---------------------------------------------------------------------------
# CaseResult defaults
# ---------------------------------------------------------------------------


class TestCaseResult:
    def test_defaults(self):
        from cliyard.engine.case_runner import CaseResult

        r = CaseResult(name="x", kind="command", target="pets.list", status="passed")
        assert r.assertion_failures == []
        assert r.error is None
        assert r.duration_ms == 0
        assert r.steps == []


# ---------------------------------------------------------------------------
# run_case — kind=command
# ---------------------------------------------------------------------------


class TestCommandCase:
    def test_passes_with_structured_assert(self, fake_http):
        from cliyard.engine.case_runner import CaseResult, run_case

        fake_http["responses"].append({"code": 0, "data": [{"id": 1}]})
        case = CaseSpec(
            name="list-pets",
            kind="command",
            target="pets.list",
            asserts=[{"jsonpath": "$.code", "op": "eq", "value": 0}],
        )

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert isinstance(result, CaseResult)
        assert result.status == "passed"
        assert result.assertion_failures == []
        assert result.error is None
        assert result.duration_ms >= 0

    def test_assertion_failure_marks_failed_and_emits_events(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append({"code": 1})
        case = CaseSpec(
            name="bad-assert",
            kind="command",
            target="pets.list",
            asserts=[
                {"jsonpath": "$.code", "op": "exists"},
                {"jsonpath": "$.code", "op": "eq", "value": 0},
            ],
        )
        events: list[tuple[str, dict]] = []

        result = run_case(
            case, SERVICE_CTX, SERVICE_SPEC, step_cb=lambda n, p: events.append((n, p))
        )

        assert result.status == "failed"
        assert len(result.assertion_failures) == 1
        failure = result.assertion_failures[0]
        assert "expected" in failure.message and "got" in failure.message

        assert_results = [p for n, p in events if n == "assert_result"]
        assert len(assert_results) == 2
        # 第一条通过：index/passed/message 精确断言（防误导性成功）
        assert assert_results[0]["index"] == 0
        assert assert_results[0]["passed"] is True
        assert assert_results[0]["message"] == ""
        # 第二条失败：携带原文与失败原因
        assert assert_results[1]["index"] == 1
        assert assert_results[1]["passed"] is False
        assert assert_results[1]["assertion"] == {
            "jsonpath": "$.code",
            "op": "eq",
            "value": 0,
        }
        assert assert_results[1]["message"]

    def test_target_not_found_is_error(self, fake_http):
        from cliyard.engine.case_runner import run_case

        case = CaseSpec(name="ghost", kind="command", target="pets.missing")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "error"
        assert result.error is not None
        assert "missing" in result.error

    def test_malformed_target_is_error(self, fake_http):
        from cliyard.engine.case_runner import run_case

        case = CaseSpec(name="malformed", kind="command", target="noparse")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "error"
        assert result.error is not None
        assert "noparse" in result.error

    def test_cliy_error_is_failed(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append(ApiError(500, "http://fake/pets", "boom"))
        case = CaseSpec(name="api-down", kind="command", target="pets.list")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "failed"
        assert result.error is not None
        assert "500" in result.error

    def test_unknown_op_collected_as_failure_not_raised(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append({"code": 0})
        case = CaseSpec(
            name="unknown-op",
            kind="command",
            target="pets.list",
            asserts=[{"jsonpath": "$.code", "op": "frobnicate", "value": 1}],
        )

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "failed"
        assert "unknown op" in result.assertion_failures[0].message

    def test_empty_asserts_pass_trivially(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append({"code": 0})
        case = CaseSpec(name="no-asserts", kind="command", target="pets.list")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "passed"

    def test_params_override_wins_over_case_params(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append({"code": 0})
        case = CaseSpec(
            name="merged", kind="command", target="pets.list", params={"status": "sold"}
        )

        run_case(case, SERVICE_CTX, SERVICE_SPEC, params_override={"status": "available"})

        assert fake_http["calls"][0]["query_params"] == {"status": "available"}

    def test_steps_collect_events_and_forward(self, fake_http):
        from cliyard.engine.case_runner import run_case

        fake_http["responses"].append({"code": 0})
        case = CaseSpec(name="steps", kind="command", target="pets.list")
        forwarded: list[tuple[str, dict]] = []

        result = run_case(
            case, SERVICE_CTX, SERVICE_SPEC, step_cb=lambda n, p: forwarded.append((n, p))
        )

        event_names = [s["event"] for s in result.steps]
        assert "validate" in event_names
        assert "request" in event_names
        assert "response" in event_names
        # steps 与转发内容一一对应（透传无损）
        rebuilt = [
            (s["event"], {k: v for k, v in s.items() if k != "event"})
            for s in result.steps
        ]
        assert rebuilt == forwarded


# ---------------------------------------------------------------------------
# run_case — kind=flow
# ---------------------------------------------------------------------------


class TestFlowCase:
    def test_passes_with_three_key_context_asserts(self, fake_http, tmp_path):
        from cliyard.engine.case_runner import run_case

        spec_dir = _write_flow_spec(tmp_path)
        fake_http["responses"].append({"code": 0, "data": [{"id": 1}, {"id": 2}]})
        case = CaseSpec(
            name="flow-ok",
            kind="flow",
            target="check-pets",
            params={"status": "available"},
            asserts=[
                "{{ result.outcome == 'completed' }}",
                {"jsonpath": "$.step_state.fetch.pets", "op": "exists"},
                {"jsonpath": "$.outcome", "op": "eq", "value": "completed"},
            ],
        )

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC, spec_dir=spec_dir)

        assert result.status == "passed", f"error={result.error} failures={result.assertion_failures}"
        assert result.assertion_failures == []

    def test_failing_assert_marks_failed(self, fake_http, tmp_path):
        from cliyard.engine.case_runner import run_case

        spec_dir = _write_flow_spec(tmp_path)
        fake_http["responses"].append({"code": 0, "data": []})
        case = CaseSpec(
            name="flow-bad",
            kind="flow",
            target="check-pets",
            asserts=[{"jsonpath": "$.outcome", "op": "eq", "value": "returned"}],
        )
        events: list[tuple[str, dict]] = []

        result = run_case(
            case, SERVICE_CTX, SERVICE_SPEC, spec_dir=spec_dir,
            step_cb=lambda n, p: events.append((n, p)),
        )

        assert result.status == "failed"
        assert len(result.assertion_failures) == 1
        assert any(n == "assert_result" and p["passed"] is False for n, p in events)

    def test_unknown_flow_target_is_error(self, fake_http, tmp_path):
        from cliyard.engine.case_runner import run_case

        spec_dir = _write_flow_spec(tmp_path)
        case = CaseSpec(name="flow-ghost", kind="flow", target="no-such-flow")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC, spec_dir=spec_dir)

        assert result.status == "error"
        assert result.error is not None
        assert "no-such-flow" in result.error

    def test_missing_spec_dir_is_error(self, fake_http):
        from cliyard.engine.case_runner import run_case

        case = CaseSpec(name="flow-nodir", kind="flow", target="check-pets")

        result = run_case(case, SERVICE_CTX, SERVICE_SPEC)

        assert result.status == "error"
        assert result.error is not None
        assert "spec_dir" in result.error

    def test_flow_step_cb_events_forwarded(self, fake_http, tmp_path):
        from cliyard.engine.case_runner import run_case

        spec_dir = _write_flow_spec(tmp_path)
        fake_http["responses"].append({"code": 0, "data": []})
        case = CaseSpec(name="flow-events", kind="flow", target="check-pets")
        events: list[tuple[str, dict]] = []

        result = run_case(
            case, SERVICE_CTX, SERVICE_SPEC, spec_dir=spec_dir,
            step_cb=lambda n, p: events.append((n, p)),
        )

        names = [n for n, _ in events]
        assert "flow_start" in names
        assert "step_start" in names
        assert "flow_end" in names
        # steps 与转发内容一一对应（透传无损）
        rebuilt = [
            (s["event"], {k: v for k, v in s.items() if k != "event"})
            for s in result.steps
        ]
        assert rebuilt == events


# ---------------------------------------------------------------------------
# filter_cases — 标签表达式过滤
# ---------------------------------------------------------------------------


def _case(name: str, labels: list[str]) -> CaseSpec:
    return CaseSpec(name=name, labels=labels)


class TestFilterCases:
    CASES = [
        _case("a-only", ["a"]),
        _case("ab", ["a", "b"]),
        _case("c", ["c"]),
    ]

    def test_none_returns_all_in_order(self):
        from cliyard.engine.case_runner import filter_cases

        assert [c.name for c in filter_cases(self.CASES, None)] == [
            "a-only", "ab", "c",
        ]

    def test_comma_groups_are_ored(self):
        from cliyard.engine.case_runner import filter_cases

        names = [c.name for c in filter_cases(self.CASES, "c,a")]
        assert names == ["a-only", "ab", "c"]

    def test_plus_within_group_is_and(self):
        from cliyard.engine.case_runner import filter_cases

        names = [c.name for c in filter_cases(self.CASES, "a+b")]
        assert names == ["ab"]

    def test_mixed_or_of_and_groups(self):
        from cliyard.engine.case_runner import filter_cases

        names = {c.name for c in filter_cases(self.CASES, "a+b,c")}
        assert names == {"ab", "c"}

    def test_whitespace_stripped(self):
        from cliyard.engine.case_runner import filter_cases

        names = {c.name for c in filter_cases(self.CASES, " a+b , c ")}
        assert names == {"ab", "c"}

    def test_empty_tokens_ignored(self):
        from cliyard.engine.case_runner import filter_cases

        names = [c.name for c in filter_cases(self.CASES, ",a,,")]
        assert names == ["a-only", "ab"]

    def test_duplicate_labels_harmless(self):
        from cliyard.engine.case_runner import filter_cases

        # a+a+b 与 a+b 等价
        assert [c.name for c in filter_cases(self.CASES, "a+a+b")] == [
            c.name for c in filter_cases(self.CASES, "a+b")
        ] == ["ab"]

    @pytest.mark.parametrize("expr", ["", ",,,", " , ", "+"])
    def test_all_empty_raises_value_error(self, expr):
        from cliyard.engine.case_runner import filter_cases

        with pytest.raises(ValueError, match="invalid labels"):
            filter_cases(self.CASES, expr)
