"""Output-layer pagination and summary transforms (opt-in via ``output`` spec)."""

from __future__ import annotations

from typing import Any


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def apply_output_transforms(
    data: Any, output_spec: dict[str, Any], params: dict[str, Any]
) -> Any:
    """Apply ``output.summary`` / ``output.project`` / ``output.paginate`` when opted in."""
    summary_spec = output_spec.get("summary")
    if isinstance(summary_spec, dict) and _truthy(params.get("summary")):
        return summarize(data, summary_spec)
    project_spec = output_spec.get("project")
    if isinstance(project_spec, dict):
        data = project(data, project_spec, params)
    paginate_spec = output_spec.get("paginate")
    if isinstance(paginate_spec, dict):
        return paginate(data, paginate_spec, params)
    return data


def paginate(
    data: Any, spec: dict[str, Any], params: dict[str, Any]
) -> Any:
    """Slice ``data["items"]`` by page/page_size; no-op unless page_size given."""
    if not isinstance(data, dict):
        return data
    items = data.get("items")
    if not isinstance(items, list):
        return data
    size = _as_int(params.get(spec.get("size_param", "page_size")))
    if size is None or size <= 0:
        return data
    page = _as_int(params.get(spec.get("page_param", "page"))) or 1
    page = max(page, 1)
    total = data.get("total")
    if not isinstance(total, int):
        total = len(items)
    start = (page - 1) * size
    result = dict(data)
    result["items"] = items[start : start + size]
    result["total"] = total
    result["page"] = page
    result["page_size"] = size
    return result


def summarize(data: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Aggregate items into per-field value counts plus an optional field distribution."""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    group_by: list[str] = [str(f) for f in (spec.get("group_by") or [])]
    groups: dict[str, dict[str, int]] = {field: {} for field in group_by}
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in group_by:
            key = str(item.get(field))
            bucket = groups[field]
            bucket[key] = bucket.get(key, 0) + 1
    result: dict[str, Any] = {"summary": {"total": len(items)}, "groups": groups}
    health_field = spec.get("health_field")
    if health_field:
        dist: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get(health_field, "unknown"))
            dist[key] = dist.get(key, 0) + 1
        result["summary"]["distribution"] = dist
    return result


def _as_field_list(value: Any) -> list[str]:
    """Normalise ``fields`` argument: comma/space separated string or a list."""
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


def project(data: Any, spec: dict[str, Any], params: dict[str, Any]) -> Any:
    """Project list items to requested fields (``fields``/``brief``).

    This is a generic output capability: the spec opts in and declares the
    field whitelist / brief field set, so the same code serves every product.

    Spec keys:
        fields_param (default ``fields``): param holding comma-separated names
            or a list of names.
        brief_param (default ``brief``): truthy → use ``brief_fields``.
        brief_fields: fields returned when ``brief`` is true (default: the
            first three ``allowed_fields``).
        allowed_fields: optional whitelist; unknown/not-allowed names are
            ignored and reported under ``projection.ignored``.

    Result keeps ``items`` / ``total`` (always list / int) and adds a small
    ``projection`` metadata block so callers can see what was applied.
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
    requested = _as_field_list(params.get(fields_param))
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
