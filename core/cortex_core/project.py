"""The grammar of ``cortex.toml`` and ``cortex.local.toml`` — ADR-008 §3.4 — and of
``cortex.lock`` — ADR-010 §3.2.

One definition for the two readers, the ``cortex`` command and the runtime, so that neither
accepts a file the other refuses. It checks what a TOML parser returned: each reader parses the file
with its own ``tomllib``.

Versions and theme names are ASCII, matched whole: ``\\d`` would take the digits of any script,
and ``$`` a trailing newline — ``"1.0.0\\n"`` would then name a directory of the store.
"""

from __future__ import annotations

import json
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

_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
# X.Y.Z, or a pre-release, as Semantic Versioning writes them; no build metadata.
VERSION = re.compile(rf"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-({_IDENTIFIER}(?:\.{_IDENTIFIER})*))?",
                     re.ASCII)
# A theme names a directory: a name, never a path.
THEME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)


class ProjectFileError(ValueError):
    """A file refused — the message names the file and the key."""


class LaterFormatError(ProjectFileError):
    """A ``cortex.lock`` of a format a later Cortex writes."""


def is_version(value: object) -> bool:
    return isinstance(value, str) and VERSION.fullmatch(value) is not None


def is_theme(value: object) -> bool:
    return isinstance(value, str) and THEME.fullmatch(value) is not None


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
