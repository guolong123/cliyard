"""Generic truncation envelope tests (no product semantics)."""
from __future__ import annotations

import json
from pathlib import Path

from cliyard.server.mcp.executor import MCPExecutor

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spec-dir"
_ARGS = {"page": 1, "page_size": 20, "keyword": '应用 日志 "quoted" & 中文'}


def _executor() -> MCPExecutor:
    return MCPExecutor(str(_FIXTURES), output_base="http://example.test")


def _items(n: int, payload: int = 180) -> dict:
    return {
        "items": [{"name": f"item{i}", "payload": "y" * payload} for i in range(n)],
        "total": n,
    }


def test_over_limit_keeps_meta_arguments_and_url():
    out = _executor()._limit_result(_items(400, 200), "repo.list", _ARGS)
    body = json.loads(out)
    assert body["_meta"]["truncated"] is True
    assert body["_meta"]["arguments"] == _ARGS
    assert body["_meta"]["url"].startswith("http://example.test/")
    assert "notice" in body


def test_under_limit_has_meta_truncated_false():
    out = _executor()._limit_result(_items(174), "repo.list", _ARGS)
    body = json.loads(out)
    assert body["_meta"]["truncated"] is False
    assert body["_meta"]["tool"] == "repo.list"
    assert body["_meta"]["returned"] == 174


def test_unicode_and_special_arguments_are_preserved():
    args = {"keyword": '应用 日志 "quoted" & 中文', "page": 2, "page_size": 10}
    out = _executor()._limit_result(_items(5), "repo.list", args)
    body = json.loads(out)
    assert body["_meta"]["arguments"] == args
