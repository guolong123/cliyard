# Learnings — mcp-file-client-origin

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## 2026-09-14 — todo 1: upload store 接口定稿（todos 2/5 消费）
- 模块 `src/cliyard/server/uploads.py`；常量 `UPLOAD_TTL_S=1800`、`UPLOAD_QUOTA_BYTES=500MiB`、`UPLOAD_QUOTA_FILES=1000`；`MAX_UPLOAD_BYTES` 从 `cliyard.server.executor` import（单一真相源，不重定义）。
- `save_upload(filename, data, upload_dir=None) -> {path, file_name, bytes, expires_at}`：服务端名 `cliyard-upload-<8hex>-<sanitized>`；O_EXCL 独占创建（0600）；超限抛 `CliyError`（todo 2 映射 413）；附带 `expires_at`（ISO8601 带时区，供 todo 2 响应直接用）。
- `sweep(upload_dir=None, now=None) -> {removed, freed_bytes, kept}`：单次非递归 `os.scandir`，仅 mtime/size；跳过 symlink/子目录/无前缀文件；unlink 前 realpath 复核仍在目录内；失败 `logger.warning` 永不抛错。
- `validate_dir(upload_dir, spec_dir=None) -> resolved`：拒绝 `/`、`/tmp`、`/var`、`/etc`、`$HOME`（resolve 后精确相等比较，子目录放行——默认目录即 `<tmp>/cliyard-uploads`）、spec 自身及祖先；通过后 `makedirs`（fail fast）。
- `is_managed(path, upload_dir=None) -> bool`；`assert_server_readable(path, upload_dir, allow_dirs=None) -> realpath`（越狱/缺失均 `CliyError` 带重传指引；`allow_dirs` 接受 list 或 `os.pathsep` 字符串）；`redact_upload_path(msg, upload_dir)` 替换 realpath 与 abspath 两种别名为 `<upload_dir>`。
- 默认目录 `DEFAULT_UPLOAD_DIR=<tmp>/cliyard-uploads`（todo 3 `--upload-dir` 缺省值须与此一致）。
