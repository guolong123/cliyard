"""YAML spec loader for cliyard services.

Loads a service directory structure into a merged dict::

    spec_dir/
    ├── _auth.yaml      # Service config (server, auth)
    ├── repos.yaml          # Resource definition → resource name "repos"
    ├── users.yaml          # Resource definition → resource name "users"
    └── ...

Usage::

    from cliyard.engine.loader import load_service, load_resource

    service = load_service("/path/to/specs/github")
    resource = load_resource("/path/to/specs/github/repos.yaml")
"""

from __future__ import annotations

import importlib
import warnings
from pathlib import Path
from typing import Any

import yaml

from cliyard.engine.case import CaseSpec
from cliyard.engine.flow import (
    FlowSpec,
    FlowStep,
    ForEachConfig,
    RetryConfig,
    UntilConfig,
)


def load_service(spec_dir: str | Path) -> dict[str, Any]:
    """Load a cliyard service from a directory.

    Reads ``_auth.yaml`` for service metadata, then scans for
    ``*.yaml`` resource files (excluding ``_auth.yaml`` and
    ``_service.*.yaml`` variants like ``_service.local.yaml``).

    Each resource YAML filename (minus ``.yaml``) becomes the resource name.

    Args:
        spec_dir: Path to the service spec directory.

    Returns:
        Dict with keys: ``name``, ``version``, ``description``,
        ``server``, ``auth``, ``resources`` (list of resource dicts).

    Raises:
        FileNotFoundError: If ``_auth.yaml`` is missing.
        yaml.YAMLError: If any YAML file has syntax errors.
        ValueError: If ``_auth.yaml`` is missing required fields.
    """
    spec_dir = Path(spec_dir)
    service_path = spec_dir / "_auth.yaml"

    if not service_path.exists():
        raise FileNotFoundError(
            f"Missing _auth.yaml in {spec_dir}"
        )

    # Load service config
    service = _load_yaml(service_path)
    _render_server_templates(service)

    # Normalize server config: support both list (new) and dict (old) format
    server_raw = service.get("server", {})
    if isinstance(server_raw, list):
        # New format: [{name: "serve1", base_url: "...", prefix: "..."}, ...]
        servers: dict[str, Any] = {}
        for entry in server_raw:
            sname = entry.get("name", "")
            if sname:
                servers[sname] = entry
        if not servers:
            raise ValueError(f"{service_path}: 'server' list must contain at least one entry with a 'name'")
        service["servers"] = servers
        # First server is default
        service["server"] = servers[list(servers.keys())[0]]
    elif isinstance(server_raw, dict):
        # Old format: {base_url: "...", prefix: "..."}
        # Check if it's already a named dict
        if "base_url" in server_raw:
            service["servers"] = {"default": server_raw}
        else:
            # Already a named dict like {serve1: {base_url: ...}}
            service["servers"] = server_raw
    else:
        raise ValueError(f"{service_path}: 'server' is required and must be a mapping or list")

    # Scan for resource YAML files (root dir + resources/ subdir)
    resources: list[dict[str, Any]] = []
    for scan_dir in (spec_dir, spec_dir / "resources"):
        if not scan_dir.is_dir():
            continue
        for yaml_file in sorted(scan_dir.glob("*.yaml")):
            if _is_resource_file(yaml_file):
                resource_spec = _load_yaml(yaml_file)

                if not isinstance(resource_spec.get("methods"), dict):
                    raise ValueError(
                        f"{yaml_file}: 'methods' is required and must be a mapping"
                    )

                resource_name = resource_spec.get("name") or yaml_file.stem
                if "name" not in resource_spec:
                    resource_spec["name"] = resource_name
                resources.append(resource_spec)

    # Ensure auth defaults to empty steps
    if "auth" not in service:
        service["auth"] = {"steps": []}

    # Discover plugins from the spec directory's plugins/ subdirectory
    from cliyard.plugin.discovery import discover_plugins

    discover_plugins(str(spec_dir))

    # Register plugins from YAML spec
    _register_plugins(service.get("plugins", {}))

    service["resources"] = resources
    return service


def load_resource(yaml_path: str | Path) -> dict[str, Any]:
    """Load a single resource YAML file.

    Args:
        yaml_path: Path to a resource YAML file.

    Returns:
        Parsed resource dict with ``methods`` key.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the YAML has syntax errors.
        ValueError: If ``methods`` is missing or not a mapping.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Resource YAML not found: {yaml_path}")

    resource = _load_yaml(yaml_path)

    if not isinstance(resource.get("methods"), dict):
        raise ValueError(f"{yaml_path}: 'methods' is required and must be a mapping")

    resource["name"] = yaml_path.stem
    return resource


def load_flows(spec_dir: str | Path) -> list[FlowSpec]:
    """Load flow definitions from a spec directory.

    Looks for ``flows/_flows.yaml`` (or ``_flows.yaml`` at root for backward
    compatibility).  Each flow's ``steps:`` can be:

    * A **list** of step dicts (inline).
    * A **string** file path (load steps from that file).

    Step lists also support ``include: <path>`` entries, which resolve to steps
    in the referenced file (Ansible-playbook style).  All file paths are
    resolved relative to the directory containing ``_flows.yaml``.
    """
    spec_dir = Path(spec_dir).resolve()

    # Look in flows/ subdirectory first, then root (backward compat)
    flows_dir = spec_dir / "flows"
    if flows_dir.is_dir():
        flows_path = flows_dir / "_flows.yaml"
        base_dir = flows_dir
    else:
        flows_path = spec_dir / "_flows.yaml"
        base_dir = spec_dir

    if not flows_path.exists():
        return []

    raw = _load_yaml(flows_path)
    raw_flows = raw.get("flows") or {}

    flows: list[FlowSpec] = []
    for fname, fdict in raw_flows.items():
        if not isinstance(fdict, dict):
            continue
        if "command" not in fdict:
            continue

        raw_steps = fdict.get("steps")
        steps = _resolve_steps(raw_steps, base_dir, flows_path)

        if not steps:
            continue

        flows.append(FlowSpec(
            command=fdict["command"],
            description=fdict.get("description", ""),
            category=fdict.get("category", ""),
            category_label=fdict.get("category_label", ""),
            labels=fdict.get("labels", []),
            params=fdict.get("params", {}),
            steps=steps,
            hooks=fdict.get("hooks"),
        ))

    return flows


def load_cases(spec_dir: str | Path) -> list[CaseSpec]:
    """Load case definitions from a spec directory.

    Looks for ``cases/_cases.yaml`` first, falling back to ``_cases.yaml``
    at the root (mirroring :func:`load_flows`).  Malformed entries emit a
    warning and are skipped — loading never raises for bad entries.
    """
    from cliyard.engine.labels import resolve_labels

    spec_dir = Path(spec_dir).resolve()

    # Look in cases/ subdirectory first, then root (mirror load_flows)
    cases_dir = spec_dir / "cases"
    if cases_dir.is_dir():
        cases_path = cases_dir / "_cases.yaml"
    else:
        cases_path = spec_dir / "_cases.yaml"

    if not cases_path.exists():
        return []

    raw = _load_yaml(cases_path)
    raw_cases = raw.get("cases") or {}

    cases: list[CaseSpec] = []
    for case_name, case_dict in raw_cases.items():
        if not isinstance(case_dict, dict):
            warnings.warn(
                f"{cases_path}: case {case_name!r} must be a mapping; skipping",
                UserWarning,
                stacklevel=2,
            )
            continue

        name = case_dict.get("name") or case_name
        if not name:
            warnings.warn(
                f"{cases_path}: case entry {case_name!r} has no name; skipping",
                UserWarning,
                stacklevel=2,
            )
            continue

        kind = case_dict.get("kind") or "command"
        if kind not in ("command", "flow"):
            warnings.warn(
                f"{cases_path}: case {name!r} has unknown kind {kind!r}; "
                "coercing to 'command'",
                UserWarning,
                stacklevel=2,
            )
            kind = "command"

        params = case_dict.get("params", {})
        if not isinstance(params, dict):
            warnings.warn(
                f"{cases_path}: case {name!r} 'params' must be a mapping; ignoring",
                UserWarning,
                stacklevel=2,
            )
            params = {}

        cases.append(CaseSpec(
            name=name,
            description=case_dict.get("description", ""),
            kind=kind,
            target=case_dict.get("target", ""),
            labels=resolve_labels(case_dict),
            params=params,
            asserts=case_dict.get("asserts", []),
        ))

    return cases


def _resolve_steps(
    raw_steps: Any,
    spec_dir: Path,
    source_path: Path,
) -> list[FlowStep]:
    """Resolve a ``steps:`` value into a flat list of FlowStep.

    Handles three forms:

    * **String** — ``steps: _flow_foo.yaml``: load steps from that file.
    * **List** — ``steps: [{id: …}, …]``: parse each item; if an item has an
      ``include:`` key, recursively load steps from the referenced file.
    * **None/empty** — returns ``[]``.
    """
    if not raw_steps:
        return []

    # String form: steps: _flow_foo.yaml
    if isinstance(raw_steps, str):
        steps_path = (spec_dir / raw_steps).resolve()
        if not steps_path.is_file():
            return []
        content = _load_yaml(steps_path)
        inner = content if isinstance(content, list) else content.get("steps", [])
        return _resolve_steps(inner, spec_dir, steps_path)

    # List form
    result: list[FlowStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        # include: <path> — pull in steps from another file
        include_path = item.get("include")
        if include_path:
            included = _resolve_steps(include_path, spec_dir, source_path)
            result.extend(included)
        else:
            result.append(_parse_flow_step(item, source_path))
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_server_templates(service: dict[str, Any]) -> None:
    """Render ``{{ env("VAR") }}`` templates in every server ``base_url``.

    If a referenced env var is missing (empty render result) or rendering
    fails, keep the literal template and emit a warning so the
    misconfiguration is visible instead of silently producing an empty
    ``base_url`` that falls back to an unexpected default.
    """
    from cliyard.engine.template import Template

    server_raw = service.get("server", {})
    entries: list[dict[str, Any]] = []
    if isinstance(server_raw, list):
        entries = [e for e in server_raw if isinstance(e, dict)]
    elif isinstance(server_raw, dict):
        entries = [server_raw]
    for entry in entries:
        raw_url = entry.get("base_url")
        if isinstance(raw_url, str) and ("{{" in raw_url or "{%" in raw_url):
            try:
                rendered = Template(raw_url).render()
            except Exception as exc:
                warnings.warn(
                    f"base_url template {raw_url!r} failed to render: {exc}; "
                    "keeping the literal template",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            if not rendered.strip():
                warnings.warn(
                    f"base_url template {raw_url!r} rendered empty "
                    "(referenced env var not set?); keeping the literal template",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            entry["base_url"] = rendered


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read and parse a YAML file with safe_load."""
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML must parse to a mapping (dict), got {type(data).__name__}")

    return data


def _parse_flow_step(step_dict: dict[str, Any], flows_path: Path) -> FlowStep:
    """Parse a single flow step from a YAML dict into a FlowStep dataclass.

    Args:
        step_dict: Raw dict parsed from YAML.
        flows_path: Path to _flows.yaml (for error messages).

    Returns:
        A :class:`FlowStep` instance.

    Raises:
        ValueError: If the step is missing a required ``id``.
    """
    step_id = step_dict.get("id")
    if not step_id:
        raise ValueError(f"{flows_path}: Each step must have an 'id' field")

    for_each = None
    if "for_each" in step_dict:
        fe = step_dict["for_each"]
        for_each = ForEachConfig(
            items=fe["items"],
            as_name=fe["as"],
            steps=[_parse_flow_step(s, flows_path) for s in fe.get("steps", [])],
        )

    retry = None
    if "retry" in step_dict:
        r = step_dict["retry"]
        retry = RetryConfig(
            max_attempts=r.get("max_attempts", 3),
            delay=r.get("delay", 1),
            backoff=r.get("backoff"),
            on_exhausted=r.get("on_exhausted"),
        )

    until = None
    if "until" in step_dict:
        u = step_dict["until"]
        until = UntilConfig(
            max_iterations=u.get("max_iterations", 30),
            interval=u.get("interval", 5),
            condition=u.get("condition", ""),
            timeout_action=u.get("timeout_action", "abort"),
            timeout_message=u.get("timeout_message", ""),
        )

    params = step_dict.get("params", {})
    if step_dict.get("type") == "echo" and "message" in step_dict:
        if isinstance(params, dict):
            params = {**params, "message": step_dict["message"]}
        else:
            params = {"message": step_dict["message"]}

    return FlowStep(
        id=step_id,
        description=step_dict.get("description", ""),
        use=step_dict.get("use", ""),
        params=params,
        extract=step_dict.get("extract"),
        on_result=step_dict.get("on_result"),
        on_failure=step_dict.get("on_failure"),
        assert_=step_dict.get("assert"),
        for_each=for_each,
        retry=retry,
        until=until,
        hooks=step_dict.get("hooks"),
        type=step_dict.get("type", ""),
        show_response=step_dict.get("show_response", False),
    )


def _is_resource_file(path: Path) -> bool:
    """Check if a YAML file is a resource file (not config files)."""
    name = path.name
    if name == "_auth.yaml":
        return False
    if name.startswith("_service.") and name.endswith(".yaml"):
        return False
    if name.startswith("_"):
        return False  # _groups.yaml, _other config files
    return True


def _register_plugins(plugins_config: dict[str, Any]) -> None:
    """Register plugins from a YAML ``plugins:`` section.

    Expected format::

        plugins:
          auth:
            my_oauth: mypackage.auth.MyOAuthStep
          types:
            email: mypackage.validators.EmailType
          hooks:
            add_timestamp: mypackage.hooks.add_timestamp

    Args:
        plugins_config: Dict parsed from the ``plugins:`` key in ``_auth.yaml``.
    """
    if not plugins_config:
        return

    from cliyard.plugin import PluginRegistry

    category_registry_map = {
        "auth": PluginRegistry._auth_steps,
        "types": PluginRegistry._field_types,
        "hooks": PluginRegistry._hooks,
    }

    for category, items in plugins_config.items():
        registry_attr = category_registry_map.get(category)
        if registry_attr is None:
            continue

        for name, import_path in items.items():
            try:
                module_path, attr_name = import_path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                attr = getattr(module, attr_name)
                registry_attr[name] = attr
            except Exception as e:
                import sys
                print(
                    f"Warning: failed to load plugin {name!r} "
                    f"({import_path!r}): {e}",
                    file=sys.stderr,
                )
