"""``cortex prune`` — remove from the store the versions no project uses (ADR-010 §3.6).

``cortex prune DIR... [--keep VERSION]... [--dry-run]`` finds the projects under each ``DIR`` —
every ``cortex.toml`` up to six directories down, past ``.git``, ``node_modules``, ``vendor``,
``.venv`` and the ``cortex/`` a link or copy sync made — keeps the versions they pin, this
binary's own and each ``--keep``, and removes every other version from the store.

Nothing lists the projects of a machine (ADR-008 §3.3): the user says where they are, and a
project elsewhere loses its version. It gets it back at its next ``cortex sync``, checked against
its ``cortex.lock``. A version is removed by a rename first, so that a sync running meanwhile finds
it whole or absent. The runtime reads the store too: a version only it uses is kept with ``--keep``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterator, List, TextIO

from cortex_core.project import shown

from . import config, store, sync
from .paths import display, working_directory
from .semver import Version

DEPTH = 6
SKIPPED = (".git", "node_modules", "vendor", ".venv")


class PruneError(Exception):
    pass


def projects(directory: Path) -> Iterator[Path]:
    """The directories holding a ``cortex.toml`` under ``directory``, ``DEPTH`` levels down at most."""
    for root, subdirs, files in os.walk(directory):
        here = Path(root)
        if config.PROJECT_FILE in files:
            yield here
        depth = len(here.relative_to(directory).parts)
        subdirs[:] = [] if depth >= DEPTH else [
            name for name in subdirs
            if name not in SKIPPED and not (name == sync.LINK and (sync.is_link(here / name) or (here / name / sync.MARKER).is_file()))]


def stored(the_store: store.Store) -> List[Version]:
    """The versions in the store: its complete version directories, stagings left out."""
    if not the_store.versions.is_dir():
        return []
    found = [Version(entry.name) for entry in the_store.versions.iterdir()
             if not entry.name.startswith(".") and Version.valid(entry.name)]
    return sorted(version for version in found if the_store.has(version))


def prune(directories: List[str], keep: List[str], dry_run: bool, out: TextIO, err: TextIO) -> int:
    directories = [os.path.join(working_directory(), directory) for directory in directories]   # an absolute one stays
    for directory in directories:
        if not os.path.isdir(directory):
            raise PruneError(f"{display(directory)}: no such directory")
    the_store = store.Store()
    pinned: Dict[Version, List[str]] = {}
    found = 0
    for directory in directories:
        for root in projects(Path(os.path.abspath(directory))):
            found += 1
            try:
                pinned.setdefault(config.load(str(root)).version, []).append(display(str(root)))
            except config.ConfigError as error:
                err.write(f"note: {display(str(root))}: {error} — its version is not known, so none is kept for it\n")
    kept = {version: f"pinned by {', '.join(roots)}" for version, roots in pinned.items()}
    kept.setdefault(the_store.own, "this cortex's own")
    for version in keep:
        kept.setdefault(Version(version), "--keep")
    out.write(f"{found} project(s) found under {', '.join(display(d) for d in directories)}\n")
    versions = stored(the_store)
    removed = [version for version in versions if version not in kept]
    for version in versions:
        if version in kept:
            out.write(f"  kept     {version} — {kept[version]}\n")
    for version in removed:
        if not dry_run:
            the_store.remove(version)
        out.write(f"  {'would remove' if dry_run else 'removed'}  {version}\n")
    if not removed:
        out.write("Nothing to remove.\n")
    elif dry_run:
        out.write("--dry-run: nothing was removed\n")
    else:
        out.write("A project elsewhere gets its version back at its next `cortex sync`.\n")
    return 0


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex prune",
        description="Remove from the store every version that no project under DIR pins — this cortex's own, and "
                    "each --keep, kept. ADR-010 §3.6.")
    parser.add_argument("directories", nargs="+", metavar="DIR", help="where the projects of this machine are")
    parser.add_argument("--keep", action="append", default=[], metavar="VERSION",
                        help="a version to keep, which no project pins — one the runtime uses, say")
    parser.add_argument("--dry-run", action="store_true", help="say what would be removed, and remove nothing")
    options = parser.parse_args(args)
    for version in options.keep:
        if not Version.valid(version):
            return sync.failed("cortex prune", f"--keep {shown(version)} is no version — X.Y.Z, for instance 1.4.0", 2)
    try:
        return prune(options.directories, options.keep, options.dry_run, sys.stdout, sys.stderr)
    except PruneError as error:
        return sync.failed("cortex prune", str(error), 2)
    except (config.ConfigError, store.StoreError) as error:
        return sync.failed("cortex prune", str(error))
    except OSError as error:
        return sync.failed("cortex prune", sync.os_error(error))
