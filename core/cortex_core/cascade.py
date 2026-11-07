"""The configuration cascade — ADR-013 §3.1: each setting, its value, and where it comes from.

The tiers, the most specific first: the environment, the developer's ``cortex.local.toml``, the
project's ``cortex.toml``, and the machine's ``config.toml`` in the Cortex home. For each key the
first tier that sets it wins, else its default (§3.2). One resolution for the ``cortex`` command
and the runtime, so that the two never read a project apart. The team's resolution — what CI
checks with ``--locked``, and what the runtime reads — leaves the developer's ``cortex.local.toml``
out.

It reads the files of a project and the machine's file, with ``project``'s grammar. Whether
``cortex.toml`` is committed is not asked here: that takes git, which the runtime's container
may refuse. The caller says it — the command from git, the runtime not at all (§3.12).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from . import project
from .project import LOCAL_FILE, MACHINE_FILE, MACHINE_KEYS, PROJECT_FILE

PROJECT_SETTINGS: Tuple[str, ...] = ("version", "theme", "sync", "claude_access")
KEYS: Tuple[str, ...] = PROJECT_SETTINGS + MACHINE_KEYS
DEFAULT = "default"
DEFAULTS: Mapping[str, object] = {"sync": "store", "claude_access": None,
                                  "releases_url": project.DEFAULT_RELEASES_URL, "update_check": True, "register": True}
# The environment's keys (§3.7), as they behave since they were variables only: CORTEX_RELEASES_URL
# names the host, an empty one counting as unset; the two others, set and not empty, turn their key
# off — no variable turns one back on.
VARIABLES: Mapping[str, str] = {"releases_url": "CORTEX_RELEASES_URL", "update_check": "CORTEX_NO_UPDATE_CHECK",
                                "register": "CORTEX_NO_REGISTER"}


@dataclass(frozen=True)
class Setting:
    key: str
    value: object                     # None: unset, as claude_access may be
    origin: str                       # a file, as the project root names it; a variable; or "default"
    overridden: Tuple[Tuple[str, object], ...] = ()     # (origin, value) of the tiers it won over


@dataclass(frozen=True)
class Resolution:
    """The settings of a project — or, outside one, of the machine — in the order of ``KEYS``."""

    settings: Mapping[str, Setting]
    committed: Optional[bool] = None      # whether cortex.toml is committed: the caller's answer, None when unasked
    locked: bool = False                  # the team's resolution: cortex.local.toml left out
    warnings: Tuple[str, ...] = field(default=())       # the machine's file's, which never stop a read

    def __getitem__(self, key: str) -> Setting:
        return self.settings[key]

    def __contains__(self, key: str) -> bool:
        return key in self.settings

    def value(self, key: str) -> object:
        return self.settings[key].value


def _environment(key: str, environ: Mapping[str, str], variables: Mapping[str, str]) -> Optional[object]:
    name = variables.get(key)
    raw = environ.get(name) if name else None
    if not raw:
        return None
    return raw if key == "releases_url" else False


def resolve(project_data: Optional[Mapping], local_data: Optional[Mapping], machine_data: Mapping,
            environ: Mapping[str, str], *, machine_name: str = MACHINE_FILE, locked: bool = False,
            committed: Optional[bool] = None, warnings: Tuple[str, ...] = (), machine: bool = True,
            variables: Mapping[str, str] = VARIABLES) -> Resolution:
    """The settings from the files read already — ``project_data`` and ``local_data`` checked by
    ``project``'s grammar, ``machine_data`` the keys of the machine's file that read. Without
    ``project_data``, outside a project, only the machine's keys; ``machine`` off, the project's only.
    ``locked`` gives the team's resolution, without ``cortex.local.toml``. ``machine_name`` is how an
    origin names the machine's file. ``variables`` are the environment's keys, each reader's own: the
    command's are ``VARIABLES``."""
    keys = (KEYS if machine else PROJECT_SETTINGS) if project_data is not None else MACHINE_KEYS
    settings: Dict[str, Setting] = {}
    for key in keys:
        found: List[Tuple[str, object]] = []
        from_environment = _environment(key, environ, variables)
        if from_environment is not None:
            found.append((variables[key], from_environment))
        if local_data is not None and not locked and key in project.LOCAL_KEYS and key in local_data:
            found.append((LOCAL_FILE, local_data[key]))
        if project_data is not None and key in project.PROJECT_KEYS and key in project_data:
            found.append((PROJECT_FILE, project_data[key]))
        if key in MACHINE_KEYS and key in machine_data:
            found.append((machine_name, machine_data[key]))
        if found:
            (origin, value), overridden = found[0], tuple(found[1:])
        else:
            origin, value, overridden = DEFAULT, DEFAULTS.get(key), ()
        settings[key] = Setting(key, value, origin, overridden)
    return Resolution(settings, committed=committed if project_data is not None else None, locked=locked,
                      warnings=warnings)


def _toml(path: str) -> Dict:
    with open(path, "rb") as fh:
        return tomllib.loads(fh.read().decode("utf-8-sig"))    # a byte order mark, as Notepad writes one


def _checked_toml(path: str, name: str) -> Dict:
    try:
        return _toml(path)
    except UnicodeDecodeError:
        raise project.ProjectFileError(f"{name}: not UTF-8 text")
    except tomllib.TOMLDecodeError as error:
        raise project.ProjectFileError(f"{name}: {error}")


def read_project(root: str, local: bool = True) -> Tuple[Mapping, Optional[Mapping]]:
    """``cortex.toml`` at ``root``, and ``cortex.local.toml`` unless ``local`` is off — or ``None``
    when it is not there — each checked with the grammar. Raises ``project.ProjectFileError`` naming
    the file; ``OSError`` for a file that cannot be opened. The one reader of the two files, the
    command's and ``cortex config``'s."""
    data = _checked_toml(os.path.join(root, PROJECT_FILE), PROJECT_FILE)
    project.check_project(data)
    local_data = None
    path = os.path.join(root, LOCAL_FILE)
    if local and os.path.isfile(path):
        local_data = _checked_toml(path, LOCAL_FILE)
        project.check_local(local_data)
    return data, local_data


def read_machine(path: str, name: Optional[str] = None) -> Tuple[Dict[str, object], List[str]]:
    """The keys of the machine's file at ``path`` that read, and the warnings: a file that cannot be
    read is one, never an error (§3.7). No file, no keys."""
    name = name or path
    if not os.path.isfile(path):
        return {}, []
    try:
        data = _toml(path)
    except OSError as error:
        return {}, [f"{name} cannot be read, and is not: {error.strerror or error}"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return {}, [f"{name} is not read: {error}"]
    return project.machine_values(data, name)


def loose_version(root: str) -> Optional[Tuple[str, str]]:
    """The file that pins the version of the project at ``root`` and that version — read before any
    other key is checked (§3.8, step 1), so that a version newer than the binary is said as such,
    not as a key the binary does not know. ``None`` when no file pins a version that reads."""
    try:
        data = _toml(os.path.join(root, PROJECT_FILE))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    version = data.get("version")
    return (PROJECT_FILE, version) if project.is_version(version) else None
