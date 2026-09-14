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


# ---------------------------------------------------------------------------
# file 参数 description 模板（todo 4：curl 上传指引 + upload_base 透传链）
# ---------------------------------------------------------------------------

#: ``upload_base`` 缺省时的占位基址（回环地址；模板另附一句配置提示）。
_FILE_UPLOAD_BASE_PLACEHOLDER = "http://127.0.0.1:8081"


def _display_upload_base(upload_base: str | None) -> tuple[str, bool]:
    """返回 ``(描述模板用的基址, 是否为缺省占位)``。

    * 显式 ``upload_base``：去尾 ``/``；其中通配绑定 host（``0.0.0.0`` /
      ``::``）回退 ``127.0.0.1``——复用
      :func:`cliyard.server.mcp.server.display_host`（函数内懒导入：
      ``mcp.server → executor → tools → schema_bridge`` 的导入环要求
      不能在模块顶层 import，此处调用时各模块已加载完毕）。
    * 缺省（``None`` / 空串）：返回占位基址 + ``True``（调用方据此追加
      一句 ``--upload-base-url`` 配置提示）。
    """
    if upload_base and upload_base.strip():
        base = upload_base.strip().rstrip("/")
        try:
            from urllib.parse import urlsplit, urlunsplit

            from cliyard.server.mcp.server import display_host

            parts = urlsplit(base)
            hostname = parts.hostname or ""
            if hostname in ("0.0.0.0", "::"):
                loopback = display_host(hostname)
                netloc = ""
                if parts.username:
                    netloc += parts.username
                    if parts.password:
                        netloc += f":{parts.password}"
                    netloc += "@"
                netloc += loopback
                if parts.port:
                    netloc += f":{parts.port}"
                base = urlunsplit(
                    (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
                )
        except Exception:
            pass
        return base, False
    return _FILE_UPLOAD_BASE_PLACEHOLDER, True


def _file_upload_guide(upload_base: str | None, transport: str) -> str:
    """file 参数 description 追加的 curl 上传指引（todo 4）。

    三要素恒成立：``curl -X POST <基址>/upload``、``$TOKEN`` 占位（永不含
    真实 token——本链路根本不接收 token 参数）、``不要直接填你机器的本地路径``。
    ``transport="stdio"`` 追加同机 ``file_path`` 段（且全程不谈 allowlist，
    allowlist 是 todo 5 的 jail 文案）；``transport="http"`` 无此段。
    """
    base, is_placeholder = _display_upload_base(upload_base)
    lines = [
        "不要直接填你机器的本地路径（server 读不到你机器上的文件）：先上传，再把返回的服务端路径填入本参数。",
        f'curl -X POST {base}/upload -H "Authorization: Bearer $TOKEN" -F "file=@本地文件路径"',
        "（$TOKEN 换成连接本 MCP/serve 所用的 token；返回 JSON 中的服务端路径填入本参数。）",
    ]
    if is_placeholder:
        lines.append(
            "（未配置 --upload-base-url：请把上例地址替换为实际服务地址，或启动时传入 --upload-base-url。）"
        )
    if transport == "stdio":
        lines.append(
            "stdio 同机模式：文件若已在 server 本机，也可直接填 server 上的 file_path 绝对路径。"
        )
    return "\n".join(lines)


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
    param: dict[str, Any],
    location: str,
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> tuple[str, dict[str, Any], bool] | None:
    """把单个参数映射为 ``(属性名, JSON Schema 属性, required)``.

    ``multiple: true`` 时包装为 ``{"type": "array", "items": {...}}``
    （items 用单值映射）；``default`` / ``description`` 透传；每个属性
    附加 ``x-location`` 扩展字段（供前端按 query/body/header/path/
    argument 分组展示）。

    ``type: file`` 的参数额外在 ``description`` 后追加 curl 上传指引
    （见 :func:`_file_upload_guide`；``upload_base`` / ``transport`` 仅
    影响该指引文本，非 file 参数输出与今日逐字节一致）。
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
    if param.get("type") == "file":
        guide = _file_upload_guide(upload_base, transport)
        if prop.get("description"):
            prop["description"] = f"{prop['description']}\n{guide}"
        else:
            prop["description"] = guide

    prop["x-location"] = location
    return name, prop, _is_required(param)


def params_to_json_schema(
    param_list: dict[str, Any] | None,
    title: str | None = None,
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> dict[str, Any]:
    """把 method ``params`` 的 5 个位置（path/query/header/body/argument）
    合并为一个 JSON Schema object.

    Args:
        param_list: 位置分组 dict，如 ``{"query": [...], "body": [...]}``。
        title: 命令名，写入顶层 ``title`` 字段。
        upload_base: 对外 ``POST /upload`` 基地址（仅 file 参数描述模板
            消费；缺省 ``None`` → 占位地址 + 一句配置提示）。
        transport: ``"http"`` 或 ``"stdio"``（仅 file 参数描述模板消费；
            ``"stdio"`` 追加同机 ``file_path`` 段）。

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
            mapped = _param_to_property(
                param, location, upload_base=upload_base, transport=transport
            )
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
    flow_params: dict[str, Any] | None,
    title: str | None = None,
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> dict[str, Any]:
    """把 flow ``params``（_flows.yaml 的 params.query/body/header 结构）
    映射为 JSON Schema，映射规则同 :func:`params_to_json_schema`。

    flow 无 params 时返回空 object schema。
    """
    if not flow_params:
        return {"type": "object", "properties": {}, "required": []}
    return params_to_json_schema(
        flow_params, title=title, upload_base=upload_base, transport=transport
    )


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


def build_command_tree(
    spec_dir: str | Path,
    *,
    upload_base: str | None = None,
    transport: str = "http",
) -> dict[str, Any]:
    """加载 spec 目录并输出命令树 / flow 树元数据。

    Args:
        spec_dir: cliyard spec 目录（含 _auth.yaml、资源 YAML、flows/）。
        upload_base: 对外 ``POST /upload`` 基地址（透传给各命令 schema 的
            file 参数描述模板；缺省 ``None`` → 占位地址 + 配置提示）。
        transport: ``"http"`` 或 ``"stdio"``（透传给 file 参数描述模板）。

    Returns:
        ``{"service": {name, description},
        "groups": [{"group", "desc", "commands", "resources":
        [{"name", "desc", "commands": [{"name", "labels", "desc", "path",
        "method", "schema"}]}]}],
        "flows": [{"name", "description", "command", "category", "labels", "params_schema",
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
                        method_spec.get("params"),
                        title=mname,
                        upload_base=upload_base,
                        transport=transport,
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
                "category": flow.category,
                "category_label": flow.category_label,
                "labels": flow.labels,
                "params_schema": build_flow_schema(
                    flow.params,
                    title=flow.command,
                    upload_base=upload_base,
                    transport=transport,
                ),
                "step_count": len(flow.steps),
            }
        )

    return {
        "service": {
            "name": service.get("name"),
            "description": service.get("description"),
            "web": service.get("web", {}),
        },
        "groups": groups,
        "flows": flow_list,
    }
