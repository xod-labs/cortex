"""``cortex prune`` — remove from the store the versions no project uses (ADR-010 §3.6, ADR-011 §3.4).

``cortex prune [DIR...] [--keep VERSION]... [--dry-run]`` keeps the versions the projects pin and
the ones they read now, this binary's own and each ``--keep``, and removes every other version from
the store. The projects are:

- with no ``DIR``, the registry's (ADR-011), each read where it is. An entry whose project is gone —
  ``missing``, or ``no-project`` with no version read there — is dropped from the registry, and its
  version no longer kept for it. An ``unreadable`` one stays, named, and keeps what can be read of
  its versions; so does a ``no-project`` one whose ``cortex.local.toml`` still reads a version;
- with ``DIR...``, as in 1.4.0, those under each ``DIR`` — every ``cortex.toml`` up to six
  directories down, past ``.git``, ``node_modules``, ``vendor``, ``.venv`` and the ``cortex/`` a link
  or copy sync made. The registry is neither read nor written.

A project elsewhere loses its version, and gets it back at its next ``cortex sync``, checked
against its ``cortex.lock``. A version is removed by a rename first, so that a sync running
meanwhile finds it whole or absent. The runtime reads the store too: a version only it uses is kept
with ``--keep``.

The entries gone are dropped before a version is removed, in one write. A registry that cannot be
written stops the command there, nothing removed: no version is removed while the registry lists a
project gone, which would say its version is kept. A removal that fails after the drop leaves in
the store a version no listed project uses, which the next prune removes.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections import deque
from pathlib import Path
from typing import Dict, Iterator, List, Optional, TextIO

from cortex_core.project import is_version, shown

from . import config, registry, store, sync, validate
from .paths import display, working_directory
from .semver import Version

DEPTH = 6
SKIPPED = (".git", "node_modules", "vendor", ".venv")


class PruneError(Exception):
    pass


def projects(directory: Path, err: TextIO) -> Iterator[Path]:
    """The directories holding a ``cortex.toml`` under ``directory``, ``DEPTH`` levels down at most —
    through a link to a directory too, each directory once. Breadth first: a directory is reached by
    its shortest path before any other, so that a deeper one, through a link, cuts nothing under
    it. One it cannot read is named."""
    seen = set()
    queue = deque([(directory, 0)])
    while queue:
        here, depth = queue.popleft()
        real = os.path.realpath(here)
        if real in seen:
            continue
        seen.add(real)
        try:
            with os.scandir(here) as found:
                entries = sorted(found, key=lambda entry: entry.name)
        except OSError as error:
            err.write(f"note: {display(str(error.filename or here))} cannot be read — a project under it is not counted\n")
            continue
        if any(entry.name == config.PROJECT_FILE and not _is_dir(entry) for entry in entries):
            yield here
        if depth >= DEPTH:
            continue
        queue.extend((here / entry.name, depth + 1) for entry in entries if _is_dir(entry) and _searched(here, entry.name))


def _is_dir(entry: os.DirEntry) -> bool:
    """A directory, or a link to one — as ``os.walk`` tells them."""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _searched(here: Path, name: str) -> bool:
    """Whether the directory ``name`` of ``here`` may hold a project of the machine."""
    return name not in SKIPPED and not (name == sync.LINK and (sync.is_link(here / name) or (here / name / sync.MARKER).is_file()))


def _loosely(root: Path) -> Optional[config.Project]:
    """The project at ``root`` read for its versions only — ``version`` in ``cortex.toml``, ``spec``
    in ``cortex.local.toml`` — when the files hold what this cortex refuses: a key it does not know,
    a value it does not take. ``None`` when its version is not known."""
    def read(name: str) -> dict:
        try:
            return tomllib.loads((root / name).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return {}

    pinned, spec = read(config.PROJECT_FILE).get("version"), read(config.LOCAL_FILE).get("spec")
    if not is_version(pinned):
        return None
    return config.Project(root=str(root), version=Version(pinned), theme="none", sync=None, local_theme=None,
                          spec=spec if isinstance(spec, str) else None)


def versions_of(root: Path, err: TextIO) -> List[str]:
    """The versions a project uses: the one its ``cortex.toml`` pins, and the one it reads now —
    ``spec`` in ``cortex.local.toml``, or ``cortex/`` — which differ until it syncs a teammate's bump.
    Only those are needed: a key this cortex does not know, in either file, loses no version."""
    refused = None
    try:
        project = config.load(str(root))
    except config.ConfigError as error:
        refused, project = error, _loosely(root)
        if project is None:
            err.write(f"note: {display(str(root))}: {error} — its version is not known, so none is kept for it\n")
            return []
    except OSError as error:
        err.write(f"note: {display(str(root))}: {sync.os_error(error)} — its version is not known, so none is kept for it\n")
        return []
    used = [str(project.version)]
    spec = project.spec_directory()
    if spec is not None and os.path.isdir(spec):
        synced = validate._synced_version(project.root, project.spec, spec)
        if synced and synced not in used:
            used.append(synced)
    if refused is not None:
        err.write(f"note: {display(str(root))}: {refused} — only its versions are read: {', '.join(used)}\n")
    return used


def stored(the_store: store.Store) -> List[Version]:
    """The versions in the store: its complete version directories, stagings left out."""
    if not the_store.versions.is_dir():
        return []
    found = [Version(entry.name) for entry in the_store.versions.iterdir()
             if not entry.name.startswith(".") and Version.valid(entry.name)]
    return sorted(version for version in found if the_store.has(version))


def _under(directories: List[str], used: Dict[Version, List[str]], err: TextIO) -> int:
    """Add to ``used`` the versions of the projects under ``directories``, each project once — though
    under two of them — and return how many there are."""
    found = set()
    for directory in directories:
        for root in projects(Path(os.path.abspath(directory)), err):
            if os.path.realpath(root) in found:
                continue                    # under two of the directories given
            found.add(os.path.realpath(root))
            for version in versions_of(root, err):
                used.setdefault(Version(version), []).append(display(str(root)))
    return len(found)


def _registered(entries: List[registry.Entry], used: Dict[Version, List[str]], err: TextIO) -> List[registry.Entry]:
    """Add to ``used`` the versions of the registered projects, each read where it is (``registry.state``),
    and return the entries whose project is gone: ``missing``, or ``no-project`` with no version read
    there. An ``unreadable`` one is named: it stays, and keeps the versions that can be read of it, as a
    project under a ``DIR`` would. So does a ``no-project`` one whose ``cortex.local.toml`` still reads
    a version: a branch without Cortex checked out, and the project is back at the next one."""
    gone = []
    for entry in entries:
        found = registry.state(entry)
        if found.state == registry.MISSING or (found.state == registry.NO_PROJECT and not found.reads):
            gone.append(entry)
            continue
        versions = list(dict.fromkeys(version for version in (found.pinned, found.reads) if version))
        if found.state == registry.NO_PROJECT:
            err.write(f"note: {entry.name}: {entry.path} holds no {config.PROJECT_FILE}, but its {config.LOCAL_FILE} "
                      f"still reads {found.reads} — a branch without Cortex checked out? It stays in the registry, and "
                      f"keeps that version; `cortex project remove {entry.name}` drops it\n")
        if found.state == registry.UNREADABLE:
            what = (f"only its versions are read: {', '.join(versions)}" if versions
                    else "its version is not known, so none is kept for it")
            err.write(f"note: {entry.name}: {entry.path}: {found.why} — it stays in the registry, and {what}\n")
        for version in versions:
            used.setdefault(Version(version), []).append(entry.name)
    return gone


def _drop(gone: List[registry.Entry], dry_run: bool, out: TextIO, err: TextIO) -> bool:
    """Drop from the registry the entries whose project is gone, in one write, and say each. ``False``
    when the registry cannot be written: then nothing is to be removed, since the registry still
    lists those projects."""
    if dry_run:
        for entry in gone:
            out.write(f"{entry.name}: {entry.path} is gone — it would be dropped from the registry\n")
        return True
    try:
        dropped = registry.drop(gone)
    except registry.RegistryError as error:
        err.write(f"cortex prune: {', '.join(entry.name for entry in gone)}: gone, and not dropped from the registry — "
                  f"{error}\nNothing was removed: no version is removed while the registry lists a project gone. "
                  "Run the command again, or give the directories of the projects: cortex prune DIR...\n")
        return False
    for entry in gone:
        if entry in dropped:
            out.write(f"{entry.name}: {entry.path} is gone — dropped from the registry\n")
        else:   # no longer listed as it was: removed meanwhile, by another command or by hand
            out.write(f"{entry.name}: {entry.path} is gone — the registry changed meanwhile, and is left as it is\n")
    return True


def prune(directories: List[str], keep: List[str], dry_run: bool, out: TextIO, err: TextIO) -> int:
    """Prune the store, keeping the versions of the projects under ``directories`` — of the projects
    registered when there is none — this cortex's own and each of ``keep``."""
    directories = [os.path.join(working_directory(), directory) for directory in directories]   # an absolute one stays
    for directory in directories:
        if not os.path.isdir(directory):
            raise PruneError(f"{display(directory)}: no such directory")
    the_store = store.Store()
    # Listed before the projects are read: a version a project syncs meanwhile is in no list, and stays.
    versions = stored(the_store)
    used: Dict[Version, List[str]] = {}
    gone: List[registry.Entry] = []
    if directories:
        found = _under(directories, used, err)
        out.write(f"{found} project(s) found under {', '.join(display(d) for d in directories)}\n")
    else:
        entries = registry.load()
        if not entries:
            raise PruneError("no DIR, and no project registered on this machine: pruning would remove every version. "
                             "Give the directories of the projects — cortex prune DIR... — or register each one: "
                             "`cortex project add` in it")
        out.write(f"{len(entries)} project(s) registered in {display(str(registry.registry_path()))}\n")
        gone = _registered(entries, used, err)
        # Dropped before a version is removed: a registry that cannot be written stops here, nothing removed.
        if gone and not _drop(gone, dry_run, out, err):
            return 1
        # Read again just before the removals: a project registered since the first read — a sync
        # that ended meanwhile — keeps its versions. One registered after this read loses a version
        # it synced from the store already, until its next sync, which its assistant runs (§5).
        more = [entry for entry in registry.load() if entry not in entries]
        if more:
            out.write(f"{len(more)} more project(s) registered meanwhile, read too\n")
        _registered(more, used, err)
    kept = {version: f"used by {', '.join(roots)}" for version, roots in used.items()}
    kept.setdefault(the_store.own, "this cortex's own")
    for version in keep:
        kept.setdefault(Version(version), "--keep")
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
            if the_store.path(version).exists():
                err.write(f"cortex prune: {version} is still in the store — {sync.os_error(error)}\n")
            else:   # renamed out of the store, and not all deleted: a staging, which the store deletes
                err.write(f"cortex prune: {version} is out of the store, but not all of its files were deleted — "
                          f"{sync.os_error(error)}. A sync deletes what is left, a day from now\n")
            continue
        out.write(f"  removed  {version}\n")
    if not removed:
        out.write("Nothing to remove.\n")
    elif not dry_run:
        elsewhere = "elsewhere" if directories else "not registered"
        out.write(f"A project {elsewhere} gets its version back at its next `cortex sync`.\n")
    if dry_run and (removed or gone):
        undone = [what for what, any_ in (("nothing was removed", removed), ("no entry was dropped", gone)) if any_]
        out.write(f"--dry-run: {', and '.join(undone)}\n")
    return 1 if failed else 0


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex prune",
        description="Remove from the store every version that no project pins or reads — the projects registered "
                    "on this machine, or those under each DIR — this cortex's own, and each --keep, kept. With no DIR, "
                    "an entry whose project is gone is dropped from the registry. ADR-010 §3.6, ADR-011 §3.4.")
    parser.add_argument("directories", nargs="*", metavar="DIR",
                        help="where the projects of this machine are, instead of the registry — "
                             "`cortex project list` lists the projects registered")
    parser.add_argument("--keep", action="append", default=[], metavar="VERSION",
                        help="a version to keep, which no project pins — one the runtime uses, say")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be removed, and dropped from the registry, and change nothing")
    options = parser.parse_args(args)
    for version in options.keep:
        if not Version.valid(version):
            return sync.failed("cortex prune", f"--keep {shown(version)} is no version — X.Y.Z, for instance 1.4.0", 2)
    try:
        return prune(options.directories, options.keep, options.dry_run, sys.stdout, sys.stderr)
    except PruneError as error:
        return sync.failed("cortex prune", str(error), 2)
    except (config.ConfigError, store.StoreError, registry.RegistryError) as error:
        return sync.failed("cortex prune", str(error))
    except OSError as error:
        return sync.failed("cortex prune", sync.os_error(error))
