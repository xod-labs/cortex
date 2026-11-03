"""``cortex prune`` — remove from the store the versions no project uses (ADR-010 §3.6).

``cortex prune DIR... [--keep VERSION]... [--dry-run]`` finds the projects under each ``DIR`` —
every ``cortex.toml`` up to six directories down, past ``.git``, ``node_modules``, ``vendor``,
``.venv`` and the ``cortex/`` a link or copy sync made — keeps the versions they pin and the ones
they read now, this binary's own and each ``--keep``, and removes every other version from the store.

Nothing lists the projects of a machine (ADR-008 §3.3): the user says where they are, and a
project elsewhere loses its version. It gets it back at its next ``cortex sync``, checked against
its ``cortex.lock``. A version is removed by a rename first, so that a sync running meanwhile finds
it whole or absent. The runtime reads the store too: a version only it uses is kept with ``--keep``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import Dict, Iterator, List, TextIO

from cortex_core.project import is_version, shown

from . import config, store, sync, validate
from .paths import display, working_directory
from .semver import Version

DEPTH = 6
SKIPPED = (".git", "node_modules", "vendor", ".venv")


class PruneError(Exception):
    pass


def projects(directory: Path, err: TextIO) -> Iterator[Path]:
    """The directories holding a ``cortex.toml`` under ``directory``, ``DEPTH`` levels down at most —
    through a link to a directory too, each directory once. One it cannot read is named."""
    seen = set()

    def unreadable(error: OSError) -> None:
        err.write(f"note: {display(str(error.filename))} cannot be read — a project under it is not counted\n")

    for root, subdirs, files in os.walk(directory, onerror=unreadable, followlinks=True):
        here = Path(root)
        real = os.path.realpath(here)
        if real in seen:
            subdirs[:] = []
            continue
        seen.add(real)
        if config.PROJECT_FILE in files:
            yield here
        depth = len(here.relative_to(directory).parts)
        subdirs[:] = [] if depth >= DEPTH else [
            name for name in subdirs
            if name not in SKIPPED and not (name == sync.LINK and (sync.is_link(here / name) or (here / name / sync.MARKER).is_file()))]


def versions_of(root: Path, err: TextIO) -> List[str]:
    """The versions a project uses: the one its ``cortex.toml`` pins, and the one it reads now —
    ``spec`` in ``cortex.local.toml``, or ``cortex/`` — which differ until it syncs a teammate's bump.
    Only ``version`` is needed: a key this cortex does not know elsewhere loses no version."""
    try:
        project = config.load(str(root))
    except config.ConfigError as error:
        try:
            pinned = tomllib.loads((root / config.PROJECT_FILE).read_text(encoding="utf-8-sig")).get("version")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            pinned = None
        if is_version(pinned):
            err.write(f"note: {display(str(root))}: {error} — only its version, {pinned}, is read\n")
            return [pinned]
        err.write(f"note: {display(str(root))}: {error} — its version is not known, so none is kept for it\n")
        return []
    except OSError as error:
        err.write(f"note: {display(str(root))}: {sync.os_error(error)} — its version is not known, so none is kept for it\n")
        return []
    used = [str(project.version)]
    spec = project.spec_directory()
    if spec is not None and os.path.isdir(spec):
        synced = validate._synced_version(project, spec)
        if synced and synced not in used:
            used.append(synced)
    return used


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
    # Listed before the projects are read: a version a project syncs meanwhile is in no list, and stays.
    versions = stored(the_store)
    used: Dict[Version, List[str]] = {}
    found = set()
    for directory in directories:
        for root in projects(Path(os.path.abspath(directory)), err):
            if os.path.realpath(root) in found:
                continue                    # under two of the directories given
            found.add(os.path.realpath(root))
            for version in versions_of(root, err):
                used.setdefault(Version(version), []).append(display(str(root)))
    kept = {version: f"used by {', '.join(roots)}" for version, roots in used.items()}
    kept.setdefault(the_store.own, "this cortex's own")
    for version in keep:
        kept.setdefault(Version(version), "--keep")
    out.write(f"{len(found)} project(s) found under {', '.join(display(d) for d in directories)}\n")
    for version in versions:
        if version in kept:
            out.write(f"  kept     {version} — {kept[version]}\n")
    removed = [version for version in versions if version not in kept]
    failed = 0
    for version in removed:
        if dry_run:
            out.write(f"  would remove  {version}\n")
            continue
        try:
            the_store.remove(version)
        except OSError as error:
            failed += 1
            err.write(f"cortex prune: {version} is still in the store — {sync.os_error(error)}\n")
            continue
        out.write(f"  removed  {version}\n")
    if not removed:
        out.write("Nothing to remove.\n")
    elif dry_run:
        out.write("--dry-run: nothing was removed\n")
    else:
        out.write("A project elsewhere gets its version back at its next `cortex sync`.\n")
    return 1 if failed else 0


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
