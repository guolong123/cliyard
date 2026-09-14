"""``POST /upload`` —— MCP 文件交接点（multipart 单 ``file`` 字段）。

远端 agent 先 ``POST .../upload``（``-F file=@a.pdf``）拿到 server 绝对
路径，再把该路径填进业务工具的 ``file`` 参数照常调用。

挂载（全路径钉死）：
* serve 侧：``POST /api/upload``（:func:`cliyard.server.app.create_app`
  经本模块 ``router`` 挂载，鉴权走 :func:`verify_upload_token` Depends）；
* MCP Streamable HTTP 侧：``POST /upload``（``build_mcp_http_app`` 经本模块
  :func:`mcp_upload_endpoint` 挂载，鉴权复用 ``_StaticTokenVerifier`` 语义）。

存储委托给 :mod:`cliyard.server.uploads` 的 pinned 接口
``save_upload(filename, data, upload_dir)`` / ``sweep(upload_dir)``。

.. note::
    曾为并行波准备的同名最小 shim 已删除——todo 1 的
    ``cliyard.server.uploads`` 已落地，本模块直接 import 真接口
    （若未来该模块缺失，测试/启动即 ImportError 显式失败，
    不再静默回退到第二套 API）。
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from cliyard.engine.errors import CliyError
from cliyard.server.executor import MAX_UPLOAD_BYTES
from cliyard.server.uploads import save_upload, sweep


router = APIRouter()


async def verify_upload_token(request: Request) -> None:
    """serve 侧 ``/api/upload`` 鉴权 Depends（读 ``app.state.upload_token``）。

    * 未配置 token（本地回环开发态）→ 免鉴，与 ``is_local_host`` 一致；
    * 已配置 → 要求 ``Authorization: Bearer <token>`` 恒定时间比对，
      缺失/错误一律 ``401 {"detail": ...}``。
    """
    token = getattr(request.app.state, "upload_token", None)
    if not token:
        return None
    auth = request.headers.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    if (
        scheme.lower() == "bearer"
        and credential
        and hmac.compare_digest(credential, token)
    ):
        return None
    raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def _process_upload(
    filename: str | None,
    data: bytes,
    upload_dir: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """上传核心语义（serve 与 MCP 双 app 共用；返回 ``(状态码, 响应体)``）。

    * 无 file part / 空文件名 / 空体 → 400；
    * 超 ``MAX_UPLOAD_BYTES``（自 executor import，不自定）→ 413；
    * 存储层 ``CliyError``（超限/配额/落盘失败）→ 413（uploads 模块契约）；
    * 成功 → 200 ``{"path","file_name","bytes","expires_at"}``
      （后三者由 ``save_upload`` 直接返回，``expires_at`` 为 tz-aware ISO8601）。
    """
    if not filename or not data:
        return 400, {
            "detail": "missing or empty 'file' part "
            "(multipart field name must be 'file')"
        }
    if len(data) > MAX_UPLOAD_BYTES:
        return 413, {
            "detail": f"file too large: {len(data)} bytes "
            f"exceeds limit of {MAX_UPLOAD_BYTES} bytes"
        }
    sweep(upload_dir)  # 搭车清过期/超配额（永不抛错，无后台线程）
    try:
        saved = save_upload(filename, data, upload_dir)
    except CliyError as exc:
        return 413, {"detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 未知落盘错误如实 500
        return 500, {"detail": f"failed to store upload: {exc}"}
    return 200, {
        "path": saved["path"],
        "file_name": saved["file_name"],
        "bytes": saved["bytes"],
        "expires_at": saved["expires_at"],
    }


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile | None = File(default=None),
    _: None = Depends(verify_upload_token),
) -> JSONResponse:
    """``POST /api/upload``（serve 侧经 ``create_app`` 以 ``/api`` 前缀挂载）。"""
    data = await file.read() if file is not None else b""
    filename = file.filename if file is not None else None
    upload_dir = getattr(request.app.state, "upload_dir", None)
    status, body = _process_upload(filename, data, upload_dir)
    return JSONResponse(status_code=status, content=body)


async def mcp_upload_endpoint(request: StarletteRequest) -> StarletteResponse:
    """``POST /upload``（MCP Streamable HTTP 侧经 ``build_mcp_http_app`` 挂载）。

    鉴权复用 ``_StaticTokenVerifier`` 语义（延迟导入避开
    ``server ↔ api.upload`` 循环导入）；未配置 token 时免鉴。
    """
    from cliyard.server.mcp.server import _StaticTokenVerifier

    token = getattr(request.app.state, "upload_token", None)
    if token:
        auth = request.headers.get("authorization", "")
        scheme, _, credential = auth.partition(" ")
        verifier = _StaticTokenVerifier(token)
        verified = await verifier.verify_token(
            credential if scheme.lower() == "bearer" else ""
        )
        if verified is None:
            return JSONResponse(
                status_code=401, content={"detail": "invalid or missing bearer token"}
            )
    try:
        form = await request.form()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"detail": "expected multipart form with a 'file' field"},
        )
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse(
            status_code=400,
            content={
                "detail": "missing or empty 'file' part "
                "(multipart field name must be 'file')"
            },
        )
    data = await upload.read()  # type: ignore[union-attr]
    filename = getattr(upload, "filename", None)
    upload_dir = getattr(request.app.state, "upload_dir", None)
    status, body = _process_upload(filename, data, upload_dir)
    return JSONResponse(status_code=status, content=body)
