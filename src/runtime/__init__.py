"""cliyard.runtime — Runtime pipeline for generated CLIs.

Three usage modes:

1. **Direct CLI** (generated mode)::

       from cliyard.runtime import run_with_spec
       run_with_spec("path/to/spec-dir")

2. **Library mode** (embed in your app)::

       from cliyard.runtime import create_cli
       cli = create_cli("path/to/spec-dir")
       cli()  # runs the CLI, or attach to your own Click app

3. **Headless mode** (no CLI, direct data access)::

       from cliyard.runtime import create_client
       client = create_client("path/to/spec-dir")
       repos = client.get("/api/v1/repos")
"""

from cliyard.runtime.runner import run_with_spec, create_cli

__all__ = ["run_with_spec", "create_cli"]
