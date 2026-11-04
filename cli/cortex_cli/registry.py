"""``projects.toml`` and ``cortex project`` — the Cortex projects of this machine (ADR-011).

``$CORTEX_HOME/projects.toml`` — ``~/.cortex/projects.toml`` by default, beside the store — holds
one entry per project root, the directory of ``cortex.toml``: a ``name`` and a ``path``, nothing
else. The grammar is the core's (``cortex_core.project.check_registry``), which the cockpit and the
runtime read the file with too.

A path is written as the shell names it, a link not resolved; ``add`` compares it resolved with the
paths listed, so that two entries never name the same directory — the core sees the text alone,
and refuses two that write the same one.

The file is written whole or not at all — a temporary file beside it, then a rename — and one write
at a time, under a lock on ``projects.toml.lock`` beside it: two commands that register at once
both land. A reader takes no lock: it finds the file as it was before a write, or after it.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, TextIO, Tuple

from cortex_core import project
from cortex_core.project import LaterFormatError, ProjectFileError, shown

from . import config, store, sync
from .paths import display, working_directory

FILE = project.REGISTRY_FILE
HEADER = "# The Cortex projects of this machine (ADR-011). Written by cortex — `cortex project` edits it.\n"
LOCK_WAIT = 30    # seconds a write waits for another: one takes milliseconds
LISTS = "`cortex project list` lists this machine's projects"


class RegistryError(Exception):
    """The registry cannot be read or written, or refuses what it is asked — the message says why,
    and names the file or the project."""


@dataclass(frozen=True)
class Entry:
    name: str
    path: str       # absolute, as the shell named it, with / on every platform (``display``)


def registry_path() -> Path:
    """Where the registry is: ``$CORTEX_HOME/projects.toml``, or ``~/.cortex/projects.toml``."""
    return store.cortex_home() / FILE


def _read(path: Path) -> List[Entry]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        return []
    except UnicodeDecodeError:
        raise RegistryError(f"{display(str(path))}: not UTF-8 text")
    except OSError as error:
        raise RegistryError(f"{display(str(path))} cannot be read — {sync.os_error(error)}")
    try:
        data = tomllib.loads(text)
        project.check_registry(data, display(str(path)))
    except tomllib.TOMLDecodeError as error:
        raise RegistryError(f"{display(str(path))}: {error}")
    except LaterFormatError as error:
        raise RegistryError(f"{error}: this cortex is older than the one that wrote it — update it:\n"
                            "    cortex self-update")
    except ProjectFileError as error:
        raise RegistryError(str(error))
    return sorted((Entry(entry["name"], entry["path"]) for entry in data.get("project", [])),
                  key=lambda entry: entry.name)


def load() -> List[Entry]:
    """The entries of the registry, in name order — none when there is no registry. A file the
    core's grammar refuses is a ``RegistryError`` naming it and what is wrong; one of a later format
    names ``cortex self-update``, as a later ``cortex.lock`` does."""
    return _read(registry_path())


def render(entries: Iterable[Entry]) -> str:
    """The registry holding ``entries``, in name order, under the header of ADR-011 §3.1."""
    text = HEADER + f"format = {project.REGISTRY_FORMAT}\n"
    for entry in sorted(entries, key=lambda entry: entry.name):
        text += f"\n[[project]]\nname = {config.toml_string(entry.name)}\npath = {config.toml_string(entry.path)}\n"
    return text


def _write(path: Path, entries: List[Entry]) -> None:
    """Write the registry whole — read back first: a file this cortex would refuse is not written.
    A path that is no UTF-8, or holds a character a TOML string cannot, is refused so."""
    text = render(entries)
    try:
        text.encode("utf-8")
        project.check_registry(tomllib.loads(text))
    except (UnicodeEncodeError, tomllib.TOMLDecodeError, ProjectFileError) as error:
        raise RegistryError(f"{display(str(path))} is not written: it would not read back — {error}")
    try:
        config.write_text(str(path), text)
    except OSError as error:
        raise RegistryError(f"{display(str(path))} cannot be written — {sync.os_error(error)}")


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """One write of the registry at a time: the others wait for it, ``LOCK_WAIT`` seconds at most.
    The lock is on a file beside it, never replaced: the registry itself is, at each write. Where
    the file system takes no lock, the write goes on without it."""
    lock = path.with_name(path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as error:
        raise RegistryError(f"{display(str(path))} cannot be written — {sync.os_error(error)}")
    locked = False
    try:
        try:
            locked = sync.wait_for_lock(fd, 0, LOCK_WAIT)
            if not locked:
                raise RegistryError(f"another cortex command has held {display(str(lock))} for {LOCK_WAIT} seconds — "
                                    "wait for it, then run the command again")
        except OSError:
            pass                            # a file system without the lock
        yield
    finally:
        if locked:
            sync.release_lock(fd, 0)
        os.close(fd)


def same_directory(one: str, other: str) -> bool:
    """Whether two paths name one directory once resolved — through a link, or another spelling of
    it, such as a case the file system ignores."""
    if os.path.normcase(os.path.realpath(one)) == os.path.normcase(os.path.realpath(other)):
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def default_name(root: str, taken: Iterable[str]) -> str:
    """The name ``add`` gives the project at ``root`` (ADR-011 §3.2): its directory's name, lowered,
    each character a name does not take turned to ``-`` — those it cannot start with dropped, 64 at
    most, ``project`` when none is left — then ``-2``, ``-3``… when the name is taken."""
    base = os.path.basename(root.rstrip("/" + os.sep))
    name = "".join(char if char in "abcdefghijklmnopqrstuvwxyz0123456789._-" else "-" for char in base.lower())
    name = name.lstrip("._-")[:64] or "project"
    taken = set(taken)
    number = 1
    candidate = name
    while candidate in taken:
        number += 1
        candidate = f"{name[:64 - len(str(number)) - 1]}-{number}"
    return candidate


def _has_project(root: str) -> bool:
    return os.path.isfile(os.path.join(root, config.PROJECT_FILE))


def add(root: str, name: Optional[str] = None, warn: Optional[TextIO] = None) -> Tuple[Optional[Entry], bool]:
    """Register the project at ``root`` — absolute, as the shell names it — under ``name``, or the
    name ``default_name`` gives. The entry, and whether it was added: ``False`` when a listed path
    names that directory already, which is the entry returned, and nothing changes.

    ``RegistryError`` when ``root`` holds no ``cortex.toml``, when ``name`` is no project name or
    another path's, and when the registry cannot be read or written. With ``warn``, nothing is
    raised: the reason is said there as a warning, and ``(None, False)`` returned — for a command
    whose work is done, that the registry must not fail."""
    try:
        if not _has_project(root):
            raise RegistryError(f"{display(root)} holds no {config.PROJECT_FILE} — `cortex init` makes a directory a "
                                "Cortex project")
        if name is not None and not project.is_project_name(name):
            raise RegistryError(f"{shown(name)} is no project name — {project.PROJECT_NAME_RULE}")
        path = registry_path()
        with _locked(path):
            entries = _read(path)
            listed = next((entry for entry in entries if same_directory(entry.path, root)), None)
            if listed is not None:
                return listed, False
            if name is None:
                name = default_name(root, (entry.name for entry in entries))
            holder = next((entry for entry in entries if entry.name == name), None)
            if holder is not None:
                raise RegistryError(f"the name {shown(name)} is taken, by {display(holder.path)} — "
                                    "choose another, with --name")
            entry = Entry(name, display(root))
            _write(path, entries + [entry])
            return entry, True
    except RegistryError as error:
        if warn is None:
            raise
        warn.write(f"warning: {display(root)} is not registered — {error}\n")
        return None, False


def find(entries: Iterable[Entry], key: str, cwd: Optional[str] = None) -> Optional[Entry]:
    """The entry ``key`` names: by its name, or else by its path — relative to ``cwd``, the current
    directory by default — which names the entry's directory, once resolved."""
    entries = list(entries)
    named = next((entry for entry in entries if entry.name == key), None)
    if named is not None:
        return named
    path = os.path.abspath(os.path.join(cwd or working_directory(), key))
    return next((entry for entry in entries if same_directory(entry.path, path)), None)


def remove(key: str, cwd: Optional[str] = None) -> Entry:
    """Drop from the registry the entry ``key`` names (``find``), and return it. The project's files
    are not touched. ``RegistryError`` when no entry is named so."""
    path = registry_path()
    missing = RegistryError(f"no project is registered as {shown(key)}, by name or by path — {LISTS}")
    if not path.exists():
        raise missing
    with _locked(path):
        entries = _read(path)
        entry = find(entries, key, cwd)
        if entry is None:
            raise missing
        _write(path, [kept for kept in entries if kept != entry])
    return entry


def drop(entries: Iterable[Entry]) -> List[Entry]:
    """Drop ``entries`` from the registry in one write, and return those it held: one changed
    meanwhile — its name or its path — is no longer any of them, and stays."""
    path = registry_path()
    gone = set(entries)
    if not gone or not path.exists():
        return []
    with _locked(path):
        listed = _read(path)
        dropped = [entry for entry in listed if entry in gone]
        if dropped:
            _write(path, [entry for entry in listed if entry not in gone])
    return dropped


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def _add(options: argparse.Namespace, out: TextIO) -> int:
    if options.name is not None and not project.is_project_name(options.name):
        return sync.failed("cortex project add", f"--name {shown(options.name)} is no project name — "
                           f"{project.PROJECT_NAME_RULE}", 2)
    cwd = working_directory()
    if options.path is None:
        root = config.find_project(cwd)
        if root is None:
            return sync.failed("cortex project add", f"no {config.PROJECT_FILE} in {display(cwd)} or above it — "
                               "`cortex init` makes a directory a Cortex project")
    else:
        root = os.path.abspath(os.path.join(cwd, options.path))       # an absolute one stays
        if not os.path.isdir(root):
            return sync.failed("cortex project add", f"{display(root)}: no such directory", 2)
    entry, added = add(root, options.name)
    if added:
        out.write(f"registered {display(root)} as {entry.name} — {LISTS}\n")
    else:
        again = (f" — `cortex project remove {entry.name}` first, to register it as {options.name}"
                 if options.name not in (None, entry.name) else "")
        out.write(f"{display(root)} is registered already, as {entry.name}: nothing changed{again}\n")
    return 0


def _remove(options: argparse.Namespace, out: TextIO) -> int:
    entry = remove(options.project)
    out.write(f"dropped {entry.name}, {entry.path}, from the registry — the project's files are as they were\n")
    return 0


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex project",
        description="The Cortex projects of this machine, in projects.toml beside the store: "
                    "$CORTEX_HOME/projects.toml, ~/.cortex/projects.toml by default. ADR-011.")
    actions = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    adding = actions.add_parser("add", help="register a project — the one of the current directory, by default",
                                description="Register the project at PATH — by default the one of the current "
                                            "directory, the nearest cortex.toml — named after its directory.")
    adding.add_argument("path", nargs="?", metavar="PATH", help="the project's root, the directory of its cortex.toml")
    adding.add_argument("--name", metavar="NAME", help="the name to register it as, instead of its directory's")
    removing = actions.add_parser("remove", help="drop a project from the registry — its files are not touched",
                                  description="Drop a project from the registry, by its name or its path. Its "
                                              "files are not touched.")
    removing.add_argument("project", metavar="NAME|PATH", help="the project's name, or its path")
    options = parser.parse_args(args)
    try:
        if options.action == "add":
            return _add(options, sys.stdout)
        return _remove(options, sys.stdout)
    except RegistryError as error:
        return sync.failed(f"cortex project {options.action}", str(error))
