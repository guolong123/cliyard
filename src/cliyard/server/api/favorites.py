"""``/api/favorites`` — 常用命令 CRUD API.

读写 ``~/.cliyard/favorites.json`` 中的常用命令列表。

- ``GET /api/favorites``  返回全部常用命令 ``{"favorites": [...]}``
- ``POST /api/favorites`` 全量更新常用命令列表

路由经 :data:`cliyard.server.api.router`（前缀 ``/api``）挂载。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_FAVORITES_FILE = Path.home() / ".cliyard" / "favorites.json"


def _load() -> dict:
    """读取 ``~/.cliyard/favorites.json``；缺失或解析失败返回空列表。"""
    if not _FAVORITES_FILE.exists():
        return {"favorites": []}
    try:
        return json.loads(_FAVORITES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"favorites": []}


def _save(data: dict) -> None:
    """把 data 写入 ``~/.cliyard/favorites.json``（确保父目录存在）。"""
    _FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FAVORITES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/favorites")
async def get_favorites() -> dict:
    """返回所有常用命令。"""
    return _load()


@router.post("/favorites")
async def update_favorites(body: dict) -> dict:
    """全量更新常用命令列表。"""
    if "favorites" not in body or not isinstance(body["favorites"], list):
        raise HTTPException(400, "body must contain 'favorites' list")
    _save(body)
    return {"status": "ok", "count": len(body["favorites"])}
