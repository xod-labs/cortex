"""The ``cortex`` command line (ADR-008).

The command behaves the same under either of its names, ``cortex`` or ``cortex-ai`` (§3.1):
nothing here reads the name it was called by.
"""

from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import console
from .version import VERSION

# name -> (summary, handler). Handlers import their module when called — statically, so that
# PyInstaller sees every module the binary needs.
def _validate(args: List[str]) -> int:
    from . import validate

    return validate.run(args)


def _init(args: List[str]) -> int:
    from . import init

    return init.run(args)


def _sync(args: List[str]) -> int:
    from . import sync

    return sync.run(args)


def _prune(args: List[str]) -> int:
    from . import prune

    return prune.run(args)


def _upgrade(args: List[str]) -> int:
    from . import upgrade

    return upgrade.run(args)


def _project(args: List[str]) -> int:
    from . import registry

    return registry.run(args)


def _self_update(args: List[str]) -> int:
    from . import self_update

    return self_update.run(args)


COMMANDS: Dict[str, Tuple[str, Callable[[List[str]], int]]] = {
    "init": ("Make a directory a Cortex project: cortex.toml, the AI tool's instructions", _init),
    "sync": ("Put the pinned Cortex version in the store, and tell the project where it is", _sync),
    "upgrade": ("Move the project to another Cortex version, saying first what changes", _upgrade),
    "self-update": ("Replace this cortex with a release's binary — the latest, by default", _self_update),
    "prune": ("Remove from the store the versions no project under the directories given pins", _prune),
    "project": ("Add a project to this machine's registry, or remove one", _project),
    "validate": ("Check the project's overlays against the spec (ADR-001)", _validate),
}


def known_specs_report() -> str:
    """The spec archives this binary knows the checksum of (ADR-008 §9): a download of one of
    them is refused when its release says otherwise."""
    from . import store

    known = store.known_specs()
    if not known:
        return "known spec archives: 0 — a download is checked against its release's SHA256SUMS alone\n"
    return f"known spec archives: {len(known)}\n" + "".join(f"  {version}  {digest}\n"
                                                          for version, digest in sorted(known.items()))


def usage() -> str:
    commands = "".join(f"  {name:<12} {summary}\n" for name, (summary, _) in COMMANDS.items())
    return ("Usage: cortex <command> [options]\n\n"
            + (f"Commands:\n{commands}\n" if commands else "")
            + "Options:\n"
              "  -V, --version   Print the version and exit — with --verbose, the spec archives it knows\n"
              "  -h, --help      Show this help and exit\n\n"
              "`cortex <command> --help` shows the options of a command.\n")


def main(argv: Optional[List[str]] = None) -> int:
    console.setup()
    if getattr(sys, "frozen", False):
        from . import binary

        binary.tidy()           # what a self-update left: the binary renamed aside, a new one never installed
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        sys.stderr.write(usage())
        return 2
    command, rest = args[0], args[1:]
    if command in ("-h", "--help"):
        sys.stdout.write(usage())
        return 0
    if command in ("-V", "--version"):
        unknown = [arg for arg in rest if arg not in ("-v", "--verbose")]
        if unknown:
            sys.stderr.write(f"cortex: unknown option '{unknown[0]}' for --version\n")
            return 2
        sys.stdout.write(f"cortex {VERSION}\n")
        if rest:
            sys.stdout.write(known_specs_report())
        return 0
    if command not in COMMANDS:
        sys.stderr.write(f"cortex: unknown command '{command}'\n\n{usage()}")
        return 2
    sys.stdout.flush()
    return COMMANDS[command][1](rest)
