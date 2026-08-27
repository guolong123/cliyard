"""Tests for the favorites API (GET/POST /api/favorites).

Covers:
- Empty state: no file -> ``{"favorites": []}``
- Round-trip: POST then GET returns the saved list
- Validation: invalid body (missing ``favorites`` key / non-list / bad item)
  returns 400
- Corrupt-file tolerance: malformed JSON on disk -> empty list
- Persistence path is monkeypatched to a tmp_path so no real user data is
  touched.

Follows the same TestClient pattern as ``test_serve_history.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cliyard.server.api import favorites as favorites_mod
from cliyard.server.api.favorites import FavoriteItem, FavoritesBody
from cliyard.server.app import create_app

_FIXTURES_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec-dir"


@pytest.fixture
def fav_file(tmp_path, monkeypatch):
    """Point the favorites store at an isolated tmp file for each test."""
    target = tmp_path / "favorites.json"
    monkeypatch.setattr(favorites_mod, "_FAVORITES_FILE", target)
    return target


@pytest.fixture
def client(monkeypatch):
    """TestClient over fixtures/spec-dir with the "frontend not built" branch."""
    from cliyard.server import app as server_app

    monkeypatch.setattr(
        server_app,
        "_WEBUI_DIST",
        Path(__file__).resolve().parent / "no-such-dist",
    )
    return TestClient(create_app(str(_FIXTURES_SPEC)))


# ---------------------------------------------------------------------------
# Data-layer helpers
# ---------------------------------------------------------------------------


def test_favorite_item_schema_requires_name_target_group():
    """M5: item with missing/invalid fields must fail Pydantic validation."""
    with pytest.raises(Exception):
        FavoriteItem(name="", target="a.b", group="g")
    with pytest.raises(Exception):
        FavoriteItem(name="n", target="", group="g")
    with pytest.raises(Exception):
        FavoriteItem(name="n", target="a.b", group="")
    # valid minimal item passes
    item = FavoriteItem(name="n", target="a.b", group="g")
    assert item.name == "n"
    assert item.description == ""


def test_favorites_body_rejects_non_list():
    with pytest.raises(Exception):
        FavoritesBody.model_validate({"favorites": "not-a-list"})


# ---------------------------------------------------------------------------
# API: empty / round-trip / validation
# ---------------------------------------------------------------------------


def test_api_get_empty_when_no_file(client, fav_file):
    resp = client.get("/api/favorites")
    assert resp.status_code == 200
    assert resp.json() == {"favorites": []}


def test_api_post_then_get_roundtrip(client, fav_file):
    payload = {
        "favorites": [
            {
                "name": "公司列表",
                "target": "company-base-info.list",
                "group": "公司管理",
                "description": "查询测试公司",
            },
            {
                "name": "供应商引入",
                "target": "flow.supplier-introduce",
                "group": "供应商管理",
            },
        ]
    }
    resp = client.post("/api/favorites", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "count": 2}

    # File on disk is the persisted source of truth
    on_disk = json.loads(fav_file.read_text(encoding="utf-8"))
    assert on_disk["favorites"][0]["target"] == "company-base-info.list"

    get_resp = client.get("/api/favorites")
    assert get_resp.status_code == 200
    # POST 经 Pydantic 归一化：description 未传时默认空字符串
    expected = [
        {**payload["favorites"][0]},
        {**payload["favorites"][1], "description": ""},
    ]
    assert get_resp.json()["favorites"] == expected


def test_api_post_missing_favorites_key_400(client, fav_file):
    resp = client.post("/api/favorites", json={})
    assert resp.status_code == 400


def test_api_post_non_list_favorites_400(client, fav_file):
    resp = client.post("/api/favorites", json={"favorites": "oops"})
    assert resp.status_code == 400


def test_api_post_invalid_item_missing_target_400(client, fav_file):
    """M5: item missing required field must be rejected."""
    resp = client.post(
        "/api/favorites",
        json={"favorites": [{"name": "x", "group": "g"}]},
    )
    assert resp.status_code == 400


def test_api_post_invalid_item_empty_name_400(client, fav_file):
    resp = client.post(
        "/api/favorites",
        json={"favorites": [{"name": "", "target": "a.b", "group": "g"}]},
    )
    assert resp.status_code == 400


def test_api_corrupt_file_returns_empty(client, fav_file):
    fav_file.write_text("{ not valid json !!!", encoding="utf-8")
    resp = client.get("/api/favorites")
    assert resp.status_code == 200
    assert resp.json() == {"favorites": []}


def test_api_post_ignores_extra_fields(client, fav_file):
    """M5: extra fields on an item are dropped, not stored."""
    resp = client.post(
        "/api/favorites",
        json={
            "favorites": [
                {
                    "name": "n",
                    "target": "a.b",
                    "group": "g",
                    "hacker": "drop-me",
                }
            ]
        },
    )
    assert resp.status_code == 200
    saved = json.loads(fav_file.read_text(encoding="utf-8"))
    assert "hacker" not in saved["favorites"][0]


def test_api_save_is_merge_not_clobber(client, fav_file):
    """H3 mitigation: writing favorites preserves other keys in the file."""
    fav_file.write_text(
        json.dumps({"favorites": [], "custom_key": "keep-me"}),
        encoding="utf-8",
    )
    resp = client.post(
        "/api/favorites",
        json={"favorites": [{"name": "n", "target": "a.b", "group": "g"}]},
    )
    assert resp.status_code == 200
    saved = json.loads(fav_file.read_text(encoding="utf-8"))
    assert saved["custom_key"] == "keep-me"
    assert len(saved["favorites"]) == 1


def test_api_module_registered(client):
    """Router is wired into the /api prefix."""
    # _IncludedRouter wraps sub-routers; verify via an actual request instead.
    resp = client.get("/api/favorites")
    assert resp.status_code == 200
