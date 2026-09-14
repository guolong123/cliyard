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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
from mcp.types import Tool

from cliyard.server.schema_bridge import _file_upload_guide, build_command_tree

logger = logging.getLogger("cliyard.server.mcp")

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


@dataclass(frozen=True)
class ToolSpec:
    """单条 MCP 工具的元数据。

    Attributes:
        name: MCP tool name（= /api/execute target，命令场景二者一致）。
        kind: ``"command"``、``"flow"``、``"plugin"`` 或 ``"grouped"``。
        target: 传给执行内核的 target（resource.method 或 flow command；
            grouped 场景为资源级工具名，不可直接执行，按 operation 转交）。
        description: 面向 LLM 的工具说明（method 描述 + HTTP 方法/路径 + 资源描述）。
        input_schema: ``params_to_json_schema`` / ``build_flow_schema`` 的 JSON Schema。
        operations: grouped 工具的载体（``{operation -> 原 ToolSpec}``，原
            ToolSpec 的 name 沿用扁平恒等式 ``f"{tool}.{op}"``）；非分组
            工具恒为 ``None``。
    """

    name: str
    kind: str
    target: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(_EMPTY_SCHEMA))
    operations: dict[str, ToolSpec] | None = None

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


def _short_op_desc(cmd: dict[str, Any]) -> str:
    """取单个 method 的短描述（描述首句；缺省回退方法名）。

    供分组工具 ``operation`` 枚举的逐操作描述用。
    """
    name = str(cmd.get("name") or "")
    desc = str(cmd.get("desc") or "").strip()
    if not desc:
        return name
    first = re.split(r"[。\n.!?！？；;]+", desc)[0].strip()
    return first or name


def build_union_schema(
    resource_name: str, resource_desc: str, commands: list[dict]
) -> dict[str, Any]:
    """把同一资源下各操作的 schema 合并为分组工具的 union schema。

    合并规则（todo 2 定稿）：

    * 全量合并各操作的 ``properties``（keep-first：首个操作的中标定义胜出）。
    * 碰撞标注：同名字段在不同操作中含义不同（schema 不相等）时，中标定义的
      ``description`` 追加 ``（X 取自 opA；opB 的 X 被遮蔽，用 opB 时缺该字段走
      运行时报错）``（多遮蔽操作各一句，以 ``；`` 连接）；定义完全相等的同名字段
      视为同一含义，不标注。标注只进 ``description``，不引入任何 ``x-`` 扩展。
    * ``required`` 恒为 ``["operation"]``（各操作自有必填由运行时 binder 收紧）。
    * 顶层 ``description`` = 资源描述 + ``操作：`` 清单（一操作一行
      ``<op> - <短描述>``）；逐操作文档只放 description（stdio ``tools/list``
      实 dump 可达）。
    * 文件模板 interplay：各操作 schema 里的 file 字段描述已由
      ``schema_bridge`` 按 ``upload_base`` / ``transport`` 渲染好 curl 指引，
      合并时原文保留（不重写、不重实现模板函数）；兜底补齐见
      :func:`_grouped_resource_spec`。

    函数名与签名保持稳定：

    ``build_union_schema(resource_name, resource_desc, commands) -> dict``

    Args:
        resource_name: 资源名（消歧场景传短名；工具名另行组装）。
        resource_desc: 资源描述（进顶层 description）。
        commands: 命令树中该资源的原始 command 条目列表（每个含
            ``name`` / ``desc`` / ``schema``）。

    Returns:
        分组工具的 ``input_schema``（含 ``operation`` 枚举 + 合并参数）。
    """
    op_names: list[str] = [str(cmd.get("name") or "") for cmd in commands]
    op_lines: list[str] = [f"{cmd.get('name')} - {_short_op_desc(cmd)}" for cmd in commands]
    op_block = "\n".join(op_lines)
    properties: dict[str, Any] = {
        "operation": {
            "type": "string",
            "enum": op_names,
            "description": (
                "选择要执行的操作（operation），合法值：\n" + op_block
                if op_block
                else "选择要执行的操作（operation）"
            ),
        }
    }
    winner_op: dict[str, str] = {}
    winner_prop: dict[str, Any] = {}
    shadowed: dict[str, list[str]] = {}
    for cmd in commands:
        op = str(cmd.get("name") or "")
        schema = cmd.get("schema") or {}
        for key, value in (schema.get("properties") or {}).items():
            if key not in properties:
                properties[key] = dict(value) if isinstance(value, dict) else value
                winner_op[key] = op
                winner_prop[key] = value
            elif key == "operation":
                continue  # 与操作选择器同名的业务参数直接丢弃（极端边界，见 learnings）
            elif winner_prop[key] != value:
                if op not in shadowed.setdefault(key, []):
                    shadowed[key].append(op)
    for key, ops in shadowed.items():
        first = winner_op.get(key, "")
        clauses = "；".join(
            f"{op} 的 {key} 被遮蔽，用 {op} 时缺该字段走运行时报错" for op in ops
        )
        annotation = f"（{key} 取自 {first}；{clauses}）"
        prop = properties[key]
        if isinstance(prop, dict):
            existing = prop.get("description") or ""
            prop["description"] = f"{existing}\n{annotation}" if existing else annotation
    base = resource_desc or resource_name
    description = f"{base}\n操作：\n{op_block}" if op_block else base
    return {
        "type": "object",
        "properties": properties,
        "required": ["operation"],
        "description": description,
    }


def _union_file_fields(commands: list[dict]) -> list[str]:
    """收集各操作 schema 中 file 类型（含 multiple 数组）的参数名（按出现序去重）。"""
    names: list[str] = []
    for cmd in commands:
        schema = cmd.get("schema") or {}
        for key, value in (schema.get("properties") or {}).items():
            if not isinstance(value, dict):
                continue
            items = value.get("items")
            is_file = value.get("format") == "binary" or (
                isinstance(items, dict) and items.get("format") == "binary"
            )
            if is_file and key not in names:
                names.append(key)
    return names


def _has_upload_guide(description: str) -> bool:
    """curl 指引三要素是否齐全（/upload 地址 + $TOKEN 占位 + 本地路径警告）。"""
    return (
        "/upload" in description
        and "$TOKEN" in description
        and "不要直接填你机器的本地路径" in description
    )


def _grouped_resource_spec(
    tool_name: str,
    resource_name: str,
    resource_desc: str,
    commands: list[dict],
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> ToolSpec:
    """把同一资源的全部 command 条目合并为一条分组 ToolSpec。

    file 兜底：中标定义本身是 file 类型却缺 curl 指引三要素时（正常命令树不
    会发生——指引已由 schema_bridge 按本组 ``upload_base`` / ``transport``
    渲染；仅手工构造的 commands 触发），用 :func:`_file_upload_guide` 按原
    组合补齐。被碰撞遮蔽的 file 定义不补（标注已指明遮蔽，运行时走报错）。

    ``operations`` 载体：每个操作一条原 ToolSpec（name 沿用扁平恒等式
    ``f"{tool}.{op}"``，与 ``mode="flat"`` 表同名），供 executor 的 grouped
    分支按 ``operation`` 转交原路执行。
    """
    union = build_union_schema(resource_name, resource_desc, commands)
    props = union.get("properties") or {}
    for fname in _union_file_fields(commands):
        prop = props.get(fname)
        if not isinstance(prop, dict):
            continue
        items = prop.get("items")
        kept_is_file = prop.get("format") == "binary" or (
            isinstance(items, dict) and items.get("format") == "binary"
        )
        if not kept_is_file:
            continue
        if _has_upload_guide(prop.get("description") or ""):
            continue
        guide = _file_upload_guide(upload_base, transport)
        existing = prop.get("description") or ""
        prop["description"] = f"{existing}\n{guide}" if existing else guide
    operations: dict[str, ToolSpec] = {}
    for cmd in commands:
        op = str(cmd.get("name") or "")
        if not op:
            continue
        operations[op] = _command_spec(f"{tool_name}.{op}", cmd, resource_desc, "")
    return ToolSpec(
        name=tool_name,
        kind="grouped",
        target=tool_name,  # 资源级 target；todo 4 按 operation 转交原 ToolSpec
        description=str(union.get("description") or tool_name),
        input_schema=union,
        operations=operations,
    )


def _register_flat_commands(
    tree: dict[str, Any], specs: dict[str, ToolSpec]
) -> None:
    """扁平装配（与改动前逐行一致；``mode="flat"`` 输出逐 key 不变）。"""
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


def _register_grouped_commands(
    tree: dict[str, Any],
    specs: dict[str, ToolSpec],
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> None:
    """分组装配：每资源一条 ToolSpec（消歧规则与扁平一致）。

    * 唯一资源名 → 工具名 = 资源名（如 ``alpha``）。
    * 跨组重名 → 工具名 = ``group.resource``（如 ``g1.token``），与扁平
      ``group.resource.method`` 的三段消歧同规则（``tool.operation`` 拼出
      扁平名）。
    * 零 method 资源直接跳过（不断言崩溃；todo 2 钉死语义，此处先行兼容）。
    """
    groups = tree.get("groups") or []
    duplicate_names = _duplicate_resource_names(groups)
    for group in groups:
        gname: str = group.get("group") or ""
        grouped_resources = group.get("resources") or []
        if grouped_resources:
            for resource in grouped_resources:
                rname: str = resource.get("name") or ""
                rdesc: str = resource.get("desc") or rname
                commands = resource.get("commands") or []
                if not commands:
                    continue
                if rname in duplicate_names:
                    tool_name = f"{gname}.{rname}"
                else:
                    tool_name = rname
                _register(
                    specs,
                    tool_name,
                    _grouped_resource_spec(
                        tool_name, rname, rdesc, commands,
                        upload_base=upload_base, transport=transport,
                    ),
                )
        else:
            # 扁平资源（无 group 字段）：group name == 资源 name
            commands = group.get("commands") or []
            if not commands:
                continue
            tool_name = gname
            _register(
                specs,
                tool_name,
                _grouped_resource_spec(
                    tool_name, gname, group.get("desc") or "", commands,
                    upload_base=upload_base, transport=transport,
                ),
            )


def build_tool_specs(
    spec_dir: str | Path,
    *,
    upload_base: str | None = None,
    transport: str = "http",
    mode: str = "flat",
) -> dict[str, ToolSpec]:
    """把 spec 命令树 / flow 树映射为 MCP 工具表（``name -> ToolSpec``）。

    Args:
        spec_dir: cliyard spec 目录。
        upload_base: 对外 ``POST /upload`` 基地址（透传给 file 参数描述
            模板；缺省 ``None`` → 占位地址 + 一句配置提示）。
        transport: ``"http"`` 或 ``"stdio"``（透传给 file 参数描述模板；
            ``"stdio"`` 追加同机 ``file_path`` 段）。
        mode: ``"flat"``（缺省，与改动前逐 key 一致，每 method 一工具）或
            ``"grouped"``（每 resource 一工具，``operation`` 枚举选方法；
            flow 透传，``cmd.*`` 按顶层命名空间分组）。

    Returns:
        ``{tool_name: ToolSpec}``，工具名与 /api/execute target 对齐。

    Raises:
        FileNotFoundError: spec_dir 缺 _auth.yaml 时由 build_command_tree 抛出。
        ValueError: ``mode`` 非 ``"flat"`` / ``"grouped"``。
    """
    if mode not in ("flat", "grouped"):
        raise ValueError(
            f"unknown MCP tool mode {mode!r} (expected 'flat' or 'grouped')"
        )
    tree = build_command_tree(spec_dir, upload_base=upload_base, transport=transport)
    specs: dict[str, ToolSpec] = {}

    if mode == "flat":
        _register_flat_commands(tree, specs)
    else:
        _register_grouped_commands(
            tree, specs, upload_base=upload_base, transport=transport
        )

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

    # 命令级插件（@register_command）→ cmd.<command> 工具（命名空间隔离；
    # grouped 模式按顶层命名空间合并为 cmd.<ns> 分组工具）
    specs.update(build_plugin_tool_specs(spec_dir, mode=mode))

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


def _group_plugin_tool_specs(
    flat_specs: dict[str, ToolSpec],
) -> dict[str, ToolSpec]:
    """把扁平 ``cmd.*`` 表按顶层命名空间合并为分组工具（``mode="grouped"`` 用）。

    * ``cmd.pkg.info`` + ``cmd.pkg.search`` → ``cmd.pkg``（``operation`` 为
      剩余点分路径 ``info`` / ``search`` 的枚举，释义逐操作）。
    * 单件命名空间（``cmd.hello``）保持单工具，``operation`` 枚举单值（取自身
      短名，直通原 ToolSpec）。
    * 输入已由 ``_walk_plugin_commands`` 按 hidden-skip 语义过滤——隐藏命令不
      进表，此处不做二次判断。
    * union schema 复用 :func:`build_union_schema`（碰撞标注语义同样成立：被
      遮蔽字段走选中 op 原 spec 的 click 解析，类型不符报 UsageError）；与
      ``operation`` 选择器同名的业务参数同样被丢弃（todo 4 钉死的 deliberate
      edge，分发走同一 ``grouped`` 分支，无需重复处理）。
    """
    buckets: dict[str, list[tuple[str, ToolSpec]]] = {}
    for name, spec in flat_specs.items():
        rest = name.removeprefix("cmd.")
        ns, sep, sub = rest.partition(".")
        op = sub if sep else rest
        buckets.setdefault(ns, []).append((op, spec))
    grouped: dict[str, ToolSpec] = {}
    for ns, members in buckets.items():
        operations = {op: spec for op, spec in members}
        union = build_union_schema(
            f"cmd.{ns}",
            f"命令插件命名空间 cmd.{ns}",
            [
                {"name": op, "desc": spec.description, "schema": spec.input_schema}
                for op, spec in members
            ],
        )
        tool_name = f"cmd.{ns}"
        _register(
            grouped,
            tool_name,
            ToolSpec(
                name=tool_name,
                kind="grouped",
                target=tool_name,  # 命名空间级 target；按 operation 转交原 ToolSpec
                description=str(union.get("description") or tool_name),
                input_schema=union,
                operations=operations,
            ),
        )
    return grouped


def build_plugin_tool_specs(
    spec_dir: str | Path, base_ctx: Any = None, mode: str = "flat"
) -> dict[str, ToolSpec]:
    """把命令级插件（``@register_command``）映射为 MCP 工具表。

    Args:
        spec_dir: cliyard spec 目录（插件从 ``{spec_dir}/plugins/*.py`` 发现）。
        base_ctx: ServiceContext；缺省时按 spec 服务配置构建（与 executor
            一致，避免插件 builder 内依赖 ``ctx.base_url`` 等字段）。
        mode: ``"flat"``（缺省，与改动前逐 key 一致）或 ``"grouped"``（按顶层
            命名空间合并为 ``cmd.<ns>`` 分组工具，``operation`` 为剩余点分路径）。

    Returns:
        ``{tool_name: ToolSpec}``，工具名统一 ``cmd.`` 前缀。

    Raises:
        ValueError: ``mode`` 非 ``"flat"`` / ``"grouped"``。
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
    if mode == "flat":
        return specs
    if mode != "grouped":
        raise ValueError(
            f"unknown MCP tool mode {mode!r} (expected 'flat' or 'grouped')"
        )
    return _group_plugin_tool_specs(specs)
