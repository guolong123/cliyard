"""Hook execution engine for pre/post request processing."""

from __future__ import annotations

from cliyard.engine.assembler import Request
from cliyard.plugin import PluginRegistry


def run_pre_request_hooks(hook_names: list[str], req: Request) -> Request:
    """Run pre-request hooks in order. Each hook receives and returns a Request."""
    for name in hook_names:
        hook_fn = PluginRegistry.get_hook(name)
        if hook_fn:
            result = hook_fn(req)
            if result is not None:
                req = result
    return req


def run_post_response_hooks(hook_names: list[str], response_data: dict) -> dict:
    """Run post-response hooks in order."""
    for name in hook_names:
        hook_fn = PluginRegistry.get_hook(name)
        if hook_fn:
            result = hook_fn(response_data)
            if result is not None:
                response_data = result
    return response_data
