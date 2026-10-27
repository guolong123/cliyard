"""MCPExecutor —— 把 MCP tool call 翻译为 serve 执行内核调用。

每次 tool call 走与 serve ``execution_manager._run_command`` 相同的路径：

    load_service（缓存一次）→ _lookup_resource_method
    → build_service_context（env / 已存凭据自动注入，与 runner 同解析链）
    → _bridge_file_params（file 参数 base64 → 临时文件，复用 serve 桥接）
    → execute_pipeline（同步阻塞，返回结构化结果）

随后把结果经 :func:`redact_sensitive` 脱敏后返回给 MCP 客户端（沿用 serve
事件同源脱敏，不泄露敏感字段）。

MCP 回调是 async，而 ``execute_pipeline`` 是同步阻塞的 HTTP 调用——tool 回调内
用 :func:`anyio.to_thread.run_sync` 放进线程池执行，避免阻塞事件循环
（Streamable HTTP 模式关键，对应方案 R2）。

``--server`` / ``<SERVICE>_SERVER`` 环境覆盖：``build_service_context`` 已内置
runner 的 ``_resolve_base_url_override`` 解析链（显式参数 > ``<SERVICE>_SERVER``
> ``CLIYARD_SERVER``）。构造 executor 时传入 ``server_override`` 会作为显式
``base_url_override`` 传给 ``build_service_context``，等价于 CLI 的 ``--server``
运行时覆盖（方案 R7）——不写全局环境变量，避免污染进程其他执行。
"""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

import anyio
from mcp.server.lowlevel import Server as MCPServer
from mcp.types import CallToolResult, ListToolsResult, TextContent

from cliyard.engine.builder import ServiceContext, execute_pipeline
from cliyard.engine.loader import load_cases, load_flows, load_service
from cliyard.engine.orchestrator import _lookup_resource_method, run_flow
from cliyard.server.context import build_service_context
from cliyard.server.executor import _sanitize_error, execution_manager
from cliyard.server.redact import redact_sensitive
from cliyard.server.uploads import redact_upload_path, sweep as _sweep_uploads

from cliyard.server.mcp.context import raw_tool_arguments
from cliyard.server.mcp.tools import (
    ToolSpec,
    build_plugin_tool_specs,
    build_tool_specs,
    search_catalog,
    short_tool_desc,
)

logger = logging.getLogger("cliyard.server.mcp")

# P1-4: structured commands return a uniform envelope; text-stream tools are
# explicitly whitelisted and keep their raw text contract. New text tools must
# be added here (and documented) — everything else is structured by default.
# Output kinds are declared by tool metadata (spec ``output.kind`` or command
# plugin ``register_command(output_kind=...)``); no product names live here.
def command_envelope(out: str, err: str, output_kind: str = "json") -> Any:
    """Normalise command output according to the declared *output_kind*."""
    if output_kind == "text":
        if err and out:
            return f"{out}\n[stderr] {err}"
        return out or err or "(no output)"
    if not out:
        if err:
            return {"status": "error", "items": [], "total": 0, "warn": err}
        return {"status": "empty", "items": [], "total": 0}
    try:
        payload = json.loads(out)
    except (TypeError, ValueError):
        return {"status": "ok", "items": [], "total": 0, "text": out,
                **({"warn": err} if err else {})}
    result: dict[str, Any]
    if isinstance(payload, list):
        result = {"status": "ok" if payload else "empty", "items": payload, "total": len(payload)}
    elif isinstance(payload, dict):
        payload_status = str(payload.get("status") or "")
        payload_warn = payload.get("warn")
        if "items" in payload:
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            total = payload.get("total")
            if not isinstance(total, int):
                total = len(items)
            status = "error" if payload_status == "error" else ("ok" if items else "empty")
            result = {"status": status, "items": items, "total": total}
            for key, value in payload.items():
                if key not in ("items", "total", "status", "warn"):
                    result[key] = value
        elif payload_status == "error":
            result = {"status": "error", "items": [], "total": 0}
            result.update({k: v for k, v in payload.items() if k not in ("status", "warn")})
        else:
            result = {"status": "ok", "items": [payload], "total": 1}
        if payload_warn:
            result["warn"] = payload_warn
    else:
        result = {"status": "ok", "items": [payload], "total": 1}
    if err:
        result["warn"] = err
    return result


def _render_result(result: Any) -> str:
    """Render a structured result as MCP text (dict/list → indented JSON)."""
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass
    return str(result)


def _projection_spec(output_spec: dict[str, Any] | None) -> dict[str, Any]:
    spec = dict((output_spec or {}).get("project") or {})
    spec.setdefault("fields_param", "fields")
    spec.setdefault("brief_param", "brief")
    spec.setdefault("brief_fields", [])
    spec.setdefault("allowed_fields", [])
    return spec


def _project_items(
    result: dict[str, Any],
    pspec: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """Project items, or return None when projection would empty the payload."""
    from cliyard.output import projection

    items = result.get("items")
    if not isinstance(items, list) or not items:
        return None
    work = dict(pspec)
    if not work.get("brief_fields"):
        work["brief_fields"] = projection.default_brief_fields(items, work)
    if not work["brief_fields"]:
        return None
    projected = projection.project(result, work, params)
    if projected is result:
        return None
    projected_items = projected.get("items")
    if isinstance(projected_items, list) and items and all(
        isinstance(item, dict) and not item for item in projected_items
    ):
        return None
    return projected


class MCPExecutor:
    """基于 spec 的 MCP 工具执行器。

    Args:
        spec_dir: cliyard spec 目录。
        server_override: 可选的 base_url 运行时覆盖（同 CLI ``--server``，作为
            ``base_url_override`` 传入 ``build_service_context``）。
        upload_base: 对外 ``POST /upload`` 基地址（透传给 file 参数描述模板）。
        transport: ``"http"`` 或 ``"stdio"``（透传给 file 参数描述模板）。
        upload_dir: 上传目录（path jail 根；``None`` → 默认上传目录）。
        file_allow_dirs: 额外可读目录（``--file-allow-dirs``，path jail 放行）。
        tool_mode: ``"flat"``（每方法一工具，默认）、``"grouped"``（每资源一
            工具；严格透传给 ``build_tool_specs``，不做值校验）或
            ``"discover"``（仅暴露 search/describe/call/catalog 元工具，
            全量工具存于隐藏 catalog 按需展开）。
    """

    def __init__(
        self,
        spec_dir: str | Path,
        server_override: str | None = None,
        *,
        upload_base: str | None = None,
        transport: str = "http",
        upload_dir: str | None = None,
        file_allow_dirs: tuple[str, ...] | list[str] | None = None,
        tool_mode: str = "flat",
        output_base: str | None = None,
    ) -> None:
        self.spec_dir: str = str(Path(spec_dir).resolve())
        self.server_override = server_override
        self.upload_base = upload_base
        self.transport = transport
        self.upload_dir = upload_dir
        self.file_allow_dirs: tuple[str, ...] = tuple(file_allow_dirs or ())
        self.tool_mode = tool_mode
        self.output_base = output_base
        _spec_kwargs: dict[str, Any] = {"upload_base": upload_base, "transport": transport}
        # todo 1（`build_tool_specs(mode=...)`）尚未落地时的兼容：callee 无
        # `mode` 形参则暂存不传（`self.tool_mode` 仍可查），落地后自动透传。
        if "mode" in inspect.signature(build_tool_specs).parameters:
            _spec_kwargs["mode"] = tool_mode
        if self.tool_mode == "discover":
            self._catalog: dict[str, ToolSpec] = build_tool_specs(
                self.spec_dir,
                upload_base=upload_base,
                transport=transport,
                mode="flat",
            )
            self._tool_specs = build_tool_specs(self.spec_dir, **_spec_kwargs)
        else:
            self._catalog = {}
            self._tool_specs = build_tool_specs(self.spec_dir, **_spec_kwargs)
            # 命令级插件（@register_command）→ cmd.<command> 工具（flat）或
            # cmd.<ns> 分组工具（grouped；与 build_tool_specs 内合并同 mode，
            # 否则扁平 cmd.* 会泄漏进分组表，同名单件还会覆盖掉分组工具）
            self._tool_specs.update(
                build_plugin_tool_specs(self.spec_dir, mode=self.tool_mode)
            )
        self._service: dict[str, Any] | None = None
        self._flows: list[Any] | None = None

    # ------------------------------------------------------------------
    # spec 数据（启动时加载一次，工具表/执行均复用）
    # ------------------------------------------------------------------

    @property
    def tool_specs(self) -> dict[str, ToolSpec]:
        """已注册的 MCP 工具表（``name -> ToolSpec``）。"""
        return self._tool_specs

    def _load_service(self) -> dict[str, Any]:
        if self._service is None:
            self._service = load_service(self.spec_dir)
        return self._service

    def _load_flows(self) -> list[Any]:
        if self._flows is None:
            self._flows = load_flows(self.spec_dir)
        return self._flows

    # ------------------------------------------------------------------
    # 同步执行内核（在 anyio.to_thread 线程池中运行）
    # ------------------------------------------------------------------

    def execute_command(self, target: str, params: dict[str, Any]) -> Any:
        """执行一个 ``resource.method`` 命令，返回脱敏后的结构化结果。

        与 serve ``_run_command`` 相同路径；file 参数经 base64 桥接写入临时
        文件，执行结束清理。
        """
        _sweep_uploads(self.upload_dir)
        service = self._load_service()
        resource, method_spec = _lookup_resource_method(target, service)
        service_ctx = build_service_context(
            self.spec_dir,
            service,
            resource,
            base_url_override=self.server_override,
        )

        bridged, tmp_files = execution_manager._bridge_file_params(method_spec, params or {})
        try:
            result = execute_pipeline(
                kwargs=bridged,
                method_spec=method_spec,
                resource_spec=resource,
                service_ctx=service_ctx,
                resource_name=resource.get("name") or target.split(".")[0],
                server_mode=self.transport != "stdio",
                upload_dir=self.upload_dir,
                allow_dirs=self.file_allow_dirs,
                server_tmp_files=tmp_files,
                spec_dir=self.spec_dir,
            )
        finally:
            execution_manager._cleanup_tmp_files(tmp_files)
        return redact_sensitive(result)

    def execute_flow(self, flow_command: str, params: dict[str, Any]) -> Any:
        """执行一个 flow，返回 ``{outcome, step_count, steps}`` 汇总。

        ``run_flow`` 本身返回 None（结果存在内部 step_state）；通过 ``step_cb``
        收集每个 step 的 id/status/result_preview（事件载荷已脱敏），供 MCP
        客户端看到逐步结果摘要。
        """
        service = self._load_service()
        flow_spec = next(
            (f for f in self._load_flows() if f.command == flow_command),
            None,
        )
        if flow_spec is None:
            raise ValueError(
                f"Flow {flow_command!r} not found in spec dir {self.spec_dir}"
            )
        service_ctx = build_service_context(
            self.spec_dir,
            service,
            base_url_override=self.server_override,
        )
        # 与 execute_command 一致的 jail 语义：stdio 同机免检，HTTP 强制 jail。
        # isinstance 门卫：单元测试的 build_service_context 双件可返回裸 object，
        # 此时 run_flow 同样是双件，直接跳过 stamping（真链路恒为 ServiceContext）。
        if isinstance(service_ctx, ServiceContext):
            service_ctx.server_mode = self.transport != "stdio"
            service_ctx.upload_dir = self.upload_dir
            service_ctx.allow_dirs = self.file_allow_dirs

        step_results: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {}

        def _cb(name: str, payload: dict[str, Any]) -> None:
            if name == "step_done":
                step_results.append(
                    {
                        "id": payload.get("id"),
                        "label": payload.get("label"),
                        "status": payload.get("status"),
                        "result_preview": payload.get("result_preview", ""),
                    }
                )
            elif name == "flow_end":
                outcome["outcome"] = payload.get("outcome")

        run_flow(flow_spec, params or {}, service_ctx, service, step_cb=_cb,
                 spec_dir=self.spec_dir)
        return redact_sensitive(
            {
                "outcome": outcome.get("outcome", "completed"),
                "step_count": len(step_results),
                "steps": step_results,
            }
        )

    def execute_plugin_command(
        self, spec: ToolSpec, arguments: dict[str, Any]
    ) -> str:
        """执行一个命令级插件工具（``cmd.<command>``），返回捕获的文本输出。

        复用 ``build_plugin_tool_specs`` 的挂载方式：构建临时 click.Group +
        ServiceContext，调用全部命令插件 builder，再用 click 的
        ``Command.main(standalone_mode=False)`` 执行目标命令，stdout/stderr
        一并捕获为文本返回（rich console / click.echo 输出均在其中）。

        命令内 ``sys.exit(0)``（正常退出）被吞掉；参数解析错误
        （``click.UsageError``）与执行异常以文本形式返回，由上层标记 is_error。
        """
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import click

        from cliyard.engine.errors import CliyError
        from cliyard.engine.loader import load_service
        from cliyard.server.context import build_service_context
        from cliyard.server.mcp.tools import _build_plugin_cli

        service = self._load_service()
        service_ctx = build_service_context(
            self.spec_dir,
            service,
            base_url_override=self.server_override,
        )
        cli = _build_plugin_cli(self.spec_dir, service_ctx)

        # 从 cmd.<group>.<sub> 逐级解析目标 click.Command
        parts = spec.target.removeprefix("cmd.").split(".")
        ctx = click.Context(cli)
        cmd = cli
        for part in parts:
            if isinstance(cmd, click.Group):
                cmd = cmd.get_command(ctx, part)
            else:
                break
        if cmd is None:
            raise ValueError(f"Command plugin {spec.target!r} not found")

        # MCP arguments → click CLI 参数（长选项名对齐 schema 属性名）
        args: list[str] = []
        for param in getattr(cmd, "params", []) or []:
            if isinstance(param, click.Argument):
                value = (arguments or {}).get(param.name)
                if value is None:
                    continue
                if param.nargs == -1:
                    values = value if isinstance(value, list) else [value]
                    args.extend(str(v) for v in values)
                else:
                    args.append(str(value))
            elif isinstance(param, click.Option):
                prop_name = ""
                for opt in param.opts or []:
                    if opt.startswith("--"):
                        prop_name = opt[2:].replace("-", "_")
                        break
                if not prop_name:
                    prop_name = param.name or ""
                value = (arguments or {}).get(prop_name)
                if value is None:
                    continue
                long_opt = next((o for o in param.opts if o.startswith("--")), param.opts[-1])
                if param.is_flag:
                    if value:
                        args.append(long_opt)
                    continue
                if param.multiple or param.nargs == -1:
                    values = value if isinstance(value, list) else [value]
                    for v in values:
                        args.extend([long_opt, str(v)])
                else:
                    args.extend([long_opt, str(value)])

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                try:
                    cmd.main(
                        args,
                        prog_name=f"cliyard {spec.target}",
                        standalone_mode=False,
                    )
                except SystemExit as e:
                    code = e.code
                    if code is None or code == 0:
                        pass  # 命令内 sys.exit(0)/sys.exit(None) — 正常退出
                    else:
                        raise CliyError(
                            f"Plugin command '{spec.target}' exited with code {code!r}"
                        )
                except click.exceptions.Exit:
                    pass  # 命令内 sys.exit(0) — 正常退出
        except click.UsageError:
            raise  # 参数错误由 call_tool 统一转 is_error（与 execute_command 一致）

        out = out_buf.getvalue().strip()
        err = err_buf.getvalue().strip()
        return command_envelope(out, err, getattr(spec, "output_kind", "json"))

    def execute_case(self, case_name: str, params: dict[str, Any]) -> Any:
        """Execute a case and return aggregated results."""
        service = self._load_service()
        from cliyard.engine.case_runner import run_case

        case_spec = next(
            (c for c in load_cases(self.spec_dir) if c.name == case_name),
            None,
        )
        if case_spec is None:
            raise ValueError(
                f"Case '{case_name}' not found in spec dir {self.spec_dir}"
            )
        service_ctx = build_service_context(
            self.spec_dir,
            service,
            base_url_override=self.server_override,
        )
        # 与 execute_flow 一致的 jail 语义：stdio 同机免检，HTTP 强制 jail。
        # 否则 case 内 flow 的 file 参数会绕过 path jail（params 可由调用方覆盖）。
        if isinstance(service_ctx, ServiceContext):
            service_ctx.server_mode = self.transport != "stdio"
            service_ctx.upload_dir = self.upload_dir
            service_ctx.allow_dirs = self.file_allow_dirs
        result = run_case(
            case_spec, self.spec_dir, service_ctx, service, params_override=params
        )
        return redact_sensitive(result)

    def execute_case_list(self) -> Any:
        """Return metadata for all available test cases."""
        cases = load_cases(self.spec_dir)
        return [
            {
                "name": c.name,
                "description": c.description,
                "flow": c.flow,
                "labels": c.labels,
                "assert_count": len(c.assert_),
                "has_data": bool(c.data),
            }
            for c in cases
        ]

    def execute_spec(self, spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        """按 ToolSpec 分派执行，并向命令实现暴露本次原始 MCP arguments。

        The context is set once for the outermost call and left untouched for
        recursive grouped dispatch, so command plugins always see the complete
        original arguments (including non-schema fields).
        """
        token = None
        if raw_tool_arguments.get() is None:
            token = raw_tool_arguments.set(dict(arguments or {}))
        try:
            return self._execute_spec(spec, arguments)
        finally:
            if token is not None:
                raw_tool_arguments.reset(token)

    def _execute_spec(self, spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        """按 ToolSpec 分派执行（command / flow / plugin / case / grouped / discover）。"""
        if spec.kind == "discover_search":
            return self._discover_search(arguments)
        if spec.kind == "discover_describe":
            return self._discover_describe(arguments)
        if spec.kind == "discover_call":
            return self._discover_call(arguments)
        if spec.kind == "discover_catalog":
            return self._discover_catalog(arguments)
        if spec.kind == "case":
            return self.execute_case(spec.target, arguments)
        if spec.kind == "case_list":
            return self.execute_case_list()
        if spec.kind == "flow":
            return self.execute_flow(spec.target, arguments)
        if spec.kind == "plugin":
            return self.execute_plugin_command(spec, arguments)
        if spec.kind == "grouped":
            args = dict(arguments or {})
            # Deliberate edge：名为 `operation` 的业务参数无法穿透分组工具——
            # `operation` 在此被剥离为操作选择器（union schema 构造时同名业务
            # 参数已被丢弃，见 tools.build_union_schema）；不做绕行处理。
            op = args.pop("operation", None)
            ops = spec.operations or {}
            legal = sorted(ops)
            if op is None:
                raise ValueError(
                    f"Tool {spec.name!r} requires 'operation'; "
                    f"usage: {spec.name} operation=<{'|'.join(legal)}> ...; "
                    f"available operations: {', '.join(legal)}"
                )
            original = ops.get(str(op))
            if original is None:
                raise ValueError(
                    f"Unknown operation {str(op)!r} for tool {spec.name!r}; "
                    f"available operations: {', '.join(legal)}"
                )
            return self.execute_spec(original, args)
        return self.execute_command(spec.target, arguments)

    def _discover_search(self, arguments: dict[str, Any]) -> Any:
        query = str((arguments or {}).get("query") or "")
        try:
            limit = int((arguments or {}).get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        results = search_catalog(self._catalog, query, limit)
        return {"query": query, "count": len(results), "results": results}

    def _discover_describe(self, arguments: dict[str, Any]) -> Any:
        name = str((arguments or {}).get("name") or "")
        spec = self._catalog.get(name)
        if spec is None:
            raise ValueError(
                f"Unknown tool {name!r}; use 'search' or 'catalog' to find names"
            )
        tool = spec.as_tool()
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def _discover_call(self, arguments: dict[str, Any]) -> Any:
        args = dict(arguments or {})
        name = str(args.pop("name", "") or "")
        call_args = args.get("arguments") or {}
        if not isinstance(call_args, dict):
            raise ValueError("'arguments' must be an object")
        spec = self._catalog.get(name)
        if spec is None:
            raise ValueError(
                f"Unknown tool {name!r}; use 'search' or 'catalog' to find names"
            )
        return self.execute_spec(spec, call_args)

    def _discover_catalog(self, arguments: dict[str, Any]) -> Any:
        needle = str((arguments or {}).get("query") or "").lower()
        tools = [
            {"name": name, "description": short_tool_desc(spec.description)}
            for name, spec in self._catalog.items()
            if not needle or needle in name.lower()
        ]
        return {"count": len(tools), "tools": tools}

    # ------------------------------------------------------------------
    # MCP 回调（async；同步内核在线程池中执行）
    # ------------------------------------------------------------------

    async def list_tools(
        self, ctx: Any, params: Any
    ) -> ListToolsResult:
        """``tools/list``：返回当前 spec 全部已注册工具。"""
        return ListToolsResult(
            tools=[spec.as_tool() for spec in self._tool_specs.values()]
        )

    async def call_tool(
        self, ctx: Any, params: Any
    ) -> CallToolResult:
        """``tools/call``：查找工具并在线程池中执行，返回脱敏结果。

        未知工具名抛 ``ValueError`` → MCP 协议层错误；执行失败（校验 / 网络 /
        上游错误）返回 ``is_error=True`` 的 CallToolResult，让 LLM 看到可修正
        的错误信息。
        """
        spec = self._tool_specs.get(params.name)
        if spec is None:
            raise ValueError(
                f"Unknown tool {params.name!r}; "
                f"available: {sorted(self._tool_specs)[:10]}"
            )
        arguments: dict[str, Any] = params.arguments or {}
        try:
            result = await anyio.to_thread.run_sync(self.execute_spec, spec, arguments)
        except Exception as exc:
            logger.exception("MCP tool %s failed", params.name)
            # 双根脱敏：先 <spec_dir> 再 <upload_dir>（_sanitize_error 只认
            # spec 根；任一真值均不出镜，占位符除外；用户文案逐字节保留）。
            text = redact_upload_path(
                _sanitize_error(str(exc), self.spec_dir), self.upload_dir
            )
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "error", "items": [], "total": 0, "warn": text},
                        ensure_ascii=False,
                    ),
                )],
                is_error=True,
            )
        # Discover-mode ``call`` wraps a catalog target: use THAT target's output
        # metadata for projection (the wrapper itself has none).
        output_spec = getattr(spec, "output", None)
        if isinstance(arguments, dict):
            target_name = arguments.get("name")
            if target_name and target_name in getattr(self, "_catalog", {}):
                output_spec = getattr(self._catalog[target_name], "output", output_spec)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=self._limit_result(result, arguments, output_spec),
                )
            ]
        )

    def _limit_result(
        self,
        result: Any,
        arguments: dict[str, Any] | None = None,
        output_spec: dict[str, Any] | None = None,
    ) -> str:
        """Unified projection + output size guard (see module docstring)."""
        from cliyard.output import projection

        if isinstance(result, dict) and isinstance(result.get("items"), list):
            if "status" not in result:
                result = dict(result)
                result["status"] = "ok" if result["items"] else "empty"
            if not isinstance(result.get("total"), int):
                result["total"] = len(result["items"])

        text = _render_result(result)
        try:
            from cliyard.output.limits import (
                infer_schema,
                resolve_max_chars,
                truncate_text,
            )

            limit = resolve_max_chars()
            params = arguments or {}
            if isinstance(params.get("arguments"), dict) and "name" in params:
                params = params["arguments"]

            spec = output_spec if isinstance(output_spec, dict) else {}
            enabled = bool(spec.get("items_path")) \
                and spec.get("unified_projection", True) is not False \
                and spec.get("projection", True) is not False
            items = result.get("items") if isinstance(result, dict) else None
            projectable = enabled and isinstance(items, list)
            pspec = _projection_spec(spec)
            fields = projection.parse_fields(params.get(pspec["fields_param"]))
            brief = projection._truthy(params.get(pspec["brief_param"]))

            if projectable and (fields or brief):
                projected = _project_items(result, pspec, params)
                if projected is not None:
                    result = projected
                    text = _render_result(result)

            if len(text) <= limit:
                return text

            file_path: str | None = None
            url: str | None = None
            overflow_error: str | None = None
            try:
                from cliyard.server.uploads import save_output_text, sweep

                sweep(self.upload_dir)
                saved = save_output_text(text, self.upload_dir)
                if self.output_base:
                    from cliyard.server.api.output_tokens import output_url

                    url = output_url(self.output_base, saved["file_name"])
                else:
                    file_path = saved.get("path")
            except Exception as exc:
                overflow_error = f"{type(exc).__name__}: {exc}"
                logger.warning("output overflow handoff failed", exc_info=True)

            auth_hint = (
                {"header": "Authorization: Bearer <KETACLI_MCP_TOKEN>",
                 "env": "KETACLI_MCP_TOKEN"}
                if url else None
            )
            schema = infer_schema(result)
            item_count = len(items) if isinstance(items, list) else None

            if projectable:
                base_meta: dict[str, Any] = {
                    "truncated": True,
                    "projected": False,
                    "reason": "output_truncated",
                    "bytes": len(text.encode("utf-8")),
                    "original_bytes": len(text.encode("utf-8")),
                    "limit": limit,
                    "items": item_count or 0,
                    "schema": schema,
                }
                if url:
                    base_meta["url"] = url
                if file_path:
                    base_meta["file"] = file_path
                if auth_hint:
                    base_meta["auth"] = auth_hint
                if overflow_error:
                    base_meta["overflow_error"] = overflow_error

                projected_result = None
                projection_error = None
                if items:
                    if all(not isinstance(item, dict) for item in items):
                        base_meta["projection_error"] = "scalar_list_cannot_project"
                        base_meta["hint"] = "输出超过单次上限，items 为前 N 条；完整 JSON 见 url"
                    else:
                        projected_result = _project_items(
                            result, pspec, {pspec["brief_param"]: True}
                        )
                        if projected_result is None:
                            projection_error = "all_items_are_nested_objects"
                if projected_result is not None:
                    result = projected_result
                    base_meta["projected"] = True
                    base_meta["projection"] = result.get("projection")
                    base_meta["hint"] = (
                        "输出超过单次上限，已自动精简字段；items 为精简后的数据，"
                        "完整 JSON 见 url；也可以用 fields=... / brief=true 自行指定字段"
                    )
                if projection_error:
                    base_meta["projection_error"] = projection_error
                if "hint" not in base_meta:
                    base_meta["hint"] = "输出超过单次上限，items 为前 N 条；完整 JSON 见 url，或减小 page_size"
                payload, returned = projection.fit_items(result, limit, base_meta=base_meta)
                payload["returned"] = returned
                return json.dumps(payload, ensure_ascii=False, indent=2)

            hint = "输出超过单次上限：请减小 page_size，或按 url 下载完整 JSON 后自行筛选"
            if item_count is not None:
                hint += f"；本次 items={item_count}"
            rendered, notice = truncate_text(
                text, limit, file=file_path, url=url, json_safe=True,
                auth=auth_hint, schema=schema, hint=hint,
            )
            if notice is not None:
                if item_count is not None:
                    notice["items"] = item_count
                if overflow_error:
                    notice["overflow_error"] = overflow_error
                rendered = json.dumps(notice, ensure_ascii=False, indent=2)
            return rendered
        except Exception:
            return text

    # ------------------------------------------------------------------
    # 低层 MCP Server 构造
    # ------------------------------------------------------------------

    def to_mcp_server(self, name: str | None = None, version: str = "0.12.1") -> MCPServer:
        """构建绑定本 executor 的低层 MCP Server。

        Args:
            name: serverInfo 名称；缺省取 spec 的 ``name`` 字段。
            version: serverInfo 版本。
        """
        service = self._load_service()
        server_name = name or service.get("name") or Path(self.spec_dir).name
        return MCPServer(
            name=server_name,
            version=version,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )
