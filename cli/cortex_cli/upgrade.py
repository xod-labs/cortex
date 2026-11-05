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
nothing at all when one of them held the developer's own changes. Neither is the default, and
nothing is ever pushed.
"""

from __future__ import annotations

import argparse
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
from cortex_core.project import shown

from . import config, init, registry, self_update, store, sync
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

    def names(self) -> List[str]:
        return [_shown_path(path, self.root) for path in self.files]


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


def written_by(kept: bytes, templates: Path, layouts: List[str], personality: bool) -> Optional[str]:
    """The layout ``kept`` — normalised — is exactly the file of, as the templates at ``templates``
    write it for this personality: the team's theme decides it, as for ``cortex init``. ``None``
    when a team changed it, or another version wrote it."""
    for layout in layouts:
        template = templates / TEMPLATES[layout]
        if template.is_file() and kept == init.instructions(template.read_bytes(), personality):
            return layout
    return None


def _written(templates: Path, layout: str, personality: bool) -> Optional[bytes]:
    """The bootstrap the templates at ``templates`` write for ``layout``, as Cortex writes it."""
    template = templates / TEMPLATES[layout]
    return init.stamped(init.instructions(template.read_bytes(), personality)) if template.is_file() else None


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
    again = "cortex init --force writes it again, and keeps yours as .bak"
    if len(found) > 1:
        return None, f"{display(str(path))} holds several bootstrap markers, so none is read, and it is kept: {again}"
    if not (found or init.bootstrap_of_cortex(content)):
        return None, None       # no Cortex bootstrap: cortex init says so
    layouts = [found[0][0]] if found else list(TEMPLATES)
    intact = init.untouched(content)
    if intact is None and current is None:
        return None, (f"{display(str(path))} was not checked: its marker carries no digest, and Cortex {pinned}, whose "
                      f"templates would tell whether a team changed it, is not in the store. It is kept: {again}")
    layout = layouts[0] if intact else None
    if intact is None:
        layout = written_by(content, current / "templates", layouts, personality)
    if layout is None:
        # A team changed it: said when the target writes another file than the pinned version, or,
        # without the pinned version, a later format.
        if current is not None:
            matters = any(_written(current / "templates", name, personality) !=
                          _written(target / "templates", name, personality) for name in layouts)
        else:
            matters = _format(content) < max(_format(_written(target / "templates", name, personality))
                                             for name in layouts)
        if not matters:
            return None, None
        what = "not what Cortex wrote, by the digest in its marker — a team changed it" if intact is False else \
            (f"{f'format {found[0][1]}' if found else 'a bootstrap without the marker'}, not what Cortex {pinned} "
             "writes — a team changed it, or an older Cortex wrote it")
        return None, f"{display(str(path))} is {what} — so it is kept, and the target's template is not written: {again}"
    wanted = _written(target / "templates", layout, personality)
    if wanted is None or wanted == content:
        return None, None
    if b"\r\n" in kept:
        wanted = wanted.replace(b"\n", b"\r\n")
    return (init.BOM + wanted if kept.startswith(init.BOM) else wanted), None


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
    if reads != str(project.version):
        return "this machine has not synced it" if reads is None else f"this machine reads Cortex {reads}"
    lock = config.load_lock(str(root))
    if lock is None:
        return f"there is no {config.LOCK_FILE}"
    if lock.version != project.version:
        return f"{config.LOCK_FILE} names Cortex {lock.version}"
    return None


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
    unless ``CORTEX_NO_UPDATE_CHECK`` is set."""
    if wanted is not None or os.environ.get("CORTEX_NO_UPDATE_CHECK"):
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


def upgrade(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, named: Optional[str],
            out: TextIO, err: TextIO, branch: Optional[str] = None, message: Optional[str] = None) -> int:
    """``cortex upgrade``: the project of ``cwd``. ``branch`` is ``--branch``'s, ``message`` that of
    ``--commit``, which commits only when it is given."""
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
    """What the project must commit — or what ``--commit`` committed, and where."""
    names = outcome.names()
    if outcome.ignored:
        ready = f"Cortex {outcome.target} is ready. " if outcome.target != outcome.pinned else ""
        out.write(f"\n{ready}Nothing to commit: git ignores {_and(names)}.\n")
    elif outcome.target != outcome.pinned:
        done = f"Committed {', '.join(names)} on {outcome.committed} — nothing was pushed" if outcome.committed else \
            f"Commit {', '.join(names)}"
        out.write(f"\nCortex {outcome.target} is ready. {done}; teammates run `cortex sync` after they pull.\n")
    elif names:
        out.write(f"\nCommitted {_and(names)} on {outcome.committed} — nothing was pushed.\n" if outcome.committed else
                  f"\nCommit {_and(names)}.\n")


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
    project = config.load(root)
    the_store = store.Store()
    current, target = project.version, Version(wanted) if wanted else the_store.own
    if wanted is None and current > the_store.own:
        # The default moves a project to this cortex's version, never down: a teammate's older binary
        # would otherwise undo the team's pin.
        raise UpgradeError(f"this project uses Cortex {current}, newer than this cortex, {the_store.own} — nothing was "
                           f"changed. Update this cortex to serve it:\n    {store.update_command(current)}\nor move the "
                           f"project down to this cortex's version by naming it: cortex upgrade {the_store.own}")
    _served(the_store, target, f"cortex upgrade {target}")
    if announce:
        out.write(f"Project: {display(root)}\n")
    files = _instructions_files(Path(root), named)
    personality = project.theme != "none"

    if target == current:
        out.write(f"Cortex {current}: already on it — nothing to move\n")
        return _resume(root, project, the_store, files, personality, dry_run, out, err, prepare)

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
        for path, _ in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {target}'s template — no one changed it\n")
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

    def switch() -> None:
        config.write_text(os.path.join(root, config.PROJECT_FILE), config.render_project(root, {"version": str(target)}))
        sync.sync(root, None, None, out, err, announce=False)

    _write(rewrites, committed, root, err, switch)
    for path, _ in rewrites:
        out.write(f"{_shown_path(path, root)}: written again from Cortex {target}'s template — no one changed it\n")
    return Outcome(root, current, target, files=team)


def _resume(root: str, project: config.Project, the_store: store.Store, files: List[Path], personality: bool,
            dry_run: bool, out: TextIO, err: TextIO,
            prepare: Optional[Callable[[List[Path]], None]] = None) -> Outcome:
    """On the pinned version, finish what an upgrade cut short, or a pin moved by hand, left: a
    bootstrap file nobody changed, of another version, written again; this machine and the lock,
    when they are not on the pin, synced (ADR-010 §3.5, step 1)."""
    current = project.version
    behind = out_of_step(Path(root), project, the_store)
    rewrites: List[Tuple[Path, bytes]] = []
    if files:
        try:
            the_store.ensure(current)
        except store.StoreError as error:
            err.write(f"note: the bootstrap files were not checked — Cortex {current} is not in the store: {error}\n")
        else:
            rewrites = bootstraps(files, current, the_store.path(current), the_store.path(current), personality, err)
    if behind is not None:
        out.write(f"{behind}, and {config.PROJECT_FILE} pins Cortex {current} — an upgrade cut short, or a pin moved by "
                  f"hand: {'the sync would put them on it' if dry_run else 'syncing'}\n")
    lock = Path(root) / config.LOCK_FILE
    relock = behind is not None and getattr(config.load_lock(root), "version", None) != current
    team = ([lock] if relock else []) + [path for path, _ in rewrites]
    if prepare is not None and team:
        prepare(team)
    if dry_run:
        for path, _ in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {current}'s template — no one "
                      "changed it\n")
        if rewrites or behind is not None:
            out.write("--dry-run: nothing of the project was changed\n")
        return Outcome(root, current, current, files=team)
    if not rewrites and behind is None:
        return Outcome(root, current, current)
    locked = lock.read_bytes() if lock.is_file() else None
    _write(rewrites, {lock: locked} if behind is not None else {}, root, err,
           (lambda: sync.sync(root, None, None, out, err, announce=False)) if behind is not None else (lambda: None))
    for path, _ in rewrites:
        out.write(f"{_shown_path(path, root)}: written again from Cortex {current}'s template — no one changed it\n")
    relocked = lock.is_file() and lock.read_bytes() != locked
    return Outcome(root, current, current, files=([lock] if relocked else []) + [path for path, _ in rewrites])


def _write(rewrites: List[Tuple[Path, bytes]], committed: Dict[Path, Optional[bytes]], root: str, err: TextIO,
           then: Callable[[], None]) -> None:
    """Write the bootstrap files again, then run ``then`` — the switch: anything that fails puts back
    the files, and ``committed``, as they were. The bootstrap files go first: a run cut short after
    them leaves files whose digest the next `cortex upgrade` reads as nobody's change — and the
    pinned version it finds then, it finishes."""
    before = {**committed, **{path: path.read_bytes() for path, _ in rewrites}}
    try:
        for path, content in rewrites:
            _put(path, content)
        then()
    except BaseException:
        for path, content in before.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _put(path, content)
        names = _and([_shown_path(path, root) for path in before])
        err.write(f"note: {names} {'are as they were' if len(before) > 1 else 'is as it was'}"
                  f"{' — `cortex sync` puts this machine back on them' if committed else ''}\n")
        raise


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
                              stdin=subprocess.DEVNULL, timeout=timeout)
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
    if any(os.path.lexists(os.path.join(directory, ".git")) for directory in (root, *map(str, Path(root).parents))):
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
    seen, fields = set(), iter(proc.stdout.split("\0"))
    for field in fields:                # "XY path", and after a rename or a copy, the path it was
        if len(field) > 3:
            seen.add(_real(os.path.join(top, field[3:])))
            if "R" in field[:2] or "C" in field[:2]:
                seen.add(_real(os.path.join(top, next(fields, ""))))
    return [path for path in files if _real(path) in seen]


def _commit(root: str, top: str, files: List[Path], message: str,
            theirs: List[Path]) -> Tuple[Optional[str], Optional[str]]:
    """Commit ``files`` in the work tree of ``root``, whose top is ``top``, with ``message``, and
    nothing else: a change of another file, staged or not, stays as it is. Where it was committed —
    the branch, and the commit — or why nothing was: git tracks no ``cortex.toml`` there — the pin is
    this developer's own (ADR-010 §3.8); git ignores one of the files; they are as committed; or
    ``theirs``, those git saw changed before the upgrade wrote them, hold the developer's own
    changes, which are no part of the team's commit. ``UpgradeError`` when git fails, or refuses the
    commit — the files stay staged then."""
    paths = _git_paths(root, files)
    if _git(root, "ls-files", "--error-unmatch", "--", *_literal([config.PROJECT_FILE])).returncode != 0:
        return None, f"git does not track its {config.PROJECT_FILE} — the pin is this developer's own"
    ignored = _ignored(root, files)
    if ignored:
        return None, f"git ignores {_and(ignored)}"
    if not _changed(root, top, files):
        return None, f"{_and(paths)} {'are' if len(paths) > 1 else 'is'} as committed already"
    if theirs:
        names = [_shown_path(path, root) for path in theirs]
        return None, (f"{_and(names)} held changes of yours before the upgrade wrote "
                      f"{'them' if len(names) > 1 else 'it'} — commit by hand what is the team's")
    _git_ok(root, "add", "--", *_literal(paths), timeout=None)
    if _git(root, "diff", "--cached", "--quiet", "--", *_literal(paths)).returncode == 0:
        return None, f"{_and(paths)} {'are' if len(paths) > 1 else 'is'} as committed already"
    proc = _git(root, "commit", "-q", "-m", message, "--", *_literal(paths), timeout=None)      # its hooks run
    if proc.returncode != 0:
        raise UpgradeError(f"git refused the commit — {(proc.stderr or proc.stdout).strip() or proc.returncode}\n"
                           f"{_and(paths)} {'stay' if len(paths) > 1 else 'stays'} staged, ready to commit by hand")
    commit = _git_ok(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"{_on_branch(root) or 'a detached HEAD'}, {commit}", None


class Interrupted(KeyboardInterrupt):
    """Ctrl-C during the upgrade of a project that ``--branch`` made a branch for."""

    def __init__(self, branch: str):
        super().__init__(branch)
        self.branch = branch


def _move(cwd: str, options: _Options, out: TextIO, err: TextIO, announce: bool = True,
          made: Optional[Dict[str, str]] = None) -> Outcome:
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


def _git_move(cwd: str, options: _Options, out: TextIO, err: TextIO, announce: bool, made: Dict[str, str],
              branched: List[str]) -> Outcome:
    """The upgrade, with the branch made once all is checked and before anything is written, where
    there is something to commit; the files the upgrade wrote committed. ``made``: the repositories
    this run made the branch in, and the work tree it is checked out in — a second project of that
    work tree finds it checked out, one of another work tree of the repository is refused. ``branched``
    gets the work tree this project's branch was made in."""
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
    theirs: List[Path] = []
    if options.message is not None and top is not None and not options.dry_run:
        # What git sees changed before anything is written is the developer's: never committed.
        theirs = _changed(root, top, [pin, Path(root) / config.LOCK_FILE, *_instructions_files(Path(root), options.named)])
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
        if made.get(repository, key) != key:
            raise UpgradeError(f"the branch {options.branch} is checked out in {display(made[repository])}, where this "
                               "run made it, and git checks a branch out in one work tree at a time — nothing was "
                               f"changed. `cortex upgrade --branch NAME` in {display(root)} moves it on another branch")
        if _branch_exists(root, options.branch):
            raise UpgradeError(f"the branch {options.branch} exists already in {display(top)} — nothing was changed. "
                               "Name another one with --branch")
        if options.dry_run:
            return
        _git_ok(root, "checkout", "-q", "-b", options.branch)
        made[repository] = key
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
    if options.message is None or options.dry_run or outcome.why is not None or not outcome.files:
        return outcome
    if top is None:
        outcome.uncommitted = "in no git work tree"
        return outcome
    if outcome.ignored:
        err.write(f"note: nothing to commit in {display(root)} — git ignores {_and(outcome.names())}\n")
        return outcome
    written = {_real(path) for path in outcome.files}
    try:
        outcome.committed, outcome.uncommitted = _commit(
            root, top, outcome.files, options.message,
            [path for path in theirs if path == pin or _real(path) in written])
    except UpgradeError as error:
        outcome.code, outcome.uncommitted = 1, str(error)
        out.flush()
        err.write(f"cortex upgrade: {error} — the upgrade is done, and nothing was committed\n")
    else:
        if outcome.uncommitted is not None:
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
                  "moves those not reached\n")
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
    made: Dict[str, str] = {}
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
            outcome = _move(entry.path, options, out, err, announce=False, made=made)
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
    commits = [(entry, outcome) for entry, outcome in written if not outcome.ignored]
    if not commits:
        out.write("\nNothing to commit, in any project.\n")
        for entry, outcome in written:
            out.write(f"  {entry.name:<{width}}  {entry.path}: git ignores {_and(outcome.names())}\n")
        return
    committing = options.message is not None and not options.dry_run
    out.write(f"\nWhat each project {'would have to' if options.dry_run else 'must'} commit:\n")
    for entry, outcome in written:
        if outcome.ignored:
            out.write(f"  {entry.name:<{width}}  {entry.path}: nothing — git ignores {_and(outcome.names())}\n")
            continue
        said = "" if not committing else f" — committed on {outcome.committed}" if outcome.committed else \
            f" — not committed: {_first(outcome.uncommitted)}"
        out.write(f"  {entry.name:<{width}}  {entry.path}: {_and(outcome.names())}{said}\n")
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
    options = parser.parse_args(args)
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
        if options.all:
            return upgrade_all(_Options(options.version, options.dry_run, options.force, None, options.branch, message),
                               sys.stdout, sys.stderr)
        return upgrade(working_directory(), options.version, options.dry_run, options.force,
                       options.instructions_file, sys.stdout, sys.stderr, options.branch, message)
    except REFUSALS as error:
        return sync.failed("cortex upgrade", _refusal(error))
