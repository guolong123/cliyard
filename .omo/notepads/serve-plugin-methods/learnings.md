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
