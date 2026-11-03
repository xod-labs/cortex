"""``cortex prune`` — the versions no project uses, out of the store (ADR-010 §3.6).

From source the command stands for Cortex 9.9.9; 9.9.6, 9.9.7 and 9.9.8 are served from this machine
(``release_fixture``) and reach the store through ``cortex sync``. A built binary downloads nothing:
the tests that need other versions skip there.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
import cli_harness as harness  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")


class PruneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-prune-")).resolve()
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "cortex-home"
        self.release = FakeRelease(("9.9.6", "9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN)
        self.code = self.tmp / "code"

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, path, version, sync=True):
        root = self.code / path
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n', encoding="utf-8")
        if sync:
            self.assert_ok(self.run_cortex(root, "sync"))
        return root

    def run_cortex(self, cwd, *args):
        proc = harness.run(*args, cwd=cwd, env=self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)

    def in_store(self):
        return sorted(entry.name for entry in (self.home / "versions").iterdir() if not entry.name.startswith("."))


@DOWNLOADS
class PruneTests(PruneTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.project("a", "9.9.7")
        self.b = self.project("deep/b", "9.9.8")
        gone = self.project("gone", "9.9.6")       # a project that synced 9.9.6, then left the machine
        shutil.rmtree(gone)
        own = self.project("own", OWN)
        shutil.rmtree(own)

    def test_it_removes_the_versions_no_project_pins_and_keeps_its_own(self):
        self.assertEqual(self.in_store(), ["9.9.6", "9.9.7", "9.9.8", OWN])
        proc = self.run_cortex(self.tmp, "prune", "code")
        self.assert_ok(proc)
        self.assertEqual(self.in_store(), ["9.9.7", "9.9.8", OWN])
        self.assertIn("2 project(s) found under", proc.out)
        self.assertIn(f"  kept     9.9.7 — used by {str(self.a).replace(os.sep, '/')}\n", proc.out)
        self.assertIn("  kept     9.9.8 — used by ", proc.out)
        self.assertIn(f"  kept     {OWN} — this cortex's own", proc.out)
        self.assertIn("  removed  9.9.6", proc.out)

    def test_the_version_a_project_reads_now_is_kept_until_it_syncs(self):
        # A teammate's bump, pulled and not synced: cortex.toml pins 9.9.6, and spec still reads 9.9.7.
        (self.a / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code")
        self.assert_ok(proc)
        self.assertEqual(self.in_store(), ["9.9.6", "9.9.7", "9.9.8", OWN])

    def test_a_key_this_cortex_does_not_know_loses_no_version(self):
        (self.a / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\nlater = "x"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code")
        self.assert_ok(proc)
        self.assertIn("only its version, 9.9.6, is read", proc.err)
        self.assertIn("9.9.6", self.in_store())

    def test_a_project_is_counted_once_and_through_a_link(self):
        elsewhere = self.tmp / "elsewhere" / "c"
        elsewhere.mkdir(parents=True)
        (elsewhere / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        try:
            os.symlink(elsewhere, self.code / "c", target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic link here")
        proc = self.run_cortex(self.tmp, "prune", "code", "code/a", "code")
        self.assert_ok(proc)
        self.assertIn("3 project(s) found", proc.out)
        self.assertIn("9.9.6", self.in_store())

    def test_a_copy_sync_made_is_not_searched(self):
        project = self.project("copied", "9.9.7")
        (project / "cortex.toml").write_text('version = "9.9.7"\ntheme = "h2g2"\nsync = "copy"\n', encoding="utf-8")
        self.assert_ok(self.run_cortex(project, "sync"))
        copy = project / "cortex"
        os.chmod(copy, 0o755)
        (copy / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code")
        self.assert_ok(proc)
        self.assertIn("3 project(s) found", proc.out)
        self.assertNotIn("9.9.6", self.in_store())

    def test_keep_and_dry_run(self):
        proc = self.run_cortex(self.tmp, "prune", "code", "--keep", "9.9.6")
        self.assert_ok(proc)
        self.assertIn("  kept     9.9.6 — --keep", proc.out)
        self.assertIn("Nothing to remove.", proc.out)
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn("  would remove  9.9.6", proc.out)
        self.assertEqual(self.in_store(), ["9.9.6", "9.9.7", "9.9.8", OWN])

    def test_what_is_not_a_project_of_the_machine_is_not_counted(self):
        for path in ("a/node_modules/dep", "a/.git/modules/x", "b/vendor/lib", ".venv/x"):
            (self.code / path).mkdir(parents=True)
            (self.code / path / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        seven = self.code.joinpath(*"1234567")             # deeper than six directories
        seven.mkdir(parents=True)
        (seven / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code")
        self.assert_ok(proc)
        self.assertIn("  removed  9.9.6", proc.out)
        self.assertIn("2 project(s) found", proc.out)

    def test_a_project_that_cannot_be_read_is_named(self):
        broken = self.code / "broken"
        broken.mkdir()
        (broken / "cortex.toml").write_text('version = "1.4"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn("broken: cortex.toml", proc.err)
        self.assertIn("its version is not known", proc.err)

    def test_a_pruned_version_comes_back_at_the_next_sync_and_still_matches_the_lock(self):
        returning = self.project("returning", "9.9.6")
        lock = (returning / "cortex.lock").read_text(encoding="utf-8")
        moved = self.tmp / "elsewhere"
        shutil.move(str(returning), str(moved))           # out of the directories prune is shown
        self.assert_ok(self.run_cortex(self.tmp, "prune", "code"))
        self.assertNotIn("9.9.6", self.in_store())
        proc = self.run_cortex(moved, "sync")
        self.assert_ok(proc)
        self.assertIn("downloaded to the store", proc.out)
        self.assertEqual((moved / "cortex.lock").read_text(encoding="utf-8"), lock)


class PruneArgumentTests(PruneTestCase):
    def test_no_directory_exits_2(self):
        proc = self.run_cortex(self.tmp, "prune")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("DIR", proc.err)

    def test_a_directory_that_is_not_there_exits_2(self):
        proc = self.run_cortex(self.tmp, "prune", "nowhere")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("nowhere: no such directory", proc.err)

    def test_a_keep_that_is_no_version_exits_2(self):
        self.code.mkdir()
        proc = self.run_cortex(self.tmp, "prune", "code", "--keep", "1.4")
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--keep "1.4" is no version', proc.err)

    def test_the_command_is_listed(self):
        self.assertIn(b"  prune ", harness.run("--help").stdout)


if __name__ == "__main__":
    unittest.main()
