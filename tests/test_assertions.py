"""Tests for cliyard.engine.assertions (dual-form assertion engine)."""

from __future__ import annotations

import pytest

from cliyard.engine.assertions import (
    AssertionFailure,
    evaluate_assertion,
    evaluate_assertions,
)


class TestExpressionForm:
    def test_pass_when_expression_truthy(self):
        assert evaluate_assertion("result.code == 0", {"result": {"code": 0}}) is None

    def test_fail_when_expression_falsy_with_exact_message(self):
        failure = evaluate_assertion("result.code == 1", {"result": {"code": 0}})

        assert isinstance(failure, AssertionFailure)
        assert failure.message == "expression 'result.code == 1' evaluated to falsy"
        assert failure.op is None
        assert failure.assertion == "result.code == 1"

    def test_wrapped_markers_stripped_before_evaluation(self):
        failure = evaluate_assertion("{{ result.code == 1 }}", {"result": {"code": 0}})

        assert failure is not None
        assert failure.message == "expression 'result.code == 1' evaluated to falsy"

    def test_dict_expr_form_uses_same_path(self):
        assert (
            evaluate_assertion({"expr": "result.code == 0"}, {"result": {"code": 0}})
            is None
        )
        failure = evaluate_assertion({"expr": "result.code == 1"}, {"result": {"code": 0}})

        assert failure is not None
        assert failure.message == "expression 'result.code == 1' evaluated to falsy"

    def test_missing_context_keys_degrade_to_falsy_not_raise(self):
        failure = evaluate_assertion("step.foo.bar == 1", {})

        assert failure is not None
        assert failure.message == "expression 'step.foo.bar == 1' evaluated to falsy"

    def test_syntax_error_becomes_failure_not_raise(self):
        failure = evaluate_assertion("{{ ", {})

        assert failure is not None
        # 表达式先 strip 再求值（与 orchestrator._evaluate_expression 一致），message 用剥离后的形式
        assert failure.message == "invalid expression '{{'"


class TestStructuredComparisonOps:
    @pytest.mark.parametrize(
        ("op", "value", "actual", "passes"),
        [
            ("eq", 0, 0, True),
            ("eq", 1, 0, False),
            ("ne", 1, 0, True),
            ("ne", 0, 0, False),
            ("gt", 1, 5, True),
            ("gt", 5, 5, False),
            ("ge", 5, 5, True),
            ("ge", 6, 5, False),
            ("lt", 5, 3, True),
            ("lt", 5, 5, False),
            ("le", 5, 5, True),
            ("le", 4, 5, False),
            ("contains", "a", ["a", "b"], True),
            ("contains", "z", ["a", "b"], False),
            ("not_contains", "z", ["a", "b"], True),
            ("not_contains", "a", ["a", "b"], False),
            ("in", ["active", "ok"], "active", True),
            ("in", ["active", "ok"], "stopped", False),
            ("not_in", ["active", "ok"], "stopped", True),
            ("not_in", ["active", "ok"], "active", False),
            ("matches", "^al", "alice", True),
            ("matches", "^bo", "alice", False),
            ("length_eq", 2, ["a", "b"], True),
            ("length_eq", 3, ["a", "b"], False),
        ],
    )
    def test_op_semantics(self, op, value, actual, passes):
        assertion = {"jsonpath": "$.target", "op": op, "value": value}
        result = evaluate_assertion(assertion, {"result": {"target": actual}})

        if passes:
            assert result is None
        else:
            assert isinstance(result, AssertionFailure)
            assert result.op == op
            assert result.expected == value
            assert result.actual == actual

    def test_comparison_failure_message_exact_format(self):
        assertion = {"jsonpath": "$.code", "op": "eq", "value": 1}

        failure = evaluate_assertion(assertion, {"result": {"code": 0}})

        assert failure is not None
        assert failure.message == "expected eq 1, got 0"
        assert failure.assertion == assertion

    def test_type_error_comparison_becomes_failure_not_raise(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.name", "op": "gt", "value": 5}, {"result": {"name": "abc"}}
        )

        assert failure is not None
        assert failure.message == "expected gt 5, got abc"

    def test_contains_on_non_container_becomes_failure_not_raise(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.code", "op": "contains", "value": 1}, {"result": {"code": 0}}
        )

        assert failure is not None
        assert failure.message == "expected contains 1, got 0"

    def test_matches_invalid_regex_becomes_failure_not_raise(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.name", "op": "matches", "value": "("}, {"result": {"name": "alice"}}
        )

        assert failure is not None
        assert failure.message == "invalid regex '('"

    def test_length_eq_on_non_sized_actual_becomes_failure_not_raise(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.code", "op": "length_eq", "value": 2}, {"result": {"code": 0}}
        )

        assert failure is not None
        assert failure.message == "expected length_eq 2, got 0"


class TestJsonpathExtraction:
    def test_no_match_fails_with_exact_message(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.missing", "op": "eq", "value": 1}, {"result": {"code": 0}}
        )

        assert failure is not None
        assert failure.message == "jsonpath '$.missing' matched nothing"
        assert failure.actual is None

    def test_exists_passes_when_matched(self):
        assertion = {"jsonpath": "$.code", "op": "exists"}

        assert evaluate_assertion(assertion, {"result": {"code": 0}}) is None

    def test_exists_fails_when_no_match(self):
        failure = evaluate_assertion({"jsonpath": "$.missing", "op": "exists"}, {"result": {}})

        assert failure is not None
        assert failure.message == "jsonpath '$.missing' matched nothing"

    def test_not_exists_passes_when_no_match(self):
        assert evaluate_assertion({"jsonpath": "$.missing", "op": "not_exists"}, {"result": {}}) is None

    def test_not_exists_fails_when_matched(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.code", "op": "not_exists"}, {"result": {"code": 0}}
        )

        assert failure is not None
        assert failure.message == "expected not_exists None, got 0"

    def test_nested_jsonpath_extracts_value(self):
        assertion = {"jsonpath": "$.user.name", "op": "eq", "value": "alice"}

        assert evaluate_assertion(assertion, {"result": {"user": {"name": "alice"}}}) is None

    def test_missing_result_key_degrades_to_no_match_not_raise(self):
        failure = evaluate_assertion({"jsonpath": "$.code", "op": "eq", "value": 0}, {})

        assert failure is not None
        assert failure.message == "jsonpath '$.code' matched nothing"

    def test_invalid_jsonpath_syntax_becomes_failure_not_raise(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.x[", "op": "eq", "value": 1}, {"result": {"x": 1}}
        )

        assert failure is not None
        assert failure.message == "invalid jsonpath '$.x['"


class TestUnknownOpAndMalformed:
    def test_unknown_op_fails_with_exact_message(self):
        failure = evaluate_assertion(
            {"jsonpath": "$.code", "op": "bogus", "value": 1}, {"result": {"code": 0}}
        )

        assert failure is not None
        assert failure.message == "unknown op 'bogus'"

    @pytest.mark.parametrize(
        ("bad_input", "expected_message"),
        [
            (42, "malformed assertion: 42"),
            ({}, "malformed assertion: {}"),
            ({"foo": "bar"}, "malformed assertion: {'foo': 'bar'}"),
            ({"jsonpath": "$.code"}, "malformed assertion: {'jsonpath': '$.code'}"),
        ],
    )
    def test_malformed_inputs_become_failures_not_raise(self, bad_input, expected_message):
        failure = evaluate_assertion(bad_input, {"result": {"code": 0}})

        assert failure is not None
        assert failure.message == expected_message


class TestEvaluateAssertions:
    def test_one_crashing_item_does_not_stop_others(self):
        assertions = [
            {"jsonpath": "$.code", "op": "eq", "value": 0},
            "{{ 1 + 'a' }}",  # raises TypeError inside jinja2 evaluation
            {"jsonpath": "$.code", "op": "eq", "value": 99},
        ]

        failures = evaluate_assertions(assertions, {"result": {"code": 0}})

        assert len(failures) == 2
        assert "TypeError" in failures[0].message
        assert failures[1].message == "expected eq 99, got 0"

    def test_none_treated_as_empty_list(self):
        assert evaluate_assertions(None, {"result": {}}) == []

    def test_bare_dict_treated_as_single_item(self):
        assert (
            evaluate_assertions(
                {"jsonpath": "$.code", "op": "eq", "value": 0}, {"result": {"code": 0}}
            )
            == []
        )

    def test_tuple_iterable_accepted(self):
        failures = evaluate_assertions(
            ({"jsonpath": "$.code", "op": "eq", "value": 7},), {"result": {"code": 0}}
        )

        assert len(failures) == 1
        assert failures[0].message == "expected eq 7, got 0"

    def test_all_pass_returns_empty_list(self):
        assertions = [
            "{{ result.code == 0 }}",
            {"jsonpath": "$.code", "op": "eq", "value": 0},
            {"jsonpath": "$.tags", "op": "contains", "value": "a"},
        ]

        assert evaluate_assertions(assertions, {"result": {"code": 0, "tags": ["a"]}}) == []
