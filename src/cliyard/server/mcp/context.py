"""Request-scoped MCP execution context.

Command implementations occasionally need the *raw* tool-call arguments that
were supplied by the MCP client, including fields that are not part of the
target command's public schema (for example a transport/session hint that is
consumed by a command-specific auth layer).

``raw_tool_arguments`` is intentionally product-agnostic: cliyard only exposes
the arguments for the duration of one tool execution. It contains no ketacli
or other product semantics.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping, Optional

raw_tool_arguments: ContextVar[Optional[Mapping[str, Any]]] = ContextVar(
    "raw_tool_arguments", default=None
)


def get_raw_tool_arguments() -> Optional[Mapping[str, Any]]:
    """Return the raw MCP arguments for the current tool execution, if any."""
    return raw_tool_arguments.get()


__all__ = ["raw_tool_arguments", "get_raw_tool_arguments"]
