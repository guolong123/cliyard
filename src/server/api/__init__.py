"""``cliyard.server.api`` — FastAPI routers for the ``/api`` prefix.

Aggregates the per-feature routers (spec / execute / history / auth) into a
single ``router`` mounted by :func:`cliyard.server.app.create_app` at the
``/api`` prefix.

In this wave (T3) the individual sub-modules only expose 501 placeholders —
the real implementations land in later todos (T2 spec, T5 executor, T6
history, T7 auth).
"""

from __future__ import annotations

from fastapi import APIRouter

from cliyard.server.api import auth, execute, history, spec

router = APIRouter(prefix="/api")

router.include_router(spec.router)
router.include_router(execute.router)
router.include_router(history.router)
router.include_router(auth.router)
