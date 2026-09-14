"""``cliyard.server.uploads`` — 上传文件 store（MCP/serve 文件交接点）。

有 shell 的 agent 先 ``POST /upload`` 把文件送到 server，拿到 server
绝对路径后再照常调用业务工具；本模块只管落盘文件的生命周期与路径安全：

* :func:`save_upload` —— 保存上传字节，文件名形如
  ``cliyard-upload-<8hex>-<清洗后原名>``（随机前缀风格与
  ``server.executor._write_base64_temp_file`` 一致）；
* :func:`sweep` —— 搭车清理（过期 / 超配额 oldest-first），只删前缀匹配项，
  外来文件 / 子目录 / symlink 纹丝不动；
* :func:`validate_dir` —— 启动期危险目录 fail fast；
* :func:`is_managed` / :func:`assert_server_readable` —— 路径 jail；
* :func:`redact_upload_path` —— 错误文案脱敏（上传目录真值 → ``<upload_dir>``）。

配额与保留期为代码常量（v1 不做 CLI 旗标，见计划 mcp-file-client-origin）：

* ``UPLOAD_TTL_S = 1800``（30min，窗口期内可重放，不消费删除）；
* ``UPLOAD_QUOTA_BYTES = 500MB``；
* ``UPLOAD_QUOTA_FILES = 1000``。

单文件大小上限复用 ``server.executor.MAX_UPLOAD_BYTES``（10MB，单一真相源）。
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cliyard.engine.errors import CliyError
from cliyard.server.executor import MAX_UPLOAD_BYTES

logger = logging.getLogger("cliyard.server.uploads")

# 保留期：上传文件自 mtime 起存活 30min（窗口期内可重放，不消费删除）。
UPLOAD_TTL_S = 1800
# 配额：超限时按 mtime 最老优先淘汰（oldest-first）。
UPLOAD_QUOTA_BYTES = 500 * 1024 * 1024
UPLOAD_QUOTA_FILES = 1000

# 服务端文件名固定前缀 —— sweep 只认此前缀，调用方路径永不作为删除输入。
UPLOAD_FILENAME_PREFIX = "cliyard-upload-"

# 默认上传目录（与 ``--upload-dir`` 缺省值一致）：系统临时目录下独立子目录。
DEFAULT_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "cliyard-uploads")

# 文件名 allowlist：allowlist 之外字符一律替换为 ``_``，保留可读后缀供 UX。
_FILENAME_KEEP_RE = re.compile(r"[^A-Za-z0-9._-]+")


def default_upload_dir() -> str:
    """默认上传目录（系统临时目录下的 ``cliyard-uploads``）。"""
    return DEFAULT_UPLOAD_DIR


def _resolve_dir(upload_dir: str | os.PathLike[str] | None) -> str:
    """上传目录解析为绝对路径（realpath，不跟随不存在路径抛错）。"""
    base = str(upload_dir) if upload_dir else DEFAULT_UPLOAD_DIR
    return os.path.realpath(os.path.abspath(os.path.expanduser(base)))


def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（带时区，与 executor._now_iso 同格式）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def sanitize_filename(filename: str) -> str:
    """清洗客户端文件名，保留可读后缀。

    ``basename`` 去目录成分（含 ``../`` 与 Windows 分隔符），allowlist
    之外字符替换为 ``_``，去首部 ``.``（防隐藏文件），空结果回退 ``upload``，
    超长截断至 100 字符。
    """
    name = str(filename or "")
    name = name.replace("\\", "/")
    name = os.path.basename(name.strip())
    name = name.lstrip(".")
    name = _FILENAME_KEEP_RE.sub("_", name)
    name = name.strip("._")
    if not name:
        name = "upload"
    if len(name) > 100:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[: 100 - len(ext) - 1] + dot + ext
        else:
            name = name[:100]
    return name


def save_upload(
    filename: str,
    data: bytes,
    upload_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """保存上传字节到上传目录，返回 ``{path, file_name, bytes, expires_at}``。

    服务端文件名固定为 ``cliyard-upload-<8hex>-<清洗后原名>``；``data`` 超过
    ``MAX_UPLOAD_BYTES`` 时抛 ``CliyError``（由路由映射为 413）。

    Args:
        filename: 客户端原始文件名（仅取清洗后后缀，不作路径使用）。
        data: 文件字节。
        upload_dir: 上传目录（缺省默认目录）。

    Raises:
        CliyError: 数据超限或落盘失败。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise CliyError(f"upload data 必须为 bytes，得到 {type(data).__name__}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise CliyError(
            f"upload 文件过大：{len(data)} 字节，上限 {MAX_UPLOAD_BYTES} 字节"
        )
    resolved_dir = _resolve_dir(upload_dir)
    try:
        os.makedirs(resolved_dir, exist_ok=True)
    except OSError as exc:
        raise CliyError(f"upload 目录不可写：{resolved_dir}：{exc}") from exc
    safe = sanitize_filename(filename)
    # O_EXCL 独占创建防碰撞（极小概率 8hex 碰撞时重试）。
    for _ in range(5):
        server_name = f"{UPLOAD_FILENAME_PREFIX}{uuid4().hex[:8]}-{safe}"
        dest = os.path.join(resolved_dir, server_name)
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(bytes(data))
        except OSError as exc:
            # 部分写入不留残件：配额统计只认完整文件，截断文件必须删掉。
            try:
                os.unlink(dest)
            except OSError:
                logger.warning("upload 部分写入清理失败 %s", dest)
            raise CliyError(f"upload 落盘失败：{exc}") from exc
        return {
            "path": dest,
            "file_name": server_name,
            "bytes": len(data),
            "expires_at": _expires_at_iso(),
        }
    raise CliyError("upload 落盘失败：文件名碰撞，请重试")


def _expires_at_iso(now: float | None = None) -> str:
    """按 TTL 推算的过期时间 ISO 8601 字符串。"""
    base = now if now is not None else time.time()
    return (
        datetime.fromtimestamp(base + UPLOAD_TTL_S)
        .astimezone()
        .isoformat(timespec="milliseconds")
    )


def sweep(
    upload_dir: str | os.PathLike[str] | None = None,
    now: float | None = None,
) -> dict[str, int]:
    """搭车清理上传目录，返回 ``{removed, freed_bytes, kept}``。

    单次非递归 ``os.scandir`` 一遍、逐项仅取 mtime/size（不 open 文件）：

    * 跳过子目录 / symlink / 无前缀外来文件（零误删）；
    * 先删过期项（``now - mtime > UPLOAD_TTL_S``）；
    * 再按配额（个数 / 总字节）mtime 最老优先淘汰。

    删除按名 unlink，unlink 前 realpath 复核仍在目录内（防 TOCTOU
    symlink 替换）；清理失败记 ``logger.warning``，永不抛错（搭车执行
    不得影响主链路）。
    """
    resolved_dir = _resolve_dir(upload_dir)
    moment = now if now is not None else time.time()
    try:
        entries = list(os.scandir(resolved_dir))
    except FileNotFoundError:
        return {"removed": 0, "freed_bytes": 0, "kept": 0}
    except OSError as exc:
        logger.warning("upload sweep 扫描目录失败 %s: %s", resolved_dir, exc)
        return {"removed": 0, "freed_bytes": 0, "kept": 0}

    managed: list[tuple[float, int, str]] = []  # (mtime, size, name)
    for entry in entries:
        try:
            is_symlink = entry.is_symlink()
        except OSError as exc:
            logger.debug("upload sweep 跳过不可判定项 %s: %s", entry.name, exc)
            continue
        if is_symlink:
            logger.debug("upload sweep 跳过 symlink %s", entry.name)
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue  # 子目录等非文件跳过
        except OSError as exc:
            logger.debug("upload sweep 跳过不可判定项 %s: %s", entry.name, exc)
            continue
        if not entry.name.startswith(UPLOAD_FILENAME_PREFIX):
            logger.debug("upload sweep 跳过外来文件 %s", entry.name)
            continue  # 外来文件：纹丝不动
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.debug("upload sweep 跳过不可 stat 项 %s: %s", entry.name, exc)
            continue
        managed.append((stat.st_mtime, stat.st_size, entry.name))

    removed = 0
    freed_bytes = 0

    def _unlink(name: str) -> int:
        """按名删除（realpath 复核仍在目录内），返回释放字节数（失败 -1）。"""
        nonlocal removed
        candidate = os.path.join(resolved_dir, name)
        try:
            real = os.path.realpath(candidate)
            if os.path.dirname(real) != resolved_dir:
                return -1
            if os.path.islink(candidate) or not os.path.isfile(candidate):
                return -1
            size = os.path.getsize(candidate)
            os.unlink(candidate)
            removed += 1
            return size
        except OSError as exc:
            logger.warning("upload sweep 删除失败 %s: %s", name, exc)
            return -1

    survivors: list[tuple[float, int, str]] = []
    for mtime, size, name in managed:
        if moment - mtime > UPLOAD_TTL_S:
            freed = _unlink(name)
            if freed >= 0:
                freed_bytes += freed
            else:
                survivors.append((mtime, size, name))
        else:
            survivors.append((mtime, size, name))

    # 配额淘汰：最老优先。
    survivors.sort(key=lambda item: item[0])
    total_bytes = sum(size for _, size, _ in survivors)
    while len(survivors) > UPLOAD_QUOTA_FILES or total_bytes > UPLOAD_QUOTA_BYTES:
        _, size, name = survivors.pop(0)
        freed = _unlink(name)
        if freed >= 0:
            freed_bytes += freed
            total_bytes -= size
        else:
            total_bytes -= size

    return {"removed": removed, "freed_bytes": freed_bytes, "kept": len(survivors)}


def validate_dir(
    upload_dir: str | os.PathLike[str] | None,
    spec_dir: str | os.PathLike[str] | None = None,
) -> str:
    """启动期校验上传目录，返回 resolve 后绝对路径。

    拒绝清单（resolve 后比较）：``/``、``/tmp``、``/var``、``/etc``、
    ``$HOME``、spec_dir 自身及其所有祖先目录；其他目录放行（仍受文件名
    前缀 gate 与 jail 保护）。校验通过后确保目录存在（fail fast）。

    Raises:
        CliyError: 危险目录或无法创建。
    """
    if upload_dir is None or str(upload_dir).strip() == "":
        raise CliyError("upload 目录不能为空")
    try:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(str(upload_dir))))
    except Exception as exc:
        raise CliyError(f"upload 目录无法解析：{upload_dir}：{exc}") from exc
    if not os.path.isabs(resolved):
        raise CliyError(f"upload 目录必须为绝对路径：{upload_dir}")

    denied: list[str] = [os.path.sep]
    for raw in ("/tmp", "/var", "/etc"):
        try:
            denied.append(os.path.realpath(os.path.abspath(raw)))
        except Exception:
            denied.append(raw)
    home = os.path.expanduser("~")
    if home and home != "~":
        try:
            denied.append(os.path.realpath(os.path.abspath(home)))
        except Exception:
            pass
    if resolved in denied:
        raise CliyError(f"upload 目录不允许使用系统/家目录：{resolved}")

    if spec_dir is not None:
        try:
            spec_resolved = os.path.realpath(
                os.path.abspath(os.path.expanduser(str(spec_dir)))
            )
        except Exception as exc:
            raise CliyError(f"spec 目录无法解析：{spec_dir}：{exc}") from exc
        if spec_resolved == resolved or spec_resolved.startswith(resolved + os.path.sep):
            raise CliyError(
                f"upload 目录不得为 spec 目录自身或其祖先：{resolved}"
            )

    try:
        os.makedirs(resolved, exist_ok=True)
    except OSError as exc:
        raise CliyError(f"upload 目录无法创建：{resolved}：{exc}") from exc
    return resolved


def is_managed(
    path: str | os.PathLike[str],
    upload_dir: str | os.PathLike[str] | None = None,
) -> bool:
    """realpath 落在上传目录内才 ``True``（symlink 跟随到目标判定）。"""
    try:
        resolved_dir = _resolve_dir(upload_dir)
        real = os.path.realpath(os.path.abspath(str(path)))
        return real == resolved_dir or real.startswith(resolved_dir + os.path.sep)
    except Exception:
        return False


def assert_server_readable(
    path: str | os.PathLike[str],
    upload_dir: str | os.PathLike[str] | None = None,
    allow_dirs: list[str] | tuple[str, ...] | str | None = None,
) -> str:
    """断言 server 侧可读：realpath 必须在上传目录或 allowlist 内。

    越狱（目录外）与缺失文件均抛 ``CliyError``（调用方映射为 isError /
    可执行错误，不泄露文件内容）。上传目录内托管文件另受 TTL 约束：
    mtime 超过 ``UPLOAD_TTL_S`` 即按过期拒绝（过期但尚未被搭车 sweep
    扫掉的文件不得永久可读）；allowlist 目录文件不受 TTL 约束。

    Args:
        path: 调用方传来的 server 路径（桥接前的原值）。
        upload_dir: 上传目录。
        allow_dirs: ``--file-allow-dirs`` 额外放行目录（list 或 ``os.pathsep`` 分隔串）。

    Returns:
        realpath 解析后的绝对路径。

    Raises:
        CliyError: jail 越狱、文件不存在或托管文件已过期。
    """
    if isinstance(allow_dirs, str):
        allow_list = [d for d in allow_dirs.split(os.pathsep) if d.strip()]
    else:
        allow_list = [str(d) for d in (allow_dirs or []) if str(d).strip()]
    roots = [_resolve_dir(upload_dir)]
    for extra in allow_list:
        try:
            roots.append(os.path.realpath(os.path.abspath(os.path.expanduser(extra))))
        except Exception:
            continue
    real = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    if not any(real == root or real.startswith(root + os.path.sep) for root in roots):
        raise CliyError(
            f"文件路径不在允许范围内：{redact_upload_path(str(path), _resolve_dir(upload_dir))}。"
            f"请先 POST /upload 上传文件，再使用返回的 path 调用"
        )
    if not os.path.isfile(real):
        raise CliyError(
            f"文件不存在或已过期清理：{redact_upload_path(real, _resolve_dir(upload_dir))}。"
            f"请重新 POST /upload 上传"
        )
    if is_managed(real, upload_dir):
        try:
            age = time.time() - os.path.getmtime(real)
        except OSError:
            raise CliyError(
                f"文件不存在或已过期清理：{redact_upload_path(real, _resolve_dir(upload_dir))}。"
                f"请重新 POST /upload 上传"
            ) from None
        if age > UPLOAD_TTL_S:
            raise CliyError(
                f"文件不存在或已过期清理：{redact_upload_path(real, _resolve_dir(upload_dir))}。"
                f"请重新 POST /upload 上传"
            )
    return real


def redact_upload_path(
    msg: str,
    upload_dir: str | os.PathLike[str] | None = None,
) -> str:
    """把文本中的上传目录绝对路径替换为 ``<upload_dir>``（与 ``<spec_dir>`` 并列脱敏）。"""
    text = str(msg)
    candidates: list[str] = []
    try:
        candidates.append(_resolve_dir(upload_dir))
    except Exception:
        pass
    try:
        raw = str(upload_dir) if upload_dir else DEFAULT_UPLOAD_DIR
        candidates.append(os.path.abspath(os.path.expanduser(raw)))
    except Exception:
        pass
    for candidate in candidates:
        if candidate and candidate in text:
            text = text.replace(candidate, "<upload_dir>")
    return text


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_TTL_S",
    "UPLOAD_QUOTA_BYTES",
    "UPLOAD_QUOTA_FILES",
    "UPLOAD_FILENAME_PREFIX",
    "DEFAULT_UPLOAD_DIR",
    "default_upload_dir",
    "sanitize_filename",
    "save_upload",
    "sweep",
    "validate_dir",
    "is_managed",
    "assert_server_readable",
    "redact_upload_path",
]
