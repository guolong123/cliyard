# Learnings — mcp-file-client-origin

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## 2026-09-14 — todo 2: POST /upload 双 app 挂载（commit dbc6607）

- **Shim 预案未启用（LOUD NOTE）**：开工时 `src/cliyard/server/uploads.py` 不存在，已按 plan 写好同名 shim；实施中途 todo 1 lane 落地并提交了 `9b97daa`（真接口 `save_upload(filename, data, upload_dir) -> {path,file_name,bytes,expires_at}` / `sweep(upload_dir)` / 超限抛 `CliyError`），遂删除 shim、直连真接口。`upload.py` 头部 docstring 记录了此事。
- **真接口返回已含 `file_name`/`expires_at`**：handler 不再自算过期时间，直接透传三字段；`CliyError` → 413（uploads 模块契约原文"由路由映射为 413"）；`upload_dir` 取自 `app.state.upload_dir`（getattr 缺省 None，todo 3 负责写入 state）。
- **`mount_mcp_http` 需父级自有 `/upload` 路由**：子 app 以 `Route(endpoint=mcp_app)` 形式被调用时其内部路由表不参与父级 path 匹配，故父级 `insert(0, Route("/upload", ...))`，与 `/mcp` 同理插最前避开 serve `/` 静态兜底。
- **FastAPI 0.141 的 `app.routes` 含 `_IncludedRouter`**：`include_router` 注册后按 path grep 路由表查不到 `/api/*`（deferred include），探活必须走真实请求（TestClient/curl），不要断言路由表。
- **并行 wave 同树作业的提交隔离**：todo 3 lane 的未提交改动与本 todo 同文件交织（`app.py`/`mcp/server.py`）；用 hunk 级过滤（`git apply --cached` 精选本 todo hunks + `git add` 新文件）保证提交只含本 todo 内容。另：stage 与 commit 之间被疑似并行 lane 的 index 操作打掉过一次（commit 漏文件），已用同命令内 stage+amend 关闭竞态窗口。
- **验证证据**：127.0.0.1 双真机（serve:18081 `/api/upload`、MCP:18082 `/upload`）curl 全过——200（含落盘）、401（错 token / 无 token）、400（无 part）；413 走 TestClient（11MB 体）；无 token 本地免鉴 200；`/api/spec` 与 `/mcp` 原路由 intact；`test_serve_executor.py` 2 个 flow 失败经 `git stash` 对照证实为基线 pre-existing（与本 todo 无关）。服务器已 kill，临时目录已清。

## 2026-09-14 — todo 1: upload store 接口定稿（todos 2/5 消费）
- 模块 `src/cliyard/server/uploads.py`；常量 `UPLOAD_TTL_S=1800`、`UPLOAD_QUOTA_BYTES=500MiB`、`UPLOAD_QUOTA_FILES=1000`；`MAX_UPLOAD_BYTES` 从 `cliyard.server.executor` import（单一真相源，不重定义）。
- `save_upload(filename, data, upload_dir=None) -> {path, file_name, bytes, expires_at}`：服务端名 `cliyard-upload-<8hex>-<sanitized>`；O_EXCL 独占创建（0600）；超限抛 `CliyError`（todo 2 映射 413）；附带 `expires_at`（ISO8601 带时区，供 todo 2 响应直接用）。
- `sweep(upload_dir=None, now=None) -> {removed, freed_bytes, kept}`：单次非递归 `os.scandir`，仅 mtime/size；跳过 symlink/子目录/无前缀文件；unlink 前 realpath 复核仍在目录内；失败 `logger.warning` 永不抛错。
- `validate_dir(upload_dir, spec_dir=None) -> resolved`：拒绝 `/`、`/tmp`、`/var`、`/etc`、`$HOME`（resolve 后精确相等比较，子目录放行——默认目录即 `<tmp>/cliyard-uploads`）、spec 自身及祖先；通过后 `makedirs`（fail fast）。
- `is_managed(path, upload_dir=None) -> bool`；`assert_server_readable(path, upload_dir, allow_dirs=None) -> realpath`（越狱/缺失均 `CliyError` 带重传指引；`allow_dirs` 接受 list 或 `os.pathsep` 字符串）；`redact_upload_path(msg, upload_dir)` 替换 realpath 与 abspath 两种别名为 `<upload_dir>`。
- 默认目录 `DEFAULT_UPLOAD_DIR=<tmp>/cliyard-uploads`（todo 3 `--upload-dir` 缺省值须与此一致）。

## 2026-09-14 — todo 3: CLI 选项 --upload-dir/--upload-base-url/--file-allow-dirs 及透传
- 共享选项：`src/cliyard/cli/mcp_options.py` 6→9（新增 `_OPT_UPLOAD_DIR/_OPT_UPLOAD_BASE_URL/_OPT_FILE_ALLOW_DIRS`，`_apply_options` 内层追加，transport 保持最外层）。`cliyard mcp` + 生成 CLI `mcp` 自动一致；`serve.py`/`server_command.py` 复用同一三个对象 + `_OPT_TOKEN`，decorator 自下而上按 `DIR/BASE/FILE/TOKEN` 堆叠可使 help 顺序与 mcp 完全一致。
- 单一真相源：`DEFAULT_UPLOAD_DIR` 只在 `uploads.py` 定义（`mcp_options` 从其 import；`launcher` 不再自建）。
- 启动校验：`launcher.check_upload_dir(upload_dir, spec_dir)` 委托 todo 1 `validate_dir`（`/`、`/tmp`、`/var`、`/etc`、`$HOME`、spec 祖先全拒，`CliyError→ClickException` 转非零退出）；`run_mcp_server`（transport 分支之前，全 transport 生效）与 `run_server` 均透传 `spec_dir`。
- `display_host(host)` 抽取自 `server.py` 两处 inline `0.0.0.0/::→127.0.0.1`（`_auth_settings_for` + `run_mcp_server`），经 `mcp/__init__.py` 导出供 todo 4 模板回退复用。
- 缝线（已接通）：`run_mcp_server(upload_base_url=...)` → `build_mcp_http_app(upload_base=...)`（todo 2 签名已存在）→ `app.state.upload_base`；`create_app(token/upload_dir/upload_base_url/file_allow_dirs)` 写 `app.state`（`upload_token` 行复用 todo 2 的，`token` 保持 positional-or-keyword 不动其签名）。
- 未接缝（留给后续）：`save_upload/sweep` 缺省 `DEFAULT_UPLOAD_DIR`——自定义 `--upload-dir` 已在启动期校验+`makedirs`，但运行时尚未转交 store（`uploads` v1 无 configure API，`api/upload._process_upload` 调 `save_upload(filename, data)` 不带 dir）；`--reload` 模式 `create_app_from_env` 只读 `CLIYARD_SPEC_DIR`，token/upload state 会丢失（已知限制）。
- serve `--token` 语义与 MCP 对齐（非本地强制，`launcher._check_serve_auth` 复用 `is_local_host`）；`tests/test_serve_cli.py` 旧用例（`0.0.0.0` 无 token 期望 exit 0）已按新契约补 `--token`，新增远端无 token fail-fast + help 三选项断言。
- 回归基线：`test_mcp_tools/mcp_cli/serve_cli` 30 绿；`test_mcp_stdio_e2e` 5 败、`test_serve_executor` 2 败、`test_mcp_http_e2e` 3 败、`test_serve_app` webui 2 败均为 stash 对照一致的环境/预存失败，与本 todo 无关。

## 2026-09-14 — todo 3 follow-up：`upload_dir` 透传 MCP standalone-HTTP 全链路闭环（修正上条"未接缝"）
- 根因：`run_mcp_server` 调 `check_upload_dir(...)` 后丢弃返回值；`_mount_upload_route` 只写 `upload_token/upload_base`。handler（todo 2 已支持 `_process_upload(filename, data, upload_dir)` + `getattr(state, "upload_dir", None)`）遂回退 DEFAULT。复现实证（pre-fix）：`--upload-dir /tmp/custom-up-prefix` 上传后 custom 为空，文件落在 `.../T/cliyard-uploads/cliyard-upload-ff69917e-probe.txt`。
- 修复（仅 `server/mcp/server.py`）：`resolved = check_upload_dir(upload_dir, spec_dir)` → `build_mcp_http_app(..., upload_dir=resolved)`；`build_mcp_http_app(..., upload_dir=None)` → `_mount_upload_route(..., upload_dir)` 写 `app.state.upload_dir`；`mount_mcp_http` 加同名可选参并按 `upload_base` 同款 `if ... is not None` 写 state（serve 侧 `create_app` 已写，此处仅显式覆盖时生效）。签名全后向兼容（可选 kwarg，None→handler getattr→DEFAULT 链不变）。
- 实证（post-fix）：同 curl 落 `/private/tmp/custom-up-postfix/cliyard-upload-223233a2-probe.txt`，默认目录缺席；无 flag 时仍落 DEFAULT（行为与今日一致）。服务器已 kill，临时目录/探针文件/日志已清。
- 上条"未接缝"备注作废：store 运行时消费已闭环（state→`_process_upload(upload_dir)`→`save_upload`），剩余 todo 4/5 只消费 `upload_base`/`file_allow_dirs`。
