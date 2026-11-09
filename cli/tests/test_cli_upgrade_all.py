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
import cli_harness as harness  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402
from test_cli_upgrade import DOWNLOADS, MARKED, OLD_BOOTSTRAP, OVERLAY, OWN, UpgradeTestCase, snapshot  # noqa: E402

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

    def make(self, name, version="9.9.8", synced=True, bootstrap=False, tree=False):
        """A project of ``version``; ``tree``, with a ``.git`` that makes it a work tree to a command that only
        looks for one, git or not on its PATH: its files are to commit."""
        root = self.code / name
        root.mkdir(parents=True)
        if tree:
            (root / ".git").mkdir()
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
        alpha, beta, gamma = self.make("alpha", bootstrap=True, tree=True), self.make("beta"), self.make("gamma", tree=True)
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
        self.assertIn(f"\nempty: {display(self.code / 'empty')}\nskipped: no-project — no cortex.toml there\n", proc.out)
        self.assertIn(f"  alpha  moved 9.9.8 → {OWN}\n  empty  skipped: no-project\n  gone   skipped: missing\n",
                      self.summary(proc))
        self.assertFalse((self.code / "gone").exists())

    def test_an_unreadable_project_is_skipped_and_said_and_it_exits_0(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        (beta / "cortex.toml").write_text('version = "9.9.8\n', encoding="utf-8")       # no TOML: not read
        self.register(alpha, beta)
        before = snapshot(beta)
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assert_moved(alpha)
        self.assertEqual(snapshot(beta), before)
        self.assertIn(f"\nbeta: {display(beta)}\nskipped: unreadable — ", proc.out)
        self.assertIn(f"  alpha  moved 9.9.8 → {OWN}\n  beta   skipped: unreadable\n", self.summary(proc))

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

    @harness.outside_a_repository
    def test_projects_in_no_work_tree_have_nothing_to_commit(self):
        # 2.B workspaces: their files are committed nowhere, as cortex init says of them (ADR-008 §9).
        alpha, beta = self.make("alpha"), self.make("beta", tree=True)
        self.register(alpha, beta)
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assertTrue(proc.out.endswith(f"\nWhat each project must commit:\n"
                                          f"  alpha  {display(alpha)}: nothing — in no git work tree\n"
                                          f"  beta   {display(beta)}: cortex.toml and cortex.lock\n" + NOT_COMMITTED),
                        proc.out)
        shutil.rmtree(beta / ".git")
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace(f'"{OWN}"', '"9.9.8"'), encoding="utf-8")
        (beta / "cortex.toml").write_text(self.pinned(beta).replace(f'"{OWN}"', '"9.9.8"'), encoding="utf-8")
        proc = self.upgrade_all()
        self.assert_ok(proc)
        self.assertTrue(proc.out.endswith(f"\nNothing to commit, in any project.\n"
                                          f"  alpha  {display(alpha)}: in no git work tree\n"
                                          f"  beta   {display(beta)}: in no git work tree\n"), proc.out)

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    @harness.outside_a_repository
    def test_a_project_in_no_work_tree_still_says_the_file_linked_into_a_repository(self):
        # Its own files are committed nowhere, and the team's bootstrap is to commit where it is: the project has
        # something to commit, said with that file — never "nothing".
        alpha = self.make("alpha")
        team = self.tmp / "team"
        (team / ".git").mkdir(parents=True)
        (team / "CLAUDE.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))   # 9.9.8's, nobody changed it
        (alpha / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
        self.register(alpha)
        proc = self.upgrade_all()
        self.assert_ok(proc)
        where = os.path.realpath(team / "CLAUDE.md").replace(os.sep, "/")
        self.assertIn(f"  alpha  {display(alpha)}: cortex.toml and cortex.lock committed nowhere — in no git work tree; "
                      f"{where} — outside its git work tree: commit it where it is\n", proc.out)
        self.assertNotIn("nothing — in no git work tree", proc.out)

    def test_dry_run_changes_nothing_in_any_project_and_exits_as_the_run_would(self):
        alpha, beta = self.make("alpha", bootstrap=True, tree=True), self.make("beta")
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
        alpha = self.make("alpha", tree=True)
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
            self.assertEqual(upgrade.upgrade_all(self.options(), io.StringIO(), io.StringIO()), 130)
        self.assertTrue(answered.is_set())
        self.assertFalse([t for t in threading.enumerate() if isinstance(t, upgrade._NewerRelease)])

    def test_an_interrupted_run_says_what_each_project_did_and_exits_130(self):
        roots = [self.make(name, synced=False, tree=True) for name in ("alpha", "beta", "gamma")]
        self.register(*roots)

        def one(cwd, *args, **kwargs):
            if cwd.endswith("/beta"):
                raise KeyboardInterrupt
            return upgrade.Outcome(cwd, Version("9.9.8"), Version(OWN), files=[Path(cwd) / "cortex.toml"])

        out, err = io.StringIO(), io.StringIO()
        with self.in_process(asks=False), mock.patch.object(upgrade, "_upgrade", side_effect=one):
            code = upgrade.upgrade_all(self.options(), out, err)
        self.assertEqual(code, 130)
        self.assertTrue(out.getvalue().endswith(
            f" in each registered project:\n"
            f"  alpha  moved 9.9.8 → {OWN}\n"
            "  beta   interrupted — it may be half written: `git status` there says what was written\n"
            "  gamma  not reached\n"
            f"\nWhat each project must commit:\n  alpha  {display(roots[0])}: cortex.toml\n" + NOT_COMMITTED),
            out.getvalue())
        self.assertEqual(err.getvalue(), "cortex upgrade: interrupted — `cortex upgrade --all` again finishes the "
                                         "project cut short, and moves those not reached; what the one cut short has "
                                         "left to commit, `git status` there says\n")

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

    def git(self, root, *args, check=True, path=None):
        """git in ``root``, with the command's environment — and ``path`` after its PATH, for a set-up
        step that needs the machine's tools."""
        env = self.env if path is None else dict(self.env, PATH=os.pathsep.join([self.env["PATH"], path]))
        proc = subprocess.run([GIT, "-C", str(root), *args], env=env, capture_output=True, encoding="utf-8",
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

    def test_a_branch_name_git_would_expand_exits_2(self):
        repository = self.tmp / "repository"
        repository.mkdir()
        self.repository(repository)
        self.git(repository, "checkout", "-q", "-b", "elsewhere")
        self.git(repository, "checkout", "-q", "-")             # @{-1} names elsewhere now
        proc = self.run_cortex(repository, "upgrade", "--branch", "@{-1}")
        self.assertEqual(proc.returncode, 2, proc.out + proc.err)
        self.assertIn('--branch "@{-1}" is no branch name git takes', proc.err)


@DOWNLOADS
@NEEDS_GIT
class BranchTests(GitTestCase):
    @harness.outside_a_repository
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

    def test_a_project_refused_at_its_checks_gets_no_branch(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        self.break_overlays(alpha)
        heads = {root: self.repository(root) for root in (alpha, beta)}
        (beta / "cortex").mkdir()
        (beta / "cortex" / "notes.md").write_text("ours\n", encoding="utf-8")    # a cortex/ sync did not make
        on = {root: self.branch(root) for root in (alpha, beta)}
        self.register(alpha, beta)
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        for root in (alpha, beta):
            self.assertEqual((self.branch(root), self.head(root)), (on[root], heads[root]))
            self.assertEqual(self.git(root, "branch", "--list", "cortex-up"), "")
        self.assertIn(f"  alpha  refused: the overlays fail against Cortex {OWN} — nothing was changed\n", proc.out)
        self.assertIn("\n  beta   refused: cortex/ is ", proc.out)
        self.assertNotIn("git: on a new branch", proc.out)
        self.assertNotIn("made for it", proc.out + proc.err)

    def test_the_same_run_again_moves_what_was_refused_and_refuses_none_it_moved(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        self.break_overlays(beta)
        for root in (alpha, beta):
            self.repository(root)
        self.register(alpha, beta)
        args = ("--branch", "cortex-up", "--commit", "--message", "chore: Cortex 9.9.9")
        self.assertEqual(self.upgrade_all(*args).returncode, 1)
        moved = self.head(alpha)
        shutil.rmtree(beta / "agents")
        self.git(beta, "commit", "-q", "-am", "the overlay, fixed")
        proc = self.upgrade_all(*args)
        self.assert_ok(proc)
        self.assertEqual((self.branch(alpha), self.head(alpha)), ("cortex-up", moved))
        self.assertEqual(self.branch(beta), "cortex-up")
        self.assert_moved(beta)
        self.assertEqual(self.changed(beta), ["cortex.lock", "cortex.toml"])
        self.assertNotIn("exists already", proc.out + proc.err)
        self.assertIn(f"  alpha  already on {OWN}\n  beta   moved 9.9.8 → {OWN}\n", self.summary(proc))

    def test_a_project_on_the_branch_already_is_written_there(self):
        alpha = self.make("alpha")
        first = self.repository(alpha)
        self.git(alpha, "checkout", "-q", "-b", "cortex-up")
        self.register(alpha)
        proc = self.upgrade_all("--branch", "cortex-up", "--commit", "--message", "chore: Cortex 9.9.9")
        self.assert_ok(proc)
        self.assertIn("git: on the branch cortex-up already\n", proc.out)
        self.assertEqual(self.branch(alpha), "cortex-up")
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s%n%P"), f"chore: Cortex 9.9.9\n{first}")
        self.assert_moved(alpha)

    def test_a_branch_this_run_made_in_another_work_tree_of_the_repository_is_said(self):
        one = self.make("one")
        self.repository(one)
        two = self.code / "two"
        self.git(one, "worktree", "add", "-q", "-b", "other", str(two))
        self.assert_ok(self.run_cortex(two, "sync"))
        self.register(one, two)
        before, start = worktree(two), self.branch(one)
        proc = self.upgrade_all("--dry-run", "--branch", "cortex-up")          # refused as the run refuses it
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"  two  refused: the branch cortex-up would be checked out in {display(one)}, where this run "
                      "would make it, and git checks a branch out", proc.out)
        self.assertEqual((self.branch(one), self.branch(two)), (start, "other"))
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual((self.branch(one), self.branch(two)), ("cortex-up", "other"))
        self.assertEqual(worktree(two), before)
        self.assertIn(f"  two  refused: the branch cortex-up is checked out in {display(one)}, where this run made it, "
                      "and git checks a branch out in one work tree at a time — nothing was changed. `cortex upgrade "
                      f"--branch NAME` in {display(two)} moves it on another branch\n", proc.out)

    def test_where_git_ignores_every_file_written_no_branch_is_made(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        for root in (alpha, beta):
            self.repository(root, ignore="cortex.local.toml\ncortex.toml\ncortex.lock\n")
        on = self.branch(alpha)
        self.register(alpha)
        proc = self.upgrade_all("--branch", "cortex-up")
        self.assert_ok(proc)
        self.assert_moved(alpha)
        self.assertEqual(self.branch(alpha), on)
        self.assertEqual(self.git(alpha, "branch", "--list", "cortex-up"), "")
        self.assertIn("git: no branch cortex-up is made — git ignores cortex.toml and cortex.lock\n", proc.out)
        self.assertTrue(self.summary(proc).endswith(
            f"\nNothing to commit, in any project.\n  alpha  {display(alpha)}: git ignores cortex.toml and cortex.lock\n"),
            proc.out)
        proc = self.upgrade(beta, "--branch", "cortex-up")
        self.assert_ok(proc)
        self.assertIn(f"\nCortex {OWN} is ready. Nothing to commit: git ignores cortex.toml and cortex.lock.\n", proc.out)

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

    @harness.outside_a_repository
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
        self.assertIn(f"  gamma  {display(gamma)}: nothing — in no git work tree\n", proc.out)

    def test_where_git_does_not_track_the_pin_or_ignores_a_file_nothing_is_committed(self):
        # A workspace whose cortex.toml is not committed: the pin is the developer's own (ADR-010 §3.8).
        alpha, beta, gamma = self.make("alpha"), self.make("beta"), self.make("gamma")
        first = {alpha: self.repository(alpha, ignore="cortex.local.toml\ncortex.toml\ncortex.lock\n"),
                 beta: self.repository(beta, ignore="cortex.local.toml\ncortex.lock\n"), gamma: self.repository(gamma)}
        self.git(gamma, "rm", "-q", "--cached", "cortex.toml")
        self.git(gamma, "commit", "-q", "-m", "the pin, untracked")
        first[gamma] = self.head(gamma)
        self.register(alpha, beta, gamma)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        for root in (alpha, beta, gamma):
            self.assert_moved(root)
            self.assertEqual(self.head(root), first[root])
        self.assertIn(f"  gamma  {display(gamma)}: cortex.toml and cortex.lock — not committed: git does not track its "
                      "cortex.toml — the pin is this developer's own\n", proc.out)
        self.assertIn(f"  alpha  {display(alpha)}: nothing — git ignores cortex.toml and cortex.lock\n", proc.out)
        self.assertIn(f"note: nothing to commit in {display(alpha)} — git ignores cortex.toml and cortex.lock\n", proc.err)
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

    def test_files_holding_changes_of_yours_are_not_committed(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        (alpha / "notes.md").write_text("ours\n", encoding="utf-8")
        first = {root: self.repository(root) for root in (alpha, beta)}
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('theme = "h2g2"', 'theme = "none"'),
                                           encoding="utf-8")                  # not for the team
        (alpha / "notes.md").write_text("staged\n", encoding="utf-8")
        self.git(alpha, "add", "notes.md")
        pinned = self.pinned(beta)
        (beta / "cortex.toml").write_text(pinned + "# staged\n", encoding="utf-8")
        self.git(beta, "add", "cortex.toml")
        (beta / "cortex.toml").write_text(pinned + "# staged\n# not staged\n", encoding="utf-8")
        staged = self.git(beta, "diff", "--cached")
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        for root in (alpha, beta):
            self.assert_moved(root)
            self.assertEqual(self.head(root), first[root])
            self.assertIn(f"  {root.name:<5}  {display(root)}: cortex.toml and cortex.lock — not committed: cortex.toml "
                          "held changes of yours before the upgrade — commit by hand what is the team's\n", proc.out)
            self.assertIn(f"note: nothing is committed in {display(root)} — cortex.toml held changes of yours", proc.err)
        self.assertIn('theme = "none"', self.pinned(alpha))
        self.assertEqual(self.git(alpha, "diff", "--cached", "--name-only"), "notes.md")
        self.assertEqual(self.git(beta, "diff", "--cached"), staged)
        self.assertIn("# not staged", self.pinned(beta))

    def test_a_lock_never_committed_or_deleted_is_committed(self):
        # Written whole from the pin, it is the same whatever it held: no change of yours rides in the commit.
        alpha, beta = self.make("alpha"), self.make("beta")
        for root in (alpha, beta):
            self.repository(root)
        self.git(alpha, "rm", "-q", "--cached", "cortex.lock")                    # a 1.4.0 sync's, never committed
        self.git(alpha, "commit", "-q", "-m", "the lock, untracked")
        (beta / "cortex.lock").unlink()
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        for root in (alpha, beta):
            self.assertEqual(self.changed(root), ["cortex.lock", "cortex.toml"])
            self.assertEqual(self.git(root, "status", "--porcelain"), "")
        self.assertNotIn("changes of yours", proc.out + proc.err)

    def test_a_bootstrap_written_again_over_changes_of_yours_is_not_committed(self):
        # Written again only where no one changed it: a change of yours decided it. Committed, a
        # template restored over a team's edit — cortex init --force — would undo the edit in HEAD.
        alpha, beta = self.make("alpha", bootstrap=True), self.make("beta", bootstrap=True)
        first = {alpha: self.repository(alpha)}
        self.git(alpha, "rm", "-q", "--cached", "CLAUDE.md")                      # cortex init's, never committed
        self.git(alpha, "commit", "-q", "-m", "the bootstrap, untracked")
        first[alpha] = self.head(alpha)
        pristine = (beta / "CLAUDE.md").read_bytes()
        self.repository(beta)
        (beta / "CLAUDE.md").write_bytes(pristine + b"\nTeam rule: run the linter.\n")
        self.git(beta, "commit", "-qam", "the team's rule")
        first[beta] = self.head(beta)
        (beta / "CLAUDE.md").write_bytes(pristine)                              # the template, restored here
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        for root in (alpha, beta):
            self.assert_moved(root)
            self.assertEqual(self.head(root), first[root])
            self.assertNotEqual((root / "CLAUDE.md").read_bytes(), pristine)     # written again all the same
            self.assertIn(f"  {root.name:<5}  {display(root)}: cortex.toml, cortex.lock and CLAUDE.md — not committed: "
                          "CLAUDE.md held changes of yours before the upgrade — commit by hand what is the team's\n",
                          proc.out)
        self.assertIn("Team rule", self.git(beta, "show", "HEAD:CLAUDE.md"))

    def test_on_the_pinned_version_a_lock_never_committed_is_committed(self):
        alpha = self.make("alpha")
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        self.repository(alpha)
        self.git(alpha, "rm", "-q", "--cached", "cortex.lock")                    # untracked, the one file written
        self.git(alpha, "commit", "-q", "-m", "the lock, untracked")
        self.register(alpha)
        self.assert_ok(self.upgrade_all("--commit", "--message", self.MESSAGE))
        self.assertEqual(self.changed(alpha), ["cortex.lock"])
        self.assertEqual(self.git(alpha, "log", "-1", "--format=%s"), self.MESSAGE)

    def test_a_pin_moved_by_hand_and_not_committed_commits_nothing(self):
        # On the pinned version the lock alone is written: committed, it would say a version the
        # committed pin does not.
        alpha = self.make("alpha")
        first = self.repository(alpha)
        (alpha / "cortex.toml").write_text(self.pinned(alpha).replace('"9.9.8"', f'"{OWN}"'), encoding="utf-8")
        self.register(alpha)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.head(alpha), first)
        self.assertIn(f"  alpha  {display(alpha)}: cortex.lock — not committed: cortex.toml held changes of yours "
                      "before the upgrade — commit by hand what is the team's\n", proc.out)

    def test_a_bootstrap_of_yours_is_not_committed_and_stops_no_commit(self):
        alpha = self.make("alpha", bootstrap=True)
        self.repository(alpha)
        mine = (alpha / "CLAUDE.md").read_bytes() + b"\nOur own rule, not committed.\n"
        (alpha / "CLAUDE.md").write_bytes(mine)                          # a team's change: kept, not written
        self.register(alpha)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.changed(alpha), ["cortex.lock", "cortex.toml"])
        self.assertEqual(self.git(alpha, "status", "--porcelain"), " M CLAUDE.md")
        self.assertEqual((alpha / "CLAUDE.md").read_bytes(), mine)

    def test_a_bootstrap_outside_the_work_tree_is_written_and_left_out_of_the_commit(self):
        alpha = self.make("alpha")
        shared = self.code / "alpha-notes.md"         # the team's, beside the repository, its name starting as it
        shared.write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        self.repository(alpha)
        proc = self.upgrade(alpha, "--instructions-file", "../alpha-notes.md", "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assert_moved(alpha)
        self.assertNotEqual(shared.read_bytes(), init.instructions(OLD_BOOTSTRAP, True))
        self.assertEqual(self.changed(alpha), ["cortex.lock", "cortex.toml"])
        where = display(os.path.realpath(shared))
        self.assertIn(f"note: {where} is outside the git work tree of {display(alpha)} — not committed with it: commit "
                      "it where it is\n", proc.err)
        self.assertIn(f"\nCortex {OWN} is ready. Committed cortex.toml, cortex.lock on {self.branch(alpha)}, "
                      f"{self.git(alpha, 'rev-parse', '--short', 'HEAD')} — nothing was pushed; {where} — outside its git "
                      "work tree: commit it where it is; teammates run `cortex sync` after they pull.\n", proc.out)

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_a_bootstrap_linked_outside_the_work_tree_is_still_to_commit_where_it_is(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        shared = self.code / "alpha-notes.md"         # beside the repository, its name starting as the repository's
        shared.write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (alpha / "CLAUDE.md").symlink_to(Path("..") / "alpha-notes.md")
        for root in (alpha, beta):
            self.repository(root)
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertNotEqual(shared.read_bytes(), init.instructions(OLD_BOOTSTRAP, True))      # written all the same
        self.assertEqual(self.changed(alpha), ["cortex.lock", "cortex.toml"])
        where = display(os.path.realpath(shared))
        self.assertIn(f"note: {where} (where CLAUDE.md leads) is outside the git work tree of {display(alpha)} — not "
                      "committed with it: commit it where it is\n", proc.err)
        commit = {root: f"{self.branch(root)}, {self.git(root, 'rev-parse', '--short', 'HEAD')}" for root in (alpha, beta)}
        self.assertIn(f"\nWhat each project must commit:\n"
                      f"  alpha  {display(alpha)}: cortex.toml and cortex.lock — committed on {commit[alpha]}; {where} — "
                      "outside its git work tree: commit it where it is\n"
                      f"  beta   {display(beta)}: cortex.toml and cortex.lock — committed on {commit[beta]}\n", proc.out)

    def test_run_again_it_names_what_a_commit_refused_left_to_commit(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        for root in (alpha, beta):
            self.repository(root)
        self.git(alpha, "config", "commit.gpgsign", "true")
        self.git(alpha, "config", "gpg.program", sys.executable)        # takes none of gpg's options: it fails
        self.register(alpha, beta)
        args = ("--branch", "cortex-up", "--commit", "--message", self.MESSAGE)
        self.assertEqual(self.upgrade_all(*args).returncode, 1)
        self.git(alpha, "config", "commit.gpgsign", "false")
        proc = self.upgrade_all(*args)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing was written in {display(alpha)}, and cortex.toml and cortex.lock hold changes git "
                      "has not committed — a run cut short, or a commit refused? `git status` there says what is left "
                      "to commit\n", proc.err)
        self.assertTrue(self.summary(proc).endswith(
            "\nNothing this run wrote is to commit.\n"
            f"  alpha  {display(alpha)}: nothing written — cortex.toml and cortex.lock not committed, from before: "
            "`git status` there says what\n"), self.summary(proc))
        self.assertNotIn("beta   " + display(beta), self.summary(proc))

    def test_what_a_run_before_left_is_said_beside_what_this_one_committed(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        for root in (alpha, beta):
            self.repository(root)
        self.git(beta, "config", "commit.gpgsign", "true")
        self.git(beta, "config", "gpg.program", sys.executable)         # takes none of gpg's options: it fails
        self.register(beta)
        self.assertEqual(self.upgrade_all("--commit", "--message", self.MESSAGE).returncode, 1)
        self.git(beta, "config", "commit.gpgsign", "false")
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        commit = f"{self.branch(alpha)}, {self.git(alpha, 'rev-parse', '--short', 'HEAD')}"
        self.assertTrue(self.summary(proc).endswith(
            "\nWhat each project must commit:\n"
            f"  alpha  {display(alpha)}: cortex.toml and cortex.lock — committed on {commit}\n"
            f"  beta   {display(beta)}: nothing written — cortex.toml and cortex.lock not committed, from before: "
            "`git status` there says what\n"
            "Nothing was pushed. Teammates run `cortex sync` after they pull.\n"), self.summary(proc))

    @unittest.skipIf(os.name == "nt", "a link to a file needs a privilege on Windows")
    def test_what_is_left_is_read_inside_the_work_tree_and_nothing_when_git_cannot_tell(self):
        alpha = self.make("alpha")
        (self.code / "alpha-notes.md").write_bytes(init.instructions(OLD_BOOTSTRAP, True))
        (alpha / "CLAUDE.md").symlink_to(Path("..") / "alpha-notes.md")      # outside: git would refuse the path
        self.repository(alpha)
        (alpha / "cortex.toml").write_text(self.pinned(alpha) + "# left\n", encoding="utf-8")
        top = self.git(alpha, "rev-parse", "--show-toplevel")
        self.assertEqual(upgrade._left(str(alpha), top, None), [alpha / "cortex.toml"])
        with mock.patch.object(upgrade, "_changed", side_effect=upgrade.UpgradeError("git status failed — fatal: x")):
            self.assertEqual(upgrade._left(str(alpha), top, None), [])

    def test_a_file_git_sees_deleted_is_named_by_its_path(self):
        # Not there, it has no identity on disk to compare: its path from the top is compared instead.
        # An upgrade writes a missing lock again before it looks — a caller of these sees it all the same.
        alpha = self.make("alpha")
        self.repository(alpha)
        (alpha / "cortex.lock").unlink()
        top = self.git(alpha, "rev-parse", "--show-toplevel")
        self.assertEqual(upgrade._changed(str(alpha), top, [alpha / "cortex.toml", alpha / "cortex.lock"]),
                         [alpha / "cortex.lock"])
        self.assertEqual(upgrade._left(str(alpha), top, None), [alpha / "cortex.lock"])

    def test_on_the_pinned_version_a_bootstrap_written_outside_the_work_tree_is_said_where_it_is(self):
        alpha = self.make("alpha", OWN)
        shared = self.code / "alpha-notes.md"         # beside the repository, as 9.9.8 wrote it: the only file written
        shared.write_bytes(init.stamped(init.instructions(MARKED, True)))
        self.repository(alpha)
        proc = self.upgrade(alpha, "--instructions-file", "../alpha-notes.md", "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertNotEqual(shared.read_bytes(), init.stamped(init.instructions(MARKED, True)))
        where = display(os.path.realpath(shared))
        self.assertTrue(proc.out.endswith(f"\n{where} — outside its git work tree: commit it where it is.\n"), proc.out)

    def test_a_git_that_cannot_tell_stops_the_commit_never_the_upgrade(self):
        alpha = self.make("alpha", synced=False)
        top, pin, lock, claude = str(alpha), alpha / "cortex.toml", alpha / "cortex.lock", alpha / "CLAUDE.md"
        with mock.patch.object(upgrade, "_changed", side_effect=upgrade.UpgradeError("git status failed — fatal: x")):
            before = upgrade._before(top, top, [pin])
        self.assertEqual(before, ([], "git status failed — fatal: x"))
        self.assertEqual(upgrade._theirs(top, pin, *before, [pin, lock]),
                         "git could not tell whether cortex.toml or a bootstrap file held changes of yours — git status "
                         "failed — fatal: x")
        elsewhere = str(self.code / "elsewhere")                         # a file outside the work tree is not read
        with mock.patch.object(upgrade, "_changed", side_effect=AssertionError("read")):
            self.assertEqual(upgrade._before(elsewhere, elsewhere, [pin, claude]), ([], None))
        self.assertIsNone(upgrade._theirs(top, pin, [claude], None, [pin, lock]))        # kept, not written
        self.assertEqual(upgrade._theirs(top, pin, [claude, pin], None, [pin, lock, claude]),
                         "CLAUDE.md and cortex.toml held changes of yours before the upgrade — commit by hand what is "
                         "the team's")

    @unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")
    def test_the_top_of_the_work_tree_is_found_whatever_its_spelling(self):
        # On a disk that ignores case — macOS's — realpath keeps the case a path was typed with, and
        # git spells the top as the disk does. Here, a second name of the directory that realpath,
        # made to resolve nothing, keeps as it is.
        alpha = self.make("alpha", synced=False)
        self.repository(alpha)
        (alpha / "cortex.toml").write_text(self.pinned(alpha) + "# changed\n", encoding="utf-8")
        spelt = self.code / "Alpha-as-git-spells-it"
        spelt.symlink_to(alpha, target_is_directory=True)
        files = [alpha / "cortex.toml", alpha / "cortex.lock", self.code / "alpha-notes.md"]
        with mock.patch.object(os.path, "realpath", os.path.abspath):
            self.assertEqual([upgrade._inside(str(spelt), path) for path in files], [True, True, False])
            self.assertEqual(upgrade._changed(str(alpha), str(spelt), files[:2]), [alpha / "cortex.toml"])

    @unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")
    def test_a_change_below_the_top_is_seen_whatever_the_spelling_of_the_project(self):
        # The project in a directory of a larger work tree, its path typed in another case: git spells
        # that directory as the disk does, from the top. A change there is still a change of yours.
        # Here, a second name of the directory — not a case variant, which a disk that ignores case,
        # macOS's, takes for the first.
        alpha = self.make("alpha", synced=False)
        self.repository(self.code)
        (alpha / "cortex.toml").write_text(self.pinned(alpha) + "# changed\n", encoding="utf-8")
        spelt = self.code / "alpha-typed-otherwise"
        spelt.symlink_to(alpha, target_is_directory=True)
        top = self.git(alpha, "rev-parse", "--show-toplevel")
        with mock.patch.object(os.path, "realpath", os.path.abspath):
            self.assertEqual(upgrade._changed(str(spelt), top, [spelt / "cortex.toml", spelt / "cortex.lock"]),
                             [spelt / "cortex.toml"])

    def test_a_commit_git_refuses_says_so_and_leaves_the_files_staged(self):
        alpha = self.make("alpha")
        first = self.repository(alpha)
        self.git(alpha, "config", "commit.gpgsign", "true")
        self.git(alpha, "config", "gpg.program", sys.executable)        # takes none of gpg's options: it fails
        proc = self.upgrade(alpha, "--commit", "--message", self.MESSAGE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assert_moved(alpha)
        self.assertEqual(self.head(alpha), first)
        self.assertEqual(self.git(alpha, "diff", "--cached", "--name-only").splitlines(), ["cortex.lock", "cortex.toml"])
        self.assertIn("cortex upgrade: git refused the commit — ", proc.err)
        self.assertIn("\ncortex.toml and cortex.lock stay staged, ready to commit by hand — the upgrade is done, and "
                      "nothing was committed\n", proc.err)

    def test_a_project_that_is_a_submodule_says_its_superproject_shows_it_changed(self):
        source = self.make("source")
        self.repository(source)
        top = self.code / "super"
        top.mkdir()
        (top / "README.md").write_text("super\n", encoding="utf-8")
        self.repository(top)
        # `git submodule` is a shell script on some systems — Homebrew's on macOS — that needs basename.
        self.git(top, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "mod",
                 path=os.environ.get("PATH", ""))
        self.git(top, "commit", "-q", "-m", "the submodule")
        mod = top / "mod"
        for key, value in (("user.name", "Cortex Tests"), ("user.email", "tests@example.com")):
            self.git(mod, "config", key, value)
        self.assert_ok(self.run_cortex(mod, "sync"))
        self.register(mod)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.git(mod, "log", "-1", "--format=%s"), self.MESSAGE)
        self.assertIn(f"note: {display(mod)} is a submodule of {display(top)}, which now shows it changed — commit it "
                      "there too\n", proc.err)

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
        self.assertIn(f"\nNothing to commit, in any project.\n  alpha  {display(alpha)}: cortex.lock is as committed already\n",
                      proc.out)                                          # #326: not under what to commit
        self.assertNotIn("What each project must commit", proc.out)

    def test_a_project_as_committed_already_beside_one_committed_is_said_to_need_nothing(self):
        alpha, beta = self.make("alpha", OWN), self.make("beta", bootstrap=True)
        self.repository(alpha)
        self.repository(beta)
        lock = (alpha / "cortex.lock").read_text(encoding="utf-8")
        (alpha / "cortex.lock").write_text(lock.replace(f'"{OWN}"', '"9.9.8"', 1), encoding="utf-8")
        self.register(alpha, beta)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertIn(f"  alpha  {display(alpha)}: nothing — cortex.lock is as committed already\n", proc.out)
        self.assertIn(f"  beta   {display(beta)}: cortex.toml, cortex.lock and CLAUDE.md — committed on ", proc.out)

    def test_files_as_committed_already_are_not_said_to_commit(self):
        # #313: on its version, a lock out of step written back as committed — the ending asks to commit
        # nothing that --commit found committed already.
        alpha = self.make("alpha", OWN)
        first = self.repository(alpha)
        lock = (alpha / "cortex.lock").read_text(encoding="utf-8")
        (alpha / "cortex.lock").write_text(lock.replace(f'"{OWN}"', '"9.9.8"', 1), encoding="utf-8")
        proc = self.upgrade(alpha, "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.head(alpha), first)
        self.assertIn(f"note: nothing is committed in {display(alpha)} — cortex.lock is as committed already\n", proc.err)
        self.assertNotIn("Commit cortex.lock", proc.out)

    def test_a_move_to_files_as_committed_already_asks_to_commit_nothing(self):
        # A pin moved back by hand, not committed: the move writes the files as they are committed.
        alpha = self.make("alpha", OWN)
        first = self.repository(alpha)
        for name in ("cortex.toml", "cortex.lock"):
            text = (alpha / name).read_text(encoding="utf-8")
            (alpha / name).write_text(text.replace(f'"{OWN}"', '"9.9.8"', 1), encoding="utf-8")
        proc = self.upgrade(alpha, "--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        self.assertEqual(self.head(alpha), first)
        self.assertIn(f"note: nothing is committed in {display(alpha)} — cortex.toml and cortex.lock are as committed "
                      "already\n", proc.err)
        self.assertIn(f"\nCortex {OWN} is ready. Teammates run `cortex sync` after they pull.\n", proc.out)

    @unittest.skipIf(os.name == "nt", "a symbolic link needs a privilege on Windows")
    def test_as_committed_already_a_file_written_outside_is_still_said_to_commit_there(self):
        # #326: what is as committed is the work tree's; a file written outside it is still to commit where it is.
        alpha = self.make("alpha", OWN)
        shared = self.code / "alpha-notes.md"
        shared.write_bytes(init.stamped(init.instructions(MARKED, True)))
        os.symlink(os.path.join("..", "alpha-notes.md"), alpha / "CLAUDE.md")
        self.repository(alpha)
        lock = (alpha / "cortex.lock").read_text(encoding="utf-8")
        (alpha / "cortex.lock").write_text(lock.replace(f'"{OWN}"', '"9.9.8"', 1), encoding="utf-8")
        self.register(alpha)
        proc = self.upgrade_all("--commit", "--message", self.MESSAGE)
        self.assert_ok(proc)
        where = display(os.path.realpath(shared))
        self.assertIn(f"\nWhat each project must commit:\n  alpha  {display(alpha)}: nothing — cortex.lock is as committed "
                      f"already; {where} — outside its git work tree: commit it where it is\n", proc.out)

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

    def test_a_repository_git_refuses_above_the_project_is_its_refusal_too(self):
        # The project in a subdirectory of the work tree: the .git git refuses is above its root, not at it — no
        # root in no work tree, whose files would be written and said committed nowhere.
        beta = self.make("outer/beta")
        (self.code / "outer" / ".git").write_text("gitdir: ../nowhere\n", encoding="utf-8")
        before = worktree(beta)
        proc = self.upgrade(beta, "--commit", "--message", self.MESSAGE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("git rev-parse failed — fatal: ", proc.err)
        self.assertNotIn("in no git work tree", proc.out + proc.err)
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
class CommitInProcessTests(GitTestCase):
    """What a process cannot show: a git that fails at one step only."""

    MESSAGE = CommitTests.MESSAGE

    def test_a_git_that_cannot_tell_before_the_upgrade_commits_nothing(self):
        alpha = self.make("alpha")
        first = self.repository(alpha)
        changed, calls = upgrade._changed, []

        def blind_before(*args, **kwargs):            # the status read before anything is written, and it alone
            calls.append(args)
            if len(calls) == 1:
                raise upgrade.UpgradeError("git status failed — fatal: index file corrupt")
            return changed(*args, **kwargs)

        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url,
                       "CORTEX_NO_UPDATE_CHECK": "1", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": self.env["GIT_CONFIG_GLOBAL"]}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment), mock.patch.object(upgrade, "_changed", blind_before), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            code = upgrade.upgrade(str(alpha), None, False, False, None, out, err, message=self.MESSAGE)
        self.assertEqual(code, 0, err.getvalue())
        self.assert_moved(alpha)
        self.assertEqual(self.head(alpha), first)
        self.assertIn(f"note: nothing is committed in {display(alpha)} — git could not tell whether cortex.toml or a "
                      "bootstrap file held changes of yours — git status failed — fatal: index file corrupt\n",
                      err.getvalue())
        self.assertIn(f"\nCortex {OWN} is ready. Commit cortex.toml, cortex.lock; teammates run", out.getvalue())


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

    def test_a_project_interrupted_once_its_branch_is_made_says_the_branch(self):
        alpha, beta = self.make("alpha"), self.make("beta")
        for root in (alpha, beta):
            self.repository(root)
        self.register(alpha, beta)

        def interrupted(root, *args, **kwargs):
            raise KeyboardInterrupt

        environment = {"CORTEX_HOME": str(self.home), "CORTEX_RELEASES_URL": self.release.url,
                       "CORTEX_NO_UPDATE_CHECK": "1", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": self.env["GIT_CONFIG_GLOBAL"]}
        with mock.patch.dict(os.environ, environment), mock.patch.object(sync, "sync", interrupted), \
                mock.patch.object(store.Store, "__init__", functools.partialmethod(store.Store.__init__, own_version=OWN)):
            out, err = io.StringIO(), io.StringIO()
            code = upgrade.upgrade_all(upgrade._Options(None, False, False, branch="cortex-up"), out, err)
            self.assertEqual(code, 130)
            self.assertIn("  alpha  interrupted on the branch cortex-up, made for it — it may be half written: `git status` "
                          "there says what was written\n  beta   not reached\n", out.getvalue())
            self.assertIn(f"\nnote: interrupted — the branch cortex-up was made for it, and is checked out in "
                          f"{display(alpha)}: `git status` there says what was written\n", err.getvalue())
            self.assertEqual(self.branch(alpha), "cortex-up")
            self.assertEqual(self.git(alpha, "status", "--porcelain"), "")      # the files put back as they were
            out, err = io.StringIO(), io.StringIO()
            with self.assertRaises(KeyboardInterrupt):
                upgrade.upgrade(str(beta), None, False, False, None, out, err, branch="cortex-up")
            self.assertIn(f"\nnote: interrupted — the branch cortex-up was made for it, and is checked out in "
                          f"{display(beta)}", err.getvalue())
        self.assertEqual(self.branch(beta), "cortex-up")


if __name__ == "__main__":
    unittest.main()
