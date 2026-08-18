"""Auth chain engine for cliyard.

Executes multi-step authentication flows defined in YAML specs:

- ``env`` step reads credentials from environment variables.
- ``login`` step sends an HTTP request and extracts values via JSONPath.
- ``inject`` step attaches an extracted value to the HTTP client's default headers.

Step config templates (endpoint, body, headers, etc.) are rendered via Jinja2
with access to ``auth_state`` (accumulated step results) and an ``env()``
function for reading environment variables.
"""

from __future__ import annotations

import os
import time
from typing import Any

import jsonpath_ng
from jsonpath_ng import parse as jp_parse
from jinja2 import Environment, BaseLoader, StrictUndefined, UndefinedError

from cliyard.engine.errors import AuthError


# ---------------------------------------------------------------------------
# Jinja2 environment for template rendering (no filesystem — only inline)
# ---------------------------------------------------------------------------

_jinja_env = Environment(loader=BaseLoader(), undefined=StrictUndefined)


def _jinja_env_func(name: str) -> str:
    """Jinja2 ``env()`` helper — reads from OS environment.

    Usage in YAML::

        {{ env("KETA_USER") }}

    Raises:
        AuthError: If the variable is not set.
    """
    value = os.environ.get(name)
    if value is None:
        raise AuthError(f"Environment variable {name!r} is not set (via env() in template)")
    return value


def _build_template_context(auth_state: dict[str, Any]) -> dict[str, Any]:
    """Build a Jinja2 template context from the current auth state.

    In addition to ``auth_state`` (full dict), top-level auth_state keys that
    are plain strings are also exposed as direct variables for backward
    compatibility with simple ``{{ step_name }}`` references.
    """
    ctx: dict[str, Any] = {
        "auth_state": auth_state,
        "env": _jinja_env_func,
        "time": time,
    }
    for key, value in auth_state.items():
        if isinstance(value, str):
            ctx[key] = value
    return ctx


def _render_template(value: Any, template_context: dict[str, Any]) -> Any:
    """Render a config value through Jinja2 if it is a string containing ``{{``.

    Returns the original value unchanged for non-strings or strings without
    Jinja2 tags.  Falls back to the original string on template errors
    (e.g. when a step references a value in a later step's template).
    """
    if not isinstance(value, str):
        return value
    if "{{" not in value:
        return value
    try:
        template = _jinja_env.from_string(value)
        return template.render(**template_context)
    except UndefinedError:
        return value
    except Exception as exc:
        raise AuthError(f"Template rendering failed: {exc}") from exc


def _deep_render(obj: Any, template_context: dict[str, Any]) -> Any:
    """Recursively render all string values in a nested structure."""
    if isinstance(obj, str):
        return _render_template(obj, template_context)
    if isinstance(obj, dict):
        return {k: _deep_render(v, template_context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_render(item, template_context) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Token cache (in-memory, session-scoped)
# ---------------------------------------------------------------------------


class TokenCache:
    """In-memory token cache with TTL.

    Tokens are stored in memory only (not persisted to disk) for security.
    Expired tokens are automatically cleaned up on access.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Store token with TTL in seconds.

        Args:
            key: Cache key (typically step name + endpoint).
            value: Token value to cache.
            ttl: Time-to-live in seconds. Defaults to 3600 (1 hour).
        """
        self._cache[key] = (value, time.time() + ttl)

    def get(self, key: str) -> Any | None:
        """Get token if not expired. Returns None if expired or missing.

        Args:
            key: Cache key.

        Returns:
            Cached token value, or None if expired/missing.
        """
        if key in self._cache:
            value, expires_at = self._cache[key]
            if time.time() < expires_at:
                return value
            del self._cache[key]  # Clean up expired
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_auth_chain(
    auth_spec: dict[str, Any],
    http_client: Any = None,
    cache: TokenCache | None = None,
    pre_filled: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute auth chain steps in order.

    Each step produces a named value stored in ``auth_state``.  Step config
    templates are rendered via Jinja2 with ``auth_state`` and ``env()`` in
    the template context, enabling cross-step references.

    Args:
        auth_spec: Auth chain spec with ``steps`` key.  Example::

            {
                "steps": [
                    {
                        "name": "login_step",
                        "type": "login",
                        "config": {
                            "endpoint": "/api/v1/account/login",
                            "method": "POST",
                            "body": {
                                "username": "{{ env('KETA_USER') }}",
                                "password": "{{ env('KETA_PASS') }}",
                            },
                        },
                        "extract": {"csrf_token": "$.X-Csrf-Token"},
                    },
                    {
                        "name": "create_token",
                        "type": "login",
                        "config": {
                            "endpoint": "/api/v1/auth/tokens",
                            "method": "POST",
                            "headers": {
                                "X-Csrf-Token": "{{ auth_state.login_step.csrf_token }}",
                            },
                            "body": {"username": "{{ env('KETA_USER') }}"},
                        },
                        "extract": {"token": "$.token", "ttl": "$.expires_in"},
                    },
                    {
                        "name": "inject",
                        "type": "inject",
                        "config": {
                            "into": "header",
                            "name": "Authorization",
                            "prefix": "Bearer ",
                            "source": "create_token",
                            "field": "token",
                        },
                    },
                ]
            }
        http_client: Object with ``.request(method, url, ...) -> response``
            and ``.default_headers: dict``.  Required for ``login`` and
            ``inject`` steps.
        cache: Optional TokenCache for caching login tokens within a session.
        pre_filled: Optional dict of pre-populated auth_state values
            (e.g. loaded from saved credentials). Steps whose names match
            keys in pre_filled are skipped.

    Returns:
        ``auth_state`` dict mapping step names to their resolved values.
        For ``env`` steps the value is a string; for ``login`` steps the
        value is a dict of extracted fields.

    Raises:
        AuthError: When an ``env`` variable is missing, a ``login`` fails,
            or ``http_client`` is required but not provided.
    """
    auth_state: dict[str, Any] = {}
    if pre_filled:
        auth_state.update(pre_filled)

    steps: list[dict[str, Any]] = auth_spec.get("steps", [])

    for step in steps:
        step_type: str = step.get("type", "")
        name: str = step.get("name", "")
        config_raw: dict[str, Any] = step.get("config", {})

        # Support extract at both step level (new) and inside config (legacy)
        extract_raw: dict[str, str] | None = (
            step.get("extract") or config_raw.get("extract")
        )

        # Skip steps whose result is already pre-filled
        # Check BEFORE template rendering to avoid missing env vars
        if pre_filled and name in pre_filled:
            # Still need to update auth_state with pre_filled values
            if isinstance(pre_filled[name], dict):
                auth_state.setdefault(name, {}).update(pre_filled[name])
            else:
                auth_state[name] = pre_filled[name]
            # For plugin steps: inject pre-filled value into http_client
            if step_type.startswith("plugin:") and http_client is not None:
                val = pre_filled[name]
                if isinstance(val, str):
                    http_client.default_headers.setdefault("Authorization", f"Bearer {val}")
            continue

        # If any step is pre-filled (credentials already available),
        # skip all env and login steps — they're only needed for auth
        if pre_filled and step_type in ('env', 'login'):
            continue

        # Build template context with auth_state and env() helper
        template_context = _build_template_context(auth_state)

        # Render config templates (and extract too)
        config = _deep_render(config_raw, template_context)
        extract = _deep_render(extract_raw, template_context) if extract_raw is not None else None

        # Skip steps whose result is already pre-filled
        if pre_filled and name in pre_filled:
            continue

        if step_type == "env":
            _handle_env_step(name, config, auth_state)
        elif step_type == "login":
            _handle_login_step(name, config, extract, http_client, auth_state, cache)
        elif step_type == "inject":
            _handle_inject_step(name, config, http_client, auth_state)
        elif step_type.startswith("plugin:"):
            _handle_plugin_step(
                step_type, name, config, extract, http_client, auth_state, cache
            )
        else:
            raise AuthError(f"Unknown auth step type: {step_type!r}")

    return auth_state


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


def _handle_env_step(
    name: str,
    config: dict[str, Any],
    auth_state: dict[str, Any],
) -> None:
    """Read a value from an environment variable and store it."""
    env_name: str | None = config.get("name")
    if not env_name:
        raise AuthError(f"env step {name!r}: config.name must not be empty")

    value = os.environ.get(env_name)
    if value is None:
        raise AuthError(f"Environment variable {env_name!r} is not set")

    auth_state[name] = value


def _handle_login_step(
    name: str,
    config: dict[str, Any],
    extract: dict[str, str] | None,
    http_client: Any,
    auth_state: dict[str, Any],
    cache: TokenCache | None = None,
) -> None:
    """Send an HTTP login request and extract values via JSONPath.

    All keys in ``extract`` are treated as ``field_name → JSONPath`` mappings.
    Extracted values are stored as a dict in ``auth_state[name]``.

    If ``extract`` contains a key named ``ttl``, it is used as the token
    cache TTL (in seconds) rather than being included in the extracted dict.

    If a ``TokenCache`` is provided and the step config includes ``extract.ttl``,
    the entire extracted dict is cached for reuse within the session.
    """
    if http_client is None:
        raise AuthError(
            f"login step {name!r} requires an http_client, but none was provided"
        )

    # Check in-memory cache first
    if cache is not None and extract is not None:
        cache_key = f"{name}:{config.get('endpoint', '')}"
        cached = cache.get(cache_key)
        if cached is not None:
            # Apply single-field unwrapping for backward compat
            if isinstance(cached, dict) and len(cached) == 1:
                auth_state[name] = list(cached.values())[0]
            else:
                auth_state[name] = cached
            return

    if not extract:
        # Distinguish empty dict (legacy: missing token) from None
        if isinstance(extract, dict):
            raise AuthError(
                f"login step {name!r}: config.extract.token must not be empty"
            )
        raise AuthError(
            f"login step {name!r}: config.extract must not be empty "
            f"(need at least one field mapping)"
        )

    method: str = config.get("method", "POST")
    endpoint: str | None = config.get("endpoint")
    if not endpoint:
        raise AuthError(f"login step {name!r}: config.endpoint must not be empty")

    body = config.get("body")
    query = config.get("query")
    headers = config.get("headers")

    # Only pass headers if present (backward-compat with mock clients)
    request_kwargs: dict[str, Any] = {"method": method, "url": endpoint}
    if body is not None:
        request_kwargs["data"] = body
    if query is not None:
        request_kwargs["query_params"] = query
    if headers is not None:
        request_kwargs["headers"] = headers

    resp = http_client.request(**request_kwargs)

    try:
        resp_body = resp.json()
    except Exception as exc:
        raise AuthError(
            f"login step {name!r}: failed to parse response as JSON: {exc}"
        ) from exc

    # Extract all fields from the extract dict
    extracted: dict[str, Any] = {}
    ttl: int | None = None

    for field_name, json_path in extract.items():
        # TTL is special: used for cache expiry AND stored as a field
        if field_name == "ttl":
            try:
                ttl = int(json_path) if isinstance(json_path, (int, float)) else None
            except (ValueError, TypeError):
                pass
            if ttl is None and isinstance(json_path, str) and not json_path.lstrip("-").isdigit():
                # JSONPath expression — extract it
                try:
                    jp_expr = jp_parse(json_path)
                    match = jp_expr.find(resp_body)
                    if match:
                        ttl = int(match[0].value)
                        extracted[field_name] = match[0].value
                except Exception:
                    pass
            continue

        jsonpath_expr = jp_parse(json_path)
        match = jsonpath_expr.find(resp_body)
        if not match:
            raise AuthError(
                f"login step {name!r}: JSONPath {json_path!r} did not match "
                f"any value in response (field: {field_name!r})"
            )

        extracted[field_name] = match[0].value

    # Backward-compat: unwrap single-field extracts ONLY if field is "token".
    # Named fields (like "csrf_token") must remain as dict for cross-step refs.
    non_ttl_fields = [k for k in extract if k != "ttl"]
    if len(non_ttl_fields) == 1 and non_ttl_fields[0] == "token":
        auth_state[name] = list(extracted.values())[0]
    else:
        auth_state[name] = extracted

    # Cache the extracted result
    if cache is not None and ttl is not None and ttl > 0:
        cache_key = f"{name}:{endpoint}"
        cache.set(cache_key, extracted, ttl=ttl)


def _handle_inject_step(
    name: str,
    config: dict[str, Any],
    http_client: Any,
    auth_state: dict[str, Any],
) -> None:
    """Inject a previously-resolved value into the HTTP client's default headers.

    Supports two resolution modes:

    1. **Named-step mode** (backward-compatible): The inject step's ``name``
       matches a previous step name.  If ``auth_state[name]`` is a dict,
       ``config.field`` selects which key to use (default: ``"token"``).
       If it is a plain string, it is used directly.

    2. **Source mode** (new): ``config.source`` specifies which step name
       to reference, and ``config.field`` selects the key.
    """
    if http_client is None:
        raise AuthError(
            f"inject step {name!r} requires an http_client, but none was provided"
        )

    target: str = config.get("into", "header")
    header_name: str | None = config.get("name")
    if not header_name:
        raise AuthError(f"inject step {name!r}: config.name must not be empty")

    prefix: str = config.get("prefix", "")

    # Resolve source: config.source overrides step's own name
    source: str = config.get("source", name)
    field: str = config.get("field", "token")

    raw_value = auth_state.get(source)

    if raw_value is None:
        raise AuthError(
            f"inject step {name!r}: source {source!r} not found in auth_state"
        )

    if isinstance(raw_value, dict):
        value = raw_value.get(field, "")
    else:
        value = raw_value

    if target == "header":
        if not hasattr(http_client, "default_headers"):
            raise AuthError(
                f"inject step {name!r}: http_client does not have "
                f"a default_headers attribute"
            )
        http_client.default_headers[header_name] = f"{prefix}{value}"
    elif target == "query":
        # Future: inject into query params
        pass
    else:
        raise AuthError(
            f"inject step {name!r}: unknown inject target {target!r}"
        )


def _handle_plugin_step(
    step_type: str,
    name: str,
    config: dict[str, Any],
    extract: dict[str, str] | None,
    http_client: Any,
    auth_state: dict[str, Any],
    cache: TokenCache | None = None,
) -> None:
    """Execute a custom auth step registered as a plugin.

    The step type format is ``plugin:<name>``.  The plugin name is extracted,
    looked up in the PluginRegistry, and its ``execute()`` method is called.

    Args:
        step_type: Full step type string (e.g. ``plugin:my_oauth``).
        name: Step name for storing result in ``auth_state``.
        config: Step configuration dict.
        extract: Optional extraction mapping (passed to the plugin).
        http_client: HTTP client object.
        auth_state: Accumulated auth state dictionary.
        cache: Optional token cache for session-level caching.
    """
    # Extract plugin name from "plugin:<name>"
    plugin_name = step_type[7:]

    from cliyard.plugin import PluginRegistry
    from cliyard.plugin.discovery import discover_plugins

    discover_plugins()
    step_class = PluginRegistry.get_auth_step(plugin_name)
    if step_class is None:
        raise AuthError(f"Auth plugin {plugin_name!r} not found")

    instance = step_class() if isinstance(step_class, type) else step_class
    result = instance.execute(auth_state, config, http_client)
    auth_state[name] = result
