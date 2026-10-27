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
    total_pages_spec = output_spec.get("total_pages")
    if isinstance(total_pages_spec, dict):
        data = add_total_pages(data, total_pages_spec, params)
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
    """Backward-compatible wrapper: single implementation lives in projection."""
    from cliyard.output.projection import project as _project

    return _project(data, spec, params)


def add_total_pages(data: Any, spec: dict[str, Any], params: dict[str, Any]) -> Any:
    """Add ``totalPages`` from ``total`` and the effective page size.

    Server-side paginated APIs already return the real ``total``; this helper
    only computes the page count and never slices ``items`` (unlike
    :func:`paginate`). Spec: ``output.total_pages: {size_param: per_page}``.
    """
    if not isinstance(data, dict):
        return data
    total = data.get("total")
    if not isinstance(total, int):
        return data
    size = _as_int(params.get(spec.get("size_param", "page_size")))
    if not size:
        size = _as_int(spec.get("default_size"))
    if not size or size <= 0:
        return data
    import math

    result = dict(data)
    result["totalPages"] = int(math.ceil(total / size))
    return result
    return result
