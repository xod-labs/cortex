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

    def test_sync_from_a_checkout_writes_no_bootstrap(self):
        root = self.project(team=f'version = "{OWN}"\n', committed=False)
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

    def test_a_team_s_version_before_1_8_0_is_never_behind(self):
        root = self.project(toml='version = "1.7.0"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE))
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("behind", proc.err)
        self.assertNotIn("BOOTSTRAP_BEHIND", (proc := self.cortex(root, "validate")).out + proc.err)

    def test_without_the_team_s_version_in_the_store_validate_says_not_checked(self):
        root = self.project(team='version = "9.9.8"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / "CLAUDE.md").write_bytes(written(TEMPLATE))
        project = config.load(str(root))
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.tmp / "empty-home")}):
            said = upgrade.behind(str(root), project, None, fetch=False)
        self.assertEqual(len(said), 1)
        self.assertIn("not checked against Cortex 9.9.8, the team's version, which is not in the store", said[0])


if __name__ == "__main__":
    unittest.main()
