"""cliyard.engine.assembler — HTTP request assembler.

Combines YAML method specs + user params into a complete HTTP Request.
Does NOT send requests — only assembles them for the HTTP client layer.

Security model:
- Path and body templates are rendered via sandboxed Jinja2 (Template class).
- No network calls, no file I/O, no authentication logic.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from typing import Any

import yaml

from cliyard.engine.template import Template


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------


@dataclass
class Request:
    """Fully assembled HTTP request (not sent, just structured)."""

    method: str  # GET / POST / PUT / DELETE
    url: str  # Full URL
    headers: dict[str, str]  # Request headers
    query_params: dict[str, str]  # Query parameters (all values str)
    body: dict[str, Any] | None  # JSON body or multipart form fields (or None)
    files: dict[str, Any] | None = None  # File uploads for multipart requests


# ---------------------------------------------------------------------------
# Helper: recursively render templates in a dict/string
# ---------------------------------------------------------------------------


def _render_value(value: Any, variables: dict[str, str]) -> Any:
    """Recursively render Jinja2 templates in a value.

    - str  → render via Template
    - dict → recurse on keys and values
    - list → recurse on each element
    - other → pass through unchanged
    """
    if isinstance(value, str):
        # Quick check: skip rendering if no Jinja2 markers present
        if "{{" not in value and "{%" not in value:
            return value
        try:
            rendered = Template(value).render(**variables)
            # When the rendering context contains list/tuple/dict values, the
            # finalize hook in template.py serializes them as JSON strings.
            # Parse the rendered output back to native Python types so that
            # {{ id }} with id=("3","4") produces ["3","4"] (list) instead
            # of the JSON string "[\"3\",\"4\"]", and {{ spec }} with
            # spec={"kind": "agent"} produces the dict instead of a string.
            if "tojson" in value or any(
                isinstance(v, (list, tuple, dict)) for v in variables.values()
            ):
                try:
                    parsed = json.loads(rendered)
                except (json.JSONDecodeError, ValueError):
                    pass
                else:
                    if "tojson" in value or isinstance(parsed, (dict, list)):
                        return parsed
            return rendered
        except Exception:
            # If rendering fails (missing var), return original
            return value
    elif isinstance(value, dict):
        return {k: _render_value(v, variables) for k, v in value.items()}
    elif isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    else:
        return value


def _to_query_string(value: Any) -> str:
    """Serialize a value for query/header placement.

    dict/list values (e.g. from ``type: json`` params) are serialized as
    JSON so they round-trip correctly instead of becoming Python reprs.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).lower() if isinstance(value, bool) else str(value)


def _parse_rendered_body(rendered: str) -> Any:
    """Parse a rendered request_body string (block scalar) into a body dict.

    ``request_body`` may be written as a YAML block scalar so Jinja
    conditionals (``{% if %}``) survive the spec's YAML parse.  After
    rendering, parse the result back into a mapping.  If the rendered text
    is not valid YAML or does not parse to a mapping, keep it as a literal
    string and warn so template mistakes are visible instead of surfacing
    as an opaque HTTP 400.
    """
    if not rendered or not rendered.strip():
        return rendered
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        warnings.warn(
            f"request_body rendered to invalid YAML: {exc}; "
            "sending as literal string",
            UserWarning,
            stacklevel=2,
        )
        return rendered
    if not isinstance(parsed, dict):
        warnings.warn(
            f"request_body rendered to {type(parsed).__name__}, expected a "
            "mapping; sending as literal string",
            UserWarning,
            stacklevel=2,
        )
        return rendered
    return parsed


def _strip_url_path(url: str) -> tuple[str, str]:
    """Split URL into (scheme+authority, path).

    Example:
        "https://api.example.com/api/v1" → ("https://api.example.com", "/api/v1")
        "https://api.example.com" → ("https://api.example.com", "")
    """
    # Remove trailing slashes for clean join later
    url = url.rstrip("/")

    # Find where the path starts (after scheme://authority)
    match = re.match(r"(https?://[^/]+)(.*)", url)
    if match:
        return match.group(1), match.group(2)

    # Fallback: treat entire string as base, no path
    return url, ""


def _join_path(*parts: str) -> str:
    """Join URL path parts, normalizing slashes.

    >>> _join_path("https://api.example.com", "/api/v1", "repos/myrepo")
    'https://api.example.com/api/v1/repos/myrepo'
    """
    result = parts[0]
    for part in parts[1:]:
        part = part.strip("/")
        if part:
            result = result.rstrip("/") + "/" + part
    return result


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------


def assemble_request(
    method_spec: dict[str, Any],
    params: dict[str, Any],
    base_url: str,
    prefix: str = "",
) -> Request:
    """Assemble an HTTP request from YAML spec + user params.

    Args:
        method_spec: Parsed YAML method definition. Expected keys:
            - http.method: str (GET, POST, etc.)
            - http.path: str (Jinja2 template, e.g. "users/{{ user_id }}")
            - http.headers: dict (optional, static headers)
            - http.query_params: list[dict] (optional, each with "field" key)
            - http.body: dict (optional, JSON body template)
        params: User-supplied parameters. May contain:
            - "query": dict of query param values
            - "header": dict of header values
            - Any top-level key for path/body template rendering
        base_url: Base URL (e.g. "https://api.example.com")
        prefix: Optional URL prefix (e.g. "/api/v1")

    Returns:
        Request with rendered URL, params, headers, and body.

    Raises:
        ValueError: If http.method or http.path is missing from method_spec.
    """
    http = method_spec.get("http", {})

    # --- Validate required fields ---
    method = http.get("method")
    path_template = http.get("path", "")
    if not method:
        raise ValueError("method_spec.http.method is required")

    # --- 1. Render path template ---
    rendered_path = Template(path_template).render(**params) if path_template else ""

    # --- 2. Build full URL ---
    base, base_path = _strip_url_path(base_url)

    # Normalize prefix: ensure leading slash
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix

    # Clean prefix trailing slash (will be joined by _join_path)
    prefix = prefix.rstrip("/")

    full_url = _join_path(base, base_path, prefix, rendered_path)

    # --- 3. Collect query params ---
    query_params: dict[str, str] = {}

    # Build field_name mapping from method_spec params
    field_map: dict[str, str] = {}
    for location in ("query", "header"):
        for param in method_spec.get("params", {}).get(location, []):
            name = param.get("name", "")
            field = param.get("field", name)
            if name:
                field_map[name] = field

    # Static query params from spec (dict format: {key: value})
    spec_query_dict = http.get("query", {})
    if isinstance(spec_query_dict, dict):
        for k, v in spec_query_dict.items():
            rendered = _render_value(v, params)
            if rendered is not None and rendered != "":
                query_params[k] = _to_query_string(rendered)

    # Static query params from spec (list format: [{field: name, default: val}])
    spec_query = http.get("query_params", [])
    if isinstance(spec_query, list):
        for qp in spec_query:
            if isinstance(qp, dict):
                field_name = qp.get("field", "")
                if field_name:
                    value = params.get(field_name, qp.get("default"))
                    if value is not None and value != "":
                        query_params[field_name] = _to_query_string(value)

    # User-provided query params (override spec defaults)
    user_query = params.get("query", {})
    if isinstance(user_query, dict):
        for k, v in user_query.items():
            if v is not None and v != "":
                api_key = field_map.get(k, k)
                query_params[api_key] = _to_query_string(v)

    # --- 4. Collect headers ---
    headers: dict[str, str] = {}

    # Static headers from spec
    spec_headers = http.get("headers", {})
    if isinstance(spec_headers, dict):
        for k, v in spec_headers.items():
            headers[k] = _to_query_string(v)

    # User-provided headers (override spec)
    user_headers = params.get("header", {})
    if isinstance(user_headers, dict):
        for k, v in user_headers.items():
            api_key = field_map.get(k, k)
            headers[api_key] = _to_query_string(v)

    # --- 5. Build body (JSON or multipart) ---
    body: dict[str, Any] | None = None
    files: dict[str, Any] | None = None

    if method_spec.get("body_type") == "multipart":
        # --- 5a. Multipart: fields → query params, file → files ---
        body_params_spec = method_spec.get("params", {}).get("body", [])
        user_body = params.get("body", {})
        file_path = None

        for param in body_params_spec:
            name = param["name"]
            field_name = param.get("field", name)
            value = params.get(name)
            if value is None and isinstance(user_body, dict):
                value = user_body.get(name)
            if value is None:
                value = param.get("default")

            if param.get("type") == "file":
                if value:
                    file_path = value
            elif value is not None:
                query_params[field_name] = _to_query_string(value)

        # Set file upload from the parsed file_path
        if file_path:
            import os
            file_name = os.path.basename(file_path)
            files = {"file": (file_name, open(file_path, "rb"), "application/octet-stream")}

        body = None
    else:
        # --- 5b. Standard JSON body ---
        spec_body = http.get("body")
        if spec_body is not None:
            body = _render_value(spec_body, params)
        else:
            # Check for top-level request_body template (cliyard YAML format)
            req_body = method_spec.get("request_body")
            if req_body is not None:
                body = _render_value(req_body, params)
                if isinstance(body, str):
                    body = _parse_rendered_body(body)
            else:
                # Fallback: use body params from method_spec with field mapping
                body_params = params.get("body", {})
                if body_params:
                    # Apply field mapping to body params
                    body = {}
                    for param in method_spec.get("params", {}).get("body", []):
                        name = param["name"]
                        field = param.get("field", name)
                        if name in body_params:
                            body[field] = body_params[name]
                    if not body:
                        body = body_params

    return Request(
        method=method.upper(),
        url=full_url,
        headers=headers,
        query_params=query_params,
        body=body,
        files=files,
    )
