"""The grammar of ``cortex.toml`` and ``cortex.local.toml`` — ADR-008 §3.4 — of
``cortex.lock`` — ADR-010 §3.2 — of the machine's ``projects.toml`` — ADR-011 §3.1 — of its
``config.toml`` — ADR-013 §3.7 — and of the team's ``cortex.team.toml`` — ADR-013 §3.5.

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
import unicodedata
from typing import Dict, List, Mapping, Tuple

PROJECT_FILE = "cortex.toml"
LOCAL_FILE = "cortex.local.toml"
MODES = ("store", "link", "copy")
PROJECT_KEYS: Tuple[str, ...] = ("version", "theme", "sync", "claude_access", "agents")
# The keys an unknown key's message names to a project before 1.8.0, as 1.7.0 named them.
PROJECT_LISTED: Tuple[str, ...] = ("version", "theme", "sync", "claude_access")
LOCAL_LISTED: Tuple[str, ...] = ("theme", "spec", "claude_access", "claude_entry")
PROJECT_REQUIRED: Tuple[str, ...] = ("version", "theme")
LOCAL_KEYS: Tuple[str, ...] = ("version", "theme", "spec", "claude_access", "claude_entry", "synced")
BOOLEAN_KEYS: Tuple[str, ...] = ("claude_access",)
# The team's settings, in the team's folder, committed in the team's repository (ADR-013 §3.5): read
# only once the team's version is 1.8.0 or later — an earlier version's templates know none of it.
TEAM_FOLDER = "agents"      # the team's folder, which a committed cortex.toml's `agents` renames (ADR-013 §3.6)
TEAM_FILE = "cortex.team.toml"
TEAM_LOCK_FILE = "cortex.team.lock"
TEAM_KEYS: Tuple[str, ...] = ("version", "theme", "sync", "claude_access", "locked")
TEAM_LOCKABLE: Tuple[str, ...] = ("theme", "sync", "claude_access")
FIRST_TEAM_VERSION = "1.8.0"
# What `cortex sync` records in cortex.local.toml (ADR-013 §3.1): the version read from each file, the
# team's, and the one synced — "" for a checkout.
SYNCED_KEYS: Tuple[str, ...] = ("team", "project", "local", "pinned", "reads", "agents")
SYNCED_REQUIRED: Tuple[str, ...] = ("pinned", "reads")
LOCK_FILE = "cortex.lock"
LOCK_KEYS: Tuple[str, ...] = ("format", "version", "digest")
LOCK_FORMAT = 1
# The digest of a spec: SHA-256, lowercase hex.
DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
REGISTRY_FILE = "projects.toml"
REGISTRY_KEYS: Tuple[str, ...] = ("format", "project")
REGISTRY_ENTRY_KEYS: Tuple[str, ...] = ("name", "path")
REGISTRY_FORMAT = 1
# The machine's settings, in the Cortex home (ADR-013 §3.7): read whatever version a project pins.
MACHINE_FILE = "config.toml"
MACHINE_KEYS: Tuple[str, ...] = ("releases_url", "update_check", "register")
MACHINE_BOOLEAN_KEYS: Tuple[str, ...] = ("update_check", "register")
DEFAULT_RELEASES_URL = "https://github.com/xod-labs/cortex/releases"
# What a path of projects.toml never holds: the C0 controls and DEL — NUL, which no system takes in a
# file name, and the others a terminal reads as commands, which a message would print. The C1 controls
# and the bidirectional marks are not refused: a file name may hold them.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
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


def version_key(text: str) -> Tuple:
    """The order of two versions, as Semantic Versioning orders them: a pre-release before its release,
    its identifiers compared numerically when they are numbers, lexically otherwise, a number before a
    word (semver.org §11). The command's ``Version`` orders them alike."""
    match = VERSION.fullmatch(text)
    if match is None:
        raise ValueError(f"not a version: {text!r}")
    pre = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in match.group(4).split(".")) \
        if match.group(4) else ()
    return tuple(int(part) for part in match.group(1, 2, 3)), (0, pre) if pre else (1, ())


def before_team(text: str) -> bool:
    """Whether a version predates the team's file — 1.8.0, which first reads it. A pre-release has
    its release's features: ``1.8.0-rc.1``, a source checkout's ``1.8.0-dev`` and a development
    build's ``1.8.0-dev.N`` read it (ADR-009 §9, 2026-11-09)."""
    return version_key(text)[0] < version_key(FIRST_TEAM_VERSION)[0]


def is_theme(value: object) -> bool:
    return isinstance(value, str) and THEME.fullmatch(value) is not None


def is_project_name(value: object) -> bool:
    return isinstance(value, str) and PROJECT_NAME.fullmatch(value) is not None


def directory_key(path: str) -> str:
    """``path`` as two entries of ``projects.toml`` that name one directory write it alike: ``.``,
    ``..`` and a final separator undone, and, on Windows, the case. Read from the text alone — a
    link, or a case the file system ignores elsewhere, is the file system's, which the reader
    resolves."""
    return os.path.normcase(os.path.normpath(path))


def shown(value: object) -> str:
    """A value of the file, quoted and escaped as JSON writes it: a committed ``cortex.toml`` may
    hold a newline or a terminal's control sequence, which a message must not print raw."""
    return json.dumps(value)


def listed(keys: Tuple[str, ...]) -> str:
    return ", ".join(keys[:-1]) + f" and {keys[-1]}" if len(keys) > 1 else keys[0]


def _check_keys(name: str, data: Mapping, allowed: Tuple[str, ...], required: Tuple[str, ...],
                others: Tuple[str, ...] = (), named: Tuple[str, ...] = ()) -> None:
    """Every key allowed, the required ones there, each a string or a boolean — but ``others``,
    which their reader checks. ``named``, the keys an unknown key's message lists — ``allowed`` by
    default."""
    for key in data:
        if key not in allowed:
            raise ProjectFileError(f'{name}: unknown key {shown(key)} — {name} takes {listed(named or allowed)}'
                                   + (" only" if name == LOCAL_FILE else ""))
    for key in required:
        if key not in data:
            raise ProjectFileError(f'{name}: "{key}" is required')
    for key, value in data.items():
        if key in others:
            continue
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


def _check_version(name: str, value: object) -> None:
    if not is_version(value):
        raise ProjectFileError(f'{name}: version {shown(value)} is no version — X.Y.Z, for instance 1.0.0')


_AGENTS_ONLY = (f'"agents" is set in {PROJECT_FILE} only — every developer reads the same team\'s folder '
                "(ADR-013, decision 30)")


def agents_before(version: str) -> str:
    """The refusal of ``agents`` while the team's version is before 1.8.0 (ADR-013 §3.8, step 3)."""
    return (f'{PROJECT_FILE}: "agents" is read once the team\'s version is {FIRST_TEAM_VERSION} or later — the '
            f"team's is {version}: move to {FIRST_TEAM_VERSION} first, or remove `agents`")


# An ``agents`` in a cortex.toml that is not committed (decision 30): said by the command, which asks git, and by
# the runtime, from ``synced``.
AGENTS_UNCOMMITTED = (f'{PROJECT_FILE}: "agents" is read from a committed {PROJECT_FILE} only, and this one is not — '
                      "every developer reads the same team's folder (ADR-013, decision 30): commit it, or remove "
                      f"`agents` — the team's folder is then {TEAM_FOLDER}/")


def _one_case(char: str) -> str:
    """A character in one case: lower-cased, then upper-cased, each kept as written where it is longer."""
    lower = char.lower()
    char = lower if len(lower) == 1 else char        # ẞ is ß, ϴ is θ, as the simple case folding folds them
    upper = char.upper()
    return upper if len(upper) == 1 else char


def folded(name: str) -> str:
    """A name of the workspace, folded to compare it in any case — as the file systems that ignore case
    fold the common names, and past them to one folder rather than two: composed (NFC), as APFS takes a
    name decomposed for the same one, then each character in one case on its own, as NTFS's table
    upper-cases it — a character whose case is longer kept as written —, then composed again: ``i`` and a
    dot above, upper-cased, is ``İ``. ``ς`` and ``σ`` are one, ``ß`` is not ``ss``. It takes for one folder
    every two that Unicode's simple upper case — NTFS's table — or simple case folding takes for one, ``ﬅ``
    and ``ﬆ`` aside, and some a disk keeps apart: the Kelvin sign and ``k`` — composed, it is ``K`` —, a
    name composed and decomposed, two on NTFS; ``ı`` and ``i``, two on APFS. ``str.casefold`` folds
    further, ``ß`` as ``ss``, and would take two folders of every disk for one. Compare what it gives with
    what it gives, never with a literal."""
    return unicodedata.normalize("NFC", "".join(_one_case(char) for char in unicodedata.normalize("NFC", name)))


def check_agents(value: object, name: str = PROJECT_FILE, loose: bool = False) -> str:
    """Refuse an ``agents`` that names no folder inside the workspace (ADR-013 §3.6), from its text
    alone: a relative path with ``/`` — no ``\\`` nor ``:``, no absolute path of any platform, no ``..`` nor a
    component that starts with ``.`` or ends with ``.`` or a space, neither the project root itself nor
    ``cortex/``, the spec, nor what it holds. ``cortex`` is told in any case: the file is committed, and
    read where names ignore case too, macOS and Windows — which drops a final ``.`` or space, ``cortex.``
    naming ``cortex``. A service's folder, or one holding a project's ``cortex.toml``, takes the file
    system: its reader checks them. The folder, as the messages name it: without a final ``/``.

    ``loose`` — the version gate's reading, before the text is checked (§3.8, step 1) — takes what a later
    version may take, a component that starts with ``.``; what names no folder inside the workspace, nor
    one any version reads, is refused all the same."""
    rule = f"a relative path, with /, naming a folder inside the workspace — {TEAM_FOLDER} by default"
    if not isinstance(value, str) or not value:
        raise ProjectFileError(f'{name}: "agents" must be {rule}, in quotes')
    if CONTROL.search(value) or "\\" in value:
        raise ProjectFileError(f"{name}: agents {shown(value)} is not {rule}")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ProjectFileError(f"{name}: agents {shown(value)} is an absolute path — it must be {rule}")
    if ":" in value:
        # NTFS reads "cortex::$INDEX_ALLOCATION" as cortex/, a stream of the folder: another alias of a name.
        raise ProjectFileError(f"{name}: agents {shown(value)} holds a \":\", which Windows reads as a stream — it "
                               f"must be {rule}")
    written = value                     # quoted as written in every refusal
    while value.startswith("./"):
        value = value[2:]               # ./team-agents names team-agents
    parts = value.rstrip("/").split("/")
    if parts in ([""], ["."]) or any(not part for part in parts):
        raise ProjectFileError(f"{name}: agents {shown(written)} names the project root, or an empty folder name — "
                               f"it must be {rule}")
    for part in parts:
        if part == "..":
            raise ProjectFileError(f"{name}: agents {shown(written)} climbs out of the folder it is in — it must be {rule}")
        if part.endswith((".", " ")):
            # Windows drops it: "cortex." and "cortex " name cortex, "team." names team.
            raise ProjectFileError(f"{name}: agents {shown(written)} names a folder that ends with \".\" or a space, "
                                   f"which Windows drops — it must be {rule}")
        if part.startswith(".") and not loose:
            raise ProjectFileError(f"{name}: agents {shown(written)} names a folder that starts with \".\" — it must be "
                                   f"{rule}")
    if folded(parts[0]) == folded("cortex"):
        raise ProjectFileError(f"{name}: agents {shown(written)} is in cortex/, the spec's place — it must be {rule}")
    # Only agents itself is the default: Agents names a folder of its own, as written (§9).
    return "/".join(parts)


def check_project(data: Mapping, optional: Tuple[str, ...] = ()) -> None:
    """Refuse a ``cortex.toml`` that is not one — ``ProjectFileError`` names the file and the key.
    ``optional``, of the required keys, those a team's file may give instead (ADR-013 §3.2): whether it
    does is its reader's to check."""
    _check_keys(PROJECT_FILE, data, PROJECT_KEYS, tuple(key for key in PROJECT_REQUIRED if key not in optional),
                named=PROJECT_LISTED if lists_1_7_0(data) else PROJECT_KEYS)
    if "agents" in data:
        check_agents(data["agents"])
    if "version" in data:
        _check_version(PROJECT_FILE, data["version"])
    if "theme" in data:
        _check_theme(PROJECT_FILE, data["theme"])
    if "sync" in data and data["sync"] not in MODES:
        raise ProjectFileError(f'{PROJECT_FILE}: sync {shown(data["sync"])} is none of {listed(MODES)}')


def lists_1_7_0(project_data: Mapping) -> bool:
    """Whether an unknown key's message names 1.7.0's keys, as 1.7.0 named them: a ``cortex.toml`` whose
    ``version`` is before 1.8.0, or does not read. One of 1.8.0 or later, or that leaves its version to the
    team's file, is told the keys it may set."""
    return "version" in project_data and not (is_version(project_data["version"])
                                              and not before_team(str(project_data["version"])))


def check_local(data: Mapping, listed_1_7_0: bool = False) -> None:
    """Refuse a ``cortex.local.toml`` that is not one — an unknown key named among 1.7.0's keys when
    ``listed_1_7_0``, ``lists_1_7_0`` of the project's ``cortex.toml``."""
    if "agents" in data:
        raise ProjectFileError(f"{LOCAL_FILE}: {_AGENTS_ONLY}")
    _check_keys(LOCAL_FILE, data, LOCAL_KEYS, (), ("synced",), named=LOCAL_LISTED if listed_1_7_0 else LOCAL_KEYS)
    if "version" in data:
        _check_version(LOCAL_FILE, data["version"])
    if "theme" in data:
        _check_theme(LOCAL_FILE, data["theme"])
    if "synced" in data:
        check_synced(data["synced"])


def check_synced(value: object) -> None:
    """Refuse a ``synced`` that is not one: a table of ``SYNCED_KEYS``, each a version — ``reads``
    ``""`` too, a checkout's — ``pinned`` and ``reads`` required."""
    name = f'{LOCAL_FILE}: "synced"'
    again = " — `cortex sync` writes it again"
    if not isinstance(value, dict):
        raise ProjectFileError(f"{name} must be a table, as `cortex sync` writes it{again}")
    for key in value:
        if key not in SYNCED_KEYS:
            raise ProjectFileError(f"{name}: unknown key {shown(key)} — it takes {listed(SYNCED_KEYS)} only{again}")
    for key in SYNCED_REQUIRED:
        if key not in value:
            raise ProjectFileError(f'{name}: "{key}" is required{again}')
    for key, version in value.items():
        if key == "agents":             # the team's folder sync read, when cortex.toml renames it (ADR-013 §3.1)
            try:
                folder = check_agents(version, name)
            except ProjectFileError as error:
                raise ProjectFileError(f"{error}{again}")
            if folder == TEAM_FOLDER:
                raise ProjectFileError(f"{name}: agents is written only for a folder other than {TEAM_FOLDER}{again}")
            continue
        if not (is_version(version) or (key == "reads" and version == "")):
            raise ProjectFileError(f"{name}: {key} {shown(version)} is no version{again}")


def check_team(data: Mapping, name: str = f"{TEAM_FOLDER}/{TEAM_FILE}") -> None:
    """Refuse a team's ``cortex.team.toml`` that is not one (ADR-013 §3.5): its keys, none required;
    a ``version`` of 1.8.0 or later — an earlier one would not read the file; ``locked``, keys the file
    sets, each once, never ``version`` nor ``locked``, and ``claude_access`` only at ``false``: a team
    may forbid a permission, never grant one on a developer's machine (decision 25). ``name`` is how
    the messages name the file."""
    if "agents" in data:
        raise ProjectFileError(f"{name}: {_AGENTS_ONLY} — the team's file is in the folder it names")
    _check_keys(name, data, TEAM_KEYS, (), ("locked",))
    if "version" in data:
        _check_version(name, data["version"])
        if before_team(data["version"]):
            raise ProjectFileError(f"{name}: version {shown(data['version'])} is before {FIRST_TEAM_VERSION}, "
                                   f"which first reads {TEAM_FILE} — pin {FIRST_TEAM_VERSION} or later")
    if "theme" in data:
        _check_theme(name, data["theme"])
    if "sync" in data and data["sync"] not in MODES:
        raise ProjectFileError(f'{name}: sync {shown(data["sync"])} is none of {listed(MODES)}')
    locked = data.get("locked", [])
    if not isinstance(locked, list) or not all(isinstance(key, str) for key in locked):
        raise ProjectFileError(f'{name}: "locked" must be a list of keys, in quotes')
    for number, key in enumerate(locked):
        if key in locked[:number]:
            raise ProjectFileError(f"{name}: locked names {shown(key)} twice")
        if key == "version":
            raise ProjectFileError(f'{name}: "version" is never locked — moving it is the team\'s matter, and a '
                                   "developer may try a later one (ADR-013, decision 19)")
        if key not in TEAM_LOCKABLE:
            raise ProjectFileError(f"{name}: locked names {shown(key)}, which no lock takes — "
                                   f"{listed(TEAM_LOCKABLE)} do")
        if key not in data:
            raise ProjectFileError(f"{name}: locked names {shown(key)}, which the file does not set")
        if key == "claude_access" and data[key] is True:
            raise ProjectFileError(f"{name}: claude_access is locked to false only — a team may forbid a "
                                   "permission, never grant one on a developer's machine (ADR-013, decision 25)")


def check_lock(data: Mapping, name: str = LOCK_FILE) -> None:
    """Refuse a ``cortex.lock`` that is not one — or the lock ``name`` names, the team's beside its file
    (ADR-013 §3.4): every key required, none other, ``format`` an integer this Cortex reads —
    ``LaterFormatError`` for a later one."""
    for key in data:
        if key not in LOCK_KEYS:
            raise ProjectFileError(f"{name}: unknown key {shown(key)} — {name} takes {listed(LOCK_KEYS)} only")
    for key in LOCK_KEYS:
        if key not in data:
            raise ProjectFileError(f'{name}: "{key}" is required')
    form = data["format"]
    if isinstance(form, bool) or not isinstance(form, int) or form < 1:
        raise ProjectFileError(f'{name}: "format" must be a positive integer, without quotes')
    if form > LOCK_FORMAT:
        raise LaterFormatError(f"{name}: format {form} is written by a later Cortex — this one reads format {LOCK_FORMAT}")
    if not is_version(data["version"]):
        raise ProjectFileError(f'{name}: version {shown(data["version"])} is no version — X.Y.Z, for instance 1.0.0')
    if not isinstance(data["digest"], str) or DIGEST.fullmatch(data["digest"]) is None:
        raise ProjectFileError(f'{name}: digest {shown(data["digest"])} is no digest — "sha256:" and 64 lowercase '
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
        if CONTROL.search(entry["path"]):
            raise ProjectFileError(f'{where}: path {shown(entry["path"])} holds a control character — '
                                   f"{REGISTRY_FILE} takes none in a path")
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


def machine_values(data: Mapping, name: str = MACHINE_FILE) -> Tuple[Dict[str, object], List[str]]:
    """The keys of the machine's ``config.toml`` that read, and a warning for each that does not —
    an unknown key, or a value of the wrong type — never an error (ADR-013 §3.7): the file is the
    machine's, read by every command and every version, and a key a later Cortex adds must not stop
    an older one, ``cortex self-update`` above all. ``name`` is how the warnings name the file.
    Whether ``releases_url`` is a place to download from is its reader's to check."""
    values: Dict[str, object] = {}
    warnings: List[str] = []
    for key, value in data.items():
        if key == "agents":
            warnings.append(f'{name}: "agents" is set in {PROJECT_FILE} only, not read — every developer reads the same '
                            "team's folder (ADR-013, decision 30)")
        elif key not in MACHINE_KEYS:
            warnings.append(f"{name}: unknown key {shown(key)}, not read — {MACHINE_FILE} takes {listed(MACHINE_KEYS)}")
        elif key in MACHINE_BOOLEAN_KEYS and not isinstance(value, bool):
            warnings.append(f'{name}: "{key}" must be true or false, without quotes — not read')
        elif key not in MACHINE_BOOLEAN_KEYS and (not isinstance(value, str) or not value):
            warnings.append(f'{name}: "{key}" must be a string, in quotes, not empty — not read')
        else:
            values[key] = value
    return values, warnings
