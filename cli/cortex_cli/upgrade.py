"""``cortex upgrade`` — move a project to another Cortex version (ADR-010 §3.5).

``cortex upgrade [VERSION]`` moves the project of the current directory — the nearest
``cortex.toml`` — to ``VERSION``, or to this binary's own version. Before it changes anything it
says what changes: the agents the target adds, removes and changes, layer by layer; then it runs
the overlay validator with the target as the base, and stops if the project would fail it, unless
``--force``. The switch is ``version`` written in ``cortex.toml``, its comments kept, and a sync —
``cortex.local.toml``, the link or copy, ``cortex.lock``. A bootstrap file of an older format than
the target's is written again when it is exactly what the pinned version wrote; one a team changed
is kept, and the command says how ``cortex init --force`` writes it again.

``--dry-run`` says all of it and changes nothing in the project; the store may gain the target.
The binary is never upgraded here: the install script does that.
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

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
    """The files under ``prefix``, by path below it — an agent by its name, without ``.md``, and
    never a layer's README, which is no agent."""
    found = {}
    for path, sha in hashes.items():
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if agents:
            if rel.rsplit("/", 1)[-1].casefold() == "readme.md":
                continue
            rel = rel[:-3] if rel.endswith(".md") else rel
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
    """The layout ``kept`` is exactly the file of, as the templates at ``templates`` write it for
    this personality — the team's theme decides it, as for ``cortex init``; line endings aside, as
    git may check a file out with CRLF. ``None`` when a team changed it, or another version wrote it."""
    kept = kept.replace(b"\r\n", b"\n")
    for layout in layouts:
        template = templates / TEMPLATES[layout]
        if template.is_file() and kept == init.instructions(template.read_bytes(), personality):
            return layout
    return None


def bootstrap(path: Path, current: Path, target: Path, personality: bool) -> Tuple[Optional[bytes], Optional[str]]:
    """What becomes of the bootstrap at ``path`` when the spec moves from ``current`` to ``target``:
    the bytes to write, or the note to give — ``(None, None)`` when nothing is to be done."""
    kept = path.read_bytes()
    found = init.markers(kept)
    if len(found) > 1 or not (found or init.bootstrap_of_cortex(kept)):
        return None, None       # several markers, or no Cortex bootstrap: cortex init says so
    layouts = [found[0][0]] if found else list(TEMPLATES)
    has = found[0][1] if found else 0
    wanted = None
    for layout in layouts:
        template = target / "templates" / TEMPLATES[layout]
        marker = init.markers(template.read_bytes()) if template.is_file() else []
        if marker:
            wanted = max(wanted or 0, marker[0][1])
    if wanted is None or wanted <= has:
        return None, None       # the target writes no later format
    layout = written_by(kept, current / "templates", layouts, personality)
    if layout is None:
        what = f"format {has}" if found else "a bootstrap without the marker"
        return None, (f"{display(str(path))} is {what}, and the target writes format {wanted}. It is not what the "
                      "pinned version writes — a team changed it, or an older Cortex wrote it — so it is kept: "
                      "cortex init --force writes it again, and keeps yours as .bak")
    content = init.instructions((target / "templates" / TEMPLATES[layout]).read_bytes(), personality)
    return (content.replace(b"\n", b"\r\n") if b"\r\n" in kept else content), None


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
    if target > the_store.own:
        raise UpgradeError(f"Cortex {target} is newer than this cortex, {the_store.own} — upgrade this cortex first:\n"
                           f"    {store.upgrade_command(target)}")
    the_store.check_served(target)
    out.write(f"Project: {display(root)}\n")
    files = _instructions_files(Path(root), named)
    personality = project.theme != "none"

    if target == current:
        out.write(f"Cortex {current}: already on it — nothing to move\n")
        for path in files:
            _, note = bootstrap(path, the_store.path(current), the_store.path(current), personality) \
                if the_store.has(current) else (None, None)
            if note:
                err.write(f"note: {note}\n")
        return 0

    the_store.ensure(target)
    target_path = the_store.path(target)
    direction = "an upgrade" if target > current else "a downgrade"
    out.write(f"Cortex {current} → {target}: {direction} — the release notes: {store.releases_url()}/tag/{target}\n")
    try:
        the_store.ensure(current)
        say_changes(changes(the_store.path(current), target_path), out)
        current_path: Optional[Path] = the_store.path(current)
    except store.StoreError as error:
        err.write(f"note: what changes is not said — Cortex {current} is not in the store: {error}\n")
        current_path = None

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

    rewrites: List[Tuple[Path, bytes]] = []
    for path in files:
        content, note = bootstrap(path, current_path, target_path, personality) if current_path is not None else (None, None)
        if note:
            err.write(f"note: {note}\n")
        if content is not None:
            rewrites.append((path, content))

    if dry_run:
        for path, _ in rewrites:
            out.write(f"{_shown_path(path, root)}: would be written again from Cortex {target}'s template — no one changed it\n")
        if stops:
            err.write(f"cortex upgrade: the overlays would fail against Cortex {target}, and stop it — fix them, "
                      "or --force moves the project anyway\n")
        out.write("--dry-run: nothing of the project was changed\n")
        return 1 if stops else 0

    # The pin and the lock are what the team commits: a sync that fails leaves both as they were.
    # What it changed on this machine — cortex.local.toml, cortex/ — the next `cortex sync` puts right.
    committed = {name: (Path(root) / name).read_bytes() if (Path(root) / name).is_file() else None
                 for name in (config.PROJECT_FILE, config.LOCK_FILE)}
    config.write_text(os.path.join(root, config.PROJECT_FILE), config.render_project(root, {"version": str(target)}))
    try:
        sync.sync(root, None, None, out, err, announce=False)
    except BaseException:
        for name, content in committed.items():
            if content is None:
                (Path(root) / name).unlink(missing_ok=True)
            else:
                (Path(root) / name).write_bytes(content)
        err.write(f"note: {config.PROJECT_FILE} and {config.LOCK_FILE} are as they were — `cortex sync` puts this "
                  "machine back on them\n")
        raise
    for path, content in rewrites:
        path.write_bytes(content)
        out.write(f"{_shown_path(path, root)}: written again from Cortex {target}'s template — no one changed it\n")
    written = ", ".join([config.PROJECT_FILE, config.LOCK_FILE, *(_shown_path(path, root) for path, _ in rewrites)])
    out.write(f"\nCortex {target} is ready. Commit {written}; teammates run `cortex sync` after they pull.\n")
    return 0


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
