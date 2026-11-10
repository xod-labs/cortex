"""The store, in process — ADR-008 §3.3.

``test_cli_sync`` drives the store through the command; these tests call it directly, with its
version, its embedded spec and its downloads given, to reach what a command line cannot: a
binary's embedded archive, a write interrupted, two writes of one version at once.
"""

import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core"),
                str(Path(__file__).resolve().parent)]
from cortex_cli import net, store  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import FakeRelease, spec_archive  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-store-"))
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "home"

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                os.chmod(os.path.join(directory, name), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def listing(self):
        versions = self.home / "versions"
        return sorted(p.name for p in versions.iterdir()) if versions.is_dir() else []


class EmbeddedTests(StoreTestCase):
    def test_a_binary_writes_the_archive_it_embeds(self):
        archive = spec_archive(self.tmp, "2.0.0")
        the_store = store.Store(self.home, own_version="2.0.0", embedded=archive, fetch=self.no_network)
        self.assertEqual(the_store.ensure(Version("2.0.0")), "written")
        self.assertEqual(sorted(p.name for p in the_store.path(Version("2.0.0")).iterdir()), sorted(store.SPEC_TREES))
        self.assertEqual(the_store.ensure(Version("2.0.0")), "stored")

    def test_a_source_checkout_writes_its_own_spec(self):
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.ensure(Version("2.0.0"))
        written = the_store.path(Version("2.0.0"))
        self.assertEqual((written / "agents" / "roles" / "prompt-manager.md").read_bytes(),
                         (REPO / "agents" / "roles" / "prompt-manager.md").read_bytes())

    def test_a_dev_version_serves_itself(self):
        for own in ("1.8.0-dev", "1.8.0-dev.7"):               # a source checkout's, a CI build's (ADR-009 §9)
            with self.subTest(own=own):
                the_store = store.Store(self.home, own_version=own, checkout=REPO, fetch=self.no_network)
                self.assertEqual(the_store.ensure(Version(own)), "written")

    def test_another_dev_version_is_served_from_the_store_only(self):
        # No release serves a build of no release's version: the network is never asked for it (ADR-009 §9).
        the_store = store.Store(self.home, own_version="1.8.1-dev.5", checkout=REPO, fetch=self.no_network)
        for version in ("1.8.0-dev.3", "1.8.2-dev"):                     # older, and newer, than this cortex
            with self.subTest(version=version):
                with self.assertRaises(store.StoreError) as raised:
                    the_store.ensure(Version(version))
                self.assertEqual(str(raised.exception), f"Cortex {version} is a build of no release, which no "
                                 "release serves — pin a release: `cortex upgrade VERSION`")
        held = the_store.path(Version("1.8.0-dev.3"))                    # written by that build, still there
        for tree in store.SPEC_TREES:
            (held / tree).mkdir(parents=True)
        self.assertEqual(the_store.ensure(Version("1.8.0-dev.3")), "stored")
        newer = the_store.path(Version("1.8.2-dev.1"))                    # held, but newer than this cortex
        for tree in store.SPEC_TREES:
            (newer / tree).mkdir(parents=True)
        with self.assertRaises(store.StoreError) as raised:
            the_store.ensure(Version("1.8.2-dev.1"), origin="agents/cortex.team.toml")
        self.assertEqual(str(raised.exception), "Cortex 1.8.2-dev.1 is a build of no release, which no release "
                         "serves — pin a release: `cortex upgrade --team VERSION`")
        # From a build that prepares 1.8.0, every release that reads the team's file is newer: a self-update first.
        early = store.Store(self.home, own_version="1.8.0-dev.5", checkout=REPO, fetch=self.no_network)
        with self.assertRaises(store.StoreError) as raised:
            early.ensure(Version("1.8.0-dev.4"), origin="agents/cortex.team.toml")
        self.assertEqual(str(raised.exception), "Cortex 1.8.0-dev.4 is a build of no release, which no release serves "
                         "— update this cortex to a release that reads the team's file — 1.8.0 or a candidate of it — "
                         "then pin one: `cortex self-update VERSION`, `cortex upgrade --team VERSION`")

    def test_the_pin_is_moved_where_a_file_holds_it(self):
        for origin, command in ((None, "cortex upgrade VERSION"), ("cortex.toml", "cortex upgrade VERSION"),
                                ("agents/cortex.team.toml", "cortex upgrade --team VERSION"),
                                ("team-agents/cortex.team.toml", "cortex upgrade --team VERSION"),
                                ("team-agents\\cortex.team.toml", "cortex upgrade --team VERSION"),
                                ("cortex.local.toml", "cortex upgrade --local VERSION")):
            with self.subTest(origin=origin):
                self.assertEqual(store.pin_command(origin), command)
        self.assertEqual(store.update_command(Version("1.8.0-rc.1")), "cortex self-update 1.8.0-rc.1")

    @unittest.skipIf(AS_ROOT, "root writes any file")
    def test_it_is_read_only_once_written(self):
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.ensure(Version("2.0.0"))
        card = the_store.path(Version("2.0.0")) / "agents" / "roles" / "prompt-manager.md"
        with self.assertRaises(PermissionError):
            card.write_text("edited", encoding="utf-8")

    def test_an_interrupted_write_leaves_nothing(self):
        the_store = store.Store(self.home, own_version="2.0.0", checkout=self.tmp / "no-such-checkout",
                                fetch=self.no_network)
        with self.assertRaises(FileNotFoundError):
            the_store.ensure(Version("2.0.0"))
        self.assertEqual(self.listing(), [])

    def test_a_version_written_meanwhile_by_another_sync(self):
        # Two syncs that both found the version missing: the second rename finds it in place.
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.versions.mkdir(parents=True)
        fill = lambda staging: store._copy_trees(REPO, staging)  # noqa: E731
        the_store._install(Version("2.0.0"), fill)
        the_store._install(Version("2.0.0"), fill)
        self.assertEqual(self.listing(), ["2.0.0"])

    def test_an_empty_version_directory_is_no_version(self):
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.path(Version("2.0.0")).mkdir(parents=True)
        self.assertFalse(the_store.has(Version("2.0.0")))
        with self.assertRaisesRegex(store.StoreError, "holds no complete spec"):
            the_store.ensure(Version("2.0.0"))

    @unittest.skipIf(os.name == "nt", "the mode of a directory is POSIX's")
    def test_a_version_left_writable_is_made_read_only_again(self):
        # Interrupted between the rename and the chmod, a sync left the version writable.
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.ensure(Version("2.0.0"))
        store.make_writable(the_store.path(Version("2.0.0")))
        self.assertEqual(the_store.ensure(Version("2.0.0")), "stored")
        self.assertFalse(os.stat(the_store.path(Version("2.0.0"))).st_mode & 0o222)

    def test_a_file_left_writable_is_made_read_only_again(self):
        # On Windows the read-only attribute is a file's, never a directory's: a file tells.
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.ensure(Version("2.0.0"))
        card = the_store.path(Version("2.0.0")) / "agents" / "roles" / "prompt-manager.md"
        os.chmod(card, 0o644)
        self.assertEqual(the_store.ensure(Version("2.0.0")), "stored")
        self.assertFalse(os.stat(card).st_mode & 0o222)

    def test_a_staging_left_for_a_day_is_removed(self):
        the_store = store.Store(self.home, own_version="2.0.0", checkout=REPO, fetch=self.no_network)
        the_store.ensure(Version("2.0.0"))
        old, fresh = the_store.versions / ".2.0.1-abandoned", the_store.versions / ".download-inflight"
        old.mkdir()
        fresh.write_bytes(b"x")
        os.utime(old, (0, 0))
        the_store.ensure(Version("2.0.0"))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    @staticmethod
    def no_network(url, limit):
        raise AssertionError(f"no download expected, got {url}")


class DownloadTests(StoreTestCase):
    """Other versions come from their release's spec archive, checked against its SHA256SUMS."""

    def release(self, version, archive_bytes=None, sums_line=None):
        archive = archive_bytes if archive_bytes is not None else spec_archive(self.tmp, version).read_bytes()
        digest = hashlib.sha256(archive).hexdigest()
        files = {"SHA256SUMS": (sums_line if sums_line is not None else f"{digest}  cortex-spec.tar.gz\n").encode(),
                 "cortex-spec.tar.gz": archive}
        self.requested = []

        def fetch(url, limit):
            self.requested.append(url)
            name = url.rsplit("/", 1)[1]
            if f"/download/{version}/" not in url or name not in files:
                raise net.DownloadError(f"{url}: HTTP 404 Not Found")
            return files[name]
        return store.Store(self.home, own_version="3.0.0", embedded=self.tmp / "unused.tar.gz", fetch=fetch)

    def test_downloaded_and_verified(self):
        the_store = self.release("2.0.0")
        self.assertEqual(the_store.ensure(Version("2.0.0")), "downloaded")
        self.assertIn("Cortex 2.0.0", (the_store.path(Version("2.0.0")) / "agents/roles/prompt-manager.md").read_text(encoding="utf-8"))
        self.assertEqual([u.rsplit("/", 2)[-2:] for u in self.requested], [["2.0.0", "SHA256SUMS"], ["2.0.0", "cortex-spec.tar.gz"]])

    def test_a_checksum_that_differs(self):
        the_store = self.release("2.0.0", sums_line=f"{'0' * 64}  cortex-spec.tar.gz\n")
        with self.assertRaisesRegex(store.StoreError, "does not match its checksum"):
            the_store.ensure(Version("2.0.0"))
        self.assertEqual(self.listing(), [])

    def test_a_checksum_in_upper_case_or_with_a_star_is_read(self):
        archive = spec_archive(self.tmp, "2.0.0").read_bytes()
        line = f"{hashlib.sha256(archive).hexdigest().upper()} *cortex-spec.tar.gz\n"
        self.release("2.0.0", archive, sums_line=line).ensure(Version("2.0.0"))
        self.assertEqual(self.listing(), ["2.0.0"])

    def test_members_a_spec_archive_cannot_hold(self):
        def archive_with(member):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                for tree in store.SPEC_TREES:
                    info = tarfile.TarInfo(f"{tree}/x.md")
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
                tar.addfile(member, io.BytesIO(b"x" * member.size))
            return buffer.getvalue()

        link = tarfile.TarInfo("agents/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        hard = tarfile.TarInfo("agents/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "../../outside"
        device = tarfile.TarInfo("agents/dev")
        device.type = tarfile.CHRTYPE
        escape = tarfile.TarInfo("../escape.md")
        escape.size = 1
        for member in (link, hard, device, escape):
            with self.subTest(member=member.name):
                with self.assertRaisesRegex(store.StoreError, "which a spec archive cannot hold"):
                    self.release("2.0.0", archive_with(member)).ensure(Version("2.0.0"))
                self.assertEqual(self.listing(), [])

    def test_an_archive_without_the_three_trees(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo("agents/x.md")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(store.StoreError, "holds agents, not agents, templates, docs"):
            self.release("2.0.0", buffer.getvalue()).ensure(Version("2.0.0"))
        self.assertEqual(self.listing(), [])

    def test_the_versions_it_serves(self):
        the_store = self.release("2.0.0")
        with self.assertRaisesRegex(store.StoreError, "newer than this cortex, 3.0.0"):
            the_store.ensure(Version("3.0.1"))
        with self.assertRaisesRegex(store.StoreError, "has no spec archive: the first one is 1.0.0"):
            the_store.ensure(Version("0.10.1"))
        with self.assertRaisesRegex(store.StoreError, "has no spec archive"):
            the_store.ensure(Version("1.0.0-rc.1"))
        self.assertEqual(self.requested, [])

    def test_a_known_checksum_the_release_contradicts_is_refused(self):
        # SHA256SUMS comes from where the archive does: a release replaced wholesale passes it.
        # The checksum this binary was built with does not move.
        the_store = self.release("2.0.0")
        the_store.known = {"2.0.0": "f" * 64}
        with self.assertRaisesRegex(store.StoreError, "the release was changed since this binary was built"):
            the_store.ensure(Version("2.0.0"))
        self.assertEqual(self.listing(), [])

    def test_a_known_checksum_that_agrees(self):
        archive = spec_archive(self.tmp, "2.0.0").read_bytes()
        the_store = self.release("2.0.0", archive)
        the_store.known = {"2.0.0": hashlib.sha256(archive).hexdigest()}
        self.assertEqual(the_store.ensure(Version("2.0.0")), "downloaded")

    def test_a_version_the_binary_does_not_know_falls_back_on_sha256sums(self):
        # A release made after this binary, on an older line — 2.0.1 after 3.0.0 — is not in its table.
        the_store = self.release("2.0.0")
        the_store.known = {"1.0.0": "a" * 64}
        self.assertEqual(the_store.ensure(Version("2.0.0")), "downloaded")

    def test_a_release_that_does_not_exist(self):
        with self.assertRaisesRegex(store.StoreError, "could not download the SHA256SUMS of Cortex 2.5.0"):
            self.release("2.0.0").ensure(Version("2.5.0"))


class NetTests(unittest.TestCase):
    def test_the_context_verifies_certificates(self):
        context = net.ssl_context()
        import ssl

        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    @unittest.skipIf(os.name == "nt", "Windows gives Python its own certificate store")
    def test_with_no_default_certificates_a_system_bundle_is_loaded(self):
        from unittest import mock
        import ssl

        empty = ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", "/nonexistent/cert.pem", "SSL_CERT_DIR", "/nonexistent")
        bundle = net.fallback_bundle()
        if bundle is None:
            self.skipTest("no system bundle on this machine")
        with mock.patch("ssl.get_default_verify_paths", return_value=empty), \
                mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("SSL_CERT_FILE", None)
            env.pop("SSL_CERT_DIR", None)
            with mock.patch.object(ssl.SSLContext, "load_verify_locations") as loaded:
                net.ssl_context()
        loaded.assert_called_with(cafile=bundle)

    def test_only_https_or_http_to_this_machine(self):
        for url in ("https://github.com/x", "http://127.0.0.1:8765/x", "http://localhost/x", "http://[::1]:80/x"):
            with self.subTest(url=url):
                net.check_url(url)
        for url in ("http://example.com/x", "file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1.example.com/x"):
            with self.subTest(url=url):
                with self.assertRaises(net.DownloadError):
                    net.check_url(url)

    def test_the_releases_url_is_checked(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": "file:///srv/releases"}):
            with self.assertRaisesRegex(store.StoreError, "CORTEX_RELEASES_URL"):
                store.releases_url()

    def test_a_redirect_off_https_is_refused(self):
        import http.server
        import threading

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://example.com/SHA256SUMS")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaisesRegex(net.DownloadError, "only https"):
            net.get(f"http://127.0.0.1:{server.server_address[1]}/SHA256SUMS", 1024)

    def test_a_url_holding_a_control_character(self):
        with self.assertRaises(net.DownloadError):
            net.get("https://github.com/xod-labs/cortex/releases/download/1.0.0\n/SHA256SUMS", 1024)

    @unittest.skipIf(os.name == "nt", "Windows gives Python its own certificate store")
    def test_an_empty_certificate_directory_holds_no_certificate(self):
        from unittest import mock
        import ssl
        import tempfile

        with tempfile.TemporaryDirectory() as empty:
            paths = ssl.DefaultVerifyPaths(None, empty, "SSL_CERT_FILE", "/nonexistent", "SSL_CERT_DIR", empty)
            with mock.patch("ssl.get_default_verify_paths", return_value=paths), \
                    mock.patch.dict(os.environ, {}, clear=False) as env:
                env.pop("SSL_CERT_FILE", None)
                env.pop("SSL_CERT_DIR", None)
                self.assertFalse(net._defaults_hold_certificates())

    @unittest.skipUnless(os.environ.get("CI") == "true", "reaches github.com: in CI only")
    def test_github_is_reached_over_verified_https(self):
        # A release page that does not exist: a 404 proves the TLS handshake was verified.
        with self.assertRaisesRegex(net.DownloadError, "HTTP 404"):
            net.get("https://github.com/xod-labs/cortex/releases/download/0.0.0-no-such-release/SHA256SUMS", 1024)


class DigestTests(unittest.TestCase):
    """The digest of a spec (ADR-010 §3.2): its files' bytes, however they reached the store."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-digest-"))
        self.addCleanup(lambda: (store.make_writable(self.tmp), shutil.rmtree(self.tmp, ignore_errors=True)))
        self.tree = self.tmp / "checkout"
        for rel, text in (("agents/roles/a.md", "A\n"), ("agents/roles/b.md", "B\n"), ("templates/t.md", "T\n"),
                          ("docs/\u00e9.md", "D\n")):
            (self.tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / rel).write_bytes(text.encode("utf-8"))

    def test_the_digest_is_sha256sum_of_the_files_in_the_order_of_their_paths(self):
        lines = sorted((rel.encode("utf-8"), hashlib.sha256(text.encode()).hexdigest().encode())
                       for rel, text in (("agents/roles/a.md", "A\n"), ("agents/roles/b.md", "B\n"),
                                         ("templates/t.md", "T\n"), ("docs/\u00e9.md", "D\n")))
        expected = hashlib.sha256(b"".join(sha + b"  " + rel + b"\n" for rel, sha in lines)).hexdigest()
        self.assertEqual(store.digest(self.tree), f"sha256:{expected}")

    def test_written_from_an_archive_or_from_a_checkout_the_digest_is_the_same(self):
        archive = self.tmp / "spec.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for tree in store.SPEC_TREES:
                tar.add(self.tree / tree, arcname=tree)
        from_archive = store.Store(home=self.tmp / "a", own_version="9.9.9", embedded=archive, fetch=None, known={})
        from_checkout = store.Store(home=self.tmp / "b", own_version="9.9.9", embedded=None, checkout=self.tree,
                                    fetch=None, known={})
        self.assertEqual(from_archive.ensure(Version("9.9.9")), "written")
        self.assertEqual(from_checkout.ensure(Version("9.9.9")), "written")
        self.assertEqual(from_archive.digest(Version("9.9.9")), from_checkout.digest(Version("9.9.9")))
        self.assertEqual(from_archive.digest(Version("9.9.9")), store.digest(self.tree))

    def test_one_byte_changes_it_and_a_file_outside_the_trees_does_not(self):
        before = store.digest(self.tree)
        (self.tree / ".synced").write_text("{}", encoding="utf-8")
        self.assertEqual(store.digest(self.tree), before)
        (self.tree / "docs" / "\u00e9.md").write_bytes(b"d\n")
        self.assertNotEqual(store.digest(self.tree), before)

    def test_a_checkouts_theme_marker_is_not_the_spec(self):
        (self.tree / "agents" / "personalities").mkdir(parents=True)
        before = store.digest(self.tree)
        (self.tree / "agents" / "personalities" / ".active-h2g2").write_text("", encoding="utf-8")
        the_store = store.Store(home=self.tmp / "d", own_version="9.9.9", embedded=None, checkout=self.tree,
                                fetch=None, known={})
        the_store.ensure(Version("9.9.9"))
        self.assertEqual(the_store.digest(Version("9.9.9")), before)

    def test_a_version_that_cannot_be_had_again_is_put_back(self):
        # Offline, or a download refused: the version is not lost for it.
        the_store = store.Store(home=self.tmp / "e", own_version="9.9.9", embedded=None, checkout=self.tree,
                                fetch=None, known={})
        the_store.ensure(Version("9.9.9"))
        the_store.checkout = None
        with self.assertRaises(store.StoreError):
            the_store.replace(Version("9.9.9"))
        self.assertTrue(the_store.has(Version("9.9.9")))
        self.assertEqual([p.name for p in the_store.versions.iterdir()], ["9.9.9"])

    def test_the_same_version_embedded_or_downloaded_has_the_same_digest(self):
        # A binary writes its own version from the archive it embeds, packed on its runner: not the
        # release's bytes — other dates, another order, directories listed — and the same files.
        release = FakeRelease(("2.0.0",))
        self.addCleanup(release.close)
        published = release.root / "download" / "2.0.0" / "cortex-spec.tar.gz"
        with tarfile.open(published) as source:
            files = {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile()}
        embedded = self.tmp / "runner" / "cortex-spec.tar.gz"
        embedded.parent.mkdir()
        with tarfile.open(embedded, "w:gz") as archive:
            for tree in reversed(store.SPEC_TREES):
                folder = tarfile.TarInfo(tree)
                folder.type, folder.mode, folder.mtime = tarfile.DIRTYPE, 0o755, 1700000000
                archive.addfile(folder)
            for name in sorted(files, reverse=True):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(files[name]), 0o600, 1700000000
                archive.addfile(info, io.BytesIO(files[name]))
        self.assertNotEqual(embedded.read_bytes(), published.read_bytes())
        written = store.Store(home=self.tmp / "own", own_version="2.0.0", embedded=embedded, fetch=None, known={})
        downloaded = store.Store(home=self.tmp / "later", own_version="3.0.0", embedded=self.tmp / "unused.tar.gz",
                                 known={})
        with mock.patch.dict(os.environ, {"CORTEX_RELEASES_URL": release.url}):
            self.assertEqual(written.ensure(Version("2.0.0")), "written")
            self.assertEqual(downloaded.ensure(Version("2.0.0")), "downloaded")
        self.assertEqual(written.digest(Version("2.0.0")), downloaded.digest(Version("2.0.0")))

    def test_every_path_of_this_spec_is_one_sha256sum_writes_as_it_is(self):
        # The digest is sha256sum's lines (ADR-010 §3.2): sha256sum escapes a path holding "\" or
        # a line break, and marks its line — the digest would no longer be its output.
        for tree in store.SPEC_TREES:
            for path in (REPO / tree).rglob("*"):
                rel = path.relative_to(REPO).as_posix()
                with self.subTest(path=rel):
                    self.assertFalse(set(rel) & {"\\", "\n", "\r"}, f"{rel!r}: rename it")

    def test_a_missing_tree_has_no_digest(self):
        # A version removed while a sync read it: the empty set's digest would be locked.
        shutil.rmtree(self.tree / "docs")
        with self.assertRaisesRegex(store.StoreError, r"holds no docs/ — no whole spec to take the digest of"):
            store.digest(self.tree)
        with self.assertRaisesRegex(store.StoreError, "holds no agents/, templates/, docs/"):
            store.digest(self.tmp / "no-such-version")

    def test_a_file_or_a_tree_that_goes_while_it_is_read_leaves_no_digest(self):
        # Renamed aside while it was walked: what was not read yet gave nothing, and no error.
        read, roles = Path.read_bytes, self.tree / "agents" / "roles"
        for gone in ("the docs/ tree", "a file listed, not read yet"):
            with self.subTest(gone=gone):
                def then_gone(path, gone=gone, done=[]):
                    data = read(path)
                    if not done:            # the first file read is one of agents/roles: the other goes
                        done.append(path)
                        victim = self.tree / "docs" if gone.startswith("the docs") else \
                            next(p for p in roles.iterdir() if p.name != path.name)
                        shutil.rmtree(victim) if victim.is_dir() else victim.unlink()
                    return data

                with mock.patch.object(Path, "read_bytes", then_gone):
                    with self.assertRaisesRegex(store.StoreError, "while its digest was taken"):
                        store.digest(self.tree)
                (self.tree / "docs").mkdir(exist_ok=True)
                (self.tree / "docs" / "\u00e9.md").write_bytes(b"D\n")
                for name, text in (("a.md", b"A\n"), ("b.md", b"B\n")):
                    (roles / name).write_bytes(text)

    def downloading(self, state):
        """A store that downloads Cortex 2.0.0 from a release in this process — none while
        ``state["online"]`` is false."""
        archive = spec_archive(self.tmp, "2.0.0").read_bytes()
        sums = f"{hashlib.sha256(archive).hexdigest()}  cortex-spec.tar.gz\n".encode()

        def fetch(url, limit):
            if not state["online"]:
                raise net.DownloadError(f"{url}: no network")
            return sums if url.endswith("SHA256SUMS") else archive

        return store.Store(home=self.tmp / "f", own_version="3.0.0", embedded=self.tmp / "unused.tar.gz",
                           fetch=fetch, known={})

    def test_what_a_replace_set_aside_is_renamed_removed_before_it_is_deleted(self):
        # Named "kept", an aside is whole: one half deleted must never be taken for it, and put back.
        the_store, version = self.downloading({"online": True}), Version("2.0.0")
        the_store.ensure(version)
        seen, discard = [], the_store._discard

        def deleting(aside):
            seen.append((aside.name, [p.name for p in the_store.versions.glob(".2.0.0-kept-*")]))
            return discard(aside)

        with mock.patch.object(the_store, "_discard", deleting):
            the_store.replace(version)
        self.assertEqual(len(seen), 1)
        self.assertIn("-removed-", seen[0][0])
        self.assertEqual(seen[0][1], [])

    def test_of_two_asides_the_latest_is_put_back(self):
        # Two replaces cut short: the later one set aside the store as it last was.
        state = {"online": True}
        the_store, version = self.downloading(state), Version("2.0.0")
        the_store.ensure(version)
        the_store._aside(version, "kept")
        the_store.ensure(version)
        latest = the_store._aside(version, "kept")
        store.make_writable(latest)
        (latest / "agents" / "latest.md").write_bytes(b"the latest\n")
        state["online"] = False
        self.assertEqual(the_store.ensure(version), "put back")
        self.assertTrue((the_store.path(version) / "agents" / "latest.md").is_file())

    def test_a_version_a_replace_cut_short_left_aside_is_put_back_offline(self):
        # Killed while it had the version again, a sync left it aside: offline, the next finds it.
        archive = spec_archive(self.tmp, "2.0.0").read_bytes()
        sums = f"{hashlib.sha256(archive).hexdigest()}  cortex-spec.tar.gz\n".encode()
        online = True

        def fetch(url, limit):
            if not online:
                raise net.DownloadError(f"{url}: no network")
            return sums if url.endswith("SHA256SUMS") else archive

        the_store = store.Store(home=self.tmp / "f", own_version="3.0.0", embedded=self.tmp / "unused.tar.gz",
                                fetch=fetch, known={})
        version = Version("2.0.0")
        the_store.ensure(version)
        before = store.digest(the_store.path(version))
        kept, ensure = [], the_store.ensure

        def while_it_downloads(*args, **kwargs):
            kept.extend(p.name for p in the_store.versions.glob(".2.0.0-kept-*"))
            return ensure(*args, **kwargs)

        with mock.patch.object(the_store, "ensure", while_it_downloads):
            the_store.replace(version)
        self.assertEqual(len(kept), 1)
        self.assertEqual([p.name for p in the_store.versions.iterdir()], ["2.0.0"])
        the_store._aside(version, "kept")       # a replace killed there
        online = False
        self.assertEqual(the_store.ensure(version), "put back")
        self.assertEqual(store.digest(the_store.path(version)), before)
        self.assertEqual([p.name for p in the_store.versions.iterdir()], ["2.0.0"])
        # What prune set aside on its way out is never put back; nor anything, by replace itself.
        the_store._aside(version)
        with self.assertRaisesRegex(store.StoreError, "could not download"):
            the_store.ensure(version)
        store.remove_tree(next(the_store.versions.glob(".2.0.0-removed-*")))
        online = True
        the_store.ensure(version)
        online = False
        with self.assertRaisesRegex(store.StoreError, "could not download"):
            the_store.replace(version)
        self.assertEqual([p.name for p in the_store.versions.iterdir()], ["2.0.0"])

    def test_a_version_removed_between_two_looks_is_written_again(self):
        # cortex prune renamed it away after has() said it was there, before the tidying.
        the_store = store.Store(home=self.tmp / "f", own_version="9.9.9", embedded=None, checkout=self.tree,
                                fetch=None, known={})
        the_store.ensure(Version("9.9.9"))
        tidy = the_store._tidy

        def pruned_meanwhile(version):
            the_store.remove(version)
            tidy(version)

        the_store._tidy = pruned_meanwhile
        self.assertEqual(the_store.ensure(Version("9.9.9")), "written")
        self.assertTrue(the_store.has(Version("9.9.9")))

    def test_a_version_put_in_again_is_whole(self):
        the_store = store.Store(home=self.tmp / "c", own_version="9.9.9", embedded=None, checkout=self.tree,
                                fetch=None, known={})
        the_store.ensure(Version("9.9.9"))
        path = the_store.path(Version("9.9.9")) / "agents" / "roles" / "a.md"
        os.chmod(path, 0o644)
        path.write_text("changed\n", encoding="utf-8")
        self.assertEqual(the_store.replace(Version("9.9.9")), "written")
        self.assertEqual(path.read_text(encoding="utf-8"), "A\n")
        self.assertEqual([p.name for p in the_store.versions.iterdir()], ["9.9.9"])


if __name__ == "__main__":
    unittest.main()
