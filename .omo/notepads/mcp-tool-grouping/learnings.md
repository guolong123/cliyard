# Learnings — mcp-tool-grouping

Conventions, patterns, and successful approaches discovered during work on this plan.

---

## 2026-09-14 — todo 1: 分组表构造落地（tools.py）

### loud record for todo 2（必须读）
- 稳定 helper 签名（已钉死，todo 2 在此基础上扩展，不要改名/改参）：
  `build_union_schema(resource_name: str, resource_desc: str, commands: list[dict]) -> dict`
  其中 `commands` = 命令树中该资源的**原始 command 条目列表**（每个含 `name` / `desc` / `method` /
  `path` / `schema`，`schema` 即 `params_to_json_schema` 产物，形如
  `{"type":"object","properties":{...},"required":[...]}`）。
- 当前 MINIMAL 结构（todo 2 将替换/扩展为碰撞规则 + curl 模板）：
  `{"type":"object","properties":{"operation":{"type":"string","enum":[方法名...],"description":"选择要执行的操作...；<op> - <短描述>；..."},<各操作properties全并keep-first>...},"required":["operation"],"description":"<资源描述> | 操作：<op> - <短描述>；..."}`
- 短描述规则（`_short_op_desc`）：`desc` 按 `[。\n.!?！？；;]+` 取首句，缺省回退方法名。
- 消歧场景传给 `build_union_schema` 的是**短资源名**（如 `token`），工具名（`g1.token`）另行组装。

### loud record for todo 4（必须读）
- 分组工具 `ToolSpec` 形状：`kind="grouped"`（executor 尚无此分支——todo 4 负责），`target=<资源级工具名>`
  （如 `alpha` / `g1.token`；**不是**可执行 target，todo 4 剥 `operation` 后转交原 ToolSpec），
  `description` = union 顶层 description，`input_schema` = `build_union_schema` 产物。
- 映射恒等式（已用合成 spec 断言）：对每个分组工具 `T` 及其每个 `op`，`f"{T}.{op}"` 必在 flat 表中。
  即 `tool.operation` 拼出扁平名——分发实现可直接复用该恒等式查 flat 表。
- `ToolSpec` 仍 frozen、无新字段（todo 4 负责加 `operations` 载体字段）。
- flow（`flow.*`）与命令级插件（`cmd.*`）在本 todo 中原样透传，未分组——todo 5 拥有插件分组。

### 本 todo 的实现位置
- `src/cliyard/server/mcp/tools.py` ONLY：新增 `import re`、`_short_op_desc`、`build_union_schema`、
  `_grouped_resource_spec`、`_register_flat_commands`（原 flat 循环逐行搬运）、
  `_register_grouped_commands`；`build_tool_specs(..., mode: str = "flat")` 按 mode 分发，
  未知 mode → `ValueError`。`_command_spec`、`_duplicate_resource_names`（L156-169 原体）、
  `_register` 未动；`_duplicate_resource_names` 内 return 后的 dead block（原 L171-182）原样保留未复制。
- 零 method 资源在分组路径直接跳过（todo 2 的 QA-failure 语义，此处先行兼容）。

### 验证证据
- BEFORE：`build_tool_specs('/tmp/probe1', mode='grouped')` → `TypeError: unexpected keyword 'mode'`。
- AFTER：合成 spec（alpha×2方法 + beta×2方法 + g1.token×2 + g2.token×3）grouped 表
  `count = 4 resources + 0 flows + 11 cmd.signal.* plugins = 15`；消歧恒等式全过；
  `mode='bogus'` → `ValueError`。
- flat 不变性：`examples/demo` / `tests/fixtures/spec-dir` / `tests/fixtures/spec-dup` 三处
  `mode="flat"` 表与改动前 `sort_keys=True` 归一化 JSON 逐字节 `cmp` 一致（`IDENTICAL`×3）。
- `pytest tests/test_mcp_tools.py tests/test_mcp_plugin_tools.py -v` → 24 passed。

## 2026-09-14 — todo 3: 模式透传 + CLI --mcp-tool-mode 落地

### 完整透传路径（CLI → executor → tools）
- `mcp_options.py`: 新增 `_OPT_MCP_TOOL_MODE`（`--mcp-tool-mode`,
  `click.Choice(['flat','grouped'])`, default `flat`），定义居末、
  `_apply_options` 首行应用 → `--help` 末位、`--transport` 仍首位。
  docstring "9 个"→"10 个" + 用法示例同步 `mcp_tool_mode`。
- `cli/mcp.py`（`cliyard mcp`）与 `runtime/mcp_command.py`（生成 CLI 的 `mcp`
  子命令）：函数签名各加 `mcp_tool_mode: str`，`run_mcp_server(..., tool_mode=mcp_tool_mode)`。
- `server/mcp/server.py`: `create_mcp_server` / `build_mcp_http_app` /
  `mount_mcp_http` / `run_mcp_server` / `run_mcp_stdio` 全部新增可选 kwarg
  `tool_mode: str = "flat"` 并逐层透传；旧调用方零改动。
- `server/mcp/executor.py`: `MCPExecutor.__init__(..., tool_mode="flat")`
  存 `self.tool_mode`，经 `inspect.signature(build_tool_specs)` 探测到 `mode`
  形参后以 `mode=tool_mode` 严格透传（不做值校验；未知值由 tools.py `ValueError` 收紧）。
- 终点：todo 1 的 `build_tool_specs(..., mode="flat")`（未知 mode → `ValueError`）
  在本 todo 执行中途并发落地——开工时探测为缺席（`TypeError` 路径），收尾时已存在，
  兼容垫片自动激活，无需二次修改。

### 两个 seeding fixes（本 todo 顺带钉死）
- BEFORE `mount_mcp_http` L216-222: `create_mcp_server(...)` 调用缺 `upload_base`
  → serve 嵌入模式 grouped 文件描述静默回退占位地址。
  AFTER: `create_mcp_server(..., upload_base=upload_base, ..., tool_mode=tool_mode)`。
- BEFORE `run_mcp_stdio` L271-288: 签名仅 `(server_override, upload_base)`，
  jail dirs 被丢弃（HTTP 与 stdio jail 语义分叉）。
  AFTER: 新增 `upload_dir / file_allow_dirs / tool_mode` 三个可选 kwarg 并透传；
  `run_mcp_server` stdio 分支改传 `resolved_upload_dir + file_allow_dirs + tool_mode`。

### loud record（阻塞项，out of scope，未改）
- `cliyard mcp ... --mcp-tool-mode foo` 经 `cli/__main__.py:main()` 出口时退出码为 **0**
  而非 2：`main()` 以 `standalone_mode=False` 调用后 `except UsageError: echo` 吞掉退出码
  （`--transport foo` 同样 exit 0，pre-existing）。生成 CLI 路径（`create_cli`，
  standalone 模式）对非法值正确 exit 2 + stderr。
  修复需碰 `cli/__main__.py`（不在本 todo 文件清单内），留给后续 todo / 另起任务，
  不要在本分支顺手改。

### 验证证据
- `python -m cliyard.cli mcp --help`: 首行 `--transport`，末位 `--mcp-tool-mode [flat|grouped] [default: flat]`；
  生成 CLI `mcp --help` 同序。
- 非法值：生成 CLI 路径 exit 2 + `Invalid value for '--mcp-tool-mode'`；`cliyard mcp` 路径
  exit 0（上条阻塞项，stderr 文案正确 `'foo' is not one of 'flat', 'grouped'`）。
- 数量对比（examples/demo）：flat 29 vs grouped 19；
  `MCPExecutor(tool_mode='grouped').tool_specs` keys 与直调 `build_tool_specs(mode='grouped')` 完全一致。
- `pytest tests/test_serve_cli.py tests/test_mcp_tools.py -v` → 20 passed（零测试修改）。
- LSP 不可用（basedpyright 未安装且用户已拒装）；以全模块 import OK + focused pytest 代替。

## 2026-09-14 — todo 3 fixup: UsageError 出口 exit 2（__main__.py）

### Root cause（已证实）
- `cli/__main__.py:main()` 以 `cli(standalone_mode=False)` 调用，`UsageError`
  变成抛异常而非进程退出；`except UsageError: echo` 后无 `sys.exit` → 非法值 exit 0。
- BEFORE 证据：`mcp --mcp-tool-mode foo` exit 0；`mcp --transport foo` exit 0（同 bug）；
  `--help` exit 0。

### Fix（`src/cliyard/cli/__main__.py` 唯一改动，一行）
- `except UsageError` 分支追加 `sys.exit(2)`（Click 标准码；`sys` 已 import）。
- `MissingParameter` 有独立的前置分支（仍 exit 0，无变化）；
  `NoArgsIsHelpError` 是 `UsageError` 子类且其 handler 排在后面（本就 dead code），
  故裸 `cliyard` 走同一分支：stderr 文案与改前逐字一致（仍 `Error: Usage: ...`），
  仅退出码 0→2——裸调用本就是 usage error，exit 2 对齐 Click 语义。

### AFTER 证据
- `mcp examples/demo --mcp-tool-mode foo` → exit 2 + `'foo' is not one of 'flat', 'grouped'`。
- `mcp examples/demo --transport foo` → exit 2（同 fix 顺带修好，record）。
- `mcp --help` / `--version` / `gen --help` → exit 0（成功路径无变化）。
- `cliyard gen`（缺 `--name`，MissingParameter 分支）→ 仍 exit 0（其他错误类无变化）。
- `pytest tests/test_serve_cli.py -v` → 8 passed；`pytest tests/test_mcp_cli.py -v` →
  10 passed（sanity；两套皆经 CliRunner 直调 `cli`，不走 `main()`，无 exit-0-on-usage 断言，
  零测试修改）。
