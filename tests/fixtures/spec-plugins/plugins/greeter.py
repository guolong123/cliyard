"""Command-level plugins for MCP spec-plugins fixture.

Mirrors the real ketacli pattern (search/skills): one top-level command
(``hello`` with argument + options) and one command group (``pkg`` with
subcommands) to cover both flat and nested registration.
"""

import click
from rich.console import Console

from cliyard.plugin import register_command

console = Console()


@register_command("hello")
def register_hello(cli, ctx):
    @click.command("hello")
    @click.argument("name", required=True)
    @click.option("-g", "--greeting", default="Hello", help="Greeting word")
    @click.option("-u", "--uppercase", is_flag=True, help="Uppercase output")
    def hello(name, greeting, uppercase):
        """Greet someone with a customizable greeting."""
        msg = f"{greeting}, {name}!"
        if uppercase:
            msg = msg.upper()
        console.print(msg)

    cli.add_command(hello)


@register_command("pkg")
def register_pkg(cli, ctx):
    @click.group("pkg")
    def pkg_group():
        """Package management demo group."""

    @pkg_group.command("info")
    @click.argument("package_name")
    @click.option("-v", "--verbose", is_flag=True, help="Show verbose info")
    def pkg_info(package_name, verbose):
        """Show package info."""
        console.print(f"pkg {package_name} verbose={verbose}")

    @pkg_group.command("search")
    @click.argument("keywords", nargs=-1, required=False)
    @click.option("-n", "--limit", type=int, default=10, help="Max results")
    def pkg_search(keywords, limit):
        """Search packages by keywords."""
        keys = list(keywords) or ["*"]
        console.print(f"search {keys} limit={limit}")

    cli.add_command(pkg_group)
