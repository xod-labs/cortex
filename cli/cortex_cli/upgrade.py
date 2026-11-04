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
The binary is never upgraded here: the install script does that.
"""

from __future__ import annotations

import argparse
import io
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

from cortex_core import validate as overlay_validator
from cortex_core.project import shown

from . import config, init, store, sync
from .paths import display, working_directory
from .semver import Version

LAYERS = ("roles", "capabilities", "personalities", "workflows")
TEMPLATES = {"single": "bootstrap-instructions.md", "workspace": "bootstrap-instructions-workspace.md"}


class UpgradeError(Exception):
    pass


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


def upgrade(cwd: str, wanted: Optional[str], dry_run: bool, force: bool, named: Optional[str],
            out: TextIO, err: TextIO) -> int:
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
                           f"changed. Upgrade this cortex to serve it:\n    {store.upgrade_command(current)}\nor move the "
                           f"project down to this cortex's version by naming it: cortex upgrade {the_store.own}")
    if target > the_store.own:
        raise UpgradeError(f"Cortex {target} is newer than this cortex, {the_store.own} — upgrade this cortex first:\n"
                           f"    {store.upgrade_command(target)}")
    the_store.check_served(target)
    out.write(f"Project: {display(root)}\n")
    files = _instructions_files(Path(root), named)
    personality = project.theme != "none"

    if target == current:
        out.write(f"Cortex {current}: already on it — nothing to move\n")
        return _resume(root, project, the_store, files, personality, dry_run, out, err)

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
            return 1
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

    if dry_run:
        for path, _ in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {target}'s template — no one changed it\n")
        if stops:
            err.write(f"cortex upgrade: the overlays would fail against Cortex {target}, and stop it — fix them, "
                      "or --force moves the project anyway\n")
        if refused is not None:
            err.write(f"cortex upgrade: the sync would refuse it — {refused}\n")
        out.write("--dry-run: nothing of the project was changed\n")
        return 1 if stops or refused is not None else 0

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
    written = ", ".join([config.PROJECT_FILE, config.LOCK_FILE, *(_shown_path(path, root) for path, _ in rewrites)])
    out.write(f"\nCortex {target} is ready. Commit {written}; teammates run `cortex sync` after they pull.\n")
    return 0


def _resume(root: str, project: config.Project, the_store: store.Store, files: List[Path], personality: bool,
            dry_run: bool, out: TextIO, err: TextIO) -> int:
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
    if dry_run:
        for path, _ in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {current}'s template — no one "
                      "changed it\n")
        if rewrites or behind is not None:
            out.write("--dry-run: nothing of the project was changed\n")
        return 0
    if not rewrites and behind is None:
        return 0
    lock = Path(root) / config.LOCK_FILE
    locked = lock.read_bytes() if lock.is_file() else None
    _write(rewrites, {lock: locked} if behind is not None else {}, root, err,
           (lambda: sync.sync(root, None, None, out, err, announce=False)) if behind is not None else (lambda: None))
    for path, _ in rewrites:
        out.write(f"{_shown_path(path, root)}: written again from Cortex {current}'s template — no one changed it\n")
    commit = [_shown_path(path, root) for path, _ in rewrites]
    if lock.is_file() and lock.read_bytes() != locked:
        commit.insert(0, config.LOCK_FILE)
    if commit:
        out.write(f"\nCommit {_and(commit)}.\n")
    return 0


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


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex upgrade",
        description="Move the project to another Cortex version — this cortex's own, by default — saying first "
                    "what changes. ADR-010 §3.5.")
    parser.add_argument("version", nargs="?", metavar="VERSION", help="the version to move to: X.Y.Z")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, and change nothing")
    parser.add_argument("--force", action="store_true",
                        help="move the project even if its overlays fail against the target")
    parser.add_argument("--instructions-file", metavar="PATH",
                        help="the bootstrap file to write again, when it is no known tool's")
    options = parser.parse_args(args)
    if options.version is not None and not Version.valid(options.version):
        return sync.failed("cortex upgrade", f"{shown(options.version)} is no version — X.Y.Z, for instance 1.4.0", 2)
    try:
        return upgrade(working_directory(), options.version, options.dry_run, options.force,
                       options.instructions_file, sys.stdout, sys.stderr)
    except (config.ConfigError, store.StoreError, sync.SyncError, UpgradeError) as error:
        return sync.failed("cortex upgrade", str(error))
    except OSError as error:
        return sync.failed("cortex upgrade", sync.os_error(error))
    except subprocess.CalledProcessError as error:
        return sync.failed("cortex upgrade", f"{' '.join(map(str, error.cmd))} failed: "
                                             f"{(error.stderr or b'').decode(errors='replace').strip() or error.returncode}")
