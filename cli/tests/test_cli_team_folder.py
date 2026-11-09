"""The team's folder — ADR-013 phase 3 (#336).

A committed ``cortex.toml``'s ``agents`` renames ``agents/``: a relative path inside the workspace, read
from that file only. The team's file and its lock, the workspace's overlays, the team's context files and
the project's own themes follow the folder; a missing one is warned, an error under ``--locked``, and an
``agents/`` left behind is said.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli_format3 import FORMAT_2, OURS, TEMPLATE, written  # noqa: E402
from test_cli_team import DEV_OWN, DOWNLOADS, GIT, GIT_ENV, NEEDS_GIT, OWN, TeamTestCase  # noqa: E402

FOLDER = "team-agents"
NAMED = f'agents = "{FOLDER}"\ntheme = "h2g2"\n'
LINKS = unittest.skipIf(os.name == "nt", "a link to a directory needs a privilege on Windows")


class FolderTestCase(TeamTestCase):
    def named(self, toml=NAMED, team=f'version = "{OWN}"\n', committed=True, local=None, name="app"):
        """A project whose cortex.toml names the team's folder, its team's file there."""
        root = self.project(toml, committed=committed, local=local, name=name)
        if team is not None:
            (root / FOLDER).mkdir()
            (root / FOLDER / "cortex.team.toml").write_text(team, encoding="utf-8")
        return root


class FollowsTests(FolderTestCase):
    """What follows the folder: the team's file and lock, the overlays, the context and the themes."""

    def test_the_team_s_file_and_its_lock_are_in_the_folder_named(self):
        root = self.named()
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertTrue((root / FOLDER / "cortex.team.lock").is_file())
        self.assertFalse((root / "agents").exists())
        self.assertFalse((root / "cortex.lock").exists())
        self.assertIn(f"{FOLDER}/cortex.team.lock: written — commit it in {FOLDER}/", proc.out)
        self.assertEqual(self.value(root, "agents"), FOLDER)
        lines = {line.split()[0]: line.split(None, 2)[1:] for line in self.cortex(root, "config").out.splitlines()}
        self.assertEqual(lines["version"], [OWN, f"{FOLDER}/cortex.team.toml"])
        self.assertEqual(lines["agents"], [FOLDER, "cortex.toml"])
        self.assertEqual(self.local(root)["synced"]["team"], OWN)
        self.assertEqual(self.local(root)["synced"]["agents"], FOLDER)       # what the runtime reads (§3.12)
        (root / "cortex.toml").write_text(f'agents = "./{FOLDER}/"\ntheme = "h2g2"\n', encoding="utf-8")
        self.assertEqual(self.value(root, "agents"), FOLDER)                    # as every message names it

    def test_the_overlays_validated_are_the_folder_s(self):
        root = self.named()
        roles = root / FOLDER / "roles" / "engineering"
        roles.mkdir(parents=True)
        (roles / "our-own-role.md").write_text("# Ours\n", encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "validate")
        self.assert_ok(proc)
        self.assertIn(f"{FOLDER}/roles/engineering/our-own-role.md", proc.out)
        self.assertIn("Checked:  1", proc.out.replace("\x1b[1m", "").replace("\x1b[0m", ""))

    def test_the_project_s_own_theme_is_the_folder_s(self):
        root = self.named(toml=f'agents = "{FOLDER}"\ntheme = "ours"\n')
        (root / FOLDER / "personalities" / "ours").mkdir(parents=True)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("the Prompt Manager would not find it", proc.err)
        (root / FOLDER / "personalities" / "ours").rmdir()
        proc = self.cortex(root, "sync")
        self.assertIn(f'theme "ours" is neither in the spec nor in {FOLDER}/personalities/', proc.err)

    def test_init_takes_a_theme_of_the_folder_s(self):
        root = self.named()
        (root / FOLDER / "personalities" / "ours").mkdir(parents=True)
        proc = self.cortex(root, "init", "--tool", "claude", "--theme", "ours")
        self.assert_ok(proc)
        self.assertIn('theme = "ours"', (root / "cortex.toml").read_text(encoding="utf-8"))
        proc = self.cortex(root, "init", "--tool", "claude", "--theme", "theirs", "--force")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"nor in {FOLDER}/personalities/", proc.err)

    def test_a_version_too_new_is_said_of_the_team_s_file_in_the_folder(self):
        root = self.named(team='version = "99.0.0"\n')
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"Cortex 99.0.0, which its {FOLDER}/cortex.team.toml pins, newer than this cortex", proc.err)
        self.assertIn(f"or move the team's pin: cortex upgrade --team {'VERSION' if DEV_OWN else OWN}", proc.err)

    def test_the_team_s_pair_init_scaffolds_is_in_the_folder(self):
        root = self.named()
        (root / FOLDER / ".git").mkdir()                    # the team's folder, its own repository (ADR-006)
        proc = self.cortex(root, "init", "--workspace", "--tool", "claude")
        self.assert_ok(proc)
        self.assertTrue((root / FOLDER / "project-overview.md").is_file())
        self.assertFalse((root / "agents").exists())


class RefusalTests(FolderTestCase):
    """Where `agents` is set, and what it may name."""

    def test_agents_is_set_in_cortex_toml_only(self):
        root = self.named(local=f'agents = "{FOLDER}"\n')
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('cortex.local.toml: "agents" is set in cortex.toml only', proc.err)
        root = self.named(team=f'version = "{OWN}"\nagents = "x"\n', name="team")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f'{FOLDER}/cortex.team.toml: "agents" is set in cortex.toml only', proc.err)
        root = self.named(name="machine")
        (self.home / "config.toml").write_text('agents = "elsewhere"\n', encoding="utf-8")
        proc = self.cortex(root, "config")
        self.assert_ok(proc)
        self.assertIn('config.toml: "agents" is set in cortex.toml only, not read', proc.err)
        self.assertIn(f"agents         {FOLDER}", proc.out)

    def test_a_value_that_names_no_team_s_folder_is_refused(self):
        made = self.named(name="made")
        (made / "svc").mkdir()
        (made / "svc" / "project-overview.md").write_text("# svc\n", encoding="utf-8")
        (made / "svc" / "agents").mkdir()
        (made / "apps" / "web" / "agents").mkdir(parents=True)
        (made / "apps" / "web" / "project-overview.md").write_text("# web\n", encoding="utf-8")
        (made / "other").mkdir()
        (made / "other" / "cortex.toml").write_text('version = "1.8.0"\n', encoding="utf-8")
        for value in ("../x", "a/../b", "/x", "C:\\x", "\\\\server\\x", "a\\b", ".", "cortex", "cortex/x", ".team",
                      "svc/agents", "svc/team", "svc", "apps", "other"):
            with self.subTest(value=value):
                (made / "cortex.toml").write_text(f"agents = {json.dumps(value)}\ntheme = \"h2g2\"\n", encoding="utf-8")
                proc = self.cortex(made, "sync")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"cortex.toml: agents {json.dumps(value)} ", proc.err)

    def test_the_default_agents_is_never_refused(self):
        root = self.project(f'agents = "agents"\nversion = "{OWN}"\ntheme = "h2g2"\n')
        self.assert_ok(self.cortex(root, "sync"))
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n', committed=False, name="nogit")
        (root / "agents" / "roles").mkdir(parents=True)
        self.assert_ok(self.cortex(root, "sync"))
        root = self.project(f'agents = "Agents"\nversion = "{OWN}"\ntheme = "h2g2"\n', committed=False, name="cased")
        proc = self.cortex(root, "sync")                            # a folder of its own, as written: not committed
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('"agents" is read from a committed cortex.toml only', proc.err)
        for value in ("agents", "./agents/"):
            with self.subTest(value=value):
                root = self.project(f'agents = "{value}"\nversion = "{OWN}"\ntheme = "h2g2"\n', committed=False,
                                    name=f"named-{value.strip('./')}-{len(value)}")
                self.assert_ok(self.cortex(root, "sync"))           # named, uncommitted: the default all the same
                self.assertNotIn("agents", self.local(root)["synced"])

    def test_agents_in_an_uncommitted_cortex_toml_is_refused(self):
        root = self.named(committed=False)
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('"agents" is read from a committed cortex.toml only', proc.err)
        self.assertIn("the team's folder is then agents/", proc.err)

    def test_before_1_8_0_agents_is_refused_naming_1_8_0(self):
        root = self.project(f'version = "1.7.0"\ntheme = "h2g2"\nagents = "{FOLDER}"\n')
        (root / FOLDER).mkdir()
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('"agents" is read once the team\'s version is 1.8.0 or later — the team\'s is 1.7.0', proc.err)
        root = self.project(f'version = "1.7.0"\ntheme = "h2g2"\nagents = "{FOLDER}"\n', committed=False, name="nogit")
        proc = self.cortex(root, "sync")                            # at a 2.B root: 1.8.0 named, not "commit it"
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('"agents" is read once the team\'s version is 1.8.0 or later — the team\'s is 1.7.0', proc.err)

    @DOWNLOADS
    def test_a_move_below_1_8_0_is_refused_for_agents(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "{FOLDER}"\n')
        (root / FOLDER).mkdir()
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "upgrade", "1.7.0")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("cortex.toml's agents", proc.err)


class FolderStateTests(FolderTestCase):
    """A team's folder linked outside, missing, or an agents/ left behind."""

    @LINKS
    def test_a_team_s_folder_linked_outside_the_workspace_is_read_through_the_link(self):
        shared = self.tmp / "shared-agents"
        shared.mkdir()
        (shared / "cortex.team.toml").write_text(f'version = "{OWN}"\n', encoding="utf-8")
        root = self.project(NAMED)
        (root / FOLDER).symlink_to(shared, target_is_directory=True)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertTrue((shared / "cortex.team.lock").is_file())
        self.assertIn(f"{FOLDER}/cortex.team.lock: written", proc.out)

    def test_a_missing_folder_the_key_names_warns_and_fails_locked(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "{FOLDER}"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"warning: {FOLDER}/ is not there — the team's folder cortex.toml names", proc.err)
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"--locked: {FOLDER}/ is not there", proc.err)
        proc = self.cortex(root, "validate")
        self.assertIn("TEAM_FOLDER_MISSING", proc.out)
        (root / "cortex.toml").write_text(NAMED, encoding="utf-8")      # following a team's file it cannot read
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f'"version" is required, or in {FOLDER}/cortex.team.toml — {FOLDER}/', proc.err)

    def test_a_folder_the_key_names_an_empty_submodule_fails_locked(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "{FOLDER}"\n')
        (root / FOLDER).mkdir()
        (root / ".gitmodules").write_text(f'[submodule "t"]\n\tpath = {FOLDER}\n\turl = ../t\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"warning: {FOLDER}/ is empty — a submodule not checked out", proc.err)
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn(f"--locked: {FOLDER}/ is empty — a submodule not checked out", proc.err)

    def test_an_agents_left_behind_is_said(self):
        root = self.named()
        (root / "agents" / "roles").mkdir(parents=True)
        (root / "agents" / "project-context.md").write_text("# the team\n", encoding="utf-8")
        (root / "agents" / "cortex.team.toml").write_text('claude_access = false\nlocked = ["claude_access"]\n',
                                                          encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"warning: agents/ still holds cortex.team.toml, project-context.md, roles — the team's folder "
                      f"is {FOLDER}/", proc.err)
        proc = self.cortex(root, "validate")
        self.assertIn("TEAM_FOLDER_LEFT_BEHIND", proc.out)

    @LINKS
    def test_an_agents_kept_as_a_link_to_the_folder_is_not_left_behind(self):
        root = self.named()
        (root / FOLDER / "roles").mkdir()
        (root / "agents").symlink_to(FOLDER, target_is_directory=True)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("still holds", proc.err)
        self.assertNotIn("TEAM_FOLDER_LEFT_BEHIND", self.cortex(root, "validate").out)

    def test_an_agents_inside_the_folder_is_said_unread(self):
        root = self.named()
        (root / FOLDER / "agents" / "roles").mkdir(parents=True)
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f'warning: {FOLDER}/agents/ is not read — the team\'s folder is {FOLDER}/ itself', proc.err)
        self.assertIn(f'want agents = "{FOLDER}/agents"', proc.err)
        self.assertIn("TEAM_FOLDER_NESTED", self.cortex(root, "validate").out)

    def test_a_team_s_lock_that_does_not_read_is_named_in_the_folder(self):
        root = self.named()
        self.assert_ok(self.cortex(root, "sync"))
        (root / FOLDER / "cortex.team.lock").write_text("garbage = \n", encoding="utf-8")
        for args in (("init", "--tool", "claude"), ("upgrade", "--dry-run")):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertIn(f"{FOLDER}/cortex.team.lock", proc.out + proc.err)
                self.assertNotIn("agents/cortex.team.lock", (proc.out + proc.err).replace(f"{FOLDER}/", ""))

    def test_nothing_is_said_of_the_default_folder(self):
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\n')
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertNotIn("is not there", proc.err)
        self.assertNotIn("still holds", proc.err)


class UpgradeGateTests(FolderTestCase):
    """Every overlay gate of `cortex upgrade` checks the team's folder, as `cortex validate` does."""

    BROKEN = "<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/nowhere.md\n     Scope: workspace\n-->\n\n# x\n"

    def broken(self, root, folder):
        overlay = root / folder / "roles" / "engineering" / "architect.md"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(self.BROKEN, encoding="utf-8")

    def test_team_stops_on_the_folder_s_failing_overlays(self):
        root = self.named()
        self.broken(root, FOLDER)
        before = (root / FOLDER / "cortex.team.toml").read_bytes()
        proc = self.cortex(root, "upgrade", "--team", OWN)       # no lock yet: one to write, the overlays checked
        self.assertEqual(proc.returncode, 1, proc.out + proc.err)
        self.assertIn(f"{FOLDER}/roles/engineering/architect.md", proc.out)
        self.assertEqual((root / FOLDER / "cortex.team.toml").read_bytes(), before)
        self.assertFalse((root / FOLDER / "cortex.team.lock").exists())

    def test_an_agents_left_behind_stops_no_move(self):
        root = self.named()
        self.broken(root, "agents")
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"The project's overlays pass against Cortex {OWN}", proc.out)
        self.assertTrue((root / FOLDER / "cortex.team.lock").is_file())

    @DOWNLOADS
    def test_a_move_stops_on_the_folder_s_failing_overlays(self):
        root = self.project(f'version = "9.9.8"\ntheme = "h2g2"\nagents = "{FOLDER}"\n')
        self.broken(root, FOLDER)
        self.assert_ok(self.cortex(root, "sync"))
        for args in ((OWN, "--dry-run"), (OWN,)):
            with self.subTest(args=args):
                proc = self.cortex(root, "upgrade", *args)
                self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                self.assertIn(f"{FOLDER}/roles/engineering/architect.md", proc.out)
                self.assertIn('version = "9.9.8"', (root / "cortex.toml").read_text(encoding="utf-8"))


@DOWNLOADS
class LinkedBootstrapTests(FolderTestCase):
    """A bootstrap that is a link is asked of git in the repository of the file it leads to (§3.1)."""

    @LINKS
    @NEEDS_GIT
    def test_a_bootstrap_linked_into_the_team_s_repository_is_asked_of_that_repository(self):
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        # Where git ignores the link and tracks the file it leads to, and the other way round.
        for case, ignored_by in (("the team's", "project"), ("own", "team")):
            with self.subTest(case=case):
                root = self.named(committed=False, name=case.replace("'", ""))
                (root / FOLDER / "CLAUDE.md").write_bytes(written(FORMAT_2) + OURS)
                (root / "CLAUDE.md").symlink_to(Path(FOLDER) / "CLAUDE.md")
                (root / ".gitignore").write_text(f"/cortex.local.toml\n/{FOLDER}/\n"
                                                 + ("/CLAUDE.md\n" if ignored_by == "project" else ""), encoding="utf-8")
                if ignored_by == "team":
                    (root / FOLDER / ".gitignore").write_text("/CLAUDE.md\n", encoding="utf-8")
                for where in (root, root / FOLDER):
                    for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "x")):
                        self.git(where, *args)
                proc = self.cortex(root, "sync", **env)
                self.assert_ok(proc)
                self.assertTrue((root / "CLAUDE.md").is_symlink())
                if case == "the team's":
                    self.assertEqual((root / FOLDER / "CLAUDE.md").read_bytes(), written(FORMAT_2) + OURS)
                    self.assertIn("CLAUDE.md is behind", proc.err)
                else:
                    self.assertEqual((root / FOLDER / "CLAUDE.md").read_bytes(), written(TEMPLATE) + OURS)
                    self.assertIn("CLAUDE.md: written again", proc.out)

if __name__ == "__main__":
    unittest.main()
