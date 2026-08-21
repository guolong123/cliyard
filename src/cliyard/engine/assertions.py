"""Assertion engine — dual-form (structured / Jinja2 expression) evaluation.

Each assertion is either:

* a **string** (or ``{"expr": "..."}`` dict) holding a Jinja2 expression —
  truthy means pass, e.g. ``"{{ result.code == 0 }}"``;
* a **structured dict** ``{"jsonpath": "$...", "op": "...", "value": ...}`` —
  the jsonpath is applied to ``context["result"]`` and the extracted value
  is compared via *op*.

Evaluation never raises: malformed input, invalid jsonpath/regex syntax and
type-incompatible comparisons all degrade into an :class:`AssertionFailure`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jsonpath_ng
from jinja2 import ChainableUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from jsonpath_ng.exceptions import JSONPathError


@dataclass
class AssertionFailure:
    """A single failed assertion.

    Attributes:
        assertion: Original assertion text/dict as provided by the caller.
        message: Human-readable failure reason.
        op: Structured op name (``None`` for expression/malformed failures).
        expected: Expected value from the structured form.
        actual: Value extracted from the context (jsonpath) or the evaluated
            falsy result (expression form).
    """

    assertion: Any
    message: str
    op: str | None = None
    expected: Any = None
    actual: Any = None


def _expression_failure(assertion: Any, expr: str, actual: Any = None) -> AssertionFailure:
    return AssertionFailure(
        assertion=assertion,
        message=f"expression '{expr}' evaluated to falsy",
        actual=actual,
    )


def _compare_failure(
    assertion: Any, op: str | None, value: Any, actual: Any
) -> AssertionFailure:
    return AssertionFailure(
        assertion=assertion,
        message=f"expected {op} {value}, got {actual}",
        op=op,
        expected=value,
        actual=actual,
    )


def _eval_expression(
    assertion: Any, raw: str, context: dict[str, Any]
) -> AssertionFailure | None:
    """Evaluate a Jinja2 expression (same pattern as orchestrator._evaluate_expression)."""
    expr = raw.strip()
    if expr.startswith("{{") and expr.endswith("}}"):
        expr = expr[2:-2].strip()

    try:
        env = SandboxedEnvironment(undefined=ChainableUndefined)
        compiled = env.compile_expression(expr)
        result = compiled(**context)
    except TemplateSyntaxError:
        return AssertionFailure(assertion=assertion, message=f"invalid expression '{expr}'")
    except UndefinedError:
        # 缺失键经 ChainableUndefined 容错为 falsy，不抛异常
        return _expression_failure(assertion, expr)

    if result:
        return None
    return _expression_failure(assertion, expr, actual=result)


def _eval_structured(
    assertion: dict[str, Any], context: dict[str, Any]
) -> AssertionFailure | None:
    """Evaluate a ``{"jsonpath": ..., "op": ..., "value": ...}`` assertion."""
    path = assertion["jsonpath"]
    op = assertion.get("op")
    value = assertion.get("value")

    try:
        expr = jsonpath_ng.parse(path)
    except JSONPathError:
        return AssertionFailure(
            assertion=assertion, op=op, expected=value, message=f"invalid jsonpath '{path}'"
        )

    # 结构化 jsonpath 断言的作用对象固定为 context["result"]
    matches = expr.find(context.get("result"))

    if op == "exists":
        if matches:
            return None
        return AssertionFailure(
            assertion=assertion, op=op, expected=value, message=f"jsonpath '{path}' matched nothing"
        )
    if op == "not_exists":
        if not matches:
            return None
        return _compare_failure(assertion, op, value, matches[0].value)

    if not matches:
        return AssertionFailure(
            assertion=assertion, op=op, expected=value, message=f"jsonpath '{path}' matched nothing"
        )
    actual = matches[0].value

    try:
        if op == "eq":
            passed = actual == value
        elif op == "ne":
            passed = actual != value
        elif op == "gt":
            passed = actual > value
        elif op == "ge":
            passed = actual >= value
        elif op == "lt":
            passed = actual < value
        elif op == "le":
            passed = actual <= value
        elif op == "contains":
            passed = value in actual
        elif op == "not_contains":
            passed = value not in actual
        elif op == "in":
            passed = actual in value
        elif op == "not_in":
            passed = actual not in value
        elif op == "matches":
            if not isinstance(value, str):
                return AssertionFailure(
                    assertion=assertion,
                    op=op,
                    expected=value,
                    actual=actual,
                    message=f"invalid regex '{value}'",
                )
            try:
                pattern = re.compile(value)
            except re.error:
                return AssertionFailure(
                    assertion=assertion,
                    op=op,
                    expected=value,
                    actual=actual,
                    message=f"invalid regex '{value}'",
                )
            passed = pattern.search(str(actual)) is not None
        elif op == "length_eq":
            passed = len(actual) == value
        else:
            return AssertionFailure(
                assertion=assertion, op=op, expected=value, actual=actual,
                message=f"unknown op '{op}'",
            )
    except TypeError:
        # 类型不可比较（如 gt 作用于字符串）按比较失败处理
        return _compare_failure(assertion, op, value, actual)

    if passed:
        return None
    return _compare_failure(assertion, op, value, actual)


def evaluate_assertion(assertion: Any, context: dict[str, Any]) -> AssertionFailure | None:
    """Evaluate a single assertion against *context*.

    Args:
        assertion: String/Jinja2 expression, ``{"expr": ...}`` or
            ``{"jsonpath": ..., "op": ..., "value": ...}`` dict.
        context: Template variables; the structured jsonpath target is fixed
            to ``context["result"]``. Missing keys degrade to falsy.

    Returns:
        ``None`` when the assertion passes, else an :class:`AssertionFailure`.
        Never raises for malformed input.
    """
    if isinstance(assertion, str):
        return _eval_expression(assertion, assertion, context)
    if isinstance(assertion, dict):
        if "expr" in assertion:
            return _eval_expression(assertion, assertion["expr"], context)
        if "jsonpath" in assertion and "op" in assertion:
            return _eval_structured(assertion, context)
    return AssertionFailure(
        assertion=assertion, message=f"malformed assertion: {assertion!r}"
    )


def evaluate_assertions(
    assertions: Any, context: dict[str, Any]
) -> list[AssertionFailure]:
    """Evaluate a batch of assertions, isolating per-item crashes.

    Input tolerance: ``None`` is treated as an empty list; a bare ``dict``
    or ``str`` as a single-item list; any other iterable is iterated.

    Each item is evaluated inside its own guard so one crashing assertion
    becomes that item's failure (message carries the exception) instead of
    aborting the loop — hence the broad ``except Exception`` here, which is
    the only sanctioned use in this module.

    Returns:
        All failures (empty list when every assertion passes).
    """
    if assertions is None:
        return []
    items: list[Any] = [assertions] if isinstance(assertions, (dict, str)) else list(assertions)

    failures: list[AssertionFailure] = []
    for item in items:
        try:
            failure = evaluate_assertion(item, context)
        except Exception as exc:  # noqa: BLE001 — 单条断言崩溃不中断批量求值
            failure = AssertionFailure(
                assertion=item, message=f"{type(exc).__name__}: {exc}"
            )
        if failure is not None:
            failures.append(failure)
    return failures
