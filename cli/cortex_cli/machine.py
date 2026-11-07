"""The machine's settings — ADR-013 §3.7: ``config.toml`` in the Cortex home, and the variables over it.

``releases_url``, where releases are downloaded from; ``update_check``, whether ``cortex upgrade``
says a newer release is out; ``register``, whether ``init``, ``sync`` and ``upgrade`` keep the
registry. The file is optional, and no command writes it. It never stops one: an unknown key, a
value of the wrong type, or a file that cannot be read is a warning naming it, said once, and the
rest is read — ``cortex self-update`` has to run whatever the file holds. ``CORTEX_HOME`` stays a
variable: it says where the file is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Set, Tuple

from cortex_core import cascade
from cortex_core.project import MACHINE_FILE

from .paths import display

_warned: Set[str] = set()


def path(home: Path) -> Path:
    return home / MACHINE_FILE


def read(home: Path) -> Tuple[Dict[str, object], Tuple[str, ...], str]:
    """The keys of the machine's file that read, its warnings, and how an origin names it. The
    warnings are said on standard error, once per process: every command reads the file, some
    more than once."""
    where = path(home)
    name = display(str(where))
    values, warnings = cascade.read_machine(str(where), name)
    for warning in warnings:
        if warning not in _warned:
            _warned.add(warning)
            sys.stderr.write(f"warning: {warning}\n")
    return values, tuple(warnings), name


def resolution(home: Path) -> cascade.Resolution:
    """The machine's keys, each with its origin: a variable, the file, or the default."""
    values, warnings, name = read(home)
    return cascade.resolve(None, None, values, os.environ, machine_name=name, warnings=warnings)


def setting(home: Path, key: str) -> cascade.Setting:
    return resolution(home)[key]
