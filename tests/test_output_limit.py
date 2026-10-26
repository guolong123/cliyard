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
    assert json.loads(out) == data  # no projection, no metadata


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
    assert "50000" in spec.description and "下载" in spec.description
