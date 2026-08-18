"""Field type validators for cliyard parameters.

Validates field values against their specifications before HTTP requests.
Supports 5 core types: string, int, float, bool, enum.

Example::

    spec = {"name": "age", "type": "int", "min": 0, "max": 150}
    validate_field(spec, "25")  # Returns 25
    validate_field(spec, "-5")  # Raises ValidationError
"""

from __future__ import annotations

import json
import re
from typing import Any


class ValidationError(Exception):
    """Raised when field validation fails.

    Attributes:
        field: Field name that failed validation.
        message: Human-readable error message.
    """

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"Validation error for '{field}': {message}")


def validate_field(field_spec: dict[str, Any], value: Any) -> Any:
    """Validate a field value against its spec. Returns validated value or raises.

    Args:
        field_spec: Field specification dict with 'name', 'type', and constraints.
        value: Value to validate.

    Returns:
        Validated and converted value.

    Raises:
        ValidationError: If value fails validation.
    """
    field_type = field_spec.get("type", "string")

    if field_type == "string":
        return _validate_string(field_spec, value)
    elif field_type in ("int", "integer"):
        return _validate_int(field_spec, value)
    elif field_type == "float":
        return _validate_float(field_spec, value)
    elif field_type == "bool":
        return _validate_bool(field_spec, value)
    elif field_type == "enum":
        return _validate_enum(field_spec, value)
    elif field_type == "file":
        return str(value)
    elif field_type in ("json", "object"):
        return _validate_json(field_spec, value)
    else:
        # Check plugin-registered field types before failing
        from cliyard.plugin import PluginRegistry
        from cliyard.plugin.discovery import discover_plugins

        discover_plugins()
        custom_type = PluginRegistry.get_field_type(field_type)
        if custom_type is not None:
            return custom_type.validate(value)

        raise ValidationError(
            field_spec.get("name", "unknown"),
            f"Unknown type: {field_type}",
        )


def _validate_string(field_spec: dict[str, Any], value: Any) -> str:
    """Validate string field with optional constraints.

    Constraints:
        min_length: Minimum string length.
        max_length: Maximum string length.
        pattern: Regex pattern the string must match.

    Args:
        field_spec: Field specification dict.
        value: Value to validate.

    Returns:
        Validated string value.

    Raises:
        ValidationError: If validation fails.
    """
    name = field_spec.get("name", "unknown")

    # Convert to string if needed
    if not isinstance(value, str):
        value = str(value)

    # Check min_length
    min_length = field_spec.get("min_length")
    if min_length is not None and len(value) < min_length:
        raise ValidationError(
            name, f"String too short: {len(value)} < {min_length}"
        )

    # Check max_length
    max_length = field_spec.get("max_length")
    if max_length is not None and len(value) > max_length:
        raise ValidationError(
            name, f"String too long: {len(value)} > {max_length}"
        )

    # Check pattern
    pattern = field_spec.get("pattern")
    if pattern is not None and not re.match(pattern, value):
        raise ValidationError(name, f"String does not match pattern: {pattern}")

    return value


def _validate_int(field_spec: dict[str, Any], value: Any) -> int:
    """Validate integer field with optional constraints.

    Constraints:
        min: Minimum value (inclusive).
        max: Maximum value (inclusive).

    Args:
        field_spec: Field specification dict.
        value: Value to validate.

    Returns:
        Validated integer value.

    Raises:
        ValidationError: If validation fails.
    """
    name = field_spec.get("name", "unknown")

    # Convert to int if needed
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise ValidationError(name, f"Cannot convert '{value}' to integer")
    elif isinstance(value, float):
        # Check if it's an integer value
        if value != int(value):
            raise ValidationError(name, f"Value is not an integer: {value}")
        value = int(value)
    elif not isinstance(value, int):
        raise ValidationError(name, f"Cannot convert {type(value).__name__} to integer")

    # Check min
    min_val = field_spec.get("min")
    if min_val is not None and value < min_val:
        raise ValidationError(name, f"Value too small: {value} < {min_val}")

    # Check max
    max_val = field_spec.get("max")
    if max_val is not None and value > max_val:
        raise ValidationError(name, f"Value too large: {value} > {max_val}")

    return value


def _validate_float(field_spec: dict[str, Any], value: Any) -> float:
    """Validate float field with optional constraints.

    Constraints:
        min: Minimum value (inclusive).
        max: Maximum value (inclusive).

    Args:
        field_spec: Field specification dict.
        value: Value to validate.

    Returns:
        Validated float value.

    Raises:
        ValidationError: If validation fails.
    """
    name = field_spec.get("name", "unknown")

    # Convert to float if needed
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise ValidationError(name, f"Cannot convert '{value}' to float")
    elif isinstance(value, int):
        value = float(value)
    elif not isinstance(value, float):
        raise ValidationError(name, f"Cannot convert {type(value).__name__} to float")

    # Check min
    min_val = field_spec.get("min")
    if min_val is not None and value < min_val:
        raise ValidationError(name, f"Value too small: {value} < {min_val}")

    # Check max
    max_val = field_spec.get("max")
    if max_val is not None and value > max_val:
        raise ValidationError(name, f"Value too large: {value} > {max_val}")

    return value


def _validate_json(field_spec: dict[str, Any], value: Any) -> Any:
    """Validate a JSON field, parsing a JSON string into an object/array.

    Accepts:
        - str: parsed via ``json.loads`` (must be a JSON object or array).
        - dict/list: passed through as-is (already structured).

    Returns:
        Parsed JSON object/array.

    Raises:
        ValidationError: If the string is not valid JSON, or the value is a
            non-JSON scalar that cannot represent a structured object.
    """
    name = field_spec.get("name", "unknown")

    if not isinstance(value, str):
        if isinstance(value, (dict, list)):
            return value
        raise ValidationError(
            name, f"Cannot convert {type(value).__name__} to JSON object"
        )

    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValidationError(name, f"Cannot parse '{value}' as JSON") from None

    if not isinstance(parsed, (dict, list)):
        raise ValidationError(
            name, f"JSON value must be an object or array, got {type(parsed).__name__}"
        )
    return parsed


def _validate_bool(field_spec: dict[str, Any], value: Any) -> bool:
    """Validate boolean field.

    Accepts:
        - True/False (native bool)
        - "true"/"false" (case-insensitive strings)
        - "1"/"0" (string numbers)
        - 1/0 (integer numbers)

    Args:
        field_spec: Field specification dict.
        value: Value to validate.

    Returns:
        Validated boolean value.

    Raises:
        ValidationError: If validation fails.
    """
    name = field_spec.get("name", "unknown")

    # Handle native bool
    if isinstance(value, bool):
        return value

    # Handle string
    if isinstance(value, str):
        lower_val = value.lower()
        if lower_val in ("true", "1", "yes"):
            return True
        elif lower_val in ("false", "0", "no"):
            return False
        else:
            raise ValidationError(name, f"Cannot convert '{value}' to boolean")

    # Handle int
    if isinstance(value, int):
        return bool(value)

    raise ValidationError(name, f"Cannot convert {type(value).__name__} to boolean")


def _validate_enum(field_spec: dict[str, Any], value: Any) -> str:
    """Validate enum field against allowed choices.

    Constraints:
        choices: List of allowed values (required).

    Args:
        field_spec: Field specification dict.
        value: Value to validate.

    Returns:
        Validated string value.

    Raises:
        ValidationError: If validation fails.
    """
    name = field_spec.get("name", "unknown")
    choices = field_spec.get("choices", [])

    if not choices:
        raise ValidationError(name, "Enum type requires 'choices' list")

    # Convert to string if needed
    if not isinstance(value, str):
        value = str(value)

    # Check if value is in choices (case-insensitive comparison)
    if value.lower() not in [c.lower() for c in choices]:
        raise ValidationError(
            name, f"Invalid choice: '{value}'. Allowed: {choices}"
        )

    # Return the original case from choices
    for choice in choices:
        if choice.lower() == value.lower():
            return choice

    # This should not happen, but just in case
    return value
