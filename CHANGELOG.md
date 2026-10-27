# Changelog

## 0.21.0 (BREAKING)

### Breaking changes
- **MCP 结构化命令输出统一 envelope**：`cmd.*`（以及声明为 `json` 的命令）返回
  `{status: ok|empty|error, items: [...], total: N, warn?: string, text?: string}`，
  不再是裸文本/裸数组/裸对象。
- **超限 notice 与正常路径同契约**：始终包含
  `status/items(list)/total/truncated/item_count/url/limit`；条数使用 `item_count`，
  `items` 永远是数组（不再放整数）。
- **文本流命令必须显式声明**：删除框架内的产品白名单。命令插件使用
  `@register_command("name", output_kind="text")`，HTTP spec 使用
  `output.kind: text`（或 `output.envelope: false`）。

### Migration
1. 客户端读取 `items/status/total`；需要总条数时用 `item_count`（超限分支）。
2. 依赖原始文本的命令：在插件声明 `output_kind="text"`，或在 spec 加
   `output: {kind: text}`。
3. 关闭自动投影：spec 设置 `output.unified_projection: false` 或 `output.projection: false`。

### Fixes / features
- 投影实现统一到 `output/projection.py`（CLI/serve/MCP 一致，含 allowed/ignored）。
- 标量列表 / 混合列表超限不再丢数据或返回空；全嵌套对象不再投影成 `{}`。
- `infer_schema` 对 map 形态 key（邮箱/UUID/前缀 ID/结构化 map）脱敏为 `<map-key>`。
- `fit_items` 小 limit 不再反向超限。
- `totalPages` 通过 `output.total_pages` 真正接通（不切片）。
