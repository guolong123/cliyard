# cliyard

**CLI + YAML + Yard** — a framework that generates CLI commands from YAML specs. Define your REST API in a few YAML files, and cliyard produces a fully-structured Click CLI with parameters, types, response formatting, and auto-generated help.

## Installation

```bash
pip install cliyard

# Or for development
pip install -e .
```

## Quick Start

```bash
# Generate a CLI from spec files
cliyard gen --name mycli --defs-path ./specs/
cd mycli && pip install -e .

# Use it
mycli --help
mycli repos list --page-size 10
```

## How It Works

cliyard turns a directory of YAML files into CLI commands. One directory equals one API service. Each YAML file becomes a resource group with subcommands for every defined method.

```
specs/
├── _auth.yaml              # Service config: name, servers, auth chain
├── _groups.yaml            # (optional) Group definitions for nesting
├── _flows.yaml             # (optional) Flow orchestration index
├── resources/              # Resource YAML files
│   ├── repos.yaml
│   └── ...
├── flows/                  # (optional) Flow step definitions
│   ├── _flow_create_repo.yaml
│   └── ...
└── plugins/                # Python plugins
    └── *.py
```

**Data flow** for a single invocation:

```
CLI input → bind & validate → auth chain → assemble request → HTTP call → format response
```

## Features

- **YAML-driven**: Add a new API resource by creating a `.yaml` file, no code changes
- **Plugin system**: 7 extension points for auth, types, hooks, methods, commands, field resolvers, flow steps
- **Flow orchestration**: Define multi-step workflows with conditional branching, loops, and lifecycle hooks
- **Multi-server**: Support multiple API endpoints in a single CLI
- **Rich output**: Tables, JSON, CSV formatting with datetime conversion
- **Resource grouping**: Nest related commands under parent groups

## Web UI (cliyard serve)

`cliyard serve` 以 Web 方式启动 spec 目录：命令自动渲染为表单，执行过程以步骤流实时展示，并保留可重放的执行历史。

### 用法

```bash
cliyard serve ./specs
cliyard serve examples/demo --port 8080 --host 0.0.0.0
cliyard serve examples/demo --open --reload
```

### 选项

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 绑定地址 |
| `--port` | `8080` | 监听端口 |
| `--open` | 关闭 | 启动后自动打开浏览器 |
| `--reload` | 关闭 | uvicorn 自动重载（配合前端开发） |

### 前端构建

静态资源由 FastAPI 托管，首次使用前需构建前端：

```bash
cd webui && npm install && npm run build
```

未构建时访问 `/` 返回提示 JSON（而非 500）。开发模式可在 `webui/` 下运行 `npm run dev`（Vite 默认 `5173` 端口，已配置 CORS）。

### API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/spec` | 命令树 + flow 元数据（含 JSON Schema） |
| POST | `/api/execute` | 提交命令/流程执行，返回 `execution_id` |
| GET | `/api/executions/{id}/stream` | SSE 步骤流（validate→auth→…→done） |
| GET | `/api/executions/{id}` | 执行状态 + 全量步骤（轮询兜底） |
| GET | `/api/executions` | 历史列表（时间倒序、分页、kind 过滤） |
| POST | `/api/executions/{id}/replay` | 用历史参数重放执行 |
| DELETE | `/api/executions` | 清空历史 |
| GET | `/api/auth/profiles` | 凭据 profile 列表（token 掩码） |
| POST | `/api/auth/switch` | 切换当前 profile |
| POST | `/api/upload`（serve）/ `/upload`（MCP） | 文件交接点：multipart `file` 字段上传，返回 server 绝对路径 |

### 示例

```bash
# 启动 demo 服务
cliyard serve examples/demo --port 8080

# 提交一个命令执行
curl -X POST http://127.0.0.1:8080/api/execute \
  -H 'Content-Type: application/json' \
  -d '{"kind":"command","target":"user.list","params":{}}'
# => {"execution_id":"..."}

# 订阅步骤流（SSE）
curl -N http://127.0.0.1:8080/api/executions/<id>/stream

# 查看历史
curl http://127.0.0.1:8080/api/executions
```

### 文件参数（MCP）

MCP 的 tool call 通道传不了 client 本地文件，所以 `type: file` 参数走一次
HTTP 交接：**有 shell 的 agent 先 `POST .../upload`（一条 curl）拿到 server
绝对路径，再把该路径填进业务工具的 `file` 参数照常调用**。后续执行链路除
服务端路径校验外零改动。

```bash
# 1. 上传文件，拿 server 绝对路径（serve 侧是 /api/upload，MCP 直连是 /upload）
curl -X POST http://127.0.0.1:8080/api/upload \
  -F "file=@report.pdf"
# => {"path":".../cliyard-upload-<8hex>-report.pdf","file_name":"...","bytes":12345,"expires_at":"..."}

# 远端（非本地）服务需带 token；本地回环无 token 时免鉴
curl -X POST http://127.0.0.1:8080/api/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@report.pdf"

# 2. 把返回的 path 填进业务工具的 file 参数，照常调用
curl -X POST http://127.0.0.1:8080/api/execute \
  -H 'Content-Type: application/json' \
  -d '{"kind":"command","target":"repos.upload","params":{"file":"<path>"}}'
# => {"execution_id":"..."}

# 3. 同一 path 在保留期内可重放（不消费删除）；过期/被清理后调业务工具会
#    返回可用错误（请重新 POST /upload 上传），按第 1 步重传即可
```

默认值（v1 为代码常量，不做 CLI 旗标）：保留期 TTL **30 分钟**、配额
**500MB / 1000 个**（超限 oldest-first 淘汰）、单文件上限 **10MB**（超限 413）。

相关选项（`cliyard serve` 与 `cliyard mcp` 一致）：

| 选项 | 默认值 | 说明 |
|---|---|---|
| `--upload-dir` | `<tmp>/cliyard-uploads` | 服务端交接存储目录（危险目录启动即拒绝） |
| `--upload-base-url` | `http://<host>:<port>` | 对外地址（缺省从监听地址推导，`0.0.0.0` 回退 `127.0.0.1`） |
| `--file-allow-dirs` | 空（可重复） | 上传目录之外的额外服务端可读目录 |

`serve` 另有 `--token`（与 MCP `--token` 同语义：绑定非本地地址时强制，
`/api/upload` 无 token 或错 token 返回 401）。

适用边界：**仅适用于有 shell 的 agent**（能执行 curl 的 MCP host）。
无 shell 的纯聊天 host 无法传递本地文件——暂不支持（调用会返回可执行
的上传指引而非静默失败）；`source_url` 服务端拉取为后续项。

stdio 同机模式是例外：server 与 agent 同一台机器，`file` 参数直接填本地
路径即可，无需上传（无路径校验）。

## MCP (cliyard mcp)

`cliyard mcp <spec-dir>` 把同一套 YAML spec 暴露为 MCP server（stdio 默认，
`--transport http` 可选）。工具与 CLI / WebUI 共用同一执行内核，参数校验与
执行语义逐字一致。

### flat vs grouped：工具布局

`--mcp-tool-mode flat|grouped`（默认 `flat`）控制 `tools/list` 的输出形状：

| | flat（默认） | grouped |
|---|---|---|
| 工具粒度 | 一个 method 一个工具（`pet.list`、`pet.create`…） | 一个 resource 一个工具（`pet`，`operation` 枚举选方法） |
| 工具数量 | 方法总数（ketacli 约 275；demo 29） | 资源数 + flows + 插件命名空间（ketacli 约 71；demo 9） |
| 执行精度 | — | 与 flat 同参同结果（静态放宽、运行时收紧：缺必填 / 类型错走同一 binder，文案同字） |
| 延迟 | — | 多一次 `operation` 查表分发，可忽略；`tools/list` 负载更小 |
| 适用 | spec 小、工具名精确直达 | spec 大（50+ 工具）、模型 context 紧张、工具名相近易混淆 |

flow（`flow.*`）两种模式都保持 1:1，不合并；`cmd.*` 插件在 grouped 下按顶层命名空间合并
（`cmd.signal.*` → `cmd.signal`，`operation` 取剩余点分路径）。

何时开 grouped：`tools/list` 返回几十上百个工具、agent 开始选错工具或 context 被挤爆时；
只读小 spec 用默认 flat 即可。

### 示例

```bash
# 默认 flat：29 个工具
cliyard mcp examples/demo --transport http --port 8081 &

# grouped：9 个工具（3 resources + 4 flows + 2 插件命名空间）
cliyard mcp examples/demo --transport http --port 8082 --mcp-tool-mode grouped &
```

另起终端，握手后取 `tools/list` 并计数（grouped 端口）：

```bash
python3 - <<'EOF'
import json, urllib.request
BASE = "http://127.0.0.1:8082/mcp"
def post(payload, sid=None):
    req = urllib.request.Request(BASE, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    if sid:
        req.add_header("mcp-session-id", sid)
    with urllib.request.urlopen(req) as r:
        return r.read().decode(), r.headers.get("mcp-session-id")
body, sid = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "demo", "version": "1"}}})
post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
body, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
names = []
for line in body.splitlines():
    if line.startswith("data: "):
        names = [t["name"] for t in json.loads(line[6:])["result"]["tools"]]
print(len(names), sorted(names))
EOF
# => 9 ['cmd.health', 'cmd.signal', 'flow.add_user', 'flow.hook_demo',
#       'flow.plugin_demo', 'flow.retry_demo', 'order', 'pet', 'user']
```

grouped 调用只多一个 `operation` 参数，其余与 flat 同名同义：

```json
{"name": "pet", "arguments": {"operation": "list"}}
// 等价于 flat 下 {"name": "pet.list", "arguments": {}}
```

### 兼容声明

- 默认 `flat`，不传 `--mcp-tool-mode` 的行为与之前逐字节一致（含工具名）。
- flat 工具名稳定，不改名；grouped 为 opt-in，不改变任何 flat 表。
- 当前无改变默认值的计划（改默认是 breaking change）。

## Examples

See the [examples/](examples/) directory for ready-to-use spec sets:

- `examples/demo/` — Pet Store API demo with resource commands, plugins, and flow orchestration

```
# Library mode (read YAML at runtime)
python3 -c "from cliyard.runtime import create_cli; create_cli('examples/demo')()"

# Flow orchestration
petstore flow list
petstore flow run add-user --name 张三
petstore flow run retry-demo
petstore flow run plugin-demo
petstore flow run hook-demo
```

## Documentation

Generated CLI projects include a `README.md` with full usage docs covering:

- Usage & environment management
- Adding new resources
- Plugin authoring (all 7 plugin types)
- Flow orchestration with conditional branching, loops, and hooks
- Multi-server configuration
- Custom method plugins

## License

MIT
