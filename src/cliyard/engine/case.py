"""CaseSpec dataclass — case definitions loaded from ``_cases.yaml``.

A *case* is a reusable invocation of a command or flow with fixed params
and assertions, used for smoke/nightly scenario execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseSpec:
    """Top-level case specification.

    Attributes:
        name: Case name (explicit ``name`` field or YAML key).
        description: Human-readable description.
        kind: Case kind — ``"command"`` or ``"flow"``.
        target: Command/flow path the case invokes (e.g. ``repos.list``).
        labels: List of labels for filtering/grouping.
        params: Parameters passed to the target (name → value).
        asserts: Assertion entries evaluated against the execution result.
    """

    name: str
    description: str = ""
    kind: str = "command"
    target: str = ""
    labels: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    asserts: list[Any] = field(default_factory=list)
