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


def test_view_true_output_under_2kb():
    result = {
        "items": [
            {"name": "n", "kind": "k", "size": 1, "ok": True, "at": "2026-11-11T00:00:00Z"}
            for _ in range(20)
        ],
        "total": 128,
    }
    out = _executor()._limit_result(result, "repo.list", {"view": True, "page": 1, "page_size": 10})
    assert len(out.encode()) <= 2048
    body = json.loads(out)
    assert body["view"] is True
    assert set(body["summary"]) >= {"total", "returned", "columns", "truncated"}
    assert body["_meta"]["arguments"]["view"] is True


def test_view_summary_columns_and_types():
    result = {"items": [{"name": "a", "count": 3, "ok": False, "at": "2026-11-11T00:00:00Z"}]}
    body = json.loads(_executor()._limit_result(result, "repo.list", {"view": True}))
    types = {c["name"]: c["type"] for c in body["summary"]["columns"]}
    assert types == {"name": "string", "count": "integer", "ok": "boolean", "at": "time"}


def test_view_topn_is_capped_at_five():
    result = {"items": [], "total": 0, "topN": [{"key": str(i), "count": i} for i in range(9)]}
    body = json.loads(_executor()._limit_result(result, "repo.list", {"view": True}))
    assert len(body["summary"]["topN"]) == 5


def test_view_false_keeps_data_and_meta():
    result = {"items": [{"name": "a"}], "total": 1}
    body = json.loads(_executor()._limit_result(result, "repo.list", {"view": False, "page": 1}))
    assert body["items"] == [{"name": "a"}]
    assert body["_meta"]["arguments"]["page"] == 1
    assert body["_meta"]["truncated"] is False


def test_truncated_output_has_meta_in_both_views():
    result = {"dashboards": [{"id": str(i), "xml": "y" * 4000} for i in range(20)], "total": 49}
    view_body = json.loads(_executor()._limit_result(result, "dashboard.list", {"view": True, "page": 1}))
    data_body = json.loads(_executor()._limit_result(result, "dashboard.list", {"view": False, "page": 1}))
    assert view_body["summary"]["truncated"] is True
    assert view_body["_meta"]["truncated"] is True
    assert data_body["_meta"]["truncated"] is True
    assert "notice" in view_body and "notice" in data_body
