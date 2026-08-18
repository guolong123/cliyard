"""cliyard.engine.template — Sandboxed Jinja2 template engine.

Uses SandboxedEnvironment to prevent arbitrary code execution in YAML templates.
Only whitelisted filters and functions are available. Template compilation is cached
to avoid recompiling identical templates (pattern from ketacli).

Security model:
- SandboxedEnvironment blocks attribute access on unsafe types
- __builtins__, import, open, exec, eval are NOT injected into context
- Only whitelisted filters are registered
- Only whitelisted global functions are registered
"""

from __future__ import annotations

import datetime as datetime_module
import json
import os
import random as random_module
import re
import time as time_module
from typing import Any

from jinja2 import ChainableUndefined
from jinja2.sandbox import SandboxedEnvironment


# ---------------------------------------------------------------------------
# Template cache — avoid recompiling identical template strings (ketacli pattern)
# ---------------------------------------------------------------------------
_template_cache: dict[str, dict[str, Any]] = {}


class Template:
    """Sandboxed Jinja2 template wrapper.

    Usage::

        t = Template("Hello {{ name }}")
        result = t.render(name="world")
        # result == "Hello world"

        t2 = Template("{{ env('HOME') }}")
        result2 = t2.render()
        # result2 == value of $HOME
    """

    def __init__(self, template_str: str) -> None:
        self.template_str = template_str

        # Check cache first
        if template_str in _template_cache:
            self.env = _template_cache[template_str]["env"]
            self.temp = _template_cache[template_str]["temp"]
        else:
            # Create sandboxed environment — NO builtins injected
            # ChainableUndefined: allows {{ var|default(x) }} to work,
            # but raises on attribute access of undefined variables.
            # finalize: converts list/tuple/dict to JSON string so that
            # {{ var }} renders as JSON array/object, not Python repr.
            self.env = SandboxedEnvironment(
                undefined=ChainableUndefined,
                keep_trailing_newline=True,
                finalize=_finalize_list_tuple,
            )

            # --- Register whitelisted filters only ---
            self.env.filters["default"] = _filter_default
            self.env.filters["env"] = _filter_env
            self.env.filters["upper"] = _filter_upper
            self.env.filters["lower"] = _filter_lower
            self.env.filters["replace"] = _filter_replace
            self.env.filters["join"] = _filter_join
            self.env.filters["length"] = _filter_length
            self.env.filters["first"] = _filter_first
            self.env.filters["last"] = _filter_last
            self.env.filters["tojson"] = _filter_tojson
            self.env.filters["str_to_list"] = _filter_str_to_list
            self.env.filters["split"] = _filter_split
            self.env.filters["re_extract"] = _filter_re_extract

            # --- Register whitelisted global functions ---
            self.env.globals["env"] = _func_env
            self.env.globals["time"] = time_module
            self.env.globals["datetime"] = datetime_module
            self.env.globals["random"] = random_module
            self.env.globals["int"] = int
            self.env.globals["float"] = float
            self.env.globals["str"] = str
            self.env.globals["len"] = len
            self.env.globals["range"] = range
            self.env.globals["None"] = None
            self.env.globals["True"] = True
            self.env.globals["False"] = False

            # Try to register faker (optional dependency)
            try:
                from faker import Faker
                _faker = Faker()
                self.env.globals["faker"] = _faker
            except ImportError:
                pass

            # Compile and cache
            self.temp = self.env.from_string(template_str)
            _template_cache[template_str] = {"env": self.env, "temp": self.temp}

    def render(self, **kwargs: Any) -> str:
        """Render template with given variables.

        Returns:
            Rendered string with variables substituted.

        Raises:
            jinja2.UndefinedError: If a variable is referenced but not provided
                (StrictUndefined mode).
            jinja2.sandbox.SecurityError: If a forbidden operation is attempted
                (e.g., ``open``, ``import``, ``__builtins__``).
        """
        return self.temp.render(**kwargs)

    def batch_render(self, count: int, render: bool = True) -> list[str]:
        """Render the template multiple times, producing *count* independent results.

        Each render picks up new values from globals like ``random`` and ``faker``,
        so each result is independent. This is useful for generating mock data.

        Args:
            count: Number of times to render.
            render: If ``True``, call ``self.temp.render()`` each time.
                If ``False``, just repeat the raw template string.

        Returns:
            List of *count* rendered strings.
        """
        from jinja2 import UndefinedError

        results: list[str] = [None] * count  # type: ignore
        for i in range(count):
            if not render:
                results[i] = self.template_str
            else:
                try:
                    results[i] = self.temp.render()
                except UndefinedError as e:
                    raise RuntimeError(
                        f"Template rendering failed: {self.template_str}. "
                        f"Provide required params. {e}"
                    ) from e
        return results


# ---------------------------------------------------------------------------
# Whitelisted filters
# ---------------------------------------------------------------------------


def _finalize_list_tuple(value: Any) -> Any:
    """Finalize hook: convert list/tuple/dict to JSON string for safe {{ var }} output."""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _filter_default(value: Any, default_value: Any = "") -> Any:
    """Return *default_value* if *value* is undefined/None/empty."""
    from jinja2 import Undefined

    if isinstance(value, Undefined) or value is None or value == "":
        return default_value
    return value


def _filter_env(name: str, default: str = "") -> str:
    """Read an environment variable by name."""
    return os.environ.get(name, default)


def _filter_upper(value: str) -> str:
    return str(value).upper()


def _filter_lower(value: str) -> str:
    return str(value).lower()


def _filter_replace(value: str, old: str, new: str, count: int = -1) -> str:
    if count == -1:
        return str(value).replace(old, new)
    return str(value).replace(old, new, count)


def _filter_join(value: list | tuple, delimiter: str = ", ") -> str:
    return delimiter.join(str(v) for v in value)


def _filter_length(value: Any) -> int:
    return len(value)


def _filter_first(value: list | tuple | str | Any) -> Any:
    """Return the first item of a sequence, or None if empty.

    Handles generators (from filters like selectattr/map) by converting
    to list first. Compatible with Jinja2's built-in first filter.
    """
    if hasattr(value, '__iter__') and not isinstance(value, (str, dict)):
        for item in value:
            return item
        return None
    if isinstance(value, (list, tuple)) and len(value) > 0:
        return value[0]
    return None


def _filter_last(value: list | tuple | str) -> Any:
    return value[-1]


def _filter_tojson(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, indent=indent, ensure_ascii=False)


def _filter_str_to_list(value: str, delimiter: str = ",") -> list[str]:
    """Split a comma-separated string into a list."""
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def _filter_split(value: Any, sep: str | None = None, maxsplit: int = -1) -> list[str]:
    """Split a string into a list, mirroring Python's ``str.split``.

    The sandbox blocks method calls on strings (``msg.split('<br/>')``
    silently degrades to the original text), so this filter restores the
    capability in a whitelisted, safe way::

        {{ msg | split('<br/>') }}   →  ["a", "b"]
        {{ msg | split() }}          →  whitespace split
        {{ msg | split(',', 2) }}    →  maxsplit honored

    Args:
        value: The string to split.
        sep: Separator. ``None`` splits on arbitrary whitespace.
        maxsplit: Maximum number of splits (default: -1 = no limit).
    """
    if sep is None:
        return str(value).split()
    return str(value).split(sep, maxsplit)


def _filter_re_extract(value: Any, pattern: str, group: int = 0) -> str:
    """Extract the first regex match from a string.

    Returns ``""`` when the pattern does not match, so templates degrade
    safely instead of raising.  Useful for pulling ids/tokens out of
    response text::

        {{ msg | re_extract(r'\\d{4}-\\d{2}-\\d{2}') }}  →  "2026-08-11"
        {{ msg | re_extract('code=(\\w+)', 1) }}          →  "HELLO"

    Args:
        value: The string to search.
        pattern: Regular expression (re.search semantics — first match).
        group: Capture group to return (default 0 = whole match).

    Returns:
        The matched substring, or ``""`` if no match (or bad group).
    """
    try:
        match = re.search(pattern, str(value))
    except (re.error, TypeError):
        return ""
    if not match:
        return ""
    try:
        return match.group(group)
    except (IndexError, AttributeError):
        return match.group(0)


# ---------------------------------------------------------------------------
# Whitelisted global functions
# ---------------------------------------------------------------------------


def _func_env(name: str, default: str = "") -> str:
    """Read an environment variable (usable as ``{{ env('VAR') }}``)."""
    return os.environ.get(name, default)
