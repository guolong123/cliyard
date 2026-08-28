"""Plugin discovery: entry points + directory scanning.

Discovers plugins from four sources:
1. Python entry points (``cliyard.auth``, ``cliyard.types``, ``cliyard.hooks``, ``cliyard.steps``)
2. Spec-local plugin directory: ``{spec_dir}/plugins/*.py``
3. CWD hidden dir: ``./.cliyard/plugins/*.py`` (project-local, e.g. repo/.cliyard/plugins)
4. Global plugin directory: ``~/.cliyard/plugins/*.py``
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from cliyard.plugin import PluginRegistry


_scanned_dirs: set[str] = set()


def discover_plugins(spec_dir: str | None = None) -> None:
    """Discover plugins from all sources.

    1. Entry points: ``cliyard.auth``, ``cliyard.types``, ``cliyard.hooks``, ``cliyard.steps``
    2. Spec directory: ``{spec_dir}/plugins/*.py``
    3. CWD hidden dir: ``./.cliyard/plugins/*.py`` (project-local)
    4. Global dir: ``~/.cliyard/plugins/*.py``

    Entry points are scanned once only. Directories are scanned at most once
    each, so subsequent calls with new directories will pick up new files.

    Args:
        spec_dir: Optional path to a spec directory whose ``plugins/``
            subdirectory should be scanned.
    """
    global _scanned_dirs
    if not _scanned_dirs:
        _discover_entry_points()
        PluginRegistry._loaded = True

    if spec_dir:
        _scan_if_new(Path(spec_dir) / "plugins")
        _scan_if_new(Path(spec_dir).parent / "plugins")
    _scan_if_new(Path.cwd() / ".cliyard" / "plugins")
    _scan_if_new(Path.home() / ".cliyard" / "plugins")


def _scan_if_new(plugins_dir: Path) -> None:
    """Scan *plugins_dir* if it hasn't been scanned before."""
    global _scanned_dirs
    key = str(plugins_dir.resolve())
    if key in _scanned_dirs:
        return
    _scanned_dirs.add(key)
    _discover_directory(plugins_dir)


def _discover_entry_points() -> None:
    """Discover plugins via Python entry points.

    Uses Python's ``importlib.metadata`` entry_points API (PEP 621 / setuptools
    entry point groups). Each entry point's value should be a fully-qualified
    import path to the plugin class/function.

    Group mapping:
        ``cliyard.auth``  → PluginRegistry._auth_steps
        ``cliyard.types`` → PluginRegistry._field_types
        ``cliyard.hooks`` → PluginRegistry._hooks
        ``cliyard.steps`` → PluginRegistry._step_types
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return  # Python < 3.9 — entry_points() not available

    for group_name, registry_attr in [
        ("cliyard.auth", "_auth_steps"),
        ("cliyard.types", "_field_types"),
        ("cliyard.hooks", "_hooks"),
        ("cliyard.steps", "_step_types"),
    ]:
        try:
            eps = entry_points(group=group_name)
            for ep in eps:
                try:
                    obj = ep.load()
                    getattr(PluginRegistry, registry_attr)[ep.name] = obj
                except Exception:
                    # Silently skip plugins that fail to load
                    pass
        except Exception:
            # entry_points(group=...) raises TypeError in Python < 3.12
            # when the group is not found; TypeErrors are also acceptable
            # for missing optional dependencies
            pass


def _discover_directory(plugins_dir: Path) -> None:
    """Scan a directory for ``.py`` plugin files and import them."""
    if not plugins_dir.is_dir():
        return

    # Add plugins dir to sys.path so subdirectory packages can be imported
    plugins_dir_str = str(plugins_dir)
    if plugins_dir_str not in sys.path:
        sys.path.insert(0, plugins_dir_str)

    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_") or py_file.name == "setup.py":
            continue
        module_name = py_file.stem
        try:
            # Import via file location — plugins dir is not on the Python path
            spec = importlib.util.spec_from_file_location(
                module_name, py_file
            )
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                sys.modules.setdefault(module_name, mod)
                spec.loader.exec_module(mod)
        except Exception:
            pass
