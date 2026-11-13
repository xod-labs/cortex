"""``cortex sync`` — where the project finds the spec (ADR-008 §3.5).

``cortex sync`` finds ``cortex.toml`` in the current directory or the nearest ancestor, validates
it and ``cortex.local.toml``, makes sure the pinned version is in the store, and writes ``spec``
in ``cortex.local.toml``. The spec reaches the project in one of three modes:

- ``store`` — nothing in the project: ``spec`` is the store's absolute path, and the spec is read
  where it lives;
- ``link`` — ``cortex/`` links to the store: a symbolic link on Linux and macOS, a directory
  junction on Windows, which needs no administrator right and no developer mode;
- ``copy`` — ``cortex/`` is a read-only copy, with a ``.synced`` marker naming the version.

``cortex/`` becomes a name: the directory ``spec`` names. Sync never deletes what it did not
write — a submodule, a clone, any other directory at ``cortex/`` is refused, in every mode — and
switching back to ``store`` removes the link or the copy it made, and nothing else.

A sync that succeeded registers the project in the machine's ``projects.toml`` when it is not
listed there, and says so once (ADR-011 §3.3) — unless the machine's ``register`` is off (ADR-013 §3.7).
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unicodedata
from pathlib import Path
from typing import Collection, Dict, List, Optional, Pattern, Set, TextIO, Tuple

from cortex_core import cascade
from cortex_core.validate import shown

from . import claude, config, machine, registry, store
from .paths import display, working_directory

LINK = "cortex"
MARKER = ".synced"
MARKER_NOTE = "A read-only copy of the Cortex spec, written by `cortex sync`: run it again to change this copy, never edit it."
STAGING = ".cortex-sync-"


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------- #
# What is at cortex/
# --------------------------------------------------------------------------- #

def is_junction(path: Path) -> bool:
    if hasattr(os.path, "isjunction"):                     # Python 3.12
        return os.path.isjunction(path)
    try:
        return getattr(os.lstat(path), "st_reparse_tag", None) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)
    except OSError:
        return False


def is_link(path: Path) -> bool:
    return os.path.islink(path) or is_junction(path)


def not_a_file(path: Path) -> bool:
    """Whether ``path`` is there as neither a file, a directory nor a link: a named pipe, a socket, a device — which
    git, reading it as .gitignore, waits on, as would Cortex opening it as a file."""
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or is_link(path))


def link_target(path: Path) -> str:
    target = os.readlink(path)
    if target.startswith("\\\\?\\"):                        # how Windows spells a junction's target
        target = target[4:]
    return os.path.normpath(os.path.join(os.path.dirname(path), target))


def _inside(path: str, directory: Path) -> bool:
    path, directory = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(directory))
    return path.startswith(directory + os.sep)


def is_checkout(path: Path) -> bool:
    """A checkout of Cortex holds the spec's three trees — ``--from`` copies or links all three."""
    return all((path / tree).is_dir() for tree in store.SPEC_TREES)


def _digest(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _files(root: Path) -> Dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file() and p.name != MARKER}


def read_marker(copy: Path) -> Optional[dict]:
    """The ``.synced`` marker of a copy sync made — ``None`` when it is none: the version it copied
    (``None`` for a checkout), the checkout (``from``), and the digest of every file it wrote."""
    try:
        marker = json.loads((copy / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict) or not isinstance(marker.get("files"), dict):
        return None
    return marker


def synced_version(copy: Path) -> Optional[str]:
    marker = read_marker(copy)
    return marker.get("version") if marker else None


def changed_files(copy: Path, marker: dict) -> List[str]:
    """What the copy holds that sync did not write: a file added since, or modified."""
    written = marker["files"]
    return sorted(rel for rel, path in _files(copy).items() if written.get(rel) != _digest(path))


def missing_files(copy: Path, marker: dict) -> List[str]:
    """What sync wrote in the copy and is no longer there."""
    return sorted(rel for rel in marker["files"] if not (copy / rel).is_file())


def _gitdir(path: Path) -> Optional[str]:
    """The repository a ``.git`` file names — a submodule's, or a worktree's — absolute."""
    try:
        text = (path / ".git").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    if not text.startswith("gitdir:"):
        return None
    return os.path.normpath(os.path.join(path, text[len("gitdir:"):].strip()))


def is_submodule(path: Path, relative: Optional[str] = None) -> bool:
    """``.gitmodules`` has a ``path = cortex``: a submodule, whether its repository is under the
    superproject's ``.git/modules/`` or, as older git made them, in the ``.git`` it holds. ``relative``,
    the folder as the project root names it — a team's folder, ``team/agents`` — the ``.gitmodules`` read
    is the root's."""
    # The root, as many folders up as ``relative`` has names: a Windows path, written with backslashes, never
    # ends with its text.
    top = path.parent if relative is None else path.parents[len(relative.split("/")) - 1]
    name = path.name if relative is None else relative
    try:
        lines = (top / ".gitmodules").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "path" and value.strip().strip('"').rstrip("/") == name:
            return True
    return False


OLD_THEME_MARKER = "agents/personalities/.active-*"     # the theme the scripts chose, before 1.0.0


def only_submodule(path: Path) -> bool:
    """``.gitmodules`` holds this submodule and no other: once it is removed, the file is left
    empty, and still tracked."""
    try:
        lines = (path.parent / ".gitmodules").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    paths = {value.strip().strip('"').rstrip("/") for key, _, value in (line.partition("=") for line in lines)
             if key.strip() == "path"}
    return paths == {path.name}


GIT_LOCAL = config.GIT_LOCAL                   # ADR-013 §3.1: git asked of the project's own repository
git_environment = config.git_environment


def _git(root: str, *args: str, optional_locks: bool = True) -> Optional[subprocess.CompletedProcess]:
    git = shutil.which("git")
    if git is None:
        return None
    # Without optional locks through the environment, not --no-optional-locks: a git before 2.15 refuses the
    # option, and ignores the variable.
    env = git_environment() if optional_locks else {**git_environment(), "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run([git, "-C", root, *args], capture_output=True, encoding="utf-8", errors="replace",
                              timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _read_above(project) -> str:
    """What is read above the team's version: a try, or a developer's own ``cortex.toml``, uncommitted."""
    developer = project.developer_version
    return f"your own {config.PROJECT_FILE}" if developer is not None and developer.origin == config.PROJECT_FILE \
        else "a try"


def team_file_change(team_file: Path) -> Optional[str]:
    """``"moved"`` when the team's file pins another version than the one its repository has committed — moved there
    by hand —, ``"new"`` when it is committed nowhere yet: its lock goes with it then, or a teammate's checkout reads a
    lock of another version than its file. ``"unknown"`` where git refuses the folder the file is in — another user's
    repository, say: it may go with its lock — or in no work tree, where nothing is committed and no note says where.
    ``None`` for a change of another key, the version as committed — the lock goes alone —, without git, and for a
    file that does not read."""
    folder = str(team_file.parent)
    # Every untracked file listed, whatever status.showUntrackedFiles says: a new team's file is one. Without the
    # optional locks: a status refreshes the index, and would write it — in the team's repository, which a sync, or a
    # dry run, leaves as it was.
    status = _git(folder, "status", "--porcelain", "--untracked-files=all", "--", f":(literal){team_file.name}",
                  optional_locks=False)
    if status is not None and status.returncode != 0:
        return "unknown"
    if status is None or status.returncode != 0 or not status.stdout.strip():
        return None                                     # no git, no work tree, or as committed
    try:
        now = tomllib.loads(team_file.read_bytes().decode("utf-8-sig")).get("version")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    shown = _git(folder, "show", f"HEAD:./{team_file.name}")
    if shown is None:
        return None
    if shown.returncode != 0:                           # committed nowhere yet: it goes with its lock
        return "new"
    try:
        return "moved" if tomllib.loads(shown.stdout.lstrip("\ufeff")).get("version") != now else None
    except tomllib.TOMLDecodeError:
        return "moved"


def team_file_moved(team_file: Path) -> bool:
    """Whether the team's file goes with its lock (``team_file_change``): moved by hand, or committed nowhere yet."""
    return team_file_change(team_file) in ("moved", "new")


def team_lock_commit(folder: Path, shown: str = config.TEAM_FOLDER) -> str:
    """Where the team's lock in ``folder`` is to be committed, as a note says it: in the team's folder — ``shown``, as
    the root names it — with the team's file, when it was moved there by hand, or is committed nowhere yet — or may
    have been, where git refuses the repository (``team_file_change``)."""
    change = team_file_change(folder / config.TEAM_FILE)
    if change == "unknown":
        return f" in {shown}/, with {config.TEAM_FILE} if you moved it"
    why = {"moved": "moved to its version by hand", "new": "committed nowhere yet"}.get(change)
    return f" in {shown}/" + (f", with {config.TEAM_FILE}, {why}" if why else "")


def in_index(path: Path) -> Optional[bool]:
    """Whether the project's index still holds ``cortex`` as a submodule — mode 160000 — as the
    migrator's does until the removal is committed. ``False`` once a pull brought the removal:
    git dropped the entry and left the rest. ``None`` without a repository, or git, to ask."""
    proc = _git(str(path.parent), "ls-files", "--stage", "--", path.name)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.startswith("160000 ")


def _top_and_prefix(root: str) -> Optional[Tuple[str, str]]:
    """The top of the repository the project is in, and the project's place in it, as git says."""
    proc = _git(root, "rev-parse", "--show-toplevel", "--show-prefix")
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    top, _, rest = proc.stdout.partition("\n")
    return top, rest.split("\n", 1)[0]


def submodule_name(path: Path) -> str:
    """The name of the submodule at ``cortex/`` — its section in ``.gitmodules``, at the top of the
    repository, read by git — or, without one, the name ``git submodule add`` gives: its path there."""
    where = _top_and_prefix(str(path.parent))
    if where is not None:
        top, prefix = where
        proc = _git(top, "config", "-f", ".gitmodules", "-z", "--get-regexp", r"^submodule\..*\.path$")
        names = [key[len("submodule."):-len(".path")]
                 for key, _, value in (entry.partition("\n") for entry in
                                       (proc.stdout.split("\0") if proc is not None and proc.returncode == 0 else []))
                 if value.rstrip("/") == prefix + path.name]
        return names[-1] if names else prefix + path.name          # two sections with that path: git takes the last
    # Without git: the project's .gitmodules, read as git reads it — a header in any case, a comment
    # after it, and no section left open by a header of another kind.
    try:
        lines = (path.parent / ".gitmodules").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return path.name
    name, found = None, path.name
    for line in lines:
        if line.lstrip().startswith("["):
            section = re.fullmatch(r'\s*\[submodule\s+"(.+)"\s*\]\s*(?:[#;].*)?', line, flags=re.I)
            name = section.group(1) if section else None
            continue
        key, _, value = line.partition("=")
        if name and key.strip().lower() == "path" and value.strip().strip('"').rstrip("/") == path.name:
            found = name
    return found


def _named_in_gitmodules(path: Path) -> bool:
    """``.gitmodules`` gives the name the submodule at ``cortex/`` would have to another path: a
    submodule moved with ``git mv``, which owns the settings — ``cortex/`` is then a clone of its own."""
    where = _top_and_prefix(str(path.parent))
    if where is None:
        return False
    top, prefix = where
    proc = _git(top, "config", "-f", ".gitmodules", "--get", f"submodule.{submodule_name(path)}.path")
    return proc is not None and proc.returncode == 0 and proc.stdout.strip().rstrip("/") != prefix + path.name


def _left_by_submodule(path: Path) -> bool:
    """A ``cortex/`` whose repository is its own ``.git`` directory, in a project whose settings still
    hold the submodule: the one ``git submodule add`` was run on, as a clone, once a pull removed it."""
    if not (path / ".git").is_dir() or _named_in_gitmodules(path):
        return False
    section = _git(str(path.parent), "config", "--local", "--get-regexp",
                   rf"^submodule\.{re.escape(submodule_name(path))}\.")
    return section is not None and section.returncode == 0 and bool(section.stdout.strip())


def _staged(path: Path) -> bool:
    """The index holds for ``cortex`` what the last commit does not — a submodule never committed, or a
    new commit of it staged: ``git rm`` refuses it without ``-f``, even once ``deinit`` emptied it. git rm
    sees it whatever ``submodule.<name>.ignore`` or ``diff.ignoreSubmodules`` says, and so does this."""
    proc = _git(str(path.parent), "diff", "--cached", "--quiet", "--ignore-submodules=none", "--", path.name)
    return proc is not None and proc.returncode != 0


def _plain_name(name: str) -> bool:
    """A name git takes for a submodule — it ignores one with a ``..`` in its path — and that can be
    printed in a command as it is: no control character (#85)."""
    return bool(name) and ".." not in re.split(r"[/\\]", name) and shown(name) == name


def _absorbed(path: Path) -> Tuple[str, str, str]:
    """The submodule's name, where ``git submodule absorbgitdirs`` moves a repository kept in
    ``cortex/.git`` — ``modules/<name>``, under ``.git/worktrees/<worktree>/`` in a linked worktree —
    and that ``modules/``."""
    name = submodule_name(path)
    # --git-path answers relative to the directory git runs in, its real one — undoing a .. on the
    # side of a link would land elsewhere — or absolute in a linked worktree; git older than 2.31
    # knows no --path-format, and would print it back.
    proc = _git(str(path.parent), "rev-parse", "--git-path", "modules")
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        modules = os.path.normpath(os.path.join(os.path.realpath(str(path.parent)), proc.stdout.strip()))
    else:
        modules = os.path.normpath(os.path.join(str(path.parent), ".git", "modules"))
    return name, os.path.normpath(modules + os.sep + name), modules     # as git joins it: an absolute name too


def _empty_directory(path: str) -> bool:
    """An empty directory, no link: git moves a repository onto it, as rename does."""
    try:
        return os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path)
    except OSError:
        return False


def _stuck(name: str, target: str, modules: str) -> str:
    """Why git cannot move a repository kept in ``cortex/.git`` under ``modules/`` — so that the
    commands removing the submodule would fail — or nothing."""
    if not _plain_name(name) or not target.startswith(modules + os.sep):
        return (f"Its repository is in {LINK}/.git, and the name .gitmodules gives the submodule, "
                f'"{shown(name)}", is not one Cortex can follow under .git/modules/: git ignores a name with .. in '
                f"its path, . would make the repository .git/modules/ itself, and a control character has no "
                f"place in a command. Give the submodule another name in .gitmodules and .git/config, or remove it "
                f"by hand.")
    if os.path.lexists(target) and not _empty_directory(target):
        return (f"Its repository is in {LINK}/.git, and git moves it to {display(target)} before the submodule goes "
                f"— but a repository is there already: one an earlier submodule of that name left, as git rm does, "
                f"or another submodule's. git cannot move {LINK}/.git there, and the commands that remove the "
                f"submodule would fail. Find out whose repository that is before you touch it: another submodule "
                f"may still use it.")
    return ""


def kind_of(path: Path) -> str:
    """What is at ``cortex/`` when sync did not make it."""
    gitdir = _gitdir(path) if (path / ".git").is_file() else None
    # A worktree of another repository has an administrative directory, …/worktrees/<name>, which holds
    # commondir; a submodule's repository — under modules/, of the project or of a linked worktree of
    # it — never does, whatever its directories are called.
    if gitdir is not None and os.path.isfile(os.path.join(gitdir, "commondir")):
        return "worktree"
    if is_submodule(path) or (path / ".git").is_file() or _left_by_submodule(path):
        return "submodule"
    if (path / ".git").is_dir():
        return "clone"
    return "directory" if path.is_dir() else "file"


def describe(path: Path) -> str:
    return {"worktree": "a git worktree", "submodule": "a git submodule", "clone": "a git clone",
            "directory": "a directory", "file": "a file"}[kind_of(path)]


def quote(path: str) -> str:
    """A path as the shell the commands are shown for reads it: PowerShell on Windows."""
    if os.name == "nt":
        return "'" + path.replace("'", "''") + "'"
    return shlex.quote(path)


def _word(text: str) -> str:
    """``text`` as one word of the shell the commands are shown for, quoted only when it needs it."""
    return text if re.fullmatch(r"[\w./-]+", text) else quote(text)


def _remove_command(path: str) -> str:
    return f"Remove-Item -Recurse -Force {quote(path)}" if os.name == "nt" else f"rm -rf {quote(path)}"


def _checks(path: Path) -> str:
    """The three checks that ``cortex/`` holds nothing of the developer's, before its repository goes."""
    target = quote(display(str(path)))
    # The scripts' theme marker, ignored in every Cortex before 1.0.0 and read by nothing now: any
    # agents/personalities/.active-*, as 1.0.0's own .gitignore treats them.
    marker = quote(f":(exclude){OLD_THEME_MARKER}")
    # The reflogs keep commits no Cortex branch holds any more — rewritten or deleted upstream — and
    # --not --remotes leaves them in: the author tells them apart.
    authored = quote("--format=%h %an: %s")
    return ("Check first that it holds nothing of yours: the first command lists the files changed, added or "
            "ignored in it, the second the commits no remote branch holds — the reflog's included — and the "
            "third the stashes. The reflog also keeps commits that no Cortex branch holds any more, rewritten or "
            "deleted upstream: their author tells them from yours.\n\n"
            f"    git -C {target} status --short --ignored -- . {marker}\n"
            f"    git -C {target} log {authored} HEAD --branches --reflog --not --remotes\n"
            f"    git -C {target} stash list\n\n")


def leaving(path: Path) -> str:
    """How to leave the submodule, the worktree or the clone at ``cortex/`` (ADR-008 §3.10): the
    commands, shown and never run — they rewrite the project's git state, and the developer should
    see them first. Every path in them is absolute: they run from any directory."""
    root = display(str(path.parent))
    kind = kind_of(path)
    if kind == "submodule" and in_index(path) is False:
        # A teammate who pulled the removal: git dropped the entry, and kept the directory, the
        # submodule's repository and its settings — deinit and rm have nothing left to act on.
        gitdir = _gitdir(path)
        modules = gitdir is not None and os.path.isdir(gitdir)
        kept = modules or (path / ".git").is_dir()          # a clone made a submodule in place keeps its .git
        commands = []
        # Its name: app/cortex, for one git submodule add made in the subdirectory app of the repository.
        name = submodule_name(path)
        section = _git(root, "config", "--local", "--get-regexp", rf"^submodule\.{re.escape(name)}\.")
        if section is not None and section.returncode == 0 and section.stdout.strip():
            commands.append(f"git -C {quote(root)} config --remove-section {_word(f'submodule.{name}')}")
        commands.append(_remove_command(display(str(path))))
        if modules:
            commands.append(_remove_command(display(gitdir)))
        shown = "".join(f"    {command}\n" for command in commands)
        if kept:
            check = f"here, with the submodule's repository. {_checks(path)}"
        else:
            # The third command 1.0.0 and 1.1.0 printed — the one that ran — removed the repository.
            check = (f"here. Its repository is already gone, so git cannot show what changed in it: look through "
                     f"{LINK}/ yourself first.\n\n")
        return (f"The submodule was removed by a commit you pulled: git no longer tracks {LINK}/, and left it {check}"
                f"These commands change the project at {root}, so they are shown here, not run:\n\n{shown}\n"
                "Then run the command again. The migration guide covers it: "
                "https://github.com/xod-labs/cortex/blob/main/docs/migrating-to-the-binary.md")
    if kind == "submodule":
        # git rm refuses without -f a submodule whose index entry is not the last commit's.
        force = "-f " if in_index(path) and _staged(path) else ""
        commands = [f"git -C {quote(root)} submodule deinit -f {LINK}", f"git -C {quote(root)} rm {force}{LINK}"]
        if (path / ".git").is_dir():
            # deinit refuses a repository kept in cortex/.git before git 2.35: absorbgitdirs, there since
            # 2.12, moves it under modules/ first.
            commands.insert(0, f"git -C {quote(root)} submodule absorbgitdirs {LINK}")
        if only_submodule(path):
            # git rm empties .gitmodules of its last section, and leaves the file, tracked.
            commands.append(f"git -C {quote(root)} rm -f .gitmodules")
        # A submodule's repository stays under the superproject's .git/modules/ once it is gone — and
        # one kept in cortex/.git goes there too, moved by absorbgitdirs.
        gitdir = _gitdir(path)
        if gitdir is None and (path / ".git").is_dir():
            name, gitdir, modules = _absorbed(path)
            stuck = _stuck(name, gitdir, modules)
            if stuck:
                # A removal there could only reach another repository; the commands, only fail.
                return (f"Cortex now lives in the store, and the submodule is to go. {stuck} Then run the command "
                        "again. The migration guide covers it: "
                        "https://github.com/xod-labs/cortex/blob/main/docs/migrating-to-the-binary.md")
        if gitdir is not None:
            commands.append(_remove_command(display(gitdir)))
        shown = "".join(f"    {command}\n" for command in commands)
        # A clone made a submodule in place is the likeliest to hold the developer's own work; any
        # submodule's repository may hold some, and deinit -f throws away its changes first (#170).
        if (path / ".git").is_dir():
            own = (f"The submodule keeps its repository in {LINK}/.git, as a clone made a submodule in place does, "
                   f"and it goes with the submodule. {_checks(path)}")
        elif gitdir is not None and os.path.isdir(gitdir):
            own = f"Its repository, {display(gitdir)}, goes with the submodule. {_checks(path)}"
        else:
            own = ""
        return (f"Cortex now lives in the store, and the submodule is to go. {own}These commands rewrite the git "
                f"state of the project at {root}, so they are shown here, not run:\n\n{shown}\n"
                "Then run the command again. The migration guide covers it: "
                "https://github.com/xod-labs/cortex/blob/main/docs/migrating-to-the-binary.md")
    if kind == "worktree":
        common = os.path.dirname(os.path.dirname(_gitdir(path)))       # …/.git/worktrees/<name> → …/.git
        return (f"Cortex now lives in the store, and this worktree of {display(common)} is to go. Once you have "
                f"checked it holds nothing of yours:\n\n    git --git-dir {quote(display(common))} worktree remove "
                f"{quote(display(str(path)))}\n\nThen run the command again.")
    if kind == "clone":
        return (f"Cortex now lives in the store, and the clone is to go. {_checks(path)}"
                f"Then remove it:\n\n    {_remove_command(display(str(path)))}\n\nThen run the command again.")
    return "Move or remove it, then run the command again."


def existing_entry(target: Path, the_store: store.Store, project: config.Project,
                   replaces: bool = False, pinned: bool = True) -> Optional[str]:
    """What sync made at ``cortex/`` — ``"link"``, ``"copy"`` — or ``None`` when nothing is there.
    Anything else there is refused, and so is a copy holding a file sync did not write: removing
    it would lose that file. ``replaces`` — a link sync from a checkout, ``--link --from`` — a link
    the project reads that leads nowhere is ``"gone"``: the sync names where it should lead (#310). ``pinned``
    off — `cortex init` in a project without its ``cortex.toml`` (#328) — the advice is init's: no sync runs there."""
    if not os.path.lexists(target):
        return None
    if is_link(target):
        pointed = link_target(target)
        if _inside(pointed, the_store.versions) or (project.spec == LINK and is_checkout(Path(pointed))):
            return "link"
        if project.spec == LINK and not os.path.exists(target):
            # A link the project reads, that leads nowhere: to a checkout a `--from` sync linked, moved or
            # deleted since, or into a store moved since — CORTEX_HOME changed. One message says both (#312).
            if replaces:
                return "gone"
            again = f"`cortex sync --link --from` the checkout it should follow, which replaces it, or remove it, then " \
                f"`cortex sync` for the version {config.PROJECT_FILE} pins" if pinned else \
                "`cortex init --link --from` the checkout it should follow, which replaces it, or remove it, then run " \
                "the command again"
            raise SyncError(f"{LINK}/ is a link to {shown(display(pointed))}, which leads nowhere — a checkout or a "
                            f"store moved or deleted since it was linked. {again}.")
        raise SyncError(f"{LINK}/ is a link to {shown(display(pointed))}, which Cortex did not make. Move or remove it, "
                        "then run the command again.")
    if target.is_dir() and not (target / ".git").exists() and (target / MARKER).is_file():
        marker = read_marker(target)
        if marker is None:
            raise SyncError(f"{LINK}/ holds a {MARKER} file Cortex cannot read: Cortex did not write this directory. "
                            "Move or remove it, then run the command again.")
        changed = changed_files(target, marker)
        if changed:
            names = "\n".join(f"    {shown(rel)}" for rel in changed[:20]) + ("\n    …" if len(changed) > 20 else "")
            raise SyncError(f"{LINK}/ is a copy Cortex made, and it holds files Cortex did not write:\n{names}\n"
                            "Overlays belong in agents/, not in the copy. Move those files out, or remove cortex/ "
                            "yourself, then run the command again.")
        return "copy"
    raise SyncError(f"{LINK}/ is {describe(target)}, which Cortex did not write. It would shadow the spec "
                    f"{config.LOCAL_FILE} names, and Cortex never deletes what it did not write.\n{leaving(target)}")


# --------------------------------------------------------------------------- #
# Making, swapping and removing the link and the copy
# --------------------------------------------------------------------------- #

def _discard(path: Path) -> None:
    """Remove a link or a copy sync made — staged, or moved aside — by what it is now, not by
    what it was when sync looked: another sync may have swapped it meanwhile."""
    if is_link(path):
        if os.name == "nt":
            os.rmdir(path)                     # a junction, or a directory link: never what it points at
        else:
            os.unlink(path)
    elif path.is_dir():
        store.remove_tree(path)
    elif os.path.lexists(path):
        os.unlink(path)


LOCK_WAIT = 120   # seconds a sync waits for another sync of the same project
# What the lock answers when another process holds it. Anything else is a file system without
# the lock: NFS emulates flock with a POSIX lock, which a read-only descriptor cannot take (EBADF).
HELD = {errno.EACCES, errno.EDEADLOCK} if os.name == "nt" else {errno.EWOULDBLOCK, errno.EAGAIN}
LEFT_LOCAL = re.compile(r"(?:" + re.escape(config.LOCAL_FILE) + "|" + re.escape(config.LOCK_FILE)
                        + r")\.[0-9]+\.tmp")   # config.write_text's, for either file


def lock_path(root: Path) -> Path:
    """What the lock is taken on. On POSIX, the project's directory: nothing is written for it. On
    Windows, one file of the user's temporary directory for every project, ``cortex-sync.lock``, of
    which each project locks a byte (``lock_offset``). Not ``cortex.toml``: a file held open cannot
    be replaced on Windows, and the lock failed a ``git pull``, or an editor that saves by renaming,
    for as long as a sync ran or waited. Not a file per project either: they piled up there."""
    return root if os.name != "nt" else Path(tempfile.gettempdir()) / "cortex-sync.lock"


def lock_offset(root: Path) -> int:
    """The byte of ``cortex-sync.lock`` a project locks, drawn from its path. Two projects that
    draw the same one — one chance in 2**40 — only wait for each other."""
    key = os.path.normcase(os.path.realpath(root)).encode("utf-8", "surrogatepass")
    return int.from_bytes(hashlib.sha256(key).digest()[:5], "big")


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)       # the byte at the file's position
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def wait_for_lock(fd: int, at: int, wait: float) -> bool:
    """Take the lock on ``fd`` — on Windows, its byte at ``at`` — waiting ``wait`` seconds at most
    while another process holds it: ``False`` when it still does. An ``OSError`` is a file system
    without the lock."""
    deadline = time.monotonic() + wait
    while True:
        try:
            if os.name == "nt":
                os.lseek(fd, at, os.SEEK_SET)
            _try_lock(fd)
            return True
        except OSError as error:
            if error.errno not in HELD:
                raise
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)


def release_lock(fd: int, at: int) -> None:
    """Free the lock ``wait_for_lock`` took on ``fd``, before the descriptor is closed — which frees
    it too, on every system: on Windows, now rather than when Windows gets to it."""
    if os.name == "nt":
        import msvcrt

        with contextlib.suppress(OSError):
            os.lseek(fd, at, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _take(fd: int, at: int, root: Path) -> None:
    if not wait_for_lock(fd, at, LOCK_WAIT):
        raise SyncError(f"another cortex sync of {display(str(root))} has run for {LOCK_WAIT} seconds — "
                        "wait for it, then run the command again")


@contextlib.contextmanager
def project_lock(root: Path, err: TextIO):
    """One sync of a project at a time on this machine: the others wait for it. Where the lock
    cannot be taken at all, the sync runs without it, and says so."""
    fd, locked = None, False
    at = lock_offset(root) if os.name == "nt" else 0
    try:
        fd = os.open(lock_path(root), os.O_RDONLY if os.name != "nt" else os.O_RDWR | os.O_CREAT)
        _take(fd, at, root)
        locked = True
    except OSError as error:
        err.write(f"warning: {display(str(root))} cannot be locked here ({error.strerror}) — a sync of this project "
                  "run at the same time is not kept out\n")
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    try:
        yield
    finally:
        if fd is not None:
            if locked:
                release_lock(fd, at)                   # the file serves every project, on Windows
            os.close(fd)


def tidy(root: Path) -> None:
    """Remove what an interrupted sync left beside ``cortex/`` — a staged link or copy, an old
    entry moved aside, a ``cortex.local.toml`` half written, which names this machine's paths and
    which git does not ignore, or a ``cortex.lock`` half written. Run under the project's lock: no
    other sync is using them. A link among them names this machine's store, and `/cortex` does not
    keep it out of a commit."""
    left = [entry for entry in root.glob("*.tmp") if LEFT_LOCAL.fullmatch(entry.name)]
    for entry in [*root.glob(f"{STAGING}*"), *left]:
        try:
            _discard(entry)
        except OSError:
            pass


def make_link(target: Path, source: Path) -> None:
    if os.name != "nt":
        os.symlink(source, target, target_is_directory=True)
        return
    # A junction: no privilege, where a symbolic link needs administrator rights or developer
    # mode. _winapi is CPython's own, not public: mklink is the fallback if it goes.
    try:
        import _winapi

        _winapi.CreateJunction(str(source), str(target))
    except (ImportError, AttributeError):
        cmd = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "cmd.exe")
        subprocess.run([cmd, "/c", "mklink", "/J", str(target), str(source)], check=True, capture_output=True)


def _aside(root: Path) -> Path:
    return root / f"{STAGING}{os.getpid()}-{secrets.token_hex(4)}"


def stage_link(root: Path, source: Path) -> Path:
    path = _aside(root)
    make_link(path, source)
    return path


def stage_copy(root: Path, source: Path, version: Optional[str], checkout: Optional[str]) -> Path:
    """A read-only copy of ``source``'s three trees, beside ``cortex/``, with its manifest."""
    staging = Path(tempfile.mkdtemp(prefix=STAGING, dir=root))
    try:
        for tree in store.SPEC_TREES:
            # A checkout's theme marker is its developer's choice, never the project's.
            shutil.copytree(source / tree, staging / tree, ignore=shutil.ignore_patterns(".active-*"))
        marker = {"note": MARKER_NOTE, "version": version, "from": checkout,
                  "files": {rel: _digest(path) for rel, path in sorted(_files(staging).items())}}
        (staging / MARKER).write_text(json.dumps(marker, indent=1) + "\n", encoding="utf-8")
        store.make_read_only(staging)
        _unseal(staging)                       # sealed once in place: see _seal
    except BaseException:
        if staging.exists():
            store.remove_tree(staging)
        raise
    return staging


def _seal(path: Path) -> None:
    """Make a copy's own directory read-only, once it is in place. Everything in it is read-only
    already; the directory itself stays writable while it is moved, since macOS renames no
    directory its owner may not write in — Linux does."""
    if os.name != "nt" and path.is_dir() and not is_link(path):
        os.chmod(path, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def _unseal(path: Path) -> None:
    """Let a copy's own directory be moved — see ``_seal``."""
    if os.name != "nt" and path.is_dir() and not is_link(path):
        os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def swap(target: Path, new: Optional[Path], old: Optional[str]) -> Optional[Path]:
    """Put ``new`` at ``target`` — or nothing — and return what was there, moved aside, for the
    caller to remove once everything else is written. On failure, the old entry goes back —
    unless another sync put its own at ``target`` meanwhile, and ours is not needed."""
    aside = None
    if old is not None:
        aside = _aside(target.parent)
        try:
            _unseal(target)
            os.rename(target, aside)
        except FileNotFoundError as error:
            raise SyncError(RACE) from error           # taken away since sync looked at it
    if new is not None:
        try:
            os.rename(new, target)
        except OSError as error:
            # Something is at cortex/ that neither this sync put there nor moved away: another sync
            # got there first. Every system says so its own way — ENOTEMPTY, EEXIST, EACCES on macOS.
            if aside is not None:
                try:
                    os.rename(aside, target)
                    _seal(target)
                except OSError:
                    _discard(aside)
                    raise SyncError(RACE) from error
            elif os.path.lexists(target):
                raise SyncError(RACE) from error
            raise
        _seal(target)
    return aside


RACE = (f"{LINK}/ was put back while this sync ran — by another cortex sync, most likely. "
        "Run the command again.")


def unswap(target: Path, aside: Optional[Path]) -> None:
    """Undo ``swap``: what it put at ``target`` goes, what it moved aside comes back."""
    if os.path.lexists(target):
        gone = _aside(target.parent)
        _unseal(target)
        os.rename(target, gone)
        _discard(gone)
    if aside is not None:
        os.rename(aside, target)
        _seal(target)


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def sync(cwd: str, mode: Optional[str], source: Optional[str], out: TextIO, err: TextIO,
         claude_access: Optional[bool] = None, notes: bool = True, locked: bool = False,
         announce: bool = True, registers: bool = True, bootstraps: Optional[bool] = None,
         said: Collection[str] = ()) -> None:
    """Sync the project at or above ``cwd``. ``notes`` off, what git ignores is left to the
    caller — ``cortex init`` writes .gitignore once ``cortex/`` has its final form. ``locked``,
    ``cortex.lock`` is never written: a sync that would write it stops. ``announce`` off, the
    project is not named: ``cortex upgrade`` has named it already. ``registers`` off, the project
    is not registered: ``cortex init`` registers it once all of it succeeded, not only its sync.
    ``bootstraps`` — `cortex sync`'s own run, and ``cortex upgrade --local``'s — writes the developer's
    own bootstrap again and says whether the team's is behind (ADR-013 §3.10), still under the
    project's lock: ``True`` writes the developer's own whatever the team's file, ``False`` only
    where there is one. ``said``: the notes of the versions the caller said already —
    ``cortex upgrade``'s — not said again."""
    root = config.find_project(cwd)
    if root is None:
        raise SyncError(f"no {config.PROJECT_FILE} in {display(cwd)} or above it — `cortex init` makes a directory "
                        "a Cortex project")
    with project_lock(Path(root), err):
        _sync(root, mode, source, out, err, claude_access, notes, locked, announce, said)
        if bootstraps is not None and source is None and not locked:
            from . import upgrade       # upgrade imports this module: the bootstraps are its
            project = config.load(root)
            upgrade.own_bootstraps(root, project, store.Store(), out, err, forced=bootstraps)
            for path, note in upgrade.behind(root, project, None, fetch=True):
                err.write(f"note: {note}\n" if path is None else f"note: {upgrade._shown_path(path, root)} is behind: {note}\n")
    if registers:
        register(root, out, err)


def register(root: str, out: TextIO, err: TextIO) -> None:
    """Register the project at ``root`` when the machine's registry does not list it, and say so
    once (ADR-011 §3.3) — after a sync, or an init, that succeeded. Named as ``cortex project add``
    names it; an entry is never renamed nor moved: a project moved on disk is registered again,
    under a free name. A registry that cannot be read or written is a warning, never a failure:
    the project is synced, and not listed.

    A project listed already costs a read of the registry, without its lock, and no look at the
    other entries' directories: nothing is written, and nothing waits for another command's write.
    The machine's ``register`` off — ``CORTEX_NO_REGISTER`` set to anything but an empty value, or
    ``register = false`` in ``config.toml`` — leaves the registry alone, neither read nor written."""
    if not machine.setting(store.cortex_home, "register").value:
        return
    root = os.path.abspath(root)
    try:
        if registry.listed(registry.load(), root) is not None:
            return
    except registry.RegistryError:
        pass                                # add reads it again, and warns why
    entry, added = registry.add(root, warn=err)
    if added:
        out.write(f"registered as {entry.name} — {registry.LISTS}\n")


def commit_it(directory: str, version: store.Version, where: str = "") -> str:
    """What follows a file written in ``directory`` for ``version``: `` — commit it``, then ``where`` — the
    folder it is committed in. Nothing in no git repository, where it is committed nowhere (ADR-008 §9), nor
    for a build of no release's version, which no teammate can download (ADR-009 §9)."""
    return "" if in_no_repository(directory) or store.development(version) else " — commit it" + where


NOWHERE = " — in no git work tree, so committed nowhere"     # a file beyond a link, in no repository (#351)


def then_commit(directory: str, version: store.Version) -> str:
    """What follows a remedy that writes a lock in ``directory`` for ``version``: ``, then commit it``. Nothing in
    no git repository, nor for a build of no release's version, as ``commit_it`` says; a link out of the project's
    repository to a folder in none — git does not follow it — is said committed nowhere (#351)."""
    if in_no_repository(directory) or store.development(version):
        return ""
    return "; it is in no git work tree, so committed nowhere" if in_no_repository(os.path.realpath(directory)) \
        else ", then commit it"


def team_lock_note(folder: str, version: store.Version, shown: str = config.TEAM_FOLDER) -> str:
    """What follows the team's lock written in ``folder`` — ``shown``, as the root names it: ``commit_it``, asked
    where the folder is. A link out of the project's repository to a folder in none — git does not follow it — is
    said committed nowhere (#351); in no repository as written either, nothing is said, as ``commit_it`` says."""
    real = os.path.realpath(folder)
    if in_no_repository(real) and not in_no_repository(folder) and not store.development(version):
        return NOWHERE
    return commit_it(real, version, team_lock_commit(Path(folder), shown))


def check_lock(root: str, version: store.Version, the_store: store.Store, out: TextIO,
               locked: bool = False, name: str = config.LOCK_FILE, writer: str = "`cortex sync`",
               folder: str = config.TEAM_FOLDER) -> Tuple[str, Optional[str]]:
    """``cortex.lock`` — or the lock ``name`` names in ``root``, the team's in its folder ``folder`` —
    against ``version`` in the store (ADR-010 §3.3), before anything of the project changes: the digest
    of the spec, and what to say of the lock to write — ``None`` when it stays as it is. A spec whose
    files are not those the project locked is put in the store again, once: still different, the
    release is not the one the team locked, and it is refused. ``locked`` — ``cortex sync --locked``, in
    a project's CI — a lock to write is refused instead: the one committed must already be the pin's."""
    team = name == config.TEAM_LOCK_FILE
    shown_name = f"{folder}/{config.TEAM_LOCK_FILE}" if team else name
    pins = config.cascade.team_name(folder) if team else config.PROJECT_FILE
    lock = config.load_lock(root, name, shown_name, writer=writer)
    digest = the_store.digest(version)
    if lock is not None and lock.version == version:
        if lock.digest == digest:
            return digest, None
        try:
            the_store.replace(version)
        except store.StoreError as error:
            raise SyncError(f"Cortex {version} in the store is not what {shown_name} names — its spec is {digest}, "
                            f"and the lock's {lock.digest} — and it could not be put in the store again: {error}. "
                            "Nothing of the project was changed")
        digest = the_store.digest(version)
        if digest != lock.digest:
            raise SyncError(f"Cortex {version}: its spec is {digest}, and {shown_name} names {lock.digest} — "
                            "this is not the release the project locked. Nothing of the project was changed")
        out.write(f"Cortex {version}: put in the store again — its files were not those {shown_name} names\n")
        return digest, None
    if locked:
        why = (f"no {shown_name}" if lock is None else
               f"{shown_name} names Cortex {lock.version}, and {pins} pins {version}")
        raise SyncError(f"--locked: {why} — {writer} writes it{then_commit(root, version)}. Nothing of the project "
                        "was changed")
    return digest, ("written" if lock is None else f"Cortex {version}, where it named {lock.version}")


def against_locks(project: config.Project, flags: Dict[str, object]) -> None:
    """Refuse a flag that sets a key the team's file locks to another value (ADR-013 §3.5) — its
    value would not be read, and the command would say it did what it did not."""
    for key, value in flags.items():
        setting = project.settings[key] if project.settings is not None and key in project.settings else None
        if value is not None and setting is not None and setting.locked and setting.value != value:
            raise SyncError(f"{key} is locked to {config.toml_value(setting.value)} by {project.team_name} — the "
                            "team's choice, which a flag does not undo")


def version_notes(project: config.Project, locked: bool = False) -> List[str]:
    """What a sync, an upgrade and a validate say of the versions, every time (ADR-013 §3.3): the
    developer's version ahead of the team's — not read under ``--locked``, which checks the team's —
    or caught up and changing nothing; what the team's resolution says of the team's file and of a
    locked key; a committed ``cortex.lock`` nothing reads."""
    said: List[str] = []
    found = project.developer_version
    team, origin = project.team_version, project.team_origin
    if found is not None:
        mine = store.Version(str(found.value))
        if mine > team and locked:
            said.append(f"{found.origin}'s version, {mine}, is not read: --locked checks the team's {team} ({origin})")
        elif mine > team:
            # A build of no release is downloaded by no one: the store holds it from the build itself (ADR-009 §9).
            said.append(f"Cortex {mine} is read — {found.origin}'s, above the team's {team} ({origin})"
                        + ((": it is not locked, a build of no release, which no SHA256SUMS checks"
                            if store.development(mine) else
                            ": it is not locked, its release checked against its SHA256SUMS when downloaded")
                           if found.origin == config.LOCAL_FILE else ""))
        else:
            remedy = f"remove it: cortex upgrade --local {team}" if found.origin == config.LOCAL_FILE else \
                f"remove `version` from {found.origin}"
            said.append(f"{found.origin}'s version, {mine}, is at or below the team's {team} ({origin}) and changes "
                        f"nothing — {remedy}")
    said.extend(project.settings.notes if project.settings is not None else ())
    if project.follows and project.settings.committed \
            and os.path.isfile(os.path.join(project.root, config.LOCK_FILE)):
        said.append(f"{config.LOCK_FILE} is not read: {config.PROJECT_FILE} pins no version, the team's file does — "
                    f"remove {config.LOCK_FILE}")
    return said


def gitlink(path: Path) -> bool:
    """git's index holds ``path`` as a submodule — mode 160000 — wherever the project sits in its
    repository: in a subdirectory, the ``.gitmodules`` that names it is at the repository's top, and
    names it by its path from there. ``False`` without git to ask."""
    proc = _git(str(path.parent), "ls-files", "--stage", "-z", "--", path.name)
    if proc is None or proc.returncode != 0:
        return False
    return any(entry.startswith("160000 ") and entry.partition("\t")[2] == path.name for entry in proc.stdout.split("\0"))


def _team_folder_note(project: config.Project) -> Optional[str]:
    """The team's folder, a submodule left empty (ADR-013 §3.4): a warning, which ``validate --strict``
    fails — for a project whose team's version reads what it holds. A submodule by the ``.gitmodules``
    beside it, or by git's index: a CI checkout without its submodules, the project in a subdirectory of
    its repository."""
    folder = Path(project.team_dir)
    if project_before_team(project) or not folder.is_dir() or \
            not (is_submodule(folder, project.team_folder) or gitlink(folder)):
        return None
    try:
        empty = not any(folder.iterdir())
    except OSError:
        return None
    return f"{project.team_folder}/ is empty — a submodule not checked out: `git submodule update --init`" \
        if empty else None


# What a root agents/ left behind still holds, when cortex.toml names another team's folder (ADR-013 §3.6):
# overlays, context files, and a team's file and lock that no longer apply.
_TEAM_CONTENT = ("roles", "capabilities", "personalities", "workflows", "project-overview.md", "project-context.md",
                 config.TEAM_FILE, config.TEAM_LOCK_FILE)


def renamed_folder_notes(project: config.Project) -> List[Tuple[str, str]]:
    """What is said of a team's folder ``cortex.toml`` names (ADR-013 §3.6), at every sync and validate:
    missing — its overlays, its context and the team's file are not read — or the root's ``agents/``
    left behind, still holding overlays or context files nobody reads. Nothing of the default folder:
    a project of 1.7.0 is said nothing new. Each with ``cortex validate``'s code."""
    if project.team_folder == config.TEAM_FOLDER:
        return []
    said = []
    if os.path.islink(project.team_dir) and not os.path.exists(project.team_dir):
        target = json.dumps(display(os.readlink(project.team_dir)))
        said.append(("TEAM_FOLDER_MISSING",
                     f"{project.team_folder}/ is a link to {target}, which leads nowhere: make it lead to the team's "
                     "folder, or remove it and check that folder out in its place"))
    elif not os.path.isdir(project.team_dir):
        said.append(("TEAM_FOLDER_MISSING",
                     f"{project.team_folder}/ is not there — the team's folder {config.PROJECT_FILE} names: its "
                     "overlays, its context files and the team's file are not read; check it out there"))
    elif os.path.isdir(os.path.join(project.team_dir, config.TEAM_FOLDER)):
        # An agents/ inside the team's folder, no service's (ADR-013 §9): what it holds is read from neither.
        nested = f"{project.team_folder}/{config.TEAM_FOLDER}"
        said.append(("TEAM_FOLDER_NESTED",
                     f"{nested}/ is not read — the team's folder is {project.team_folder}/ itself, its roles/, "
                     f"capabilities/, personalities/ and workflows/; overlays kept in {nested}/ want "
                     f'agents = "{nested}"'))
    old = os.path.join(project.root, config.TEAM_FOLDER)
    try:
        same = os.path.samefile(old, project.team_dir)      # a link kept for a while, or a name in another case
    except OSError:
        same = False
    try:
        left = [] if same else [name for name in sorted(os.listdir(old)) if name in _TEAM_CONTENT]
    except OSError:
        left = []
    if left:
        said.append(("TEAM_FOLDER_LEFT_BEHIND",
                     f"{config.TEAM_FOLDER}/ still holds {', '.join(left)} — the team's folder is "
                     f"{project.team_folder}/, which {config.PROJECT_FILE} names: what {config.TEAM_FOLDER}/ holds is "
                     "not read; move it there"))
    return said


def project_before_team(project: config.Project) -> bool:
    """Whether the team's version predates 1.8.0, and reads nothing ADR-013 adds."""
    return config.project.before_team(str(project.team_version))


def synced_record(project: config.Project, reads: str) -> Optional[Dict[str, str]]:
    """What sync records in ``cortex.local.toml`` (ADR-013 §3.1): the version read from each file, the
    team's, and the one synced — ``""`` for a checkout. ``None`` — none — where the team's version is
    before 1.8.0: a cortex before it refuses the key."""
    if project_before_team(project):
        return None
    candidates = project.settings.candidates if project.settings is not None else {}
    # The team's folder read, when cortex.toml renames it: the runtime reads no other (ADR-013 §3.1, §3.12).
    folder = {"agents": project.team_folder} if project.team_folder != config.TEAM_FOLDER else {}
    return {**{key: candidates[key] for key in ("team", "project", "local") if key in candidates},
            "pinned": str(project.team_version), "reads": reads, **folder}


def _sync(root: str, mode: Optional[str], source: Optional[str], out: TextIO, err: TextIO,
          claude_access: Optional[bool], notes: bool, locked: bool = False, announce: bool = True,
          said: Collection[str] = ()) -> None:
    if source is None:
        # A version newer than this cortex, said before any key is checked (ADR-013 §3.8) — `--from`
        # checks no version, as before.
        old = config.too_new(root, store.own_version())
        if old is not None:
            raise SyncError(old)
    project = config.load(root, lenient=True)          # a synced it cannot read is its own to write again
    against_locks(project, {"sync": mode, "claude_access": claude_access})
    if announce:
        out.write(f"Project: {display(root)}\n")
    for note in version_notes(project, locked):
        if note in said:
            continue                        # said by `cortex upgrade`, before this sync
        # A locked key set otherwise is a warning (ADR-013 §3.5): its value is not read.
        err.write(f"{'warning' if ' is locked by ' in note else 'note'}: {note}\n")
    if claude_access is not None:
        # This developer's choice, written with spec and kept for the next syncs: cortex.local.toml's
        # wins over the team's — unless the team's file locks it.
        project = project.with_claude_access(claude_access)
    # --locked checks the team's version and its lock, never a developer's try (ADR-013 §3.4).
    version = project.team_version if locked else project.version
    origin = project.team_origin if locked else project.version_origin
    mode = mode or str(project.settings.value("sync"))      # cortex.toml's, else the team's file's, else store
    the_store = store.Store()
    target = Path(root) / LINK
    tidy(Path(root))
    existing = existing_entry(target, the_store, project, replaces=source is not None and mode == "link")
    gone = link_target(target) if existing == "gone" else None

    if source is not None:
        spec_source = Path(os.path.abspath(source))
        if not is_checkout(spec_source):
            raise SyncError(f"--from {display(source)}: no Cortex checkout there — it holds no agents/, templates/ "
                            "and docs/")
        err.write(f"warning: the spec is the checkout at {display(str(spec_source))}, not Cortex {version}: "
                  "no version is checked (--from)\n")
    else:
        # This developer's cortex.toml, uncommitted beside the team's file: a pin no command writes.
        how = the_store.ensure(version, origin=origin, by_hand=project.by_hand(origin))
        spec_source = the_store.path(version)
        said = {"stored": "in the store", "written": "written to the store, from this cortex",
                "downloaded": "downloaded to the store, checked against its SHA256SUMS",
                "put back": "put back in the store as it was — a sync cut short had set it aside, and it could "
                            "not be downloaded again"}[how]
        out.write(f"Cortex {version}: {said} — {display(str(spec_source))}\n")
    where = config.lock_of(project, origin)
    digest, lock = None, None
    if source is None and where is None:
        digest = the_store.digest(version)      # a local try: no lock, nor checked again (ADR-013 §3.4)
    elif source is None:
        # During a try, a plain sync reads the try and leaves the committed lock: `cortex upgrade` writes it —
        # named with the version, which resumes it, where a plain one would move a project that pins its own.
        ahead = project.version != project.team_version
        digest, lock = check_lock(where[0], version, the_store, out, locked, where[1],
                                  f"`cortex upgrade {version}`, which {_read_above(project)} leaves to it,"
                                  if locked and ahead else "`cortex sync`", project.team_folder)
    # CI reads what the team committed: a folder cortex.toml names, not checked out, reads nothing of it —
    # missing, or a submodule left empty (ADR-013 §3.4); the default agents/ left empty stays a warning.
    folder_note = _team_folder_note(project)
    if folder_note is not None and locked and project.team_folder != config.TEAM_FOLDER:
        raise SyncError(f"--locked: {folder_note}. Nothing of the project was changed")
    if folder_note is not None:
        err.write(f"warning: {folder_note}\n")
    for code, said in renamed_folder_notes(project):
        if locked and code == "TEAM_FOLDER_MISSING":
            raise SyncError(f"--locked: {said}. Nothing of the project was changed")
        err.write(f"warning: {said}\n")

    # Everything is prepared and checked before anything of the project moves: the new
    # cortex.local.toml, the new link or copy beside cortex/. Then one rename puts it in place.
    spec = display(str(spec_source)) if mode == "store" else LINK
    recorded = synced_record(project, "" if source is not None else str(version))
    local_text = config.render_local(root, {"spec": spec, **({"claude_access": claude_access}
                                                             if claude_access is not None else {}),
                                            "synced": recorded},
                                     {"spec": "written by `cortex sync`",
                                      "synced": "written by `cortex sync` — the versions it read"},
                                     drop_synced=project.synced_unread)
    keep, new = False, None
    if mode == "link":
        keep = existing == "link" and os.path.normcase(link_target(target)) == os.path.normcase(str(spec_source))
        new = None if keep else stage_link(Path(root), spec_source)
    elif mode == "copy":
        marker = read_marker(target) if existing == "copy" else None
        keep = bool(marker) and source is None and marker.get("version") == str(version) \
            and marker.get("from") is None \
            and store.digest_of(marker.get("files") or {}) == digest   # the store's, as locked
        missing = missing_files(target, marker) if keep else []
        if missing:
            keep = False
            err.write(f"note: {LINK}/ lacked {len(missing)} of the files sync copied — "
                      f"{', '.join(missing[:3])}{', …' if len(missing) > 3 else ''}: copied again\n")
        new = None if keep else stage_copy(Path(root), spec_source, None if source else str(version),
                                           display(str(spec_source)) if source else None)
    if keep and mode == "copy":
        _seal(target)                          # a sync cut short between its rename and the seal left it open
    if lock is not None:
        # Before the swap: a lock that cannot be written stops the sync with nothing moved. It names
        # the version from the file it is beside, whatever comes next. The team's is written under a
        # lock of its folder, which several projects may sync at once; one this user cannot write is a
        # warning on a developer's machine, an error under --locked (ADR-013 §3.4).
        if where[1] == config.TEAM_LOCK_FILE:
            try:
                with project_lock(Path(where[0]), err):
                    config.write_lock(where[0], version, digest, where[1])
            except OSError as error:
                if locked or error.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                    raise
                err.write(f"warning: {where[2]} cannot be written — {os_error(error)}: the team's lock is not "
                          "updated here\n")
                lock = None
        else:
            config.write_lock(root, version, digest)
    try:
        aside = None if keep else swap(target, new, existing)
    except BaseException:
        if new is not None and os.path.lexists(new):
            _discard(new)
        raise
    try:
        config.write_local_text(root, local_text)
    except BaseException:
        if not keep:
            unswap(target, aside)
        raise
    if aside is not None:
        _discard(aside)

    if mode == "store":
        out.write("The spec is read where it is: nothing of it is in the project.\n")
    elif mode == "link":
        if gone is not None:
            out.write(f"{LINK}/ was a link to {shown(display(gone))}, which led nowhere: replaced.\n")
        out.write(f"{LINK}/ links to {display(str(spec_source))}.\n")
    else:
        out.write(f"{LINK}/ is a read-only copy of {display(str(spec_source))}.\n")
    out.write(f'{config.LOCAL_FILE}: spec = "{spec}"\n')
    if source is not None:
        out.write(f"{config.LOCK_FILE}: neither read nor written — a checkout has no version (--from)\n")
    elif lock is not None:
        said = team_lock_note(where[0], version, project.team_folder) if where[1] == config.TEAM_LOCK_FILE \
            else commit_it(where[0], version)
        out.write(f"{where[2]}: {lock}{said}\n")
    if notes:
        _notes(Path(root), mode, err)
    _claude(Path(root), project, None if mode == "copy" else spec_source, out, err)
    theme = project.active_theme
    if theme != "none" and not any(base.is_dir() for base in (spec_source / "agents" / "personalities" / theme,
                                                              Path(project.team_dir) / "personalities" / theme)):
        err.write(f'warning: theme "{theme}" is neither in the spec nor in {project.team_folder}/personalities/ — '
                  "the Prompt Manager would not find it\n")


def _claude(root: Path, project: config.Project, spec: Optional[Path], out: TextIO, err: TextIO) -> None:
    """Keep Claude Code's permission to read the spec without asking, when the project or the
    developer asks for it (``claude_access``) — and only the entry Cortex wrote, recorded as
    ``claude_entry``. A copy is inside the project: nothing to allow. While neither file sets
    ``claude_access``, and Cortex wrote no entry, Claude Code's settings are left alone."""
    access = project.active_claude_access
    if access is None and project.claude_entry is None:
        return
    allow = claude.entry_for(spec) if access and spec is not None else None
    try:
        said, owned = claude.update(root, allow, project.claude_entry,
                                    claude.entry_for(spec) if spec is not None else None)
    except claude.ClaudeSettingsError as error:
        err.write(f"warning: {error}\n")
        return
    if owned != project.claude_entry:
        config.write_local(str(root), {"claude_entry": owned},
                           {"claude_entry": f"written by `cortex sync` — the entry it keeps in {claude.SETTINGS}"})
    if said:
        out.write(f"{said}\n")
    elif access is False and spec is not None and claude.allows(root, claude.entry_for(spec)):
        # Off, with nothing of Cortex's to remove: an entry of the developer's still grants it.
        err.write(f"note: {claude.SETTINGS} has an entry of your own that lets Claude Code read "
                  f"{claude.entry_for(spec)} without asking — remove it there for Claude Code to ask again\n")
    if allow is not None:
        added = add_to_gitignore(root, {claude.SETTINGS: claude.SETTINGS})
        if added:
            out.write(f".gitignore: {', '.join(added)}\n")


def add_to_gitignore(root: Path, entries: Dict[str, str]) -> List[str]:
    """Add to ``.gitignore`` each line of ``entries`` whose path — its value — git does not ignore
    yet, and return them. The file keeps its bytes: its encoding, its byte order mark, its line
    endings. Without a repository to ask, a line is added unless it, or one that means the same,
    is there with no negation below it — and no .gitignore is created: it would keep nothing out of any commit. A
    .gitignore that is a symbolic link is never written through — git does not read it, and its target may be outside
    the project, or none yet: the note names the link (``_notes``)."""
    path = root / ".gitignore"
    if is_link(path) or not_a_file(path):        # never written through, nor waited on: the note names it
        return []
    if not path.is_file() and ignored(root, config.LOCAL_FILE) is None:
        return []
    data = path.read_bytes() if path.is_file() else b""
    lines = gitignore_lines(data.decode("utf-8", errors="replace"))
    missing = []
    for line, probe in entries.items():
        state = ignored(root, probe)
        if state is None:                       # no repository to ask
            name = probe.rstrip("/")
            state = _named(lines, {name, f"/{name}"} | ({f"{name}/", f"/{name}/"} if probe.endswith("/") else set()),
                           name)
        # A line that is a pattern is asked of a name standing in for the paths it matches: a negation below it that
        # is a pattern too, or names one of them, may take back another — `!/.cortex-sync-[0-9]*` the real ones.
        if not state and not _named(lines, {line}, probe, _stands_in(line)):  # there, with no negation below: kept
            missing.append(line)
    if missing:
        newline = b"\r\n" if b"\r\n" in data else b"\n"
        if data and not data.endswith(b"\n"):
            data += newline
        path.write_bytes(data + b"".join(line.encode("utf-8") + newline for line in missing))
    return missing


in_no_repository = config.in_no_repository     # ADR-008 §9: a root that is no work tree is committed nowhere
committed = config.committed                   # ADR-013 §3.1


def gitignore_lines(text: str) -> List[str]:
    """``.gitignore``'s lines as git reads them, in their order: split at a line feed only — a lone carriage return,
    a form feed, a line separator are text to git — one carriage return before it left out, the byte order mark an
    editor may put at the start of the file left out, and each line's trailing spaces — not one a backslash escapes,
    nor a tab: what git leaves out of a pattern, and nothing more. One reading, to tell what is ignored and what to
    add alike."""
    text = text[1:] if text.startswith("\ufeff") else text            # one, as git skips: a second is text
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    return [re.sub(r"(?<!\\) +$", "", line) for line in lines]


_GLOB = re.compile(r"[*?\[\\]")


def _stands_in(line: str) -> Optional[Pattern[str]]:
    """The paths ``line`` of .gitignore matches, when it is a pattern — one Cortex cannot read matching any —, for
    which a name only stands in (``_negations``); ``None`` for a line that names one path."""
    if _GLOB.search(line) is None:
        return None
    return _pattern(line) or re.compile(".*", re.DOTALL)


def _negations(lines: List[str], name: str, glob: Optional[Pattern[str]] = None) -> bool:
    """Whether a line of ``lines`` is a negation that can match ``name``, read as git reads a pattern of the root's
    .gitignore (``_pattern``): one with a ``/`` but at its end against the path from the root, any other against its
    last component too — and against both, over-matching being the safe side —, the name as written and case folded,
    as a repository that does not ignore case and one that does compare it, and a name written decomposed as the same
    name composed (macOS writes them so). A negation Cortex cannot read is one that may: only git can tell; and so is
    any pattern, or any name ``glob`` matches, when ``name`` stands in for the paths of the pattern ``glob``. Any
    other — ``!.gitkeep``, ``!*.d.ts`` — takes back another path, never this one."""
    paths = {unicodedata.normalize("NFC", name.rstrip("/")), _folded(name.rstrip("/"))}
    paths |= {path.rsplit("/", 1)[-1] for path in paths}
    for line in lines:
        if not line.startswith("!"):
            continue
        pattern = _pattern(line[1:])
        if pattern is None or any(pattern.fullmatch(path) for path in paths) or (
                glob is not None and (_GLOB.search(line[1:]) is not None
                                      or glob.fullmatch(unicodedata.normalize("NFC", line[1:].strip("/"))))):
            return True
    return False


def _folded(text: str) -> str:
    """``text`` as a repository that ignores case compares it, composed: a letter with its accent and the letter
    followed by a combining accent are one."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _pattern(text: str) -> Optional[Pattern[str]]:
    """A .gitignore pattern, its ``!`` left out, as git's wildmatch reads it, composed, and blind to case but in a
    negated bracket, whose members are compared as written — over-matching in a repository that ignores case and one
    that does not: ``*`` and ``?`` within one component, ``**`` as a whole component across any number of them —
    one right after the literal head too, which git compares before it globs the rest —, a bracket expression, a
    backslash that makes the next character a literal — one of the root's .gitignore, its leading ``/`` and a final
    ``/`` set aside: a final ``/`` keeps it to directories, and over-matching is the safe side. ``None`` for what only
    git reads: a character class such as ``[:alpha:]``, a bracket never closed, a backslash at the end."""
    text = unicodedata.normalize("NFC", text)       # case: re.IGNORECASE, a negated bracket aside
    if text.endswith("/"):
        text = text[:-1]
    if text.startswith("/"):
        text = text[1:]
    head = next((index for index, char in enumerate(text) if char in "*?[\\"), len(text))
    out, at = [], 0
    while at < len(text):
        char = text[at]
        if char == "\\":
            if at + 1 == len(text):
                return None
            out.append(re.escape(text[at + 1]))
            at += 2
        elif char == "*":
            end = at
            while end < len(text) and text[end] == "*":
                end += 1
            # git compares the literal head before the first wildcard, then globs the rest: a `**` right after the
            # head is at the start of what it globs — whole, as one after a `/` is (dir.c, match_pathname).
            whole = end - at >= 2 and (at == head or text[at - 1] == "/") and (end == len(text) or text[end] == "/")
            if whole and end < len(text):
                out.append("(?:.*/)?")          # `**/`: any number of folders, none included
                end += 1
            else:
                out.append(".*" if whole else "[^/]*")
            at = end
        elif char == "?":
            out.append("[^/]")
            at += 1
        elif char == "[":
            found = _bracket(text, at + 1)
            if found is None:
                return None
            out.append(found[0])
            at = found[1]
        else:
            out.append(re.escape(char))
            at += 1
    try:
        return re.compile("".join(out), re.DOTALL | re.IGNORECASE)
    except re.error:                            # a range backwards, say: git's to read
        return None


def _bracket(text: str, at: int) -> Optional[Tuple[str, int]]:
    """The bracket expression of ``text`` that starts at ``at``, past its ``[``, as a regular expression that never
    matches ``/``, and where it ends; ``None`` for a class such as ``[:alpha:]``, or one never closed."""
    negated = at < len(text) and text[at] in "!^"
    at += negated
    members, first = [], True
    while at < len(text):
        char = text[at]
        if char == "]" and not first:
            if negated:                         # never narrowed by case: git compares its members as written
                return f"(?-i:(?!/)[^{''.join(members)}])", at + 1
            return f"(?!/)[{''.join(members)}]", at + 1
        if char == "[" and text.startswith("[:", at):
            return None
        if char == "\\":
            if at + 1 == len(text):
                return None
            char, at = text[at + 1], at + 1
        if at + 2 < len(text) and text[at + 1] == "-" and text[at + 2] != "]":
            high = text[at + 2]
            if high == "\\":
                if at + 3 == len(text):
                    return None
                high, at = text[at + 3], at + 1
            members.append(f"{re.escape(char)}-{re.escape(high)}")
            at += 3
        else:
            members.append(re.escape(char))
            at += 1
        first = False
    return None


def _named(lines: List[str], wanted: Set[str], name: str, glob: Optional[Pattern[str]] = None) -> bool:
    """Whether a line of ``lines`` names the path ``name`` as one of ``wanted`` writes it, with no negation below the
    last such line that could take it back: what Cortex can tell without git. A pattern, another .gitignore,
    info/exclude or core.excludesFile is git's to read: not that Cortex can tell. ``glob``: the paths of the line of
    ``wanted``, a pattern, ``name`` stands in for (``_stands_in``)."""
    found = [index for index, line in enumerate(lines) if line in wanted]
    return bool(found) and not _negations(lines[found[-1] + 1:], name, glob)


def git_ignores(root: Path, name: str) -> Optional[bool]:
    """git's own answer, whether it ignores ``name`` in the project: ``None`` when there is no git to ask, or it
    refuses the repository — its owner, say — or there is none, or it would wait on a .gitignore that is no file."""
    git = shutil.which("git")
    if git is None or not_a_file(root / ".gitignore"):     # a named pipe there: git would wait on it
        return None
    try:
        proc = subprocess.run([git, "-C", str(root), "check-ignore", "-q", "--", name],
                              capture_output=True, timeout=30, env=git_environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode == 0 if proc.returncode in (0, 1) else None


def ignored(root: Path, name: str) -> Optional[bool]:
    """Whether git ignores ``name`` in the project: asked of git when the project is a repository
    of it; ``None`` when there is no repository to ask about — no ``.git`` at the root or above it.
    Without git's answer, ``True`` only for what Cortex can tell from the project's own .gitignore."""
    answer = git_ignores(root, name)
    if answer is not None:
        return answer
    if in_no_repository(root):
        return None
    # No git to ask, or git refusing the repository: the project's own .gitignore, at the root of the repository or
    # in a subdirectory of it, read for a line that names the path as Cortex writes it — no pattern, no negation
    # below it, no .gitignore of a folder above, which git would read too. A link is a file to git — `cortex/` does
    # not match it. A .gitignore that is a link git does not read, nor does Cortex.
    if is_link(root / ".gitignore"):
        return False
    directory = (root / name).is_dir() and not is_link(root / name)
    wanted = {name, f"/{name}"} | ({f"{name}/", f"/{name}/"} if directory else set())
    try:
        lines = gitignore_lines(cascade.read_file(str(root / ".gitignore")).decode("utf-8", errors="replace"))
    except OSError:
        return False
    return _named(lines, wanted, name)


def _not_ignored(root: Path, name: str, shown: str) -> Optional[str]:
    """What a note says of ``name``, which git must ignore: that git does not, or — without git's answer — that
    Cortex cannot tell it does; ``None`` when it is ignored, or the root is in no repository."""
    answer = git_ignores(root, name)
    if answer is not None:
        return None if answer else f"git does not ignore {shown}"
    if ignored(root, name) is not False:        # in no repository, or a line of .gitignore names it
        return None
    return f"git could not be asked whether it ignores {shown}, and Cortex cannot tell from .gitignore that it does"


def developers_own(path: Path) -> bool:
    """Whether the bootstrap at ``path`` is the developer's own (ADR-013 §3.1): one git ignores, or one in no
    repository — a link asked of the file it resolves to, in that file's repository; a file not yet written,
    of the first folder there is on its way. Any other is the team's."""
    real = Path(os.path.realpath(path))
    folder = real.parent
    while not folder.is_dir() and folder != folder.parent:
        folder = folder.parent
    # git matches its patterns against `/`: a Windows relative path would match none of them.
    return in_no_repository(folder) or ignored(folder, Path(os.path.relpath(real, folder)).as_posix()) is True


def _notes(root: Path, mode: str, err: TextIO) -> None:
    # A .gitignore that is a symbolic link, which git does not read: no line added to it would count (#329).
    linked = ".gitignore is a symbolic link, which git does not read: make it a file, with " \
        if is_link(root / ".gitignore") else ".gitignore is not a file, which git cannot read: make it a file, with " \
        if not_a_file(root / ".gitignore") else None
    said = _not_ignored(root, LINK, f"{LINK}/") if mode != "store" else None
    if said is not None:
        line = f"a `/{LINK}` line" + ("" if linked else " to .gitignore")
        err.write(f"note: {said} — {linked or 'add '}{line}, without the final slash: a link is a file to git\n")
    said = _not_ignored(root, config.LOCAL_FILE, f"{config.LOCAL_FILE}, which holds this machine's paths")
    if said is not None:
        err.write(f"note: {said} — {linked + 'a line for it' if linked else 'add it to .gitignore'}\n")


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex sync",
        description="Put the Cortex version cortex.toml pins in the store, and tell the project where it is "
                    "(spec, in cortex.local.toml). ADR-008 §3.5.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--store", dest="mode", action="store_const", const="store",
                       help="read the spec where it is, in the store (the default)")
    modes.add_argument("--link", dest="mode", action="store_const", const="link",
                       help="link cortex/ to it: a symbolic link, a junction on Windows")
    modes.add_argument("--copy", dest="mode", action="store_const", const="copy",
                       help="copy it into cortex/, read-only")
    parser.add_argument("--from", dest="source", metavar="PATH",
                        help="use a checkout of Cortex instead of the store: no version is checked")
    parser.add_argument("--locked", action="store_true",
                        help="never write cortex.lock: stop when it is missing, or names another version than "
                             "cortex.toml — for a project's CI, before `cortex validate --strict`")
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--claude-access", dest="claude_access", action="store_const", const=True,
                        help="let Claude Code read the spec without asking, from now on, for you: "
                             "claude_access = true in cortex.local.toml")
    access.add_argument("--no-claude-access", dest="claude_access", action="store_const", const=False,
                        help="stop it: claude_access = false in cortex.local.toml")
    options = parser.parse_args(args)
    if options.locked and options.source:
        parser.error("--locked checks cortex.lock, and --from reads none: a checkout has no version")
    try:
        sync(working_directory(), options.mode, options.source, sys.stdout, sys.stderr, options.claude_access,
             locked=options.locked, bootstraps=False)
    except (config.ConfigError, store.StoreError, SyncError) as error:
        return failed("cortex sync", str(error))
    except OSError as error:
        return failed("cortex sync", os_error(error))
    except subprocess.CalledProcessError as error:
        return failed("cortex sync", f"{' '.join(map(str, error.cmd))} failed: "
                                     f"{(error.stderr or b'').decode(errors='replace').strip() or error.returncode}")
    return 0


def os_error(error: OSError) -> str:
    """An OSError as the user reads it: what failed, on which path."""
    paths = [display(str(p)) for p in (error.filename, error.filename2) if p]
    return f"{error.strerror or error}" + (f": {' → '.join(paths)}" if paths else "")


def failed(command: str, message: str, code: int = 1) -> int:
    sys.stdout.flush()
    sys.stderr.write(f"{command}: {message}\n")
    return code
