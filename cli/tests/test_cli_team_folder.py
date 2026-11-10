"""The team's folder — ADR-013 phase 3 (#336).

A committed ``cortex.toml``'s ``agents`` renames ``agents/``: a relative path inside the workspace, read
from that file only. The team's file and its lock, the workspace's overlays, the team's context files and
the project's own themes follow the folder; a missing one is warned, an error under ``--locked``, and an
``agents/`` left behind is said.
"""

import json
import os
import re
import shutil
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli_format3 import FORMAT_2, OURS, TEMPLATE, written  # noqa: E402
from test_cli_team import (AS_ROOT, COMMIT_IT, DEV_OWN, DOWNLOADS, GIT, GIT_ENV, NEEDS_GIT, OWN, SOURCE, TEAM_MOVES,  # noqa: E402
                           TEAM_RELEASE, TeamTestCase, harness_path)

import cli_harness as harness  # noqa: E402
from cortex_cli import config, store, upgrade, validate  # noqa: E402

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
        self.assertIn(f"{FOLDER}/cortex.team.lock: written{COMMIT_IT}{f' in {FOLDER}/' if COMMIT_IT else ''}\n", proc.out)
        self.assertEqual(self.value(root, "agents"), FOLDER)
        lines = {line.split()[0]: line.split(None, 2)[1:] for line in self.cortex(root, "config").out.splitlines()}
        self.assertEqual(lines["version"], [OWN, f"{FOLDER}/cortex.team.toml"])
        self.assertEqual(lines["agents"], [FOLDER, "cortex.toml"])
        self.assertEqual(self.local(root)["synced"]["team"], OWN)
        self.assertEqual(self.local(root)["synced"]["agents"], FOLDER)       # what the runtime reads (§3.12)
        (root / "cortex.toml").write_text(f'agents = "./{FOLDER}/"\ntheme = "h2g2"\n', encoding="utf-8")
        self.assertEqual(self.value(root, "agents"), FOLDER)                    # as every message names it

    def test_every_spelling_of_the_folder_is_recorded_as_the_bootstrap_compares_it(self):
        # The bootstrap compares cortex.toml's agents with the folder synced records, every leading ./ and every
        # final / dropped (§3.10): each spelling the command takes, a sync records as that comparison reads it —
        # no sync in every conversation for a spelling.
        for name in ("bootstrap-instructions.md", "bootstrap-instructions-workspace.md"):
            self.assertIn("compared with every leading `./` and every final `/` dropped",
                          (harness.REPO / "templates" / name).read_text(encoding="utf-8"))
        for value in (FOLDER, f"./{FOLDER}/", f"././{FOLDER}", f"{FOLDER}//", f"./././{FOLDER}///"):
            with self.subTest(value=value):
                root = self.named(toml=f'agents = "{value}"\ntheme = "h2g2"\n', name=f"spelling-{len(value)}")
                self.assert_ok(self.cortex(root, "sync"))
                self.assertEqual(self.local(root)["synced"]["agents"], re.sub(r"^(\./)+", "", value).rstrip("/"))

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
        for value in ("../x", "a/../b", "/x", "C:\\x", "C:x", "\\\\server\\x", "a\\b", ".", "cortex", "cortex/x", ".team",
                      "cortex.", "team ", "svc/agents", "svc/team", "svc", "apps", "other"):
            with self.subTest(value=value):
                (made / "cortex.toml").write_text(f"agents = {json.dumps(value)}\ntheme = \"h2g2\"\n", encoding="utf-8")
                proc = self.cortex(made, "sync")
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"cortex.toml: agents {json.dumps(value)} ", proc.err)

    @harness.outside_a_repository
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

    @harness.outside_a_repository
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


class CounterReviewTests(FolderTestCase):
    """What the counter-review of the 1.8.0 stack found, 2026-11-09."""

    def test_a_version_too_new_is_said_before_the_folder_s_name_is_refused(self):
        # ADR-013 §3.8, the order: a later version may take a name this one refuses, and its team's file pin it.
        root = self.project('agents = ".team"\ntheme = "h2g2"\n')
        (root / ".team").mkdir()
        (root / ".team" / "cortex.team.toml").write_text('version = "99.0.0"\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("Cortex 99.0.0, which its .team/cortex.team.toml pins, newer than this cortex", proc.err)
        self.assertNotIn('starts with "."', proc.err)
        self.assertNotIn("--team", proc.err)                            # a move this cortex would refuse
        proc = self.cortex(root, "config", "version")                   # the CI recipe
        self.assert_ok(proc)
        self.assertEqual(proc.out.strip(), "99.0.0")
        (root / ".team" / "cortex.team.toml").write_text(f'version = "{OWN}"\n', encoding="utf-8")
        proc = self.cortex(root, "sync")                                # the gate passed: the name is refused
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('cortex.toml: agents ".team" names a folder that starts with "."', proc.err)

    def test_a_pin_too_new_in_a_folder_this_cortex_refuses_names_no_command_of_it(self):
        # .team: this cortex takes no --team of it — a release newer than it is installed, a build of no release
        # pinned there is edited by hand (ADR-013 §3.8, step 1; ADR-009 §9).
        root = self.project('agents = ".team"\ntheme = "h2g2"\n')
        (root / ".team").mkdir()
        (root / ".team" / "cortex.team.toml").write_text('version = "99.0.0"\n', encoding="utf-8")
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn("which its .team/cortex.team.toml pins, newer than this cortex", proc.err)
        self.assertIn("Update this cortex to serve it:\n    cortex self-update", proc.err.replace("\r\n", "\n"))
        self.assertNotIn("--team", proc.err)
        self.assertNotIn("--local", proc.err)
        (root / ".team" / "cortex.team.toml").write_text('version = "99.0.0-dev.1"\n', encoding="utf-8")
        # A release this cortex serves would be refused there too: one newer than it, that takes the folder.
        by_hand = ("pin a release by hand: set .team/cortex.team.toml's version to a release newer than this cortex, one "
                   "that takes the folder .team/ — this cortex does not take it, so no command of it moves the team's pin")
        for args in (("sync",), ("upgrade",), ("config", "version")):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode != 0, args[0] != "config", proc.err)
                self.assertIn(by_hand[1:], proc.err)
                self.assertNotIn("--team", proc.err)
                if args == ("upgrade",):       # a sentence of its own, after the refusal's
                    self.assertIn("nothing was changed. Pin a release by hand", proc.err)

    @harness.outside_a_repository
    def test_the_gate_reads_no_folder_an_uncommitted_cortex_toml_names(self):
        root = self.named(team='version = "99.0.0"\n', committed=False)
        proc = self.cortex(root, "sync")
        self.assertEqual(proc.returncode, 1, proc.err)
        self.assertIn('"agents" is read from a committed cortex.toml only', proc.err)
        self.assertNotIn("99.0.0", proc.err)

    def test_a_team_s_lock_left_in_agents_is_said_and_validate_names_agents(self):
        root = self.named()
        (root / "agents").mkdir()
        (root / "agents" / "cortex.team.lock").write_text("format = 1\n", encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"warning: agents/ still holds cortex.team.lock — the team's folder is {FOLDER}/", proc.err)
        out = self.cortex(root, "validate").out.replace("\r\n", "\n")
        self.assertIn("agents/\n  TEAM_FOLDER_LEFT_BEHIND", out)
        self.assertNotIn(f"{FOLDER}/\n  TEAM_FOLDER_LEFT_BEHIND", out)

    def test_a_nested_folder_the_key_names_an_empty_submodule_is_warned(self):
        folder = "team/shared"
        root = self.project(f'version = "{OWN}"\ntheme = "h2g2"\nagents = "{folder}"\n')
        (root / "team" / "shared").mkdir(parents=True)
        (root / ".gitmodules").write_text(f'[submodule "t"]\n\tpath = {folder}\n\turl = ../t\n', encoding="utf-8")
        proc = self.cortex(root, "sync")
        self.assert_ok(proc)
        self.assertIn(f"warning: {folder}/ is empty — a submodule not checked out", proc.err)

    @DOWNLOADS
    def test_prune_reads_a_refused_project_s_team_s_file_in_the_folder(self):
        for name, version in (("seven", "9.9.7"), ("eight", "9.9.8")):
            self.assert_ok(self.cortex(self.project(f'version = "{version}"\ntheme = "h2g2"\n', name=name), "sync"))
        root = self.named(toml=NAMED + "later = 1\n", team='version = "9.9.7"\n', local='version = "9.9.8"\n',
                          name="refused")
        proc = self.cortex(self.tmp, "prune", str(root))
        self.assert_ok(proc)
        self.assertIn("only its versions are read: 9.9.8, 9.9.7", proc.err)
        kept = {path.name for path in (self.home / "versions").iterdir() if not path.name.startswith(".")}
        self.assertTrue({"9.9.7", "9.9.8"} <= kept, kept)


@LINKS
class SharedRenamedFolderTests(FolderTestCase):
    """--all --team groups the followers by the team's folder cortex.toml names — a link to one folder."""

    def shared(self, failing):
        team = self.tmp / "team"
        team.mkdir()
        (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
        roots = []
        for name in ("alpha", "beta"):
            root = self.project(NAMED, name=name)
            (root / FOLDER).symlink_to(team, target_is_directory=True)
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

    @SOURCE
    def test_a_project_that_pins_its_own_version_is_no_follower_of_the_shared_file(self):
        # --all --team checks the overlays of the followers only: beta shares the folder, but its committed
        # cortex.toml pins its own version — its overlays, which fail, do not stop the move.
        team, (alpha, beta) = self.shared("beta")
        (beta / "cortex.toml").write_text(NAMED + 'version = "9.9.8"\n', encoding="utf-8")
        if upgrade.check_overlays(str(beta), Path(harness.REPO))[0] == 0:
            self.skipTest("the validator reads no service overlay here")
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f'version = "{OWN}"', (team / "cortex.team.toml").read_text(encoding="utf-8"))
        self.assertNotIn(f"The overlays of {harness_path(beta)}", proc.out)
        self.assertIn("pins its own version, 9.9.8", proc.out + proc.err)

    @TEAM_RELEASE
    def test_a_failing_follower_stops_the_shared_file_in_either_order(self):
        for failing in ("alpha", "beta"):
            with self.subTest(failing=failing):
                for name in ("team", "alpha", "beta"):
                    if (self.tmp / name).exists():
                        shutil.rmtree(self.tmp / name)
                team, _ = self.shared(failing)
                if upgrade.check_overlays(str(self.tmp / failing), Path(harness.REPO))[0] == 0:
                    self.skipTest("the validator reads no service overlay here")
                before = (team / "cortex.team.toml").read_bytes()
                proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertEqual((team / "cortex.team.toml").read_bytes(), before)
                self.assertIn(f"The overlays of {harness_path(self.tmp / failing)}, checked against Cortex {OWN}:", proc.out)

    @TEAM_RELEASE
    def test_the_shared_file_moves_once(self):
        team, roots = self.shared(None)
        proc = self.cortex(self.tmp, "upgrade", "--all", "--team", OWN)
        self.assert_ok(proc)
        self.assertEqual(proc.out.count(f"{FOLDER}/cortex.team.toml: Cortex 9.9.8 → {OWN}"), 1)
        for root in roots:
            self.assertEqual(self.local(root)["synced"]["reads"], OWN)


class CodeReviewFolderTests(FolderTestCase):
    """The counter-review's fixes of the team's file and lock (2026-11-09), in a folder cortex.toml names."""

    @SOURCE
    @DOWNLOADS
    def test_a_resume_during_a_try_writes_the_team_s_lock_in_the_folder(self):
        root = self.named(team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        lock = root / FOLDER / "cortex.team.lock"
        lock.write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64), encoding="utf-8")
        self.assert_ok(self.cortex(root, "sync"))
        proc = self.cortex(root, "sync", "--locked")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"--locked: {FOLDER}/cortex.team.lock names Cortex 9.9.7", proc.err)
        proc = self.cortex(root, "upgrade", "9.9.8")
        self.assert_ok(proc)
        self.assertIn(f"{FOLDER}/cortex.team.lock: Cortex 9.9.8, where it named 9.9.7 — commit it in {FOLDER}/\n",
                      proc.out)
        self.assertEqual(str(config.load_lock(str(lock.parent), lock.name).version), "9.9.8")
        self.assertFalse((root / "agents").exists())
        self.assert_ok(self.cortex(root, "sync", "--locked"))

    @SOURCE
    def test_a_team_s_lock_that_is_none_during_a_try_is_named_in_its_folder(self):
        root = self.named(team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        (root / FOLDER / "cortex.team.lock").write_text("<<<<<<< HEAD\n", encoding="utf-8")
        for args in (("upgrade", "--dry-run"), ("upgrade", "9.9.8", "--dry-run")):
            with self.subTest(args=args):
                proc = self.cortex(root, *args)
                self.assertEqual(proc.returncode, 1, proc.out + proc.err)
                self.assertIn(f"cortex upgrade: {FOLDER}/cortex.team.lock: Invalid", proc.err)
                self.assertIn("delete it, and `cortex upgrade 9.9.8` writes it again", proc.err)
                self.assertNotIn(" agents/cortex.team.lock", proc.err)

    @SOURCE
    @DOWNLOADS
    @AS_ROOT
    def test_a_resume_during_a_try_warns_of_the_folder_it_cannot_write(self):
        # As in agents/: a warning naming the folder cortex.toml names, the lock not updated, the rest done.
        root = self.named(team='version = "9.9.8"\n', local=f'version = "{OWN}"\n')
        self.assert_ok(self.cortex(root, "sync"))
        lock = root / FOLDER / "cortex.team.lock"
        lock.write_text(config.render_lock(store.Version("9.9.7"), "sha256:" + "0" * 64), encoding="utf-8")
        before = lock.read_bytes()
        os.chmod(root / FOLDER, 0o555)
        self.addCleanup(os.chmod, root / FOLDER, 0o755)
        proc = self.cortex(root, "upgrade", "--dry-run")
        self.assert_ok(proc)
        self.assertIn(f"{FOLDER}/ cannot be written here — it would be a warning, the lock not updated", proc.out)
        proc = self.cortex(root, "upgrade")
        self.assert_ok(proc)
        self.assertIn(f"warning: {FOLDER}/cortex.team.lock cannot be written — ", proc.err)
        self.assertEqual(lock.read_bytes(), before)
        self.assertFalse((root / "agents").exists())

    @TEAM_RELEASE
    @AS_ROOT
    def test_team_puts_back_what_it_could_not_write_in_the_folder(self):
        # As in agents/: the folder cannot be written, --team fails, and names the team's files as they were.
        root = self.named(team='version = "9.9.8"\n')
        team = (root / FOLDER / "cortex.team.toml").read_bytes()
        os.chmod(root / FOLDER, 0o555)
        self.addCleanup(os.chmod, root / FOLDER, 0o755)
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"note: {FOLDER}/cortex.team.toml and {FOLDER}/cortex.team.lock are as they were\n", proc.err)
        self.assertNotIn("not put back", proc.err)
        self.assertEqual((root / FOLDER / "cortex.team.toml").read_bytes(), team)
        self.assertFalse((root / "agents").exists())

    @SOURCE
    @DOWNLOADS
    def test_team_ending_names_the_folder_it_commits_in(self):
        root = self.named(team='version = "9.9.8"\n')
        proc = self.cortex(root, "upgrade", "--team", OWN)
        self.assert_ok(proc)
        self.assertIn(f"\nCommit {FOLDER}/cortex.team.toml and {FOLDER}/cortex.team.lock in {FOLDER}/", proc.out)
        self.assertNotIn(" in agents/", proc.out)

    @SOURCE
    @DOWNLOADS
    @NEEDS_GIT
    @LINKS
    @harness.outside_a_repository
    def test_team_ending_names_a_bootstrap_linked_into_the_folder_where_it_leads(self):
        # A bootstrap linked into the team's folder is named there, in the folder cortex.toml names.
        marked = (harness.REPO / "templates" / "bootstrap-instructions.md").read_bytes().replace(
            b"# Cortex AI Team", b"# Cortex AI Team, as 9.9.8 wrote it", 1)
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        for commit in (False, True):
            with self.subTest(commit=commit):
                team = self.tmp / f"team-{commit}"
                team.mkdir()
                (team / "cortex.team.toml").write_text('version = "9.9.8"\n', encoding="utf-8")
                (team / "CLAUDE.md").write_bytes(upgrade.init.stamped(upgrade.init.instructions(marked, True)))
                self.git(team, "init", "-q")
                self.git(team, "add", "-A")
                self.git(team, "commit", "-q", "-m", "init")
                root = self.project(NAMED, name=f"app-{commit}")
                (root / FOLDER).symlink_to(team, target_is_directory=True)
                (root / "CLAUDE.md").symlink_to(team / "CLAUDE.md")
                args = ("--commit", "--message", "move") if commit else ()
                proc = self.cortex(root, "upgrade", "--team", OWN, *args, **env)
                self.assert_ok(proc)
                self.assertIn(" M CLAUDE.md\n", self.git(team, "status", "--short").stdout)
                if commit:
                    self.assertRegex(proc.out, rf"\nCommitted in {FOLDER}/ on \S+, [0-9a-f]+ — nothing was pushed; "
                                               rf"CLAUDE.md not committed — it leads into {FOLDER}/, where --team "
                                               r"commits the team's files only: commit it there; teammates")
                else:
                    self.assertIn(f"\nCommit {FOLDER}/cortex.team.toml and {FOLDER}/cortex.team.lock in {FOLDER}/, "
                                  "and CLAUDE.md there too, where it leads; teammates", proc.out)
                self.assertNotIn("agents/", proc.out.replace(f"{FOLDER}/", ""))

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_names_the_folder_of_the_developer_s_own_change(self):
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        root = self.named(team='version = "1.8.0"\n', committed=False)
        (root / ".gitignore").write_text("cortex.local.toml\ncortex\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "init")
        team = root / FOLDER / "cortex.team.toml"
        team.write_text(team.read_text(encoding="utf-8") + 'theme = "mine"\n', encoding="utf-8")
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: nothing is committed in {FOLDER}/ — {FOLDER}/cortex.team.toml holds changes of yours "
                      "besides its version", proc.err)
        self.assertEqual(self.git(root, "log", "-1", "--format=%s").stdout.strip(), "init")
        self.assertFalse((root / "agents").exists())

    @LINKS
    @NEEDS_GIT
    @TEAM_RELEASE
    @harness.outside_a_repository
    def test_team_commit_names_the_folder_in_no_work_tree(self):
        # A renamed folder is read from a committed cortex.toml: it is in no work tree as a link out of it.
        root, shared = self.tmp / "ws", self.tmp / "shared"
        for where in (root, shared):
            where.mkdir()
        (shared / "cortex.team.toml").write_text('version = "1.8.0"\n', encoding="utf-8")
        (root / FOLDER).symlink_to(shared, target_is_directory=True)
        (root / "cortex.toml").write_text(NAMED, encoding="utf-8")
        (root / ".gitignore").write_text(f"cortex.local.toml\ncortex\n{FOLDER}\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "ws")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"{FOLDER}/ is in no git work tree", proc.out + proc.err)
        self.assertNotIn(" agents/ is in no", proc.out + proc.err)

    @NEEDS_GIT
    @TEAM_RELEASE
    def test_team_commit_in_a_submodule_names_the_folder_and_the_superproject(self):
        team, root = self.tmp / "team", self.tmp / "ws"
        for where in (team, root):
            where.mkdir()
            self.git(where, "init", "-q")
        (team / "cortex.team.toml").write_text('version = "1.8.0"\n', encoding="utf-8")
        self.git(team, "add", "-A")
        self.git(team, "commit", "-q", "-m", "t")
        (root / "cortex.toml").write_text(NAMED, encoding="utf-8")
        self.git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(team), FOLDER)
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "ws")
        env = {"PATH": os.path.dirname(GIT), **GIT_ENV}
        proc = self.cortex(root, "upgrade", "--team", OWN, "--commit", "--message", "move", **env)
        self.assert_ok(proc)
        self.assertIn(f"note: {FOLDER}/ is a submodule of {harness_path(root)}, which now shows it changed — commit "
                      "it there too", proc.err)

    @harness.outside_a_repository
    def test_validate_names_the_folder_whose_lock_this_user_cannot_write(self):
        root = self.named()
        self.assert_ok(self.cortex(root, "sync"))
        (root / FOLDER / "cortex.team.lock").unlink()
        with mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)}):
            project = config.load(str(root))
            with mock.patch.object(validate.os, "access", return_value=False):
                found = validate.lock_warnings(project, str(self.home / "versions" / OWN))
        self.assertEqual([code for _, code, _ in found], ["LOCK_MISSING"])
        self.assertIn(f"{FOLDER}/ cannot be written here", found[0][2])


class CounterReviewRoundTwoFolderTests(FolderTestCase):
    """Bluspark's second pass over the code (2026-11-10): --team is named where it moves the pin only."""

    def refused_folders(self):
        """Projects whose committed agents --team refuses for no fault of its text: inside a service — the gate reads
        its team's file there — and naming no folder inside the workspace, where the gate reads agents/."""
        service = self.project('agents = "svc/team"\ntheme = "h2g2"\n', name="service")
        (service / "svc" / "agents").mkdir(parents=True)
        (service / "svc" / "project-overview.md").write_text("# A service\n", encoding="utf-8")
        (service / "svc" / "team").mkdir()
        outside = self.project('agents = "../shared"\ntheme = "h2g2"\n', name="outside")
        (outside / "agents").mkdir()
        return ((service, "svc/team/cortex.team.toml", 'agents "svc/team" is inside the service "svc"'),
                (outside, "agents/cortex.team.toml", 'agents "../shared" climbs out of the folder it is in'))

    def test_a_too_new_pin_names_no_team_move_where_agents_makes_team_refuse(self):
        for root, team, why in self.refused_folders():
            (root / team).write_text('version = "99.0.0"\n', encoding="utf-8")
            for args in (("sync",), ("validate",), ("init", "--tool", "claude"), ("upgrade",)):
                with self.subTest(team=team, args=args):
                    proc = self.cortex(root, *args)
                    self.assertNotEqual(proc.returncode, 0, proc.out + proc.err)
                    self.assertIn(f"Cortex 99.0.0, which its {team} pins, newer than this cortex", proc.err)
                    self.assertIn("cortex self-update 99.0.0", proc.err)
                    self.assertNotIn("--team", proc.err)
            # The move, were it named, is refused — for agents, which the gate says nothing of.
            proc = self.cortex(root, "upgrade", "--team", "9.9.8")
            self.assertEqual(proc.returncode, 1, proc.err)
            self.assertIn(why, proc.err)

    @SOURCE
    def test_a_pin_of_no_release_there_is_moved_by_hand_and_the_edit_works(self):
        for root, team, why in self.refused_folders():
            (root / team).write_text('version = "9.9.10-dev.3"\n', encoding="utf-8")
            for args in (("sync",), ("upgrade",), ("init", "--tool", "claude"), ("config", "version")):
                with self.subTest(team=team, args=args):
                    proc = self.cortex(root, *args)
                    self.assertEqual(proc.returncode != 0, args[0] != "config", proc.out + proc.err)
                    self.assertIn(f"pin a release by hand: set {team}'s version to one — no command of this cortex "
                                  f"moves the team's pin: cortex.toml: {why}", proc.err.replace("Pin a", "pin a"))
                    self.assertNotIn("--team", proc.err)
            # The edit named, made: the gate passes, and what is left is agents, said as such.
            (root / team).write_text('version = "9.9.8"\n', encoding="utf-8")
            proc = self.cortex(root, "sync")
            self.assertEqual(proc.returncode, 1, proc.err)
            self.assertIn(why, proc.err)
            self.assertNotIn("9.9.10-dev.3", proc.err)

    def test_a_too_new_pin_in_the_folder_team_moves_still_names_it(self):
        root = self.named(team='version = "99.0.0"\n')
        proc = self.cortex(root, "upgrade")
        self.assertEqual(proc.returncode, 1, proc.err)
        if TEAM_MOVES:
            self.assertIn("`cortex upgrade --team VERSION` moves the team's pin", proc.err)
            if not DEV_OWN:                     # named: it works — to this cortex's release, or the suite's 9.9.8
                self.assert_ok(self.cortex(root, "upgrade", "--team", OWN if harness.BINARY else "9.9.8"))
        else:
            self.assertNotIn("--team", proc.err)


if __name__ == "__main__":
    unittest.main()
