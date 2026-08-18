"""``/api/execute`` — execution + SSE stream endpoints.

* ``POST /api/execute`` —— 提交命令/流程执行，返回 ``{execution_id}``；
* ``GET /api/executions/{id}/stream`` —— SSE 事件流（sse-starlette）；
* ``GET /api/executions/{id}`` —— 轮询兜底：当前状态 + 全量 steps。

校验规则：kind 必须为 ``command|flow``、target 非空、command target 必须
是 ``resource.method`` 格式，否则 400；未知 resource/method/flow 不在此处
拦截——由后台线程查找失败并推送 ``{"type": "error"}`` 事件（执行仍在，
status=error）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from cliyard.server.executor import execution_manager

router = APIRouter()


class ExecuteRequest(BaseModel):
    """POST /api/execute 请求体。"""

    kind: str = Field(..., description='"command" | "flow"')
    target: str = Field(..., description='"resource.method" 或 flow command')
    params: dict[str, Any] = Field(default_factory=dict, description="执行参数（平铺 dict）")


@router.post("/execute")
async def execute(request: Request, body: ExecuteRequest):
    """提交一个命令/流程执行，立即返回 ``execution_id``（后台线程执行）。"""
    if body.kind not in ("command", "flow"):
        return JSONResponse(
            status_code=400,
            content={"detail": f"kind must be 'command' or 'flow', got {body.kind!r}"},
        )
    if not body.target:
        return JSONResponse(status_code=400, content={"detail": "target is required"})
    if body.kind == "command" and "." not in body.target:
        return JSONResponse(
            status_code=400,
            content={
                "detail": f"command target must be 'resource.method', got {body.target!r}"
            },
        )
    try:
        if body.kind == "command":
            execution_id = execution_manager.submit_command(
                request.app.state.spec_dir, body.target, body.params
            )
        else:
            execution_id = execution_manager.submit_flow(
                request.app.state.spec_dir, body.target, body.params
            )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"execution_id": execution_id}


@router.get("/executions/{execution_id}/stream")
async def stream_execution(request: Request, execution_id: str):
    """SSE 事件流：validate→auth→request→response→format→done（命令）。

    执行不存在返回 404。沿用 sse-starlette 默认心跳（15s comment 帧），
    保持代理连接存活；事件驱动流会在 done 事件后自然收尾。
    """
    execution = execution_manager.get(execution_id)
    if execution is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"execution {execution_id} not found"},
        )
    return EventSourceResponse(execution_manager.iter_events(execution_id, request=request))


@router.get("/executions/{execution_id}")
async def get_execution(request: Request, execution_id: str):
    """轮询兜底：返回当前状态与全量 steps（含已结束执行）。"""
    execution = execution_manager.get(execution_id)
    if execution is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"execution {execution_id} not found"},
        )
    return {
        "id": execution.id,
        "kind": execution.kind,
        "target": execution.target,
        "status": execution.status,
        "created_at": execution.created_at,
        "steps": list(execution.steps),
    }
