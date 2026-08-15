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

import json
import logging
from pathlib import Path
from typing import Any

import anyio
from mcp.server.lowlevel import Server as MCPServer
from mcp.types import CallToolResult, ListToolsResult, TextContent

from cliyard.engine.builder import execute_pipeline
from cliyard.engine.loader import load_flows, load_service
from cliyard.engine.orchestrator import _lookup_resource_method, run_flow
from cliyard.server.context import build_service_context
from cliyard.server.executor import _sanitize_error, execution_manager
from cliyard.server.redact import redact_sensitive

from cliyard.server.mcp.tools import ToolSpec, build_plugin_tool_specs, build_tool_specs

logger = logging.getLogger("cliyard.server.mcp")


def _render_result(result: Any) -> str:
    """把结构化结果渲染为 MCP 文本（dict/list → 缩进 JSON，其余 str()）。"""
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass
    return str(result)


class MCPExecutor:
    """基于 spec 的 MCP 工具执行器。

    Args:
        spec_dir: cliyard spec 目录。
        server_override: 可选的 base_url 运行时覆盖（同 CLI ``--server``，作为
            ``base_url_override`` 传入 ``build_service_context``）。
    """

    def __init__(
        self,
        spec_dir: str | Path,
        server_override: str | None = None,
    ) -> None:
        self.spec_dir: str = str(Path(spec_dir).resolve())
        self.server_override = server_override
        self._tool_specs: dict[str, ToolSpec] = build_tool_specs(self.spec_dir)
        # 命令级插件（@register_command）→ cmd.<command> 工具
        self._tool_specs.update(build_plugin_tool_specs(self.spec_dir))
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

        run_flow(flow_spec, params or {}, service_ctx, service, step_cb=_cb)
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
                except click.exceptions.Exit:
                    pass  # 命令内 sys.exit(0) — 正常退出
        except click.UsageError:
            raise  # 参数错误由 call_tool 统一转 is_error（与 execute_command 一致）

        out = out_buf.getvalue().strip()
        err = err_buf.getvalue().strip()
        if err and out:
            return f"{out}\n[stderr] {err}"
        return out or err or f"(no output from {spec.target})"

    def execute_spec(self, spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        """按 ToolSpec 分派执行（command / flow / plugin）。"""
        if spec.kind == "flow":
            return self.execute_flow(spec.target, arguments)
        if spec.kind == "plugin":
            return self.execute_plugin_command(spec, arguments)
        return self.execute_command(spec.target, arguments)

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
            return CallToolResult(
                content=[TextContent(type="text", text=_sanitize_error(str(exc), self.spec_dir))],
                is_error=True,
            )
        return CallToolResult(content=[TextContent(type="text", text=_render_result(result))])

    # ------------------------------------------------------------------
    # 低层 MCP Server 构造
    # ------------------------------------------------------------------

    def to_mcp_server(self, name: str | None = None, version: str = "0.12.0") -> MCPServer:
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
