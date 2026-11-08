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

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from cortex_core import cascade, project


@dataclass(frozen=True)
class Pin:
    """What a project's files say the runtime needs: the team's version, and the team's theme — locked by
    the team's file, it wins over the deployment's ``CORTEX_THEME`` (ADR-013 §3.12)."""

    version: str
    theme: str
    theme_locked: bool = False

    def to_job(self) -> dict:
        return {"version": self.version, "theme": self.theme, "theme_locked": self.theme_locked}

    @classmethod
    def from_job(cls, data: Optional[Mapping[str, Any]]) -> Optional["Pin"]:
        return None if data is None else cls(version=data["version"], theme=data["theme"],
                                             theme_locked=bool(data.get("theme_locked", False)))


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
    team = cascade.read_team(str(root))
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
        project.check_project(data, project.PROJECT_REQUIRED if team is not None else ())
        if isinstance(team, cascade.Unreadable):
            raise project.ProjectFileError(team.error)
        found = cascade.resolve(data, None, {}, {}, locked=True, team_data=team)
    except OSError as error:
        raise ValueError(f"{project.PROJECT_FILE} of this workspace cannot be read: {error.strerror or error}")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{project.PROJECT_FILE} of this workspace cannot be read: {error}")
    except project.ProjectFileError as error:
        raise ValueError(f"{error} — cortex sync refuses this file too")
    team_version = None if not found.team or team is None else team.get("version")
    version = _team_version(data.get("version"), team_version, _local(Path(root)))
    return Pin(version=version, theme=str(found.value("theme")), theme_locked=found["theme"].locked)


def _local(root: Path) -> Mapping[str, Any]:
    """Of ``cortex.local.toml``, ``version`` and ``synced`` — what a 1.7.0 runtime never read, so a file that
    does not read is none, as it was."""
    try:
        data = tomllib.loads((root / project.LOCAL_FILE).read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return {key: data[key] for key in ("version", "synced") if key in data}


def _team_version(pin: Optional[str], team: Optional[str], local: Mapping[str, Any]) -> str:
    """The team's version (ADR-013 §3.12), with no git to ask: ``synced``'s ``pinned``, once each file's
    ``version`` is still what ``cortex sync`` recorded; without ``synced``, ``cortex.toml``'s when it pins
    one, else the team's file's. ``ValueError`` — a ``422`` naming ``cortex sync`` — when they differ, and,
    naming the key, when the version this machine's ``cortex`` reads is a developer's try above it."""
    tried = local.get("version")
    if tried is not None and not project.is_version(tried):
        raise ValueError(f'{project.LOCAL_FILE}: "version" is no version — cortex sync refuses this file too')
    synced = local.get("synced")
    if synced is not None:
        try:
            project.check_synced(synced)
        except project.ProjectFileError as error:
            raise ValueError(f"{error} — run `cortex sync` in the project, which writes it again")
        now = {name: value for name, value in (("team", team), ("project", pin), ("local", tried)) if value is not None}
        recorded = {name: synced[name] for name in ("team", "project", "local") if name in synced}
        if now != recorded:
            raise ValueError(f"the versions this workspace's files pin are not those `cortex sync` recorded in "
                             f"{project.LOCAL_FILE} — a teammate moved one: run `cortex sync` in the project")
        version, reads = synced["pinned"], synced["reads"]
        if reads and reads != version:
            which = f"{project.LOCAL_FILE}'s version" if tried == reads else f"{project.PROJECT_FILE}'s version"
            raise ValueError(f"this machine's cortex reads Cortex {reads}, {which} — a try — and the runtime the team's "
                             f"{version}: remove the try, or move the team to it, to run")
        return version
    if pin is not None and team is not None and pin != team:
        raise ValueError(f"{project.PROJECT_FILE} pins Cortex {pin} and {cascade.TEAM_NAME} Cortex {team}, and "
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
