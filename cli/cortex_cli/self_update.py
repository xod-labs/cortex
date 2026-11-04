"""``cortex self-update`` — replace this binary with a release's (ADR-010 §3.7).

``cortex self-update [VERSION]`` installs the binary of ``VERSION``, or of the latest release, in
place of the one running, under the name it was installed with. It downloads the asset of the
build running — its target, stamped at build time — from where the store and the install scripts
download, checks it against the release's ``SHA256SUMS``, writes the binary beside the old one
and runs it: only a binary that answers ``--version`` with the target is installed, by a rename
(``binary.replace``).

The latest release is the version ``latest/download/SHA256SUMS`` redirects to, as GitHub's releases
do. A latest release older than this binary — a pre-release running, a patch of an older line
published since — is not installed unless it is named. A project never moves here: ``cortex
upgrade`` moves one.
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
import zipfile
import zlib
from pathlib import Path
from typing import Callable, List, Optional, TextIO, Tuple

from cortex_core.project import shown

from . import net, store, sync
from .binary import ASIDE, environment, replace, running_binary
from .paths import display
from .semver import Version
from .version import TARGET, VERSION

# The first release with this command: a binary before it is moved by the install script only.
FIRST_SELF_UPDATE = Version("1.4.0")
MAX_ASSET = 256 * 1024 * 1024
MAX_BINARY = 512 * 1024 * 1024
# How long `cortex upgrade` waits to learn the latest release: a notice, never worth a wait.
NOTICE_TIMEOUT = 5
MAX_OUTPUT = 64 * 1024
RUN_TIMEOUT = 120
SUMS = "SHA256SUMS"
_DOWNLOADED_SUMS = re.compile(r"/download/([^/]+)/SHA256SUMS$")


class SelfUpdateError(Exception):
    """A binary that cannot be replaced — the message says why, and that nothing was."""


# --------------------------------------------------------------------------- #
# The release
# --------------------------------------------------------------------------- #

def asset_of(target: str) -> str:
    """The release asset of a build target, ``linux-x86_64`` say, as ``cli/build.py`` packages it."""
    return f"cortex-{target}.zip" if target.startswith("windows-") else f"cortex-{target}.tar.gz"


def asset_name(system: str, machine: str) -> str:
    """The release asset of this system, as the install scripts name it — for a binary that names
    no target of its own."""
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
    except (net.DownloadError, store.StoreError, OSError, ValueError):  # a certificate store unread, say
        return None
    return found if found is not None and found > own else None


def expected_checksum(sums: bytes, asset: str, version: Version) -> str:
    found = store.listed_checksum(sums.decode("utf-8", errors="replace"), asset)
    if found is None:
        raise SelfUpdateError(f"the {SUMS} of Cortex {version} lists no {asset} — nothing was replaced")
    return found


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
    # zlib.error: data that inflates to nothing; RuntimeError: an encrypted member; NotImplementedError:
    # a compression zipfile does not know.
    except (tarfile.TarError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError,
            OSError) as error:
        raise SelfUpdateError(f"{asset} cannot be read: {error} — nothing was replaced")


# --------------------------------------------------------------------------- #
# The binary
# --------------------------------------------------------------------------- #

def answers(binary: Path) -> str:
    """What ``binary --version`` prints, or why it does not run. Its output goes to files, not
    pipes: on Windows a built binary runs as a child of its own, which a timeout does not kill —
    a pipe it holds open would keep the wait for its end from ending."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            code = subprocess.run([str(binary), "--version"], stdout=stdout, stderr=stderr, timeout=RUN_TIMEOUT,
                                  stdin=subprocess.DEVNULL, env=environment()).returncode
        except (OSError, subprocess.SubprocessError) as error:
            raise SelfUpdateError(f"the new binary does not run on this machine: {error} — nothing was replaced")
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read(MAX_OUTPUT).decode("utf-8", errors="replace").strip()
        if code != 0:
            why = stderr.read(MAX_OUTPUT).decode("utf-8", errors="replace").strip() or f"exit code {code}"
            raise SelfUpdateError(f"the new binary does not run on this machine: {why} — nothing was replaced")
    return output.splitlines()[0] if output else ""


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #

def beside(binary: Path, target: Version) -> Path:
    """A new, empty file beside ``binary``, named as ``binary.tidy`` knows a new binary — or the
    refusal of a directory this user cannot write in."""
    try:
        fd, name = tempfile.mkstemp(prefix=f".{binary.name}.new-", suffix=".exe" if os.name == "nt" else "",
                                    dir=binary.parent)
    except OSError as error:
        raise SelfUpdateError(f"cannot write in {display(str(binary.parent))} ({error.strerror or error}) — an "
                              "administrator or a package manager installed this cortex: update it the way it was "
                              f"installed, or install one of your own with:\n    {store.install_command(target)}")
    os.close(fd)
    return Path(name)


def self_update(wanted: Optional[str], dry_run: bool, out: TextIO, err: TextIO, *,
                binary: Optional[Path], own: Version, fetch: Callable[..., bytes] = net.get,
                run: Callable[[Path], str] = answers, built_for: Optional[str] = None,
                system: Optional[str] = None, machine: Optional[str] = None) -> int:
    if binary is None:
        raise SelfUpdateError("this cortex runs from a source checkout, not from a binary — there is none to "
                              f"replace. Install one with:\n    {store.install_command()}")
    # The build's own target, not the machine's: Windows on ARM runs the x86_64 build, and tells
    # the process it emulates that the machine is ARM64.
    asset = asset_of(built_for) if built_for else asset_name(system or platform.system(),
                                                              machine or platform.machine())
    base = store.releases_url()

    def from_where() -> None:
        # Integrity against the host the releases come from, not authenticity (ADR-010 §3.7):
        # another host than GitHub's says so, before it is downloaded from.
        if base != store.DEFAULT_RELEASES_URL:
            err.write(f"note: downloading from {base}, set by CORTEX_RELEASES_URL — the binary is checked against "
                      "that host's SHA256SUMS, not against GitHub's releases\n")

    sums: Optional[bytes] = None
    if wanted is None:
        from_where()
        try:
            target, sums = latest(fetch)
        except net.DownloadError as error:     # offline, or a mirror that serves no latest/
            raise SelfUpdateError(f"could not learn the latest release: {error} — name the one to install: "
                                  "cortex self-update X.Y.Z")
        if target is None:
            raise SelfUpdateError(f"{base} does not say which release is the latest — name the one to install: "
                                  "cortex self-update X.Y.Z")
        if target < own:
            out.write(f"cortex {own}: newer than the latest release, {target} — nothing to install. "
                      f"`cortex self-update {target}` installs it anyway\n")
            return 0
    else:
        target = Version(wanted)
    if target == own:
        out.write(f"cortex {own}: already on it — nothing to install\n")
        return 0
    if wanted is not None:
        from_where()

    # Beside the old binary, on its file system: the last step is a rename. A directory this user
    # cannot write in fails here, before the binary is downloaded — a bare self-update has read
    # the latest release's SHA256SUMS by then. The file is made again once the binary is had: a
    # start of another cortex leaves a new binary alone for an hour only (binary.STALE), and a
    # download may take longer.
    probe = beside(binary, target)
    try:
        probe.unlink()
    except OSError:     # held, by an antivirus say: empty, and removed by a start an hour later
        pass
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
    found = unpack(data, asset)
    new = beside(binary, target)
    try:
        new.write_bytes(found)
        os.chmod(new, 0o755)
        # A first start can take a while: macOS checks a new executable before it runs it.
        out.write(f"Running it once, to check it answers Cortex {target}\n")
        out.flush()
        answer = run(new)
        if answer != f"cortex {target}":
            raise SelfUpdateError(f"the binary of Cortex {target} answers {answer or 'nothing'!r} to --version, "
                                  f"not 'cortex {target}' — nothing was replaced")
        if dry_run:
            out.write(f"--dry-run: Cortex {target} downloaded, checked and run — {display(str(binary))} "
                      "was not replaced\n")
            return 0
        # From here on, nothing may be imported: a built binary reads its bundled modules from the
        # file it started from, by its path — which now names the new binary.
        replace(new, binary, ASIDE)
    finally:
        try:
            new.unlink(missing_ok=True)
        except OSError:     # still held, on Windows: a start an hour later removes it
            pass

    out.write(f"Installed cortex {target} as {display(str(binary))}, in place of {own}.\n")
    out.flush()             # before the notes: a pipe would otherwise show them first
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
                           binary=running_binary(), own=Version(VERSION), built_for=TARGET)
    except (SelfUpdateError, store.StoreError) as error:
        return sync.failed("cortex self-update", str(error))
    except OSError as error:
        return sync.failed("cortex self-update", sync.os_error(error))
