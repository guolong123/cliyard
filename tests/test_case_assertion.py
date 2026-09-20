"""Tests for the cliyard case assertion engine.

Covers eq/ne/contains/comparison/regex/exists operators plus JSONPath
no-match and evaluate_all behavior.
"""
from __future__ import annotations

import pytest

from cliyard.engine.case_assertion import evaluate_all, evaluate_assertion
from cliyard.engine.flow import CaseAssertion


def mk(operator, jsonpath, expected, step="s1"):
    return CaseAssertion(step=step, jsonpath=jsonpath, operator=operator, expected=expected)


def test_eq_string_passes():
    a = mk("eq", "$.name", "Alice")
    assert evaluate_assertion(a, {"name": "Alice"}) == (True, "Alice")


def test_eq_string_fails():
    a = mk("eq", "$.name", "Bob")
    assert evaluate_assertion(a, {"name": "Alice"}) == (False, "Alice")


def test_eq_int_passes():
    a = mk("eq", "$.count", 3)
    assert evaluate_assertion(a, {"count": 3}) == (True, 3)


def test_eq_int_fails():
    a = mk("eq", "$.count", 4)
    assert evaluate_assertion(a, {"count": 3}) == (False, 3)


def test_ne():
    a = mk("ne", "$.count", 5)
    assert evaluate_assertion(a, {"count": 3}) == (True, 3)


def test_contains_on_list():
    a = mk("contains", "$.items", "x")
    assert evaluate_assertion(a, {"items": ["x", "y"]}) == (True, ["x", "y"])


def test_contains_on_string():
    a = mk("contains", "$.msg", "ell")
    assert evaluate_assertion(a, {"msg": "hello"}) == (True, "hello")


def test_contains_on_dict():
    a = mk("contains", "$.tags", "env")
    assert evaluate_assertion(a, {"tags": {"env": "prod"}}) == (True, {"env": "prod"})


def test_gt():
    assert evaluate_assertion(mk("gt", "$.n", 2), {"n": 3}) == (True, 3)
    assert evaluate_assertion(mk("gt", "$.n", 5), {"n": 3}) == (False, 3)


def test_gte():
    assert evaluate_assertion(mk("gte", "$.n", 3), {"n": 3}) == (True, 3)
    assert evaluate_assertion(mk("gte", "$.n", 4), {"n": 3}) == (False, 3)


def test_lt():
    assert evaluate_assertion(mk("lt", "$.n", 10), {"n": 3}) == (True, 3)
    assert evaluate_assertion(mk("lt", "$.n", 1), {"n": 3}) == (False, 3)


def test_lte():
    assert evaluate_assertion(mk("lte", "$.n", 3), {"n": 3}) == (True, 3)
    assert evaluate_assertion(mk("lte", "$.n", 2), {"n": 3}) == (False, 3)


def test_gt_non_numeric_returns_false_no_raise():
    a = mk("gt", "$.name", 5)
    passed, actual = evaluate_assertion(a, {"name": "abc"})
    assert passed is False
    assert actual == "abc"


def test_regex_fullmatch_passes():
    a = mk("regex", "$.code", r"a.c")
    assert evaluate_assertion(a, {"code": "abc"}) == (True, "abc")


def test_regex_fullmatch_fails():
    a = mk("regex", "$.code", r"a.c")
    assert evaluate_assertion(a, {"code": "abcd"}) == (False, "abcd")


def test_exists_true_when_present():
    assert evaluate_assertion(mk("exists", "$.name", True), {"name": "A"}) == (True, "A")


def test_exists_false_when_missing():
    assert evaluate_assertion(mk("exists", "$.name", True), {}) == (False, None)


def test_jsonpath_no_match_returns_false_none():
    a = mk("eq", "$.nonexistent", "x")
    assert evaluate_assertion(a, {"name": "Alice"}) == (False, None)


def test_evaluate_all_returns_triples():
    assertions = [
        mk("eq", "$.name", "Alice"),
        mk("eq", "$.missing", "z"),
    ]
    step_state = {"s1": {"name": "Alice"}}
    results = evaluate_all(assertions, step_state)
    assert isinstance(results, list)
    assert len(results) == 2
    for result in results:
        assert isinstance(result, tuple)
        assert len(result) == 3
    assert results[0] == (True, assertions[0], "Alice")
    assert results[1] == (False, assertions[1], None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))