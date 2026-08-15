"""Plugin registry for cliyard extensions.

Extension points:
- auth: Custom authentication step types
- types: Custom field types (validation + conversion)
- hooks: Custom pre/post request processing hooks
- methods: Custom business logic methods (multi-step API calls)
- commands: Custom top-level Click commands
- field_resolvers: Dynamic field value resolvers (e.g. get latest version)
- steps: Custom step types for flow orchestration
- output_formats: Custom output formatters (e.g. "yaml")
"""

from __future__ import annotations

from typing import Any, Callable


class PluginRegistry:
    """Central registry for all plugin types."""

    _auth_steps: dict[str, type] = {}
    _field_types: dict[str, type] = {}
    _hooks: dict[str, Callable] = {}
    _methods: dict[str, Callable] = {}
    _commands: dict[str, Callable] = {}
    _field_resolvers: dict[str, Callable] = {}
    _step_types: dict[str, Callable] = {}
    _output_formats: dict[str, Callable] = {}
    _loaded: bool = False

    @classmethod
    def register_auth_step(cls, name: str, step_class: type) -> None:
        cls._auth_steps[name] = step_class

    @classmethod
    def register_field_type(cls, name: str, type_class: type) -> None:
        cls._field_types[name] = type_class

    @classmethod
    def register_hook(cls, name: str, hook_fn: Callable) -> None:
        cls._hooks[name] = hook_fn

    @classmethod
    def register_method(cls, name: str, method_fn: Callable) -> None:
        """Register a custom business logic method.

        The function receives ``(params, http_client, config)`` and returns
        a dict that will be formatted as JSON output.

        Usage in YAML::

            methods:
              complex_task:
                type: plugin:my_method
                config:
                  key: value
                params:
                  body:
                    - name: input
                      type: string
        """
        cls._methods[name] = method_fn

    @classmethod
    def register_field_resolver(cls, name: str, resolver_fn: Callable) -> None:
        """Register a field resolver plugin.

        The function receives ``(params, http_client, config)`` and returns
        the resolved value for the field.

        Usage in YAML::

            params:
              body:
                - name: version
                  type: string
                  resolver: plugin:get_latest_version
                  description: 应用版本号
        """
        cls._field_resolvers[name] = resolver_fn

    @classmethod
    def register_command(cls, name: str, command_fn: Callable) -> None:
        """Register a custom top-level Click command builder.

        The function receives ``(cli, ctx)`` where *cli* is the top-level
        ``click.Group`` and *ctx* is the ``ServiceContext``.
        """
        cls._commands[name] = command_fn

    @classmethod
    def register_step_type(cls, name: str, step_fn: Callable) -> None:
        """Register a custom step type for flow orchestration.

        The function receives ``(params: dict, context: Any) -> dict`` and
        returns a dict result.
        """
        cls._step_types[name] = step_fn

    @classmethod
    def register_output_format(cls, name: str, format_fn: Callable) -> None:
        """Register a custom output formatter for the ``--format`` option.

        The function receives ``(data, fields=None)`` and returns a string.
        Built-in formats (table/json/csv/yaml) cannot be overridden; a
        registered name is added to the ``--format`` choices on every
        command.
        """
        cls._output_formats[name] = format_fn

    @classmethod
    def get_auth_step(cls, name: str) -> type | None:
        return cls._auth_steps.get(name)

    @classmethod
    def get_field_type(cls, name: str) -> type | None:
        return cls._field_types.get(name)

    @classmethod
    def get_hook(cls, name: str) -> Callable | None:
        return cls._hooks.get(name)

    @classmethod
    def get_method(cls, name: str) -> Callable | None:
        return cls._methods.get(name)

    @classmethod
    def get_field_resolver(cls, name: str) -> Callable | None:
        return cls._field_resolvers.get(name)

    @classmethod
    def get_command(cls, name: str) -> Callable | None:
        return cls._commands.get(name)

    @classmethod
    def get_step_type(cls, name: str) -> Callable | None:
        return cls._step_types.get(name)

    @classmethod
    def get_output_format(cls, name: str) -> Callable | None:
        return cls._output_formats.get(name)

    @classmethod
    def get_output_formats(cls) -> dict[str, Callable]:
        return dict(cls._output_formats)

    @classmethod
    def get_all_commands(cls) -> dict[str, Callable]:
        return dict(cls._commands)

    @classmethod
    def clear(cls) -> None:
        cls._auth_steps.clear()
        cls._field_types.clear()
        cls._hooks.clear()
        cls._methods.clear()
        cls._commands.clear()
        cls._field_resolvers.clear()
        cls._step_types.clear()
        cls._output_formats.clear()
        cls._loaded = False
        # 失效插件发现缓存：clear() 后允许目录/入口点被重新发现，
        # 否则先前已扫描目录（如 examples/demo/plugins）会因 _scanned_dirs
        # 残留而跳过重扫，导致类型/钩子等注册丢失（函数内导入避免循环依赖）。
        try:
            from cliyard.plugin.discovery import _scanned_dirs

            _scanned_dirs.clear()
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Decorator helpers
# ---------------------------------------------------------------------------


def register_auth_step(name: str):
    def decorator(cls):
        PluginRegistry.register_auth_step(name, cls)
        return cls
    return decorator


def register_field_type(name: str):
    def decorator(cls):
        PluginRegistry.register_field_type(name, cls)
        return cls
    return decorator


def register_hook(name: str):
    def decorator(fn):
        PluginRegistry.register_hook(name, fn)
        return fn
    return decorator


def register_method(name: str):
    """Decorator that registers a function as a custom method plugin.

    Usage::

        @register_method("multi_step_import")
        def multi_step_import(params, http_client, config):
            r1 = http_client.request("POST", "/api/step1", data=params)
            r2 = http_client.request("POST", "/api/step2", json=r1.json())
            return {"result": r2.json()}
    """
    def decorator(fn):
        PluginRegistry.register_method(name, fn)
        return fn
    return decorator


def register_command(name: str):
    """Decorator that registers a function as a top-level command builder."""
    def decorator(fn):
        PluginRegistry.register_command(name, fn)
        return fn
    return decorator


def register_field_resolver(name: str):
    """Decorator that registers a function as a field value resolver.

    The function receives ``(params, http_client, config)`` and returns
    the resolved value.

    Usage in YAML::

        params:
          body:
            - name: version
              type: string
              resolver: plugin:get_latest_version
    """
    def decorator(fn):
        PluginRegistry.register_field_resolver(name, fn)
        return fn
    return decorator


def register_step_type(name: str):
    """Decorator that registers a function as a step type for flow orchestration.

    The function receives ``(params: dict, context: Any) -> dict`` and
    returns a dict result.

    Usage::

        @register_step_type("http_call")
        def http_call(params, context):
            client = context["http_client"]
            resp = client.request("POST", "/api/step", json=params)
            return resp.json()
    """
    def decorator(fn):
        PluginRegistry.register_step_type(name, fn)
        return fn
    return decorator


def register_output_format(name: str):
    """Decorator that registers a function as an output formatter.

    The function receives ``(data, fields=None)`` and returns a string.
    The registered name becomes available to every command's ``--format``
    option (unless it collides with a built-in format).

    Usage::

        @register_output_format("xml")
        def format_as_xml(data, fields=None):
            return xml_to_string(data)
    """
    def decorator(fn):
        PluginRegistry.register_output_format(name, fn)
        return fn
    return decorator
