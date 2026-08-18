"""Schema type definitions for cliyard YAML specs.

All types represent the structure of YAML configuration files used to define
API services and resources. These are plain TypedDict definitions with no
runtime validation logic — just type hints for IDE support and static analysis.

Example usage::

    from cliyard.schema.types import ServiceSpec
    spec: ServiceSpec = {
        "name": "my-service",
        "version": "1.0.0",
        "description": "My API",
        "server": {"base_url": "https://api.example.com"},
        "auth": {"steps": []},
        "resources": [],
    }
"""

from __future__ import annotations

from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Top-level: _auth.yaml
# ---------------------------------------------------------------------------


class ServerConfig(TypedDict, total=False):
    """Server connection configuration.

    Attributes:
        base_url: Base URL of the API (required).
        prefix: URL prefix for all endpoints (default: "").
        timeout: Request timeout in seconds (default: 30).
    """

    base_url: str
    prefix: str
    timeout: int


class AuthStep(TypedDict, total=False):
    """Single authentication step.

    Attributes:
        name: Human-readable name for this step.
        type: Authentication type: "env", "login", or "inject".
        config: Flexible configuration dict specific to the auth type.
        extract: JSONPath extraction map (``field_name → jsonpath``) for
            ``login`` steps.
    """

    name: str
    type: str
    config: dict[str, Any]
    extract: dict[str, str]


class AuthPersistField(TypedDict, total=False):
    """Single field in the persist configuration.

    Attributes:
        from_: Step.field reference (e.g. ``"create_token.token"``).
        default: Fallback value if the referenced field is missing.
    """

    from_: str
    default: Any


class AuthPersist(TypedDict, total=False):
    """Credential persistence configuration.

    Attributes:
        to: Storage target — ``"cliyard-config"`` (default), ``"env"``,
            or ``"file"``.
        fields: Mapping of ``field_name → {from: "step.field"}``.
    """

    to: str
    fields: dict[str, AuthPersistField]


class AuthChain(TypedDict, total=False):
    """Authentication chain — ordered list of auth steps.

    Attributes:
        id: Optional service identifier for credential persistence.
        steps: List of AuthStep definitions to execute in order.
        persist: Optional persistence configuration for saving credentials.
    """

    id: str
    steps: list[AuthStep]
    persist: AuthPersist


class ServiceSpec(TypedDict):
    """Top-level structure of a `_auth.yaml` file.

    This is the root type for a cliyard service definition. It describes
    the service metadata, server connection, authentication, and resources.

    Attributes:
        name: Service name identifier.
        version: Semver version string.
        description: Human-readable service description.
        server: Server connection configuration.
        auth: Authentication chain (can be empty steps list).
        resources: List of resource specifications.
    """

    name: str
    version: str
    description: str
    server: ServerConfig
    auth: AuthChain
    resources: list[ResourceSpec]


# ---------------------------------------------------------------------------
# Resource: per-resource YAML file
# ---------------------------------------------------------------------------


class ParamSpec(TypedDict, total=False):
    """Single parameter definition.

    Attributes:
        name: Parameter name.
        type: Parameter type: "string", "int", "float", "bool", "enum".
        required: Whether parameter is required (default: False).
        default: Default value if not required.
        description: Human-readable parameter description.
        choices: Allowed values for "enum" type parameters.
        depends_on: Dependencies on other parameters.
    """

    name: str
    type: str
    required: bool
    default: Any
    description: str
    choices: list[str]
    depends_on: dict[str, Any]


class ParamConfig(TypedDict, total=False):
    """Parameters organized by location.

    Attributes:
        path: Path parameters (e.g., /repos/{id}).
        query: Query string parameters.
        header: HTTP header parameters.
        body: Request body parameters.
    """

    path: list[ParamSpec]
    query: list[ParamSpec]
    header: list[ParamSpec]
    body: list[ParamSpec]


class HttpConfig(TypedDict):
    """HTTP request configuration.

    Attributes:
        method: HTTP method (GET, POST, PUT, DELETE).
        path: URL path (may include path parameters).
    """

    method: str
    path: str


class FieldSpec(TypedDict, total=False):
    """Field definition for output formatting.

    Attributes:
        name: Field name in the response.
        alias: Display alias for the field (optional).
    """

    name: str
    alias: str


class OutputSpec(TypedDict, total=False):
    """Output configuration for parsing API responses.

    Attributes:
        items_path: JSONPath to the list of items.
        total_path: JSONPath to total count (optional).
        fields: List of field definitions for output display.
        default: Default ``--format`` for this method (table/json/csv/yaml
            or a plugin-registered format).
    """

    items_path: str
    total_path: str
    fields: list[FieldSpec]
    default: str


class MethodSpec(TypedDict, total=False):
    """Method definition within a resource.

    Attributes:
        http: HTTP request configuration.
        params: Parameters organized by location.
        output: Output configuration (optional).
        request_body: Request body template (optional).
    """

    http: HttpConfig
    params: ParamConfig
    output: OutputSpec
    request_body: dict[str, Any]


class ResourceSpec(TypedDict):
    """Resource YAML file top-level structure.

    This represents a single resource file (e.g., repos.yaml, users.yaml).
    It defines the resource path, description, available methods, and
    optional resource-level authentication.

    Attributes:
        description: Human-readable resource description.
        path: URL path segment for this resource.
        methods: Dict mapping method names to MethodSpec definitions.
        auth: Optional resource-level auth override.
    """

    description: str
    path: str
    methods: dict[str, MethodSpec]
    auth: AuthChain


# ---------------------------------------------------------------------------
# Flow: _flows.yaml
# ---------------------------------------------------------------------------


class ForEachConfigSpec(TypedDict, total=False):
    """ForEach iteration configuration.

    Attributes:
        items: Jinja2 expression evaluating to an iterable.
        as_name: Variable name for each item inside the loop body.
        steps: Steps to execute per iteration.
    """

    items: str
    as_name: str
    steps: list[FlowStepSpec]


class RetryConfigSpec(TypedDict, total=False):
    """Retry configuration for a step.

    Attributes:
        max_attempts: Maximum number of attempts (default: 3).
        delay: Delay between retries in seconds (default: 1).
        backoff: Multiplier applied to delay after each attempt.
        on_exhausted: Action to take when all retries are exhausted.
    """

    max_attempts: int
    delay: int
    backoff: int
    on_exhausted: dict[str, Any]


class UntilConfigSpec(TypedDict, total=False):
    """Polling / wait-until configuration.

    Attributes:
        max_iterations: Maximum polling iterations (default: 30).
        interval: Seconds between iterations (default: 5).
        condition: Jinja2 expression that must evaluate to truthy to stop.
        timeout_action: Action on timeout — "abort" or "continue".
        timeout_message: Message to display on timeout.
    """

    max_iterations: int
    interval: int
    condition: str
    timeout_action: str
    timeout_message: str


class FlowStepSpec(TypedDict, total=False):
    """A single step inside a Flow.

    Attributes:
        id: Unique step identifier within the flow.
        description: Human-readable step description.
        use: Target command/flow to delegate to (for type=use).
        params: Parameters to pass to the target.
        extract: JSONPath extraction map (field_name → jsonpath).
        on_result: Conditional branching based on extraction results.
        on_failure: Fallback action when this step fails.
        assert_: Jinja2 expression that must be truthy for the step to succeed.
        for_each: Iteration config (mutually exclusive with until).
        retry: Retry configuration.
        until: Polling/wait-until configuration (mutually exclusive with for_each).
        hooks: Per-step hook overrides.
        type: Step type override (normally inferred from config).
    """

    id: str
    description: str
    use: str
    params: dict[str, Any]
    extract: dict[str, str]
    on_result: list[dict[str, Any]]
    on_failure: dict[str, Any]
    assert_: str
    for_each: ForEachConfigSpec
    retry: RetryConfigSpec
    until: UntilConfigSpec
    hooks: dict[str, dict[str, Any]]
    type: str


class FlowSpecSpec(TypedDict, total=False):
    """Top-level Flow specification.

    Attributes:
        command: CLI command name (e.g. "deploy").
        description: Human-readable description.
        params: Parameter definitions (name → config dict).
        steps: Ordered list of FlowStepSpec.
        hooks: Lifecycle hooks (hook_type → config dict).
    """

    command: str
    description: str
    params: dict[str, Any]
    steps: list[FlowStepSpec]
    hooks: dict[str, dict[str, Any]]
