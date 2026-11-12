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
    out = _executor()._limit_result(_items(400, 200), _ARGS, None, "repo.list")
    body = json.loads(out)
    assert body["_meta"]["truncated"] is True
    assert body["_meta"]["arguments"] == _ARGS
    assert body["_meta"]["url"].startswith("http://example.test/")
    assert "notice" in body


def test_under_limit_has_meta_truncated_false():
    out = _executor()._limit_result(_items(174), {**_ARGS, "view": True}, None, "repo.list")
    body = json.loads(out)
    assert body["_meta"]["truncated"] is False
    assert body["_meta"]["tool"] == "repo.list"
    assert body["_meta"]["returned"] == 174


def test_unicode_and_special_arguments_are_preserved():
    args = {"keyword": '应用 日志 "quoted" & 中文', "page": 2, "page_size": 10}
    out = _executor()._limit_result(_items(5), {**args, "view": True}, None, "repo.list")
    body = json.loads(out)
    assert body["_meta"]["arguments"] == {**args, "view": True}


def test_default_output_unchanged_without_view_or_truncation():
    out = _executor()._limit_result({"ok": True}, {"page": 1}, None, "repo.list")
    assert json.loads(out) == {"ok": True}
    assert "_meta" not in json.loads(out)


def test_view_summary_is_generic_and_under_2kb():
    out = _executor()._limit_result(
        _items(500, 200), {"view": True, "page": 1}, None, "repo.list"
    )
    assert len(out.encode("utf-8")) <= 2048
    body = json.loads(out)
    assert body["view"] is True
    assert body["summary"]["returned"] == 500
    assert body["summary"]["truncated"] is True
    assert body["summary"]["columns"][0] == {"name": "name", "type": "string"}
    assert body["_meta"]["arguments"] == {"view": True, "page": 1}


def test_view_summary_accepts_generic_data_rows_results_keys():
    for key in ("data", "rows", "results"):
        out = _executor()._limit_result(
            {key: [{"id": 1, "name": "x"}], "total": 1},
            {"view": True}, None, "repo.list",
        )
        body = json.loads(out)
        assert body["summary"]["returned"] == 1
        assert [c["name"] for c in body["summary"]["columns"]] == ["id", "name"]


def test_truncated_output_keeps_legacy_toplevel_fields():
    out = _executor()._limit_result(_items(400, 200), {"page": 2}, None, "repo.list")
    body = json.loads(out)
    for key in ("truncated", "total", "returned", "limit", "url"):
        assert key in body
    assert body["truncated"] is True
    assert body["_meta"]["truncated"] is True
    assert body["_meta"]["arguments"] == {"page": 2}


def test_view_summary_stays_under_2kb_with_long_keys_and_args():
    long_key = "k" * 500
    data = {long_key: [{long_key: "v" * 500}], "total": 1}
    args = {"view": True, "q": "x" * 5000}
    out = _executor()._limit_result(data, args, None, "repo.list")
    assert len(out.encode("utf-8")) <= 2048
    json.loads(out)
