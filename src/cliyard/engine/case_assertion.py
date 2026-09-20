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


#: Sentinel returned by :func:`_extract` when a valid JSONPath matches nothing.
#: Distinguishes "no match" from a literal JSON ``null`` (which extracts as None).
_MISSING = object()


def _extract(jsonpath: str, data: Any) -> Any:
    """Extract value(s) via jsonpath-ng.

    Returns the matched value, a list of values when multiple nodes match, or
    the :data:`_MISSING` sentinel when the path is valid but matches nothing.
    A malformed JSONPath raises ``ValueError`` (instead of being swallowed as
    "no match"), so reports can tell a bad path from a value mismatch.

    Mirrors orchestrator._execute_use_step extract (jsonpath_ng parse + find).
    """
    try:
        expr = jp_parse(jsonpath)
    except Exception as exc:
        raise ValueError(f"invalid jsonpath {jsonpath!r}: {exc}") from exc
    matches = expr.find(data)
    if not matches:
        return _MISSING
    # If multiple matches, return the list of values
    if len(matches) > 1:
        return [m.value for m in matches]
    return matches[0].value


def evaluate_assertion(
    assertion: CaseAssertion, step_result: Any
) -> tuple[bool, Any]:
    """Evaluate a single assertion against a step result.

    Returns (passed, actual). When the JSONPath yields no match, passed=False.
    A malformed JSONPath raises ``ValueError``; no-match and value mismatches
    never raise. Operator semantics follow the plan's D11 table:
      eq       == (no implicit coercion)
      ne       !=
      contains list: in / str: substring / dict: key in
      gt/gte/lt/lte numeric comparison (non-numeric => fail)
      regex    re.fullmatch(expected, str(actual))
      exists   node exists (expected truthy) or not — a literal JSON null
               counts as existing
    """
    if assertion.operator == "exists":
        actual = _extract(assertion.jsonpath, step_result)
        wanted = bool(assertion.expected)
        found = actual is not _MISSING
        return found == wanted, (actual if found else None)

    actual = _extract(assertion.jsonpath, step_result)
    if actual is _MISSING:
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