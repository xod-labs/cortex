"""``cortex upgrade --all`` — every registered project moved (ADR-011 §3.5).

The projects are registered by hand, in a home of each test's own, and ``CORTEX_NO_REGISTER`` keeps
a sync from registering one. As in ``test_cli_upgrade``, the command stands for Cortex 9.9.9, and
9.9.7 and 9.9.8 are served from this machine.
"""

import io
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
from cortex_cli import init, self_update, upgrade  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402
from test_cli_upgrade import DOWNLOADS, OLD_BOOTSTRAP, OVERLAY, OWN, UpgradeTestCase, snapshot  # noqa: E402

BROKEN = OVERLAY.replace("architect.md", "nowhere.md")      # an overlay no version has the base of
NOT_COMMITTED = "Nothing was committed. Teammates run `cortex sync` after they pull.\n"


def display(path):
    return str(path).replace(os.sep, "/")


class AllTestCase(UpgradeTestCase):
    def setUp(self):
        super().setUp()
        self.env["CORTEX_NO_REGISTER"] = "1"      # the registry is the test's: no sync adds to it
        self.code = self.tmp / "code"

    def make(self, name, version="9.9.8", synced=True, bootstrap=False):
        root = self.code / name
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(f'version = "{version}"\ntheme = "h2g2"\n', encoding="utf-8")
        if bootstrap:
            (root / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))   # 9.9.8's, nobody changed it
        if synced:
            self.assert_ok(self.run_cortex(root, "sync"))
        return root

    def break_overlays(self, root):
        overlay = root / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(BROKEN, encoding="utf-8")

    def register(self, *roots, text=None):
        self.home.mkdir(exist_ok=True)
        entries = "".join(f'\n[[project]]\nname = "{root.name}"\npath = "{display(root)}"\n' for root in roots)
        (self.home / "projects.toml").write_text(text if text is not None else "format = 1\n" + entries,
                                                 encoding="utf-8")

    def upgrade_all(self, *args):
        return self.run_cortex(self.tmp, "upgrade", "--all", *args)

    def summary(self, proc):
        return proc.out[proc.out.index(f"\nCortex {OWN}, in each registered project"):]

    def assert_moved(self, root, version=OWN):
        self.assertIn(f'version = "{version}"', self.pinned(root))
        self.assertIn(f'version = "{version}"', (root / "cortex.lock").read_text(encoding="utf-8"))


@DOWNLOADS
class AllTests(AllTestCase):
    def test_a_project_that_refuses_stops_none_of_the_others_and_it_exits_1(self):
        alpha, beta, gamma = self.make("alpha", bootstrap=True), self.make("beta"), self.make("gamma")
        self.break_overlays(beta)
        self.register(gamma, beta, alpha)
        before = snapshot(beta)
        proc = self.upgrade_all()
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assert_moved(alpha)
        self.assert_moved(gamma)
        self.assertEqual(snapshot(beta), before)
        self.assertLess(proc.out.index(f"alpha: {display(alpha)}\n"), proc.out.index(f"\nbeta: {display(beta)}\n"))
        self.assertLess(proc.out.index(f"\nbeta: {display(beta)}\n"), proc.out.index(f"\ngamma: {display(gamma)}\n"))
        self.assertIn("BASE_NOT_FOUND", proc.out)
        self.assertNotIn("Project: ", proc.out)         # each is named once, by its entry
        self.assertIn(f"cortex upgrade: the overlays would fail against Cortex {OWN} — nothing was changed", proc.err)
        self.assertEqual(self.summary(proc),
                         f"\nCortex {OWN}, in each registered project:\n"
                         f"  alpha  moved 9.9.8 → {OWN}\n"
                         f"  beta   refused: the overlays fail against Cortex {OWN} — nothing was changed\n"
                         f"  gamma  moved 9.9.8 → {OWN}\n"
                         "\nWhat each project must commit:\n"
                         f"  alpha  {display(alpha)}: cortex.toml, cortex.lock and CLAUDE.md\n"
                         f"  gamma  {display(gamma)}: cortex.toml and cortex.lock\n" + NOT_COMMITTED)

    def test_a_sync_that_refuses_is_that_projects_refusal(self):
        alpha, beta, gamma = self.make("alpha"), self.make("beta"), self.make("gamma")
        (beta / "cortex").mkdir()
        (beta / "cortex" / "notes.md").write_text("ours\n", encoding="utf-8")    # a cortex/ sync did not make
        self.register(alpha, beta, gamma)
        before = snapshot(beta)
        proc = self.upgrade_all()
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assert_moved(alpha)
        self.assert_moved(gamma)
        self.assertEqual(snapshot(beta), before)
        self.assertIn("cortex upgrade: cortex/ is ", proc.err)
        self.assertIn("\n  beta   refused: cortex/ is ", self.summary(proc))

    def test_a_project_that_is_not_ok_is_skipped_and_said(self):
        alpha = self.make("alpha")
        (self.code / "empty").mkdir()
        self.register(alpha, self.code / "gone", self.code / "empty")
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assert_moved(alpha)
        self.assertIn(f"\ngone: {display(self.code / 'gone')}\nskipped: missing — no directory there\n", proc.out)
        self.assertIn(f"\nempty: {display(self.code / 'empty')}\nskipped: no project — no cortex.toml there\n", proc.out)
        self.assertIn(f"  alpha  moved 9.9.8 → {OWN}\n  empty  skipped: no project\n  gone   skipped: missing\n",
                      self.summary(proc))
        self.assertFalse((self.code / "gone").exists())

    def test_a_pin_newer_than_this_cortex_is_refused_and_the_others_move(self):
        alpha, beta = self.make("alpha"), self.make("beta", "9.10.0", synced=False)
        self.register(alpha, beta)
        before = snapshot(beta)
        proc = self.upgrade_all()
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assert_moved(alpha)
        self.assertEqual(snapshot(beta), before)
        self.assertIn(f"cortex upgrade: this project uses Cortex 9.10.0, newer than this cortex, {OWN} — nothing was "
                      "changed. Update this cortex to serve it:\n    cortex self-update 9.10.0\n", proc.err)
        self.assertIn(f"  beta   refused: this project uses Cortex 9.10.0, newer than this cortex, {OWN} — nothing was "
                      "changed. Update this cortex to serve it\n", self.summary(proc))

    def test_a_version_named_moves_every_project_to_it_down_too(self):
        alpha, beta = self.make("alpha", OWN), self.make("beta", "9.10.0", synced=False)
        self.register(alpha, beta)
        proc = self.upgrade_all("9.9.8")
        self.assert_ok(proc)
        self.assert_moved(alpha, "9.9.8")
        self.assert_moved(beta, "9.9.8")
        self.assertIn(f"  alpha  moved {OWN} → 9.9.8\n  beta   moved 9.10.0 → 9.9.8\n", proc.out)
        self.assertIn("\nCortex 9.9.8, in each registered project:\n", proc.out)

    def test_a_version_newer_than_this_cortex_is_refused_once_before_any_project(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        self.register(alpha, beta)
        before = snapshot(self.code)
        proc = self.upgrade_all("9.10.0")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(proc.err.count("is newer than this cortex"), 1, proc.err)
        self.assertIn("then run `cortex upgrade --all 9.10.0` again:\n    cortex self-update 9.10.0", proc.err)
        self.assertEqual(proc.out, "")
        self.assertEqual(snapshot(self.code), before)

    def test_dry_run_changes_nothing_in_any_project_and_exits_as_the_run_would(self):
        alpha, beta = self.make("alpha", bootstrap=True), self.make("beta")
        self.break_overlays(beta)
        self.register(alpha, beta)
        before = snapshot(self.code)
        proc = self.upgrade_all("--dry-run")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(snapshot(self.code), before)
        self.assertEqual(self.summary(proc),
                         f"\nCortex {OWN}, in each registered project — a dry run:\n"
                         f"  alpha  would move 9.9.8 → {OWN}\n"
                         f"  beta   refused: the overlays fail against Cortex {OWN}\n"
                         "\nWhat each project would have to commit:\n"
                         f"  alpha  {display(alpha)}: cortex.toml, cortex.lock and CLAUDE.md\n"
                         "--dry-run: nothing of any project was changed.\n")
        self.assert_ok(self.upgrade_all("--dry-run", "--force"))
        self.assertEqual(snapshot(self.code), before)

    def test_on_the_pinned_version_what_an_upgrade_left_is_finished_and_said_to_commit(self):
        alpha = self.make("alpha")
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        self.register(alpha)
        proc = self.upgrade_all("--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"  alpha  already on {OWN}\n\nWhat each project would have to commit:\n"
                      f"  alpha  {display(alpha)}: cortex.lock\n", proc.out)
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assert_moved(alpha)
        self.assertIn(f"  alpha  already on {OWN}\n\nWhat each project must commit:\n"
                      f"  alpha  {display(alpha)}: cortex.lock\n" + NOT_COMMITTED, proc.out)
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assertTrue(proc.out.endswith(f"  alpha  already on {OWN}\n\nNothing to commit, in any project.\n"),
                        proc.out)


class AllCommandLineTests(AllTestCase):
    """What needs no second version: the registry read, the options refused."""

    def test_an_empty_registry_moves_nothing_and_exits_0(self):
        for text in (None, "format = 1\n"):
            with self.subTest(text=text):
                if text is not None:
                    self.register(text=text)
                proc = self.upgrade_all()
                self.assert_ok(proc)
                self.assertEqual(proc.out, "Nothing to move. No project is registered on this machine — `cortex project "
                                           "add` registers the one of the current directory\n")

    def test_a_registry_this_cortex_refuses_is_said(self):
        self.register(text="format = 2\n")
        proc = self.upgrade_all()
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"cortex upgrade: {display(self.home / 'projects.toml')}", proc.err)
        self.assertIn("cortex self-update", proc.err)
        self.assertNotIn("Traceback", proc.err)

    def test_instructions_file_is_refused_with_all(self):
        proc = self.upgrade_all("--instructions-file", "CLAUDE.md")
        self.assertEqual(proc.returncode, 2, proc.out + proc.err)
        self.assertIn("--instructions-file names a file of one project, and --all moves every registered one", proc.err)


class AllInProcessTests(AllTestCase):
    """What a process cannot show: an error of any kind in one project, a run interrupted."""

    def options(self, **kwargs):
        return upgrade._Options(None, False, False, **kwargs)

    def test_an_os_error_in_one_project_is_its_refusal_and_the_others_move(self):
        roots = [self.make(name, synced=False) for name in ("alpha", "beta", "gamma")]
        self.register(*roots)
        moved = []

        def one(cwd, *args, **kwargs):
            if cwd.endswith("/beta"):
                raise PermissionError(13, "Permission denied", os.path.join(cwd, "cortex.toml"))
            moved.append(os.path.basename(cwd))
            return upgrade.Outcome(cwd, Version("9.9.8"), Version(OWN), files=[Path(cwd) / "cortex.toml"])

        out, err = io.StringIO(), io.StringIO()
        with self.in_process(asks=False), mock.patch.object(upgrade, "_upgrade", side_effect=one):
            code = upgrade.upgrade_all(self.options(), out, err)
        self.assertEqual(code, 1)
        self.assertEqual(moved, ["alpha", "gamma"])
        self.assertIn(f"  beta   refused: Permission denied: {display(roots[1] / 'cortex.toml')}\n", out.getvalue())
        self.assertIn(f"cortex upgrade: Permission denied: {display(roots[1] / 'cortex.toml')}\n", err.getvalue())

    def test_an_interrupted_run_waits_for_the_question_before_it_ends(self):
        # A daemon thread still in its request as Python exits crashed it, -11 (test_cli_upgrade).
        self.register(self.make("alpha", synced=False))
        asked, answered = threading.Event(), threading.Event()
        self.addCleanup(answered.set)

        def slow(own):
            asked.set()
            answered.wait(30)
            return None

        def interrupted(*args, **kwargs):
            asked.wait(5)
            threading.Timer(0.3, answered.set).start()
            raise KeyboardInterrupt

        with self.in_process(), mock.patch.object(self_update, "newer_release", side_effect=slow), \
                mock.patch.object(upgrade, "_upgrade", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                upgrade.upgrade_all(self.options(), io.StringIO(), io.StringIO())
        self.assertTrue(answered.is_set())
        self.assertFalse([t for t in threading.enumerate() if isinstance(t, upgrade._NewerRelease)])

    def in_process(self, asks=True):
        return mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url,
                                            "CORTEX_NO_UPDATE_CHECK": "" if asks else "1"})


class AllReleaseTests(AllTestCase):
    """The release newer than this cortex, asked for once for the whole run (ADR-011 §3.5)."""

    def test_the_release_is_asked_for_once_and_said_at_the_end(self):
        FakeRelease.publish(self.release.root / "download" / "99.0.0", "99.0.0")
        self.release.redirect("/latest/download/SHA256SUMS", f"{self.release.url}/download/99.0.0/SHA256SUMS")
        self.register(*(self.make(name, OWN) for name in ("alpha", "beta", "gamma")))
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assertEqual(len([path for path in self.release.requested if path.startswith("/latest/")]), 1)
        self.assertTrue(proc.out.endswith(
            f"  alpha  already on {OWN}\n  beta   already on {OWN}\n  gamma  already on {OWN}\n"
            "\nNothing to commit, in any project.\n"
            f"\nCortex 99.0.0 is out, and this cortex is {OWN}: `cortex self-update`, then `cortex upgrade --all`, "
            "moves the projects to it.\n"), proc.out)
        self.assertEqual(proc.out.count(" is out"), 1)
        self.assert_ok(self.upgrade_all(OWN))
        self.assertEqual(len([path for path in self.release.requested if path.startswith("/latest/")]), 1)


if __name__ == "__main__":
    unittest.main()
