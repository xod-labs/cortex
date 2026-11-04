"""``cortex upgrade`` — a project moved to another version, said first (ADR-010 §3.5).

From source the command stands for Cortex 9.9.9, written to the store from this checkout, and the
fake releases 9.9.7 and 9.9.8 are served from this machine (``release_fixture``): 9.9.8's spec is
four files, so that moving between the two adds or removes most of the agents. A built binary is a
build of no release and downloads nothing: the tests that need a second version skip there.
"""

import functools
import hashlib
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
from cortex_cli import init  # noqa: E402
from cortex_cli.store import digest  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
OLD_BOOTSTRAP = b"# Cortex AI Team\n\nWritten by 9.9.8: read cortex.local.toml first.\n"
MARKED = b"# Cortex AI Team\n\n<!-- cortex:bootstrap layout=single format=1 -->\n\nWritten by 9.9.8, with the marker.\n"
PERSONAL = (b"# Cortex AI Team\n\n<!-- PERSONALITY:BEGIN -->\nBe a character.\n<!-- PERSONALITY:END -->\n\n"
            b"Written by 9.9.8: read cortex.local.toml first.\n")
OLD_WORKSPACE = b"# Cortex AI Team \xe2\x80\x94 Workspace Mode\n\nWritten by 9.9.8: read cortex.local.toml first.\n"
TEMPLATE = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes()
OVERLAY = ("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
           "     Semantic: additive\n-->\n\n# Architect — our rules\n")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else "dir" for p in sorted(root.rglob("*"))
            if not os.path.islink(p)}


class UpgradeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-upgrade-")).resolve()
        self.addCleanup(self.cleanup)
        self.home = self.tmp / "cortex-home"
        self.release = FakeRelease(("9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": OLD_BOOTSTRAP})
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN)

    def cleanup(self):
        for directory, subdirs, files in os.walk(self.tmp):
            for name in subdirs + files:
                path = os.path.join(directory, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, version, theme="h2g2", synced=True):
        root = self.tmp / "app"
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'# ours\nversion = "{version}"  # the team\'s pin\ntheme = "{theme}"\n',
                                          encoding="utf-8")
        if synced:
            self.assert_ok(self.run_cortex(root, "sync"))
        return root

    def run_cortex(self, project, *args):
        proc = harness.run(*args, cwd=project, env=self.env)
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def upgrade(self, project, *args):
        return self.run_cortex(project, "upgrade", *args)

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)

    def pinned(self, project):
        return (project / "cortex.toml").read_text(encoding="utf-8")


@DOWNLOADS
class UpgradeTests(UpgradeTestCase):
    def test_it_moves_the_project_to_this_cortex_and_says_what_changes(self):
        project = self.project("9.9.8")
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex 9.9.8 → {OWN}: an upgrade — the release notes: {self.release.url}/tag/{OWN}", proc.out)
        self.assertRegex(proc.out, r"  roles +\d+ added")
        self.assertIn("      + engineering/architect\n", proc.out)
        self.assertIn("  templates ", proc.out)
        self.assertEqual(self.pinned(project), f'# ours\nversion = "{OWN}"  # the team\'s pin\ntheme = "h2g2"\n')
        lock = (project / "cortex.lock").read_text(encoding="utf-8")
        self.assertIn(f'version = "{OWN}"', lock)
        self.assertIn(f'digest = "{digest(self.home / "versions" / OWN)}"', lock)
        self.assertIn(f"versions/{OWN}", (project / "cortex.local.toml").read_text(encoding="utf-8"))
        self.assertIn(f"Cortex {OWN} is ready. Commit cortex.toml, cortex.lock;", proc.out)
        self.assertEqual(proc.out.count("Project: "), 1)

    def test_to_an_older_version_is_a_downgrade(self):
        project = self.project(OWN)
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN} → 9.9.8: a downgrade", proc.out)
        self.assertIn("      - engineering/architect\n", proc.out)
        self.assertIn('version = "9.9.8"', self.pinned(project))
        self.assertIn('version = "9.9.8"', (project / "cortex.lock").read_text(encoding="utf-8"))

    def test_the_pinned_version_moves_nothing(self):
        project = self.project(OWN)
        before = snapshot(project)
        proc = self.upgrade(project, OWN)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_a_version_newer_than_this_cortex_is_refused_with_the_upgrade_command(self):
        project = self.project("9.9.8")
        proc = self.upgrade(project, "9.10.0")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"Cortex 9.10.0 is newer than this cortex, {OWN} — upgrade this cortex first:", proc.err)
        self.assertIn("install", proc.err)
        self.assertIn('version = "9.9.8"', self.pinned(project))

    def test_a_malformed_version_exits_2(self):
        project = self.project("9.9.8")
        for version in ("1.4", "v1.4.0", "1.4.0\n"):
            with self.subTest(version=version):
                proc = self.upgrade(project, version)
                self.assertEqual(proc.returncode, 2, proc.err)
                self.assertIn("is no version", proc.err)

    def test_dry_run_changes_nothing_in_the_project(self):
        project = self.project("9.9.8")
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn("--dry-run: nothing of the project was changed", proc.out)
        self.assertIn("      + engineering/architect\n", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_an_overlay_the_target_breaks_stops_it_unless_forced(self):
        project = self.project(OWN)
        overlay = project / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(OVERLAY, encoding="utf-8")
        before = snapshot(project)
        proc = self.upgrade(project, "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("BASE_NOT_FOUND", proc.out)
        self.assertIn("the overlays would fail against Cortex 9.9.8 — nothing was changed", proc.err)
        self.assertEqual(snapshot(project), before)
        self.assertEqual(self.upgrade(project, "9.9.8", "--dry-run").returncode, 1)
        self.assertEqual(self.upgrade(project, "9.9.8", "--dry-run", "--force").returncode, 0)   # as the real run
        self.assertEqual(snapshot(project), before)
        proc = self.upgrade(project, "9.9.8", "--force")
        self.assert_ok(proc)
        self.assertIn('version = "9.9.8"', self.pinned(project))

    def test_a_bootstrap_nobody_changed_is_written_again(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        self.assertIn("Commit cortex.toml, cortex.lock, CLAUDE.md;", proc.out)

    def test_a_bootstrap_checked_out_with_crlf_is_still_nobodys_change(self):
        # git's core.autocrlf, on Windows.
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True).replace(b"\n", b"\r\n"))
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n"))

    def test_a_dry_run_says_the_bootstrap_too_when_the_overlays_stop_it(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (project / "agents" / "roles" / "engineering").mkdir(parents=True)
        (project / "agents" / "roles" / "engineering" / "architect.md").write_text(
            OVERLAY.replace("architect.md", "nowhere.md"), encoding="utf-8")
        before = snapshot(project)
        proc = self.upgrade(project, "--dry-run")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template", proc.out)
        self.assertIn("--dry-run: nothing of the project was changed", proc.out)
        self.assertEqual(snapshot(project), before)

    def test_a_bootstrap_a_team_changed_is_kept(self):
        project = self.project("9.9.8")
        ours = OLD_BOOTSTRAP + b"\n## Our own rules\n"
        (project / "CLAUDE.md").write_bytes(ours)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), ours)
        self.assertIn("CLAUDE.md is a bootstrap without the marker, not what Cortex 9.9.8 writes — a team changed it, or "
                      "an older Cortex wrote it — so it is kept, and the target's template is not written: cortex init "
                      "--force writes it again, and keeps yours as .bak", proc.err)

    def test_a_sync_that_fails_leaves_the_pin_as_it_was(self):
        project = self.project("9.9.8")
        (project / "cortex").mkdir()
        (project / "cortex" / "notes.md").write_text("ours\n", encoding="utf-8")    # sync refuses a cortex/ of others
        before = self.pinned(project)
        proc = self.upgrade(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(self.pinned(project), before)
        self.assertIn('version = "9.9.8"', (project / "cortex.lock").read_text(encoding="utf-8"))


@DOWNLOADS
class DigestTests(UpgradeTestCase):
    """A bootstrap nobody changed is known by the digest in its marker, and written again whenever
    the target writes another file — whatever the formats, either way (ADR-010 §3.5, step 5)."""

    def marked(self, version="9.9.8", template=MARKED, name="bootstrap-instructions.md"):
        self.release.replace_spec(version, {f"templates/{name}": template})
        return init.stamped(init.instructions(template, True))

    def test_an_untouched_bootstrap_of_the_same_format_is_written_again(self):
        written = self.marked()
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))
        self.assertIn("Commit cortex.toml, cortex.lock, CLAUDE.md;", proc.out)

    def test_an_untouched_bootstrap_the_target_writes_alike_is_left_as_it_is(self):
        written = self.marked(template=TEMPLATE)
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), written)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_a_downgrade_writes_an_untouched_bootstrap_again(self):
        self.marked()
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(init.stamped(init.instructions(TEMPLATE, True)))
        proc = self.upgrade(project, "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(MARKED, True)))

    def test_one_a_team_changed_is_kept_and_said_when_the_target_writes_another(self):
        changed = self.marked() + b"\n## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — a team changed it — so it is "
                      "kept, and the target's template is not written: cortex init --force writes it again", proc.err)

    def test_one_a_team_changed_is_not_said_when_the_target_writes_the_same(self):
        changed = self.marked(template=TEMPLATE) + b"\n## Our own rules\n"
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(changed)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), changed)
        self.assertNotIn("CLAUDE.md", proc.err)

    def test_crlf_and_a_byte_order_mark_are_nobodys_change_and_kept(self):
        written = self.marked()
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(b"\xef\xbb\xbf" + written.replace(b"\n", b"\r\n"))
        self.assert_ok(self.upgrade(project))
        self.assertEqual((project / "CLAUDE.md").read_bytes(),
                         b"\xef\xbb\xbf" + init.stamped(init.instructions(TEMPLATE, True)).replace(b"\n", b"\r\n"))

    def test_already_on_it_writes_again_what_an_interrupted_upgrade_left(self):
        # Cut short after the switch, or a pin moved by hand: 9.9.8's file under the pin of this cortex.
        written = self.marked()
        project = self.project(OWN)
        (project / "CLAUDE.md").write_bytes(written)
        proc = self.upgrade(project, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), written)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — no one changed it", proc.out)
        self.assertIn("Commit CLAUDE.md.", proc.out)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, True)))

    def test_without_a_digest_and_the_pinned_version_it_is_not_checked_and_said_so(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        shutil.rmtree(self.home / "versions" / "9.9.8")
        self.env["CORTEX_RELEASES_URL"] = "http://127.0.0.1:9"        # pruned, and offline
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md was not checked: its marker carries no digest, and Cortex 9.9.8, whose templates would "
                      "tell whether a team changed it, is not in the store. It is kept: cortex init --force", proc.err)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.instructions(OLD_BOOTSTRAP, True))

    def test_several_markers_are_said(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(MARKED, True) + b"\n" + MARKED)
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertIn("CLAUDE.md holds several bootstrap markers, so none is read, and it is kept: cortex init --force "
                      "writes it again", proc.err)

    def test_no_personality_is_compared_and_written_without_its_block(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": PERSONAL})
        project = self.project("9.9.8", theme="none")
        (project / "CLAUDE.md").write_bytes(init.instructions(PERSONAL, False))
        proc = self.upgrade(project)
        self.assert_ok(proc)
        self.assertEqual((project / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(TEMPLATE, False)))
        self.assertNotIn(b"PERSONALITY:BEGIN", (project / "CLAUDE.md").read_bytes())

    def test_a_workspace_bootstrap_without_the_marker_is_known_by_its_template(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions-workspace.md": OLD_WORKSPACE})
        project = self.project("9.9.8")
        (project / "AGENTS.md").write_bytes(init.instructions(OLD_WORKSPACE, True))
        self.assert_ok(self.upgrade(project))
        workspace = (harness.REPO / "templates" / "bootstrap-instructions-workspace.md").read_bytes()
        self.assertEqual((project / "AGENTS.md").read_bytes(), init.stamped(init.instructions(workspace, True)))


class StampTests(unittest.TestCase):
    """The digest a bootstrap carries in its marker (ADR-010 §3.1)."""

    def test_it_is_the_digest_of_the_file_with_its_marker_plain(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        digest = hashlib.sha256(init.instructions(TEMPLATE, True)).hexdigest()[:16]
        self.assertIn(f"<!-- cortex:bootstrap layout=single format=1 digest={digest} -->\n".encode(), written)
        self.assertEqual(init.markers(written), [("single", 1)])
        self.assertIs(init.untouched(written), True)
        self.assertEqual(init.stamped(written), written)

    def test_what_git_and_editors_do_to_a_file_is_no_change(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        for kept in (written.replace(b"\n", b"\r\n"), b"\xef\xbb\xbf" + written, written.rstrip(b"\n"),
                     written + b"\n\n"):
            with self.subTest(kept=kept[:8] + kept[-4:]):
                self.assertIs(init.untouched(kept), True)

    def test_a_change_anywhere_is_one(self):
        written = init.stamped(init.instructions(TEMPLATE, True))
        for kept in (written.replace(b"Cortex", b"Kortex", 1), written + b"## Ours\n",
                     written.replace(b"format=1 digest=", b"format=2 digest="),
                     written.replace(b"layout=single", b"layout=workspace")):
            with self.subTest(kept=kept[-40:]):
                self.assertIs(init.untouched(kept), False)

    def test_no_digest_tells_nothing(self):
        for kept in (init.instructions(TEMPLATE, True), OLD_BOOTSTRAP,
                     init.stamped(init.instructions(TEMPLATE, True)) * 2):
            with self.subTest(kept=kept[:40]):
                self.assertIsNone(init.untouched(kept))
        self.assertEqual(init.stamped(OLD_BOOTSTRAP), OLD_BOOTSTRAP)


@DOWNLOADS
class RollbackTests(UpgradeTestCase):
    """A sync that fails once the lock is written: the pin and the lock are put back (ADR-010 §3.5, step 6)."""

    def test_the_pin_and_the_lock_are_put_back(self):
        project = self.project("9.9.8")
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock")}
        sys.path[:0] = [str(harness.CLI), str(harness.CORE)]
        from cortex_cli import config, store, sync, upgrade

        def a_sync_that_fails_late(root, *args, **kwargs):
            config.write_lock(root, store.Version(OWN), "sha256:" + "1" * 64)     # as sync does, before its swap
            raise OSError("the swap failed")

        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url}
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", a_sync_that_fails_late), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            err = io.StringIO()
            with self.assertRaises(OSError):
                upgrade.upgrade(str(project), None, False, False, None, io.StringIO(), err)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)
        self.assertIn("cortex.toml and cortex.lock are as they were", err.getvalue())

    def in_process(self, project, a_sync):
        sys.path[:0] = [str(harness.CLI), str(harness.CORE)]
        from cortex_cli import store, sync, upgrade
        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url}
        err = io.StringIO()
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", a_sync), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            with self.assertRaises(OSError):
                upgrade.upgrade(str(project), None, False, False, None, io.StringIO(), err)
        return err.getvalue()

    def test_the_bootstrap_written_again_is_put_back_too(self):
        project = self.project("9.9.8")
        (project / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock", "CLAUDE.md")}

        def a_sync_that_fails(root, *args, **kwargs):
            raise OSError("the swap failed")

        err = self.in_process(project, a_sync_that_fails)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)
        self.assertIn("cortex.toml, cortex.lock and CLAUDE.md are as they were", err)

    def test_a_bootstrap_that_cannot_be_written_moves_nothing(self):
        project = self.project("9.9.8")
        old = init.instructions(OLD_BOOTSTRAP, True)
        (project / "CLAUDE.md").write_bytes(old)
        committed = {name: (project / name).read_bytes() for name in ("cortex.toml", "cortex.lock", "CLAUDE.md")}
        write_bytes = Path.write_bytes

        def a_disk_that_refuses_the_new_bootstrap(path, data):
            if path.name == "CLAUDE.md" and data != old:
                raise OSError("no space left on device")
            return write_bytes(path, data)

        with mock.patch.object(Path, "write_bytes", a_disk_that_refuses_the_new_bootstrap):
            self.in_process(project, lambda *args, **kwargs: None)
        self.assertEqual({name: (project / name).read_bytes() for name in committed}, committed)


class PinAfterThisCortexTests(UpgradeTestCase):
    """A project pinned to a version after this cortex's: with no version named, it does not move
    down — a teammate's older binary would undo the team's pin (ADR-010 §3.5, step 1)."""

    def test_it_is_refused_with_no_version_named(self):
        project = self.project("9.10.0", synced=False)
        before = snapshot(project)
        proc = self.upgrade(project)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"this project uses Cortex 9.10.0, newer than this cortex, {OWN} — nothing was changed. Upgrade "
                      "this cortex to serve it:", proc.err)
        self.assertIn(f"cortex upgrade {OWN}", proc.err)
        self.assertEqual(snapshot(project), before)

    def test_it_moves_down_to_the_version_named(self):
        project = self.project("9.10.0", synced=False)
        proc = self.upgrade(project, OWN)
        self.assert_ok(proc)
        self.assertIn(f"Cortex 9.10.0 → {OWN}: a downgrade", proc.out)
        self.assertIn(f'version = "{OWN}"', self.pinned(project))


class UpgradeHelpTests(unittest.TestCase):
    def test_the_command_is_listed(self):
        self.assertIn(b"  upgrade ", harness.run("--help").stdout)

    def test_outside_a_project_it_says_so(self):
        with tempfile.TemporaryDirectory() as empty:
            proc = harness.run("upgrade", cwd=empty, env=harness.environment(CORTEX_HOME=os.path.join(empty, "h")))
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"no cortex.toml", proc.stderr)


if __name__ == "__main__":
    unittest.main()
