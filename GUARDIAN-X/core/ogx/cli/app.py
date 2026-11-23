"""Root argument parser and entry point (`ogx` / `python -m ogx`)."""
from __future__ import annotations

import argparse
import sys

from ogx import __version__
from ogx.kernel.envfile import EnvFileError, load_runtime_env
from ogx.cli.common import BANNER


from ogx.cli.commands import setup
from ogx.cli.commands import analysis
from ogx.cli.commands import supply_chain
from ogx.cli.commands import devtools
from ogx.cli.commands import ecosystem
from ogx.cli.commands import vcs
from ogx.cli.commands import protection
from ogx.cli.commands import governance
from ogx.cli.commands import runtime

GROUPS = (setup, analysis, supply_chain, devtools, ecosystem, vcs, protection, governance, runtime,)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ogx", description=BANNER)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for group in GROUPS:
        group.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # `env init` and `env path` must remain usable even when an existing env file is broken.
    if not (args.command == "env" and args.env_action in {"init", "path"}):
        try:
            load_runtime_env()
        except EnvFileError as exc:
            print(f"Environment configuration failed: {exc}", file=sys.stderr)
            return 2
    return args.func(args)
