"""Case assertion engine.

Evaluates CaseAssertion (step + jsonpath + operator + expected) against a
flow's step results. Reuses jsonpath-ng for extraction, mirroring the flow
orchestrator's extract mechanism.
"""
from __future__ import annotations

import re
from typing import Any

from jsonpath_ng import parse as jp_parse

from cliyard.engine.flow import CaseAssertion


def _extract(jsonpath: str, data: Any):
    """Extract value(s) via jsonpath-ng. Returns match value or None if no match.

    Mirrors orchestrator._execute_use_step extract (jsonpath_ng parse + find).
    """
    try:
        expr = jp_parse(jsonpath)
        matches = expr.find(data)
        if not matches:
            return None
        first = matches[0].value
        # If multiple matches, return the list of values
        if len(matches) > 1:
            return [m.value for m in matches]
        return first
    except Exception:
        return None


def evaluate_assertion(
    assertion: CaseAssertion, step_result: Any
) -> tuple[bool, Any]:
    """Evaluate a single assertion against a step result.

    Returns (passed, actual). When the JSONPath yields no match, passed=False
    (never raises). Operator semantics follow the plan's D11 table:
      eq       == (no implicit coercion)
      ne       !=
      contains list: in / str: substring / dict: key in
      gt/gte/lt/lte numeric comparison (non-numeric => fail)
      regex    re.fullmatch(expected, str(actual))
      exists   matches exist (expected is truthy) or not
    """
    if assertion.operator == "exists":
        actual = _extract(assertion.jsonpath, step_result)
        wanted = bool(assertion.expected)
        return (actual is not None) == wanted, actual

    actual = _extract(assertion.jsonpath, step_result)
    if actual is None:
        return False, None

    op = assertion.operator
    expected = assertion.expected

    if op == "eq":
        return actual == expected, actual
    if op == "ne":
        return actual != expected, actual
    if op == "contains":
        if isinstance(actual, (list, tuple, set)):
            return expected in actual, actual
        if isinstance(actual, dict):
            return expected in actual, actual
        return expected in str(actual), actual
    if op in ("gt", "gte", "lt", "lte"):
        try:
            if op == "gt":
                return actual > expected, actual
            if op == "gte":
                return actual >= expected, actual
            if op == "lt":
                return actual < expected, actual
            return actual <= expected, actual
        except TypeError:
            return False, actual
    if op == "regex":
        return bool(re.fullmatch(str(expected), str(actual))), actual

    return False, actual


def evaluate_all(
    assertions, step_state: dict
) -> list[tuple[bool, CaseAssertion, Any]]:
    """Evaluate all assertions against the flow step_state.

    Returns list of (passed, assertion, actual) tuples.
    """
    results: list[tuple[bool, CaseAssertion, Any]] = []
    for a in assertions:
        step_result = step_state.get(a.step, {})
        passed, actual = evaluate_assertion(a, step_result)
        results.append((passed, a, actual))
    return results