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

## 2026-09-14 — todo 3 fixup：server 子命令 mock 容忍 `create_app` 新 kwargs
- verifier 抓到 2 回归（`test_server_launches_uvicorn_with_captured_spec_dir`、`test_server_app_build_error_exits_nonzero`）：`launcher.build_app_or_exit` 现以 `**upload_kwargs`（含 `token=`）调 `create_app`，而该文件两处 mock（`fake_create_app(spec_dir)`/`boom(spec_dir)`）签名过紧 → `TypeError`。纯测试修复：两处加 `**kwargs`，原断言（spec_dir 捕获 / build-error 退出）逐字保留。教训：`build_app_or_exit(spec_dir, **kwargs)` 转发语义下，任何 mock `create_app` 的测试都须 `**kwargs` 兜底。
- tip 已是 wave-3 提交（`499cc99`），故独立 fixup commit，未 amend `1d29d98`。

## 2026-09-14 — todo 4: file 参数 description curl 模板 + upload_base 透传链（commit 待填）

- **透传链（实际落地）**：`_file_upload_guide/_display_upload_base`（`schema_bridge.py` 新建）← `_param_to_property(*, upload_base=None, transport="http")` ← `params_to_json_schema(..., upload_base=None, transport="http")` ← `build_flow_schema`/`build_command_tree`（同文件内新增同名 kwargs，计划未点名但必经——`build_tool_specs` 只消费 `build_command_tree` 产物，直连 `params_to_json_schema` 的边根本不存在）← `build_tool_specs(spec_dir, *, upload_base=None, transport="http")` ← `MCPExecutor.__init__(..., upload_base=None, transport="http")`（另存 `self.upload_base/self.transport`）← `create_mcp_server(..., upload_base=None, transport="http")` ← `run_mcp_server`（stdio 分支透传 `upload_base_url` 经 `run_mcp_stdio(..., upload_base=None)` 以 `transport="stdio"` 重建；http 分支经 `build_mcp_http_app` 原样透传 `upload_base` + 硬编码 `transport="http"`）+ serve 侧 `get_spec` 读 `getattr(request.app.state, "upload_base", None)`（`create_app` 已写 state，getattr 防老 state 缺属性）。
- **签名漂移 vs 计划（LOUD）**：① 计划只给 `MCPExecutor/__init__` 与 `create_mcp_server` 写了 `upload_base`，但无 `transport` 则 stdio 变体永不可达——两处均补 `transport="http"`（计划首句"全 upload_base…/transport…"佐证本意如此）。② `build_command_tree/build_flow_schema/run_mcp_stdio` 的 kwargs 计划未点名，均为链路必经最小补齐。③ `mount_mcp_http` 故意未动（serve 内嵌 MCP 的模板线留给后续 lane，缺省占位+hint 照常工作）。④ `build_mcp_http_app` 收到 `upload_base=None` 时原样透传（不做 host/port 推导），缺省模板用固定占位 `http://127.0.0.1:8081` + 一句 `--upload-base-url` 提示——`0.0.0.0` 回退语义由占位本身的回环地址 + 显式 base 的 `display_host` 归一化共同覆盖。
- **循环导入坑**：`schema_bridge` 顶层 `from cliyard.server.mcp.server import display_host` 必死（`mcp.server→executor→tools→schema_bridge` 环）——改为 `_display_upload_base` 内懒导入（调用时各模块已就绪），附通配符 host 的 urlsplit 重组（保留 userinfo/port，`::` 括号处理）。
- **Token 卫生**：模板链全程不接收 token（`build_mcp_http_app` 的 token 只进鉴权/state，不进 schema 函数）；`$TOKEN` 纯占位 + "换成连接本 MCP/serve 所用的 token"一句，不教唆硬编码。证据：116 个 schema/description blob 零命中假 token。
- **测试变更（LOUD，按计划授权）**：`test_mcp_tools.py::test_schema_type_mapping_in_enum_and_file` 与 `test_serve_schema.py::test_params_to_json_schema_type_mapping` 的 file prop 精确相等断言改为三要素包含断言（curl 行/`$TOKEN`/本地路径句）——其余 29 项零改动通过。`test_serve_executor.py` 2 个 flow 失败经 `git stash` 对照为基线 pre-existing（与本 todo 无关）。`test_mcp_executor/plugin_tools` 51 项中除此 2 外全绿。
- **环境坑**：裸 `python3` 解析到 ketacli venv 的已安装 cliyard（非本仓）——本仓无 venv，所有验证须 `PYTHONPATH=src`。

## 2026-09-14 — todo 5: path jail 接入 builder/assembler（commit 待填）

- **jail 调用点（精确位置）**：`engine/builder.py::execute_pipeline` 非 multipart 读文件处（`open()` 前，先 jail 后读）；`engine/assembler.py::assemble_request` multipart `open(rb)` 前。两处均为 `if server_mode:` 内联分支 + 懒 import（engine 禁止顶层 import server：`server.uploads` 反向依赖 `server.executor→engine.builder`，顶层 import 必循环）。
- **签名扩展（全缺省，后向兼容）**：`execute_pipeline(..., server_mode=False, upload_dir=None, allow_dirs=None, server_tmp_files=None)`；`assemble_request` 同样四参；`execute_pipeline` 直透四参给 `assemble_request`。`server_mode=False` 缺省时新增代码零执行（CLI 逐行一致，throwaway 证明）。
- **PRE-bridge 语义的 bypass 机制（LOUD）**：jail 校验的是桥接前调用方原值——`_bridge_file_params` 把 base64 变成 temp 后，pipeline 已分不清 caller 路径与桥接产物。故 server 两调用点把 `_bridge_file_params` 返回的 `tmp_files` 原样透传为 `server_tmp_files`，jail 跳过其中精确字符串匹配项。WebUI base64 链（temp 落系统 temp 非上传目录）靠此 bypass 不受影响（QA t3 双向证明：带 bypass 放行、不带则 jail）。
- **exists 短路（`server/executor.py::_bridge_file_params`）**：`isinstance(value,str) and os.path.exists(value)` → `continue`（保持原值、不进 base64 尝试、不进 tmp_files，下游 pipeline 做 jail）。非 str 值不受影响（`_write_base64_temp_file` 原有 None 路径不变）；`no-such-file.txt` 类既非存在路径又非 base64 的仍保持原样（既有用例锁定）。
- **allow_dirs 缝线（LOUD，按 task 要求记录）**：
  - serve：`_run_command` 是后台线程、无 app 访问，故 `ExecutionManager` 单例新增 `server_upload_dir=None` / `server_allow_dirs=()`，由 `create_app` 在写 `app.state` 处同步赋值（`app.py` +2 行；launcher 已透传故自动跟随）。刻意未动 `api/execute.py` 路由 handler（MUST NOT）。多 app 同进程为 last-write-wins（单 server 进程假设，v1 可接受）。
  - MCP：`MCPExecutor.__init__` 新增 `upload_dir/file_allow_dirs`（构造器缝线）；`create_mcp_server` → `MCPExecutor`、`build_mcp_http_app`（新增 `file_allow_dirs` 参）→ `create_mcp_server`、`mount_mcp_http`（新增 `file_allow_dirs` 参）→ `create_mcp_server`；`run_mcp_server` 把既有 `file_allow_dirs` 补传给 `build_mcp_http_app`（todo 3 漏传，本 todo 补上）。
  - `server_mode` 取值：serve `_run_command` 硬编码 `True`；MCP `execute_command` 取 `self.transport != "stdio"`（todo 4 已把 transport 钉死：http 链全为 `"http"`，stdio 为 `"stdio"`——plan 要求 stdio 同机 `False`，HTTP `True`，无需新增 transport 缝线）。
- **multiple:true**：两处 jail 均逐元素（tuple/list 展开）；assembler 在 server_mode 下对 tuple/list 取 `[0]` 做 open（与 builder 既有 `[0]` 语义对齐；CLI 缺省路径未动，tuple 仍走原逻辑）。
- **files 句柄生命周期**：零改动（无新增 close、无新增 open 点；QA t8 手动 close 仅为脚本内防泄漏）。
- **并发 lane 事件**：开工时 todo 4 有未提交改动（mcp/executor、mcp/server 等）；实施中途 todo 4 提交 `7e50489`，本 todo 工作树 diff 经核对只含自身 6 文件 hunks（`git diff` 逐文件确认），无交织。
- **验证证据**：throwaway `/tmp/jail_qa.py` 9/9 绿（server 拒 `/etc/hosts` 含重传指引且无内容泄漏；upload-dir 放行；bridge-temp bypass 双向；tuple 逐元素；jail 内缺失→missing-file CliyError 非 FileNotFound；CLI 缺省读外部文件成功；allow_dirs 有/无对照；multipart 双向；bridge 短路三态）。聚焦 `test_serve_executor/mcp_executor/validate_types`：83 pass + 2 flow 失败；全量 `tests/`：529 pass + 15 fail；`git stash` 对照全量同样 15 fail（名单逐项一致：mcp_http_e2e×3、mcp_stdio_e2e×5、serve_app webui×2、serve_executor flow×2、serve_events flow×1、server_subcommand×2——均为基线，与本 todo 无关）。

## 2026-09-14 — todo 7: 端点测试 tests/test_upload_endpoint.py（18 项全绿）

- **Failing-first 探针无产品 bug**：先单跑 200 主用例（字段/落盘/命名），一次通过——当前 `upload.py` + `uploads.py` 契约与 plan 一致，无需产品修复。
- **413/配额零大文件**：超限测 monkeypatch `api.upload.MAX_UPLOAD_BYTES` + `uploads.MAX_UPLOAD_BYTES`=16，发 17B 即 413；配额测 monkeypatch `uploads.UPLOAD_QUOTA_BYTES`=5，预埋 10B seed 后新上传触发搭车 sweep 淘汰 seed。注意 sweep 读的是 `uploads` 模块全局量，patch 该模块即生效（`_process_upload` 持的是函数对象，patch 常量侧即可）。
- **sweep 先于 save**：配额断言只能是"旧文件被清 + 新文件 200"，不能断言总数（save 后总数会再次超配额，下次上传才清）。
- **MCP 侧 TestClient 可直测**：`TestClient(build_mcp_http_app(...))` 作 context manager（lifespan 跑 session manager）即可发 `POST /upload`，无需真起 uvicorn；serve 侧 `create_app(..., token=..., upload_dir=...)` 直连 TestClient。
- **隔离约定**：全部用例 `tmp_path` 独立 upload_dir（并发 lane 防碰撞）；显式传 `upload_dir`，永不污染 `DEFAULT_UPLOAD_DIR`。

## 2026-09-14 — todo 8: sweep/jail 安全测试（`test_upload_sweep.py` + `test_upload_jail.py`，15 绿）

- **Failing-first 设计（无产品改动）**：外来项（无前缀重要文件、嵌套子目录、symlink）一律 age 到 TTL 之外——guard 移除即误删，测试即红；guard 映射写进 sweep 文件头注释（prefix gate / symlink skip / subdir skip / unlink 前 realpath 复核）。
- **sweep 时间/配额注入**：`sweep(now=...)` 传参（永不 sleep）；配额测 monkeypatch `uploads` 模块全局量（`UPLOAD_QUOTA_FILES/BYTES`，sweep 调用时读全局，patch 模块即生效）；count 与 bytes 各一测，均断言 oldest-first 淘汰顺序（去哪两个/留哪三个）。
- **jail pipeline 缝线（无 mock）**：真 `execute_pipeline(server_mode=True, upload_dir=...)` + 最小 method_spec（`type: file` query param）+ 下游 fake HTTP client（非被测单元）+ `raw_response=True` 跳过 output 解析；`server_mode=False` 对照证明 CLI/stdio 零影响；allowlist 源文件在 pipeline + 全过期 sweep 后仍在（never-delete-inputs 铁证）。
- **隔离约定**：每测独立 `tempfile.mkdtemp`（不用 `tmp_path` 以外的共享目录，防并发 verifier 碰撞），fixture teardown `rmtree` 后断言目录已删；显式传 `upload_dir`，永不碰 `DEFAULT_UPLOAD_DIR`。验证须 `PYTHONPATH=src`（裸 python3 落 ketacli venv，但 PYTHONPATH 优先命中本仓 `src/cliyard/__init__.py`，已实证）。
- **产品零改动**：`git status` 本 lane 仅两个新测试文件；扫到 bug 只 loud 上报、不修。

## 2026-09-14 — 独立验证 todos 1-5（verifier 实测，非 executor 粘贴）

- ** verdict 总览**：todo1 confirmed；todo2 confirmed；todo3 **needs-fix**（新回归，见下）；todo4 confirmed；todo5 confirmed（功能全过；其 learnings 中"server_subcommand×2 均为基线"一句失实，实为 todo3 引入）。
- **todo1（9b97daa）confirmed**：常量 `UPLOAD_TTL_S=1800`/`QUOTA=500MiB/1000`/`DEFAULT=<tmp>/cliyard-uploads` 实测一致；save→存在→mtime-age→sweep 删除；外来文件/子目录/symlink sweep 后纹丝不动；新鲜保留；超限 `CliyError`；`validate_dir` 拒 `/ /tmp /var /etc $HOME`；`assert_server_readable` 越狱指引含重传句；`redact_upload_path`→`<upload_dir>`。
- **todo2（dbc6607）confirmed（比 executor 更强：413 亦走 live）**：127.0.0.1 真机 serve:18081 `/api/upload` + MCP:18082 `/upload`，curl 实测 200（含落盘+`path/file_name/bytes/expires_at`）/401（错 token 与无 token）/400（无 part）双 app 全过；11MB 体双 app 同报 413 `file too large ... exceeds limit of 10485760`；无 token 本地免鉴 200（18083）；`/api/spec` 200 intact，`/mcp` 401（鉴权存活）。
- **todo3（1d29d98）needs-fix**：help 三选项双 CLI 一致、缺省值=DEFAULT、fail-fast（`--upload-dir /`→`ClickException: upload 目录不允许使用系统/家目录：/`）、custom-dir 落盘（`/tmp/custom-up-verify3` 着陆、默认目录缺席）全部实测通过；**但引入 2 新回归**：`tests/test_server_subcommand.py::test_server_launches_uvicorn_with_captured_spec_dir` 与 `::test_server_app_build_error_exits_nonzero` —— `build_app_or_exit(spec_dir, **kwargs)` 现透传 `token=` 给 `create_app`，而两测试的 mock 仅接受 `(spec_dir)`，致 `TypeError: ... got an unexpected keyword argument 'token'`。bisect 铁证：78e0a51 基线 worktree 同文件 5 passed；1d29d98 worktree 同样 2 failed。**一句话修复**：两 mock 签名加 `**kwargs`（如 `def fake_create_app(spec_dir, **kwargs)` / `def boom(spec_dir, **kwargs)`），零产品改动。
- **todo4（7e50489）confirmed**：file 参数 description 三要素（`curl -X POST <base>/upload`/`$TOKEN`/本地路径句）+ 缺省占位 `http://127.0.0.1:8081` + `--upload-base-url` 提示句实测俱在；`transport="stdio"` 变体含 `file_path` 直传段、http 变体无；显式 base `http://10.0.0.5:9000/` 透传着陆；`0.0.0.0`→`127.0.0.1` 归一；token 结构性隔离（`build_mcp_http_app` 的 token 只进 auth/state/schema 链无 token 形参）+ live `/api/spec`（真 token `verify-secret-token` 在 app.state 存活）零命中 + 全 schema fake-token 扫描干净。
- **todo5（e7ffc8a）confirmed**：multipart/non-multipart 双 jail 点 server_mode=True 拒 `/etc/hosts`（`CliyError` 含重传指引，首行内容零泄露）vs server_mode=False 照读；upload-dir/allow_dirs（含 `os.pathsep` 字符串形）放行对照；tuple 逐元素拒；bridge-temp bypass 双向（在列则放行、不在则拒）；`_bridge_file_params` exists 短路（已存原值保持、零 tmp；`no-such-file.txt` 原样）；`call_tool` 真路径 is_error=True（含指引、无泄露）；`execute_command` 的 `server_mode=self.transport != "stdio"` 源码确认。
- **全量回归对照（worktree 法，因已提交故不用 stash 动树）**：现树 `tests/` 562 passed + 15 failed，名单 = mcp_http_e2e×3 + mcp_stdio_e2e×5 + serve_app webui×2 + serve_executor flow×2 + serve_events×1 + server_subcommand×2；78e0a51 基线 worktree 同批 13/13 复现（server_subcommand 除外，基线 5 passed）——除 todo3 的 2 个外无新失败。
- **打扫 receipts**：4 台验证服务器（18081/18082/18083/18084）已 kill、`ps` 无残留、两端口连通性 down；2 个审计 worktree 已 remove；`/tmp` 与 sys-tmp 下全部 `verify-*`/`custom-up-*`/probe/big11/spec.json 已删；`git stash list` 空；产品树 `src/ tests/` 零 dirty（仅 `.omo/boulder.json` 计划切换 + harness 未跟踪文件，属正常 bookkeeping）；验证全程零产品/测试改动、零提交。

## 2026-09-14 — todo 6: MCP call_tool 双根脱敏（commit 待填）

- **Failure-first 实证（pre-fix，`/tmp/todo6_prefix.py`）**：三类真错 jail/expired/missing 经 `call_tool` 均已 `is_error=True` 且指引 intact（todo 5 落地时已满足）；但 except 通道仅 `_sanitize_error(str(exc), spec_dir)`——含上传目录真值的合成缺参错（`tried <up_real>/stale.txt`）原样透出，`upload real in text: True`，GAP 坐实。
- **修复（仅 `server/mcp/executor.py`，+8/-2 行）**：顶层 `from cliyard.server.uploads import redact_upload_path`（server→server 无环：uploads 只依赖 `engine.errors` + `server.executor`）+ except 内 `_sanitize_error` → `redact_upload_path(..., self.upload_dir)` 叠加 + 一行双根注释。用户文案逐字节保留（post-fix 同错文本仅真值变 `<upload_dir>`/`<spec_dir>`）。
- **Post-fix 证据**：同脚本 raw-path 用例 `upload real in text: False`（`<upload_dir>/stale.txt` + `<spec_dir>/repos.yaml`）；`upload_dir=None` 时 `redact_upload_path` 回退 DEFAULT 根，行为不变。`tests/test_mcp_executor.py` 13 passed；`git status` 产品树仅本文件 dirty。

## 2026-09-14 — todo 9: E2E tests/test_upload_e2e.py（4 项全绿）

- **Failing-first 顺序**：先单跑 negative（d）——删服务端文件后 `call_tool` 即 `is_error=True`（"文件不存在或已过期清理…请重新 POST /upload 上传"），证明 suite 能发 red 信号；再跑全文件 4 绿。产品零改动（MUST NOT）。
- **HTTP E2E（a）**：`TestClient(build_mcp_http_app(upload_dir=...))` 真 `POST /upload`（handler 永不 mock）→ 同 `upload_dir` 的 `MCPExecutor(transport="http")` 真 `call_tool("repos.upload", {"file": path})`（经 `SimpleNamespace(name, arguments)` + `asyncio.run` 直调 async 回调，线程池内核照走）；两次文本逐字相等（identical success，不止 non-error）+ 每次调用后断言落盘仍在（no-consume-delete 铁证）+ 上游两条 record 体均含文件字节。
- **stdio 对照（b）**：同一 jail 外路径——`transport="stdio"`（server_mode=False）成功且上游收到字节；`transport="http"`（server_mode=True）同路径 `is_error=True`（"不在允许范围内…请先 POST /upload…"），且 jail 侧上游 record 数不变（未外发）。
- **文案扫描（c）**：`build_mcp_http_app(token=FAKE)` 配假 token（只进鉴权/state）+ `executor.list_tools` 全表 `model_dump` JSON 串零命中假值；另断言 `POST`/`/upload` 交接指引仍在（有指引、无秘密）。
- **代理坑（LOUD，环境非产品）**：本机 `HTTP_PROXY=http://127.0.0.1:7890`（dead proxy）使 httpx 信任环境代理、回环 MockUpstream 全变 502——此前 `test_mcp_stdio_e2e` 5 败/`test_mcp_http_e2e` 3 败"基线失败"实为此因（clean-env 下 16/16 全绿，非产品回归）。本文件自带 autouse `_no_proxy` fixture（delenv 四变量 + `NO_PROXY=127.0.0.1,localhost`，文件内隔离）；验证既有 trio 时 shell 侧 `env -u HTTP_PROXY -u HTTPS_PROXY ...`（不动其他文件）。
- **隔离约定**：每测独立 `tmp_path` upload_dir + MockUpstream  ephemeral 端口（零固定端口、零共享静态目录）；`TestClient` 直连免 lifespan（`/upload` 纯路由）；`upload_dir` 预 `mkdir`；stdio 侧用直调 executor（`transport="stdio"` 语义与子进程一致，免 spawn 开销）。
- **验证证据**：`PYTHONPATH=src pytest tests/test_upload_e2e.py tests/test_mcp_http_e2e.py tests/test_mcp_stdio_e2e.py` 20 passed；`git status` 仅新文件 + notepad。

## 2026-09-14 — todo 10: 全回归 + README 上传章节（commit 待填）

- **回归（clean-env 全量）**：`env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy PYTHONPATH=src python3 -m pytest tests/ -v` → **573 passed + 8 failed**（29.33s）。开工 `env | grep -i proxy` 实测本机 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`（dead proxy），全程 unset 后跑。
- **8 失败 worktree bisect 铁证（NOT 凭感觉判 pre-existing）**：`git worktree add /tmp/cliyard-base 78e0a51`（plan 前基线）同 4 文件同 clean-env → **8 failed + 52 passed，失败名单与现树逐项一致**：`test_mcp_http_e2e`×3（standalone/list-and-call、mounted-into-serve、auth；`session.initialize` 报 MCPError peer error，与 upload 链无关）、`test_serve_app` webui×2、`test_serve_events`×1、`test_serve_executor` flow×2（SSE 期望 `step_start` 实得 `flow_start`，事件命名基线漂移）。worktree 已 remove。结论：8 个均为基线 pre-existing，本 plan 零新失败、零产品 bug → 无 needs-fix，按 lane 规则不碰产品代码。
- **README（仅此一文件）**：API 一览表 +1 行 `POST /api/upload（serve）/ /upload（MCP）`；新增 `### 文件参数（MCP）` 节——curl 三步（上传→execute→重放说明，token 变体）、stdio 例外段、三默认值（TTL 30min / 500MB·1000 个 oldest-first / MAX 10MB→413）、三选项表（`--upload-dir/--upload-base-url/--file-allow-dirs`，`--help` 实测核对缺省文案）+ serve `--token`、边界声明（shell-capable agents only；纯聊天 host 不支持；`source_url` 后续项）。零新文件。
- **README curl 逐字实执行（非 dry-run）**：`run_server(spec=write_spec(MockUpstream), port=18081→实为18090, upload_dir=/tmp/readme-verify-uploads)` 真机；step1 `curl -F file=@/tmp/report.pdf` → 200 `{"path":".../cliyard-upload-8bee5033-report.pdf","file_name":...,"bytes":20,"expires_at":"2026-09-14T18:06:29.996+08:00"}`；step2 同 path `POST /api/execute repos.upload` → `{"execution_id":"857d..."}` → 轮询 `status:done`（validate/auth/request/upstream-200/format/done 全链，上游 `{"ok": true}`）。服务器已 kill，`/tmp/readme-*` + 探针文件 + 日志已清。

## 2026-09-14 — F1 plan-compliance audit verdict: REJECT（1 blocking，其余 9 通过）

- **方法**：read-only；`git log --oneline -15` 11 个 SHAs 就位（`29f589a` 已 amend 进 `1d29d98`，属正常 amend，非缺失）；`git show --stat` 逐个 spot-check（各提交仅含本 todo  scope + learnings append，无串 scope）；`git status` 产品树干净（仅 `.omo/*` bookkeeping）；全仓 grep `sweep` 确认调用点。
- **T1 部分通过（store 本体 ✓，触发点 ✗-blocking）**：常量/命名/sweep-guard/deny-list/`is_managed`/`assert_server_readable`/`redact_upload_path`/`DEFAULT_UPLOAD_DIR` 全在 `src/cliyard/server/uploads.py:42-51,97,159,253,309,322,367`；但 acceptance 钉死"sweep 触发点三处"，全仓仅 1 处调用（`src/cliyard/server/api/upload.py:84`，POST handler 内），`MCPExecutor.execute_command`（`server/mcp/executor.py:118`）与 serve `_run_command`/`submit_command`（`server/executor.py:355`）入口均无 `sweep()`。**BLOCKING：补两处搭车 `sweep(upload_dir)`（单次 scandir，永不抛错）即关单。**
- **T2 ✓**：`POST /api/upload` + `POST /upload`（`api/upload.py:99,113` + `mcp/server.py:185,239` 挂载）、`verify_upload_token`（`api/upload.py:40`）、400/401/413（commit `dbc6607`，测试 15 项 `tests/test_upload_endpoint.py` 全覆盖）。
- **T3 ✓**：三选项 + serve `--token`（`cli/mcp_options.py`、`cli/serve.py`、`launcher.check_upload_dir:44`、`mcp/server.py:62 display_host`）；mock 回归已由 `77bdb6b` 修复（测试-only）。
- **T4 ✓**：三要素模板 `_file_upload_guide`（`schema_bridge.py:84-106`：curl 行/`$TOKEN`/本地路径句 + stdio/http 变体 + 占位提示），透传链含计划外最小补齐（LOUD 已记录）。
- **T5 ✓**：jail 两调用点（`engine/builder.py:365`、`engine/assembler.py:315`）、`server_mode` 取值（serve 硬编码 True `server/executor.py:379`；MCP `transport != "stdio"` `mcp/executor.py:141`）、bridge exists 短路 + tmp bypass；测试 9 项。
- **T6 ✓**：双根脱敏叠加（`mcp/executor.py:335-339`），文案逐字节保留。
- **T7 ✓**：`tests/test_upload_endpoint.py` 15 项（200/400/401/413/字段/落盘/命名/`../` 无害化）。
- **T8 ✓**：`test_upload_sweep.py` 6 项 + `test_upload_jail.py` 9 项（外来零损失/过期清/新鲜留/oldest-first 双维度/jail 三态/pipeline 后输入仍在）。
- **T9 ✓**：`tests/test_upload_e2e.py` 4 项（重放两次逐字相等 + 落盘仍在、`stdio` vs `http` jail 对照、schema 零 token 真值、删文件后 isError 指引）。
- **T10 ✓**：README 上传行 + `### 文件参数（MCP）`（curl 三步/token 变体/三默认值/三选项表/边界声明，live curl 已实执行）；全量 573+8 与基线 worktree 逐项一致（记录在案，本审按 MUST-NOT 不重跑）。
- **Success criteria**：两 shell 走通 ✓ / diff 最小（jail 两点 + exists 短路）✓ / 重放两次 ✓ / 越狱 isError 无泄露 ✓ / 脏扫零误删 ✓ / 文案无 token ✓ / README 可执行 ✓；唯"sweep 三触发点"一子项缺 2/3 → REJECT。
- **Verdict: REJECT** —— 单一 blocking（T1 sweep 触发点），修复面 2 行，复审只需 grep `sweep(` 三处 +  focused e2e。

## 2026-09-14 — F4 Scope-fidelity audit verdict: APPROVE

- 基线：`git diff 78e0a51..HEAD --stat` = 28 files, 2301+/48-（含 `.omo/*` bookkeeping 6 项 + `README.md` + `src/` 19 项 + `tests/` 8 项）。工作树 dirty 仅 `.omo/boulder.json` + notepad（harness bookkeeping，非产品）。
- Finding 1（diff-scope 全映射，无 creep）：11 个提交逐个 `git show --stat` 核对——`9b97daa`→todo1（`server/uploads.py` 新建）；`dbc6607`→todo2（`server/api/upload.py` 新建 + `app.py`/`mcp/server.py` 挂载）；`1d29d98`→todo3（`cli/mcp.py`、`cli/mcp_options.py`、`cli/serve.py`、`runtime/mcp_command.py`、`runtime/server_command.py`、`server/app.py`、`server/launcher.py`、`server/mcp/__init__.py`、`tests/test_serve_cli.py`，其中 `server_command.py`/`launcher.py`/`mcp.py` 为透传缝线、`mcp/__init__.py` 为 `display_host` 抽取导出，learnings 均有记录）；`7e50489`→todo4（`server/api/spec.py`、`server/mcp/executor.py`、`server/mcp/server.py`、`server/mcp/tools.py`、`server/schema_bridge.py`、`tests/test_mcp_tools.py`、`test_serve_schema.py`）；`e7ffc8a`→todo5（`engine/assembler.py`、`engine/builder.py`、`server/app.py`、`server/executor.py`、`server/mcp/executor.py`、`server/mcp/server.py`，builder/assembler 接线为计划点名）；`27dffd2`→todo7、`499cc99`→todo8、`9fd27bf`→todo9（新测试三文件）；`77bdb6b`→todo3 fixup（`tests/test_server_subcommand.py` 两处 `**kwargs` mock，为 brief 点名的 plan-mandated seam）；`cf9dfd6`→todo6（`server/mcp/executor.py` +8/-2 双根脱敏）；`9990ed5`→todo10（`README.md` +50）。零未映射产品文件。
- Finding 2（OUT-list 全守）：(a) 无 `upload.create` 类 MCP 工具——diff grep `upload.create|register.*upload|@mcp.tool.*upload` 零命中，`tools.py` diff 仅 kwargs 透传；(b) 无 `file_content`/base64 新通道——grep 零命中，仅 exists 短路 + `server_tmp_files` bypass；(c) 无 `source_url` 实现——唯一命中为 `README.md:169` 延后声明；(d) CLI 默认行为不变——`builder.py:294`/`assembler.py:175` `server_mode: bool = False`，jail 全在 `if server_mode:` 内（`builder.py:365`、`assembler.py:315`），serve `--token` 仅非本地强制（todo3 计划点名）；(e) 无 DB/线程/新鉴权体系——新增行 grep `threading|sqlite|Lock()|BackgroundTask|create_task` 零命中（唯一含 "threading" 的新增行为 docstring 词义=选项透传），鉴权仅 `verify_upload_token` Depends（todo2 计划点名）+ `_StaticTokenVerifier` 复用；(f) 无 blanket purge——无 `rmtree/purge`，sweep 四 guard 俱在（prefix/symlink-skip/subdir-skip/unlink 前 realpath 复核，`uploads.py:209-243`）；(g) unlink 仅两处且均合法——`uploads.py:220`（guarded sweep 内）+ `server/executor.py:580`（`git blame` 证实为 8 月基线 `_cleanup_tmp_files`，仅清桥接 temp）。
- Finding 3（结构契约成立）：plan `## Todos` 含 10 行 column-zero `- [x] 1.`…`- [x] 10.`（`:43-106`），`## Final verification wave` 含 4 行 column-zero `- [ ] F1.`…`F4.`（`:115-118`）。
- Finding 4（deferred 可见）：`README.md:168-169`（纯聊天 host 不支持 + `source_url` 后续项）+ `.omo/drafts/mcp-file-client-origin.md:51,65`（延后 + SSRF 另立项）。
- Finding 5（观察项，非 REJECT：属 F1 范畴的计划短fall 非 creep）：todo1 钉死 sweep 搭车三处（POST `/upload` + MCP `execute_command` + serve `submit_command`），但 `grep -rn "sweep(" src/cliyard/server/mcp/executor.py src/cliyard/server/executor.py` 零命中，实际调用仅 `server/api/upload.py:84` 一处。未新增任何文件/行为，故不构成 creep，不阻断 F4。
- Verdict：**APPROVE**——无 creep 文件，OUT-list 七项全守，结构契约与 deferred 标记齐备。

## 2026-09-14 — F2 code-quality review (read-only): verdict REJECT (3 major + 6 minor)

Scope: `git diff 78e0a51..HEAD --stat` = 28 files (+2301/-48); base 78e0a51 confirmed via `git log --oneline -15`
(plan todo-10 bisect record matches; no re-discovery needed). `py_compile` on all 18 product files: OK.
No edits/commits made; full test suite NOT run (per MUST NOT DO).

Files examined (all 18 + plan + 4 notepads): `server/uploads.py` (404 lines, new),
`server/api/upload.py` (153, new), `server/app.py`, `server/launcher.py`,
`server/mcp/server.py`, `server/mcp/executor.py`, `server/executor.py`,
`engine/builder.py` + `engine/assembler.py` (diff hunks), `server/schema_bridge.py`,
`server/mcp/tools.py`, `server/api/spec.py`, `cli/mcp_options.py`, `cli/serve.py`,
`cli/mcp.py`, `runtime/server_command.py`, `runtime/mcp_command.py`,
`server/mcp/__init__.py`, plus `engine/orchestrator.py:257-313` (flow path, reached via jail-tracing).

PASS (no finding): hmac compare both auth paths (`api/upload.py:55`, `mcp/server.py:57`);
mount order safe (`app.py:142` before static `app.py:163`; `mcp/server.py:232,239` insert(0));
O_EXCL 0600 save + idempotent sweep (multi-worker/threadpool safe); fd lifecycle unchanged;
no shim leftover (only historical docstring note `api/upload.py:16`); no `upload_id`/`upload.create`/
PUT-reserve refs; `$TOKEN` placeholder only, token never enters schema chain; Chinese docstrings
consistent; `tools.py:171-182` dead code confirmed PRE-EXISTING in 78e0a51 (not this change set).

Findings:

1. [major] `server/executor.py` + `server/mcp/executor.py` — 2 of 3 plan-pinned sweep sites missing.
   Plan line 45 pins sweep at `POST /upload`内 + MCP `execute_command`入口 + serve `submit_command`入口;
   grep proves `sweep(` is called ONLY at `server/api/upload.py:84`. Expired files are therefore
   never collected on business-call paths, and `assert_server_readable` (`uploads.py:322`) checks
   path+existence but NOT TTL — an expired file stays readable indefinitely until someone happens
   to upload. Fix: add搭车 `sweep()` at `ExecutionManager.submit_command` (or `_run_command` entry)
   and `MCPExecutor.execute_command` entry (both documented never-raise, cost = one non-recursive scandir).
2. [major] `engine/orchestrator.py:304-313` — flow `use:` steps bypass the path jail entirely.
   `execute_use_step` calls `execute_pipeline` WITHOUT `server_mode/upload_dir/allow_dirs`, so serve
   `POST /api/execute {kind:flow}` and MCP `flow.*` tools with a file-typed step param can open
   arbitrary server paths (e.g. `/etc/hostname`) despite the todo-5 jail. Fix: thread jail roots
   through `FlowContext`/`run_flow` → `execute_use_step` (same 4 kwargs, `server_mode=True` on
   serve/MCP-HTTP, bypass list from bridge tmp files), or explicitly record flow+jail as deferred
   with a spec-side guard.
3. [major] `server/api/upload.py:106,149` — unbounded `await file.read()` before the 10MB check.
   Both endpoints slurp the whole body into RAM and only then compare `len(data) > MAX_UPLOAD_BYTES`
   (`api/upload.py:79`); no Content-Length pre-check, no streaming cap → single large POST can OOM
   the server worker. Fix: reject on `content-length` header > MAX upfront and/or stream-read with
   running cap (413 as soon as exceeded).
4. [minor] `server/uploads.py:125` via `server/api/upload.py:88,90` — absolute `upload_dir` leaks in
   API errors. `save_upload` embeds `resolved_dir` in CliyError text; `_process_upload` returns
   `str(exc)` verbatim (413) and `f"failed to store upload: {exc}"` (500) with no `redact_upload_path`.
   Fix: wrap both returns with `redact_upload_path(..., upload_dir)`.
5. [minor] `server/app.py:181-190` + `server/launcher.py:110-125` — `--reload` drops token/upload state.
   `create_app_from_env` calls `create_app(spec_dir)` bare, so a reloaded worker loses `upload_token`
   (→ `/api/upload` silently becomes unauthenticated) and upload/jail roots (→ DEFAULT). Known
   limitation per todo-3 learnings, but the auth-downgrade direction deserves at least a loud
   warning or env-forwarding. Fix: forward token/upload settings via env or refuse `--reload`+remote.
6. [minor] `engine/builder.py:~372-390` + `engine/assembler.py:~317-335` — check-then-open TOCTOU.
   Jail validates `_candidate` but discards the returned realpath and `open()`s the original string,
   leaving a symlink-swap window. Fix: `open()` the realpath returned by `_assert_readable`
   (single-element case; keep element-wise validation for multiple:true).
7. [minor] `server/uploads.py:135-139` — partial file left on failed write. If `f.write` raises
   mid-stream, the truncated `cliyard-upload-*` file stays and counts toward quota. Fix: unlink
   `dest` in the `except OSError` arm before raising.
8. [minor] `server/app.py:120-121` — multi-app same-process last-write-wins on
   `execution_manager.server_upload_dir/server_allow_dirs` (documented v1 trade-off; fine for one
   server process, wrong if two `create_app`s share a process). Fix (later): per-spec registry or
   loud comment at the two assignment lines.
9. [minor] `server/api/upload.py:134-135` + `server/schema_bridge.py:78-79` — swallowed errors without
   logging. Form-parse failure → bare 400 with no `logger.warning`; `_display_upload_base` URL-parse
   failure → silent `pass` (cosmetic fallback, acceptable but unlogged). Fix: one-line
   `logger.warning`/`logger.debug` in each.

Verdict: REJECT — fix majors 1-3 (sweep sites, flow jail, upload size cap) then re-review; minors 4-9
may ride along or be filed as follow-ups. No product/test files touched by this review.

## 2026-09-14 — F3 hands-on QA verdict: PASS (5/5 live probes, pasted evidence)

- **Env**：开工 `env | grep -i proxy` → `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`（dead proxy）；全部探活用 `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy`，clean 证据 `env|grep -i proxy` exit=1。`PYTHONPATH=src cliyard` 实证 import 自本仓 `src/cliyard/__init__.py`（version 0.14.2）。端口：upstream 18150 / serve 8080 / MCP 18152 / serve+token 18151 / MCP+token 18153（开工全 DOWN，收工全 DOWN）。spec=`tests.mcp_helpers.write_spec`（repos.upload multipart file 真上游 `http://127.0.0.1:18150`）。QA 目录 `/tmp/f3qa-f3run`（已删）；`/tmp/f3qa` 系他人遗留未动。
- **(a) serve ✓**：README 逐字 `curl -X POST http://127.0.0.1:8080/api/upload -F "file=@report.pdf"` → 200 `{"path":".../up-serve/cliyard-upload-5c9da5a9-report.pdf","file_name":...,"bytes":22,"expires_at":"2026-09-14T18:10:52.841+08:00"}`；README step2 同 path `POST /api/execute repos.upload` → execution `41e6721d…` → 轮询 `status:done`（validate/auth/request/upstream-200/format/done）；上游日志 `POST /repos has_probe_bytes:true`；同 path 重放第二次 → 第二个 `POST /repos has_probe_bytes:true`（不消费删除铁证）。
- **(b) jailbreak ✓**：`/etc/hostname` 在 macOS 不存在（`ls` 实证）——仍按任务逐字传入 + 加测存在的 `/etc/hosts` 作泄漏断言。`tools/call repos.upload {"file":"/etc/hosts"}` → `IS_ERROR: True` + `文件路径不在允许范围内：/etc/hosts。请先 POST /upload 上传文件，再使用返回的 path 调用`；`grep -c broadcasthost` = 0（零泄漏），`POST /upload` 指引 ×1。
- **(c) auth ✓**：错 token → `{"detail":"invalid or missing bearer token"}` 401 双路由（S2 `/api/upload` + M2 `/upload`）；正确 token → 双路由 200（含落盘 path）。
- **(d) CLI ✓**：`cliyard mcp --help` + `cliyard serve --help` 均含 `--upload-dir/--upload-base-url/--file-allow-dirs` 三选项；`--upload-dir /` 双 CLI 均 `ClickException: upload 目录不允许使用系统/家目录：/` exit=1（serve 附 traceback 同样非零）。
- **(e) sweep ✓**：`up-serve` 预埋外来 `important-keep.txt` + 真上传后 `os.utime` age 2h（`ls` 显示 15:43 vs TTL 30min）；新上传触发搭车 sweep → 200；断言 FOREIGN-SURVIVES yes / EXPIRED-GONE yes / FRESH-TRIGGER-PRESENT yes / FRESH-OLD-PRESENT yes。
- **环境坑（LOUD，后人必读）**：① `python3 -m cliyard` 不可用（无 `__main__`，入口是 console script `cliyard=cliyard.cli.__main__:main`），须 `PYTHONPATH=src cliyard …`。② macOS 无 `timeout` 命令，fail-fast 用退出码直测。③ 本机除 env 代理外疑似还有系统级代理：httpx `trust_env=True` 即使 env 已 unset 仍回环 502（curl 正常），python 侧探针须 `trust_env=False` 或 `NO_PROXY=127.0.0.1,localhost`（与 todo9 的 NO_PROXY fixture 同根）。④ MCP SDK 版 `streamablehttp_client` 不存在（本机为 `streamable_http.streamable_http_client`，yield 2 元组，且 SDK `initialize()` 对本仓 server 报 peer error——与 todo10 基线 8 失败中的 `test_mcp_http_e2e×3` 同因），故 jailbreak 改走 raw JSON-RPC（initialize→202 initialized→tools/call SSE 解析），仍是真 HTTP 真 server。⑤ zsh 下 `VAR="a b c"; $VAR cmd` 不分词——后台起服须写全前缀。
- **打扫 receipts**：5 进程（93284/93285/93286/93287/93288）kill 后 `ps` 全 gone；5 端口 curl 全 down；`/tmp/f3qa-f3run` 已删（`ls` 无此目录）；`git status` 产品树零 dirty（仅 `.omo/*` bookkeeping）。零产品/测试/计划文件改动，零提交。
- **与 F1/F2 的关系**：本 F3 按任务书"经由一次 upload 调用触发"验证 sweep，通过；F1/F2 指出 `execute_command`/`submit_command` 入口缺搭车 sweep 与 flow jail 缺口——属其余路径，不推翻本 F3 证据。

## 2026-09-14 — F1/F2 fix lane：7+1 项全关（commit 待填）

- **Failure-first 实证（`/tmp/f1f2_prefix.py`，pre-fix 全红）**：P1 `grep sweep(` 仅 `api/upload.py:84` 一处；P2 stamp `server_mode=True` 的 `run_flow` 读 `/etc/hosts` 后直达 HTTP（`Connection refused`，jail 零触发）；P3 age 2h 的托管文件 `assert_server_readable` 照常返回 path；P4 `_process_upload` 413 原样透出真 `upload_dir`（`leaks real dir: True`）；P5 `upload.py:106/149` 先 `await read()` 后比长；P6 `create_app_from_env` 只读 `CLIYARD_SPEC_DIR`。
- **(A) sweep 三触发点**：`mcp/executor.py:124`（`execute_command` 入口 `_sweep_uploads(self.upload_dir)`，顶层 import——`uploads` 只依赖 `engine.errors+server.executor`，沿用 todo6 零环结论）+ `server/executor.py:365`（`_run_command` 入口，函数内懒 import 避开 `uploads→executor` 环）+ 既有 `upload.py:108`。`run_mcp_server` 无需动：HTTP 分支已透传 `resolved_upload_dir→build_mcp_http_app→create_mcp_server→MCPExecutor`（读码确认）；stdio 分支未传（`run_mcp_stdio` 无此参，`mcp/server.py` 不在本 lane 文件清单内故不动——stdio `server_mode=False` 不消费上传目录，`execute_command` 内 `sweep(None)` 回退 DEFAULT 无害）。
- **(B) flow jail**：`ServiceContext` +4 缺省字段（`builder.py`，CLI 构造全兼容）；`run_flow` +4 可选 kwarg（`None`=继承 stamped `service_ctx`，CLI 不 stamp→恒 False，逐字节一致）；`FlowContext` +4 字段（含 `for_each` 的 `iter_ctx` 透传）；`execute_use_step` 直透给 `execute_pipeline`。两 server 调用方 stamp `service_ctx` 而非向 `run_flow` 传新 kwarg——**原因 LOUD**：`test_mcp_executor.py:156` 的 `fake_run_flow(flow_spec, params, service_ctx, service, step_cb=None)` 签名冻结（MUST NOT 碰测试），传新 kwarg 即 `TypeError`；stamp 走既有对象通道，fake 照过、真链路继承。另加 `isinstance(service_ctx, ServiceContext)` 门卫（该测试把 `build_service_context` mock 成裸 `object()`，直接 stamp 会 `AttributeError`；真链路恒为真类）。证据：同 P2 脚本 post-fix 切 jail（`允许范围`），unstamped CLI 对照仍直达 HTTP（`Connection refused` 非 jail）。
- **(C) bounded read**：`upload.py` 新增 `_content_length_exceeds`（缺失/非法 header→`None`，回退 post-read 检查，永不抛）+ `_oversized_body`；serve handler 在 `await file.read()` 前 413，MCP handler 在 `await request.form()` **之前** 413（form 解析本身读全体，先查后解是关键顺序）。证据：MAX 压到 64B，200B 文件→413 且 `UploadFile.read` 调用 0 次。
- **(D) redact**：`_process_upload` 的 413/500 两 `return` 包 `redact_upload_path(..., upload_dir)`。证据：`save_upload` 抛含真路径的 `RuntimeError`→500 体为 `<upload_dir>/xx`，`grep` 响应体零真值。
- **(E) TTL on read**：`assert_server_readable` 在 `isfile` 后加 `is_managed(real)→mtime age>TTL→同一 missing/expired CliyError`（allowlist 文件非托管，不受 TTL；`getmtime` 失败按缺失处理）。证据：age 文件拒（文案自动带 `<upload_dir>` 脱敏）、新鲜文件照读。
- **(F) partial-write**：`save_upload` 的 `except OSError` 内先 `os.unlink(dest)`（失败记 warning）再抛 `CliyError`。
- **(G) reload（选 env-forward，未选 fail-fast，LOUD）**：`run_server` reload 分支写 `CLIYARD_TOKEN/CLIYARD_UPLOAD_DIR/CLIYARD_UPLOAD_BASE_URL/CLIYARD_FILE_ALLOW_DIRS`（缺席值 `pop` 清 stale），`create_app_from_env` 全读回传给 `create_app`。证据：env 灌入后 `create_app_from_env().state` 四值俱在。**singleton-roots limitation（accepted, rare）**：`app.py:120-121` 加注释——多 app 同进程 `execution_manager.server_upload_dir/allow_dirs` last-write-wins，后台线程无 app 访问只能读单例，单 server 进程假设下成立。
- **(H) log not swallow**：`sweep` 的 4 处 silent `continue`（is_symlink 判定失败/symlink/is_file 判定失败/stat 失败）+ 外来文件跳过各加 `logger.debug`；never-raise 契约不变。
- **TOCTOU**：`builder.py`/`assembler.py` 两处 jail 后 `open()` 改用 `_assert_readable` 返回的 realpath（tuple 取首元素语义保留，bypass 项保持原值）。
- **回归**：focused 6 文件 `76 passed + 2 failed`，2 失败经 `git worktree add /tmp/cliyard-base HEAD` 对照为基线 pre-existing（`flow_start` vs `step_start` 命名漂移）；clean-env 全量 `573 passed + 8 failed`，名单与 todo10 基线逐项一致（`mcp_http_e2e×3`/`serve_app webui×2`/`serve_events×1`/`serve_executor flow×2`，其中 `serve_events×1` 亦在 base worktree 复现——本 lane 动过 `run_flow` 签名，特意单测确认）。`git stash` 全程未用。
