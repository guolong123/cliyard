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
    """Keep as many projected items as fit under *limit* (with metadata).

    Returns ``(payload, returned_count)`` where ``payload`` already includes the
    truncation/projection metadata and the trimmed ``items`` array.
    """
    items = [item for item in (data.get("items") or []) if isinstance(item, dict)]
    total = data.get("total")
    if not isinstance(total, int):
        total = len(items)
    keep = len(items)
    while keep > 0:
        payload = {k: v for k, v in data.items() if k not in ("items", "total")}
        payload.update(base_meta or {})
        payload.update({
            "items": items[:keep],
            "returned": keep,
            "total": total,
        })
        if _encoded_size(payload) <= max(limit - FIT_MARGIN, 1024):
            return payload, keep
        keep -= 1
    payload = {k: v for k, v in data.items() if k not in ("items", "total")}
    payload.update(base_meta or {})
    payload.update({"items": [], "returned": 0, "total": total})
    return payload, 0
