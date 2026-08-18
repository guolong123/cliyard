"""Structured error types for cliyard."""

from __future__ import annotations

from typing import Any


class CliyError(Exception):
    """Base exception for all cliyard errors."""


class ValidationError(CliyError):
    """Parameter validation failed before HTTP request."""

    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        if value is None or (isinstance(value, tuple) and not value):
            super().__init__(f"{field}: {reason}")
        else:
            super().__init__(f"{field}: {reason} (got {value!r})")


class AuthError(CliyError):
    """Authentication failed (env var missing, login failed, etc.)."""


class ApiError(CliyError):
    """API returned 4xx/5xx error."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"[{status}] {url}: {body[:200]}")


class SpecError(CliyError):
    """YAML configuration error."""

    def __init__(self, file: str, path: str, message: str) -> None:
        self.file = file
        self.path = path
        self.message = message
        super().__init__(f"{file}:{path}: {message}")
