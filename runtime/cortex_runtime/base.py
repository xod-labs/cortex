"""Where a project's base is — ADR-008 §3.6, amending ADR-002 §3.4.

A project that uses the ``cortex`` binary holds no spec: its ``cortex.toml`` pins a version, and
the spec of that version is in the store, ``{CORTEX_HOME}/versions/{version}``. The runtime reads
the store itself — never ``spec`` in ``cortex.local.toml``, a path of the developer's machine that
the runtime's container does not see. A project without ``cortex.toml`` keeps its base where it
always was, ``{root}/cortex``.

``cortex.toml`` is read with the core's grammar, the one ``cortex sync`` reads it with: a file one
refuses, the other refuses too. Its values are the core's resolution (ADR-013 §3.1) — the team's,
which leaves the developer's ``cortex.local.toml`` out, and asks no git, which the container's
read-only mount of another user's files may refuse (§3.12). Its version is the team's: what ``cortex
sync`` recorded in ``synced``, once each file still says what it recorded — of ``cortex.local.toml``
the runtime reads ``version`` and ``synced`` only, never ``spec``, a path of the developer's machine.
"""

from __future__ import annotations

import logging
import os
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from cortex_core import cascade, project, workspace

logger = logging.getLogger("cortex_runtime.base")


@dataclass(frozen=True)
class Pin:
    """What a project's files say the runtime needs: the team's version, and the team's theme — locked by
    the team's file, it wins over the deployment's ``CORTEX_THEME`` (ADR-013 §3.12) — and the team's
    folder, ``cortex.toml``'s ``agents``, where the workspace's overlays and the team's context are (§3.6)."""

    version: str
    theme: str
    theme_locked: bool = False
    team_folder: str = project.TEAM_FOLDER

    def to_job(self) -> dict:
        return {"version": self.version, "theme": self.theme, "theme_locked": self.theme_locked,
                "team_folder": self.team_folder}

    @classmethod
    def from_job(cls, data: Optional[Mapping[str, Any]]) -> Optional["Pin"]:
        return None if data is None else cls(version=data["version"], theme=data["theme"],
                                             theme_locked=bool(data.get("theme_locked", False)),
                                             team_folder=str(data.get("team_folder", project.TEAM_FOLDER)))


def cortex_home() -> Path:
    home = os.environ.get("CORTEX_HOME")
    return Path(home) if home else Path.home() / ".cortex"


def read_pin(root: Path) -> Optional[Pin]:
    """The version and theme the project at ``root`` pins, or ``None`` when it has no
    ``cortex.toml``.

    Raises ``ValueError`` — a ``422`` at the API — for a ``cortex.toml`` that is not one.
    """
    path = Path(root) / project.PROJECT_FILE
    if not os.path.lexists(path):
        return None
    if not path.is_file():
        raise ValueError(f"{project.PROJECT_FILE} of this workspace is not a file")
    remedy = "cortex sync refuses this file too"
    alone = None        # before 1.8.0, what 1.7.0's grammar — every key required — says of cortex.toml read alone
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
        local = _local(Path(root))          # after cortex.toml, as the command reads them
        before = project.is_version(data.get("version")) and project.before_team(data["version"])
        if before:
            # Below 1.8.0 nothing of ADR-013 is read (§3.8): committed, `cortex sync` reads cortex.toml alone, and
            # refuses it with 1.7.0's grammar, whatever a `synced` left from another version says (§3.12). Nor is
            # the team's file read, but to say what a cortex.toml 1.7.0 refuses lacks.
            try:
                project.check_project(data)
            except project.ProjectFileError as error:
                alone = error
                lacking = tuple(key for key in project.PROJECT_REQUIRED if key not in data)
                if _gives_what_it_lacks(Path(root), data):
                    # Mid-adoption: of a cortex.toml no commit holds, `cortex sync` reads the team's file instead
                    # (§3.3), which may give what this one lacks — committed, it is read alone, and refused. A file
                    # it would refuse whatever the team's gives — a theme out of the store, a key it does not know —
                    # keeps the remedy, as a team's file that gives nothing in its place does.
                    remedy = (f"run `cortex sync` in the project, which may read {cascade.TEAM_NAME} instead of a "
                              f"{project.PROJECT_FILE} not committed — committed, it needs its {project.listed(lacking)}")
                if local.get("synced") is None:
                    raise
        if local.get("synced") is None and before:
            # `cortex sync` records no `synced` for it (§3.1): the version is cortex.toml's alone, as 1.7.0 read it
            # — a 422 there no sync could ever clear (§9).
            found = cascade.resolve(data, None, {}, {}, locked=True)
            return Pin(version=_team_version(data["version"], None, local), theme=str(found.value("theme")))
        # The team's folder cortex.toml names, inside the root the runtime mounts (§3.6) — asked of no git,
        # whose committedness the command checks: its text, and what the file system says of it.
        folder = project.check_agents(data["agents"]) if "agents" in data else project.TEAM_FOLDER
        synced = local.get("synced")
        # pinned other than cortex.toml's: the team's file won over an uncommitted cortex.toml (decision 24 lets a
        # committed one win), whose keys the team's resolution leaves out where the file sets them (§3.1).
        committed = False if isinstance(synced, dict) and "project" in synced \
            and synced.get("pinned") != synced["project"] else None
        if folder != project.TEAM_FOLDER:
            workspace.check_team_folder(str(root), folder)
        if folder != project.TEAM_FOLDER and committed is False:
            raise project.ProjectFileError(project.AGENTS_UNCOMMITTED)      # as the command refuses it (§9)
        if folder != project.TEAM_FOLDER and not (isinstance(synced, dict) and synced.get("agents") == folder):
            # Read from a committed cortex.toml only, which sync checks with git and records (§3.1, §9).
            raise ValueError(f"{project.PROJECT_FILE}: agents {project.shown(folder)} is not the team's folder "
                             f"`cortex sync` recorded in {project.LOCAL_FILE} — run `cortex sync` in the project, "
                             f"which reads it from a committed {project.PROJECT_FILE} only")
        recorded = synced.get("agents") if isinstance(synced, dict) else None
        if folder == project.TEAM_FOLDER and recorded is not None and recorded != folder:
            # The folder sync recorded is gone from cortex.toml: the template reads the recorded one (§3.10), and
            # the runtime would serve another — a sync tells which, once more.
            raise ValueError(f"{project.LOCAL_FILE}: the team's folder `cortex sync` recorded, {project.shown(recorded)}, "
                             f"is not the one {project.PROJECT_FILE} names, {folder}/ — run `cortex sync` in the project")
        name = cascade.team_name(folder)
        team = cascade.read_team(str(root), folder)    # never raises: what is there and unread is an Unreadable
        project.check_project(data, project.PROJECT_REQUIRED if team is not None else ())
        if before and alone is None:
            # A synced left from another version: `cortex sync` reads a committed cortex.toml before 1.8.0 alone, and
            # records none (§3.8) — what the team's file holds, it clears; not committed, it reads that file too.
            remedy = (f"run `cortex sync` in the project, which reads a committed {project.PROJECT_FILE} before "
                      f"{project.FIRST_TEAM_VERSION} alone — not committed, it refuses this file too")
        if isinstance(team, cascade.Unreadable):
            raise project.ProjectFileError(team.error)
        found = cascade.resolve(data, None, {}, {}, locked=True, team_data=team, committed=committed, team_name=name)
    except OSError as error:
        raise ValueError(f"{project.PROJECT_FILE} of this workspace cannot be read: {error.strerror or error}")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{project.PROJECT_FILE} of this workspace cannot be read: {error}")
    except project.ProjectFileError as error:
        raise ValueError(f"{error} — {remedy}")
    team_version = None if not found.team or team is None else team.get("version")
    try:
        version = _team_version(data.get("version"), team_version, local, name)
    except Unrecorded:
        if alone is None:
            raise
        # The record is another version's, and the sync it names refuses or replaces cortex.toml as 1.7.0 reads it.
        # Only that refusal: a try, a version that is none, keep their own — the sync clears neither.
        raise ValueError(f"{alone} — {remedy}") from None
    return Pin(version=version, theme=str(found.value("theme")), theme_locked=found["theme"].locked, team_folder=folder)


def _gives_what_it_lacks(root: Path, data: Mapping[str, Any]) -> bool:
    """Whether ``cortex.toml`` fails 1.7.0's grammar only for a key the team's file gives instead — the one
    case a sync that reads the team's file clears (ADR-013 §3.12): a team's file of its own grammar, which
    pins a version — 1.8.0 or later, above this one — and sets each required key ``cortex.toml`` lacks. Never
    one that names a team's folder: a sync reads ``agents`` from a committed ``cortex.toml`` of 1.8.0 or later
    only, and refuses this one (§3.6)."""
    if "agents" in data:
        return False
    try:
        project.check_project(data, project.PROJECT_REQUIRED)
    except project.ProjectFileError:
        return False
    team = cascade.read_team(str(root))
    if team is None or isinstance(team, cascade.Unreadable):
        return False
    try:
        project.check_team(team)
    except project.ProjectFileError:
        return False
    return project.is_version(team.get("version")) and all(key in data or key in team
                                                           for key in project.PROJECT_REQUIRED)


class LocalUnreadable(ValueError):
    """``cortex.local.toml`` opens and does not parse: a ``422`` naming the file and what the command says of it."""


def _local(root: Path) -> Mapping[str, Any]:
    """Of ``cortex.local.toml``, ``version`` and ``synced``. A file that opens and does not parse — a conflict a
    pull left, a typo — is refused as ``cortex sync`` refuses it, a ``LocalUnreadable`` (#344): the command
    refuses everything until it is fixed. What is no file — a directory, a named pipe, never waited on — is none,
    and said nowhere, as ``cortex config`` reads none there (#375). A file the runtime cannot open — a container's
    mount, another user's — is none, as a 1.7.0 runtime, which never read it, served it: said in the log, once per
    error."""
    path = root / project.LOCAL_FILE
    try:
        raw = cascade.read_file(str(path))
    except FileNotFoundError:
        _unread(path, None)
        return {}
    except OSError as error:
        if not os.path.isfile(path):        # the CLI's own test: no file there, which it reads as none (read_project)
            _unread(path, None)
            return {}
        if _unread(path, (error.errno, error.strerror or str(error))):
            logger.warning("%s cannot be read, and is not: %s", path, error.strerror or error)
        return {}
    _unread(path, None)
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise LocalUnreadable(f"{project.LOCAL_FILE}: not UTF-8 text — cortex sync refuses this file too") from None
    except tomllib.TOMLDecodeError as error:
        raise LocalUnreadable(f"{project.LOCAL_FILE}: {error} — cortex sync refuses this file too") from None
    return {key: data[key] for key in ("version", "synced") if key in data}


# The error each cortex.local.toml the runtime could not open last gave (#375): it is read at every request, and its
# warning is said once per error — again once the file was read, or gone, in between.
_UNREAD: dict = {}
_UNREAD_LOCK = threading.Lock()


def _unread(path: Path, error: Optional[tuple]) -> bool:
    """Records ``error`` — ``None`` once the file at ``path`` is read, or gone — as what ``path`` last gave;
    whether it differs from what it gave before: a warning to say."""
    with _UNREAD_LOCK:
        if error is None:
            _UNREAD.pop(str(path), None)
            return False
        if _UNREAD.get(str(path)) == error:
            return False
        _UNREAD[str(path)] = error
        return True


class Unrecorded(ValueError):
    """The versions the workspace's files pin are not those ``cortex sync`` recorded: a ``422`` naming the sync."""


def _team_version(pin: Optional[str], team: Optional[str], local: Mapping[str, Any],
                  team_name: str = cascade.TEAM_NAME) -> str:
    """The team's version (ADR-013 §3.12), with no git to ask: ``synced``'s ``pinned``, once each file's
    ``version`` is still what ``cortex sync`` recorded; without ``synced``, ``cortex.toml``'s when it pins
    one — alone before 1.8.0, ``team`` then ``None`` — else the team's file's. ``ValueError`` — a ``422``
    naming ``cortex sync`` — when they differ, and, naming the key, when the version this machine's ``cortex``
    reads is a developer's try above it."""
    tried = local.get("version")
    if tried is not None and not project.is_version(tried):
        raise ValueError(f'{project.LOCAL_FILE}: "version" is no version — cortex sync refuses this file too')
    synced = local.get("synced")
    if synced is not None:
        try:
            project.check_synced(synced)
        except project.ProjectFileError as error:       # its message names `cortex sync`, which writes it again
            raise ValueError(str(error)) from None
        now = {name: value for name, value in (("team", team), ("project", pin), ("local", tried)) if value is not None}
        recorded = {name: synced[name] for name in ("team", "project", "local") if name in synced}
        if now != recorded:
            raise Unrecorded(f"the versions this workspace's files pin are not those `cortex sync` recorded in "
                             f"{project.LOCAL_FILE} — a teammate moved one: run `cortex sync` in the project")
        version, reads = synced["pinned"], synced["reads"]
        if reads and reads != version:
            which = f"{project.LOCAL_FILE}'s version" if tried == reads else f"{project.PROJECT_FILE}'s version"
            raise ValueError(f"this machine's cortex reads Cortex {reads}, {which} — a try — and the runtime the team's "
                             f"{version}: remove the try, or move the team to it, to run")
        return version
    if pin is not None and team is not None and pin != team:
        raise ValueError(f"{project.PROJECT_FILE} pins Cortex {pin} and {team_name} Cortex {team}, and "
                         f"`cortex sync` recorded neither: run `cortex sync` in the project")
    version = pin if pin is not None else team
    if tried is not None and project.version_key(tried) > project.version_key(version):
        raise ValueError(f"this machine's cortex reads Cortex {tried}, {project.LOCAL_FILE}'s version — a try — and the "
                         f"runtime the team's {version}: remove the try, or move the team to it, to run")
    return version


def base_for(pin: Optional[Pin]) -> Optional[Path]:
    """The store's copy of the pinned version, or ``None`` — the default ``{root}/cortex`` —
    without a pin.

    Raises ``ValueError`` when the store does not hold that version: the run would otherwise
    resolve against no spec at all.
    """
    if pin is None:
        return None
    base = cortex_home() / "versions" / pin.version
    if not (base / "agents").is_dir():
        raise ValueError(f"this workspace uses Cortex {pin.version}, which is not in the store the runtime reads "
                         f"({cortex_home()}/versions): run `cortex sync` in the project, and mount the store "
                         "— CORTEX_STORE_PATH in deploy/.env")
    return base


def base_root_for(root: Path) -> Optional[Path]:
    """The base of the project at ``root``, as its ``cortex.toml`` says now."""
    return base_for(read_pin(root))
