"""The bootstrap of format 3 — ADR-013 phase 2b (#335), §3.10.

Both templates read the team's file and compare each file's ``version`` with what ``cortex sync``
recorded in ``synced``. The developer's own bootstrap — one git ignores, or in no repository —
follows the version this machine reads: ``cortex sync`` writes an untouched one again in a project
that has a team's file, ``cortex upgrade --local`` whenever it sets a try, and below 1.8.0 an
untouched format 3 one goes back to its version's format. The team's bootstrap follows the team's
version: ``cortex sync`` and ``cortex validate`` say when an untouched one is behind it.
"""

import io
import os
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


class TemplateTests(unittest.TestCase):
    def test_both_templates_are_format_3_and_read_the_team_s_file(self):
        for name, text in (("single", TEMPLATE), ("workspace", WORKSPACE)):
            with self.subTest(template=name):
                self.assertEqual(init.markers(text), [(name, 3)])
                self.assertIsNotNone(init.END_MARKER.search(text))
                for rule in (b"**The team's folder** is the folder `agents` names in `cortex.toml`, else `agents/`",
                             b"`cortex.team.toml`", b"**A version moved**", b"never order versions",
                             b"**A checkout**", b"**No `synced`**", b"below 1.8.0, run `cortex self-update`",
                             b"**It names a missing team's folder**",
                             b"the team's file's own when its `locked` lists `theme`",
                             b"in `cortex.local.toml` or in the team's file, of your own accord"):
                    self.assertIn(rule, text)
                self.assertNotIn(b"**The spec is of a version other than", text)


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
                                  "`cortex upgrade` writes it again", proc.err)
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
        # The theme too: a local theme of none writes the developer's own file without the personality block.
        root = self.project(toml='version = "9.9.8"\ntheme = "h2g2"\n', local='theme = "none"\n', name="init-none")
        (root / ".gitignore").write_text("CLAUDE.md\ncortex.local.toml\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "init", "--tool", "claude"))
        self.assertEqual((root / "CLAUDE.md").read_bytes(), init.stamped(init.instructions(FORMAT_2, False)))


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
        self.assertIn("note: CLAUDE.md is behind: Cortex 1.7.0, the team's version, writes another — `cortex upgrade` "
                      "writes it again", proc.err)
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
        self.assertIn(".github/copilot-instructions.md", proc.out + proc.err)
        self.assertNotIn("../.github", proc.out + proc.err)

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
