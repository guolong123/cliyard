"""Output size guard — truncation with structured notice and overflow handoff."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from uuid import uuid4

DEFAULT_MAX_OUTPUT_CHARS = 50000
OUTPUT_LIMIT_ENV = "CLIYARD_MAX_OUTPUT_CHARS"
LOCAL_OVERFLOW_DIR = os.path.join(tempfile.gettempdir(), "cliyard-outputs")


def resolve_max_chars(explicit: int | None = None) -> int:
    """Resolve the character limit: explicit value > env > default (10000)."""
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    raw = os.environ.get(OUTPUT_LIMIT_ENV)
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_OUTPUT_CHARS


def build_notice(
    original_chars: int,
    limit: int,
    *,
    file: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Build the structured truncation notice."""
    notice: dict[str, Any] = {
        "truncated": True,
        "original_chars": original_chars,
        "limit": limit,
    }
    if file:
        notice["file"] = file
    if url:
        notice["url"] = url
    return notice


def truncate_text(
    text: str,
    limit: int | None,
    *,
    file: str | None = None,
    url: str | None = None,
    json_safe: bool = True,
) -> tuple[str, dict[str, Any] | None]:
    """Truncate *text* over *limit*; return ``(rendered, notice_or_None)``.

    ``json_safe`` emits only the notice object (valid JSON) for machine-readable
    formats; otherwise a text head plus the JSON notice is emitted.
    """
    if limit is None or limit <= 0 or len(text) <= limit:
        return text, None
    notice = build_notice(len(text), limit, file=file, url=url)
    if json_safe:
        return json.dumps(notice, ensure_ascii=False, indent=2), notice
    return text[:limit] + "\n" + json.dumps(notice, ensure_ascii=False), notice


def write_local_overflow(text: str, dir_path: str | None = None) -> str:
    """Persist *text* locally and return its absolute path."""
    base = dir_path or LOCAL_OVERFLOW_DIR
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"cliyard-output-{uuid4().hex[:8]}.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def human_warning(notice: dict[str, Any]) -> str:
    """Render a human-readable warning line for *notice*."""
    parts = [
        f"⚠ 输出超过 {notice.get('limit')} 字符已截断"
        f"（原始 {notice.get('original_chars')} 字符）"
    ]
    if notice.get("file"):
        parts.append(f"完整内容: {notice['file']}")
    if notice.get("url"):
        parts.append(f"下载: {notice['url']}")
    return "；".join(parts)
