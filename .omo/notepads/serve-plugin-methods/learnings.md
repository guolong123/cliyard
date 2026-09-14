# serve-plugin-methods learnings — todo 1 (2026-09-14)

## Kernel signature (钉死，todo 2 直接消费)
```python
def execute_plugin_method(
    plugin_name: str,
    kwargs: dict[str, Any],
    method_spec: dict[str, Any],
    service_ctx: ServiceContext,
    http_client: Any = None,
    base_url_override: str | None = None,
    spec_dir: str | None = None,
    server_mode: bool = False,
    upload_dir: str | None = None,
    allow_dirs: list[str] | tuple[str, ...] | str | None = None,
    server_tmp_files: list[str] | tuple[str, ...] | set[str] | None = None,
    event_cb: Callable[[str, dict], None] | None = None,
) -> dict
```
位置：`src/cliyard/engine/builder.py`（`_make_plugin_callback` 正上方）。
`PluginNotFoundError(CliyError)`：`src/cliyard/engine/errors.py`（已验证该文件即 `CliyError` 本家，无新模块）。

## 内核顺序（已实现）
registry-check 第一（内含 `discover_plugins(spec_dir or None)`，CLI 传 None=原裸调用语义）
→ `bind_and_validate` → file jail（server_mode 才执行；仅 `assert_server_readable` 断言，
不读文件内容——插件自己读文件，与原 CLI 一致）→ merge（原 903-909 逐行等价）→ client 解析
（传入 `http_client` 优先，否则 `HttpClient(base_url_override or service_ctx.base_url)` + auth 链）
→ `plugin_fn(params=merged, http_client=client, config)` → raw dict 原样返回。

## 与计划的偏差（有理由）
1. `event_cb` 参数在 todo 1 内未消费：仅占位保签名稳定，todo 2 的 pipeline 分支再用；内核不伪造事件。
2. jail 扫描位置用 `("argument","path","query","header","body")` 五处（pipeline 原循环是四处无 argument）：
   插件 params 可含 argument 位，超集扫描更严而 CLI 侧 server_mode=False 零影响。
3. CLI wrapper 里 `PluginNotFoundError` 必须写在 `CliyError` 之前捕获（子类关系），打印原文
   `Plugin method 'x' not found` 后 return（exit 0），与原 897-899 字节一致。
4. CLI wrapper 先解析 `_server` 再进 try（原代码在 try 内解析 root-obj；root-obj 缺失时原代码
   `(ctx.find_root().obj or {})` 已做 None 防护，不抛异常，故外移无行为差）。

## 给 todo 4 的隔离纪律（已验证有效）
- throwaway probes 只放 `/tmp`（本次 `/var/folders/.../T/opencode/todo1_*.py|txt`），绝不进 `tests/`。
- 每个 probe：入口 `snap = set(_scanned_dirs)`，`finally: PluginRegistry.clear(); _scanned_dirs.clear(); _scanned_dirs.update(snap)`。
- 注意：`PluginRegistry.clear()` 会连带清空 `_scanned_dirs`（见 `plugin/__init__.py:163-171`），故恢复顺序必须是 clear → restore snapshot，不可反。

## QA 证据
- 基线 7 cases（echo-params / raises-CliyError / raises-raw / unknown-name / _formatted静默 / query-merge / root-server-obj），重构前后 `diff` 空（DIFF_EMPTY）。
- query-merge 证实：query 值只进嵌套 `merged["query"]`，不进顶层（仅 path/body/argument 上浮）。
- `pytest tests/test_builder.py -v`：8 passed。
- 内核直调：未知名抛 `PluginNotFoundError`（消息含名）；server_mode=True 拦 jail 外路径（插件零调用，抛 CliyError 指引）；`server_tmp_files` bypass 放行；CLI 模式直通。

# serve-plugin-methods learnings — todo 2 (2026-09-14)

## 插入位置（钉死）
`execute_pipeline` 内 `validate` 事件块之后、`# Read file-type params` 注释之前：
```python
_method_type = method_spec.get("type") or ""
if _method_type.startswith("plugin:"):
    ...
    return _plugin_result
```
分支内直接 return——file 内容读取循环（366-407）、resolver 块、assembler/HTTP 全跳过。
`validate` 事件照常先触发（分支在其后）。

## 事件载荷形状（钉死）
- `request`: `{"plugin": name, "result_preview": redact_sensitive(result)}`
- `response`: 同上（同值两次发送；request 在内核成功返回后发送——无伪造 method/url）。
- 无 `auth`/`format` 事件（插件分支不走这两段；flow 侧 `_emit_format_event` 由调用方照常处理）。
- preview 必经 `redact_sensitive`（secret-leak bar：probe 用 `token: SUPERSECRET` 证实事件流仅见 `***`）。

## 返回语义（与计划字句的裁决，大声记录）
- `_formatted`：分支剥 marker 返回内容（`{k:v except _formatted}` 拷贝，不动内核 raw）。
- `raw_response=True`：同样返回剥 marker 后的 raw dict——两条路径返回值相同，因为插件输出
  按计划根本不进 `parse_response`/output 解析。`raw_response` 在此分支是 no-op（非“保留 marker”）。
  若 todo 5 E2E 需要 raw 保留 marker，请单独提计划修订；默认当前语义。
- 未知插件：`PluginNotFoundError`（`CliyError` 子类）直接抛给调用方，分支内不捕获
  （CLI wrapper 另行捕获；serve/MCP 转 isError 由下游做）。

## 签名变更
`execute_pipeline(..., server_tmp_files=None, spec_dir: str | None = None)`——新增末尾可选参数，
全透传给内核（含 `event_cb`，本次正式接线；内核此前已占位）。

## QA 证据
- Failure-first：无 http 块 fixture 改前 `ValueError: method_spec.http.method is required`，改后成功返回。
- `execute_pipeline` 结果 `== json.loads(CLI 输出)`（echo fixture）。
- `--server` parity：`base_url_override` 与默认 ctx 同效（无 auth 下相等）。
- `pytest tests/test_builder.py tests/test_serve_events.py`：20 passed + 1 failed，
  失败为 `test_run_flow_step_callback`——clean HEAD worktree（7813911）同测同败，属基线已有失败，与本分支无关。
- todo 1 回归：7-case CLI 基线 diff 仍空（CLI_DIFF_EMPTY）。

# serve-plugin-methods learnings — todo 3 (2026-09-14)

## Seams（显式传参，ServiceContext 未动）
1. `orchestrator.FlowContext.spec_dir: str | None = None`（server_tmp_files 之后）。
2. `run_flow(..., spec_dir: str | None = None)`（末尾可选）→ `FlowContext(spec_dir=spec_dir)` 直接赋值
   （无 service_ctx 回退项——ServiceContext 不 stamp spec_dir，按计划）。
3. `execute_use_step` → `execute_pipeline(..., spec_dir=getattr(context, "spec_dir", None))`
   （getattr 仿 jail 四件套，防旧 context）。
4. for_each `iter_ctx` 拷贝同样带 `spec_dir=getattr(...)`（否则循环内 use 步丢失发现路径）。
5. serve `_run_command` → `execute_pipeline(..., spec_dir=execution.spec_dir)`。
6. serve `_run_flow` → `run_flow(..., spec_dir=execution.spec_dir)`。
7. MCP `execute_command` → `execute_pipeline(..., spec_dir=self.spec_dir)`。
8. MCP `execute_flow` → `run_flow(..., spec_dir=self.spec_dir)`。
9. CLI `flow run`（builder.py:1148）未改——默认 None，CLI 行为零变化。

## QA 证据
- Failure-first：spec-local-only 插件目录（tmp/plugins，entry-points/`~/.cliyard` 均无）→
  serve 形状（server_mode=True, 无 spec_dir）`PluginNotFoundError`；加 `spec_dir` 即成功。
- 4 缝 mock 断言（patch 各命名空间 `execute_pipeline`/`run_flow` 捕获 kwargs）全过。
- 真链路：serve-context `execute_pipeline(spec_dir, server_mode=True)` 成功；
  `run_flow(spec_dir)` 经 `use: t.do`（plugin 方法）`step_done` 成功。
- Jail 对比（计数插件）：server_mode=True + jail 外路径 → `CliyError` 且插件零调用；
  `server_tmp_files` bypass 成功；server_mode=False 直通（CLI 不变）。
- 内核未动证明：todo-1 七 case CLI diff 空 + todo-2 verify 五项全过。
- `pytest test_flow + test_builder + test_serve_events`：93 passed，仅已知基线失败
  `test_run_flow_step_callback`。

## 已知新红（1 个，机械性，留给 todo 4）
- `tests/test_mcp_executor.py::test_execute_flow_collects_step_summary`：其 `fake_run_flow`
  签名无 `spec_dir` 形参，被 `execute_flow` 的新显式 kwarg 打破
  （`TypeError: ... got an unexpected keyword argument 'spec_dir'`）。
  不碰 tests（本 todo 禁区）；todo 4 修 double 加 `spec_dir=None` 即可。
  另两红（serve executor flow 事件序列 ×2）HEAD worktree 同败，属基线。

# serve-plugin-methods learnings — todo 4 (2026-09-14)

## 新文件 `tests/test_method_plugin.py`（17 tests，全绿）
- CLI 类（6）：echo 输出 / `_formatted` 静默 / 未知名（exit 0 原文）/ CliyError 打印 /
  非 CliyError `错误:` 前缀 / 缺必填（click exit 2 `Missing option`）。
- pipeline 类（6）：与 CLI JSON 相等 / 无 http 块成功 / 未知名抛 `PluginNotFoundError` /
  query merge parity（仅嵌套）/ 缺必填抛 `ValidationError` / `_formatted` 去 marker
  （默认与 raw_response 一致）。
- jail 类（3）：True 拦 + 计数插件零调用 / `server_tmp_files` bypass / False 直通。
- 发现类（2）：spec-local 插件目录（tmp/plugins，uuid 模块名防 `sys.modules` 碰撞）
  `spec_dir` 直调成功；不传则 `PluginNotFoundError`。
- 隔离：autouse fixture（`PluginRegistry.clear()` + `_scanned_dirs` 快照/恢复；
  顺序 clear→restore，因 clear 连带清空缓存）；uuid 插件名/模块名防跨 test 污染；
  无 fixtures/*.py 导入。

## todo 3 fallout 修（单行）
- `tests/test_mcp_executor.py:156`：`fake_run_flow(..., spec_dir=None)`——TypeError 复现后修，
  `test_mcp_executor.py` 13/13 绿。其他 fake 均 `**kwargs` 风格，本处按任务要求用显式形参。

## QA 证据
- 新文件隔离：17 passed；全 suite 内顺序：同 17 项无一失败（590 passed 中的一部分）。
- 全 suite：8 failed 皆基线——3 mcp_http_e2e + 2 serve_app（HEAD worktree 同败，环境类）；
  3 flow 事件（`test_run_flow_step_callback` + serve executor ×2，既往 HEAD 已证）。
- 产品代码零改动（本 todo 只动 tests + notepad）。
