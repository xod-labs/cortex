"""The workspace's layout on disk — what the validator walks and the prompt shows (ADR-007, #88).

``find`` walks a tree the way the validator's script ran ``find``, in name order; ``services`` is the one
discovery of a workspace's services — a folder with a ``project-overview.md``, as cortex init
scaffolds one — for the validator's overlay roots and for the index an agent is shown.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from .project import PROJECT_FILE, ProjectFileError, folded, shown

# Where a workspace keeps its dependencies: never a service, and the bulk of its files.
_DEPENDENCY_TREES = ("node_modules", "vendor", ".venv")
_ALIAS = re.compile(r"<!--\s*@alias:\s*([^\s>]+)\s*-->")


def _is(test, follow_links: bool) -> bool:
    """``entry.is_file`` or ``entry.is_dir``, false when the link cannot be followed — a loop
    (ELOOP), a target out of reach (EACCES) — as ``find -L`` treats it."""
    try:
        return test(follow_symlinks=follow_links)
    except OSError:
        return False


def find(top: str, name: str, *, maxdepth: Optional[int] = None, regular_files: bool = False,
         prune_names: Tuple[str, ...] = (), prune_paths: Tuple[str, ...] = (),
         follow_links: bool = False) -> List[str]:
    """``find TOP [-maxdepth N] -name NAME [-type f]``, without entering the directories below
    ``TOP`` that are named in ``prune_names`` or located at ``prune_paths``.

    Depth first, each directory's entries sorted by name — not in the order the file system
    returns them, as ``find`` prints them: that order is ext4's on one machine, APFS's or NTFS's on
    another, and the report must be the same on every platform (ADR-008 §3.1). The script's
    captured outputs came out sorted too. Pruning looks below ``TOP`` only: what the directories above it are called changes nothing.
    With ``follow_links``, files and directories behind symbolic links count as the resolver
    reads them — ``find -L`` — and, as there, a directory that is one of its own ancestors — a
    link loop — is not entered again, while a second path to the same directory is listed too.
    """
    found: List[str] = []
    pruned = {os.path.normpath(p) for p in prune_paths}

    def visit(directory: str, depth: int, ancestors: frozenset = frozenset()) -> None:
        if follow_links:
            try:
                st = os.stat(directory)
            except OSError:
                return
            if (st.st_dev, st.st_ino) in ancestors:
                return
            ancestors = ancestors | {(st.st_dev, st.st_ino)}
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            return                    # find reports it on stderr, which the script discards
        for entry in entries:
            path = f"{directory}/{entry.name}"
            if (maxdepth is None or depth + 1 <= maxdepth) and fnmatch.fnmatchcase(entry.name, name) \
                    and (not regular_files or _is(entry.is_file, follow_links)):
                found.append(path)
            if _is(entry.is_dir, follow_links) and (maxdepth is None or depth + 1 < maxdepth) \
                    and entry.name not in prune_names and os.path.normpath(path) not in pruned:
                visit(path, depth + 1, ancestors)

    visit(top, 0)
    return found


def same_directory(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normpath(a) == os.path.normpath(b)


def services(project_root: str, base_root: Optional[str] = None, team_folder: Optional[str] = None) -> List[str]:
    """Every service of the workspace: a folder below the project root, five levels deep at most,
    holding a ``project-overview.md`` — in ``find``'s order, sorted by name.

    Services are looked for outside the base, wherever it is mounted and whatever it is called,
    outside the project root's own ``agents/`` and the team's folder ``team_folder`` that renames it
    (ADR-013 §3.6) — the cascade's tiers and ADR-006's team files, no service — outside any directory
    named ``cortex`` or ``.git`` below the project root — a service may mount its own Cortex — and
    outside its dependency trees, ``node_modules``, ``vendor``, ``.venv``.
    """
    project_root = str(project_root)
    base_root = str(base_root) if base_root is not None else f"{project_root}/cortex"
    team = (str(team_folder),) if team_folder is not None else ()
    found = find(project_root, "project-overview.md", maxdepth=5,
                 prune_names=("cortex", ".git") + _DEPENDENCY_TREES,
                 prune_paths=(base_root, f"{project_root}/agents") + team)
    if team_folder is not None:         # a renamed team's folder, in any case (ADR-013 §3.6, §9)
        found = [f for f in found if not _within(f, str(team_folder))]
    return [d for d in (os.path.dirname(f) for f in found) if d != project_root]


def _within(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or below it, the names compared in any case."""
    path, folder = folded(os.path.normpath(path)), folded(os.path.normpath(folder))
    return path == folder or path.startswith(folder + os.sep)


def _same_name(a: str, b: str) -> bool:
    """Two names of the workspace, compared in any case on every platform (ADR-013 §3.6, §9): ``cortex.toml``
    is committed, and read where names ignore case — macOS, Windows, a Linux workspace on such a file system."""
    return folded(a) == folded(b)


def check_team_folder(project_root: str, folder: str, base_root: Optional[str] = None) -> None:
    """Refuse a team's folder ``folder`` — as ``cortex.toml``'s ``agents`` names it, its text checked
    already (``project.check_agents``) — that the file system says is no team's folder (ADR-013 §3.6): a
    folder inside a service — its ``{service}/agents/`` among them — or one holding a project's
    ``cortex.toml``. A folder holding a ``project-overview.md`` is no service once it is the team's:
    ADR-006's team files are there, and the service scan leaves it out. The path is compared as
    written: a link is the developer's own, and never resolved. ``ProjectFileError`` names the key."""
    root = str(project_root)
    where = f"{root}/{folder}"
    if os.path.isfile(os.path.join(where, PROJECT_FILE)):
        raise ProjectFileError(f"{PROJECT_FILE}: agents {shown(folder)} holds a project's {PROJECT_FILE} — the "
                               "team's folder is no project")
    if os.path.isdir(os.path.join(where, "agents")) and os.path.isfile(os.path.join(where, "project-overview.md")):
        raise ProjectFileError(f"{PROJECT_FILE}: agents {shown(folder)} is a service, its project-overview.md and "
                               "agents/ — the team's folder is no service: its overlays would not be read")
    for overview in find(where, "project-overview.md", maxdepth=5, prune_names=("cortex", ".git") + _DEPENDENCY_TREES):
        below = os.path.dirname(overview)
        if below != where and os.path.isdir(os.path.join(below, "agents")):
            raise ProjectFileError(f"{PROJECT_FILE}: agents {shown(folder)} holds the service "
                                   f"{shown(os.path.relpath(below, root).replace(os.sep, '/'))} — the team's folder "
                                   "holds no service: its overlays would not be read")
    parts = folder.split("/")
    for service in services(root, base_root, where):
        rel = Path(service).relative_to(root).parts
        if len(parts) > len(rel) and all(_same_name(a, b) for a, b in zip(parts, rel)):
            raise ProjectFileError(f"{PROJECT_FILE}: agents {shown(folder)} is inside the service "
                                   f"{shown('/'.join(rel))} — the team's folder is the workspace's, no service's")


class Refusal(NamedTuple):
    """Why the runtime refuses a service (ADR-013 §3.6): ``path``, the service or a folder above it, is the team's
    folder — ``kind`` ``"team"`` — or ``agents/``, the team's folder renamed — ``"agents"``; or ``path`` is the
    service's own ``agents/``, the team's folder, a link to it — ``"own agents"`` —, whose overlays would be read
    twice."""
    kind: str
    path: Path


def refusal(root: Path, folder: str, team: Path) -> Optional[Refusal]:
    """Why the runtime refuses the service ``folder``, below ``root``, as the disk opens it (``Refusal``) — ``None``
    when it serves it. ``team`` is the team's folder; ``agents`` where names ignore case. The folders above it are
    asked first, the nearest the root first. The scan compares names as written."""
    parts = Path(folder).parts
    above = [root.joinpath(*parts[:depth]) for depth in range(1, len(parts) + 1)]
    for path, folders in [(path, ((team, "team"), (root / "agents", "agents"))) for path in above] + \
            [(root / folder / "agents", ((team, "own agents"),))]:
        for other, kind in folders:
            try:
                if os.path.samefile(path, other):
                    return Refusal(kind, path)
            except OSError:       # not there: the scan found the service, by a name the disk takes
                pass
    return None


def listed_services(project_root: str, base_root: Optional[str] = None, team_folder: Optional[str] = None) -> List[str]:
    """``services``, less each the runtime refuses (``refusal``): the services an agent is shown, and the overlay
    roots ``cortex validate`` checks — a service whose ``agents/`` is the team's folder holds the team's overlays,
    which the workspace's tier reads already (ADR-013 §3.6)."""
    root = Path(project_root)
    team = Path(team_folder) if team_folder is not None else root / "agents"
    return [s for s in services(str(project_root), base_root, team_folder)
            if refusal(root, Path(s).relative_to(root).as_posix(), team) is None]


def service_index(project_root, base_root=None, active: Optional[str] = None, team_folder: Optional[str] = None) -> str:
    """The workspace's services as an agent is shown them — one line each, by folder:
    ``- `@alias` — `folder/` — title``, the alias from the overview's ``<!-- @alias: … -->``
    marker (the folder's name without one), the title from its first heading. ``active`` — the
    service of the run — is marked. Empty when the workspace has no service.
    """
    root = Path(project_root)
    lines = []
    for folder in sorted(Path(s).relative_to(root).as_posix() for s in listed_services(str(root), base_root, team_folder)):
        text = (root / folder / "project-overview.md").read_text(encoding="utf-8", errors="replace")
        alias = _ALIAS.search(text)
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), "")
        line = f"- `@{alias.group(1) if alias else Path(folder).name}` — `{folder}/`" + (f" — {title}" if title else "")
        if active is not None and Path(folder) == Path(active):
            line += " (active)"
        lines.append(line)
    return "\n".join(lines)
