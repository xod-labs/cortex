"""The configuration cascade — ADR-013 §3.1: each setting, its value, and where it comes from.

The tiers, the most specific first: the environment, the developer's ``cortex.local.toml``, the
project's ``cortex.toml``, the team's ``cortex.team.toml`` in the team's folder, and the machine's
``config.toml`` in the Cortex home. For each key the first tier that sets it wins, else its default
(§3.2) — but a key the team's file locks, which takes the team's value (§3.5), and ``version``:
between the developer's version and the team's, the highest wins (§3.1, §3.3). One resolution for
the ``cortex`` command and the runtime, so that the two never read a project apart. The team's
resolution — what CI checks with ``--locked``, and what the runtime reads — leaves the developer's
``cortex.local.toml`` out, and the keys of an uncommitted ``cortex.toml`` the team's file sets.

It reads the files of a project and the machine's file, with ``project``'s grammar. Whether
``cortex.toml`` is committed is not asked here: that takes git, which the runtime's container
may refuse. The caller says it — the command from git, the runtime not at all (§3.12).
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from . import project
from .project import LOCAL_FILE, MACHINE_FILE, MACHINE_KEYS, PROJECT_FILE, TEAM_FILE, TEAM_FOLDER, version_key

PROJECT_SETTINGS: Tuple[str, ...] = ("version", "theme", "sync", "claude_access", "agents")
KEYS: Tuple[str, ...] = PROJECT_SETTINGS + MACHINE_KEYS
DEFAULT = "default"
DEFAULTS: Mapping[str, object] = {"sync": "store", "claude_access": None, "agents": TEAM_FOLDER,
                                  "releases_url": project.DEFAULT_RELEASES_URL, "update_check": True, "register": True}
# The environment's keys (§3.7), as they behave since they were variables only: CORTEX_RELEASES_URL
# names the host, an empty one counting as unset; the two others, set and not empty, turn their key
# off — no variable turns one back on. A variable of another key is its value, as written.
VARIABLES: Mapping[str, str] = {"releases_url": "CORTEX_RELEASES_URL", "update_check": "CORTEX_NO_UPDATE_CHECK",
                                "register": "CORTEX_NO_REGISTER"}
NEGATING: Tuple[str, ...] = ("update_check", "register")


TEAM_NAME = f"{TEAM_FOLDER}/{TEAM_FILE}"


def team_name(folder: str = TEAM_FOLDER) -> str:
    """How the team's file in the team's folder ``folder`` is named: from the project root, as an
    origin and a message name it."""
    return f"{folder}/{TEAM_FILE}"


@dataclass(frozen=True)
class Unreadable:
    """A team's file that does not parse, or cannot be opened: refused, unless a committed pin before
    1.8.0 makes the team's version its own — the file is not read then, and said so."""
    error: str


@dataclass(frozen=True)
class Setting:
    key: str
    value: object                     # None: unset, as claude_access may be
    origin: str                       # a file, as the project root names it; a variable; or "default"
    overridden: Tuple[Tuple[str, object], ...] = ()     # (origin, value) of the tiers it won over
    locked: bool = False              # the team's file locks it: its value is the team's


@dataclass(frozen=True)
class Resolution:
    """The settings of a project — or, outside one, of the machine — in the order of ``KEYS``."""

    settings: Mapping[str, Setting]
    committed: Optional[bool] = None      # whether cortex.toml is committed: the caller's answer, None when unasked
    locked: bool = False                  # the team's resolution: cortex.local.toml left out
    warnings: Tuple[str, ...] = field(default=())       # the machine's file's, which never stop a read
    team_version: Optional[Setting] = None     # the team's version, and the file it comes from (§3.1)
    developer_version: Optional[Setting] = None     # cortex.local.toml's, or an uncommitted cortex.toml's
    candidates: Mapping[str, str] = field(default_factory=dict)     # the version of each file: team, project, local
    team: bool = False                    # the team's file is read
    notes: Tuple[str, ...] = field(default=())      # what is said of the team's file, and of a locked key overridden

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
    return False if key in NEGATING else raw


def _higher(first: Tuple[str, str], second: Tuple[str, str]) -> Tuple[str, str]:
    """The higher of two ``(origin, version)`` — the first on a tie."""
    return second if version_key(second[1]) > version_key(first[1]) else first


def _versions(project_data: Mapping, local_data: Optional[Mapping], team_data: Optional[Mapping],
              committed: Optional[bool], team_name: str
              ) -> Tuple[Tuple[str, str], Optional[Tuple[str, str]], Optional[Mapping], List[str]]:
    """The team's version, the developer's, the team's file as read — ``None`` when it is not — and
    what is said of it (§3.1, §3.3, §3.8 step 3). The team's version is a committed ``cortex.toml``'s
    (decision 24), else the team's file's, else an uncommitted ``cortex.toml``'s, as 1.7.0 reads it;
    the developer's, ``cortex.local.toml``'s and an uncommitted ``cortex.toml``'s while the team's
    file pins one. A team's version before 1.8.0 reads neither the team's file nor a local version."""
    notes: List[str] = []
    pin = project_data.get("version")
    own = bool(committed) and pin is not None              # the project pins its own version, committed
    # A file that does not read — a conflict a pull left in it, say — may pin the team's version: it is no pinless
    # file, and is refused below, unless a committed pin makes the team's version cortex.toml's.
    pinless = team_data is not None and not isinstance(team_data, Unreadable) and "version" not in team_data
    # The team's version, before the file is read: a committed pin's, else the team's file's, else an
    # uncommitted pin's. Before 1.8.0, the team's file is not read — said, never refused.
    unread = pin if own else pin if pinless and pin is not None else None
    if team_data is not None and unread is not None and project.before_team(unread):
        notes.append(f"{team_name} is not read: the team's version is {unread}, before {project.FIRST_TEAM_VERSION}")
        team_data = None
    if isinstance(team_data, Unreadable):
        raise project.ProjectFileError(team_data.error)
    if team_data is not None:
        project.check_team(team_data, team_name)
    team_pin = team_data.get("version") if team_data is not None else None
    if own:
        team = (PROJECT_FILE, pin)
    elif team_pin is not None:
        team = (team_name, team_pin)
    elif pin is not None:
        team = (PROJECT_FILE, pin)
    else:
        raise project.ProjectFileError(f'{PROJECT_FILE}: "version" is required'
                                       + (f" — or in {team_name}" if team_data is not None else ""))
    developer: Optional[Tuple[str, str]] = None
    local = local_data.get("version") if local_data is not None else None
    if local is not None:
        if project.before_team(team[1]):
            raise project.ProjectFileError(
                f'{LOCAL_FILE}: "version" is read once the team\'s version is {project.FIRST_TEAM_VERSION} or later — '
                f"the team's is {team[1]}: the team moves to {project.FIRST_TEAM_VERSION} first, or remove `version` from "
                f"{LOCAL_FILE}")
        developer = (LOCAL_FILE, local)
    if not own and pin is not None and team_pin is not None:
        developer = _higher(developer, (PROJECT_FILE, pin)) if developer else (PROJECT_FILE, pin)
    return team, developer, team_data, notes


def resolve(project_data: Optional[Mapping], local_data: Optional[Mapping], machine_data: Mapping,
            environ: Mapping[str, str], *, machine_name: str = MACHINE_FILE, locked: bool = False,
            committed: Optional[bool] = None, warnings: Tuple[str, ...] = (), machine: bool = True,
            variables: Mapping[str, str] = VARIABLES, team_data: Optional[Mapping] = None,
            team_name: str = TEAM_NAME) -> Resolution:
    """The settings from the files read already — ``project_data`` and ``local_data`` checked by
    ``project``'s grammar, ``machine_data`` the keys of the machine's file that read, ``team_data``
    the team's file as parsed, which is checked here once the team's version says it is read. Without
    ``project_data``, outside a project, only the machine's keys; ``machine`` off, the project's only.
    ``locked`` gives the team's resolution, without ``cortex.local.toml``, and without the keys of an
    uncommitted ``cortex.toml`` the team's file sets. ``committed``, whether ``cortex.toml`` is, is the
    caller's answer: asked only where a team's file is there. ``machine_name`` and ``team_name`` are how
    an origin names those files. ``variables`` are the environment's keys, each reader's own: the
    command's are ``VARIABLES``. ``ProjectFileError`` for what the team's version refuses (§3.8)."""
    keys = (KEYS if machine else PROJECT_SETTINGS) if project_data is not None else MACHINE_KEYS
    if project_data is not None and "agents" in project_data:       # the folder as every message names it
        project_data = {**project_data, "agents": project.check_agents(project_data["agents"])}
    settings: Dict[str, Setting] = {}
    notes: List[str] = []
    team_version = developer_version = None
    candidates: Dict[str, str] = {}
    if project_data is not None:
        team, developer, team_data, notes = _versions(project_data, local_data, team_data, committed, team_name)
        team_version = Setting("version", team[1], team[0])
        developer_version = Setting("version", developer[1], developer[0]) if developer else None
        candidates = {name: data["version"] for name, data in
                      (("team", team_data), ("project", project_data), ("local", local_data))
                      if data is not None and "version" in data}
        if "agents" in project_data and project.before_team(team[1]):
            raise project.ProjectFileError(project.agents_before(team[1]))
    else:
        team_data = None
    lock_keys = tuple(team_data.get("locked", ())) if team_data is not None else ()
    for key in keys:
        if key == "version" and project_data is not None:
            read = team if locked or developer is None else _higher(team, developer)
            others = [(origin, value) for origin, value in ((team_version.origin, team_version.value),
                      *(((developer_version.origin, developer_version.value),) if developer_version else ()))
                      if (origin, value) != read]
            settings[key] = Setting(key, read[1], read[0], tuple(others))
            continue
        found: List[Tuple[str, object]] = []
        from_environment = _environment(key, environ, variables)
        if from_environment is not None:
            found.append((variables[key], from_environment))
        if local_data is not None and not locked and key in project.LOCAL_KEYS and key in local_data:
            found.append((LOCAL_FILE, local_data[key]))
        if project_data is not None and key in project.PROJECT_KEYS and key in project_data \
                and not (locked and committed is False and team_data is not None and key in team_data):
            found.append((PROJECT_FILE, project_data[key]))
        if team_data is not None and key in project.TEAM_KEYS and key in team_data:
            found.append((team_name, team_data[key]))
        if key in MACHINE_KEYS and key in machine_data:
            found.append((machine_name, machine_data[key]))
        if key in lock_keys:
            value = team_data[key]
            overridden = tuple(item for item in found if item[0] != team_name)
            for origin, other in overridden:
                if other != value:
                    notes.append(f"{key} is locked by {team_name} to {project.shown(value)}: {origin}'s "
                                 f"{project.shown(other)} is not read")
            settings[key] = Setting(key, value, team_name, overridden, locked=True)
            continue
        if found:
            (origin, value), overridden = found[0], tuple(found[1:])
        else:
            origin, value, overridden = DEFAULT, DEFAULTS.get(key), ()
        if key == "theme" and origin == DEFAULT:
            raise project.ProjectFileError(f'{PROJECT_FILE}: "theme" is required'
                                           + (f" — or in {team_name}" if team_data is not None else ""))
        settings[key] = Setting(key, value, origin, overridden)
    return Resolution(settings, committed=committed if project_data is not None else None, locked=locked,
                      warnings=warnings, team_version=team_version, developer_version=developer_version,
                      candidates=candidates, team=team_data is not None, notes=tuple(notes))


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


# A synced cortex sync cannot read — its own cache — which ``read_project(lenient=True)`` puts in its place:
# never blocking the sync that rewrites it (§3.1).
UNREADABLE_SYNCED = Unreadable("synced")


def read_team(root: str, folder: str = TEAM_FOLDER):
    """The team's file in the team's folder of the project at ``root``, as parsed — ``None`` when it
    is not there, ``Unreadable`` when it does not parse, or cannot be opened. Its grammar is checked by
    ``resolve``, which refuses such a file unless a committed pin before 1.8.0 makes the team's version
    cortex.toml's: a project kept there reads nothing of it."""
    path, name = os.path.join(root, folder, TEAM_FILE), f"{folder}/{TEAM_FILE}"
    if not team_file_there(root, folder):
        return None
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return Unreadable(f"{name}: cannot be read — not a file")
        return _checked_toml(path, name)
    except project.ProjectFileError as error:
        return Unreadable(str(error))
    except FileNotFoundError:
        return Unreadable(f"{name}: cannot be read — a link that leads nowhere")
    except OSError as error:
        return Unreadable(f"{name}: cannot be read — {error.strerror or error}")


def team_file_there(root: str, folder: str = TEAM_FOLDER) -> bool:
    """Whether the team's file is there, to read or to refuse: anything at its path — a file, a
    directory, a link that leads nowhere, or what cannot be told, its folder unsearchable — but
    nothing, or a folder that is no directory (ADR-013 §3.1, §3.5). Never read as no file: the
    project would lose the team's version and its locks."""
    path = os.path.join(root, folder, TEAM_FILE)
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return os.path.islink(path)
    except OSError:
        return True
    return True


def read_project(root: str, local: bool = True, lenient: bool = False,
                 folder: str = TEAM_FOLDER) -> Tuple[Mapping, Optional[Mapping]]:
    """``cortex.toml`` at ``root``, and ``cortex.local.toml`` unless ``local`` is off — or ``None``
    when it is not there — each checked with the grammar. Raises ``project.ProjectFileError`` naming
    the file; ``OSError`` for a file that cannot be opened. The one reader of the two files, the
    command's and ``cortex config``'s. Beside a team's file — in the team's folder ``folder`` — the
    ``version`` and ``theme`` of ``cortex.toml`` may be the file's: ``resolve`` says whether they are."""
    data = _checked_toml(os.path.join(root, PROJECT_FILE), PROJECT_FILE)
    team = team_file_there(root, folder)
    project.check_project(data, project.PROJECT_REQUIRED if team else ())
    local_data = None
    path = os.path.join(root, LOCAL_FILE)
    if local and os.path.isfile(path):
        local_data = _checked_toml(path, LOCAL_FILE)
        if lenient and "synced" in local_data:
            try:
                project.check_synced(local_data["synced"])
            except project.ProjectFileError:
                local_data = {key: value for key, value in local_data.items() if key != "synced"}
                local_data["synced"] = UNREADABLE_SYNCED
        project.check_local({key: value for key, value in local_data.items() if value is not UNREADABLE_SYNCED},
                            project.lists_1_7_0(data))
    return data, local_data


def read_machine(path: str, name: Optional[str] = None) -> Tuple[Dict[str, object], List[str]]:
    """The keys of the machine's file at ``path`` that read, and the warnings: a file that cannot be
    read is one, never an error (§3.7). No file, no keys."""
    name = name or path
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return {}, [f"{name} is not a file, and is not read"]
        data = _toml(path)
    except FileNotFoundError:
        return {}, []
    except OSError as error:                # a home this user cannot search, say
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


def loose_agents(root: str) -> Optional[object]:
    """``cortex.toml``'s ``agents`` at ``root``, as written — read before any other key, with the
    versions (§3.8, step 1), to find the team's file. ``None`` when the file does not read or sets none:
    its reader checks the value, and whether ``cortex.toml`` is committed (§3.6)."""
    try:
        return _toml(os.path.join(root, PROJECT_FILE)).get("agents")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError):
        return None


def loose_versions(root: str, folder: str = TEAM_FOLDER) -> Dict[str, Tuple[str, str]]:
    """Each file's ``version`` that reads, by its role — ``project``, ``team``, ``local`` — as
    ``(name, version)``, read before any other key is checked (§3.8, step 1): what a too-new version
    is said from, and what ``cortex prune`` keeps. The team's file is in the team's folder ``folder``."""
    found: Dict[str, Tuple[str, str]] = {}
    for role, name in (("project", PROJECT_FILE), ("team", team_name(folder)), ("local", LOCAL_FILE)):
        try:
            version = _toml(os.path.join(root, name)).get("version")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError):
            continue
        if project.is_version(version):
            found[role] = (name, version)
    return found
