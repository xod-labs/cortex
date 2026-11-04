"""``cortex prune`` — the versions no project uses, out of the store (ADR-010 §3.6, ADR-011 §3.4).

From source the command stands for Cortex 9.9.9; 9.9.6, 9.9.7 and 9.9.8 are served from this machine
(``release_fixture``) and reach the store through ``cortex sync``. A built binary downloads nothing:
the tests that need other versions skip there. The tests of the registry — ``cortex prune`` with no
``DIR`` — put their versions in the store by hand, and run through the binary too.
"""

import errno
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
from cortex_cli import prune, registry, store, sync  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from cortex_cli.version import VERSION  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
PERMISSIONS = unittest.skipIf(AS_ROOT or os.name == "nt", "root reads every directory, and Windows has no chmod 000")


def display(path):
    return str(path).replace(os.sep, "/")


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
        # A bump not synced, beside a key of a later cortex — in either file: both versions are kept.
        local = (self.a / "cortex.local.toml").read_text(encoding="utf-8")
        for name in ("cortex.toml", "cortex.local.toml"):
            with self.subTest(file=name):
                (self.a / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n'
                                                    + ('later = "x"\n' if name == "cortex.toml" else ""), encoding="utf-8")
                (self.a / "cortex.local.toml").write_text(local + ('later = "x"\n' if name == "cortex.local.toml" else ""),
                                                          encoding="utf-8")
                proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
                self.assert_ok(proc)
                self.assertIn(f'{name}: unknown key "later"', proc.err)
                self.assertIn("only its versions are read: 9.9.6, 9.9.7", proc.err)
                self.assertIn("  kept     9.9.6 — used by ", proc.out)
                self.assertIn("  kept     9.9.7 — used by ", proc.out)

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
        self.assertIn(f"  kept     9.9.7 — used by {str(self.a).replace(os.sep, '/')}\n", proc.out)    # once
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

    def test_six_directories_down_is_the_limit(self):
        six, seven = self.code.joinpath(*"123456"), self.code.joinpath(*"x234567")
        for directory, version in ((six, "9.9.6"), (seven, "9.9.6")):
            directory.mkdir(parents=True)
            (directory / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn("3 project(s) found", proc.out)          # a, b, and the one six directories down
        self.assertIn(f"  kept     9.9.6 — used by {str(six).replace(os.sep, '/')}\n", proc.out)

    def test_a_directory_reached_through_a_deeper_link_first_is_still_searched(self):
        # Reached first through the link, six directories down, the target's children were cut, and
        # reached later by its own path, one down, it was skipped as seen: its project lost its version.
        # Both orders, since the order of a directory's entries is the file system's.
        for chain, target in (("a", "z"), ("z", "a")):
            with self.subTest(link_under=chain):
                code = self.tmp / f"links-{chain}"
                deep = code.joinpath(chain, "b", "c", "d", "e")
                deep.mkdir(parents=True)
                project = code / target / "p" / "q"
                project.mkdir(parents=True)
                (project / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
                try:
                    os.symlink(code / target, deep / "lnk", target_is_directory=True)
                except OSError:
                    self.skipTest("no symbolic link here")
                proc = self.run_cortex(self.tmp, "prune", code.name, "--dry-run")
                self.assert_ok(proc)
                self.assertIn("1 project(s) found", proc.out)
                self.assertIn("  kept     9.9.6 — used by ", proc.out)

    def test_a_project_that_cannot_be_read_is_named(self):
        broken = self.code / "broken"
        broken.mkdir()
        (broken / "cortex.toml").write_text('version = "1.4"\n', encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn("broken: cortex.toml", proc.err)
        self.assertIn("its version is not known", proc.err)

    @unittest.skipIf(os.name == "nt" or AS_ROOT, "a directory its owner cannot read: POSIX, and not as root")
    def test_a_directory_that_cannot_be_read_is_named(self):
        locked = self.code / "locked"
        (locked / "inner").mkdir(parents=True)
        (locked / "inner" / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        os.chmod(locked, 0)
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"note: {str(locked).replace(os.sep, '/')} cannot be read — a project under it is not counted",
                      proc.err)
        self.assertIn("  would remove  9.9.6", proc.out)

    @unittest.skipIf(os.name == "nt" or AS_ROOT, "a file its owner cannot read: POSIX, and not as root")
    def test_a_project_file_that_cannot_be_opened_is_named(self):
        locked = self.code / "locked"
        locked.mkdir()
        (locked / "cortex.toml").write_text('version = "9.9.6"\ntheme = "h2g2"\n', encoding="utf-8")
        os.chmod(locked / "cortex.toml", 0)
        proc = self.run_cortex(self.tmp, "prune", "code", "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"note: {str(locked).replace(os.sep, '/')}: Permission denied: ", proc.err)
        self.assertIn("its version is not known, so none is kept for it", proc.err)
        self.assertIn("  would remove  9.9.6", proc.out)

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


class InProcessTests(unittest.TestCase):
    """What a command line cannot reach: a removal that fails, a sync meanwhile. The versions are
    directories with the three trees, which is all the store asks of one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-prune-")).resolve()
        self.addCleanup(self.cleanup)
        self.home, self.code = self.tmp / "home", self.tmp / "code"
        for version in ("9.9.5", "9.9.6", "9.9.7"):
            self.put(version)
        (self.code / "p").mkdir(parents=True)
        (self.code / "p" / "cortex.toml").write_text('version = "9.9.7"\ntheme = "h2g2"\n', encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    cleanup = PruneTestCase.cleanup       # a version a sync wrote is read-only

    def put(self, version):
        for tree in store.SPEC_TREES:
            (self.home / "versions" / version / tree).mkdir(parents=True)

    def in_store(self):
        return sorted(entry.name for entry in (self.home / "versions").iterdir() if not entry.name.startswith("."))

    def prune(self):
        out, err = io.StringIO(), io.StringIO()
        code = prune.prune([str(self.code)], [], False, out, err)
        return code, out.getvalue(), err.getvalue()

    def test_a_removal_that_fails_stops_none_of_the_others(self):
        real = os.rename

        def refused(src, dst):
            if Path(src).name == "9.9.5":
                raise PermissionError(errno.EACCES, "Permission denied", str(src))
            return real(src, dst)

        with mock.patch.object(store.os, "rename", refused):
            code, out, err = self.prune()
        self.assertEqual(code, 1)
        self.assertIn("cortex prune: 9.9.5 is still in the store — Permission denied: ", err)
        self.assertIn("  removed  9.9.6", out)
        self.assertEqual(self.in_store(), ["9.9.5", "9.9.7"])

    def test_a_version_renamed_out_and_not_all_deleted_is_said_to_be_out_of_the_store(self):
        real = store.remove_tree

        def half(root):
            if root.name.startswith(".9.9.5-removed-"):
                raise PermissionError(errno.EACCES, "Permission denied", str(root / "agents"))
            return real(root)

        with mock.patch.object(store, "remove_tree", half):
            code, out, err = self.prune()
        self.assertEqual(code, 1)
        self.assertIn("cortex prune: 9.9.5 is out of the store, but not all of its files were deleted", err)
        self.assertIn("A sync deletes what is left, a day from now", err)
        self.assertNotIn("still in the store", err)
        self.assertEqual(self.in_store(), ["9.9.7"])

    def test_a_version_is_renamed_out_of_the_store_before_a_file_of_it_is_deleted(self):
        # So that a sync meanwhile finds it whole or absent, never half deleted.
        real, deleted = store.remove_tree, []

        def watched(root):
            deleted.append((root.name, (self.home / "versions" / "9.9.5").exists()))
            return real(root)

        with mock.patch.object(store, "remove_tree", watched):
            self.assertEqual(self.prune()[0], 0)
        first = next(entry for entry in deleted if "9.9.5" in entry[0])
        self.assertTrue(first[0].startswith(".9.9.5-removed-"), deleted)
        self.assertFalse(first[1], "9.9.5 was still in its place when its files were deleted")

    def test_the_store_is_listed_before_the_projects_are_read(self):
        # A version a sync puts in the store while prune reads the projects is in no list, and stays.
        real = prune.versions_of

        def a_sync_meanwhile(root, err):
            if "9.9.4" not in self.in_store():
                self.put("9.9.4")
            return real(root, err)

        with mock.patch.object(prune, "versions_of", a_sync_meanwhile):
            code, out, _ = self.prune()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.in_store(), ["9.9.4", "9.9.7"])

    def test_a_sync_whose_version_a_prune_removes_writes_no_lock(self):
        # Removed between the sync's ensure() and its digest, the version gave the digest of no file,
        # which the lock kept: every later sync refused the project. Removed while it was read, some.
        project = self.tmp / "app"
        project.mkdir()
        (project / "cortex.toml").write_text(f'version = "{VERSION}"\ntheme = "h2g2"\n', encoding="utf-8")
        real, read = store.hashes, store.digest
        for when in ("first", "before", "after"):
            with self.subTest(removed=when):
                def pruned(directory, when=when):
                    if when == "after":
                        found = real(directory)
                    store.Store(self.home).remove(Version(VERSION))
                    return found if when == "after" else real(directory)

                def gone_first(directory):
                    store.Store(self.home).remove(Version(VERSION))
                    return read(directory)

                patched = ("digest", gone_first) if when == "first" else ("hashes", pruned)
                with mock.patch.object(store, *patched):
                    with self.assertRaisesRegex(store.StoreError, f"Cortex {VERSION} left the store while this command "
                                                                  "read it .* Nothing of the project was changed"):
                        sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
                self.assertFalse((project / "cortex.lock").exists())
                self.assertFalse((project / "cortex.local.toml").exists())
                sync.sync(str(project), None, None, io.StringIO(), io.StringIO())       # run again: it is ensured
                self.assertEqual((project / "cortex.lock").read_text(encoding="utf-8").count(store.digest(
                    store.Store(self.home).path(Version(VERSION)))), 1)
                for name in ("cortex.lock", "cortex.local.toml"):
                    (project / name).unlink()


class RegistryTestCase(unittest.TestCase):
    """``cortex prune`` with no ``DIR``: the projects are the registry's (ADR-011 §3.4). The versions
    are directories with the three trees, which is all the store asks of one, and the registry is
    written as ``cortex project`` writes it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-prune-")).resolve()
        self.addCleanup(self.cleanup)
        self.home, self.code = self.tmp / "cortex-home", self.tmp / "code"
        self.registry = self.home / "projects.toml"
        self.own = [OWN] if OWN else []
        # Nothing is downloaded: a version the store has not is no version of these tests.
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_SOURCE_VERSION=OWN or "9.9.9",
                                       CORTEX_RELEASES_URL="http://127.0.0.1:9")
        self.code.mkdir(parents=True)

    cleanup = PruneTestCase.cleanup
    run_cortex = PruneTestCase.run_cortex
    assert_ok = PruneTestCase.assert_ok
    in_store = PruneTestCase.in_store

    def put(self, *versions):
        for version in versions:
            for tree in store.SPEC_TREES:
                (self.home / "versions" / version / tree).mkdir(parents=True, exist_ok=True)

    def project(self, path, version, reads=None):
        """A project at ``path`` — under ``code``, unless absolute — pinning ``version``, and reading
        ``reads`` from the store, as a sync before a teammate's bump left it."""
        root = self.code / path
        root.mkdir(parents=True, exist_ok=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n', encoding="utf-8")
        if reads:
            (root / "cortex.local.toml").write_text(f'spec = "{display(self.home / "versions" / reads)}"\n',
                                                    encoding="utf-8")
        return root

    def register(self, **entries):
        self.home.mkdir(exist_ok=True)
        self.registry.write_text("format = 1\n" + "".join(f'\n[[project]]\nname = "{name}"\npath = "{display(path)}"\n'
                                                          for name, path in sorted(entries.items())), encoding="utf-8")

    def entries(self):
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            return [(entry.name, entry.path) for entry in registry.load()]

    def in_home(self):
        return sorted(entry.name for entry in self.home.iterdir())


class RegistryTests(RegistryTestCase):
    def test_with_no_dir_it_keeps_the_versions_of_the_registered_projects(self):
        # Pinned to A and B, and a store of A, B, C and the binary's own: C goes, only.
        self.put("9.9.5", "9.9.6", "9.9.7", *self.own)
        a = self.project("a", "9.9.6")
        b = self.project(self.tmp / "elsewhere" / "b", "9.9.7")          # under no DIR in common with a
        self.register(a=a, b=b)
        before = self.registry.read_bytes()
        proc = self.run_cortex(self.tmp, "prune")
        self.assert_ok(proc)
        self.assertEqual(self.in_store(), ["9.9.6", "9.9.7", *self.own])
        self.assertTrue(proc.out.startswith(f"2 project(s) registered in {display(self.registry)}\n"), proc.out)
        self.assertIn("  kept     9.9.6 — used by a\n  kept     9.9.7 — used by b\n", proc.out)
        if OWN:
            self.assertIn(f"  kept     {OWN} — this cortex's own\n", proc.out)
        self.assertIn("  removed  9.9.5\n", proc.out)
        self.assertTrue(proc.out.endswith("A project not registered gets its version back at its next `cortex sync`.\n"),
                        proc.out)
        self.assertEqual(proc.err, "")
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.in_home(), ["projects.toml", "versions"])        # no entry gone: not even locked

    def test_the_version_a_project_reads_now_and_each_keep_are_kept(self):
        # A teammate's bump, pulled and not synced: cortex.toml pins 9.9.5, and spec still reads 9.9.6.
        self.put("9.9.5", "9.9.6", "9.9.7", "9.9.8")
        self.register(a=self.project("a", "9.9.5", reads="9.9.6"))
        proc = self.run_cortex(self.tmp, "prune", "--keep", "9.9.7")
        self.assert_ok(proc)
        self.assertEqual(self.in_store(), ["9.9.5", "9.9.6", "9.9.7"])
        self.assertIn("  kept     9.9.5 — used by a\n"
                      "  kept     9.9.6 — used by a\n"
                      "  kept     9.9.7 — --keep\n", proc.out)
        self.assertIn("  removed  9.9.8\n", proc.out)

    def test_an_unreadable_entry_is_named_and_kept_with_the_versions_read_of_it(self):
        self.put("9.9.5", "9.9.6", "9.9.7")
        later = self.project("later", "9.9.6", reads="9.9.7")
        with open(later / "cortex.toml", "a", encoding="utf-8") as toml:
            toml.write('later = "x"\n')                                     # a key of a later cortex
        broken = self.project("broken", "9.9.5")
        (broken / "cortex.toml").write_text("version = \n", encoding="utf-8")
        self.register(later=later, broken=broken)
        before = self.registry.read_bytes()
        proc = self.run_cortex(self.tmp, "prune")
        self.assert_ok(proc)
        self.assertIn(f'note: later: {display(later)}: cortex.toml: unknown key "later"', proc.err)
        self.assertIn("— it stays in the registry, and only its versions are read: 9.9.6, 9.9.7\n", proc.err)
        self.assertIn(f"note: broken: {display(broken)}: cortex.toml: ", proc.err)
        self.assertIn("— it stays in the registry, and its version is not known, so none is kept for it\n", proc.err)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.in_store(), ["9.9.6", "9.9.7"])
        self.assertIn("  kept     9.9.6 — used by later\n", proc.out)

    @PERMISSIONS
    def test_a_project_that_cannot_be_reached_is_unreadable_never_gone(self):
        # A directory that cannot be searched is no proof that the project is gone.
        self.put("9.9.6")
        inside = self.project("closed/acme", "9.9.6")
        self.register(acme=inside)
        before = self.registry.read_bytes()
        os.chmod(self.code / "closed", 0)
        proc = self.run_cortex(self.tmp, "prune")
        self.assert_ok(proc)
        self.assertIn(f"note: acme: {display(inside)}: Permission denied", proc.err)
        self.assertIn("it stays in the registry, and its version is not known", proc.err)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_an_empty_registry_and_no_dir_exits_2(self):
        self.put("9.9.6", *self.own)
        for case in ("none", "empty"):
            with self.subTest(registry=case):
                if case == "empty":
                    self.register()
                proc = self.run_cortex(self.tmp, "prune")
                self.assertEqual(proc.returncode, 2, proc.out + proc.err)
                self.assertEqual(proc.err, "cortex prune: no DIR, and no project registered on this machine: pruning "
                                           "would remove every version. Give the directories of the projects — cortex "
                                           "prune DIR... — or register each one: `cortex project add` in it\n")
                self.assertEqual(self.in_store(), ["9.9.6", *self.own])
                self.assertEqual(self.in_home(), ["versions"] if case == "none" else ["projects.toml", "versions"])

    def test_with_dir_the_registry_is_neither_read_nor_written(self):
        self.put("9.9.6", "9.9.7", *self.own)
        self.project("a", "9.9.6")
        gone = f'format = 1\n\n[[project]]\nname = "gone"\npath = "{display(self.tmp / "gone")}"\n'
        for case, text in (("refused if read", "format = 2\n"), ("an entry gone", gone)):
            with self.subTest(registry=case):
                self.put("9.9.7")
                self.registry.write_text(text, encoding="utf-8")
                proc = self.run_cortex(self.tmp, "prune", "code")
                self.assert_ok(proc)
                self.assertIn("1 project(s) found under", proc.out)
                self.assertEqual(self.in_store(), ["9.9.6", *self.own])
                self.assertEqual(self.registry.read_text(encoding="utf-8"), text)
                self.assertEqual(self.in_home(), ["projects.toml", "versions"])

    def test_a_registry_it_refuses_removes_nothing(self):
        self.put("9.9.6", *self.own)
        self.home.mkdir(exist_ok=True)
        self.registry.write_text("format = 2\n", encoding="utf-8")
        proc = self.run_cortex(self.tmp, "prune")
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.err.startswith(f"cortex prune: {display(self.registry)}: format 2 is written by a later "
                                            "Cortex"), proc.err)
        self.assertIn("cortex self-update", proc.err)
        self.assertEqual(self.in_store(), ["9.9.6", *self.own])


class RegistryInProcessTests(RegistryTestCase):
    """What a command line cannot reach: a sync meanwhile."""

    def setUp(self):
        super().setUp()
        self.put("9.9.5", "9.9.6", "9.9.7")
        self.a, self.gone = self.project("a", "9.9.7"), self.code / "gone"
        self.register(a=self.a, gone=self.gone)
        environment = mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def prune(self, dry_run=False):
        out, err = io.StringIO(), io.StringIO()
        code = prune.prune([], [], dry_run, out, err)
        return code, out.getvalue(), err.getvalue()

    def test_the_store_is_listed_before_the_projects_are_read(self):
        # A version a sync puts in the store while prune reads the projects is in no list, and stays.
        real = registry.state

        def a_sync_meanwhile(entry):
            self.put("9.9.4")
            return real(entry)

        with mock.patch.object(registry, "state", a_sync_meanwhile):
            code, out, _ = self.prune()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.in_store(), ["9.9.4", "9.9.7"])


class PruneArgumentTests(PruneTestCase):
    def test_no_directory_and_no_registry_exits_2(self):
        proc = self.run_cortex(self.tmp, "prune")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no DIR, and no project registered on this machine", proc.err)
        self.assertIn("`cortex project add`", proc.err)

    def test_a_directory_is_optional(self):
        proc = self.run_cortex(self.tmp, "prune", "--help")
        self.assert_ok(proc)
        self.assertIn("[DIR ...]", proc.out)

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
