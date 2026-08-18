"""Manage saved credentials in ~/.cliyard/credentials.yaml.

Credentials are namespaced **per service** so that every cliyard-based CLI
(e.g. ``jcli``, ``ketacli``) only sees its own profiles — no cross-tool
leaks of profiles, current pointer, or tokens.

File format::

    services:
      jcli:
        current: myjenkins
        profiles:
          myjenkins:
            endpoint: https://jenkins.example.com
            token: eyJ...
      ketacli:
        current: prod
        profiles:
          prod:
            endpoint: https://prod.example.com
            token: eyJ...
          dev:
            endpoint: https://dev.example.com
            token: eyJ...

Legacy single-service files (a flat ``profiles``/``current`` block without a
``services`` dimension) are still readable: they are treated as the block of
whatever service reads them, and are transparently migrated into the new
``services`` layout on the first write.
"""

from __future__ import annotations

import os
import time

import yaml

CLIYARD_DIR = os.path.expanduser("~/.cliyard")
CREDENTIALS_PATH = os.path.join(CLIYARD_DIR, "credentials.yaml")

DEFAULT_SERVICE = "default"


def _load_raw() -> dict:
    """Load raw YAML, returning empty dict on failure."""
    if not os.path.exists(CREDENTIALS_PATH):
        return {}
    try:
        with open(CREDENTIALS_PATH) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _save(raw: dict) -> None:
    """Atomic write of raw dict to credentials file."""
    os.makedirs(CLIYARD_DIR, exist_ok=True)
    with open(CREDENTIALS_PATH, "w") as f:
        yaml.dump(raw, f)


def _service_block(raw: dict, service: str) -> dict:
    """Extract a service's ``{profiles, current}`` block from raw.

    Legacy flat files (no ``services`` dimension) are treated as belonging
    to the requesting service so old data keeps working unchanged.
    """
    services = raw.get("services")
    if isinstance(services, dict):
        block = services.get(service)
        return block if isinstance(block, dict) else {}
    return raw


def _resolve_service(raw: dict, service: str) -> str:
    """Resolve the effective service for a call that passed none.

    Historical no-arg calls (``service == DEFAULT_SERVICE``) must keep
    working after the services migration: prefer an explicit ``default``
    block, else fall back to the sole configured service.
    """
    if service != DEFAULT_SERVICE:
        return service
    services = raw.get("services")
    if isinstance(services, dict):
        if DEFAULT_SERVICE in services:
            return DEFAULT_SERVICE
        if len(services) == 1:
            return next(iter(services))
    return service


def _resolve_current_service(raw: dict) -> str:
    """Pick the service backing a no-arg ``get_current_profile()`` call.

    Mirrors the pre-migration semantics of the single global ``current``
    pointer: an explicit ``default`` block wins, then a sole service, then
    the first service that has a ``current`` pointer set.
    """
    services = raw.get("services")
    if not isinstance(services, dict):
        return DEFAULT_SERVICE  # legacy flat file — handled by _service_block
    if DEFAULT_SERVICE in services:
        return DEFAULT_SERVICE
    if len(services) == 1:
        return next(iter(services))
    for svc, block in services.items():
        if isinstance(block, dict) and block.get("current"):
            return svc
    return DEFAULT_SERVICE


def _ensure_services(raw: dict, service: str) -> tuple[dict, dict]:
    """Return ``(services, raw)`` guaranteeing a ``services`` dimension.

    If the file is still in the legacy flat layout, its ``profiles`` /
    ``current`` block is migrated under *service* before any write.
    """
    services = raw.get("services")
    if isinstance(services, dict):
        return services, raw
    legacy = {k: v for k, v in raw.items() if k in ("profiles", "current")}
    services = {}
    if legacy:
        services[service] = legacy
    return services, {"services": services}


# ---------------------------------------------------------------------------
# Profile CRUD (service-scoped)
# ---------------------------------------------------------------------------


def list_profiles(service: str = DEFAULT_SERVICE) -> dict[str, dict]:
    """Return ``{name: fields, ...}`` for the given service."""
    raw = _load_raw()
    block = _service_block(raw, _resolve_service(raw, service))
    return block.get("profiles", {})


def list_services() -> dict[str, dict]:
    """Return ``{service_id: {profiles, current}, ...}`` for all services."""
    raw = _load_raw()
    services = raw.get("services")
    if isinstance(services, dict):
        return services
    legacy = {k: v for k, v in raw.items() if k in ("profiles", "current")}
    if legacy:
        return {DEFAULT_SERVICE: legacy}
    return {}


def get_profile(name: str, service: str = DEFAULT_SERVICE) -> dict | None:
    """Get a profile by name, or *None* if not found / expired."""
    profiles = list_profiles(service)
    profile = profiles.get(name)
    if not profile:
        return None
    expires_at = profile.get("expires_at")
    if expires_at and time.time() > expires_at:
        return None
    return profile


def get_current_profile(service: str = DEFAULT_SERVICE) -> dict | None:
    """Get the active profile for a service (from ``current`` pointer).

    Without an explicit *service*, mirrors the legacy single-global-current
    behavior: returns the profile the ``current`` pointer actually selects,
    whichever service it lives under.
    """
    raw = _load_raw()
    if service == DEFAULT_SERVICE:
        service = _resolve_current_service(raw)
    block = _service_block(raw, service)
    name = block.get("current")
    if not name:
        return None
    profile = get_profile(name, service=service)
    if profile:
        profile["_name"] = name
    return profile


def save_profile(
    name: str,
    fields: dict,
    set_current: bool = True,
    service: str = DEFAULT_SERVICE,
) -> None:
    """Save or update a profile for the given service.

    Args:
        name: Profile name (e.g. ``"prod"``, ``"dev"``).
        fields: Credential fields to store.
        set_current: If True, also set as the current profile.
        service: Service/CLI namespace to save under.
    """
    raw = _load_raw()
    services, raw = _ensure_services(raw, service)
    block = services.get(service)
    if not isinstance(block, dict):
        block = {}
    if "profiles" not in block:
        block["profiles"] = {}
    if name not in block["profiles"]:
        block["profiles"][name] = {}
    block["profiles"][name].update(fields)
    if set_current:
        block["current"] = name
    services[service] = block
    raw["services"] = services
    _save(raw)


def delete_profile(name: str, service: str = DEFAULT_SERVICE) -> None:
    """Delete a profile. If it was current, reset current."""
    raw = _load_raw()
    if not isinstance(raw.get("services"), dict):
        # Legacy flat file: operate in place without migrating
        raw.get("profiles", {}).pop(name, None)
        if raw.get("current") == name:
            raw.pop("current", None)
            if raw.get("profiles"):
                raw["current"] = next(iter(raw["profiles"]))
        _save(raw)
        return
    block = raw["services"].get(service)
    if not isinstance(block, dict):
        return
    block.get("profiles", {}).pop(name, None)
    if block.get("current") == name:
        block.pop("current", None)
        if block.get("profiles"):
            block["current"] = next(iter(block["profiles"]))
    raw["services"][service] = block
    _save(raw)


def switch_profile(name: str, service: str = DEFAULT_SERVICE) -> bool:
    """Switch the ``current`` pointer of a service. Returns False if absent."""
    profiles = list_profiles(service)
    if name not in profiles:
        return False
    raw = _load_raw()
    services, raw = _ensure_services(raw, service)
    block = services.get(service)
    if not isinstance(block, dict):
        block = {}
    block["current"] = name
    services[service] = block
    raw["services"] = services
    _save(raw)
    return True


# ---------------------------------------------------------------------------
# Service-level helpers
# ---------------------------------------------------------------------------


def save_service_credentials(service_id: str, fields: dict) -> None:
    """Save credentials for a service under its own namespace."""
    save_profile(service_id, fields, service=service_id)


def get_service_credentials(service_id: str) -> dict | None:
    """Get a service's current profile, falling back to its named profile."""
    cur = get_current_profile(service=service_id)
    if cur:
        cur.pop("_name", None)
        return cur
    return get_profile(service_id, service=service_id)


def clear_service_credentials(service_id: str) -> None:
    """Remove a service's entire credentials block."""
    raw = _load_raw()
    if isinstance(raw.get("services"), dict):
        if service_id in raw["services"]:
            del raw["services"][service_id]
            _save(raw)
        return
    # Legacy flat file: remove the profile matching the service id
    delete_profile(service_id)


def clear_all_credentials() -> None:
    """Remove the entire credentials file."""
    if os.path.exists(CREDENTIALS_PATH):
        os.remove(CREDENTIALS_PATH)


# Keep old names for backward compat
load_credentials = _load_raw
