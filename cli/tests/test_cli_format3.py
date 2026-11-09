"""The bootstrap of format 3 — ADR-013 phase 2b (#335), §3.10.

Both templates read the team's file and compare each file's ``version`` with what ``cortex sync``
recorded in ``synced``. The developer's own bootstrap — one git ignores, or in no repository —
follows the version this machine reads: ``cortex sync`` writes an untouched one again in a project
that has a team's file, ``cortex upgrade --local`` whenever it sets a try, and below 1.8.0 an
untouched format 3 one goes back to its version's format. The team's bootstrap follows the team's
version: ``cortex sync`` and ``cortex validate`` say when an untouched one is behind it.
"""

import errno
import io
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))

import cli_harness as harness  # noqa: E402
from cortex_cli import config, init, store, upgrade  # noqa: E402
from test_cli_team import DOWNLOADS, OWN, TEAM, TeamTestCase  # noqa: E402

TEMPLATE = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes()
WORKSPACE = (harness.REPO / "templates" / "bootstrap-instructions-workspace.md").read_bytes()
# A template of format 2 with its end marker — what 1.7.0 writes, and the fixture's 1.7.0 and 9.9.8.
FORMAT_2 = TEMPLATE.replace(b"format=3", b"format=2").replace(b"## Where the spec is", b"## Where the spec is, in 2", 1)
OURS = b"\n## Ours\n\nRun the linter first.\n"


def written(template):
    return init.stamped(init.instructions(template, True))


def remove_store(path):
    """Remove a version of the store, read-only as the store writes it (ADR-008 §3.3): writable first — a user
    who is not root, and Windows, remove no read-only file."""
    for directory, _, files in os.walk(path):
        os.chmod(directory, 0o700)
        for name in files:
            os.chmod(os.path.join(directory, name), 0o600)
    shutil.rmtree(path)


class TemplateTests(unittest.TestCase):
    def test_both_templates_are_format_3_and_read_the_team_s_file(self):
        for name, text in (("single", TEMPLATE), ("workspace", WORKSPACE)):
            with self.subTest(template=name):
                self.assertEqual(init.markers(text), [(name, 3)])
                self.assertIsNotNone(init.END_MARKER.search(text))
                for rule in (b"**The team's folder** is the folder `agents` names in `cortex.toml`, else `agents/`",
                             b"`cortex.team.toml`", b"**A version moved**", b"never order versions",
                             b"**A checkout**", b"**No `synced`**",
                             b"below 1.8.0, read as above \xe2\x80\x94 the numbers before any `-` \xe2\x80\x94 run `cortex self-update`",
                             # A pre-release or a development build of 1.8.0 is 1.8.0 (ADR-009), and each
                             # self-update is followed by one sync only: no loop when nothing newer is out.
                             b"read the numbers before any `-`: `1.7.3` is below it, `1.8.0-rc.1` and `1.8.0-dev.2` "
                             b"are not", b"run `cortex self-update`, then `cortex sync`, once each. No `synced` still",
                             b"then `cortex sync` again, once; when it still says so, tell the user, and go no further",
                             b"`cortex sync`, once each; otherwise, or when it still refuses, tell the user",
                             b"**It names a missing team's folder**",
                             b"the team's file's own when its `locked` lists `theme`",
                             b"in `cortex.local.toml` or in the team's file, of your own accord"):
                    self.assertIn(rule, text)
                self.assertNotIn(b"**The spec is of a version other than", text)
                # A checkout is said before a version moved, a missing team's folder before a missing version.
                self.assertLess(text.index(b"**A checkout**"), text.index(b"**A version moved**"))
                self.assertLess(text.index(b"**It names a missing team's folder**"), text.index(b"**It refuses"))
                self.assertIn(b"**No `synced`** in `cortex.local.toml`, and `cortex/` a link \xe2\x86\x92 nothing to "
                              b"compare", text)


@DOWNLOADS
class OwnBootstrapTests(TeamTestCase):
    """The developer's own bootstrap follows the version this machine reads (§3.10)."""

    def setUp(self):
        super().setUp()
        self.release.replace_spec("1.7.0", {"templates/bootstrap-instructions.md": FORMAT_2})
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": FORMAT_2})

    def test_sync_writes_format_3_back_over_the_developer_s_own_file_only(self):
        # What 1.7.0's own upgrade left — its format 2, the team's part kept — over the developer's own file.
        for case, committed, ignored in (("no repository", False, False), ("ignored", True, True),
                                          ("the team's", True, False)):
            with self.subTest(case=case):
                root = self.project(team=f'version = "{OWN}"\n', committed=committed, name=case.replace(" ", "-"))
                if ignored:
                    (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
                (root / "CLAUDE.md").write_bytes(written(FORMAT_2) + OURS)
                proc = self.cortex(root, "sync")
                self.assert_ok(proc)
                if case == "the team's":
                    self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
                    self.assertIn(f"CLAUDE.md is behind: Cortex {OWN}, the team's version, writes another — "
                                  f"`cortex upgrade {OWN}` writes it again", proc.err)
                else:
                    self.assertEqual((root / "CLAUDE.md").read_bytes(), written(TEMPLATE) + OURS)
                    self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — your own bootstrap follows "
                                  "the version this machine reads", proc.out)
                    self.assert_ok(again := self.cortex(root, "sync"))
                    self.assertNotIn("written again", again.out)            # in step: nothing more

    def test_without_a_team_s_file_sync_writes_no_bootstrap(self):
        root = self.project(toml='version = "9.9.8"\ntheme = "h2g2"\n', committed=False)
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))
        self.assertNotIn("written again", proc.out)

    def test_a_1_7_0_project_s_bootstrap_is_left_as_it_is(self):
        root = self.project(toml='version = "1.7.0"\ntheme = "h2g2"\n', committed=False)
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_a_sync_below_1_8_0_writes_an_untouched_format_3_file_in_its_version_s_format(self):
        root = self.project(toml='version = "1.7.0"\ntheme = "h2g2"\n', committed=False)
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE) + OURS)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
        changed = written(TEMPLATE).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(changed)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is of format 3, which Cortex 1.8.0 or later reads, and not what Cortex wrote — so it is "
                      "kept", proc.err)

    def test_a_move_below_1_8_0_writes_the_developer_s_own_format_3_bootstrap_in_the_target_s_format(self):
        # 1.7.0's own upgrade does the same to an untouched format 3 file: its format 2, the team's part kept.
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE) + OURS)
        proc = self.cortex(root, "upgrade", "1.7.0")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
        root = self.project(toml=f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False, name="changed")
        self.assert_ok(self.cortex(root, "sync"))
        changed = written(TEMPLATE).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(changed)
        proc = self.cortex(root, "upgrade", "1.7.0")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is of format 3, which Cortex 1.8.0 or later reads, and not what Cortex wrote", proc.err)

    def test_upgrade_local_writes_the_developer_s_own_bootstrap_from_the_try_and_back(self):
        root = self.project(toml='version = "9.9.8"\ntheme = "h2g2"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2) + OURS)
        proc = self.cortex(root, "upgrade", "--local", OWN)
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(TEMPLATE) + OURS)
        proc = self.cortex(root, "upgrade", "--local", "9.9.8")            # the try removed: the team's again
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)

    def test_a_file_that_cannot_be_read_is_left_alone(self):
        real = Path.read_bytes

        def read_bytes(path):
            if path.name == "CLAUDE.md":
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)

        for committed in (False, True):                                    # the developer's own, the team's
            with self.subTest(committed=committed):
                root = self.project(team=f'version = "{OWN}"\n', committed=committed, name=f"unread-{committed}")
                self.assert_ok(self.cortex(root, "sync"))
                (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}), \
                        mock.patch.object(Path, "read_bytes", read_bytes):
                    project = config.load(str(root))
                    upgrade.own_bootstraps(str(root), project, store.Store(), out, err)
                    self.assertEqual(upgrade.behind(str(root), project, None, fetch=False), [])
                self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
                self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))

    def test_a_folder_this_user_cannot_enter_holds_no_bootstrap(self):
        # Path.is_file raises on EACCES: `cortex sync` and `cortex validate` would fail where 1.7.0 looked at nothing.
        real = os.stat

        def stat(path, *args, **kwargs):
            if f"{os.sep}.github" in os.fspath(path):
                raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
            return real(path, *args, **kwargs)

        for name, toml, team in (("old", 'version = "1.7.0"\ntheme = "h2g2"\n', None),
                                 ("new", 'theme = "h2g2"\n', f'version = "{OWN}"\n')):
            with self.subTest(project=name):
                root = self.project(toml=toml, team=team, committed=False, name=f"eacces-{name}")
                self.assert_ok(self.cortex(root, "sync"))
                (root / ".github").mkdir()
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
                    project = config.load(str(root))
                    with mock.patch("os.stat", stat):
                        upgrade.own_bootstraps(str(root), project, store.Store(), out, err)
                        self.assertEqual(upgrade.behind(str(root), project, None, fetch=False), [])
                self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_changed_own_bootstrap_is_said_kept_when_its_version_writes_another(self):
        root = self.project(team=f'version = "{OWN}"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))
        stale = written(FORMAT_2).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(stale)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), stale)
        self.assertIn(f"note: CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is "
                      f"kept, and Cortex {OWN}'s template is not written: cortex init --force writes it again, and keeps "
                      "yours as .bak — your own rules go below its end marker", proc.err)
        # Changed after Cortex wrote this version's: nothing new to say.
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("CLAUDE.md", proc.out + proc.err)

    def test_sync_from_a_checkout_writes_no_bootstrap(self):
        root = self.project(team=f'version = "{OWN}"\n', committed=False)
        self.assert_ok(self.cortex(root, "sync"))                          # the version in the store: it could
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root, "sync", "--link", "--from", str(harness.REPO))
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))

    def test_the_adoption_path_brings_a_teammate_on_1_7_0_to_the_team_s_version(self):
        # ADR-013 §5: at a 2.B root, the team's move announced; each teammate removes version and theme.
        root = self.project(toml='version = "1.7.0"\ntheme = "none"\n', committed=False,
                            team=f'version = "{OWN}"\ntheme = "h2g2"\n')
        (root / "agents" / "project-context.md").write_text("Cortex moves to the team's file: remove version and "
                                                            "theme from cortex.toml, then cortex sync.\n")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertEqual(self.value(root, "version"), OWN)
        self.assertIn("1.7.0", proc.err)                                   # cortex.toml's version: remove it
        self.assertEqual(self.value(root, "theme"), "none")                # cortex.toml's theme still wins
        (root / "cortex.toml").write_text("", encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("1.7.0", proc.err)
        self.assertEqual((self.value(root, "version"), self.value(root, "theme")), (OWN, "h2g2"))
        self.assertEqual(self.local(root)["synced"]["pinned"], OWN)


@DOWNLOADS
class TryTests(TeamTestCase):
    """During a try above the team's version, a move, a resume and init leave the developer's own bootstrap
    to the try: written from the version this machine reads, and never named among what the team commits."""

    def setUp(self):
        super().setUp()
        self.f97 = FORMAT_2.replace(b"in 2", b"in 2, of 9.9.7", 1)
        for version, template in (("9.9.7", self.f97), ("9.9.8", FORMAT_2)):
            self.release.replace_spec(version, {"templates/bootstrap-instructions.md": template,
                                                "templates/project-overview.md.template": b"# Overview\n",
                                                "templates/project-context.md.template": b"# Context\n"})

    def tried(self, name, toml='version = "9.9.7"\ntheme = "h2g2"\n', team=None, ignored=True):
        root = self.project(toml=toml, team=team, local=f'version = "{OWN}"\n', name=name)
        if ignored:
            (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        return root

    def assert_own(self, root, proc, before):
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(TEMPLATE) + OURS)
        self.assertNotRegex(proc.out, r"Commit [^\n]*CLAUDE\.md|CLAUDE\.md in the project")
        if before != written(TEMPLATE) + OURS:
            self.assertIn(f"CLAUDE.md: written again from Cortex {OWN}'s template — your own bootstrap follows the "
                          "version this machine reads", proc.out)
        else:
            self.assertNotIn("CLAUDE.md", proc.out)

    def test_a_move_leaves_the_developer_s_own_bootstrap_to_the_try(self):
        for before in (written(TEMPLATE) + OURS, written(self.f97) + OURS):
            with self.subTest(before=before[:60]):
                root = self.tried(f"move-{len(before)}")
                (root / "CLAUDE.md").write_bytes(before)
                self.assert_own(root, self.cortex(root, "upgrade", "9.9.8"), before)
                self.assertIn('version = "9.9.8"', (root / "cortex.toml").read_text(encoding="utf-8"))

    def test_a_resume_leaves_the_developer_s_own_bootstrap_to_the_try(self):
        for before in (written(TEMPLATE) + OURS, written(FORMAT_2) + OURS):
            with self.subTest(before=before[:60]):
                root = self.tried(f"resume-{len(before)}", toml='version = "9.9.8"\ntheme = "h2g2"\n')
                (root / "CLAUDE.md").write_bytes(before)
                self.assert_own(root, self.cortex(root, "upgrade", "9.9.8"), before)

    def test_a_team_move_leaves_a_follower_s_own_bootstrap_to_the_try(self):
        for before in (written(TEMPLATE) + OURS, written(self.f97) + OURS):
            with self.subTest(before=before[:60]):
                root = self.tried(f"team-{len(before)}", toml='theme = "h2g2"\n', team='version = "9.9.7"\n')
                (root / "CLAUDE.md").write_bytes(before)
                self.assert_own(root, self.cortex(root, "upgrade", "--team", "9.9.8"), before)
                self.assertIn('version = "9.9.8"', (root / TEAM).read_text(encoding="utf-8"))

    def test_outside_a_try_the_developer_s_own_bootstrap_keeps_the_developer_s_theme(self):
        bare = (lambda template: init.stamped(init.instructions(template, False)))
        for case, toml, team, args, before in (
                ("move", 'version = "9.9.7"\ntheme = "h2g2"\n', None, ("9.9.8",), bare(self.f97)),
                ("resume", 'version = "9.9.8"\ntheme = "h2g2"\n', None, ("9.9.8",), written(FORMAT_2)),
                ("team", 'theme = "h2g2"\n', 'version = "9.9.7"\n', ("--team", "9.9.8"), bare(self.f97))):
            with self.subTest(case=case):
                root = self.project(toml=toml, team=team, local='theme = "none"\n', name=f"theme-{case}")
                (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
                self.assert_ok(self.cortex(root, "sync"))
                (root / "CLAUDE.md").write_bytes(before + OURS)
                proc = self.cortex(root, "upgrade", *args)
                self.assert_ok(proc)
                self.assertEqual((root / "CLAUDE.md").read_bytes(), bare(FORMAT_2) + OURS)
                self.assertNotRegex(proc.out, r"Commit [^\n]*CLAUDE\.md|CLAUDE\.md in the project")
                self.assert_ok(self.cortex(root, "sync"))                      # and no flip back
                self.assertEqual((root / "CLAUDE.md").read_bytes(), bare(FORMAT_2) + OURS)

    def test_a_dry_run_says_what_it_would_write_of_the_developer_s_own_bootstrap(self):
        for case, toml, before in (("move", 'version = "9.9.7"\ntheme = "h2g2"\n', written(self.f97)),
                                   ("resume", 'version = "9.9.8"\ntheme = "h2g2"\n', written(FORMAT_2))):
            with self.subTest(case=case):
                root = self.tried(f"dry-{case}", toml=toml)
                (root / "CLAUDE.md").write_bytes(before + OURS)
                proc = self.cortex(root, "upgrade", "9.9.8", "--dry-run")
                self.assert_ok(proc)
                self.assertIn(f"CLAUDE.md: would be written again from Cortex {OWN}'s template — your own bootstrap "
                              "follows the version this machine reads", proc.out)
                self.assertEqual((root / "CLAUDE.md").read_bytes(), before + OURS)

    def test_at_a_2b_root_team_names_no_own_file_to_commit(self):
        root = self.project(toml="", team='version = "9.9.7"\ntheme = "h2g2"\n', local='theme = "none"\n',
                            committed=False, name="2b")
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(self.f97))
        proc = self.cortex(root, "upgrade", "--team", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(FORMAT_2, False)))
        self.assertIn("\nCommit agents/cortex.team.toml and agents/cortex.team.lock in agents/; teammates run", proc.out)

    def test_init_writes_the_developer_s_own_bootstrap_in_the_theme_that_still_wins(self):
        # --theme, or --no-personality, under a local theme that still wins: the next sync must not flip it.
        for option, local, personality in ((("--theme", "h2g2"), "none", False), (("--no-personality",), "h2g2", True)):
            with self.subTest(option=option):
                root = self.project(toml="", team='version = "9.9.8"\ntheme = "h2g2"\n', local=f'theme = "{local}"\n',
                                    committed=False, name=f"flip-{local}")
                self.assert_ok(self.cortex(root, "sync"))
                self.assert_ok(self.cortex(root, "init", "--tool", "claude", "--force", *option))
                wanted = init.stamped(init.instructions(FORMAT_2, personality))
                self.assertEqual((root / "CLAUDE.md").read_bytes(), wanted)
                proc = self.cortex(root, "sync")
                self.assert_ok(proc)
                self.assertEqual((root / "CLAUDE.md").read_bytes(), wanted)

    def test_an_own_bootstrap_without_a_digest_is_known_in_the_team_s_theme(self):
        # Written by a Cortex before 1.8.0 in the team's theme; the developer's theme is none.
        root = self.project(toml='version = "9.9.7"\ntheme = "h2g2"\n', local='theme = "none"\n', name="nodigest")
        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(init.instructions(self.f97, True))
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(FORMAT_2, False)))

    def test_a_changed_own_bootstrap_is_said_changed_by_you(self):
        root = self.project(toml='version = "9.9.7"\ntheme = "h2g2"\n', name="yours")
        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        changed = written(self.f97).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(changed)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), changed)
        self.assertIn("CLAUDE.md is not what Cortex wrote, by the digest in its marker — you changed it — so it is kept",
                      proc.err)

    def test_a_resume_checks_the_developer_s_own_bootstrap_without_the_team_s_version(self):
        root = self.tried("own-only", toml='version = "9.9.8"\ntheme = "h2g2"\n')
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2) + OURS)
        self.assertFalse((self.home / "versions" / "9.9.8").exists())           # the try is what was synced
        # Without the lock of the team's version, which the sync leaves to it, the resume writes that lock: it needs
        # the version, and says it could not get it — nothing written (ADR-013 §3.4).
        proc = self.cortex(root, "upgrade", "9.9.8", CORTEX_RELEASES_URL="http://127.0.0.1:9")
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn("Cortex 9.9.8", proc.err)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
        self.assertFalse((root / "cortex.lock").exists())
        # The lock committed, as a team's project has it: the developer's own bootstrap needs no team's version.
        (root / "cortex.lock").write_text(config.render_lock(store.Version("9.9.8"), "sha256:" + "0" * 64),
                                          encoding="utf-8")
        proc = self.cortex(root, "upgrade", "9.9.8", CORTEX_RELEASES_URL="http://127.0.0.1:9")
        self.assert_ok(proc)
        self.assertNotIn("not in the store", proc.err)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(TEMPLATE) + OURS)

    def test_a_resume_names_the_try_s_version_it_cannot_find(self):
        root = self.project(toml='version = "9.9.7"\ntheme = "h2g2"\n', local='version = "9.9.8"\n', name="try-gone")
        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(self.f97))
        remove_store(self.home / "versions" / "9.9.8")
        proc = self.cortex(root, "upgrade", "9.9.7", CORTEX_RELEASES_URL="http://127.0.0.1:9")
        self.assertIn("note: CLAUDE.md not checked — Cortex 9.9.8, the version this machine reads, is not in the store",
                      proc.err)
        self.assertNotIn("Cortex 9.9.7 is not in the store", proc.err)

    def test_the_team_s_bootstrap_still_follows_the_team_s_version(self):
        root = self.tried("theirs", ignored=False)
        (root / "CLAUDE.md").write_bytes(written(self.f97) + OURS)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
        self.assertRegex(proc.out, r"Commit [^\n]*CLAUDE\.md")

    def test_init_writes_the_developer_s_own_bootstrap_from_the_full_resolution(self):
        root = self.tried("init", toml='version = "9.9.8"\ntheme = "h2g2"\n')
        proc = self.cortex(root, "init", "--tool", "claude")
        self.assert_ok(proc)
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(TEMPLATE))
        root = self.tried("init-theirs", toml='version = "9.9.8"\ntheme = "h2g2"\n', ignored=False)
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))
        # A file whose folder is not there yet, git asked of the first folder there is.
        root = self.tried("init-copilot", toml='version = "9.9.8"\ntheme = "h2g2"\n', ignored=False)
        (root / ".gitignore").write_text(".github/copilot-instructions.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "copilot"))
        self.assertEqual((root / ".github" / "copilot-instructions.md").read_bytes(), written(TEMPLATE))
        # The theme too: a local theme of none writes the developer's own file without the personality block.
        root = self.project(toml='version = "9.9.8"\ntheme = "h2g2"\n', local='theme = "none"\n', name="init-none")
        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(FORMAT_2, False)))


@DOWNLOADS
class BeforeTheTeamTests(TeamTestCase):
    """Before a team's version of 1.8.0, `cortex init` writes the developer's own bootstrap as 1.7.0 does."""

    def setUp(self):
        super().setUp()
        self.release.replace_spec("1.7.0", {"templates/bootstrap-instructions.md": FORMAT_2,
                                            "templates/project-overview.md.template": b"# Overview\n",
                                            "templates/project-context.md.template": b"# Context\n"})

    def test_init_keeps_the_team_s_theme_in_a_1_7_0_project(self):
        # A local theme whose none-ness differs from cortex.toml's: 1.7.0 finds the file it would write.
        for team, local in (("h2g2", "none"), ("none", "h2g2")):
            for case, committed in (("2.B", False), ("ignored", True)):
                with self.subTest(team=team, case=case):
                    root = self.project(toml=f'version = "1.7.0"\ntheme = "{team}"\n', committed=committed,
                                        name=f"{team}-{case}")
                    if committed:
                        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
                    self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
                    before = (root / "CLAUDE.md").read_bytes()
                    self.assertEqual(before, init.stamped(init.instructions(FORMAT_2, team != "none")))
                    with open(root / "cortex.local.toml", "a", encoding="utf-8") as file:
                        file.write(f'theme = "{local}"\n')
                    proc = self.cortex(root, "init", "--tool", "claude")
                    self.assert_ok(proc)
                    self.assertNotIn("personality block", proc.err)
                    proc = self.cortex(root, "init", "--tool", "claude", "--force")
                    self.assert_ok(proc)
                    self.assertIn("CLAUDE.md is what --force would write: left as it is", proc.out)
                    self.assertEqual((root / "CLAUDE.md").read_bytes(), before)
                    self.assertEqual([path.name for path in root.iterdir() if ".bak" in path.name], [])

    def test_a_sync_writes_an_own_format_3_file_back_with_the_team_s_theme(self):
        # As 1.7.0's own upgrade writes it: cortex.toml's theme, whatever cortex.local.toml's.
        root = self.project(toml='version = "1.7.0"\ntheme = "h2g2"\n', local='theme = "none"\n', committed=False)
        (root / "CLAUDE.md").write_bytes(init.stamped(init.instructions(TEMPLATE, False)))
        self.assert_ok(self.cortex(root, "sync"))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), written(FORMAT_2))


@DOWNLOADS
class BehindTests(TeamTestCase):
    """The team's bootstrap follows the team's version: untouched and not its template, it is behind."""

    def setUp(self):
        super().setUp()
        self.release.replace_spec("9.9.8", {"templates/bootstrap-instructions.md": FORMAT_2})

    def test_validate_warns_and_strict_fails_on_a_team_s_bootstrap_behind(self):
        root = self.project(team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root, "validate")
        self.assert_ok(proc)
        self.assertIn("BOOTSTRAP_BEHIND", proc.out + proc.err)
        self.assertNotEqual(self.cortex(root, "validate", "--strict").returncode, 0)
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE))                # in step: nothing said
        proc = self.cortex(root, "validate", "--strict")
        self.assert_ok(proc)
        self.assertNotIn("BOOTSTRAP_BEHIND", proc.out + proc.err)
        changed = written(FORMAT_2).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(changed)                          # a changed block is never behind
        self.assertNotIn("BOOTSTRAP_BEHIND", (proc := self.cortex(root, "validate")).out + proc.err)

    def test_before_1_8_0_only_an_untouched_format_3_file_is_behind(self):
        # A pin moved by hand below 1.8.0: no version before it writes format 3, which `cortex upgrade` writes again.
        root = self.project(toml='version = "1.7.0"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("behind", proc.err)
        self.assertNotIn("BOOTSTRAP_BEHIND", (proc := self.cortex(root, "validate")).out + proc.err)
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn("note: CLAUDE.md is behind: Cortex 1.7.0, the team's version, writes another — `cortex upgrade "
                      "1.7.0` writes it again", proc.err)
        self.assertIn("CLAUDE.md", (proc := self.cortex(root, "validate")).out + proc.err)
        self.assertIn("BOOTSTRAP_BEHIND", proc.out + proc.err)
        changed = written(TEMPLATE).replace(b"Cortex AI Team\n", b"Cortex AI Team, ours\n", 1)
        (root / "CLAUDE.md").write_bytes(changed)                          # a changed block is never behind
        self.assertNotIn("BOOTSTRAP_BEHIND", (proc := self.cortex(root, "validate")).out + proc.err)

    def test_a_file_in_a_service_is_named_from_the_root(self):
        root = self.project(team=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / ".github").mkdir()
        (root / ".github" / "copilot-instructions.md").write_bytes(written(FORMAT_2))
        proc = self.cortex(root / "agents", "validate")
        self.assertIn("⚠ .github/copilot-instructions.md\n  BOOTSTRAP_BEHIND — ", proc.out + proc.err)

    def test_without_the_team_s_version_in_the_store_validate_says_not_checked(self):
        root = self.project(team='version = "9.9.8"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE))
        proc = self.cortex(root, "validate", CORTEX_HOME=str(self.tmp / "empty-home"))
        self.assertIn("note: CLAUDE.md not checked against Cortex 9.9.8, the team's version, which is not in the "
                      "store — `cortex sync` puts it there", proc.err)
        self.assertNotIn("BOOTSTRAP_BEHIND", proc.out + proc.err)

    def test_a_machine_reading_a_checkout_is_never_behind(self):
        for case, toml, team, options in (
                ("synced", 'theme = "h2g2"\n', f'version = "{OWN}"\n', ("--link",)),
                ("link", 'version = "1.7.0"\ntheme = "h2g2"\n', None, ("--link",)),
                ("copy", 'version = "1.7.0"\ntheme = "h2g2"\n', None, ("--copy",))):
            with self.subTest(case=case):
                root = self.project(toml=toml, team=team, name=case)
                self.assert_ok(self.cortex(root, "sync"))
                (root / "CLAUDE.md").write_bytes(written(FORMAT_2 if team else TEMPLATE))
                self.assertIn("BOOTSTRAP_BEHIND", (proc := self.cortex(root, "validate")).out + proc.err)
                self.assert_ok(self.cortex(root, "sync", *options, "--from", str(harness.REPO)))
                proc = self.cortex(root, "validate")
                self.assertNotIn("BOOTSTRAP_BEHIND", proc.out + proc.err)
                self.assertNotIn("not checked", proc.err)


if __name__ == "__main__":
    unittest.main()
