"""``/api/executions`` — execution history endpoints (serve T6).

* ``GET /api/executions`` —— 历史列表（时间倒序、分页、kind 过滤；params
  只回脱敏摘要，result_preview 已截断 2000 字符）；
* ``POST /api/executions/{id}/replay`` —— 用历史记录的原始 params 重新
  提交（kind 决定 submit_command/submit_flow），返回新 execution_id；
  历史中不存在该 id 返回 404；
* ``DELETE /api/executions`` —— 清空历史，返回 ``{deleted: N}``。

历史存储实例由 ``create_app`` 注入到 ``app.state.history_store``。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cliyard.server.executor import execution_manager

router = APIRouter()

_MAX_LIMIT = 200


@router.get("/executions")
async def list_executions(
    request: Request, limit: int = 50, offset: int = 0, kind: str | None = None
):
    """List past executions (time desc, paginated, optional kind filter)."""
    if kind is not None and kind not in ("command", "flow"):
        return JSONResponse(
            status_code=400,
            content={"detail": f"kind must be 'command' or 'flow', got {kind!r}"},
        )
    store = request.app.state.history_store
    return store.list(
        limit=min(max(limit, 1), _MAX_LIMIT),
        offset=max(offset, 0),
        kind=kind,
    )


@router.post("/executions/{execution_id}/replay")
async def replay_execution(request: Request, execution_id: str):
    """Re-run a past execution from its stored params; return the new id."""
    store = request.app.state.history_store
    original = store.get_params(execution_id)
    if original is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"execution {execution_id} not found in history"},
        )
    spec_dir = request.app.state.spec_dir
    try:
        if original["kind"] == "flow":
            new_id = execution_manager.submit_flow(
                spec_dir, original["target"], original["params"]
            )
        else:
            new_id = execution_manager.submit_command(
                spec_dir, original["target"], original["params"]
            )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"execution_id": new_id}


@router.delete("/executions")
async def clear_executions(request: Request):
    """Clear the execution history; return the number of deleted rows."""
    store = request.app.state.history_store
    return {"deleted": store.clear()}
