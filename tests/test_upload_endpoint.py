"""Endpoint tests for ``POST /upload`` (plan todo 7).

Covers the REAL routes over TestClient with real multipart bodies (no mocked
handler):

* serve side: ``POST /api/upload`` (``create_app`` + ``verify_upload_token``)
* MCP side: ``POST /upload`` (``build_mcp_http_app`` + ``_StaticTokenVerifier``)

Cases: 200 (fields ``path/file_name/bytes/expires_at``, file on disk under the
configured dir, server name ``cliyard-upload-*``), 400 (missing/empty part),
401 (bad token; plus no-token loopback-allowed), 413 (over-limit body +
quota-full via small-quota monkeypatch — never writes large files), filename
traversal (``../../x``, absolute, backslash) harmless-ized on disk.

Product code is NOT touched here: if any test fails against current code it is
reported as a product bug instead of being fixed in this file.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cliyard.server import uploads as uploads_mod
from cliyard.server.api import upload as upload_mod
from cliyard.server.app import create_app
from cliyard.server.mcp.server import build_mcp_http_app

_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"

_TOKEN = "test-upload-token"


def _serve_client(upload_dir: Path, token: str | None = _TOKEN) -> TestClient:
    """TestClient over the serve app with an isolated upload dir."""
    return TestClient(create_app(str(_FIXTURES_SPEC), token=token, upload_dir=str(upload_dir)))


def _auth_headers(token: str = _TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(
    client: TestClient,
    path: str,
    filename: str = "hello.txt",
    data: bytes = b"hello upload",
    token: str | None = _TOKEN,
) -> object:
    headers = _auth_headers(token) if token is not None else {}
    return client.post(path, files={"file": (filename, data, "text/plain")}, headers=headers)


# ===========================================================================
# 200 — response fields, on-disk presence, server naming (failing-first probe)
# ===========================================================================


def test_upload_200_returns_fields_and_lands_on_disk(tmp_path):
    """Failing-first probe: must pass against current code.

    If this fails, it is a product bug in ``POST /api/upload`` — report it
    loudly, do NOT fix product code from this lane.
    """
    upload_dir = tmp_path / "up"
    upload_dir.mkdir()
    client = _serve_client(upload_dir)

    payload = b"hello upload \xe4\xb8\xad\xe6\x96\x87"
    resp = _upload(client, "/api/upload", filename="hello.txt", data=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) >= {"path", "file_name", "bytes", "expires_at"}
    assert body["bytes"] == len(payload)
    assert body["file_name"].startswith("cliyard-upload-")

    resolved_dir = os.path.realpath(str(upload_dir))
    assert os.path.realpath(body["path"]).startswith(resolved_dir + os.sep)
    assert os.path.isfile(body["path"])
    with open(body["path"], "rb") as f:
        assert f.read() == payload


def test_upload_200_expires_at_is_tz_aware_iso8601(tmp_path):
    upload_dir = tmp_path / "up"
    upload_dir.mkdir()
    client = _serve_client(upload_dir)

    body = _upload(client, "/api/upload").json()
    parsed = datetime.fromisoformat(body["expires_at"])
    assert parsed.tzinfo is not None


# ===========================================================================
# 400 — missing / empty part
# ===========================================================================


def test_upload_400_when_no_file_part(tmp_path):
    client = _serve_client(tmp_path / "up")
    resp = client.post("/api/upload", headers=_auth_headers())
    assert resp.status_code == 400


def test_upload_400_when_empty_file(tmp_path):
    client = _serve_client(tmp_path / "up")
    resp = _upload(client, "/api/upload", filename="empty.txt", data=b"")
    assert resp.status_code == 400


def test_upload_400_when_wrong_field_name(tmp_path):
    client = _serve_client(tmp_path / "up")
    resp = client.post(
        "/api/upload",
        files={"notfile": ("a.txt", b"data", "text/plain")},
        headers=_auth_headers(),
    )
    assert resp.status_code == 400


# ===========================================================================
# 401 — bad / missing token; no-token loopback allowed
# ===========================================================================


def test_upload_401_on_bad_token(tmp_path):
    client = _serve_client(tmp_path / "up", token=_TOKEN)
    resp = _upload(client, "/api/upload", token="wrong-token")
    assert resp.status_code == 401
    assert "detail" in resp.json()


def test_upload_401_on_missing_token_when_configured(tmp_path):
    client = _serve_client(tmp_path / "up", token=_TOKEN)
    resp = client.post(
        "/api/upload", files={"file": ("a.txt", b"data", "text/plain")}
    )
    assert resp.status_code == 401


def test_upload_200_without_token_when_unset_loopback(tmp_path):
    """No token configured → loopback dev mode, no auth (mirrors is_local_host)."""
    upload_dir = tmp_path / "up"
    upload_dir.mkdir()
    client = _serve_client(upload_dir, token=None)
    resp = client.post(
        "/api/upload", files={"file": ("a.txt", b"data", "text/plain")}
    )
    assert resp.status_code == 200, resp.text
    assert os.path.isfile(resp.json()["path"])


# ===========================================================================
# 413 — over-limit body + quota-full (both via monkeypatch, no big writes)
# ===========================================================================


def test_upload_413_when_body_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_mod, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(uploads_mod, "MAX_UPLOAD_BYTES", 16)
    client = _serve_client(tmp_path / "up")
    resp = _upload(client, "/api/upload", data=b"x" * 17)
    assert resp.status_code == 413
    assert "detail" in resp.json()


def test_upload_quota_full_evicts_oldest_via_tiny_quota(tmp_path, monkeypatch):
    """Quota-full path with a tiny monkeypatched quota — never writes 500MB."""
    upload_dir = tmp_path / "up"
    upload_dir.mkdir()
    seed = uploads_mod.save_upload("seed.txt", b"0123456789", str(upload_dir))
    assert os.path.isfile(seed["path"])

    monkeypatch.setattr(uploads_mod, "UPLOAD_QUOTA_BYTES", 5)
    client = _serve_client(upload_dir)

    resp = _upload(client, "/api/upload", filename="fresh.txt", data=b"abc")
    assert resp.status_code == 200, resp.text
    # sweep (搭车执行) evicted the 10-byte seed to honor the 5-byte quota.
    assert not os.path.exists(seed["path"])
    assert os.path.isfile(resp.json()["path"])


# ===========================================================================
# Filename traversal — harmless-ized on disk
# ===========================================================================


@pytest.mark.parametrize(
    "evil_name",
    ["../../x", "../traversal.txt", "/abs/path/evil.txt", "..\\..\\win.txt"],
)
def test_upload_traversal_filename_is_harmless(tmp_path, evil_name):
    upload_dir = tmp_path / "up"
    upload_dir.mkdir()
    client = _serve_client(upload_dir)

    payload = b"traversal probe"
    resp = _upload(client, "/api/upload", filename=evil_name, data=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    server_name = body["file_name"]
    assert server_name.startswith("cliyard-upload-")
    assert ".." not in server_name
    assert "/" not in server_name
    assert "\\" not in server_name

    resolved_dir = os.path.realpath(str(upload_dir))
    assert os.path.dirname(os.path.realpath(body["path"])) == resolved_dir
    with open(body["path"], "rb") as f:
        assert f.read() == payload

    # Nothing escaped next to the upload dir.
    assert not (upload_dir.parent / "x").exists()


# ===========================================================================
# MCP side — POST /upload on build_mcp_http_app (TestClient, real route)
# ===========================================================================


def _mcp_client(upload_dir: Path, token: str | None = _TOKEN) -> TestClient:
    app = build_mcp_http_app(
        str(_FIXTURES_SPEC), token=token, upload_dir=str(upload_dir)
    )
    return TestClient(app)


def test_mcp_upload_200_and_lands_on_disk(tmp_path):
    upload_dir = tmp_path / "mcp-up"
    upload_dir.mkdir()
    with _mcp_client(upload_dir) as client:
        resp = _upload(client, "/upload", filename="mcp.txt", data=b"mcp bytes")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {"path", "file_name", "bytes", "expires_at"}
        assert body["file_name"].startswith("cliyard-upload-")
        assert body["bytes"] == len(b"mcp bytes")
        assert os.path.isfile(body["path"])
        assert os.path.realpath(body["path"]).startswith(
            os.path.realpath(str(upload_dir)) + os.sep
        )


def test_mcp_upload_401_on_bad_token(tmp_path):
    upload_dir = tmp_path / "mcp-up"
    upload_dir.mkdir()
    with _mcp_client(upload_dir, token=_TOKEN) as client:
        resp = _upload(client, "/upload", token="wrong-token")
        assert resp.status_code == 401


def test_mcp_upload_200_without_token_when_unset(tmp_path):
    upload_dir = tmp_path / "mcp-up"
    upload_dir.mkdir()
    with _mcp_client(upload_dir, token=None) as client:
        resp = client.post(
            "/upload", files={"file": ("a.txt", b"data", "text/plain")}
        )
        assert resp.status_code == 200, resp.text


def test_mcp_upload_400_when_no_file_part(tmp_path):
    upload_dir = tmp_path / "mcp-up"
    upload_dir.mkdir()
    with _mcp_client(upload_dir) as client:
        resp = client.post("/upload", headers=_auth_headers())
        assert resp.status_code == 400
