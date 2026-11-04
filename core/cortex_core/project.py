"""The grammar of ``cortex.toml`` and ``cortex.local.toml`` — ADR-008 §3.4 — of
``cortex.lock`` — ADR-010 §3.2 — and of the machine's ``projects.toml`` — ADR-011 §3.1.

One definition for the readers, the ``cortex`` command, the runtime and the cockpit, so that none
accepts a file another refuses. It checks what a TOML parser returned: each reader parses the file
with its own ``tomllib``. It reads nothing else: a check that needs the file system is the reader's.

Versions and theme names are ASCII, matched whole: ``\\d`` would take the digits of any script,
and ``$`` a trailing newline — ``"1.0.0\\n"`` would then name a directory of the store.
"""

from __future__ import annotations

import json
import os
import re
from typing import Mapping, Tuple

PROJECT_FILE = "cortex.toml"
LOCAL_FILE = "cortex.local.toml"
MODES = ("store", "link", "copy")
PROJECT_KEYS: Tuple[str, ...] = ("version", "theme", "sync", "claude_access")
PROJECT_REQUIRED: Tuple[str, ...] = ("version", "theme")
LOCAL_KEYS: Tuple[str, ...] = ("theme", "spec", "claude_access", "claude_entry")
BOOLEAN_KEYS: Tuple[str, ...] = ("claude_access",)
LOCK_FILE = "cortex.lock"
LOCK_KEYS: Tuple[str, ...] = ("format", "version", "digest")
LOCK_FORMAT = 1
# The digest of a spec: SHA-256, lowercase hex.
DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
REGISTRY_FILE = "projects.toml"
REGISTRY_KEYS: Tuple[str, ...] = ("format", "project")
REGISTRY_ENTRY_KEYS: Tuple[str, ...] = ("name", "path")
REGISTRY_FORMAT = 1
# The name of a project of the registry: lowercase, so that two never differ by case alone.
PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}", re.ASCII)
PROJECT_NAME_RULE = 'lowercase letters, digits, ".", "_" and "-", 64 at most, the first a letter or a digit'

_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
# X.Y.Z, or a pre-release, as Semantic Versioning writes them; no build metadata.
VERSION = re.compile(rf"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?",
                     re.ASCII)
# A theme names a directory: a name, never a path.
THEME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)


class ProjectFileError(ValueError):
    """A file refused — the message names the file and the key."""


class LaterFormatError(ProjectFileError):
    """A ``cortex.lock``, or a ``projects.toml``, of a format a later Cortex writes."""


def is_version(value: object) -> bool:
    return isinstance(value, str) and VERSION.fullmatch(value) is not None


def is_theme(value: object) -> bool:
    return isinstance(value, str) and THEME.fullmatch(value) is not None


def is_project_name(value: object) -> bool:
    return isinstance(value, str) and PROJECT_NAME.fullmatch(value) is not None


def directory_key(path: str) -> str:
    """``path`` as two entries of ``projects.toml`` that name one directory write it alike: ``.``,
    ``..`` and a final separator undone, and the case, where the system ignores it. Read from the
    text alone — a link is the file system's, which the reader resolves."""
    return os.path.normcase(os.path.normpath(path))


def shown(value: object) -> str:
    """A value of the file, quoted and escaped as JSON writes it: a committed ``cortex.toml`` may
    hold a newline or a terminal's control sequence, which a message must not print raw."""
    return json.dumps(value)


def listed(keys: Tuple[str, ...]) -> str:
    return ", ".join(keys[:-1]) + f" and {keys[-1]}" if len(keys) > 1 else keys[0]


def _check_keys(name: str, data: Mapping, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> None:
    for key in data:
        if key not in allowed:
            raise ProjectFileError(f'{name}: unknown key {shown(key)} — {name} takes {listed(allowed)}'
                                   + (" only" if name == LOCAL_FILE else ""))
    for key in required:
        if key not in data:
            raise ProjectFileError(f'{name}: "{key}" is required')
    for key, value in data.items():
        if key in BOOLEAN_KEYS:
            if not isinstance(value, bool):
                raise ProjectFileError(f'{name}: "{key}" must be true or false, without quotes')
            continue
        if not isinstance(value, str):
            raise ProjectFileError(f'{name}: "{key}" must be a string, in quotes')
        if not value:
            raise ProjectFileError(f'{name}: "{key}" is empty')


def _check_theme(name: str, theme: str) -> None:
    if not is_theme(theme):
        raise ProjectFileError(f'{name}: theme {shown(theme)} is no theme name — letters, digits, ".", "_" and "-"')


def check_project(data: Mapping) -> None:
    """Refuse a ``cortex.toml`` that is not one — ``ProjectFileError`` names the file and the key."""
    _check_keys(PROJECT_FILE, data, PROJECT_KEYS, PROJECT_REQUIRED)
    if not is_version(data["version"]):
        raise ProjectFileError(f'{PROJECT_FILE}: version {shown(data["version"])} is no version — X.Y.Z, for instance 1.0.0')
    _check_theme(PROJECT_FILE, data["theme"])
    if "sync" in data and data["sync"] not in MODES:
        raise ProjectFileError(f'{PROJECT_FILE}: sync {shown(data["sync"])} is none of {listed(MODES)}')


def check_local(data: Mapping) -> None:
    """Refuse a ``cortex.local.toml`` that is not one."""
    _check_keys(LOCAL_FILE, data, LOCAL_KEYS, ())
    if "theme" in data:
        _check_theme(LOCAL_FILE, data["theme"])


def check_lock(data: Mapping) -> None:
    """Refuse a ``cortex.lock`` that is not one: every key required, none other, ``format`` an
    integer this Cortex reads — ``LaterFormatError`` for a later one."""
    for key in data:
        if key not in LOCK_KEYS:
            raise ProjectFileError(f"{LOCK_FILE}: unknown key {shown(key)} — {LOCK_FILE} takes {listed(LOCK_KEYS)} only")
    for key in LOCK_KEYS:
        if key not in data:
            raise ProjectFileError(f'{LOCK_FILE}: "{key}" is required')
    form = data["format"]
    if isinstance(form, bool) or not isinstance(form, int) or form < 1:
        raise ProjectFileError(f'{LOCK_FILE}: "format" must be a positive integer, without quotes')
    if form > LOCK_FORMAT:
        raise LaterFormatError(f"{LOCK_FILE}: format {form} is written by a later Cortex — this one reads format {LOCK_FORMAT}")
    if not is_version(data["version"]):
        raise ProjectFileError(f'{LOCK_FILE}: version {shown(data["version"])} is no version — X.Y.Z, for instance 1.0.0')
    if not isinstance(data["digest"], str) or DIGEST.fullmatch(data["digest"]) is None:
        raise ProjectFileError(f'{LOCK_FILE}: digest {shown(data["digest"])} is no digest — "sha256:" and 64 lowercase '
                               "hexadecimal digits")


def check_registry(data: Mapping, name: str = REGISTRY_FILE) -> None:
    """Refuse a ``projects.toml`` that is not one (ADR-011 §3.1): ``format`` an integer this
    Cortex reads — ``LaterFormatError`` for a later one, whatever else it holds — and each
    ``[[project]]`` exactly a ``name`` and an absolute ``path``, no two with the same name, nor
    writing the same directory (``directory_key``). ``name`` is how the messages name the file: it
    is not in a project, and its reader names where it is."""
    if "format" not in data:
        raise ProjectFileError(f'{name}: "format" is required')
    form = data["format"]
    if isinstance(form, bool) or not isinstance(form, int) or form < 1:
        raise ProjectFileError(f'{name}: "format" must be a positive integer, without quotes')
    if form > REGISTRY_FORMAT:
        raise LaterFormatError(f"{name}: format {form} is written by a later Cortex — this one reads format "
                               f"{REGISTRY_FORMAT}")
    for key in data:
        if key not in REGISTRY_KEYS:
            raise ProjectFileError(f"{name}: unknown key {shown(key)} — {REGISTRY_FILE} takes {listed(REGISTRY_KEYS)} "
                                   "only")
    entries = data.get("project", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ProjectFileError(f'{name}: "project" must be tables, each headed [[project]]')
    names: dict = {}
    paths: dict = {}
    for number, entry in enumerate(entries, 1):
        where = f"{name}: project {number}"
        for key in entry:
            if key not in REGISTRY_ENTRY_KEYS:
                raise ProjectFileError(f"{where}: unknown key {shown(key)} — a project takes "
                                       f"{listed(REGISTRY_ENTRY_KEYS)} only")
        for key in REGISTRY_ENTRY_KEYS:
            if key not in entry:
                raise ProjectFileError(f'{where}: "{key}" is required')
            if not isinstance(entry[key], str):
                raise ProjectFileError(f'{where}: "{key}" must be a string, in quotes')
        if not is_project_name(entry["name"]):
            raise ProjectFileError(f'{where}: name {shown(entry["name"])} is no project name — {PROJECT_NAME_RULE}')
        if not os.path.isabs(entry["path"]):
            raise ProjectFileError(f'{where}: path {shown(entry["path"])} is not absolute')
        if entry["name"] in names:
            raise ProjectFileError(f'{where}: name {shown(entry["name"])} is project {names[entry["name"]]}\'s too — '
                                   "two projects never share a name")
        key = directory_key(entry["path"])
        if key in paths:
            first, number_first = paths[key]
            raise ProjectFileError(f"{where}: path {shown(entry['path'])} names the directory of project {number_first}, "
                                   f"{shown(first)} — two projects never name the same directory")
        names[entry["name"]] = number
        paths[key] = (entry["path"], number)
