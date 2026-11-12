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
                              ("team-agents/", "team-agents"), ("agents", "agents"), ("./team-agents", "team-agents"),
                              ("Agents", "Agents"), ("./agents/", "agents")):
            with self.subTest(value=value):
                self.assertEqual(project.check_agents(value), folder)

    def test_what_names_no_folder_inside_the_workspace_is_refused(self):
        for value in ("../x", "a/../b", "/x", "C:\\x", "C:/x", "C:x", "C:", "\\\\server\\x", "a\\b", ".", "./", "././",
                      "a//b", ".team", "a/.b", "a/./b", "cortex", "Cortex", "cortex/x", "", 3, "a\nb", "a\tb", "a\x7fb",
                      "cortex.", "cortex ", "team.", "a/b ", " ", "cortex::$INDEX_ALLOCATION", "team:x", "a/b:c",
                      "team./agents", "team /agents", "a/b./c"):           # Windows drops it in any component
            with self.subTest(value=value), self.assertRaises(project.ProjectFileError) as caught:
                project.check_agents(value)
            self.assertIn("agents", str(caught.exception))

    def test_the_gate_reads_a_name_a_later_version_may_take(self):
        # ADR-013 §3.8, step 1: a folder starting with "." is read loosely, before its text is refused.
        for value, folder in ((".team", ".team"), ("./.team/", ".team"), ("a/.b", "a/.b"), ("team-agents", "team-agents")):
            with self.subTest(value=value):
                self.assertEqual(project.check_agents(value, loose=True), folder)
        for value in ("../x", "/x", "C:x", "a\\b", "a\nb", ".", "a/./b", "cortex", "cortex.", "team ", "", 3,
                      "cortex::$INDEX_ALLOCATION", ".team:x", ".team./agents", ".team /agents"):
            with self.subTest(value=value), self.assertRaises(project.ProjectFileError):
                project.check_agents(value, loose=True)

    def test_names_fold_as_file_systems_fold_them(self):
        f = project.folded
        self.assertEqual(f("Team-Agents"), f("team-agents"))
        self.assertNotEqual(f("straße"), f("strasse"))                  # two folders on NTFS and APFS
        self.assertEqual(f("STRASSE"), f("strasse"))
        self.assertEqual(f("ΟΜΑΔΕΣ"), f("ομαδες"))                       # a final sigma is a sigma
        self.assertEqual(f("ομαδεσ"), f("ομαδες"))
        self.assertEqual(f("\u212aeys"), f("keys"))                     # the Kelvin sign is K composed: one on APFS
        self.assertEqual(f("\u00e9quipe"), f("e\u0301quipe"))          # composed or not, one name on APFS
        self.assertEqual(f("\u00c9QUIPE"), f("e\u0301quipe"))
        self.assertEqual(f("i\u0307"), f("\u0130"))                     # İ is i and a dot above, upper-cased
        self.assertEqual(f("I\u0307"), f("\u0130"))
        self.assertEqual(f("\u1e9e"), f("\u00df"))                      # ẞ is ß, as the simple case folding folds it
        self.assertEqual(f("\u03f4"), f("\u03b8"))                      # ϴ is θ, likewise
        self.assertEqual(f("\u1f80"), f("\u1f88"))                      # ᾀ upper-cased is ᾈ: one on NTFS
        self.assertEqual(f("\u0131"), f("i"))                           # one folder rather than two: two on APFS
        for small, capital in (("\u01f0", "J\u030c"), ("\u1e97", "T\u0308"), ("\u1e96", "H\u0331"),
                               ("\u0390", "\u03aa\u0301"), ("\u1f50", "\u03a5\u0313")):
            with self.subTest(name=small):                              # a letter whose upper case is longer, and
                self.assertEqual(f(small), f(capital))                  # its marks: decomposed, one on NTFS too
        self.assertNotEqual(f("straße"), f("strase"))                   # ß is kept as written, not cut to S
        for full, compatible in (("\uff54\uff45\uff41\uff4d", "team"), ("x\u00b2", "x2")):
            with self.subTest(name=full):                               # a compatible form: two on NTFS and APFS
                self.assertNotEqual(f(full), f(compatible))
        for value in ("cortex", "Cortex", "CORTEX", "Cortex/x"):
            with self.subTest(value=value), self.assertRaises(project.ProjectFileError):
                project.check_agents(value)

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
        old, new = "cortex.toml takes version, theme, sync and claude_access", "cortex.toml takes version, theme, sync, claude_access and agents"
        for version, said in (("1.7.0", old), ("1.7", old), ("1.8.0", new), (None, new)):
            data = {**TEAM, "version": version, "verison": "1.8.0"} if version else {"theme": "h2g2", "verison": "1"}
            with self.subTest(version=version), self.assertRaises(project.ProjectFileError) as caught:
                project.check_project(data, ("version",))
            self.assertIn(said, str(caught.exception))
        with self.assertRaises(project.ProjectFileError) as caught:            # cortex.local.toml, as 1.7.0 said
            project.check_local({"foo": "x"}, project.lists_1_7_0({"version": "1.7.0"}))
        self.assertIn("cortex.local.toml takes theme, spec, claude_access and claude_entry", str(caught.exception))
        with self.assertRaises(project.ProjectFileError) as caught:
            project.check_local({"foo": "x"}, project.lists_1_7_0({"version": "1.8.0"}))
        self.assertIn("claude_entry and synced", str(caught.exception))
        with self.assertRaises(project.ProjectFileError) as caught:            # quoted as written
            project.check_agents(".//x")
        self.assertIn('agents ".//x"', str(caught.exception))

    def test_synced_records_a_renamed_team_s_folder_only(self):
        base = {"project": "1.8.0", "pinned": "1.8.0", "reads": "1.8.0"}
        project.check_synced({**base, "agents": "team-agents"})
        for folder in ("agents", "../x", 3):
            with self.subTest(folder=folder), self.assertRaises(project.ProjectFileError):
                project.check_synced({**base, "agents": folder})


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
        found = [Path(path).name for path in workspace.services(str(self.tmp), str(self.base),
                                                                str(self.tmp / "Team-Agents"))]
        self.assertEqual(found, ["svc"])                        # cortex.toml's name, in another case than the disk's

    def test_the_scan_leaves_out_the_team_s_folder_only(self):
        for folder in ("team", "teamwork", "straße"):
            (self.tmp / folder).mkdir(exist_ok=True)
            (self.tmp / folder / "project-overview.md").write_text(f"# {folder}\n", encoding="utf-8")
        found = sorted(Path(path).name for path in workspace.services(str(self.tmp), str(self.base), str(self.tmp / "team")))
        self.assertEqual(found, ["straße", "teamwork"])              # a name it begins, no folder below it
        found = sorted(Path(path).name for path in workspace.services(str(self.tmp), str(self.base),
                                                                      str(self.tmp / "strasse")))
        self.assertEqual(found, ["straße", "team", "teamwork"])      # another folder on every file system

    def test_a_folder_holding_team_files_below_it_is_no_service(self):
        (self.tmp / "team-agents" / "docs").mkdir()
        (self.tmp / "team-agents" / "docs" / "project-overview.md").write_text("# notes\n", encoding="utf-8")
        workspace.check_team_folder(str(self.tmp), "team-agents", str(self.base))     # no agents/ there: no service
        (self.tmp / "straße" / "agents").mkdir(parents=True)
        (self.tmp / "straße" / "project-overview.md").write_text("# straße\n", encoding="utf-8")
        workspace.check_team_folder(str(self.tmp), "strasse/team", str(self.base))     # not inside the service straße

    def test_service_dot_reads_the_team_s_folder(self):
        (self.base / "agents" / "roles" / "engineering" / "lead-backend.md").write_text("base", encoding="utf-8")
        out = io.StringIO()
        validate.validate(str(self.tmp), str(self.base), ".", False, out, validate.Colors(False),
                          team_folder=str(self.tmp / "team-agents"))
        self.assertIn("team-agents/roles/engineering/architect.md\n", out.getvalue())
        self.assertNotIn("agents/roles/engineering/architect.md\n", out.getvalue().replace("team-agents/", ""))

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
                             ("svc", "is a service, its project-overview.md and agents/"),
                             ("apps", 'holds the service "apps/web"')):
            with self.subTest(folder=folder), self.assertRaises(project.ProjectFileError) as caught:
                workspace.check_team_folder(str(self.tmp), folder, str(self.base))
            self.assertIn(said, str(caught.exception))
        workspace.check_team_folder(str(self.tmp), "team-agents", str(self.base))     # its team files are no service
        (self.tmp / "plain" / "agents").mkdir(parents=True)        # an agents/ in no service's folder: warned by sync
        workspace.check_team_folder(str(self.tmp), "plain", str(self.base))

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

    def test_service_dot_reads_as_1_7_0_did(self):
        out = io.StringIO()
        (self.base / "agents" / "roles" / "engineering" / "lead-backend.md").write_text("base", encoding="utf-8")
        (self.tmp / "agents" / "roles" / "engineering" / "lead-backend.md").write_text(
            "<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
            "     Semantic: additive\n-->\n## Ours (additive)\n", encoding="utf-8")
        for service in (".", "./"):
            with self.subTest(service=service):
                out = io.StringIO()
                validate.validate(str(self.tmp), str(self.base), service, False, out, validate.Colors(False))
                self.assertIn("architect.md\n", out.getvalue())
                self.assertIn("overlay path 'agents/roles/engineering/lead-backend.md' must mirror base", out.getvalue())
        out = io.StringIO()
        validate.validate(str(self.tmp), str(self.base), ".", False, out, validate.Colors(False))
        self.assertIn("⚠ ./agents/roles/engineering/architect.md\n", out.getvalue())

    def test_the_validator_s_messages_name_the_team_s_folder(self):
        overlay = self.tmp / "team-agents" / "roles" / "engineering" / "architect.md"
        overlay.write_text("<!-- OVERLAY\n     Base: cortex/agents/roles/engineering/architect.md\n     Scope: workspace\n"
                           "     Semantic: replacement\n-->\n# x\n", encoding="utf-8")
        out = io.StringIO()
        validate.validate(str(self.tmp), str(self.base), "", False, out, validate.Colors(False),
                          team_folder=str(self.tmp / "team-agents"))
        self.assertIn("replacement is only allowed for files under team-agents/workflows/", out.getvalue())
        empty = Path(tempfile.mkdtemp(prefix="cortex-empty-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(empty, ignore_errors=True))
        out = io.StringIO()
        validate.validate(str(empty), str(self.base), "", False, out, validate.Colors(False),
                          team_folder=str(empty / "team-agents"))
        self.assertIn("(no team-agents/ directory at workspace level, no agents/ at service level)", out.getvalue())

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
