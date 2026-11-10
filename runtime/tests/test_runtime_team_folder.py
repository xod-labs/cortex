"""The runtime reads the team's folder ``cortex.toml`` names — ADR-013 §3.6 and §3.12, phase 3.

``agents = "team-agents"`` moves the workspace tier of the cascade, the team's context files and the
team's file; an ``agents/`` left behind is not read, and a service is never the team's folder.
"""

import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_base_root import FIXTURE, HAVE_FASTAPI, SyncedProjectTestCase  # noqa: E402

from cortex_runtime.base import Pin, read_pin  # noqa: E402
from cortex_runtime.run import RunRequest, resolve_run  # noqa: E402

FOLDER = "team-agents"


class TeamFolderTestCase(SyncedProjectTestCase):
    """The synced fixture, its ``agents/`` renamed ``team-agents/`` by ``cortex.toml``."""

    def setUp(self):
        super().setUp()
        shutil.move(str(self.project / "agents"), self.project / FOLDER)
        (self.project / "cortex.toml").write_text(f'version = "9.9.9"\ntheme = "h2g2"\nagents = "{FOLDER}"\n',
                                                  encoding="utf-8")
        self.synced(project="9.9.9")

    def synced(self, agents=FOLDER, **versions):
        """cortex.local.toml's synced, as `cortex sync` records it: the versions read, and the team's folder."""
        pinned = versions.get("project") or versions.get("team")
        fields = [f'{key} = "{value}"' for key, value in versions.items()]
        fields += [f'pinned = "{pinned}"', f'reads = "{pinned}"'] + ([f'agents = "{agents}"'] if agents else [])
        (self.project / "cortex.local.toml").write_text(f"synced = {{ {', '.join(fields)} }}\n", encoding="utf-8")


class PinTests(TeamFolderTestCase):
    def test_the_pin_names_the_team_s_folder_and_a_queued_job_keeps_it(self):
        pin = read_pin(self.project)
        self.assertEqual((pin.version, pin.team_folder), ("9.9.9", FOLDER))
        self.assertEqual(Pin.from_job(pin.to_job()), pin)
        self.assertEqual(Pin.from_job({"version": "9.9.9", "theme": "h2g2"}).team_folder, "agents")   # a job of before

    def test_the_team_s_file_is_read_in_the_folder(self):
        (self.project / "cortex.toml").write_text(f'theme = "h2g2"\nagents = "{FOLDER}"\n', encoding="utf-8")
        (self.project / FOLDER / "cortex.team.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
        self.synced(team="9.9.9")
        self.assertEqual(read_pin(self.project).version, "9.9.9")
        (self.project / FOLDER / "cortex.team.toml").unlink()
        with self.assertRaisesRegex(ValueError, '"version" is required'):
            read_pin(self.project)

    def test_an_uncommitted_cortex_toml_as_synced_shows_it_names_no_team_s_folder(self):
        # The team's file won over cortex.toml: it is not committed (decision 24), and its agents not read (§3.6).
        (self.project / "cortex.toml").write_text(f'version = "9.9.8"\ntheme = "h2g2"\nagents = "{FOLDER}"\n',
                                                  encoding="utf-8")
        (self.project / FOLDER / "cortex.team.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
        (self.project / "cortex.local.toml").write_text(
            'synced = { team = "9.9.9", project = "9.9.8", pinned = "9.9.9", reads = "9.9.9" }\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, '"agents" is read from a committed cortex.toml only'):
            read_pin(self.project)
        (self.project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\nagents = "agents"\n',
                                                  encoding="utf-8")
        (self.project / "agents").mkdir()
        (self.project / "agents" / "cortex.team.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
        self.assertEqual(read_pin(self.project).team_folder, "agents")      # the default, named: never refused

    def test_a_team_s_folder_sync_did_not_record_is_refused(self):
        # A folder sync never read — an uncommitted cortex.toml it refused, a key added since — is not read (§3.12).
        for synced in ({"agents": None}, {"agents": "other-agents"}):
            with self.subTest(synced=synced):
                self.synced(project="9.9.9", **synced)
                with self.assertRaisesRegex(ValueError, "is not the team's folder `cortex sync` recorded"):
                    read_pin(self.project)
        (self.project / "cortex.local.toml").unlink()
        with self.assertRaisesRegex(ValueError, "is not the team's folder `cortex sync` recorded"):
            read_pin(self.project)

    def test_a_team_s_folder_sync_recorded_and_cortex_toml_no_longer_names_is_refused(self):
        # A teammate dropped agents from cortex.toml: the template reads the folder synced records, the runtime
        # agents/ — one sync puts them back on one folder (§3.12).
        (self.project / "agents").mkdir()
        for text in ('version = "9.9.9"\ntheme = "h2g2"\n', 'version = "9.9.9"\ntheme = "h2g2"\nagents = "agents"\n'):
            with self.subTest(text=text):
                (self.project / "cortex.toml").write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, rf'^cortex\.local\.toml: the team\'s folder `cortex sync` '
                                                        rf'recorded, "{FOLDER}", is not the one cortex\.toml names, '
                                                        rf"agents/ — run `cortex sync` in the project$"):
                    read_pin(self.project)
                self.synced(project="9.9.9", agents=None)
                self.assertEqual(read_pin(self.project).team_folder, "agents")
                self.synced(project="9.9.9")

    def test_what_cortex_sync_refuses_of_agents_the_runtime_refuses(self):
        (self.project / "other").mkdir()
        (self.project / "other" / "cortex.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
        for value, said in (("../x", "climbs out"), ("/x", "absolute path"), ("cortex/x", "the spec's place"),
                            ("svc-a/agents", 'inside the service "svc-a"'), ("other", "holds a project's cortex.toml")):
            with self.subTest(value=value):
                (self.project / "cortex.toml").write_text(f'version = "9.9.9"\ntheme = "h2g2"\nagents = "{value}"\n',
                                                          encoding="utf-8")
                with self.assertRaisesRegex(ValueError, said):
                    read_pin(self.project)


class ResolveTests(TeamFolderTestCase):
    def request(self, service="svc-a"):
        return RunRequest(workspace="host", role="lead-backend", service=service, workflow="code-review")

    def test_the_run_reads_the_team_s_folder_as_the_fixture_read_agents(self):
        expected = resolve_run(self.request(), FIXTURE, theme="h2g2")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertEqual(run.system_prompt, expected.system_prompt)
        self.assertEqual(run.workflow, expected.workflow)
        self.assertEqual((run.capabilities, run.layers), (expected.capabilities, expected.layers))
        for token in ("WORKSPACE-RULE", "SERVICE-RULE", "WORKSPACE-WORKFLOW"):
            self.assertIn(token, run.system_prompt + (run.workflow or ""))

    def test_an_agents_left_behind_is_not_read(self):
        left = self.project / "agents" / "roles" / "engineering"
        left.mkdir(parents=True)
        (left / "lead-backend.md").write_text("LEFT-BEHIND\n", encoding="utf-8")
        (self.project / "agents" / "project-context.md").write_text("LEFT-CONTEXT: Rust\n", encoding="utf-8")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertNotIn("LEFT-BEHIND", run.system_prompt)
        self.assertNotIn("LEFT-CONTEXT", run.system_prompt)
        self.assertIn("WORKSPACE-RULE", run.system_prompt)

    def test_the_team_s_context_and_capabilities_are_the_folder_s(self):
        (self.project / FOLDER / "project-context.md").write_text("TEAM-CONTEXT: we write Rust and TypeScript\n",
                                                                  encoding="utf-8")
        caps = self.project / FOLDER / "capabilities" / "languages"
        caps.mkdir(parents=True)
        (caps / "rust.md").write_text("TEAM-ONLY-CAP\n", encoding="utf-8")         # in no other tier
        (caps / "typescript.md").write_text("TEAM-CAP\n", encoding="utf-8")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertIn("TEAM-CONTEXT", run.system_prompt)
        self.assertIn("languages/rust.md", run.capabilities)
        self.assertIn("languages/typescript.md", run.capabilities)
        self.assertIn("TEAM-ONLY-CAP", run.system_prompt)
        self.assertIn("TEAM-CAP", run.system_prompt)

    def test_the_service_whose_agents_is_the_team_s_folder_is_refused(self):
        (self.project / "team").mkdir()
        shutil.move(str(self.project / FOLDER), self.project / "team" / "agents")
        (self.project / "cortex.toml").write_text('version = "9.9.9"\ntheme = "h2g2"\nagents = "team/agents"\n',
                                                  encoding="utf-8")
        self.synced(agents="team/agents", project="9.9.9")
        self.assertIn("WORKSPACE-RULE", resolve_run(self.request(), self.project, theme="h2g2").system_prompt)
        for service in ("team", "Team"):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "whose agents/ is the team's folder"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")

    def test_the_team_s_folder_is_no_service(self):
        (self.project / FOLDER / "project-overview.md").write_text("# The team\n", encoding="utf-8")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertNotIn(f"`{FOLDER}/`", run.system_prompt)
        for service in (FOLDER, f"{FOLDER}/roles", FOLDER.upper()):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "the team's folder"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")

    def test_a_service_whose_name_folds_further_is_no_team_s_folder(self):
        # NTFS and APFS take straße and strasse for two folders: so does the runtime.
        shutil.move(str(self.project / FOLDER), self.project / "strasse")
        (self.project / "cortex.toml").write_text('version = "9.9.9"\ntheme = "h2g2"\nagents = "strasse"\n',
                                                  encoding="utf-8")
        self.synced(agents="strasse", project="9.9.9")
        (self.project / "straße" / "agents").mkdir(parents=True)
        (self.project / "straße" / "project-overview.md").write_text("# straße\n", encoding="utf-8")
        resolve_run(self.request(service="straße"), self.project, theme="h2g2")
        with self.assertRaisesRegex(ValueError, "the team's folder"):
            resolve_run(self.request(service="STRASSE"), self.project, theme="h2g2")


def _link(test, target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        test.skipTest(f"no symbolic link here: {error}")


class AgentsOnTheDiskTests(SyncedProjectTestCase):
    """``agents/`` is refused as written, as 1.7.0 refused it, and as the disk opens it — never by its name
    folded: on Linux, ``Agents/`` is a folder of its own, a service 1.7.0 served (ADR-013 §9)."""

    request = ResolveTests.request

    def ignores_case(self):
        return (self.project / "AGENTS").exists()

    def test_a_service_agents_names_in_another_case_is_served_where_it_is_another_folder(self):
        if self.ignores_case():
            self.skipTest("this disk ignores case: Agents is agents/")
        shutil.copytree(self.project / "svc-a", self.project / "Agents")
        shutil.copytree(self.project / "svc-a", self.project / "agents-api")
        for service in ("Agents", "agents-api"):
            with self.subTest(service=service):
                run = resolve_run(self.request(service=service), self.project, theme="h2g2")
                self.assertIn("SERVICE-RULE", run.system_prompt)        # its own overlays, as in 1.7.0

    def test_a_service_the_disk_opens_as_agents_is_refused(self):
        if self.ignores_case():
            services = ("Agents", "AGENTS/roles")
        else:
            _link(self, self.project / "agents", self.project / "alias")
            services = ("alias", "alias/roles", "./alias/")
        for service in ("agents", "agents/roles", *services):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "other than agents/"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")

    def test_a_service_whose_agents_the_disk_opens_as_agents_is_refused(self):
        # A link to agents/, or to the root: the workspace's overlays read twice, the second time as a service's.
        (self.project / "svc-b").mkdir()
        _link(self, self.project / "agents", self.project / "svc-b" / "agents")
        _link(self, self.project, self.project / "root-alias")
        for service in ("svc-b", "root-alias", "./svc-b/"):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "whose agents/ is the team's "
                                                                                   "folder, agents/"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")
        run = resolve_run(self.request(service="svc-a"), self.project, theme="h2g2")      # an agents/ of its own
        self.assertIn("SERVICE-RULE", run.system_prompt)

    def test_agents_as_written_is_refused_where_the_disk_has_none(self):
        shutil.rmtree(self.project / "agents")
        with self.assertRaisesRegex(ValueError, "other than agents/"):
            resolve_run(self.request(service="agents/roles"), self.project, theme="h2g2")
        if not self.ignores_case():
            resolve_run(self.request(service="Agents"), self.project, theme="h2g2")


class TeamFolderOnTheDiskTests(TeamFolderTestCase):
    request = ResolveTests.request

    def test_a_service_the_disk_opens_as_the_team_s_folder_is_refused(self):
        _link(self, self.project / FOLDER, self.project / "alias")
        for service in ("alias", "alias/roles"):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "the team's folder, got"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")

    def test_a_service_whose_agents_the_disk_opens_as_the_team_s_folder_is_refused(self):
        (self.project / "svc-b").mkdir()
        _link(self, self.project / FOLDER, self.project / "svc-b" / "agents")
        with self.assertRaisesRegex(ValueError, "whose agents/ is the team's folder"):
            resolve_run(self.request(service="svc-b"), self.project, theme="h2g2")


@unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
class ApiTests(TeamFolderTestCase):
    def test_an_agents_cortex_sync_refuses_is_a_422(self):
        from fastapi.testclient import TestClient

        from cortex_runtime.api import create_app
        from cortex_runtime.app import WorkspaceConfig
        from cortex_runtime.runtime import build_runtime
        from cortex_runtime.state_store import InMemoryStateStore

        client = TestClient(create_app(build_runtime({"host": WorkspaceConfig(root=self.project, theme="h2g2")},
                                                     store=InMemoryStateStore(), model_backend="demo")))
        r = client.post("/resolve", json={"workspace": "host", "role": "lead-backend", "subject": "ACME-11"})
        self.assertEqual(r.status_code, 200, r.text)
        (self.project / "cortex.toml").write_text('version = "9.9.9"\ntheme = "h2g2"\nagents = "../x"\n',
                                                  encoding="utf-8")
        r = client.post("/resolve", json={"workspace": "host", "role": "lead-backend", "subject": "ACME-11"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('agents "../x"', r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
