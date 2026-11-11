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


def _repository_above(directory):
    """A ``.git`` at ``directory`` or above it, as written or resolved: the folder that holds it, or ``None``."""
    for start in {directory, os.path.realpath(directory)}:
        for folder in (start, *map(str, Path(start).parents)):
            if os.path.lexists(os.path.join(folder, ".git")):
                return folder
    return None


REPOSITORY_ABOVE_TEMP = _repository_above(tempfile.gettempdir())
# The source's cortex runs here without git on its PATH: a project in a temporary directory inside a repository is
# then one whose cortex.toml counts as committed, which these tests' projects are not. Skipped, saying why — as the
# CLI's own tests are (cli/tests/cli_harness.py).
outside_a_repository = unittest.skipIf(
    REPOSITORY_ABOVE_TEMP is not None,
    f"needs a temporary directory in no git repository: {REPOSITORY_ABOVE_TEMP} is one — set TMPDIR outside it")

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
        self.assertEqual(pin().version, "1.7.0")        # absent: not synced by a 1.8 cortex — the spec it has

    def test_below_1_8_0_without_synced_cortex_toml_is_read_alone(self):
        # A committed pin before 1.8.0: the CLI reads it and records no synced — a 422 nothing could clear.
        for team in ('version = "1.8.0"\n', "version = \n", 'version = "1.8.0"\nagents = "x"\n'):
            with self.subTest(team=team):
                pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team=team)
                self.assertEqual((pin().version, pin().theme), ("1.7.0", "h2g2"))
        pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team="version = \n", local='version = "1.8.0"\n')
        with self.assertRaisesRegex(ValueError, "reads Cortex 1.8.0, cortex.local.toml's version — a try"):
            pin()                                                                # a try above it is still one
        pin = self.files('version = "1.8.0"\ntheme = "h2g2"\n', team="version = \n", local="")
        with self.assertRaisesRegex(ValueError, "cortex sync refuses this file too"):
            pin()                                                                # from 1.8.0, it is read

    def test_a_team_s_file_that_cannot_be_opened_is_a_422(self):
        # There and unread — a directory, a link that leads nowhere, a folder this user cannot search — is
        # refused as the CLI refuses it, never read as no file: the team's version would be lost (§3.5).
        team = self.project / "agents" / "cortex.team.toml"
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n')
        team.mkdir()
        with self.assertRaisesRegex(ValueError, "^agents/cortex.team.toml: cannot be read — not a file — "):
            pin()
        team.rmdir()
        try:
            os.symlink(self.project / "gone", team)
        except (OSError, NotImplementedError):
            pass                                                        # Windows without the right to make links
        else:
            with self.assertRaisesRegex(ValueError, "^agents/cortex.team.toml: cannot be read — a link that leads "
                                                    "nowhere — "):
                pin()
            team.unlink()
        if os.name == "posix" and os.geteuid() != 0:                    # root searches any folder
            team.write_text('version = "9.9.9"\n', encoding="utf-8")
            (self.project / "agents").chmod(0)
            try:
                with self.assertRaisesRegex(ValueError, "^agents/cortex.team.toml: cannot be read — "):
                    pin()
            finally:
                (self.project / "agents").chmod(0o755)

    def test_at_a_2b_root_the_team_s_theme_as_cortex_config_locked_gives_it(self):
        # An uncommitted cortex.toml's theme is left out where the team's file sets one (ADR-013 §3.1).
        recorded = 'synced = { team = "9.9.9", project = "9.9.8", pinned = "9.9.9", reads = "9.9.9" }\n'
        pin = self.files('version = "9.9.8"\ntheme = "none"\n', team='version = "9.9.9"\ntheme = "h2g2"\n',
                         local=recorded)
        self.assertEqual((pin().version, pin().theme), ("9.9.9", "h2g2"))

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

    def test_a_version_a_file_gained_or_lost_since_the_sync_is_refused(self):
        # Each candidate is compared both ways: one `cortex sync` recorded that its file no longer pins, and one a
        # file pins that it did not record — a team's file added since, a try written by hand.
        recorded = 'synced = { team = "9.9.9", project = "9.9.9", pinned = "9.9.9", reads = "9.9.9" }\n'
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', team='version = "9.9.9"\n', local=recorded)
        self.assertEqual(pin().version, "9.9.9")
        (self.project / "agents" / "cortex.team.toml").write_text('theme = "h2g2"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"not those `cortex sync` recorded .* run `cortex sync`"):
            pin()                                                                # lost: the team's file's version
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', team='version = "9.9.9"\n',
                         local='synced = { project = "9.9.9", pinned = "9.9.9", reads = "9.9.9" }\n')
        with self.assertRaisesRegex(ValueError, r"not those `cortex sync` recorded .* run `cortex sync`"):
            pin()                                                                # gained: a team's file since
        (self.project / "agents" / "cortex.team.toml").unlink()
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n',
                         local='version = "9.9.9"\nsynced = { project = "9.9.9", pinned = "9.9.9", reads = "9.9.9" }\n')
        with self.assertRaisesRegex(ValueError, r"not those `cortex sync` recorded .* run `cortex sync`"):
            pin()                                                                # gained: a try not synced

    def test_below_1_8_0_cortex_toml_is_checked_as_1_7_0_checked_it(self):
        # Read alone, it is still read with the grammar `cortex sync` reads it with: no theme out of the store.
        for text in ('version = "1.7.0"\ntheme = "../../x"\n', 'version = "1.7.0"\ntheme = "h2g2"\ntypo = "x"\n',
                     'version = "1.7.0"\n'):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "— cortex sync refuses this file too$"):
                    self.files(text)()

    def test_below_1_8_0_beside_a_team_s_file_the_remedy_is_a_sync(self):
        # Mid-adoption, at a root no repository holds: `cortex sync` reads the team's file over an uncommitted
        # cortex.toml, and takes the theme this one lacks from it — it does not refuse this file.
        pin = self.files('version = "1.7.0"\n', team='version = "1.8.0"\ntheme = "h2g2"\n')
        with self.assertRaisesRegex(ValueError, r'"theme" is required — run `cortex sync` in the project, which may '
                                                r"read agents/cortex\.team\.toml instead of a cortex\.toml not committed "
                                                r"— committed, it needs its theme$"):
            pin()

    def test_below_1_8_0_a_valid_cortex_toml_reads_no_team_s_file(self):
        # §3.12: before 1.8.0 the team's file is neither parsed nor read — but to tell what a refused cortex.toml lacks.
        from cortex_runtime import base

        pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team='version = "1.8.0"\ntheme = "h2g2"\n')
        with mock.patch.object(base.cascade, "read_team", side_effect=AssertionError("the team's file was read")):
            self.assertEqual((pin().version, pin().theme), ("1.7.0", "h2g2"))

    def test_below_1_8_0_beside_a_team_s_file_a_file_a_sync_refuses_too_says_so(self):
        # The team's file gives no theme out of the store, nor a key 1.7.0 did not know: `cortex sync` refuses
        # these whatever it reads, so naming it would send the user round in a circle.
        for text in ('version = "1.7.0"\ntheme = "../../x"\n', 'version = "1.7.0"\ntheme = "h2g2"\ntypo = "x"\n',
                     'version = "1.7.0"\nsync = "x"\n'):
            with self.subTest(text=text):
                pin = self.files(text, team='version = "1.8.0"\ntheme = "h2g2"\n')
                with self.assertRaisesRegex(ValueError, "— cortex sync refuses this file too$"):
                    pin()

    def cli(self, *args):
        """`cortex` from this checkout's source, in the project: what the host's own cortex says of its files."""
        import subprocess

        repo = Path(__file__).resolve().parents[2]
        call = ("import sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; "
                "from cortex_cli.main import main; sys.exit(main())")
        env = {"CORTEX_HOME": str(self.home), "CORTEX_SOURCE_VERSION": "9.9.9", "CORTEX_NO_REGISTER": "1",
               "CORTEX_NO_UPDATE_CHECK": "1", "PATH": "", "HOME": str(self.home)}
        return subprocess.run([sys.executable, "-I", "-S", "-c", call, str(repo / "core"), str(repo / "cli"), *args],
                              cwd=self.project, env=env, capture_output=True)

    def test_the_workflow_runs_these_tests_on_a_change_of_what_they_run(self):
        # cli() runs the CLI's source, and the runtime embeds the core: a change of either runs this suite, which a
        # path filter that leaves one out would skip — GitHub counts a skipped job as passing.
        workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "runtime-tests.yml")
        lines = workflow.read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "paths: |") + 1
        paths = []
        for line in lines[start:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" in line:
                break
            paths.append(line.strip())
        for folder in ("runtime/**", "core/**", "cli/**", ".github/workflows/runtime-tests.yml"):
            self.assertIn(folder, paths)

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_a_cortex_toml_naming_a_team_s_folder_names_no_sync(self):
        # `agents` is read from a committed cortex.toml of 1.8.0 or later only: the sync refuses this file, with
        # its theme or without, whatever the team's file gives — naming it would send the user round in a circle.
        for text in ('version = "1.7.0"\nagents = "team-agents"\n',
                     'version = "1.7.0"\ntheme = "h2g2"\nagents = "team-agents"\n'):
            with self.subTest(text=text):
                pin = self.files(text, team='version = "1.8.0"\ntheme = "h2g2"\n')
                with self.assertRaisesRegex(ValueError, "— cortex sync refuses this file too$") as caught:
                    pin()
                self.assertNotIn("run `cortex sync`", str(caught.exception))
                host = self.cli("config", "--locked")
                self.assertNotEqual(host.returncode, 0, host.stdout.decode())
                self.assertIn('"agents"', host.stderr.decode())

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_the_sync_is_named_only_where_it_reads_the_team_s_file(self):
        # `cortex sync` is named only where the host's cortex resolves these files — where the team's file pins
        # 1.8.0 or later and gives the theme cortex.toml lacks. A team's file that gives nothing in its place —
        # no version, one before 1.8.0, no theme, none that parses — and the sync refuses this file too.
        for team, named in (('version = "1.8.0"\ntheme = "h2g2"\n', True), ('theme = "h2g2"\n', False),
                            ('version = "1.7.5"\ntheme = "h2g2"\n', False), ('version = "1.8.0"\n', False),
                            ("version = [\n", False)):
            with self.subTest(team=team):
                pin = self.files('version = "1.7.0"\n', team=team)
                with self.assertRaises(ValueError) as caught:
                    pin()
                self.assertEqual("run `cortex sync`" in str(caught.exception), named, caught.exception)
                if not named:
                    self.assertTrue(str(caught.exception).endswith("— cortex sync refuses this file too"),
                                    caught.exception)
                host = self.cli("config", "--locked")         # what the sync named would read, or refuse
                self.assertEqual(host.returncode == 0, named, host.stderr.decode())

    def store_1_7_0(self):
        """1.7.0 in the store, as the host's cortex finds a version there: its three trees."""
        shutil.copytree(self.home / "versions" / "9.9.9", self.home / "versions" / "1.7.0")
        for tree in ("agents", "templates", "docs"):
            (self.home / "versions" / "1.7.0" / tree).mkdir(exist_ok=True)

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_a_committed_cortex_toml_is_said_to_need_what_it_lacks(self):
        # Committed, `cortex sync` reads it alone, and refuses it for the theme the team's file would have given.
        self.store_1_7_0()
        (self.project / ".git").mkdir()                    # committed, to the host's cortex without git on its PATH
        pin = self.files('version = "1.7.0"\n', team='version = "1.8.0"\ntheme = "h2g2"\n')
        with self.assertRaisesRegex(ValueError, r"— committed, it needs its theme$"):
            pin()
        host = self.cli("sync")
        self.assertEqual(host.returncode, 1, host.stdout + host.stderr)
        self.assertIn(b'cortex.toml: "theme" is required', host.stderr)

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_a_synced_left_over_names_the_sync_that_clears_it(self):
        # cortex.toml moved back below 1.8.0, and committed: `cortex sync` reads it alone, and records no synced —
        # what the team's file holds, a 1.7.0 project never reads. The remedy is that sync, not "it refuses this too".
        self.store_1_7_0()
        (self.project / ".git").mkdir()
        stale = 'synced = { team = "1.8.0", project = "1.8.0", pinned = "1.8.0", reads = "1.8.0" }\n'
        for team in ("version = [\n", 'version = "1.8.0"\nunknown = 1\n'):
            with self.subTest(team=team):
                pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team=team, local=stale)
                with self.assertRaisesRegex(ValueError, r"^agents/cortex\.team\.toml: .* — run `cortex sync` in the "
                                                        r"project, which reads a committed cortex\.toml before 1\.8\.0 "
                                                        r"alone — not committed, it refuses this file too$"):
                    pin()
                host = self.cli("sync")                     # the command named
                self.assertEqual(host.returncode, 0, host.stdout + host.stderr)
                self.assertEqual((pin().version, pin().theme), ("1.7.0", "h2g2"))
        # Not committed, the sync reads the team's file in its place (§3.3), and refuses it too — one that does not
        # parse included: beside a pin no commit holds, it is refused, not read as pinning nothing (§3.5).
        (self.project / ".git").rmdir()
        for team, said in (('version = "1.8.0"\nunknown = 1\n', b'agents/cortex.team.toml: unknown key "unknown"'),
                           ("version = [\n", b"agents/cortex.team.toml: ")):
            with self.subTest(team=team):
                pin = self.files('version = "1.7.0"\ntheme = "h2g2"\n', team=team, local=stale)
                with self.assertRaisesRegex(ValueError, r"not committed, it refuses this file too$"):
                    pin()
                host = self.cli("sync")
                self.assertEqual(host.returncode, 1, host.stdout + host.stderr)
                self.assertIn(said, host.stderr)

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_a_synced_left_over_beside_a_file_1_7_0_refuses_names_what_the_sync_does(self):
        # A synced left from another version beside a cortex.toml 1.7.0's grammar refuses: committed, the sync reads
        # it alone and refuses it too — never "reads it alone", which reads as a sync that clears it.
        self.store_1_7_0()
        (self.project / ".git").mkdir()
        stale = 'synced = { team = "1.8.0", project = "1.8.0", pinned = "1.8.0", reads = "1.8.0" }\n'
        for team, said in (('version = "1.8.0"\n', r"— cortex sync refuses this file too$"),
                           ('version = "1.8.0"\ntheme = "h2g2"\n', r"^cortex\.toml: \"theme\" is required — run `cortex "
                                                                  r"sync` .* — committed, it needs its theme$")):
            with self.subTest(team=team):
                pin = self.files('version = "1.7.0"\n', team=team, local=stale)
                with self.assertRaisesRegex(ValueError, said):
                    pin()
                host = self.cli("sync")                     # the sync, committed: it refuses the file, as said
                self.assertEqual(host.returncode, 1, host.stdout + host.stderr)
                self.assertIn(b'cortex.toml: "theme" is required', host.stderr)
        # Not committed, the sync named reads the team's file in its place, and records what the runtime then serves.
        (self.project / ".git").rmdir()
        shutil.copytree(self.home / "versions" / "1.7.0", self.home / "versions" / "1.8.0")
        pin = self.files('version = "1.7.0"\n', team='version = "1.8.0"\ntheme = "h2g2"\n', local=stale)
        with self.assertRaisesRegex(ValueError, r"— run `cortex sync` in the project, which may read "
                                                r"agents/cortex\.team\.toml instead of a cortex\.toml not committed"):
            pin()
        host = self.cli("sync")
        self.assertEqual(host.returncode, 0, host.stdout + host.stderr)
        self.assertEqual((pin().version, pin().theme), ("1.8.0", "h2g2"))

    @unittest.skipIf(os.name == "nt", "the source's cortex run with an empty environment")
    @outside_a_repository
    def test_below_1_8_0_a_try_or_a_version_that_is_none_keeps_its_own_422(self):
        # Mid-adoption — cortex.toml without its theme, the team's file giving it, synced: a try above the team's,
        # or a local version that is none, is said as such, never as cortex.toml's lack, which no sync clears there.
        self.store_1_7_0()
        for version in ("1.8.0", "1.9.0"):
            shutil.copytree(self.home / "versions" / "1.7.0", self.home / "versions" / version)
        pin = self.files('version = "1.7.0"\n', team='version = "1.8.0"\ntheme = "h2g2"\n')
        (self.project / "cortex.local.toml").unlink(missing_ok=True)
        host = self.cli("sync")
        self.assertEqual(host.returncode, 0, host.stdout + host.stderr)
        self.assertEqual(pin().version, "1.8.0")
        host = self.cli("upgrade", "--local", "1.9.0", "--force")
        self.assertEqual(host.returncode, 0, host.stdout + host.stderr)
        with self.assertRaisesRegex(ValueError, r"a try — and the runtime the team's 1\.8\.0: remove the try"):
            pin()
        host = self.cli("upgrade", "--local", "1.8.0")          # the try removed: at the team's, the key goes
        self.assertEqual(host.returncode, 0, host.stdout + host.stderr)
        self.assertEqual(pin().version, "1.8.0")
        synced = 'synced = { team = "1.8.0", project = "1.7.0", pinned = "1.8.0", reads = "1.8.0" }\n'
        (self.project / "cortex.local.toml").write_text('version = "x"\n' + synced, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r'^cortex\.local\.toml: "version" is no version'):
            pin()

    def test_a_synced_that_is_none_names_its_remedy_once(self):
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', local='synced = { pinned = "9.9.9" }\n')
        with self.assertRaisesRegex(ValueError, r'"synced": "reads" is required') as caught:
            pin()
        self.assertEqual(str(caught.exception).count("cortex sync"), 1, caught.exception)

    def test_a_checkout_this_machine_reads_leaves_the_team_s_version(self):
        # `cortex sync --from` records reads = "": a checkout is no try, and the runtime takes the team's version.
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n',
                         local='synced = { project = "9.9.9", pinned = "9.9.9", reads = "" }\n')
        self.assertEqual(pin().version, "9.9.9")

    def test_a_try_is_one_above_the_team_s_version_in_semver_order(self):
        for team, tried in (("9.9.9", "9.9.9"), ("1.10.0", "1.9.0"), ("9.9.9", "9.9.9-rc.1")):
            with self.subTest(team=team, tried=tried):              # a tie, a lower version, a pre-release of it
                pin = self.files(f'version = "{team}"\ntheme = "h2g2"\n', local=f'version = "{tried}"\n')
                self.assertEqual(pin().version, team)
        pin = self.files('version = "1.9.0"\ntheme = "h2g2"\n', local='version = "1.10.0"\n')
        with self.assertRaisesRegex(ValueError, r"reads Cortex 1\.10\.0, cortex\.local\.toml's version — a try"):
            pin()

    def test_a_try_in_cortex_toml_is_named_as_cortex_toml_s(self):
        # At a 2.B root, an uncommitted cortex.toml above the team's file is the developer's try.
        recorded = 'synced = { team = "9.9.8", project = "9.9.9", pinned = "9.9.8", reads = "9.9.9" }\n'
        pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', team='version = "9.9.8"\n', local=recorded)
        with self.assertRaisesRegex(ValueError, r"reads Cortex 9\.9\.9, cortex\.toml's version — a try — and the "
                                                r"runtime the team's 9\.9\.8"):
            pin()

    def test_a_local_version_that_is_no_version_is_refused_naming_the_key(self):
        for text in ('version = "x"\n', "version = 1\n", 'version = "1.0"\n'):
            with self.subTest(text=text):
                pin = self.files('version = "9.9.9"\ntheme = "h2g2"\n', local=text)
                with self.assertRaisesRegex(ValueError, r'^cortex\.local\.toml: "version" is no version — cortex sync '
                                                        r"refuses this file too$"):
                    pin()

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
