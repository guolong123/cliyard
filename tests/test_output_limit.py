from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cliyard.output.limits import (
    DEFAULT_MAX_OUTPUT_CHARS,
    build_notice,
    human_warning,
    resolve_max_chars,
    truncate_text,
    write_local_overflow,
)
from cliyard.server.mcp.executor import MCPExecutor

from tests.mcp_helpers import write_spec

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


def test_resolve_max_chars_precedence(monkeypatch):
    monkeypatch.delenv("CLIYARD_MAX_OUTPUT_CHARS", raising=False)
    assert resolve_max_chars() == DEFAULT_MAX_OUTPUT_CHARS
    assert resolve_max_chars(123) == 123
    monkeypatch.setenv("CLIYARD_MAX_OUTPUT_CHARS", "77")
    assert resolve_max_chars() == 77
    assert resolve_max_chars(5) == 5


def test_truncate_below_and_equal_limit_unchanged():
    text = json.dumps({"items": [1, 2, 3]})
    out, notice = truncate_text(text, len(text))
    assert out == text and notice is None
    out, notice = truncate_text(text, len(text) + 10)
    assert out == text and notice is None


def test_truncate_above_limit_is_json_parseable():
    text = json.dumps({"blob": "x" * 5000})
    out, notice = truncate_text(text, 100, file="/tmp/f.json")
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert parsed["original_chars"] == len(text)
    assert parsed["limit"] == 100
    assert parsed["file"] == "/tmp/f.json"
    assert notice is not None and "⚠" in human_warning(notice)


def test_write_local_overflow(tmp_path):
    text = "payload"
    path = write_local_overflow(text, str(tmp_path))
    assert os.path.isfile(path)
    assert Path(path).read_text(encoding="utf-8") == text


def test_executor_truncation_returns_structured_object(tmp_path):
    """Over-limit non-paginated output is one valid JSON object — no string + ⚠ tail."""
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    text = ex._limit_result({"blob": "y" * 60000})
    parsed = json.loads(text)  # whole payload must parse
    assert parsed["truncated"] is True
    assert parsed["reason"] == "output_truncated"
    assert parsed["bytes"] > 10000
    assert parsed["chars"] > 10000
    assert parsed["limit"] > 0
    assert parsed["url"].startswith("http://h:1/outputs/")
    assert "page_size" in parsed["hint"] or "下载" in parsed["hint"]
    # full field structure is present, values are only type names
    assert parsed["schema"]["blob"] == "string"
    assert "⚠" not in text
    ident = parsed["url"].split("/outputs/", 1)[1].split("?", 1)[0]
    assert os.path.isfile(os.path.join(str(tmp_path), ident))


def test_executor_no_truncation_below_limit(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    text = ex._limit_result({"ok": True})
    assert json.loads(text) == {"ok": True}


def _fake_request(upload_dir, ident, token=None, headers=None):
    state = SimpleNamespace(upload_dir=str(upload_dir), upload_token=token)
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        path_params={"id": ident},
        headers=headers or {},
        query_params={},
    )


def test_cli_truncates_json_and_points_to_local_file(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from cliyard.runtime import create_cli

    spec = write_spec(tmp_path, "http://127.0.0.1:1")
    monkeypatch.setattr(
        "cliyard.engine.builder.execute_pipeline",
        lambda *args, **kwargs: {
            "items": [{"name": "x" * 30000}],
            "total": 1,
            "fields": [{"name": "name", "alias": "name"}],
        },
    )
    monkeypatch.setenv("CLIYARD_MAX_OUTPUT_CHARS", "200")
    cli = create_cli(str(spec))
    result = CliRunner().invoke(cli, ["repos", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output.split("\n⚠", 1)[0])
    assert data["truncated"] is True
    assert data["original_chars"] > 200
    assert os.path.isfile(data["file"])
    assert "⚠" in result.output


def test_outputs_endpoint_serves_file_and_404(tmp_path):
    from cliyard.server.api.outputs import mcp_output_endpoint

    (tmp_path / "cliyard-upload-abcd1234-output.json").write_text("{}", encoding="utf-8")
    resp = asyncio.run(
        mcp_output_endpoint(_fake_request(tmp_path, "cliyard-upload-abcd1234-output.json"))
    )
    assert resp.__class__.__name__ == "FileResponse"
    missing = asyncio.run(mcp_output_endpoint(_fake_request(tmp_path, "nope.json")))
    assert missing.status_code == 404
    traversal = asyncio.run(mcp_output_endpoint(_fake_request(tmp_path, "../etc/passwd")))
    assert traversal.status_code in (400, 404)


def test_outputs_endpoint_auth(tmp_path):
    from cliyard.server.api.outputs import mcp_output_endpoint

    (tmp_path / "f.json").write_text("{}", encoding="utf-8")
    noauth = asyncio.run(mcp_output_endpoint(_fake_request(tmp_path, "f.json", token="secret")))
    assert noauth.status_code == 401
    ok = asyncio.run(
        mcp_output_endpoint(
            _fake_request(
                tmp_path,
                "f.json",
                token="secret",
                headers={"authorization": "Bearer secret"},
            )
        )
    )
    assert ok.__class__.__name__ == "FileResponse"


def test_paginate_slices_only_with_size():
    from cliyard.output.pagination import paginate

    spec = {"page_param": "page", "size_param": "page_size"}
    data = {"items": list(range(10)), "total": 10}
    assert paginate(data, spec, {"page_size": None}) == data
    out = paginate(data, spec, {"page": 2, "page_size": 4})
    assert out["items"] == [4, 5, 6, 7]
    assert out["total"] == 10 and out["page"] == 2 and out["page_size"] == 4


def test_summarize_groups_and_distribution():
    from cliyard.output.pagination import summarize

    data = {"items": [
        {"index": "a", "state": "STARTED"},
        {"index": "a", "state": "STARTED"},
        {"index": "b", "state": "RELOCATING"},
    ]}
    out = summarize(data, {"group_by": ["index"], "health_field": "state"})
    assert out["summary"]["total"] == 3
    assert out["groups"]["index"] == {"a": 2, "b": 1}
    assert out["summary"]["distribution"] == {"STARTED": 2, "RELOCATING": 1}


def test_output_token_valid_expired_tampered(monkeypatch):
    from cliyard.server.api.output_tokens import sign_output_token, verify_output_token

    monkeypatch.setenv("KETACLI_MCP_TOKEN", "abc")
    good = sign_output_token("file.json", 300)
    assert verify_output_token("file.json", good) is True
    expired = sign_output_token("file.json", -5)
    assert verify_output_token("file.json", expired) is False
    expiry, _, sig = good.partition(".")
    assert verify_output_token("file.json", f"{expiry}.{'0' * len(sig)}") is False
    assert verify_output_token("other.json", good) is False


def test_outputs_endpoint_query_token(tmp_path):
    import os

    from cliyard.server.api.output_tokens import sign_output_token
    from cliyard.server.api.outputs import mcp_output_endpoint

    (tmp_path / "f.json").write_text("{}", encoding="utf-8")
    good = sign_output_token("f.json")
    req = _fake_request(tmp_path, "f.json", token="secret")
    req.query_params = {"t": good}
    assert asyncio.run(mcp_output_endpoint(req)).__class__.__name__ == "FileResponse"
    req_bad = _fake_request(tmp_path, "f.json", token="secret")
    req_bad.query_params = {"t": "1.deadbeef"}
    assert asyncio.run(mcp_output_endpoint(req_bad)).status_code == 401
    req_header = _fake_request(
        tmp_path, "f.json", token="secret", headers={"authorization": "Bearer secret"}
    )
    req_header.query_params = {}
    assert asyncio.run(mcp_output_endpoint(req_header)).__class__.__name__ == "FileResponse"


def test_local_overflow_sweep(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("CLIYARD_OUTPUT_TTL_S", "1")
    old = tmp_path / "cliyard-output-old00000.json"
    old.write_text("x", encoding="utf-8")
    past = time.time() - 3600
    os.utime(old, (past, past))
    new = write_local_overflow("fresh", str(tmp_path))
    assert not old.exists()
    assert os.path.isfile(new)


def test_outputs_endpoint_sweeps_expired_upload(tmp_path):
    import os
    import time

    from cliyard.server.api.outputs import mcp_output_endpoint

    stale = tmp_path / "cliyard-upload-deadbeef-output.json"
    stale.write_text("{}", encoding="utf-8")
    past = time.time() - 7200
    os.utime(stale, (past, past))
    asyncio.run(mcp_output_endpoint(_fake_request(tmp_path, "cliyard-upload-deadbeef-output.json")))
    assert not stale.exists()


def test_infer_schema_merges_item_keys_and_types():
    from cliyard.output.limits import infer_schema

    schema = infer_schema({
        "items": [
            {"name": "a", "type": "EVENTS", "size": 1},
            {"name": "b", "app": "search", "flag": True},
        ],
        "total": 2,
    })
    item = schema["items"][0]
    assert item["name"] == "string"
    assert item["type"] == "string"
    assert item["size"] == "integer"
    assert item["app"] == "string"
    assert item["flag"] == "boolean"
    assert schema["total"] == "integer"


def test_project_fields_and_brief():
    from cliyard.output.pagination import project

    data = {
        "items": [
            {"name": "a", "type": "EVENTS", "app": "search", "bytes": 10, "description": "x"},
            {"name": "b", "type": "METRICS", "app": "search", "bytes": 20, "description": "y"},
        ],
        "total": 2,
    }
    spec = {
        "fields_param": "fields",
        "brief_param": "brief",
        "allowed_fields": ["name", "type", "app", "bytes", "description"],
        "brief_fields": ["name", "type", "app"],
    }
    out = project(data, spec, {"fields": "name,type"})
    assert out["items"] == [{"name": "a", "type": "EVENTS"}, {"name": "b", "type": "METRICS"}]
    assert out["total"] == 2
    assert out["projection"] == {"fields": ["name", "type"], "brief": False}

    brief = project(data, spec, {"brief": True})
    assert brief["items"][0] == {"name": "a", "type": "EVENTS", "app": "search"}
    assert brief["projection"]["brief"] is True

    as_list = project(data, spec, {"fields": ["app", "nope"]})
    assert as_list["items"][0] == {"app": "search"}
    assert as_list["projection"]["ignored"] == ["nope"]

    assert project(data, spec, {}) is data


def test_project_keeps_items_list_and_total_int():
    from cliyard.output.pagination import project

    spec = {"allowed_fields": ["name"], "brief_fields": ["name"]}
    out = project({"items": None, "total": None}, spec, {"brief": True})
    assert out["items"] == []
    assert out["total"] == 0


def test_local_params_exposed_in_tool_schema():
    """local-only switches (fields/brief) must reach the MCP tool schema."""
    from cliyard.server.schema_bridge import params_to_json_schema

    schema = params_to_json_schema({
        "query": [{"name": "page_size", "type": "int"}],
        "local": [{"name": "fields", "type": "string"}, {"name": "brief", "type": "bool", "default": False}],
    })
    assert "fields" in schema["properties"] and "brief" in schema["properties"]
    assert schema["properties"]["brief"]["type"] == "boolean"
    assert schema["properties"]["fields"]["x-location"] == "local"
    assert "fields" not in schema["required"]


def _repos(n, pad=700):
    return {"items": [{"id": i, "name": f"r{i}", "type": "EVENTS", "app": "search", "blob": "x" * pad} for i in range(n)],
            "total": n}


def test_unified_projection_below_limit_is_unchanged(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"id": 1, "name": "a", "type": "T", "blob": "x" * 10}], "total": 1}
    out = ex._limit_result(data, {"page_size": 1}, {"items_path": "$.items"})
    parsed = json.loads(out)
    assert parsed["items"] == data["items"] and parsed["total"] == 1
    assert "projection" not in parsed and parsed["status"] == "ok"  # no projection, status marker only


def test_unified_projection_explicit_fields_below_limit(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"id": 1, "name": "a", "type": "T", "blob": "x" * 10}], "total": 1}
    out = ex._limit_result(data, {"fields": "id,name"}, {"items_path": "$.items"})
    parsed = json.loads(out)
    assert parsed["items"] == [{"id": 1, "name": "a"}]
    assert parsed["projection"] == {"fields": ["id", "name"], "brief": False}
    assert parsed["total"] == 1


def test_unified_projection_above_limit_auto_brief_with_url(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = _repos(40, pad=2000)  # ~80KB > 50000: auto-brief must kick in
    out = ex._limit_result(data, {"page_size": 40}, {"items_path": "$.items"})
    parsed = json.loads(out)  # whole payload is valid JSON object
    assert parsed["truncated"] is True and parsed["projected"] is True
    assert isinstance(parsed["items"], list) and parsed["items"]
    assert parsed["returned"] == len(parsed["items"]) <= parsed["total"] == 40
    assert parsed["url"].startswith("http://h:1/outputs/")
    assert "fields" in parsed["items"][0] or "id" in parsed["items"][0]
    assert "blob" not in parsed["items"][0]  # auto brief dropped the big field
    assert parsed["schema"]["items"][0]["blob"] == "string"
    assert "fields" in parsed["hint"] or "brief" in parsed["hint"]


def test_unified_projection_respects_spec_brief_fields(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    spec = {"items_path": "$.items", "project": {"brief_fields": ["name", "type"]}}
    out = ex._limit_result(_repos(40, pad=2000), {"page_size": 40}, spec)
    parsed = json.loads(out)
    assert parsed["projection"]["fields"] == ["name", "type"]
    assert set(parsed["items"][0]) == {"name", "type"}


def test_non_paginated_over_limit_has_no_projection_hint(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    out = ex._limit_result({"blob": "y" * 60000}, {}, None)
    parsed = json.loads(out)
    assert parsed["truncated"] is True
    assert "fields" not in parsed["hint"] and "brief" not in parsed["hint"]
    assert "page_size" in parsed["hint"] or "下载" in parsed["hint"]


def test_text_stream_result_is_not_projected(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    assert ex._limit_result("plain text", {}, None) == "plain text"


def test_paginated_tool_schema_injects_unified_fields_and_brief():
    from cliyard.server.schema_bridge import params_to_json_schema

    schema = params_to_json_schema(
        {"query": [{"name": "page", "type": "int"}]},
        output_spec={"items_path": "$.items"},
    )
    assert schema["properties"]["fields"]["x-location"] == "local"
    assert schema["properties"]["brief"]["type"] == "boolean"
    plain = params_to_json_schema({"query": [{"name": "page", "type": "int"}]}, output_spec=None)
    assert "fields" not in plain["properties"] and "brief" not in plain["properties"]


def test_paginated_tool_description_uses_uniform_wording():
    from cliyard.server.mcp.tools import _command_spec

    spec = _command_spec(
        "dashboard.list",
        {"desc": "仪表盘列表", "method": "GET", "path": "dashboard", "schema": {}, "output": {"items_path": "$.dashboards"}},
        "仪表盘",
        "",
    )
    assert "fields" in spec.description and "brief" in spec.description
    assert "单次输出上限" in spec.description and "下载" in spec.description


def test_discover_call_nested_arguments_apply_fields(tmp_path):
    """catalog ``call`` nests target args; fields/brief must still apply."""
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"id": 1, "name": "a", "blob": "x" * 10}], "total": 1}
    out = ex._limit_result(
        data,
        {"name": "dashboard.list", "arguments": {"fields": "name"}},
        {"items_path": "$.items"},
    )
    parsed = json.loads(out)
    assert parsed["items"] == [{"name": "a"}]
    assert parsed["projection"]["fields"] == ["name"]
def test_command_envelope_structured_shapes():
    from cliyard.server.mcp.executor import command_envelope

    empty = command_envelope("", "")
    assert empty == {"status": "empty", "items": [], "total": 0}

    rows = command_envelope(json.dumps([{"id": 1}]), "")
    assert rows["status"] == "ok" and rows["total"] == 1 and rows["items"][0]["id"] == 1

    page = command_envelope(json.dumps({"items": [{"id": 1}], "total": 9}), "")
    assert page["total"] == 9 and page["status"] == "ok"

    single = command_envelope(json.dumps({"id": "d1", "chartCount": 2}), "")
    assert single == {"status": "ok", "items": [{"id": "d1", "chartCount": 2}], "total": 1}

    warn = command_envelope(json.dumps({"id": "d1"}), "deprecated flag")
    assert warn["warn"] == "deprecated flag" and warn["status"] == "ok"

    # Fail-closed style payloads must stay errors (never downgraded to empty).
    err = command_envelope(
        json.dumps({"status": "error", "items": [], "total": 0, "warn": "missing _session_id"}),
        "",
    )
    assert err["status"] == "error" and err["warn"] == "missing _session_id"


def test_command_envelope_text_kind_is_declared_not_hardcoded():
    from cliyard.server.mcp.executor import command_envelope

    assert command_envelope("skill-a\nskill-b", "", "text") == "skill-a\nskill-b"
    assert "stderr" in command_envelope("body", "warn", "text")
    assert command_envelope("", "", "text") == "(no output)"
    # Same content with the default json kind becomes a structured envelope.
    assert command_envelope("skill-a", "", "json")["status"] == "ok"


def test_add_total_pages():
    from cliyard.output.pagination import add_total_pages

    spec = {"size_param": "per_page", "default_size": 20}
    out = add_total_pages({"items": [1, 2], "total": 41}, spec, {"per_page": 20})
    assert out["totalPages"] == 3
    assert out["total"] == 41 and out["items"] == [1, 2]
    assert add_total_pages({"items": [], "total": 5}, spec, {"per_page": 0})["totalPages"] == 1


def test_paginated_result_gets_status_marker(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    ok = json.loads(ex._limit_result({"items": [{"id": 1}], "total": 3}))
    assert ok["status"] == "ok" and ok["total"] == 3
    empty = json.loads(ex._limit_result({"items": [], "total": 0}))
    assert empty == {"items": [], "total": 0, "status": "empty"}
    # total missing -> derived from items
    derived = json.loads(ex._limit_result({"items": [{"id": 1}, {"id": 2}]}))
    assert derived["total"] == 2 and derived["status"] == "ok"


def test_scalar_list_over_limit_keeps_items(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [f"scalar-{i}-" + "x" * 80 for i in range(2000)], "total": 2000}
    parsed = json.loads(ex._limit_result(data, {}, {"items_path": "$.items"}))
    assert parsed["truncated"] is True
    assert parsed["returned"] > 0
    assert len(parsed["items"]) == parsed["returned"]
    assert parsed["items"][0].startswith("scalar-0-")
    assert parsed["projection_error"] == "scalar_list_cannot_project"
    assert parsed["url"].startswith("http://h:1/outputs/")


def test_all_nested_objects_not_projected_to_empty(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"nested": {"deep": ["y" * 100]}} for _ in range(2000)], "total": 2000}
    parsed = json.loads(ex._limit_result(data, {}, {"items_path": "$.items"}))
    assert parsed["truncated"] is True
    assert parsed["returned"] > 0
    assert parsed["items"][0] == {"nested": {"deep": ["y" * 100]}}
    assert parsed["projection_error"] == "all_items_are_nested_objects"
    assert parsed["projected"] is False


def test_projection_can_be_disabled_by_spec(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"id": i, "blob": "z" * 5000} for i in range(20)], "total": 20}
    for flag in ({"unified_projection": False}, {"projection": False}):
        spec = {"items_path": "$.items", **flag}
        parsed = json.loads(ex._limit_result(data, {"fields": "id"}, spec))
        assert "projection" not in parsed
        # Projection disabled: over-limit falls back to the structured truncation
        # notice (items is the count, full data via url) instead of projecting.
        assert parsed["truncated"] is True
        assert parsed["items"] == 20
        assert parsed["url"].startswith("http://h:1/outputs/")


def test_tiny_limit_never_overshoots(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    data = {"items": [{"id": i, "blob": "q" * 500} for i in range(50)], "total": 50}
    for limit in (100, 500, 1200, 1535):
        text = ex._limit_result(data, {}, {"items_path": "$.items"})
        # default limit applies here; call fit_items directly for the tiny-limit contract
        from cliyard.output.projection import fit_items

        payload, _ = fit_items(data, limit, base_meta={"truncated": True, "limit": limit})
        assert len(json.dumps(payload, ensure_ascii=False).encode()) <= max(limit, 2)


def test_infer_schema_redacts_map_keys():
    from cliyard.output.limits import infer_schema

    schema = infer_schema({"alice@example.com": {"role": "admin"}, "bob@example.com": {}})
    dumped = json.dumps(schema, ensure_ascii=False)
    assert "alice@example.com" not in dumped and "bob@example.com" not in dumped
    assert "<map-key>" in dumped
    record = infer_schema({"uid": "1", "name": "x"})
    assert record["uid"] == "string" and record["name"] == "string"


def test_total_pages_wired_through_transforms():
    from cliyard.output.pagination import apply_output_transforms

    data = {"items": [{"id": 1}], "total": 41}
    out = apply_output_transforms(
        data, {"items_path": "$.x", "total_pages": {"size_param": "per_page"}}, {"per_page": 20}
    )
    assert out["totalPages"] == 3
