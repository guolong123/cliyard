"""``/api/favorites`` — 常用命令 CRUD API.

读写 ``~/.cliyard/favorites.json`` 中的常用命令列表。

- ``GET /api/favorites``         返回全部常用命令 ``{"favorites": [...]}``
- ``POST /api/favorites``        全量更新常用命令列表（写入前对每个 item 做 schema 校验）
- ``POST /api/favorites/toggle`` 增量添加/移除单条常用命令（H3 缓解）

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

    必填：``name`` / ``target`` / ``group``；``description`` 可选，缺省为空字符串。
    多余字段默认忽略。
    """

    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    group: str = Field(min_length=1)
    description: str = ""


class FavoritesBody(BaseModel):
    """POST body：``favorites`` 必须是 FavoriteItem 列表。"""

    favorites: list[FavoriteItem]


class ToggleRequest(BaseModel):
    """增量切换请求：添加或移除一条常用命令。

    - 若 ``target`` 已存在，移除该条目。
    - 若 ``target`` 不存在，将 ``item`` 追加到列表。
    """

    target: str = Field(min_length=1)
    item: FavoriteItem | None = None


def _load() -> dict:
    """读取 ``~/.cliyard/favorites.json``；缺失或解析失败返回 ``{"favorites": []}``。"""
    if not _FAVORITES_FILE.exists():
        return {"favorites": []}
    try:
        data = json.loads(_FAVORITES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"favorites": []}
    # 防御合法 JSON 但非 dict 的情况（如数组、标量）
    if not isinstance(data, dict):
        return {"favorites": []}
    return data


def _save(data: dict) -> None:
    """把 data 写入 ``~/.cliyard/favorites.json``（确保父目录存在）。

    写入采用「读-合并-写」：先读取当前文件内容，用本次提交的 ``favorites``
    覆盖其 ``favorites`` 键，再原子性写入临时文件后替换。这样可以缓解
    并发写竞争（H3）——两个请求并发时，后写方基于前写方的最新内容合并，
    而不是整体覆盖。``favorites`` 列表本身仍是整体覆盖（last-write-wins），
    增量操作用 ``/favorites/toggle`` 端点避免全量覆盖。
    """
    _FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
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
def get_favorites() -> dict:
    """返回所有常用命令。"""
    return _load()


@router.post("/favorites")
def update_favorites(body: dict) -> dict:
    """全量更新常用命令列表。

    body 必须是 ``{"favorites": [...]}``，其中每个 item 需包含非空的
    ``name`` / ``target`` / ``group`` 字段（M5 校验）。格式错误返回 400，
    含所有校验失败的详细信息。
    """
    try:
        parsed = FavoritesBody.model_validate(body)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise HTTPException(400, f"favorites 格式错误: {details}") from exc
    items = [item.model_dump() for item in parsed.favorites]
    _save({"favorites": items})
    return {"status": "ok", "count": len(items)}


@router.post("/favorites/toggle")
def toggle_favorite(body: dict) -> dict:
    """增量添加/移除一条常用命令（H3 缓解）。

    若 ``target`` 已存在则移除，否则追加。避免全量 POST 的 last-write-wins
    竞态——每次 toggle 只修改一条数据，不依赖前端完整列表的闭包快照。
    """
    try:
        parsed = ToggleRequest.model_validate(body)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise HTTPException(400, f"toggle 请求格式错误: {details}") from exc

    data = _load()
    items = data.get("favorites", [])
    if not isinstance(items, list):
        items = []

    target = parsed.target
    idx = next((i for i, f in enumerate(items) if isinstance(f, dict) and f.get("target") == target), -1)

    if idx >= 0:
        # 移除
        items.pop(idx)
        action = "removed"
    else:
        # 添加
        if parsed.item is None:
            raise HTTPException(400, "target 不存在且未提供 item 参数")
        items.append(parsed.item.model_dump())
        action = "added"

    _save({"favorites": items})
    return {"status": "ok", "action": action, "count": len(items)}