"""HTTP response parsing using JSONPath expressions."""

from __future__ import annotations

from typing import Any

import requests
from jsonpath_ng.ext import parse as jp_parse


class ResponseParseError(Exception):
    """Raised when JSONPath extraction fails."""


def parse_response(response: requests.Response | dict, output_spec: dict) -> dict:
    """Parse an API response using JSONPath to locate data.

    Args:
        response: HTTP response object or pre-parsed dict (already checked
            for 2xx by the caller).
        output_spec: Output specification from YAML service definition.

            - items_path (str): JSONPath expression for the list of items,
              e.g. ``"$.data.items"``.
            - total_path (str, optional): JSONPath expression for the total count,
              e.g. ``"$.data.total"``.
            - fields (list[dict], optional): Field definitions (name, alias).
              Stored in result but not used during parsing.

    Returns:
        ``{"items": [...], "total": N}``.
        ``total`` defaults to ``len(items)`` when *total_path* is absent.

    Raises:
        ResponseParseError: If JSONPath extraction fails (bad path, non-JSON body,
            or no match).
    """
    # --- 1. Deserialize body ---------------------------------------------------
    if isinstance(response, (dict, list)):
        data = response
    else:
        try:
            data = response.json()
        except ValueError as exc:
            raise ResponseParseError(
                f"Response body is not valid JSON: {exc}"
            ) from exc

    # --- 2. Extract items via JSONPath -----------------------------------------
    items_path = output_spec.get("items_path")
    if not items_path:
        raise ResponseParseError("output_spec missing required 'items_path'")

    items = _extract(data, items_path)

    if not isinstance(items, list):
        items = [items] if items is not None else []

    # --- 3. Extract total (optional) -------------------------------------------
    total_path = output_spec.get("total_path")
    if total_path:
        total = _extract(data, total_path)
        if not isinstance(total, int):
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(items)
    else:
        total = len(items)

    # --- 4. Collect field metadata (pass-through) -----------------------------
    fields = output_spec.get("fields", [])

    return {"items": items, "total": total, "fields": fields}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract(root: Any, path: str) -> Any:
    """Evaluate a JSONPath expression against *root* and return the first match.

    Raises ResponseParseError if the path yields no matches.
    """
    try:
        expr = jp_parse(path)
    except Exception as exc:
        raise ResponseParseError(f"Invalid JSONPath '{path}': {exc}") from exc

    matches = expr.find(root)
    if not matches:
        import json as _json
        _body = _json.dumps(root, indent=2, ensure_ascii=False)[:2000]
        raise ResponseParseError(f"JSONPath '{path}' 未匹配到数据\n原始响应:\n{_body}")

    # Return a single value if only one match; otherwise return a list.
    if len(matches) == 1:
        return matches[0].value
    return [m.value for m in matches]
