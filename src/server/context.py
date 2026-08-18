"""ServiceContext 构造 —— 供 serve 执行引擎复用 runner 的解析链。

base_url 解析优先级与 CLI 完全一致（``runner.py:create_cli``）：

    runtime override（``<SERVICE>_SERVER`` / ``CLIYARD_SERVER`` 环境变量）
    > saved profile endpoint（``~/.cliyard/credentials.yaml``）
    > spec 默认 server 的 ``base_url``（兜底 ``http://localhost:8080``）

资源级 ``server`` 覆盖（服务级 + 资源级）也照搬 runner 逻辑：资源声明了
``server: <name>`` 且该名字存在于 service ``servers`` 时，用该 server 的
``base_url`` / ``prefix`` / ``timeout`` 覆盖默认值。
"""

from __future__ import annotations

from typing import Any

from cliyard.engine.builder import ServiceContext
from cliyard.runtime.runner import _resolve_base_url_override


def build_service_context(
    spec_dir: str,
    service: dict[str, Any],
    resource: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> ServiceContext:
    """构造与 runner.py 一致的 :class:`ServiceContext`。

    Args:
        spec_dir: Spec 目录路径（仅用于日志/错误场景，不参与解析）。
        service: :func:`cliyard.engine.loader.load_service` 的返回值。
        resource: 可选资源 spec（``server`` 字段触发资源级 server 覆盖）。
        base_url_override: 显式 base_url 覆盖（同 CLI ``--server``），优先级
            高于 ``<SERVICE>_SERVER`` / ``CLIYARD_SERVER`` 环境变量（复用
            runner 的 ``_resolve_base_url_override`` 解析链）。

    Returns:
        配置好 ``base_url`` / ``prefix`` / ``auth_spec`` / ``pre_filled_auth``
        / ``servers`` / ``timeout`` / ``default_format`` 的 ServiceContext。
    """
    from cliyard.client.credentials import get_current_profile, get_service_credentials

    service_name: str = service.get("name", "cliyard")
    servers: dict[str, Any] = service.get("servers", {}) or {}
    default_server_name: str = service.get("_default_server", "")
    auth_spec: dict[str, Any] | None = service.get("auth")
    service_id: str = auth_spec.get("id", service_name) if auth_spec else service_name

    # 默认 server 配置（base_url 可能来自保存的凭据）
    default_server: dict[str, Any] = {}
    if servers:
        default_server = servers.get(default_server_name) or next(iter(servers.values()), {})

    # 优先级：runtime override（显式参数 > <SERVICE>_SERVER / CLIYARD_SERVER env）
    # > saved profile endpoint > spec base_url
    runtime_override = _resolve_base_url_override(service_name, base_url_override)
    saved_profile = get_current_profile(service=service_id)
    saved_endpoints: dict[str, str] = saved_profile.get("endpoints", {}) if saved_profile else {}
    saved_endpoint = saved_profile.get("endpoint") if saved_profile else None
    base_url = runtime_override or saved_endpoint or default_server.get("base_url", "http://localhost:8080")
    prefix = default_server.get("prefix", "")

    # persist 配置下自动读入已保存凭据（与 runner.py L131-154 等价）
    pre_filled: dict[str, Any] | None = None
    if auth_spec and auth_spec.get("persist"):
        saved = get_service_credentials(service_id)
        if saved:
            persist = auth_spec.get("persist", {})
            persist_fields = persist.get("fields", {})
            pre_filled = {}
            for storage_key, field_config in persist_fields.items():
                ref: str = field_config.get("from", "")
                if "." in ref:
                    step_name, field_name = ref.split(".", 1)
                    value = saved.get(storage_key)
                    if value is not None:
                        pre_filled.setdefault(step_name, {})[field_name] = value
                else:
                    value = saved.get(storage_key)
                    if value is not None:
                        pre_filled[ref] = value
            if not persist_fields:
                pre_filled = saved

    # 资源级 server 覆盖（runner.py L222-236 等价）
    timeout = default_server.get("timeout", 30)
    if resource is not None:
        resource_server_name = resource.get("server", "")
        if resource_server_name and servers and resource_server_name in servers:
            srv = servers[resource_server_name]
            base_url = (
                runtime_override
                or saved_endpoints.get(resource_server_name)
                or saved_endpoint
                or srv.get("base_url", base_url)
            )
            prefix = srv.get("prefix", prefix)
            timeout = srv.get("timeout", 30)
        elif resource_server_name and saved_endpoints.get(resource_server_name):
            base_url = runtime_override or saved_endpoints[resource_server_name]
            timeout = 30

    return ServiceContext(
        base_url=base_url,
        prefix=prefix,
        auth_spec=auth_spec,
        pre_filled_auth=pre_filled,
        servers=servers,
        timeout=timeout,
        default_format=(service.get("output") or {}).get("default") or "json",
    )
