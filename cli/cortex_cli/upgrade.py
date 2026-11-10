"""``cortex upgrade`` — move a project to another Cortex version (ADR-010 §3.5).

``cortex upgrade [VERSION]`` moves the project of the current directory — the nearest
``cortex.toml`` — to ``VERSION``, or to this binary's own version. Before it changes anything it
says what changes: the agents the target adds, removes and changes, layer by layer; then it runs
the overlay validator with the target as the base, and stops if the project would fail it, unless
``--force``. The switch is ``version`` written in ``cortex.toml``, its comments kept, and a sync —
``cortex.local.toml``, the link or copy, ``cortex.lock``. A bootstrap file nobody changed — the
digest in its marker says so, or, without one, its being the pinned version's file — is written
again from the target's template whenever that differs; one a team changed is kept, and the command
says how ``cortex init --force`` writes it again. On the pinned version, nothing moves, and what an
upgrade cut short or a pin moved by hand left is finished: a file nobody changed whose text is not
the pinned version's is written again, and a machine or a ``cortex.lock`` not on the pin is synced.
The bootstrap files are written first, each whole or not at all.

With no ``VERSION``, a project pinned after this cortex is refused: a downgrade is named.

``--dry-run`` says all of it and changes nothing in the project; the store may gain the target.
The binary is never upgraded here: ``cortex self-update`` does that, and with no ``VERSION`` this
command says when a release newer than the binary is out — learnt while it works, a few seconds at
most, and said by nothing offline. ``CORTEX_NO_UPDATE_CHECK``, set, keeps it from asking.

``cortex upgrade --all`` moves every project of the registry (ADR-011 §3.5), in name order, to the
same target: one that is not ``ok`` is skipped, one that refuses stops none of the others, and the
command ends with a line per project and what each must commit. The release is asked for once.
``--branch NAME`` makes the branch ``NAME`` in each project that is a git work tree and has something
to commit, before anything of it is written — refused where it exists, unless it is the branch
checked out; ``--commit --message M`` commits there the files the upgrade wrote, and nothing else —
nothing at all when ``cortex.toml``, or a bootstrap file it wrote, held the developer's own changes
— the lock never counts. Neither is the default, and nothing is ever pushed.
"""

from __future__ import annotations

import argparse
import errno
import io
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

from cortex_core import validate as overlay_validator
from cortex_core import project as grammar
from cortex_core.project import shown

from . import config, init, machine, registry, self_update, store, sync
from .paths import display, working_directory
from .semver import Version
from .version import VERSION

LAYERS = ("roles", "capabilities", "personalities", "workflows")
TEMPLATES = {"single": "bootstrap-instructions.md", "workspace": "bootstrap-instructions-workspace.md"}


class UpgradeError(Exception):
    pass


@dataclass
class Outcome:
    """What the upgrade of one project did — or, dry, would do: what ``--all`` says of it, and what
    ``--commit`` commits."""
    root: str
    pinned: Optional[Version]       # the version it was on — None when it was refused before it was read
    target: Optional[Version]
    code: int = 0                   # 1 when it stopped, or would
    why: Optional[str] = None       # why it stopped, or would
    files: List[Path] = field(default_factory=list)     # what the team commits, written — or that would be
    committed: Optional[str] = None                     # --commit: where — the branch, and the commit
    uncommitted: Optional[str] = None                   # --commit: why nothing was
    ignored: bool = False                               # git ignores every one of the files: nothing to commit
    nowhere: bool = False                               # in no git work tree: the files are committed nowhere
    clean: bool = False                                 # --commit: the files are as committed already
    left: List[Path] = field(default_factory=list)      # --commit, nothing written: files git sees changed still
    outside: List[Path] = field(default_factory=list)   # --commit: of the files, those outside the git work tree
    team: Optional[str] = None                          # --team: the team's folder, where --commit commits its files
    team_files: List[Path] = field(default_factory=list)  # --team: the team's files, which --commit commits there

    def names(self) -> List[str]:
        """The files to commit in the project — those outside its git work tree, ``elsewhere``."""
        return [_shown_path(path, self.root) for path in self.files if path not in self.outside]

    def elsewhere(self) -> str:
        """The files written outside the project's git work tree, where they are, and what to do — ``""``
        when there are none."""
        if not self.outside:
            return ""
        them = "them where they are" if len(self.outside) > 1 else "it where it is"
        where = _and([display(os.path.realpath(path)) for path in self.outside])
        return f"{where} — outside its git work tree: commit {them}"


# --------------------------------------------------------------------------- #
# What changes
# --------------------------------------------------------------------------- #

def compare(old: Dict[str, str], new: Dict[str, str]) -> Tuple[List[str], List[str], List[str]]:
    """The paths added, removed and changed from ``old`` to ``new``."""
    return (sorted(set(new) - set(old)), sorted(set(old) - set(new)),
            sorted(path for path in set(old) & set(new) if old[path] != new[path]))


def _under(hashes: Dict[str, str], prefix: str, agents: bool = False) -> Dict[str, str]:
    """The files under ``prefix``, by path below it — an agent by its name, without ``.md``; a
    layer's README, which is no agent, by its file's."""
    found = {}
    for path, sha in hashes.items():
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if agents and rel.endswith(".md") and rel.rsplit("/", 1)[-1].casefold() != "readme.md":
            rel = rel[:-3]
        found[rel] = sha
    return found


def changes(old: Path, new: Path) -> List[Tuple[str, Tuple[List[str], List[str], List[str]]]]:
    """What the spec at ``new`` changes from the one at ``old``: each agent layer, by agent, then
    the templates by name, then the docs — from the files the store's digest reads."""
    before, after = store.hashes(old), store.hashes(new)
    found = [(layer, compare(_under(before, f"agents/{layer}/", True), _under(after, f"agents/{layer}/", True)))
             for layer in LAYERS]
    found += [(tree, compare(_under(before, f"{tree}/"), _under(after, f"{tree}/"))) for tree in ("templates", "docs")]
    return found


def say_changes(found, out: TextIO) -> None:
    for name, (added, removed, changed) in found:
        if not (added or removed or changed):
            out.write(f"  {name:<14} no change\n")
            continue
        counts = ", ".join(f"{len(paths)} {what}" for paths, what in
                           ((added, "added"), (removed, "removed"), (changed, "changed")) if paths)
        out.write(f"  {name:<14} {counts}\n")
        if name == "docs":
            continue            # the docs are many, and read through the release notes
        for sign, paths in (("+", added), ("-", removed), ("~", changed)):
            for path in paths:
                out.write(f"      {sign} {path}\n")


# --------------------------------------------------------------------------- #
# The bootstrap file
# --------------------------------------------------------------------------- #

def _instructions_files(root: Path, named: Optional[str]) -> List[Path]:
    """The instructions files to look at: the one named, or every known tool's that is there."""
    if named is not None:
        path = Path(named) if os.path.isabs(named) else Path(working_directory()) / named
        if not path.is_file():
            raise UpgradeError(f"--instructions-file {display(str(path))}: no such file")
        return [path]
    return [root / rel for rel in init.TOOLS.values() if (root / rel).is_file()]


def written_by(kept: bytes, templates: Path, layouts: List[str], personality: bool, path: Path) -> Optional[str]:
    """The layout ``kept`` — normalised — is exactly the file of, as the templates at ``templates``
    write it for this personality: the team's theme decides it, as for ``cortex init``. ``None``
    when a team changed it, or another version wrote it. A Cursor rule is that file with or without
    the frontmatter a Cortex before 1.6.0 did not write (#306)."""
    for layout in layouts:
        template = templates / TEMPLATES[layout]
        if template.is_file():
            text = init.instructions(template.read_bytes(), personality)
            if kept in (text, init.for_tool(path, text)):
                return layout
    return None


def _written(templates: Path, layout: str, personality: bool, path: Path) -> Optional[bytes]:
    """The bootstrap the templates at ``templates`` write for ``layout``, as Cortex writes it at ``path``."""
    template = templates / TEMPLATES[layout]
    if not template.is_file():
        return None
    return init.stamped(init.for_tool(path, init.instructions(template.read_bytes(), personality)))


def _format(content: Optional[bytes]) -> int:
    found = init.markers(content or b"")
    return found[0][1] if found else 0


def bootstrap(path: Path, pinned: Version, current: Optional[Path], target: Path,
              personality: bool) -> Tuple[Optional[bytes], Optional[str]]:
    """What becomes of the bootstrap at ``path`` when the project moves from ``pinned``, whose spec
    is at ``current`` — ``None`` when the store has it not — to the spec at ``target`` (ADR-010
    §3.5, step 5): the bytes to write, or the note to give; ``(None, None)`` when nothing is to be
    done. A file nobody changed is written again whenever the target writes another, whichever the
    direction: its marker's digest says so, or, without one, its being the pinned version's file."""
    kept = path.read_bytes()
    content = init.normalised(kept)
    found = init.markers(content)
    # A workspace's file — by its markers, or, written before the marker, by its title — is named with --workspace,
    # which cortex init reads from it without the option too (#329).
    workspace = init.written_for_workspace(content)
    again = f"cortex init {init.tool_options(path)} --force{' --workspace' if workspace else ''} writes it again, " \
        "and keeps yours as .bak"
    if len(found) > 1:
        return None, f"{display(str(path))} holds several bootstrap markers, so none is read, and it is kept: {again}"
    if not (found or init.bootstrap_of_cortex(content)):
        return None, None       # no Cortex bootstrap: cortex init says so
    layouts = [found[0][0]] if found else list(TEMPLATES)
    # ADR-012 §3.4: the end marker of the file `cortex init --force` writes, the target's — none before format 2.
    ends = any(init.END_MARKER.search(_written(target / "templates", name, personality, path) or b"") for name in layouts)
    below = " — your own rules go below its end marker, where Cortex keeps them" if ends else ""
    intact = init.untouched(content)
    if intact is None and current is None:
        return None, (f"{display(str(path))} was not checked: its marker carries no digest, and Cortex {pinned}, whose "
                      f"templates would tell whether a team changed it, is not in the store. It is kept: {again}")
    layout = layouts[0] if intact else None
    if intact is None:
        layout = written_by(init.parts(content)[0], current / "templates", layouts, personality, path)
    if layout is None:
        # A team changed it: said when the target writes another file than the pinned version, or,
        # without the pinned version, a later format — and, of a Cursor rule, when it lacks the
        # `alwaysApply: true` line Cursor documents. Both, when both hold.
        cursor = init.is_cursor_rule(path) and not init.applies_always(content)
        if current is not None:
            matters = any(_written(current / "templates", name, personality, path) !=
                          _written(target / "templates", name, personality, path) for name in layouts)
        else:
            matters = _format(content) < max(_format(_written(target / "templates", name, personality, path))
                                             for name in layouts)
        if not matters:
            if cursor:
                return None, (f"{display(str(path))} is not what Cortex wrote — a team changed it — and it has no "
                              "`alwaysApply: true` line in a frontmatter, as Cursor's documentation writes it: Cursor "
                              f"may not apply it in every conversation. It is kept: {again}{below}")
            if not found and intact is None:    # #313: no marker, and not the pinned version's file
                return None, (f"{display(str(path))} is a bootstrap without the marker, not what Cortex {pinned} writes "
                              f"— a team changed it, or an older Cortex wrote it — so it is kept: {again}{below}")
            return None, None
        what = "not what Cortex wrote, by the digest in its marker — a team changed it" if intact is False else \
            (f"{f'format {found[0][1]}' if found else 'a bootstrap without the marker'}, not what Cortex {pinned} "
             "writes — a team changed it, or an older Cortex wrote it")
        rule = ("; and with no `alwaysApply: true` line in a frontmatter, as Cursor's documentation writes it, "
                "Cursor may not apply it in every conversation") if cursor else ""
        return None, (f"{display(str(path))} is {what} — so it is kept, and the target's template is not written"
                      f"{rule}: {again}{below}")
    wanted = _written(target / "templates", layout, personality, path)
    if wanted is None or wanted == init.parts(content)[0]:
        return None, None
    block, team = init.parts(init.without_bom(kept))    # ADR-012 §3.4: the team's part goes back as it was
    if init.LINE_BREAK.sub(b"\n", team).strip(b"\n") and init.END_MARKER.search(wanted) is None:
        # A target before format 2 has no end marker: the team's part would end up inside Cortex's block.
        return None, (f"{display(str(path))} has a part of its own below its end marker, and the target's template has "
                      "no end marker to keep it below — so it is kept as it is")
    if b"\r\n" in block:
        wanted = wanted.replace(b"\n", b"\r\n")
    return (init.BOM + wanted if kept.startswith(init.BOM) else wanted) + team, None


def _nobody(written: bytes) -> str:
    """Why a bootstrap is written again, as the run says it: nobody changed it — or, of a file with a team's
    part, Cortex's block, the team's part staying as it is (ADR-012 §3.4)."""
    if init.parts(init.without_bom(written))[1]:
        return "no one changed Cortex's block, and what is below its end marker stays as it is"
    return "no one changed it"


def bootstraps(files: List[Path], pinned: Version, current: Optional[Path], target: Path, personality: bool,
               err: TextIO) -> List[Tuple[Path, bytes]]:
    """The bootstrap files to write again, and what they are now; the notes said on ``err``."""
    rewrites = []
    for path in files:
        content, note = bootstrap(path, pinned, current, target, personality)
        if note:
            err.write(f"note: {note}\n")
        if content is not None:
            rewrites.append((path, content))
    return rewrites


def _and(names: List[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _put(path: Path, content: bytes) -> None:
    """Write ``content`` to ``path`` whole or not at all, as the pin and the lock are written: a
    temporary file beside the file — beside where a link points, so that the link stays one — then
    a rename, with the file's mode. A run killed meanwhile leaves the old file whole."""
    real = Path(os.path.realpath(path))
    temporary = real.with_name(f"{real.name}.{os.getpid()}.tmp")
    try:
        mode = stat.S_IMODE(real.stat().st_mode) if real.exists() else None
        with open(temporary, "wb") as fh:
            fh.write(content)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, real)
    except BaseException:
        if os.path.lexists(temporary):
            os.unlink(temporary)
        raise


# --------------------------------------------------------------------------- #
# The machine and the lock, against the pin
# --------------------------------------------------------------------------- #

def _reads(root: Path, project: config.Project, the_store: store.Store) -> Optional[str]:
    """The version whose spec this machine reads: ``""`` for a checkout synced with ``--from``;
    ``None`` for none — not synced here, or a ``cortex/`` sync did not make."""
    if project.spec is None:
        return None
    entry = root / sync.LINK
    if project.spec != sync.LINK:
        source = project.spec_directory()
    elif sync.is_link(entry):
        source = sync.link_target(entry)
    else:
        marker = sync.read_marker(entry)
        if marker is None:
            return None
        return "" if marker.get("from") is not None else marker.get("version")
    if os.path.normcase(os.path.realpath(os.path.dirname(os.path.normpath(source)))) != \
            os.path.normcase(os.path.realpath(the_store.versions)):
        return ""
    return os.path.basename(os.path.normpath(source))


def out_of_step(root: Path, project: config.Project, the_store: store.Store) -> Optional[str]:
    """What, of this machine and ``cortex.lock``, is not on the pinned version — left by an upgrade
    cut short between the pin and the end of its sync, or by a pin moved by hand; ``None`` when both
    are on it. A spec synced from a checkout is the developer's choice, and has no lock: left alone."""
    reads = _reads(root, project, the_store)
    if reads == "":
        return None
    # The machine, against the full resolution: a developer's try is no upgrade cut short (ADR-013 §3.11).
    if reads != str(project.version):
        return "this machine has not synced it" if reads is None else f"this machine reads Cortex {reads}"
    where = config.lock_of(project)
    if where is None:                       # a local try: no lock (ADR-013 §3.4)
        return None
    lock = config.load_lock(where[0], where[1])
    if lock is None:
        return f"there is no {where[2]}"
    if lock.version != project.version:
        return f"{where[2]} names Cortex {lock.version}"
    return None


def try_lock(project: config.Project) -> Optional[Tuple[Tuple[str, str, str], str]]:
    """During a try, the lock of the team's version — which the sync, reading the try, leaves alone
    (ADR-013 §3.4) — when it does not name that version: where it is, and what it names. ``None``
    without a try, or when it names it: a lock of that version whose digest differs is the sync's to
    refuse. ``cortex upgrade`` writes the lock of the version it moves; its resume checks that lock."""
    if project.version == project.team_version:
        return None
    where = config.lock_of(project, project.team_origin)
    if where is None:
        return None
    # The sync leaves it: `cortex upgrade` writes it — named with the version, which resumes it, where a plain one
    # would move a project that pins its own version.
    lock = config.load_lock(where[0], where[1], writer=f"`cortex upgrade {project.team_version}`")
    if lock is not None and lock.version == project.team_version:
        return None
    return where, f"there is no {where[2]}" if lock is None else f"{where[2]} names Cortex {lock.version}"


def _write_try_lock(where: Tuple[str, str, str], version: Version, the_store: store.Store, out: TextIO,
                    err: TextIO) -> None:
    """Write the lock ``where`` names — the team's version's, which a try leaves — from ``version`` in
    the store: the team's under a lock of its folder, as the sync writes it (ADR-013 §3.4). A team's
    folder this user cannot write is a warning, as it is to the sync on a developer's machine: the
    lock is not updated here."""
    directory, name, shown_name = where
    digest, said = sync.check_lock(directory, version, the_store, out, name=name)
    if shown_name == config.TEAM_LOCK_NAME:
        try:
            with sync.project_lock(Path(directory), err):
                config.write_lock(directory, version, digest, name)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise
            err.write(f"warning: {shown_name} cannot be written — {sync.os_error(error)}: the team's lock is not "
                      "updated here\n")
            return
    else:
        config.write_lock(directory, version, digest, name)
    team = f" in {config.TEAM_FOLDER}/" if shown_name == config.TEAM_LOCK_NAME else ""
    out.write(f"{shown_name}: {said}{'' if sync.in_no_repository(directory) else ' — commit it' + team}\n")


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def check_overlays(root: str, base: Path) -> Tuple[int, str]:
    """The overlay validator's exit code and report, with ``base`` as the spec."""
    report = io.StringIO()
    code = overlay_validator.validate(root, str(base), "", False, report, overlay_validator.Colors(False))
    return code, report.getvalue()


def _shown_path(path: Path, root: str) -> str:
    """A path as the project names it — absolute when it is elsewhere, another drive included."""
    try:
        return display(os.path.relpath(path, root))
    except ValueError:
        return display(str(path))


class _NewerRelease(threading.Thread):
    """The latest release, when it is newer than this binary — asked for while the command works."""

    def __init__(self, own: Version):
        super().__init__(daemon=True)
        self.own, self.found = own, None
        self.start()

    def run(self) -> None:
        try:
            self.found = self_update.newer_release(self.own)
        except Exception:       # a notice: never a traceback in the middle of the upgrade
            self.found = None

    def say(self, out: TextIO, every: bool = False) -> None:
        self.join(self_update.NOTICE_TIMEOUT)
        if self.found is not None:
            moves = "`cortex upgrade --all`, moves the projects" if every else "`cortex upgrade`, moves the project"
            out.write(f"\nCortex {self.found} is out, and this cortex is {self.own}: `cortex self-update`, then "
                      f"{moves} to it.\n")


def _asking(wanted: Optional[str]) -> Optional[_NewerRelease]:
    """The question of a release newer than this binary: asked with no ``VERSION``, once per command,
    unless the machine's ``update_check`` is off — ``CORTEX_NO_UPDATE_CHECK`` set, or ``config.toml``."""
    if wanted is not None or not machine.setting(store.cortex_home, "update_check").value:
        return None
    return _NewerRelease(Version(VERSION))


@dataclass(frozen=True)
class _Options:
    wanted: Optional[str]                   # VERSION
    dry_run: bool
    force: bool
    named: Optional[str] = None             # --instructions-file
    branch: Optional[str] = None            # --branch
    message: Optional[str] = None           # --commit, with its --message
    which: str = "project"                  # what moves: cortex.toml's version, --team's, or --local's


def upgrade(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, named: Optional[str],
            out: TextIO, err: TextIO, branch: Optional[str] = None, message: Optional[str] = None,
            which: str = "project") -> int:
    """``cortex upgrade``: the project of ``cwd``. ``branch`` is ``--branch``'s, ``message`` that of
    ``--commit``, which commits only when it is given. ``which`` — ``team`` or ``local`` — moves the
    team's file's version, or this developer's try, instead of ``cortex.toml``'s (ADR-013 §3.11)."""
    if which == "local":
        outcome = local_move(cwd, wanted, dry_run, force, out, err)
        return outcome.code
    if which == "team":
        outcome = team_move(cwd, wanted, dry_run, force, out, err, branch, message)
        # The team's files are those team_move commits — not whatever lies in the team's folder: a bootstrap
        # linked into it is the project's, written again and left uncommitted.
        team = [path for path in outcome.files if path in outcome.team_files]
        if not dry_run and outcome.why is None and team:
            own = [path for path in outcome.files if path not in team]
            # A bootstrap linked into the team's folder is committed there, where it leads — git sees it changed
            # in the team's repository; the others, in the project.
            there = [path for path in own if outcome.team is not None and _inside(outcome.team, path)]
            mine = [_shown_path(path, outcome.root) for path in own if path not in there]
            leads = _and([_shown_path(path, outcome.root) for path in there]) if there else ""
            lead = "they lead" if len(there) > 1 else "it leads"
            if outcome.committed is None:
                also = f", and {leads} there too, where {lead}" if there else ""
                ours = f"; {_and(mine)} in the project" if mine else ""
                out.write(f"\nCommit {_and([_shown_path(path, outcome.root) for path in team])} in "
                          f"{config.TEAM_FOLDER}/{also}{ours}; teammates run `cortex sync` after they pull.\n")
            else:
                left = ([f"{leads} not committed — {lead} into {config.TEAM_FOLDER}/, where --team commits the "
                         f"team's files only: commit {'them' if len(there) > 1 else 'it'} there"] if there else []) + \
                    ([f"{_and(mine)} not committed: --team commits in the team's folder only"] if mine else [])
                out.write(f"\nCommitted in {config.TEAM_FOLDER}/ on {outcome.committed} — nothing was pushed"
                          f"{''.join('; ' + part for part in left)}; teammates run `cortex sync` after they pull.\n")
        return outcome.code
    newer = _asking(wanted)
    try:
        outcome = _move(cwd, _Options(wanted, dry_run, force, named, branch, message), out, err)
    except BaseException:
        if newer is not None:       # a thread left in a request as Python exits can crash it
            newer.join(self_update.NOTICE_TIMEOUT)
        raise
    if not dry_run and outcome.why is None:
        _ending(outcome, out)
    if newer is not None:
        newer.say(out)
    return outcome.code


def _ending(outcome: Outcome, out: TextIO) -> None:
    """What the project must commit — or what ``--commit`` committed, and where; then what is to
    commit where it is, outside the project's git work tree."""
    names, elsewhere = ([] if outcome.clean else outcome.names()), outcome.elsewhere()
    if outcome.nowhere:
        ready = f"Cortex {outcome.target} is ready. " if outcome.target != outcome.pinned else ""
        out.write(f"\n{ready}Written: {_and(names)} — in no git work tree, so committed nowhere: each developer "
                  f"moves their own (ADR-008 §9){'; ' + elsewhere if elsewhere else ''}.\n")
    elif outcome.ignored:
        ready = f"Cortex {outcome.target} is ready. " if outcome.target != outcome.pinned else ""
        out.write(f"\n{ready}Nothing to commit: git ignores {_and(names)}.\n")
    elif outcome.target != outcome.pinned:
        done = f"Committed {', '.join(names)} on {outcome.committed} — nothing was pushed" if outcome.committed else \
            f"Commit {', '.join(names)}" if names else ""
        done = "; ".join(part for part in (done, elsewhere) if part)
        out.write(f"\nCortex {outcome.target} is ready. {done + '; teammates' if done else 'Teammates'} run `cortex sync` "
                  "after they pull.\n")
    elif names or elsewhere:
        done = f"Committed {_and(names)} on {outcome.committed} — nothing was pushed" if outcome.committed else \
            f"Commit {_and(names)}" if names else ""
        out.write(f"\n{'; '.join(part for part in (done, elsewhere) if part)}.\n")


def _project(cwd: str) -> Tuple[str, config.Project]:
    root = config.find_project(cwd)
    if root is None:
        raise UpgradeError(f"no {config.PROJECT_FILE} in {display(cwd)} or above it — `cortex init` makes a directory "
                           "a Cortex project")
    old = config.too_new(root, store.own_version())
    if old is not None and config.loose_pin(root)[0] == config.PROJECT_FILE:
        raise UpgradeError(old)
    return root, config.load(root)


def _overlays(root: str, target: Version, target_path: Path, force: bool, dry_run: bool, out: TextIO,
              err: TextIO) -> bool:
    """The project's overlays against ``target``: whether they stop the move."""
    code, report = check_overlays(root, target_path)
    if code == 0:
        out.write(f"The project's overlays pass against Cortex {target}.\n")
        return False
    out.write(f"\nThe project's overlays, checked against Cortex {target}:\n{report}")
    if force:
        err.write("warning: the overlays fail against the target, and --force moves it anyway\n")
        return False
    err.write(f"cortex upgrade: the overlays would fail against Cortex {target} — nothing was changed. Fix them, or "
              "--force moves it anyway\n")
    return True


def local_move(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, out: TextIO, err: TextIO) -> Outcome:
    """``cortex upgrade --local VERSION`` (ADR-013 §3.11): this developer's try, in ``cortex.local.toml``,
    synced — no committed file written. At or below the team's version, the try changes nothing: its
    key is removed, and said (decision 28)."""
    root, project = _project(cwd)
    team = project.team_version
    if grammar.before_team(str(team)):
        raise UpgradeError(f"a try in {config.LOCAL_FILE} is read once the team's version is "
                           f"{grammar.FIRST_TEAM_VERSION} or later — the team's is {team}. Nothing was changed")
    target = Version(wanted)
    out.write(f"Project: {display(root)}\n")
    tried = project.developer_version
    if target <= team:
        if tried is None or tried.origin != config.LOCAL_FILE:
            out.write(f"Cortex {target} is at or below the team's {team} ({project.team_origin}): there is no try "
                      "to remove — nothing to move\n")
            return Outcome(root, project.version, project.version)
        out.write(f"{config.LOCAL_FILE}: version {tried.value} {'would be' if dry_run else 'is'} removed — the team's "
                  f"{team} ({project.team_origin}) is read\n")
        if not dry_run:
            _local_then_sync(root, {"version": None}, out, err)
        return Outcome(root, Version(str(tried.value)), team)
    the_store = store.Store()
    _served(the_store, target, f"cortex upgrade --local {target}")
    the_store.ensure(target)
    out.write(f"Cortex {target}: a try, above the team's {team} ({project.team_origin}) — nothing committed is "
              "written\n")
    if _overlays(root, target, the_store.path(target), force, dry_run, out, err):
        return Outcome(root, project.version, target, 1, f"the overlays fail against Cortex {target} — nothing was changed")
    if dry_run:
        out.write("--dry-run: nothing of the project was changed\n")
        return Outcome(root, project.version, target)
    _local_then_sync(root, {"version": str(target)}, out, err)
    return Outcome(root, project.version, target)


def _local_then_sync(root: str, values: Dict[str, object], out: TextIO, err: TextIO) -> None:
    """Write ``values`` in ``cortex.local.toml``, then sync — the file put back as it was when the sync fails."""
    path = Path(root) / config.LOCAL_FILE
    before = path.read_bytes() if path.is_file() else None
    config.write_local(root, values, {"version": "your try — `cortex upgrade --local` writes it"})
    try:
        sync.sync(root, None, None, out, err, announce=False)
    except BaseException:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            _put(path, before)
        err.write(f"note: {config.LOCAL_FILE} is as it was\n")
        raise


def _team_git(agents: str, branch: Optional[str], out: TextIO, err: TextIO) -> Optional[str]:
    """The work tree of the team's folder, for --branch and --commit, which act in the team's
    repository (ADR-013 §3.11) — the branch made before anything is written."""
    top = _work_tree(agents)
    if top is None:
        err.write(f"note: {config.TEAM_FOLDER}/ is in no git work tree — nothing is committed there\n")
        return None
    if branch is not None and _on_branch(agents) != branch:
        if _branch_exists(agents, branch):
            raise UpgradeError(f"the branch {branch} exists already in {display(top)} — nothing was changed. Name "
                               "another one with --branch")
        _git_ok(agents, "checkout", "-q", "-b", branch)
        out.write(f"git: on a new branch in {config.TEAM_FOLDER}/, {branch}\n")
    return top


def team_pin(project: config.Project) -> Optional[Version]:
    """The version the team's file names — what ``--team`` moves, from a project that follows the file or
    one that pins its own: the file is the team's either way (ADR-013 §3.11)."""
    team = project.inputs[2] if project.inputs is not None else None
    named = team.get("version") if isinstance(team, dict) else None
    return Version(str(named)) if named is not None else None


def _no_release(the_store: store.Store) -> UpgradeError:
    """``--team`` with no VERSION from a source checkout or a development build: its own version is no
    release a teammate can download, and it serves none, every release being newer than it."""
    kind = "a development build" if store.embedded_archive() is not None else "a source checkout"
    return UpgradeError(f"this cortex is {kind}, {the_store.own}, which serves no release — the team's pin is moved "
                        "from a released cortex: `cortex upgrade --team [VERSION]` there. Nothing was changed")


def team_target(pinned: Optional[Version], wanted: Optional[str], the_store: store.Store) -> Version:
    """The version ``--team`` moves the team's file to: ``wanted``, or this cortex's own — never down to it,
    nor to a source or development build's, which names no release a teammate can download."""
    if wanted is None and store.development(the_store.own):
        raise _no_release(the_store)
    if wanted is None and pinned is not None and pinned > the_store.own:
        raise UpgradeError(f"{config.TEAM_NAME} pins Cortex {pinned}, newer than this cortex, {the_store.own} — nothing "
                           f"was changed. Update this cortex:\n    {store.update_command(pinned)}")
    target = Version(wanted) if wanted else the_store.own
    if grammar.before_team(str(target)):
        raise UpgradeError(f"{config.TEAM_NAME} reads Cortex {grammar.FIRST_TEAM_VERSION} or later, and {target} is "
                           "before it — nothing was changed")
    _served(the_store, target, f"cortex upgrade --team {target}")
    return target


def _pinned_and_locked(agents: str, pinned: Optional[Version], target: Version, the_store: store.Store,
                       out: TextIO) -> bool:
    """Whether ``--team`` has nothing to move: the target is the team's pin, and its lock names it. The
    lock is checked as sync checks it, by a dry run too: a spec whose files differ from the ones the
    team locked is refused, not locked again."""
    return target == pinned and sync.check_lock(agents, target, the_store, out, name=config.TEAM_LOCK_FILE)[1] is None


def _team_left(agents: str, files: List[Path]) -> List[Path]:
    """Those of the team's ``files`` its work tree sees changed, when ``--team`` writes nothing: what a
    run before this one left — its commit refused, or not asked. Nothing when git cannot tell."""
    try:
        top = _work_tree(agents)
        return _changed(agents, top, files) if top is not None else []
    except UpgradeError:
        return []


def team_move(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, out: TextIO, err: TextIO,
              branch: Optional[str] = None, message: Optional[str] = None, checked: Optional[str] = None) -> Outcome:
    """``cortex upgrade --team [VERSION]`` (ADR-013 §3.11): the team's file's version and its lock, in the
    team's folder; the current project synced, and its untouched team's bootstrap written again, when it
    follows the file. ``--branch`` and ``--commit`` act in the team's repository."""
    root, project = _project(cwd)
    team_file = Path(root) / config.TEAM_FOLDER / config.TEAM_FILE
    if not team_file.is_file():
        raise UpgradeError(f"no {config.TEAM_NAME} — the team creates it, by a commit in its repository. Nothing was "
                           "changed")
    if not project.team_file:
        # Moved to 1.8.0 first, the project reads the team's file, whose version --team then moves.
        raise UpgradeError(f"{config.TEAM_NAME} is not read: this project pins Cortex {project.team_version}, before "
                           f"{grammar.FIRST_TEAM_VERSION} — move it there first: cortex upgrade "
                           f"{grammar.FIRST_TEAM_VERSION}. Nothing was changed")
    the_store = store.Store()
    pinned = team_pin(project)
    target = team_target(pinned, wanted, the_store)
    developer = project.developer_version
    if developer is not None and project.follows:
        after = max(target, Version(str(developer.value)))
        if store.too_old_for(after, the_store.own):
            raise UpgradeError(f"after the move, this project would read Cortex {after}, {developer.origin}'s, newer "
                               f"than this cortex, {the_store.own} — drop the try first: cortex upgrade --local "
                               f"{target}. Nothing was changed")
    out.write(f"Project: {display(root)}\n")
    the_store.ensure(target)
    agents = str(Path(root) / config.TEAM_FOLDER)
    unchanged = _pinned_and_locked(agents, pinned, target, the_store, out)
    said = f"Cortex {target}, pinned and locked already — nothing to move" if unchanged else \
        f"Cortex {target}, pinned already — its lock {'would be' if dry_run else 'is'} written" if target == pinned \
        else f"Cortex {pinned} → {target}" if pinned is not None else f"Cortex {target}, a version added"
    out.write(f"{config.TEAM_NAME}: {said}\n")
    if project.follows and checked is None and not unchanged:
        if _overlays(root, target, the_store.path(target), force, dry_run, out, err):
            return Outcome(root, pinned, target, 1, f"the overlays fail against Cortex {target} — nothing was changed")
    elif not project.follows:
        out.write(f"This project pins its own version, Cortex {project.team_version}, in its committed "
                  f"{config.PROJECT_FILE}: it does not follow {config.TEAM_NAME}, and its overlays are not checked\n")
    team_files = [team_file, team_file.with_name(config.TEAM_LOCK_FILE)]
    # At the pin, its lock missing or stale, the lock alone is written: the file names the version already.
    writes = team_files[1:] if target == pinned else team_files
    if dry_run:
        out.write("--dry-run: nothing of the project was changed\n")
        return Outcome(root, pinned, target, files=[] if unchanged else writes, team=agents,
                       team_files=[] if unchanged else list(writes))
    asked = branch is not None or message is not None
    # At the pin nothing is written: what a run before this one left of the two files — its commit refused,
    # or not asked — --commit commits, as that run would have; otherwise no branch is made, as an upgrade
    # already on its target makes none, and what is left is said.
    left = _team_left(agents, team_files) if unchanged and asked else []
    files = writes if not unchanged else left if message is not None else []
    if unchanged and not files:
        if branch is not None and _on_branch(agents) != branch:
            out.write(f"git: no branch {branch} is made — nothing was written\n")
        if left:
            names = [_shown_path(path, root) for path in left]
            err.write(f"note: nothing was written in {config.TEAM_FOLDER}/, and {_and(names)} "
                      f"{'hold' if len(names) > 1 else 'holds'} changes git has not committed — a run cut short, or "
                      "a commit refused? `git status` there says what is left to commit\n")
    top = _team_git(agents, branch, out, err) if asked and files else None
    before = {} if unchanged else {path: path.read_bytes() if path.is_file() else None for path in files}
    try:
        if not unchanged:
            with sync.project_lock(Path(agents), err):
                if target == pinned:        # checked again, under the folder's lock
                    digest, written = sync.check_lock(agents, target, the_store, out, name=config.TEAM_LOCK_FILE)
                    if written is not None:
                        config.write_lock(agents, target, digest, config.TEAM_LOCK_FILE)
                else:
                    config.write_text(str(team_file), config.render_team(root, {"version": str(target)}))
                    config.write_lock(agents, target, the_store.digest(target), config.TEAM_LOCK_FILE)
        if project.follows:
            sync.sync(root, None, None, out, err, announce=False)
    except BaseException:
        back = _put_back(before, root, err)
        if back:
            names = _and([_shown_path(path, root) for path in back])
            err.write(f"note: {names} {'are as they were' if len(back) > 1 else 'is as it was'}\n")
        raise
    outcome = Outcome(root, pinned, target, files=list(files), team=agents, team_files=list(files))
    outcome.left = [] if files else left
    if project.follows:
        current_path = None
        try:
            the_store.ensure(project.team_version)
            current_path = the_store.path(project.team_version)
        except store.StoreError:
            pass
        rewrites = bootstraps(_instructions_files(Path(root), None), project.team_version, current_path,
                              the_store.path(target), project.team_theme != "none", err)
        for path, content in rewrites:
            _put(path, content)
            out.write(f"{_shown_path(path, root)}: written again from Cortex {target}'s template — left uncommitted, "
                      "this project's\n")
            outcome.files.append(path)
        if developer is not None and Version(str(developer.value)) > target:
            out.write(f"This machine reads Cortex {developer.value}, {developer.origin}'s — the try, above {target}.\n")
    theirs = _team_theirs(agents, team_file, target) if message is not None and top is not None \
        and team_file in files else None
    if theirs is not None:
        outcome.uncommitted = theirs
        err.write(f"note: nothing is committed in {config.TEAM_FOLDER}/ — {theirs}\n")
    elif message is not None and files and top is None:
        outcome.uncommitted = f"{config.TEAM_FOLDER}/ is in no git work tree"
    elif message is not None and top is not None:
        paths = [os.path.relpath(path, agents) for path in files]
        try:
            _git_ok(agents, "add", "--", *paths)
            if _git(agents, "diff", "--cached", "--quiet", "--", *paths).returncode != 0:
                _git_ok(agents, "commit", "-q", "-m", message, "--", *paths)
                outcome.committed = f"{_on_branch(agents) or 'HEAD'}, " \
                    f"{_git_ok(agents, 'rev-parse', '--short', 'HEAD').stdout.strip()}"
                superproject = _git(agents, "rev-parse", "--show-superproject-working-tree").stdout.strip()
                if superproject:
                    err.write(f"note: {config.TEAM_FOLDER}/ is a submodule of {display(superproject)}, which now shows "
                              "it changed — commit it there too\n")
            else:
                outcome.uncommitted = f"{_and([_shown_path(path, root) for path in files])} " \
                    f"{'are' if len(files) > 1 else 'is'} {AS_COMMITTED}"
        except UpgradeError as error:
            outcome.code, outcome.uncommitted = 1, str(error)
            out.flush()
            err.write(f"cortex upgrade: {error} — the upgrade is done, and nothing was committed\n")
    return outcome


def _team_theirs(agents: str, team_file: Path, target: Version) -> Optional[str]:
    """Why ``--team --commit`` is to commit nothing in the team's folder — ``None`` when nothing stops it:
    the team's file holds more than the version ``--team`` writes over the one committed — a developer's
    own change, which would ride in the team's commit, as a plain ``--commit`` refuses (``_theirs``). The
    lock does not count: written whole, it is the same whatever it held. Line endings aside."""
    try:
        proc = _git(agents, "show", f"HEAD:./{config.TEAM_FILE}")
        committed = proc.stdout if proc.returncode == 0 else None       # not committed yet: the version alone
        expected = config.set_keys(committed, {"version": str(target)}, config.TEAM_NAME, "")
        found = team_file.read_bytes().decode("utf-8")
    except (UpgradeError, config.ConfigError, OSError, UnicodeDecodeError) as error:
        return f"it could not be told whether {config.TEAM_NAME} held changes of yours — {error}"
    if found.replace("\r\n", "\n") == expected.replace("\r\n", "\n"):
        return None
    if committed is None:
        return (f"{config.TEAM_NAME} is not committed yet, and holds more than the version --team writes — commit by "
                "hand what is the team's")
    return (f"{config.TEAM_NAME} holds changes of yours besides its version — commit by hand what is the team's")


def _served(the_store: store.Store, target: Version, again: str) -> None:
    """Refuse a target this cortex does not serve: one newer than it, naming ``cortex self-update``,
    then ``again``; one before the first spec archive."""
    if target > the_store.own:
        raise UpgradeError(f"Cortex {target} is newer than this cortex, {the_store.own} — update this cortex first, "
                           f"then run `{again}` again:\n    {store.update_command(target)}")
    the_store.check_served(target)


def _upgrade(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, named: Optional[str],
             out: TextIO, err: TextIO, announce: bool = True,
             prepare: Optional[Callable[[List[Path]], None]] = None) -> Outcome:
    """The upgrade of the project at or above ``cwd`` (ADR-010 §3.5): what it did, or would do.
    ``announce`` off, the project is not named — ``--all`` has named it. ``prepare`` runs once all is
    checked, before anything of the project is written, with the files the team commits that will be
    written — and never when there are none: ``--branch``'s branch. A dry run runs it too, where it
    may refuse, and makes nothing."""
    root = config.find_project(cwd)
    if root is None:
        raise UpgradeError(f"no {config.PROJECT_FILE} in {display(cwd)} or above it — `cortex init` makes a directory "
                           "a Cortex project")
    the_store = store.Store()
    pinned = config.loose_pin(root)
    if wanted is None and pinned is not None and pinned[1] > the_store.own and pinned[0] != config.PROJECT_FILE:
        # A try, or the team's pin, newer than this cortex: said with its own remedy (ADR-013 §3.8) — a
        # source or development build, which the gate lets by, never moves the project down to itself.
        name, newer = pinned
        team = os.path.isfile(os.path.join(root, config.TEAM_FOLDER, config.TEAM_FILE))
        raise UpgradeError(f"this project reads Cortex {newer}, which its {name} pins, newer than this cortex, "
                           f"{the_store.own} — nothing was changed. Name the version to move to: "
                           + ("`cortex upgrade --team VERSION` moves the team's pin, " if team else "")
                           + "`cortex upgrade --local VERSION` the try")
    if wanted is None and pinned is not None and pinned[1] > the_store.own:
        # The default moves a project to this cortex's version, never down: a teammate's older binary
        # would otherwise undo the team's pin. Said from the version read before any key is checked,
        # whatever key of a later version the file holds (ADR-013 §3.8). A source checkout or a
        # development build is refused too, as in 1.7.0: passing, it would move the project down to
        # its own 0.0.0-dev.
        name, newer = pinned
        raise UpgradeError(f"this project uses Cortex {newer}, which its {name} pins, newer than this cortex, "
                           f"{the_store.own} — nothing was changed. Update this cortex to serve it:\n"
                           f"    {store.update_command(newer)}\nor move the project down to this cortex's version by "
                           f"naming it: cortex upgrade {the_store.own}")
    project = config.load(root)
    # The version a plain upgrade moves is cortex.toml's: the team's version, which what the team
    # commits follows — never a developer's try (ADR-013 §3.11).
    current = project.team_version
    if project.follows:
        # It follows the team's file — an uncommitted cortex.toml included, at a 2.B root: a move is the
        # team's, or a try; without VERSION, or with the team's or the version read, it resumes.
        if wanted is not None and Version(wanted) not in (current, project.version):
            raise UpgradeError(f"this project follows {config.TEAM_NAME}, which pins Cortex {current} — moving it "
                               f"is the team's: `cortex upgrade --team {wanted}`; trying it here: `cortex upgrade "
                               f"--local {wanted}`. Nothing was changed")
        wanted = None
        target = current
    else:
        target = Version(wanted) if wanted else the_store.own
    _served(the_store, target, f"cortex upgrade {target}")
    developer = project.developer_version
    if developer is not None and target != current:
        after = max(target, Version(str(developer.value)))
        if store.too_old_for(after, the_store.own):
            raise UpgradeError(f"after the move, this project would read Cortex {after}, {developer.origin}'s, newer "
                               f"than this cortex, {the_store.own} — drop the try first: cortex upgrade --local "
                               f"{target}. Nothing was changed")
    if target != current and grammar.before_team(str(target)):
        uses = [what for what, used in ((config.TEAM_NAME, project.team_file),
                                        (f"{config.LOCAL_FILE}'s version", developer is not None
                                         and developer.origin == config.LOCAL_FILE),
                                        (f"a {config.PROJECT_FILE} without version or theme", project.theme is None))
                if used]
        if uses:
            raise UpgradeError(f"Cortex {target} is before {grammar.FIRST_TEAM_VERSION}, which this project needs for "
                               f"{_and(uses)} — nothing was changed")
    if announce:
        out.write(f"Project: {display(root)}\n")
    said = tuple(sync.version_notes(project))
    for note in said:
        err.write(f"note: {note}\n")
    files = _instructions_files(Path(root), named)
    personality = project.team_theme != "none"

    if target == current:
        out.write(f"Cortex {current}: already on it — nothing to move\n")
        return _resume(root, project, the_store, files, personality, dry_run, out, err, prepare, said)

    the_store.ensure(target)
    target_path = the_store.path(target)
    direction = "an upgrade" if target > current else "a downgrade"
    out.write(f"Cortex {current} → {target}: {direction} — the release notes: {store.releases_url()}/tag/{target}\n")
    current_path: Optional[Path] = None
    if current > the_store.own:
        # The move down the refusal names: this cortex has not the version to compare with.
        err.write(f"note: what changes is not said — this cortex, {the_store.own}, does not serve Cortex {current}\n")
    else:
        try:
            the_store.ensure(current)
            say_changes(changes(the_store.path(current), target_path), out)
            current_path = the_store.path(current)
        except store.StoreError as error:
            err.write(f"note: what changes is not said — Cortex {current} is not in the store: {error}\n")

    code, report = check_overlays(root, target_path)
    stops = code != 0 and not force
    if code != 0:
        out.write(f"\nThe project's overlays, checked against Cortex {target}:\n{report}")
        if stops and not dry_run:
            err.write(f"cortex upgrade: the overlays would fail against Cortex {target} — nothing was changed. "
                      "Fix them, or --force moves the project anyway\n")
            return Outcome(root, current, target, 1, f"the overlays fail against Cortex {target} — nothing was changed")
        if not stops:
            err.write("warning: the overlays fail against the target, and --force moves the project anyway\n")
    else:
        out.write(f"The project's overlays pass against Cortex {target}.\n")
    # What sync refuses at cortex/, refused before anything moves — and said by the dry run, as the run fails.
    try:
        sync.existing_entry(Path(root) / sync.LINK, the_store, project)
        refused = None
    except sync.SyncError as error:
        if not dry_run:
            raise
        refused = str(error)

    rewrites = bootstraps(files, current, current_path, target_path, personality, err)
    team = [Path(root) / config.PROJECT_FILE, Path(root) / config.LOCK_FILE, *(path for path, _ in rewrites)]
    if prepare is not None and not stops and refused is None:
        prepare(team)

    if dry_run:
        for path, content in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {target}'s template — {_nobody(content)}\n")
        if stops:
            err.write(f"cortex upgrade: the overlays would fail against Cortex {target}, and stop it — fix them, "
                      "or --force moves the project anyway\n")
        if refused is not None:
            err.write(f"cortex upgrade: the sync would refuse it — {refused}\n")
        out.write("--dry-run: nothing of the project was changed\n")
        why = f"the overlays fail against Cortex {target}" if stops else \
            f"the sync would refuse it — {refused}" if refused is not None else None
        return Outcome(root, current, target, 0 if why is None else 1, why, team)

    # The pin, the lock and the bootstrap files are what the team commits: a run that fails leaves
    # them as they were. What the sync changed on this machine — cortex.local.toml, cortex/ — the next
    # `cortex sync` puts right.
    committed = {Path(root) / name: (Path(root) / name).read_bytes() if (Path(root) / name).is_file() else None
                 for name in (config.PROJECT_FILE, config.LOCK_FILE)}
    machine = _machine(root)

    def switch() -> None:
        config.write_text(os.path.join(root, config.PROJECT_FILE), config.render_project(root, {"version": str(target)}))
        if developer is not None and Version(str(developer.value)) > target:
            # During a try above it, the sync reads the try and leaves the committed lock alone: the lock
            # of the version moved is written here, from the store (ADR-013 §3.4). Otherwise the sync
            # writes it, and says so, as before.
            config.write_lock(root, target, the_store.digest(target))
            out.write(f"{config.LOCK_FILE}: Cortex {target}{'' if sync.in_no_repository(root) else ' — commit it'}\n")
        sync.sync(root, None, None, out, err, announce=False, said=said)

    _write(rewrites, committed, root, err, switch, lambda: _machine(root) != machine)
    for path, content in rewrites:
        out.write(f"{_shown_path(path, root)}: written again from Cortex {target}'s template — {_nobody(content)}\n")
    if developer is not None and Version(str(developer.value)) > target:
        out.write(f"This machine reads Cortex {developer.value}, {developer.origin}'s — the try, above {target}.\n")
    return Outcome(root, current, target, files=team)


def _resume(root: str, project: config.Project, the_store: store.Store, files: List[Path], personality: bool,
            dry_run: bool, out: TextIO, err: TextIO,
            prepare: Optional[Callable[[List[Path]], None]] = None, said: Tuple[str, ...] = ()) -> Outcome:
    """On the pinned version, finish what an upgrade cut short, or a pin moved by hand, left: a
    bootstrap file nobody changed, of another version, written again; this machine and the lock,
    when they are not on the pin, synced (ADR-010 §3.5, step 1). During a try, the lock of the team's
    version, which the sync leaves alone, is written from the store when it does not name it (ADR-013
    §3.4)."""
    current = project.team_version         # what the committed bootstrap follows (ADR-013 §3.10)
    behind = out_of_step(Path(root), project, the_store)
    stale = try_lock(project)
    rewrites: List[Tuple[Path, bytes]] = []
    if files:
        try:
            the_store.ensure(current)
        except store.StoreError as error:
            err.write(f"note: the bootstrap files were not checked — Cortex {current} is not in the store: {error}\n")
        else:
            rewrites = bootstraps(files, current, the_store.path(current), the_store.path(current), personality, err)
    if behind is not None:
        out.write(f"{behind}, and {project.version_origin} pins Cortex {project.version} — an upgrade cut short, or a "
                  f"pin moved by hand: {'the sync would put them on it' if dry_run else 'syncing'}\n")
    # A team's folder this user cannot write: the lock is a warning, not written (``_write_try_lock``) — no branch
    # is made for it, and the dry run says so. A prediction: on Windows, os.access does not read a folder's ACL.
    unwritable = stale is not None and stale[0][2] == config.TEAM_LOCK_NAME and not os.access(stale[0][0], os.W_OK)
    if stale is not None:
        would = "it would be written" + ("" if the_store.has(current) else f", once Cortex {current} is downloaded")
        if unwritable:
            would = f"{config.TEAM_FOLDER}/ cannot be written here — it would be a warning, the lock not updated"
        out.write(f"{stale[1]}, and {project.team_origin} pins Cortex {current} — the sync reads the try and leaves "
                  f"it to `cortex upgrade`: {would if dry_run else 'writing it'}\n")
    where = config.lock_of(project)
    lock = Path(where[0]) / where[1] if where is not None else Path(root) / config.LOCK_FILE
    relock = behind is not None and where is not None and \
        getattr(config.load_lock(where[0], where[1]), "version", None) != project.version
    team_lock = Path(stale[0][0]) / stale[0][1] if stale is not None else None
    # The try's lock may be the project's own `cortex.lock` — a committed pin of its own, read beside a
    # try: it is left out only when the relock names it already.
    locks = ([lock] if relock else [])
    locks += [team_lock] if team_lock is not None and not unwritable and team_lock not in locks else []
    team = locks + [path for path, _ in rewrites]
    # What the sync refuses at cortex/, refused before anything is written — as the upgrade does it — and said by
    # the dry run, as the run fails: a refusal half-way would write a lock, say it, then put it back.
    refused = None
    if behind is not None:
        try:
            sync.existing_entry(Path(root) / sync.LINK, the_store, project)
        except sync.SyncError as error:
            if not dry_run:
                raise
            refused = str(error)
    if prepare is not None and team and refused is None:
        prepare(team)
    if dry_run:
        for path, content in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {current}'s template — "
                      f"{_nobody(content)}\n")
        if refused is not None:
            err.write(f"cortex upgrade: the sync would refuse it — {refused}\n")
        if rewrites or behind is not None or stale is not None:
            out.write("--dry-run: nothing of the project was changed\n")
        why = None if refused is None else f"the sync would refuse it — {refused}"
        return Outcome(root, current, current, 0 if why is None else 1, why, team)
    if not rewrites and behind is None and stale is None:
        return Outcome(root, current, current)
    kept = {path: path.read_bytes() if path.is_file() else None
            for path in ([lock] if behind is not None else []) + ([team_lock] if team_lock is not None else [])}

    machine = _machine(root)

    def then() -> None:
        if stale is not None:
            the_store.ensure(current)
            _write_try_lock(stale[0], current, the_store, out, err)
        if behind is not None:
            sync.sync(root, None, None, out, err, announce=False, said=said)

    _write(rewrites, kept, root, err, then, lambda: _machine(root) != machine)
    for path, content in rewrites:
        out.write(f"{_shown_path(path, root)}: written again from Cortex {current}'s template — {_nobody(content)}\n")
    relocked = [path for path, before in kept.items() if path.is_file() and path.read_bytes() != before]
    return Outcome(root, current, current, files=relocked + [path for path, _ in rewrites])


def _machine(root: str) -> Tuple[Optional[bytes], Optional[str]]:
    """What of this machine a sync moves: ``cortex.local.toml``, and where ``cortex/`` leads — a link's target, or
    the marker of a copy. Two readings that differ say the sync moved this machine."""
    local, link = Path(root) / config.LOCAL_FILE, Path(root) / sync.LINK
    try:
        text = local.read_bytes() if local.is_file() else None
    except OSError:
        text = None
    try:
        lead = os.readlink(link) if os.path.islink(link) else repr(sync.read_marker(link)) if link.is_dir() else None
    except OSError:
        lead = None
    return text, lead


def _write(rewrites: List[Tuple[Path, bytes]], committed: Dict[Path, Optional[bytes]], root: str, err: TextIO,
           then: Callable[[], None], synced: Callable[[], bool] = lambda: True) -> None:
    """Write the bootstrap files again, then run ``then`` — the switch: anything that fails puts back
    the files, and ``committed``, as they were. The bootstrap files go first: a run cut short after
    them leaves files whose digest the next `cortex upgrade` reads as nobody's change — and the
    pinned version it finds then, it finishes. A file still as it was is not written back; one that
    cannot be is said, and the others are put back all the same. ``synced`` tells whether the sync
    moved this machine — ``_machine`` read again differs: only then does `cortex sync` put it back."""
    before = {**committed, **{path: path.read_bytes() for path, _ in rewrites}}
    try:
        for path, content in rewrites:
            _put(path, content)
        then()
    except BaseException:
        back = _put_back(before, root, err)
        if back:
            names = _and([_shown_path(path, root) for path in back])
            err.write(f"note: {names} {'are as they were' if len(back) > 1 else 'is as it was'}"
                      f"{' — `cortex sync` puts this machine back on them' if committed and synced() else ''}\n")
        raise


def _put_back(before: Dict[Path, Optional[bytes]], root: str, err: TextIO) -> List[Path]:
    """Put each file of ``before`` back as it was — removed, where it was none — and return those that are:
    a file still as it was is not written again; one that cannot be put back is said, and the others are
    all the same. Never raises: it runs as a failure is on its way out."""
    back = []
    for path, content in before.items():
        try:
            now = path.read_bytes() if path.is_file() else None
            if now != content:
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _put(path, content)
            back.append(path)
        except OSError as error:
            err.write(f"warning: {_shown_path(path, root)} — {sync.os_error(error)}: not put back as it was\n")
    return back


# --------------------------------------------------------------------------- #
# git: --branch and --commit (ADR-011 §3.5)
# --------------------------------------------------------------------------- #

def _git(root: str, *args: str, timeout: Optional[float] = 30) -> subprocess.CompletedProcess:
    """``git -C root ARGS``, its answer whatever its exit code. ``UpgradeError`` when git cannot be run."""
    git = shutil.which("git")
    if git is None:
        raise UpgradeError("git is not on PATH")
    try:
        return subprocess.run([git, "-C", root, *args], capture_output=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout, env=sync.git_environment())
    except (OSError, subprocess.SubprocessError) as error:
        raise UpgradeError(f"git {args[0]} could not run — {error}")


def _literal(paths: List[str]) -> List[str]:
    """``paths`` as pathspecs git takes as they are written, never as patterns."""
    return [f":(literal){path}" for path in paths]


def _failed(proc: subprocess.CompletedProcess) -> UpgradeError:
    """A git that failed, with what it said."""
    return UpgradeError(f"git {proc.args[3]} failed — {(proc.stderr or proc.stdout).strip() or proc.returncode}")


def _git_ok(root: str, *args: str, timeout: Optional[float] = 30) -> subprocess.CompletedProcess:
    """``_git``, refused when git fails."""
    proc = _git(root, *args, timeout=timeout)
    if proc.returncode != 0:
        raise _failed(proc)
    return proc


def _work_tree(root: str) -> Optional[str]:
    """The top of the git work tree ``root`` is in — ``None`` when there is none: no ``.git`` at or
    above it. One that git refuses — a repository of another owner, say — is refused."""
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    if not sync.in_no_repository(root):
        raise _failed(proc)
    return None


def _branch_exists(root: str, name: str) -> bool:
    return _git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0


def _branch_name(name: str) -> bool:
    """Whether git takes ``name`` for a new branch, as it is written: ``@{-1}`` and its like, which
    ``check-ref-format --branch`` expands to a branch that exists, are refused."""
    cwd = working_directory()
    return (_git(cwd, "check-ref-format", "--branch", name).returncode == 0
            and _git(cwd, "check-ref-format", f"refs/heads/{name}").returncode == 0)


def _on_branch(root: str) -> Optional[str]:
    """The branch checked out in the work tree of ``root`` — ``None`` on a detached HEAD."""
    return _git(root, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or None


def _repository(root: str) -> str:
    """The repository of the work tree of ``root``: its linked work trees share it, and its branches."""
    found = _git_ok(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return os.path.normcase(os.path.realpath(found))


def _real(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _within(top: str, path: Path) -> Optional[str]:
    """Where ``path`` — a link, by where it points — is in the work tree whose top is ``top``, from
    that top; ``None`` when it is elsewhere: a bootstrap linked to a shared file, or named by
    --instructions-file, may be. The top is found among its directories by what it is, not by how
    it is spelt: git spells the top as the disk does, and on a disk that ignores case — macOS's,
    Windows' — the project's path may be spelt otherwise, and ``realpath`` keeps that spelling."""
    here, below = os.path.realpath(path), []
    while True:
        try:
            if os.path.samefile(here, top):
                return os.path.join(*reversed(below)) if below else ""
        except OSError:
            pass                        # not there: a file git sees deleted, say
        parent = os.path.dirname(here)
        if parent == here:
            return None
        below.append(os.path.basename(here))
        here = parent


def _other_tree(top: str, root: str, path: Path) -> bool:
    """Whether ``path``, the team's file or its lock, is in a work tree of its own inside ``top`` — the
    team's folder a repository or a submodule, whose files are committed there (ADR-013 §3.11). Git
    says which work tree, as it would commit the file. When it cannot — a repository of another owner,
    a submodule whose repository is gone — the work tree of ``top`` says: a file it neither tracks nor
    sees is another repository's. Any other file is left to the commit, as in 1.7.0."""
    folder = Path(os.path.abspath(root), config.TEAM_FOLDER)
    if Path(os.path.abspath(path)) not in (folder / config.TEAM_FILE, folder / config.TEAM_LOCK_FILE):
        return False
    proc = _git(os.path.dirname(os.path.realpath(path)), "rev-parse", "--show-toplevel")
    found = proc.stdout.strip()
    if proc.returncode == 0 and found:
        try:
            return not os.path.samefile(found, top)
        except OSError:
            return False
    name = _literal(_git_paths(top, [path]))
    if _git(top, "ls-files", "--error-unmatch", "--", *name).returncode == 0:
        return False
    return not _git(top, "status", "--porcelain", "--untracked-files=all", "--", *name).stdout.strip()


def _inside(top: str, path: Path) -> bool:
    """Whether ``path`` — a link, by where it points — is in the work tree whose top is ``top``."""
    return _within(top, path) is not None


def _git_paths(root: str, files: List[Path]) -> List[str]:
    """``files`` as git names them in the work tree of ``root`` — a link, by where it points."""
    real = os.path.realpath(root)
    return [display(os.path.relpath(os.path.realpath(path), real)) for path in files]


def _ignored(root: str, files: List[Path]) -> List[str]:
    """Those of ``files`` git ignores, as git names them."""
    return [path for path in _git_paths(root, files) if _git(root, "check-ignore", "-q", "--", path).returncode == 0]


def _changed(root: str, top: str, files: List[Path]) -> List[Path]:
    """Those of ``files`` git sees changed in the work tree of ``root``, whose top is ``top``: staged,
    not staged, or untracked."""
    proc = _git_ok(root, "status", "--porcelain", "-z", "--untracked-files=all", "--",
                   *_literal(_git_paths(root, files)), timeout=None)
    seen, fields = [], iter(proc.stdout.split("\0"))
    for field in fields:                # "XY path", from the top, and after a rename or a copy, the path it was
        if len(field) > 3:
            seen.append(field[3:])
            if "R" in field[:2] or "C" in field[:2]:
                seen.append(next(fields, ""))
    return [path for path in files if any(_same(top, path, name) for name in seen)]


def _same(top: str, path: Path, name: str) -> bool:
    """Whether ``name``, a path git gives from the top of the work tree, is ``path``: the same file
    on disk, however each is spelt — git spells the directories below the top as the disk does, and
    the project's path may spell them otherwise. By the text when one of them is not there, a file
    git sees deleted."""
    try:
        return os.path.samefile(path, os.path.join(top, name))
    except OSError:
        return _from_top(_within(top, path)) == _from_top(name)


def _from_top(path: Optional[str]) -> Optional[str]:
    """A path from the top of a work tree, as two of them compare."""
    return None if path is None else os.path.normcase(os.path.normpath(path))


def _before(root: str, top: str, files: List[Path]) -> Tuple[List[Path], Optional[str]]:
    """Those of ``files`` git sees changed — staged or not, or untracked — in the work tree of ``root``,
    whose top is ``top``, read before the upgrade writes anything; and why git could not tell, when it
    could not. A file outside the work tree is not read."""
    inside = [path for path in files if _inside(top, path)]
    if not inside:
        return [], None
    try:
        return _changed(root, top, inside), None
    except UpgradeError as error:
        return [], str(error)


def _theirs(root: str, pin: Path, before: List[Path], error: Optional[str], written: List[Path]) -> Optional[str]:
    """Why ``--commit`` is to commit nothing in the project at ``root`` — ``None`` when nothing stops
    it — from what ``_before`` read. A change git saw is the developer's where it could ride in the
    team's commit: in the pin, which the upgrade edits and keeps the rest of, and in a bootstrap file
    it wrote, which it writes again only where no one changed it — a change, a template restored by
    ``cortex init --force`` say, decided it. The lock does not count: written whole from the pin, it
    is the same whatever it held. A git that cannot tell stops the commit, never the upgrade."""
    if error is not None:
        return f"git could not tell whether {config.PROJECT_FILE} or a bootstrap file held changes of yours — {error}"
    wrote = {_real(path) for path in written}
    theirs = [path for path in before if _real(path) == _real(pin) or _real(path) in wrote]
    if not theirs:
        return None
    return (f"{_and([_shown_path(path, root) for path in theirs])} held changes of yours before the upgrade — "
            "commit by hand what is the team's")


AS_COMMITTED = "as committed already"      # why --commit committed nothing, when there was nothing to


def _commit(root: str, top: str, files: List[Path], message: str,
            theirs: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Commit ``files`` in the work tree of ``root``, whose top is ``top``, with ``message``, and
    nothing else: a change of another file, staged or not, stays as it is. Where it was committed —
    the branch, and the commit — or why nothing was: git tracks no ``cortex.toml`` there — the pin is
    this developer's own (ADR-010 §3.8); git ignores one of the files; they are as committed; or
    ``theirs``, what ``_theirs`` found. ``UpgradeError`` when git fails, or refuses
    the commit — the files stay staged then."""
    paths = _git_paths(root, files)
    if _git(root, "ls-files", "--error-unmatch", "--", *_literal([config.PROJECT_FILE])).returncode != 0:
        return None, f"git does not track its {config.PROJECT_FILE} — the pin is this developer's own"
    ignored = _ignored(root, files)
    if ignored:
        return None, f"git ignores {_and(ignored)}"
    if not _changed(root, top, files):
        return None, f"{_and(paths)} {'are' if len(paths) > 1 else 'is'} {AS_COMMITTED}"
    if theirs is not None:
        return None, theirs
    _git_ok(root, "add", "--", *_literal(paths), timeout=None)
    if _git(root, "diff", "--cached", "--quiet", "--", *_literal(paths)).returncode == 0:
        return None, f"{_and(paths)} {'are' if len(paths) > 1 else 'is'} {AS_COMMITTED}"
    proc = _git(root, "commit", "-q", "-m", message, "--", *_literal(paths), timeout=None)      # its hooks run
    if proc.returncode != 0:
        raise UpgradeError(f"git refused the commit — {(proc.stderr or proc.stdout).strip() or proc.returncode}\n"
                           f"{_and(paths)} {'stay' if len(paths) > 1 else 'stays'} staged, ready to commit by hand")
    commit = _git_ok(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"{_on_branch(root) or 'a detached HEAD'}, {commit}", None


def _where(path: Path, root: str) -> str:
    """A file written outside the git work tree of ``root``, as it is named: where it is — and, for
    a link, the link that leads there."""
    real = display(os.path.realpath(path))
    return f"{real} (where {_shown_path(path, root)} leads)" if os.path.islink(path) else real


def _left(root: str, top: str, named: Optional[str]) -> List[Path]:
    """The files of the team — the pin, the lock, the bootstrap files — that git sees changed in the
    work tree of ``root``, whose top is ``top``, when the upgrade wrote nothing: what a run before
    this one may have left to commit. Said, never committed; nothing when git cannot tell."""
    root_path = Path(root)
    files = [root_path / config.PROJECT_FILE, root_path / config.LOCK_FILE, *_instructions_files(root_path, named)]
    try:
        return _changed(root, top, [path for path in files if _inside(top, path)])
    except UpgradeError:
        return []


class Interrupted(KeyboardInterrupt):
    """Ctrl-C during the upgrade of a project that ``--branch`` made a branch for."""

    def __init__(self, branch: str):
        super().__init__(branch)
        self.branch = branch


def _move(cwd: str, options: _Options, out: TextIO, err: TextIO, announce: bool = True,
          made: Optional[Dict[str, Tuple[str, str]]] = None) -> Outcome:
    """The upgrade of the project of ``cwd``, and what ``--branch`` and ``--commit`` ask of git around
    it (``_git_move``). Interrupted once its branch is made, it says so, and raises ``Interrupted``."""
    branched: List[str] = []
    try:
        return _git_move(cwd, options, out, err, announce, {} if made is None else made, branched)
    except KeyboardInterrupt as error:
        if not branched:
            raise
        out.flush()
        err.write(f"\nnote: interrupted — the branch {options.branch} was made for it, and is checked out in "
                  f"{display(branched[0])}: `git status` there says what was written\n")
        raise Interrupted(options.branch) from error


def _git_move(cwd: str, options: _Options, out: TextIO, err: TextIO, announce: bool,
              made: Dict[str, Tuple[str, str]], branched: List[str]) -> Outcome:
    """The upgrade, with the branch made once all is checked and before anything is written, where
    there is something to commit; the files the upgrade wrote committed. ``made``: the repositories
    this run made the branch in, and the work tree it is checked out in — resolved, to compare, and
    as git names it, to say — a second project of that work tree finds it checked out, one of
    another work tree of the repository is refused. ``branched`` gets the work tree this project's
    branch was made in."""
    root = config.find_project(cwd)
    top = None
    if root is not None and (options.branch is not None or options.message is not None):
        top = _work_tree(root)
        if top is None:
            what = (["no branch is made"] if options.branch is not None else []) + \
                (["nothing is committed"] if options.message is not None else [])
            err.write(f"note: {display(root)} is in no git work tree — {' and '.join(what)} there\n")
    branching = options.branch is not None and top is not None
    pin = Path(root) / config.PROJECT_FILE if root is not None else None
    before: Tuple[List[Path], Optional[str]] = ([], None)
    if options.message is not None and top is not None and not options.dry_run:
        # Before anything is written: the pin and the bootstrap files, never the lock (``_theirs``).
        before = _before(root, top, [pin, *_instructions_files(Path(root), options.named)])
    asked = []

    def branch(files: List[Path]) -> None:
        asked.append(True)
        ignored = _ignored(root, files)
        if len(ignored) == len(files):
            if not options.dry_run:
                out.write(f"git: no branch {options.branch} is made — git ignores {_and(ignored)}\n")
            return
        if _on_branch(root) == options.branch:
            if not options.dry_run:
                out.write(f"git: on the branch {options.branch} already\n")
            return
        key, repository = _real(top), _repository(root)
        if made.get(repository, (key, top))[0] != key:
            dry = options.dry_run
            raise UpgradeError(f"the branch {options.branch} {'would be' if dry else 'is'} checked out in "
                               f"{display(made[repository][1])}, where this run {'would make' if dry else 'made'} it, "
                               "and git checks a branch out in one work tree at a time — nothing was changed. "
                               f"`cortex upgrade --branch NAME` in {display(root)} moves it on another branch")
        if _branch_exists(root, options.branch):
            raise UpgradeError(f"the branch {options.branch} exists already in {display(top)} — nothing was changed. "
                               "Name another one with --branch")
        if options.dry_run:
            made[repository] = (key, top)       # as the run would: a project of another work tree is refused
            return
        _git_ok(root, "checkout", "-q", "-b", options.branch)
        made[repository] = (key, top)
        branched.append(top)
        out.write(f"git: on a new branch, {options.branch}\n")

    try:
        outcome = _upgrade(cwd, options.wanted, options.dry_run, options.force, options.named, out, err, announce,
                           branch if branching else None)
    except REFUSALS as error:
        if not branched:
            raise
        raise UpgradeError(f"{_refusal(error)} — the branch {options.branch} was made for it, and is checked out") \
            from error
    if branching and not asked and not options.dry_run and outcome.why is None \
            and _on_branch(root) != options.branch:
        out.write(f"git: no branch {options.branch} is made — nothing was written\n")
    if top is not None and outcome.why is None and outcome.files:
        outcome.ignored = len(_ignored(root, outcome.files)) == len(outcome.files)
    if outcome.why is None and outcome.files and top is None and sync.in_no_repository(outcome.root):
        # Committed nowhere (ADR-008 §9) — but a file linked into a repository, the team's say, is to commit there.
        outcome.outside = [path for path in outcome.files
                           if not sync.in_no_repository(os.path.dirname(os.path.realpath(path)))]
        outcome.nowhere = bool(outcome.names())
    if options.message is None or options.dry_run or outcome.why is not None:
        return outcome
    if top is None:
        if outcome.files:
            outcome.uncommitted = "in no git work tree"
        return outcome
    if not outcome.files:
        outcome.left = _left(root, top, options.named)
        if outcome.left:
            names = [_shown_path(path, root) for path in outcome.left]
            err.write(f"note: nothing was written in {display(root)}, and {_and(names)} "
                      f"{'hold' if len(names) > 1 else 'holds'} changes git has not committed — a run cut short, or "
                      "a commit refused? `git status` there says what is left to commit\n")
        return outcome
    if outcome.ignored:
        err.write(f"note: nothing to commit in {display(root)} — git ignores {_and(outcome.names())}\n")
        return outcome
    # A file of another work tree inside this one — the team's folder, a submodule — is that repository's.
    outcome.outside = [path for path in outcome.files if not _inside(top, path) or _other_tree(top, root, path)]
    if outcome.outside:
        names = [_where(path, root) for path in outcome.outside]
        them = "them where they are" if len(names) > 1 else "it where it is"
        err.write(f"note: {_and(names)} {'are' if len(names) > 1 else 'is'} outside the git work tree of "
                  f"{display(root)} — not committed with it: commit {them}\n")
    inside = [path for path in outcome.files if path not in outcome.outside]
    if not inside:
        return outcome
    try:
        outcome.committed, outcome.uncommitted = _commit(root, top, inside, options.message,
                                                         _theirs(root, pin, *before, inside))
    except UpgradeError as error:
        outcome.code, outcome.uncommitted = 1, str(error)
        out.flush()
        err.write(f"cortex upgrade: {error} — the upgrade is done, and nothing was committed\n")
    else:
        if outcome.uncommitted is not None:
            outcome.clean = outcome.uncommitted.endswith(AS_COMMITTED)
            err.write(f"note: nothing is committed in {display(root)} — {outcome.uncommitted}\n")
        else:
            superproject = _git(root, "rev-parse", "--show-superproject-working-tree").stdout.strip()
            if superproject:
                err.write(f"note: {display(top)} is a submodule of {display(superproject)}, which now shows it changed "
                          "— commit it there too\n")
    return outcome


# --------------------------------------------------------------------------- #
# --all: every project of the registry (ADR-011 §3.5)
# --------------------------------------------------------------------------- #

def _first(why: str) -> str:
    """The first line of a reason: a refusal may go on with the commands to run."""
    return why.splitlines()[0].rstrip(":") if why else why


def upgrade_all(options: _Options, out: TextIO, err: TextIO) -> int:
    """``cortex upgrade --all``: each registered project, in name order, to the same target. One that
    is not ``ok`` is skipped; one that refuses stops none of the others, and the command exits 1. It
    ends with a line per project, and what each must commit. The release is asked for once.
    Interrupted, it ends so all the same, the projects not reached said, and exits 130."""
    entries = registry.load()
    if not entries:
        out.write(f"Nothing to move. {registry.NONE}\n")
        return 0
    the_store = store.Store()
    target = Version(options.wanted) if options.wanted else the_store.own
    if options.wanted:
        _served(the_store, target, f"cortex upgrade --all {target}")       # every project would refuse it
    elif options.which == "team" and store.development(the_store.own) and _followers(entries):
        raise _no_release(the_store)                                        # every team's file would refuse it
    newer = _asking(options.wanted)
    done: List[Tuple[registry.Entry, Optional[Outcome], Optional[str]]] = []
    try:
        _each(entries, options, out, err, done)
    except KeyboardInterrupt:
        if newer is not None:       # a thread left in a request as Python exits can crash it
            newer.join(self_update.NOTICE_TIMEOUT)
        done += [(entry, None, "not reached") for entry in entries[len(done):]]
        out.flush()
        _summary(done, target, options, out)
        err.write("cortex upgrade: interrupted — `cortex upgrade --all` again finishes the project cut short, and "
                  "moves those not reached; what the one cut short has left to commit, `git status` there says\n")
        return 130
    except BaseException:
        if newer is not None:
            newer.join(self_update.NOTICE_TIMEOUT)
        raise
    _summary(done, target, options, out)
    if newer is not None:
        newer.say(out, every=True)
    return 1 if any(outcome is not None and outcome.code for _, outcome, _ in done) else 0


def _each(entries: List[registry.Entry], options: _Options, out: TextIO, err: TextIO,
          done: List[Tuple[registry.Entry, Optional[Outcome], Optional[str]]]) -> None:
    """Each project's upgrade, in turn, added to ``done``: what it did — or, when it did nothing, why:
    the state it was skipped for, or its being interrupted."""
    made: Dict[str, Tuple[str, str]] = {}
    moved: Dict[str, Optional[str]] = {}    # --all --team: each team's folder, once — None moved, or why refused
    targets: Dict[str, Version] = {}        # --all --team: the version each team's folder moved to, or would
    followers = _followers(entries) if options.which == "team" else []
    for entry in entries:
        out.write(("\n" if done else "") + f"{entry.name}: {entry.path}\n")
        try:
            found = registry.state(entry)
            if found.state != registry.OK:
                why = {registry.MISSING: "no directory there",
                       registry.NO_PROJECT: f"no {config.PROJECT_FILE} there"}.get(found.state, found.why)
                out.write(f"skipped: {found.state} — {why}\n")
                done.append((entry, None, f"skipped: {found.state}"))
                continue
            outcome = _each_move(entry, options, out, err, made, moved, followers, targets)
            if isinstance(outcome, str):
                out.write(f"skipped: {outcome}\n")
                done.append((entry, None, f"skipped: {outcome}"))
                continue
        except REFUSALS as error:
            outcome = Outcome(entry.path, None, None, 1, _refusal(error))
            out.flush()
            err.write(f"cortex upgrade: {outcome.why}\n")
        except KeyboardInterrupt as error:
            branch = getattr(error, "branch", None)
            made_for = f" on the branch {branch}, made for it" if branch else ""
            done.append((entry, None, f"interrupted{made_for} — it may be half written: `git status` there says "
                                      "what was written"))
            raise
        done.append((entry, outcome, None))
        out.flush()


def folder_of(root: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.join(root, config.TEAM_FOLDER)))


def _followers(entries: List[registry.Entry]) -> List[Tuple[str, str]]:
    """``(path, team's folder)`` of each registered project that follows its team's file."""
    found = []
    for entry in entries:
        try:
            project = config.load(entry.path)
        except (config.ConfigError, OSError):
            continue
        if project.follows:
            found.append((entry.path, folder_of(entry.path)))
    return found


def _each_move(entry: registry.Entry, options: _Options, out: TextIO, err: TextIO,
               made: Dict[str, Tuple[str, str]], moved: Dict[str, Optional[str]],
               followers: List[Tuple[str, str]] = (), targets: Optional[Dict[str, Version]] = None):
    """One project of ``--all``: its outcome, or why it is skipped (ADR-013 §3.11) — a project that
    follows its team's file, a developer's ``cortex.toml`` included, which ``--all`` does not move;
    under ``--team``, one with no team's file, or that does not follow it. ``--all --team`` moves each
    team's folder once, and runs step 5 in each follower — ``targets``, each folder's target."""
    targets = {} if targets is None else targets
    project = config.load(entry.path)
    if options.which != "team":
        if project.follows:
            return f"follows the team's {project.team_version}"
        return _move(entry.path, options, out, err, announce=False, made=made)
    if not project.follows:
        return "no team's file to move" if not project.team_file else \
            f"pins its own version, {project.team_version}, and does not follow its team's file"
    folder = folder_of(entry.path)
    if folder in moved:
        why = moved[folder]
        if why is not None:                 # refused once for every follower: never tried again
            return Outcome(entry.path, project.team_version, None, 1, why)
        target = targets.get(folder)
        if options.dry_run and target is not None and target != project.team_version:
            # The dry run moved nothing: this follower would follow the file to its target, which it cannot resume.
            out.write(f"{config.TEAM_NAME}: Cortex {project.team_version} → {target}, moved once, above — the sync "
                      "would put this project on it\n--dry-run: nothing of the project was changed\n")
            return Outcome(entry.path, project.team_version, target)
        outcome = _move(entry.path, _Options(None, options.dry_run, options.force), out, err, announce=False, made=made)
        outcome.team = os.path.join(entry.path, config.TEAM_FOLDER)     # its files are its own: --team commits none
        return outcome
    # Every follower of this folder, checked against the target team_move will use, before it moves.
    the_store = store.Store()
    try:
        target = team_target(team_pin(project), options.wanted, the_store)
    except UpgradeError as error:
        moved[folder] = str(error)
        out.write(f"refused: {error}\n")
        return Outcome(entry.path, project.team_version, None, 1, str(error))
    the_store.ensure(target)
    failing = []
    # At the pin, with its lock, nothing moves: no overlay is checked, as team_move checks none.
    pinned_and_locked = _pinned_and_locked(os.path.join(entry.path, config.TEAM_FOLDER), team_pin(project), target,
                                           the_store, out)
    for other in [path for path, shared in followers if shared == folder and not pinned_and_locked]:
        code, report = check_overlays(other, the_store.path(target))
        if code != 0:
            failing.append(other)
            out.write(f"The overlays of {display(other)}, checked against Cortex {target}:\n{report}")
    if failing and not options.force:
        why = (f"the overlays of {_and([display(path) for path in failing])} fail against Cortex {target} — "
               f"{config.TEAM_NAME} was not moved, for any project that follows it")
        moved[folder] = why
        out.write(f"refused: {why}\n")
        return Outcome(entry.path, project.team_version, target, 1, why)
    if failing:
        err.write("warning: the overlays of a follower fail against the target, and --force moves the team's file "
                  "anyway\n")
    outcome = team_move(entry.path, options.wanted, options.dry_run, options.force, out, err, options.branch,
                        options.message, checked=folder)
    moved[folder] = None if outcome.why is None else outcome.why
    targets[folder] = target
    return outcome


def _what_is_committed(outcome: Outcome, committing: bool) -> str:
    """The files of ``outcome`` to commit, and, ``committing``, what ``--commit`` did of them. Under ``--team``,
    the team's files are committed in the team's folder, and the project's own — a bootstrap written again — are
    left to the project: said apart (ADR-013 §3.11)."""
    def said(why: Optional[str]) -> str:
        return "" if not committing else f" — committed on {outcome.committed}" if outcome.committed else \
            f" — not committed{': ' + _first(why) if why else ''}"
    if outcome.team is None:
        return f"{_and(outcome.names())}{said(outcome.uncommitted)}" if outcome.names() else ""
    # The team's files are those team_move commits — not whatever lies in the team's folder: a bootstrap linked
    # into it is the project's, left uncommitted.
    team = [path for path in outcome.files if path not in outcome.outside and path in outcome.team_files]
    own = [path for path in outcome.files if path not in outcome.outside and path not in team]
    parts = [f"{_and([_shown_path(path, outcome.root) for path in team])}{said(outcome.uncommitted)}"] if team else []
    if own:
        left = " — not committed: --team commits in the team's folder only" if committing else ""
        parts.append(f"{_and([_shown_path(path, outcome.root) for path in own])}{left}")
    return "; ".join(parts)


def _summary(done: List[Tuple[registry.Entry, Optional[Outcome], Optional[str]]], target: Version,
             options: _Options, out: TextIO) -> None:
    """A line per project — moved, already on the target, refused, skipped, interrupted, not reached
    — then what each must commit, by path, or what --commit did."""
    width = max(len(entry.name) for entry, _, _ in done)
    out.write(f"\nCortex {target}, in each registered project{' — a dry run' if options.dry_run else ''}:\n")
    for entry, outcome, said in done:
        if outcome is None:
            status = said
        elif outcome.why is not None:
            status = f"refused: {_first(outcome.why)}"
        elif outcome.target != outcome.pinned:
            status = f"{'would move' if options.dry_run else 'moved'} {outcome.pinned} → {outcome.target}"
        else:
            status = f"already on {outcome.target}"
        out.write(f"  {entry.name:<{width}}  {status}\n")
    written = [(entry, outcome) for entry, outcome, _ in done if outcome is not None and outcome.why is None
               and outcome.files]
    def nothing(outcome: Outcome) -> bool:
        """Nothing to commit for it: git ignores its files, or they are as committed already — and none
        was written outside its work tree, to commit where it is (#326)."""
        return outcome.ignored or ((outcome.nowhere or outcome.clean) and not outcome.outside)

    def why_nothing(outcome: Outcome) -> str:
        return "in no git work tree" if outcome.nowhere else outcome.uncommitted if outcome.clean else \
            f"git ignores {_and(outcome.names())}"

    commits = [(entry, outcome) for entry, outcome in written if not nothing(outcome)]
    left = [(entry, outcome) for entry, outcome, _ in done if outcome is not None and outcome.left]

    def from_before() -> None:
        for entry, outcome in left:
            names = [_shown_path(path, outcome.root) for path in outcome.left]
            out.write(f"  {entry.name:<{width}}  {entry.path}: nothing written — {_and(names)} not committed, from "
                      "before: `git status` there says what\n")

    if not commits:
        out.write("\nNothing this run wrote is to commit.\n" if left else "\nNothing to commit, in any project.\n")
        for entry, outcome in written:
            why = why_nothing(outcome)
            out.write(f"  {entry.name:<{width}}  {entry.path}: {why}\n")
        from_before()
        return
    committing = options.message is not None and not options.dry_run
    out.write(f"\nWhat each project {'would have to' if options.dry_run else 'must'} commit:\n")
    for entry, outcome in written:
        if nothing(outcome):
            why = why_nothing(outcome)
            out.write(f"  {entry.name:<{width}}  {entry.path}: nothing — {why}\n")
            continue
        here = f"nothing — {outcome.uncommitted}" if outcome.clean else f"{_and(outcome.names())} committed nowhere — in no git work tree" \
            if outcome.nowhere else _what_is_committed(outcome, committing)
        line = "; ".join(part for part in (here, outcome.elsewhere()) if part)
        out.write(f"  {entry.name:<{width}}  {entry.path}: {line}\n")
    from_before()
    if options.dry_run:
        out.write("--dry-run: nothing of any project was changed — no branch was made, nothing was committed.\n")
        return
    done = "Nothing was pushed" if committing else "Nothing was committed: --commit --message MESSAGE commits these files"
    out.write(f"{done}. Teammates run `cortex sync` after they pull.\n")


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #

REFUSALS = (config.ConfigError, store.StoreError, sync.SyncError, UpgradeError, registry.RegistryError, OSError,
            subprocess.CalledProcessError)


def _refusal(error: BaseException) -> str:
    """What a refusal says — one of ``REFUSALS``."""
    if isinstance(error, subprocess.CalledProcessError):
        return (f"{' '.join(map(str, error.cmd))} failed: "
                f"{(error.stderr or b'').decode(errors='replace').strip() or error.returncode}")
    if isinstance(error, OSError):
        return sync.os_error(error)
    return str(error)


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex upgrade",
        description="Move the project to another Cortex version — this cortex's own, by default — saying first "
                    "what changes. ADR-010 §3.5; --all, --branch and --commit, ADR-011 §3.5.")
    parser.add_argument("version", nargs="?", metavar="VERSION", help="the version to move to: X.Y.Z")
    parser.add_argument("--all", action="store_true",
                        help="move every project of this machine's registry, in name order — `cortex project list` "
                             "lists them")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, and change nothing")
    parser.add_argument("--force", action="store_true",
                        help="move the project even if its overlays fail against the target")
    parser.add_argument("--instructions-file", metavar="PATH",
                        help="the bootstrap file to write again, when it is no known tool's")
    parser.add_argument("--branch", metavar="NAME",
                        help="make the git branch NAME, and switch to it, before anything is written — refused where "
                             "it exists, unless it is checked out; none where there is nothing to commit")
    parser.add_argument("--commit", action="store_true",
                        help="commit the files the upgrade wrote, and nothing else, with --message — nothing is pushed")
    parser.add_argument("--message", metavar="MESSAGE", help="the message of --commit's commit")
    moves = parser.add_mutually_exclusive_group()
    moves.add_argument("--team", action="store_true",
                       help="move the team's file's version, agents/cortex.team.toml, and its lock — --branch and "
                            "--commit act in the team's repository (ADR-013 §3.11)")
    moves.add_argument("--local", action="store_true",
                       help="try VERSION here, in cortex.local.toml, writing no committed file — at or below the "
                            "team's version, the try is removed")
    options = parser.parse_args(args)
    if options.local and (options.all or options.branch is not None or options.commit):
        parser.error("--local writes cortex.local.toml, this developer's own: --all, --branch and --commit do not "
                     "go with it")
    if options.local and options.version is None:
        parser.error("--local needs the VERSION to try")
    if (options.team or options.local) and options.instructions_file is not None:
        parser.error("--instructions-file names the bootstrap a plain upgrade writes: --team and --local write the "
                     "bootstrap they write without it")
    if options.version is not None and not Version.valid(options.version):
        return sync.failed("cortex upgrade", f"{shown(options.version)} is no version — X.Y.Z, for instance 1.4.0", 2)
    if options.all and options.instructions_file is not None:
        parser.error("--instructions-file names a file of one project, and --all moves every registered one: run "
                     "`cortex upgrade --instructions-file PATH` in that project")
    if options.commit and options.message is None:
        parser.error("--commit needs --message: a team's commit convention is its own, so there is no default one")
    if options.message is not None and not options.commit:
        parser.error("--message is the message of --commit's commit: give --commit too")
    if options.commit and not options.message.strip():
        parser.error("--message is empty, and git commits no empty message")
    if (options.branch is not None or options.commit) and shutil.which("git") is None:
        flags = " and ".join(flag for flag, given in (("--branch", options.branch is not None),
                                                      ("--commit", options.commit)) if given)
        return sync.failed("cortex upgrade", f"{flags} {'run' if ' and ' in flags else 'runs'} git, which is not on "
                                             "PATH — nothing was changed")
    if options.branch is not None and not _branch_name(options.branch):
        parser.error(f"--branch {shown(options.branch)} is no branch name git takes")
    message = options.message if options.commit else None
    try:
        which = "team" if options.team else "local" if options.local else "project"
        if options.all:
            return upgrade_all(_Options(options.version, options.dry_run, options.force, None, options.branch, message,
                                        which), sys.stdout, sys.stderr)
        return upgrade(working_directory(), options.version, options.dry_run, options.force,
                       options.instructions_file, sys.stdout, sys.stderr, options.branch, message, which)
    except REFUSALS as error:
        return sync.failed("cortex upgrade", _refusal(error))
