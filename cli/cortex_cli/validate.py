"""``cortex validate`` — ADR-007's validator, run by the command (ADR-008 §3.8).

Same checks, same report, same exit codes as the validator script it replaces: the options are
the core's own, and the core parses them. The command only says which two roots it validates:

- in a project — the nearest directory holding ``cortex.toml`` — the project root, and the spec
  ``cortex.local.toml`` names: the pinned version, or a checkout ``cortex sync --from`` gave;
- in a checkout of Cortex, the checkout itself: the base as its own project (ADR-007 §3.1), its
  files read once and never as overlays of themselves.

A project whose spec is missing, or synced for another version than the one ``cortex.toml`` pins,
is not validated: it would be checked against a spec it does not use.

A spec of a known version is also checked against ``cortex.lock`` (ADR-010 §3.4): a missing lock, a
lock of another version, a spec whose digest is not the lock's. Warnings, by the version rule of
ADR-008 §9 — a project of 1.3 has no lock — which ``--strict`` makes errors. A checkout synced
with ``--from`` has no version, and no lock.

Where the team's version is 1.8.0 or later (ADR-013 §3.11), each file's version is first checked
against what ``cortex sync`` recorded — a bump pulled and not synced refuses, as a spec of another
version does — then the spec against the version it recorded as read. The lock checked is the one
beside the file that version comes from: none for a developer's try in ``cortex.local.toml``. A key
the team's file locks, set otherwise, and the default team's folder left empty, are warnings.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cortex_core import validate
from cortex_core.project import shown

from . import config, store, sync
from .paths import display, working_directory


class RootsError(Exception):
    pass


def _synced_version(root: str, spec: Optional[str], base: str) -> str:
    """The version the spec at ``base`` is, when that is known — ``""`` for a checkout.

    A copy's marker says it. Otherwise the spec is the store's, read in place or through a link:
    a directory of some ``versions/``, whichever ``CORTEX_HOME`` it was synced with — a sync made
    with another home is still checked against the version pinned.
    """
    link = Path(root) / sync.LINK
    if spec == sync.LINK and sync.is_link(link):
        base = sync.link_target(link)
    elif spec == sync.LINK and (link / sync.MARKER).is_file():
        marker = sync.read_marker(link)
        return (marker.get("version") or "") if marker else ""
    resolved = Path(os.path.realpath(base))
    return resolved.name if resolved.parent.name == "versions" and store.Version.valid(resolved.name) else ""


def _spec_path(root: str, spec: str) -> str:
    """The directory ``spec`` names, as ``config.Project.spec_directory`` reads it."""
    return spec if os.path.isabs(spec) else f"{root}/{spec}"


def lock_warnings(project: config.Project, base: str, files: Optional[Dict[str, str]] = None,
                  origin: Optional[str] = None, version: Optional[store.Version] = None) -> List[Tuple[str, str, str]]:
    """What the lock says of the spec at ``base``, a spec of ``version`` — the version read, by
    default — from ``origin``: ``cortex.lock``, or the team's beside its file, none for a local try
    (ADR-013 §3.4). A copy's digest from its manifest, ``files``, checked already against the copy."""
    where = config.lock_of(project, origin)
    if where is None:
        return []
    directory, name, shown_name = where
    version = version or project.version
    pins = config.PROJECT_FILE if shown_name == config.LOCK_FILE else config.TEAM_NAME
    try:
        lock = config.load_lock(directory, name)
    except config.ConfigError as error:
        return [(shown_name, "LOCK_INVALID", str(error))]
    commit = "" if sync.in_no_repository(directory) else ", then commit it"     # committed nowhere (ADR-008 §9)
    # The team's lock in a folder this user cannot write: the sync only warns of it here (ADR-013 §3.4). A
    # prediction: on Windows, os.access does not read a folder's ACL.
    fix = (f"{config.TEAM_FOLDER}/ cannot be written here — a `cortex sync` where it can be writes it, committed in "
           "the team's repository") if shown_name == config.TEAM_LOCK_NAME and not os.access(directory, os.W_OK) \
        else f"run `cortex sync`{commit}"
    if lock is None:
        return [(shown_name, "LOCK_MISSING", f"no {shown_name} — {fix}")]
    if lock.version != version:
        return [(shown_name, "LOCK_STALE", f"{shown_name} names Cortex {lock.version}, and {pins} pins "
                 f"{version} — {fix}")]
    try:
        actual = store.digest_of(files) if files is not None else store.digest(Path(base))
    except store.StoreError as error:      # a version removed from the store meanwhile
        return [(shown_name, "LOCK_DIGEST", f"{error} — run `cortex sync`")]
    if actual != lock.digest:
        return [(shown_name, "LOCK_DIGEST", f"the spec synced is {actual}, and {shown_name} names {lock.digest} — "
                 "run `cortex sync`, which puts it in the store again")]
    return []


ROLES = {"team": config.TEAM_NAME, "project": config.PROJECT_FILE, "local": config.LOCAL_FILE}


def _recorded(project: config.Project) -> Optional[str]:
    """What, of the files' versions, is not what ``cortex sync`` recorded — ``None`` when all are
    (ADR-013 §3.11): the test the bootstrap gives the assistant, by plain equality."""
    candidates = project.settings.candidates if project.settings is not None else {}
    for role, name in ROLES.items():
        now, then = candidates.get(role), (project.synced or {}).get(role)
        if now != then:
            return (f"{name} {'pins Cortex ' + now if now else 'pins no version'}, and `cortex sync` recorded "
                    f"{then or 'none'} — run `cortex sync`")
    return None


def _origin_of(project: config.Project, reads: str) -> Optional[str]:
    """The file the version read comes from — the team's, or the developer's try."""
    if reads == str(project.team_version):
        return project.team_origin
    found = project.developer_version
    return found.origin if found is not None and reads == str(found.value) else None


def roots(cwd: str) -> Tuple[str, str, List[Tuple[str, str, str]]]:
    """The project root and the base root to validate from ``cwd``, and the warnings of the
    project itself."""
    root = config.find_project(cwd)
    if root is not None:
        # A version newer than this cortex, said before any key is checked (ADR-013 §3.8) — but of a
        # spec that names no version, a checkout's, there is nothing to compare. A spec that is not
        # there is no checkout: the version is said first.
        old = config.too_new(root, store.own_version())
        spec = config.local_spec(root)
        if old is not None and (spec is None or not os.path.isdir(_spec_path(root, spec))
                                or _synced_version(root, spec, _spec_path(root, spec)) != ""):
            raise RootsError(old)
        try:
            project = config.load(root)
        except config.ConfigError as error:
            raise RootsError(str(error))
        base = project.spec_directory()
        if base is None:
            raise RootsError(f"{config.LOCAL_FILE} names no spec yet — run `cortex sync`")
        if not os.path.isdir(base):
            raise RootsError(f"the spec {config.LOCAL_FILE} names, {shown(display(base))}, is not there — run `cortex sync`")
        expected = str(project.version)
        if project.synced is not None:
            off = _recorded(project)
            if off is not None:
                raise RootsError(off)
            expected = project.synced["reads"]
        synced = _synced_version(project.root, project.spec, base)
        if synced and expected and synced != expected:
            raise RootsError((f"{config.PROJECT_FILE} pins Cortex {project.version}" if project.synced is None else
                              f"`cortex sync` recorded Cortex {expected}") +
                             f", and the spec synced is {shown(synced)} — run `cortex sync`")
        copy = Path(project.root) / sync.LINK
        marker = sync.read_marker(copy) if project.spec == sync.LINK and not sync.is_link(copy) else None
        if marker is not None:
            off = sync.changed_files(copy, marker) + sync.missing_files(copy, marker)
            if off:
                raise RootsError(f"{sync.LINK}/ no longer holds what sync copied — {len(off)} file(s) added, changed "
                                 f"or missing, {shown(off[0])} first: it would be validated against another spec. "
                                 "Run `cortex sync`, which says what to do")
        for note in sync.version_notes(project):
            if "is locked by" not in note and "is not read" not in note:
                sys.stderr.write(f"note: {note}\n")
        warnings = [(config.TEAM_NAME, "LOCKED_KEY" if "is locked by" in note else "TEAM_NOT_READ", note)
                    for note in (project.settings.notes if project.settings is not None else ())]
        folder = sync._team_folder_note(project)
        if folder is not None:
            warnings.append((config.TEAM_FOLDER + "/", "TEAM_FOLDER_EMPTY", folder))
        if synced:
            origin = _origin_of(project, synced) if project.synced is not None else None
            warnings += lock_warnings(project, base, marker.get("files") if marker else None,
                                      origin or project.version_origin, store.Version(synced))
        # The team's bootstrap behind its version's template (ADR-013 §3.10) — told from the store, never fetched.
        from . import upgrade       # upgrade imports sync, which this module's importers reach first
        for path, note in upgrade.behind(root, project, None, fetch=False):
            if path is None:
                sys.stderr.write(f"note: {note}\n")
            else:
                warnings.append((Path(os.path.relpath(path, root)).as_posix(), "BOOTSTRAP_BEHIND", note))
        return root, base, warnings
    if sync.is_checkout(Path(cwd)) and os.path.isfile(os.path.join(cwd, "templates", "bootstrap-instructions.md")):
        return cwd, cwd, []
    hint = (" — a cortex/ submodule or clone is here: `cortex init` says how to leave it"
            if os.path.isdir(os.path.join(cwd, "cortex")) else "")
    raise RootsError(f"no {config.PROJECT_FILE} in {display(cwd)} or above it — `cortex init` makes a directory a "
                     f"Cortex project{hint}")


def run(args: List[str]) -> int:
    if any(arg in ("-h", "--help") for arg in args):
        return validate.main(args, project_root=".", base_root="cortex", prog="cortex validate")
    try:
        project_root, base_root, warnings = roots(working_directory())
    except RootsError as error:
        return sync.failed("cortex validate", str(error), 2)
    except OSError as error:
        return sync.failed("cortex validate", sync.os_error(error), 2)
    return validate.main(args, project_root=project_root, base_root=base_root, prog="cortex validate",
                         project_warnings=warnings)
