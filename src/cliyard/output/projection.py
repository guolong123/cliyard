"""Unified list projection (fields/brief) for paginated tool output.

One implementation for every list tool: the tool schema always exposes
``fields`` / ``brief`` (injected by :mod:`cliyard.server.schema_bridge`), and the
MCP output pipeline applies the same projection rules:

* explicit ``fields`` (comma separated / list) → only those keys;
* ``brief=true`` → a small metadata field set (spec ``output.project.brief_fields``
  or a generic id/name/type/app pick);
* over-limit output without explicit switches → auto-brief so the response still
  fits, with the full JSON available via the download URL.

Text streams (``str`` results) and non-paginated native contracts (no
``items`` list) are left untouched.
"""

from __future__ import annotations

import json
from typing import Any

# Field names that make a useful compact view when the spec does not declare
# ``output.project.brief_fields``.
_BRIEF_PREFERRED = ("id", "name", "title", "type", "app", "status", "owner")

# Safety margin: keep the projected object under the character limit.
FIT_MARGIN = 512


def parse_fields(value: Any) -> list[str]:
    """Normalise ``fields``: comma/space separated string or a list of names."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = str(value).replace(";", ",").split(",")
    fields: list[str] = []
    for item in raw:
        for part in str(item).split():
            name = part.strip()
            if name and name not in fields:
                fields.append(name)
    return fields


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def is_projectable(result: Any) -> bool:
    """Only ``{"items": [...]}`` shaped results take part in unified projection."""
    return isinstance(result, dict) and isinstance(result.get("items"), list)


def default_brief_fields(items: list[Any], output_spec: dict[str, Any] | None = None) -> list[str]:
    """Pick brief fields from spec metadata, else from the first items' keys."""
    spec = output_spec or {}
    project_spec = spec.get("project") if isinstance(spec.get("project"), dict) else {}
    declared = project_spec.get("brief_fields")
    if isinstance(declared, list) and declared:
        return [str(name) for name in declared]
    keys: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key in keys:
                continue
            if isinstance(value, (dict, list)):
                continue
            keys.append(str(key))
    preferred = [name for name in _BRIEF_PREFERRED if name in keys]
    rest = [name for name in keys if name not in preferred]
    return (preferred + rest)[:4]


def project(data: Any, spec: dict[str, Any], params: dict[str, Any]) -> Any:
    """Unified projection used by MCP and CLI/serve.

    Spec keys: fields_param, brief_param, brief_fields, allowed_fields.
    Keeps ``items`` as a list and ``total`` as int; adds ``projection.fields``
    and ``projection.ignored`` (requested but not allowed).
    """
    if not isinstance(data, dict):
        return data
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    allowed = [str(name) for name in (spec.get("allowed_fields") or [])]
    fields_param = str(spec.get("fields_param") or "fields")
    brief_param = str(spec.get("brief_param") or "brief")
    brief = _truthy(params.get(brief_param))
    requested = parse_fields(params.get(fields_param))
    brief_fields = [str(name) for name in (spec.get("brief_fields") or [])]

    effective: list[str] = []
    if brief and not requested:
        effective = brief_fields or allowed[:3]
    elif requested:
        effective = requested
    if not effective:
        return data

    ignored: list[str] = []
    if allowed:
        for name in list(effective):
            if name not in allowed:
                ignored.append(name)
        effective = [name for name in effective if name in allowed]

    result = dict(data)
    result["items"] = [
        {key: item[key] for key in effective if key in item}
        for item in items
        if isinstance(item, dict)
    ]
    total = data.get("total")
    result["total"] = total if isinstance(total, int) else len(items)
    result["projection"] = {
        "fields": effective,
        "brief": bool(brief and not requested),
    }
    if ignored:
        result["projection"]["ignored"] = ignored
    return result


def project_items(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Return a copy of *data* whose items only carry *fields* (skips missing)."""
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    projected = [
        {key: item[key] for key in fields if key in item}
        for item in items
        if isinstance(item, dict)
    ]
    result = dict(data)
    result["items"] = projected
    return result


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def fit_items(
    data: dict[str, Any],
    limit: int,
    *,
    base_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """Keep as many items (scalar or object) as fit under *limit*.

    The returned JSON is guaranteed not to exceed *limit* when *limit* >= 2;
    metadata from *data* (e.g. totalPages/status) is preserved. Never returns
    an empty list while items exist unless even the minimal object cannot fit —
    in that case ``returned=0`` is explicit with ``truncated=true``.
    """
    items = data.get("items") if isinstance(data.get("items"), list) else []
    total = data.get("total")
    if not isinstance(total, int):
        total = len(items)
    extras = {k: v for k, v in data.items() if k not in ("items", "total")}
    meta = dict(base_meta or {})
    # Always leave room for the JSON wrapper; small limits must not overshoot.
    budget = max(int(limit) - FIT_MARGIN, 1)

    keep = len(items)
    while keep >= 0:
        payload = dict(extras)
        payload.update(meta)
        payload.update({"items": items[:keep], "returned": keep, "total": total})
        if _encoded_size(payload) <= min(budget, max(int(limit) - 2, 1)):
            return payload, keep
        keep -= 1

    # Minimal fallback: ensure the object itself fits tiny limits.
    minimal = {"truncated": True, "returned": 0, "total": total}
    for key in ("reason", "projected", "limit"):
        if key in meta:
            minimal[key] = meta[key]
    if _encoded_size(minimal) <= max(int(limit), 2):
        return minimal, 0
    return {"truncated": True}, 0
