"""``cortex upgrade --all``, ``--branch`` and ``--commit`` — every registered project moved (ADR-011 §3.5).

The projects are registered by hand, in a home of each test's own, and ``CORTEX_NO_REGISTER`` keeps
a sync from registering one. As in ``test_cli_upgrade``, the command stands for Cortex 9.9.9, and
9.9.7 and 9.9.8 are served from this machine. git runs with no configuration but each
repository's own: no system file, an empty global one.
"""

import functools
import io
import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the harness, not a package named tests
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "core")]
from cortex_cli import init, self_update, store, sync, upgrade  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402
from test_cli_upgrade import DOWNLOADS, OLD_BOOTSTRAP, OVERLAY, OWN, UpgradeTestCase, snapshot  # noqa: E402

GIT = shutil.which("git")
NEEDS_GIT = unittest.skipIf(GIT is None, "needs git")
BROKEN = OVERLAY.replace("architect.md", "nowhere.md")      # an overlay no version has the base of
NOT_COMMITTED = "Nothing was committed: --commit --message MESSAGE commits these files. Teammates run `cortex sync` " \
                "after they pull.\n"


def display(path):
    return str(path).replace(os.sep, "/")


def worktree(root):
    """What the project holds, its .git left out."""
    return {name: content for name, content in snapshot(root).items() if Path(name).parts[0] != ".git"}


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
                         "--dry-run: nothing of any project was changed — no branch was made, nothing was committed.\n")
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

    def test_commit_needs_a_message_and_a_message_needs_commit(self):
        for args, said in ((("--commit",), "--commit needs --message: a team's commit convention is its own"),
                           (("--message", "chore: cortex"), "--message is the message of --commit's commit"),
                           (("--commit", "--message", " "), "--message is empty")):
            for every in ((), ("--all",)):
                with self.subTest(args=args, every=every):
                    proc = self.run_cortex(self.tmp, "upgrade", *every, *args)
                    self.assertEqual(proc.returncode, 2, proc.out + proc.err)
                    self.assertIn(said, proc.err)

    def test_branch_and_commit_without_git_change_nothing(self):
        alpha = self.make("alpha", OWN)
        self.register(alpha)
        before = snapshot(self.code)
        for args, said in ((("--branch", "up"), "--branch runs git"),
                           (("--commit", "--message", "m"), "--commit runs git"),
                           (("--branch", "up", "--commit", "--message", "m"), "--branch and --commit run git")):
            for every in ((), ("--all",)):
                with self.subTest(args=args, every=every):
                    proc = self.run_cortex(alpha, "upgrade", *every, *args)     # PATH holds no git
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn(f"cortex upgrade: {said}, which is not on PATH — nothing was changed", proc.err)
                    self.assertEqual(snapshot(self.code), before)


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


# --------------------------------------------------------------------------- #
# --branch and --commit
# --------------------------------------------------------------------------- #

class GitTestCase(AllTestCase):
    def setUp(self):
        super().setUp()
        empty = self.tmp / "gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env.update(PATH=os.pathsep.join([os.path.dirname(GIT), self.env["PATH"]]), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=str(empty))

    def git(self, root, *args, check=True):
        proc = subprocess.run([GIT, "-C", str(root), *args], env=self.env, capture_output=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL)
        if check:
            self.assertEqual(proc.returncode, 0, f"git {' '.join(args)}: {proc.stderr}")
        return proc.stdout.rstrip() if check else proc

    def repository(self, root, ignore="cortex.local.toml\n/cortex\n"):
        """``root`` made a git repository of its own, everything committed but what ``ignore`` names."""
        self.git(root, "init", "-q")
        for key, value in (("user.name", "Cortex Tests"), ("user.email", "tests@example.com"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git(root, "config", key, value)
        (root / ".gitignore").write_text(ignore, encoding="utf-8")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "the project")
        return self.git(root, "rev-parse", "HEAD")

    def branch(self, root):
        return self.git(root, "symbolic-ref", "--short", "HEAD")

    def head(self, root):
        return self.git(root, "rev-parse", "HEAD")

    def changed(self, root, commit="HEAD"):
        """The files ``commit`` changed."""
        return sorted(self.git(root, "show", "--name-only", "--format=", commit).splitlines())


@NEEDS_GIT
class GitCommandLineTests(GitTestCase):
    def test_a_branch_name_git_does_not_take_exits_2(self):
        for every in ((), ("--all",)):
            with self.subTest(every=every):
                proc = self.run_cortex(self.tmp, "upgrade", *every, "--branch", "two..dots")
                self.assertEqual(proc.returncode, 2, proc.out + proc.err)
                self.assertIn('--branch "two..dots" is no branch name git takes', proc.err)


@DOWNLOADS
@NEEDS_GIT
class BranchTests(GitTestCase):
    def test_the_branch_is_made_in_each_git_project_and_refused_where_it_exists(self):
        alpha, beta, gamma = self.make("alpha"), self.make("beta"), self.make("gamma")
        first = self.repository(alpha)
        self.repository(beta)
        self.git(beta, "branch", "cortex-up")
        on = self.branch(beta)
        self.register(alpha, beta, gamma)
        before = worktree(beta)
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(self.branch(alpha), "cortex-up")
        self.assertEqual(self.head(alpha), first)                  # made where it was, nothing committed
        self.assert_moved(alpha)
        self.assertEqual(sorted(self.git(alpha, "status", "--porcelain").splitlines()),
                         [" M cortex.lock", " M cortex.toml"])
        self.assertIn(f"alpha: {display(alpha)}\n", proc.out)
        self.assertIn("git: on a new branch, cortex-up\n", proc.out)
        self.assertEqual(worktree(beta), before)
        self.assertEqual(self.branch(beta), on)
        self.assertIn(f"  beta   refused: the branch cortex-up exists already in {display(beta)} — nothing was changed. "
                      "Name another one with --branch\n", proc.out)
        self.assert_moved(gamma)
        self.assertIn(f"note: {display(gamma)} is in no git work tree — no branch is made there\n", proc.err)

    def test_a_project_with_nothing_to_write_gets_no_branch(self):
        alpha = self.make("alpha", OWN)
        self.repository(alpha)
        on = self.branch(alpha)
        self.register(alpha)
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assert_ok(proc)
        self.assertEqual(self.branch(alpha), on)
        self.assertEqual(self.git(alpha, "branch", "--list", "cortex-up"), "")
        self.assertIn("git: no branch cortex-up is made — nothing was written\n", proc.out)

    def test_the_branch_is_made_before_anything_is_written(self):
        alpha = self.make("alpha")
        self.repository(alpha)
        (alpha / ".git" / "index.lock").write_text("", encoding="utf-8")   # another git at work: no branch
        self.register(alpha)
        before = worktree(alpha)
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("  alpha  refused: git checkout failed — fatal: Unable to create ", proc.out)
        self.assertEqual(worktree(alpha), before)
        self.assertNotIn("cortex-up", self.git(alpha, "branch", "--list"))

    def test_two_projects_of_one_repository_share_the_branch(self):
        alpha, beta = self.make("mono/alpha"), self.make("mono/beta")
        self.repository(self.code / "mono")
        self.register(alpha, beta)
        proc = self.upgrade_all("--branch", "cortex-up", "--commit", "--message", "chore: Cortex 9.9.9")
        self.assert_ok(proc)
        self.assertEqual(self.branch(alpha), "cortex-up")
        self.assertEqual(self.changed(alpha, "HEAD~1"), ["alpha/cortex.lock", "alpha/cortex.toml"])
        self.assertEqual(self.changed(alpha), ["beta/cortex.lock", "beta/cortex.toml"])

    def test_dry_run_makes_no_branch_commits_nothing_and_refuses_as_the_run_would(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        first = self.repository(alpha)
        self.repository(beta)
        (alpha / "cortex.toml").write_text(self.pinned(alpha) + "# not committed\n", encoding="utf-8")
        self.register(alpha, beta)
        before = {root: worktree(root) for root in (alpha, beta)}
        args = ("--dry-run", "--branch", "cortex-up", "--commit", "--message", "chore: Cortex 9.9.9")
        self.assert_ok(self.upgrade_all(*args))
        self.git(beta, "branch", "cortex-up")
        proc = self.upgrade_all(*args)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("  beta   refused: the branch cortex-up exists already", proc.out)
        self.assertEqual({root: worktree(root) for root in (alpha, beta)}, before)
        self.assertEqual(self.git(alpha, "branch", "--list", "cortex-up"), "")
        self.assertEqual(self.head(alpha), first)

    def test_one_project_gets_its_branch_too_and_is_refused_where_it_exists(self):
        alpha = self.make("alpha")
        self.repository(alpha)
        self.git(alpha, "branch", "cortex-up")
        before = worktree(alpha)
        proc = self.upgrade(alpha, "--branch", "cortex-up")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"cortex upgrade: the branch cortex-up exists already in {display(alpha)} — nothing was changed",
                      proc.err)
        self.assertEqual(worktree(alpha), before)
        self.assert_ok(self.upgrade(alpha, "--branch", "cortex-2"))
        self.assertEqual(self.branch(alpha), "cortex-2")
        self.assert_moved(alpha)


@DOWNLOADS
@NEEDS_GIT
class CommitTests(GitTestCase):
    MESSAGE = "chore: move to Cortex 9.9.9"

    def test_it_commits_the_files_the_upgrade_wrote_and_nothing_else_and_pushes_nothing(self):
        alpha = self.make("alpha", bootstrap=True)
        for name in ("notes.md", "README.md"):
            (alpha / name).write_text("ours\n", encoding="utf-8")
        first = self.repository(alpha)
        origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", str(origin))
        self.git(alpha, "push", "-q", str(origin), "HEAD:refs/heads/main")
        (alpha / "notes.md").write_text("staged\n", encoding="utf-8")
        self.git(alpha, "add", "notes.md")
        (alpha / "README.md").write_text("not staged\n", encoding="utf-8")
        (alpha / "todo.md").write_text("untracked\n", encoding="utf-8")
        self.register(alpha)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s%n%P"), f"{self.MESSAGE}\n{first}")
        self.assertEqual(self.changed(alpha), ["CLAUDE.md", "cortex.lock", "cortex.toml"])
        self.assertEqual(sorted(self.git(alpha, "status", "--porcelain").splitlines()),
                         [" M README.md", "?? todo.md", "M  notes.md"])
        self.assertEqual(self.git(origin, "rev-parse", "main"), first)
        commit = self.git(alpha, "rev-parse", "--short", "HEAD")
        self.assertIn(f"\nWhat each project must commit:\n  alpha  {display(alpha)}: cortex.toml, cortex.lock and "
                      f"CLAUDE.md — committed on {self.branch(alpha)}, {commit}\n"
                      "Nothing was pushed. Teammates run `cortex sync` after they pull.\n", proc.out)

    def test_a_project_outside_git_is_said_and_nothing_is_committed_there(self):
        alpha, gamma = self.make("alpha"), self.make("gamma")
        self.repository(alpha)
        self.register(alpha, gamma)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s"), self.MESSAGE)
        self.assert_moved(gamma)
        self.assertFalse((gamma / ".git").exists())
        self.assertIn(f"note: {display(gamma)} is in no git work tree — nothing is committed there\n", proc.err)
        self.assertIn(f"  gamma  {display(gamma)}: cortex.toml and cortex.lock — not committed: in no git work tree\n",
                      proc.out)

    def test_where_git_does_not_track_the_pin_or_ignores_a_file_nothing_is_committed(self):
        # A workspace whose cortex.toml is not committed: the pin is the developer's own (ADR-010 §3.8).
        alpha, beta = self.make("alpha"), self.make("beta")
        first = {alpha: self.repository(alpha, ignore="cortex.local.toml\ncortex.toml\ncortex.lock\n"),
                 beta: self.repository(beta, ignore="cortex.local.toml\ncortex.lock\n")}
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        for root in (alpha, beta):
            self.assert_moved(root)
            self.assertEqual(self.head(root), first[root])
        self.assertIn(f"  alpha  {display(alpha)}: cortex.toml and cortex.lock — not committed: git does not track its "
                      "cortex.toml — the pin is this developer's own\n", proc.out)
        self.assertIn(f"  beta   {display(beta)}: cortex.toml and cortex.lock — not committed: git ignores cortex.lock\n",
                      proc.out)
        self.assertIn(f"note: nothing is committed in {display(beta)} — git ignores cortex.lock\n", proc.err)
        self.assertEqual(self.git(beta, "diff", "--cached", "--name-only"), "")

    def test_a_commit_git_refuses_is_that_projects_refusal_and_the_upgrade_stays(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        self.repository(alpha)
        first = self.repository(beta)
        (beta / ".git" / "index.lock").write_text("", encoding="utf-8")    # another git at work
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s"), self.MESSAGE)
        self.assert_moved(beta)
        self.assertEqual(self.head(beta), first)
        self.assertIn(f"  beta   moved 9.9.8 → {OWN}\n", proc.out)
        self.assertIn(f"  beta   {display(beta)}: cortex.toml and cortex.lock — not committed: git add failed — fatal: "
                      "Unable to create ", proc.out)
        self.assertIn("— the upgrade is done, and nothing was committed\n", proc.err)

    def test_on_the_pinned_version_the_lock_it_wrote_is_committed(self):
        alpha = self.make("alpha")
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        self.repository(alpha)                    # the pin moved by hand, and committed with the old lock
        self.register(alpha)
        proc = self.upgrade_all("--branch", "cortex-up", "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.branch(alpha), "cortex-up")
        self.assertEqual(self.changed(alpha), ["cortex.lock"])
        self.assertIn(f"  alpha  already on {OWN}\n", proc.out)

    def test_files_as_committed_already_make_no_commit(self):
        alpha = self.make("alpha", OWN)
        first = self.repository(alpha)
        lock = (alpha / "cortex.lock").read_text(encoding="utf-8")
        (alpha / "cortex.lock").write_text(lock.replace(f'"{OWN}"', '"9.9.8"', 1), encoding="utf-8")
        self.register(alpha)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.head(alpha), first)
        self.assertEqual((alpha / "cortex.lock").read_text(encoding="utf-8"), lock)
        self.assertIn(f"  alpha  {display(alpha)}: cortex.lock — not committed: cortex.lock is as committed already\n",
                      proc.out)

    def test_one_project_is_committed_and_said_so(self):
        alpha = self.make("alpha", bootstrap=True)
        self.repository(alpha)
        proc = self.upgrade(alpha, "--branch", "cortex-up", "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        commit = self.git(alpha, "rev-parse", "--short", "HEAD")
        self.assertEqual(self.changed(alpha), ["CLAUDE.md", "cortex.lock", "cortex.toml"])
        self.assertIn(f"\nCortex {OWN} is ready. Committed cortex.toml, cortex.lock, CLAUDE.md on cortex-up, {commit} — "
                      "nothing was pushed; teammates run `cortex sync` after they pull.\n", proc.out)

    def test_one_project_on_its_pinned_version_says_what_was_committed(self):
        alpha = self.make("alpha")
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        self.repository(alpha)
        proc = self.upgrade(alpha, "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        commit = self.git(alpha, "rev-parse", "--short", "HEAD")
        self.assertIn(f"\nCommitted cortex.lock on {self.branch(alpha)}, {commit} — nothing was pushed.\n", proc.out)

    def test_one_project_whose_commit_git_refuses_exits_1_and_says_what_to_commit(self):
        alpha = self.make("alpha")
        first = self.repository(alpha)
        (alpha / ".git" / "index.lock").write_text("", encoding="utf-8")
        proc = self.upgrade(alpha, "--commit", "--message", self.MESSAGE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assert_moved(alpha)
        self.assertEqual(self.head(alpha), first)
        self.assertIn(f"\nCortex {OWN} is ready. Commit cortex.toml, cortex.lock; teammates run", proc.out)
        self.assertIn("cortex upgrade: git add failed — fatal: Unable to create ", proc.err)

    def test_a_repository_git_refuses_is_that_projects_refusal(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        self.repository(alpha)
        (beta / ".git").write_text("gitdir: ../nowhere\n", encoding="utf-8")     # a worktree whose repository is gone
        self.register(alpha, beta)
        before = worktree(beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s"), self.MESSAGE)
        self.assertIn("  beta   refused: git rev-parse failed — fatal: ", proc.out)
        self.assertEqual(worktree(beta), before)

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_a_bootstrap_reached_through_a_link_is_committed_where_it_points(self):
        alpha = self.make("alpha")
        (alpha / "shared").mkdir()
        (alpha / "shared" / "assistant.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (alpha / "CLAUDE.md").symlink_to(Path("shared") / "assistant.md")      # no tool's file of its own
        self.repository(alpha)
        self.register(alpha)
        self.assert_ok(self.upgrade_all("--commit", "--message", self.MESSAGE))
        self.assertEqual(self.changed(alpha), ["cortex.lock", "cortex.toml", "shared/assistant.md"])
        self.assertEqual(self.git(alpha, "status", "--porcelain"), "")


@DOWNLOADS
@NEEDS_GIT
class BranchRefusedLaterTests(GitTestCase):
    def test_a_project_refused_once_its_branch_is_made_says_the_branch_stays(self):
        alpha = self.make("alpha")
        self.repository(alpha)
        self.register(alpha)

        def a_sync_that_fails(root, *args, **kwargs):
            raise OSError("the swap failed")

        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url,
                       "CORTEX_NO_UPDATE_CHECK": "1", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": self.env["GIT_CONFIG_GLOBAL"]}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", a_sync_that_fails), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            code = upgrade.upgrade_all(upgrade._Options(None, False, False, branch="cortex-up"), out, err)
        self.assertEqual(code, 1)
        self.assertIn("  alpha  refused: the swap failed — the branch cortex-up was made for it, and is checked out\n",
                      out.getvalue())
        self.assertEqual(self.branch(alpha), "cortex-up")
        self.assertIn('version = "9.9.8"', self.pinned(alpha))
        self.assertEqual(self.git(alpha, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
