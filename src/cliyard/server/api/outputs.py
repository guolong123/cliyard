"""``GET /outputs/<id>`` — MCP 溢出内容下载点（截断内容回取）。"""

from __future__ import annotations

import os

from starlette.requests import Request as StarletteRequest
from starlette.responses import FileResponse, JSONResponse


async def mcp_output_endpoint(request: StarletteRequest):
    """鉴权语义与 ``POST /upload`` 一致；未配置 token 时免鉴。"""
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

    ident = request.path_params.get("id", "")
    if not ident or "/" in ident or ident in (".", ".."):
        return JSONResponse(status_code=400, content={"detail": "invalid output id"})

    upload_dir = getattr(request.app.state, "upload_dir", None)
    if not upload_dir:
        from cliyard.server.uploads import default_upload_dir

        upload_dir = default_upload_dir()
    base = os.path.realpath(upload_dir)
    target = os.path.realpath(os.path.join(base, ident))
    if os.path.dirname(target) != base or not os.path.isfile(target):
        return JSONResponse(status_code=404, content={"detail": "output not found"})
    return FileResponse(target, media_type="application/json", filename=ident)
