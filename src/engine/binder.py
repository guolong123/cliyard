"""CLI parameter binder — groups Click kwargs by HTTP location and validates.

Binds raw CLI keyword arguments to method spec parameter definitions,
validates each value, and groups them into path / query / header / body
dictionaries ready for request assembly.

Example::

    spec = {
        "params": {
            "path": [{"name": "id", "type": "int", "required": True}],
            "query": [{"name": "limit", "type": "int", "default": 10}],
        }
    }
    result = bind_and_validate({"id": "42"}, spec)
    # result.path == {"id": 42}
    # result.query == {"limit": 10}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cliyard.engine.errors import ValidationError
from cliyard.validate.types import (
    ValidationError as FieldValidationError,
    validate_field,
)

_LOCATIONS = ("argument", "path", "query", "header", "body")


def _norm_param_name(name: str) -> str:
    """Normalize a param name for loose matching: lowercase, '-' == '_'."""
    return name.lower().replace("-", "_")


@dataclass
class ValidatedParams:
    """Parameters grouped by HTTP location, ready for request assembly.

    Attributes:
        argument: Positional CLI arguments (e.g. SPL query string).
        path: Path template variables (e.g. ``{"id": 42}``).
        query: Query string parameters (e.g. ``{"limit": 10}``).
        header: Request headers (e.g. ``{"Authorization": "Bearer ..."}``).
        body: Request body template values.
    """

    argument: dict[str, Any] = field(default_factory=dict)
    path: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    header: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


def bind_and_validate(kwargs: dict[str, Any], method_spec: dict[str, Any]) -> ValidatedParams:
    """Bind CLI kwargs to method spec params, validate, and group by location.

    Iterates over each parameter location (path, query, header, body) in the
    method spec, resolves values from *kwargs*, applies defaults for optional
    fields, validates non-None values, and populates the corresponding
    :class:`ValidatedParams` dict.

    Args:
        kwargs: Raw keyword arguments from Click (all values are strings or
            ``None``).
        method_spec: Method specification dict containing a ``params`` key
            with location-grouped field specs.

    Returns:
        A :class:`ValidatedParams` instance with validated, typed values.

    Raises:
        ValidationError: If a required field is missing or validation fails.
    """
    params_config = method_spec.get("params", {})
    validated = ValidatedParams()

    for location in _LOCATIONS:
        field_specs = params_config.get(location, [])
        target: dict[str, Any] = getattr(validated, location)

        for field_spec in field_specs:
            name = field_spec["name"]
            value = kwargs.get(name)

            # Click normalizes argument names to lowercase; try case-insensitive
            # fallback, treating '-' and '_' as equivalent (Click turns option
            # flags like --x-namespace into the kwarg x_namespace).
            if value is None:
                for k, v in kwargs.items():
                    if _norm_param_name(k) == _norm_param_name(name):
                        value = v
                        break

            # Required check (multiple=True passes () not None)
            is_missing = value is None or (field_spec.get("multiple") and value == ())
            if is_missing and field_spec.get("required"):
                raise ValidationError(name, value, "required")

            # Apply default if not provided
            if value is None:
                value = field_spec.get("default")

            # Validate and store
            if value is not None:
                if field_spec.get("multiple"):
                    # Multiple values: pass through as-is (Click handles types)
                    target[name] = value
                else:
                    try:
                        validated_value = validate_field(field_spec, value)
                    except FieldValidationError as exc:
                        raise ValidationError(name, value, exc.message) from exc
                    target[name] = validated_value

    return validated
