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


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID_RE = __import__("re").compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-")
_DIGITS_RE = __import__("re").compile(r"^\d{4,}$")


_PREFIXED_ID_RE = __import__("re").compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}$", __import__("re").I)


def _looks_like_map(value: dict) -> bool:
    """True for keyed maps (emails/uuids/ids/prefix ids) not record fields.

    Structural rule: >=2 keys and every value is a container → the keys are
    identifiers, so redact them. Regex fallback covers single-key maps and
    business prefixes such as ``app-search-events-000001``.
    """
    keys = [str(k) for k in value.keys()]
    if not keys:
        return False
    if len(keys) >= 2 and all(isinstance(v, (dict, list)) for v in value.values()):
        return True
    for key in keys:
        if _EMAIL_RE.match(key) or _UUID_RE.match(key) or _DIGITS_RE.match(key):
            return True
        if _PREFIXED_ID_RE.match(key) and any(ch.isdigit() for ch in key):
            return True
    return False


def infer_schema(
    value: Any,
    *,
    max_keys: int = 60,
    max_items: int = 5,
    depth: int = 3,
) -> Any:
    """Describe the *shape* of *value* without carrying the data.

    Lists are sampled (``max_items``) so per-item keys are merged; dicts keep
    at most ``max_keys`` keys; scalars become type names.  This keeps a
    truncation notice small while still telling the caller which fields exist
    and what type each field has, so it can use ``fields``/``brief`` or a
    download URL instead of guessing.
    """
    if isinstance(value, dict):
        if depth > 0 and _looks_like_map(value):
            first = next(iter(value.values()), None)
            return {"<map-key>": _type_name(first), "size": len(value)}
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_keys:
                out["…"] = f"({len(value) - max_keys} more keys)"
                break
            out[str(key)] = infer_schema(item, max_keys=max_keys, max_items=max_items, depth=depth - 1) \
                if depth > 0 else _type_name(item)
        return out
    if isinstance(value, list):
        if not value:
            return []
        merged: dict[str, Any] = {}
        for item in value[:max_items]:
            if isinstance(item, dict):
                for key, val in item.items():
                    if key in merged:
                        continue
                    merged[key] = infer_schema(val, max_keys=max_keys, max_items=max_items, depth=depth - 1) \
                        if depth > 0 else _type_name(val)
        if merged:
            return [merged]
        return [_type_name(value[0])]
    return _type_name(value)


def build_notice(
    original_chars: int,
    limit: int,
    *,
    file: str | None = None,
    url: str | None = None,
    auth: dict[str, Any] | None = None,
    original_bytes: int | None = None,
    schema: Any = None,
    hint: str | None = None,
    reason: str = "output_truncated",
) -> dict[str, Any]:
    """Build the structured truncation notice.

    The notice is a plain JSON object (never a bare string) and always carries
    ``truncated``/``reason``/``limit`` plus a download ``url`` or ``file`` when
    one is available, the full field ``schema`` when the caller can infer it,
    and a machine/human usable ``hint``.
    """
    notice: dict[str, Any] = {
        "truncated": True,
        "reason": reason,
        "bytes": original_bytes if original_bytes is not None else original_chars,
        "chars": original_chars,
        # kept for backward compatibility with the pre-0.21 notice shape
        "original_chars": original_chars,
        "limit": limit,
    }
    if file:
        notice["file"] = file
    if url:
        notice["url"] = url
    if auth:
        notice["auth"] = auth
    if schema is not None:
        notice["schema"] = schema
    notice["hint"] = hint or (
        "输出超过单次上限：请减小 page_size，或使用 fields=brief/字段白名单，"
        "或按 url 下载完整 JSON 后自行筛选"
    )
    return notice


def truncate_text(
    text: str,
    limit: int | None,
    *,
    file: str | None = None,
    url: str | None = None,
    json_safe: bool = True,
    auth: dict[str, Any] | None = None,
    schema: Any = None,
    hint: str | None = None,
    reason: str = "output_truncated",
) -> tuple[str, dict[str, Any] | None]:
    """Truncate *text* over *limit*; return ``(rendered, notice_or_None)``.

    ``json_safe`` emits **only** the structured notice object (valid JSON) for
    machine-readable formats, so callers get an object instead of a truncated
    string; otherwise a text head plus the JSON notice is emitted (human/table
    output, where the caller may append a warning line itself).
    """
    if limit is None or limit <= 0 or len(text) <= limit:
        return text, None
    notice = build_notice(
        len(text),
        limit,
        file=file,
        url=url,
        auth=auth,
        original_bytes=len(text.encode("utf-8")),
        schema=schema,
        hint=hint,
        reason=reason,
    )
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
