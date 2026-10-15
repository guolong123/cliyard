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


def test_executor_truncation_writes_overflow_and_url(tmp_path):
    ex = MCPExecutor(_FIXTURES, upload_dir=str(tmp_path), output_base="http://h:1")
    text = ex._limit_result({"blob": "y" * 50000})
    head = text.split("\n{", 1)[0] if False else text[: text.rindex("}") + 1]
    parsed = json.loads(head)
    assert parsed["truncated"] is True
    assert parsed["original_chars"] > 10000
    assert parsed["url"].startswith("http://h:1/outputs/")
    assert "⚠" in text
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
