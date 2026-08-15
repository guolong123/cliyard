"""命令树 / flow 树 → MCP tools 注册（复用 schema_bridge）。

把 :func:`cliyard.server.schema_bridge.build_command_tree` 产出的命令树 /
flow 树映射为 MCP 工具表，工具命名与 ``/api/execute`` 的 target 对齐，保证
``CLI / serve / MCP`` 三端同一命令标识：

* 无 group 资源：``<resource>.<method>``（如 ``user.list``）
* 有 group 资源：``<resource>.<method>``（group 前缀不入工具名——执行内核
  ``_lookup_resource_method`` 仅解析 ``resource.method``；资源名全局唯一）
* flow：``flow.<command>``（``-`` 转 ``_``，如 ``flow.add_user``；target 保持
  ``_flows.yaml`` 的原始 command ``add-user``）

``inputSchema`` 直接复用 ``params_to_json_schema`` / ``build_flow_schema`` 的产物，
保证参数模型与 serve /api/spec 完全一致（required / enum / multiple→array /
file→format:binary / json|object→object）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
