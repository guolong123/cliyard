"""Field dependency support for cliyard parameters.

Checks conditional required fields based on depends_on.eq conditions.

Example::

    field_spec = {
        "name": "db_password",
        "type": "string",
        "required": True,
        "depends_on": {"field": "auth_type", "eq": "password"}
    }
    errors = check_dependencies({"auth_type": "password"}, [field_spec])
    # Returns [ValidationError] because db_password is required but missing
"""

from __future__ import annotations

from typing import Any

from cliyard.validate.types import ValidationError


def check_dependencies(
    params: dict[str, Any], field_specs: list[dict[str, Any]]
) -> list[ValidationError]:
    """Check field dependencies. Returns list of ValidationErrors.

    For each field with a depends_on.eq condition:
    - If condition is met AND field is required AND no value provided → error
    - If condition is not met → field is ignored (no error)

    Args:
        params: User-provided parameter values.
        field_specs: List of field specification dicts.

    Returns:
        List of ValidationError objects (empty if all pass).
    """
    errors = []

    for field_spec in field_specs:
        depends_on = field_spec.get("depends_on")
        if not depends_on:
            continue

        dep_field = depends_on.get("field")
        dep_value = depends_on.get("eq")

        # Check if dependency condition is met
        actual_value = params.get(dep_field)
        condition_met = actual_value == dep_value

        if condition_met and field_spec.get("required"):
            if field_spec["name"] not in params:
                errors.append(
                    ValidationError(
                        field=field_spec["name"],
                        message=f"required when {dep_field}={dep_value}",
                    )
                )

    return errors
