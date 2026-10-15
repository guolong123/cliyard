"""Output size guard — truncation with structured notice and overflow handoff."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any
from uuid import uuid4

DEFAULT_MAX_OUTPUT_CHARS = 50000
OUTPUT_LIMIT_ENV = "CLIYARD_MAX_OUTPUT_CHARS"
LOCAL_OVERFLOW_DIR = os.path.join(tempfile.gettempdir(), "cliyard-outputs")
LOCAL_OVERFLOW_PREFIX = "cliyard-output-"
OUTPUT_TTL_ENV = "CLIYARD_OUTPUT_TTL_S"
OUTPUT_QUOTA_BYTES_ENV = "CLIYARD_OUTPUT_QUOTA_BYTES"
OUTPUT_QUOTA_FILES_ENV = "CLIYARD_OUTPUT_QUOTA_FILES"
DEFAULT_OUTPUT_TTL_S = 1800
DEFAULT_OUTPUT_QUOTA_BYTES = 200 * 1024 * 1024
DEFAULT_OUTPUT_QUOTA_FILES = 200


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def resolve_max_chars(explicit: int | None = None) -> int:
    """Resolve the character limit: explicit value > env > default (50000)."""
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
    auth: dict[str, Any] | None = None,
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
    if auth:
        notice["auth"] = auth
    return notice


def truncate_text(
    text: str,
    limit: int | None,
    *,
    file: str | None = None,
    url: str | None = None,
    json_safe: bool = True,
    auth: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Truncate *text* over *limit*; return ``(rendered, notice_or_None)``.

    ``json_safe`` emits only the notice object (valid JSON) for machine-readable
    formats; otherwise a text head plus the JSON notice is emitted.
    """
    if limit is None or limit <= 0 or len(text) <= limit:
        return text, None
    notice = build_notice(len(text), limit, file=file, url=url, auth=auth)
    if json_safe:
        return json.dumps(notice, ensure_ascii=False, indent=2), notice
    return text[:limit] + "\n" + json.dumps(notice, ensure_ascii=False), notice


def write_local_overflow(text: str, dir_path: str | None = None) -> str:
    """Persist *text* locally, sweeping expired/over-quota outputs first."""
    base = dir_path or LOCAL_OVERFLOW_DIR
    os.makedirs(base, exist_ok=True)
    _sweep_local(base)
    path = os.path.join(base, f"{LOCAL_OVERFLOW_PREFIX}{uuid4().hex[:8]}.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _sweep_local(base: str) -> None:
    """TTL + quota cleanup for local overflow files (prefix-scoped, oldest-first)."""
    ttl = _env_int(OUTPUT_TTL_ENV, DEFAULT_OUTPUT_TTL_S)
    quota_bytes = _env_int(OUTPUT_QUOTA_BYTES_ENV, DEFAULT_OUTPUT_QUOTA_BYTES)
    quota_files = _env_int(OUTPUT_QUOTA_FILES_ENV, DEFAULT_OUTPUT_QUOTA_FILES)
    now = time.time()
    try:
        scanned = list(os.scandir(base))
    except OSError:
        return
    kept: list[tuple[float, int, str]] = []
    for entry in scanned:
        try:
            if not entry.name.startswith(LOCAL_OVERFLOW_PREFIX):
                continue
            if entry.is_symlink() or not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        if now - stat.st_mtime > ttl:
            try:
                os.unlink(entry.path)
                continue
            except OSError:
                pass
        kept.append((stat.st_mtime, stat.st_size, entry.path))
    kept.sort(key=lambda item: item[0])
    total = sum(size for _, size, _ in kept)
    count = len(kept)
    index = 0
    while index < len(kept) and (total > quota_bytes or count > quota_files):
        _, size, path = kept[index]
        try:
            os.unlink(path)
            total -= size
            count -= 1
        except OSError:
            pass
        index += 1


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
