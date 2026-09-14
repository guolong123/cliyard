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
