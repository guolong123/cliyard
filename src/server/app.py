"""FastAPI app factory for ``cliyard serve``.

Builds the web backend for a YAML spec directory:

* loads the service + flow definitions once at startup and injects them
  into ``app.state`` (``service`` / ``spec_dir``);
* registers CORS for the Vite dev server origins;
* mounts the ``/api`` routers (spec / execute / history / auth);
* exposes ``/health``;
* serves the built frontend from ``webui/dist`` when present — otherwise
  ``/`` returns a friendly JSON hint instead of a 500.

The real executor / history / auth handlers land in later todos; the API
sub-modules currently return 501 placeholders.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cliyard.engine.loader import load_flows, load_service
from cliyard.server.api import router as api_router
from cliyard.server.executor import execution_manager
from cliyard.server.history import DEFAULT_HISTORY_DB_PATH, HistoryStore

# 前端产物定位：
# * 包内（PyPI 安装：site-packages/cliyard/server/webui/dist）——随 wheel 分发
# * 项目根（开发/源码：cliyard/webui/dist）
# 包内路径存在时优先（安装场景），否则回退项目根（开发场景）。
_PACKAGE_WEBUI = Path(__file__).resolve().parent / "webui" / "dist"
_PROJECT_WEBUI = Path(__file__).resolve().parents[3] / "webui" / "dist"
_WEBUI_DIST = _PACKAGE_WEBUI if _PACKAGE_WEBUI.is_dir() else _PROJECT_WEBUI

# SQLite 执行历史库路径（测试可 monkeypatch 覆盖）。
_HISTORY_DB = DEFAULT_HISTORY_DB_PATH

# Vite dev server origins (see docs/cliyard-web design). 可通过
# ``create_app(..., cors_origins=...)`` 参数或 ``CLIYARD_CORS_ORIGINS``
# 环境变量（逗号分隔）覆盖，供生产部署放开前端域名。
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# CORS origins 环境变量名（逗号分隔；空值/未设置回退 _DEV_ORIGINS）。
_CORS_ORIGINS_ENV = "CLIYARD_CORS_ORIGINS"

_BRIDGE_NOT_BUILT_MESSAGE = "前端未构建，请先 cd webui && npm run build"


def _resolve_cors_origins(cors_origins: list[str] | None) -> list[str]:
    """解析 CORS origins：显式参数 > 环境变量 > 默认开发源。"""
    if cors_origins is not None:
        return cors_origins
    env = os.environ.get(_CORS_ORIGINS_ENV)
    if env:
        return [origin.strip() for origin in env.split(",") if origin.strip()]
    return _DEV_ORIGINS


def create_app(
    spec_dir: str | os.PathLike[str],
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the FastAPI application for a YAML spec directory.

    Loads the service/flow specs once, injects them into ``app.state``,
    registers the ``/api`` routers, the ``/health`` endpoint and — when the
    frontend has been built — serves the static ``webui/dist`` at ``/``.

    Args:
        spec_dir: Path to the cliyard spec directory (must contain
            ``_auth.yaml``).
        cors_origins: Explicit CORS ``allow_origins`` list. Defaults to
            ``None`` → resolve from the ``CLIYARD_CORS_ORIGINS`` environment
            variable (comma-separated), falling back to the Vite dev origins
            (``_DEV_ORIGINS``).

    Returns:
        A configured :class:`fastapi.FastAPI` instance.

    Raises:
        FileNotFoundError: If *spec_dir* does not exist or is not a valid
            cliyard spec directory (missing ``_auth.yaml``).
    """
    spec_path = Path(spec_dir)
    if not spec_path.is_dir():
        raise FileNotFoundError(f"Spec directory not found: {spec_path}")

    # Load once at startup; invalid specs fail fast here.
    service = load_service(spec_path)
    load_flows(spec_path)

    app = FastAPI(
        title="cliyard serve",
        description="Web UI for cliyard YAML specs",
    )

    app.state.service = service
    app.state.spec_dir = str(spec_path)

    # 执行历史存储：~/cliyard/serve_history.db（SQLite, WAL），注入
    # app.state 供 /api/executions 使用，并传给 executor 单例在终态写库。
    history_store = HistoryStore(_HISTORY_DB)
    app.state.history_store = history_store
    execution_manager.history_store = history_store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_resolve_cors_origins(cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "spec_dir": app.state.spec_dir,
            "service": service.get("name") or spec_path.name,
        }

    # Static frontend hosting — mounted last so it never swallows /api.
    if _WEBUI_DIST.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(_WEBUI_DIST), html=True),
            name="webui",
        )
    else:

        @app.get("/")
        async def index() -> JSONResponse:
            return JSONResponse(
                status_code=200,
                content={"message": _BRIDGE_NOT_BUILT_MESSAGE},
            )

    return app


def create_app_from_env() -> FastAPI:
    """Zero-arg factory for uvicorn ``--reload`` (import-string mode).

    ``uvicorn.run(..., reload=True)`` requires an import string rather than
    an app instance; this reads ``CLIYARD_SPEC_DIR`` set by ``cliyard serve``.
    """
    spec_dir = os.environ.get("CLIYARD_SPEC_DIR")
    if not spec_dir:
        raise RuntimeError("CLIYARD_SPEC_DIR is not set; run via `cliyard serve`")
    return create_app(spec_dir)
