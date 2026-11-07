"""The team's file, a developer's version and the lock — ADR-013 phase 2a (#334).

The team pins its version in ``agents/cortex.team.toml``, its lock beside it; a developer may read a
higher version in ``cortex.local.toml``, never a lower one; a committed ``cortex.toml`` pins its own
version, over the team's file. ``cortex sync`` records the versions it read in ``synced``, which
``cortex validate`` checks first. ``cortex upgrade`` moves ``cortex.toml``'s version, ``--team`` the
team's, ``--local`` the try.
"""

import errno
import io
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

import cli_harness as harness  # noqa: E402
from cortex_cli import config, init, store, sync, upgrade  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
DEV_BUILD = re.compile(r"0\.0\.0-dev(\.[0-9]+)?")
DEVELOPMENT = unittest.skipIf(harness.BINARY is not None and DEV_BUILD.fullmatch(harness.EXPECTED_VERSION or "0.0.0-dev"),
                              "a development build passes the version gate")
# The suite's own versions — 9.9.7 and 9.9.8 served, 9.9.9 this cortex's — which a build's version, 1.8.0 or a
# development one, does not order as the source does.
SOURCE = unittest.skipIf(harness.BINARY, "the suite's versions, ordered against this cortex's")
AS_ROOT = unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "root writes where its permissions say it may not")
TEAM = "agents/cortex.team.toml"
OLD = b"# Cortex AI Team\n\nWritten by 9.9.7: read cortex.local.toml first.\n"
NEW = b"# Cortex AI Team\n\nWritten by 9.9.8: read cortex.local.toml first.\n"


class TeamTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-team-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "cortex-home"
        self.home.mkdir()
        self.release = FakeRelease(("1.7.0", "9.9.7", "9.9.8"))
        self.addCleanup(self.release.close)
        self.env = harness.environment(CORTEX_HOME=str(self.home), CORTEX_RELEASES_URL=self.release.url,
                                       CORTEX_SOURCE_VERSION=OWN, CORTEX_NO_REGISTER="1", CORTEX_NO_UPDATE_CHECK="1")

    def project(self, toml='theme = "h2g2"\n', team=None, local=None, committed=True, name="app"):
        """A project: its cortex.toml, a team's file, a local file — and a .git that makes it a work tree,
        its cortex.toml committed to a cortex that looks for one, git or not on its PATH."""
        root = self.tmp / name
        root.mkdir(parents=True)
        (root / "cortex.toml").write_text(toml, encoding="utf-8")
        if team is not None:
            (root / "agents").mkdir()
            (root / TEAM).write_text(team, encoding="utf-8")
        if local is not None:
            (root / "cortex.local.toml").write_text(local, encoding="utf-8")
        if committed:
            (root / ".git").mkdir()
        return root

    def cortex(self, cwd, *args, **env):
        proc = harness.run(*args, cwd=cwd, env={**self.env, **env})
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.out + proc.err)

    def value(self, root, key, *args):
        proc = self.cortex(root, "config", key, *args)
        self.assert_ok(proc)
        return proc.out.strip()

    def local(self, root):
        return tomllib.loads((root / "cortex.local.toml").read_text(encoding="utf-8"))


class VersionTests(TeamTestCase):
    """A local try and the team's version; a committed pin and the team's file; a 2.B root."""

    @DOWNLOADS
    def test_a_local_try_above_the_team_s_version_is_read_with_the_note(self):
        root = self.project(team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN} is read — cortex.local.toml's, above the team's 9.9.8 ({TEAM}): it is not locked",
                      proc.err)
        self.assertIn(f"versions/{OWN}", self.local(root)["spec"])
        self.assertFalse((root / "cortex.lock").exists())
        self.assertFalse((root / "agents" / "cortex.team.lock").exists())   # a try writes no lock

    def test_a_pre_release_sorts_below_its_release_and_a_tie_is_the_team_s(self):
        root = self.project(team='version = "1.9.0"\n', local='version = "1.9.0-rc.1"\n')
        self.assertEqual(self.value(root, "version"), "1.9.0")
        (root / "cortex.local.toml").write_text('version = "1.9.0"\n', encoding="utf-8")
        proc = self.cortex(root, "config", "--json")
        self.assert_ok(proc)
        self.assertEqual(json.loads(proc.out)["settings"]["version"]["origin"], TEAM)
        (root / "cortex.local.toml").write_text('version = "1.9.1-rc.1"\n', encoding="utf-8")
        self.assertEqual(self.value(root, "version"), "1.9.1-rc.1")
        self.assertEqual(self.value(root, "version", "--locked"), "1.9.0")

    def test_a_try_at_the_team_s_version_is_said_to_change_nothing(self):
        root = self.project(team=f'version = "{OWN}"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"cortex.local.toml's version, {OWN}, is at or below the team's {OWN} ({TEAM}) and changes "
                      f"nothing — remove it: cortex upgrade --local {OWN}", proc.err)

    def test_a_committed_pin_before_1_8_0_reads_no_team_s_file(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\nunknown = 1\n')
        proc = self.cortex(root, "config", "version")
        self.assert_ok(proc)
        self.assertEqual(proc.out, "1.7.0\n")
        self.assertIn(f"note: {TEAM} is not read: the team's version is 1.7.0, before 1.8.0", proc.err)

    def test_at_a_2b_root_the_team_s_file_wins_over_an_uncommitted_cortex_toml(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\n', committed=False)
        self.assertEqual(self.value(root, "version"), "1.8.0")
        found = json.loads(self.cortex(root, "config", "--json").out)
        self.assertEqual((found["settings"]["version"]["origin"], found["committed"]), (TEAM, False))
        (root / ".git").mkdir()                                    # committed: the project's own pin wins
        self.assertEqual(self.value(root, "version"), "1.7.0")

    @SOURCE
    def test_a_pulled_bump_not_synced_makes_validate_refuse(self):
        root = self.project(team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        self.assert_ok(self.cortex(root, "validate"))
        (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")     # a teammate's move, pulled
        proc = self.cortex(root, "validate")
        self.assertEqual(proc.returncode, 2, proc.out + proc.err)
        self.assertIn(f"{TEAM} pins Cortex 9.9.8, and `cortex sync` recorded {OWN} — run `cortex sync`", proc.err)

    def test_a_local_version_over_a_team_s_version_before_1_8_0_is_refused(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', local='version = "1.7.5"\n')
        proc = self.cortex(root, "config")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex.local.toml: \"version\" is read once the team's version is 1.8.0 or later — the team's "
                      "is 1.7.0", proc.err)


@DEVELOPMENT
class TooNewTests(TeamTestCase):
    def test_the_refusal_names_the_file_and_its_remedy(self):
        root = self.project('version = "1.8.0"\ntheme = "h2g2"\n', local='version = "99.0.0"\n')
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Cortex 99.0.0, which its cortex.local.toml pins, newer than this cortex", proc.err)
        self.assertIn("or drop the try: cortex upgrade --local 1.8.0", proc.err)          # the team's version
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n', name="team")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"which its {TEAM} pins", proc.err)
        self.assertIn(f"or move the team's pin: cortex upgrade --team {OWN}", proc.err)

    def test_upgrade_refuses_a_move_that_still_reads_a_version_too_new(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local='version = "99.0.0"\n')
        before = (root / "cortex.toml").read_bytes()
        proc = self.cortex(root, "upgrade", "1.7.0")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("after the move, this project would read Cortex 99.0.0, cortex.local.toml's", proc.err)
        self.assertEqual((root / "cortex.toml").read_bytes(), before)


class LockTests(TeamTestCase):
    @SOURCE
    def test_a_local_version_writes_no_lock_warns_of_none_and_passes_strict(self):
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("cortex.lock", proc.out)
        self.assertFalse((root / "cortex.lock").exists())
        proc = self.cortex(root, "validate", "--strict")
        self.assert_ok(proc)
        self.assertNotIn("LOCK", proc.out + proc.err)

    @DOWNLOADS
    def test_during_a_try_upgrade_writes_the_committed_lock_and_locked_then_strict_pass(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertIn('version = "9.9.8"', (root / "cortex.toml").read_text(encoding="utf-8"))
        self.assertIn('version = "9.9.8"', (root / "cortex.lock").read_text(encoding="utf-8"))
        self.assertIn(f"This machine reads Cortex {OWN}, cortex.local.toml's — the try, above 9.9.8.", proc.out)
        self.assertEqual(self.local(root)["synced"]["reads"], OWN)
        self.assert_ok(self.cortex(root, "sync", "--locked"))
        self.assertEqual(self.local(root)["synced"]["reads"], "9.9.8")             # the version checked
        proc = self.cortex(root, "validate", "--strict")
        self.assert_ok(proc)

    def test_locked_keeps_the_local_claude_access(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local="claude_access = true\n")
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "sync", "--locked")
        self.assert_ok(proc)
        self.assertIn(f"versions/{OWN}", (root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def test_the_team_s_lock_is_beside_its_file(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("agents/cortex.team.lock: written — commit it in agents/", proc.out)
        self.assertIn(f'version = "{OWN}"', (root / "agents" / "cortex.team.lock").read_text(encoding="utf-8"))
        self.assertFalse((root / "cortex.lock").exists())
        self.assert_ok(self.cortex(root, "sync", "--locked"))
        proc = self.cortex(root, "validate", "--strict")
        self.assert_ok(proc)

    @AS_ROOT
    def test_a_team_s_folder_this_user_cannot_write_warns(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        os.chmod(root / "agents", 0o555)
        self.addCleanup(os.chmod, root / "agents", 0o755)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("warning: agents/cortex.team.lock cannot be written", proc.err)
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1)

    def test_the_default_team_s_folder_an_empty_submodule_warns(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        (root / "agents").mkdir()
        (root / ".gitmodules").write_text('[submodule "agents"]\n\tpath = agents\n\turl = ../agents\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("warning: agents/ is empty — a submodule not checked out: `git submodule update --init`", proc.err)
        proc = self.cortex(root, "validate", "--strict")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TEAM_FOLDER_EMPTY", proc.out + proc.err)


class SyncedTests(TeamTestCase):
    @SOURCE
    def test_synced_records_the_versions_read(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team=f'version = "{OWN}"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        self.assertEqual(self.local(root)["synced"], {"team": OWN, "project": "9.9.7", "pinned": OWN, "reads": OWN})

    def test_a_checkout_s_reads_is_empty(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync", "--link", "--from", str(harness.REPO)))
        self.assertEqual(self.local(root)["synced"]["reads"], "")

    @DOWNLOADS
    def test_a_project_kept_before_1_8_0_records_nothing_a_cortex_of_1_7_0_refuses(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        self.assertLessEqual(set(self.local(root)), {"theme", "spec", "claude_access", "claude_entry"})

    @DOWNLOADS
    def test_a_move_below_1_8_0_removes_synced(self):
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        self.assertIn("synced", self.local(root))
        self.assert_ok(self.cortex(root, "upgrade", "1.7.0"))
        self.assertNotIn("synced", self.local(root))

    def test_init_leaves_out_what_the_team_s_file_sets(self):
        root = self.tmp / "fresh"
        (root / "agents").mkdir(parents=True)
        (root / TEAM).write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assert_ok(proc)
        written = tomllib.loads((root / "cortex.toml").read_text(encoding="utf-8"))
        self.assertNotIn("version", written)
        self.assertNotIn("theme", written)
        self.assertIn(f"✓ cortex.toml: Cortex {OWN}, theme h2g2 — version and theme from {TEAM}", proc.out)

    @DOWNLOADS
    def test_prune_keeps_every_candidate_version(self):
        # ADR-013 §3.11: the team's file's, cortex.toml's and a try's — a try below the team's, which changes
        # nothing, included — and the version read.
        other = self.project('version = "9.9.7"\ntheme = "h2g2"\n', name="other")
        self.assert_ok(self.cortex(other, "sync"))                     # 9.9.7 in the store
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local='version = "9.9.7"\n')
        self.assert_ok(self.cortex(root, "sync"))                      # 9.9.8 read: the try changes nothing
        proc = self.cortex(self.tmp, "prune", str(root))
        self.assert_ok(proc)
        kept = {path.name for path in (self.home / "versions").iterdir() if not path.name.startswith(".")}
        self.assertTrue({"9.9.7", "9.9.8"} <= kept, kept)


class TeamFileTests(TeamTestCase):
    def test_a_team_s_version_before_1_8_0_is_refused(self):
        root = self.project('theme = "h2g2"\n', team='version = "1.7.0"\n')
        proc = self.cortex(root, "config")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f'{TEAM}: version "1.7.0" is before 1.8.0', proc.err)

    def test_what_locked_refuses(self):
        for team, fragment in (('version = "1.8.0"\nlocked = ["version"]\n', '"version" is never locked'),
                               ('locked = ["locked"]\n', 'which no lock takes'),
                               ('locked = ["theme"]\n', "which the file does not set"),
                               ('theme = "h2g2"\nlocked = ["theme", "theme"]\n', "twice"),
                               ('claude_access = true\nlocked = ["claude_access"]\n', "locked to false only")):
            with self.subTest(team=team):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team=team, name=f"p{abs(hash(team))}")
                proc = self.cortex(root, "sync")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(fragment, proc.err)

    def test_a_locked_key_overridden_warns_at_sync_and_fails_strict(self):
        root = self.project(f'version = "{OWN}"\n', team='theme = "h2g2"\nlocked = ["theme"]\n', local='theme = "none"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"theme is locked by {TEAM} to \"h2g2\": cortex.local.toml's \"none\" is not read", proc.err)
        proc = self.cortex(root, "validate", "--strict")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("LOCKED_KEY", proc.out + proc.err)

    def test_a_flag_against_a_lock_exits_1(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='sync = "store"\nlocked = ["sync"]\n')
        proc = self.cortex(root, "sync", "--link")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f'sync is locked to "store" by {TEAM}', proc.err)
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='claude_access = false\nlocked = ["claude_access"]\n',
                            name="access")
        proc = self.cortex(root, "sync", "--claude-access")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"claude_access is locked to false by {TEAM}", proc.err)

    def test_the_docs_say_claude_access_locks_to_false_only_and_why(self):
        for name in ("README.md", "docs/getting-started.md"):
            with self.subTest(name=name):
                text = (harness.REPO / name).read_text(encoding="utf-8")
                self.assertIn("`claude_access` locks to `false` only", text)
                self.assertIn("never grant one on a developer's machine", text)


class UpgradeTests(TeamTestCase):
    def test_team_without_a_team_s_file_is_refused_and_adds_a_version_to_one_without(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        proc = self.cortex(root, "upgrade", "--team")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"no {TEAM} — the team creates it, by a commit in its repository", proc.err)
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False, name="b")
        proc = self.cortex(root, "upgrade", "--team")
        self.assert_ok(proc)
        self.assertEqual(tomllib.loads((root / TEAM).read_text(encoding="utf-8"))["version"], OWN)
        self.assertIn(f'version = "{OWN}"', (root / "agents" / "cortex.team.lock").read_text(encoding="utf-8"))
        self.assertIn(f"{TEAM}: Cortex {OWN}, a version added", proc.out)
        self.assertIn("Commit agents/cortex.team.toml and agents/cortex.team.lock in agents/", proc.out)
        # An uncommitted cortex.toml follows the team's file (ADR-013 §3.1): checked, and synced.
        self.assertNotIn("pins its own version", proc.out)
        self.assertIn(f"The project's overlays pass against Cortex {OWN}", proc.out)
        self.assertEqual(self.local(root)["synced"]["pinned"], OWN)

    def test_team_before_1_8_0_is_refused_before_writing(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", "1.7.0")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reads Cortex 1.8.0 or later, and 1.7.0 is before it", proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)

    def test_local_with_all_or_team_is_a_bad_argument(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        for args, said in ((("--all", "--local", OWN), "--all, --branch and --commit do not go with it"),
                           (("--team", "--local", OWN), "not allowed with argument --team"),
                           (("--local", OWN, "--commit", "--message", "m"), "--all, --branch and --commit do not go"),
                           (("--team", "--instructions-file", "X.md"), "--team and --local write the bootstrap"),
                           (("--local", OWN, "--instructions-file", "X.md"), "--team and --local write the bootstrap")):
            with self.subTest(args=args):
                proc = self.cortex(root, "upgrade", *args)
                self.assertEqual(proc.returncode, 2)
                self.assertIn(said, proc.err)

    def test_local_at_the_team_s_version_removes_the_try(self):
        root = self.project(team=f'version = "{OWN}"\n', local='version = "99.0.0"\n')
        proc = self.cortex(root, "upgrade", "--local", OWN)
        self.assert_ok(proc)
        self.assertIn(f"cortex.local.toml: version 99.0.0 is removed — the team's {OWN} ({TEAM}) is read", proc.out)
        self.assertNotIn("version", self.local(root))

    @DOWNLOADS
    def test_local_writes_the_try_and_no_committed_file(self):
        root = self.project(team='version = "9.9.8"\n')
        self.assert_ok(self.cortex(root, "sync"))
        before = {name: (root / name).read_bytes() for name in (TEAM, "agents/cortex.team.lock", "cortex.toml")}
        proc = self.cortex(root, "upgrade", "--local", OWN)
        self.assert_ok(proc)
        self.assertEqual(self.local(root)["version"], OWN)
        self.assertEqual({name: (root / name).read_bytes() for name in before}, before)

    def test_a_plain_upgrade_on_a_version_less_cortex_toml_resumes_and_refuses_a_move(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade")
        self.assert_ok(proc)
        self.assertIn(f"Cortex {OWN}: already on it — nothing to move", proc.out)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"this project follows {TEAM}, which pins Cortex {OWN} — moving it is the team's: "
                      "`cortex upgrade --team 9.9.8`; trying it here: `cortex upgrade --local 9.9.8`", proc.err)

    def test_at_a_2b_root_a_move_is_refused_as_all_skips_the_project(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', committed=False)
        before = (root / "cortex.toml").read_bytes()
        proc = self.cortex(root, "upgrade", OWN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("`cortex upgrade --team", proc.err)
        self.assertIn("`cortex upgrade --local", proc.err)
        self.assertEqual((root / "cortex.toml").read_bytes(), before)
        (self.home / "projects.toml").write_text(f'format = 1\n\n[[project]]\nname = "app"\npath = "{harness_path(root)}"\n',
                                                 encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--dry-run")
        self.assert_ok(proc)
        self.assertIn("skipped: follows the team's 9.9.8", proc.out)

    @DOWNLOADS
    def test_with_a_try_upgrade_writes_the_bootstrap_from_the_version_it_moves(self):
        self.release.replace_spec("9.9.7", {"templates/bootstrap-instructions.md": OLD})
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": NEW})
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        (root / "CLAUDE.md").write_bytes(init.instructions(OLD, True))
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(NEW, True)))

    @DOWNLOADS
    def test_team_moves_the_file_and_its_lock_and_syncs_a_follower(self):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n')
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"{TEAM}: Cortex 9.9.8 → {OWN}", proc.out)
        self.assertIn(f'version = "{OWN}"', (root / "agents" / "cortex.team.lock").read_text(encoding="utf-8"))
        self.assertEqual(self.local(root)["synced"]["reads"], OWN)

    def test_team_stops_on_a_follower_s_failing_overlays_but_under_force(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        overlay = root / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: workspace\n"
                           "-->\n\n# x\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)
        self.assert_ok(self.cortex(root, "upgrade", "--team", OWN, "--force"))


class AllTeamTests(TeamTestCase):
    def test_all_team_moves_each_follower_s_file_and_skips_the_others(self):
        follower = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name="alpha")
        own = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', name="beta")
        bare = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name="gamma")
        (self.home / "projects.toml").write_text("format = 1\n" + "".join(
            f'\n[[project]]\nname = "{root.name}"\npath = "{harness_path(root)}"\n' for root in (follower, own, bare)),
            encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"{TEAM}: Cortex 9.9.8 → {OWN}", proc.out)
        self.assertIn(f"skipped: pins its own version, {OWN}, and does not follow its team's file", proc.out)
        self.assertIn("skipped: no team's file to move", proc.out)


class ReviewTests(TeamTestCase):
    """What the counter-review of phase 2a found."""

    @SOURCE
    @DOWNLOADS
    def test_claude_access_flags_keep_the_team_s_file(self):
        # sync --claude-access re-resolved without the team's file: a try's lock, the try under --locked.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "upgrade", "--team", "9.9.8"))      # the team's lock, 9.9.8
        proc = self.cortex(root, "sync", "--no-claude-access")
        self.assert_ok(proc)
        self.assertFalse((root / "cortex.lock").exists())
        self.assertEqual(self.local(root)["synced"], {"team": "9.9.8", "local": OWN, "pinned": "9.9.8", "reads": OWN})
        self.assert_ok(self.cortex(root, "validate"))
        proc = self.cortex(root, "sync", "--locked", "--claude-access")
        self.assert_ok(proc)
        self.assertEqual(self.local(root)["synced"]["reads"], "9.9.8")
        self.assertFalse((root / "cortex.lock").exists())

    @DOWNLOADS
    def test_upgrade_still_says_the_lock_it_writes(self):
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade", OWN)
        self.assert_ok(proc)
        self.assertIn(f"cortex.lock: Cortex {OWN}, where it named 9.9.8 — commit it\n", proc.out)
        self.assertEqual(proc.out.count("cortex.lock:"), 1)

    def test_a_synced_that_does_not_read_never_blocks_the_sync(self):
        for text in ('synced = "garbage"\n', 'synced = { pinned = "1.8.0", reads = "1.8.0", when = "now" }\n',
                     'synced = { pinned = "1.8.0" }\n', 'synced = { pinned = "x", reads = "1.8.0" }\n',
                     '[synced]\nspec = "/x"\n'):
            with self.subTest(text=text):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local=text, name=f"s{abs(hash(text))}")
                self.assertEqual(self.cortex(root, "validate").returncode, 2)       # validate refuses it
                proc = self.cortex(root, "sync")
                self.assert_ok(proc)
                self.assertEqual(self.local(root)["synced"]["reads"], OWN)        # written again
                self.assert_ok(self.cortex(root, "validate"))

    def test_a_pinless_team_s_file_at_a_2b_root_before_1_8_0_is_not_read(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='theme = "team"\nlocked = ["theme"]\n',
                            committed=False)
        proc = self.cortex(root, "config", "theme")
        self.assert_ok(proc)
        self.assertEqual(proc.out, "h2g2\n")
        self.assertIn(f"{TEAM} is not read: the team's version is 1.7.0", proc.err)

    def test_an_unparseable_team_s_file_beside_a_pin_before_1_8_0_is_only_said(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team="oops =\n")
        proc = self.cortex(root, "config", "version")
        self.assert_ok(proc)
        self.assertIn(f"{TEAM} is not read", proc.err)
        (root / "cortex.toml").write_text('version = "1.8.0"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(root, "config", "version")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(TEAM, proc.err)

    def test_a_version_newer_than_this_cortex_names_what_moves_it(self):
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n')
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"this project reads Cortex 99.0.0, which its {TEAM} pins, newer than this cortex", proc.err)
        self.assertIn("`cortex upgrade --team VERSION` moves the team's pin", proc.err)

    def test_a_read_only_team_s_folder_warns(self):
        own = str(store.own_version())
        root = self.project('theme = "h2g2"\n', team=f'version = "{own}"\n')
        real = config.write_lock

        def refused(directory, version, digest, name=config.LOCK_FILE):
            if name == config.TEAM_LOCK_FILE:
                raise OSError(errno.EROFS, "Read-only file system", str(Path(directory) / name))
            return real(directory, version, digest, name)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home), "CORTEX_NO_REGISTER": "1"}), \
                mock.patch.object(config, "write_lock", side_effect=refused):
            sync.sync(str(root), None, None, out, err, registers=False)
        self.assertIn("warning: agents/cortex.team.lock cannot be written — Read-only file system", err.getvalue())
        self.assertFalse((root / "agents" / "cortex.team.lock").exists())

    def test_local_puts_cortex_local_toml_back_when_its_sync_fails(self):
        own = str(store.own_version())
        root = self.project('theme = "h2g2"\n', team=f'version = "{own}"\n', local='theme = "mine"\n')
        before = (root / "cortex.local.toml").read_bytes()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), \
                mock.patch.object(sync, "sync", side_effect=sync.SyncError("refused")), \
                mock.patch.object(upgrade, "_overlays", return_value=False), \
                mock.patch.object(store.Store, "ensure", return_value="stored"), \
                mock.patch.object(upgrade, "_served"), \
                mock.patch.object(store.Store, "path", return_value=root):
            with self.assertRaises(sync.SyncError):
                upgrade.local_move(str(root), "99.0.0", False, False, out, err)
        self.assertEqual((root / "cortex.local.toml").read_bytes(), before)
        self.assertIn("cortex.local.toml is as it was", err.getvalue())

    @DEVELOPMENT
    def test_team_refuses_a_move_that_still_reads_a_try_too_new(self):
        root = self.project('theme = "h2g2"\n', team='version = "1.8.0"\n', local='version = "99.0.0"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", "1.8.0")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("after the move, this project would read Cortex 99.0.0", proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)

    def test_project_list_gives_pinned_s_origin(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        (self.home / "projects.toml").write_text(f'format = 1\n\n[[project]]\nname = "app"\npath = "{harness_path(root)}"\n',
                                                 encoding="utf-8")
        proc = self.cortex(self.tmp, "project", "list", "--json")
        self.assert_ok(proc)
        found = json.loads(proc.out)["projects"][0]
        self.assertEqual((found["pinned"], found["pinned_origin"]), (OWN, TEAM))

    @SOURCE
    def test_locked_during_a_try_names_what_writes_the_lock(self):
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("`cortex upgrade`, which a try leaves to it, writes it", proc.err)


@unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")
class SharedTeamFolderTests(TeamTestCase):
    """--all --team: the followers of one team's folder checked before it moves, once."""

    def shared(self, failing):
        team = self.tmp / "team"
        team.mkdir()
        (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        roots = []
        for name in ("alpha", "beta"):
            root = self.project('theme = "h2g2"\n', name=name)
            (root / "agents").symlink_to(team, target_is_directory=True)
            if name == failing:
                service = root / "svc"
                (service / "agents" / "roles" / "engineering").mkdir(parents=True)
                (service / "project-overview.md").write_text("@alias: svc\n", encoding="utf-8")
                (service / "agents" / "roles" / "engineering" / "architect.md").write_text(
                    "<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: service\n-->\n\n# x\n",
                    encoding="utf-8")
            roots.append(root)
        (self.home / "projects.toml").write_text("format = 1\n" + "".join(
            f'\n[[project]]\nname = "{root.name}"\npath = "{harness_path(root)}"\n' for root in roots), encoding="utf-8")
        return team, roots

    def test_a_failing_follower_stops_the_shared_file_in_either_order(self):
        for failing in ("alpha", "beta"):
            with self.subTest(failing=failing):
                if (self.tmp / "team").exists():
                    shutil.rmtree(self.tmp / "team")
                    for name in ("alpha", "beta"):
                        shutil.rmtree(self.tmp / name)
                team, _ = self.shared(failing)
                code, _ = upgrade.check_overlays(str(self.tmp / failing), Path(harness.REPO))
                if code == 0:
                    self.skipTest("the validator reads no service overlay here")
                before = (team / "cortex.team.toml").read_bytes()
                proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertEqual((team / "cortex.team.toml").read_bytes(), before)
                self.assertEqual(proc.out.count(f"fail against Cortex {OWN} — {TEAM} was not moved"), 3)  # 2 + summary

    def test_the_shared_file_moves_once_and_each_follower_resumes(self):
        team, roots = self.shared(None)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
        self.assert_ok(proc)
        self.assertEqual(tomllib.loads((team / "cortex.team.toml").read_text(encoding="utf-8"))["version"], OWN)
        self.assertEqual(proc.out.count(f"{TEAM}: Cortex 9.9.8 → {OWN}"), 1)
        for root in roots:
            self.assertEqual(self.local(root)["synced"]["reads"], OWN)


def harness_path(root):
    return str(root).replace(os.sep, "/")


if __name__ == "__main__":
    unittest.main()
