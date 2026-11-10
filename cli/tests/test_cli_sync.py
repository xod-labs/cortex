"""``cortex sync`` and the store — ADR-008 §3.3, §3.5, phase 2.

The command runs against a store of its own (``CORTEX_HOME``) and a release served from this
machine (``release_fixture``). From source it stands for Cortex 9.9.9 (``CORTEX_SOURCE_VERSION``),
so that it may download the fake releases 9.9.7 and 9.9.8. A built binary — a release's, or a
build of no release's, ``X.Y.Z-dev.N`` — is older than them, and serves only its own version: the
tests that download skip there.
"""

import errno
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
import cli_harness as harness  # noqa: E402
from cortex_cli import store, sync  # noqa: E402
from cortex_cli.store import digest  # noqa: E402
from cortex_cli.sync import link_target  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
# Nothing of a build of no release's version, X.Y.Z-dev.N, is said to commit, its lock included (ADR-009 §9): from a
# release, a candidate or the source's 9.9.9, a lock to write in a repository is.
THEN_COMMIT_IT = "" if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-dev(\.[0-9]+)?", OWN) else ", then commit it"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
TREES = ["agents", "docs", "templates"]
REGISTRY_HEADER = "# The Cortex projects of this machine (ADR-011). Written by cortex — `cortex project` edits it.\n"
LISTS = "`cortex project list` lists this machine's projects"


def display(path):
    return str(path).replace(os.sep, "/")


def snapshot(root):
    """Everything under ``root``: files as bytes, links as their target."""
    if os.path.islink(root):
        return {".": ("link", os.readlink(root))}
    if root.is_file():
        return {".": root.read_bytes()}
    return {str(p.relative_to(root)): (("link", os.readlink(p)) if p.is_symlink() else
                                       p.read_bytes() if p.is_file() else "dir")
            for p in sorted(root.rglob("*"))}


def is_junction(path):
    return getattr(os.lstat(path), "st_reparse_tag", None) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-sync-")).resolve()
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "cortex-home"
        self.release = FakeRelease(("9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN)

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, name="app", version=OWN, extra=""):
        root = self.tmp / name
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n{extra}', encoding="utf-8")
        return root

    def sync(self, project, *args, cwd=None):
        proc = harness.run("sync", *args, cwd=cwd or project, env=self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def validate(self, project, *args):
        proc = harness.run("validate", *args, cwd=project, env=self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def spec(self, project):
        text = (project / "cortex.local.toml").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.startswith("spec = "))
        return line.split('"')[1]

    def stored(self, version=OWN):
        return self.home / "versions" / version

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.err)


class StoreModeTests(SyncTestCase):
    def test_its_own_version_is_written_from_itself_without_a_network(self):
        project = self.project()
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn("written to the store, from this cortex", proc.out)
        self.assertEqual(sorted(p.name for p in self.stored().iterdir()), TREES)
        self.assertTrue((self.stored() / "agents" / "roles" / "prompt-manager.md").is_file())
        self.assertEqual(self.release.requested, [])
        self.assertEqual(self.spec(project), display(self.stored()))

    def test_it_writes_nothing_in_the_project_but_cortex_local_toml_and_the_lock(self):
        project = self.project()
        before = sorted(p.name for p in project.iterdir())
        self.assert_ok(self.sync(project))
        self.assertEqual(sorted(p.name for p in project.iterdir()), sorted(before + ["cortex.local.toml", "cortex.lock"]))

    def test_a_second_sync_finds_it_stored(self):
        project = self.project()
        self.sync(project)
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn("in the store", proc.out)

    def test_a_subdirectory_finds_its_project(self):
        project = self.project()
        deep = project / "src" / "deep"
        deep.mkdir(parents=True)
        self.assert_ok(self.sync(project, cwd=deep))
        self.assertTrue((project / "cortex.local.toml").is_file())
        self.assertFalse((deep / "cortex.local.toml").exists())

    def test_the_developers_theme_and_comments_are_kept(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('# mine\ntheme = "star-wars"  # yes\n', encoding="utf-8")
        self.assert_ok(self.sync(project))
        text = (project / "cortex.local.toml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('# mine\ntheme = "star-wars"  # yes\nspec = "'), text)

    @DOWNLOADS
    def test_two_projects_on_two_versions_and_a_third_that_downloads_nothing(self):
        first, second, third = self.project("a", "9.9.8"), self.project("b", "9.9.7"), self.project("c", "9.9.8")
        for project in (first, second):
            proc = self.sync(project)
            self.assert_ok(proc)
            self.assertIn("downloaded to the store, checked against its SHA256SUMS", proc.out)
        self.assertEqual(self.spec(first), display(self.stored("9.9.8")))
        self.assertEqual(self.spec(second), display(self.stored("9.9.7")))
        self.assertIn("Cortex 9.9.8", (self.stored("9.9.8") / "agents/roles/prompt-manager.md").read_text(encoding="utf-8"))
        requested = self.release.requested
        proc = self.sync(third)
        self.assert_ok(proc)
        self.assertIn("in the store", proc.out)
        self.assertEqual(self.release.requested, requested)

    @DOWNLOADS
    def test_a_spec_whose_checksum_differs_is_refused_and_leaves_nothing(self):
        self.release.alter_one_byte("spec", "9.9.8")
        proc = self.sync(self.project(version="9.9.8"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not match its checksum", proc.err)
        self.assertEqual(list((self.home / "versions").iterdir()), [])

    @DOWNLOADS
    def test_an_archive_leaving_its_tree_is_refused_and_leaves_nothing(self):
        for name in ("../escape.md", "/etc/cortex.md", "agents/../../escape.md"):
            with self.subTest(member=name):
                self.release.replace_spec("9.9.8", {name: b"x"})
                proc = self.sync(self.project(f"p{len(name)}", "9.9.8"))
                self.assertEqual(proc.returncode, 1)
                self.assertIn("which a spec archive cannot hold", proc.err)
                self.assertEqual(list((self.home / "versions").iterdir()), [])
                self.assertFalse((self.tmp / "escape.md").exists())

    @DOWNLOADS
    def test_a_release_without_a_spec_archive_is_refused(self):
        sums = self.release.root / "download" / "9.9.8" / "SHA256SUMS"
        sums.write_text("".join(l for l in sums.read_text().splitlines(True) if "cortex-spec" not in l))
        proc = self.sync(self.project(version="9.9.8"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("lists no cortex-spec.tar.gz", proc.err)

    def test_a_version_newer_than_the_binary_is_refused_with_the_self_update_command(self):
        proc = self.sync(self.project(version="99.0.0"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"newer than this cortex, {OWN} — update it:\n    cortex self-update 99.0.0", proc.err)
        self.assertFalse(self.home.exists() and any((self.home / "versions").iterdir()))

    @DOWNLOADS
    def test_a_version_before_the_first_spec_archive_is_refused(self):
        proc = self.sync(self.project(version="0.10.1"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("has no spec archive: the first one is 1.0.0", proc.err)
        self.assertEqual(self.release.requested, [])


@unittest.skipIf(AS_ROOT, "root writes any file")
class ReadOnlyStoreTests(SyncTestCase):
    def assert_cannot_write(self, path):
        with self.assertRaises(PermissionError):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("edited through a project\n")
        with self.assertRaises(PermissionError):
            os.remove(path)

    def test_a_file_of_the_store_cannot_be_written_in_place(self):
        project = self.project()
        self.assert_ok(self.sync(project))
        self.assert_cannot_write(self.stored() / "agents" / "roles" / "prompt-manager.md")

    def test_nor_through_the_link(self):
        project = self.project()
        self.assert_ok(self.sync(project, "--link"))
        self.assert_cannot_write(project / "cortex" / "agents" / "roles" / "prompt-manager.md")

    def test_nor_in_a_copy(self):
        project = self.project()
        self.assert_ok(self.sync(project, "--copy"))
        self.assert_cannot_write(project / "cortex" / "agents" / "roles" / "prompt-manager.md")

    @unittest.skipIf(os.name == "nt", "on Windows a read-only directory still takes new files — ADR-008 §9")
    def test_nor_can_a_file_be_added(self):
        project = self.project()
        self.assert_ok(self.sync(project))
        with self.assertRaises(PermissionError):
            (self.stored() / "agents" / "roles" / "new.md").write_text("x", encoding="utf-8")


class ForeignCortexTests(SyncTestCase):
    """Sync never deletes what it did not write — a submodule, a clone, any directory, in every mode."""

    def foreign(self, project, kind):
        cortex = project / "cortex"
        if kind == "file":
            cortex.write_text("mine\n", encoding="utf-8")
            return cortex
        (cortex / "agents").mkdir(parents=True)
        (cortex / "agents" / "notes.md").write_text("mine\n", encoding="utf-8")
        if kind == "submodule":
            (cortex / ".git").write_text("gitdir: ../.git/modules/cortex\n", encoding="utf-8")
        elif kind == "clone":
            (cortex / ".git").mkdir()
            (cortex / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        return cortex

    def test_refused_and_untouched_in_every_mode(self):
        described = {"submodule": "a git submodule", "clone": "a git clone", "directory": "a directory", "file": "a file"}
        for kind, words in described.items():
            for mode in ("--store", "--link", "--copy"):
                with self.subTest(kind=kind, mode=mode):
                    project = self.project(f"{kind}{mode}")
                    cortex = self.foreign(project, kind)
                    before = snapshot(cortex)
                    proc = self.sync(project, mode)
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn(f"cortex/ is {words}, which Cortex did not write", proc.err)
                    self.assertEqual(snapshot(cortex), before)
                    self.assertFalse((project / "cortex.local.toml").exists())

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_a_link_it_did_not_make_is_refused(self):
        project = self.project()
        elsewhere = self.tmp / "my-cortex"
        (elsewhere / "agents").mkdir(parents=True)
        (project / "cortex").symlink_to(elsewhere, target_is_directory=True)
        proc = self.sync(project, "--link")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("which Cortex did not make", proc.err)
        self.assertEqual(os.readlink(project / "cortex"), str(elsewhere))

    def test_a_link_to_a_checkout_that_is_gone_is_replaced_by_a_link_sync_from_a_checkout_only(self):        # a junction on Windows: no privilege
        checkout, moved = self.tmp / "checkout", self.tmp / "moved"
        for tree in ("agents", "templates", "docs"):
            (checkout / tree).mkdir(parents=True)
        project = self.project()
        self.assert_ok(self.sync(project, "--link", "--from", str(checkout)))
        pointed = link_target(project / "cortex")
        checkout.rename(moved)
        for args in ((), ("--link",), ("--copy", "--from", str(moved)), ("--from", str(moved))):
            with self.subTest(args=args):                               # no checkout named, or no link made
                proc = self.sync(project, *args)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"cortex/ is a link to {display(pointed)}, which leads nowhere — a checkout or a store "
                              "moved or deleted since it was linked. `cortex sync --link --from` the checkout it should "
                              "follow, which replaces it, or remove it, then `cortex sync` for the version cortex.toml "
                              "pins.", proc.err)
                self.assertEqual(link_target(project / "cortex"), pointed)
        proc = self.sync(project, "--link", "--from", str(moved))       # where the checkout is now
        self.assert_ok(proc)
        self.assertIn(f"cortex/ was a link to {display(pointed)}, which led nowhere: replaced.\n", proc.out)
        self.assertEqual(os.path.realpath(project / "cortex"), os.path.realpath(moved))
        self.assertEqual(self.spec(project), "cortex")
        self.assertEqual([p.name for p in project.iterdir() if p.name.startswith(".cortex-")], [])

    def test_a_project_that_links_by_default_replaces_it_from_a_checkout_too(self):        # a junction on Windows: no privilege
        checkout, moved = self.tmp / "checkout", self.tmp / "moved"
        for tree in ("agents", "templates", "docs"):
            (checkout / tree).mkdir(parents=True)
        project = self.project(extra='sync = "link"\n')
        self.assert_ok(self.sync(project, "--from", str(checkout)))
        checkout.rename(moved)
        proc = self.sync(project, "--from", str(moved))
        self.assert_ok(proc)
        self.assertIn("which led nowhere: replaced.", proc.out)
        self.assertEqual(os.path.realpath(project / "cortex"), os.path.realpath(moved))

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_a_link_into_a_store_moved_since_is_said_to_lead_nowhere(self):
        project = self.project()
        self.assert_ok(self.sync(project, "--link"))                    # into the store: spec = "cortex"
        pointed = os.readlink(project / "cortex")
        moved = self.tmp / "cortex-home-moved"
        self.home.rename(moved)
        self.home = moved
        self.env["CORTEX_HOME"] = str(moved)                            # CORTEX_HOME changed, the old one gone
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"cortex/ is a link to {display(pointed)}, which leads nowhere — a checkout or a store "
                      "moved or deleted since it was linked. `cortex sync --link --from` the checkout it should "
                      "follow, which replaces it, or remove it, then `cortex sync`", proc.err)
        self.assertNotIn("which Cortex did not make", proc.err)
        self.assertEqual(os.readlink(project / "cortex"), pointed)
        os.unlink(project / "cortex")                                   # what it says to do
        self.assert_ok(self.sync(project))

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_only_the_link_a_project_reads_is_said_to_lead_nowhere(self):
        elsewhere = self.tmp / "my-cortex"
        (elsewhere / "agents").mkdir(parents=True)                      # there, and no checkout
        linked = self.project("linked")
        self.assert_ok(self.sync(linked, "--link"))                     # it reads cortex/: spec = "cortex"
        os.unlink(linked / "cortex")
        (linked / "cortex").symlink_to(elsewhere, target_is_directory=True)
        dangling = self.project("dangling")                             # it reads no cortex/
        (dangling / "cortex").symlink_to(self.tmp / "nowhere", target_is_directory=True)
        checkout = self.tmp / "checkout"
        for tree in ("agents", "templates", "docs"):
            (checkout / tree).mkdir(parents=True)
        for project in (linked, dangling):
            for args in (("--link",), ("--link", "--from", str(checkout))):  # never replaced, a checkout named or not
                with self.subTest(project=project.name, args=args):
                    pointed = os.readlink(project / "cortex")
                    proc = self.sync(project, *args)
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn(f"cortex/ is a link to {display(pointed)}, which Cortex did not make", proc.err)
                    self.assertNotIn("leads nowhere", proc.err)
                    self.assertEqual(os.readlink(project / "cortex"), pointed)


class LinkAndCopyTests(SyncTestCase):
    def assert_link_to(self, project, target):
        link = project / "cortex"
        if os.name == "nt":
            self.assertTrue(is_junction(link), "a junction, which needs no privilege")
        else:
            self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(link), os.path.realpath(target))

    def test_link(self):
        project = self.project()
        proc = self.sync(project, "--link")
        self.assert_ok(proc)
        self.assert_link_to(project, self.stored())
        self.assertEqual(self.spec(project), "cortex")
        self.assertIn(f"Project: {display(project)}", proc.out)

    def test_back_to_store_removes_the_link_and_only_it(self):
        project = self.project()
        self.sync(project, "--link")
        store_before = snapshot(self.stored())
        self.assert_ok(self.sync(project, "--store"))
        self.assertFalse(os.path.lexists(project / "cortex"))
        self.assertEqual(snapshot(self.stored()), store_before)
        self.assertEqual(self.spec(project), display(self.stored()))

    def test_copy(self):
        project = self.project()
        (project / ".gitignore").write_text("/cortex/\n", encoding="utf-8")
        proc = self.sync(project, "--copy")
        self.assert_ok(proc)
        copy = project / "cortex"
        self.assertFalse(os.path.islink(copy))
        self.assertEqual(sorted(p.name for p in copy.iterdir()), sorted(TREES + [".synced"]))
        marker = json.loads((copy / ".synced").read_text(encoding="utf-8"))
        self.assertEqual((marker["version"], marker["from"]), (OWN, None))
        self.assertIn("agents/roles/prompt-manager.md", marker["files"])
        self.assertEqual(self.spec(project), "cortex")

    def test_back_to_store_removes_the_copy_and_only_it(self):
        project = self.project()
        (project / "notes.md").write_text("mine\n", encoding="utf-8")
        self.sync(project, "--copy")
        self.assert_ok(self.sync(project))
        self.assertEqual(sorted(p.name for p in project.iterdir()), ["cortex.local.toml", "cortex.lock", "cortex.toml", "notes.md"])
        self.assertTrue(self.stored().is_dir())

    def test_cortex_toml_sets_the_mode_and_a_flag_overrides_it(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        self.assertTrue((project / "cortex" / ".synced").is_file())
        self.assert_ok(self.sync(project, "--link"))
        self.assert_link_to(project, self.stored())

    @DOWNLOADS
    def test_a_new_version_moves_the_link(self):
        project = self.project(version="9.9.7", extra='sync = "link"\n')
        self.assert_ok(self.sync(project))
        (project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\nsync = "link"\n', encoding="utf-8")
        self.assert_ok(self.sync(project))
        self.assert_link_to(project, self.stored("9.9.8"))

    @DOWNLOADS
    def test_a_new_version_replaces_the_copy(self):
        project = self.project(version="9.9.7", extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        (project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\nsync = "copy"\n', encoding="utf-8")
        self.assert_ok(self.sync(project))
        self.assertIn("Cortex 9.9.8", (project / "cortex/agents/roles/prompt-manager.md").read_text(encoding="utf-8"))


@unittest.skipIf(shutil.which("git") is None, "needs git")
class GitIgnoreTests(SyncTestCase):
    """What git ignores, asked of git: a link is a file to git, which `cortex/` does not match."""

    def repository(self, ignore):
        project = self.project()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        (project / ".gitignore").write_text(ignore, encoding="utf-8")
        return project

    def check_ignore(self, project, name):
        return subprocess.run(["git", "-C", str(project), "check-ignore", "-q", name]).returncode == 0

    @unittest.skipIf(os.name == "nt", "a junction is a directory to git on Windows")
    def test_a_link_is_not_ignored_by_a_trailing_slash(self):
        project = self.repository("cortex.local.toml\n/cortex/\n")
        proc = self.sync(project, "--link")
        self.assert_ok(proc)
        self.assertFalse(self.check_ignore(project, "cortex"))
        self.assertIn("add a `/cortex` line to .gitignore, without the final slash", proc.err)

    def test_without_the_slash_a_link_and_a_copy_are_ignored(self):
        for mode in ("--link", "--copy"):
            with self.subTest(mode=mode):
                project = self.repository("cortex.local.toml\n/cortex\n")
                shutil.move(str(project), str(self.tmp / f"repo{mode}"))
                project = self.tmp / f"repo{mode}"
                proc = self.sync(project, mode)
                self.assert_ok(proc)
                self.assertTrue(self.check_ignore(project, "cortex"))
                self.assertNotIn("note:", proc.err)

    def test_cortex_local_toml_not_ignored_is_said(self):
        project = self.repository("")
        proc = self.sync(project)
        self.assertIn("git does not ignore cortex.local.toml", proc.err)


class HousekeepingTests(SyncTestCase):
    """What an interrupted or a racing sync leaves, and a copy that lost a file."""

    def test_a_copy_missing_a_file_is_refused_by_validate_and_copied_again_by_sync(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        roles = project / "cortex" / "agents" / "roles"
        os.chmod(roles, 0o755)
        (roles / "prompt-manager.md").chmod(0o644)
        (roles / "prompt-manager.md").unlink()
        proc = self.validate(project)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no longer holds what sync copied", proc.err)
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn("lacked 1 of the files sync copied — agents/roles/prompt-manager.md: copied again", proc.err)
        self.assertTrue((roles / "prompt-manager.md").is_file())
        self.assertEqual(self.validate(project).returncode, 0)

    def test_what_an_interrupted_sync_left_is_removed(self):
        # A link among them names this machine's store, and /cortex does not keep it out of a commit.
        project = self.project()
        self.assert_ok(self.sync(project))
        old_copy, fresh = project / ".cortex-sync-abandoned", project / ".cortex-sync-2-live"
        (old_copy / "agents").mkdir(parents=True)
        (old_copy / "agents" / "x.md").write_text("x\n", encoding="utf-8")
        os.chmod(old_copy / "agents" / "x.md", 0o444)
        fresh.mkdir()
        os.utime(old_copy, (1, 1))
        # A link's own time is set without following it on POSIX only; a junction's cannot be.
        old_link = project / ".cortex-sync-1-dead"
        if os.name != "nt":
            old_link.symlink_to(self.stored(), target_is_directory=True)
            os.utime(old_link, (1, 1), follow_symlinks=False)
        self.assert_ok(self.sync(project))
        self.assertFalse(os.path.lexists(old_link))
        self.assertFalse(os.path.lexists(old_copy))
        self.assertFalse(os.path.lexists(fresh))           # under the project's lock, no sync is using it
        self.assertTrue(self.stored().is_dir())

    def test_syncs_of_one_project_run_one_after_the_other(self):
        # Racing, they failed in most runs with an errno on paths of their own; under the project's
        # lock each one waits for the one before it.
        project = self.project()
        self.assert_ok(self.sync(project, "--copy"))
        env = dict(self.env, PWD=str(project))
        procs = [subprocess.Popen(harness.command("sync", mode), cwd=project, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for mode in ("--link", "--copy") * 4]
        results = [(p.wait(timeout=300), p.stderr.read().decode()) for p in procs]
        for proc in procs:
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual([code for code, _ in results], [0] * 8, "\n".join(err for _, err in results))
        self.assertEqual(list(project.glob(".cortex-sync-*")), [])
        self.assertEqual(self.spec(project), "cortex")
        self.assertTrue(os.path.lexists(project / "cortex"))
        self.assert_ok(self.validate(project))

    def test_a_kept_copy_left_open_is_sealed_again(self):
        if os.name == "nt":
            self.skipTest("the mode of a directory is POSIX's")
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        os.chmod(project / "cortex", 0o755)                 # a sync killed between its rename and the seal
        self.assert_ok(self.sync(project))
        self.assertFalse(os.stat(project / "cortex").st_mode & 0o222)

    def test_a_failed_write_of_cortex_local_toml_puts_cortex_back(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        before = snapshot(project / "cortex")
        (project / "cortex.local.toml").unlink()
        (project / "cortex.local.toml").mkdir()
        proc = self.sync(project, "--link")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.err)
        self.assertEqual(snapshot(project / "cortex"), before)
        self.assertEqual(list(project.glob(".cortex-sync-*")), [])

    @unittest.skipIf(os.name == "nt", "the mode of a directory is POSIX's")
    def test_a_copy_is_moved_as_macos_moves_directories(self):
        # macOS renames no directory its owner may not write in: a copy sealed before it was put in
        # place, or moved aside sealed, would never move there.
        sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
        import errno
        import io
        from unittest import mock

        from cortex_cli import sync
        from cortex_cli.version import VERSION

        real = os.rename

        def as_macos(src, dst):
            if os.path.isdir(src) and not os.path.islink(src) and not os.stat(src).st_mode & stat.S_IWUSR:
                raise PermissionError(errno.EACCES, "Permission denied", str(src), None, str(dst))
            return real(src, dst)

        project = self.project(version=VERSION)
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), mock.patch.object(sync.os, "rename", as_macos):
            for mode in ("copy", "link", "copy", "store"):
                with self.subTest(mode=mode):
                    sync.sync(str(project), mode, None, io.StringIO(), io.StringIO())
                    if mode == "copy":
                        self.assertFalse(os.stat(project / "cortex").st_mode & 0o222)     # sealed once in place
        self.assertFalse(os.path.lexists(project / "cortex"))
        self.assertEqual(list(project.glob(".cortex-sync-*")), [])

    def test_a_sync_that_loses_a_race_says_so_and_leaves_nothing(self):
        sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
        import io
        from unittest import mock

        from cortex_cli import sync
        from cortex_cli.version import VERSION

        import errno

        real_swap, real_rename = sync.swap, os.rename

        def racing(target, new, old):
            (target / "agents").mkdir(parents=True)             # another sync's copy, put in place meanwhile
            return real_swap(target, new, old)

        def as_macos(src, dst):
            # macOS answers EACCES, not ENOTEMPTY, for a read-only directory renamed onto another.
            if os.path.isdir(dst):
                raise PermissionError(errno.EACCES, "Permission denied", str(src), None, str(dst))
            return real_rename(src, dst)

        for name, rename in (("this system", real_rename), ("macOS", as_macos)):
            with self.subTest(rename=name):
                project = self.project(f"app-{name.replace(' ', '-')}", version=VERSION, extra='sync = "copy"\n')
                with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), \
                        mock.patch.object(sync, "swap", racing), mock.patch.object(sync.os, "rename", rename):
                    with self.assertRaisesRegex(sync.SyncError, "was put back while this sync ran"):
                        sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
                self.assertEqual(list(project.glob(".cortex-sync-*")), [])


    def test_a_local_file_half_written_is_removed(self):
        # A sync killed while it wrote cortex.local.toml left its temporary file, which names this
        # machine's paths and which git does not ignore.
        project = self.project()
        left, mine = project / "cortex.local.toml.4242.tmp", project / "cortex.local.toml.mine.tmp"
        left.write_text('spec = "/home/someone/.cortex/versions/9.9.9"\n', encoding="utf-8")
        mine.write_text("mine\n", encoding="utf-8")
        self.assert_ok(self.sync(project))
        self.assertFalse(os.path.lexists(left))
        self.assertTrue(mine.is_file())                    # no name sync writes


def in_process():
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
    from cortex_cli import sync
    from cortex_cli.version import VERSION

    return sync, VERSION


class LockTests(SyncTestCase):
    """One sync of a project at a time; a file system without the lock stops no sync."""

    def test_a_sync_waits_for_the_lock_and_names_the_one_it_waited_for(self):
        sync, version = in_process()
        project = self.project(version=version)
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), mock.patch.object(sync, "LOCK_WAIT", 0.3):
            with sync.project_lock(project, io.StringIO()):
                with self.assertRaisesRegex(sync.SyncError, "another cortex sync of .* has run for 0.3 seconds"):
                    sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
                self.assertFalse((project / "cortex.local.toml").exists())
            err = io.StringIO()
            sync.sync(str(project), None, None, io.StringIO(), err)          # released: it runs
        self.assertNotIn("cannot be locked", err.getvalue())
        self.assertTrue((project / "cortex.local.toml").is_file())

    def test_a_file_system_without_the_lock_warns_and_does_not_wait(self):
        # NFS emulates flock with a POSIX lock, which a read-only descriptor cannot take: EBADF.
        sync, version = in_process()
        for code in (errno.EBADF, errno.ENOLCK, errno.EOPNOTSUPP, errno.EINVAL):
            with self.subTest(errno=errno.errorcode[code]):
                project = self.project(f"app-{errno.errorcode[code]}", version=version)

                tries = []

                def no_lock(fd, code=code):
                    # Counted, not timed: time.sleep is the process's, and subprocess waits with it on macOS.
                    tries.append(fd)
                    if len(tries) > 1:
                        raise AssertionError("tried again a lock that cannot be taken")
                    raise OSError(code, os.strerror(code))

                err = io.StringIO()
                # The registry's lock is tried with the same function, and its tests are cortex project's.
                with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), \
                        mock.patch.object(sync, "_try_lock", no_lock), mock.patch.object(sync, "register"):
                    sync.sync(str(project), None, None, io.StringIO(), err)
                self.assertEqual(len(tries), 1)
                self.assertIn(f"cannot be locked here ({os.strerror(code)})", err.getvalue())
                self.assertTrue((project / "cortex.local.toml").is_file())

    def test_two_projects_sync_at_the_same_time(self):
        # On Windows they share one lock file, a byte each.
        sync, version = in_process()
        one, two = self.project("one", version=version), self.project("two", version=version)
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), mock.patch.object(sync, "LOCK_WAIT", 0.3):
            with sync.project_lock(one, io.StringIO()):
                sync.sync(str(two), None, None, io.StringIO(), io.StringIO())
        self.assertTrue((two / "cortex.local.toml").is_file())

    def test_the_lock_leaves_one_file_at_most(self):
        # A lock file per project piled up in the temporary directory on Windows, never removed.
        sync, version = in_process()
        temporary = self.tmp / "temporary"
        temporary.mkdir()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), \
                mock.patch.object(tempfile, "tempdir", str(temporary)):
            for name in ("one", "two", "three"):
                sync.sync(str(self.project(name, version=version)), None, None, io.StringIO(), io.StringIO())
        self.assertEqual([entry.name for entry in temporary.iterdir()], ["cortex-sync.lock"] if os.name == "nt" else [])

    def test_the_project_file_can_be_replaced_while_a_sync_holds_the_lock(self):
        # Held on cortex.toml, the Windows lock failed a git pull, or an editor saving by renaming.
        sync, version = in_process()
        project = self.project(version=version)
        with sync.project_lock(project, io.StringIO()):
            self.assertEqual([entry.name for entry in project.iterdir()], ["cortex.toml"])   # nothing written in it
            new = project / "cortex.toml.new"
            new.write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
            os.replace(new, project / "cortex.toml")
        self.assertIn("9.9.8", (project / "cortex.toml").read_text(encoding="utf-8"))


class SafetyTests(SyncTestCase):
    """Sync deletes nothing it did not write, and changes nothing before every check passed."""

    def test_a_directory_with_an_empty_marker_is_no_copy(self):
        project = self.project()
        (project / "cortex" / "agents").mkdir(parents=True)
        (project / "cortex" / "agents" / "mine.md").write_text("mine\n", encoding="utf-8")
        (project / "cortex" / ".synced").write_text("", encoding="utf-8")
        before = snapshot(project / "cortex")
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("holds a .synced file Cortex cannot read", proc.err)
        self.assertEqual(snapshot(project / "cortex"), before)

    def test_a_clone_with_a_marker_is_still_a_clone(self):
        project = self.project()
        (project / "cortex" / ".git").mkdir(parents=True)
        (project / "cortex" / ".synced").write_text('{"files": {}}', encoding="utf-8")
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a git clone", proc.err)
        self.assertTrue((project / "cortex" / ".git").is_dir())

    def test_a_file_added_to_a_copy_is_named_and_kept(self):
        # A read-only directory takes new files on Windows: an agent writing cortex/agents/…
        # instead of agents/… would lose its file at the next sync.
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        roles = project / "cortex" / "agents" / "roles"
        os.chmod(roles, 0o755)
        (roles / "mine.md").write_text("mine\n", encoding="utf-8")
        for mode in ("--store", "--copy", "--link"):
            with self.subTest(mode=mode):
                proc = self.sync(project, mode)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("agents/roles/mine.md", proc.err)
                self.assertEqual((roles / "mine.md").read_text(encoding="utf-8"), "mine\n")

    def test_a_from_that_is_no_whole_checkout_changes_nothing(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        before = (snapshot(project / "cortex"), (project / "cortex.local.toml").read_text(encoding="utf-8"))
        partial = self.tmp / "partial"
        for tree in ("agents", "templates"):
            shutil.copytree(harness.REPO / tree, partial / tree)
        proc = self.sync(project, "--copy", "--from", str(partial))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no agents/, templates/ and docs/", proc.err)
        self.assertEqual((snapshot(project / "cortex"), (project / "cortex.local.toml").read_text(encoding="utf-8")),
                         before)

    def test_a_spec_line_it_cannot_rewrite_changes_nothing(self):
        project = self.project(extra='sync = "link"\n')
        self.assert_ok(self.sync(project))
        (project / "cortex.local.toml").write_text('spec = """\ncortex\n"""\n', encoding="utf-8")
        proc = self.sync(project, "--copy")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot rewrite", proc.err)
        self.assertTrue(os.path.lexists(project / "cortex"))
        self.assertFalse((project / "cortex" / ".synced").exists())

    def test_no_traceback_when_the_store_cannot_be_made(self):
        project = self.project()
        (self.tmp / "a-file").write_text("x", encoding="utf-8")
        env = dict(self.env, CORTEX_HOME=str(self.tmp / "a-file"))
        proc = harness.run("sync", cwd=project, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertIn(b"a-file", proc.stderr)

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_a_link_to_a_directory_that_is_no_checkout_is_not_replaced(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('spec = "cortex"\n', encoding="utf-8")
        mine = self.tmp / "mine"
        (mine / "agents").mkdir(parents=True)
        (project / "cortex").symlink_to(mine, target_is_directory=True)
        proc = self.sync(project, "--link")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(os.readlink(project / "cortex"), str(mine))


class FromTests(SyncTestCase):
    def test_spec_names_the_checkout_and_a_warning_says_so_every_time(self):
        project = self.project()
        for _ in range(2):
            proc = self.sync(project, "--from", str(harness.REPO))
            self.assert_ok(proc)
            self.assertIn("no version is checked (--from)", proc.err)
        self.assertEqual(self.spec(project), display(harness.REPO))
        self.assertFalse((self.home / "versions").exists())          # the store is not touched

    def test_validate_reads_the_checkouts_base(self):
        project = self.project()
        self.sync(project, "--from", str(harness.REPO))
        proc = self.validate(project)
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"Cortex dir:    {display(harness.REPO)}", proc.out)

    def test_validate_after_a_link_or_a_copy_from_a_checkout(self):
        # CONTRIBUTING's loop, in the mode where Claude Code asks for nothing: the copy is of a
        # checkout, whose version nothing checks.
        for mode in ("--link", "--copy"):
            with self.subTest(mode=mode):
                project = self.project(f"p{mode}")
                self.assert_ok(self.sync(project, mode, "--from", str(harness.REPO)))
                proc = self.validate(project)
                self.assertEqual(proc.returncode, 0, proc.err)
                self.assertIn(f"Cortex dir:    {display(project / 'cortex')}", proc.out)

    def test_a_copy_leaves_the_checkouts_theme_marker_out(self):
        checkout = self.tmp / "checkout"
        for tree in ("agents/personalities", "templates", "docs"):
            (checkout / tree).mkdir(parents=True)
        (checkout / "agents" / "personalities" / ".active-marker").write_text("star-wars\n", encoding="utf-8")
        project = self.project()
        for _ in range(2):
            self.assert_ok(self.sync(project, "--copy", "--from", str(checkout)))
            self.assertFalse((project / "cortex" / "agents" / "personalities" / ".active-marker").exists())

    def test_a_directory_that_is_no_checkout_is_refused(self):
        project = self.project()
        proc = self.sync(project, "--from", str(self.tmp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no Cortex checkout there", proc.err)


class ProjectFileTests(SyncTestCase):
    def test_no_cortex_toml(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        proc = self.sync(bare)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no cortex.toml in", proc.err)
        self.assertIn("cortex init", proc.err)

    def test_a_bad_cortex_toml_writes_nothing(self):
        project = self.project(extra='verison = "1.2.3"\n')
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('unknown key "verison"', proc.err)
        self.assertFalse((project / "cortex.local.toml").exists())
        self.assertFalse(self.home.exists())

    def test_a_bad_cortex_local_toml_is_refused(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('pinned = "1.0.0"\n', encoding="utf-8")
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('unknown key "pinned"', proc.err)
        self.assertIn("version, theme, spec, claude_access, claude_entry and synced only", proc.err)

    def test_two_modes_at_once_are_a_bad_argument(self):
        proc = self.sync(self.project(), "--link", "--copy")
        self.assertEqual(proc.returncode, 2)

    def test_an_unknown_theme_is_said(self):
        project = self.project()
        (project / "cortex.local.toml").write_text('theme = "no-such-theme"\n', encoding="utf-8")
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn('theme "no-such-theme" is neither in the spec nor in agents/personalities/', proc.err)


class ValidateTests(SyncTestCase):
    def test_validate_reads_the_synced_spec(self):
        project = self.project()
        self.sync(project)
        proc = self.validate(project, "--strict")
        self.assertEqual(proc.returncode, 0, proc.err)
        self.assertIn(f"Cortex dir:    {display(self.stored())}", proc.out)
        self.assertIn(f"Project root:  {display(project)}", proc.out)


    def test_a_spec_that_is_not_there_is_quoted_escaped(self):
        # cortex.local.toml is this machine's, but a message prints no control sequence raw.
        project = self.project()
        (project / "cortex.local.toml").write_text('spec = "\\u001b]0;pwned\\u0007/nowhere"\n', encoding="utf-8")
        proc = self.validate(project)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("\x1b", proc.err)
        self.assertIn("\\u001b", proc.err)

    def test_validate_before_sync(self):
        proc = self.validate(self.project())
        self.assertEqual(proc.returncode, 2)
        self.assertIn("names no spec yet — run `cortex sync`", proc.err)

    @DOWNLOADS
    def test_validate_refuses_a_spec_synced_for_another_version(self):
        for mode in ("store", "link", "copy"):
            with self.subTest(mode=mode):
                project = self.project(f"p-{mode}", "9.9.7", f'sync = "{mode}"\n')
                self.assert_ok(self.sync(project))
                (project / "cortex.toml").write_text(f'version = "9.9.8"\ntheme = "h2g2"\nsync = "{mode}"\n',
                                                     encoding="utf-8")
                proc = self.validate(project)
                self.assertEqual(proc.returncode, 2)
                # ADR-013 §3.11: the bump pulled, said against what sync recorded — first.
                self.assertIn("cortex.toml pins Cortex 9.9.8, and `cortex sync` recorded 9.9.7 — run `cortex sync`",
                              proc.err)
                local = project / "cortex.local.toml"                      # without the record, as 1.7.0 said it
                local.write_text("".join(line for line in local.read_text(encoding="utf-8").splitlines(True)
                                         if not line.startswith("synced")), encoding="utf-8")
                proc = self.validate(project)
                self.assertEqual(proc.returncode, 2)
                self.assertIn('pins Cortex 9.9.8, and the spec synced is "9.9.7"', proc.err)

    @unittest.skipIf(os.name == "nt", "a Windows file name holds no control character")
    def test_names_read_from_the_project_are_printed_escaped(self):
        project = self.project()
        os.symlink(self.tmp / "\x1b]0;pwned\x07", project / "cortex")
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a link to", proc.err)
        self.assertNotIn("\x1b", proc.err)
        self.assertIn("\\x1b]0;pwned\\x07", proc.err)
        checkout, linked = self.tmp / "checkout", self.project("linked")
        for tree in ("agents", "templates", "docs"):
            (checkout / tree).mkdir(parents=True)
        self.assert_ok(self.sync(linked, "--link", "--from", str(checkout)))     # spec = "cortex": a link it reads
        os.unlink(linked / "cortex")
        os.symlink(self.tmp / "\x1b]0;pwned\x07", linked / "cortex")
        proc = self.sync(linked, "--link", "--from", str(checkout))      # replaced: its name said as escaped
        self.assert_ok(proc)
        self.assertNotIn("\x1b", proc.out)
        self.assertIn("\\x1b]0;pwned\\x07, which led nowhere: replaced.", proc.out)
        copied = self.project("copied", extra='sync = "copy"\n')
        self.assert_ok(self.sync(copied))
        os.chmod(copied / "cortex" / "agents", 0o755)
        (copied / "cortex" / "agents" / "\x1b[2Jmine.md").write_text("mine\n", encoding="utf-8")
        for command, proc, escaped in (("sync", self.sync(copied), "\\x1b[2Jmine.md"),
                                       ("validate", self.validate(copied), "\\u001b[2Jmine.md")):
            with self.subTest(command=command):
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn("\x1b", proc.err)
                self.assertIn(escaped, proc.err)


class CortexLockTests(SyncTestCase):
    """``cortex.lock`` — the spec a project resolved (ADR-010 §3.2 to §3.4). Not ``LockTests``: that
    name is the project lock's, above, which a second class of it would hide."""

    OTHER = "sha256:" + "0" * 64

    def lock(self, project):
        return (project / "cortex.lock").read_text(encoding="utf-8")

    def write_lock(self, project, version=OWN, digest_=None, extra=""):
        (project / "cortex.lock").write_text(f'format = 1\nversion = "{version}"\ndigest = "{digest_ or self.OTHER}"\n{extra}',
                                             encoding="utf-8")

    @harness.outside_a_repository
    def test_in_no_repository_the_lock_is_not_said_to_commit(self):
        # A 2.B workspace root: cortex init says cortex.toml is committed nowhere, and so is the lock — nor does
        # `sync --locked` or `validate` say to commit it.
        project = self.project()
        proc = self.sync(project, "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("--locked: no cortex.lock — `cortex sync` writes it. Nothing of the project was changed", proc.err)
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn("cortex.lock: written\n", proc.out)
        self.assertNotIn("commit", proc.out.lower())
        good = self.lock(project)
        self.write_lock(project, version="9.9.8", digest_=digest(self.stored()))
        proc = self.sync(project, "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"cortex.toml pins {OWN} — `cortex sync` writes it. Nothing", proc.err)
        for change, said in ((lambda: (project / "cortex.lock").unlink(), "no cortex.lock — run `cortex sync`\n"),
                             (lambda: self.write_lock(project, version="9.9.8", digest_=digest(self.stored())),
                              f"pins {OWN} — run `cortex sync`\n")):
            (project / "cortex.lock").write_text(good, encoding="utf-8")
            change()
            proc = self.validate(project)
            self.assertIn(said, proc.out)
            self.assertNotIn("commit", proc.out.lower())
        (project / ".git").mkdir()                                  # a repository: the lock is to commit
        proc = self.validate(project)
        self.assertIn(f"pins {OWN} — run `cortex sync`{THEN_COMMIT_IT}\n", proc.out)

    @harness.outside_a_repository
    def test_a_repository_is_a_git_at_the_root_or_above_it_as_written_or_resolved(self):
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "ws").mkdir()
        (self.tmp / "alone").mkdir()
        self.assertFalse(sync.in_no_repository(repo / "ws"))
        self.assertTrue(sync.in_no_repository(self.tmp / "alone"))
        if os.name != "nt":                                         # a link to a directory needs a privilege there
            (self.tmp / "ws-link").symlink_to(repo / "ws", target_is_directory=True)
            self.assertFalse(sync.in_no_repository(self.tmp / "ws-link"))   # resolved: inside repo/
        (self.tmp / "alone" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")   # a worktree's, or a submodule's
        self.assertFalse(sync.in_no_repository(self.tmp / "alone"))

    def test_sync_writes_the_lock_of_the_pinned_version_then_leaves_it(self):
        project = self.project()
        (project / ".git").mkdir()                                  # a repository, git or not on the PATH
        proc = self.sync(project)
        self.assert_ok(proc)
        # A build of no release's lock is not to commit: no teammate can download its version (ADR-009 §9).
        self.assertIn("cortex.lock: written\n" if "-dev" in OWN else "cortex.lock: written — commit it\n", proc.out)
        self.assertTrue(self.lock(project).endswith(f'format = 1\nversion = "{OWN}"\ndigest = "{digest(self.stored())}"\n'))
        before = self.lock(project)
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertNotIn("cortex.lock", proc.out)
        self.assertEqual(self.lock(project), before)

    def test_a_spec_changed_in_the_store_is_put_in_again(self):
        project = self.project()
        self.assert_ok(self.sync(project))
        changed = self.stored() / "agents" / "roles" / "prompt-manager.md"
        original = changed.read_bytes()
        os.chmod(changed, 0o644)
        changed.write_bytes(b"# not the spec\n")
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: put in the store again — its files were not those cortex.lock names", proc.out)
        self.assertEqual(changed.read_bytes(), original)
        self.assertIn(f'digest = "{digest(self.stored())}"', self.lock(project))

    def test_a_release_that_is_not_the_one_locked_is_refused(self):
        project = self.project()
        self.write_lock(project)
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"its spec is {digest(self.stored())}, and cortex.lock names {self.OTHER}", proc.err)
        self.assertIn("this is not the release the project locked", proc.err)
        self.assertFalse((project / "cortex.local.toml").exists())
        self.assertIn(self.OTHER, self.lock(project))

    @DOWNLOADS
    def test_a_lock_of_another_version_is_written_again(self):
        project = self.project()
        self.write_lock(project, version="9.9.8")
        (project / ".git").mkdir()
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn(f"cortex.lock: Cortex {OWN}, where it named 9.9.8 — commit it", proc.out)
        self.assertIn(f'version = "{OWN}"', self.lock(project))

    def test_a_checkout_has_no_lock(self):
        project = self.project()
        self.write_lock(project)
        before = self.lock(project)
        proc = self.sync(project, "--from", str(harness.REPO))
        self.assert_ok(proc)
        self.assertIn("cortex.lock: neither read nor written — a checkout has no version (--from)", proc.out)
        self.assertEqual(self.lock(project), before)

    def test_a_lock_that_is_not_one_is_refused(self):
        project = self.project()
        for text, fragment in (("format = 1\nversion = \"1.0.0\"\ndigest = \"x\"\n", 'digest "x" is no digest'),
                               (f'format = 1\nversion = "1.0.0"\ndigest = "{self.OTHER}"\nnote = "x"\n', 'unknown key "note"'),
                               (f'format = 2\nversion = "1.0.0"\ndigest = "{self.OTHER}"\n',
                                "format 2 is written by a later Cortex — this one reads format 1: this cortex is older "
                                "than the project's — update it:\n    cortex self-update")):
            with self.subTest(fragment=fragment):
                (project / "cortex.lock").write_text(text, encoding="utf-8")
                proc = self.sync(project)
                self.assertEqual(proc.returncode, 1, proc.err)
                self.assertIn(f"cortex.lock: {fragment}", proc.err)
                if "later" not in fragment:
                    self.assertIn("delete it, and `cortex sync` writes it again", proc.err)
                self.assertFalse((project / "cortex.local.toml").exists())
        (project / "cortex.lock").write_text("<<<<<<< ours\n", encoding="utf-8")    # a merge's markers
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("delete it, and `cortex sync` writes it again", proc.err)

    def test_a_copy_of_a_spec_the_lock_refuses_is_copied_again(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        team_lock = self.lock(project)
        prompt = self.stored() / "agents" / "roles" / "prompt-manager.md"
        genuine = prompt.read_bytes()
        os.chmod(prompt, 0o644)
        prompt.write_bytes(b"# altered\n")
        (project / "cortex.lock").unlink()          # a lock of the altered store, and a copy of it
        self.assert_ok(self.sync(project))
        self.assertEqual((project / "cortex" / "agents" / "roles" / "prompt-manager.md").read_bytes(), b"# altered\n")
        (project / "cortex.lock").write_text(team_lock, encoding="utf-8")    # the team's, pulled
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertEqual((project / "cortex" / "agents" / "roles" / "prompt-manager.md").read_bytes(), genuine)
        self.assertNotIn("LOCK_", self.validate(project).out)

    def test_validate_warns_on_the_lock(self):
        project = self.project()
        self.assert_ok(self.sync(project))
        proc = self.validate(project)
        self.assert_ok(proc)
        self.assertNotIn("LOCK_", proc.out)
        good = self.lock(project)
        for change, code in ((lambda: (project / "cortex.lock").unlink(), "LOCK_MISSING"),
                             (lambda: self.write_lock(project, version="9.9.8", digest_=digest(self.stored())), "LOCK_STALE"),
                             (lambda: self.write_lock(project), "LOCK_DIGEST"),
                             (lambda: (project / "cortex.lock").write_text("format = 9\n", encoding="utf-8"), "LOCK_INVALID")):
            with self.subTest(code=code):
                (project / "cortex.lock").write_text(good, encoding="utf-8")
                change()
                proc = self.validate(project)
                self.assertEqual(proc.returncode, 0, proc.out)
                self.assertIn(code, proc.out)
                self.assertIn("Warnings: 1", proc.out)
                self.assertEqual(self.validate(project, "--strict").returncode, 1)

    def test_validate_warns_on_a_stored_version_missing_a_tree(self):
        # Pruned while it was read, or a sync cut short: a warning, never a traceback.
        for mode in ("--store", "--link"):
            with self.subTest(mode=mode):
                project = self.project(name=f"app{mode}")
                self.assert_ok(self.sync(project, mode))
                store.make_writable(self.stored())
                shutil.rmtree(self.stored() / "docs")
                proc = self.validate(project)
                self.assertEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn("LOCK_DIGEST", proc.out)
                self.assertIn("holds no docs/ — no whole spec to take the digest of", proc.out)
                self.assertNotIn("Traceback", proc.out + proc.err)
                self.assertEqual(self.validate(project, "--strict").returncode, 1)
                store.remove_tree(self.stored())        # the next sync writes it whole

    @DOWNLOADS
    def test_offline_a_stored_spec_the_lock_refuses_is_said_so(self):
        # It could not be had again: the error says why the sync wanted it, not only that it failed.
        project = self.project(version="9.9.8")
        self.assert_ok(self.sync(project))
        before = self.lock(project)
        changed = next(p for p in sorted((self.stored("9.9.8") / "agents").rglob("*")) if p.is_file())
        os.chmod(changed, 0o644)
        changed.write_bytes(b"# altered\n")
        self.release.close()
        proc = self.sync(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("Cortex 9.9.8 in the store is not what cortex.lock names — its spec is sha256:", proc.err)
        self.assertIn("and it could not be put in the store again: could not download", proc.err)
        self.assertIn("Nothing of the project was changed", proc.err)
        self.assertEqual(self.lock(project), before)

    def test_validate_reads_a_copys_digest_from_its_manifest(self):
        project = self.project(extra='sync = "copy"\n')
        self.assert_ok(self.sync(project))
        self.assertNotIn("LOCK_", self.validate(project).out)
        self.write_lock(project)
        proc = self.validate(project)
        self.assert_ok(proc)
        self.assertIn("LOCK_DIGEST", proc.out)
        self.assertIn(f"the spec synced is {digest(self.stored())}, and cortex.lock names {self.OTHER}", proc.out)

    def test_a_copy_reads_the_store_once(self):
        # Its digest is the lock's check and the copy's: one reading of every file, not two.
        sync, version = in_process()
        project = self.project(version=version, extra='sync = "copy"\n')
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
            with mock.patch.object(sync.store, "digest", wraps=sync.store.digest) as taken:
                sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
        self.assertEqual(taken.call_count, 1)

    def test_a_lock_that_cannot_be_written_moves_nothing(self):
        # Written before the swap: a sync it stops leaves cortex/ and cortex.local.toml as they were.
        sync, version = in_process()
        project = self.project(version=version, extra='sync = "copy"\n')
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
            (project / "cortex.lock").unlink()
            before = snapshot(project / "cortex"), (project / "cortex.local.toml").read_bytes()
            with mock.patch.object(sync.config, "write_lock", side_effect=PermissionError(13, "Permission denied")):
                with self.assertRaises(PermissionError):
                    sync.sync(str(project), "link", None, io.StringIO(), io.StringIO())
        self.assertEqual((snapshot(project / "cortex"), (project / "cortex.local.toml").read_bytes()), before)
        self.assertFalse((project / "cortex.lock").exists())

    @DOWNLOADS
    def test_a_version_a_sync_cut_short_set_aside_is_put_back_offline(self):
        project = self.project(version="9.9.8")
        self.assert_ok(self.sync(project))
        os.rename(self.stored("9.9.8"), self.home / "versions" / ".9.9.8-kept-4242-1")     # killed there
        self.release.close()
        proc = self.sync(project)
        self.assert_ok(proc)
        self.assertIn("Cortex 9.9.8: put back in the store as it was — a sync cut short had set it aside, and it "
                      "could not be downloaded again", proc.out)
        self.assertEqual(sorted(p.name for p in (self.home / "versions").iterdir()), ["9.9.8"])

    def test_locked_never_writes_the_lock(self):
        # A project's CI: `cortex sync --locked && cortex validate --strict`.
        project = self.project()
        (project / ".git").mkdir()                                  # a repository, git or not on the PATH
        proc = self.sync(project, "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"--locked: no cortex.lock — `cortex sync` writes it{THEN_COMMIT_IT}. Nothing of the project "
                      "was changed", proc.err)
        self.assertFalse((project / "cortex.lock").exists())
        self.assertFalse((project / "cortex.local.toml").exists())
        self.write_lock(project, version="9.9.8", digest_=digest(self.stored()))
        before = self.lock(project)
        proc = self.sync(project, "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"--locked: cortex.lock names Cortex 9.9.8, and cortex.toml pins {OWN} — `cortex sync` writes "
                      f"it{THEN_COMMIT_IT}. Nothing", proc.err)
        self.assertEqual(self.lock(project), before)
        self.assertFalse((project / "cortex.local.toml").exists())
        self.write_lock(project)                    # the pin's version, not its files: as without --locked
        proc = self.sync(project, "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("this is not the release the project locked", proc.err)
        (project / "cortex.lock").unlink()
        self.assert_ok(self.sync(project))
        before = (project / "cortex.lock").read_bytes()
        proc = self.sync(project, "--locked")
        self.assert_ok(proc)
        self.assertNotIn("cortex.lock", proc.out)
        self.assertEqual((project / "cortex.lock").read_bytes(), before)

    def test_locked_and_a_checkout_are_a_bad_argument(self):
        proc = self.sync(self.project(), "--locked", "--from", str(harness.REPO))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--locked checks cortex.lock, and --from reads none", proc.err)

    def test_validate_of_a_project_without_a_lock_passes_without_strict(self):
        # A project of 1.3: synced by a cortex that wrote no lock.
        project = self.project()
        self.assert_ok(self.sync(project))
        (project / "cortex.lock").unlink()
        self.assertEqual(self.validate(project).returncode, 0)


class RegisterTests(SyncTestCase):
    """A sync that succeeded registers the project in the machine's registry, and says so once
    (ADR-011 §3.3)."""

    def setUp(self):
        super().setUp()
        self.registry = self.home / "projects.toml"

    def sync_with(self, project, *args, **env):
        proc = harness.run("sync", *args, cwd=project, env=dict(self.env, **env))
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def entries(self):
        data = tomllib.loads(self.registry.read_text(encoding="utf-8"))
        return [(entry["name"], entry["path"]) for entry in data.get("project", [])]

    def written(self):
        """The registry and its lock, as a write would change them: bytes, time and file."""
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                for path in self.home.glob("projects.toml*")}

    def test_the_first_sync_registers_the_projects_root_and_says_it_once(self):
        project = self.project("acme")
        (project / "src").mkdir()
        proc = self.sync(project, cwd=project / "src")
        self.assert_ok(proc)
        self.assertTrue(proc.out.endswith(f"\nregistered as acme — {LISTS}\n"), proc.out)
        self.assertEqual(proc.out.count("registered"), 1)
        self.assertEqual(self.registry.read_text(encoding="utf-8"),
                         REGISTRY_HEADER + f'format = 1\n\n[[project]]\nname = "acme"\npath = "{display(project)}"\n')

    def test_a_path_through_a_link_is_registered_as_the_shell_names_it(self):
        project = self.project("real/acme")
        link = self.tmp / "link"
        try:
            os.symlink(project, link, target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic link here")
        proc = self.sync(link)                    # PWD names the link, as a shell that changed into it
        self.assert_ok(proc)
        self.assertIn(f"registered as link — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("link", display(link))])

    def test_synced_again_nothing_is_said_or_written(self):
        project = self.project("acme")
        self.assert_ok(self.sync(project))
        before = self.written()
        self.assertEqual(sorted(before), ["projects.toml", "projects.toml.lock"])
        for args in ((), ("--link",)):
            with self.subTest(args=args):
                proc = self.sync(project, *args)
                self.assert_ok(proc)
                self.assertNotIn("registered", proc.out + proc.err)
                self.assertEqual(self.written(), before)

    @harness.outside_a_repository
    def test_a_listed_project_waits_for_no_other_write_of_the_registry(self):
        sync, version = in_process()
        from cortex_cli import registry

        project, other = self.project("acme", version=version), self.project("other", version=version)
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), mock.patch.object(registry, "LOCK_WAIT", 0.3):
            sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
            with registry._locked(self.registry):                   # another command writes it meanwhile
                err = io.StringIO()
                sync.sync(str(project), None, None, io.StringIO(), err)
                self.assertEqual(err.getvalue(), "")
                sync.sync(str(other), None, None, io.StringIO(), err)       # one to add waits for it
        self.assertEqual(err.getvalue(), f"warning: {display(other)} is not registered — another cortex command has "
                                         f"held {display(self.home / 'projects.toml.lock')} for 0.3 seconds — wait for "
                                         "it, then run the command again\n")
        self.assertEqual(self.entries(), [("acme", display(project))])

    def test_a_listed_project_resolves_no_other_entrys_path(self):
        # One on a share that does not answer would hold the sync: the paths as written tell first.
        sync, version = in_process()
        from cortex_cli import registry

        projects = [self.project(name, version=version) for name in ("one", "two")]
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            for project in projects:
                sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
            out, before = io.StringIO(), self.written()
            with mock.patch.object(registry, "same_directory", side_effect=AssertionError("a path resolved")):
                sync.register(str(projects[1]), out, out)
        self.assertEqual((out.getvalue(), self.written()), ("", before))

    def test_a_project_another_sync_registered_meanwhile_is_not_said_again(self):
        sync, version = in_process()
        from cortex_cli import registry

        project = self.project("acme", version=version)
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            sync.sync(str(project), None, None, io.StringIO(), io.StringIO())
            before = self.written()
            out = io.StringIO()
            with mock.patch.object(registry, "load", lambda: []):     # read before the other one wrote it
                sync.sync(str(project), None, None, out, io.StringIO())
        self.assertNotIn("registered", out.getvalue())
        self.assertEqual(self.written(), before)

    def test_a_sync_that_fails_registers_nothing(self):
        project = self.project("acme")
        proc = self.sync(project, "--locked")              # no cortex.lock: refused, the version stored
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertTrue(self.stored().is_dir())
        self.assertNotIn("registered", proc.out + proc.err)
        self.assertFalse(self.registry.exists())

    def test_locked_and_from_register_too(self):
        locked, checkout = self.project("locked"), self.project("checkout")
        self.assert_ok(self.sync(locked))                                        # its cortex.lock
        self.registry.unlink()
        for project, args in ((locked, ("--locked",)), (checkout, ("--from", str(harness.REPO)))):
            with self.subTest(args=args):
                proc = self.sync(project, *args)
                self.assert_ok(proc)
                self.assertIn(f"registered as {project.name} — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("checkout", display(checkout)), ("locked", display(locked))])

    def test_a_project_moved_on_disk_is_registered_again_under_a_free_name(self):
        old = self.project("old/acme")
        self.assert_ok(self.sync(old))
        new = self.tmp / "new" / "acme"
        new.parent.mkdir()
        os.rename(old, new)
        proc = self.sync(new)
        self.assert_ok(proc)
        self.assertIn(f"registered as acme-2 — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("acme", display(old)), ("acme-2", display(new))])
        listed = json.loads(harness.run("project", "list", "--json", cwd=self.tmp, env=self.env).stdout)["projects"]
        self.assertEqual([(entry["name"], entry["state"]) for entry in listed], [("acme", "missing"), ("acme-2", "ok")])

    def test_a_registry_that_cannot_be_written_or_read_is_a_warning_and_the_sync_succeeds(self):
        later = "format = 2\n"
        self.home.mkdir()
        (self.home / "projects.toml.lock").mkdir()                  # no file to lock, as root too
        for case in ("its lock is a directory", "a later format"):
            with self.subTest(case=case):
                if case == "a later format":
                    (self.home / "projects.toml.lock").rmdir()
                    self.registry.write_text(later, encoding="utf-8")
                project = self.project(case.replace(" ", "-"))
                proc = self.sync(project)
                self.assert_ok(proc)
                self.assertTrue((project / "cortex.local.toml").is_file())
                self.assertNotIn("registered as", proc.out)
                warning = f"warning: {display(project)} is not registered — "
                self.assertIn(warning + (f"{display(self.registry)} cannot be written — " if case != "a later format"
                                         else f"{display(self.registry)}: format 2"), proc.err)
                if case == "a later format":
                    self.assertIn("cortex self-update", proc.err)
                    self.assertEqual(self.registry.read_text(encoding="utf-8"), later)
                else:
                    self.assertFalse(self.registry.exists())

    @unittest.skipIf(AS_ROOT or os.name == "nt", "root writes any directory, and Windows has no read-only directory")
    def test_a_home_that_cannot_be_written_is_a_warning_and_the_sync_succeeds(self):
        stored = self.project("stored")
        self.assert_ok(self.sync(stored))                        # the version in the store, and the registry
        before = self.registry.read_bytes()
        project = self.project("acme")
        os.chmod(self.home, 0o555)
        proc = self.sync(project)
        os.chmod(self.home, 0o755)
        self.assert_ok(proc)
        self.assertIn(f"warning: {display(project)} is not registered — {display(self.registry)} cannot be written — "
                      "Permission denied", proc.err)
        self.assertTrue((project / "cortex.lock").is_file())
        self.assertEqual(self.registry.read_bytes(), before)

    @harness.outside_a_repository
    def test_cortex_no_register_set_neither_reads_nor_writes_the_registry_and_empty_counts_as_unset(self):
        project = self.project("acme")
        for value in ("1", "yes"):
            with self.subTest(value=value):
                proc = self.sync_with(project, CORTEX_NO_REGISTER=value)
                self.assert_ok(proc)
                self.assertNotIn("registered", proc.out + proc.err)
                self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["versions"])
        self.registry.write_text("format = 2\n", encoding="utf-8")     # read, it would be warned of
        proc = self.sync_with(project, CORTEX_NO_REGISTER="1")
        self.assert_ok(proc)
        self.assertEqual(proc.err, "")
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["projects.toml", "versions"])
        self.registry.unlink()
        proc = self.sync_with(project, CORTEX_NO_REGISTER="")
        self.assert_ok(proc)
        self.assertIn(f"registered as acme — {LISTS}\n", proc.out)
        self.assertEqual(self.entries(), [("acme", display(project))])

    def test_an_upgrades_sync_registers_and_says_it_once(self):
        project = self.project("acme")             # pinned to this cortex, never synced: the upgrade syncs it
        proc = harness.run("upgrade", cwd=project, env=dict(self.env, CORTEX_NO_UPDATE_CHECK="1"))
        out = proc.stdout.decode()
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertIn("syncing", out)
        self.assertIn(f"registered as acme — {LISTS}\n", out)
        self.assertEqual(out.count("registered"), 1)
        self.assertEqual(self.entries(), [("acme", display(project))])


if __name__ == "__main__":
    unittest.main()
