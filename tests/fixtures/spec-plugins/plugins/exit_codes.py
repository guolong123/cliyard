"""退出码 fixture：验证 SystemExit 隔离。"""

import sys

import click

from cliyard.plugin import register_command


@register_command("exit-boom")
def register_exit_boom(cli, ctx):
    @click.command("exit-boom")
    def exit_boom():
        """以非零码退出。"""
        sys.exit(1)

    cli.add_command(exit_boom)


@register_command("exit-ok")
def register_exit_ok(cli, ctx):
    @click.command("exit-ok")
    def exit_ok():
        """以零码退出。"""
        sys.exit(0)

    cli.add_command(exit_ok)
