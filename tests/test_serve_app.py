"""Tests for the ``cliyard.server.app`` FastAPI app factory.

Covers ``/health``, the ``/api`` 501 placeholders, the no-frontend root
hint, and the ``FileNotFoundError`` raised for invalid spec dirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from cliyard.server import app as server_app
from cliyard.server.app import _DEV_ORIGINS, create_app

_DEMO_SPEC = Path(__file__).resolve().parent.parent / "examples" / "demo"


def _cors_origins(app):
    """Extract the configured allow_origins from the CORS middleware."""
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs["allow_origins"]
    return None


@pytest.fixture()
def client(monkeypatch):
    """TestClient with the "frontend not built" branch forced on.

    ``webui/dist`` doesn't exist yet (built in T8); monkeypatching the
    module-level constant keeps the test deterministic regardless of when
    the frontend lands.
    """
    monkeypatch.setattr(
        server_app,
        "_WEBUI_DIST",
        Path(__file__).resolve().parent / "no-such-dist",
    )
    return TestClient(create_app(str(_DEMO_SPEC)))


def test_health_returns_ok_with_spec_meta(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "examples" in body["spec_dir"]
    assert body["service"] == "petstore"


def test_api_spec_responds_parsable_and_not_5xx(client):
    """``/api/spec`` may be 200 (T2 landed) or 501 (placeholder) — never crash."""
    resp = client.get("/api/spec")
    assert resp.status_code in (200, 501)
    assert isinstance(resp.json(), dict)


def test_api_execute_invalid_kind_returns_400(client):
    """``/api/execute`` validates kind — 非法 kind 返回 400，不启动执行。"""
    resp = client.post(
        "/api/execute",
        json={"kind": "bogus", "target": "user.list", "params": {}},
    )
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_root_returns_build_hint_json(client):
    """``/`` returns a friendly hint (200), not a 500, when dist is missing."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert "message" in body
    assert "npm run build" in body["message"]


def test_package_webui_dist_selected_when_present():
    """包内 dist（随 wheel 分发）存在时 _WEBUI_DIST 指向包内路径。

    源码检出下 webui/dist 是构建产物、默认不存在；该断言仅对已安装 wheel
    有意义，故 dist 缺失时跳过。
    """
    package_dist = Path(server_app.__file__).resolve().parent / "webui" / "dist"
    if not package_dist.is_dir():
        pytest.skip("packaged webui/dist not built (source checkout)")
    assert server_app._WEBUI_DIST == package_dist
    assert (server_app._WEBUI_DIST / "index.html").is_file()


def test_root_serves_frontend_when_built():
    """前端已构建（包内 dist 存在）时 / 返回 index.html 而非提示 JSON。"""
    package_dist = Path(server_app.__file__).resolve().parent / "webui" / "dist"
    if not package_dist.is_dir():
        pytest.skip("packaged webui/dist not built (source checkout)")
    client = TestClient(create_app(str(_DEMO_SPEC)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<!doctype html" in resp.text.lower()


def test_create_app_nonexistent_spec_dir_raises():
    with pytest.raises(FileNotFoundError):
        create_app("/no/such/spec-dir")


def test_create_app_dir_without_auth_yaml_raises(tmp_path):
    """An existing dir without ``_auth.yaml`` is not a valid spec dir."""
    with pytest.raises(FileNotFoundError):
        create_app(str(tmp_path))


# ---------------------------------------------------------------------------
# CORS origins configurability
# ---------------------------------------------------------------------------


def test_cors_defaults_to_dev_origins(monkeypatch):
    monkeypatch.delenv("CLIYARD_CORS_ORIGINS", raising=False)
    app = create_app(str(_DEMO_SPEC))
    assert _cors_origins(app) == _DEV_ORIGINS


def test_cors_create_app_argument_overrides(monkeypatch):
    monkeypatch.delenv("CLIYARD_CORS_ORIGINS", raising=False)
    app = create_app(str(_DEMO_SPEC), cors_origins=["http://prod.example.com"])
    assert _cors_origins(app) == ["http://prod.example.com"]


def test_cors_environment_variable_overrides(monkeypatch):
    monkeypatch.setenv(
        "CLIYARD_CORS_ORIGINS", "http://a.example.com, http://b.example.com"
    )
    app = create_app(str(_DEMO_SPEC))
    assert _cors_origins(app) == [
        "http://a.example.com",
        "http://b.example.com",
    ]


def test_cors_explicit_argument_beats_environment(monkeypatch):
    monkeypatch.setenv("CLIYARD_CORS_ORIGINS", "http://env.example.com")
    app = create_app(
        str(_DEMO_SPEC), cors_origins=["http://arg.example.com"]
    )
    assert _cors_origins(app) == ["http://arg.example.com"]
