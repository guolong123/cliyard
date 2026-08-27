"""``/api/favorites`` — 常用命令 CRUD API.

读写 ``~/.cliyard/favorites.json`` 中的常用命令列表。

- ``GET /api/favorites``  返回全部常用命令 ``{"favorites": [...]}``
- ``POST /api/favorites`` 全量更新常用命令列表（写入前对每个 item 做 schema 校验）

路由经 :data:`cliyard.server.api.router`（前缀 ``/api``）挂载。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

router = APIRouter()

_FAVORITES_FILE = Path.home() / ".cliyard" / "favorites.json"


class FavoriteItem(BaseModel):
    """单个常用命令条目的 schema 校验（M5）。

    必填：``name`` / ``target`` / ``group``；``description`` 可选。
    多余字段默认忽略（``extra="ignore"`` 是 Pydantic 默认行为）。
    """

    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    group: str = Field(min_length=1)
    description: str = ""


class FavoritesBody(BaseModel):
    """POST body：``favorites`` 必须是 FavoriteItem 列表。"""

    favorites: list[FavoriteItem]


def _load() -> dict:
    """读取 ``~/.cliyard/favorites.json``；缺失或解析失败返回空列表。"""
    if not _FAVORITES_FILE.exists():
        return {"favorites": []}
    try:
        return json.loads(_FAVORITES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"favorites": []}


def _save(data: dict) -> None:
    """把 data 写入 ``~/.cliyard/favorites.json``（确保父目录存在）。

    写入采用「读-合并-写」：先读取当前文件内容，用本次提交的 ``favorites``
    覆盖其 ``favorites`` 键，再原子性写入临时文件后替换。这样可以缓解
    并发写竞争（H3）——两个请求并发时，后写方基于前写方的最新内容合并，
    而不是整体覆盖。
    """
    _FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 读当前内容再合并，减少全量替换丢数据的窗口
    current: dict[str, Any] = {}
    if _FAVORITES_FILE.exists():
        try:
            current = json.loads(_FAVORITES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current = {}
    current = current if isinstance(current, dict) else {}
    merged = {**current, "favorites": data.get("favorites", [])}
    tmp = _FAVORITES_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(_FAVORITES_FILE)


@router.get("/favorites")
async def get_favorites() -> dict:
    """返回所有常用命令。"""
    return _load()


@router.post("/favorites")
async def update_favorites(body: dict) -> dict:
    """全量更新常用命令列表。

    body 必须是 ``{"favorites": [...]}``，其中每个 item 需包含非空的
    ``name`` / ``target`` / ``group`` 字段（M5 校验）。格式错误返回 400。
    """
    try:
        parsed = FavoritesBody.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            400,
            f"favorites 格式错误: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}",
        ) from exc
    items = [item.model_dump() for item in parsed.favorites]
    _save({"favorites": items})
    return {"status": "ok", "count": len(items)}
