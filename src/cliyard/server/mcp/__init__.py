"""cliyard 作为 MCP Server 启动能力（可选依赖 ``cliyard[mcp]``）。

把 spec 命令树 / flow 树动态注册为 MCP 工具（工具名与 /api/execute target 对齐），
执行复用 serve 的同一内核（execute_pipeline / build_service_context /
_bridge_file_params / redact_sensitive），支持 stdio（默认）与 Streamable HTTP
两种 transport。

快速使用::

    pip install "cliyard[mcp]"
    cliyard mcp ./examples/demo            # stdio（Claude Desktop 等拉起）
    cliyard mcp ./examples/demo --transport http --port 8081   # Streamable HTTP
"""

from cliyard.server.mcp.tools import ToolSpec, build_tool_specs
from cliyard.server.mcp.executor import MCPExecutor
from cliyard.server.mcp.server import (
    build_mcp_http_app,
    create_mcp_server,
    is_local_host,
    mount_mcp_http,
    run_mcp_server,
    run_mcp_stdio,
)

__all__ = [
    "ToolSpec",
    "build_tool_specs",
    "MCPExecutor",
    "create_mcp_server",
    "build_mcp_http_app",
    "mount_mcp_http",
    "run_mcp_server",
    "run_mcp_stdio",
    "is_local_host",
]
