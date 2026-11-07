"""``cortex.toml`` and ``cortex.local.toml`` — ADR-008 §3.4.

``cortex.toml`` is committed at the project root: the Cortex ``version`` it uses, the team's
``theme``, and optionally how ``sync`` brings the spec to it and whether Claude Code may read it
without asking (``claude_access``). ``cortex.local.toml``, next to it and ignored by git, is this
developer on this machine: a ``theme`` and a ``claude_access`` of their own, and the ``spec``
``cortex sync`` writes. An unknown key is an error, never a warning: these are the files a later
ADR extends, and a typo that silently does nothing is the failure they must not start with.

``cortex.lock``, next to ``cortex.toml`` and committed, is the spec the project resolved: its
version and its digest (ADR-010 §3.2). ``cortex sync`` writes it whole; nobody edits it.

What a project reads of each key — the local file's over ``cortex.toml``'s, over the team's
``cortex.team.toml`` in its folder — is the core's resolution (``cortex_core.cascade``, ADR-013
§3.1), the one the runtime reads with too. Its version is the higher of the team's and the
developer's (§3.3); the lock of a version is beside the file it comes from (§3.4).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from cortex_core import cascade, project
from cortex_core.project import (LOCAL_FILE, LOCK_FILE, MODES, PROJECT_FILE, TEAM_FILE, TEAM_FOLDER,  # noqa: F401
                                  TEAM_LOCK_FILE)

from . import store
from .semver import Version

PROJECT_HEADER = "# Written by `cortex init`. Committed: the Cortex version this project uses, and the team's theme.\n"
LOCAL_HEADER = ("# This developer, on this machine — ignored by git (ADR-008 §3.4).\n"
                "# `cortex sync` writes spec. A theme set here overrides cortex.toml's.\n")
LOCK_HEADER = "# Written by `cortex sync`: the spec this project resolved. Committed — do not edit (ADR-010 §3.2).\n"


class ConfigError(Exception):
    """A configuration file cortex refuses — the message names the file and the key."""


TEAM_NAME = cascade.TEAM_NAME
TEAM_LOCK_NAME = f"{TEAM_FOLDER}/{TEAM_LOCK_FILE}"


@dataclass(frozen=True)
class Project:
    root: str                  # the directory holding cortex.toml, as the shell names it
    version: Version           # what this machine reads: the higher of the team's version and the developer's
    theme: Optional[str]       # cortex.toml's, when set — a team's file may give it instead
    sync: Optional[str]        # cortex.toml's sync, when set
    local_theme: Optional[str]
    spec: Optional[str]        # cortex.local.toml's spec, when synced
    claude_access: Optional[bool] = None        # cortex.toml's, when set
    local_claude_access: Optional[bool] = None  # cortex.local.toml's, which wins
    claude_entry: Optional[str] = None          # the entry sync keeps in Claude Code's settings
    settings: Optional[cascade.Resolution] = None   # the project's keys, each with its origin, as load resolved them
    team_settings: Optional[cascade.Resolution] = None  # the team's resolution: what the team commits follows
    synced: Optional[Dict[str, str]] = None     # what `cortex sync` recorded (ADR-013 §3.1)

    def _settings(self) -> cascade.Resolution:
        """The resolution ``load`` made — or, of a project another reader built, the one its fields make."""
        return self.settings if self.settings is not None else _resolved(self)

    @property
    def active_theme(self) -> str:
        return str(self._settings().value("theme"))

    @property
    def version_origin(self) -> str:
        """The file the version this machine reads comes from."""
        return self._settings()["version"].origin

    @property
    def team_version(self) -> Version:
        """The team's version (ADR-013 §3.1): what the team commits follows, and what CI checks."""
        found = self._settings().team_version
        return Version(str(found.value)) if found is not None else self.version

    @property
    def team_origin(self) -> str:
        found = self._settings().team_version
        return found.origin if found is not None else PROJECT_FILE

    @property
    def developer_version(self) -> Optional[cascade.Setting]:
        return self._settings().developer_version

    @property
    def team_file(self) -> bool:
        """The team's file is read: there, and the team's version 1.8.0 or later."""
        return self._settings().team

    @property
    def team_theme(self) -> str:
        """The theme of the team's resolution: what a committed bootstrap's personality block follows."""
        return str((self.team_settings or self._settings()).value("theme"))

    @property
    def follows(self) -> bool:
        """The project follows the team's file (ADR-013 §3.1): there is one, and ``cortex.toml`` holds no
        committed ``version`` — an uncommitted one, at a 2.B root, included."""
        return self.team_file and self.team_origin == TEAM_NAME

    def lock_dir(self, origin: Optional[str] = None) -> Optional[str]:
        """Where the lock of the version from ``origin`` — the version read, by default — is
        (ADR-013 §3.4): beside ``cortex.toml``, beside the team's file, or nowhere for a local try."""
        origin = origin or self.version_origin
        if origin == LOCAL_FILE:
            return None
        return os.path.join(self.root, TEAM_FOLDER) if origin == TEAM_NAME else self.root

    @property
    def active_claude_access(self) -> Optional[bool]:
        """``None`` while neither file sets it: Claude Code's settings are then left alone."""
        return self._settings().value("claude_access")     # type: ignore[return-value]

    def with_claude_access(self, value: bool) -> "Project":
        """This project with this developer's ``claude_access`` — ``cortex sync --claude-access``, written
        with ``spec`` — over ``cortex.toml``'s."""
        changed = dataclasses.replace(self, local_claude_access=value, settings=None)
        return dataclasses.replace(changed, settings=_resolved(changed))

    def spec_directory(self) -> Optional[str]:
        """The directory ``spec`` names: absolute as written, or relative to the project root."""
        if self.spec is None:
            return None
        spec = self.spec
        return spec if os.path.isabs(spec) else f"{self.root}/{spec}"


def _resolved(found: Project) -> cascade.Resolution:
    data = {**({"version": str(found.version)} if found.version is not None else {}), "theme": found.theme or "none",
            **({"sync": found.sync} if found.sync else {}),
            **({"claude_access": found.claude_access} if found.claude_access is not None else {})}
    local = {**({"theme": found.local_theme} if found.local_theme else {}),
             **({"claude_access": found.local_claude_access} if found.local_claude_access is not None else {})}
    return cascade.resolve(data, local, {}, {}, machine=False)


@dataclass
class Lock:
    version: Version
    digest: str


def find_project(start: str) -> Optional[str]:
    """The directory holding ``cortex.toml``: ``start`` or its nearest ancestor that does."""
    current = start
    while True:
        if os.path.isfile(os.path.join(current, PROJECT_FILE)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _read(path: str) -> Dict:
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig")    # a byte order mark, as Notepad writes one
    except UnicodeDecodeError:
        raise ConfigError(f"{os.path.basename(path)}: not UTF-8 text")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{os.path.basename(path)}: {error}")


def in_no_repository(root) -> bool:
    """No ``.git`` at ``root`` or above it — as written or resolved, git or not on this machine: what Cortex
    writes there is committed nowhere, the root being no work tree (ADR-008 §9)."""
    return not any(os.path.lexists(os.path.join(directory, ".git"))
                   for start in {str(root), os.path.realpath(root)}
                   for directory in (start, *map(str, Path(start).parents)))


def committed(root) -> bool:
    """Whether ``cortex.toml`` at ``root`` is committed (ADR-013 §3.1): git tracks it — ADR-011 §3.5's
    test. Without git on ``PATH``, or with a git that refuses — a repository another user owns, say —
    a ``.git`` at or above the root counts. A ``cortex.toml`` git does not track, in a repository, is
    not committed: the developer's own."""
    git = shutil.which("git")
    if git is not None:
        try:
            proc = subprocess.run([git, "-C", str(root), "ls-files", "--error-unmatch", "--", PROJECT_FILE],
                                  capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode in (0, 1):
            return proc.returncode == 0                     # 1: a pathspec git does not know — untracked
    return not in_no_repository(root)


def team_folder_state(root: str) -> Optional[str]:
    """What is wrong with the default team's folder, ``agents/``, as a version-less ``cortex.toml``
    needs it — ``None`` when nothing: missing, or an empty directory, a submodule not checked out."""
    folder = os.path.join(root, TEAM_FOLDER)
    if not os.path.isdir(folder):
        return f"{TEAM_FOLDER}/ is not there"
    try:
        if not os.listdir(folder):
            return f"{TEAM_FOLDER}/ is empty"
    except OSError:
        return None
    return None


def load(root: str) -> Project:
    """Read and validate the files of the project at ``root`` — with the core's reader and grammar,
    which the runtime reads them with too — and resolve its keys once, and the team's resolution.
    Whether ``cortex.toml`` is committed is asked of git only where a team's file is there: without
    one, the answer changes nothing (ADR-013 §3.1)."""
    try:
        data, found = cascade.read_project(root)
        team = cascade.read_team(root)
        known = committed(root) if team is not None else None
        local = found or {}
        try:
            settings = cascade.resolve(data, local, {}, {}, machine=False, team_data=team, committed=known)
        except project.ProjectFileError:
            missing = team_folder_state(root) if "version" not in data and team is None else None
            if missing is not None:
                raise ConfigError(f'{PROJECT_FILE}: "version" is required, or in {TEAM_NAME} — {missing}: check it out, '
                                  "a submodule with `git submodule update --init`")
            raise
        team_settings = cascade.resolve(data, local, {}, {}, machine=False, team_data=team, committed=known,
                                        locked=True)
    except project.ProjectFileError as error:
        raise ConfigError(str(error))
    synced = local.get("synced")
    return Project(root=root, version=Version(str(settings.value("version"))), theme=data.get("theme"),
                   sync=data.get("sync"), local_theme=local.get("theme"), spec=local.get("spec"),
                   claude_access=data.get("claude_access"), local_claude_access=local.get("claude_access"),
                   claude_entry=local.get("claude_entry"), settings=settings, team_settings=team_settings,
                   synced=dict(synced) if synced is not None else None)


def loose_pin(root: str) -> Optional[Tuple[str, Version]]:
    """The file the version the project resolves comes from, and that version, read before any other
    key is checked (ADR-013 §3.8, step 1) — ``None`` when none reads. The rules of §3.1 on the files'
    versions alone: a committed ``cortex.toml``'s wins over the team's file, and the developer's over
    the team's when it is higher. Git is asked only where a team's file is there."""
    found = cascade.loose_versions(root)
    if not found:
        return None
    pin, team, local = found.get("project"), found.get("team"), found.get("local")
    own = pin is not None and (team is None or committed(root))
    chosen = pin if own else team or pin
    for developer in (local, pin if not own and team is not None else None):
        if developer is not None and (chosen is None or Version(developer[1]) > Version(chosen[1])):
            chosen = developer
    return None if chosen is None else (chosen[0], Version(chosen[1]))


def too_new(root: str, own: Version) -> Optional[str]:
    """Why this cortex is too old for the project at ``root`` — or ``None`` — from the versions read
    before any other key (ADR-013 §3.8, steps 1–2): a key of a later version is then said as the
    version it comes with, which ``cortex self-update`` brings, not as a key this cortex does not
    know. Said as the store says it, which the bootstrap's rule acts on — and the file that holds the
    version, with its other remedy: ``--local`` for a try, ``--team`` for the team's pin."""
    found = loose_pin(root)
    if found is None or not store.too_old_for(found[1], own):
        return None
    name, version = found
    other = (f"\nor drop the try: cortex upgrade --local {own}" if name == LOCAL_FILE else
             f"\nor move the team's pin: cortex upgrade --team {own}" if name == TEAM_NAME else "")
    return (f"this project uses Cortex {version}, which its {name} pins, newer than this cortex, {own} — update it:\n"
            f"    {store.update_command(version)}{other}")


def local_spec(root: str) -> Optional[str]:
    """The ``spec`` of ``cortex.local.toml`` at ``root``, read alone: a project whose ``cortex.toml``
    is gone keeps it (#328). ``None`` when the file is not there, names none, or cannot be read."""
    path = os.path.join(root, LOCAL_FILE)
    if not os.path.isfile(path):
        return None
    try:
        local = _read(path)
        project.check_local(local)
    except (OSError, ConfigError, project.ProjectFileError):
        return None
    return local.get("spec")


def toml_string(value: str) -> str:
    """``value`` as a TOML basic string — JSON's escapes are TOML's."""
    return json.dumps(value, ensure_ascii=False)


def toml_value(value) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return ("true" if value else "false") if isinstance(value, bool) else toml_string(value)


def _read_text(path: str) -> Optional[str]:
    """The file as it is — its byte order mark too, which ``set_keys`` writes back."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError(f"{os.path.basename(path)}: not UTF-8 text")


def _comment(line: str, key: str) -> str:
    """The comment that ends ``line`` — with the blanks before it — or ``""``. A ``#`` inside a
    string is no comment: the comment starts at the first ``#`` before which the line reads as
    the key alone."""
    body = line.rstrip("\r\n")
    for index, char in enumerate(body):
        if char != "#":
            continue
        try:
            if key in tomllib.loads(body[:index]):
                return body[len(body[:index].rstrip()):]
        except tomllib.TOMLDecodeError:
            continue
    return ""


def set_keys(text: Optional[str], values: Dict[str, object], name: str, header: str,
             comments: Optional[Dict[str, str]] = None) -> str:
    """``text`` — the file ``name``, or ``None`` when it is not there yet — with each key of
    ``values`` set, or removed when its value is ``None``.

    ``tomllib`` reads TOML but does not write it: the one line that holds a key is rewritten, or
    added, and every other line — the developer's own keys, their comments, the file's line
    endings and byte order mark — is kept as it was, and so is the comment that ends the line
    rewritten. The result is read back: a key written some way a line cannot hold is refused.
    """
    comments = comments or {}
    if text is None:
        text = header
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for key, value in values.items():
        key_line = re.compile(rf"""^[ \t]*(?:{key}|"{key}"|'{key}')[ \t]*=""")
        if value is None:
            lines = [existing for existing in lines if not key_line.match(existing)]
            continue
        line = f"{key} = {toml_value(value)}"
        for index, existing in enumerate(lines):
            if key_line.match(existing):
                kept = _comment(existing, key) or (f"    # {comments[key]}" if key in comments else "")
                lines[index] = line + kept + existing[len(existing.rstrip("\r\n")):]
                break
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(line + (f"    # {comments[key]}" if key in comments else "") + newline)
    text = "".join(lines)
    try:
        written = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        written = {}
    for key, value in values.items():
        if written.get(key) != value or (value is None and key in written):
            raise ConfigError(f"{name}: its {key} is written in a way cortex cannot rewrite — "
                              "remove that line and run the command again")
    return bom + text


def render_spec(root: str, spec: str) -> str:
    """``cortex.local.toml`` with ``spec`` set — computed, not written, so that sync validates it
    before it changes anything in the project."""
    return set_keys(_read_text(os.path.join(root, LOCAL_FILE)), {"spec": spec}, LOCAL_FILE, LOCAL_HEADER,
                    {"spec": "written by `cortex sync`"})


def render_local(root: str, values: Dict[str, object], comments: Optional[Dict[str, str]] = None) -> str:
    """``cortex.local.toml`` with each key of ``values`` set, or removed — see ``set_keys``."""
    return set_keys(_read_text(os.path.join(root, LOCAL_FILE)), values, LOCAL_FILE, LOCAL_HEADER, comments)


def render_project(root: str, values: Dict[str, object]) -> str:
    """``cortex.toml`` with each key of ``values`` set — a new file when there is none — checked
    against the grammar before anything is written: beside a team's file, its ``version`` and
    ``theme`` may be the file's."""
    text = set_keys(_read_text(os.path.join(root, PROJECT_FILE)), values, PROJECT_FILE, PROJECT_HEADER)
    team = os.path.isfile(os.path.join(root, TEAM_FOLDER, TEAM_FILE))
    try:
        project.check_project(tomllib.loads(text.lstrip("\ufeff")), project.PROJECT_REQUIRED if team else ())
    except project.ProjectFileError as error:
        raise ConfigError(str(error))
    return text


def write_text(path: str, text: str) -> None:
    """Write ``text`` to ``path`` whole or not at all: a temporary file beside it, then a rename."""
    temporary = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def write_local_text(root: str, text: str) -> None:
    write_text(os.path.join(root, LOCAL_FILE), text)


def write_local(root: str, values: Dict[str, object], comments: Optional[Dict[str, str]] = None) -> None:
    """Set or remove keys of ``cortex.local.toml``, creating the file if needed."""
    write_local_text(root, render_local(root, values, comments))


def write_spec(root: str, spec: str) -> None:
    """Set ``spec`` in ``cortex.local.toml``, creating the file if needed."""
    write_local_text(root, render_spec(root, spec))


def load_lock(root: str, name: str = LOCK_FILE) -> Optional[Lock]:
    """The project's ``cortex.lock`` — or the lock ``name`` names under ``root``, the team's in its
    folder — checked with the core's grammar; ``None`` when there is none."""
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        return None
    again = "delete it, and `cortex sync` writes it again"     # a merge left conflict markers in it, say
    try:
        data = _read(path)
        project.check_lock(data)
    except ConfigError as error:
        raise ConfigError(f"{error} — {again}")
    except project.LaterFormatError as error:
        # Said as `cortex sync` says a version too new: the bootstrap has the assistant act on it.
        raise ConfigError(f"{error}: this cortex is older than the project's — update it:\n    cortex self-update")
    except project.ProjectFileError as error:
        raise ConfigError(f"{error} — {again}")
    return Lock(version=Version(data["version"]), digest=data["digest"])


def render_lock(version: Version, digest: str) -> str:
    return (LOCK_HEADER + f"format = {project.LOCK_FORMAT}\n" + f"version = {toml_string(str(version))}\n"
            + f"digest = {toml_string(digest)}\n")


def write_lock(root: str, version: Version, digest: str, name: str = LOCK_FILE) -> None:
    write_text(os.path.join(root, name), render_lock(version, digest))


def lock_of(found: Project, origin: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """The lock of the version from ``origin`` — the version read, by default: its directory, its
    name there, and how the project root names it — ``None`` for a local try, which has none."""
    directory = found.lock_dir(origin)
    if directory is None:
        return None
    team = os.path.normpath(directory) != os.path.normpath(found.root)
    return (directory, TEAM_LOCK_FILE, TEAM_LOCK_NAME) if team else (directory, LOCK_FILE, LOCK_FILE)


def render_team(root: str, values: Dict[str, object]) -> str:
    """The team's ``cortex.team.toml`` with each key of ``values`` set — ``cortex upgrade --team`` —
    checked against the grammar before anything is written."""
    path = os.path.join(root, TEAM_FOLDER, TEAM_FILE)
    text = set_keys(_read_text(path), values, TEAM_NAME, "")
    try:
        project.check_team(tomllib.loads(text.lstrip("\ufeff")), TEAM_NAME)
    except project.ProjectFileError as error:
        raise ConfigError(str(error))
    return text
