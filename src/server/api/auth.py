"""``/api/auth`` — credential profile endpoints (read-only + switch).

Exposes the saved credential profiles (via :mod:`cliyard.client.credentials`)
scoped to the served spec's service namespace:

* ``GET /api/auth/profiles`` lists every profile with a masked token plus the
  currently active one;
* ``POST /api/auth/switch`` moves the ``current`` pointer.

Profile creation / deletion / editing stays on the CLI (``cliyard auth``) —
this API never writes tokens and never returns a plaintext token.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from cliyard.client import credentials as cred

router = APIRouter()

# ``credentials.yaml`` is written without a lock (``_save`` in credentials.py);
# guard switch operations with a module-level lock so concurrent requests
# cannot interleave read-modify-write cycles and corrupt the file.
_switch_lock = threading.Lock()

_MASK = "\u2022\u2022\u2022\u2022"  # ••••


def _mask_token(token: str) -> str:
    """Mask a token as ``••••`` + last 4 chars (short tokens fully masked)."""
    if not token:
        return token
    if len(token) <= 4:
        return _MASK
    return _MASK + token[-4:]


def _profile_view(name: str, fields: dict) -> dict:
    """Build the public view of a profile — never includes the raw token."""
    view: dict = {
        "name": name,
        "endpoint": fields.get("endpoint", ""),
        "token_masked": _mask_token(str(fields.get("token", ""))),
    }
    if "expires_at" in fields:
        view["expires_at"] = fields["expires_at"]
    return view


def _service_id(request: Request) -> str:
    """Resolve the credentials namespace for the served spec.

    Mirrors ``runner.py``: the auth spec ``id`` wins, else the service name.
    """
    service = request.app.state.service
    service_name: str = service.get("name", "cliyard")
    auth_spec = service.get("auth")
    return auth_spec.get("id", service_name) if auth_spec else service_name


@router.get("/auth/profiles")
async def get_profiles(request: Request) -> dict:
    """List credential profiles with masked tokens + the current profile."""
    sid = _service_id(request)
    current = cred.get_current_profile(service=sid)
    profiles = cred.list_profiles(service=sid)

    current_view = None
    if current:
        current_view = _profile_view(current.get("_name", ""), current)

    return {
        "current": current_view,
        "profiles": [_profile_view(name, fields) for name, fields in profiles.items()],
    }


class SwitchBody(BaseModel):
    """Request body for ``POST /api/auth/switch``."""

    profile: str


@router.post("/auth/switch")
async def switch_profile(body: SwitchBody, request: Request) -> dict:
    """Switch the current profile of the served spec's service."""
    sid = _service_id(request)
    with _switch_lock:
        ok = cred.switch_profile(body.profile, service=sid)
    if not ok:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"current": body.profile}
