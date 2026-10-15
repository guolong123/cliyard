"""Signed one-time download tokens for ``GET /outputs/<id>?t=...``."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from cliyard.server.uploads import UPLOAD_TTL_S

_secret: str | None = None


def get_output_secret() -> str:
    """Process-stable signing secret (derived from KETACLI_MCP_TOKEN when set)."""
    global _secret
    if _secret is None:
        base = os.environ.get("KETACLI_MCP_TOKEN") or secrets.token_hex(16)
        _secret = hashlib.sha256(f"cliyard-outputs:{base}".encode()).hexdigest()
    return _secret


def _sign(ident: str, expiry: int, secret: str) -> str:
    return hmac.new(
        secret.encode(), f"{ident}.{expiry}".encode(), hashlib.sha256
    ).hexdigest()[:32]


def sign_output_token(ident: str, ttl_s: int | None = None) -> str:
    """Return ``<expiry>.<sig>`` valid for *ttl_s* (default: file TTL)."""
    expiry = int(time.time()) + int(ttl_s if ttl_s is not None else UPLOAD_TTL_S)
    return f"{expiry}.{_sign(ident, expiry, get_output_secret())}"


def verify_output_token(ident: str, token: str, ttl_s: int | None = None) -> bool:
    """Validate a ``<expiry>.<sig>`` token: well-formed, unexpired, untampered."""
    if not token or "." not in token:
        return False
    expiry_raw, _, sig = token.partition(".")
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    if time.time() > expiry:
        return False
    return hmac.compare_digest(sig, _sign(ident, expiry, get_output_secret()))


def output_url(base: str, ident: str) -> str:
    """Build the signed download URL for *ident*."""
    return f"{base.rstrip('/')}/outputs/{ident}?t={sign_output_token(ident)}"
