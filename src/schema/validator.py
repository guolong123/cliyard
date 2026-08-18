"""YAML spec schema validator for cliyard.

Catches configuration errors at load time rather than runtime.
Validates _auth.yaml and resource YAML files against expected structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of a schema validation pass.

    Attributes:
        is_valid: Whether the spec passed all required-field checks.
        errors: Human-readable error strings in the format
            ``"file:field_path: message"``.
    """

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)

    def add(self, file: str, path: str, message: str) -> None:
        """Append an error and flip ``is_valid`` to False."""
        self.is_valid = False
        self.errors.append(f"{file}:{path}: {message}")


# ---------------------------------------------------------------------------
# Top-level: validate_service
# ---------------------------------------------------------------------------

def validate_service(spec: dict, filename: str = "_auth.yaml") -> ValidationResult:
    """Validate a ``_auth.yaml`` spec dict.

    Checks:
    - ``name`` must be present and non-empty.
    - ``server.base_url`` must be present and non-empty.
    - ``auth.steps`` items (if present) must have ``name``, ``type``, and
      type-specific config fields.

    Args:
        spec: Parsed YAML dict (from ``yaml.safe_load``).
        filename: Filename used in error messages (default ``_auth.yaml``).

    Returns:
        :class:`ValidationResult` with ``is_valid`` flag and ``errors`` list.
    """
    result = ValidationResult()

    # -- Service-level fields ------------------------------------------------
    _require_non_empty(spec, filename, "", "name", result)
    _require_non_empty(spec, filename, "", "server", result)

    server = spec.get("server")
    if isinstance(server, dict):
        _require_non_empty(server, filename, "server", "base_url", result)

    # -- Auth steps ----------------------------------------------------------
    auth = spec.get("auth")
    if isinstance(auth, dict):
        steps = auth.get("steps")
        if not isinstance(steps, list):
            result.add(filename, "auth.steps", "must be a list")
        else:
            for i, step in enumerate(steps):
                prefix = f"auth.steps[{i}]"
                if not isinstance(step, dict):
                    result.add(filename, prefix, "must be a mapping")
                    continue
                _validate_auth_step(step, filename, prefix, result)

    # -- Resources -----------------------------------------------------------
    resources = spec.get("resources")
    if isinstance(resources, dict):
        for res_name, res_spec in resources.items():
            prefix = f"resources.{res_name}"
            if not isinstance(res_spec, dict):
                result.add(filename, prefix, "must be a mapping")
                continue
            _validate_resource(res_spec, filename, prefix, result)

    return result


# ---------------------------------------------------------------------------
# Auth step validation
# ---------------------------------------------------------------------------

_VALID_AUTH_TYPES = frozenset({"env", "login", "inject"})


def _validate_auth_step(
    step: dict, filename: str, prefix: str, result: ValidationResult
) -> None:
    _require_non_empty(step, filename, prefix, "name", result)
    _require_non_empty(step, filename, prefix, "type", result)

    step_type = step.get("type")
    if step_type and step_type not in _VALID_AUTH_TYPES:
        result.add(
            filename,
            f"{prefix}.type",
            f"{step_type!r} not in {sorted(_VALID_AUTH_TYPES)}",
        )

    config = step.get("config", {})
    if not isinstance(config, dict):
        result.add(filename, f"{prefix}.config", "must be a mapping")
        return

    if step_type == "login":
        _require_non_empty(config, filename, f"{prefix}.config", "endpoint", result)
        _require_non_empty(config, filename, f"{prefix}.config", "extract", result)
    elif step_type == "inject":
        _require_non_empty(config, filename, f"{prefix}.config", "into", result)
        _require_non_empty(config, filename, f"{prefix}.config", "name", result)


# ---------------------------------------------------------------------------
# Resource / Method / Param validation
# ---------------------------------------------------------------------------

_VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})


def _validate_resource(
    res: dict, filename: str, prefix: str, result: ValidationResult
) -> None:
    methods = res.get("methods")
    if methods is None:
        result.add(filename, f"{prefix}.methods", "must not be empty")
        return
    if not isinstance(methods, dict):
        result.add(filename, f"{prefix}.methods", "must be a mapping")
        return

    for method_name, method_spec in methods.items():
        m_prefix = f"{prefix}.methods.{method_name}"
        if not isinstance(method_spec, dict):
            result.add(filename, m_prefix, "must be a mapping")
            continue
        _validate_method(method_spec, filename, m_prefix, result)


def _validate_method(
    method: dict, filename: str, prefix: str, result: ValidationResult
) -> None:
    http = method.get("http")
    if not isinstance(http, dict):
        result.add(filename, f"{prefix}.http", "must be a mapping")
        return

    http_method = http.get("method")
    if not http_method:
        result.add(filename, f"{prefix}.http.method", "must not be empty")
    elif http_method.upper() not in _VALID_HTTP_METHODS:
        result.add(
            filename,
            f"{prefix}.http.method",
            f"{http_method!r} not in {sorted(_VALID_HTTP_METHODS)}",
        )

    # Validate params if present
    params = method.get("params")
    if isinstance(params, list):
        for i, param in enumerate(params):
            if not isinstance(param, dict):
                result.add(filename, f"{prefix}.params[{i}]", "must be a mapping")
                continue
            _validate_param(param, filename, f"{prefix}.params[{i}]", result)


def _validate_param(
    param: dict, filename: str, prefix: str, result: ValidationResult
) -> None:
    param_type = param.get("type")
    if param_type == "enum":
        choices = param.get("choices")
        if choices is None:
            result.add(
                filename,
                f"{prefix}.choices",
                "enum type requires 'choices' field",
            )
        elif not isinstance(choices, list):
            result.add(filename, f"{prefix}.choices", "must be a list")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_non_empty(
    d: dict, filename: str, prefix: str, key: str, result: ValidationResult
) -> None:
    """Check that *key* exists in *d* and is truthy (non-empty string, list, etc)."""
    value = d.get(key)
    if value is None:
        result.add(filename, f"{prefix}.{key}" if prefix else key, "must not be empty")
    elif isinstance(value, str) and not value.strip():
        result.add(filename, f"{prefix}.{key}" if prefix else key, "must not be empty")
