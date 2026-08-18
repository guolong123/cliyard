"""cliyard.engine.flow — Flow data model.

Defines the data structures for command orchestration (Flow), including
step types, hook types, retry/loop configs, and the top-level FlowSpec.

Security model:
- No runtime execution logic — pure data containers.
- Templates are rendered by the engine layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FlowStepType(str, Enum):
    """Step execution types."""

    USE = "use"  # Delegate to a command (method/flow/auth)
    ECHO = "echo"  # Print a message
    PLUGIN = "plugin"  # Execute a plugin step
    ACTION = "action"  # Internal action (extract, set, etc.)


class FlowHookType(str, Enum):
    """Hook lifecycle points."""

    ON_START = "on_start"
    ON_END = "on_end"
    ON_FAILURE = "on_failure"
    ON_STEP_START = "on_step_start"
    ON_STEP_END = "on_step_end"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ForEachConfig:
    """Configuration for iteration over a collection.

    Attributes:
        items: Jinja2 expression evaluating to an iterable.
        as_name: Variable name for each item inside the loop body.
        steps: Steps to execute per iteration.
    """

    items: str
    as_name: str
    steps: list[FlowStep] = field(default_factory=list)


@dataclass
class RetryConfig:
    """Retry configuration for a step.

    Attributes:
        max_attempts: Maximum number of attempts (default: 3).
        delay: Delay between retries in seconds (default: 1).
        backoff: Multiplier applied to delay after each attempt (None = no backoff).
        on_exhausted: Action to take when all retries are exhausted.
    """

    max_attempts: int = 3
    delay: int = 1
    backoff: int | None = None
    on_exhausted: dict[str, Any] | None = None


@dataclass
class UntilConfig:
    """Polling / wait-until configuration.

    Attributes:
        max_iterations: Maximum polling iterations (default: 30).
        interval: Seconds between iterations (default: 5).
        condition: Jinja2 expression that must evaluate to truthy to stop.
        timeout_action: Action on timeout — "abort" or "continue".
        timeout_message: Message to display on timeout.
    """

    max_iterations: int = 30
    interval: int = 5
    condition: str = ""
    timeout_action: str = "abort"
    timeout_message: str = ""


# ---------------------------------------------------------------------------
# FlowStep dataclass
# ---------------------------------------------------------------------------


@dataclass
class FlowStep:
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
        hooks: Per-step hook overrides (FlowHookType → config dict).
        type: Step type override (normally inferred from config).
        show_response: If ``True``, print this step's resolved params and
            response details after execution (per-step verbose override).
    """

    id: str
    description: str = ""
    use: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    extract: dict[str, str] | None = None
    on_result: list[dict[str, Any]] | None = None
    on_failure: dict[str, Any] | None = None
    assert_: str | None = None
    for_each: ForEachConfig | None = None
    retry: RetryConfig | None = None
    until: UntilConfig | None = None
    hooks: dict[str, dict[str, Any]] | None = None
    type: str = ""
    show_response: bool = False


# ---------------------------------------------------------------------------
# FlowSpec dataclass (top-level)
# ---------------------------------------------------------------------------


@dataclass
class FlowSpec:
    """Top-level Flow specification.

    Attributes:
        command: CLI command name (e.g. "deploy").
        description: Human-readable description.
        params: Parameter definitions (name → config dict).
        steps: Ordered list of FlowStep.
        hooks: Lifecycle hooks (FlowHookType → config dict).
    """

    command: str
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    steps: list[FlowStep] = field(default_factory=list)
    hooks: dict[str, dict[str, Any]] | None = None
