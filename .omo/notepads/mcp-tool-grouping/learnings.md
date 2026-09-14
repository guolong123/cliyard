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

## 2026-09-14 — todo 2: union schema 构造规则落地（tools.py）

### 定稿规则（todo 4 消费本形状，不要再改语义）
- 碰撞判定 = 同名字段 schema 按 `!=` 比较：完全相等视为同一含义（不标注）；
  不等即不同含义 → keep-first（首操作胜出）+ 中标 `description` 追加
  `（X 取自 opA；opB 的 X 被遮蔽，用 opB 时缺该字段走运行时报错）`
  （多遮蔽操作各一句，以 `；` 连接）。比较基准是 pristine 中标副本
  （`winner_prop`），避免标注文本污染第三操作的相等判断。
- 中标属性写入 union 前做浅拷贝（`dict(value)`）——绝不 mutation 命令树原 schema，
  否则同进程内 flat/grouped 共享树会被污染。
- `required` 恒 `["operation"]`；顶层 description 改为多行：
  `{资源描述}\n操作：\n<op> - <短描述>\n…`（一操作一行）；
  `operation` 属性 description 同样多行（`合法值：\n…`）。
- 逐操作文档只进 description 字符串；实 dump（`as_tool().model_dump()` 全表 JSON）
  证明 `x-` 键集合 ⊆ `{"x-location"}`（后者是 schema_bridge 自带、flat 一致的参数元数据，
  非本 todo 引入）。
- 文件 interplay：file 指引文本由各操作 schema 自带（schema_bridge 已按
  `upload_base`×`transport` 渲染），合并时原文保留；`_grouped_resource_spec` 新增
  可选 kwarg `upload_base/transport`（只传给兜底补齐，不进 `build_union_schema`
  ——签名冻结），仅当**中标定义本身是 file 类型却缺三要素**时调用顶层 import 的
  `_file_upload_guide(upload_base, transport)` 补齐（正常命令树永不触发；
  被遮蔽的 file 定义不补，标注即文档）。顶层 import 无循环风险
  （`schema_bridge` 的 mcp 引用本就是函数内 lazy，且本文件早已顶层 import 同一模块）。
- 已知边界（deliberate，不修）：与 `operation` 选择器同名的业务参数被静默丢弃
  （`elif key == "operation": continue`）；`_register_grouped_commands` 零 method
  资源跳过（todo 1 已埋，复用）；浅拷贝只护顶层 `description`（嵌套从不改）。

### 验证证据（合成 spec `/tmp/probe2/item.yaml`：search.filter=int/query vs create.filter=string/body 碰撞；payload=string vs file 碰撞；attachment=file 独有）
- BEFORE（todo-1 构建）：`filter`/`payload` 合并无标注（`被遮蔽` 零命中）；
  create 的 file 型 `payload` 指引被静默丢弃、无迹可查。
- AFTER：`filter` 注 `（filter 取自 search；create 的 filter 被遮蔽，用 create 时缺该字段走运行时报错）`；
  `payload` 同理；`attachment` curl 三要素在，标注行 + curl 行同 schema 共存。
- 遮蔽语义 binder 实证：`bind_and_validate({'filter':'abc',...}, search_spec)` →
  `ValidationError: filter: Cannot convert 'abc' to integer`（首含义强制）；
  同值对 create 原 spec 通过（影子含义本可接受，反证遮蔽生效）。
- 空操作：`build_union_schema('empty','Empty',[])` → `enum:[]`/`required:["operation"]`，不崩。
- 4 组合（base None/`http://example.com:9000` × http/stdio）：`attachment` 描述三要素全在；
  base 缺省带 `--upload-base-url` 提示行，stdio 带 `file_path` 段，http 无。
- fake-token 扫描（demo + probe2 全分组表 × http/stdio，正则
  `sk-…|ghp_…|Bearer <12+>…`）：零命中（指引恒用 `$TOKEN` 字面占位）。
- flat 不变性：三 spec 目录 `mode="flat"` 归一化 JSON 与 todo-1 基线 `cmp` 一致（`IDENTICAL`×3）。
 - `pytest tests/test_mcp_tools.py tests/test_serve_schema.py -v` → 31 passed。

## 2026-09-14 — todo 6: flat 不变性证明（只读证据 lane，零代码改动）

### 基线与方法（钉死）
- Base commit = `2e1738d3dbd52127515619f7ab208b3b18517988`（grouping 栈 `3dd2eb0` 之前最后一个 commit）。
- Clean worktree：`git worktree add /tmp/cliyard-base-2e1738d 2e1738d`（detached HEAD），实验后已移除（见收据）。
- Clean env：每次网络相关命令前 `env | grep -i proxy` → exit 1（无输出，proxy vars 全 unset）；
  实际执行形如 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy ...`。
- 关键方法修正：当前 shell 的 `python3` 是 ketacli venv（`/Users/mac/01work/git-project/ketacli/.venv`），
  其 site-packages 内含一份** stale 安装版 cliyard 0.14.2**（`tools.py` 无 `mode` 参）；
  无 `PYTHONPATH` 时 `pytest` 实际测的是该旧拷贝（实证：多出
  `test_schema_type_mapping_in_enum_and_file KeyError: 'description'` 伪失败）。
  因此所有证据命令均带 `PYTHONPATH=<tree>/src`（`import cliyard` 路径已验为对应树内 `src`）。
- 归一化 dump 形状（base 无 `mode` 参/无 `operations` 字段，HEAD 有——归一化取交集）：
  `[{name,kind,target,description,input_schema}]` 按 name 排序 → `json.dumps(sort_keys=True)`。
  抛弃脚本 `/tmp/flat_dump.py`（`mode` 形参探测 + dict/list 兼容），实验后已删除。

### Suite 证据（5 套 flat 相关，零修改运行）
- 工作树命令：
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy PYTHONPATH=<repo>/src python3 -m pytest tests/test_mcp_tools.py tests/test_mcp_plugin_tools.py tests/test_mcp_executor.py tests/test_mcp_stdio_e2e.py tests/test_mcp_http_e2e.py -q`
- 工作树结果（HEAD `2fd05cd` 与并行 lane 落地后的 `dc4663c` 各跑一次，两次完全一致）：
  `3 failed, 50 passed`；失败精确名单：
  1. `tests/test_mcp_http_e2e.py::test_http_standalone_list_and_call` — `MCPError: Server returned an error response`
  2. `tests/test_mcp_http_e2e.py::test_http_mounted_into_serve_same_port` — `assert 502 == 200`（serve health `502 Bad Gateway`）
  3. `tests/test_mcp_http_e2e.py::test_http_auth_required_and_token_works` — `MCPError: Server returned an error response`
  （另有 `RuntimeError: Attempted to exit cancel scope...` 的 anyio 关闭期噪音，挂在 mcp client 收尾，非断言失败。）
- 基线命令（clean worktree 内同命令，仅 `PYTHONPATH=/tmp/cliyard-base-2e1738d/src`）：
  `3 failed, 50 passed` —— **同名同因**（同 3 个用例、同 3 行错误签名，`test_mcp_http_e2e.py -q` 单跑亦 `3 failed, 4 passed`）。
- 结论：3 失败全部 baseline-attributed（live-HTTP 环境类失败，已知 pre-existing 类别），**非阻塞，无 needs-fix 项**。

### 归一化 compare 证据（`examples/demo`，count=29）
- 工作树：`PYTHONPATH=<repo>/src python3 /tmp/flat_dump.py examples/demo /tmp/flat_worktree.json`
  → `count=29 sha256=c76786aae17537e8bda2074465305039b8a3beae83f1230b976bd7c448073d25`
- 基线：`PYTHONPATH=/tmp/cliyard-base-2e1738d/src python3 /tmp/flat_dump.py examples/demo /tmp/flat_base.json`
  → `count=29 sha256=c76786aae17537e8bda2074465305039b8a3beae83f1230b976bd7c448073d25`
- `cmp /tmp/flat_worktree.json /tmp/flat_base.json` → `IDENTICAL (cmp exit 0)`；
  `shasum -a 256` 双文件同哈希（见上）。
- 树中移动处理：首轮 compare 时 HEAD=`2fd05cd`；并行 lane 随后落地
  `dc4663c feat(mcp): dispatch grouped tool calls by operation`（todo 4）。
  已按 plan 要求在终态重跑 compare：`/tmp/flat_worktree2.json` 同为
  `count=29 sha256=c76786aa…d25`，`cmp` vs base 仍 `IDENTICAL`；
  5 套 suite 在 `dc4663c` 重跑亦为 `3 failed, 50 passed`（同名同因）。
  注：`operations` 载体字段已在树内出现（todo 4），但 flat 路径恒 `None` 且不在归一化形状内，故零影响。

### 清理收据
- `git worktree remove --force /tmp/cliyard-base-2e1738d`；`rm -f /tmp/flat_dump.py /tmp/flat_worktree.json /tmp/flat_base.json /tmp/flat_worktree2.json`；
  `git worktree list` 仅剩主树；`git status --short -- src/ tests/ examples/` 空（零 repo 文件改动，无提交，无 stash）。

## 2026-09-14 — todo 4：分组分发 + operations 载体（tools.py / executor.py）

### 载体形状（todo 5 直接消费，不要改名/改语义）
- `ToolSpec` frozen dataclass 新增唯一字段 `operations: dict[str, ToolSpec] | None = None`
  （`tools.py:60`；`from __future__ import annotations` 已在，无需前向引用技巧；
  旧构造零改动——缺省 `None` 即非分组工具）。
- `_grouped_resource_spec`（`tools.py:218-257`）为每个操作装一条原 ToolSpec：
  `operations[op] = _command_spec(f"{tool_name}.{op}", cmd, resource_desc, "")`，
  name 沿用扁平恒等式 `f"{T}.{op}"`（todo 1 钉死），kind=`"command"`，target 可执行。
  消歧资源同样成立（`tool_name=g1.token` → `g1.token.<op>` ∈ flat 表）。
- `as_tool()` 未动——`operations` 纯服务端载体，`model_dump()` 实证无泄漏
  （keys 无 `operations`；MCP wire 面零变化）。

### 分支位置与语义（`executor.py:315-342`，`execute_spec` 内唯一新增）
- `flow` / `plugin` 分支原样前置；`grouped` 分支：`args = dict(arguments or {})` 拷贝后
  `pop("operation")` → `spec.operations` 查表 → 递归 `execute_spec(original, args)`。
  `execute_command` / `execute_flow` / `execute_plugin_command` / `list_tools` /
  `call_tool` 零改动（含双根脱敏 `_sanitize_error + redact_upload_path` 堆叠顺序，
  todo 6 的 lane 在 `call_tool`——分组错误 raise 后自然落入同一通道）。
- 缺 `operation` → `ValueError: Tool 'repos' requires 'operation'; usage:
  repos operation=<create|list> ...; available operations: create, list`；
  未知 operation → `ValueError: Unknown operation 'nope' for tool 'repos';
  available operations: create, list`（合法值全列，排序后稳定）。
- 静态侧保持宽松（union `required==["operation"]`，todo 2 定稿），运行时由选中 op 的
  ORIGINAL spec 经 `execute_command` → binder 收紧——实证 `repos create` 缺 `name` 时
  flat 与 grouped 同字 `name: required`（isError）。
- `list_tools` 无需改代码：`tool_specs` 建表时已按 mode 隔离，实证 grouped 表无
  `repos.list`、flat 表无裸 `repos`（零交叉泄漏）。

### Deliberate edge（已钉死，不修——todo 5 同样遵守）
- 与 `operation` 选择器同名的业务参数在两处被丢弃：构造时
  `build_union_schema` 的 `elif key == "operation": continue`（todo 2），分发时本分支的
  `pop("operation")`（剥离点注释见 `executor.py:327-329`）。不做绕行/改名工程。

### 验证证据（`tests/fixtures/spec-dir`：repos.list + repos.create）
- BEFORE：grouped `repos` 直调 `execute_spec` → `ValueError: Invalid 'use' format
  'repos': expected 'resource.method'`（unknown-kind fallthrough 经 `execute_command`）。
- AFTER：flat-vs-grouped 同参双调（list `page=2` + create `name=n1`，fake pipeline 回显）
  `json.dumps(sort_keys=True)` 逐字节 `IDENTICAL`×2；含 secret 回显双调同字脱敏
  （`abc123` 零泄漏）；missing/unknown 经 `call_tool` 均为 isError（上贴文案逐字）。
- `pytest tests/test_mcp_executor.py tests/test_mcp_tools.py -v` → 25 passed。

## 2026-09-14 — todo 5：cmd.* 按命名空间分组（tools.py + executor 一行）

### 分组规则（`_group_plugin_tool_specs`，`tools.py` 新函数；todo 7 直接消费）
- 输入 = 扁平 `cmd.*` 表（`_walk_plugin_commands` 产物，hidden-skip 已在 walk 层执行，
  此处不做二次判断——隐藏命令天然不进表，合成树实证 `ghost` 出表又出 enum）。
- 切分规则：`cmd.` 后首段 = 命名空间，剩余点分路径 = operation
  （`cmd.pkg.info` → 工具 `cmd.pkg` + op `info`；三级 `cmd.a.b.c` → 工具 `cmd.a` + op `b.c`）。
- 单件命名空间（`cmd.hello`）保持单工具，`operation` 枚举单值取自身短名
  （`ops={'hello'}`，调用需 `{"operation": "hello"}`——冗余但统一，直通原 ToolSpec）。
- union schema 复用 `build_union_schema`（合成 `{"name": op, "desc": leaf短help,
  "schema": click-schema}` 条目；碰撞标注/单值 `required==["operation"]`/
  `operation` 同名业务参数丢弃语义与资源分组完全一致）。
- `operations` 载体值 = walk 产出的 ORIGINAL `ToolSpec`（kind=`plugin` 不变，
  target 可执行）——分发走 todo 4 的 `grouped` 分支递归 `execute_spec`，
  click 参数拼装（`execute_plugin_command`）零改动。

### 接线（两处 `mode` 透传；未知 mode 两处均 `ValueError` 同文案）
- `build_plugin_tool_specs(..., mode="flat")`：flat 原样返回（代码行零改动）；
  grouped → `_group_plugin_tool_specs`；bogus → `ValueError`。
- `build_tool_specs` 调用点改 `mode=mode` 透传（`tools.py` 注释行已标行为）。
- **executor.py 唯一改动（一行，非 dispatch）**：`__init__` L101 插件合并改
  `build_plugin_tool_specs(self.spec_dir, mode=self.tool_mode)`。理由（不改即错）：
  该行跑在 `build_tool_specs` 之后，grouped 下会把扁平 `cmd.*` 重新注回表——更糟的是
  扁平 `cmd.hello` 与分组单件**同键**，直接覆盖删除分组工具。
  `execute_spec` / `call_tool` / `list_tools` dispatch 逻辑零改动（todo 4 分支通用处理，
  已验证无需重写）。

### 验证证据（`tests/fixtures/spec-plugins`：hello + pkg{info,search}；另有全局 signal 组 11 叶）
- BEFORE（grouped executor 表）：`cmd.hello` / `cmd.pkg.info` / `cmd.pkg.search` /
  `cmd.signal.*`×11 全 `kind=plugin` 铺平（15 工具）。
- AFTER：`cmd.hello`（ops `[hello]`）/ `cmd.pkg`（ops `[info,search]`）/
  `cmd.signal`（ops 11 子路径）全 `kind=grouped`；扁平键零残留；flat executor 表
  14 键全 `kind=plugin`（双向零泄漏）。
- 真调：`cmd.pkg{search:[a,b]/5}` → `search ['a', 'b'] limit=5`；
  `cmd.pkg{info:app/verbose}` → `pkg app verbose=True`；
  `cmd.hello{hello/keta}` → `Hello, keta!`（均为 is_error=False）。
- isError：`operation=nope` → `Unknown operation 'nope' for tool 'cmd.pkg';
  available operations: info, search`；缺 operation → usage 一行；
  缺选中 op 必填（hello 无 name）→ click 原文 `Missing parameter: name`
 （运行时收紧，静态宽松——与资源分组同哲学）。
- flat 不变性：扁平 walk 代码行未动；`build_plugin_tool_specs` 缺省 `mode="flat"`
  逐字旧路；归一化 flat 插件表 sha256=`bf9715d7…0e9`（count=14，含环境全局 signal 组）。
- `pytest tests/test_mcp_plugin_tools.py tests/test_mcp_tools.py -v` → 24 passed。

## 2026-09-14 — todo 8：分组 E2E（stdio + http，含 file 工具）

### 文件（本 todo 唯一新增；零产品代码/测试改动）
- `tests/test_grouped_e2e.py` ONLY（6 用例，文件内 failure-first 排序）。

### 覆盖（全 real surface：real session / TestClient / MockUpstream；无 mock）
- unknown-op 先行：stdio 与 http 双通道 `call_tool("repos", {"operation":"nope"})`
  → 均为 `is_error=True` + `Unknown operation 'nope'` + `available operations`，
  且 `upstream.records == []`（坏 op 永不到上游）。
- stdio：子进程 `run_mcp_server(..., tool_mode='grouped')`（`-c` snippet 传
  `tool_mode='grouped'`）；`list_tools` 见裸 `repos` 且无 `repos.list` 泄漏；
  `repos{list/page=2}` + `repos{create/name=n1}` 双调成功，上游 GET + POST 各一。
- http：`build_mcp_http_app(..., tool_mode='grouped', upload_dir=...)` + 真 uvicorn
  （ephemeral port）+ `streamable_http_client` 真连；`repos{list}` 成功。
- file：真 `POST /upload`（TestClient）→ 取 path → grouped executor
  （`transport="http"`, jail 生效）`repos{upload/file=path}` → 上游 multipart 体含
  文件字节；同文件分组 schema `model_dump` 含 `POST` + `/upload` + `curl` 行
  （curl 三要素在分组 schema 同样可读）。
- negative：上传后 `unlink` 服务端文件 → 分组 `upload` 调 → `is_error=True` +
  `POST /upload` 短式指引 + `已过期/不存在`，上游零记录。

### 环境经验（钉死）
- 本机 shell 有 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`（`env | grep -i proxy`
  实证）；文件级 `_no_proxy` fixture（del 四变量 + `NO_PROXY=127.0.0.1,localhost`）
  后 stdio 子进程（继承 env）与 uvicorn/httpx 全链路直连，无需 `trust_env=False`。
- 执行恒带 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` +
  `PYTHONPATH=<repo>/src`（ketacli venv 内 stale cliyard 0.14.2 无 `mode` 参，
  不带 PYTHONPATH 会测错包——todo 6 已证实）。
- 有了 `_no_proxy` fixture，SDK `streamable_http_client` 在本环境一次即通
  （todo 6 的 3 个 http 基线失败主因即缺该 fixture；本文件 http 双用例全绿，
  无需 raw JSON-RPC 兜底）。

### 验证证据
- `env -u ... PYTHONPATH=src python3 -m pytest tests/test_grouped_e2e.py -v` →
  `6 passed`（首跑即绿）。
- Live curl 链（grouped uvicorn `:18231` + 常驻 MockUpstream，事后全杀、`rm -rf`）：
  1. `curl POST .../upload -F file=@local.bin` →
     `{"path":".../cliyard-upload-46448f0c-local.bin","bytes":24,...}`
  2. `POST /mcp initialize` 取 `mcp-session-id` → `notifications/initialized` → 202
  3. `tools/call {"name":"repos","arguments":{"operation":"upload","file":"<path>"}}` →
     `{"content":[{"text":"{\n  \"ok\": true\n}"}],"isError":false}`（上游 `{"ok":true}`，
     即 multipart 已达上游；字节级断言在 pytest 内）。
- 清理收据：后台 uvicorn + upstream 已 kill（`:18231` 连 `000` 确认 down），
  `/tmp/gchain` 已删；pytest 用 `tmp_path` + ephemeral ports，无残留。

### 给后续 lane 的 note
- stdio 分组靠启动参数，不要在已起的 flat server 上切 mode（executor 建表时快照）。
- `test_mcp_grouped_tools.py`（并行 lane）与本文件零交集：本文件只测传输层 E2E，
  不测 dispatch 单元语义。
