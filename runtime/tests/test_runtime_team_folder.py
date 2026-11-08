"""The runtime reads the team's folder ``cortex.toml`` names — ADR-013 §3.6 and §3.12, phase 3.

``agents = "team-agents"`` moves the workspace tier of the cascade, the team's context files and the
team's file; an ``agents/`` left behind is not read, and a service is never the team's folder.
"""

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


class PinTests(TeamFolderTestCase):
    def test_the_pin_names_the_team_s_folder_and_a_queued_job_keeps_it(self):
        pin = read_pin(self.project)
        self.assertEqual((pin.version, pin.team_folder), ("9.9.9", FOLDER))
        self.assertEqual(Pin.from_job(pin.to_job()), pin)
        self.assertEqual(Pin.from_job({"version": "9.9.9", "theme": "h2g2"}).team_folder, "agents")   # a job of before

    def test_the_team_s_file_is_read_in_the_folder(self):
        (self.project / "cortex.toml").write_text(f'theme = "h2g2"\nagents = "{FOLDER}"\n', encoding="utf-8")
        (self.project / FOLDER / "cortex.team.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
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

    def test_the_team_s_folder_is_no_service(self):
        (self.project / FOLDER / "project-overview.md").write_text("# The team\n", encoding="utf-8")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertNotIn(f"`{FOLDER}/`", run.system_prompt)
        for service in (FOLDER, f"{FOLDER}/roles"):
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, "the team's folder"):
                resolve_run(self.request(service=service), self.project, theme="h2g2")


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
