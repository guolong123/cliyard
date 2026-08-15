"""YAML spec → 命令树 / flow 树 + JSON Schema 转换器。

供 serve Web 前端生成命令树与 rjsf 表单使用。本模块是**纯函数**——
无 IO 副作用：spec 由调用方（app）启动时加载一次并缓存，或传入
``spec_dir`` 由内部调用 :func:`cliyard.engine.loader.load_service` /
:func:`cliyard.engine.loader.load_flows` 加载。

类型映射与 ``src/cliyard/validate/types.py`` 一致；labels 解析复用
``cliyard.engine.labels.resolve_labels``（与 Click 命令树 builder 共用，
避免重复实现）。

Example::

    from cliyard.server.schema_bridge import build_command_tree

    tree = build_command_tree("examples/demo")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cliyard.engine.labels import resolve_labels
from cliyard.engine.loader import load_flows, load_service

# JSON Schema 属性位置的固定遍历顺序（与 method params 的 YAML 分组一致）
_PARAM_LOCATIONS = ("path", "query", "header", "body", "argument")


# ---------------------------------------------------------------------------
# 参数 → JSON Schema
# ---------------------------------------------------------------------------


def _base_schema_for(param: dict[str, Any]) -> dict[str, Any]:
    """单个参数的 JSON Schema 类型映射（不含 ``multiple`` 包装）。

    ``string`` / ``int|integer`` / ``float`` / ``bool`` / ``enum`` /
    ``file`` / ``json|object`` 依次映射为 JSON Schema 基础类型；
    未知类型降级为 ``{"type": "string"}``（与 validate/types.py 的
    默认 string 兜底一致）。
    """
    t = param.get("type", "string")
    if t in ("int", "integer"):
        return {"type": "integer"}
    if t == "float":
        return {"type": "number"}
    if t == "bool":
        return {"type": "boolean"}
    if t == "enum":
        return {"type": "string", "enum": list(param.get("choices") or [])}
    if t == "file":
        return {"type": "string", "format": "binary"}
    if t in ("json", "object"):
        return {"type": "object"}
    return {"type": "string"}


def _is_required(param: dict[str, Any]) -> bool:
    """解析 ``required`` 字段（YAML 布尔或字符串均兼容）。"""
    value = param.get("required")
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _param_to_property(
    param: dict[str, Any], location: str
) -> tuple[str, dict[str, Any], bool] | None:
    """把单个参数映射为 ``(属性名, JSON Schema 属性, required)``。

    ``multiple: true`` 时包装为 ``{"type": "array", "items": {...}}``
    （items 用单值映射）；``default`` / ``description`` 透传；每个属性
    附加 ``x-location`` 扩展字段（供前端按 query/body/header/path/
    argument 分组展示）。
    """
    name = param.get("name") or param.get("field")
    if not name:
        return None

    prop = _base_schema_for(param)
    if param.get("multiple"):
        prop = {"type": "array", "items": _base_schema_for(param)}

    if "default" in param:
        prop["default"] = param["default"]
    if param.get("description"):
        prop["description"] = param["description"]

    prop["x-location"] = location
    return name, prop, _is_required(param)


def params_to_json_schema(
    param_list: dict[str, Any] | None, title: str | None = None
) -> dict[str, Any]:
    """把 method ``params`` 的 5 个位置（path/query/header/body/argument）
    合并为一个 JSON Schema object。

    Args:
        param_list: 位置分组 dict，如 ``{"query": [...], "body": [...]}``。
        title: 命令名，写入顶层 ``title`` 字段。

    Returns:
        JSON Schema object：``{"type": "object", "properties": {...},
        "required": [...]}``。
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for location in _PARAM_LOCATIONS:
        params = (param_list or {}).get(location)
        if not isinstance(params, list):
            continue
        for param in params:
            if not isinstance(param, dict):
                continue
            mapped = _param_to_property(param, location)
            if mapped is None:
                continue
            name, prop, is_required = mapped
            properties[name] = prop
            if is_required and name not in required:
                required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    if title:
        schema["title"] = title
    return schema


def build_flow_schema(
    flow_params: dict[str, Any] | None, title: str | None = None
) -> dict[str, Any]:
    """把 flow ``params``（_flows.yaml 的 params.query/body/header 结构）
    映射为 JSON Schema，映射规则同 :func:`params_to_json_schema`。

    flow 无 params 时返回空 object schema。
    """
    if not flow_params:
        return {"type": "object", "properties": {}, "required": []}
    return params_to_json_schema(flow_params, title=title)


# ---------------------------------------------------------------------------
# 命令树 / flow 树
# ---------------------------------------------------------------------------


def _load_group_definitions(spec_dir: str | Path) -> dict[str, Any]:
    """读取 ``_groups.yaml`` 分组定义（可选，容错）。

    与 runner.py 的分组逻辑一致（group 名 → ``{"description": ...}``）：
    文件缺失或解析失败时返回空 dict，不抛异常，保证命令树仍可构建。
    """
    groups_file = Path(spec_dir) / "_groups.yaml"
    if not groups_file.is_file():
        return {}
    import yaml

    try:
        data = yaml.safe_load(groups_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_command_tree(spec_dir: str | Path) -> dict[str, Any]:
    """加载 spec 目录并输出命令树 / flow 树元数据。

    Args:
        spec_dir: cliyard spec 目录（含 _auth.yaml、资源 YAML、flows/）。

    Returns:
        ``{"service": {name, description},
        "groups": [{"group", "desc", "commands", "resources":
        [{"name", "desc", "commands": [{"name", "labels", "desc", "path",
        "method", "schema"}]}]}],
        "flows": [{"name", "description", "command", "params_schema",
        "step_count"}]}``

        分组对齐 CLI ``<group> <resource> <method>`` 结构：``group`` 来自资源的
        ``group`` 字段（无则用资源 name 自身），``desc`` 优先取 ``_groups.yaml``
        的 description，缺省回退资源 description。有 ``group`` 字段的资源写入
        ``resources``（三级：组 > 资源 > 命令）；无 ``group`` 字段的扁平资源
        ``resources`` 为空数组、命令直接挂组下（二级，前端据此判定扁平渲染）。
        ``commands`` 为兼容字段：扁平资源直接拍平方法名；两级组资源以
        ``资源名.方法名`` 命名消歧（与 ``resources[].commands`` 的 target
        语义一致），避免同组下不同资源的同名方法重复展示。

    Raises:
        FileNotFoundError: spec_dir 缺少 _auth.yaml 时由 load_service 抛出。
    """
    service = load_service(spec_dir)
    flows = load_flows(spec_dir)
    group_defs = _load_group_definitions(spec_dir)

    grouped: dict[str, dict[str, Any]] = {}
    for resource in service.get("resources", []):
        rname = resource.get("name") or ""
        rdesc = resource.get("description") or rname
        methods = resource.get("methods") or {}

        commands: list[dict[str, Any]] = []
        for mname, method_spec in methods.items():
            if not isinstance(method_spec, dict):
                continue
            http = method_spec.get("http") or {}
            method = str(http.get("method") or "GET").upper()
            path = http.get("path") or resource.get("path") or rname
            commands.append(
                {
                    "name": mname,
                    "labels": resolve_labels(method_spec),
                    "desc": method_spec.get("description") or mname,
                    "path": path,
                    "method": method,
                    "schema": params_to_json_schema(
                        method_spec.get("params"), title=mname
                    ),
                }
            )

        gname = resource.get("group") or rname
        entry = grouped.setdefault(
            gname, {"group": gname, "desc": "", "commands": [], "resources": []}
        )
        if not entry["desc"]:
            _gdesc = (group_defs.get(gname) or {}).get("description")
            entry["desc"] = _gdesc or rdesc or f"{gname} 管理"
        if resource.get("group"):
            # 有 group → 三级：组 > 资源 > 命令；无 group 则 resources 保持 [] 触发前端二级扁平分支
            entry["resources"].append({"name": rname, "desc": rdesc, "commands": commands})
            # 兼容字段 commands：两级组以 资源名.方法名 消歧，避免同组下不同资源的同名方法重复展示
            entry["commands"].extend(
                {**cmd, "name": f"{rname}.{cmd['name']}"} for cmd in commands
            )
        else:
            entry["commands"].extend(commands)

    groups = list(grouped.values())

    flow_list: list[dict[str, Any]] = []
    for flow in flows:
        flow_list.append(
            {
                "name": flow.command.replace("-", "_"),
                "description": flow.description,
                "command": flow.command,
                "params_schema": build_flow_schema(flow.params, title=flow.command),
                "step_count": len(flow.steps),
            }
        )

    return {
        "service": {
            "name": service.get("name"),
            "description": service.get("description"),
        },
        "groups": groups,
        "flows": flow_list,
    }
