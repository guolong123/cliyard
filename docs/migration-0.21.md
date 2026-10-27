# 迁移到 cliyard 0.21（BREAKING）

## 1) 结构化命令输出
0.21 起，MCP 的结构化命令（默认 `output_kind="json"`）统一返回：

```json
{"status":"ok","items":[...],"total":3}
```

- 空结果：`{"status":"empty","items":[],"total":0}`
- 失败/拒绝：`{"status":"error","items":[],"total":0,"warn":"..."}`
- 非 JSON 文本输出：`{"status":"ok","items":[],"total":0,"text":"..."}`；
  若文本像错误（error/失败/500/denied…）则为 `status:"error"` + `warn`

**迁移**：客户端按 `items/status/total` 读取；不要再假设裸数组。

## 2) 超限 notice 与正常路径同契约
```json
{"status":"ok","truncated":true,"items":[...],"returned":N,"item_count":10000,"total":10000,"url":"...","limit":50000}
```
- `items` 永远是数组；总条数用 `item_count`（不要从 `items` 推断）。
- 非分页/不可投影时：`items: []` + `item_count` + `url`（完整数据）。

## 3) 文本流命令声明
框架不再内置 `cmd.skills/cmd.spl/...` 白名单。

```python
@register_command("skills", output_kind="text")
def register_skills(cli, ctx): ...
```

HTTP spec：

```yaml
output:
  kind: text      # 或 envelope: false
```

未声明的命令默认走结构化 envelope。

## 4) 关闭自动投影
```yaml
output:
  items_path: $.items
  unified_projection: false   # 或 projection: false
```
