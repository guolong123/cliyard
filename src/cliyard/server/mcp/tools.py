"""命令树 / flow 树 → MCP tools 注册（复用 schema_bridge）。

把 :func:`cliyard.server.schema_bridge.build_command_tree` 产出的命令树 /
flow 树映射为 MCP 工具表，工具命名与 ``/api/execute`` 的 target 对齐，保证
``CLI / serve / MCP`` 三端同一命令标识：

* 无 group 资源：``<resource>.<method>``（如 ``user.list``）
* 有 group 资源：``<resource>.<method>``（group 前缀不入工具名——执行内核
  ``_lookup_resource_method`` 仅解析 ``resource.method``；资源名全局唯一）
* flow：``flow.<command>``（``-`` 转 ``_``，如 ``flow.add_user``；target 保持
  ``_flows.yaml`` 的原始 command ``add-user``）
* 命令级插件：``cmd.<command>``（如 ``cmd.search``）或 ``cmd.<group>.<sub>``
  （如 ``cmd.skills.list``）——顶层命令插件的子命令层级以点号展平，与资源 /
  flow 命名空间隔离，避免与 ``resource.method`` 冲突

``inputSchema`` 直接复用 ``params_to_json_schema`` / ``build_flow_schema`` 的产物，
保证参数模型与 serve /api/spec 完全一致（required / enum / multiple→array /
file→format:binary / json|object→object）；命令级插件则从 click 命令的
``params``（argument / option）提取，映射规则与 schema_bridge 对齐。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
from mcp.types import Tool

from cliyard.server.schema_bridge import build_command_tree

logger = logging.getLogger("cliyard.server.mcp")

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


@dataclass(frozen=True)
class ToolSpec:
    """单条 MCP 工具的元数据。

    Attributes:
        name: MCP tool name（= /api/execute target，命令场景二者一致）。
        kind: ``"command"`` 或 ``"flow"``。
        target: 传给执行内核的 target（resource.method 或 flow command）。
        description: 面向 LLM 的工具说明（method 描述 + HTTP 方法/路径 + 资源描述）。
        input_schema: ``params_to_json_schema`` / ``build_flow_schema`` 的 JSON Schema。
    """

    name: str
    kind: str
    target: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(_EMPTY_SCHEMA))

    def as_tool(self) -> Tool:
        """转换为 MCP :class:`~mcp.types.Tool`（tools/list 返回用）。"""
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


def _command_spec(
    name: str, cmd: dict[str, Any], resource_desc: str, group_desc: str
) -> ToolSpec:
    """把命令树里的单个 command 条目转换为 ToolSpec。"""
    desc_parts: list[str] = []
    if cmd.get("desc"):
        desc_parts.append(str(cmd["desc"]))
    method = cmd.get("method")
    path = cmd.get("path")
    if method and path:
        desc_parts.append(f"HTTP {method} {path}")
    elif method:
        desc_parts.append(f"HTTP {method}")
    if resource_desc and resource_desc not in desc_parts:
        desc_parts.append(f"({resource_desc})")
    elif not resource_desc and group_desc and group_desc not in desc_parts:
        desc_parts.append(f"({group_desc})")
    description = " | ".join(p for p in desc_parts if p) or name

    return ToolSpec(
        name=name,
        kind="command",
        target=name,  # 命令场景 tool name == /api/execute target
        description=description,
        input_schema=cmd.get("schema") or dict(_EMPTY_SCHEMA),
    )


def build_tool_specs(spec_dir: str | Path) -> dict[str, ToolSpec]:
    """把 spec 命令树 / flow 树映射为 MCP 工具表（``name -> ToolSpec``）。

    Args:
        spec_dir: cliyard spec 目录。

    Returns:
        ``{tool_name: ToolSpec}``，工具名与 /api/execute target 对齐。

    Raises:
        FileNotFoundError: spec_dir 缺 _auth.yaml 时由 build_command_tree 抛出。
    """
    tree = build_command_tree(spec_dir)
    specs: dict[str, ToolSpec] = {}

    groups = tree.get("groups") or []
    duplicate_names = _duplicate_resource_names(groups)
    for group in groups:
        gname: str = group.get("group") or ""
        grouped_resources = group.get("resources") or []
        if grouped_resources:
            for resource in grouped_resources:
                rname: str = resource.get("name") or ""
                rdesc: str = resource.get("desc") or rname
                for cmd in resource.get("commands") or []:
                    if rname in duplicate_names:
                        name = f"{gname}.{rname}.{cmd.get('name')}"
                    else:
                        name = f"{rname}.{cmd.get('name')}"
                    _register(specs, name, _command_spec(name, cmd, rdesc, group.get("desc") or ""))
        else:
            # 扁平资源（无 group 字段）：group name == 资源 name
            for cmd in group.get("commands") or []:
                name = f"{gname}.{cmd.get('name')}"
                _register(specs, name, _command_spec(name, cmd, group.get("desc") or "", ""))

    for flow in tree.get("flows") or []:
        name = f"flow.{flow.get('name')}"
        spec = ToolSpec(
            name=name,
            kind="flow",
            target=flow.get("command") or flow.get("name") or name,
            description=flow.get("description") or flow.get("command") or name,
            input_schema=flow.get("params_schema") or dict(_EMPTY_SCHEMA),
        )
        _register(specs, name, spec)

    # 命令级插件（@register_command）→ cmd.<command> 工具（命名空间隔离）
    specs.update(build_plugin_tool_specs(spec_dir))

    return specs


def _duplicate_resource_names(groups: list[dict[str, Any]]) -> set[str]:
    """跨组重名的资源名集合（tool name 需 group 前缀消歧）。

    扁平资源（组名 == 资源名）视为独立名字，不参与消歧判断——它们天然
    通过 CLI 二级命令区分，且 tool name ``组名.方法名`` 不会与带 group 的
    三级资源冲突（前者无资源名段）。
    """
    seen: dict[str, set[str]] = {}
    for group in groups:
        for resource in group.get("resources") or []:
            rname: str = resource.get("name") or ""
            gname: str = group.get("group") or ""
            seen.setdefault(rname, set()).add(gname)
    return {name for name, groups_ in seen.items() if len(groups_) > 1}

    for flow in tree.get("flows") or []:
        name = f"flow.{flow.get('name')}"
        spec = ToolSpec(
            name=name,
            kind="flow",
            target=flow.get("command") or flow.get("name") or name,
            description=flow.get("description") or flow.get("command") or name,
            input_schema=flow.get("params_schema") or dict(_EMPTY_SCHEMA),
        )
        _register(specs, name, spec)

    return specs


def _register(specs: dict[str, ToolSpec], name: str, spec: ToolSpec) -> None:
    """注册工具；同名冲突时后者覆盖并记录警告（不应发生的边界场景）。"""
    if name in specs:
        logger.warning(
            "Duplicate MCP tool name %r (kind=%s); overwriting with %s",
            name,
            spec.kind,
            spec.target,
        )
    specs[name] = spec


# ---------------------------------------------------------------------------
# 命令级插件 → MCP tools
# ---------------------------------------------------------------------------


def _click_type_to_json_schema(param: Any) -> dict[str, Any]:
    """把 click 参数类型映射为 JSON Schema 单值描述。

    * ``is_flag`` → boolean
    * ``click.Choice`` → string + enum
    * ``INT``/``FLOAT`` → integer/number
    * ``BOOL``/``click.BoolParamType`` → boolean
    * ``Path`` → string（格式提示；MCP 侧传路径字符串）
    * 其余（str 等）→ string
    """
    if getattr(param, "is_flag", False):
        return {"type": "boolean"}
    ptype: Any = getattr(param, "type", None)
    if isinstance(ptype, click.Choice):
        return {"type": "string", "enum": list(ptype.choices)}
    if ptype is click.INT or isinstance(ptype, click.IntRange):
        return {"type": "integer"}
    if ptype is click.FLOAT or isinstance(ptype, click.FloatRange):
        return {"type": "number"}
    if ptype is click.BOOL:
        return {"type": "boolean"}
    if isinstance(ptype, click.Path):
        return {"type": "string", "description": "File path"}
    return {"type": "string"}


def _click_param_to_property(
    param: Any,
) -> tuple[str, dict[str, Any], bool] | None:
    """把单个 click.Parameter 映射为 ``(属性名, JSON Schema 属性, required)``。

    * ``click.Argument``：属性名为 ``param.name``（如 ``spl``），required 取
      ``param.required``；``nargs=-1``（多值）包装为 array。
    * ``click.Option``：属性名取长选项名去 ``--`` 转下划线（如 ``--limit`` →
      ``limit``；``--format`` 且 dest 为 ``format_`` → ``format``——与 CLI
      参数名一致，供 LLM 直观传参）；``multiple`` 包装为 array。
    * ``default`` / ``help`` 透传；``show_default`` 仅体现在 default。
    """
    name: str = ""
    if isinstance(param, click.Argument):
        name = param.name or ""
    elif isinstance(param, click.Option):
        # 长选项名（--xxx）优先；无长选项时回退 dest 名
        for opt in param.opts or []:
            if opt.startswith("--"):
                name = opt[2:].replace("-", "_")
                break
        if not name:
            name = param.name or ""
    if not name:
        return None

    prop = _click_type_to_json_schema(param)
    if param.multiple or (isinstance(param, click.Argument) and param.nargs == -1):
        prop = {"type": "array", "items": _click_type_to_json_schema(param)}
    default = param.default
    if default is not None and type(default).__name__ != "Sentinel":
        if not isinstance(default, (tuple, list, dict)):
            prop["default"] = default
    help_text = getattr(param, "help", None)
    if help_text:
        prop["description"] = help_text
    return name, prop, bool(getattr(param, "required", False))


def _click_command_to_schema(cmd: Any) -> dict[str, Any]:
    """从 click.Command 的 params 构建 JSON Schema（对齐 params_to_json_schema）。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in getattr(cmd, "params", []) or []:
        mapped = _click_param_to_property(param)
        if mapped is None:
            continue
        name, prop, is_required = mapped
        properties[name] = prop
        if is_required and name not in required:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _walk_plugin_commands(
    cmd: Any, prefix: str, specs: dict[str, ToolSpec]
) -> None:
    """递归遍历 click 命令（含 group 子命令），注册为 ``cmd.<...>`` 工具。

    命令插件的工具名统一加 ``cmd.`` 前缀，与资源工具（``resource.method``）
    和 flow 工具（``flow.<command>``）命名空间隔离，杜绝冲突。
    """
    base = f"cmd.{prefix}" if prefix else "cmd"
    if isinstance(cmd, click.Group):
        ctx = click.Context(cmd)
        for sub_name in cmd.list_commands(ctx):
            sub = cmd.get_command(ctx, sub_name)
            if sub is None or getattr(sub, "hidden", False):
                continue
            _walk_plugin_commands(sub, f"{prefix}.{sub_name}" if prefix else sub_name, specs)
        return

    # 叶子命令 → ToolSpec
    name = base
    description = cmd.get_short_help_str(80) or getattr(cmd, "help", None) or name
    spec = ToolSpec(
        name=name,
        kind="plugin",
        target=name,
        description=str(description),
        input_schema=_click_command_to_schema(cmd),
    )
    _register(specs, name, spec)


def _build_plugin_cli(
    spec_dir: str | Path, base_ctx: Any
) -> click.Group:
    """构建挂载了全部命令级插件的临时 click.Group（复用 runner 的挂载方式）。

    Args:
        spec_dir: cliyard spec 目录（用于 discover_plugins 加载插件）。
        base_ctx: ServiceContext（命令插件 builder 的 ``ctx`` 参数）。

    Returns:
        挂载了所有 ``@register_command`` 命令的临时 ``click.Group``。
    """
    from cliyard.plugin import PluginRegistry
    from cliyard.plugin.discovery import discover_plugins

    discover_plugins(str(spec_dir))
    cli = click.Group(name="plugins")
    for _cmd_name, _cmd_fn in PluginRegistry.get_all_commands().items():
        try:
            _cmd_fn(cli, base_ctx)
        except Exception:  # 单个插件挂载失败不应阻断其他工具
            logger.exception("command plugin %r failed to mount", _cmd_name)
    return cli


def build_plugin_tool_specs(
    spec_dir: str | Path, base_ctx: Any = None
) -> dict[str, ToolSpec]:
    """把命令级插件（``@register_command``）映射为 MCP 工具表。

    Args:
        spec_dir: cliyard spec 目录（插件从 ``{spec_dir}/plugins/*.py`` 发现）。
        base_ctx: ServiceContext；缺省时按 spec 服务配置构建（与 executor
            一致，避免插件 builder 内依赖 ``ctx.base_url`` 等字段）。

    Returns:
        ``{tool_name: ToolSpec}``，工具名统一 ``cmd.`` 前缀。
    """
    from cliyard.server.context import build_service_context

    service = None
    if base_ctx is None:
        from cliyard.engine.loader import load_service

        spec_str = str(spec_dir)
        service = load_service(spec_str)
        base_ctx = build_service_context(spec_str, service)

    cli = _build_plugin_cli(spec_dir, base_ctx)
    specs: dict[str, ToolSpec] = {}
    ctx = click.Context(cli)
    for name in cli.list_commands(ctx):
        cmd = cli.get_command(ctx, name)
        if cmd is None or getattr(cmd, "hidden", False):
            continue
        _walk_plugin_commands(cmd, name, specs)
    return specs
