"""cliyard CLI entry point."""

import sys

import click
from click.exceptions import NoArgsIsHelpError, MissingParameter, UsageError


def _intercept_spec_dir() -> None:
    """If ``--spec-dir`` is in sys.argv, extract it and run the spec-based CLI.

    This must happen *before* Click's command resolution because the
    spec-driven commands (e.g. ``repos list``) don't exist as registered
    Click subcommands — they are built dynamically from YAML specs.

    Does NOT intercept if the command is ``auth``, ``gen``, ``init``, or ``run``
    — those are native cliyard commands, not spec-driven commands.
    """
    if "--spec-dir" not in sys.argv:
        return

    # Skip interception for native cliyard commands
    for arg in sys.argv[1:]:
        if not arg.startswith("-") and arg in ("auth", "gen", "init", "run", "serve", "usage", "mcp"):
            return
        if arg.startswith("-"):
            continue
        break

    try:
        idx = sys.argv.index("--spec-dir")
    except ValueError:
        return

    if idx + 1 >= len(sys.argv):
        click.echo("Error: --spec-dir requires a directory path", err=True)
        sys.exit(1)

    spec_dir = sys.argv[idx + 1]

    sys.argv.pop(idx)
    sys.argv.pop(idx)

    from cliyard.runtime.runner import run_with_spec

    run_with_spec(spec_dir)


@click.group()
@click.version_option()
def cli():
    """cliyard — YAML-driven CLI framework for any REST API.

    Turn any REST API into CLI commands by writing simple YAML specs.
    """


@cli.command()
def init():
    """Initialize a new cliyard spec directory."""
    click.echo("Not yet implemented. Stay tuned.")


from cliyard.cli.gen import gen

cli.add_command(gen)

from cliyard.cli.usage import usage

cli.add_command(usage)

from cliyard.cli.serve import serve

cli.add_command(serve)

from cliyard.cli.mcp import mcp

cli.add_command(mcp)


@cli.command()
@click.argument("spec_file")
@click.argument("resource")
@click.argument("operation", required=False)
@click.option("-e", "--extra", type=str, help="Extra args, key=val,key2=val2")
def run(spec_file, resource, operation, extra):
    """Run a command from a YAML spec file."""
    click.echo(f"Not yet implemented. Would run: {spec_file} {resource} {operation}")


def main():
    """Entry point — intercept --spec-dir before Click, then run CLI."""
    _intercept_spec_dir()
    try:
        cli(standalone_mode=False)
    except MissingParameter as e:
        click.echo(f"Error: {e}", err=True)
        click.echo(f"  Usage: cliyard gen --name <name> [--defs-path <path>]")
    except UsageError as e:
        click.echo(f"Error: {e}", err=True)
    except NoArgsIsHelpError as e:
        click.echo(e.format_message())
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
