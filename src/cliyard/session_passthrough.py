"""Session passthrough credential loading (fail-closed).

Reads ``$KETACLI_SESSION_DIR/<sid>.json`` written by the ai-assistant backend and
converts it into per-request ``Cookie`` + ``X-Csrf-Token`` headers.  Any missing,
expired, inactive or mismatched binding raises :class:`SessionAuthError` so the
caller can fail closed without touching the upstream API.
"""

from __future__ import annotations

import json
import os
import time

SESSION_DIR_ENV = "KETACLI_SESSION_DIR"
SESSION_ID_ENV = "KETACLI_SESSION_ID"
SESSION_FIELD = "_session_id"
DEFAULT_ROOT_DIR = "/var/lib/ketacli/sessions"
DEFAULT_USER_SUBDIR = os.path.join(".local", "state", "ketacli", "sessions")


class SessionAuthError(RuntimeError):
    """Raised when a session binding cannot be used (fail-closed)."""


def resolve_session_dir() -> str:
    raw = (os.environ.get(SESSION_DIR_ENV) or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return DEFAULT_ROOT_DIR
    return os.path.join(os.path.expanduser("~"), DEFAULT_USER_SUBDIR)


def binding_path(sid: str) -> str:
    if not sid or "/" in sid or "\\" in sid or sid in (".", ".."):
        raise SessionAuthError("invalid session id")
    return os.path.join(resolve_session_dir(), f"{sid}.json")


def load_binding(sid: str) -> dict:
    sid = str(sid or "").strip()
    path = binding_path(sid)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise SessionAuthError("session credential not found") from None
    except (OSError, ValueError) as exc:
        raise SessionAuthError(f"session credential unreadable: {exc}") from exc
    if data.get("status") != "active":
        raise SessionAuthError("session not active")
    if int(data.get("exp") or 0) <= int(time.time()):
        raise SessionAuthError("session expired")
    if data.get("sid") and str(data["sid"]) != sid:
        raise SessionAuthError("session binding mismatch")
    cookie = data.get("cookie")
    csrf = data.get("csrf")
    if not cookie or not csrf:
        raise SessionAuthError("session credential incomplete")
    return data


def session_headers(sid: str) -> tuple[dict[str, str], dict]:
    data = load_binding(sid)
    return {"Cookie": data["cookie"], "X-Csrf-Token": data["csrf"]}, data


def pop_session_id(params):
    if not isinstance(params, dict):
        return params, None
    sid = params.pop(SESSION_FIELD, None)
    return params, (str(sid).strip() if sid else None)
