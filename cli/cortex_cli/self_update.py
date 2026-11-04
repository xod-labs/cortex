"""``cortex self-update`` — replace this binary with a release's (ADR-010 §3.7).

``cortex self-update [VERSION]`` installs the binary of ``VERSION``, or of the latest release, in
place of the one running, under the name it was installed with. It downloads the asset of this
system from where the store and the install scripts download, checks it against the release's
``SHA256SUMS``, writes the binary beside the old one and runs it: only a binary that answers
``--version`` with the target is installed.

The replacement is a rename, on the same file system. On Linux and macOS the new binary is renamed
over the old one, and the running process keeps the file it started from. Windows refuses to
overwrite a running executable, not to rename it: the old one is renamed ``NAME.old`` first, and the
next ``cortex`` that starts removes it (``tidy``).

The latest release is the version ``latest/download/SHA256SUMS`` redirects to, as GitHub's releases
do. A project never moves here: ``cortex upgrade`` moves one.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, TextIO, Tuple

from cortex_core.project import shown

from . import net, store, sync
from .paths import display
from .semver import Version
from .version import VERSION

# The first release with this command: a binary before it is moved by the install script only.
FIRST_SELF_UPDATE = Version("1.4.0")
MAX_ASSET = 256 * 1024 * 1024
MAX_BINARY = 512 * 1024 * 1024
# How long `cortex upgrade` waits to learn the latest release: a notice, never worth a wait.
NOTICE_TIMEOUT = 5
RUN_TIMEOUT = 120
SUMS = "SHA256SUMS"
_DOWNLOADED_SUMS = re.compile(r"/download/([^/]+)/SHA256SUMS$")


class SelfUpdateError(Exception):
    """A binary that cannot be replaced — the message says why, and that nothing was."""


# --------------------------------------------------------------------------- #
# The release
# --------------------------------------------------------------------------- #

def asset_name(system: str, machine: str) -> str:
    """The release asset of this system, as the install scripts name it."""
    system, machine = system.lower(), machine.lower()
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "cortex-linux-x86_64.tar.gz"
    if system == "linux" and machine in ("aarch64", "arm64"):
        return "cortex-linux-aarch64.tar.gz"
    if system == "darwin" and machine == "arm64":
        return "cortex-macos-arm64.tar.gz"
    if system == "windows" and machine in ("amd64", "x86_64"):
        return "cortex-windows-x86_64.zip"
    raise SelfUpdateError(f"no build for {system} on {machine} — the targets are Linux x86_64 and aarch64, "
                          "macOS arm64 and Windows x86_64")


def latest(fetch: Callable[..., bytes], timeout: float = net.TIMEOUT) -> Tuple[Optional[Version], bytes]:
    """The latest release and its ``SHA256SUMS`` — the version ``latest/download/SHA256SUMS``
    redirects to; ``None`` where the releases are served without that redirect."""
    redirects: List[str] = []
    sums = fetch(f"{store.releases_url()}/latest/download/{SUMS}", store.MAX_SUMS, timeout=timeout,
                 redirects=redirects)
    for url in redirects:
        found = _DOWNLOADED_SUMS.search(url.split("?", 1)[0])
        if found and Version.valid(found.group(1)):
            return Version(found.group(1)), sums
    return None, sums


def newer_release(own: Version, fetch: Callable[..., bytes] = net.get) -> Optional[Version]:
    """The latest release when it is newer than ``own`` — ``None`` when it is not, or when the
    releases do not answer within a few seconds: offline, nothing is said."""
    try:
        found, _ = latest(fetch, NOTICE_TIMEOUT)
    except (net.DownloadError, store.StoreError):
        return None
    return found if found is not None and found > own else None


def expected_checksum(sums: bytes, asset: str, version: Version) -> str:
    text = sums.decode("utf-8", errors="replace")
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == asset:
            return fields[0].lower()
    raise SelfUpdateError(f"the {SUMS} of Cortex {version} lists no {asset} — nothing was replaced")


def unpack(data: bytes, asset: str) -> bytes:
    """The binary an asset holds: its one member named ``cortex`` — ``cortex.exe`` in a zip —
    a regular file."""
    member = "cortex.exe" if asset.endswith(".zip") else "cortex"
    nothing = SelfUpdateError(f"{asset} holds no {member} — nothing was replaced")
    try:
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = next((i for i in archive.infolist() if i.filename == member), None)
                if info is None or info.is_dir() or info.file_size > MAX_BINARY:
                    raise nothing
                return archive.read(info)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            info = next((i for i in archive.getmembers() if i.name in (member, f"./{member}")), None)
            if info is None or not info.isfile() or info.size > MAX_BINARY:
                raise nothing
            return archive.extractfile(info).read()
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
        raise SelfUpdateError(f"{asset} cannot be read: {error} — nothing was replaced")


# --------------------------------------------------------------------------- #
# The binary
# --------------------------------------------------------------------------- #

def running_binary() -> Optional[Path]:
    """The binary running — through any link to it — or ``None`` from a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(os.path.realpath(sys.executable))
    return None


def answers(binary: Path) -> str:
    """What ``binary --version`` prints, or why it does not run."""
    try:
        proc = subprocess.run([str(binary), "--version"], capture_output=True, timeout=RUN_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        raise SelfUpdateError(f"the new binary does not run on this machine: {error} — nothing was replaced")
    output = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        why = proc.stderr.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"
        raise SelfUpdateError(f"the new binary does not run on this machine: {why} — nothing was replaced")
    return output.splitlines()[0] if output else ""


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


def tidy(binary: Optional[Path] = None) -> None:
    """Remove what a replacement on Windows left beside the binary — once it no longer runs."""
    binary = binary or running_binary()
    if binary is None:
        return
    try:
        for entry in binary.parent.glob(binary.name + ".old*"):
            try:
                entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def self_update(wanted: Optional[str], dry_run: bool, out: TextIO, err: TextIO, *,
                binary: Optional[Path], own: Version, fetch: Callable[..., bytes] = net.get,
                run: Callable[[Path], str] = answers, system: Optional[str] = None,
                machine: Optional[str] = None) -> int:
    if binary is None:
        raise SelfUpdateError("this cortex runs from a source checkout, not from a binary — there is none to "
                              f"replace. Install one with:\n    {store.install_command()}")
    asset = asset_name(system or platform.system(), machine or platform.machine())
    base = store.releases_url()
    sums: Optional[bytes] = None
    if wanted is None:
        try:
            target, sums = latest(fetch)
        except net.DownloadError as error:
            raise SelfUpdateError(f"could not learn the latest release: {error}")
        if target is None:
            raise SelfUpdateError(f"{base} does not say which release is the latest — name the one to install: "
                                  "cortex self-update X.Y.Z")
    else:
        target = Version(wanted)
    if target == own:
        out.write(f"cortex {own}: already on it — nothing to install\n")
        return 0

    # Beside the old binary, on its file system: the last step is a rename. A directory this user
    # cannot write in fails here, before anything is downloaded.
    try:
        fd, name = tempfile.mkstemp(prefix=f".{binary.name}.new-", suffix=".exe" if os.name == "nt" else "",
                                    dir=binary.parent)
    except OSError as error:
        raise SelfUpdateError(f"cannot write in {display(str(binary.parent))} ({error.strerror or error}) — an "
                              "administrator or a package manager installed this cortex: update it the way it was "
                              f"installed, or install one of your own with:\n    {store.install_command(target)}")
    new = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            out.write(f"Downloading {asset} of Cortex {target} from {base}\n")
            try:
                if sums is None:
                    sums = fetch(f"{base}/download/{target}/{SUMS}", store.MAX_SUMS)
                expected = expected_checksum(sums, asset, target)
                data = fetch(f"{base}/download/{target}/{asset}", MAX_ASSET)
            except net.DownloadError as error:
                raise SelfUpdateError(f"could not download Cortex {target}: {error} — nothing was replaced")
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise SelfUpdateError(f"{asset} does not match its checksum — expected {expected}, got {actual}. "
                                      "Nothing was replaced.")
            fh.write(unpack(data, asset))
        os.chmod(new, 0o755)
        answer = run(new)
        if answer != f"cortex {target}":
            raise SelfUpdateError(f"the binary of Cortex {target} answers {answer or 'nothing'!r} to --version, "
                                  f"not 'cortex {target}' — nothing was replaced")
        if dry_run:
            out.write(f"--dry-run: Cortex {target} downloaded, checked and run — {display(str(binary))} "
                      "was not replaced\n")
            return 0
        replace(new, binary)
    finally:
        if new.exists():
            new.unlink()

    out.write(f"Installed cortex {target} as {display(str(binary))}, in place of {own}.\n")
    if target < own:
        err.write(f"note: a downgrade — a project pinned to a version after {target} is now refused\n")
        if target < FIRST_SELF_UPDATE:
            err.write(f"note: Cortex {target} has no self-update — the install script moves it again:\n"
                      f"    {store.install_command()}\n")
    out.write("No project moved: `cortex upgrade` moves one to this version.\n")
    return 0


def run(args: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex self-update",
        description="Replace this cortex with a release's binary — the latest, by default — checked against the "
                    "release's SHA256SUMS. ADR-010 §3.7.")
    parser.add_argument("version", nargs="?", metavar="VERSION", help="the version to install: X.Y.Z")
    parser.add_argument("--dry-run", action="store_true",
                        help="download, check and run the binary, and replace nothing")
    options = parser.parse_args(args)
    if options.version is not None and not Version.valid(options.version):
        return sync.failed("cortex self-update", f"{shown(options.version)} is no version — X.Y.Z, for instance 1.4.0", 2)
    try:
        return self_update(options.version, options.dry_run, sys.stdout, sys.stderr,
                           binary=running_binary(), own=Version(VERSION))
    except (SelfUpdateError, store.StoreError) as error:
        return sync.failed("cortex self-update", str(error))
    except OSError as error:
        return sync.failed("cortex self-update", sync.os_error(error))
