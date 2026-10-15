"""``GET /outputs/<id>`` — MCP 溢出内容下载点（一次性签名令牌或 Bearer 头）。"""

from __future__ import annotations

import os

from starlette.requests import Request as StarletteRequest
from starlette.responses import FileResponse, JSONResponse

from cliyard.server.api.output_tokens import verify_output_token


def _header_authorized(request: StarletteRequest, token: str | None) -> bool:
    if not token:
        return False
    auth = request.headers.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    return scheme.lower() == "bearer" and bool(credential) and credential == token


async def mcp_output_endpoint(request: StarletteRequest):
    """Serve an overflow file: ``?t=<signed>`` (no header) OR Bearer header."""
    ident = request.path_params.get("id", "")
    if not ident or "/" in ident or ident in (".", ".."):
        return JSONResponse(status_code=400, content={"detail": "invalid output id"})

    upload_dir = getattr(request.app.state, "upload_dir", None)
    if not upload_dir:
        from cliyard.server.uploads import default_upload_dir

        upload_dir = default_upload_dir()

    from cliyard.server.uploads import sweep

    sweep(upload_dir)

    token = getattr(request.app.state, "upload_token", None)
    sig = request.query_params.get("t")
    if sig is not None:
        authorized = verify_output_token(ident, sig)
    elif token:
        authorized = _header_authorized(request, token)
    else:
        authorized = True
    if not authorized:
        return JSONResponse(
            status_code=401, content={"detail": "invalid or expired download token"}
        )

    base = os.path.realpath(upload_dir)
    target = os.path.realpath(os.path.join(base, ident))
    if os.path.dirname(target) != base or not os.path.isfile(target):
        return JSONResponse(status_code=404, content={"detail": "output not found"})
    return FileResponse(target, media_type="application/json", filename=ident)
