"""``cortex self-update`` — this binary replaced by a release's (ADR-010 §3.7).

In process, from source: the releases are ``release_fixture``'s, served from this machine, and the
binary replaced is a file in a temporary directory. A release's stand-in answers ``--version`` on
POSIX only, so these tests read what it would answer from its last line, and the POSIX ones run it.

Through a built binary, a copy of it is replaced — never the binary under test. On POSIX the copy
installs the stand-in, which then answers for itself; on Windows the stand-in answers nothing
sensible, so the copy refuses it — and installs instead a program built from pip's own launcher,
which answers, while the copy runs.
"""

import importlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
from cortex_cli import binary, machine, net, self_update, store, version  # noqa: E402
from cortex_cli.paths import display  # noqa: E402
from cortex_cli.self_update import SelfUpdateError  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import TARGETS, FakeRelease, build, stand_in  # noqa: E402

LINUX = ("Linux", "x86_64")
WINDOWS = ("Windows", "AMD64")
OLD = b"the binary before\n"
TARGET = "77.7.7"
POSIX = unittest.skipIf(os.name == "nt", "a release's stand-in runs on POSIX only")
SOURCE = unittest.skipIf(harness.BINARY, "from source")
BUILT = unittest.skipUnless(harness.BINARY, "through a built binary")


def aged(*paths):
    """Make ``paths`` older than a self-update at work: two hours old."""
    for path in paths:
        then = time.time() - 2 * binary.STALE
        os.utime(path, (then, then))


def zip_of_cortex_exe(compression=zipfile.ZIP_STORED, flags=None, method=None, data=None):
    """A Windows asset whose cortex.exe — its first member — is altered after the fact: its
    general purpose flags, its compression method, or its stored bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("cortex.exe"), bytes(range(256)) * 4, compress_type=compression)
    raw = bytearray(buffer.getvalue())
    local, central = raw.index(b"PK\x03\x04"), raw.index(b"PK\x01\x02")
    if flags is not None:
        raw[local + 6:local + 8] = raw[central + 8:central + 10] = flags.to_bytes(2, "little")
    if method is not None:
        raw[local + 8:local + 10] = raw[central + 10:central + 12] = method.to_bytes(2, "little")
    if data is not None:
        start = local + 30 + len("cortex.exe")
        raw[start:start + len(data)] = data
    return bytes(raw)


class Piped:
    """stdout as a pipe holds it — what is written reaches the reader once flushed — and stderr,
    unbuffered, both read in the order they arrive."""

    def __init__(self):
        self.read, self.held = [], []
        self.out = mock.Mock(write=self.held.append, flush=self.flush)
        self.err = mock.Mock(write=self.read.append)

    def flush(self):
        self.read.append("".join(self.held))
        self.held.clear()


def answering_windows_program(version):
    """A Windows program that answers ``cortex VERSION``: pip's launcher, then the line naming the
    Python to run, then a zip of the script — how pip writes its commands on Windows. ``None``
    where pip ships no launcher."""
    try:
        import pip._vendor.distlib as distlib
    except ImportError:
        return None
    launcher = Path(distlib.__file__).parent / "t64.exe"
    if not launcher.is_file():
        return None
    script = io.BytesIO()
    with zipfile.ZipFile(script, "w") as archive:
        archive.writestr("__main__.py", f"print('cortex {version}')\n")
    return launcher.read_bytes() + f'#!"{sys.executable}"\n'.encode() + script.getvalue()


def answer_of(path):
    """What a release's stand-in answers to ``--version``: the version it was written with."""
    return "cortex " + re.findall(rb"cortex (\d+\.\d+\.\d+)", path.read_bytes())[-1].decode()


class ReleaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-self-update-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.binary = self.bin / "cortex"
        self.binary.write_bytes(OLD)
        self.release = FakeRelease(("9.9.8", "9.9.9"))
        self.addCleanup(self.release.close)
        patcher = mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": self.release.url})
        patcher.start()
        self.addCleanup(patcher.stop)

    def latest_is(self, version):
        self.release.redirect("/latest/download/SHA256SUMS", f"{self.release.url}/download/{version}/SHA256SUMS")

    def update(self, wanted=None, dry_run=False, own="9.9.8", system=LINUX, run=answer_of, binary=..., built_for=None):
        out, err = io.StringIO(), io.StringIO()
        code = self_update.self_update(wanted, dry_run, out, err, binary=self.binary if binary is ... else binary,
                                       own=Version(own), run=run, built_for=built_for, system=system[0],
                                       machine=system[1])
        return code, out.getvalue(), err.getvalue()

    def assert_untouched(self):
        self.assertEqual(self.binary.read_bytes(), OLD)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), ["cortex"])


class SelfUpdateTests(ReleaseTestCase):
    @property
    def ELSEWHERE(self):
        return (f"note: downloading from {self.release.url}, set by CORTEX_RELEASES_URL — the binary is checked "
                "against that host's SHA256SUMS, not against GitHub's releases\n")

    def test_releases_from_another_host_say_so_before_anything_is_downloaded(self):
        for wanted in ("9.9.9", None):
            with self.subTest(wanted=wanted):
                self.latest_is("9.9.9")
                piped = Piped()
                self_update.self_update(wanted, True, piped.out, piped.err, binary=self.binary, own=Version("9.9.8"),
                                        run=answer_of, system="Linux", machine="x86_64")
                piped.flush()
                self.assertEqual(piped.read[0], self.ELSEWHERE)
        self.assertEqual(self.update("9.9.8")[2], "")       # nothing downloaded: nothing to say

    def test_the_machine_s_file_names_the_host_and_its_other_keys_only_warn(self):
        # ADR-013 §3.7: config.toml in the Cortex home, read through a key this cortex does not know.
        machine_file = Path(os.environ["CORTEX_HOME"]) / "config.toml"
        machine_file.parent.mkdir(parents=True, exist_ok=True)
        machine_file.write_text(f'releases_url = "{self.release.url}"\nself_update_later = true\n', encoding="utf-8")
        self.addCleanup(machine_file.unlink)
        self.latest_is("9.9.9")
        warned = io.StringIO()
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": ""}), mock.patch("sys.stderr", warned):
            code, out, err = self.update("9.9.9", dry_run=True)
        self.assertEqual(code, 0, err)
        self.assertIn(f"note: downloading from {self.release.url}, set by {display(str(machine_file))} — ", err)
        self.assertIn('unknown key "self_update_later", not read', warned.getvalue())

    def test_a_releases_url_that_does_not_read_warns_and_the_download_goes_to_github(self):
        machine_file = Path(os.environ["CORTEX_HOME"]) / "config.toml"
        machine_file.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: machine_file.exists() and machine_file.unlink())
        for text, said in (("releases_url = [\n", "is not read"), ("releases_url = 3\n", '"releases_url" must be a string')):
            with self.subTest(text=text):
                machine_file.write_text(text, encoding="utf-8")
                asked = []

                def fetch(url, *args, **kwargs):
                    asked.append(url)
                    raise net.DownloadError("offline, in this test")

                warned = io.StringIO()
                machine._warned.clear()
                with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": ""}), mock.patch("sys.stderr", warned), \
                        self.assertRaisesRegex(SelfUpdateError, "offline, in this test"):
                    self_update.self_update("9.9.9", True, io.StringIO(), io.StringIO(), binary=self.binary,
                                            own=Version("9.9.8"), fetch=fetch, run=answer_of, system="Linux",
                                            machine="x86_64")
                self.assertTrue(asked and asked[0].startswith(store.DEFAULT_RELEASES_URL + "/"), asked)
                self.assertIn(said, warned.getvalue())

    def test_the_host_is_said_before_the_first_request_to_it(self):
        for wanted in ("9.9.9", None):
            with self.subTest(wanted=wanted):
                self.latest_is("9.9.9")
                events = []

                def fetch(url, *args, **kwargs):
                    events.append("fetched")
                    return net.get(url, *args, **kwargs)

                err = mock.Mock(write=lambda text: events.append(text))
                self_update.self_update(wanted, True, io.StringIO(), err, binary=self.binary, own=Version("9.9.8"),
                                        fetch=fetch, run=answer_of, system="Linux", machine="x86_64")
                self.assertEqual(events[:2], [self.ELSEWHERE, "fetched"])

    def test_githubs_releases_say_nothing_of_where_they_come_from(self):
        os.environ.pop("CORTEX_RELEASES_URL")
        for wanted in ("9.9.9", None):
            with self.subTest(wanted=wanted):
                err = io.StringIO()
                with self.assertRaises(SelfUpdateError):
                    self_update.self_update(wanted, False, io.StringIO(), err, binary=self.binary, own=Version("9.9.8"),
                                            fetch=mock.Mock(side_effect=net.DownloadError("offline")), run=answer_of,
                                            system="Linux", machine="x86_64")
                self.assertEqual(err.getvalue(), "")

    def test_the_latest_release_takes_the_binarys_place(self):
        self.latest_is("9.9.9")
        code, out, _ = self.update()
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.9", False))
        binary.tidy(self.binary)            # on Windows, the old binary renamed aside
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), ["cortex"])
        self.assertIn(f"Installed cortex 9.9.9 as {display(str(self.binary))}, in place of 9.9.8.", out)
        self.assertIn("No project moved: `cortex upgrade` moves one to this version.", out)
        # The SHA256SUMS the redirect led to is the release's: it is not asked for twice.
        self.assertEqual(self.release.requested, ["/latest/download/SHA256SUMS", "/download/9.9.9/SHA256SUMS",
                                                  "/download/9.9.9/cortex-linux-x86_64.tar.gz"])

    @unittest.skipIf(os.name == "nt", "a mode Windows does not keep")
    def test_the_binary_installed_is_run_by_all_and_written_by_its_owner(self):
        self.assertEqual(self.update("9.9.9")[0], 0)
        self.assertEqual(self.binary.stat().st_mode & 0o777, 0o755)

    def test_a_version_named_is_the_one_installed(self):
        self.latest_is("9.9.9")
        code, _, err = self.update("9.9.9", own="9.9.7")
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.9", False))
        self.assertEqual(self.release.requested, ["/download/9.9.9/SHA256SUMS",
                                                  "/download/9.9.9/cortex-linux-x86_64.tar.gz"])
        self.assertEqual(err, self.ELSEWHERE)

    def test_the_windows_asset_gives_its_cortex_exe(self):
        code, _, _ = self.update("9.9.9", system=WINDOWS)
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.9", True))
        self.assertIn("/download/9.9.9/cortex-windows-x86_64.zip", self.release.requested)

    def test_a_build_downloads_the_asset_of_its_own_target(self):
        """Windows on ARM runs the x86_64 build, and says ARM64 to the process it emulates."""
        code, _, _ = self.update("9.9.9", system=("Windows", "ARM64"), built_for="windows-x86_64")
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.9", True))
        self.assertIn("/download/9.9.9/cortex-windows-x86_64.zip", self.release.requested)

    def test_on_its_own_version_nothing_is_downloaded(self):
        code, out, _ = self.update("9.9.8")
        self.assertEqual(code, 0)
        self.assertIn("cortex 9.9.8: already on it — nothing to install", out)
        self.assertEqual(self.release.requested, [])
        self.latest_is("9.9.8")
        self.assertEqual(self.update()[0], 0)
        self.assertFalse([path for path in self.release.requested if "cortex-" in path])
        self.assert_untouched()

    def test_a_latest_release_older_than_the_binary_is_not_installed_unless_named(self):
        self.latest_is("9.9.8")
        code, out, _ = self.update(own="9.9.9")
        self.assertEqual(code, 0)
        self.assertIn("cortex 9.9.9: newer than the latest release, 9.9.8 — nothing to install. "
                      "`cortex self-update 9.9.8` installs it anyway", out)
        self.assertFalse([path for path in self.release.requested if "cortex-" in path])
        self.assert_untouched()

    def test_a_downgrade_says_so(self):
        code, _, err = self.update("9.9.8", own="9.9.9")
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.8", False))
        self.assertIn("note: a downgrade — a project pinned to a version after 9.9.8 is now refused", err)
        self.assertNotIn("self-update", err)

    def test_a_downgrade_to_a_binary_without_self_update_names_the_install_script(self):
        FakeRelease.publish(self.release.root / "download" / "1.3.2", "1.3.2")
        code, _, err = self.update("1.3.2", own="9.9.9")
        self.assertEqual(code, 0)
        self.assertIn("note: Cortex 1.3.2 has no self-update — the install script moves it again:", err)
        self.assertIn("install.ps1" if os.name == "nt" else "install.sh | sh\n", err)

    def test_on_windows_a_downgrade_to_a_binary_without_self_update_says_the_old_one_stays(self):
        FakeRelease.publish(self.release.root / "download" / "1.3.2", "1.3.2")
        old = self.bin / "cortex.old"
        with mock.patch.object(self_update, "ASIDE", True):
            code, _, err = self.update("1.3.2", own="9.9.9")
        self.assertEqual(code, 0)
        self.assertEqual(old.read_bytes(), OLD)
        self.assertIn(f"note: {display(str(old))}, the binary this command replaced, stays — Cortex 1.3.2 does not "
                      "remove it: delete it once this command has ended", err)
        with mock.patch.object(self_update, "ASIDE", False):    # nothing renamed aside: nothing to say
            self.assertNotIn("stays", self.update("1.3.2", own="9.9.9")[2])
        with mock.patch.object(self_update, "ASIDE", True):     # 1.4.0 and later remove it themselves
            self.assertNotIn("stays", self.update("9.9.8", own="9.9.9")[2])

    def test_a_download_longer_than_an_hour_loses_nothing_to_another_start(self):
        """The new binary's file is made once the binary is had: another cortex, started while the
        download runs, finds nothing of this self-update an hour old."""
        def slow(url, *args, **kwargs):
            data = net.get(url, *args, **kwargs)
            if url.endswith(".tar.gz"):
                aged(*self.bin.glob(".cortex.new-*"))      # more than an hour since the self-update began
                binary.tidy(self.binary, aside=False)       # another cortex starts
            return data

        code = self_update.self_update("9.9.9", False, io.StringIO(), io.StringIO(), binary=self.binary,
                                       own=Version("9.9.8"), fetch=slow, run=answer_of, system="Linux",
                                       machine="x86_64")
        self.assertEqual(code, 0)
        self.assertEqual(self.binary.read_bytes(), stand_in("9.9.9", False))

    def test_releases_that_do_not_say_the_latest_ask_for_a_version(self):
        with self.assertRaisesRegex(SelfUpdateError, "does not say which release is the latest — name the one to "
                                                     "install: cortex self-update X.Y.Z"):
            self.update()
        self.assert_untouched()

    def test_releases_without_a_latest_ask_for_a_version(self):
        shutil.rmtree(self.release.root / "latest")         # a mirror of download/ alone
        with self.assertRaisesRegex(SelfUpdateError, "could not learn the latest release: .*HTTP 404.* — name the one "
                                                     "to install: cortex self-update X.Y.Z"):
            self.update()
        self.assert_untouched()

    def test_a_version_that_is_not_released_replaces_nothing(self):
        with self.assertRaisesRegex(SelfUpdateError, "could not download Cortex 9.9.7: .*HTTP 404"):
            self.update("9.9.7", own="9.9.9")
        self.assert_untouched()

    def test_a_checksum_mismatch_replaces_nothing(self):
        self.release.alter_one_byte("linux-x86_64", "9.9.9")
        with self.assertRaisesRegex(SelfUpdateError, "cortex-linux-x86_64.tar.gz does not match its checksum — "
                                                     "expected [0-9a-f]{64}, got [0-9a-f]{64}. Nothing was replaced."):
            self.update("9.9.9")
        self.assert_untouched()

    def test_sums_that_list_no_asset_of_this_system_replace_nothing(self):
        sums = self.release.root / "download" / "9.9.9" / "SHA256SUMS"
        sums.write_text("".join(line for line in sums.read_text().splitlines(True) if "linux-x86_64" not in line))
        with self.assertRaisesRegex(SelfUpdateError, "the SHA256SUMS of Cortex 9.9.9 lists no "
                                                     "cortex-linux-x86_64.tar.gz — nothing was replaced"):
            self.update("9.9.9")
        self.assert_untouched()

    def test_an_asset_that_holds_no_binary_replaces_nothing(self):
        directory = self.release.root / "download" / "9.9.9"
        with tarfile.open(directory / "cortex-linux-x86_64.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("LICENSE")
            info.size = 8
            archive.addfile(info, io.BytesIO(b"licence\n"))
        build.checksums(directory)
        with self.assertRaisesRegex(SelfUpdateError, "cortex-linux-x86_64.tar.gz holds no cortex — nothing was replaced"):
            self.update("9.9.9")
        self.assert_untouched()

    def test_an_asset_that_is_no_archive_replaces_nothing(self):
        directory = self.release.root / "download" / "9.9.9"
        (directory / "cortex-windows-x86_64.zip").write_bytes(b"no zip at all")
        build.checksums(directory)
        with self.assertRaisesRegex(SelfUpdateError, "cortex-windows-x86_64.zip cannot be read: .* — nothing was replaced"):
            self.update("9.9.9", system=WINDOWS)
        self.assert_untouched()

    def test_a_zip_that_cannot_be_inflated_replaces_nothing(self):
        for why, data in (("encrypted", zip_of_cortex_exe(flags=0x1)),
                          ("an unknown compression", zip_of_cortex_exe(method=99)),
                          ("deflate data that is none", zip_of_cortex_exe(zipfile.ZIP_DEFLATED, data=b"\xff" * 8))):
            with self.subTest(why=why):
                with self.assertRaisesRegex(SelfUpdateError, "cortex-windows-x86_64.zip cannot be read: .* — "
                                                             "nothing was replaced"):
                    self_update.unpack(data, "cortex-windows-x86_64.zip")

    def test_the_notes_come_after_what_was_installed(self):
        piped = Piped()
        code = self_update.self_update("9.9.8", False, piped.out, piped.err, binary=self.binary, own=Version("9.9.9"),
                                       run=answer_of, system="Linux", machine="x86_64")
        piped.flush()
        self.assertEqual(code, 0)
        said = "".join(piped.read)
        self.assertLess(said.index("Installed cortex 9.9.8"), said.index("note: a downgrade"))

    def test_a_binary_that_answers_another_version_replaces_nothing(self):
        with self.assertRaisesRegex(SelfUpdateError, "the binary of Cortex 9.9.9 answers 'cortex 9.9.7' to --version, "
                                                     "not 'cortex 9.9.9' — nothing was replaced"):
            self.update("9.9.9", run=lambda path: "cortex 9.9.7")
        self.assert_untouched()

    def test_dry_run_replaces_nothing(self):
        code, out, _ = self.update("9.9.9", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn(f"--dry-run: Cortex 9.9.9 downloaded, checked and run — {display(str(self.binary))} was not replaced", out)
        self.assertIn("/download/9.9.9/cortex-linux-x86_64.tar.gz", self.release.requested)
        self.assert_untouched()

    def test_a_new_binary_still_held_after_its_run_does_not_hide_the_result(self):
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("held by an antivirus")):
            code, out, _ = self.update("9.9.9", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("--dry-run: Cortex 9.9.9 downloaded, checked and run", out)
        # The new binary, and the empty file that found the directory writable: both held, both left.
        self.assertEqual(len(list(self.bin.glob(".cortex.new-*"))), 2)
        aged(*self.bin.glob(".cortex.new-*"))
        binary.tidy(self.binary)
        self.assert_untouched()

    def test_a_source_checkout_is_refused_before_anything_is_downloaded(self):
        with self.assertRaisesRegex(SelfUpdateError, "runs from a source checkout, not from a binary — there is none "
                                                     "to replace. Install one with:\n    "
                                                     + re.escape(store.install_command()) + "$"):
            self.update(binary=None)
        self.assertEqual(self.release.requested, [])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "a directory root, or Windows, can still write in")
    def test_a_directory_this_user_cannot_write_in_is_refused_before_anything_is_downloaded(self):
        self.bin.chmod(0o555)
        self.addCleanup(self.bin.chmod, 0o755)
        with self.assertRaisesRegex(SelfUpdateError, "cannot write in .* — an administrator or a package manager "
                                                     "installed this cortex: update it the way it was installed, or "
                                                     "install one of your own with:\n    "
                                                     + re.escape(store.install_command(Version("9.9.9"))) + "$"):
            self.update("9.9.9")
        self.assertEqual(self.release.requested, [])
        self.assertEqual(self.binary.read_bytes(), OLD)

    def test_a_directory_refused_names_the_install_script_of_the_version(self):
        with mock.patch.object(self_update.tempfile, "mkstemp", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(SelfUpdateError, "cannot write in .* \\(Permission denied\\) — .* install one "
                                                         "of your own with:\n    "
                                                         + re.escape(store.install_command(Version("9.9.9"))) + "$"):
                self.update("9.9.9")
        self.assertEqual(self.release.requested, [])

    @POSIX
    def test_the_new_binary_is_run_before_it_is_installed(self):
        code, _, _ = self.update("9.9.9", run=self_update.answers)
        self.assertEqual(code, 0)
        self.assertEqual(subprocess.run([str(self.binary), "--version"], capture_output=True).stdout, b"cortex 9.9.9\n")

    @POSIX
    def test_a_binary_that_hangs_is_given_up_on_though_a_child_keeps_its_output(self):
        """The timeout ends the wait, a child of the binary still running — as a built binary's
        does on Windows."""
        hangs = self.tmp / "hangs"
        hangs.write_bytes(b"#!/bin/sh\nsleep 30 &\nsleep 30\n")
        hangs.chmod(0o755)
        started = time.monotonic()
        with mock.patch.object(self_update, "RUN_TIMEOUT", 1):
            with self.assertRaisesRegex(SelfUpdateError, "does not run on this machine: .*timed out"):
                self_update.answers(hangs)
        self.assertLess(time.monotonic() - started, 15)

    @POSIX
    def test_a_binary_that_does_not_run_replaces_nothing(self):
        self.release.replace_binary("linux-x86_64", b"#!/bin/sh\necho 'no such glibc' >&2\nexit 3\n", "9.9.9")
        with self.assertRaisesRegex(SelfUpdateError, "the new binary does not run on this machine: no such glibc — "
                                                     "nothing was replaced"):
            self.update("9.9.9", run=self_update.answers)
        self.assert_untouched()


class AssetTests(unittest.TestCase):
    def test_each_target_has_the_install_scripts_asset(self):
        for (system, machine), asset in ((("Linux", "x86_64"), "cortex-linux-x86_64.tar.gz"),
                                         (("Linux", "amd64"), "cortex-linux-x86_64.tar.gz"),
                                         (("Linux", "aarch64"), "cortex-linux-aarch64.tar.gz"),
                                         (("Linux", "arm64"), "cortex-linux-aarch64.tar.gz"),
                                         (("Darwin", "arm64"), "cortex-macos-arm64.tar.gz"),
                                         (("Windows", "AMD64"), "cortex-windows-x86_64.zip")):
            with self.subTest(system=system, machine=machine):
                self.assertEqual(self_update.asset_name(system, machine), asset)
        self.assertEqual({self_update.asset_name(*pair) for pair in
                          (("Linux", "x86_64"), ("Linux", "aarch64"), ("Darwin", "arm64"), ("Windows", "AMD64"))},
                         {f"cortex-{t}.{'zip' if t.startswith('windows') else 'tar.gz'}" for t in TARGETS})

    def test_each_build_target_has_its_asset(self):
        self.assertEqual({self_update.asset_of(t) for t in TARGETS},
                         {f"cortex-{t}.{'zip' if t.startswith('windows') else 'tar.gz'}" for t in TARGETS})

    def test_a_system_without_a_build_is_refused(self):
        for system, machine in (("Darwin", "x86_64"), ("Linux", "riscv64"), ("FreeBSD", "amd64"), ("Windows", "ARM64")):
            with self.subTest(system=system, machine=machine):
                with self.assertRaisesRegex(SelfUpdateError, f"no build for {system.lower()} on {machine.lower()}"):
                    self_update.asset_name(system, machine)


class StampTests(ReleaseTestCase):
    """The asset is the one of the target stamped in the build (cli/build.py), not the machine's."""

    def test_the_command_downloads_the_asset_of_the_target_stamped_in_it(self):
        """Windows on Arm runs the x86_64 build, and tells the process it emulates ARM64."""
        def answered(args, **kwargs):
            kwargs["stdout"].write(b"cortex 9.9.9\n")
            return subprocess.CompletedProcess(args, 0)

        with mock.patch.object(self_update, "running_binary", return_value=self.binary), \
                mock.patch.object(self_update, "TARGET", "windows-x86_64"), \
                mock.patch.object(self_update, "VERSION", "9.9.8"), \
                mock.patch.object(self_update.platform, "system", return_value="Windows"), \
                mock.patch.object(self_update.platform, "machine", return_value="ARM64"), \
                mock.patch.object(self_update.subprocess, "run", side_effect=answered), \
                mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()) as err:
            code = self_update.run(["9.9.9", "--dry-run"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("/download/9.9.9/cortex-windows-x86_64.zip", self.release.requested)
        self.assert_untouched()

    def test_a_build_reads_its_target_from_its_stamp(self):
        stamp = types.ModuleType("cortex_cli._stamp")
        exec(build.stamp_module("1.2.3", "windows-x86_64"), stamp.__dict__)
        self.addCleanup(importlib.reload, version)
        with mock.patch.dict(sys.modules, {"cortex_cli._stamp": stamp}):
            importlib.reload(version)
            self.assertEqual((version.VERSION, version.TARGET), ("1.2.3", "windows-x86_64"))
        importlib.reload(version)       # a source checkout: no stamp, no target
        self.assertIsNone(version.TARGET)


class NewerReleaseTests(ReleaseTestCase):
    def test_a_newer_release_is_found(self):
        self.latest_is("9.9.9")
        self.assertEqual(self_update.newer_release(Version("9.9.8")), Version("9.9.9"))

    def test_the_same_or_an_older_one_is_not(self):
        self.latest_is("9.9.8")
        for own in ("9.9.8", "9.9.9"):
            with self.subTest(own=own):
                self.assertIsNone(self_update.newer_release(Version(own)))

    def test_releases_that_do_not_say_the_latest_say_nothing(self):
        self.assertIsNone(self_update.newer_release(Version("1.0.0")))

    def test_offline_nothing_is_said(self):
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": "http://127.0.0.1:9"}):
            self.assertIsNone(self_update.newer_release(Version("1.0.0")))
        for error in (OSError("no certificate store"), ValueError("bad URL")):
            with self.subTest(error=error):
                self.assertIsNone(self_update.newer_release(Version("1.0.0"), fetch=mock.Mock(side_effect=error)))
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": "ftp://example.com"}):
            self.assertIsNone(self_update.newer_release(Version("1.0.0")))


class RunningBinaryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
    def test_through_a_link_the_binary_is_the_file_it_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            real, link = Path(tmp).resolve() / "bin" / "cortex", Path(tmp) / "cortex"
            real.parent.mkdir()
            real.write_bytes(OLD)
            link.symlink_to(real)
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", str(link)):
                self.assertEqual(binary.running_binary(), real)
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(binary.running_binary())


class EnvironmentTests(unittest.TestCase):
    """What a built binary starts the new one in: PyInstaller's recipe (binary.environment)."""

    def test_a_built_binary_resets_what_its_bootloader_set(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEI1", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}):
            env = binary.environment()
        self.assertEqual((env["LD_LIBRARY_PATH"], env["PYINSTALLER_RESET_ENVIRONMENT"]), ("/opt/lib", "1"))
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEI1"}):
            os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
            self.assertNotIn("LD_LIBRARY_PATH", binary.environment())

    def test_the_new_binary_is_run_in_that_environment(self):
        files = []

        def answered(args, **kwargs):
            files.extend(kwargs[stream].fileno() for stream in ("stdout", "stderr"))     # files, open
            kwargs["stdout"].write(b"cortex 1.2.3\n")
            return subprocess.CompletedProcess(args, 0)

        with mock.patch.object(self_update, "environment", return_value={"FRESH": "1"}), \
                mock.patch.object(self_update.subprocess, "run", side_effect=answered) as run:
            self.assertEqual(self_update.answers(Path("cortex")), "cortex 1.2.3")
        self.assertEqual(run.call_args.kwargs["env"], {"FRESH": "1"})
        # Files, not pipes: on Windows the wait for a pipe's end outlasts the timeout.
        self.assertEqual(len(files), 2)

    def test_from_source_the_environment_is_left_as_it_is(self):
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/opt/lib"}):
            env = binary.environment()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", env)


class ListedChecksumTests(unittest.TestCase):
    def test_the_line_of_the_name_in_sha256sums_format(self):
        sums = f"{'a' * 64}  cortex-spec.tar.gz\n{'B' * 64} *cortex-linux-x86_64.tar.gz\n"
        self.assertEqual(store.listed_checksum(sums, "cortex-spec.tar.gz"), "a" * 64)
        self.assertEqual(store.listed_checksum(sums, "cortex-linux-x86_64.tar.gz"), "b" * 64)
        self.assertIsNone(store.listed_checksum(sums, "cortex-macos-arm64.tar.gz"))
        self.assertIsNone(store.listed_checksum(f"{'a' * 64}  x  y\n", "x"))


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.bin = Path(tempfile.mkdtemp(prefix="cortex-replace-")).resolve()
        self.addCleanup(shutil.rmtree, self.bin, ignore_errors=True)
        self.binary, self.new = self.bin / "cortex", self.bin / ".cortex.new-1"
        self.binary.write_bytes(b"old\n")
        self.new.write_bytes(b"new\n")

    def names(self):
        return sorted(p.name for p in self.bin.iterdir())

    def test_renamed_over_the_old_one(self):
        binary.replace(self.new, self.binary, aside=False)
        self.assertEqual(self.binary.read_bytes(), b"new\n")
        self.assertEqual(self.names(), ["cortex"])

    def test_renamed_aside_then_tidied(self):
        binary.replace(self.new, self.binary, aside=True)
        self.assertEqual(self.binary.read_bytes(), b"new\n")
        self.assertEqual((self.bin / "cortex.old").read_bytes(), b"old\n")
        binary.tidy(self.binary, aside=True)
        self.assertEqual(self.names(), ["cortex"])

    def test_an_old_one_that_cannot_be_removed_yet_is_renamed_another_way(self):
        (self.bin / "cortex.old").write_bytes(b"older, still running\n")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("in use")):
            binary.replace(self.new, self.binary, aside=True)
        self.assertEqual(self.binary.read_bytes(), b"new\n")
        self.assertEqual(len([n for n in self.names() if n.startswith("cortex.old-")]), 1)
        binary.tidy(self.binary, aside=True)
        self.assertEqual(self.names(), ["cortex"])

    def test_a_failure_between_the_two_renames_puts_the_old_one_back(self):
        rename, calls = os.rename, []

        def second_fails(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise PermissionError("antivirus")
            rename(src, dst)

        with mock.patch.object(binary.os, "rename", side_effect=second_fails):
            with self.assertRaises(PermissionError):
                binary.replace(self.new, self.binary, aside=True)
        self.assertEqual(self.binary.read_bytes(), b"old\n")
        self.assertEqual(self.names(), [".cortex.new-1", "cortex"])

    def test_tidy_removes_a_new_binary_left_behind(self):
        (self.bin / ".cortex.new-abc.exe").write_bytes(b"x")
        aged(self.bin / ".cortex.new-abc.exe", self.new)
        binary.tidy(self.binary, aside=False)
        self.assertEqual(self.names(), ["cortex"])     # and the setup's .cortex.new-1 too

    def test_a_new_binary_is_left_for_an_hour(self):
        young, old = self.bin / ".cortex.new-young", self.bin / ".cortex.new-old"
        for path, minutes in ((young, 59), (old, 61), (self.new, 59)):
            path.write_bytes(b"x")
            then = time.time() - minutes * 60
            os.utime(path, (then, then))
        binary.tidy(self.binary, aside=False)
        self.assertEqual(self.names(), [".cortex.new-1", ".cortex.new-young", "cortex"])

    def test_tidy_removes_only_the_names_replace_gives(self):
        for name in ("cortex.old", "cortex.old-12-345", "CORTEX.OLD", "cortex.old-config.toml", "cortex.older",
                     "cortex.old.bak"):
            (self.bin / name).write_bytes(b"x")
        aged(self.new)
        binary.tidy(self.binary, aside=True)
        self.assertEqual(self.names(), ["cortex", "cortex.old-config.toml", "cortex.old.bak", "cortex.older"])

    def test_tidy_leaves_the_new_binary_of_a_self_update_at_work(self):
        binary.tidy(self.binary, aside=True)
        self.assertEqual(self.names(), [".cortex.new-1", "cortex"])

    def test_without_the_rename_aside_an_old_binary_is_nobodys_leftover(self):
        (self.bin / "cortex.old").write_bytes(b"a copy someone keeps\n")
        binary.tidy(self.binary, aside=False)
        self.assertEqual(self.names(), [".cortex.new-1", "cortex", "cortex.old"])

    def test_tidy_leaves_other_files(self):
        for name in ("cortex-ai", "cortex-ai.old", "notes.old"):
            (self.bin / name).write_bytes(b"x")
        aged(self.new)
        binary.tidy(self.binary, aside=True)
        self.assertEqual(self.names(), ["cortex", "cortex-ai", "cortex-ai.old", "notes.old"])

    def test_a_running_executable_is_replaced(self):
        """A program copied in the binary's place, started, then replaced while it runs."""
        if os.name == "nt":
            program, args = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "ping.exe", ["-n", "30", "127.0.0.1"]
            exe = self.bin / "cortex.exe"
        else:
            program, args, exe = Path(shutil.which("sleep") or "/bin/sleep"), ["30"], self.binary
        if not program.is_file():
            self.skipTest(f"no {program} to run")
        exe.unlink(missing_ok=True)
        shutil.copy(program, exe)       # the bytes and the mode: macOS refuses a system file's flags
        proc = subprocess.Popen([str(exe), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        time.sleep(0.5)
        if proc.poll() is not None:
            self.skipTest(f"{program.name} did not keep running once copied")
        if os.name == "nt":
            other = self.bin / ".other.exe"
            other.write_bytes(b"x")
            with self.assertRaises(PermissionError):    # why Windows needs the rename aside
                os.replace(other, exe)
            other.unlink()
        binary.replace(self.new, exe)
        self.assertEqual(exe.read_bytes(), b"new\n")
        self.assertIsNone(proc.poll())
        proc.kill()
        proc.wait()
        binary.tidy(exe)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), sorted({exe.name, "cortex"}))


class CommandTests(unittest.TestCase):
    def run_cortex(self, *args, env=None):
        proc = harness.run(*args, env=env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def test_the_command_is_listed(self):
        self.assertIn("  self-update  Replace this cortex with a release's binary", self.run_cortex("--help").out)

    def test_a_malformed_version_exits_2(self):
        for version in ("1.4", "v1.4.0"):
            with self.subTest(version=version):
                proc = self.run_cortex("self-update", version)
                self.assertEqual(proc.returncode, 2, proc.err)
                self.assertIn("is no version", proc.err)

    @SOURCE
    def test_from_source_it_is_refused(self):
        proc = self.run_cortex("self-update", "9.9.9")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("cortex self-update: this cortex runs from a source checkout", proc.err)


@BUILT
class BuiltBinaryTests(ReleaseTestCase):
    """A copy of the binary under test, replaced — the binary under test never is — by release
    TARGET, a version no build of this checkout stands for."""

    def setUp(self):
        super().setUp()
        self.binary.unlink()
        self.binary = self.bin / Path(harness.BINARY).name
        shutil.copy2(harness.BINARY, self.binary)
        self.env = harness.environment(CORTEX_RELEASES_URL=self.release.url, CORTEX_HOME=str(self.tmp / "home"))
        FakeRelease.publish(self.release.root / "download" / TARGET, TARGET)

    def run_copy(self, *args):
        proc = subprocess.run([str(self.binary), *args], env=self.env, capture_output=True, stdin=subprocess.DEVNULL)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    @POSIX
    def test_the_copy_installs_the_release_and_the_release_answers(self):
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"Installed cortex {TARGET} as {display(str(self.binary))}", proc.out)
        self.assertEqual(self.run_copy("--version").out, f"cortex {TARGET}\n")
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), [self.binary.name])

    @POSIX
    def test_a_machine_file_that_does_not_read_warns_and_the_copy_updates_all_the_same(self):
        # ADR-013 §3.7: `cortex self-update` runs whatever the machine's config.toml holds.
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        (home / "config.toml").write_text('endpoints = "later"\nregister = 3\nreleases_url = [\n', encoding="utf-8")
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"Installed cortex {TARGET} as {display(str(self.binary))}", proc.out)
        self.assertIn(f"warning: {display(str(home / 'config.toml'))} is not read", proc.err)
        (home / "config.toml").write_text('endpoints = "later"\nregister = 3\n', encoding="utf-8")
        shutil.copy2(harness.BINARY, self.binary)                       # the copy again, replaced by the stand-in
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn('unknown key "endpoints", not read', proc.err)
        self.assertIn('"register" must be true or false', proc.err)

    @POSIX
    def test_through_a_link_the_file_it_names_is_replaced_and_the_link_kept(self):
        links = self.tmp / "links"
        links.mkdir()
        (links / "cortex").symlink_to(self.binary)
        proc = subprocess.run([str(links / "cortex"), "self-update", TARGET], env=self.env, capture_output=True,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"Installed cortex {TARGET} as {display(str(self.binary))}", proc.stdout.decode())
        self.assertEqual(os.readlink(links / "cortex"), str(self.binary))
        self.assertEqual(self.run_copy("--version").out, f"cortex {TARGET}\n")
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), [self.binary.name])

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_on_windows_the_copy_puts_a_release_in_its_place_while_it_runs(self):
        program = answering_windows_program(TARGET)
        if program is None:
            self.skipTest("no launcher of pip's to build a program that answers")
        directory = self.release.root / "download" / TARGET
        with zipfile.ZipFile(directory / "cortex-windows-x86_64.zip", "w") as archive:
            archive.writestr("cortex.exe", program)
        build.checksums(directory)
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"Installed cortex {TARGET} as {display(str(self.binary))}", proc.out)
        self.assertTrue((self.bin / f"{self.binary.name}.old").is_file())     # renamed aside, as it ran
        self.assertEqual(self.run_copy("--version").out.replace("\r\n", "\n"), f"cortex {TARGET}\n")
        binary.tidy(self.binary, aside=True)            # what the release's next start would do
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), [self.binary.name])

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_on_windows_a_stand_in_that_answers_otherwise_is_refused(self):
        before = self.binary.read_bytes()
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("nothing was replaced", proc.err)
        self.assertEqual(self.binary.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), [self.binary.name])

    @unittest.skipUnless(harness.EXPECTED_VERSION, "the version the binary stands for")
    def test_a_built_binary_runs_the_one_it_downloads(self):
        """The binary under test served as release TARGET: the copy runs it — one PyInstaller binary
        started by another — and refuses it for answering its own version, not TARGET."""
        asset = self_update.asset_name(platform.system(), platform.machine())
        directory = self.release.root / "download" / TARGET
        if asset.endswith(".zip"):
            with zipfile.ZipFile(directory / asset, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(harness.BINARY, "cortex.exe")
        else:
            with tarfile.open(directory / asset, "w:gz") as archive:
                archive.add(harness.BINARY, arcname="cortex")
        build.checksums(directory)
        before = self.binary.read_bytes()
        proc = self.run_copy("self-update", TARGET)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"the binary of Cortex {TARGET} answers 'cortex {harness.EXPECTED_VERSION}' to --version, "
                      f"not 'cortex {TARGET}' — nothing was replaced", proc.err)
        self.assertEqual(self.binary.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_on_windows_the_next_start_removes_the_old_binary(self):
        (self.bin / (self.binary.name + ".old")).write_bytes(b"the binary a self-update renamed aside")
        self.assertEqual(self.run_copy("--version").returncode, 0)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), [self.binary.name])

    def test_a_start_removes_a_new_binary_a_killed_self_update_left(self):
        left, at_work = self.bin / f".{self.binary.name}.new-left", self.bin / f".{self.binary.name}.new-at-work"
        left.write_bytes(b"a self-update killed half-way")
        at_work.write_bytes(b"another self-update, downloading")
        aged(left)
        self.assertEqual(self.run_copy("--version").returncode, 0)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), sorted([self.binary.name, at_work.name]))

    @unittest.skipUnless(harness.EXPECTED_VERSION, "the version the binary stands for")
    def test_on_its_own_version_it_downloads_nothing(self):
        proc = self.run_copy("self-update", harness.EXPECTED_VERSION)
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)
        self.assertIn(f"cortex {harness.EXPECTED_VERSION}: already on it", proc.out)
        self.assertEqual(self.release.requested, [])


if __name__ == "__main__":
    unittest.main()
