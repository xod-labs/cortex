"""The running binary: where it is, how another takes its place, what that leaves (ADR-010 §3.7).

Light on purpose: every start of a built binary calls ``tidy``, before anything else.

The replacement is a rename, on the same file system. On Linux and macOS the new binary is renamed
over the old one, and the running process keeps the file it started from. Windows refuses to
overwrite a running executable, not to rename it: the old one is renamed ``NAME.old`` first, and the
next start removes it — with the new binary a self-update could not remove after running it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

# A new binary younger than this may be another self-update's, still downloading or running it.
STALE = 3600


def running_binary() -> Optional[Path]:
    """The binary running — through any link to it — or ``None`` from a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(os.path.realpath(sys.executable))
    return None


def environment() -> Dict[str, str]:
    """The environment to start another built binary in: this one's, without what PyInstaller's
    bootloader set for this process — the library path it prepended, and the variables that would
    make the other binary take itself for a part of this one."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)    # PyInstaller's own recipe, for Linux
        if original is not None:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def replace(new: Path, binary: Path, aside: bool = os.name == "nt") -> None:
    """Put ``new`` in the place of ``binary``. With ``aside`` — on Windows, where a running
    executable can be renamed but not overwritten — the old one is renamed ``NAME.old`` first, and
    put back if the new one cannot take its name."""
    if not aside:
        os.replace(new, binary)
        return
    old = binary.with_name(binary.name + ".old")
    if old.exists():
        try:
            old.unlink()
        except OSError:     # still running: another name, which the next start removes too
            old = binary.with_name(f"{binary.name}.old-{os.getpid()}-{time.time_ns()}")
    os.rename(binary, old)
    try:
        os.rename(new, binary)
    except BaseException:
        os.rename(old, binary)
        raise


def tidy(binary: Optional[Path] = None, aside: bool = os.name == "nt") -> None:
    """Remove what a self-update left beside the binary: with ``aside``, the old one renamed aside,
    once it no longer runs; and a new one it could not remove, or was killed before it installed —
    once it is an hour old, as a younger one may be another self-update's, at work. A file still in
    use stays, until a later start. Without ``aside`` — on Linux and macOS — a ``NAME.old`` is
    nobody's leftover, and may be someone's copy: it stays."""
    binary = binary or running_binary()
    if binary is None:
        return
    try:
        found = [(entry, True) for entry in binary.parent.glob(f".{binary.name}.new-*")]
        if aside:
            found += [(entry, False) for entry in binary.parent.glob(binary.name + ".old*")]
        for entry, new in found:
            try:
                if new and time.time() - entry.stat().st_mtime < STALE:
                    continue
                entry.unlink()
            except OSError:
                pass
    except OSError:
        pass
