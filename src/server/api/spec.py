"""``/api/spec`` — YAML spec → command/flow tree metadata.

从 ``request.app.state.spec_dir`` 读取 spec 目录，交给
:func:`cliyard.server.schema_bridge.build_command_tree` 转换
（转换器为纯函数，spec 由 app 启动时加载一次并缓存到 state）。

路由经 :data:`cliyard.server.api.router`（前缀 ``/api``）挂载，
实际路径为 ``GET /api/spec``。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from cliyard.server.schema_bridge import build_command_tree

router = APIRouter()


@router.get("/spec")
async def get_spec(request: Request) -> dict:
    """Return the command tree + flow metadata for the served spec dir."""
    return build_command_tree(request.app.state.spec_dir)
