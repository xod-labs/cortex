"""The team's folder — ADR-013 §3.6: a committed ``cortex.toml``'s ``agents`` renames ``agents/``, a folder
inside the workspace, and the cascade's workspace tier, the team's files and the service scan follow it."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import cascade, catalog, project, resolver, validate, workspace  # noqa: E402

TEAM = {"version": "1.8.0", "theme": "h2g2"}


class AgentsTextTests(unittest.TestCase):
    def test_a_relative_path_inside_the_workspace_reads(self):
        for value, folder in (("team-agents", "team-agents"), ("team/agents", "team/agents"),
                              ("team-agents/", "team-agents"), ("agents", "agents")):
            with self.subTest(value=value):
                self.assertEqual(project.check_agents(value), folder)

    def test_what_names_no_folder_inside_the_workspace_is_refused(self):
        for value in ("../x", "a/../b", "/x", "C:\\x", "C:/x", "\\\\server\\x", "a\\b", ".", "./", "a//b", ".team",
                      "a/.b", "cortex", "Cortex", "cortex/x", "", 3):
            with self.subTest(value=value), self.assertRaises(project.ProjectFileError) as caught:
                project.check_agents(value)
            self.assertIn("agents", str(caught.exception))

    def test_cortex_toml_takes_it_and_the_other_files_refuse_it(self):
        project.check_project({**TEAM, "agents": "team-agents"})
        with self.assertRaises(project.ProjectFileError):
            project.check_project({**TEAM, "agents": "../x"})
        for check, name in ((project.check_local, project.LOCAL_FILE), (project.check_team, "agents/cortex.team.toml")):
            with self.subTest(file=name), self.assertRaises(project.ProjectFileError) as caught:
                check({"agents": "team-agents"})
            self.assertIn(f'{name}: "agents" is set in cortex.toml only', str(caught.exception))
        values, warnings = project.machine_values({"agents": "team-agents"}, "/h/config.toml")
        self.assertEqual(values, {})
        self.assertIn('/h/config.toml: "agents" is set in cortex.toml only', warnings[0])

    def test_an_unknown_key_s_message_is_1_7_0_s_before_1_8_0(self):
        for version, said in (("1.7.0", "cortex.toml takes version, theme, sync and claude_access"),
                              ("1.8.0", "cortex.toml takes version, theme, sync, claude_access and agents")):
            with self.subTest(version=version), self.assertRaises(project.ProjectFileError) as caught:
                project.check_project({**TEAM, "version": version, "verison": "1.8.0"})
            self.assertIn(said, str(caught.exception))


class ResolutionTests(unittest.TestCase):
    def test_agents_is_cortex_toml_s_or_the_default(self):
        found = cascade.resolve(TEAM, None, {}, {})
        self.assertEqual((found.value("agents"), found["agents"].origin), ("agents", cascade.DEFAULT))
        found = cascade.resolve({**TEAM, "agents": "team-agents"}, None, {}, {})
        self.assertEqual((found.value("agents"), found["agents"].origin), ("team-agents", project.PROJECT_FILE))

    def test_before_1_8_0_agents_is_refused_naming_1_8_0(self):
        with self.assertRaises(project.ProjectFileError) as caught:
            cascade.resolve({"version": "1.7.0", "theme": "h2g2", "agents": "team-agents"}, None, {}, {})
        self.assertIn('"agents" is read once the team\'s version is 1.8.0 or later — the team\'s is 1.7.0',
                      str(caught.exception))

    def test_the_team_s_file_is_read_in_the_folder_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cortex.toml").write_text('theme = "h2g2"\nagents = "team-agents"\n', encoding="utf-8")
            (root / "team-agents").mkdir()
            (root / "team-agents" / "cortex.team.toml").write_text('version = "1.8.1"\n', encoding="utf-8")
            data, _ = cascade.read_project(str(root), folder="team-agents")
            team = cascade.read_team(str(root), "team-agents")
            found = cascade.resolve(data, None, {}, {}, team_data=team, team_name=cascade.team_name("team-agents"))
            self.assertEqual((found.value("version"), found["version"].origin),
                             ("1.8.1", "team-agents/cortex.team.toml"))
            self.assertEqual(cascade.loose_versions(str(root), "team-agents")["team"],
                             ("team-agents/cortex.team.toml", "1.8.1"))
            self.assertEqual(cascade.loose_agents(str(root)), "team-agents")
            with self.assertRaises(project.ProjectFileError):      # agents/ has no team's file: version required
                cascade.read_project(str(root))


class CascadeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cortex-team-folder-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.base = self.tmp / "base"
        for tier in (self.base / "agents", self.tmp / "agents", self.tmp / "team-agents"):
            (tier / "roles" / "engineering").mkdir(parents=True)
            (tier / "capabilities" / "languages").mkdir(parents=True)
        (self.base / "agents" / "roles" / "engineering" / "architect.md").write_text("base", encoding="utf-8")
        (self.tmp / "agents" / "roles" / "engineering" / "architect.md").write_text("left", encoding="utf-8")
        (self.tmp / "team-agents" / "roles" / "engineering" / "architect.md").write_text("team", encoding="utf-8")
        (self.tmp / "team-agents" / "capabilities" / "languages" / "rust.md").write_text("rust\n", encoding="utf-8")
        (self.tmp / "agents" / "capabilities" / "languages" / "php.md").write_text("php\n", encoding="utf-8")

    def test_the_workspace_tier_is_the_team_s_folder(self):
        team = self.tmp / "team-agents"
        self.assertEqual(resolver.read_resolved("roles", "engineering/architect.md", None, self.tmp, base_root=self.base,
                                                team_folder=team), "base\n\nteam")
        self.assertEqual(resolver.read_resolved("roles", "engineering/architect.md", None, self.tmp,
                                                base_root=self.base), "base\n\nleft")      # the default, agents/
        self.assertEqual(catalog.capability_catalog(self.tmp, base_root=self.base, team_folder=team),
                         ["languages/rust.md"])
        self.assertEqual(catalog.capability_catalog(self.tmp, base_root=self.base), ["languages/php.md"])

    def test_the_service_scan_leaves_out_the_team_s_folder(self):
        for folder in ("team-agents", "svc"):
            (self.tmp / folder).mkdir(exist_ok=True)
            (self.tmp / folder / "project-overview.md").write_text(f"# {folder}\n", encoding="utf-8")
        found = [Path(path).name for path in workspace.services(str(self.tmp), str(self.base),
                                                                str(self.tmp / "team-agents"))]
        self.assertEqual(found, ["svc"])
        self.assertEqual(sorted(Path(path).name for path in workspace.services(str(self.tmp), str(self.base))),
                         ["svc", "team-agents"])

    def test_a_folder_inside_a_service_or_holding_a_project_is_refused(self):
        (self.tmp / "svc").mkdir()
        (self.tmp / "svc" / "project-overview.md").write_text("# svc\n", encoding="utf-8")
        (self.tmp / "other").mkdir()
        (self.tmp / "other" / "cortex.toml").write_text('version = "1.8.0"\n', encoding="utf-8")
        (self.tmp / "team-agents" / "project-overview.md").write_text("# the team\n", encoding="utf-8")
        (self.tmp / "svc" / "agents").mkdir()
        (self.tmp / "apps" / "web" / "agents").mkdir(parents=True)
        (self.tmp / "apps" / "web" / "project-overview.md").write_text("# web\n", encoding="utf-8")
        for folder, said in (("svc/agents", 'inside the service "svc"'), ("svc/team", 'inside the service "svc"'),
                             ("Svc/team", 'inside the service "svc"'), ("other", "holds a project's cortex.toml"),
                             ("svc", "holds an agents/ folder, a service's"), ("apps", 'holds the service "apps/web"')):
            with self.subTest(folder=folder), self.assertRaises(project.ProjectFileError) as caught:
                workspace.check_team_folder(str(self.tmp), folder, str(self.base))
            self.assertIn(said, str(caught.exception))
        workspace.check_team_folder(str(self.tmp), "team-agents", str(self.base))     # its team files are no service

    def test_the_validator_names_the_team_s_folder(self):
        wrong = self.tmp / "team-agents" / "roles" / "engineering" / "lead-backend.md"
        wrong.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
                         "     Semantic: additive\n-->\n## Ours (additive)\n", encoding="utf-8")
        (self.base / "agents" / "roles" / "engineering" / "lead-backend.md").write_text("base", encoding="utf-8")
        out = io.StringIO()
        validate.validate(str(self.tmp), str(self.base), "", False, out, validate.Colors(False),
                          team_folder=str(self.tmp / "team-agents"))
        self.assertIn("overlay path 'team-agents/roles/engineering/lead-backend.md' must mirror base", out.getvalue())

    def test_a_service_overlay_of_the_workspace_s_scope_names_the_team_s_folder(self):
        (self.tmp / "svc" / "agents" / "roles" / "engineering").mkdir(parents=True)
        (self.tmp / "svc" / "project-overview.md").write_text("# svc\n", encoding="utf-8")
        (self.tmp / "svc" / "agents" / "roles" / "engineering" / "architect.md").write_text(
            "<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
            "     Semantic: additive\n-->\n## Ours (additive)\n", encoding="utf-8")
        out = io.StringIO()
        validate.validate(str(self.tmp), str(self.base), "", False, out, validate.Colors(False),
                          team_folder=str(self.tmp / "team-agents"))
        self.assertIn("but file is not at workspace root (team-agents/...)", out.getvalue())

    def test_the_validator_reads_the_team_s_folder(self):
        team = str(self.tmp / "team-agents")
        self.assertEqual(validate.overlay_roots(str(self.tmp), str(self.base), "", team), [str(self.tmp)])
        out = io.StringIO()
        code = validate.validate(str(self.tmp), str(self.base), "", False, out, validate.Colors(False), team_folder=team)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("team-agents/roles/engineering/architect.md", out.getvalue())
        self.assertNotIn("agents/roles/engineering/architect.md\n", out.getvalue().replace("team-agents/", ""))


if __name__ == "__main__":
    unittest.main()
