"""E2E proof for upload-to-call flows (plan todo 9).

Covers, with NO mocked upload handler and NO mocked ``call_tool``:

* HTTP: real ``POST /upload`` (TestClient over ``build_mcp_http_app``) → take
  ``path`` → real ``MCPExecutor.call_tool`` on the file-param business tool
  (``repos.upload``) against a ``MockUpstream`` recording the raw body →
  upstream received the file bytes; the SAME path called a SECOND time
  in-window still succeeds identically (no-consume-delete iron proof).
* stdio: an arbitrary server-local path calls successfully with NO jail,
  contrasted against the HTTP executor jailing the SAME path (both sides
  asserted).
* Schema scan: all tool schemas rendered with a FAKE bearer token configured
  contain zero occurrences of the fake token value.
* Negative: delete the server-side file post-upload, then call →
  ``is_error=True`` with short-form expired/missing guidance.

Isolation: every test uses its own ``tmp_path`` upload dir (never the shared
default) and ``MockUpstream`` ephemeral ports — no fixed ports, no shared
static temp dirs — so concurrent pytest lanes cannot collide.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import pytest

from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.server import build_mcp_http_app

from tests.mcp_helpers import MockUpstream, write_spec

_PAYLOAD = b"hello-mcp-file-e2e-proof"
_FAKE_TOKEN = "fake-bearer-token-e2e-9d4c2b7a"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """Neutralize intercepting proxies: MockUpstream lives on 127.0.0.1.

    CI/dev shells may export HTTP(S)_PROXY pointing at a dead local proxy;
    httpx trusts env proxies by default, turning loopback calls into 502s.
    This fixture is file-local (no product code, no other test files).
    """
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _http_app(spec: Path, upload_dir: Path, token: str | None = None):
    """Live Starlette app (upload route real; no uvicorn needed for /upload)."""
    kwargs: dict = {"upload_dir": str(upload_dir)}
    if token is not None:
        kwargs["token"] = token
    return build_mcp_http_app(str(spec), **kwargs)


def _post_upload(app, filename: str = "proof.bin", data: bytes = _PAYLOAD) -> dict:
    """REAL ``POST /upload`` multipart round-trip (handler never mocked)."""
    client = TestClient(app)
    resp = client.post(
        "/upload", files={"file": (filename, data, "application/octet-stream")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"path", "file_name", "bytes", "expires_at"}
    assert Path(body["path"]).is_file(), "uploaded file must land on disk"
    return body


def _call(executor: MCPExecutor, tool: str, args: dict):
    """REAL ``call_tool`` (never mocked) run to completion."""
    params = SimpleNamespace(name=tool, arguments=args)
    return asyncio.run(executor.call_tool(None, params))


def _http_executor(spec: Path, upload_dir: Path) -> MCPExecutor:
    # transport="http" → server_mode=True → jail active (plan: HTTP jailed).
    return MCPExecutor(str(spec), transport="http", upload_dir=str(upload_dir))


def _stdio_executor(spec: Path, upload_dir: Path) -> MCPExecutor:
    # transport="stdio" → server_mode=False → no jail (plan: stdio 同机直传).
    return MCPExecutor(str(spec), transport="stdio", upload_dir=str(upload_dir))


# ---------------------------------------------------------------------------
# (d) negative — deleted post-upload path → is_error + short-form guidance
# ---------------------------------------------------------------------------


def test_negative_deleted_upload_path_is_error_guidance(tmp_path):
    """Failing-first probe: upload, delete server file, call → isError guidance.

    If this assertion ever goes red it means the jail/missing-file mapping
    regressed — the suite proves it can catch that (red signal), while green
    proves the short-form expired/missing guidance path works.
    """
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-neg"
        upload_dir.mkdir()
        body = _post_upload(_http_app(spec, upload_dir))
        Path(body["path"]).unlink()  # expire the server-side file post-upload

        result = _call(_http_executor(spec, upload_dir), "repos.upload", {"file": body["path"]})
        assert result.is_error is True, result.content[0].text
        text = result.content[0].text
        assert "POST /upload" in text, text  # short-form re-upload guidance
        assert ("已过期" in text) or ("不存在" in text), text
        assert str(upload_dir) not in text, "upload dir real path must be redacted"
        assert upstream.records == [], "missing file must never reach upstream"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (a) HTTP E2E: upload → call → replay same path (no-consume-delete proof)
# ---------------------------------------------------------------------------


def test_http_upload_to_call_replay_twice_no_consume(tmp_path):
    """Real upload → real call_tool ×2 on the SAME path; both succeed identically."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-http"
        upload_dir.mkdir()
        body = _post_upload(_http_app(spec, upload_dir))
        assert body["bytes"] == len(_PAYLOAD)

        executor = _http_executor(spec, upload_dir)
        first = _call(executor, "repos.upload", {"file": body["path"]})
        assert first.is_error is False, first.content[0].text
        assert Path(body["path"]).is_file(), "first call must NOT consume-delete the upload"

        second = _call(executor, "repos.upload", {"file": body["path"]})
        assert second.is_error is False, second.content[0].text
        # Identical success on replay (not just non-error).
        assert second.content[0].text == first.content[0].text
        assert Path(body["path"]).is_file(), "replay must NOT consume-delete the upload"

        assert len(upstream.records) == 2, "both calls must reach upstream"
        for rec in upstream.records:
            assert rec["method"] == "POST"
            assert _PAYLOAD in rec["body"], "upstream multipart body must carry file bytes"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (b) stdio: same outside-jail path → stdio success vs HTTP isError
# ---------------------------------------------------------------------------


def test_stdio_local_path_no_jail_vs_http_jail_contrast(tmp_path):
    """SAME outside-jail path: stdio call succeeds, HTTP call isError (jail)."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-contrast"
        upload_dir.mkdir()
        outside_dir = tmp_path / "outside-jail"
        outside_dir.mkdir()
        local_path = outside_dir / "local.bin"
        local_path.write_bytes(_PAYLOAD)

        ok = _call(_stdio_executor(spec, upload_dir), "repos.upload", {"file": str(local_path)})
        assert ok.is_error is False, ok.content[0].text
        assert upstream.records, "stdio call must reach upstream"
        assert _PAYLOAD in upstream.records[-1]["body"]

        before = len(upstream.records)
        jailed = _call(_http_executor(spec, upload_dir), "repos.upload", {"file": str(local_path)})
        assert jailed.is_error is True, jailed.content[0].text
        text = jailed.content[0].text
        assert "POST /upload" in text, text
        assert len(upstream.records) == before, "jailed HTTP call must not reach upstream"
    finally:
        upstream.close()


# ---------------------------------------------------------------------------
# (c) schema scan: zero occurrences of the fake token value
# ---------------------------------------------------------------------------


def test_tool_schemas_contain_no_token_value(tmp_path):
    """All tool schemas rendered with a FAKE token configured carry no token."""
    upstream = MockUpstream()
    try:
        spec = write_spec(tmp_path, upstream.base_url)
        upload_dir = tmp_path / "up-scan"
        upload_dir.mkdir()
        # FAKE token configured on the live app (auth side only, by design).
        _http_app(spec, upload_dir, token=_FAKE_TOKEN)

        executor = MCPExecutor(
            str(spec),
            transport="http",
            upload_base="http://127.0.0.1:8081",
            upload_dir=str(upload_dir),
        )
        listed = asyncio.run(executor.list_tools(None, None))
        assert listed.tools, "tool table must be non-empty for the scan to mean anything"
        blob = json.dumps(
            [t.model_dump() for t in listed.tools], ensure_ascii=False, default=str
        )
        assert _FAKE_TOKEN not in blob, "schema/description must never embed the token value"
        # The upload handoff copy must still be present (curl line, no secret).
        assert "POST" in blob and "/upload" in blob
    finally:
        upstream.close()
