"""Output formatters — JSON and Rich table rendering."""

from __future__ import annotations

import io
import csv
import io
import json
from typing import Any


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

def format_as_json(data: dict | list | Any, *, indent: int = 2) -> str:
    """Serialise *data* as a human-readable JSON string.

    Args:
        data: Any JSON-serialisable object (dict, list, or scalar).
        indent: Number of spaces for indentation (default 2).

    Returns:
        Formatted JSON string with ``ensure_ascii=False`` so CJK characters
        render directly.
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# YAML formatter
# ---------------------------------------------------------------------------

def format_as_yaml(data: dict | list | Any) -> str:
    """Serialise *data* as a YAML string.

    Args:
        data: Any YAML-serialisable object (dict, list, or scalar).

    Returns:
        YAML string with ``allow_unicode=True`` so CJK characters render
        directly, keys kept in definition order (``sort_keys=False``).
    """
    import yaml

    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


# ---------------------------------------------------------------------------
# Rich table formatter
# ---------------------------------------------------------------------------

def format_as_table(data: dict, fields: list[dict] | None = None, width: int | None = None) -> str:
    """Render *data* as a Rich table.

    Args:
        data: Result dict from :func:`parse_response`
            (``{"items": [...], "total": N, "fields": [...]}``).
        fields: Optional override list of field definitions.
            Each entry: ``{"name": "column_key", "alias": "Display Name"}``.
            If *None*, falls back to ``data["fields"]``.
            If both are empty, all keys from the first item are used.
        width: Console width for table layout (default 200).

    Returns:
        A string containing the rendered table (including ANSI codes for
        terminal display).
    """
    from rich.console import Console
    from rich.table import Table

    items: list[dict] = data.get("items", [])
    if fields is None:
        fields = data.get("fields", [])

    # Auto-detect fields from first item when none are provided.
    if not fields and items:
        fields = [{"name": k, "alias": k} for k in items[0]]

    table = Table(show_lines=False, expand=True)

    for field in fields:
        table.add_column(field.get("alias") or field["name"])

    for item in items:
        row = [_format_field_value(_field_value(item, f), f) for f in fields]
        table.add_row(*row)

    buf = io.StringIO()
    import shutil
    term_width = width or shutil.get_terminal_size((200, 24)).columns
    console = Console(file=buf, width=term_width, force_terminal=False)
    console.print(table)
    return buf.getvalue()


def format_as_csv(data: dict, fields: list[dict]) -> str:
    """Format data as CSV string.

    Args:
        data: Response dict with ``items`` key containing a list of records.
        fields: Field definitions with ``name`` and ``alias`` keys.

    Returns:
        CSV string with header row and data rows.
    """
    items = data.get("items", [])
    if not items:
        return ""

    field_names = [f.get("alias", f["name"]) for f in fields] if fields else list(items[0].keys())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(field_names)
    for item in items:
        row = [_format_field_value(_field_value(item, f), f) for f in fields] if fields else [str(item.get(k, "")) for k in items[0].keys()]
        writer.writerow(row)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Field value helpers
# ---------------------------------------------------------------------------


def _field_value(item: dict, field: dict) -> Any:
    """Extract a field value from *item*."""
    return item.get(field["name"], "")


# ---------------------------------------------------------------------------
# Field value formatting
# ---------------------------------------------------------------------------

def _format_field_value(value: Any, field_def: dict) -> str:
    """Format a single field value according to the field definition.

    Supports the ``format`` key in the field definition:
      - ``format: datetime`` — convert epoch ms to ``YYYY-MM-DD HH:MM:SS``
      - Otherwise — plain string conversion.
    """
    fmt = field_def.get("format")
    if fmt == "datetime" and isinstance(value, (int, float)) and value > 0:
        try:
            from datetime import datetime
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, OverflowError):
            pass
    return str(value) if value is not None else ""


# ---------------------------------------------------------------------------
# Rows-based formatters (fields+rows format, e.g. search results)
# ---------------------------------------------------------------------------

def _fmt_timestamp(val: int, raw: bool = False) -> str:
    """Format epoch milliseconds to human-readable string."""
    if raw:
        return str(val)
    try:
        from datetime import datetime
        return datetime.fromtimestamp(val / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(val)


def format_rows_as_json(result: dict) -> str:
    """Format a fields+rows result dict as a list of key-value objects.

    Converts the raw ``{"fields": [...], "rows": [[...], ...]}`` format into
    ``[{"field1": val1, "field2": val2}, ...]`` for easier programmatic use.

    Args:
        result: Dict with ``fields`` and ``rows`` keys.

    Returns:
        Indented JSON string (list of objects).
    """
    import json
    fields = result.get("fields", [])
    rows = result.get("rows", [])
    field_names = [f["name"] if isinstance(f, dict) else str(f) for f in fields]
    objects = [dict(zip(field_names, row)) for row in rows]
    return json.dumps(objects, indent=2, ensure_ascii=False)


def format_rows_as_table(result: dict, *, raw: bool = False) -> str | None:
    """Format a fields+rows result dict as a Rich table.

    Args:
        result: Dict with ``fields`` (list of dicts with ``name``) and
            ``rows`` (list of lists) keys.
        raw: If True, show timestamps as raw epoch ms.

    Returns:
        Rendered table string, or ``None`` if the result has no data.
    """
    fields = result.get("fields", [])
    rows = result.get("rows", [])
    if not fields or not rows:
        return None

    from rich.console import Console
    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan")
    for f in fields:
        table.add_column(f.get("name", str(f)), overflow="fold")

    for row in rows:
        formatted = []
        for i, val in enumerate(row):
            fn = fields[i].get("name", "") if i < len(fields) else ""
            if fn in ("_time", "timestamp", "time") and isinstance(val, (int, float)):
                formatted.append(_fmt_timestamp(int(val), raw))
            else:
                formatted.append(str(val) if val is not None else "")
        table.add_row(*formatted)

    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False)
    console.print(table)
    return buf.getvalue()
