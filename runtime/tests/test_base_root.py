"""The runtime finds the base through ``cortex.toml`` — ADR-008 §3.6, phase 3.

A synced project holds no spec: its ``cortex.toml`` pins a version, which the runtime reads from
the store, ``{CORTEX_HOME}/versions/{version}``. A project without ``cortex.toml`` resolves as it
always has, from ``{root}/cortex``. A version the store lacks is refused at submission.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_runtime.base import base_root_for  # noqa: E402
from cortex_runtime.runtime import build_runtime as build  # noqa: E402
from cortex_runtime.run import RunRequest, resolve_run  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "host"

try:
    from fastapi.testclient import TestClient  # noqa: E402

    from cortex_runtime.api import create_app  # noqa: E402
    from cortex_runtime.app import WorkspaceConfig  # noqa: E402
    from cortex_runtime.runtime import build_runtime  # noqa: E402
    from cortex_runtime.state_store import InMemoryStateStore  # noqa: E402
    HAVE_FASTAPI = True
except Exception:
    HAVE_FASTAPI = False


class SyncedProjectTestCase(unittest.TestCase):
    """The fixture project, synced: its base moved to a store, ``cortex.toml`` pinning it."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="cortex-base-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.project = tmp / "project"
        shutil.copytree(FIXTURE, self.project)
        self.home = tmp / "cortex-home"
        (self.home / "versions").mkdir(parents=True)
        shutil.move(str(self.project / "cortex"), self.home / "versions" / "9.9.9")
        (self.project / "cortex.toml").write_text('version = "9.9.9"\ntheme = "h2g2"\n', encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"CORTEX_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, **extra):
        return RunRequest(workspace="host", role="lead-backend", service="svc-a", workflow="code-review", **extra)


class BaseRootTests(SyncedProjectTestCase):
    def test_the_base_is_the_stores_copy_of_the_pinned_version(self):
        self.assertEqual(base_root_for(self.project), self.home / "versions" / "9.9.9")

    def test_a_synced_project_resolves_as_the_fixture_did(self):
        expected = resolve_run(self.request(), FIXTURE, theme="h2g2")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertEqual(run.system_prompt, expected.system_prompt)
        self.assertEqual(run.workflow, expected.workflow)
        self.assertEqual((run.capabilities, run.layers), (expected.capabilities, expected.layers))
        for token in ("BASE-THEME", "BASE-CHARACTER", "BASE-ROLE", "WORKSPACE-RULE", "SERVICE-RULE", "BASE-CAP"):
            self.assertIn(token, run.system_prompt)

    def test_a_cortex_directory_left_in_the_project_is_not_read(self):
        # The pinned version decides; an old copy — or cortex.local.toml's spec — never does.
        stale = self.project / "cortex" / "agents" / "roles" / "engineering"
        stale.mkdir(parents=True)
        (stale / "lead-backend.md").write_text("# STALE-ROLE\n", encoding="utf-8")
        (self.project / "cortex.local.toml").write_text('spec = "cortex"\n', encoding="utf-8")
        run = resolve_run(self.request(), self.project, theme="h2g2")
        self.assertIn("BASE-ROLE", run.system_prompt)
        self.assertNotIn("STALE-ROLE", run.system_prompt)

    def test_a_version_missing_from_the_store_is_refused_and_named(self):
        (self.project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uses Cortex 9.9.8, which is not in the store"):
            resolve_run(self.request(), self.project, theme="h2g2")

    def test_a_version_that_is_no_version(self):
        for text in ('version = "../../etc"\n', 'version = 1\n', 'theme = "h2g2"\n', 'version = "1.0.0/x"\n', "not toml ="):
            with self.subTest(text=text):
                (self.project / "cortex.toml").write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    base_root_for(self.project)

    def test_what_cortex_sync_refuses_the_runtime_refuses(self):
        # One grammar, the core's: a 422 never advises `cortex sync` on a file sync would refuse.
        for store_name in ("01.0.0", "1.0.0-01", "1.0.0\n", "１.0.0"):
            (self.home / "versions" / store_name).mkdir()
            (self.home / "versions" / store_name / "agents").mkdir()
        for text in ('version = "01.0.0"\ntheme = "h2g2"\n', 'version = "1.0.0-01"\ntheme = "h2g2"\n',
                     'version = "1.0.0\\n"\ntheme = "h2g2"\n', 'version = "１.0.0"\ntheme = "h2g2"\n',
                     'version = "9.9.9"\n', 'version = "9.9.9"\ntheme = "h2g2"\nsync = "bogus"\n',
                     'version = "9.9.9"\ntheme = "h2g2"\ntypo = "x"\n', 'version = "9.9.9"\ntheme = "../x"\n'):
            with self.subTest(text=text):
                (self.project / "cortex.toml").write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "cortex sync refuses this file too"):
                    base_root_for(self.project)

    def test_a_cortex_toml_that_is_no_file_is_refused(self):
        (self.project / "cortex.toml").unlink()
        (self.project / "cortex.toml").mkdir()
        with self.assertRaisesRegex(ValueError, "is not a file"):
            base_root_for(self.project)
        (self.project / "cortex.toml").rmdir()
        (self.project / "cortex.toml").symlink_to(self.project / "nowhere.toml")
        with self.assertRaisesRegex(ValueError, "is not a file"):
            base_root_for(self.project)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs a file its owner cannot read")
    def test_an_unreadable_cortex_toml_is_refused(self):
        (self.project / "cortex.toml").chmod(0)
        self.addCleanup((self.project / "cortex.toml").chmod, 0o644)
        with self.assertRaisesRegex(ValueError, "cannot be read"):
            base_root_for(self.project)

    def test_a_version_directory_without_agents_is_not_in_the_store(self):
        (self.home / "versions" / "9.9.8").mkdir()
        (self.project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "uses Cortex 9.9.8, which is not in the store"):
            base_root_for(self.project)

    def test_the_theme_is_cortex_tomls_unless_the_deployment_names_one(self):
        run = resolve_run(self.request(), self.project)
        self.assertIn("BASE-THEME", run.system_prompt)
        self.assertIn(("personalities", "h2g2/theme.md"), run.layers)
        (self.project / "cortex.toml").write_text('version = "9.9.9"\ntheme = "none"\n', encoding="utf-8")
        self.assertNotIn("BASE-THEME", resolve_run(self.request(), self.project).system_prompt)
        self.assertIn("BASE-THEME", resolve_run(self.request(), self.project, theme="h2g2").system_prompt)

    def test_the_run_names_the_version_it_resolved_on(self):
        self.assertEqual(resolve_run(self.request(), self.project).cortex_version, "9.9.9")
        self.assertIsNone(resolve_run(self.request(), FIXTURE).cortex_version)


class QueuedRunTests(SyncedProjectTestCase):
    """A queued run resolves on what cortex.toml said when it was accepted — ADR-002 §9."""

    def runtime(self):
        from cortex_runtime.app import WorkspaceConfig
        from cortex_runtime.state_store import InMemoryStateStore
        return build({"host": WorkspaceConfig(root=self.project, theme="h2g2")},
                     store=InMemoryStateStore(), model_backend="demo")

    def payload(self):
        return {"workspace": "host", "role": "support-engineer", "subject": "ACME-9", "input": {"issue": "ACME-9"}}

    def test_a_version_changed_between_prepare_and_execute(self):
        runtime = self.runtime()
        prepared = runtime.prepare(self.payload(), run_id="r1")
        self.assertEqual(prepared["cortex_version"], "9.9.9")
        # A pull of the mirror bumps the version before the host has synced it.
        (self.project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        result = runtime.execute({"run_id": "r1", "payload": self.payload(), "cortex": prepared["cortex"]})
        self.assertFalse(result.get("failed"), result)
        self.assertEqual(result["cortex_version"], "9.9.9")
        self.assertEqual(runtime.cfg.store.get_run("r1").lifecycle, "done")

    def test_a_skipped_run_names_its_run_and_its_version(self):
        runtime = self.runtime()
        runtime.execute({"run_id": "r3", "payload": self.payload(), "cortex": runtime.prepare(self.payload(), run_id="r3")["cortex"]})
        # The same subject again: its state is no longer awaiting-agent, and the run is skipped.
        prepared = runtime.prepare(self.payload(), run_id="r4")
        result = runtime.execute({"run_id": "r4", "payload": self.payload(), "cortex": prepared["cortex"]})
        self.assertTrue(result["skipped"], result)
        self.assertEqual((result["run_id"], result["cortex_version"]), ("r4", "9.9.9"))

    def test_a_version_gone_from_the_store_fails_the_run_instead_of_leaving_it_queued(self):
        runtime = self.runtime()
        prepared = runtime.prepare(self.payload(), run_id="r2")
        shutil.rmtree(self.home / "versions" / "9.9.9")
        with self.assertRaisesRegex(ValueError, "Cortex 9.9.9"):
            runtime.execute({"run_id": "r2", "payload": self.payload(), "cortex": prepared["cortex"]})
        record = runtime.cfg.store.get_run("r2")
        self.assertEqual(record.lifecycle, "failed")
        self.assertIn("Cortex 9.9.9", record.error)


class CascadeTests(SyncedProjectTestCase):
    """The pin is the core's resolution, the team's (ADR-013 §3.1, §3.12): cortex.local.toml left out,
    and no git asked — the container's read-only mount of the host's files may make git refuse."""

    def test_the_developer_s_local_file_and_git_are_not_read(self):
        from cortex_runtime.base import read_pin

        (self.project / "cortex.local.toml").write_text('theme = "none"\nspec = "/host/path"\n', encoding="utf-8")
        refused = mock.Mock(side_effect=AssertionError("the runtime asked git"))
        with mock.patch("subprocess.run", refused), mock.patch("subprocess.Popen", refused), \
                mock.patch("shutil.which", refused):
            pin = read_pin(self.project)
        self.assertEqual((pin.version, pin.theme), ("9.9.9", "h2g2"))

    @unittest.skipIf(os.name == "nt", "a read-only directory, as POSIX permissions make it")
    def test_a_read_only_mount_resolves_what_the_host_s_cortex_config_locked_prints(self):
        import json
        import subprocess

        from cortex_runtime.base import read_pin

        repo = Path(__file__).resolve().parents[2]
        call = ("import sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; "
                "from cortex_cli.main import main; sys.exit(main())")
        env = {"CORTEX_HOME": str(self.home), "CORTEX_SOURCE_VERSION": "9.9.9", "CORTEX_NO_REGISTER": "1",
               "CORTEX_NO_UPDATE_CHECK": "1", "PATH": "", "HOME": str(self.home)}
        proc = subprocess.run([sys.executable, "-I", "-S", "-c", call, str(repo / "core"), str(repo / "cli"),
                               "config", "--locked", "--json"], cwd=self.project, env=env, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        host = json.loads(proc.stdout)["settings"]
        mode = self.project.stat().st_mode
        for path in (self.project / "cortex.toml", self.project):
            path.chmod(0o555 if path.is_dir() else 0o444)
        self.addCleanup(self.project.chmod, mode)
        self.addCleanup((self.project / "cortex.toml").chmod, 0o644)
        pin = read_pin(self.project)
        self.assertEqual((pin.version, pin.theme), (host["version"]["value"], host["theme"]["value"]))


class TeamVersionTests(SyncedProjectTestCase):
    """The runtime reads the team's version, asking no git (ADR-013 §3.12): what ``cortex sync`` recorded in
    ``synced``, once each file's ``version`` is still what it recorded — a ``422`` naming ``cortex sync``
    otherwise; without ``synced``, ``cortex.toml``'s, else the team's file's."""

    def files(self, toml, team=None, local=None):
        from cortex_runtime.base import read_pin

        (self.project / "cortex.toml").write_text(toml, encoding="utf-8")
        if team is not None:
            (self.project / "agents" / "cortex.team.toml").write_text(team, encoding="utf-8")
        if local is not None:
            (self.project / "cortex.local.toml").write_text(local, encoding="utf-8")
        return lambda: read_pin(self.project)

    def test_at_a_2b_root_the_team_s_version_as_the_cli_reads_it(self):
        # An uncommitted cortex.toml at 1.7.0 beside a team's file at 1.8.0: the CLI reads 1.8.0 and records it.
        fresh = 'synced = { team = "1.8.0", project = "1.7.0", pinned = "1.8.0", reads = "1.8.0" }\n'
        pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\n', local=fresh)
        self.assertEqual(pin().version, "1.8.0")
        (self.project / "agents" / "cortex.team.toml").write_text('version = "1.8.1"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"run `cortex sync`"):          # stale: a teammate moved it
            pin()
        (self.project / "cortex.local.toml").unlink()
        with self.assertRaisesRegex(ValueError, r"cortex.toml pins Cortex 1.7.0 and agents/cortex.team.toml Cortex "
                                                r"1.8.1, and `cortex sync` recorded neither: run `cortex sync`"):
            pin()                                                                # absent: the two differ

    def test_the_team_s_file_moved_then_cortex_toml_edited_to_its_old_value_is_refused(self):
        tie = 'synced = { team = "1.8.0", project = "1.8.0", pinned = "1.8.0", reads = "1.8.0" }\n'
        pin = self.files('version = "1.8.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\n', local=tie)
        self.assertEqual(pin().version, "1.8.0")
        (self.project / "agents" / "cortex.team.toml").write_text('version = "1.9.0"\n', encoding="utf-8")
        (self.project / "cortex.toml").write_text('version = "1.8.0"\ntheme = "h2g2"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"not those `cortex sync` recorded .* run `cortex sync`"):
            pin()

    def test_without_synced_cortex_toml_s_version_else_the_team_s_file_s(self):
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', team='version = "9.9.9"\n')
        self.assertEqual(pin().version, "9.9.9")
        pin = self.files('theme = "h2g2"\n', team='version = "1.8.0"\n')
        self.assertEqual(pin().version, "1.8.0")
        pin = self.files('version = "1.8.0"\ntheme = "h2g2"\n', team='version = "1.9.0"\n')
        with self.assertRaisesRegex(ValueError, r"`cortex sync` recorded neither: run `cortex sync`"):
            pin()

    def test_a_local_version_above_the_team_s_is_refused_naming_it(self):
        recorded = 'version = "9.9.9"\nsynced = { project = "9.9.8", local = "9.9.9", pinned = "9.9.8", reads = "9.9.9" }\n'
        pin = self.files('version = "9.9.8"\ntheme = "h2g2"\n', local=recorded)
        with self.assertRaisesRegex(ValueError, r"reads Cortex 9\.9\.9, cortex\.local\.toml's version — a try"):
            pin()
        pin = self.files('version = "9.9.8"\ntheme = "h2g2"\n', local='version = "9.9.9"\n')
        with self.assertRaisesRegex(ValueError, r"reads Cortex 9\.9\.9, cortex\.local\.toml's version — a try"):
            pin()
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', local='version = "9.9.8"\n')
        self.assertEqual(pin().version, "9.9.9")                                # below the team's: changes nothing

    def test_a_cortex_local_toml_that_does_not_read_is_none_as_in_1_7_0(self):
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', local="spec = [\n")
        self.assertEqual(pin().version, "9.9.9")

    def test_cortex_theme_is_under_the_team_s_lock(self):
        self.files('version = "9.9.9"\n', team='theme = "h2g2"\nlocked = ["theme"]\n')
        self.assertIn("BASE-THEME", resolve_run(self.request(), self.project, theme="none").system_prompt)
        (self.project / "agents" / "cortex.team.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        self.assertNotIn("BASE-THEME", resolve_run(self.request(), self.project, theme="none").system_prompt)
        self.assertIn("BASE-THEME", resolve_run(self.request(), self.project).system_prompt)

    def test_a_queued_run_keeps_its_lock_on_the_theme(self):
        from cortex_runtime.base import Pin

        pin = Pin(version="9.9.9", theme="h2g2", theme_locked=True)
        self.assertEqual(Pin.from_job(pin.to_job()), pin)
        self.assertFalse(Pin.from_job({"version": "9.9.9", "theme": "h2g2"}).theme_locked)     # a job of 1.7.0


class WithoutCortexTomlTests(unittest.TestCase):
    def test_the_base_stays_under_the_project(self):
        self.assertIsNone(base_root_for(FIXTURE))

    def test_cortex_home_changes_nothing_for_it(self):
        before = resolve_run(RunRequest(workspace="host", role="lead-backend", service="svc-a"), FIXTURE, theme="h2g2")
        with mock.patch.dict(os.environ, {"CORTEX_HOME": "/nonexistent"}):
            after = resolve_run(RunRequest(workspace="host", role="lead-backend", service="svc-a"), FIXTURE, theme="h2g2")
        self.assertEqual(after.system_prompt, before.system_prompt)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi (+ test client deps) not installed")
class ApiTests(SyncedProjectTestCase):
    def client(self, queue=False):
        runtime = build_runtime({"host": WorkspaceConfig(root=self.project, theme="h2g2")},
                                store=InMemoryStateStore(), model_backend="demo")
        if not queue:
            return TestClient(create_app(runtime))
        job_queue = runtime.build_queue()
        job_queue.start()
        self.addCleanup(job_queue.shutdown)
        return TestClient(create_app(runtime, queue=job_queue))

    def test_a_synced_project_runs(self):
        r = self.client().post("/run", json={"workspace": "host", "role": "support-engineer", "subject": "ACME-7",
                                             "input": {"issue": "ACME-7"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cortex_version"], "9.9.9")

    def test_an_accepted_run_names_its_version(self):
        r = self.client(queue=True).post("/run", json={"workspace": "host", "role": "support-engineer",
                                                       "subject": "ACME-10", "input": {"issue": "ACME-10"}})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["cortex_version"], "9.9.9")

    def test_a_version_missing_from_the_store_is_a_422_naming_it(self):
        (self.project / "cortex.toml").write_text('version = "9.9.8"\ntheme = "h2g2"\n', encoding="utf-8")
        for queue in (False, True):
            with self.subTest(asynchronous=queue):
                client = self.client(queue)
                for path in ("/run", "/resolve"):
                    r = client.post(path, json={"workspace": "host", "role": "lead-backend", "subject": "ACME-8"})
                    self.assertEqual(r.status_code, 422, r.text)
                    self.assertIn("Cortex 9.9.8", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
