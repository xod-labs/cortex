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

import subprocess  # noqa: E402

import cli_harness as harness  # noqa: E402
from cortex_cli import config, init, store, sync, upgrade, validate  # noqa: E402
from cortex_cli.semver import Version  # noqa: E402
from release_fixture import FakeRelease  # noqa: E402

OWN = harness.EXPECTED_VERSION if harness.BINARY else "9.9.9"
DOWNLOADS = unittest.skipIf(harness.BINARY, "a build of no release downloads no version")
# The suite's own versions — 9.9.7 and 9.9.8 served, 9.9.9 this cortex's — which a build's version, 1.8.0 or a
# development one, does not order as the source does.
SOURCE = unittest.skipIf(harness.BINARY, "the suite's versions, ordered against this cortex's")
# A source checkout of the branch that prepares the release after the suite's 9.9.9: a build of no release.
DEV_SOURCE = "9.9.10-dev.7"
# --team with no VERSION from a build of no release, X.Y.Z-dev.N, names a version first, whatever the pin
# (ADR-013 §3.11, ADR-009 §9).
DEV_OWN = re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-dev(\.[0-9]+)?", OWN) is not None
# --team never writes a build of no release's version, which no teammate can download (ADR-009 §9): a test that
# moves the team's file to this cortex's own runs from a release, a candidate, or the source's 9.9.9.
TEAM_RELEASE = unittest.skipIf(DEV_OWN, "--team writes no build of no release's version")
# Nothing written for a build of no release's version is said to commit, nor committed (ADR-009 §9): a lock of this
# cortex's own version says `commit it` from a release, a candidate, or the source's 9.9.9 — and a test of what
# --team, --branch and --commit do in git at this cortex's own version runs from those only.
COMMIT_IT = "" if DEV_OWN else " — commit it"
COMMITS_RELEASE = unittest.skipIf(DEV_OWN, "nothing written for a build of no release is committed")
# A version that reads the team's file and that this cortex serves: 1.8.0 — or a pre-release's own, a candidate's
# 1.8.0-rc.1 or a build of no release's 1.8.0-dev.N, which has 1.8.0's features and is older than 1.8.0 (ADR-009 §9).
TEAM_PIN = OWN if Version(OWN).pre else "1.8.0"
# --team VERSION moves the team's pin to a release this cortex serves that reads the team's file: none from a build of
# no release that prepares 1.8.0 — 1.8.0 and its candidates are newer than it (ADR-009 §9).
TEAM_MOVES = not DEV_OWN or Version("{}.{}.{}".format(*Version(OWN).core)) > Version("1.8.0")
GIT = shutil.which("git")
NEEDS_GIT = unittest.skipIf(GIT is None or os.name == "nt", "needs git")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
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

    def git(self, where, *args):
        return subprocess.run([GIT, "-C", str(where), *args], check=True, capture_output=True, text=True,
                              env={**os.environ, **GIT_ENV})

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

    @harness.outside_a_repository
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
                      "is 1.7.0: the team moves to 1.8.0 first, or remove `version` from cortex.local.toml", proc.err)


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
        # A build of no release names VERSION, never its own version: no teammate can download it (ADR-009 §9) — and
        # none, where no release it serves reads the team's file.
        if TEAM_MOVES:
            self.assertIn(f"or move the team's pin: cortex upgrade --team {'VERSION' if DEV_OWN else OWN}", proc.err)
        else:
            self.assertNotIn("--team", proc.err)

    @SOURCE
    def test_from_a_build_of_no_release_the_team_s_pin_is_moved_to_a_version_named(self):
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n')
        for args in (("sync",), ("init", "--tool", "claude"), ("validate",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args, CORTEX_SOURCE_VERSION=DEV_SOURCE)
                self.assertNotEqual(proc.returncode, 0, proc.err)
                self.assertIn(f"Cortex 99.0.0, which its {TEAM} pins, newer than this cortex, {DEV_SOURCE}", proc.err)
                self.assertIn("or move the team's pin: cortex upgrade --team VERSION", proc.err)
                self.assertNotIn(f"--team {DEV_SOURCE}", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_pin_of_no_release_in_a_developer_s_cortex_toml_is_moved_by_hand(self):
        # A 2.B root: cortex.toml, uncommitted beside the team's file, holds this developer's version, which no
        # command writes — `cortex upgrade VERSION` resumes the team's, `--local` writes a try below it.
        root = self.project('version = "9.9.10-dev.3"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', committed=False)
        by_hand = ("pin a release by hand: set cortex.toml's version to one, or remove it to read the team's — "
                   "uncommitted beside the team's file, it is this developer's, which no command writes")
        for args in (("sync",), ("upgrade",), ("validate",), ("config", "version"), ("config",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode != 0, args[0] != "config", proc.err)
                self.assertIn(by_hand, proc.out + proc.err)
                self.assertNotIn("cortex upgrade VERSION", proc.out + proc.err)
        # Below a build of no release that the store does not hold: no release serves it either.
        proc = self.cortex(root, "sync", CORTEX_SOURCE_VERSION=DEV_SOURCE)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"Cortex 9.9.10-dev.3 is a build of no release, which no release serves — {by_hand}", proc.err)
        # Committed, the same pin is the team's, moved by a command.
        (root / ".git").mkdir()
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("pin a release:\n    cortex upgrade VERSION", proc.err)
        self.assertNotIn("by hand", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_init_names_the_command_of_the_file_that_pins_a_build_of_no_release(self):
        # As cortex sync names it: the file that pins the version, and the command that moves its pin.
        by_hand = "pin a release by hand: set cortex.toml's version to one"
        cases = (("2.B", 'version = "9.9.9-dev.3"\ntheme = "h2g2"\n', 'version = "9.9.8"\n', None, False, by_hand),
                 ("try", 'version = "9.9.8"\ntheme = "h2g2"\n', None, 'version = "9.9.9-dev.2"\n', True,
                  "pin a release: `cortex upgrade --local VERSION`"),
                 ("team", 'theme = "h2g2"\n', 'version = "9.9.9-dev.2"\n', None, True,
                  "pin a release: `cortex upgrade --team VERSION`"),
                 # Committed, the same pin is the project's own, which a plain upgrade moves.
                 ("committed", 'version = "9.9.9-dev.3"\ntheme = "h2g2"\n', 'version = "9.9.8"\n', None, True,
                  "pin a release: `cortex upgrade VERSION`"))
        for name, toml, team, local, committed, remedy in cases:
            with self.subTest(case=name):
                root = self.project(toml, team=team, local=local, committed=committed, name=name)
                synced = self.cortex(root, "sync")
                proc = self.cortex(root, "init", "--tool", "claude")
                self.assertEqual((synced.returncode, proc.returncode), (1, 1), synced.err + proc.err)
                self.assertIn(remedy, synced.err)
                self.assertIn(remedy, proc.err)
                if name != "committed":
                    self.assertNotIn("`cortex upgrade VERSION`", proc.err)
                else:
                    self.assertNotIn("by hand", synced.err + proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_team_s_file_that_pins_none_beside_a_developer_s_pin_of_no_release_is_moved_by_hand(self):
        # A 2.B root whose team's file pins no version: cortex.toml's, uncommitted, is read — and no command
        # writes it, a plain move refused in a project that follows the team's file: each remedy says so.
        root = self.project('version = "9.9.10-dev.3"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False)
        by_hand = "pin a release by hand: set cortex.toml's version to one"
        for args in (("sync",), ("upgrade",), ("upgrade", "--team", "9.9.8"), ("upgrade", "--local", "9.9.8"),
                     ("validate",), ("init", "--tool", "claude"), ("config", "version"), ("config",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode != 0, args[0] != "config", proc.err)
                self.assertIn(by_hand, proc.out + proc.err)
                self.assertNotIn("cortex upgrade VERSION", proc.out + proc.err)
                # Too new, as below this cortex: the team's file pins none — removed, no version would be read.
                self.assertNotIn("remove it", proc.out + proc.err)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"follows {TEAM}, which pins no version: Cortex 9.9.10-dev.3 is its uncommitted cortex.toml's, "
                      "which no command writes — set it there by hand", proc.err)
        self.assertNotIn("remove it", proc.err)       # the team's file pins none: removed, no version would be read
        self.assertNotIn("which pins Cortex 9.9.10-dev.3", proc.err)
        # A new project beside it, made by a build of no release, is told the same.
        new = self.project('theme = "h2g2"\n', team='theme = "h2g2"\n', committed=False, name="new")
        (new / "cortex.toml").unlink()
        proc = self.cortex(new, "init", "--tool", "claude", CORTEX_SOURCE_VERSION="9.9.9-dev.1")
        self.assert_ok(proc)
        self.assertIn(f"warning: cortex.toml pins Cortex 9.9.9-dev.1, a build of no release, which no teammate can "
                      f"download — {by_hand} — uncommitted", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_project_too_new_whose_key_this_cortex_does_not_know_is_moved_by_hand(self):
        # cortex config reads it loosely — the version before any key: the team's file there, the remedy is an edit.
        root = self.project('version = "9.9.10-dev.3"\ntheme = "h2g2"\nfuture_key = 1\n', team='version = "9.9.8"\n',
                            committed=False)
        for args in (("config", "version"), ("config",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assert_ok(proc)
                self.assertIn("pin a release by hand: set cortex.toml's version to one", proc.out + proc.err)
                self.assertNotIn("cortex upgrade VERSION", proc.out + proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_build_of_no_release_is_written_where_no_teammate_reads_it_only(self):
        # The team's file and a committed cortex.toml are every teammate's: a version none can download is refused
        # there (ADR-009 §9), and allowed — said — in an uncommitted cortex.toml and in a try.
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"}
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", "9.9.9-dev.1", **dev)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"Cortex 9.9.9-dev.1 is a build of no release, which no teammate can download, and every teammate "
                      f"reads {TEAM} — nothing was changed. Pin a release there; tried on this machine: "
                      "`cortex upgrade --local 9.9.9-dev.1`", proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)
        self.assertFalse((root / "agents" / "cortex.team.lock").exists())
        (self.home / "projects.toml").write_text(f'format = 1\n\n[[project]]\nname = "app"\npath = "{harness_path(root)}"\n',
                                                 encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", "9.9.9-dev.1", **dev)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"refused: Cortex 9.9.9-dev.1 is a build of no release, which no teammate can download, and every "
                      f"teammate reads {TEAM} — nothing was changed", proc.out)
        self.assertEqual((root / TEAM).read_bytes(), before)
        proc = self.cortex(root, "upgrade", "--local", "9.9.9-dev.1", **dev)        # a try: this machine's
        self.assert_ok(proc)
        self.assertIn("warning: cortex.local.toml pins Cortex 9.9.9-dev.1, a build of no release, which no teammate "
                      "can download — pin a release: `cortex upgrade --local VERSION`", proc.err)
        # A release candidate is a release: the team pins it.
        proc = self.cortex(root, "upgrade", "--team", "9.9.9-rc.1", CORTEX_SOURCE_VERSION="9.9.9-rc.1")
        self.assert_ok(proc)
        self.assertIn('version = "9.9.9-rc.1"', (root / TEAM).read_text(encoding="utf-8"))
        # cortex.toml committed: refused; not committed, written and said, with no release notes to name.
        mine = self.project('version = "9.9.8"\ntheme = "h2g2"\n', name="mine")
        before = (mine / "cortex.toml").read_bytes()
        proc = self.cortex(mine, "upgrade", "9.9.9-dev.1", **dev)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("every teammate reads its committed cortex.toml — nothing was changed. Pin a release there; tried "
                      "on this machine: `cortex upgrade --local 9.9.9-dev.1`", proc.err)
        self.assertEqual((mine / "cortex.toml").read_bytes(), before)
        followed = self.cortex(mine, "upgrade", "--local", "9.9.9-dev.1", **dev)        # named: it works
        self.assert_ok(followed)
        self.assertEqual(self.local(mine)["version"], "9.9.9-dev.1")
        self.assert_ok(self.cortex(mine, "upgrade", "--local", "9.9.8", **dev))       # the try dropped again
        self.assertNotIn("version", self.local(mine))
        shutil.rmtree(mine / ".git")
        proc = self.cortex(mine, "upgrade", "9.9.9-dev.1", **dev)
        self.assert_ok(proc)
        self.assertIn("Cortex 9.9.8 → 9.9.9-dev.1: an upgrade — a build of no release, with no release notes", proc.out)
        self.assertNotIn("/tag/", proc.out)
        self.assertIn("warning: cortex.toml pins Cortex 9.9.9-dev.1, a build of no release, which no teammate can "
                      "download — pin a release: `cortex upgrade VERSION`", proc.err)
        self.assertIn('version = "9.9.9-dev.1"', (mine / "cortex.toml").read_text(encoding="utf-8"))

    @SOURCE
    @harness.outside_a_repository
    def test_a_developer_s_pin_of_no_release_below_this_cortex_is_moved_by_hand_by_every_command(self):
        # A 2.B root whose team's file pins none, cortex.toml at a build of no release below this cortex, not in the
        # store: no release serves it, and no command writes it — every command names the edit, which works; removing
        # the version would leave none to read.
        toml = 'version = "9.9.9-dev.3"\ntheme = "h2g2"\n'
        by_hand = ("pin a release by hand: set cortex.toml's version to one — uncommitted beside the team's file, it is "
                   "this developer's, which no command writes")
        root = self.project(toml, team='theme = "h2g2"\n', committed=False)
        team = (root / TEAM).read_bytes()
        for args in (("sync",), ("upgrade",), ("upgrade", "--dry-run"), ("upgrade", "--team", "9.9.8"),
                     ("init", "--tool", "claude")):          # validate, never synced, names cortex sync
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn(by_hand, proc.err)
                for other in ("cortex upgrade VERSION", "--team VERSION", "--local VERSION", "remove it"):
                    self.assertNotIn(other, proc.err)
                self.assertEqual((root / TEAM).read_bytes(), team)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"follows {TEAM}, which pins no version: Cortex 9.9.9-dev.3 is its uncommitted cortex.toml's, "
                      "which no command writes — set it there by hand. Nothing was changed", proc.err)
        # With a try above it, init still checks the version the bootstrap follows, and names the same edit.
        tried = self.project(toml, team='theme = "h2g2"\n', local=f'version = "{OWN}"\n', committed=False, name="try")
        proc = self.cortex(tried, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(by_hand, proc.err)
        self.assertNotIn("cortex upgrade VERSION", proc.err)
        # The edit named, made: the release is read.
        for project in (root, tried):
            (project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
            self.assert_ok(self.cortex(project, "sync"))
        # Beside a team's file that pins a version, removing it reads the team's: the remedy names it, and it works.
        pins = self.project(toml, team='version = "9.9.8"\n', committed=False, name="pins")
        proc = self.cortex(pins, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("set cortex.toml's version to one, or remove it to read the team's", proc.err)
        (pins / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        self.assert_ok(self.cortex(pins, "sync"))

    @SOURCE
    def test_below_a_try_init_names_the_command_of_the_team_s_pin_of_no_release(self):
        # The try is served; the team's pin, which the bootstrap follows, is not: init names its file's command.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.9-dev.2"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("pin a release: `cortex upgrade --team VERSION`", proc.err)
        self.assertNotIn("`cortex upgrade VERSION`", proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "--team", "9.9.8"))       # named, and followed
        self.assertEqual(tomllib.loads((root / TEAM).read_text(encoding="utf-8"))["version"], "9.9.8")

    @SOURCE
    def test_a_refused_build_of_no_release_names_a_try_only_where_one_works(self):
        # A try is read above the team's version, of a version this cortex serves: --local is named then only.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n')
        for wanted in ("9.9.10-dev.2", "9.9.8-dev.4"):     # above this cortex; below it, not in the store
            with self.subTest(wanted=wanted):
                proc = self.cortex(root, "upgrade", "--team", wanted)
                self.assertEqual(proc.returncode, 1, proc.err)
                self.assertIn(f"Cortex {wanted} is a build of no release, which no teammate can download, and every "
                              f"teammate reads {TEAM} — nothing was changed. Pin a release there", proc.err)
                self.assertNotIn("--local", proc.err)
        mine = self.project('version = "9.9.8"\ntheme = "h2g2"\n', name="mine")
        proc = self.cortex(mine, "upgrade", "9.9.10-dev.2")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("every teammate reads its committed cortex.toml — nothing was changed. Pin a release there", proc.err)
        self.assertNotIn("--local", proc.err)
        # Named as a try, or at the team's pin, the remedy is the command of that file.
        proc = self.cortex(root, "upgrade", "--local", "9.9.10-dev.2")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("pin a release: `cortex upgrade --local VERSION`", proc.err)
        pinned = self.project('theme = "h2g2"\n', team='version = "9.9.8-dev.4"\n', name="pinned")
        proc = self.cortex(pinned, "upgrade", "--team", "9.9.8-dev.4")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("pin a release: `cortex upgrade --team VERSION`", proc.err)
        # Served and above the team's version, the try is named — and it is one.
        proc = self.cortex(root, "upgrade", "--team", "9.9.9-dev.1", CORTEX_SOURCE_VERSION="9.9.9-dev.1")
        self.assertIn("tried on this machine: `cortex upgrade --local 9.9.9-dev.1`", proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.9-dev.1", CORTEX_SOURCE_VERSION="9.9.9-dev.1"))
        self.assertEqual(self.local(root)["version"], "9.9.9-dev.1")

    @SOURCE
    def test_a_committed_pin_at_this_build_s_own_version_resumes(self):
        # Not a move: the pin names this build already — nothing new is written where every teammate reads it.
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"}
        root = self.project('version = "9.9.9-dev.1"\ntheme = "h2g2"\n')
        for args in (("upgrade",), ("upgrade", "9.9.9-dev.1"), ("upgrade",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args, **dev)
                self.assert_ok(proc)
                self.assertIn("Cortex 9.9.9-dev.1: already on it — nothing to move", proc.out)
                self.assertNotIn("commit it", proc.out + proc.err)
        self.assertNotIn("Nothing to commit", proc.out)       # nothing written, the last time: nothing said of it

    @SOURCE
    @NEEDS_GIT
    def test_what_a_run_writes_for_a_build_of_no_release_is_never_said_to_commit(self):
        # No teammate can download it (ADR-009 §9): written where only this machine reads it, nothing of it is to
        # commit — no "commit it", no branch, no commit, nothing in the summary.
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1", "PATH": os.path.dirname(GIT), **GIT_ENV}
        roots = []
        for name in ("a", "b"):
            root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', committed=False, name=name)
            self.git(root, "init", "-q")
            self.git(root, "commit", "-q", "--allow-empty", "-m", "start")    # cortex.toml untracked: this developer's
            roots.append(root)
        proc = self.cortex(roots[0], "upgrade", "9.9.9-dev.1", "--branch", "try", "--commit", "--message", "m", **dev)
        self.assert_ok(proc)
        self.assertIn("Cortex 9.9.9-dev.1 is ready on this machine. Nothing to commit: Cortex 9.9.9-dev.1 is a build "
                      "of no release, which no teammate can download", proc.out)
        self.assertIn("Cortex 9.9.9-dev.1 is a build of no release, which no teammate can download — no branch is made",
                      proc.err)
        for said in ("commit it", "Commit ", "Committed", "eammates run", "on a new branch", "nothing was written"):
            self.assertNotIn(said, proc.out + proc.err)
        self.assertNotIn("try", self.git(roots[0], "branch", "--list").stdout)
        self.assertEqual(self.git(roots[0], "rev-list", "--count", "HEAD").stdout.strip(), "1")
        # A committed pin at this build's own version, resumed: what the resume writes is not committed either.
        pinned = self.project('version = "9.9.9-dev.1"\ntheme = "h2g2"\n', committed=False, name="pinned")
        self.git(pinned, "init", "-q")
        self.git(pinned, "add", "cortex.toml")
        self.git(pinned, "commit", "-q", "-m", "pin")
        proc = self.cortex(pinned, "upgrade", "--commit", "--message", "m", **dev)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing is committed in {harness_path(pinned)} — Cortex 9.9.9-dev.1 is a build of no "
                      "release, which no teammate can download", proc.err.replace(os.sep, "/"))
        self.assertTrue((pinned / "cortex.lock").is_file())
        self.assertEqual(self.git(pinned, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        registry = "format = 1\n" + "".join(f'\n[[project]]\nname = "{root.name}"\npath = "{harness_path(root)}"\n'
                                           for root in roots)
        (self.home / "projects.toml").write_text(registry, encoding="utf-8")
        (roots[0] / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "9.9.9-dev.1", **dev)
        self.assert_ok(proc)
        self.assertIn("Nothing to commit, in any project.", proc.out)
        self.assertEqual(proc.out.count("Cortex 9.9.9-dev.1 is a build of no release, which no teammate can download"), 2)
        self.assertNotIn("eammates run", proc.out)
        # cortex init from a build of no release, in a repository: the lock written, not to commit.
        new = self.tmp / "new"
        new.mkdir()
        self.git(new, "init", "-q")
        proc = self.cortex(new, "init", "--tool", "claude", **dev)
        self.assert_ok(proc)
        self.assertIn("cortex.lock: written", proc.out)
        self.assertNotIn("commit it", proc.out)
        # --team at a team's pin of no release: its lock written, and neither committed nor said to commit.
        team = self.project('theme = "h2g2"\n', team='version = "9.9.9-dev.1"\n', name="team")
        proc = self.cortex(team, "upgrade", "--team", "--commit", "--message", "m", **dev)
        self.assert_ok(proc)
        self.assertIn("note: Cortex 9.9.9-dev.1 is a build of no release, which no teammate can download — nothing is "
                      "committed in agents/", proc.err)
        self.assertIn("Nothing to commit: Cortex 9.9.9-dev.1 is a build of no release", proc.out)
        self.assertTrue((team / "agents" / "cortex.team.lock").is_file())
        for said in ("Commit ", "Committed", "eammates run"):
            self.assertNotIn(said, proc.out)

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
        self.assertIn(f"agents/cortex.team.lock: written{COMMIT_IT}{' in agents/' if COMMIT_IT else ''}\n", proc.out)
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
    @harness.outside_a_repository
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

    def test_a_committed_pin_before_1_8_0_beside_a_team_s_file_syncs_its_own_version_and_records_nothing(self):
        # The state the bootstrap's rule without `synced` reads (ADR-013 §3.10): a committed cortex.toml before
        # 1.8.0 is the team's version, its team's file not read — the sync reads 1.7.0 and records no `synced`,
        # so the assistant, seeing `cortex.toml` committed and a spec of 1.7.0, runs nothing. Committed as git
        # says, and where git refuses — a `.git` it cannot read — by the `.git` above.
        cases = ["refusing"] + ([] if GIT is None or os.name == "nt" else ["tracked"])
        for case in cases:
            with self.subTest(case):
                root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\n',
                                    committed=case == "refusing", name=case)
                if case == "tracked":
                    self.git(root, "init", "-q")
                    self.git(root, "add", "cortex.toml")
                    self.git(root, "commit", "-q", "-m", "pin")
                git = {"PATH": os.path.dirname(GIT), **GIT_ENV} if case == "tracked" else {}
                proc = self.cortex(root, "sync", **git)
                self.assert_ok(proc)
                self.assertIn(f"note: {TEAM} is not read: the team's version is 1.7.0, before 1.8.0", proc.err)
                local = self.local(root)
                self.assertNotIn("synced", local)
                self.assertEqual(Path(local["spec"]).name, "1.7.0")
                self.assertEqual(Path(local["spec"]).parent.name, "versions")

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
        self.assertIn(f"warning: theme is locked by {TEAM} to \"h2g2\": cortex.local.toml's \"none\" is not read",
                      proc.err)
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
    @TEAM_RELEASE
    @harness.outside_a_repository
    def test_team_without_a_team_s_file_is_refused_and_adds_a_version_to_one_without(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        proc = self.cortex(root, "upgrade", "--team")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"no {TEAM} — the team creates it, by a commit in its repository", proc.err)
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False, name="b")
        proc = self.cortex(root, "upgrade", "--team", *((OWN,) if DEV_OWN else ()))
        self.assert_ok(proc)
        self.assertEqual(tomllib.loads((root / TEAM).read_text(encoding="utf-8"))["version"], OWN)
        self.assertIn(f'version = "{OWN}"', (root / "agents" / "cortex.team.lock").read_text(encoding="utf-8"))
        self.assertIn(f"{TEAM}: Cortex {OWN}, a version added", proc.out)
        # In no git work tree, the team's folder too: nothing commits them (round 3 of the 1.8.0 stack's review).
        self.assertIn("Written: agents/cortex.team.toml and agents/cortex.team.lock — in no git work tree, so committed "
                      "nowhere.", proc.out)
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
        # A try is read above the team's version only: below it, none is named (ADR-013 §3.11). --team is named for a
        # release this cortex serves: a build's own version, 1.8.0-rc.1 or -dev.N, is below 9.9.8.
        if Version("9.9.8") <= Version(OWN):
            self.assertIn(f"this project follows {TEAM}, which pins Cortex {OWN} — moving it is the team's: "
                          "`cortex upgrade --team 9.9.8`. Nothing was changed", proc.err)
        else:
            self.assertIn(f"this project follows {TEAM}, which pins Cortex {OWN} — moving it is the team's, not to "
                          f"9.9.8, newer than this cortex, {OWN}: `cortex self-update 9.9.8` first. Nothing was changed",
                          proc.err)
        self.assertNotIn("--local", proc.err)

    @harness.outside_a_repository
    def test_at_a_2b_root_a_move_is_refused_as_all_skips_the_project(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', committed=False)
        before = (root / "cortex.toml").read_bytes()
        proc = self.cortex(root, "upgrade", OWN)
        self.assertEqual(proc.returncode, 1)
        if not DEV_OWN:                                  # --team writes no build of no release's version
            self.assertIn("`cortex upgrade --team", proc.err)
        if Version(OWN) > Version("9.9.8"):              # a try is read above the team's version only
            self.assertIn("`cortex upgrade --local", proc.err)
        else:
            self.assertNotIn("--local", proc.err)
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
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", OWN)       # no lock yet: one to write, the overlays checked
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)
        self.assertFalse((root / "agents" / "cortex.team.lock").exists())
        self.assert_ok(self.cortex(root, "upgrade", "--team", OWN, "--force"))


class AllTeamTests(TeamTestCase):
    @TEAM_RELEASE
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
        (root / "cortex.toml").write_text(f'version = "{TEAM_PIN}"\ntheme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(root, "config", "version")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(TEAM, proc.err)

    def test_a_version_newer_than_this_cortex_names_what_moves_it(self):
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n')
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"this project reads Cortex 99.0.0, which its {TEAM} pins, newer than this cortex", proc.err)
        if TEAM_MOVES:
            self.assertIn("`cortex upgrade --team VERSION` moves the team's pin", proc.err)
        else:                   # no release this cortex serves reads the team's file
            self.assertIn("Update this cortex to serve it:\n    cortex self-update 99.0.0", proc.err.replace("\r\n", "\n"))
            self.assertNotIn("--team", proc.err)

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

    def test_team_refuses_a_move_that_still_reads_a_try_too_new(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{TEAM_PIN}"\n', local='version = "99.0.0"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", TEAM_PIN)
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
        self.assertIn("`cortex upgrade 9.9.8`, which a try leaves to it, writes it", proc.err)


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

    @TEAM_RELEASE
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
                self.assertIn(f"The overlays of {harness_path(self.tmp / failing)}, checked against Cortex {OWN}:", proc.out)

    def test_a_pin_newer_than_this_cortex_is_refused_before_any_overlay_is_checked(self):
        team, _ = self.shared("beta")
        if upgrade.check_overlays(str(self.tmp / "beta"), Path(harness.REPO))[0] == 0:
            self.skipTest("the validator reads no service overlay here")
        (team / "cortex.team.toml").write_text('version = "99.0.0"\n', encoding="utf-8")
        before = (team / "cortex.team.toml").read_bytes()
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertEqual((team / "cortex.team.toml").read_bytes(), before)
        self.assertNotIn("The overlays of", proc.out)
        # Once and in the summary; a development build refuses --all --team once, before any project.
        self.assertEqual((proc.out + proc.err).count(newer_refused("99.0.0")), 1 if DEV_OWN else 3)

    @TEAM_RELEASE
    def test_the_shared_file_moves_once_and_each_follower_resumes(self):
        team, roots = self.shared(None)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
        self.assert_ok(proc)
        self.assertEqual(tomllib.loads((team / "cortex.team.toml").read_text(encoding="utf-8"))["version"], OWN)
        self.assertEqual(proc.out.count(f"{TEAM}: Cortex 9.9.8 → {OWN}"), 1)
        for root in roots:
            self.assertEqual(self.local(root)["synced"]["reads"], OWN)


class SecondReviewTests(TeamTestCase):
    """What the second counter-review of phase 2a found."""

    def registered(self, *roots):
        (self.home / "projects.toml").write_text("format = 1\n" + "".join(
            f'\n[[project]]\nname = "{root.name}"\npath = "{harness_path(root)}"\n' for root in roots), encoding="utf-8")

    def test_all_team_never_moves_a_pin_newer_than_this_cortex_down(self):
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n')
        self.registered(root)
        before = (root / TEAM).read_bytes()
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(newer_refused("99.0.0"), proc.out + proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)

    @SOURCE
    def test_a_development_build_skips_projects_without_a_team_s_file(self):
        self.registered(self.project(f'version = "{OWN}"\ntheme = "h2g2"\n'))
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", CORTEX_SOURCE_VERSION=DEV_SOURCE)
        self.assert_ok(proc)
        self.assertIn("skipped: no team's file to move", proc.out)

    @SOURCE
    def test_a_development_build_names_the_version_for_the_team_s_pin(self):
        # Its own version is the one its branch prepares, which no teammate can download (ADR-009 §9).
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.registered(root)
        before = (root / TEAM).read_bytes()
        for args in (("upgrade", "--team"), ("upgrade", "--all", "--team")):
            with self.subTest(args=args):
                proc = self.cortex(self.tmp if "--all" in args else root, *args, CORTEX_SOURCE_VERSION=DEV_SOURCE)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"this cortex is a source checkout, {DEV_SOURCE}, the version its branch prepares, which no "
                              f"teammate can download — name the version to move to: `cortex {' '.join(args)} VERSION`. "
                              "Nothing was changed", proc.err)
                self.assertNotIn("in each registered project", proc.out)
                self.assertEqual((root / TEAM).read_bytes(), before)

    @SOURCE
    @DOWNLOADS
    def test_a_development_build_that_prepares_1_8_0_names_no_team_version_it_cannot_move_to(self):
        # A build that prepares 1.8.0 serves no release that reads the team's file — each is newer: `--team VERSION`
        # alone is refused for every one; a cortex of that release moves the pin.
        dev = {"CORTEX_SOURCE_VERSION": "1.8.0-dev.5"}
        root = self.project('theme = "h2g2"\n', team='version = "1.8.0-dev.4"\n')       # another build's pin
        self.registered(root)
        before = (root / TEAM).read_bytes()
        for args in (("upgrade", "--team"), ("upgrade", "--all", "--team")):
            with self.subTest(args=args):
                proc = self.cortex(self.tmp if "--all" in args else root, *args, **dev)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("every release that reads the team's file is newer than it — update this cortex to one "
                              f"first: `cortex self-update VERSION`, then `cortex {' '.join(args)} VERSION`. Nothing "
                              "was changed", proc.err)
                self.assertNotIn("name the version to move to", proc.err)
        for version in ("1.7.0", "1.8.0", "1.8.0-rc.1"):          # no VERSION this cortex moves the pin to
            with self.subTest(version=version):
                followed = self.cortex(root, "upgrade", "--team", version, **dev)
                self.assertEqual(followed.returncode, 1, followed.out + followed.err)
        self.assertEqual((root / TEAM).read_bytes(), before)

    @SOURCE
    def test_all_from_a_development_build_is_refused_for_a_move_up_only(self):
        # Refused once when a project would move up to the build's version unnamed: one pinned below it,
        # that follows no team's file. A pin newer than the build, or a follower, moves none.
        newer = self.project('version = "99.0.0"\ntheme = "h2g2"\n', name="a-newer")
        follower = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name="b-follower")
        for roots in ((newer,), (follower,), (newer, follower)):
            with self.subTest(roots=[root.name for root in roots]):
                self.registered(*roots)
                proc = self.cortex(self.tmp, "upgrade", "--all", "--dry-run", CORTEX_SOURCE_VERSION=DEV_SOURCE)
                self.assertNotIn("name the version to move to", proc.err)
                self.assertIn("in each registered project", proc.out)
        mover = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name="c-mover")
        self.registered(newer, follower, mover)                             # the one that moves, last
        proc = self.cortex(self.tmp, "upgrade", "--all", "--dry-run", CORTEX_SOURCE_VERSION=DEV_SOURCE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("name the version to move to: `cortex upgrade --all VERSION`", proc.err)
        self.assertNotIn("in each registered project", proc.out)

    @SOURCE
    def test_all_team_from_a_development_build_reads_every_project(self):
        alone = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', name="a-alone")       # no team's file
        follower = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name="b-follower")
        self.registered(alone, follower)                                    # the follower, last
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", "--dry-run", CORTEX_SOURCE_VERSION=DEV_SOURCE)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("name the version to move to: `cortex upgrade --all --team VERSION`", proc.err)

    @SOURCE
    def test_a_development_build_s_own_pin_is_no_move(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{DEV_SOURCE}"\n')
        self.registered(root)
        for args in (("upgrade", "--team", "--dry-run"), ("upgrade", "--all", "--team", "--dry-run")):
            with self.subTest(args=args):
                proc = self.cortex(self.tmp if "--all" in args else root, *args, CORTEX_SOURCE_VERSION=DEV_SOURCE)
                self.assert_ok(proc)
                self.assertNotIn("name the version to move to", proc.err)

    @SOURCE
    def test_a_team_s_pin_of_no_release_is_moved_by_team(self):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.10-dev.3"\n')
        before = (root / TEAM).read_bytes()
        for args in (("upgrade", "--team"), ("sync",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"Cortex 9.9.10-dev.3, a build of no release, newer than this cortex, {OWN}"
                              if args == ("upgrade", "--team") else "pins — a build of no release", proc.err)
                self.assertIn("cortex upgrade --team VERSION", proc.err)
                self.assertNotIn("self-update", proc.err)
                self.assertEqual((root / TEAM).read_bytes(), before)

    def test_another_fault_of_a_version_less_cortex_toml_is_said_as_it_is(self):
        for text, said in (('theme = "h2g2"\nbogus = 1\n', 'cortex.toml: unknown key "bogus"'),
                           ('Version = "1.8.0"\ntheme = "h2g2"\n', 'cortex.toml: unknown key "Version"'),
                           ('theme = 1\n', 'cortex.toml: "version" is required')):     # as 1.7.0 says it
            with self.subTest(text=text):
                root = self.project(text, name=f"t{abs(hash(text))}")
                proc = self.cortex(root, "sync")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(said, proc.err)
                self.assertNotIn("git submodule update --init", proc.err)

    def test_a_version_less_cortex_toml_names_its_missing_team_s_folder(self):
        root = self.project('theme = "h2g2"\n')
        for state in ("is not there", "is empty"):
            with self.subTest(state=state):
                if state == "is empty":
                    (root / "agents").mkdir()
                proc = self.cortex(root, "sync")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f'"version" is required, or in {TEAM} — agents/ {state}: check it out, a submodule with '
                              "`git submodule update --init`", proc.err)

    @SOURCE
    @DOWNLOADS
    def test_init_during_a_try_writes_the_bootstrap_from_the_team_s_version(self):
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": NEW, **{
            f"templates/{name}": (harness.REPO / "templates" / name).read_bytes()
            for name in ("project-overview.md.template", "project-context.md.template")}})
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        (root / "AGENTS.md").write_bytes(init.stamped(init.instructions(NEW, True)))
        proc = self.cortex(root, "init", "--tool", "claude", "--force")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(NEW, True)))
        self.assertIn("✓ cortex.toml kept, at Cortex 9.9.8", proc.out)
        self.assertIn(f"Cortex {OWN} is read — cortex.local.toml's, above the team's 9.9.8", proc.err)
        self.assertNotIn("AGENTS.md is there and was kept", proc.err)

    def failing_overlay(self, team):
        root = self.project('theme = "h2g2"\n', team=team)
        overlay = root / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: workspace\n"
                           "-->\n\n# x\n", encoding="utf-8")
        return root

    def test_a_team_dry_run_exits_as_the_run_would(self):
        root = self.failing_overlay(f'version = "{OWN}"\n')
        self.assertEqual(self.cortex(root, "upgrade", "--team", OWN, "--dry-run").returncode, 1)

    @SOURCE
    def test_a_local_dry_run_exits_as_the_run_would(self):
        root = self.failing_overlay('version = "1.8.0"\n')
        self.assertEqual(self.cortex(root, "upgrade", "--local", OWN, "--dry-run").returncode, 1)
        self.assertFalse((root / "cortex.local.toml").exists())

    def test_the_team_s_lock_is_named_in_a_refusal(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--locked: no agents/cortex.team.lock", proc.err)

    def test_a_team_s_lock_that_is_no_lock_is_named(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        lock = root / "agents" / "cortex.team.lock"
        lock.write_text(lock.read_text(encoding="utf-8").replace("sha256:", "sha256:0", 1), encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn('agents/cortex.team.lock: digest "sha256:0', proc.err)
        lock.write_text("<<<<<<< HEAD\n" + lock.read_text(encoding="utf-8") + "=======\n>>>>>>> other\n", encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex sync: agents/cortex.team.lock: ", proc.err)

    def test_validate_names_the_team_s_lock_when_its_spec_left_the_store(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            found = validate.lock_warnings(config.load(str(root)), str(self.tmp / "gone"))
        self.assertEqual([(name, code) for name, code, _ in found], [("agents/cortex.team.lock", "LOCK_DIGEST")])

    def test_team_at_the_version_pinned_never_locks_another_spec(self):
        for text in ('theme = "h2g2"\n', f'version = "{OWN}"\ntheme = "h2g2"\n'):     # follows, pins its own
            with self.subTest(text=text):
                root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n', name=f"t{len(text)}")
                self.assert_ok(self.cortex(root, "sync"))
                (root / "cortex.toml").write_text(text, encoding="utf-8")
                lock = root / "agents" / "cortex.team.lock"
                lock.write_text(re.sub(r'(digest = "sha256:)[0-9a-f]{8}', r"\g<1>00000000", lock.read_text(encoding="utf-8")),
                                encoding="utf-8")
                before = lock.read_bytes()
                for dry in ((), ("--dry-run",)):
                    proc = self.cortex(root, "upgrade", "--team", OWN, *dry)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn("this is not the release the project locked", proc.err)
                    self.assertEqual(lock.read_bytes(), before)

    def test_team_at_the_version_pinned_and_locked_moves_nothing(self):
        root = self.failing_overlay(f'version = "{OWN}"\n')        # what moves nothing, its overlays stop not
        self.assert_ok(self.cortex(root, "sync"))
        files = [root / TEAM, root / "agents" / "cortex.team.lock"]
        before = [path.read_bytes() for path in files]
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"{TEAM}: Cortex {OWN}, pinned and locked already — nothing to move", proc.out)
        self.assertNotIn("→", proc.out)
        self.assertNotIn("Commit", proc.out)
        self.assertEqual([path.read_bytes() for path in files], before)

    def test_all_team_at_the_pin_checks_no_follower_s_overlays(self):
        root = self.failing_overlay(f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        self.registered(root)
        for args in ((), ("--dry-run",)):
            with self.subTest(args=args):
                proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, *args)
                self.assert_ok(proc)
                self.assertNotIn("The overlays of", proc.out)
                self.assertIn(f"already on {OWN}", proc.out)

    @NEEDS_GIT
    @COMMITS_RELEASE
    def test_team_at_the_version_pinned_and_locked_makes_no_branch(self):
        env = {"PATH": os.path.dirname(GIT), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync", **env))
        for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "x")):
            subprocess.run([GIT, "-C", str(root / "agents"), *args], check=True, capture_output=True,
                           env={**os.environ, **env})
        proc = self.cortex(root, "upgrade", "--team", OWN, "--branch", "move", "--commit", "--message", "m", **env)
        self.assert_ok(proc)
        branches = subprocess.run([GIT, "-C", str(root / "agents"), "branch", "--list", "move"], capture_output=True,
                                  text=True, env={**os.environ, **env}).stdout
        self.assertEqual(branches.strip(), "")
        self.assertNotIn("Committed", proc.out)
        self.assertIn("git: no branch move is made — nothing was written", proc.out)

    def team_repository(self):
        """A follower whose team's folder its work tree holds, pinned at OWN and locked, all committed."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n', committed=False)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync", **env))
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        return root, env

    @NEEDS_GIT
    @COMMITS_RELEASE
    def test_team_commit_at_the_pin_commits_what_a_run_before_left(self):
        root, env = self.team_repository()
        lock = root / "agents" / "cortex.team.lock"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")       # as a run before left it
        hook = root / ".git" / "hooks" / "pre-commit"
        for refused in (True, False):
            with self.subTest(refused=refused):
                if refused:
                    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                    hook.chmod(0o755)
                else:
                    hook.unlink()
                proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "relock", **env)
                self.assertEqual(proc.returncode, 1 if refused else 0, proc.out + proc.err)
                self.assertIn(f"{TEAM}: Cortex {OWN}, pinned and locked already — nothing to move", proc.out)
        self.assertIn("Committed in agents/", proc.out)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "relock")
        self.assertEqual(self.git(root, "status", "--porcelain").stdout.strip(), "")

    @NEEDS_GIT
    @COMMITS_RELEASE
    def test_team_at_the_pin_says_what_a_run_before_left_uncommitted(self):
        root, env = self.team_repository()
        lock = root / "agents" / "cortex.team.lock"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--team", OWN, "--branch", "relock", **env)
        self.assert_ok(proc)
        self.assertIn("git: no branch relock is made — nothing was written", proc.out)
        self.assertIn("note: nothing was written in agents/, and agents/cortex.team.lock holds changes git has not "
                      "committed", proc.err)
        self.registered(root)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--branch", "relock", **env)
        self.assert_ok(proc)
        self.assertIn("nothing written — agents/cortex.team.lock not committed, from before", proc.out)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--commit", "--message", "relock", **env)
        self.assert_ok(proc)
        self.assertNotIn("Nothing to commit, in any project", proc.out)
        self.assertEqual(self.git(root, "status", "--porcelain").stdout.strip(), "")

    def test_team_never_moves_a_newer_pin_down_from_a_project_that_pins_its_own(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='version = "99.0.0"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(newer_refused("99.0.0"), proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)

    def test_team_at_the_version_pinned_writes_a_missing_lock(self):
        for text in ('theme = "h2g2"\n', f'version = "{OWN}"\ntheme = "h2g2"\n'):     # follows, pins its own
            with self.subTest(text=text):
                root = self.project(text, team=f'version = "{OWN}"\n', name=f"t{len(text)}")
                proc = self.cortex(root, "upgrade", "--team", OWN)
                self.assert_ok(proc)
                self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), OWN)

    @NEEDS_GIT
    @COMMITS_RELEASE
    def test_a_resume_commits_nothing_of_the_team_s_own_repository(self):
        env = {"PATH": os.path.dirname(GIT), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n', committed=False)
        for where in (root / "agents", root):
            for args in (("init", "-q", str(where)), ("-C", str(where), "add", "-A"),
                         ("-C", str(where), "commit", "-q", "-m", "x")):
                subprocess.run([GIT, *args], check=True, capture_output=True, env={**os.environ, **env})
        proc = self.cortex(root, "upgrade", "--commit", "--message", "m", **env)
        self.assert_ok(proc)
        self.assertIn("agents/cortex.team.lock", proc.err)
        self.assertIn("not committed with it: commit it where it is", proc.err)
        self.assertNotIn("as committed already", proc.out + proc.err)

    @NEEDS_GIT
    def test_git_says_which_files_of_the_team_s_folder_another_repository_holds(self):
        root = self.tmp / "ws"
        names = ("agents/cortex.team.toml", "agents/cortex.team.lock", "agents/roles/x.md",
                 ".github/copilot-instructions.md", "cortex.lock")
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("x\n", encoding="utf-8")
        for where in (root, root / "agents", root / ".github"):        # the team's folder, and .github, their own
            self.git(where, "init", "-q")
        top = str(root)
        with mock.patch.dict(os.environ, GIT_ENV):
            self.assertTrue(upgrade._other_tree(top, top, root / "agents" / "cortex.team.lock"))
            self.assertTrue(upgrade._other_tree(top, top, root / "agents" / "cortex.team.toml"))
            # Any other file is the commit's to take or refuse, as in 1.7.0 — a nested repository's included.
            self.assertFalse(upgrade._other_tree(top, top, root / "agents" / "roles" / "x.md"))
            self.assertFalse(upgrade._other_tree(top, top, root / ".github" / "copilot-instructions.md"))
            self.assertFalse(upgrade._other_tree(top, top, root / "cortex.lock"))
            # A .git that git does not take — an empty directory, a gitfile to nowhere — makes no work tree: the
            # outer one has the file, committed as it is, or sees it, a lock not committed yet.
            shutil.rmtree(root / "agents" / ".git")
            self.git(root, "add", "--", "agents/cortex.team.toml")
            self.git(root, "commit", "-q", "-m", "team")
            for stray in ("directory", "gitfile"):
                with self.subTest(stray=stray):
                    shutil.rmtree(root / "agents" / ".git", ignore_errors=True)
                    (root / "agents" / ".git").unlink(missing_ok=True)
                    if stray == "directory":
                        (root / "agents" / ".git").mkdir()
                    else:
                        (root / "agents" / ".git").write_text("gitdir: ../.git/modules/gone\n", encoding="utf-8")
                    self.assertFalse(upgrade._other_tree(top, top, root / "agents" / "cortex.team.lock"))
                    self.assertFalse(upgrade._other_tree(top, top, root / "agents" / "cortex.team.toml"))

    @NEEDS_GIT
    def test_a_submodule_whose_repository_is_gone_holds_the_team_s_lock(self):
        team, root = self.tmp / "team", self.tmp / "ws"
        for where in (team, root):
            where.mkdir()
            self.git(where, "init", "-q")
        (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        self.git(team, "add", "-A")
        self.git(team, "commit", "-q", "-m", "t")
        self.git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(team), "agents")
        self.git(root, "commit", "-q", "-m", "sub")
        (root / "agents" / "cortex.team.lock").write_text("x\n", encoding="utf-8")
        shutil.rmtree(root / ".git" / "modules" / "agents")        # git cannot tell: the outer tree neither has nor sees it
        with mock.patch.dict(os.environ, GIT_ENV):
            self.assertTrue(upgrade._other_tree(str(root), str(root), root / "agents" / "cortex.team.lock"))

    @harness.outside_a_repository
    def test_an_unreadable_project_s_pinned_is_its_team_s_version(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\nlater = "x"\n', team='version = "9.9.8"\n',
                            committed=False)
        self.registered(root)
        proc = self.cortex(self.tmp, "project", "list", "--json")
        found = json.loads(proc.out)["projects"][0]
        self.assertEqual((found["state"], found["pinned"], found["pinned_origin"]), ("unreadable", "9.9.8", TEAM))

    def test_every_form_of_synced_is_written_again_in_its_line(self):
        for text in (f'[synced]\npinned = "{OWN}"\n# mine\nreads = "{OWN}"\n',
                     f'synced.pinned = "{OWN}"\nsynced.reads = "{OWN}"\n'):
            with self.subTest(text=text):
                root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local=text, name=f"t{abs(hash(text))}")
                self.assert_ok(self.cortex(root, "sync"))
                written = (root / "cortex.local.toml").read_text(encoding="utf-8")
                self.assertIn("synced = {", written)
                self.assertNotIn("[synced]", written)
                self.assertNotIn("synced.", written)
                self.assertEqual("# mine" in written, "# mine" in text)
                self.assert_ok(self.cortex(root, "validate"))
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local='synced = "garbage"\n', name="garbage")
        proc = self.cortex(root, "validate")
        self.assertIn("— `cortex sync` writes it again", proc.err)

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_upgrade_during_a_try_says_commit_only_where_a_repository_is(self):
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n', committed=False)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertIn("cortex.lock: Cortex 9.9.8\n", proc.out)

    @harness.outside_a_repository
    def test_a_try_at_or_below_the_team_s_in_cortex_toml_says_its_remedy_once(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team=f'version = "{OWN}"\n', committed=False)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"cortex.toml's version, {OWN}, is at or below the team's {OWN} ({TEAM}) and changes nothing — "
                      "remove `version` from cortex.toml", proc.err)

    @DOWNLOADS
    def test_team_checks_a_bootstrap_against_the_team_s_version(self):
        self.release.replace_spec("9.9.7", {"templates/bootstrap-instructions.md": OLD})
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": NEW})
        root = self.project('theme = "h2g2"\n', team='version = "9.9.7"\n', local=f'version = "{OWN}"\n')
        (root / "CLAUDE.md").write_bytes(init.instructions(OLD, True))
        proc = self.cortex(root, "upgrade", "--team", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(NEW, True)))

    def test_a_try_too_new_names_no_team_without_a_team_s_file(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', local='version = "99.0.0"\n')
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("`cortex upgrade --local VERSION` moves the try — to the team's version or below, it drops it",
                      proc.err)
        self.assertNotIn("--team", proc.err)
        proc = self.cortex(root, "upgrade", "--local", OWN)       # named, and followed: the try dropped
        self.assert_ok(proc)
        self.assertNotIn("version", self.local(root) if (root / "cortex.local.toml").exists() else {})


class CodeReviewTests(TeamTestCase):
    """What the counter-review of the 1.8.0 stack's code found (2026-11-09)."""

    def registered(self, *roots):
        (self.home / "projects.toml").write_text("format = 1\n" + "".join(
            f'\n[[project]]\nname = "{root.name}"\npath = "{harness_path(root)}"\n' for root in roots), encoding="utf-8")

    @SOURCE
    @DOWNLOADS
    def test_a_resume_during_a_try_writes_the_lock_locked_names(self):
        # ADR-013 §3.4: the sync reads the try and leaves the lock of the team's version; --locked names the upgrade
        # that writes it, and that upgrade, a resume, writes it — the team's lock too.
        for follows in (False, True):
            with self.subTest(follows=follows):
                if follows:
                    root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=f'version = "{OWN}"\n',
                                        name="follows")
                    lock, name, commit = root / "agents" / "cortex.team.lock", "agents/cortex.team.lock", " in agents/"
                else:
                    root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n', name="pins")
                    lock, name, commit = root / "cortex.lock", "cortex.lock", ""
                lock.write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64), encoding="utf-8")
                self.assert_ok(self.cortex(root, "sync"))
                proc = self.cortex(root, "sync", "--locked")
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"--locked: {name} names Cortex 9.9.7", proc.err)
                self.assertIn("`cortex upgrade 9.9.8`, which a try leaves to it, writes it", proc.err)
                before = lock.read_bytes()
                proc = self.cortex(root, "upgrade", "9.9.8", "--dry-run")
                self.assert_ok(proc)
                self.assertIn(f"{name} names Cortex 9.9.7, and ", proc.out)
                self.assertIn("it would be written", proc.out)
                self.assertEqual(lock.read_bytes(), before)
                proc = self.cortex(root, "upgrade", "9.9.8")
                self.assert_ok(proc)
                self.assertIn(f"{name}: Cortex 9.9.8, where it named 9.9.7 — commit it{commit}\n", proc.out)
                self.assertEqual(str(config.load_lock(str(lock.parent), lock.name).version), "9.9.8")
                self.assert_ok(self.cortex(root, "sync", "--locked"))
                self.assertEqual(self.local(root)["synced"]["reads"], "9.9.8")

    def team_repository(self, version="1.8.0"):
        """A follower whose work tree holds its team's folder, pinned at ``version``, all committed."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team=f'version = "{version}"\n', committed=False)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        return root, env

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_never_commits_the_developer_s_own_change_of_the_team_s_file(self):
        root, env = self.team_repository()
        team = root / TEAM
        team.write_text(team.read_text(encoding="utf-8") + 'theme = "mine"\n', encoding="utf-8")     # theirs, not committed
        for run in ("a move", "at the pin"):
            with self.subTest(run=run):
                proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
                self.assert_ok(proc)
                self.assertIn(f"note: nothing is committed in agents/ — {TEAM} holds changes of yours besides its "
                              "version — commit by hand what is the team's", proc.err)
                self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "init")
        self.assertIn('theme = "mine"', team.read_text(encoding="utf-8"))
        # Without the developer's own change, what --team writes is committed — the version alone.
        self.git(root, "checkout", "-q", "--", TEAM)
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn("Committed in agents/", proc.out)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "move")

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_all_team_commit_says_what_it_committed_and_what_it_left(self):
        # The team's files are committed in the team's folder; a follower's bootstrap written again is the project's.
        marked = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes().replace(
            b"# Cortex AI Team", b"# Cortex AI Team, as 9.9.8 wrote it", 1)
        team = self.tmp / "team"
        team.mkdir()
        (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        self.git(team, "init", "-q")
        self.git(team, "add", "-A")
        self.git(team, "commit", "-q", "-m", "init")
        roots = []
        for name in ("alpha", "beta"):
            root = self.project('theme = "h2g2"\n', name=name)
            (root / "agents").symlink_to(team, target_is_directory=True)
            (root / "CLAUDE.md").write_bytes(init.stamped(init.instructions(marked, True)))  # nobody's change: its marker
            roots.append(root)
        self.registered(*roots)
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertEqual(self.git(team, "log", "-1", "--format=%s").stdout.strip(), "move")
        self.assertRegex(proc.out, rf"alpha  {re.escape(harness_path(roots[0]))}: agents/cortex.team.toml and "
                                   r"agents/cortex.team.lock — committed on \S+, [0-9a-f]+; CLAUDE.md — not committed: "
                                   r"--team commits in the team's folder only\n")
        self.assertIn(f"beta   {harness_path(roots[1])}: CLAUDE.md — not committed: --team commits in the team's folder "
                      "only\n", proc.out)
        self.assertNotIn("None", proc.out)


    @NEEDS_GIT
    def test_an_empty_default_agents_submodule_warns_in_a_subdirectory_of_its_repository(self):
        # The usual CI checkout, without its submodules: .gitmodules is at the repository's top, git's index holds
        # the submodule — and validate --strict fails.
        top = self.tmp / "repo"
        root = top / "svc"
        (root / "agents").mkdir(parents=True)
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        (top / ".gitmodules").write_text('[submodule "team"]\n\tpath = svc/agents\n\turl = ../team\n', encoding="utf-8")
        self.git(top, "init", "-q")
        self.git(top, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},svc/agents")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        said = "warning: agents/ is empty — a submodule not checked out: `git submodule update --init`"
        proc = self.cortex(root, "sync", **env)
        self.assert_ok(proc)
        self.assertIn(said, proc.err)
        proc = self.cortex(root, "validate", "--strict", **env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TEAM_FOLDER_EMPTY", proc.out + proc.err)
        # A project kept before 1.8.0 reads nothing of its team's folder: nothing is said of it.
        old = self.project('version = "1.7.0"\ntheme = "h2g2"\n', name="old")
        (old / "agents").mkdir()
        (old / ".gitmodules").write_text('[submodule "agents"]\n\tpath = agents\n\turl = ../agents\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            self.assertIsNone(sync._team_folder_note(config.load(str(old))))


    @SOURCE
    @DOWNLOADS
    def test_a_follower_upgraded_to_the_version_it_reads_resumes(self):
        # Decision 22: with the team's version, or the version read — a try's — a follower's upgrade resumes.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        for wanted in (OWN, "9.9.8"):
            with self.subTest(wanted=wanted):
                proc = self.cortex(root, "upgrade", wanted)
                self.assert_ok(proc)
                self.assertIn("Cortex 9.9.8: already on it — nothing to move", proc.out)

    def test_a_flag_that_agrees_with_a_lock_is_taken(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team='sync = "store"\nclaude_access = false\n'
                            'locked = ["sync", "claude_access"]\n')
        proc = self.cortex(root, "sync", "--store", "--no-claude-access")
        self.assert_ok(proc)
        self.assertNotIn("locked", proc.err)

    @DOWNLOADS
    def test_validate_refuses_a_try_edited_and_not_synced(self):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        self.assert_ok(self.cortex(root, "validate"))
        local = root / "cortex.local.toml"
        local.write_text(local.read_text(encoding="utf-8").replace(f'version = "{OWN}"', 'version = "9.9.8"'),
                         encoding="utf-8")
        proc = self.cortex(root, "validate")
        self.assertEqual(proc.returncode, 2, proc.out + proc.err)
        self.assertIn(f"cortex.local.toml pins Cortex 9.9.8, and `cortex sync` recorded {OWN} — run `cortex sync`",
                      proc.err)

    @SOURCE
    def test_all_team_checks_the_followers_of_the_folder_it_moves_only(self):
        alpha = self.project('theme = "h2g2"\n', team='version = "1.8.0"\n', name="alpha")
        beta = self.project('theme = "h2g2"\n', team='version = "1.8.0"\n', name="beta")
        overlay = beta / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: workspace\n"
                           "-->\n\n# x\n", encoding="utf-8")
        self.registered(alpha, beta)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)           # beta's overlays stop beta's folder
        self.assertEqual(tomllib.loads((alpha / TEAM).read_text(encoding="utf-8"))["version"], OWN)
        self.assertEqual(tomllib.loads((beta / TEAM).read_text(encoding="utf-8"))["version"], "1.8.0")
        self.assertNotIn(f"The overlays of {harness_path(alpha)}", proc.out)

    @harness.outside_a_repository
    def test_at_a_2b_root_a_team_s_pin_too_new_is_said_as_the_team_s(self):
        # ADR-013 §3.1: at a 2.B root, cortex.toml's version is the developer's, and the team's file's is the team's.
        root = self.project('version = "1.8.0"\ntheme = "h2g2"\n', team='version = "99.0.0"\n', committed=False)
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"this project uses Cortex 99.0.0, which its {TEAM} pins, newer than this cortex", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_local_at_a_2b_root_removes_no_try_cortex_toml_holds(self):
        # cortex.toml's version is the developer's at a 2.B root, but no try of cortex.local.toml's: nothing to remove.
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', committed=False)
        proc = self.cortex(root, "upgrade", "--local", "9.9.8")
        self.assert_ok(proc)
        self.assertIn("Cortex 9.9.8 is at or below the team's 9.9.8 (agents/cortex.team.toml): there is no try to remove "
                      "— nothing to move", proc.out)
        self.assertFalse((root / "cortex.local.toml").exists())


    @SOURCE
    @DOWNLOADS
    def test_locked_says_the_try_is_not_read(self):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        proc = self.cortex(root, "sync", "--locked")
        self.assertIn(f"note: cortex.local.toml's version, {OWN}, is not read: --locked checks the team's 9.9.8 ({TEAM})",
                      proc.err)
        self.assertNotIn(f"Cortex {OWN} is read", proc.err)

    @DOWNLOADS
    def test_a_resume_says_each_note_once(self):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local='version = "9.9.8"\n')
        proc = self.cortex(root, "upgrade")              # not synced yet: the resume syncs
        self.assert_ok(proc)
        self.assertIn("syncing", proc.out)
        self.assertEqual(proc.err.count("cortex.local.toml's version, 9.9.8, is at or below the team's"), 1, proc.err)

    def test_team_at_the_pin_without_its_lock_writes_the_lock_alone(self):
        root = self.project('theme = "h2g2"\n', team=f'# the team\'s\nversion = "{OWN}"\n')
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"{TEAM}: Cortex {OWN}, pinned already — its lock is written\n", proc.out)
        self.assertNotIn("→", proc.out)
        self.assertIn("Commit agents/cortex.team.lock in agents/" if COMMIT_IT else "Nothing to commit: ", proc.out)
        self.assertEqual((root / TEAM).read_bytes(), before)
        self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), OWN)

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")
    def test_all_team_dry_run_says_each_follower_would_follow(self):
        first = self.project('theme = "h2g2"\n', team='version = "1.8.0"\n', name="alpha")
        second = self.project('theme = "h2g2"\n', name="beta")
        (second / "agents").symlink_to(first / "agents", target_is_directory=True)
        self.registered(first, second)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"  alpha  would move 1.8.0 → {OWN}\n", proc.out)
        self.assertIn(f"  beta   would move 1.8.0 → {OWN}\n", proc.out)
        self.assertIn(f"alpha: agents/cortex.team.toml and agents/cortex.team.lock\n", proc.out)
        self.assertEqual(tomllib.loads((first / TEAM).read_text(encoding="utf-8"))["version"], "1.8.0")

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_in_a_submodule_names_the_superproject(self):
        team, root = self.tmp / "team", self.tmp / "ws"
        for where in (team, root):
            where.mkdir()
            self.git(where, "init", "-q")
        (team / "cortex.team.toml").write_text('version = "1.8.0"\n', encoding="utf-8")
        self.git(team, "add", "-A")
        self.git(team, "commit", "-q", "-m", "t")
        (root / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        self.git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(team), "agents")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "ws")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: agents/ is a submodule of {harness_path(root)}, which now shows it changed — commit it "
                      "there too", proc.err)

    def test_validate_names_who_writes_a_team_s_lock_this_user_cannot(self):
        root = self.project('theme = "h2g2"\n', team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "agents" / "cortex.team.lock").unlink()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            project = config.load(str(root))
            with mock.patch.object(validate.os, "access", return_value=False):
                found = validate.lock_warnings(project, str(self.home / "versions" / OWN))
        self.assertEqual([code for _, code, _ in found], ["LOCK_MISSING"])
        self.assertIn("agents/ cannot be written here", found[0][2])
        self.assertNotIn("run `cortex sync`, then commit it", found[0][2])

    def test_team_on_a_project_before_1_8_0_names_the_move_first(self):
        # 1.8.0 — or, from a release candidate of it, its own version: 1.8.0 is not out yet, which `cortex upgrade
        # 1.8.0` would ask this cortex to self-update to. A build of no release names 1.8.0, which no teammate lacks.
        own = Version(OWN)
        first = OWN if own < Version("1.8.0") and own.core == (1, 8, 0) and not DEV_OWN else "1.8.0"
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False)
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"{TEAM} is not read: this project pins Cortex 1.7.0, before 1.8.0 — move it first to a version that "
                      f"reads it: cortex upgrade {first}. Nothing was changed", proc.err)
        if first == OWN:                    # the command named, served by this cortex: then --team moves the pin
            self.assert_ok(self.cortex(root, "upgrade", first))
            self.assert_ok(self.cortex(root, "upgrade", "--team", OWN))
            self.assertIn(f'version = "{OWN}"', (root / TEAM).read_text(encoding="utf-8"))

    @SOURCE
    def test_team_before_1_8_0_from_a_release_candidate_names_its_own_version(self):
        # From 1.8.0-rc.1, before 1.8.0 is out: `cortex upgrade 1.8.0` would ask for a self-update to a release not
        # published yet. The candidate reads the team's file, and is served: the move named is to it.
        rc = {"CORTEX_SOURCE_VERSION": "1.8.0-rc.1"}
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False)
        proc = self.cortex(root, "upgrade", "--team", "1.8.0-rc.1", **rc)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("move it first to a version that reads it: cortex upgrade 1.8.0-rc.1. Nothing was changed", proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "1.8.0-rc.1", **rc))          # the command named
        self.assert_ok(self.cortex(root, "upgrade", "--team", "1.8.0-rc.1", **rc))
        self.assertIn('version = "1.8.0-rc.1"', (root / TEAM).read_text(encoding="utf-8"))
        # A build of no release names 1.8.0: its own version no teammate can download.
        dev = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False,
                           name="dev")
        proc = self.cortex(dev, "upgrade", "--team", "1.8.0-dev.5", CORTEX_SOURCE_VERSION="1.8.0-dev.5")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("move it first to a version that reads it: cortex upgrade 1.8.0. Nothing was changed", proc.err)


class CodeReviewFixTests(TeamTestCase):
    """What the counter-review of the code review's fixes found (2026-11-09)."""

    def stale_own_lock(self, committed=False):
        """A project that pins its own 9.9.8, its committed cortex.lock naming 9.9.7, and a try above."""
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', committed=committed)
        (root / "cortex.lock").write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64),
                                          encoding="utf-8")
        return root

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_resume_during_a_try_writes_its_own_lock_on_the_branch_named(self):
        # The try's lock is the project's own cortex.lock: the resume makes the branch before it writes it.
        root = self.stale_own_lock()
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q", "-b", "main")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        (root / "cortex.local.toml").write_text(f'version = "{OWN}"\n', encoding="utf-8")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        self.assert_ok(self.cortex(root, "sync", **env))
        proc = self.cortex(root, "upgrade", "9.9.8", "--branch", "relock", "--commit", "--message", "relock", **env)
        self.assert_ok(proc)
        self.assertNotIn("no branch", proc.out)
        self.assertEqual(self.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "relock")
        self.assertEqual(self.git(root, "log", "-1", "--format=%s", "main").stdout.strip(), "init")
        self.assertEqual(self.git(root, "log", "-1", "--format=%s", "relock").stdout.strip(), "relock")
        self.assertEqual(str(config.load_lock(str(root), config.LOCK_FILE).version), "9.9.8")

    @SOURCE
    @DOWNLOADS
    def test_all_dry_run_lists_the_own_lock_a_try_leaves(self):
        root = self.stale_own_lock(committed=True)
        (root / "cortex.local.toml").write_text(f'version = "{OWN}"\n', encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        (self.home / "projects.toml").write_text(
            f'format = 1\n\n[[project]]\nname = "app"\npath = "{harness_path(root)}"\n', encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "9.9.8", "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"app  {harness_path(root)}: cortex.lock\n", proc.out)
        self.assertNotIn("Nothing to commit", proc.out)

    def stale_team_lock(self, local=f'version = "{OWN}"\n', name="app"):
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local=local, name=name)
        lock = root / "agents" / "cortex.team.lock"
        lock.write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64), encoding="utf-8")
        return root, lock

    @SOURCE
    @AS_ROOT
    def test_a_resume_during_a_try_warns_of_a_team_s_folder_it_cannot_write(self):
        # As the sync, on a developer's machine: a warning, the lock not updated, the rest done (ADR-013 §3.4).
        for local in (f'version = "{OWN}"\n', None):
            with self.subTest(trying=local is not None):
                root, lock = self.stale_team_lock(local, name="trying" if local else "plain")
                self.assert_ok(self.cortex(root, "sync"))
                lock.write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64), encoding="utf-8")
                before = lock.read_bytes()
                os.chmod(root / "agents", 0o555)
                self.addCleanup(os.chmod, root / "agents", 0o755)
                if local is not None:
                    proc = self.cortex(root, "upgrade", "--dry-run")
                    self.assert_ok(proc)
                    self.assertIn("agents/ cannot be written here — it would be a warning, the lock not updated",
                                  proc.out)
                proc = self.cortex(root, "upgrade")
                self.assert_ok(proc)
                self.assertIn("warning: agents/cortex.team.lock cannot be written — ", proc.err)
                self.assertIn("the team's lock is not updated here", proc.err)
                self.assertNotIn("agents/cortex.team.lock: ", proc.out)
                self.assertNotIn("commit it in agents/", proc.out)
                self.assertEqual(lock.read_bytes(), before)

    @SOURCE
    def test_a_resume_cut_short_says_no_sync_it_did_not_run(self):
        # The team's version cannot be downloaded: the resume fails as a whole, and no sync moved this machine.
        release = FakeRelease(("9.9.7",))
        self.addCleanup(release.close)
        root, lock = self.stale_team_lock()
        env = {"CORTEX_RELEASES_URL": release.url}
        self.assert_ok(self.cortex(root, "sync", **env))
        before = lock.read_bytes()
        proc = self.cortex(root, "upgrade", "--dry-run", **env)
        self.assert_ok(proc)
        self.assertIn("it would be written, once Cortex 9.9.8 is downloaded", proc.out)
        proc = self.cortex(root, "upgrade", **env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("note: agents/cortex.team.lock is as it was\n", proc.err)
        self.assertNotIn("`cortex sync` puts this machine back", proc.err)
        self.assertEqual(lock.read_bytes(), before)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_all_team_commit_says_committed_only_what_it_committed(self):
        # A bootstrap linked into the team's folder is the project's, written again and left uncommitted: in the
        # team's folder, it is not said committed with the team's files.
        marked = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes().replace(
            b"# Cortex AI Team", b"# Cortex AI Team, as 9.9.8 wrote it", 1)
        team = self.tmp / "team"
        team.mkdir()
        (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        (team / "CLAUDE.md").write_bytes(init.stamped(init.instructions(marked, True)))
        self.git(team, "init", "-q")
        self.git(team, "add", "-A")
        self.git(team, "commit", "-q", "-m", "init")
        root = self.project('theme = "h2g2"\n', name="alpha")
        (root / "agents").symlink_to(team, target_is_directory=True)
        (root / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
        (self.home / "projects.toml").write_text(
            f'format = 1\n\n[[project]]\nname = "alpha"\npath = "{harness_path(root)}"\n', encoding="utf-8")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertEqual(self.git(team, "status", "--short").stdout, " M CLAUDE.md\n")
        self.assertRegex(proc.out, rf"alpha  {re.escape(harness_path(root))}: agents/cortex.team.toml and "
                                   r"agents/cortex.team.lock — committed on \S+, [0-9a-f]+; CLAUDE.md — not committed: "
                                   r"--team commits in the team's folder only\n")

    def team_repository(self, content, commit=True, name="app"):
        """A follower whose work tree holds its team's file, ``content`` as bytes — committed, or not."""
        root = self.project('theme = "h2g2"\n', team="", committed=False, name=name)
        (root / TEAM).write_bytes(content)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", *(["-A"] if commit else ["cortex.toml", ".gitignore"]))
        self.git(root, "commit", "-q", "-m", "init")
        return root, {"PATH": os.path.dirname(GIT), **GIT_ENV}

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_reads_a_team_s_file_in_any_line_endings(self):
        # What --team writes over the committed file, line endings and a byte order mark aside, is the team's.
        for name, content in (("crlf", b'version = "1.8.0"\r\ntheme = "h2g2"\r\n'),
                              ("bom", b'\xef\xbb\xbfversion = "1.8.0"\r\ntheme = "h2g2"\r\n')):
            with self.subTest(name=name):
                root, env = self.team_repository(content, name=name)
                proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
                self.assert_ok(proc)
                self.assertNotIn("nothing is committed", proc.err)
                self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "move")

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_says_a_team_s_file_is_not_committed_yet(self):
        root, env = self.team_repository(b'version = "1.8.0"\ntheme = "h2g2"\n', commit=False)
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing is committed in agents/ — {TEAM} is not committed yet, and holds more than the "
                      "version --team writes — commit by hand what is the team's", proc.err)
        self.assertNotIn("changes of yours", proc.err)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "init")

    @SOURCE
    def test_a_try_s_lock_that_is_no_lock_names_the_upgrade_that_writes_it(self):
        # During a try the sync leaves the team's lock: deleted, `cortex upgrade 9.9.8` writes it again.
        root, lock = self.stale_team_lock()
        lock.write_text("<<<<<<< HEAD\nversion = 1\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("agents/cortex.team.lock", proc.err)
        self.assertIn("delete it, and `cortex upgrade 9.9.8` writes it again", proc.err)
        self.assertNotIn("`cortex sync` writes it again", proc.err)
        lock.unlink()
        self.assert_ok(self.cortex(root, "upgrade", "9.9.8"))
        self.assertEqual(str(config.load_lock(str(lock.parent), lock.name).version), "9.9.8")

    def test_team_checks_no_overlay_of_a_project_that_pins_its_own_version(self):
        # It does not follow the team's file: its overlays are its own pin's, not the team's to check.
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', team=f'version = "{OWN}"\n')
        overlay = root / "agents" / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: workspace\n"
                           "-->\n\n# x\n", encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"This project pins its own version, Cortex {OWN}, in its committed cortex.toml: it does not "
                      f"follow {TEAM}, and its overlays are not checked\n", proc.out)
        self.assertNotIn("the overlays", proc.err)

    def test_a_file_that_cannot_be_put_back_is_said_and_the_others_are(self):
        root = self.tmp / "app"
        root.mkdir()
        first, second = root / "CLAUDE.md", root / "cortex.lock"
        first.write_bytes(b"old\n")
        second.write_bytes(b"lock\n")
        put = upgrade._put

        def refused(path, content):
            if path == second:
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            put(path, content)

        def then():
            second.write_bytes(b"changed\n")
            raise RuntimeError("the switch failed")

        err = io.StringIO()
        with mock.patch.object(upgrade, "_put", side_effect=refused):
            with self.assertRaisesRegex(RuntimeError, "the switch failed"):
                upgrade._write([(first, b"new\n")], {second: b"lock\n"}, str(root), err, then)
        self.assertEqual(first.read_bytes(), b"old\n")
        self.assertIn("note: CLAUDE.md is as it was", err.getvalue())
        self.assertIn("warning: cortex.lock — Permission denied", err.getvalue())
        self.assertIn(": not put back as it was\n", err.getvalue())


class CodeReviewSecondFixTests(TeamTestCase):
    """What the second counter-review of the code review's fixes found (2026-11-10)."""

    stale = CodeReviewFixTests.stale_team_lock

    @SOURCE
    @DOWNLOADS
    def test_a_try_s_own_lock_that_is_no_lock_names_the_version_to_resume(self):
        # The try's lock is the project's own cortex.lock: a plain `cortex upgrade` would move its pin to this
        # cortex's version, so the remedy names the version — which, run, writes the lock and moves nothing.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local=f'version = "{OWN}"\n')
        lock = root / "cortex.lock"
        lock.write_text("<<<<<<< HEAD\nversion = 1\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex.lock", proc.err)
        self.assertIn("delete it, and `cortex upgrade 9.9.8` writes it again", proc.err)
        self.assertNotIn("`cortex upgrade` writes it again", proc.err)
        lock.unlink()
        self.assert_ok(self.cortex(root, "upgrade", "9.9.8"))
        self.assertEqual(tomllib.loads((root / "cortex.toml").read_text(encoding="utf-8"))["version"], "9.9.8")
        self.assertEqual(str(config.load_lock(str(root)).version), "9.9.8")

    @SOURCE
    def test_locked_names_the_version_of_a_try_s_lock_that_is_no_lock(self):
        # --locked leaves the try out and checks the team's lock, which a try leaves to `cortex upgrade 9.9.8`.
        root, lock = self.stale()
        self.assert_ok(self.cortex(root, "sync"))
        lock.write_text("<<<<<<< HEAD\nversion = 1\n", encoding="utf-8")
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("delete it, and `cortex upgrade 9.9.8`", proc.err)
        self.assertNotIn("`cortex sync` writes it again", proc.err)
        lock.unlink()
        self.assert_ok(self.cortex(root, "upgrade", "9.9.8"))
        self.assert_ok(self.cortex(root, "sync", "--locked"))

    @SOURCE
    @AS_ROOT
    @NEEDS_GIT
    def test_no_branch_is_made_for_a_team_s_lock_it_cannot_write(self):
        root, lock = self.stale()
        shutil.rmtree(root / ".git")
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q", "-b", "main")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        self.assert_ok(self.cortex(root, "sync", **env))
        os.chmod(root / "agents", 0o555)
        self.addCleanup(os.chmod, root / "agents", 0o755)
        proc = self.cortex(root, "upgrade", "--branch", "fix", "--commit", "--message", "relock", **env)
        self.assert_ok(proc)
        self.assertIn("the team's lock is not updated here", proc.err)
        self.assertIn("git: no branch fix is made — nothing was written\n", proc.out)
        self.assertEqual(self.git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        self.assertEqual(self.git(root, "branch", "--list", "fix").stdout, "")

    @SOURCE
    @AS_ROOT
    def test_a_move_cut_short_before_its_sync_says_no_sync(self):
        # The pin could not be written: the sync never ran, and this machine was not moved.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        os.chmod(root, 0o555)
        self.addCleanup(os.chmod, root, 0o755)
        proc = self.cortex(root, "upgrade", OWN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("note: cortex.toml and cortex.lock are as they were", proc.err)
        self.assertNotIn("`cortex sync` puts this machine back", proc.err)

    @SOURCE
    def test_a_resume_the_sync_would_refuse_stops_before_it_writes(self):
        # What the sync refuses at cortex/ is refused before anything is written, as the upgrade does it: a lock
        # written, said to commit, then put back — and a note naming a sync that refuses it again — no longer.
        root, lock = self.stale()
        before = lock.read_bytes()
        (root / "cortex").mkdir()
        (root / "cortex" / "mine.txt").write_text("not Cortex's\n", encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--dry-run")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex upgrade: the sync would refuse it — cortex/ is a directory, which Cortex did not write",
                      proc.err)
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cortex/ is a directory, which Cortex did not write", proc.err)
        self.assertNotIn("agents/cortex.team.lock: Cortex", proc.out)
        self.assertNotIn("as they were", proc.err)
        self.assertNotIn("`cortex sync` puts this machine back", proc.err)
        self.assertEqual(lock.read_bytes(), before)

    @SOURCE
    def test_a_move_whose_sync_moved_this_machine_names_the_sync_that_puts_it_back(self):
        # The sync moved this machine, then failed: the pin and the lock are put back, and the note names
        # `cortex sync` — which, run, puts this machine back on the pin.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        out, err = io.StringIO(), io.StringIO()
        real = store.Store

        class Own(real):                # this process is the suite's cortex, OWN, as the commands it runs are
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("own_version", OWN)
                super().__init__(*args, **kwargs)

        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home), "CORTEX_NO_REGISTER": "1",
                                          "CORTEX_NO_UPDATE_CHECK": "1"}), mock.patch.object(store, "Store", Own), \
                mock.patch.object(store, "VERSION", OWN), mock.patch.object(upgrade, "VERSION", OWN), \
                mock.patch.object(sync, "_claude", side_effect=OSError(errno.EIO, "cut short")):
            with self.assertRaises(OSError):
                upgrade.upgrade(str(root), OWN, False, False, None, out, err)
        self.assertIn("note: cortex.toml and cortex.lock are as they were — `cortex sync` puts this machine back on "
                      "them", err.getvalue())
        self.assertEqual(tomllib.loads((root / "cortex.toml").read_text(encoding="utf-8"))["version"], "9.9.8")
        self.assertEqual(config.load(str(root)).synced["reads"], OWN)          # this machine was moved
        self.assert_ok(self.cortex(root, "sync"))                               # the command the note names
        self.assertEqual(config.load(str(root)).synced["reads"], "9.9.8")
        # A resume — the pin moved by hand — whose sync moved this machine, then failed: the same.
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home), "CORTEX_NO_REGISTER": "1",
                                          "CORTEX_NO_UPDATE_CHECK": "1"}), mock.patch.object(store, "Store", Own), \
                mock.patch.object(store, "VERSION", OWN), mock.patch.object(upgrade, "VERSION", OWN), \
                mock.patch.object(sync, "_claude", side_effect=OSError(errno.EIO, "cut short")):
            with self.assertRaises(OSError):
                upgrade.upgrade(str(root), None, False, False, None, out, err)
        self.assertIn("is as it was — `cortex sync` puts this machine back on them", err.getvalue())
        self.assertEqual(config.load(str(root)).synced["reads"], OWN)           # this machine was moved
        self.assert_ok(self.cortex(root, "sync"))                               # the command the note names
        self.assertEqual(config.load(str(root)).synced["reads"], OWN)

    @SOURCE
    def test_validate_names_the_upgrade_that_writes_a_try_s_lock(self):
        # Synced with --locked beside a try, the team's lock is the resume's to write: `cortex sync` reads the try
        # and leaves it. Each remedy validate gives, run, makes it pass.
        root, lock = self.stale()
        self.assert_ok(self.cortex(root, "upgrade", "9.9.8"))
        self.assert_ok(self.cortex(root, "sync", "--locked"))
        lock.write_text("<<<<<<< HEAD\nversion = 1\n", encoding="utf-8")
        proc = self.cortex(root, "validate")
        self.assertIn("delete it, and `cortex upgrade 9.9.8` writes it again", proc.out + proc.err)
        self.assertNotIn("`cortex sync` writes it again", proc.out + proc.err)
        lock.unlink()
        proc = self.cortex(root, "validate")
        self.assertIn("no agents/cortex.team.lock — run `cortex upgrade 9.9.8`", proc.out + proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "9.9.8"))                     # the command it names
        self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), "9.9.8")
        self.assert_ok(self.cortex(root, "sync", "--locked"))
        self.assertNotIn("LOCK_", self.cortex(root, "validate").out)

    @TEAM_RELEASE
    @AS_ROOT
    def test_team_puts_back_what_it_could_not_write(self):
        # The team's folder cannot be written: --team fails, and says the team's files are as they were.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n')
        team = (root / TEAM).read_bytes()
        os.chmod(root / "agents", 0o555)
        self.addCleanup(os.chmod, root / "agents", 0o755)
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("note: agents/cortex.team.toml and agents/cortex.team.lock are as they were\n", proc.err)
        self.assertNotIn("not put back", proc.err)
        self.assertEqual((root / TEAM).read_bytes(), team)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_ending_says_committed_only_what_it_committed(self):
        # A bootstrap linked into the team's folder is the project's: never said committed with the team's files.
        marked = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes().replace(
            b"# Cortex AI Team", b"# Cortex AI Team, as 9.9.8 wrote it", 1)
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        for commit in (False, True):
            with self.subTest(commit=commit):
                team = self.tmp / f"team-{commit}"
                team.mkdir()
                (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
                (team / "CLAUDE.md").write_bytes(init.stamped(init.instructions(marked, True)))
                self.git(team, "init", "-q")
                self.git(team, "add", "-A")
                self.git(team, "commit", "-q", "-m", "init")
                root = self.project('theme = "h2g2"\n', name=f"app-{commit}")
                (root / "agents").symlink_to(team, target_is_directory=True)
                (root / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
                args = ("--commit", "--message", "move") if commit else ()
                proc = self.cortex(root, "upgrade", "--team", OWN, *args, **env)
                self.assert_ok(proc)
                self.assertIn("CLAUDE.md: written again", proc.out)
                # git sees CLAUDE.md changed in the team's repository, where it leads: named there.
                self.assertIn(" M CLAUDE.md\n", self.git(team, "status", "--short").stdout)
                if commit:
                    self.assertRegex(proc.out, r"\nCommitted in agents/ on \S+, [0-9a-f]+ — nothing was pushed; CLAUDE.md "
                                               r"not committed — it leads into agents/, where --team commits the team's "
                                               r"files only: commit it there; teammates")
                else:
                    self.assertIn("\nCommit agents/cortex.team.toml and agents/cortex.team.lock in agents/, and CLAUDE.md "
                                  "there too, where it leads; teammates", proc.out)
                self.assertNotIn("CLAUDE.md in the project", proc.out)

    def test_a_file_still_as_it_was_is_not_written_back(self):
        # Writing it back would fail where it cannot be written — said lost, though it never changed.
        root = self.tmp / "app"
        root.mkdir()
        lock = root / "cortex.lock"
        lock.write_bytes(b"lock\n")
        put = []

        def then():
            raise RuntimeError("the switch failed")

        err = io.StringIO()
        with mock.patch.object(upgrade, "_put", side_effect=lambda path, content: put.append(path)):
            with self.assertRaisesRegex(RuntimeError, "the switch failed"):
                upgrade._write([], {lock: b"lock\n"}, str(root), err, then)
        self.assertEqual(put, [])
        self.assertIn("note: cortex.lock is as it was", err.getvalue())

    @NEEDS_GIT
    def test_git_keeps_the_configuration_its_caller_gives(self):
        # As git does in another repository: `-c` and GIT_CONFIG_KEY_n are the caller's — a CI's identity, its
        # safe.directory — and stay, where a hook's GIT_DIR does not.
        repository = self.tmp / "repository"
        repository.mkdir()
        self.git(repository, "init", "-q")
        given = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "CI",
                 "GIT_CONFIG_PARAMETERS": "'core.abbrev'='12'", "GIT_DIR": str(self.tmp / "elsewhere")}
        with mock.patch.dict(os.environ, given):
            environment = config.git_environment()
            name = upgrade._git(str(repository), "config", "--get", "user.name").stdout.strip()
            abbrev = upgrade._git(str(repository), "config", "--get", "core.abbrev").stdout.strip()
        self.assertEqual(environment.get("GIT_CONFIG_COUNT"), "1")
        self.assertIn("GIT_CONFIG_PARAMETERS", environment)
        self.assertNotIn("GIT_DIR", environment)
        self.assertEqual((name, abbrev), ("CI", "12"))


class CodeReviewThirdFixTests(TeamTestCase):
    """The counter-review's third round (2026-11-10): each remedy named is a command that does what it says, and
    nothing of a build of no release is said to commit, by every command that says it."""

    @SOURCE
    def test_a_lock_of_no_release_is_never_said_to_commit_by_locked_nor_validate(self):
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"}
        mine = self.project('version = "9.9.9-dev.1"\ntheme = "h2g2"\n', name="mine")      # a .git: in a repository
        team = self.project('theme = "h2g2"\n', team='version = "9.9.9-dev.1"\n', name="team")
        for root, lock in ((mine, "cortex.lock"), (team, "agents/cortex.team.lock")):
            with self.subTest(lock=lock):
                proc = self.cortex(root, "sync", "--locked", **dev)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"--locked: no {lock} — `cortex sync` writes it. Nothing of the project was changed",
                              proc.err)
                self.assertNotIn("commit it", proc.err)
                proc = self.cortex(root, "sync", **dev)           # the command named
                self.assert_ok(proc)
                self.assertIn(f"{lock}: written", proc.out)
                self.assertNotIn("commit it", proc.out)
                self.assert_ok(self.cortex(root, "sync", "--locked", **dev))
                (root / lock).unlink()
                proc = self.cortex(root, "validate", **dev)
                self.assertIn(f"no {lock} — run `cortex sync`", proc.out + proc.err)
                self.assertNotIn("commit it", proc.out + proc.err)
                self.assert_ok(self.cortex(root, "sync", **dev))
                self.assert_ok(self.cortex(root, "validate", **dev))

    @SOURCE
    def test_a_follower_s_refusal_names_only_the_commands_that_work(self):
        # --team V for a release this cortex serves; --local V for a try that is read and served; neither, said why.
        cases = (
            ("9.9.9-dev.1", "9.9.9-dev.1", None, "--local", "Cortex 9.9.9-dev.1 is a build of no release, which no "
                                                           "teammate can download"),
            ("9.9.9", "9.9.10-dev.2", None, None, "not to 9.9.10-dev.2: Cortex 9.9.10-dev.2 is a build of no release"),
            ("9.9.9-dev.1", "9.9.9-dev.2", None, None, "not to 9.9.9-dev.2: Cortex 9.9.9-dev.2 is a build of no release"),
            ("9.9.9", "9.9.8-dev.4", None, None, "not to 9.9.8-dev.4: Cortex 9.9.8-dev.4 is a build of no release"),
            ("9.9.9", "99.0.0", None, None, "not to 99.0.0, newer than this cortex, 9.9.9: `cortex self-update 99.0.0` "
                                            "first"),
            ("9.9.9", "9.9.7", "--team", None, None),
            ("9.9.9", "1.7.0", None, None, "not to 1.7.0: the team's file is read from Cortex 1.8.0 on"),
        )
        for own, wanted, team, local, why in cases:
            with self.subTest(own=own, wanted=wanted):
                env = {"CORTEX_SOURCE_VERSION": own}
                root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', name=f"f{len(os.listdir(self.tmp))}")
                proc = self.cortex(root, "upgrade", wanted, **env)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"follows {TEAM}, which pins Cortex 9.9.8 — moving it is the team's", proc.err)
                for option, named in (("--team", team), ("--local", local)):
                    command = f"`cortex upgrade {option} {wanted}`"
                    if named is None:
                        self.assertNotIn(command, proc.err)
                        continue
                    self.assertIn(command, proc.err)
                    followed = self.cortex(root, "upgrade", option, wanted, **env)      # named: it works
                    self.assert_ok(followed)
                if why is not None:
                    self.assertIn(why, proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_beside_a_team_s_file_that_pins_none_the_refusal_names_the_commands_that_work(self):
        # A team's file that pins none, at a 2.B root: the uncommitted cortex.toml is set by hand; above it, the
        # team's pin and a try are named where they work.
        pinless = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False,
                               name="pinless")
        proc = self.cortex(pinless, "upgrade", "9.9.10-dev.2")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("which no command writes — set it there by hand. Nothing was changed", proc.err)
        for option in ("--team", "--local"):
            self.assertNotIn(f"`cortex upgrade {option}", proc.err)
        proc = self.cortex(pinless, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("set it there by hand, or pin the team's: `cortex upgrade --team 9.9.8`; trying it here: "
                      "`cortex upgrade --local 9.9.8`", proc.err)
        self.assert_ok(self.cortex(pinless, "upgrade", "--local", "9.9.8"))
        self.assertEqual(self.local(pinless)["version"], "9.9.8")

    @SOURCE
    @harness.outside_a_repository
    def test_a_try_below_the_developer_s_own_cortex_toml_is_refused(self):
        # At a 2.B root, the developer's own cortex.toml is read over a lower try: one below it would never be read.
        root = self.project('version = "9.9.9"\ntheme = "h2g2"\n', team='version = "9.9.7"\n', committed=False)
        proc = self.cortex(root, "upgrade", "--local", "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("cortex.toml, not committed, pins Cortex 9.9.9 — your own, read above a try of 9.9.8, which would "
                      "never be read. Nothing was changed. To read 9.9.8, remove cortex.toml's version — the team's "
                      "9.9.7 is read then, or a try above it — and run `cortex upgrade --local 9.9.8` again", proc.err)
        self.assertFalse((root / "cortex.local.toml").exists())
        # At its version, a try is read — the higher one wins, a try on a tie: written.
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.9"))
        self.assertEqual(self.local(root)["version"], "9.9.9")
        # The remedy, followed: cortex.toml's version removed, the try below it is written, and read.
        (root / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.8"))
        self.assertEqual(self.value(root, "version"), "9.9.8")

    @SOURCE
    def test_a_pin_too_new_names_the_command_of_its_own_file(self):
        # The team's pin is moved by --team; a try by --local, which drops it at the team's version or below.
        team = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n', name="team")
        proc = self.cortex(team, "upgrade")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("Name the version to move to: `cortex upgrade --team VERSION` moves the team's pin", proc.err)
        self.assertNotIn("--local", proc.err)
        self.assert_ok(self.cortex(team, "upgrade", "--team", "9.9.8"))
        tried = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', local='version = "99.0.0"\n',
                             name="tried")
        proc = self.cortex(tried, "upgrade")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("`cortex upgrade --local VERSION` moves the try — to the team's version or below, it drops it",
                      proc.err)
        self.assertNotIn("--team", proc.err)
        self.assert_ok(self.cortex(tried, "upgrade", "--local", "9.9.8"))
        self.assertNotIn("version", self.local(tried) if (tried / "cortex.local.toml").exists() else {})

    @SOURCE
    @harness.outside_a_repository
    def test_a_refused_build_of_no_release_names_a_try_beside_a_team_s_file_that_pins_none(self):
        # Beside a team's file that pins no version, a try is compared with the uncommitted cortex.toml's version.
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"}
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='theme = "h2g2"\n', committed=False)
        before = (root / TEAM).read_bytes()
        proc = self.cortex(root, "upgrade", "--team", "9.9.9-dev.1", **dev)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("tried on this machine: `cortex upgrade --local 9.9.9-dev.1`", proc.err)
        self.assertEqual((root / TEAM).read_bytes(), before)
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.9-dev.1", **dev))       # named: a try that works
        self.assertEqual(self.local(root)["version"], "9.9.9-dev.1")

    @SOURCE
    def test_a_build_of_no_release_the_store_holds_below_the_team_s_pin_names_no_try(self):
        # Served, but below the team's version: a try there is never read, and none is named.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n')
        self.assert_ok(self.cortex(root, "sync"))
        stored = self.home / "versions"
        shutil.copytree(stored / "9.9.8", stored / "9.9.8-dev.4")
        self.assertTrue(store.Store(self.home, OWN).has(Version("9.9.8-dev.4")))
        proc = self.cortex(root, "upgrade", "--team", "9.9.8-dev.4")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("Pin a release there", proc.err)
        self.assertNotIn("--local", proc.err)

    @NEEDS_GIT
    def test_team_at_a_kept_pin_makes_a_branch_for_a_release_only(self):
        # --team at the pin, its lock to write: a branch for a release's; none for a build of no release's.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        cases = [(OWN, {})] + ([] if harness.BINARY else [("9.9.9-dev.1", {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"})])
        for own, extra in cases:
            with self.subTest(own=own):
                root = self.project('theme = "h2g2"\n', team=f'version = "{own}"\n', name=f"b{len(os.listdir(self.tmp))}")
                self.assert_ok(self.cortex(root, "sync", **env, **extra))
                (root / "agents" / "cortex.team.lock").unlink()          # what --team writes
                for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "x")):
                    self.git(root / "agents", *args)
                proc = self.cortex(root, "upgrade", "--team", "--branch", "move", **env, **extra)
                self.assert_ok(proc)
                self.assertTrue((root / "agents" / "cortex.team.lock").is_file())
                branches = self.git(root / "agents", "branch", "--list", "move").stdout.strip()
                if store.development(Version(own)):
                    self.assertEqual(branches, "")
                    self.assertIn(f"note: Cortex {own} is a build of no release, which no teammate can download — no "
                                  "branch is made in agents/", proc.err)
                else:
                    self.assertIn("move", branches)


class CounterReviewRoundTwoTests(TeamTestCase):
    """Bluspark's second pass over the code (2026-11-10): each command a refusal names is one that works."""

    @SOURCE
    def test_no_team_move_is_named_from_a_build_of_no_release_that_prepares_1_8_0(self):
        # 1.8.0 and its candidates are newer than 1.8.0-dev.N, and every release before them reads no team's file: no
        # --team VERSION moves the pin — a build that prepares 1.8.1 serves 1.8.0, and names it.
        root = self.project('theme = "h2g2"\n', team='version = "99.0.0"\n')
        for own, named in (("1.8.0-dev.5", False), ("1.8.0-dev", False), ("1.8.1-dev.2", True), ("1.8.0-rc.1", True)):
            for args in (("sync",), ("validate",), ("init", "--tool", "claude"), ("upgrade",)):
                with self.subTest(own=own, args=args):
                    proc = self.cortex(root, *args, CORTEX_SOURCE_VERSION=own)
                    self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                    self.assertIn(f"Cortex 99.0.0, which its {TEAM} pins", proc.err)
                    self.assertEqual("cortex upgrade --team" in proc.err, named, proc.err)
                    if args == ("upgrade",) and not named:
                        self.assertIn("Update this cortex to serve it:\n    cortex self-update 99.0.0",
                                      proc.err.replace("\r\n", "\n"))
        # A release candidate reads the team's file: the move it names works.
        self.assertIn("cortex upgrade --team 1.8.0-rc.1", self.cortex(root, "sync", CORTEX_SOURCE_VERSION="1.8.0-rc.1").err)
        self.assert_ok(self.cortex(root, "upgrade", "--team", "1.8.0-rc.1", CORTEX_SOURCE_VERSION="1.8.0-rc.1"))
        self.assertIn('version = "1.8.0-rc.1"', (root / TEAM).read_text(encoding="utf-8"))
        (root / TEAM).write_text('version = "99.0.0"\n', encoding="utf-8")
        # Why: each release that reads the team's file is newer than 1.8.0-dev.5, and refused as such.
        proc = self.cortex(root, "upgrade", "--team", "1.8.0", CORTEX_SOURCE_VERSION="1.8.0-dev.5")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("Cortex 1.8.0 is newer than this cortex, 1.8.0-dev.5", proc.err)

    @SOURCE
    @DOWNLOADS
    def test_a_team_s_pin_of_no_release_seen_from_a_build_that_prepares_1_8_0_names_an_update_first(self):
        # From 1.8.0-dev.5, every --team VERSION is refused — newer than this cortex, before the team's file, or this
        # build's own: the remedy to a team's pin of no release is a release of this cortex first, then --team.
        release = FakeRelease(("1.7.0", "1.8.0-rc.1"))
        self.addCleanup(release.close)
        dev = {"CORTEX_SOURCE_VERSION": "1.8.0-dev.5", "CORTEX_RELEASES_URL": release.url}
        newer = self.project(team='version = "1.8.0-dev.9"\n', name="newer")
        older = self.project(team='version = "1.8.0-dev.4"\n', name="older")    # in no store
        for root, commands in ((newer, (("sync",), ("validate",), ("config", "version"), ("upgrade",))),
                               (older, (("sync",), ("upgrade",)))):
            for args in commands:
                with self.subTest(root=root.name, args=args):
                    proc = self.cortex(root, *args, **dev)
                    text = (proc.out + proc.err).replace("\r\n", "\n")
                    first = ("pdate this cortex to a release that reads the team's file — 1.8.0 or a candidate of it — "
                             "then pin one")
                    # Too new, on lines of their own; not served, in the sentence.
                    self.assertIn(f"{first}:\n    cortex self-update VERSION\n    cortex upgrade --team VERSION\n"
                                  if root is newer else
                                  f"{first}: `cortex self-update VERSION`, `cortex upgrade --team VERSION`", text)
                    self.assertNotIn("pin a release:\n    cortex upgrade --team", text)
                    self.assertNotIn("pin a release: `cortex upgrade --team", text)
        # Followed: this cortex a release that reads the team's file — the self-update — its --team moves the pin.
        rc = {"CORTEX_SOURCE_VERSION": "1.8.0-rc.1", "CORTEX_RELEASES_URL": release.url}
        for root in (newer, older):
            with self.subTest(followed=root.name):
                self.assert_ok(self.cortex(root, "upgrade", "--team", "1.8.0-rc.1", **rc))
                self.assertIn('version = "1.8.0-rc.1"', (root / TEAM).read_text(encoding="utf-8"))
                self.assert_ok(self.cortex(root, "sync", "--locked", **rc))
        # From a build that prepares 1.8.1, 1.8.0 reads the team's file: --team VERSION is named, as before.
        later = self.project(team='version = "1.8.0-dev.4"\n', name="later")
        proc = self.cortex(later, "sync", CORTEX_SOURCE_VERSION="1.8.1-dev.2")
        self.assertIn("pin a release: `cortex upgrade --team VERSION`", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_try_is_named_above_this_developer_s_cortex_toml_only(self):
        # A 2.B root: the team's file pins 9.9.7, the uncommitted cortex.toml 9.9.8 — this developer's, read. A try at
        # or below 9.9.8 is never read: it is named above it only, and then it works.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team='version = "9.9.7"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        self.assertEqual(self.value(root, "version"), "9.9.8")
        proc = self.cortex(root, "upgrade", "9.9.8-dev.1", CORTEX_SOURCE_VERSION="9.9.9")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertNotIn("--local", proc.err)
        proc = self.cortex(root, "upgrade", OWN)
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"`cortex upgrade --team {OWN}`; trying it here: `cortex upgrade --local {OWN}`", proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "--local", OWN))                      # named: it works
        self.assertEqual(self.value(root, "version"), OWN)
        # Below this developer's version, between it and the team's: no try is named.
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.7"))                  # no try left
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("`cortex upgrade --team 9.9.8`", proc.err)
        self.assertNotIn("--local", proc.err)
        # This developer's cortex.toml below the team's: the team's is read, and a try is named above it only.
        low = self.project('version = "9.9.6"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', committed=False,
                           name="low")
        proc = self.cortex(low, "upgrade", "9.9.7")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertNotIn("--local", proc.err)

    @SOURCE
    @harness.outside_a_repository
    def test_a_try_below_this_developer_s_cortex_toml_is_refused_with_a_remedy_that_reads_it(self):
        # A 2.B root: the team's file pins 9.9.5, the uncommitted cortex.toml 9.9.7 — this developer's — and a try at
        # 9.9.9 is read. The higher version is read, a try on a tie (ADR-013 §3.1).
        release = FakeRelease(("9.9.5", "9.9.6", "9.9.7", "9.9.8"))
        self.addCleanup(release.close)
        env = {"CORTEX_RELEASES_URL": release.url}
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='version = "9.9.5"\n', committed=False)
        self.assert_ok(self.cortex(root, "upgrade", "--local", OWN, **env))
        self.assertEqual(self.value(root, "version"), OWN)
        # At cortex.toml's version, the try is read: written.
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.7", **env))
        self.assertEqual(self.local(root)["version"], "9.9.7")
        self.assertIn("cortex.local.toml", self.cortex(root, "config").out.split("\n")[0])
        # Below it, never read: refused, with the try left as it was — and the remedy named reads it.
        self.assert_ok(self.cortex(root, "upgrade", "--local", OWN, **env))
        proc = self.cortex(root, "upgrade", "--local", "9.9.6", **env)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("cortex.toml, not committed, pins Cortex 9.9.7 — your own, read above a try of 9.9.6, which "
                      "would never be read. Nothing was changed. To read 9.9.6, remove cortex.toml's version — the "
                      "team's 9.9.5 is read then, or a try above it — and run `cortex upgrade --local 9.9.6` again", proc.err)
        self.assertEqual(self.local(root)["version"], OWN)
        # Bluspark's third pass: at or below the team's version, the try is removed — and cortex.toml's version, above
        # the team's, is what is read then, as said.
        for dry in (("--dry-run",), ()):
            proc = self.cortex(root, "upgrade", "--local", "9.9.5", *dry, **env)
            self.assert_ok(proc)
            self.assertIn(f"cortex.local.toml: version {OWN} {'would be' if dry else 'is'} removed — cortex.toml's "
                          f"9.9.7, not committed, is read, above the team's 9.9.5 ({TEAM})\n", proc.out)
        self.assertEqual(self.value(root, "version"), "9.9.7")
        (root / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")                # followed
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.6", **env))
        self.assertEqual(self.value(root, "version"), "9.9.6")

    @SOURCE
    @harness.outside_a_repository
    def test_team_to_a_build_of_no_release_names_a_try_above_this_developer_s_cortex_toml_only(self):
        # The store holds 9.9.8-dev.1, a sync by that build. The team's file pins 9.9.7, the developer's uncommitted
        # cortex.toml 9.9.8: a try of 9.9.8-dev.1, below it, would never be read — --team names none.
        seed = self.project('version = "9.9.8-dev.1"\ntheme = "h2g2"\n', committed=False, name="seed")
        self.assert_ok(self.cortex(seed, "sync", CORTEX_SOURCE_VERSION="9.9.8-dev.1"))
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team='version = "9.9.7"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade", "--team", "9.9.8-dev.1")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("is a build of no release, which no teammate can download", proc.err)
        self.assertNotIn("--local", proc.err)
        (self.home / "projects.toml").write_text(f'format = 1\n\n[[project]]\nname = "app"\npath = "{harness_path(root)}"\n',
                                                 encoding="utf-8")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", "9.9.8-dev.1")
        self.assertIn("is a build of no release, which no teammate can download", proc.out + proc.err)
        self.assertNotIn("--local", proc.out + proc.err)
        # Above the team's version alone, the same try is named — and it works.
        (root / "cortex.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--team", "9.9.8-dev.1")
        self.assertIn("tried on this machine: `cortex upgrade --local 9.9.8-dev.1`", proc.err)
        self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.8-dev.1"))

    @SOURCE
    @harness.outside_a_repository
    def test_all_team_to_a_build_of_no_release_names_each_follower_its_own_try(self):
        # One team's folder, pin 9.9.7, linked by two 2.B roots: one reads it, the other its developer's own cortex.toml
        # at 9.9.8. The folder is refused once; the try is each follower's — named where it works there, in either
        # order, and it works.
        seed = self.project('version = "9.9.8-dev.1"\ntheme = "h2g2"\n', committed=False, name="seed")
        self.assert_ok(self.cortex(seed, "sync", CORTEX_SOURCE_VERSION="9.9.8-dev.1"))
        for plain, own in (("alpha", "beta"), ("zeta", "aaa")):
            with self.subTest(first=min(plain, own)):
                team = self.tmp / f"team-{plain}"
                team.mkdir()
                (team / "cortex.team.toml").write_text('version = "9.9.7"\n', encoding="utf-8")
                roots = {}
                for name, toml in ((plain, 'theme = "h2g2"\n'), (own, 'version = "9.9.8"\ntheme = "h2g2"\n')):
                    roots[name] = self.project(toml, committed=False, name=name)
                    (roots[name] / "agents").symlink_to(team, target_is_directory=True)
                    self.assert_ok(self.cortex(roots[name], "sync"))
                (self.home / "projects.toml").write_text("format = 1\n" + "".join(
                    f'\n[[project]]\nname = "{name}"\npath = "{harness_path(root)}"\n' for name, root in roots.items()),
                    encoding="utf-8")
                proc = self.cortex(self.tmp, "upgrade", "--all", "--team", "9.9.8-dev.1")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                said = {name: next(line for line in proc.out.splitlines() if line.split()[:2] == [name, "refused:"])
                        for name in roots}
                for line in said.values():
                    self.assertIn("is a build of no release, which no teammate can download", line)
                self.assertIn("tried on this machine: `cortex upgrade --local 9.9.8-dev.1`", said[plain])
                self.assertNotIn("--local", said[own])
                self.assertEqual((team / "cortex.team.toml").read_text(encoding="utf-8"), 'version = "9.9.7"\n')
                self.assert_ok(self.cortex(roots[plain], "upgrade", "--local", "9.9.8-dev.1"))   # named: it works
                self.assertEqual(self.value(roots[plain], "version"), "9.9.8-dev.1")
                self.assertEqual(self.cortex(roots[own], "upgrade", "--local", "9.9.8-dev.1").returncode, 1)  # not

    @SOURCE
    def test_team_to_its_own_version_from_a_build_that_prepares_1_8_0_names_an_update_first(self):
        # From 1.8.0-dev.5, a team's file is pinned to no release it serves: refused, the remedy is this cortex updated
        # to a release that reads the team's file — not "pin a release there", which it cannot.
        release = FakeRelease(("1.7.0", "1.8.0-rc.1"))
        self.addCleanup(release.close)
        root = self.project(team='version = "1.8.0-dev.9"\n')
        proc = self.cortex(root, "upgrade", "--team", "1.8.0-dev.5", CORTEX_SOURCE_VERSION="1.8.0-dev.5",
                           CORTEX_RELEASES_URL=release.url)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"every teammate reads {TEAM} — nothing was changed. Update this cortex to a release that reads "
                      "the team's file — 1.8.0 or a candidate of it — then pin one: `cortex self-update VERSION`, "
                      "`cortex upgrade --team VERSION`", proc.err)
        self.assertNotIn("Pin a release there", proc.err)
        # Followed: a release candidate — the self-update — pins one.
        rc = {"CORTEX_SOURCE_VERSION": "1.8.0-rc.1", "CORTEX_RELEASES_URL": release.url}
        self.assert_ok(self.cortex(root, "upgrade", "--team", "1.8.0-rc.1", **rc))
        self.assertIn('version = "1.8.0-rc.1"', (root / TEAM).read_text(encoding="utf-8"))

    @SOURCE
    def test_a_try_of_a_build_of_no_release_is_said_as_no_download(self):
        # A build of no release is downloaded by no one: its try's note names no SHA256SUMS it was checked against.
        dev = {"CORTEX_SOURCE_VERSION": "9.9.9-dev.1"}
        root = self.project(team='version = "9.9.8"\n', local='version = "9.9.9-dev.1"\n')
        proc = self.cortex(root, "sync", **dev)
        self.assert_ok(proc)
        self.assertIn(f"Cortex 9.9.9-dev.1 is read — cortex.local.toml's, above the team's 9.9.8 ({TEAM}): it is not "
                      "locked, a build of no release, which no SHA256SUMS checks", proc.err)
        self.assertNotIn("when downloaded", proc.err)
        root = self.project(team='version = "9.9.8"\n', local='version = "9.9.9"\n', name="release")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("it is not locked, its release checked against its SHA256SUMS when downloaded", proc.err)


class SecondCodeReviewTests(TeamTestCase):
    """What the second counter-review of the 1.8.0 stack's code found (2026-11-10)."""

    LOCKING = 'claude_access = false\nlocked = ["claude_access"]\n'
    PULLED = '<<<<<<< HEAD\nversion = "9.9.7"\n=======\nversion = "9.9.8"\n>>>>>>> origin/main\n' + LOCKING

    @SOURCE
    @harness.outside_a_repository
    def test_init_s_remedy_for_a_team_s_file_that_does_not_read_names_that_file(self):
        # The team's file's own error: never remove cortex.toml — the developer's own at a 2.B root, and lost — but
        # fix the file, after which init runs.
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n', team=self.PULLED,
                            committed=False)
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"{TEAM}: Invalid statement", proc.err)
        self.assertIn(f"cortex init reads {TEAM}: fix it — a conflict a pull left in it, say — then run cortex init "
                      "again", proc.err)
        self.assertNotIn("remove it", proc.err)
        (root / TEAM).write_text(f'version = "{OWN}"\n' + self.LOCKING, encoding="utf-8")       # the conflict solved
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assert_ok(proc)
        self.assertIn("claude_access = true", (root / "cortex.toml").read_text(encoding="utf-8"))

    @harness.outside_a_repository
    def test_a_team_s_file_that_does_not_read_beside_an_uncommitted_pin_is_refused(self):
        # A 2.B root at 1.7.0 whose agents/ a pull left in a conflict: the team's version is the file's, which it
        # cannot tell — refused, never read as a file that pins none, and its lock dropped (ADR-013 §3.1, §3.5).
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n', team=self.PULLED,
                            committed=False)
        for args in (("sync",), ("sync", "--locked"), ("config", "claude_access"), ("validate",)):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn(f"{TEAM}: Invalid statement", proc.out + proc.err)
                self.assertNotIn("is not read", proc.out + proc.err)
        self.assertFalse((root / "cortex.local.toml").exists())
        # Committed, cortex.toml's pin is the team's version: the file is not read, as before 1.8.0.
        (root / ".git").mkdir()
        proc = self.cortex(root, "config", "claude_access")
        self.assert_ok(proc)
        self.assertEqual(proc.out, "true\n")
        self.assertIn(f"{TEAM} is not read: the team's version is 1.7.0", proc.err)

    @AS_ROOT
    @harness.outside_a_repository
    def test_a_team_s_file_that_cannot_be_opened_beside_an_uncommitted_pin_is_refused(self):
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n',
                            team='version = "9.9.8"\n' + self.LOCKING, committed=False)
        (root / TEAM).chmod(0)
        self.addCleanup((root / TEAM).chmod, 0o644)
        for args in (("sync",), ("sync", "--locked")):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"{TEAM}: cannot be read — Permission denied", proc.err)
        self.assertFalse((root / "cortex.local.toml").exists())

    def moved_by_hand(self, name, own_repository, synced=False):
        """A follower whose team's file, at 9.9.7 and committed — with its lock, ``synced`` — was moved to 9.9.8 by
        hand: in a folder of the project's repository, or in the team's own. The repository, and git's environment."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team='version = "9.9.7"\n', committed=False, name=name)
        where = root / "agents" if own_repository else root
        (where / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        if synced:
            self.assert_ok(self.cortex(root, "sync", **env))
        self.git(where, "init", "-q")
        self.git(where, "add", "-A")
        self.git(where, "commit", "-q", "-m", "init")
        (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")
        return root, where, env

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_commit_at_a_pin_moved_by_hand_commits_the_file_with_its_lock(self):
        # --team at the version the file was moved to by hand writes the lock, and commits the two: committed alone,
        # the lock names 9.9.8 beside a committed file at 9.9.7, which a teammate's `sync --locked` refuses.
        for own in (False, True):
            with self.subTest(own_repository=own):
                root, where, env = self.moved_by_hand(f"team{int(own)}", own)
                proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--commit", "--message", "move", **env)
                self.assert_ok(proc)
                self.assertIn(f"{TEAM}: Cortex 9.9.8, pinned already — its lock is written\n", proc.out)
                self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "move")
                self.assertEqual(self.git(where, "status", "--porcelain", "--", ".").stdout, "")
                shown = "./cortex.team.toml" if own else TEAM
                self.assertIn('version = "9.9.8"', self.git(where, "show", f"HEAD:{shown}").stdout)
                # As a teammate's clean clone reads it: the lock names the version the file pins.
                self.assert_ok(self.cortex(root, "sync", "--locked", **env))

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_resume_commit_leaves_a_team_s_file_moved_by_hand_and_its_lock(self):
        # A follower's resume writes the team's lock of the version the file was moved to by hand: the file is a pin,
        # the developer's change — committed alone, the lock would name 9.9.8 beside a committed file at 9.9.7.
        root, where, env = self.moved_by_hand("resume", False, synced=True)
        proc = self.cortex(root, "upgrade", "9.9.8", "--commit", "--message", "resume", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing is committed in {root} — {TEAM} held changes of yours before the upgrade — "
                      "commit by hand what is the team's\n", proc.err)
        self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "init")
        self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), "9.9.8")
        # What the note says, done by hand: the two committed together, a teammate's clean checkout reads them.
        self.git(where, "commit", "-q", "-m", "move", "--", TEAM, "agents/cortex.team.lock")
        self.assertEqual(self.git(where, "status", "--porcelain", "--", "agents").stdout, "")
        self.assert_ok(self.cortex(root, "sync", "--locked", **env))

    def edited_at_its_version(self, name, own_repository):
        """A follower whose team's file at 9.9.8 is committed beside a stale lock at 9.9.7 — a commit that left the
        lock behind — and holds an edit of the developer's own, its version as committed: in a folder of the
        project's repository, or in the team's own. The repository, and git's environment."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team='version = "9.9.7"\n', committed=False, name=name)
        where = root / "agents" if own_repository else root
        (where / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync", **env))                  # the lock, at 9.9.7
        (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")
        self.git(where, "init", "-q")
        self.git(where, "add", "-A")
        self.git(where, "commit", "-q", "-m", "init")
        (root / TEAM).write_text('version = "9.9.8"\ntheme = "none"\n', encoding="utf-8")    # the developer's own
        return root, where, env

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_at_its_pin_commits_the_lock_alone_beside_an_edit_of_the_developer_s(self):
        # Its version as committed, the team's file is not moved by hand: the lock goes alone — committed, or named
        # alone to commit — and the developer's own edit stays out of the team's commit.
        for own in (False, True):
            for commit in (False, True):
                with self.subTest(own_repository=own, commit=commit):
                    root, where, env = self.edited_at_its_version(f"edit{int(own)}{int(commit)}", own)
                    args = ("--commit", "--message", "lock") if commit else ()
                    proc = self.cortex(root, "upgrade", "--team", "9.9.8", *args, **env)
                    self.assert_ok(proc)
                    lock = "cortex.team.lock" if own else "agents/cortex.team.lock"
                    if commit:
                        self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "lock")
                        self.assertEqual(self.git(where, "show", "--name-only", "--format=", "HEAD").stdout.split(),
                                         [lock])
                        self.assertNotIn("holds changes of yours", proc.err)
                    else:
                        self.assertIn("\nCommit agents/cortex.team.lock in agents/", proc.out)
                    self.assertNotIn("Commit agents/cortex.team.toml", proc.out)
                    shown = "cortex.team.toml" if own else TEAM
                    self.assertEqual(self.git(where, "status", "--porcelain", "--", shown).stdout.strip(), f"M {shown}")

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_resume_commits_the_lock_beside_an_edit_of_the_developer_s(self):
        # A follower's resume writes the lock of the version the committed file pins: the developer's own edit of
        # another key does not hold the commit back — the lock is committed, the edit left.
        root, where, env = self.edited_at_its_version("resume-edit", False)
        proc = self.cortex(root, "upgrade", "--commit", "--message", "resume", **env)
        self.assert_ok(proc)
        self.assertNotIn("held changes of yours", proc.err)
        self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "resume")
        self.assertIn("agents/cortex.team.lock", self.git(where, "show", "--name-only", "--format=", "HEAD").stdout)
        self.assertEqual(self.git(where, "status", "--porcelain", "--", TEAM).stdout.strip(), f"M {TEAM}")
        self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), "9.9.8")

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_project_s_own_pin_commits_beside_an_edit_of_the_team_s_file(self):
        # A committed cortex.toml pinning its own version: the team's file, edited by hand, is no pin of this project
        # — the upgrade commits cortex.toml and cortex.lock.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('version = "9.9.7"\ntheme = "h2g2"\n', team='version = "9.9.7"\n', committed=False,
                            name="own-pin")
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync", **env))
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        (root / TEAM).write_text('version = "9.9.7"\ntheme = "none"\n', encoding="utf-8")
        proc = self.cortex(root, "upgrade", "9.9.8", "--commit", "--message", "up", **env)
        self.assert_ok(proc)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "up")
        self.assertEqual(sorted(self.git(root, "show", "--name-only", "--format=", "HEAD").stdout.split()),
                         ["cortex.lock", "cortex.toml"])

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_lock_written_beside_a_team_s_file_moved_by_hand_names_the_file_too(self):
        # agents/ its own repository: the sync's note, and the resume's, name the team's file moved by hand beside
        # the lock — committed alone, the lock would name 9.9.8 beside a committed file at 9.9.7.
        root, where, env = self.moved_by_hand("named", True)
        proc = self.cortex(root, "sync", **env)
        self.assert_ok(proc)
        self.assertIn("agents/cortex.team.lock: written — commit it in agents/, with cortex.team.toml, moved to its "
                      "version by hand\n", proc.out)
        root, where, env = self.moved_by_hand("named-resume", True, synced=True)
        proc = self.cortex(root, "upgrade", "9.9.8", "--commit", "--message", "resume", **env)
        self.assert_ok(proc)
        self.assertIn("— commit it in agents/, with cortex.team.toml, moved to its version by hand\n", proc.out)
        self.assertIn(f"{root / 'agents' / 'cortex.team.lock'} and {root / TEAM} — outside its git work tree: commit "
                      "them where they are", proc.out)

    def locked_and_edited(self, name, own_repository):
        """A follower whose team's file and lock, at 9.9.8, are committed — pinned and locked — and whose team's file
        holds an edit of the developer's own, its version as committed. The repository, and git's environment."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', committed=False, name=name)
        where = root / "agents" if own_repository else root
        (where / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync", **env))                  # the lock, at 9.9.8
        self.git(where, "init", "-q")
        self.git(where, "add", "-A")
        self.git(where, "commit", "-q", "-m", "init")
        (root / TEAM).write_text('version = "9.9.8"\ntheme = "none"\n', encoding="utf-8")    # the developer's own
        return root, where, env

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_pinned_and_locked_never_says_to_commit_the_developer_s_edit(self):
        # Nothing to write: what is left to commit is the team's — the developer's edit of another key, the version as
        # committed, is never named for the team's commit, nor taken for a run cut short (ADR-013 §3.11).
        for own in (False, True):
            with self.subTest(own_repository=own):
                root, where, env = self.locked_and_edited(f"locked{int(own)}", own)
                proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--commit", "--message", "m", **env)
                self.assert_ok(proc)
                self.assertIn(f"{TEAM}: Cortex 9.9.8, pinned and locked already — nothing to move\n", proc.out)
                self.assertNotIn("Commit agents/cortex.team.toml", proc.out)
                self.assertNotIn("holds changes of yours", proc.err)
                self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "init")
                proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--branch", "b", **env)
                self.assert_ok(proc)
                self.assertIn("git: no branch b is made — nothing was written\n", proc.out)
                self.assertNotIn("a run cut short", proc.err)
                shown = "cortex.team.toml" if own else TEAM
                self.assertEqual(self.git(where, "status", "--porcelain", "--", shown).stdout.strip(), f"M {shown}")

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_team_s_file_committed_nowhere_goes_with_its_lock_and_is_said_so(self):
        # A team's file just made, committed nowhere — git told to list no untracked file, too: it goes with its
        # lock, and the sync's note says why — not that it was moved by hand.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        for name in ("new-sync", "new-team"):
            with self.subTest(name=name):
                root = self.project('theme = "h2g2"\n', committed=False, name=name)
                (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
                self.git(root, "init", "-q")
                self.git(root, "config", "status.showUntrackedFiles", "no")
                self.git(root, "add", "-A")
                self.git(root, "commit", "-q", "-m", "init")
                (root / "agents").mkdir()
                (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")
                if name == "new-sync":
                    proc = self.cortex(root, "sync", **env)
                    self.assert_ok(proc)
                    self.assertIn("agents/cortex.team.lock: written — commit it in agents/, with cortex.team.toml, "
                                  "committed nowhere yet\n", proc.out)
                    continue
                proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--commit", "--message", "add", **env)
                self.assert_ok(proc)
                self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "add")
                self.assertEqual(sorted(self.git(root, "show", "--name-only", "--format=", "HEAD").stdout.split()),
                                 ["agents/cortex.team.lock", TEAM])

    @SOURCE
    @harness.outside_a_repository
    def test_init_names_the_team_s_file_its_grammar_refuses(self):
        # The team's file parses, and its grammar refuses it: the remedy is the file's, never removing cortex.toml —
        # the developer's own at a 2.B root. Followed, init runs.
        for n, team in enumerate(('version = "9.9.8"\ncolour = "x"\n', 'version = "9.9.8"\ntheme = 3\n',
                                  "version = 9\n")):
            with self.subTest(team=team):
                root = self.project('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n', team=team,
                                    committed=False, name=f"grammar{n}")
                proc = self.cortex(root, "init", "--tool", "claude")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"cortex init reads {TEAM}: fix it", proc.err)
                self.assertNotIn("remove it", proc.err)
                (root / TEAM).write_text(f'version = "{OWN}"\n' + self.LOCKING, encoding="utf-8")
                self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
                self.assertIn("claude_access = true", (root / "cortex.toml").read_text(encoding="utf-8"))

    @SOURCE
    @harness.outside_a_repository
    def test_init_names_cortex_toml_for_its_own_error_beside_a_team_s_file_that_does_not_read(self):
        # cortex.toml's own error comes first: its remedy, never the team's file's. Each remedy, followed, leads on.
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\nfoo = 1\n', team=self.PULLED,
                            committed=False)
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn('cortex.toml: unknown key "foo"', proc.err)
        self.assertIn("cortex init keeps an existing cortex.toml: fix it", proc.err)
        self.assertNotIn(f"cortex init reads {TEAM}", proc.err)
        (root / "cortex.toml").write_text('version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n',
                                          encoding="utf-8")
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"cortex init reads {TEAM}: fix it", proc.err)
        (root / TEAM).write_text(f'version = "{OWN}"\n' + self.LOCKING, encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_an_upgrade_already_on_it_names_the_team_s_files_the_sync_left(self):
        # The sync wrote the lock of a team's file moved by hand, and named the two: an upgrade with nothing to move
        # says them too — never committed, as what is left never is.
        root, where, env = self.moved_by_hand("left", False)
        self.assert_ok(self.cortex(root, "sync", **env))
        proc = self.cortex(root, "upgrade", "9.9.8", "--commit", "--message", "m", **env)
        self.assert_ok(proc)
        self.assertIn("Cortex 9.9.8: already on it — nothing to move\n", proc.out)
        note = next(line for line in proc.err.splitlines() if "hold changes git has not committed" in line)
        self.assertIn("agents/cortex.team.lock", note)
        self.assertIn(TEAM, note)
        self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "init")

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_the_lock_left_to_an_upgrade_names_the_developer_s_own_cortex_toml_not_a_try(self):
        # A 2.B root whose uncommitted cortex.toml is above the team's, no try set: what is read above is the
        # developer's own cortex.toml — --locked and the resume say so, and the command named writes the lock.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team='version = "9.9.7"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("`cortex upgrade 9.9.7`, which your own cortex.toml leaves to it, writes it", proc.err)
        self.assertNotIn("a try", proc.err)
        proc = self.cortex(root, "upgrade", "9.9.7")
        self.assert_ok(proc)
        self.assertIn("the sync reads your own cortex.toml and leaves it to `cortex upgrade`: writing it", proc.out)
        self.assertNotIn("the try", proc.out)
        self.assert_ok(self.cortex(root, "sync", "--locked"))

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_commit_commits_nothing_when_what_the_team_s_file_held_cannot_be_told(self):
        # The team's file committed with a conflict a merge left, resolved since: what the commit would carry over
        # the committed file cannot be told — nothing is committed, and that is said.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team='<<<<<<< HEAD\nversion = "1.8.0"\n', committed=False)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        (root / TEAM).write_text('version = "1.8.0"\n', encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing is committed in agents/ — it could not be told whether {TEAM} held changes of "
                      f"yours — {TEAM}: its version is written in a way cortex cannot rewrite", proc.err)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "init")

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")
    def test_a_resume_whose_sync_moved_only_cortex_names_the_sync_that_puts_it_back(self):
        # Before 1.8.0 the sync records nothing in cortex.local.toml: that it moved this machine, cortex/ alone says.
        release = FakeRelease(("1.6.0", "1.7.0"))
        self.addCleanup(release.close)
        pinned = 'version = "{}"\ntheme = "h2g2"\nsync = "link"\n'
        root = self.project(pinned.format("1.6.0"))
        self.assert_ok(self.cortex(root, "sync", CORTEX_RELEASES_URL=release.url))
        lead, local = os.readlink(root / "cortex"), (root / "cortex.local.toml").read_bytes()
        (root / "cortex.toml").write_text(pinned.format("1.7.0"), encoding="utf-8")      # moved by hand
        out, err = io.StringIO(), io.StringIO()
        real = store.Store

        class Own(real):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("own_version", OWN)
                super().__init__(*args, **kwargs)

        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home), "CORTEX_NO_REGISTER": "1",
                                          "CORTEX_NO_UPDATE_CHECK": "1", "CORTEX_RELEASES_URL": release.url}), \
                mock.patch.object(store, "Store", Own), mock.patch.object(store, "VERSION", OWN), \
                mock.patch.object(upgrade, "VERSION", OWN), \
                mock.patch.object(sync, "_claude", side_effect=OSError(errno.EIO, "cut short")):
            with self.assertRaises(OSError):
                upgrade.upgrade(str(root), "1.7.0", False, False, None, out, err)
        self.assertIn("note: cortex.lock is as it was — `cortex sync` puts this machine back on them", err.getvalue())
        self.assertNotEqual(os.readlink(root / "cortex"), lead)                 # this machine was moved
        self.assertEqual((root / "cortex.local.toml").read_bytes(), local)    # by cortex/ alone
        self.assert_ok(self.cortex(root, "sync", CORTEX_RELEASES_URL=release.url))   # the command the note names
        self.assertEqual(os.path.basename(os.readlink(root / "cortex")), "1.7.0")   # on the pin, the resume done

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_a_link_git_tracks_as_the_team_s_folder_is_no_submodule(self):
        # git's index holds agents as a link — mode 120000 — to an empty folder: no submodule left empty.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        (self.tmp / "empty").mkdir()
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', committed=False)
        (root / "agents").symlink_to(self.tmp / "empty", target_is_directory=True)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        self.assertTrue(self.git(root, "ls-files", "--stage", "agents").stdout.startswith("120000 "))
        proc = self.cortex(root, "sync", **env)
        self.assert_ok(proc)
        self.assertNotIn("a submodule not checked out", proc.err)
        self.assertFalse(sync.gitlink(root / "agents"))

    @SOURCE
    @DOWNLOADS
    def test_a_resume_that_does_not_sync_checks_nothing_of_cortex(self):
        # Synced during a try, the team's lock is the resume's to write — no sync runs: a cortex/ of the developer's,
        # which the sync refuses, is no reason to refuse it.
        root, lock = CodeReviewFixTests.stale_team_lock(self)
        self.assert_ok(self.cortex(root, "sync"))
        (root / "cortex").mkdir()
        (root / "cortex" / "mine.txt").write_text("not Cortex's\n", encoding="utf-8")
        proc = self.cortex(root, "upgrade")
        self.assert_ok(proc)
        self.assertEqual(str(config.load_lock(str(root / "agents"), config.TEAM_LOCK_FILE).version), "9.9.8")
        self.assertTrue((root / "cortex" / "mine.txt").is_file())

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_validate_says_commit_the_team_s_lock_where_its_folder_is_a_repository(self):
        # At a 2.B root, the team's lock is committed in the team's own repository, if any — never in the root.
        for own in (True, False):
            with self.subTest(own_repository=own):
                root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', committed=False,
                                    name=f"v{int(own)}")
                if own:
                    (root / "agents" / ".git").mkdir()
                self.assert_ok(self.cortex(root, "sync"))
                (root / "agents" / "cortex.team.lock").unlink()
                proc = self.cortex(root, "validate")
                self.assertIn(f"no agents/cortex.team.lock — run `cortex sync`{', then commit it' if own else ''}\n",
                              proc.out + proc.err)

class ThirdCodeReviewTests(TeamTestCase):
    """What the third counter-review of the 1.8.0 stack's code found (2026-11-11)."""

    LOCKING = 'version = "9.9.8"\nclaude_access = false\nlocked = ["claude_access"]\n'
    PULLED = '<<<<<<< HEAD\nversion = "9.9.7"\n=======\nversion = "9.9.8"\n>>>>>>> origin/main\n'
    REFUSED = (("sync",), ("sync", "--locked"), ("config", "claude_access"), ("validate",),
               ("init", "--tool", "claude", "--claude-access"))

    def assert_refused(self, root, said):
        """Each command refuses the project, saying ``said`` — the team's file is never read as none — and this
        machine's file is left as it was."""
        local = (root / "cortex.local.toml").read_bytes() if (root / "cortex.local.toml").exists() else None
        for args in self.REFUSED:
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn(said, proc.err)
                self.assertNotIn("is not read", proc.out + proc.err)
        self.assertEqual((root / "cortex.local.toml").read_bytes() if local is not None
                         else (root / "cortex.local.toml").exists(), local if local is not None else False)

    @SOURCE
    @DOWNLOADS
    @AS_ROOT
    @harness.outside_a_repository
    def test_a_team_s_folder_that_cannot_be_searched_is_refused_never_read_as_no_file(self):
        # agents/ unsearchable — another user's, mode 700: the team's file is there, unread — refused, never a
        # project without one, whose machine the sync would move to cortex.toml's version, the team's lock dropped.
        for name, toml in (("own", 'version = "1.7.0"\ntheme = "h2g2"\nclaude_access = true\n'),
                           ("pinned", 'version = "9.9.8"\ntheme = "h2g2"\nclaude_access = true\n')):
            with self.subTest(name=name):
                root = self.project(toml, team=self.LOCKING, committed=name == "pinned", name=name)
                self.assert_ok(self.cortex(root, "sync"))
                self.assertEqual(self.value(root, "claude_access"), "false")
                os.chmod(root / "agents", 0)
                self.addCleanup(os.chmod, root / "agents", 0o755)
                self.assert_refused(root, f"{TEAM}: cannot be read — Permission denied")
                os.chmod(root / "agents", 0o755)
                self.assert_ok(self.cortex(root, "sync"))           # the folder readable again: the sync runs
                self.assertEqual(self.value(root, "claude_access"), "false")

    @SOURCE
    @AS_ROOT
    @harness.outside_a_repository
    def test_a_fresh_init_beside_a_team_s_folder_that_cannot_be_searched_says_how_to_go_on(self):
        root = self.tmp / "fresh"
        (root / "agents").mkdir(parents=True)
        (root / TEAM).write_text(f'version = "{OWN}"\n', encoding="utf-8")
        os.chmod(root / "agents", 0)
        self.addCleanup(os.chmod, root / "agents", 0o755)
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"{TEAM}: cannot be read — Permission denied", proc.err)
        self.assertIn(f"cortex init reads {TEAM}: fix it — make it a file you can read — then", proc.err)
        self.assertNotIn("conflict", proc.err)
        self.assertFalse((root / "cortex.toml").exists())
        os.chmod(root / "agents", 0o755)
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
    @harness.outside_a_repository
    def test_a_team_s_file_that_is_no_file_is_refused_never_read_as_none(self):
        # A directory, a link that leads nowhere, a loop of links: there, and unread — refused, as git does not
        # take them for no file either (ADR-013 §3.1, §3.5).
        # A cortex.toml without version, which a team's file lets it leave out: the team's file's error, not its own.
        for kind, said in (("directory", "not a file"), ("nowhere", "a link that leads nowhere"),
                           ("loop", "cannot be read — "), ("versionless", "not a file")):
            with self.subTest(kind=kind):
                toml = 'theme = "h2g2"\n' if kind == "versionless" else 'version = "1.7.0"\ntheme = "h2g2"\n'
                root = self.project(toml + "claude_access = true\n", committed=False, name=kind)
                (root / "agents").mkdir()
                if kind in ("directory", "versionless"):
                    (root / TEAM).mkdir()
                elif kind == "nowhere":
                    (root / TEAM).symlink_to(self.tmp / "gone.toml")
                else:
                    (root / "agents" / "other.toml").symlink_to(root / TEAM)
                    (root / TEAM).symlink_to(root / "agents" / "other.toml")
                self.assert_refused(root, f"{TEAM}: cannot be read — " if kind == "loop" else
                                    f"{TEAM}: cannot be read — {said}")

    @SOURCE
    @harness.outside_a_repository
    def test_a_fresh_init_beside_a_team_s_file_that_does_not_read_says_how_to_go_on(self):
        # No cortex.toml yet: the team's file's error, and its remedy — followed, init runs.
        root = self.tmp / "fresh"
        (root / "agents").mkdir(parents=True)
        (root / TEAM).write_text(self.PULLED, encoding="utf-8")
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"{TEAM}: Invalid statement", proc.err)
        self.assertIn(f"cortex init reads {TEAM}: fix it — a conflict a pull left in it, say — then run cortex init "
                      "again", proc.err)
        (root / TEAM).write_text(f'version = "{OWN}"\n', encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))

    @SOURCE
    @harness.outside_a_repository
    def test_init_s_remedy_for_cortex_toml_s_own_error_naming_the_team_s_file_is_cortex_toml_s(self):
        # cortex.toml's own error names the team's file — "version" is required, or in it —: the remedy is
        # cortex.toml's, which, followed, leads on.
        root = self.project('theme = "h2g2"\n', team='theme = "h2g2"\n', committed=False)
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f'cortex.toml: "version" is required — or in {TEAM}', proc.err)
        self.assertIn("cortex init keeps an existing cortex.toml: fix it", proc.err)
        self.assertNotIn(f"cortex init reads {TEAM}", proc.err)
        (root / "cortex.toml").write_text(f'version = "{OWN}"\ntheme = "h2g2"\n', encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))

    def team_committed(self, name, committed, now, own_repository=False):
        """A follower whose team's file, ``committed`` as bytes, is committed — with no lock — and now holds ``now``.
        The project, the repository, and git's environment."""
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team="", committed=False, name=name)
        (root / TEAM).write_bytes(committed)
        where = root / "agents" if own_repository else root
        (where / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(where, "init", "-q")
        self.git(where, "add", "-A")
        self.git(where, "commit", "-q", "-m", "init")
        (root / TEAM).write_bytes(now)
        return root, where, env

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_the_lock_s_note_reads_the_team_s_file_with_a_byte_order_mark_or_not_parsed(self):
        # What the team's file pinned when committed against what it pins now, a byte order mark aside: moved by
        # hand, it goes with its lock; another key changed, the lock goes alone; committed with a conflict, what it
        # pinned cannot be read — it goes with its lock.
        bom = b"\xef\xbb\xbf"
        alone = "agents/cortex.team.lock: written — commit it in agents/\n"
        moved = "agents/cortex.team.lock: written — commit it in agents/, with cortex.team.toml, moved to its " \
                "version by hand\n"
        for name, committed, now, said in (
                ("bom-moved", bom + b'version = "9.9.7"\n', bom + b'version = "9.9.8"\n', moved),
                ("bom-key", bom + b'version = "9.9.8"\n', bom + b'version = "9.9.8"\ntheme = "none"\n', alone),
                ("conflict", self.PULLED.encode(), b'version = "9.9.8"\n', moved)):
            with self.subTest(name=name):
                root, where, env = self.team_committed(name, committed, now)
                proc = self.cortex(root, "sync", **env)
                self.assert_ok(proc)
                self.assertIn(said, proc.out)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_team_commit_commits_a_team_s_file_with_a_byte_order_mark_moved_by_hand_with_its_lock(self):
        bom = b"\xef\xbb\xbf"
        root, where, env = self.team_committed("bom", bom + b'version = "9.9.7"\n', bom + b'version = "9.9.8"\n')
        proc = self.cortex(root, "upgrade", "--team", "9.9.8", "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertEqual(self.git(where, "log", "-1", "--format=%s").stdout.strip(), "move")
        self.assertEqual(sorted(self.git(where, "show", "--name-only", "--format=", "HEAD").stdout.split()),
                         ["agents/cortex.team.lock", TEAM])
        self.assert_ok(self.cortex(root, "sync", "--locked", **env))

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    def test_the_sync_leaves_the_team_s_index_as_it_was(self):
        # Asking git whether the team's file goes with its lock rewrites nothing in the team's repository: a status
        # that refreshes the index would write it, under its lock, where another git may be at work.
        root, where, env = self.team_committed("index", b'version = "9.9.8"\n', b'version = "9.9.8"\n',
                                               own_repository=True)
        os.utime(root / TEAM, (1_000_000_000, 1_000_000_000))       # the same content, another time: stale stat
        index = (where / ".git" / "index").read_bytes()
        proc = self.cortex(root, "sync", **env)
        self.assert_ok(proc)
        self.assertIn("agents/cortex.team.lock: written — commit it in agents/\n", proc.out)
        self.assertEqual((where / ".git" / "index").read_bytes(), index)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    @harness.outside_a_repository
    def test_the_lock_s_note_names_the_team_s_file_where_git_cannot_tell(self):
        # agents/ in a repository git refuses — its .git leads nowhere, or another user owns it: whether the team's
        # file was moved by hand cannot be told, and the note names it, if so.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.project('theme = "h2g2"\n', team='version = "9.9.8"\n', committed=False)
        (root / "agents" / ".git").write_text(f"gitdir: {self.tmp / 'gone'}\n", encoding="utf-8")
        proc = self.cortex(root, "sync", **env)
        self.assert_ok(proc)
        self.assertIn("agents/cortex.team.lock: written — commit it in agents/, with cortex.team.toml if you moved "
                      "it\n", proc.out)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    @harness.outside_a_repository
    def test_team_in_no_work_tree_says_its_files_are_committed_nowhere(self):
        # agents/ in no git work tree: --team writes its files, which nothing commits — never told to commit them.
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        for args in ((), ("--commit", "--message", "m")):
            with self.subTest(args=args):
                root = self.project('theme = "h2g2"\n', team='version = "9.9.7"\n', committed=False,
                                    name=f"nowhere{len(args)}")
                proc = self.cortex(root, "upgrade", "--team", "9.9.8", *args, **env)
                self.assert_ok(proc)
                self.assertNotIn("Commit ", proc.out)
                self.assertIn("agents/cortex.team.toml and agents/cortex.team.lock — in no git work tree, so committed "
                              "nowhere", proc.out)

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
    @harness.outside_a_repository
    def test_team_beside_a_team_s_file_that_is_no_file_says_it_cannot_be_read(self):
        # A committed pin before 1.8.0, which leaves the team's file unread: --team names the file there and unread,
        # never "no agents/cortex.team.toml" — the team need not create it.
        for kind, said in (("directory", "not a file"), ("nowhere", "a link that leads nowhere")):
            with self.subTest(kind=kind):
                root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', name=kind)
                (root / "agents").mkdir()
                if kind == "directory":
                    (root / TEAM).mkdir()
                else:
                    (root / TEAM).symlink_to(self.tmp / "gone.toml")
                proc = self.cortex(root, "upgrade", "--team", "9.9.8")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"{TEAM}: cannot be read — {said}", proc.err)
                self.assertNotIn(f"no {TEAM}", proc.err)
                self.assertIn("Nothing was changed", proc.err)

    UNREAD = (("a directory", None, f"{TEAM}: cannot be read — not a file"),
              ("garbled", 'version = 9.9.8"\n', f"{TEAM}: Expected newline or end of document"))

    def unread_team(self, root, garbled):
        (root / "agents").mkdir()
        if garbled is None:
            (root / TEAM).mkdir()
        else:
            (root / TEAM).write_text(garbled, encoding="utf-8")

    @SOURCE
    @harness.outside_a_repository
    def test_a_version_too_new_beside_a_team_s_file_that_does_not_read_names_no_move(self):
        # Bluspark's fourth pass: each move refuses that file first — the try dropped, a pin moved, a project moved
        # down — so a refusal of a version newer than this cortex names none of them, only the file to fix.
        for kind, garbled, error in self.UNREAD:
            for case, toml, local, newer in (
                    ("try", 'version = "9.9.8"\ntheme = "h2g2"\n', 'version = "9.9.10"\n', "cortex.local.toml"),
                    ("cortex.toml", 'version = "9.9.10"\ntheme = "h2g2"\n', None, "cortex.toml"),
                    ("dev", 'version = "9.9.8"\ntheme = "h2g2"\n', 'version = "9.9.10-dev.3"\n', "cortex.local.toml")):
                with self.subTest(kind=kind, case=case):
                    root = self.project(toml, local=local, committed=False, name=f"{case}-{kind}".replace(" ", "-"))
                    self.unread_team(root, garbled)
                    for args in (("upgrade",), ("sync",), ("validate",), ("init", "--tool", "claude"),
                                 ("config", "version")):
                        proc = self.cortex(root, *args)
                        said = proc.out + proc.err
                        self.assertEqual(proc.returncode, {"validate": 2, "config": 0}.get(args[0], 1), said)
                        if args[0] != "config" or case == "dev":   # a release's version: config names its update alone
                            self.assertIn("fix or restore the team's file: no command reads the project while it "
                                          f"does not — {error}", said.replace("Fix or restore", "fix or restore"))
                        if args[0] != "config":
                            self.assertIn(f"which its {newer} pins", said)
                        for move in ("--local", "--team", "cortex upgrade 9", "cortex upgrade VERSION"):
                            self.assertNotIn(move, said)
                    proc = self.cortex(root, "upgrade")
                    self.assertIn(". Then run `cortex upgrade` again", proc.err)

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_try_too_new_beside_a_team_s_file_that_does_not_read_is_moved_once_it_reads(self):
        # Each command named is followed: the move named before the fix fails on the file, the fix then the run named
        # gives the moves — and each of those does what it says.
        for kind, garbled, error in self.UNREAD:
            with self.subTest(kind=kind):
                root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', local='version = "9.9.10"\n',
                                    committed=False, name=kind.replace(" ", "-"))
                self.unread_team(root, garbled)
                for args in (("upgrade", "--local", "9.9.8"), ("upgrade", "--local", OWN), ("upgrade", "9.9.8")):
                    proc = self.cortex(root, *args)                         # what used to be named: it never worked
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn(error, proc.err)
                if garbled is None:
                    (root / TEAM).rmdir()
                (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")             # the fix, followed
                proc = self.cortex(root, "upgrade")                                             # then the run named
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("`cortex upgrade --local VERSION` moves the try — to the team's version or below, it "
                              "drops it", proc.err)
                self.assert_ok(self.cortex(root, "upgrade", "--local", OWN))                     # moves the try
                self.assertEqual(self.local(root)["version"], OWN)
                (root / "cortex.local.toml").write_text('version = "9.9.10"\n', encoding="utf-8")
                proc = self.cortex(root, "sync")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("\nor drop the try: cortex upgrade --local 9.9.8", proc.err)
                proc = self.cortex(root, "upgrade", "--local", "9.9.8")                           # drops it
                self.assert_ok(proc)
                self.assertNotIn("version", self.local(root))
                self.assertEqual(self.value(root, "version"), "9.9.8")

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_build_of_no_release_pinned_beside_a_team_s_file_that_is_no_file_names_its_fix_then_the_hand(self):
        # Bluspark's fourth pass, of #338: this developer's cortex.toml, beside a team's file that is a directory or a
        # link that leads nowhere — the fix of that file is named first; fixed, the file reads, and the remedy named
        # by hand, followed, syncs. What it does not pin: loose_team_file's team_file_there over isfile — every caller
        # asks team_refusal first, so a file there and unread never reaches it (an equivalent mutant).
        HAND = ("pin a release by hand: set cortex.toml's version to one, or remove it to read the team's — "
                "uncommitted beside the team's file, it is this developer's, which no command writes")
        for kind, follow in (("a directory", 'version = "9.9.8"\ntheme = "h2g2"\n'), ("a link", 'theme = "h2g2"\n')):
            with self.subTest(kind=kind):
                root = self.project('version = "9.9.10-dev.3"\ntheme = "h2g2"\n', committed=False,
                                    name=kind.replace(" ", "-"))
                (root / "agents").mkdir()
                if kind == "a directory":
                    (root / TEAM).mkdir()
                else:
                    (root / TEAM).symlink_to(self.tmp / "gone.toml")
                for args in (("sync",), ("upgrade",)):
                    proc = self.cortex(root, *args)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn("fix or restore the team's file", proc.err.replace("Fix", "fix"))
                    self.assertNotIn("cortex upgrade VERSION", proc.err)
                (root / TEAM).rmdir() if kind == "a directory" else (root / TEAM).unlink()
                (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")        # fixed
                for args in (("sync",), ("upgrade",)):
                    proc = self.cortex(root, *args)
                    self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                    self.assertIn(HAND, proc.err)
                (root / "cortex.toml").write_text(follow, encoding="utf-8")              # followed, by hand
                self.assert_ok(self.cortex(root, "sync"))
                self.assertEqual(self.value(root, "version"), "9.9.8")

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_try_removed_at_a_tie_with_this_developer_s_cortex_toml_says_the_team_s_version_is_read(self):
        # cortex.toml, not committed, at the team's version: the try removed, the team's is read — on a tie the team's
        # wins (ADR-013 §3.1), and the message says so.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team='version = "9.9.8"\n',
                            local=f'version = "{OWN}"\n', committed=False)
        for dry in (("--dry-run",), ()):
            proc = self.cortex(root, "upgrade", "--local", "9.9.8", *dry)
            self.assert_ok(proc)
            self.assertIn(f"cortex.local.toml: version {OWN} {'would be' if dry else 'is'} removed — the team's 9.9.8 "
                          f"({TEAM}) is read\n", proc.out)
        self.assertNotIn("version", self.local(root))
        self.assertEqual(self.value(root, "version"), "9.9.8")

class FourthCodeReviewTests(TeamTestCase):
    """What the fourth counter-review of the 1.8.0 stack's code found (2026-11-12)."""

    def cortex_within(self, cwd, *args, seconds=30, **env):
        """``cortex ARGS``, failed — never waited on — when it does not end within ``seconds``."""
        try:
            proc = subprocess.run(harness.command(*args), cwd=cwd, env={**self.env, "PWD": str(cwd), **env},
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=seconds)
        except subprocess.TimeoutExpired:
            self.fail(f"cortex {' '.join(args)} did not end: it waits on a named pipe")
        proc.out, proc.err = proc.stdout.decode(), proc.stderr.decode()
        return proc

    @SOURCE
    @DOWNLOADS
    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipe here")
    @harness.outside_a_repository
    def test_a_named_pipe_at_the_team_s_file_is_refused_never_waited_on(self):
        # A named pipe where the team's file goes: what reads the versions first — the gate — opened it as a file,
        # and waited for a writer, every command with it. Refused as no file, as a directory is; beside a committed
        # pin before 1.8.0, which leaves the team's file unread, the project is served as 1.7.0 served it.
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', committed=False, name="read")
        (root / "agents").mkdir()
        os.mkfifo(root / TEAM)
        for args in ThirdCodeReviewTests.REFUSED + (("upgrade", "9.9.8"),):
            with self.subTest(args=args):
                proc = self.cortex_within(root, *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn(f"{TEAM}: cannot be read — not a file", proc.err)
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', name="unread")
        (root / "agents").mkdir()
        os.mkfifo(root / TEAM)
        proc = self.cortex_within(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"{TEAM} is not read: the team's version is 1.7.0, before 1.8.0", proc.err)
        self.assertEqual(self.cortex_within(root, "config", "version").out.strip(), "1.7.0")

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
    @harness.outside_a_repository
    def test_a_fresh_init_beside_a_team_s_file_that_cannot_be_read_names_no_conflict(self):
        # No conflict to fix in what cannot be read — a directory, a link that leads nowhere, a named pipe: the
        # remedy says to make it a file, which, followed, lets init run.
        kinds = (("directory", "not a file"), ("nowhere", "a link that leads nowhere")) + \
            ((("pipe", "not a file"),) if hasattr(os, "mkfifo") else ())
        for kind, said in kinds:
            with self.subTest(kind=kind):
                root = self.tmp / f"fresh-{kind}"
                (root / "agents").mkdir(parents=True)
                if kind == "directory":
                    (root / TEAM).mkdir()
                elif kind == "nowhere":
                    (root / TEAM).symlink_to(self.tmp / "gone.toml")
                else:
                    os.mkfifo(root / TEAM)
                proc = self.cortex_within(root, "init", "--tool", "claude")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"{TEAM}: cannot be read — {said}", proc.err)
                self.assertIn(f"cortex init reads {TEAM}: fix it — make it a file you can read — then run cortex init "
                              "again", proc.err)
                self.assertNotIn("conflict", proc.err)
                if kind == "directory":
                    (root / TEAM).rmdir()
                else:
                    (root / TEAM).unlink()
                (root / TEAM).write_text(f'version = "{OWN}"\n', encoding="utf-8")
                self.assert_ok(self.cortex_within(root, "init", "--tool", "claude"))

    @SOURCE
    @unittest.skipIf(os.name == "nt", "a link needs a privilege on Windows")
    @harness.outside_a_repository
    def test_a_team_s_folder_that_is_a_loop_of_links_is_never_said_missing(self):
        # A cortex.toml without version, beside agents/ that is a loop of links — the team's file there, unread —
        # and a cortex.local.toml that does not read: the local file's error, never "agents/ is not there", with
        # its submodule remedy; fixed, the team's file's own error.
        root = self.project('theme = "h2g2"\n', local="bogus = 1\n", committed=False)
        (root / "agents").symlink_to(root / "others")
        (root / "others").symlink_to(root / "agents")
        for args in (("sync",), ("validate",), ("upgrade", "9.9.8"), ("init", "--tool", "claude")):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn('cortex.local.toml: unknown key "bogus"', proc.err)
                self.assertNotIn("agents/ is not there", proc.err)
        (root / "cortex.local.toml").write_text("", encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"{TEAM}: cannot be read — ", proc.err)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipe here")
    @harness.outside_a_repository
    def test_a_named_pipe_at_cortex_local_toml_holds_no_command_of_the_machine(self):
        # The registry's commands read a cortex.local.toml left without its cortex.toml, and prune one beside a
        # project this cortex refuses: opened as a file, a named pipe there waited for a writer, and the command with
        # it. Read without blocking, it is a file that cannot be read: the project is unreadable, never gone.
        env = {"CORTEX_NO_REGISTER": ""}
        left = self.project('version = "9.9.8"\ntheme = "h2g2"\n', name="left")
        self.assert_ok(self.cortex_within(left, "project", "add", **env))
        (left / "cortex.toml").unlink()                 # a branch without Cortex checked out
        os.mkfifo(left / "cortex.local.toml")
        for args in (("project", "list"), ("prune", "--dry-run"), ("upgrade", "--all", "--dry-run")):
            with self.subTest(args=args):
                proc = self.cortex_within(self.tmp, *args, **env)
                self.assertNotIn("Traceback", proc.err)
        listed = self.cortex_within(self.tmp, "project", "list", **env)
        self.assertIn("unreadable", listed.out + listed.err)
        refused = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team="version = \n", name="refused")
        os.mkfifo(refused / "cortex.local.toml")
        proc = self.cortex_within(self.tmp, "prune", "--dry-run", str(refused), **env)
        self.assertNotIn("Traceback", proc.err)


class FourthCounterReviewTests(TeamTestCase):
    """What the counter-review of the fourth pass's fixes found (2026-11-12): every remedy of a version too new,
    followed, does what it says — beside a team's file the grammar refuses, a try no --local moves, a team's file in
    a renamed folder."""

    UPDATE = "cortex self-update 9.9.10"

    def refused(self, root, *runs):
        """Each of ``runs`` — ``sync``, ``upgrade``, ``validate``, ``init`` — refused, its output."""
        said = {}
        for args in runs or (("sync",), ("upgrade",), ("validate",), ("init", "--tool", "claude")):
            proc = self.cortex(root, *args)
            self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
            self.assertNotIn("Traceback", proc.err)
            said[args[0]] = proc.out + proc.err
        return said

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_try_beside_a_committed_pin_before_1_8_0_is_dropped_by_hand_whatever_the_team_s_file(self):
        # A committed pin before 1.8.0 makes the team's version cortex.toml's, which reads nothing of the team's file:
        # never "fix the team's file". It reads no try either: --local is refused at every move — the try is removed
        # by hand, which, followed, syncs.
        for kind, team in (("a directory", None), ("garbled", 'version = 9.9.8"\n'), ("a pin", 'version = "9.9.8"\n')):
            with self.subTest(kind=kind):
                root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', local='version = "9.9.10"\n',
                                    name=kind.replace(" ", "-"))
                (root / "agents").mkdir()
                (root / TEAM).mkdir() if team is None else (root / TEAM).write_text(team, encoding="utf-8")
                for run, said in self.refused(root).items():
                    self.assertNotIn("fix or restore the team's file", said.replace("Fix", "fix"), run)
                    self.assertNotIn("--local", said, run)
                    self.assertIn("drop the try by hand: remove `version` from cortex.local.toml — the team's version, "
                                  "1.7.0, is before 1.8.0, which reads none", said, run)
                    if run != "upgrade":                                # beside the update, another way
                        self.assertIn("\nor drop the try by hand", said, run)
                proc = self.cortex(root, "upgrade", "--local", "1.7.0")             # what used to be named
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn('"version" is read once the team\'s version is 1.8.0 or later', proc.err)
                (root / "cortex.local.toml").write_text("", encoding="utf-8")       # followed, by hand
                self.assert_ok(self.cortex(root, "sync"))
                self.assertEqual(self.value(root, "version"), "1.7.0")
        # Not committed, beside a team's file that pins none: the team's version is cortex.toml's too, which reads
        # nothing of that file — a key this cortex does not know in it included.
        root = self.project('version = "1.7.0"\ntheme = "h2g2"\n', team="claude_acces = false\n",
                            local='version = "9.9.10"\n', committed=False, name="pinless")
        for run, said in self.refused(root).items():
            self.assertNotIn("claude_acces", said, run)
            self.assertIn("drop the try by hand: remove `version` from cortex.local.toml", said, run)
        (root / "cortex.local.toml").write_text("", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        self.assertEqual(self.value(root, "version"), "1.7.0")

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_team_s_file_the_grammar_refuses_names_its_fix_or_the_update_never_a_move(self):
        # A team's file that parses but that this cortex refuses: no move works before it is fixed — or, a key this
        # cortex does not know, before a release that knows it serves the project: the update alone.
        for kind, team, error, later in (
                ("before 1.8.0", 'version = "1.7.0"\n', f'{TEAM}: version "1.7.0" is before 1.8.0', False),
                ("version locked", 'version = "9.9.8"\nlocked = ["version"]\n', f'{TEAM}: "version" is never locked',
                 False),
                ("unknown key", 'version = "9.9.8"\nclaude_acces = false\n', f'{TEAM}: unknown key "claude_acces"', True)):
            with self.subTest(kind=kind):
                root = self.project('version = "9.9.8"\ntheme = "h2g2"\n', team=team, local='version = "9.9.10"\n',
                                    committed=False, name=kind.replace(" ", "-"))
                for run, said in self.refused(root).items():
                    for move in ("--local", "--team", "cortex upgrade 9", "cortex upgrade VERSION"):
                        self.assertNotIn(move, said, run)
                    if later or run != "upgrade":       # cortex upgrade names the fix alone, then itself again
                        self.assertIn(self.UPDATE, said, run)
                    if later:
                        self.assertNotIn("fix or restore", said.replace("Fix", "fix"), run)
                    else:
                        self.assertIn(f"fix or restore the team's file: no command reads the project while it does "
                                      f"not — {error}", said.replace("Fix", "fix"), run)
                proc = self.cortex(root, "upgrade", "--local", "9.9.8")             # what used to be named
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(error, proc.err)
                if later:
                    continue                    # the update named is a release this suite cannot serve
                (root / TEAM).write_text('version = "9.9.8"\n', encoding="utf-8")   # the fix, followed
                proc = self.cortex(root, "sync")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("\nor drop the try: cortex upgrade --local 9.9.8", proc.err)
                self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.8"))    # drops it
                self.assertEqual(self.value(root, "version"), "9.9.8")
        # The team's pin too new, beside a key this cortex does not know: no --team, which refuses the key first.
        root = self.project('theme = "h2g2"\n', team='version = "9.9.10"\nclaude_acces = false\n', name="team-pin")
        for run, said in self.refused(root).items():
            self.assertNotIn("--team", said, run)
            self.assertIn(self.UPDATE, said, run)
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn('unknown key "claude_acces"', proc.err)

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_a_try_beside_an_agents_every_command_refuses_names_that_refusal_never_local(self):
        # cortex.toml's agents refused — not committed, or naming a folder that holds a project's cortex.toml: every
        # command refuses it first, --local included. Its own remedy is named; followed, the try moves as named then.
        for case, committed, toml in (
                ("not committed", False, 'version = "9.9.8"\ntheme = "h2g2"\nagents = "team"\n'),
                ("a project", True, 'version = "9.9.8"\ntheme = "h2g2"\nagents = "sub"\n')):
            with self.subTest(case=case):
                root = self.project(toml, local='version = "9.9.10"\n', committed=committed, name=case.replace(" ", "-"))
                if case == "a project":
                    (root / "sub").mkdir()
                    (root / "sub" / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
                    error = 'agents "sub" holds a project\'s cortex.toml'
                else:
                    (root / "team").mkdir()
                    (root / "team" / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
                    error = '"agents" is read from a committed cortex.toml only'
                for run, said in self.refused(root).items():
                    self.assertNotIn("--local", said, run)
                    self.assertIn("no command moves the try while cortex.toml is refused — ", said, run)
                    self.assertNotIn("or no command", said, run)        # no other way than that fix
                    self.assertIn(error, said, run)
                proc = self.cortex(root, "upgrade", "--local", "9.9.8")             # what used to be named
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(error, proc.err)
                (root / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")  # `agents`
                proc = self.cortex(root, "sync")                                                          # removed
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn("\nor drop the try: cortex upgrade --local 9.9.8", proc.err)
                self.assert_ok(self.cortex(root, "upgrade", "--local", "9.9.8"))
                self.assertEqual(self.value(root, "version"), "9.9.8")
        # A folder this cortex does not take by its name, .team, which a later release may: the update alone.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\nagents = ".team"\n', local='version = "9.9.10"\n',
                            name="dot-team")
        (root / ".team").mkdir()
        (root / ".team" / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        for run, said in self.refused(root).items():
            self.assertNotIn("--local", said, run)
            self.assertIn(FourthCounterReviewTests.UPDATE, said, run)
            self.assertIn("no command of this cortex moves the try: it does not take the team's folder .team/, which a "
                          "later release may", said, run)
        proc = self.cortex(root, "upgrade", "--local", "9.9.8")                     # what used to be named
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn('agents ".team"', proc.err)

    @SOURCE
    @DOWNLOADS
    @harness.outside_a_repository
    def test_this_developer_s_cortex_toml_too_new_beside_the_team_s_file_is_moved_down_by_hand(self):
        # cortex.toml, not committed, beside a team's file: this developer's, which no command writes — a plain
        # upgrade VERSION follows the team's file and refuses. The move down named is by hand, and each edit,
        # followed, syncs.
        for number, (team, edit, read) in enumerate((
                ('version = "9.9.8"\n', 'version = "9.9.7"\ntheme = "h2g2"\n', "9.9.8"),     # the higher is read
                ('version = "9.9.8"\n', 'theme = "h2g2"\n', "9.9.8"),
                ('theme = "h2g2"\n', 'version = "9.9.7"\ntheme = "h2g2"\n', "9.9.7"))):
            with self.subTest(team=team, edit=edit):
                root = self.project('version = "9.9.10"\ntheme = "h2g2"\n', team=team, committed=False,
                                    name=f"mine-{number}")
                proc = self.cortex(root, "upgrade")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertNotIn("cortex upgrade 9", proc.err)
                self.assertIn("or move it down by hand: set cortex.toml's version to one this cortex serves", proc.err)
                self.assertEqual("version" in team, ", or remove it to read the team's" in proc.err)
                proc = self.cortex(root, "upgrade", OWN)                                    # what used to be named
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                (root / "cortex.toml").write_text(edit, encoding="utf-8")                  # followed, by hand
                self.assert_ok(self.cortex(root, "sync"))
                self.assertEqual(self.value(root, "version"), read)

    @SOURCE
    @harness.outside_a_repository
    def test_a_team_s_file_that_does_not_read_in_a_renamed_folder_is_named_there(self):
        # The team's folder renamed by a committed cortex.toml: the file to fix is named in it — read in agents/, the
        # file would not be there, and --local would be named, which refuses it.
        root = self.project('version = "9.9.8"\ntheme = "h2g2"\nagents = "team"\n', local='version = "9.9.10"\n')
        (root / "team").mkdir()
        (root / "team" / "cortex.team.toml").write_text('version = 9.9.8"\n', encoding="utf-8")
        for run, said in self.refused(root).items():
            self.assertIn("fix or restore the team's file: no command reads the project while it does not — "
                          "team/cortex.team.toml: ", said.replace("Fix", "fix"), run)
            self.assertNotIn(TEAM, said, run)
            self.assertNotIn("--local", said, run)


def newer_refused(pin):
    """What --team with no VERSION says of a team's pin newer than this cortex."""
    return (f"this cortex is a development build, {OWN}, the version its branch prepares" if DEV_OWN else
            f"{TEAM} pins Cortex {pin}, newer than this cortex, {OWN}")


def harness_path(root):
    return str(root).replace(os.sep, "/")


if __name__ == "__main__":
    unittest.main()
