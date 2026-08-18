"""Error handler functions for cliyard."""

from __future__ import annotations

import requests

from cliyard.engine.errors import ApiError


def handle_api_error(response: requests.Response) -> None:
    """Parse API error response and raise ApiError.

    Attempts to extract structured ``code``/``message`` from JSON bodies,
    falls back to raw text.
    """
    body = response.text
    try:
        data = response.json()
        code = data.get("code", "")
        message = data.get("message", "")
        error_msg = f"{code}: {message}" if code else message
    except (ValueError, KeyError, TypeError):
        error_msg = body
    raise ApiError(status=response.status_code, url=response.url, body=error_msg)
