"""The store — ``~/.cortex/versions/X.Y.Z``, one read-only directory per version (ADR-008 §3.3).

A version directory holds the three trees the IDE's LLM and the runtime read — ``agents/``,
``templates/``, ``docs/`` — and nothing else. It is filled on demand: the binary's own version
from the spec archive it embeds, with no network; any other from its release's spec archive,
checked against the release's ``SHA256SUMS``. Once written it is read-only, so that an edit of
the spec, through any project, fails instead of changing every project on that version.

A version is written beside its final place and renamed into it: an interrupted write never
leaves a half-written version, and two syncs writing the same one at once both end with it. A
version is there only when its three trees are, and read-only: one left writable by an interrupted
sync is made read-only again, and a staging left behind for a day is removed.

A download is checked against ``SHA256SUMS``, which comes from where the archive does: that
proves it whole, not who published it. A built binary also carries the checksum of every spec
archive released before it, taken when it was built: a spec archive of those versions replaced
since — a release overwritten — is refused, whatever its ``SHA256SUMS`` says.

A project's ``cortex.lock`` names the digest of the spec it resolved (ADR-010 §3.2): one SHA-256
over the files of a version's three trees, the same however the version reached the store. A
version is removed — put in again, or pruned — by a rename out of its name first, so that a sync
running meanwhile finds it whole or not at all.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

from cortex_core.project import DEFAULT_RELEASES_URL  # noqa: F401 — part of this module's API

from . import machine, net
from .semver import FIRST_SPEC_ARCHIVE, Version
from .version import VERSION

SPEC_TREES = ("agents", "templates", "docs")
SPEC_ARCHIVE = "cortex-spec.tar.gz"
# The largest spec archive and SHA256SUMS a download accepts: a version is under a megabyte.
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_SUMS = 1024 * 1024


class StoreError(Exception):
    """A version the store cannot provide — the message says why, and what to do."""


def cortex_home() -> Path:
    """``$CORTEX_HOME``, or ``~/.cortex``."""
    home = os.environ.get("CORTEX_HOME")
    return Path(os.path.abspath(home)) if home else Path.home() / ".cortex"


def releases_url() -> str:
    """Where releases are downloaded from — the machine's ``releases_url`` (ADR-013 §3.7):
    ``$CORTEX_RELEASES_URL``, as for the install scripts, else ``config.toml`` in the Cortex home,
    else GitHub's. https, or http to this machine for tests: refused otherwise, naming where it is
    set."""
    found = machine.setting(cortex_home, "releases_url")
    url = str(found.value).rstrip("/")
    try:
        net.check_url(url)
    except net.DownloadError as error:
        raise StoreError(f"{found.origin}: {error}")
    return url


def releases_origin() -> str:
    """Where ``releases_url`` is set: the variable, the machine's file, or ``default``."""
    return machine.setting(cortex_home, "releases_url").origin


def known_specs() -> Dict[str, str]:
    """The checksum of every spec archive released before this binary was built — written by
    ``cli/build.py`` for the build. A source checkout knows none."""
    try:
        from ._known_specs import KNOWN
    except ImportError:
        return {}
    return dict(KNOWN)


def install_command(version: Optional[Version] = None) -> str:
    """The install script's command — of ``version``, or of the latest release."""
    if os.name == "nt":
        return ("& ([scriptblock]::Create((irm https://raw.githubusercontent.com/xod-labs/cortex/main/install.ps1)))"
                + (f" -Version {version}" if version else ""))
    return ("curl -fsSL https://raw.githubusercontent.com/xod-labs/cortex/main/install.sh | sh"
            + (f" -s -- {version}" if version else ""))


def listed_checksum(sums: str, name: str) -> Optional[str]:
    """The checksum a ``SHA256SUMS`` lists for ``name`` — in ``sha256sum``'s format, binary
    mode's ``*`` allowed — or ``None``."""
    for line in sums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == name:
            return fields[0].lower()
    return None


def own_version() -> Version:
    """This binary's version — what the version gate compares a project's with (ADR-013 §3.8): no
    store to build, so no home to find, for a command that never needed one."""
    return Version(VERSION)


def development(version: Version) -> bool:
    """A build of no release: a source checkout's ``X.Y.Z-dev``, or a CI build's ``X.Y.Z-dev.N`` —
    the version its branch prepares (ADR-009 §9, 2026-11-09). No teammate can download it: a
    project is moved to it only by naming it."""
    return version.pre[:1] == ("dev",) and len(version.pre) <= 2 and all(part.isdigit() for part in version.pre[1:])


def too_old_for(version: Version, own: Version) -> bool:
    """Whether a binary of version ``own`` is too old for a project of ``version``, as the commands say
    it before any key is checked (ADR-013 §3.8): ``version`` newer than ``own``. A build of no release
    is no exception: ``1.8.0-dev`` is older than 1.8.0, and newer than 1.7.0."""
    return version > own


def update_command(version: Version) -> str:
    """The command that moves this binary to ``version`` (ADR-010 §3.7) — none for a build of no
    release, which no self-update installs: the project is moved to a release instead (ADR-009 §9)."""
    if development(version):
        return f"cortex upgrade VERSION, to a release — no self-update installs Cortex {version}, a build of no release"
    return f"cortex self-update {version}"


def no_release(version: Version) -> str:
    """Why a build of no release's version, other than this cortex's own, is not served."""
    return f"Cortex {version} is a build of no release, which no release serves — pin a release: `cortex upgrade VERSION`"


# --------------------------------------------------------------------------- #
# The embedded spec
# --------------------------------------------------------------------------- #

def embedded_archive() -> Optional[Path]:
    """The spec archive a built binary carries — ``None`` for a source checkout."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / SPEC_ARCHIVE
    return None


def source_checkout() -> Optional[Path]:
    """The checkout a source run comes from: its spec is the one next to the code."""
    root = Path(__file__).resolve().parents[2]
    return root if all((root / tree).is_dir() for tree in SPEC_TREES) else None


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #

def make_read_only(root: Path) -> None:
    """Remove every write permission under ``root``, ``root`` included.

    POSIX: files ``0444``, directories ``0555`` — nothing is modified, created or removed. On
    Windows the read-only attribute protects files, not directories: a file of the spec cannot
    be modified or deleted, but a new one can be created beside it (ADR-008 §9).
    """
    for directory, subdirs, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(os.path.join(directory, name), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        if os.name != "nt":
            os.chmod(directory, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def _a_file_writable(root: Path) -> bool:
    return any(os.stat(os.path.join(directory, name)).st_mode & stat.S_IWUSR
               for directory, _, files in os.walk(root) for name in files)


def make_writable(root: Path) -> None:
    if os.name != "nt":
        os.chmod(root, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    for directory, subdirs, files in os.walk(root):
        for name in subdirs:
            if os.name != "nt":
                os.chmod(os.path.join(directory, name), stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        for name in files:
            os.chmod(os.path.join(directory, name), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def remove_tree(root: Path) -> None:
    """Remove a tree the store, or sync, made read-only."""
    make_writable(root)
    shutil.rmtree(root)


def hashes(directory: Path) -> Dict[str, str]:
    """The SHA-256 of each file of the spec at ``directory``, its three trees, by path with ``/``."""
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _files(directory)}


def digest(directory: Path) -> str:
    """The digest of the spec at ``directory`` (ADR-010 §3.2): ``sha256sum``'s line for each file of
    its three trees — the path relative to ``directory``, with ``/`` — in the order of the paths'
    UTF-8 bytes, then the SHA-256 of those lines. The files' bytes only: the same version gives
    the same digest, downloaded or written from this binary, on any system. A tree missing — a
    version removed meanwhile — has no digest: the empty set's would be locked."""
    missing = [f"{tree}/" for tree in SPEC_TREES if not (directory / tree).is_dir()]
    if missing:
        raise StoreError(f"{directory} holds no {', '.join(missing)} — no whole spec to take the digest of")
    try:
        return digest_of(hashes(directory))
    except FileNotFoundError as error:
        raise StoreError(f"{directory} lost {error.filename} while its digest was taken — no whole spec to take the "
                         "digest of")


def _files(directory: Path) -> Iterator[Path]:
    """The regular files of the spec's three trees at ``directory``. A file or a tree that goes while
    they are walked — a version removed meanwhile — is an error, which ``Path.rglob`` kept silent."""
    def gone(error: OSError) -> None:
        raise error

    for tree in SPEC_TREES:
        for parent, _, names in os.walk(directory / tree, onerror=gone):
            for name in names:
                path = Path(parent) / name
                try:
                    regular = stat.S_ISREG(os.stat(path).st_mode)
                except FileNotFoundError:
                    if os.path.lexists(path):       # a link to nothing: no file of the spec
                        continue
                    raise
                if regular:
                    yield path


def digest_of(files: Dict[str, str]) -> str:
    """The digest of a spec from the SHA-256 of each of its files, by path — as a copy's manifest
    holds them. A path outside the three trees is not the spec's."""
    total = hashlib.sha256()
    for rel, sha in sorted((rel.encode("utf-8"), sha) for rel, sha in files.items() if rel.split("/")[0] in SPEC_TREES):
        total.update(sha.encode("ascii") + b"  " + rel + b"\n")
    return f"sha256:{total.hexdigest()}"


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #

class Store:
    def __init__(self, home: Optional[Path] = None, own_version: str = VERSION,
                 embedded: Optional[Path] = None, checkout: Optional[Path] = None,
                 fetch: Callable[[str, int], bytes] = net.get, known: Optional[Dict[str, str]] = None):
        self.home = home or cortex_home()
        self.versions = self.home / "versions"
        self.own = Version(own_version)
        self.embedded = embedded if embedded is not None else embedded_archive()
        self.checkout = checkout if checkout is not None or self.embedded else source_checkout()
        self.fetch = fetch
        self.known = known_specs() if known is None else known

    def path(self, version: Version) -> Path:
        return self.versions / str(version)

    def digest(self, version: Version) -> str:
        """The digest of ``version`` in the store — refused when it left the store while it was
        read: a prune meanwhile, and the files read are some of its own, or none (ADR-010 §3.6).
        Refused, not read again: a command that runs again ensures it first."""
        removed = StoreError(f"Cortex {version} left the store while this command read it — a `cortex prune` "
                             "meanwhile, say. Nothing of the project was changed: run the command again")
        try:
            found = digest(self.path(version))
        except FileNotFoundError:
            raise removed
        except StoreError:
            if not self.has(version):       # gone before it was read: no tree to read
                raise removed
            raise
        if not self.has(version):
            raise removed
        return found

    def _aside(self, version: Version, why: str = "removed") -> Path:
        """Rename ``version`` out of its name — in the same directory, so that a read-only one moves
        too: a sync meanwhile finds it whole or absent. Dated now, so that no other sync takes it
        for a staging a day old. ``why``: ``removed``, on its way out; ``kept``, whole, while
        ``replace`` has the version again."""
        aside = self.versions / f".{version}-{why}-{os.getpid()}-{time.time_ns()}"
        os.rename(self.path(version), aside)
        try:
            os.utime(aside)
        except OSError:
            pass
        return aside

    @staticmethod
    def _discard(aside: Path) -> None:
        try:
            remove_tree(aside)
        except FileNotFoundError:   # another sync's tidying took it first
            pass

    def remove(self, version: Version) -> None:
        """Take ``version`` out of the store. A removal cut short leaves a staging, which the store
        removes a day later."""
        self._discard(self._aside(version))

    def replace(self, version: Version) -> str:
        """Put ``version`` in the store again, as ``ensure`` would — its files are not those a
        project locked. Until the new one is in place the old one is kept aside, and put back if
        the new one cannot be had: offline, a download refused."""
        if not self.path(version).exists():
            return self.ensure(version)
        aside = self._aside(version, "kept")
        try:
            how = self.ensure(version, put_back=False)
        except BaseException:
            if not self.path(version).exists() and aside.exists():
                os.rename(aside, self.path(version))
            raise
        # Renamed before it is removed: what is named "kept" is always whole.
        removed = aside.with_name(aside.name.replace("-kept-", "-removed-", 1))
        try:
            os.rename(aside, removed)
        except FileNotFoundError:   # another sync put it back meanwhile, and found this one in place
            return how
        self._discard(removed)
        return how

    def _put_back(self, version: Version) -> bool:
        """Put back what a ``replace`` cut short left aside, whole — the latest it left: the store
        as it was before that replace. ``False`` when there is none."""
        def when(aside: Path) -> int:
            stamp = aside.name.rsplit("-", 1)[-1]
            return int(stamp) if stamp.isdigit() else 0

        for aside in sorted(self.versions.glob(f".{version}-kept-*"), key=when, reverse=True):
            try:
                os.rename(aside, self.path(version))
            except OSError:
                if self.has(version):   # another sync put it back first
                    return True
                continue
            return True
        return False

    def has(self, version: Version) -> bool:
        """A version is there when its three trees are — an empty directory is none."""
        return all((self.path(version) / tree).is_dir() for tree in SPEC_TREES)

    def _tidy(self, version: Version) -> None:
        """Make a stored version read-only again — a sync interrupted between the rename and the
        chmod left it writable — and remove the stagings older than a day. Every file tells, on
        every target — the chmod runs from the deepest files up, so a sync cut short leaves any of
        them writable — and a directory too, but on Windows."""
        path = self.path(version)
        if (os.name != "nt" and os.stat(path).st_mode & stat.S_IWUSR) or _a_file_writable(path):
            make_read_only(path)
        cutoff = time.time() - 86400
        for entry in self.versions.glob(".*"):
            try:
                if entry.stat().st_mtime < cutoff:
                    remove_tree(entry) if entry.is_dir() else entry.unlink()
            except OSError:
                pass

    def check_served(self, version: Version) -> None:
        """Refuse a version this binary does not serve: a build of no release's, but its own or one
        the store holds — no release serves it (ADR-009 §9); newer than itself — the tool is newer
        than the data it reads, never older; or older than the first spec archive."""
        if development(version) and version != self.own and not self.has(version):
            raise StoreError(no_release(version))
        if version > self.own:
            raise StoreError(f"this project uses Cortex {version}, newer than this cortex, {self.own} — update it:\n"
                             f"    {update_command(version)}")
        if version != self.own and version < FIRST_SPEC_ARCHIVE:
            raise StoreError(f"Cortex {version} has no spec archive: the first one is {FIRST_SPEC_ARCHIVE}, "
                             "the release that ships the cortex command — pin that version or a later one")

    def ensure(self, version: Version, put_back: bool = True) -> str:
        """Make sure ``version`` is in the store; say how it got there — ``"stored"``,
        ``"written"`` from this binary, ``"downloaded"``, or ``"put back"``: it could not be
        downloaded, and a ``replace`` cut short had left it aside."""
        self.check_served(version)
        if self.has(version):
            try:
                self._tidy(version)
            except FileNotFoundError:
                pass
            if self.has(version):   # still: not removed meanwhile — pruned, or put in again — by another
                return "stored"
        if self.path(version).exists():
            raise StoreError(f"{self.path(version)} is there, and holds no complete spec — remove it, "
                             "then run the command again")
        self.versions.mkdir(parents=True, exist_ok=True)
        if version == self.own:
            if self.embedded is not None:
                self._install(version, lambda staging: _extract(self.embedded, staging))
            elif self.checkout is not None:
                self._install(version, lambda staging: _copy_trees(self.checkout, staging))
            else:
                raise StoreError(f"this cortex carries no spec of its own version, {version}")
            return "written"
        try:
            self._download(version)
        except StoreError:
            if put_back and self._put_back(version):
                self._tidy(version)
                return "put back"
            raise
        return "downloaded"

    def _download(self, version: Version) -> None:
        base = f"{releases_url()}/download/{version}"
        try:
            sums = self.fetch(f"{base}/SHA256SUMS", MAX_SUMS).decode("utf-8")
        except net.DownloadError as error:
            raise StoreError(f"could not download the SHA256SUMS of Cortex {version}: {error}")
        expected = listed_checksum(sums, SPEC_ARCHIVE)
        if expected is None:
            raise StoreError(f"the SHA256SUMS of Cortex {version} lists no {SPEC_ARCHIVE} — nothing was written")
        known = self.known.get(str(version))
        if known is not None and known != expected:
            raise StoreError(f"the SHA256SUMS of Cortex {version} names {expected}, and this cortex knows its spec "
                             f"archive as {known}: the release was changed since this binary was built. "
                             "Nothing was written.")
        try:
            data = self.fetch(f"{base}/{SPEC_ARCHIVE}", MAX_ARCHIVE)
        except net.DownloadError as error:
            raise StoreError(f"could not download the spec of Cortex {version}: {error}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise StoreError(f"the spec of Cortex {version} does not match its checksum — expected {expected}, "
                             f"got {actual}. Nothing was written.")
        # Checked before anything is extracted, then extracted from the bytes that were checked.
        fd, archive = tempfile.mkstemp(prefix=".download-", dir=self.versions)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            self._install(version, lambda staging: _extract(Path(archive), staging))
        finally:
            os.unlink(archive)

    def _install(self, version: Version, fill: Callable[[Path], None]) -> None:
        staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=self.versions))
        try:
            fill(staging)
            found = sorted(entry.name for entry in staging.iterdir())
            if found != sorted(SPEC_TREES):
                raise StoreError(f"the spec of Cortex {version} holds {', '.join(found) or 'nothing'}, "
                                 f"not {', '.join(SPEC_TREES)} — nothing was written")
            try:
                os.rename(staging, self.path(version))
            except OSError:
                if not self.has(version):
                    raise
                # Another sync wrote it meanwhile: the same version, the same content.
                remove_tree(staging)
                return
        except BaseException:
            if staging.exists():
                remove_tree(staging)
            raise
        make_read_only(self.path(version))


def _copy_trees(source: Path, staging: Path) -> None:
    for tree in SPEC_TREES:
        # A checkout's theme marker is its developer's choice, never the spec's: a copy leaves it out too.
        shutil.copytree(source / tree, staging / tree, ignore=shutil.ignore_patterns(".active-*"))


def _extract(archive: Path, staging: Path) -> None:
    """Extract a spec archive — regular files and directories only, all of them inside
    ``staging``: no absolute path, no ``..``, no link, no device."""
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            parts = member.name.split("/")
            if member.name.startswith("/") or ".." in parts or not (member.isfile() or member.isdir()):
                raise StoreError(f"{archive.name} holds {member.name!r}, which a spec archive cannot hold — "
                                 "nothing was written")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(staging, members=members, filter="data")
        else:                                  # Python 3.11 before 3.11.4: checked above
            tar.extractall(staging, members=members)
